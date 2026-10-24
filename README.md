# Resonance

An adaptive hospital-room soundscape with **no screen for the patient or nurse**. Heart rate relative to the wearer's own baseline decides whether the room stays quiet or plays a slow, patient-approved calm track. Audio fades out when a nurse is present, and the patient can always say "stop."

Heart rate is context, not a diagnosis. This is a healthy-volunteer demo and claims no clinical effect.

## How it works

```
heart rate (fake data now, Garmin later) → controller → audio in the room
                                             ↑
                        stop button / voice, nurse badge
```

- **Controller** (`resonance/controller.py`) runs the whole loop by itself, offline, at 1 Hz:
  - Baseline = median heart rate over the first 120 s of rest.
  - **Elevated** = 10 s median heart rate ≥ baseline + 10 bpm for 20 s (the `hr_test.py` rule).
  - Elevated → play the top-ranked track from the **calm list**: slow (about 55–70 bpm felt tempo, Bernardi 2006) and sparse or ambient tracks, approved in advance by the patient (Cochrane 2013, Zhang 2026). 67 of 590 tracks qualify (`analysis/classify_calm.py`).
  - Within the calm list, tracks are ordered by **Josh's DEAM arousal model** through his `rank_music` (patient preference first, then lowest predicted arousal). The predictions agree with HKU956 listeners' own arousal ratings (ρ = 0.59). See `BASELINE-INTEGRATION.md`.
  - Still elevated after 3 min of music → 2 min of silence, which lowered heart rate most in Bernardi 2006.
  - Back near baseline for 1 min → music fades to silence. Music sessions are capped at about 30 min (Zhang 2026).
  - **Nurse present** → fade out, resume after they leave. **Stop** → instant silence until the patient resumes.
- **Developer / Demo Console** (`resonance/console.html`, served by the controller) is for judges and developers only. It shows heart rate, baseline, room state, what's playing and an event log, and its buttons only simulate real-world inputs. The product runs with it closed.
- **Music library** (`app/data/music_library.json`): 590 Creative Commons tracks from Jamendo (the HKU956 study's songs), each with tempo, key/mode, loudness, dynamics, brightness, bass/mid/treble balance, note density and license. Audio is cached locally in `data/raw/audio/` so the demo doesn't need internet.
- **Evidence** behind every rule is in `app/data/evidence.json`, and the event log cites it.

## Run the demo

```bash
python resonance/controller.py
```

Open http://127.0.0.1:8765 for the console (optional). Keys in the controller window stand in for the room's real inputs:

| Key | Stands in for |
| --- | --- |
| `s` | Patient says "stop" (voice or physical button); press again to resume |
| `n` | Nurse enters / leaves (BLE badge) |
| `r` / `c` | Simulated heart rate rises / returns to rest |
| `q` | Quit |

The baseline needs 2 minutes of rest, so start the controller at least 2 minutes before presenting. For a quick rehearsal, `--baseline-s 30` shortens it and the log says so.

### Heart-rate inputs

| `--source` | Input |
| --- | --- |
| `sim` (default) | Labeled simulated heart rate, driven by `r` / `c` |
| `csv:resonance/fake_data/fake_stress_episode.csv` | Fake dataset (`t,hr` at 1 Hz, same format as `hr_test.py`). Also `fake_mild_rise.csv` and `fake_resting_only.csv`, each with a `_labels.csv`. Regenerate with `python resonance/make_fake_hr.py`. |
| `csv:path/to/recording.csv` | Any recorded `t,hr` CSV, such as a Garmin export converted to CSV |
| `hku:hku1903` | A recorded HKU956 session (`app/data/sessions.json`) |
| `garmin` | **Not connected yet.** Planned: the watch's "broadcast heart rate" mode (standard BLE Heart Rate Service) |

`--speed 20` replays recorded input faster; `--no-console` runs with no web page at all.

### Upload a heart-rate file from the console

In the Demo Console, **Load a heart-rate file** → choose a CSV → pick a replay speed (1×, 4×, 10×) → **Load & replay**. The controller resets, learns a fresh baseline from the file's first 2 minutes, and replays it through the same decision loop, so you hear the music respond. **Back to simulated input** returns to the key-driven feed.

- Format: a header row with `t,hr` (seconds, bpm), or a single `hr` / `heart_rate` / `bpm` / `pulse` column at one reading per second. Up to 5 MB, at least 30 s.
- Cleaning (same as `hr_test.py`): readings outside 40–200 bpm are dropped, data is averaged to 1 Hz, gaps up to 5 s are filled with the last value, and longer gaps are skipped. The console reports what was dropped.
- Files whose names start with `fake` are labeled **FAKE** in the console. Uploading is a test control standing in for the wearable feed.

### Scorecard on the fake datasets

```bash
python resonance/scorecard.py
```

Runs the controller silently over each labeled fake file and reports, per phase, how much of the time music played and whether heart rate was flagged elevated. It also reports how soon music started after the rise, false alarms at rest, and a threshold sweep (+6 to +15 bpm). This checks that the system acts correctly and on time. Fake heart rate doesn't respond to music, so it says nothing about effect.

### 2-minute demo flow

1. Wearer at rest: console shows **Resting**, room is silent.
2. Press `r` (or "Heart rate rises"): about 30 s later, **Elevated** → a calm track fades in.
3. Press `n`: audio fades out (**Nurse present**). Press `n` again: it returns.
4. Press `s`: instant silence (**Stopped**).
5. Close on the business case.

## Setup

Python 3.12 with `pygame` (audio), plus `numpy`, `pandas`, `librosa` for the data scripts:

```bash
pip install pygame numpy pandas librosa
```

The audio cache and raw dataset are gitignored. To rebuild them, see `HKU956-AUDIT.md` (download HKU956 to `data/raw/HKU956`), then:

```bash
python -I analysis/build_music_library.py data/raw/HKU956
```

## Data and licenses

- Physiology: HKU956 (Hu, Li & Liu 2022), CC BY-NC 4.0.
- Music: Jamendo tracks under each track's Creative Commons license (listed per track in the library). Audio files are not committed.
- Filtered records from the data team go in `data/filtered/records.csv` (`participant_id,song_no,song_id`); `python -I analysis/build_app_data.py data/raw/HKU956` rebuilds the replay sessions.
