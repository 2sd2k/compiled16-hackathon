"""Synthetic integration tests: no audio efficacy or real patient data."""
import copy
import json
from pathlib import Path
import sys
import threading
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import controller as c
from adaptive_adapter import AdaptiveAdapter, CONTRACT, vector, poll_controller
from response_simulator import ResponseSimSource


def track(sid='1'):
    return {'song_id': sid, 'title': sid, 'artist': 'synthetic fixture', 'bpm': 60, 'felt_bpm': 60,
            'key': 'C', 'mode': 'major', 'license': 'synthetic', 'calm': True,
            'loudness_dbfs': -20, 'dynamics_db': 4, 'brightness_hz': 1500,
            'bass_share': .2, 'mid_share': .7, 'treble_share': .1,
            'onsets_per_s': .5, 'bpm_reliable': True}


class Source:
    label = 'SIMULATED integration fixture'
    interactive_simulation = True
    def __init__(self):
        self.i, self.bad, self.ended = 0, False, False
    def next(self):
        self.i += 1
        return None if self.ended else (float('nan') if self.bad else (72.0 if self.i <= 120 else 90.0))
    def set(self, mode):
        pass


class Player:
    def __init__(self, confirm=True):
        self.track, self.on_event, self.exposure = None, None, None
        self.confirm, self.plays = confirm, 0
    def play(self, tr, exposure_id=None):
        self.track, self.exposure = tr, exposure_id or str(uuid.uuid4())
        self.plays += 1
        self.on_event('playback_requested', tr, self.exposure)
        if self.confirm:
            self.on_event('playback_started', tr, self.exposure)
    def silence(self, fade_s=0):
        if self.track:
            self.on_event('playback_interrupted', self.track, self.exposure, reason='silence', fade_s=fade_s)
        self.track = None
    def ended(self):
        return False


@pytest.fixture
def setup(monkeypatch, tmp_path):
    songs = [track('1'), track('2')]
    songs[1]['brightness_hz'] = 2500
    monkeypatch.setattr(c, 'load_calm_list', lambda: copy.deepcopy(songs))
    monkeypatch.setattr(c, 'AUDIO_DIR', tmp_path)
    for tr in songs:
        (tmp_path/f"{tr['song_id']}.mp3").touch()
    def build(external=False, confirm=True, adapter=None, source=None, patient="sim-patient-01"):
        learner = adapter or AdaptiveAdapter(tmp_path/'learner.json', epsilon=0)
        ctrl = c.Controller(source or Source(), 10, player=Player(confirm), quiet=True, patient_id=patient,
                            adapter=None if external else learner, external_selection=external,
                            approved_ids={'jamendo:1': .5, 'jamendo:2': .5})
        return ctrl, learner
    return build


def advance(ctrl, ticks):
    for _ in range(ticks):
        ctrl.tick()


def wait_for_start(ctrl, limit=220):
    for _ in range(limit):
        ctrl.tick()
        if ctrl._active_exposure:
            return ctrl.t
    raise AssertionError('No confirmed playback')


def test_connected_loop_learns_once_and_survives_restart(setup, tmp_path):
    ctrl, learner = setup()
    start = wait_for_start(ctrl)
    advance(ctrl, 65)
    assert learner.summary(ctrl.patient_id)['updates'] == 1
    observation = learner.summary(ctrl.patient_id)['last_observation']
    assert observation['pre_samples'] == observation['post_samples'] == 30
    assert observation['reward'] == pytest.approx(0, abs=1e-10)
    learner.ingest(ctrl.telemetry(0))
    restored = AdaptiveAdapter(tmp_path/'learner.json')
    restored.ingest(ctrl.telemetry(0))
    assert restored.summary(ctrl.patient_id)['updates'] == 1
    assert ctrl.snapshot()['now_playing']['song_id'].startswith('jamendo:')


def test_requested_but_unconfirmed_playback_cannot_teach(setup):
    ctrl, learner = setup(confirm=False)
    advance(ctrl, 250)
    assert ctrl.player.plays == 1 and ctrl._active_exposure is None
    assert learner.summary(ctrl.patient_id)['updates'] == 0


@pytest.mark.parametrize('action', ['stop', 'nurse', 'rest', 'threshold:12'])
def test_interrupted_window_never_updates(setup, action):
    ctrl, learner = setup()
    wait_for_start(ctrl)
    advance(ctrl, 20)
    ctrl.input(action)
    advance(ctrl, 65)
    assert learner.summary(ctrl.patient_id)['updates'] == 0
    assert learner.summary(ctrl.patient_id)['last_observation']['updated'] is False
    if action in ('stop', 'nurse'):
        assert ctrl.player.track is None


def test_bad_signal_and_source_end_cancel_window(setup):
    ctrl, learner = setup()
    wait_for_start(ctrl)
    ctrl.source.bad = True
    ctrl.tick()
    assert ctrl.player.track is None
    assert learner.summary(ctrl.patient_id)['updates'] == 0
    ctrl.source.ended = True
    ctrl.tick()
    assert ctrl.done
    assert ctrl.snapshot()['source_ended'] is True


def test_stale_request_and_unapproved_song_are_rejected(setup):
    ctrl, learner = setup(external=True)
    advance(ctrl, 170)
    learner.ingest(ctrl.telemetry())
    request = learner.choose(ctrl.snapshot())
    assert request
    bad = {**request, 'song_id': 'jamendo:unapproved'}
    with pytest.raises(ValueError, match='approved'):
        ctrl.select(bad)
    ctrl.input('stop')
    with pytest.raises(ValueError, match='permit'):
        ctrl.select(request)
    assert ctrl.player.plays == 0


def test_selection_retries_are_idempotent(setup):
    ctrl, learner = setup(external=True)
    advance(ctrl, 170)
    learner.ingest(ctrl.telemetry())
    request = learner.choose(ctrl.snapshot())
    result = ctrl.select(request)
    assert result['playback_confirmed'] is False
    assert ctrl.select(request) == result
    assert ctrl.player.plays == 1
    with pytest.raises(ValueError, match='different payload'):
        ctrl.select({**request, 'song_id': 'jamendo:other'})


def test_telemetry_gap_discards_inflight_observation(setup):
    ctrl, learner = setup()
    wait_for_start(ctrl)
    ctrl.tick()
    before = learner.cursor(ctrl.session_id)
    learner.ingest({'contract_version': CONTRACT, 'session_id': ctrl.session_id,
                    'patient_id': ctrl.patient_id, 'gap': True, 'latest_seq': before+5000, 'records': []})
    assert learner.summary(ctrl.patient_id)['last_observation']['reason'] == 'telemetry_gap'
    assert learner.summary(ctrl.patient_id)['updates'] == 0


def test_checkpoint_write_failure_rolls_back_and_controller_stays_silent(setup, monkeypatch):
    ctrl, learner = setup()
    before = copy.deepcopy(learner.state)
    def fail():
        raise OSError('test write failure')
    monkeypatch.setattr(learner, '_save', fail)
    ctrl.tick()
    assert learner.state == before
    assert ctrl.snapshot()['adaptive']['error'] == 'test write failure'
    advance(ctrl, 200)
    assert ctrl.player.plays == 0


def test_future_labels_and_ids_not_in_feature_vector():
    context = {'mean': 90, 'baseline': 72, 'slope': 0}
    a = vector(track(), context)
    modified = {**track(), 'song_id': '999', 'hku_hr_delta_mean': -500, 'future_hr': 1, 'deam_pred_valence': 9}
    np.testing.assert_equal(a, vector(modified, context))


def test_declining_pre_window_is_not_credited_to_song(setup):
    ctrl, learner = setup(external=True)
    advance(ctrl, 170)
    batch = ctrl.telemetry()
    samples = [r for r in batch['records'] if r['type'] == 'sample']
    for i, sample in enumerate(samples[-30:]):
        sample['hr_raw_bpm'] = 95-.2*i
    learner.ingest(batch)
    assert learner.choose(ctrl.snapshot()) is None


def test_patient_models_are_separate(setup):
    ctrl, learner = setup()
    wait_for_start(ctrl)
    advance(ctrl, 60)
    assert learner.summary(ctrl.patient_id)['updates'] == 1
    assert learner.summary('another-person')['updates'] == 0


def test_real_http_loop_selects_and_learns_once(setup, tmp_path):
    ctrl, _ = setup(external=True)
    adapter = AdaptiveAdapter(tmp_path/'remote.json', epsilon=0)
    server = c.serve(ctrl, 0)
    url = f'http://127.0.0.1:{server.server_port}'
    try:
        advance(ctrl, 170)
        assert poll_controller(adapter, url)['accepted']
        for _ in range(65):
            ctrl.tick()
        poll_controller(adapter, url)
        poll_controller(adapter, url)
        assert adapter.summary(ctrl.patient_id)['updates'] == 1
        ctrl.input('stop')
        assert not poll_controller(adapter, url)['accepted']
    finally:
        server.shutdown()
        server.server_close()


def test_hidden_song_effects_are_separate_and_control_has_none():
    a = ResponseSimSource('p1', seed=3, no_audio_effect=True)
    b = ResponseSimSource('p1', seed=3, no_audio_effect=True)
    a.set('rise'); b.set('rise')
    a.playback('jamendo:1')
    assert [a.next() for _ in range(200)] == [b.next() for _ in range(200)]
    enabled = ResponseSimSource('p1', seed=3)
    enabled.set('rise'); enabled.playback('jamendo:1')
    assert np.mean([enabled.next() for _ in range(200)]) < 90


def test_controller_restart_invalidates_pending_window(setup, tmp_path):
    first, learner = setup()
    wait_for_start(first)
    first.tick()  # persist confirmed start and pending response
    assert learner.state['sessions'][first.session_id]['pending']
    restored = AdaptiveAdapter(tmp_path/'learner.json')
    second, _ = setup(adapter=restored)
    second.tick()
    assert restored.state['sessions'][first.session_id]['pending'] == {}
    assert restored.summary(first.patient_id)['last_observation']['reason'] == 'controller_session_changed'
    assert restored.summary(first.patient_id)['updates'] == 0


def test_single_selection_owner_prevents_external_takeover(setup):
    ctrl, learner = setup()
    advance(ctrl, 170)
    with pytest.raises(ValueError, match='Embedded adapter'):
        ctrl.select({'request_id': 'external'})


def test_forced_silence_overrides_waiting_selection(setup):
    ctrl, learner = setup(external=True)
    advance(ctrl, 170)
    learner.ingest(ctrl.telemetry())
    request = learner.choose(ctrl.snapshot())
    ctrl.pause_until = ctrl.t+120
    with pytest.raises(ValueError, match='permit'):
        ctrl.select(request)
    assert ctrl.player.track is None


def test_preference_tier_limits_exploration(setup):
    ctrl, _ = setup(external=True)
    ctrl.approved_ids = {'jamendo:1': 1, 'jamendo:2': .1}
    adapter = AdaptiveAdapter(epsilon=1)
    advance(ctrl, 170)
    adapter.ingest(ctrl.telemetry())
    request = adapter.choose(ctrl.snapshot())
    assert request['song_id'] == 'jamendo:1'
    assert request['selection_probability'] == 1


def test_actual_audio_acknowledgement_and_stop_cancels_fade(tmp_path, monkeypatch):
    import time
    import wave
    import pygame
    from adaptive_player import Player as AudioPlayer
    pygame.mixer.quit()
    monkeypatch.setenv('SDL_AUDIODRIVER', 'dummy')
    with wave.open(str(tmp_path/'1.wav'), 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(22050)
        f.writeframes(b'\x00\x00'*(22050*15))
    events, started = [], threading.Event()
    def on_event(kind, tr, exposure, **details):
        events.append(kind)
        if kind == 'playback_started':
            started.set()
    player = AudioPlayer(tmp_path, file_extension='.wav')
    player.on_event = on_event
    try:
        player.play(track(), exposure_id='audio-test')
        assert started.wait(2)
        assert events[:2] == ['playback_requested', 'playback_started']
        player.silence(fade_s=.1)
        deadline = time.monotonic()+2
        while pygame.mixer.music.get_busy() and time.monotonic() < deadline:
            time.sleep(.01)
        assert not pygame.mixer.music.get_busy()
        assert events.count('playback_started') == 1
        assert 'playback_interrupted' in events
    finally:
        player.track, player._exposure = None, None
        with player._lock:
            pygame.mixer.music.stop()
            pygame.mixer.quit()


def test_observed_responses_change_person_specific_song_ranking(setup):
    class Profile(Source):
        def __init__(self):
            super().__init__()
            self.song = None
        def playback(self, song):
            self.song = song
        def next(self):
            initial = super().next()
            return (80 if self.song == 'jamendo:2' else 94) if self.song else initial
    learner = AdaptiveAdapter(epsilon=0)
    for sid in ('jamendo:1',)*3+('jamendo:2',)*3:
        ctrl, _ = setup(adapter=learner, source=Profile())
        ctrl.approved_ids = {sid: .5}
        wait_for_start(ctrl)
        advance(ctrl, 61)
    assert learner.summary('sim-patient-01')['updates'] == 6
    ctrl, _ = setup(adapter=learner, source=Profile())
    wait_for_start(ctrl)
    assert ctrl.player.track['song_id'] == '2'
    other, _ = setup(adapter=learner, source=Profile(), patient='another-person')
    wait_for_start(other)
    assert other.player.track['song_id'] == '1'


def test_delayed_audio_start_uses_acknowledged_start_window(setup):
    ctrl, learner = setup(confirm=False)
    advance(ctrl, 175)
    assert ctrl.player.track is not None and ctrl._active_exposure is None
    ctrl.player.on_event('playback_started', ctrl.player.track, ctrl.player.exposure)
    advance(ctrl, 65)
    assert learner.summary(ctrl.patient_id)['updates'] == 1


def test_recent_history_does_not_block_all_approved_tracks(setup):
    ctrl, learner = setup(external=True)
    ctrl.recent.extend(['1', '2'])
    advance(ctrl, 170)
    learner.ingest(ctrl.telemetry())
    assert learner.choose(ctrl.snapshot()) is not None


def test_slow_checkpoint_does_not_block_stop_or_snapshot(setup, monkeypatch):
    import time
    ctrl, learner = setup()
    wait_for_start(ctrl)
    entered, release = threading.Event(), threading.Event()
    original = learner._save
    def slow_save():
        entered.set()
        assert release.wait(3)
        original()
    monkeypatch.setattr(learner, '_save', slow_save)
    worker = threading.Thread(target=ctrl.tick)
    worker.start()
    try:
        assert entered.wait(2)
        started = time.monotonic()
        ctrl.input('stop')
        assert ctrl.snapshot()['stopped']
        assert ctrl.player.track is None
        assert time.monotonic()-started < .5
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()


def test_simulator_command_gets_settling_period_before_selection(setup):
    ctrl, learner = setup(external=True)
    advance(ctrl, 170)
    ctrl.input('rise')
    ctrl.tick()
    learner.ingest(ctrl.telemetry())
    assert learner.choose(ctrl.snapshot()) is None
    advance(ctrl, 60)
    learner.ingest(ctrl.telemetry(learner.cursor(ctrl.session_id)))
    assert learner.choose(ctrl.snapshot()) is not None


def test_seeded_exploration_is_independent_of_random_session_uuids(setup):
    choices = []
    for _ in range(2):
        ctrl, _ = setup(external=True)
        learner = AdaptiveAdapter(epsilon=1, seed=70)
        advance(ctrl, 170)
        learner.ingest(ctrl.telemetry())
        choices.append(learner.choose(ctrl.snapshot())['song_id'])
    assert choices[0] == choices[1]


def test_upload_replay_resets_session_and_cannot_train_on_selected_songs(setup):
    ctrl, learner = setup()
    wait_for_start(ctrl)
    ctrl.tick()
    old_session, old_exposure, old_track = ctrl.session_id, ctrl._active_exposure, ctrl.player.track
    ctrl.load_source(c.ListSource([72.0]*60, 'FAKE uploaded fixture'), speed=4)
    assert ctrl.session_id != old_session
    assert not ctrl.selection_enabled
    assert ctrl.snapshot()['replay']['speed'] == 4
    # A late decoder callback from the old session must not contaminate the new source.
    ctrl.player.on_event('playback_started', old_track, old_exposure)
    ctrl.tick()
    assert ctrl._active_exposure is None
    assert learner.summary(ctrl.patient_id)['updates'] == 0
    assert learner.summary(ctrl.patient_id)['last_observation']['reason'] == 'controller_session_changed'
    ctrl.load_source(c.SimSource(), speed=1)
    assert ctrl.selection_enabled
    assert ctrl.snapshot()['replay'] is None


def test_http_upload_and_return_to_sim_keep_existing_console_interface(setup):
    ctrl, _ = setup(external=True)
    server = c.serve(ctrl, 0)
    url = f'http://127.0.0.1:{server.server_port}'
    def post(path, payload):
        with urlopen(Request(url+path, data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'}), timeout=3) as response:
            return json.load(response)
    try:
        sid = ctrl.session_id
        csv_text = 't,hr\n'+''.join(f'{i},72\n' for i in range(60))
        assert post('/upload', {'name':'fake_test.csv', 'text':csv_text, 'speed':4})['seconds'] == 60
        assert ctrl.session_id != sid
        assert ctrl.snapshot()['source_kind'] == 'recorded'
        assert ctrl.snapshot()['learning_enabled'] is False
        assert post('/input', {'action':'source:sim'}) == {}
        assert ctrl.snapshot()['source_kind'] == 'simulated'
        assert ctrl.snapshot()['learning_enabled'] is True
    finally:
        server.shutdown();server.server_close()


def test_learning_off_playback_then_fresh_enabled_exposure(setup):
    ctrl, learner = setup()
    ctrl.input('learning:off')
    wait_for_start(ctrl)
    advance(ctrl, 65)
    assert learner.summary(ctrl.patient_id)['updates'] == 0
    assert learner.summary(ctrl.patient_id)['last_observation']['reason'] == 'learning_disabled'
    ctrl.input('learning:on')
    ctrl.input('stop')
    advance(ctrl, 35)
    ctrl.input('stop')
    wait_for_start(ctrl)
    advance(ctrl, 65)
    assert learner.summary(ctrl.patient_id)['updates'] == 1
