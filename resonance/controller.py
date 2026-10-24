"""Resonance controller: heart rate -> decision -> audio, with no screen required.

The loop runs on its own once started. Keys in this window stand in for the room's real inputs:
    s  patient "stop" (physical button / voice stand-in); press again to resume
    n  nurse enters / leaves (BLE badge stand-in)
    r  simulated heart-rate rise      c  simulated return to rest      q  quit
The Developer / Demo Console (http://127.0.0.1:8765) only shows state and sends the same
simulated inputs. Closing it changes nothing.

Usage:
    python resonance/controller.py                      simulated heart rate (labeled)
    python resonance/controller.py --source csv:FILE    replay a t,hr CSV at 1 Hz (hr_test.py format)
    python resonance/controller.py --source hku:hku1903 replay a recorded HKU956 session
    --music spotify (default) plays calm Music-Mouv songs through the Spotify app (see spotify.py);
    --music jamendo plays the offline local Jamendo files instead.
"""
import argparse
import collections
import csv
import io
import json
import os
import random
import statistics
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import pygame  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
LIBRARY = REPO / "app" / "data" / "music_library.json"
SPOTIFY_LIBRARY = REPO / "app" / "data" / "spotify_library.json"
SESSIONS = REPO / "app" / "data" / "sessions.json"
AUDIO_DIR = REPO / "data" / "raw" / "audio"
CONSOLE = Path(__file__).resolve().parent / "console.html"
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
T_COLUMNS = ("t", "time", "seconds", "time_s")


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
        # Planned live input: the watch's "broadcast heart rate" mode sends the standard BLE Heart Rate
        # Service (0x180D), readable with a BLE library such as bleak. Implement next() to return the
        # latest bpm once per second; everything downstream stays the same.
        raise SystemExit("Garmin input is not connected yet. Use --source sim or csv:FILE for now.")
    if kind == "hku":
        sessions = json.loads(SESSIONS.read_text())["sessions"]
        s = next(x for x in sessions if x["participant_id"] == arg)
        return ListSource(s["hr"], f"Recorded HKU956 session {arg}")
    raise SystemExit(f"unknown source {spec}")


# ---------- music ----------
def _prefs():
    prefs = json.loads(PREFS.read_text()) if PREFS.exists() else {}
    return prefs.get("preferences", {}), [str(s) for s in prefs.get("excluded", [])]


def load_calm_list(music="jamendo"):
    return load_spotify_calm_list() if music == "spotify" else load_jamendo_calm_list()


def load_spotify_calm_list():
    """Calm Spotify tracks from Music-Mouv (analysis/build_spotify_library.py), played via the Spotify app.

    Ranked with Josh's rank_music: patient preference first, then lowest Spotify energy (its
    perceptual intensity, standing in for arousal; mapped to the 1-9 scale). Valence is off.
    """
    if not SPOTIFY_LIBRARY.exists():
        return []
    tracks = json.loads(SPOTIFY_LIBRARY.read_text(encoding="utf-8"))["tracks"]
    prefs, excluded = _prefs()
    cands = [{**t, "song_id": t["track_id"], "bpm": t["tempo"], "license": "Spotify",
              "predicted_arousal_mean": 1 + 8 * t["energy"],
              "predicted_valence_mean": 1 + 8 * (t["valence"] if t["valence"] is not None else 0.5),
              "detail": f"{t['key_name']}, Spotify energy {t['energy']:.2f}"}
             for t in tracks if t.get("calm")]
    return rank_music(cands, {str(k): float(v) for k, v in prefs.items()}, set(excluded),
                      target_arousal=1.0, valence_weight=0)


def load_jamendo_calm_list():
    """Calm list (see analysis/classify_calm.py) with a local file, so the demo works offline."""
    if not LIBRARY.exists():
        return []
    tracks = json.loads(LIBRARY.read_text(encoding="utf-8"))["tracks"]
    calm = [t for t in tracks if t.get("calm") and (AUDIO_DIR / f"{t['song_id']}.mp3").exists()]
    if not calm:
        return []
    for t in calm:
        arousal = f", predicted arousal {t['deam_pred_arousal']:.1f}/9" if t.get("deam_pred_arousal") else ""
        t["detail"] = f"{t['key']} {t['mode']}{arousal}"
    if any(t.get("deam_pred_arousal") is None for t in calm):
        return sorted(calm, key=lambda t: t.get("felt_bpm", t["bpm"]))  # slower first (Bernardi 2006)

    # Order within the calm list with Josh's preference-first ranking (baseline/ranking.py), using
    # his DEAM model's predicted arousal. Valence is off: predicted valence did not track listener
    # valence on this library (analysis/out/deam_on_hku_validation.json).
    prefs, excluded = _prefs()
    cands = [{**t, "song_id": int(t["song_id"]), "predicted_arousal_mean": t["deam_pred_arousal"],
              "predicted_valence_mean": t["deam_pred_valence"]} for t in calm]
    ranked = rank_music(cands, {int(k): float(v) for k, v in prefs.items() if str(k).isdigit()},
                        {int(s) for s in excluded if s.isdigit()}, target_arousal=TARGET_AROUSAL,
                        valence_weight=0)
    return [{**t, "song_id": str(t["song_id"])} for t in ranked]


class Player:
    def __init__(self):
        pygame.mixer.init()
        self.track = None
        self._lock = threading.Lock()

    def _ramp(self, target, seconds):
        start = pygame.mixer.music.get_volume()
        steps = max(1, int(seconds / 0.05))
        for i in range(1, steps + 1):
            pygame.mixer.music.set_volume(start + (target - start) * i / steps)
            time.sleep(0.05)

    # Fades run on a worker thread so the control loop never waits on audio.
    def play(self, track):
        self.track = track
        self._started = time.time()
        threading.Thread(target=self._play, args=(track,), daemon=True).start()

    def _play(self, track):
        with self._lock:
            if pygame.mixer.music.get_busy():
                self._ramp(0, 2.5)
            if self.track is not track:  # superseded while fading out
                return
            pygame.mixer.music.load(str(AUDIO_DIR / f"{track['song_id']}.mp3"))
            pygame.mixer.music.set_volume(0)
            pygame.mixer.music.play()
            self._ramp(VOLUME, 5)

    def silence(self, fade_s=3.0):
        self.track = None
        threading.Thread(target=self._silence, args=(fade_s,), daemon=True).start()

    def _silence(self, fade_s):
        with self._lock:
            if self.track is not None:  # a newer play() won
                return
            if pygame.mixer.music.get_busy():
                self._ramp(0, fade_s)
            pygame.mixer.music.stop()

    def ended(self):
        # Allow time for the fade-in thread to start playback before calling a track finished.
        return (self.track is not None and time.time() - getattr(self, "_started", 0) > 10
                and not pygame.mixer.music.get_busy())


# ---------- controller ----------
class Controller:
    def __init__(self, source, threshold, baseline_s=BASELINE_S, player=None, quiet=False, music="jamendo"):
        self.source, self.thr, self.baseline_s = source, threshold, baseline_s
        self.quiet, self.music = quiet, music
        self.player = player or Player()
        self.calm = load_calm_list(music)
        self.lock = threading.Lock()
        self.events = collections.deque(maxlen=60)
        self.speed = 1.0
        self._reset()
        if music == "spotify":
            self.rank_note = "ranked by lowest Spotify energy via Josh's rank_music"
            what = f"{len(self.calm)} Spotify tracks from Music-Mouv, slow (~{CALM_BPM[0]}-{CALM_BPM[1]} bpm felt) and low energy"
        else:
            self.rank_note = "ranked by Josh's arousal model, checked against HKU956 listener ratings"
            what = f"{len(self.calm)} local Jamendo tracks, slow (~{CALM_BPM[0]}-{CALM_BPM[1]} bpm felt tempo) and sparse"
        self.log(f"Started. Input: {source.label}. Calm list: {what} [Bernardi 2006].")
        if not self.calm:
            self.log("No calm tracks available: the room will stay silent. Build the music library first.")

    def _reset(self):
        """Fresh run: new baseline, silent room, no stop/nurse carried over."""
        self.hr, self.smooth = [], []
        self.baseline = None
        self.elevated = False
        self.above = self.below = 0
        self.stopped = self.nurse = False
        self.paused_track = None
        self.music_s = self.elevated_music_s = self.resting_s = 0
        self.pause_until = -1
        self.recent = collections.deque(maxlen=3)
        self.t = 0
        self.done = False
        self.warned_empty = False

    def load_source(self, source, speed=1.0, note=""):
        """Swap the heart-rate input (console upload or back to simulated) and start over."""
        with self.lock:
            self.player.silence(fade_s=1.5)
            self.source, self.speed = source, max(0.5, min(20.0, float(speed)))
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
            if action == "stop":
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
                    if getattr(self, "paused_track", None) and not self.stopped:
                        self.player.play(self.paused_track)
            elif action in ("rise", "rest"):
                if isinstance(self.source, SimSource):
                    self.source.set(action)
                    self.log(f"[simulated input] heart rate {'rising' if action == 'rise' else 'returning to rest'}.")
            elif action.startswith("threshold:"):
                self.thr = max(6, min(15, int(action.split(":")[1])))
                self.log(f"[test] threshold set to +{self.thr} bpm.")

    def room_state(self):
        if self.stopped:
            return "Stopped"
        if self.nurse:
            return "Nurse present"
        if self.baseline is None:
            return "Learning baseline"
        return "Elevated" if self.elevated else "Resting"

    def tick(self):
        v = self.source.next()
        if v is None:
            if not self.done:
                self.done = True
                self.log("Recorded input ended.")
            return
        with self.lock:
            self.t += 1
            self.hr.append(v)
            self.smooth.append(statistics.median(self.hr[-SMOOTH_S:]))
            if self.baseline is None and len(self.hr) >= self.baseline_s:
                self.baseline = statistics.median(self.hr[:self.baseline_s])
                self.log(f"Baseline set: {self.baseline:.0f} bpm (median of first {self.baseline_s} s"
                         f"{'' if self.baseline_s == BASELINE_S else ', shortened for demo'}).")
            if self.baseline is None:
                return
            self._update_elevated()
            self._decide()

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
            self.calm = load_calm_list()
            if self.calm:
                self.log(f"Music library loaded: {len(self.calm)} calm tracks.")
        choices =[t for t in self.calm if t["song_id"] not in self.recent] or self.calm
        if not choices:
            if not self.warned_empty:
                self.log(f"{why}, but no calm track is available → staying silent.")
                self.warned_empty = True
            return
        t = choices[0]
        self.recent.append(t["song_id"])
        self.player.play(t)
        self.log(f"{why} → playing “{t.get('title') or t['song_id']}” by {t.get('artist') or 'unknown'} "
                 f"({t.get('calm_reason', 'calm list')}; {t.get('detail', '')}) from the approved calm list "
                 f"[slow, calm music: Bernardi 2006; patient-approved: Cochrane 2013; {self.rank_note}].")

    def snapshot(self):
        with self.lock:
            tr = self.player.track
            return {
                "input": self.source.label,
                "simulated": isinstance(self.source, SimSource) or self.source.label.startswith("FAKE"),
                "interactive": isinstance(self.source, SimSource),
                "replay": None if isinstance(self.source, SimSource) else {
                    "t": self.source.i, "n": len(self.source.values), "speed": self.speed, "done": self.done},
                "room": self.room_state(),
                "hr": self.smooth[-1] if self.smooth else None,
                "baseline": self.baseline,
                "threshold": self.thr,
                "history": self.smooth[-300:],
                "baseline_progress": min(len(self.hr), self.baseline_s),
                "baseline_needed": self.baseline_s,
                "now_playing": None if not tr else {
                    "title": tr.get("title") or tr["song_id"], "artist": tr.get("artist", ""),
                    "why": tr.get("calm_reason", ""), "detail": tr.get("detail", ""),
                    "source": "Spotify" if self.music == "spotify" else "local file"},
                "music": self.music,
                "spotify": self.player.status() if hasattr(self.player, "status") else None,
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

        def _page(self, code, title, body):
            html = (f"<!doctype html><meta charset=utf-8><title>{title}</title>"
                    f"<body style='font:16px system-ui;padding:32px;max-width:640px'><h2>{title}</h2>{body}"
                    f"<p><a href='/'>Back to the Demo Console</a></p></body>")
            self._send(code, html.encode(), "text/html; charset=utf-8")

        def do_GET(self):
            path, _, query = self.path.partition("?")
            auth = getattr(ctrl, "spotify_auth", None)
            if path == "/state":
                self._send(200, json.dumps(ctrl.snapshot()).encode(), "application/json")
            elif path in ("/", "/index.html"):
                self._send(200, CONSOLE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/spotify/login":
                if not auth or not auth.configured:
                    return self._page(400, "Spotify is not set up yet",
                                      "<p>Add your Spotify app's Client ID to <code>resonance/spotify_config.json</code> "
                                      "(see README), then restart the controller.</p>")
                self.send_response(302)
                self.send_header("Location", auth.login_url())
                self.end_headers()
            elif path == "/callback" and auth:
                try:
                    auth.handle_callback(query)
                except Exception as e:  # SpotifyError or network failure
                    ctrl.log(f"Spotify sign-in failed: {e}")
                    return self._page(400, "Spotify sign-in failed", f"<p>{e}</p>")
                ctrl.log("Spotify connected. Keep the Spotify app open on this computer.")
                self._page(200, "Spotify connected", "<p>Keep the Spotify app open on this computer. "
                                                     "You can close this tab.</p>")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            if n > MAX_UPLOAD_BYTES:
                return self._send(413, json.dumps({"error": "file larger than 5 MB"}).encode(), "application/json")
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/input":
                action = body.get("action", "")
                if action == "source:sim":
                    ctrl.load_source(SimSource(), speed=1.0)
                else:
                    ctrl.input(action)
                return self._send(200, b"{}", "application/json")
            if self.path == "/upload":
                # Test control: replay an uploaded heart-rate CSV (a stand-in for the wearable feed).
                name = Path(str(body.get("name", "upload.csv"))).name[:80]
                try:
                    values, info = parse_hr_csv(str(body.get("text", "")))
                except ValueError as e:
                    return self._send(400, json.dumps({"error": str(e)}).encode(), "application/json")
                dropped = f", {info['dropped']} rows dropped" if info["dropped"] else ""
                ctrl.load_source(ListSource(values, source_label(name)), speed=body.get("speed", 1),
                                 note=f" ({info['seconds']} s of heart rate{dropped})")
                return self._send(200, json.dumps(info).encode(), "application/json")
            self._send(404, b"not found", "text/plain")

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ctrl.log(f"Developer / Demo Console at http://127.0.0.1:{port} (optional).")


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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="sim")
    ap.add_argument("--threshold", type=int, default=10)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-console", action="store_true")
    ap.add_argument("--music", choices=("spotify", "jamendo"), default="spotify",
                    help="spotify: Music-Mouv songs via the Spotify app (default); jamendo: offline local files")
    ap.add_argument("--speed", type=float, default=1.0, help="replay speed for recorded input")
    ap.add_argument("--baseline-s", type=int, default=BASELINE_S,
                    help="seconds of rest used for the baseline (120 normally; shorter only for demos)")
    args = ap.parse_args()

    auth = player = None
    if args.music == "spotify":
        from spotify import SpotifyAuth, SpotifyPlayer
        auth = SpotifyAuth(args.port)
        player = SpotifyPlayer(auth)
    ctrl = Controller(make_source(args.source), args.threshold, args.baseline_s, player=player, music=args.music)
    if auth:
        ctrl.spotify_auth, player.on_error = auth, ctrl.log
        if not auth.configured:
            ctrl.log("Spotify not set up: add a Client ID to resonance/spotify_config.json (see README). "
                     "Until then the room stays silent.")
        elif not auth.connected:
            ctrl.log(f"Spotify not connected yet: open http://127.0.0.1:{args.port} and click Connect Spotify.")
        else:
            ctrl.log("Spotify connected. Keep the Spotify app open on this computer.")
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
            time.sleep(max(0, 1.0 / ctrl.speed - (time.time() - start)))  # speed can change on upload
    except KeyboardInterrupt:
        pass
    ctrl.player.silence(fade_s=1)


if __name__ == "__main__":
    main()
