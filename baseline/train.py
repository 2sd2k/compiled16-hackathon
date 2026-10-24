"""Run from repository root: python baseline/train.py."""
import argparse
import json
from pathlib import Path
import platform
import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from data import ROOT, TARGETS, load_data, split_indices


def metrics(y, pred):
    return {name: {'mae': float(mean_absolute_error(y[:, i], pred[:, i])), 'r2': float(r2_score(y[:, i], pred[:, i]))} for i, name in enumerate(TARGETS)}


def run(root=ROOT, output=None):
    output = Path(output or Path(__file__).parent)
    output.mkdir(parents=True, exist_ok=True)
    frame, x, y, groups, audit = load_data(root)
    splits = split_indices(groups)
    tr, va, te = (splits[k] for k in ['train', 'validation', 'test'])
    dummy = DummyRegressor(strategy='mean').fit(x.iloc[tr], y[tr])
    candidates = {f'ridge_{alpha}': make_pipeline(SimpleImputer(strategy='median'), StandardScaler(), Ridge(alpha=alpha)) for alpha in [1, 10, 100, 1000]}
    candidates['extra_trees'] = make_pipeline(SimpleImputer(strategy='median'), ExtraTreesRegressor(n_estimators=100, max_depth=12, min_samples_leaf=4, max_features=.8, random_state=42, n_jobs=1))
    validation = {}
    for name, model in candidates.items():
        model.fit(x.iloc[tr], y[tr])
        validation[name] = metrics(y[va], model.predict(x.iloc[va]))
    selected = min(validation, key=lambda name: np.mean([m['mae'] for m in validation[name].values()]))
    model = candidates[selected]
    # Test evaluated only after validation selection; no refitting, no test-driven tuning.
    pred = model.predict(x.iloc[te])
    report = {'seed': 42, 'target_order': TARGETS, 'target_scale': [1, 9], 'selection_metric': 'mean validation MAE across both targets',
              'selected': selected, 'fit_partition': 'train only', 'split_counts': {k: len(v) for k, v in splits.items()},
              'validation': validation, 'mean_baseline_validation': metrics(y[va], dummy.predict(x.iloc[va])),
              'test': {selected: metrics(y[te], pred), 'train_mean': metrics(y[te], dummy.predict(x.iloc[te]))},
              'data_audit': audit, 'versions': {'python': platform.python_version(), 'numpy': np.__version__, 'pandas': pd.__version__, 'sklearn': sklearn.__version__, 'joblib': joblib.__version__}}
    joblib.dump({'model': model, 'features': list(x.columns), 'targets': TARGETS, 'scale': [1, 9], 'selected': selected}, output/'model.joblib', compress=3)
    assignments = frame[['song_id']].copy()
    for name, indices in splits.items():
        assignments.loc[indices, 'split'] = name
    assignments.to_csv(output/'splits.csv', index=False)
    predictions = frame.iloc[te][['song_id', *TARGETS]].copy()
    for i, target in enumerate(TARGETS):
        predictions[f'predicted_{target}'] = pred[:, i]
    predictions.to_csv(output/'test_predictions.csv', index=False)
    (output/'metrics.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k: report[k] for k in ['selected', 'split_counts', 'test']}, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    run(args.root, args.output)
