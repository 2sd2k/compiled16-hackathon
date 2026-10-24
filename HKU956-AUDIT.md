# HKU956 go/no-go audit (PRODUCT-BRIEF.md, order of work step 1)

Status: October 24, 2026. Archive downloaded and every record inspected. No model trained yet.

**Verdict: conditional GO.** Paired, aligned audio + physiology exists for all 956 listening records, and the audio is still obtainable. But the dataset has **no pre-exposure or silence reference**, records are **short (median 44 s)**, and listening was **free-choice browsing**, so the training target must be a within-exposure HR change, and the model must be described as predicting in-song HR drift, not "settling toward rest."

## What is in the archive

Source: [HKU956 on HKU DataHub](https://doi.org/10.25442/hku.21080821), version 4, `HKU956.zip` (9.7 MB, 33 MB unzipped).

| Item | Finding |
| --- | --- |
| Participants / records | 30 / 956; 16–68 records per participant (median 30); no gaps in `song_no` |
| Signals per record | Empatica E4: HR 1 Hz, BVP 64 Hz, EDA 4 Hz, TEMP 4 Hz, IBI (intermittent) |
| File format | One headerless column of values per file, **no timestamps**. IBI files are bare intervals with no time offsets. |
| Labels | `3. AV_ratings.csv`: self-rated valence and arousal (−10…10) and `play_duration` per record |
| Integrity | All 956 rated records have signal files; no orphan files; HR, BVP, EDA, TEMP lengths agree within 2 s; HR length equals `play_duration` for every record |
| HR quality | No samples outside 40–200 bpm |
| IBI quality | No intervals outside 0.3–2.0 s. Coverage (sum of IBIs / record length): median 55%, 25th percentile 32%; 539/956 records reach ≥50% |
| Audio | `2. original_song_audio.csv` links 592 tracks to `ccmir.cite.hku.hk`, **which no longer exists** (DNS: non-existent domain). The IDs are Jamendo track IDs and stream from Jamendo: **590/592 available** (2 return 404; see `analysis/out/jamendo_availability.csv`). Spot checks match real tracks (e.g. 370177 = Marijanh, "Red is the Rose"). |
| Stimulus diversity | 592 unique tracks; 155 heard by ≥2 participants (max 11) |

## Protocol (from the paper's methods)

Hu, Li & Liu (2022), *Applied Sciences* 12, 9354, https://doi.org/10.3390/app12189354

- Participants searched and browsed a music web app (Moody) for ≥40 minutes in a quiet room, choosing songs they liked for a playlist. They could play, pause and skip freely. Tracks came from a 10K Jamendo collection (GC14UX, CC-BY).
- A rating pop-up appeared only for songs played >30 s, so all records are ≥30 s. Average play time was about 50 s.
- The wristband was put on 2 minutes before a pre-experiment questionnaire "for signal stabilization and baseline acquisition", but **that baseline is not in the released data**. Only per-song chunks were released, cut at the logged start and end of each play.
- The paper does not say which part of each track was played. Assume playback started at 0:00 unless seeks were logged (they are not released).

## Alignment and reference windows

- **Within a record:** alignment is good. Every signal chunk spans exactly the logged play, so the excerpt is the first `play_duration` seconds of the track (with the start-at-0:00 assumption above).
- **No pre-exposure reference.** Every window is already exposed to the song. Following the brief, an early in-song window must be labeled as exposed, not as baseline.
- **Previous record as reference: approximate only.** Without timestamps I compared jumps across song boundaries with drift inside a song. HR jumps across boundaries are about 2× the 10 s within-song change (median 1.08 vs 0.55 bpm; 90th percentile 4.6 vs 2.0). Skin temperature shows the same pattern (0.016 vs 0.008 °C). So consecutive songs have **unrecorded gaps of unknown length** (searching, surveys), and the previous song's tail was recorded during different music.

## Is there signal to predict?

Candidate target: mean HR over the last 10 s minus mean HR over the first 10 s of each record.

- Mean −0.11 bpm, SD 4.1 bpm, 5th–95th percentile −6.0 to +6.2 bpm. 137/956 records change by ≥5 bpm.
- So there is variation to explain, but most of it may be noise, posture, typing and clicking, or carry-over from the previous song. The constant and initial-state-only baselines in the brief are essential.

## License

- **Physiology and ratings:** CC BY-NC 4.0. Non-commercial use is fine for the hackathon; credit the authors and cite the paper above.
- **Audio:** Jamendo tracks under Creative Commons licenses. The paper says CC-BY, but Jamendo licenses vary per track (some NC/ND), so check each track's license before redistributing audio or putting it in the demo. Don't commit audio files to the repo.

## Implications for step 2

1. **Target:** within-exposure ΔHR (last 10 s − first 10 s), only for records ≥30 s (all of them). Optional sensitivity version: previous-record tail as an approximate pre-reference, flagged as having an unknown gap and different music.
2. **Audio features:** compute the six feature groups on the first `play_duration` seconds of each Jamendo track, matching what was heard.
3. **Evaluation:** leave-participant-out against constant-change and initial-state-only predictors; report leave-song-out separately (155 repeated tracks make this possible).
4. **HRV:** RMSSD only on the ~539 records with ≥50% IBI coverage, and IBIs have no offsets, so treat RMSSD as approximate. HR is the primary outcome.
5. **Confounds to state:** self-selected, liked songs; active computer use while listening (not lying still); healthy students; short exposures. None of this matches a hospital patient.

## Reproduce

```bash
python -I analysis/inspect_hku956.py data/raw/HKU956
```
```bash
python -I analysis/check_jamendo_audio.py data/raw/HKU956
```
Outputs go to `analysis/out/` (per-record CSV, summary JSON, Jamendo availability CSV). Raw data lives in `data/raw/` (gitignored).
