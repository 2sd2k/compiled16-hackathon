"""Live Garmin heart rate as a controller source (--source garmin or garmin:DEVICE_ID).

Bridges Josh's standalone receiver (garmin-bluetooth/garmin_hr.py) into the controller: a
background thread finds the watch's standard Bluetooth Heart Rate broadcast, decodes packets
with his parse_measurement(), and reconnects after drop-outs. The controller calls next() once
per second and gets the latest fresh reading, or NaN when there is none (stale, no skin contact,
zero or out of range). next() never returns None (that would end the session) and never repeats
an old reading as new.

On the watch: Health & Wellness > Wrist Heart Rate > Broadcast Heart Rate > START.
Live data is distinct from interactive simulation. An explicitly configured live adapter
learns from unique notifications; classic mode keeps fixed calm-list ranking.
"""
import asyncio
import math
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "garmin-bluetooth"))
from garmin_hr import MEASUREMENT, SERVICE, parse_measurement  # noqa: E402  (Josh's decoder)

FRESH_S = 3.0          # a reading older than this is not "now"
RECONNECT_S = 5.0      # wait between scan/connect attempts
STALE_DISCONNECT_S = 10.0  # Josh's receiver default: no notifications for this long = reconnect
HR_RANGE = (30, 220)


class GarminSource:
    live = True

    def __init__(self, device_id=None, connect=True, on_status=None):
        self.device_id = device_id
        self.on_status = on_status or (lambda msg: None)
        self.label = "LIVE Garmin heart rate"
        self.state, self.device_name, self.error = "starting", "", ""
        self._last_problem = None
        self.readings = 0
        self._consumed = 0
        self.sample_metadata = {}
        self._latest = None  # (bpm, sensor_contact, monotonic time)
        self._lock = threading.Lock()
        self._closed = threading.Event()
        if connect:
            threading.Thread(target=self._run, daemon=True).start()

    # --- controller interface ---
    def set(self, mode):
        pass  # rise/rest are simulator controls; a real watch ignores them

    def next(self):
        with self._lock:
            latest = self._latest
            self.sample_metadata = {}
            if self.readings == self._consumed:
                return math.nan
            self._consumed = self.readings
            if latest is not None:
                self.sample_metadata = {'measurement_id': self.readings, 'received_at': latest[2]}
        if latest is None:
            return math.nan
        bpm, contact, at = latest
        if time.monotonic() - at > FRESH_S or contact is False or not HR_RANGE[0] <= bpm <= HR_RANGE[1]:
            return math.nan
        return float(bpm)

    def close(self):
        self._closed.set()
        with self._lock:
            self._latest = None

    def status(self):
        with self._lock:
            latest = self._latest
        age = None if latest is None else round(time.monotonic() - latest[2], 1)
        return {"state": self.state, "device": self.device_name, "error": self.error,
                "device_id": self.device_id,
                "readings": self.readings, "last_reading_age_s": age,
                "sensor_contact": None if latest is None else latest[1]}

    # --- Bluetooth ---
    def on_packet(self, data):
        try:
            m = parse_measurement(bytes(data))
        except ValueError:
            return  # malformed packet: ignored, never turned into a reading
        with self._lock:
            self._latest = (m["hr"], m["sensor_contact"], time.monotonic())
            self.readings += 1

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

    def _run(self):
        asyncio.run(self._loop())

    async def _loop(self):
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError:
            self._set_state("error", "Garmin: Bluetooth library missing (pip install -r resonance/requirements.txt).",
                            "bleak not installed")
            return
        while not self._closed.is_set():
            try:
                with self._lock:
                    self._latest = None
                self._set_state("scanning", "Garmin: looking for the watch's heart-rate broadcast...")
                found = await BleakScanner.discover(timeout=8, return_adv=True, service_uuids=[SERVICE])
                devices = [(d, a) for d, a in found.values()
                           if not self.device_id or d.address.casefold() == self.device_id.casefold()]
                if not devices:
                    self._set_state("not_found", "Garmin: no broadcasting watch found. On the watch: Health & "
                                    "Wellness > Wrist Heart Rate > Broadcast Heart Rate > START.", "no watch found")
                    await asyncio.sleep(RECONNECT_S)
                    continue
                if len(devices) > 1 and not self.device_id:
                    names = ", ".join(f"{a.local_name or d.name or '?'} ({d.address})" for d, a in devices)
                    self._set_state("choose", f"Garmin: several heart-rate sensors found ({names}). "
                                    "Start with --source garmin:DEVICE_ID.", "several sensors found")
                    await asyncio.sleep(RECONNECT_S)
                    continue
                device, advert = devices[0]
                self.device_id = device.address  # Keep this volunteer bound to the same sensor on reconnect.
                self.device_name = advert.local_name or device.name or device.address
                self.label = f"LIVE Garmin heart rate ({self.device_name})"
                dropped = asyncio.Event()
                async with BleakClient(device, disconnected_callback=lambda _: dropped.set()) as client:
                    connected_at = time.monotonic()
                    await client.start_notify(MEASUREMENT, lambda _c, data: self.on_packet(data))
                    self._set_state("connected", f"Garmin: connected to {self.device_name}; live heart rate flowing.")
                    while not dropped.is_set() and not self._closed.is_set():
                        await asyncio.sleep(0.5)
                        with self._lock:
                            last = self._latest[2] if self._latest else None
                        if time.monotonic() - (last if last is not None else connected_at) > STALE_DISCONNECT_S:
                            break
                with self._lock:
                    self._latest = None
                if not self._closed.is_set():
                    self._set_state("reconnecting", "Garmin: watch connection lost; reconnecting (no readings "
                                    "are repeated meanwhile).", "connection lost")
            except Exception as e:  # Bluetooth off, permissions, adapter errors
                with self._lock:
                    self._latest = None
                reason = e.args[0] if e.args and isinstance(e.args[0], str) else str(e)
                self._set_state("error", f"Garmin: Bluetooth error: {reason} (Mac: allow the terminal under "
                                "Privacy & Security > Bluetooth). Retrying every 5 s.", reason[:160])
            await asyncio.sleep(RECONNECT_S)
