# No-change HR comparison

User request: add the comparison that always predicts zero HR change. It is
scored on the same held-out rows as every other model in both participant and
track evaluations. No input, fitted parameter or training label is needed.

The new behavioral test independently calculates MAE as `mean(abs(test_HR_change))`
and R² against a vector of zero predictions for both group types. A nonzero
target fixture distinguishes this control from the training-mean baseline.
Initial RED: `test_no_change_baseline_scores_zero_on_each_held_out_partition`
failed with `KeyError: 'no_change'`. GREEN: all five functions in `test_hr.py`
passed when called directly with Python 3.9; pytest is not installed in that
local environment. The functions use plain assertions and require no pytest
fixtures. RED and GREEN are preserved in commits `e1f9468` and `2712dcc`.

The full `train_hr.py` CLI was rerun against the unchanged Music-Mouv archive
through `runpy` under Python's `trace` module, writing temporary output to
`/var/folders/qz/161pmp415wzbx18zv8j9ss1r0000gn/T/compiled16-nochange-nxx2vdto`.
Line coverage of `train_hr.py` was 91%. Both saved split CSVs were byte-identical
to the previous run; all previous metrics agreed within 1e-10. The checked-in
metrics preserve the previous values and add the two zero-change results.
The checked-in fitted model was not replaced.

The rerun emitted NumPy matrix-multiplication RuntimeWarnings under the local
Python 3.9 / NumPy 2.0.2 environment. All resulting metrics were finite and
reproduced the existing metrics within the tolerance above. The repository's
pinned Python 3.12 environment remains the reference for environment-level
reproduction.

| Held-out groups | No-change MAE (BPM) | No-change R² |
| --- | ---: | ---: |
| Participants | 6.4165121951 | -0.0038678334 |
| Tracks | 3.9557000000 | -0.1409083334 |

Review: this is a zero vector evaluated only against the existing test target;
it changes neither fitting nor splits, feature selection, labels or model
selection. A no-change prediction can be useful even without an audio effect,
so it is reported alongside the separate state-only comparison.

Self-check: accuracy 4/5 (formula checks and full rerun agree; environment
warnings remain documented); completeness 4/5 (both splits, metrics, tests,
results table and checksums updated; full pytest suite not run); clarity 4/5
(zero-change and training-mean controls distinguished); actionability 4/5
(local branch is reviewable; remote publication remains separate); conciseness
4/5 (small implementation with supporting reproduction evidence). Overall 4.0/5.
