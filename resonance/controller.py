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
"""
import argparse
import collections
import csv
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


def make_source(spec):
    if spec == "sim":
        return SimSource()
    kind, _, arg = spec.partition(":")
    if kind == "csv":
        with open(arg, newline="") as f:
            vals = [float(r["hr"]) for r in csv.DictReader(f) if r.get("hr")]
        name = Path(arg).name
        return ListSource(vals, f"FAKE dataset {name}" if name.startswith("fake") else f"Recorded CSV {name}")
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
    preferences = {int(k): float(v) for k, v in prefs.get("preferences", {}).items()}
    excluded = {int(s) for s in prefs.get("excluded", [])}
    cands = [{**t, "song_id": int(t["song_id"]), "predicted_arousal_mean": t["deam_pred_arousal"],
              "predicted_valence_mean": t["deam_pred_valence"]} for t in calm]
    ranked = rank_music(cands, preferences, excluded, target_arousal=TARGET_AROUSAL, valence_weight=0)
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
    def __init__(self, source, threshold, baseline_s=BASELINE_S):
        self.source, self.thr, self.baseline_s = source, threshold, baseline_s
        self.player = Player()
        self.calm = load_calm_list()
        self.lock = threading.Lock()
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
        self.log(f"Started. Input: {source.label}. Calm list: {len(self.calm)} local tracks, slow "
                 f"(~{CALM_BPM[0]}-{CALM_BPM[1]} bpm felt tempo) and sparse [Bernardi 2006].")
        if not self.calm:
            self.log("No calm tracks available: the room will stay silent. Build the music library first.")

    def log(self, msg):
        self.events.appendleft({"time": time.strftime("%H:%M:%S"), "msg": msg})
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
        arousal = f", predicted arousal {t['deam_pred_arousal']:.1f}/9" if t.get("deam_pred_arousal") else ""
        self.log(f"{why} → playing “{t.get('title') or t['song_id']}” by {t.get('artist') or 'unknown'} "
                 f"({t.get('calm_reason', 'calm list')}; {t['key']} {t['mode']}{arousal}) from the "
                 f"approved calm list [slow, sparse music: Bernardi 2006; patient-approved: Cochrane 2013; "
                 f"ranked by Josh's arousal model, checked against HKU956 listener ratings].")

    def snapshot(self):
        with self.lock:
            tr = self.player.track
            return {
                "input": self.source.label,
                "simulated": isinstance(self.source, SimSource) or self.source.label.startswith("FAKE"),
                "interactive": isinstance(self.source, SimSource),
                "room": self.room_state(),
                "hr": self.smooth[-1] if self.smooth else None,
                "baseline": self.baseline,
                "threshold": self.thr,
                "history": self.smooth[-300:],
                "baseline_progress": min(len(self.hr), self.baseline_s),
                "baseline_needed": self.baseline_s,
                "now_playing": None if not tr else {
                    "title": tr.get("title") or tr["song_id"], "artist": tr.get("artist", ""),
                    "bpm": tr["bpm"], "key": f"{tr['key']} {tr['mode']}", "license": tr.get("license", "")},
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
            if self.path == "/state":
                self._send(200, json.dumps(ctrl.snapshot()).encode(), "application/json")
            elif self.path in ("/", "/index.html"):
                self._send(200, CONSOLE.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/input":
                return self._send(404, b"not found", "text/plain")
            n = int(self.headers.get("Content-Length", 0))
            action = json.loads(self.rfile.read(n) or b"{}").get("action", "")
            ctrl.input(action)
            self._send(200, b"{}", "application/json")

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ctrl.log(f"Developer / Demo Console at http://127.0.0.1:{port} (optional).")


def keyboard(ctrl, stop_event):
    try:
        import msvcrt
    except ImportError:
        return
    keys = {"s": "stop", "n": "nurse", "r": "rise", "c": "rest"}
    while not stop_event.is_set():
        if msvcrt.kbhit():
            k = msvcrt.getwch().lower()
            if k == "q":
                stop_event.set()
            elif k in keys:
                ctrl.input(keys[k])
        time.sleep(0.05)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="sim")
    ap.add_argument("--threshold", type=int, default=10)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-console", action="store_true")
    ap.add_argument("--speed", type=float, default=1.0, help="replay speed for recorded input")
    ap.add_argument("--baseline-s", type=int, default=BASELINE_S,
                    help="seconds of rest used for the baseline (120 normally; shorter only for demos)")
    args = ap.parse_args()

    ctrl = Controller(make_source(args.source), args.threshold, args.baseline_s)
    if not args.no_console:
        serve(ctrl, args.port)
    stop_event = threading.Event()
    threading.Thread(target=keyboard, args=(ctrl, stop_event), daemon=True).start()
    print("Keys: s = stop/resume, n = nurse in/out, r = simulated rise, c = simulated rest, q = quit", flush=True)
    period = 1.0 / args.speed
    try:
        while not stop_event.is_set():
            start = time.time()
            ctrl.tick()
            time.sleep(max(0, period - (time.time() - start)))
    except KeyboardInterrupt:
        pass
    ctrl.player.silence(fade_s=1)


if __name__ == "__main__":
    main()
