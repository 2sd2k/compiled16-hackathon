# Resonance

An adaptive hospital-room soundscape with **no screen for the patient or nurse**. Heart rate relative to the wearer's own baseline decides whether the room stays quiet or plays a slow, patient-approved calm track. Audio fades out when a nurse is present, and the patient can always say "stop."

Heart rate is context, not a diagnosis. This is a healthy-volunteer demo and claims no clinical effect.

## Easiest way to start: double-click

After cloning the repo (step 1 below), double-click:

- **Mac:** `start-mac.command`. If macOS blocks it, right-click it, choose **Open**, then **Open** again.
- **Windows:** `start-windows.bat`

The first run sets up Python packages and downloads the available Jamendo calm tracks. It then starts Resonance and opens the console in your browser. Keep the window open while testing; press `q` in it or close it to stop. Needs Python 3.12 installed (https://www.python.org/downloads/).

**What the launcher runs:** **live heart rate from Josh's Garmin watch** (see "Live heart rate" below). Start Broadcast Heart Rate on the watch first.

For testing without a watch only: `--simulated` runs Josh's Python heart-rate simulator (`resonance/response_simulator.py`) with **his adaptive layer** (`adaptive_adapter.py`), which picks songs and learns per person how heart rate responded (`ADAPTIVE.md`); press `r` to start an episode, and music follows about 1–1.5 minutes later. `--classic` runs the simple rise/rest simulator with rule-based picks. Simulated responses are invented for testing, not clinical evidence.

## Live heart rate from a Garmin watch

1. On the watch (tested design: Forerunner 265): **Health & Wellness → Wrist Heart Rate → Broadcast Heart Rate → START**.
2. Turn on Bluetooth on the computer. On a Mac, allow the Terminal under **System Settings → Privacy & Security → Bluetooth** when asked.
3. Double-click the launcher, or run `python resonance/launch.py`, for live Garmin readings with fixed calm-list ranking. Enable live learning with `python resonance/launch.py --patient-id volunteer-01 --approve-calm`. Use a different ID for each volunteer. `--approve-calm` approves the available calm catalog; use `--approved-tracks FILE` for a narrower list. `--live` remains an optional explicit alias.

The console shows **Garmin: connected to …** and the age of the last reading. The bridge (`resonance/garmin_source.py`) uses Josh's receiver and decoder (`resonance/garmin-bluetooth/garmin_hr.py`). It finds the watch, reconnects after drop-outs, and gives the controller the latest reading once per second. Stale readings (over 3 s old), no skin contact, or out-of-range values count as "no signal": music fades and no decisions are made until a good reading returns. Readings are never repeated or invented. With several heart-rate sensors nearby, run `python resonance/garmin-bluetooth/garmin_hr.py --scan` and start with `--source garmin:DEVICE_ID`.

Live sessions now use the per-person learner with Jamendo playback. The learner uses a separate live checkpoint, unique Garmin notifications, actual timing checks, and confirmed playback. Stop, nurse presence and signal gaps reject incomplete response windows. Optional comfort ratings in the console take priority; otherwise the reward is explicitly labeled an HR proxy, not a comfort measure or causal effect. Recorded CSV replay never teaches responses to new songs.

If an old simulator is already running, the launcher now reports the mismatch. Stop that backend with Ctrl+C and rerun. `--live --classic` displays live HR with fixed calm-list ranking. Hardware testing on your watch is still required.

## Quick start for teammates (test with your own dataset files)

The working code is on **`main`** (and `chc166/dev`). You need Git and Python 3.12. The double-click launchers above do steps 2–4 for you.

**1. Get the code (once)**

```bash
git clone https://github.com/2sd2k/compiled16-hackathon.git
cd compiled16-hackathon
git checkout chc166/dev
```

Already cloned? Run `git fetch origin` and then `git checkout chc166/dev && git pull`.

**2. Install (once)**

macOS / Linux:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r resonance/requirements.txt
```

Windows (PowerShell):
```powershell
py -3.12 -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r resonance/requirements.txt
```

**3. Download Jamendo audio (once)**

```bash
python resonance/fetch_audio.py
```

**4. Run it**

```bash
python resonance/launch.py --live --patient-id volunteer-01 --approve-calm
```

Open **http://127.0.0.1:8765**. Look for **LIVE Garmin heart rate** and connection status.
The first two minutes of valid readings establish the baseline. Use the optional comfort
rating during confirmed playback. With no watch, `python resonance/launch.py --simulated` starts the
labeled simulated adaptive demo instead. Legacy Spotify code remains opt-in, outside the
live Jamendo workflow.

**5. Test a dataset file**

In the console, go to **Load a heart-rate file**, choose a CSV, pick a speed (4× is good), then click **Load & replay**. Watch the room state and event log, and listen. Try the three examples in `resonance/fake_data/`, or make your own:

```
t,hr
0,72.1
1,71.8
...
```

One row per second, heart rate in bpm. The first 2 minutes are treated as rest (the baseline); music starts when heart rate stays 10+ bpm above it for 20 s. To generate new fake scenarios, edit `SCENARIOS` in `resonance/make_fake_hr.py` and run it. To grade a file's phases without listening, add a matching `_labels.csv` (`start_s,end_s,label`) and run `python resonance/scorecard.py`.

Keys in the controller's terminal: `r` heart rate rises, `c` rests, `n` nurse in/out, `s` stop/resume, `q` quit. Label anything made-up as fake (name it `fake_...csv`) so the console shows **FAKE**.

## Connected simulated response learning

The adaptive integration and observer are documented in [resonance/ADAPTIVE.md](resonance/ADAPTIVE.md).
Both Spotify and local Jamendo audio support this mode, using separate feature
schemas and checkpoints. The example below runs offline Jamendo playback.

```bash
python resonance/fetch_audio.py
python resonance/controller.py --source sim --music jamendo --sim-response --demo-approve-calm --patient-id sim-patient-01 --adaptive-state data/raw/adaptive-patient-01.json
```

Open `http://127.0.0.1:8765` for the response observer. It shows learning updates
and simulated outcomes, including interruption/rejection reasons. The adaptive
benchmark has not demonstrated an advantage over the fixed prior; this mode is
an engineering demonstration, not an HR-lowering claim.

## How it works

```
heart rate (fake data now, Garmin later) → controller → audio in the room
                                             ↑
                        stop button / voice, nurse badge
```

- **Controller** (`resonance/controller.py`) runs the whole loop by itself at 1 Hz:
  - Baseline = median heart rate over the first 120 s of rest.
  - **Elevated** = 10 s median heart rate ≥ baseline + 10 bpm for 20 s (the `hr_test.py` rule).
  - Elevated → play the top-ranked song from the **calm list**, approved in advance by the patient (Cochrane 2013, Zhang 2026).
  - **Spotify (legacy, opt-in):** the 204 Music-Mouv songs with Spotify IDs; calm = felt tempo about 55–70 bpm (Bernardi 2006) and Spotify energy ≤ 0.5, which gives 15 songs (`analysis/build_spotify_library.py`). They're ordered with Josh's `rank_music`, patient preference first, then lowest Spotify energy (Spotify's perceived-intensity score standing in for arousal). Josh's DEAM model needs audio, which Spotify doesn't provide, so it isn't used here. Playback goes through the Spotify app (`resonance/spotify.py`).
  - **Jamendo (default, local playback):** 67 of 590 local tracks are calm (`analysis/classify_calm.py`), ordered by **Josh's DEAM arousal model**; its predictions agree with HKU956 listeners' own arousal ratings (ρ = 0.59). See `BASELINE-INTEGRATION.md`.
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
| `sim` | Labeled simulated heart rate, driven by `r` / `c` |
| `csv:resonance/fake_data/fake_stress_episode.csv` | Fake dataset (`t,hr` at 1 Hz, same format as `hr_test.py`). Also `fake_mild_rise.csv` and `fake_resting_only.csv`, each with a `_labels.csv`. Regenerate with `python resonance/make_fake_hr.py`. |
| `csv:path/to/recording.csv` | Any recorded `t,hr` CSV, such as a Garmin export converted to CSV |
| `hku:hku1903` | A recorded HKU956 session (`app/data/sessions.json`) |
| `garmin` (default) | Live watch broadcast over the standard BLE Heart Rate Service. `garmin:DEVICE_ID` selects a specific sensor. |

`--speed 20` replays recorded input faster; `--no-console` runs with no web page at all.

### Upload a heart-rate file from the console

In the Demo Console, **Load a heart-rate file** → choose a CSV → pick a replay speed (1×, 4×, 10×) → **Load & replay**. The controller resets, learns a fresh baseline from the file's first 2 minutes, and replays it through the same decision loop, so you hear the music respond. **Back to live Garmin** reconnects the selected watch and starts a fresh baseline. Use `--simulated` at startup for the key-driven test feed.

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
