# Datasets named in PRODUCT-BRIEF.md and SCOPE-REVIEW.md

The original product objective is a physiological-response experiment. DEAM's
emotion regressor is auxiliary and does not satisfy that objective by itself.
The following inspection uses actual official archive contents. No papers are
used as training rows and no DEAM/HKU/Music-Mouv IDs are arbitrarily joined.

## HKU956: inspected, acoustic training blocked

[Official source](https://doi.org/10.25442/hku.21080821), Hu, Li & Liu (2022),
[paper](https://doi.org/10.3390/app12189354); CC BY-NC 4.0.

The downloaded v4 archive contains HR series and a song URL manifest, not MP3s.
The linked `ccmir.cite.hku.hk` host failed DNS resolution over HTTP and HTTPS
in this environment. `hku_audit.json` records the archive checksum and measured
coverage. A 60-second requirement retains only 235 of 956 HR records, so a
60-second target must not silently be applied to the whole dataset. The audit
checks basic finite/positive HR only, not validated wearable signal quality.
No acoustic-response model is trained from unavailable audio or invented labels.

Reproduce without committing raw archives:

```sh
python baseline/download_data.py hku --directory /tmp/music-study-data
python baseline/inspect_hku.py --archive /tmp/music-study-data/HKU956.zip
# Or use the newly supplied archive directly:
python baseline/inspect_hku.py --archive baseline/HKU956.zip
```

## Music-Mouv: separate exploratory HR experiment

[Creator dataset and study citations](https://homepages.loria.fr/gbonnin/music-mouv/).
The raw JSON release supplies per-trial music metadata alongside physiological
series; we use those raw files, not the workshop HRV summary CSVs. The latter
are not pre/post-HR labels. Raw archives and subject traces are not redistributed
by this change. Confirm applicable source permissions before further reuse;
no new license for those data is implied.

```sh
python baseline/download_data.py music-mouv --directory /tmp/music-study-data
python baseline/train_hr.py --archive /tmp/music-study-data/music-mouv.zip
pytest -q baseline/test_hr.py
```

This experiment predicts **within-trial HR change** in BPM:
`mean(HR at seconds [30,40)) - mean(HR at seconds [0,10))`.
Time zero means the first available HR sample of that trial, not verified music
onset. Neither interval is claimed to be unexposed resting baseline. Ten valid
1 Hz samples are required in both windows; missing outcomes are excluded, never
imputed. Trials without music metadata and missing/nonpositive HR are counted
in the audit. No interpolation, synthetic response, or lower-HR success label
is introduced. Basic completeness checks cannot establish clinical signal quality.

Inputs available by the end of the first window are its mean HR and fitted HR
slope. The audio covariates are eight supplied whole-track Spotify descriptors,
listed explicitly in `train_hr.py`; they do not reproduce the brief's six proposed
excerpt-level features. Track IDs, participant IDs, questionnaires, later HR,
walk outcomes and Spotify valence are excluded from predictors. Music features
are whole-track proxies, not verified extraction over the listening window.

Fixed seed 42 splits entire participants approximately 70/15/15. Independently,
a second evaluation splits entire Spotify track IDs to probe unfamiliar tracks;
participants can overlap in that second evaluation. Neither evaluation holds
out both factors simultaneously. Ridge regularization is chosen separately for
state-only, audio-only and state-plus-audio models on validation MAE from
{1,10,100,1000}; median imputation/scaling are fitted on train only. Test compares
them with a no-change baseline (always predict a 0 BPM change) and the
training-mean baseline. The no-change prediction requires no fitting or access
to outcome labels. No model is refitted on validation/test.

`hr_metrics.json` records exclusions, counts, selection, MAE/R² and a seeded
1,000-resample held-out-group bootstrap interval for combined-minus-state MAE.
A positive difference means adding audio worsens error. Few held-out groups
limit uncertainty estimates. `hr_participant_splits.csv` and
`hr_track_id_splits.csv` record assignments. `hr_model.joblib` contains the
participant-held-out state-plus-audio model for reproducibility, whether or not
it outperforms state-only. Its presence does not imply successful audio prediction.

For inference, supply the ten numeric input columns in a CSV (HR in BPM; prior
slope in BPM/second; music fields in the source representation):

```sh
python baseline/train_hr.py --predict candidate_features.csv --model baseline/hr_model.joblib --output hr_predictions.csv
```

The outcome is observational. Walking and recovery confound HR; initial HR is
also mathematically coupled to the change target. An initial-state control is
essential, and a lower error or predicted drop cannot establish a causal music
effect. Before response-driven ranking, obtain verified exposure timing,
movement-aware reference windows and prospective participant-level evaluation.
The simulated controller stays separate from this offline experiment.

## Measured results from this run

209 trials from 30 participants and 179 Spotify track IDs passed the stated
checks. Exclusions: 71 trials without a track ID, 28 with invalid/short HR
sampling, and 2 with missing/nonpositive HR in the required windows.
The participant split contains 135/33/41 trials from 20/5/5 participants;
the separate track split contains 151/28/30 trials from 125/27/27 tracks.

| Model | Participant-held-out MAE (BPM) | R² | Track-held-out MAE (BPM) | R² |
| --- | ---: | ---: | ---: | ---: |
| No change (predict 0 BPM change) | 6.417 | -0.0039 | 3.956 | -0.141 |
| Training mean | 6.339 | -0.0005 | 3.872 | -0.103 |
| State only | 4.607 | 0.550 | 3.673 | 0.035 |
| Audio only | 6.363 | -0.003 | 3.883 | -0.110 |
| State plus audio | 4.774 | 0.526 | 3.972 | -0.131 |

Adding these music covariates **does not improve held-out MAE** over initial
state alone in either evaluation. The cluster-bootstrap 95% interval for
combined-minus-state MAE is [-0.262, 0.538] BPM across held-out participants and
[-0.079, 0.740] BPM across held-out tracks. This small observational experiment
does not establish that music has no physiological effect. It does not support
a claim that these audio features improve HR-response prediction.

The state-only model also beats the no-change baseline in both evaluations.
The state-plus-audio model beats no-change on held-out participants but is
slightly worse on held-out tracks (3.972 versus 3.956 BPM MAE). The comparison
does not establish that music causes the observed changes. Reproduction and
test evidence for the added control is in [NO_CHANGE_VALIDATION.md](NO_CHANGE_VALIDATION.md).
