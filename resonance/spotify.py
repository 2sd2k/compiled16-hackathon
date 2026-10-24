"""Spotify playback for Resonance: the controller tells the Spotify app what to play, pause and how loud.

Spotify audio cannot be downloaded, so playback goes through the Spotify Web API to a Spotify app
signed in on this computer (Spotify Premium is required for playback control).

One-time setup (done by a person, never by the software):
  1. Create an app at https://developer.spotify.com/dashboard and add the redirect URI
     http://127.0.0.1:8765/callback  (choose "Web API").
  2. Put its Client ID in resonance/spotify_config.json: {"client_id": "..."}
     (Authorization Code with PKCE: no client secret is needed or stored.)
  3. Start the controller, open the console and click "Connect Spotify"; sign in on Spotify's page.
The access token is kept in resonance/.spotify_token.json (gitignored) and refreshed automatically.
"""
import base64
import hashlib
import json
import secrets
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "spotify_config.json"
TOKEN = HERE / ".spotify_token.json"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
SCOPES = "user-modify-playback-state user-read-playback-state"
VOLUME = 60  # percent


class SpotifyError(Exception):
    pass


class SpotifyAuth:
    def __init__(self, port=8765):
        self.redirect_uri = f"http://127.0.0.1:{port}/callback"
        self.client_id = None
        if CONFIG.exists():
            self.client_id = (json.loads(CONFIG.read_text()).get("client_id") or "").strip() or None
        self.token = json.loads(TOKEN.read_text()) if TOKEN.exists() else None
        self._pending = {}  # state -> code_verifier
        self._lock = threading.Lock()

    @property
    def configured(self):
        return bool(self.client_id)

    @property
    def connected(self):
        return bool(self.token and self.token.get("refresh_token"))

    def login_url(self):
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        self._pending[state] = verifier
        return AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": self.client_id, "response_type": "code", "redirect_uri": self.redirect_uri,
            "code_challenge_method": "S256", "code_challenge": challenge, "scope": SCOPES, "state": state})

    def _token_request(self, form):
        req = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode(form).encode(),
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                tok = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise SpotifyError(f"Spotify sign-in failed ({e.code}): {e.read()[:200].decode('utf-8', 'replace')}")
        tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
        if "refresh_token" not in tok and self.token:
            tok["refresh_token"] = self.token.get("refresh_token")
        self.token = tok
        TOKEN.write_text(json.dumps(tok))

    def handle_callback(self, query):
        params = urllib.parse.parse_qs(query)
        if "error" in params:
            raise SpotifyError(f"Spotify sign-in was not completed: {params['error'][0]}")
        state, code = params.get("state", [""])[0], params.get("code", [""])[0]
        verifier = self._pending.pop(state, None)
        if not verifier or not code:
            raise SpotifyError("Sign-in link expired or mismatched; click Connect Spotify again.")
        self._token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri,
                             "client_id": self.client_id, "code_verifier": verifier})

    def access_token(self):
        with self._lock:
            if not self.connected:
                raise SpotifyError("Spotify is not connected: open the console and click Connect Spotify.")
            if time.time() >= self.token.get("expires_at", 0):
                self._token_request({"grant_type": "refresh_token", "refresh_token": self.token["refresh_token"],
                                     "client_id": self.client_id})
            return self.token["access_token"]


class SpotifyPlayer:
    """Same interface as controller.Player (track, play, silence, ended), backed by the Spotify app."""

    def __init__(self, auth, on_error=print):
        self.auth, self.on_error = auth, on_error
        self.track = None
        self.device = None  # {"id", "name"}
        self.last_error = ""
        self.on_event = None  # set by the controller's adaptive layer
        self._lock = threading.Lock()
        self._started = 0
        self._exposure, self._ended_reported = None, False
        self._state_checked, self._state = 0, None

    # --- Web API ---
    def _api(self, method, path, params=None, body=None, retry=True):
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = json.dumps(body).encode() if body is not None else (b"" if method == "PUT" else None)
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.auth.access_token()}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code == 401 and retry:
                self.auth.token["expires_at"] = 0
                return self._api(method, path, params, body, retry=False)
            detail = e.read()[:300].decode("utf-8", "replace")
            if e.code == 403 and "PREMIUM" in detail.upper():
                raise SpotifyError("Spotify Premium is required to control playback.")
            if e.code == 404:
                raise SpotifyError("No active Spotify app found: open Spotify on this computer and play anything once.")
            raise SpotifyError(f"Spotify API {e.code}: {detail[:160]}")
        except urllib.error.URLError as e:
            raise SpotifyError(f"Cannot reach Spotify (internet?): {e.reason}")

    def _pick_device(self):
        devices = (self._api("GET", "/me/player/devices") or {}).get("devices", [])
        if not devices:
            raise SpotifyError("No Spotify device available: open the Spotify app on this computer.")
        d = (next((x for x in devices if x.get("is_active")), None)
             or next((x for x in devices if x.get("type") == "Computer"), None) or devices[0])
        self.device = {"id": d["id"], "name": d.get("name", ""), "volume": d.get("supports_volume", True)}
        return self.device

    def _volume(self, pct):
        if self.device and self.device.get("volume"):
            try:
                self._api("PUT", "/me/player/volume", {"volume_percent": int(pct), "device_id": self.device["id"]})
            except SpotifyError:
                self.device["volume"] = False  # some devices refuse remote volume; keep going without fades

    def _ramp(self, start, end, seconds, steps=6):
        for i in range(1, steps + 1):
            self._volume(start + (end - start) * i / steps)
            time.sleep(seconds / steps)

    def _guard(self, fn, *args):
        try:
            fn(*args)
            self.last_error = ""
        except SpotifyError as e:
            if str(e) != self.last_error:
                self.on_error(f"Spotify: {e}")
            self.last_error = str(e)

    # --- controller interface (same events as adaptive_player.Player) ---
    def _notify(self, kind, track, exposure, **details):
        if getattr(self, "on_event", None):
            self.on_event(kind, track, exposure, **details)

    def play(self, track, exposure_id=None):
        if self.track is not None and not self._ended_reported:
            self._notify("playback_interrupted", self.track, self._exposure, reason="replaced")
        token = exposure_id or uuid.uuid4().hex
        self.track, self._exposure, self._ended_reported = track, token, False
        self._started = time.time()
        self._notify("playback_requested", track, token)
        threading.Thread(target=self._play, args=(track, token), daemon=True).start()
        return token

    def _play(self, track, token):
        with self._lock:
            if self._exposure != token:
                return
            try:
                dev = self.device or self._pick_device()
                self._volume(0)
                try:
                    self._api("PUT", "/me/player/play", {"device_id": dev["id"]}, {"uris": [track["uri"]]})
                except SpotifyError:
                    dev = self._pick_device()  # device may have changed; try once more
                    self._api("PUT", "/me/player/play", {"device_id": dev["id"]}, {"uris": [track["uri"]]})
                # Only report a start once Spotify confirms this track is playing (learning windows rely on it).
                for _ in range(16):
                    if self._exposure != token:
                        return
                    state = self._api("GET", "/me/player") or {}
                    if state.get("is_playing") and (state.get("item") or {}).get("id") == track["song_id"]:
                        break
                    time.sleep(0.5)
                else:
                    raise SpotifyError("Spotify did not confirm the song started.")
                if self._exposure != token:
                    return
                self._started = time.time()
                self.last_error = ""
                self._notify("playback_started", track, token)
                self._ramp(0, VOLUME, 5)
            except SpotifyError as e:
                if str(e) != self.last_error:
                    self.on_error(f"Spotify: {e}")
                self.last_error = str(e)
                if self._exposure == token:
                    self.track, self._exposure = None, None
                self._notify("playback_interrupted", track, token, reason="spotify_error")

    def silence(self, fade_s=3.0):
        track, token = self.track, self._exposure
        self.track, self._exposure = None, None  # cancel any in-flight start
        if track is not None and not self._ended_reported:
            self._notify("playback_interrupted", track, token, reason="silence", fade_s=fade_s)
        threading.Thread(target=self._guard, args=(self._silence, fade_s), daemon=True).start()

    def _silence(self, fade_s):
        with self._lock:
            if self.track is not None or not self.device:
                return
            if fade_s >= 1:
                self._ramp(VOLUME, 0, fade_s, steps=4)
            try:
                self._api("PUT", "/me/player/pause", {"device_id": self.device["id"]})
            except SpotifyError as e:
                if "403" not in str(e):  # already paused
                    raise
            self._volume(VOLUME)

    def ended(self):
        if self.track is None or time.time() - self._started < 15:
            return False
        if time.time() - self._state_checked >= 5:  # poll gently
            self._state_checked = time.time()
            try:
                self._state = self._api("GET", "/me/player")
            except SpotifyError:
                return False
        s = self._state or {}
        item = (s.get("item") or {}).get("id")
        ended = not s.get("is_playing") or (item is not None and item != self.track.get("song_id"))
        if ended and not self._ended_reported:
            self._ended_reported = True
            self._notify("playback_ended", self.track, self._exposure, reason="track_ended")
        return ended

    def status(self):
        return {"configured": self.auth.configured, "connected": self.auth.connected,
                "device": (self.device or {}).get("name", ""), "error": self.last_error,
                "redirect_uri": self.auth.redirect_uri}
