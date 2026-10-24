"""Live-mode protocol tests using fake BLE packets and a controlled clock."""


import json


import math


import sys


from pathlib import Path


import pytest


sys.path.insert(0, str(Path(__file__).parent))


import controller as c


import garmin_source as g


from adaptive_adapter import AdaptiveAdapter


from test_adaptive_integration import Player, track


@pytest.fixture
def live(monkeypatch, tmp_path):
    clock = [1000.0]
    monkeypatch.setattr(g.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(c, 'load_calm_list', lambda: [track()])
    monkeypatch.setattr(c.Controller, '_track_available', lambda *_: True)
    source = g.GarminSource(connect=False)
    learner = AdaptiveAdapter(tmp_path/'live.json', mode='live', epsilon=0)
    ctrl = c.Controller(source, 10, player=Player(), quiet=True, patient_id='volunteer-01',
                        adapter=learner, approved_ids={'jamendo:1': .5})
    def tick(hr=90, seconds=1):
        clock[0] += seconds
        if hr is not None:
            source.on_packet(bytes([6, hr]))
        ctrl.tick()
    for _ in range(120):
        tick(72)
    for _ in range(100):
        tick()
        if ctrl._active_exposure:
            break
    assert ctrl._active_exposure
    return ctrl, learner, tick


def test_one_ble_packet_cannot_be_used_twice():
    source = g.GarminSource(connect=False)
    source.on_packet(bytes([6, 72]))
    assert source.next() == 72
    assert math.isnan(source.next())


def test_live_loop_learns_once_and_labels_actual_source(live):
    ctrl, learner, tick = live
    for _ in range(65):
        tick(87)
    observation = learner.summary(ctrl.patient_id)['last_observation']
    assert learner.summary(ctrl.patient_id)['updates'] == 1
    assert observation['source_kind'] == 'live' and observation['simulated'] is False
    assert observation['reward_source'] == 'hr_proxy'
    assert ctrl.snapshot()['source_kind'] == 'live'
    learner.ingest(ctrl.telemetry(0))
    assert learner.summary(ctrl.patient_id)['updates'] == 1


@pytest.mark.parametrize('interruption', ['missing', 'stop', 'nurse', 'clock_gap'])
def test_interrupted_live_window_never_teaches(live, interruption):
    ctrl, learner, tick = live
    for _ in range(10):
        tick()
    if interruption == 'missing':
        tick(None)
    elif interruption == 'clock_gap':
        tick(seconds=5)
    else:
        ctrl.input(interruption)
    for _ in range(60):
        tick()
    assert learner.summary(ctrl.patient_id)['updates'] == 0
    assert learner.summary(ctrl.patient_id)['last_observation']['updated'] is False


def test_comfort_feedback_is_primary_when_available(live):
    ctrl, learner, tick = live
    exposure = ctrl._active_exposure
    ctrl.feedback({'session_id': ctrl.session_id, 'exposure_id': exposure, 'rating': 5})
    for _ in range(65):
        tick(90)
    observation = learner.summary(ctrl.patient_id)['last_observation']
    assert observation['reward_source'] == 'comfort'
    assert observation['reward'] == 1
    with pytest.raises(ValueError):
        ctrl.feedback({'session_id': ctrl.session_id, 'exposure_id': exposure, 'rating': 1})
    assert learner.summary(ctrl.patient_id)['updates'] == 1


def test_control_change_closes_feedback_window(live):
    ctrl, learner, tick = live
    exposure = ctrl._active_exposure
    ctrl.input('threshold:12')
    tick()
    assert not ctrl.snapshot()['feedback_open']
    with pytest.raises(ValueError):
        ctrl.feedback({'session_id': ctrl.session_id, 'exposure_id': exposure, 'rating': 5})


def test_live_feedback_http_route_and_repeat_submission(live):
    from urllib.request import Request, urlopen
    ctrl, learner, tick = live
    server = c.serve(ctrl, 0)
    payload = {'session_id':ctrl.session_id, 'exposure_id':ctrl._active_exposure, 'rating':4}
    try:
        for _ in range(2):
            with urlopen(Request(f'http://127.0.0.1:{server.server_port}/feedback',
                                 data=json.dumps(payload).encode(),
                                 headers={'Content-Type':'application/json'})) as response:
                assert json.load(response)['accepted']
        for _ in range(65):
            tick()
        assert learner.summary(ctrl.patient_id)['updates'] == 1
        assert learner.summary(ctrl.patient_id)['last_observation']['reward'] == .5
    finally:
        server.shutdown()
        server.server_close()


def test_live_checkpoint_rejects_simulation_and_replay_does_not_learn(live, tmp_path):
    ctrl, learner, tick = live
    with pytest.raises(ValueError, match='mode'):
        AdaptiveAdapter(learner.path, mode='simulated')
    ctrl.load_source(c.ListSource([90]*180, 'Recorded fixture'))
    for _ in range(120):
        ctrl.tick()
    assert not ctrl.selection_enabled
    assert learner.summary(ctrl.patient_id)['updates'] == 0
    assert json.loads(learner.path.read_text())['simulated'] is False


def test_live_cli_uses_jamendo_and_requires_explicit_profile():
    args = c.parse_args(['--source', 'garmin', '--adaptive-state', 'live.json',
                         '--patient-id', 'volunteer-01', '--approve-calm'])
    assert args.music == 'jamendo'
    with pytest.raises(SystemExit):
        c.parse_args(['--source', 'garmin', '--adaptive-state', 'live.json', '--demo-approve-calm'])


def test_return_to_live_preserves_watch_and_live_learner(live, monkeypatch):
    from urllib.request import Request, urlopen
    ctrl, learner, tick = live
    ctrl.source.device_id = 'watch-A'
    old_session = ctrl.session_id
    ctrl.load_source(c.ListSource([90]*180, 'Recorded fixture'))
    assert not ctrl.selection_enabled
    requested = []
    def make_source(spec):
        requested.append(spec)
        return g.GarminSource(device_id=spec.partition(':')[2], connect=False)
    monkeypatch.setattr(c, 'make_source', make_source)
    server = c.serve(ctrl, 0)
    try:
        with urlopen(Request(f'http://127.0.0.1:{server.server_port}/input',
                             data=b'{"action":"source:live"}',
                             headers={'Content-Type':'application/json'})) as response:
            assert response.status == 200
        assert requested == ['garmin:watch-A']
        assert ctrl.session_id != old_session and ctrl.selection_enabled
        assert ctrl.adapter is learner and learner.mode == 'live'
        assert ctrl.snapshot()['source_kind'] == 'live'
    finally:
        server.shutdown()
        server.server_close()


def test_reconnect_does_not_switch_to_another_watch(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    source = g.GarminSource(connect=False)
    connections = []
    scans = []
    async def discover(**kwargs):
        scans.append(1)
        address = 'watch-A' if len(scans) == 1 else 'watch-B'
        if len(scans) == 2:
            source.close()
        return {address:(SimpleNamespace(address=address, name=address), SimpleNamespace(local_name=address))}
    class Client:
        def __init__(self, device, disconnected_callback):
            connections.append(device.address)
            self.disconnected = disconnected_callback
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def start_notify(self, *_):
            self.disconnected(self)
    async def no_wait(_):
        pass
    monkeypatch.setitem(sys.modules, 'bleak', SimpleNamespace(BleakClient=Client,
                        BleakScanner=SimpleNamespace(discover=discover)))
    monkeypatch.setattr(g.asyncio, 'sleep', no_wait)
    asyncio.run(source._loop())
    assert connections == ['watch-A']
    assert source.status()['device_id'] == 'watch-A'

