"""Catalog, HTTP and playback regressions between the two controller branches."""
import json
import sys
import threading
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, build_opener, urlopen

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import controller as c
from adaptive_adapter import AdaptiveAdapter
from spotify import SpotifyPlayer
from test_spotify import FakeSpotify


class Player:
    def __init__(self):
        self.track = None
        self.auth = Mock(configured=True, connected=False)
        self.auth.login_url.return_value = 'https://accounts.spotify.invalid/authorize'
    def status(self):
        return {'configured': True, 'connected': False, 'device': '', 'error': ''}
    def silence(self, fade_s=0):
        self.track = None


def test_cli_defaults_to_jamendo_with_legacy_spotify_opt_in():
    assert c.parse_args([]).music == 'jamendo'
    assert c.parse_args([]).source == 'garmin'
    args = c.parse_args(['--source', 'sim', '--sim-response', '--adaptive-state', 'state.json', '--demo-approve-calm'])
    assert args.music == 'jamendo'
    assert c.parse_args(['--music', 'spotify']).music == 'spotify'
    assert c.parse_args(['--source', 'sim', '--music', 'jamendo', '--adaptive-state', 'state.json', '--demo-approve-calm']).music == 'jamendo'


def test_spotify_controller_preserves_metadata_without_local_mp3(monkeypatch):
    track = {'song_id': 'spotify:abc', 'track_id': 'abc', 'uri': 'spotify:track:abc',
             'title': 'Example', 'artist': 'Artist', 'bpm': 60, 'key_name': 'C minor',
             'detail': 'Spotify energy 0.2', 'calm_reason': 'slow'}
    monkeypatch.setattr(c, 'load_spotify_calm_list', lambda: [track], raising=False)
    player = Player()
    ctrl = c.Controller(c.SimSource(), 10, player=player, quiet=True, music='spotify')
    player.track = track
    state = ctrl.snapshot()
    assert state['music'] == 'spotify' and state['spotify']['configured']
    assert state['now_playing']['song_id'] == 'spotify:abc'
    assert state['now_playing']['source'] == 'Spotify'
    assert state['now_playing']['detail'] == track['detail']
    assert ctrl._track_available(track)
    with pytest.raises(ValueError, match='schema'):
        c.Controller(c.SimSource(), 10, player=Player(), quiet=True,
                     music='spotify', adapter=AdaptiveAdapter())


def test_adapter_rejects_other_catalog_before_consuming_telemetry():
    from adaptive_adapter import CONTRACT
    adapter = AdaptiveAdapter(catalog='jamendo')
    with pytest.raises(ValueError, match='catalog'):
        adapter.ingest({'contract_version': CONTRACT, 'session_id':'s', 'patient_id':'p',
                        'gap':False, 'records':[{'seq':1, 'session_id':'s', 'patient_id':'p', 'type':'playback_requested',
                        'song_id':'spotify:abc', 'exposure_id':'e'}]})
    assert adapter.cursor('s') == 0


def test_checkpoint_cannot_be_reopened_for_another_catalog(tmp_path):
    path = tmp_path/'checkpoint.json'
    adapter = AdaptiveAdapter(path, catalog='spotify')
    adapter._save()
    with pytest.raises(ValueError, match='catalog'):
        AdaptiveAdapter(path, catalog='jamendo')


def test_local_snapshot_preserves_display_fields_and_adaptive_summary(monkeypatch):
    track = {'song_id': '1', 'title': 'Local', 'artist': 'Artist', 'bpm': 60,
             'key': 'C', 'mode': 'minor', 'calm_reason': 'sparse', 'deam_pred_arousal': 2.4}
    monkeypatch.setattr(c, 'load_calm_list', lambda: [track])
    player = Player()
    ctrl = c.Controller(c.SimSource(), 10, player=player, quiet=True)
    player.track = track
    state = ctrl.snapshot()
    assert state['now_playing']['source'] == 'local file'
    assert 'predicted arousal 2.4/9' in state['now_playing']['detail']
    assert state['now_playing']['song_id'] == 'jamendo:1'
    assert 'adaptive' in state


def test_spotify_and_observer_routes_work_alongside_adaptive_telemetry(monkeypatch):
    monkeypatch.setattr(c, 'load_calm_list', lambda: [])
    ctrl = c.Controller(c.SimSource(), 10, player=Player(), quiet=True)
    ctrl.spotify_auth = ctrl.player.auth
    server = c.serve(ctrl, 0)
    url = f'http://127.0.0.1:{server.server_port}'
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    try:
        with pytest.raises(HTTPError) as redirect:
            build_opener(NoRedirect()).open(url+'/spotify/login')
        assert redirect.value.code == 302
        assert redirect.value.headers['Location'] == ctrl.spotify_auth.login_url.return_value
        with urlopen(url+'/callback?code=fake&state=test') as response:
            assert response.status == 200
        ctrl.spotify_auth.handle_callback.assert_called_once_with('code=fake&state=test')
        with urlopen(url+'/observer.js') as response:
            assert 'ResonanceObserver' in response.read().decode()
        with urlopen(url+'/telemetry?after_seq=0') as response:
            assert json.load(response)['session_id'] == ctrl.session_id
    finally:
        server.shutdown()
        server.server_close()


def test_spotify_stop_during_startup_never_confirms_or_fades_back_up():
    fake = FakeSpotify()
    player = SpotifyPlayer(Mock())
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    events = []
    player.on_event = lambda kind, *args, **kwargs: events.append(kind)
    def api(method, path, params=None, body=None, retry=True):
        if path == '/me/player/play':
            entered.set()
            assert release.wait(2)
            try:
                return fake(method, path, params, body, retry)
            finally:
                finished.set()
        return fake(method, path, params, body, retry)
    player._api = api
    player.play({'song_id': 'spotify:abc', 'track_id': 'abc', 'uri': 'spotify:track:abc'})
    assert entered.wait(2)
    player.silence(fade_s=0)
    release.set()
    assert finished.wait(2)
    for worker in list(threading.enumerate()):
        if worker.daemon and worker is not threading.current_thread():
            worker.join(timeout=2)
    assert player.track is None and fake.now is None
    assert 'playback_requested' in events and 'playback_interrupted' in events
    assert 'playback_started' not in events


def test_launcher_checks_both_runtime_dependencies(monkeypatch):
    import launch
    monkeypatch.setattr(launch, 'venv_python', lambda: Path(sys.executable))
    monkeypatch.setattr(launch.subprocess, 'run', Mock(return_value=Mock(returncode=0)))
    launch.ensure_env()
    check = launch.subprocess.run.call_args.args[0]
    assert 'pygame' in check[-1] and 'numpy' in check[-1]


def test_spotify_requires_matching_track_confirmation(monkeypatch):
    import spotify
    fake = FakeSpotify()
    player = SpotifyPlayer(Mock())
    events = []
    player.on_event = lambda kind, *args, **kwargs: events.append(kind)
    monkeypatch.setattr(spotify.time, 'sleep', lambda seconds: None)
    def api(method, path, params=None, body=None, retry=True):
        if path == '/me/player':
            return {'is_playing': True, 'item': {'id': 'different-song'}}
        return fake(method, path, params, body, retry)
    player._api = api
    player.play({'song_id': 'spotify:abc', 'uri': 'spotify:track:abc'})
    for worker in list(threading.enumerate()):
        if worker.daemon and worker is not threading.current_thread():
            worker.join(timeout=2)
    assert 'playback_started' not in events
    assert 'playback_interrupted' in events
    assert player.track is None and fake.now is None
    assert 'did not confirm' in player.last_error


def test_spotify_library_respects_catalog_preferences_without_fabricated_features(monkeypatch, tmp_path):
    path = tmp_path/'library.json'
    path.write_text(json.dumps({'tracks': [
        {'track_id':'a', 'calm':True, 'tempo':60, 'energy':.2, 'valence':.5, 'key_name':'C minor'},
        {'track_id':'b', 'calm':True, 'tempo':65, 'energy':.1, 'valence':None, 'key_name':'D minor'},
    ]}))
    prefs = tmp_path/'preferences.json'
    prefs.write_text(json.dumps({'preferences':{'spotify:a':1,'jamendo:1':1}, 'excluded':['spotify:b']}))
    monkeypatch.setattr(c, 'SPOTIFY_LIBRARY', path)
    monkeypatch.setattr(c, 'PREFS', prefs)
    tracks = c.load_spotify_calm_list()
    assert [track['song_id'] for track in tracks] == ['spotify:a']
    assert 'brightness_hz' not in tracks[0] and 'loudness_dbfs' not in tracks[0]
