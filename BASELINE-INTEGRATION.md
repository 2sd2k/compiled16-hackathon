# Integrating Josh's `baseline/` into Resonance

Status: October 24, 2026. Analysis done, the useful part absorbed on `chc166/dev`.

## What `baseline/` contains

| Piece | What it does | Absorbed? |
| --- | --- | --- |
| DEAM emotion model (`train.py`, `predict.py`, `data.py`) | ExtraTrees predicting perceived arousal and valence (1–9) from 520 openSMILE features. Held-out test: arousal R² 0.54, valence R² 0.45. | **Arousal: yes**, as the ranking signal within the calm list. **Valence: no** (see below). |
| `ranking.py` `rank_music` | Patient preference first, exclusions always win, then distance to a calm arousal/valence target | **Yes**, with a small change (`valence_weight`) |
| `ranking.py` `simulated_hr_hook` | Stateless stop/pause/hold/rank decision | No. It refuses real telemetry by design, which would block the Garmin input, and the controller already handles stop, nurse and elevated states. |
| HR-response model (`train_hr.py`, Music-Mouv) | Predicts within-trial HR change | No. Josh's own results show audio features don't beat initial-state-only, and it needs Spotify descriptors our tracks don't have. |

## How it was checked

1. **Reproduced training** with Josh's `train.py`; the controller never loads the committed `model.joblib`, since unpickling can run code. Test results match closely: arousal MAE 0.70 vs his 0.68, valence 0.70 vs 0.68. The split differs by one song (1,260/272/270 vs 1,261/271/270).
2. **Computed his 520 features for our 590 Jamendo tracks** (`analysis/deam_features.py`): openSMILE ComParE_2016 low-level descriptors and deltas, 0.5 s windows, first 45 s. The 2016 config renames MFCCs, so they're mapped back. Domain shift is small: per feature, a median 5.6% of our tracks fall outside DEAM's 1–99% range. The worst are delta-MFCC spread features (up to 62%), likely from the 2013-vs-2016 config.
3. **External validation against HKU956 listeners** (`analysis/validate_deam_on_hku.py`): Spearman correlation between predictions and the mean self-reported rating for the same songs. Those ratings were never used in training.

| Predictor | vs listener arousal (590 songs) | vs listener arousal (154 songs, 2+ raters) | vs listener valence (590 songs) |
| --- | ---: | ---: | ---: |
| **Josh predicted arousal** | **0.59** [0.53, 0.64] | **0.68** [0.57, 0.77] | 0.05 [−0.03, 0.12] |
| Josh predicted valence | 0.60 | 0.71 | 0.05 [−0.03, 0.12] |
| Brightness (ours) | 0.50 | 0.66 | 0.02 |
| Loudness (ours) | 0.46 | 0.58 | 0.01 |
| Onset density (ours) | 0.33 | 0.46 | 0.03 |
| Calm-list label (ours) | −0.19 | −0.27 | −0.04 |

Cross-validated R² for listener arousal: our three simple features 0.30, plus predicted arousal 0.34, predicted arousal alone 0.33.

**Reading it:**
- Predicted arousal agrees with how real listeners rated these songs, better than any single feature we had, so it's a reasonable way to order the calm list.
- Predicted valence carries no valence information here and mostly tracks arousal. Ranking toward "positive valence 6.5", as `rank_music` does by default, would push toward *more* energetic tracks, so the controller sets `valence_weight=0`.
- The calm list matches listener ratings: calm tracks averaged −1.1 arousal vs +1.4 for the rest, on HKU's −10..10 scale.
- This is perceived emotion, not heart-rate response. It doesn't show that lower predicted arousal lowers anyone's heart rate.

## Changes made

- `baseline/ranking.py`: new `valence_weight` argument (default 1.0, so Josh's behavior and tests are unchanged). Josh's tests: 16 pass, 1 fails, the same one as before this change (see below).
- `resonance/controller.py`: the evidence-based calm list still decides *which* tracks are eligible. `rank_music(..., target_arousal=2.5, valence_weight=0)` decides the *order*, using precomputed `deam_pred_arousal` from the library, so there's no ML at demo time. Optional `resonance/patient_preferences.json` (gathered by voice or a caregiver) ranks first, per Josh's design.
- Replaced my hand-made "texture" and HKU-observed-HR tie-breakers, which were weaker.

## Still needs fixing in `baseline/` (for Josh)

1. **Split depends on the pandas version.** `split_indices` groups by `pd.util.hash_pandas_object`, whose values changed between pandas 2.2.3 (pinned) and 3.1. On 3.1 the split shifts by a song, and `test_saved_metrics_and_training_only_scaling` fails. Group by a stable fingerprint, such as a SHA-256 of each row's rounded feature bytes.
2. **No raw-audio feature extraction.** `analysis/deam_features.py` now does it, approximately (ComParE_2016 rather than DEAM's 2013 config, track starts rather than DEAM's mid-song clips).
3. **HKU956 audio is not unavailable.** The original host is dead, but 590/592 tracks stream from Jamendo by the same IDs (`analysis/out/jamendo_availability.csv`).
