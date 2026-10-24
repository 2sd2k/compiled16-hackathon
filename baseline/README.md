# DEAM acoustic emotion baseline

Reproducible regression of perceived **arousal and valence**, using the files
already committed under `data/deam`. This is an auxiliary music representation
baseline, separate from the HR-response objective in `PRODUCT-BRIEF.md`.

## Run

Python 3.12 was used. From the repository root:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r baseline/requirements.txt
python baseline/train.py
pytest -q baseline/test_baseline.py
python baseline/predict.py --input data/deam/deam_song_level_features.csv --output predictions.csv
```

The inference example predicts the whole table for convenience; it is **not** a
held-out evaluation. Use `test_predictions.csv` and `metrics.json` for that.
For new songs supply the same 520 acoustic columns in the same extraction and
aggregation representation; column order is restored automatically. Targets
are unnecessary and ignored at inference. This project does not implement raw
audio extraction. Unknown extra input columns are ignored by inference; missing
required features, nonnumeric features, and infinities are rejected. NaNs are
imputed from training medians. Predictions are raw rating estimates, unclipped
for metrics; only the ranking distance clips to [1,9].

## Data and leakage controls

- 1,802 unique song IDs, 520 acoustic features, no missing feature values.
- Embedded `valence_mean`, `arousal_mean`, and both annotator standard deviations
  are annotations, never predictors. `song_id` is also excluded.
- Both averaged static annotation CSVs inside `annotations.zip` are read directly
  without extraction and checked one-to-one against the feature table, including
  annotation standard deviations. Dynamic and per-rater files are not used.
- Targets are static **1–9 ratings**, ordered arousal then valence. No mixing of
  dynamic annotation scales, per-second windows, or invented research labels.
- Strict acoustic column naming rejects unexpected metadata at training; numeric
  target-copy/affine-copy checks reject near-perfect correlations. These checks
  do not prove absence of all upstream extraction leakage or mislabeled audio.
- Fixed seed 42, group shuffle split approximately 70/15/15. Four repeated
  acoustic vectors exist. Identical vectors stay together even if song IDs differ,
  giving 1,261 train / 271 validation / 270 test songs. No rows are discarded.
- Imputation/scaling/models are fitted on training only. Ridge alpha in
  {1,10,100,1000} and one prespecified ExtraTrees configuration are compared by
  average validation MAE. Test is evaluated only for the selected model and the
  training-mean baseline. The saved model stays fitted on training only.

The split does not control artist, album, near-duplicate recordings, annotator,
or source catalog overlap: that metadata is unavailable. Song-held-out results
are not evidence of generalization to patients or clinical outcomes.

## Held-out test results

| Model | Arousal MAE | Arousal R² | Valence MAE | Valence R² |
| --- | ---: | ---: | ---: | ---: |
| Training mean | 1.054 | -0.046 | 0.980 | -0.001 |
| ExtraTrees, selected on validation | 0.679 | 0.538 | 0.681 | 0.446 |

These are point estimates on 270 songs from one fixed split, not confidence
intervals. Full validation comparisons, exact software versions, source file
SHA-256 checksums, audit, and test metrics are in `metrics.json`. `splits.csv`
records every song assignment; `test_predictions.csv` enables metric inspection.
`model.joblib` stores fitted preprocessing, feature schema, targets, and model.
Only load trusted joblib files: deserialization can execute code. The pinned
environment, fixed seed, and single-threaded forest support reproducibility;
bitwise identical artifacts across platforms are not guaranteed.

## Optional ranking and simulated controller

Run from `baseline/` or add it to Python's module path:

```python
from ranking import rank_music, simulated_hr_hook

# Synthetic HR input, explicitly a demo; not a physiological response model.
hook = simulated_hr_hook(85, 70, simulated=True)
# predictions = predict(...).to_dict('records') from predict.py
candidates = [{'song_id': 2, 'predicted_arousal_mean': 3.2,
               'predicted_valence_mean': 6.0}]
if hook['action'] == 'rank':
    ranked = rank_music(candidates, preferences={2: 1.0}, excluded=[],
                        target_arousal=hook['target_arousal'],
                        target_valence=hook['target_valence'])
```

Preference values [0,1] rank first, then squared affect distance to configurable
low-arousal/positive-valence targets; unknown preference defaults to 0.5 and is
marked unknown. Explicit exclusions always win. This is a transparent heuristic,
not validated personalization. Stop/discomfort, care presence, and invalid
signals produce stop/pause/hold; the caller must honor these actions. There is
no playback or live wearable integration. See [research constraints](RESEARCH.md).

## Provenance and terms

The [derived feature release](https://github.com/asher0913/deam-song-level-features)
describes 520 aggregated openSMILE descriptors and label columns. The acoustic
CSV checksum is recorded in the audit and matches its published checksum.
[DEAM](https://cvml.unige.ch/databases/DEAM/) is described by Aljanaki, Yang &
Soleymani, *Developing a benchmark for emotional analysis of music*, PLOS ONE,
2017, [doi:10.1371/journal.pone.0173392](https://doi.org/10.1371/journal.pone.0173392).
Also cite Eyben et al., *Recent developments in openSMILE*, ACM Multimedia, 2013.
The derived release identifies DEAM's Attribution–NonCommercial terms. Verify
upstream terms for intended use, including derived models; this change grants
no new data/model license and redistributes no audio.

## Physiological data referenced in the product documents

See [the archive inspection and separate HR experiment](PHYSIOLOGY.md).
`inspect_hku.py` audits HKU956; `train_hr.py` benchmarks Music-Mouv within-trial
HR change with participant- and track-held-out evaluation. These datasets stay
separate from DEAM. The uploaded DEAP paper is documentation, not paired
physiological training data. The canonical DEAM inputs remain `data/deam`, even
if copies also exist in `baseline/`.
