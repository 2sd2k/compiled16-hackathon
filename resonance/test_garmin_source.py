"""Offline tests for the live Garmin bridge: simulated Bluetooth packets, no watch or Bluetooth needed.

Run: python -m pytest -q resonance/test_garmin_source.py
"""
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import controller as c  # noqa: E402
from garmin_source import FRESH_S, GarminSource  # noqa: E402


def packet(bpm, contact=None):
    """Standard Heart Rate Measurement bytes: flags then 8-bit bpm (contact flags optional)."""
    flags = 0 if contact is None else (6 if contact else 4)
    return bytes([flags, bpm])


class FakePlayer:
    def __init__(self):
        self.track, self.on_event, self.plays, self.silenced = None, None, 0, 0

    def play(self, track, exposure_id=None):
        self.track, self.plays = track, self.plays + 1

    def silence(self, fade_s=0):
        self.track, self.silenced = None, self.silenced + 1

    def ended(self):
        return False


def test_fresh_valid_reading_is_returned():
    src = GarminSource(connect=False)
    assert math.isnan(src.next())  # nothing received yet
    src.on_packet(packet(72))
    assert src.next() == 72.0 and src.readings == 1


def test_bad_readings_become_nan_never_none():
    src = GarminSource(connect=False)
    for data in (packet(72, contact=False), packet(0), packet(250)):
        src.on_packet(data)
        value = src.next()
        assert value is not None and math.isnan(value)


def test_malformed_packets_are_ignored():
    src = GarminSource(connect=False)
    src.on_packet(packet(70))
    src.on_packet(b"\x01\x48")  # truncated 16-bit value
    assert src.next() == 70.0 and src.readings == 1


def test_stale_reading_is_not_repeated():
    src = GarminSource(connect=False)
    src.on_packet(packet(75))
    bpm, contact, _ = src._latest
    src._latest = (bpm, contact, time.monotonic() - FRESH_S - 1)
    assert math.isnan(src.next())
    assert src.status()["last_reading_age_s"] > FRESH_S


def test_make_source_builds_live_garmin_source(monkeypatch):
    monkeypatch.setattr(GarminSource, "_run", lambda self: None)  # don't touch Bluetooth
    src = c.make_source("garmin:AA-BB")
    assert isinstance(src, GarminSource) and src.device_id == "AA-BB"
    assert not getattr(src, "interactive_simulation", False)  # adaptive learner stays off on live data


def test_controller_runs_on_live_packets_and_pauses_on_signal_loss():
    src, player = GarminSource(connect=False), FakePlayer()
    ctrl = c.Controller(src, 10, player=player, quiet=True, music="spotify")
    assert not ctrl.selection_enabled
    for _ in range(120):  # resting baseline from the watch
        src.on_packet(packet(70))
        ctrl.tick()
    assert ctrl.baseline == 70
    for _ in range(40):  # sustained rise
        src.on_packet(packet(90))
        ctrl.tick()
    assert ctrl.elevated and player.plays == 1 and player.track is not None
    snap = json.loads(json.dumps(ctrl.snapshot(), allow_nan=False))  # console state must serialize
    assert snap["replay"] is None and snap["live"]["readings"] == 160 and snap["room"] == "Elevated"
    bpm, contact, _ = src._latest  # watch stops sending: reading goes stale
    src._latest = (bpm, contact, time.monotonic() - FRESH_S - 1)
    ctrl.tick()
    assert player.track is None and player.silenced >= 1 and not ctrl.signal_ok and not ctrl.done


def test_garmin_receiver_csv_time_column_is_used():
    text = "timestamp,elapsed_s,hr,sensor_contact,sample_index,session_id\n" + "\n".join(
        f"2026-10-24T00:00:{i:02d}Z,{i * 0.5},{70 + i % 2},True,{i},s" for i in range(80))
    values, info = c.parse_hr_csv(text)
    assert info["seconds"] == 40  # half-second notifications averaged to 1 Hz, not 80 rows as 80 s
