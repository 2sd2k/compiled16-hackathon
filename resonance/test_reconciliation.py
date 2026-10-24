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

