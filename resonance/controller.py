"""Resonance controller: heart rate -> decision -> audio, with no screen required.

The loop runs on its own once started. Keys in this window stand in for the room's real inputs:
    s  patient "stop" (physical button / voice stand-in); press again to resume
    n  nurse enters / leaves (BLE badge stand-in)
    r  simulated heart-rate rise      c  simulated return to rest      q  quit
The Developer / Demo Console (http://127.0.0.1:8765) only shows state and sends the same
simulated inputs. Closing it changes nothing.

Usage:
    python resonance/controller.py                      live Garmin heart rate (default)
    python resonance/controller.py --source sim         simulated heart rate (labeled, for testing)
    python resonance/controller.py --source csv:FILE    replay a t,hr CSV at 1 Hz (hr_test.py format)
    python resonance/controller.py --source hku:hku1903 replay a recorded HKU956 session
    --music jamendo (default) plays local audio; Spotify is a legacy opt-in.
    Adaptive modes use separate Spotify-descriptor or Jamendo-waveform schemas.
"""
import argparse
import collections
import csv
import io
import json
import math
import os
import random
import statistics
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import pygame  # noqa: E402

try:
    from .adaptive_controller import AdaptiveControllerMixin, CONTRACT, song_id
    from .adaptive_player import Player
    from .adaptive_adapter import AdaptiveAdapter
    from .response_simulator import ResponseSimSource
    from .spotify import SpotifyAuth, SpotifyPlayer, SpotifyError
except ImportError:
    from adaptive_controller import AdaptiveControllerMixin, CONTRACT, song_id
    from adaptive_player import Player
    from adaptive_adapter import AdaptiveAdapter
    from response_simulator import ResponseSimSource
    from spotify import SpotifyAuth, SpotifyPlayer, SpotifyError

REPO = Path(__file__).resolve().parent.parent
LIBRARY = REPO / "app" / "data" / "music_library.json"
SPOTIFY_LIBRARY = REPO / "app" / "data" / "spotify_library.json"
SESSIONS = REPO / "app" / "data" / "sessions.json"
AUDIO_DIR = REPO / "data" / "raw" / "audio"
CONSOLE = Path(__file__).resolve().parent / "console.html"
OBSERVER = Path(__file__).resolve().parent / "observer.js"
# Optional patient preferences gathered by voice or a caregiver, never on a screen:
# {"preferences": {"<song_id>": 0..1}, "excluded": ["<song_id>", ...]}
PREFS = Path(__file__).resolve().parent / "patient_preferences.json"

sys.path.insert(0, str(REPO / "baseline"))
from ranking import rank_music  # noqa: E402  (Josh's preference-first ranking)

TARGET_AROUSAL = 2.5    # Josh's elevated-state arousal target on DEAM's 1-9 scale (unvalidated prior)

BASELINE_S = 120        # hr_test.py: baseline from the first 120 s
SMOOTH_S = 10           # hr_test.py: 10 s rolling median
SUSTAIN_S = 20          # hr_test.py: elevated must hold for 20 s
CALM_BPM = (55, 70)     # Bernardi 2006: slowest tempo gave the largest HR fall
ELEVATED_MUSIC_S = 180  # still elevated after 3 min of music -> try silence (Bernardi 2006)
SILENCE_PAUSE_S = 120   # Bernardi's silent pause was 2 min
RESTING_FADE_S = 60     # back near baseline for a minute -> let the room go quiet
SESSION_CAP_S = 1800    # ~30 min sessions (Zhang 2026)
VOLUME = 0.6


# ---------- heart-rate sources ----------
class SimSource:
    """Labeled synthetic heart rate. 'rise' / 'rest' set a target the signal drifts toward."""
    interactive_simulation = True
    label = "SIMULATED heart rate"

    def __init__(self, base=72.0):
        self.base, self.lift, self.target = base, 0.0, 0.0

    def set(self, mode):
        self.target = 18.0 if mode == "rise" else 0.0

    def next(self):
        self.lift += max(-0.6, min(1.0, self.target - self.lift))
        return round(self.base + self.lift + random.gauss(0, 1.0), 1)


class ListSource:
    """Replays recorded 1 Hz heart rate."""

    def __init__(self, values, label):
        self.values, self.label, self.i = values, label, 0

    def set(self, mode):
        pass

    def next(self):
        if self.i >= len(self.values):
            return None
        v = self.values[self.i]
        self.i += 1
        return v


MAX_UPLOAD_BYTES = 5_000_000
HR_COLUMNS = ("hr", "heart_rate", "heartrate", "heart rate", "bpm", "pulse")
T_COLUMNS = ("t", "time", "seconds", "time_s", "elapsed_s")  # elapsed_s: Garmin receiver CSVs


def _num(s):
    try:
        v = float(str(s).strip())
    except ValueError:
        return None
    return v if v == v and abs(v) != float("inf") else None


def parse_hr_csv(text):
    """Heart-rate CSV -> 1 Hz values. Needs an hr column (t in seconds optional) or one bare column.

    Cleaning follows hr_test.py: drop readings outside 40-200 bpm, average to 1 Hz, carry the last
    value across gaps of up to 5 s; longer gaps are skipped rather than invented.
    """
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("the file is empty")
    header = [c.strip().lower() for c in rows[0]]
    hr_col = next((header.index(c) for c in HR_COLUMNS if c in header), None)
    t_col = next((header.index(c) for c in T_COLUMNS if c in header), None)
    if hr_col is not None:
        data = rows[1:]
    elif len(rows[0]) == 1 and _num(rows[0][0]) is not None:
        hr_col, data = 0, rows
    else:
        raise ValueError("no heart-rate column: use a header with 'hr' (and optionally 't' in seconds)")

    readings, dropped = [], 0
    for i, r in enumerate(data):
        hr = _num(r[hr_col]) if hr_col < len(r) else None
        t = (_num(r[t_col]) if t_col < len(r) else None) if t_col is not None else float(i)
        if hr is None or t is None or not 40 <= hr <= 200:
            dropped += 1
            continue
        readings.append((t, hr))
    if not readings:
        raise ValueError("no readings between 40 and 200 bpm")
    readings.sort()
    per_s = {}
    for t, hr in readings:
        per_s.setdefault(int(t - readings[0][0]), []).append(hr)
    values, last, gap, filled, skipped = [], None, 0, 0, 0
    for s in range(max(per_s) + 1):
        if s in per_s:
            last, gap = round(statistics.mean(per_s[s]), 1), 0
            values.append(last)
        else:
            gap += 1
            if gap <= 5:
                values.append(last)
                filled += 1
            else:
                skipped += 1
    if len(values) < 30:
        raise ValueError(f"only {len(values)} s of usable heart rate; need at least 30 s")
    return values, {"rows": len(data), "dropped": dropped, "seconds": len(values),
                    "gap_seconds_filled": filled, "gap_seconds_skipped": skipped}


def source_label(name):
    return f"FAKE dataset {name}" if name.lower().startswith("fake") else f"Uploaded file {name}"


def make_source(spec):
    if spec == "sim":
        return SimSource()
    kind, _, arg = spec.partition(":")
    if kind == "csv":
        values, _ = parse_hr_csv(Path(arg).read_text(encoding="utf-8-sig"))
        return ListSource(values, source_label(Path(arg).name))
    if kind == "garmin":
        # Live watch via Josh's Bluetooth receiver; see garmin_source.py. garmin:DEVICE_ID picks a watch.
        from garmin_source import GarminSource
        return GarminSource(arg or None)
    if kind == "hku":
        sessions = json.loads(SESSIONS.read_text())["sessions"]
        s = next(x for x in sessions if x["participant_id"] == arg)
        return ListSource(s["hr"], f"Recorded HKU956 session {arg}")
    raise SystemExit(f"unknown source {spec}")


# ---------- music ----------
def load_spotify_calm_list():
    """Use actual Spotify descriptors, without inventing native acoustic fields."""
    if not SPOTIFY_LIBRARY.exists():
        return []
    tracks = json.loads(SPOTIFY_LIBRARY.read_text(encoding="utf-8"))["tracks"]
    prefs = json.loads(PREFS.read_text()) if PREFS.exists() else {}
    preferences = {str(k).removeprefix('spotify:'): float(v)
                   for k, v in prefs.get('preferences', {}).items()
                   if not str(k).startswith('jamendo:')}
    excluded = {str(k).removeprefix('spotify:') for k in prefs.get('excluded', [])
                if not str(k).startswith('jamendo:')}
    candidates = [{**t, 'song_id': t['track_id'], 'bpm': t['tempo'], 'license': 'Spotify',
                   'predicted_arousal_mean': 1+8*t['energy'],
                   'predicted_valence_mean': 1+8*(t['valence'] if t['valence'] is not None else .5),
                   'detail': f"{t['key_name']}, Spotify energy {t['energy']:.2f}"}
                  for t in tracks if t.get('calm')]
    ranked = rank_music(candidates, preferences, excluded, target_arousal=1, valence_weight=0)
    return [{**t, 'song_id': 'spotify:'+t['track_id']} for t in ranked]


def load_calm_list():
    """Calm list (see analysis/classify_calm.py) with a local file, so the demo works offline."""
    if not LIBRARY.exists():
        return []
    tracks = json.loads(LIBRARY.read_text(encoding="utf-8"))["tracks"]
    calm = [t for t in tracks if t.get("calm") and (AUDIO_DIR / f"{t['song_id']}.mp3").exists()]
    if not calm:
        return []
    if any(t.get("deam_pred_arousal") is None for t in calm):
        return sorted(calm, key=lambda t: t.get("felt_bpm", t["bpm"]))  # slower first (Bernardi 2006)

    # Order within the calm list with Josh's preference-first ranking (baseline/ranking.py), using
    # his DEAM model's predicted arousal. Valence is off: predicted valence did not track listener
    # valence on this library (analysis/out/deam_on_hku_validation.json).
    prefs = json.loads(PREFS.read_text()) if PREFS.exists() else {}
    preferences = {int(str(k).removeprefix('jamendo:')): float(v)
                   for k, v in prefs.get("preferences", {}).items()
                   if str(k).removeprefix('jamendo:').isdigit()}
    excluded = {int(str(s).removeprefix('jamendo:')) for s in prefs.get("excluded", [])
                if str(s).removeprefix('jamendo:').isdigit()}
    cands = [{**t, "song_id": int(t["song_id"]), "predicted_arousal_mean": t["deam_pred_arousal"],
              "predicted_valence_mean": t["deam_pred_valence"]} for t in calm]
    ranked = rank_music(cands, preferences, excluded, target_arousal=TARGET_AROUSAL, valence_weight=0)
    return [{**t, "song_id": str(t["song_id"])} for t in ranked]


# ---------- controller ----------
class Controller(AdaptiveControllerMixin):
    def __init__(self, source, threshold, baseline_s=BASELINE_S, player=None, quiet=False,
                 patient_id="sim-patient-01", adapter=None, external_selection=False, approved_ids=None,
                 music="jamendo"):
        if music not in ('spotify', 'jamendo'):
            raise ValueError('Unknown music catalog')
        expected_schema = {'spotify': 'spotify-descriptors-state/1', 'jamendo': 'native-acoustics-state/1'}[music]
        if adapter and adapter.schema != expected_schema:
            raise ValueError('Adapter checkpoint schema does not match the music catalog')
        self.music = music
        self.source, self.thr, self.baseline_s = source, threshold, baseline_s
        self.quiet = quiet
        self.speed = 1.0
        self.sim_source_factory = SimSource
        self.player = player or (SpotifyPlayer(SpotifyAuth()) if music == 'spotify' else Player())
        self.spotify_auth = getattr(self.player, 'auth', None) if music == 'spotify' else None
        self.calm = self._load_music()
        self.lock = threading.RLock()
        self.hr, self.smooth = [], []
        self.baseline = None
        self.elevated = False
        self.above = self.below = 0
        self.stopped = self.nurse = False
        self.music_s = self.elevated_music_s = self.resting_s = 0
        self.pause_until = -1
        self.recent = collections.deque(maxlen=3)
        self.events = collections.deque(maxlen=60)
        self.t = 0
        self.done = False
        self.warned_empty = False
        self._adaptive_init(patient_id, adapter, external_selection, approved_ids)
        self.log(f"Started. Input: {source.label}. Calm list: {len(self.calm)} {self.music} tracks.")
        if not self.calm:
            self.log("No calm tracks available: the room will stay silent. Build the music library first.")

    def _load_music(self):
        return load_spotify_calm_list() if self.music == 'spotify' else load_calm_list()

    def _reset(self):
        """Fresh source/session; retain per-person model, never pending windows."""
        self.hr, self.smooth = [], []
        self.baseline = None
        self.elevated = False
        self.above = self.below = 0
        self.stopped = self.nurse = False
        self.paused_track = None
        self.music_s = self.elevated_music_s = self.resting_s = 0
        self.pause_until = -1
        self.recent = collections.deque(maxlen=3)
        self.t, self.done, self.warned_empty = 0, False, False
        self._adaptive_init(self.patient_id, self.adapter, self.external_selection, self.approved_ids)

    def load_source(self, source, speed=1.0, note=""):
        """Console upload/replay, with a new observation session and baseline."""
        speed = max(.5, min(20.0, float(speed)))
        if (getattr(source, 'interactive_simulation', False) and (self.adapter or self.external_selection)
                and speed != 1):
            raise ValueError('Adaptive simulated playback requires speed 1')
        with self.lock:
            self.player.silence(fade_s=1.5)
            if getattr(self.source, 'live', False) and getattr(self.source, 'device_id', None):
                self.live_source_spec = 'garmin:'+self.source.device_id
            if hasattr(self.source, "close"):
                self.source.close()  # e.g. release the Garmin Bluetooth connection
            self.source, self.speed = source, speed
            self._reset()
            self.log(f"Input switched to {source.label}{note}; replay speed {self.speed:g}×. "
                     f"Learning a new baseline from its first {self.baseline_s} s.")

    def log(self, msg):
        self.events.appendleft({"time": time.strftime("%H:%M:%S"), "msg": msg})
        if not self.quiet:
            print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    # Inputs from keys or the console. Each one stands in for a real-world signal.
    def input(self, action):
        with self.lock:
            if action in ("learning:on", "learning:off"):
                if not self.selection_enabled or not self.approved_ids or self._adaptive_fault:
                    raise ValueError("Learning unavailable: start with a patient profile and approved songs; resolve any adapter error.")
                paused = action == "learning:off"
                if self.learning_paused == paused:
                    return
                self.learning_paused = paused
                self.log("Learning paused; saved preferences retained." if paused else
                         "Learning enabled for new response windows.")
            elif action == "stop":
                self.stopped = not self.stopped
                if self.stopped:
                    self.player.silence(fade_s=0.3)
                    self.log("Patient said “stop” → instant silence. Stays silent until the patient resumes.")
                else:
                    self.log("Patient resumed → audio may return when heart rate calls for it.")
            elif action == "nurse":
                self.nurse = not self.nurse
                if self.nurse:
                    self.paused_track = self.player.track
                    self.player.silence(fade_s=2.5)
                    self.log("Nurse entered → audio faded out so speech and alarms are clear.")
                else:
                    self.log("Nurse left → room returns to its previous state.")
                    if (getattr(self, "paused_track", None) and not self.stopped and not self.selection_enabled
                            and self.t >= self.pause_until):
                        self.player.play(self.paused_track)
            elif action in ("rise", "rest"):
                if getattr(self.source, "interactive_simulation", False):
                    self.source.set(action)
                    self.log(f"[simulated input] heart rate {'rising' if action == 'rise' else 'returning to rest'}.")
            elif action.startswith("threshold:"):
                self.thr = max(6, min(15, int(action.split(":")[1])))
                self.log(f"[test] threshold set to +{self.thr} bpm.")
            else:
                raise ValueError("Unknown controller input")
            self._control_changed(action, source=action in ("rise", "rest"))

    def room_state(self):
        if self.stopped:
            return "Stopped"
        if self.nurse:
            return "Nurse present"
        if self.baseline is None:
            return "Learning baseline"
        return "Elevated" if self.elevated else "Resting"

    def tick(self):
        with self.lock:
            self._tick_locked()
        # Model/checkpoint work must never hold the controller's Stop/control lock.
        self._adaptive_run()
        with self.lock:
            self._drain_player_events()

    def _tick_locked(self):
        self._drain_player_events()
        if self.done:
            return
        v = self.source.next()
        if v is None:
            self.done = True
            self._opportunity = None
            self.player.silence(fade_s=.3)
            self._emit('source_ended')
            self._drain_player_events()
            self.log("Recorded input ended.")
            return
        self.t += 1
        self.signal_ok = isinstance(v, (float, int)) and math.isfinite(v) and v > 0
        if self._source_kind() == 'live' and self.selection_enabled:
            now = time.monotonic()
            if self._last_live_tick is not None and not .5 <= now-self._last_live_tick <= 1.5:
                self.signal_ok = False
            self._last_live_tick = now
        self.hr.append(v if self.signal_ok else None)
        window = self.hr[-SMOOTH_S:]
        smooth_valid = all(x is not None for x in window)
        self.smooth.append(statistics.median(window) if smooth_valid else None)
        if self.baseline is None:
            if self.signal_ok:
                self._baseline_samples.append(v)
            else:
                self._baseline_samples.clear()
            if len(self._baseline_samples) >= self.baseline_s:
                self.baseline = statistics.median(self._baseline_samples[:self.baseline_s])
                self.log(f"Baseline set: {self.baseline:.0f} bpm (median of {self.baseline_s} valid samples).")
        self._sample_event(v)
        if not self.signal_ok or not smooth_valid:
            self.above = self.below = 0
            self.elevated = False
            self._opportunity = None
            if self.player.track:
                self.player.silence(fade_s=.3)
        elif self.baseline is not None:
            self._update_elevated()
            self._decide()
        self._drain_player_events()

    def _track_available(self, track):
        if self.music == 'spotify':
            return bool(track.get('uri', '').startswith('spotify:track:'))
        return (AUDIO_DIR / f"{track['song_id']}.mp3").exists()

    def _update_elevated(self):
        sm = self.smooth[-1]
        if sm >= self.baseline + self.thr:
            self.above, self.below = self.above + 1, 0
        elif sm < self.baseline + self.thr / 2:
            self.below, self.above = self.below + 1, 0
        else:
            self.above = self.below = 0
        if not self.elevated and self.above >= SUSTAIN_S:
            self.elevated = True
            self.log(f"Heart rate +{sm - self.baseline:.0f} bpm over baseline for 20 s → Elevated "
                     f"(physiological change, not a diagnosis) [Kim 2018].")
        elif self.elevated and self.below >= SUSTAIN_S:
            self.elevated = False
            self.log(f"Heart rate back near baseline ({sm:.0f} bpm) → Resting [trend back to baseline: Cochrane 2013].")

    def _decide(self):
        playing = self.player.track
        if not self.elevated:
            self._opportunity = None
        if playing:
            self.music_s += 1
        self.elevated_music_s = self.elevated_music_s + 1 if (playing and self.elevated) else 0
        self.resting_s = self.resting_s + 1 if not self.elevated else 0
        if self.stopped or self.nurse:
            return
        if self.t < self.pause_until:
            return
        if playing and self.music_s >= SESSION_CAP_S:
            self.music_s = 0
            self._pause("About 30 min of music → silent pause, then re-evaluate [Zhang 2026].")
        elif playing and self.elevated_music_s >= ELEVATED_MUSIC_S:
            self._pause("Still elevated after 3 min of calm music → trying silence, which lowered HR most "
                        "[Bernardi 2006].")
        elif self.elevated and (not playing or self.player.ended()):
            if playing:
                self.player.track = None
            self._play_next("Elevated" if not playing else "Track ended, still elevated")
        elif playing and not self.elevated and self.resting_s >= RESTING_FADE_S:
            self.player.silence(fade_s=6)
            self.log("Near baseline for 1 min → music fades to silence.")
        elif playing and self.player.ended():
            self.player.track = None

    def _pause(self, why):
        self.player.silence(fade_s=5)
        self.pause_until = self.t + SILENCE_PAUSE_S
        self.log(why)

    def _play_next(self, why):
        if not self.calm:  # library may have been built after start-up
            self.calm = self._load_music()
            if self.calm:
                self.log(f"Music library loaded: {len(self.calm)} calm tracks.")
        pool = self.calm
        if self.selection_enabled:
            pool = [t for t in pool if song_id(t["song_id"]) in self.approved_ids]
        choices = [t for t in pool if t["song_id"] not in self.recent] or pool
        if not choices:
            if not self.warned_empty:
                self.log(f"{why}, but no calm track is available → staying silent.")
                self.warned_empty = True
            return
        if self.selection_enabled:
            self._offer_selection(choices)
            return
        t = choices[0]
        self.recent.append(t["song_id"])
        self.player.play(t)
        arousal = f", predicted arousal {t['deam_pred_arousal']:.1f}/9" if t.get("deam_pred_arousal") else ""
        self.log(f"{why} → playing “{t.get('title') or t['song_id']}” by {t.get('artist') or 'unknown'} "
                 f"({t.get('calm_reason', 'calm list')}{arousal}) from the approved {self.music} list.")

    def snapshot(self):
        with self.lock:
            tr = self.player.track
            return {
                **self._adaptive_snapshot(),
                "input": self.source.label,
                "simulated": bool(getattr(self.source, "interactive_simulation", False)) or self.source.label.startswith("FAKE"),
                "interactive": bool(getattr(self.source, "interactive_simulation", False)),
                "replay": None if not isinstance(self.source, ListSource) else {
                    "t": self.source.i, "n": len(self.source.values), "speed": self.speed, "done": self.done},
                "live": self.source.status() if hasattr(self.source, "status") else None,
                "room": self.room_state(),
                "hr": self.smooth[-1] if self.smooth else None,
                "baseline": self.baseline,
                "threshold": self.thr,
                "history": self.smooth[-300:],
                "baseline_progress": min(len(self._baseline_samples), self.baseline_s),
                "baseline_needed": self.baseline_s,
                "now_playing": None if not tr else {
                    "song_id": song_id(tr["song_id"]), "exposure_id": self._active_exposure,
                    "title": tr.get("title") or tr["song_id"], "artist": tr.get("artist", ""),
                    "bpm": tr["bpm"], "key": tr.get('key_name') or f"{tr.get('key', '')} {tr.get('mode', '')}".strip(),
                    "license": tr.get("license", ""),
                    "why": tr.get("calm_reason", ""), "arousal": tr.get("deam_pred_arousal"),
                    "detail": tr.get('detail') or (f"predicted arousal {tr['deam_pred_arousal']:.1f}/9"
                              if tr.get('deam_pred_arousal') is not None else f"{tr['bpm']:.0f} bpm"),
                    "source": 'Spotify' if self.music == 'spotify' else 'local file'},
                "music": self.music,
                "spotify": self.player.status() if self.music == 'spotify' else None,
                "silent_pause_s": max(0, self.pause_until - self.t),
                "events": list(self.events),
            }


# ---------- console server (optional; the loop does not depend on it) ----------
def serve(ctrl, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            auth = getattr(ctrl, 'spotify_auth', None)
            if parsed.path == "/state":
                self._send(200, json.dumps(ctrl.snapshot(), allow_nan=False).encode(), "application/json")
            elif parsed.path == "/telemetry":
                try:
                    query = parse_qs(parsed.query)
                    batch = ctrl.telemetry(int(query.get('after_seq', ['0'])[0]), query.get('session_id', [None])[0])
                    self._send(200, json.dumps(batch, allow_nan=False).encode(), "application/json")
                except ValueError as error:
                    self._send(409, json.dumps({'error': str(error)}).encode(), "application/json")
            elif parsed.path in ("/", "/index.html"):
                self._send(200, CONSOLE.read_bytes(), "text/html; charset=utf-8")
            elif parsed.path == '/observer.js':
                self._send(200, OBSERVER.read_bytes(), 'application/javascript; charset=utf-8')
            elif parsed.path == '/spotify/login':
                if not auth or not auth.configured:
                    return self._send(400, b'Spotify is not configured. Add the client ID and restart.', 'text/plain')
                self.send_response(302)
                self.send_header('Location', auth.login_url())
                self.end_headers()
            elif parsed.path == '/callback':
                if not auth or not auth.configured:
                    return self._send(400, b'Spotify is not configured.', 'text/plain')
                try:
                    auth.handle_callback(parsed.query)
                except (SpotifyError, ValueError, OSError) as error:
                    ctrl.log('Spotify sign-in failed.')
                    return self._send(400, str(error).encode(), 'text/plain; charset=utf-8')
                ctrl.log('Spotify connected. Keep the Spotify app open on this computer.')
                self._send(200, b'Spotify connected. You can close this tab.', 'text/plain')
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path not in ("/input", "/select", "/upload", "/feedback"):
                return self._send(404, b"not found", "text/plain")
            try:
                n = int(self.headers.get("Content-Length", 0))
                if n > MAX_UPLOAD_BYTES:
                    return self._send(413, b'{"error":"file larger than 5 MB"}', "application/json")
                if n < 0 or (self.path == '/select' and n > 65536):
                    raise ValueError("Invalid request length")
                payload = json.loads(self.rfile.read(n) or b'{}')
                if self.path == "/select":
                    result = ctrl.select(payload)
                elif self.path == '/feedback':
                    result = ctrl.feedback(payload)
                elif self.path == "/upload":
                    name = Path(str(payload.get('name', 'upload.csv'))).name[:80]
                    values, info = parse_hr_csv(str(payload.get('text', '')))
                    dropped = f", {info['dropped']} rows dropped" if info['dropped'] else ''
                    ctrl.load_source(ListSource(values, source_label(name)), speed=payload.get('speed', 1),
                                     note=f" ({info['seconds']} s of heart rate{dropped})")
                    result = info
                else:
                    action = payload.get("action", "")
                    if action == 'source:live':
                        if getattr(ctrl.source, 'live', False) and getattr(ctrl.source, 'device_id', None):
                            ctrl.live_source_spec = 'garmin:'+ctrl.source.device_id
                        live = make_source(getattr(ctrl, 'live_source_spec', 'garmin'))
                        live.on_status = ctrl.log
                        ctrl.load_source(live, speed=1)
                    elif action == 'source:sim':
                        ctrl.load_source(ctrl.sim_source_factory(), speed=1)
                    else:
                        ctrl.input(action)
                    result = {}
                self._send(200, json.dumps(result).encode(), "application/json")
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                self._send(409 if self.path == "/select" else 400,
                           json.dumps({'error': str(error)}).encode(), "application/json")

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ctrl.log(f"Developer / Demo Console at http://127.0.0.1:{srv.server_port} (optional).")
    return srv


def keyboard(ctrl, stop_event):
    """Single keypresses (no Enter) on Windows, macOS and Linux."""
    keys = {"s": "stop", "n": "nurse", "r": "rise", "c": "rest"}

    def handle(k):
        k = k.lower()
        if k == "q":
            stop_event.set()
        elif k in keys:
            ctrl.input(keys[k])

    try:
        import msvcrt
    except ImportError:
        msvcrt = None
    if msvcrt:
        while not stop_event.is_set():
            if msvcrt.kbhit():
                handle(msvcrt.getwch())
            time.sleep(0.05)
        return

    if not sys.stdin.isatty():
        return
    import atexit
    import select
    import termios
    import tty
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    atexit.register(termios.tcsetattr, fd, termios.TCSADRAIN, saved)
    tty.setcbreak(fd)  # Ctrl-C still works
    while not stop_event.is_set():
        if select.select([sys.stdin], [], [], 0.1)[0]:
            handle(sys.stdin.read(1))


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="garmin",
                    help="garmin[:DEVICE_ID] live watch (default); sim; csv:FILE; hku:PARTICIPANT")
    ap.add_argument("--threshold", type=int, default=10)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-console", action="store_true")
    ap.add_argument('--music', choices=('spotify', 'jamendo'), default='jamendo',
                    help='Local Jamendo audio (default); legacy Spotify support is optional')
    ap.add_argument("--speed", type=float, default=1.0, help="replay speed for recorded input")
    ap.add_argument("--baseline-s", type=int, default=BASELINE_S,
                    help="seconds of rest used for the baseline (120 normally; shorter only for demos)")
    selection = ap.add_mutually_exclusive_group()
    selection.add_argument('--adaptive-state', type=Path, help='Enable connected adaptive adapter; checkpoint JSON path')
    selection.add_argument('--external-selection', action='store_true', help='Let an external adapter use /select')
    ap.add_argument('--patient-id', default=None)
    approval = ap.add_mutually_exclusive_group()
    approval.add_argument('--approved-tracks', type=Path, help='JSON {patient_id: {jamendo:song_id: preference}}')
    approval.add_argument('--demo-approve-calm', action='store_true', help='SIMULATION ONLY: approve cached calm tracks')
    approval.add_argument('--approve-calm', action='store_true', help='Approve the available calm catalog for this volunteer')
    ap.add_argument('--sim-response', action='store_true', help='Use deliberately invented patient/song responses')
    ap.add_argument('--no-audio-effect', action='store_true', help='Synthetic control: song has no HR effect')
    ap.add_argument('--seed', type=int, default=42)
    args = ap.parse_args(argv)
    adaptive = bool(args.adaptive_state or args.external_selection)
    live = args.source == 'garmin' or args.source.startswith('garmin:')
    if (args.sim_response or args.no_audio_effect or args.demo_approve_calm) and args.source != 'sim':
        ap.error('Synthetic response flags require --source sim')
    if adaptive and args.source != 'sim' and not live:
        ap.error('Adaptive learning requires simulated or live Garmin input; replay cannot teach new song responses')
    if adaptive and live and not args.patient_id:
        ap.error('Live learning requires --patient-id to keep each volunteer separate')
    if adaptive and live and args.music != 'jamendo':
        ap.error('Live adaptive learning uses --music jamendo')
    if adaptive and not (args.approved_tracks or args.demo_approve_calm or args.approve_calm):
        ap.error('Adaptive mode needs --approved-tracks or explicit --approve-calm')
    args.patient_id = args.patient_id or ('live-volunteer' if live else 'sim-patient-01')
    if adaptive and args.speed != 1:
        ap.error('Adaptive playback requires --speed 1; use offline tests for accelerated simulation')
    if args.speed <= 0 or args.baseline_s < 1:
        ap.error('speed and baseline-s must be positive')
    if args.source.startswith('garmin') and args.speed != 1:
        ap.error('Live Garmin input runs in real time; --speed only applies to recorded files')
    return args


def main():
    args = parse_args()


    source = (ResponseSimSource(args.patient_id, args.seed, args.no_audio_effect)
              if args.sim_response or args.no_audio_effect else make_source(args.source))
    approvals = {}
    if args.approved_tracks:
        approvals = json.loads(args.approved_tracks.read_text()).get(args.patient_id, {})
    elif args.demo_approve_calm or args.approve_calm:
        approvals = {song_id(t['song_id']): t.get('preference', .5) for t in (load_spotify_calm_list() if args.music == 'spotify' else load_calm_list())}
    mode = 'live' if getattr(source, 'live', False) else 'simulated'
    adapter = AdaptiveAdapter(args.adaptive_state, seed=args.seed, catalog=args.music, mode=mode) if args.adaptive_state else None
    auth = SpotifyAuth(args.port) if args.music == 'spotify' else None
    player = SpotifyPlayer(auth) if auth else None
    ctrl = Controller(source, args.threshold, args.baseline_s, patient_id=args.patient_id,
                      adapter=adapter, external_selection=args.external_selection, approved_ids=approvals,
                      music=args.music, player=player)
    ctrl.live_source_spec = args.source if args.source.startswith("garmin") else "garmin"

    if hasattr(source, "on_status"):
        source.on_status = ctrl.log  # live Garmin connection messages go to the event log
        ctrl.log('Live Garmin input: response learning enabled.' if adapter or args.external_selection
                 else 'Live Garmin input: fixed calm-list ranking.')

    if auth:
        player.on_error = ctrl.log
        if not auth.configured:
            ctrl.log('Spotify is not configured. Add resonance/spotify_config.json and restart.')
        elif not auth.connected:
            ctrl.log(f'Open http://127.0.0.1:{args.port} and click Connect Spotify.')
    if args.sim_response or args.no_audio_effect:
        ctrl.sim_source_factory = lambda: ResponseSimSource(args.patient_id, args.seed, args.no_audio_effect)
    if not args.no_console:
        serve(ctrl, args.port)
    stop_event = threading.Event()
    threading.Thread(target=keyboard, args=(ctrl, stop_event), daemon=True).start()
    print("Keys: s = stop/resume, n = nurse in/out, r = simulated rise, c = simulated rest, q = quit", flush=True)
    ctrl.speed = args.speed
    try:
        while not stop_event.is_set():
            start = time.time()
            ctrl.tick()
            time.sleep(max(0, 1.0 / ctrl.speed - (time.time() - start)))
    except KeyboardInterrupt:
        pass
    ctrl.player.silence(fade_s=1)
    if hasattr(source, 'close'):
        source.close()


if __name__ == "__main__":
    main()
