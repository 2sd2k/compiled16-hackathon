"""Offline tests for resonance/spotify.py: no network, Spotify replaced by fakes.

Run: python -m pytest -q resonance/test_spotify.py
"""
import base64
import hashlib
import io
import json
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spotify  # noqa: E402


@pytest.fixture
def auth(tmp_path, monkeypatch):
    monkeypatch.setattr(spotify, "CONFIG", tmp_path / "spotify_config.json")
    monkeypatch.setattr(spotify, "TOKEN", tmp_path / ".spotify_token.json")
    spotify.CONFIG.write_text(json.dumps({"client_id": "test-client"}))
    return spotify.SpotifyAuth(port=8765)


def test_login_url_uses_pkce_and_loopback_redirect(auth):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(auth.login_url()).query)
    assert q["client_id"] == ["test-client"]
    assert q["redirect_uri"] == ["http://127.0.0.1:8765/callback"]
    assert q["code_challenge_method"] == ["S256"]
    assert "user-modify-playback-state" in q["scope"][0]
    verifier = auth._pending[q["state"][0]]
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert q["code_challenge"] == [expected]


def test_callback_rejects_unknown_state(auth):
    with pytest.raises(spotify.SpotifyError):
        auth.handle_callback("code=abc&state=forged")


def test_callback_exchanges_code_and_saves_token(auth):
    state = urllib.parse.parse_qs(urllib.parse.urlparse(auth.login_url()).query)["state"][0]
    sent = {}

    def fake_urlopen(req, timeout=0):
        sent.update(urllib.parse.parse_qs(req.data.decode()))
        return io.BytesIO(json.dumps({"access_token": "A", "refresh_token": "R", "expires_in": 3600}).encode())

    with mock.patch.object(spotify.urllib.request, "urlopen", fake_urlopen):
        auth.handle_callback(f"code=abc&state={state}")
    assert sent["grant_type"] == ["authorization_code"] and sent["code_verifier"]
    assert "client_secret" not in sent
    assert auth.connected and json.loads(spotify.TOKEN.read_text())["refresh_token"] == "R"


class FakeSpotify:
    """Records Web API calls made by SpotifyPlayer."""

    def __init__(self, premium=True):
        self.calls, self.premium, self.now = [], premium, None

    def __call__(self, method, path, params=None, body=None, retry=True):
        self.calls.append((method, path, params, body))
        if path == "/me/player/devices":
            return {"devices": [{"id": "dev1", "name": "Laptop", "type": "Computer", "is_active": True,
                                 "supports_volume": True}]}
        if path == "/me/player/play":
            if not self.premium:
                raise spotify.SpotifyError("Spotify Premium is required to control playback.")
            self.now = body["uris"][0].split(":")[-1]
        if path == "/me/player":
            return {"is_playing": self.now is not None, "item": {"id": self.now}}
        if path == "/me/player/pause":
            self.now = None
        return None


def make_player(fake):
    errors = []
    p = spotify.SpotifyPlayer(auth=mock.Mock(), on_error=errors.append)
    p._api = fake
    return p, errors


def wait_idle(p):
    """Join the player's worker threads (time.sleep is patched out, so it can't be used to wait)."""
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.daemon:
            t.join(timeout=2)


@mock.patch.object(spotify.time, "sleep", lambda s: None)
def test_play_fades_in_and_silence_fades_out_then_pauses():
    fake = FakeSpotify()
    p, errors = make_player(fake)
    track = {"song_id": "4uLU6hMCjMI75M1A2tKUQC", "uri": "spotify:track:4uLU6hMCjMI75M1A2tKUQC"}
    p.play(track)
    wait_idle(p)
    plays = [c for c in fake.calls if c[1] == "/me/player/play"]
    assert plays and plays[0][3] == {"uris": [track["uri"]]} and plays[0][2]["device_id"] == "dev1"
    vols = [c[2]["volume_percent"] for c in fake.calls if c[1] == "/me/player/volume"]
    assert vols[0] == 0 and vols[-1] == spotify.VOLUME  # starts silent, ramps up
    fake.calls.clear()
    p.silence(fade_s=3)
    wait_idle(p)
    names = [c[1] for c in fake.calls]
    assert "/me/player/pause" in names and p.track is None and not errors


@mock.patch.object(spotify.time, "sleep", lambda s: None)
def test_premium_error_is_reported_once():
    p, errors = make_player(FakeSpotify(premium=False))
    for _ in range(2):
        p.play({"song_id": "x", "uri": "spotify:track:x"})
        wait_idle(p)
    assert len(errors) == 1 and "Premium" in errors[0]


@mock.patch.object(spotify.time, "sleep", lambda s: None)
def test_simulator_adaptive_layer_and_spotify_player_connect(tmp_path):
    """Simulated HR -> controller -> Josh's adaptive learner -> Spotify player (faked) -> learning update."""
    import controller as c
    from adaptive_adapter import AdaptiveAdapter
    from adaptive_controller import song_id
    from response_simulator import ResponseSimSource

    fake = FakeSpotify()
    player, errors = make_player(fake)
    calm = c.load_spotify_calm_list()
    assert calm and all(song_id(t["song_id"]).startswith("spotify:") for t in calm)
    learner = AdaptiveAdapter(tmp_path / "adaptive.json", epsilon=0, catalog="spotify")
    ctrl = c.Controller(ResponseSimSource("p1", seed=1), 10, player=player, quiet=True, patient_id="p1",
                        adapter=learner, approved_ids={song_id(t["song_id"]): .5 for t in calm}, music="spotify")
    for _ in range(125):
        ctrl.tick()
    ctrl.input("rise")
    started = None
    for _ in range(500):
        ctrl.tick()
        wait_idle(player)
        started = started or next((r for r in ctrl._telemetry if r["type"] == "playback_started"), None)
        if learner.summary("p1")["updates"]:
            break
    assert started and started["song_id"].startswith("spotify:") and started["policy"] == "adaptive-v1"
    assert any(call[1] == "/me/player/play" for call in fake.calls)
    assert learner.summary("p1")["updates"] == 1 and not errors
    assert json.loads((tmp_path / "adaptive.json").read_text())["feature_schema"] == "spotify-descriptors-state/1"


def test_ended_detects_stopped_or_changed_track():
    fake = FakeSpotify()
    p, _ = make_player(fake)
    p.track, p._started = {"song_id": "abc"}, time.time() - 60
    fake.now = "abc"
    assert p.ended() is False
    fake.now, p._state_checked = None, 0
    assert p.ended() is True
    fake.now, p._state_checked = "other", 0
    assert p.ended() is True
