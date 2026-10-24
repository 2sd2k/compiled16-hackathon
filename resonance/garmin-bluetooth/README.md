# Standalone Garmin heart-rate receiver for Mac

These are new standalone files. They do not import, modify, or send data to the existing controller or adaptive layer. Copy `garmin_hr.py` anywhere, or add it as a new file under `resonance/` later.

## Setup (Python 3.10+)

Open Terminal in this folder and run:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install bleak==3.0.2
python garmin_hr.py --scan
```

Before scanning, wear your Forerunner 265 and hold UP → Health & Wellness → Wrist Heart Rate → Broadcast Heart Rate → START. Enable Bluetooth on the Mac. Allow the terminal/app Bluetooth access when prompted, or under System Settings → Privacy & Security → Bluetooth. Restart the terminal after changing permissions if necessary.

The scan lists heart-rate sensor names and identifiers. Copy your watch's exact identifier (a UUID on Mac), then run:

```sh
python garmin_hr.py --device 'PASTE-UUID-HERE'
```

To also create a CSV (new filename required):

```sh
python garmin_hr.py --device 'PASTE-UUID-HERE' --csv garmin-session.csv
```

Ctrl-C stops recording and disconnects. `--duration 120` ends after approximately two minutes, including connection time. Standard output contains one JSON object per notification; scan/status/errors go to standard error. Optional CSV contains UTC arrival time, elapsed seconds, HR, sensor contact, sample index and session identity. Each notification is recorded once; notification intervals are not assumed to be one second. RR intervals are decoded only when supplied by the sensor; availability is not guaranteed.

No sensors? Confirm broadcasting is actually started, bring the watch closer, check Mac Bluetooth permissions, and close other apps currently connected to its HR broadcast. Try `--scan-seconds 20`. Bluetooth being enabled for Garmin Connect alone does not enable HR broadcasting. Device identifiers on Mac are not hardware MAC addresses.

The receiver exits on disconnect or 10 seconds without a valid notification (`--stale-seconds` changes this). It never repeats the last HR as a new sample. Re-run to reconnect; a new session ID is created. Zero HR and contact-loss indications are preserved rather than interpreted as valid patient response evidence. Downstream integration must explicitly reject unsuitable samples and preserve gaps.

## Scope and verification

This is a live receiver and logger, not controller integration. It does not select music or update patient preferences. CSV output is not claimed to match the controller upload schema. A future bridge can use the JSON records without changing this receiver, but controller integration is separate work.

Run offline parser checks with `python -m unittest -v test_garmin_hr.py`. Packet decoding is tested with 8/16-bit HR, contact flags, energy, RR intervals and malformed packets. A real Forerunner/Mac connection still requires testing on your hardware; it has not been verified here. No background Bluetooth scanning starts until you run the script.

Protocol references: [Bluetooth Heart Rate Service](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/HRS_v1.0/out/en/index-en.html), [Bleak client](https://bleak.readthedocs.io/en/latest/api/client.html), [Bleak macOS guidance](https://bleak.readthedocs.io/en/latest/backends/macos.html), [Forerunner 265 broadcast instructions](https://www8.garmin.com/manuals/webhelp/GUID-F41EAFB3-6CC9-42DE-9C6C-9E358DBB0671/EN-US/GUID-D8D363C2-0690-48D4-95E2-A3557E7D53C2.html).
