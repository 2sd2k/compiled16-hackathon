"""Live Garmin heart rate as a controller source (--source garmin or garmin:DEVICE_ID).

Runs Josh's standalone receiver (garmin-bluetooth/garmin_hr.py) as a separate process, exactly as
he runs it, and reads the JSON reading it prints for each Bluetooth notification. Keeping
Bluetooth in its own process avoids macOS CoreBluetooth reporting "unsupported" when it is
driven from a background thread inside the controller. If the receiver exits (watch dropped,
no fresh readings), the bridge rescans and restarts it.

The controller calls next() once per second and gets the latest fresh reading, or NaN when there
is none (stale, no skin contact, zero or out of range). next() never returns None (that would end
the session) and never repeats an old reading as new.

On the watch: Health & Wellness > Wrist Heart Rate > Broadcast Heart Rate > START.
Live data is not interactive simulation, so the adaptive learner stays off by design; the
controller uses its regular evidence-based ranking.
"""
import json
import math
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "garmin-bluetooth"))
from garmin_hr import parse_measurement  # noqa: E402  (Josh's decoder, used for raw packets)

RECEIVER = Path(__file__).resolve().parent / "garmin-bluetooth" / "garmin_hr.py"
FRESH_S = 3.0          # a reading older than this is not "now"
RECONNECT_S = 5.0      # wait between scan/connect attempts
HR_RANGE = (30, 220)


class GarminSource:
    def __init__(self, device_id=None, connect=True, on_status=None, receiver=RECEIVER, python=sys.executable):
        self.device_id = device_id
        self.on_status = on_status or (lambda msg: None)
        self.receiver, self.python = Path(receiver), python
        self.label = "LIVE Garmin heart rate"
        self.state, self.device_name, self.error = "starting", "", ""
        self._last_problem = None
        self.readings = 0
        self._latest = None  # (bpm, sensor_contact, monotonic time)
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._proc = None
        if connect:
            threading.Thread(target=self._run, daemon=True).start()

    # --- controller interface ---
    def set(self, mode):
        pass  # rise/rest are simulator controls; a real watch ignores them

    def next(self):
        with self._lock:
            latest = self._latest
        if latest is None:
            return math.nan
        bpm, contact, at = latest
        if time.monotonic() - at > FRESH_S or contact is False or not HR_RANGE[0] <= bpm <= HR_RANGE[1]:
            return math.nan
        return float(bpm)

    def close(self):
        self._closed.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def status(self):
        with self._lock:
            latest = self._latest
        age = None if latest is None else round(time.monotonic() - latest[2], 1)
        return {"state": self.state, "device": self.device_name, "error": self.error,
                "readings": self.readings, "last_reading_age_s": age,
                "sensor_contact": None if latest is None else latest[1]}

    # --- readings ---
    def _store(self, bpm, contact):
        with self._lock:
            self._latest = (bpm, contact, time.monotonic())
            self.readings += 1

    def on_packet(self, data):
        """Raw Heart Rate Measurement bytes (decoded with Josh's parser)."""
        try:
            m = parse_measurement(bytes(data))
        except ValueError:
            return  # malformed packet: ignored, never turned into a reading
        self._store(m["hr"], m["sensor_contact"])

    def on_record(self, line):
        """One JSON line printed by garmin_hr.py for each notification."""
        try:
            record = json.loads(line)
            bpm = int(record["hr"])
        except (ValueError, KeyError, TypeError):
            return
        self._store(bpm, record.get("sensor_contact"))

    # --- status messages ---
    def _set_state(self, state, msg=None, error=""):
        """Log transitions once; a retry loop (scan -> same problem -> scan) stays quiet."""
        prev, problems = self.state, ("error", "not_found", "choose")
        self.state, self.error = state, error
        if not msg or (state == "scanning" and prev in problems):
            return
        if state in problems:
            if msg == self._last_problem:
                return
            self._last_problem = msg
        elif state == "connected":
            self._last_problem = None
        self.on_status(msg)

    @staticmethod
    def _receiver_error(stderr_lines):
        for line in stderr_lines:
            if line.startswith("Error:"):
                return line[len("Error:"):].strip()
        return ""

    # --- receiver process ---
    def _scan(self):
        """Run garmin_hr.py --scan; returns [(address, name)] or raises RuntimeError with its error."""
        result = subprocess.run([self.python, str(self.receiver), "--scan", "--scan-seconds", "8"],
                                capture_output=True, text=True, timeout=60)
        lines = result.stderr.splitlines()
        error = self._receiver_error(lines)
        if error:
            raise RuntimeError(error)
        return [tuple(line.split("\t", 1)) for line in lines if "\t" in line]

    def _run(self):
        while not self._closed.is_set():
            try:
                self._set_state("scanning", "Garmin: looking for the watch's heart-rate broadcast...")
                found = self._scan()
                if self.device_id:
                    found = [d for d in found if d[0].casefold() == self.device_id.casefold()]
                if not found:
                    self._set_state("not_found", "Garmin: no broadcasting watch found. On the watch: Health & "
                                    "Wellness > Wrist Heart Rate > Broadcast Heart Rate > START.", "no watch found")
                elif len(found) > 1 and not self.device_id:
                    names = ", ".join(f"{name} ({addr})" for addr, name in found)
                    self._set_state("choose", f"Garmin: several heart-rate sensors found ({names}). "
                                    "Start with --source garmin:DEVICE_ID.", "several sensors found")
                else:
                    self._stream(*found[0])
            except Exception as e:  # receiver missing/crashed, Bluetooth off, permissions
                reason = e.args[0] if e.args and isinstance(e.args[0], str) else str(e)
                self._set_state("error", f"Garmin: {reason} (Mac: System Settings > Privacy & Security > "
                                "Bluetooth, allow Terminal). Retrying every 5 s.", reason[:200])
            self._closed.wait(RECONNECT_S)

    def _stream(self, address, name):
        self.device_name = name or address
        self.label = f"LIVE Garmin heart rate ({self.device_name})"
        self._proc = subprocess.Popen([self.python, str(self.receiver), "--device", address],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        stderr_lines = []
        threading.Thread(target=lambda: stderr_lines.extend(self._proc.stderr), daemon=True).start()
        for line in self._proc.stdout:  # one JSON object per notification
            if self.state != "connected":
                self._set_state("connected", f"Garmin: connected to {self.device_name}; live heart rate flowing.")
            self.on_record(line)
            if self._closed.is_set():
                break
        self._proc.wait()
        if self._closed.is_set():
            return
        time.sleep(0.2)  # let the stderr reader finish
        reason = self._receiver_error(l.strip() for l in stderr_lines) or "receiver stopped"
        if self.state != "connected":  # never got a reading: report the receiver's own error
            raise RuntimeError(f"watch connection failed: {reason}")
        self._set_state("reconnecting", f"Garmin: watch connection ended ({reason}); reconnecting "
                        "(no readings are repeated meanwhile).", reason[:200])
