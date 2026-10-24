# Resonance operator console

A local web page for testing the pipeline end to end: it replays heart rate, runs the evidence agent, and plays the music the agent picks.

- **1 · Heart-rate input:** replays a recorded HKU956 session (or a clearly labeled simulated stress episode). It applies the `hr_test.py` rule: 10 s rolling median, baseline = median of the first 120 s, "elevated" when HR ≥ baseline + threshold for 20 s. The threshold slider covers 6–15 bpm.
- **2 · Evidence agent:** decides between silence, keep playing, or starting a calm track, and lists every rule it checked with its source. Includes a patient **Stop** button and a simulated **Nurse present** button (fade out and pause).
- **3 · Music library:** the 590 Jamendo tracks from HKU956, with tempo, key/mode, loudness, dynamics, brightness, bass/mid/treble balance, onsets per second, observed HKU956 HR drift, and per-track license. The calm list (55–70 bpm by default) is the patient's choice set; the "Approved" checkboxes stand in for the patient's picks.
- **4 · Evidence:** the studies behind the rules, with findings and caveats (`data/evidence.json`).

The recorded heart rate **does not respond** to the audio chosen here. It was recorded while participants listened to their own picks, so the console shows decisions, not effects.

## Run it

```bash
python -m http.server 8765 --bind 127.0.0.1 --directory app
```
Then open http://127.0.0.1:8765. Click **Start replay** once; browsers only allow audio after a click.

## Plugging in the filtered dataset (David, Luke, Josh)

Save your filtered records as `data/filtered/records.csv` with at least these columns:

```
participant_id,song_no,song_id
hku1903,0,262957
...
```

An optional `keep` column (1/0) drops rows without deleting them. Then rebuild the replay data and reload the page:

```bash
python -I analysis/build_app_data.py data/raw/HKU956
```

This needs the raw HKU956 archive unzipped at `data/raw/HKU956` (gitignored; see `HKU956-AUDIT.md`). Without a filtered file, all 956 audited records are used.

## Rebuilding the music library

```bash
python -I analysis/build_music_library.py data/raw/HKU956
```

This downloads the first ~90 s of each track to `data/raw/audio/` (gitignored, about 800 MB) and writes `app/data/music_library.json`. It needs `numpy` and `librosa`. Tempo comes from onset autocorrelation because `librosa.beat.beat_track` crashes on Windows here; it can read at double or half time, and tracks with unsteady tempo are flagged and kept off the calm list.
