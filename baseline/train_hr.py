"""Exploratory within-trial Music-Mouv HR-change benchmark, never treatment labels."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from data import split_indices

AUDIO = ['danceability', 'energy', 'loudness', 'speechiness', 'accousticness', 'instrumentalness', 'liveness', 'tempo']
STATE = ['initial_hr', 'prior_hr_slope']


def trial_row(participant, trial, value):
    music = value.get('music_data', {}).get('spotify_data', {})
    if not music.get('track_id'):
        return None, 'no_music_track_id'
    hr = value.get('watch_data', {}).get('heart_rate', {})
    times = np.asarray(hr.get('time_s', []), dtype=float)
    values = np.asarray(hr.get('value', []), dtype=float)
    if times.ndim != 1 or values.ndim != 1 or len(times) != len(values) or len(times) < 40 or not np.isfinite(times).all() or (np.diff(times) <= 0).any():
        return None, 'invalid_or_short_timestamps'
    times = times-times[0]
    early = (times >= 0) & (times < 10)
    late = (times >= 30) & (times < 40)
    # Require ten one-second samples per window; no outcome imputation.
    if early.sum() != 10 or late.sum() != 10 or not np.allclose(np.diff(times[early]), 1) or not np.allclose(np.diff(times[late]), 1):
        return None, 'incomplete_windows'
    if not np.isfinite(values[early | late]).all() or (values[early | late] <= 0).any():
        return None, 'missing_or_nonpositive_hr'
    initial = float(values[early].mean())
    row = {'participant': str(participant), 'trial': trial, 'track_id': music['track_id'],
           'initial_hr': initial, 'prior_hr_slope': float(np.polyfit(times[early], values[early], 1)[0]),
           'hr_change': float(values[late].mean()-initial)}
    for name in AUDIO:
        try:
            row[name] = float(music.get(name, np.nan))
        except (TypeError, ValueError):
            row[name] = np.nan
    if any(np.isinf(row[name]) for name in AUDIO):
        return None, 'infinite_audio_feature'
    return row, None


def load_trials(archive):
    rows, rejected, subjects = [], {}, 0
    with zipfile.ZipFile(archive) as z:
        for name in sorted(z.namelist()):
            if not name.endswith('.json') or '__MACOSX' in name:
                continue
            subject = json.loads(z.read(name))
            subjects += 1
            for trial, value in subject.items():
                if not trial.startswith('test_') or not isinstance(value, dict):
                    continue
                row, reason = trial_row(subject['id'], trial, value)
                if row is None:
                    rejected[reason] = rejected.get(reason, 0)+1
                else:
                    rows.append(row)
    frame = pd.DataFrame(rows).sort_values(['participant', 'trial']).reset_index(drop=True)
    if frame.duplicated(['participant', 'trial']).any():
        raise ValueError('Duplicate exposure identity')
    return frame, {'subject_json_files': subjects, 'accepted_trials': len(rows), 'excluded_trials': rejected,
                   'participants_retained': int(frame.participant.nunique()), 'tracks_retained': int(frame.track_id.nunique()),
                   'archive_sha256': hashlib.sha256(Path(archive).read_bytes()).hexdigest()}


def score(y, p):
    return {'mae_bpm': float(mean_absolute_error(y, p)), 'r2': float(r2_score(y, p))}


def evaluate(frame, group_column):
    groups = frame[group_column].to_numpy()
    splits = split_indices(groups)
    tr, va, te = (splits[k] for k in ['train', 'validation', 'test'])
    y = frame.hr_change.to_numpy()
    baseline = DummyRegressor(strategy='mean').fit(np.zeros((len(tr), 1)), y[tr])
    results = {'train_mean': score(y[te], baseline.predict(np.zeros((len(te), 1))))}
    validation, fitted, chosen = {}, {}, {}
    for kind, columns in [('state_only', STATE), ('audio_only', AUDIO), ('state_plus_audio', STATE+AUDIO)]:
        validation[kind] = {}
        models = {}
        for alpha in [1, 10, 100, 1000]:
            model = make_pipeline(SimpleImputer(strategy='median', keep_empty_features=True), StandardScaler(), Ridge(alpha=alpha))
            model.fit(frame.iloc[tr][columns], y[tr])
            validation[kind][str(alpha)] = score(y[va], model.predict(frame.iloc[va][columns]))
            models[str(alpha)] = model
        winner = min(validation[kind], key=lambda a: validation[kind][a]['mae_bpm'])
        fitted[kind] = {'model': models[winner], 'features': columns, 'target': 'mean_HR_t30_40_minus_mean_HR_t0_10', 'units': 'BPM', 'reference': 'first_available_trial_HR_sample_not_music_onset'}
        chosen[kind] = winner
        results[kind] = score(y[te], fitted[kind]['model'].predict(frame.iloc[te][columns]))
    # Cluster-bootstrap the paired MAE difference on held-out groups.
    # Positive difference means audio worsens error; few groups limit precision.
    rng = np.random.default_rng(42)
    held_groups = groups[te]
    unique = np.unique(held_groups)
    state_error = np.abs(y[te]-fitted['state_only']['model'].predict(frame.iloc[te][STATE]))
    combined_error = np.abs(y[te]-fitted['state_plus_audio']['model'].predict(frame.iloc[te][STATE+AUDIO]))
    boot = []
    for _ in range(1000):
        sample = np.concatenate([np.flatnonzero(held_groups == g) for g in rng.choice(unique, len(unique), replace=True)])
        boot.append(float((combined_error[sample]-state_error[sample]).mean()))
    interval = np.quantile(boot, [.025, .975]).tolist()
    assignments = frame[['participant', 'trial', 'track_id']].copy()
    for part, indices in splits.items():
        assignments.loc[indices, 'split'] = part
    return {'group_by': group_column, 'split_counts': {k: len(v) for k, v in splits.items()},
            'group_counts': {k: int(frame.iloc[v][group_column].nunique()) for k, v in splits.items()},
            'selected_alphas': chosen, 'validation': validation, 'test': results,
            'combined_minus_state_mae_bpm_cluster_bootstrap_95_interval': interval,
            'audio_adds_value_on_test': results['state_plus_audio']['mae_bpm'] < results['state_only']['mae_bpm']}, fitted, assignments


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--archive', type=Path)
    p.add_argument('--output', type=Path, default=Path(__file__).parent)
    p.add_argument('--predict', type=Path, help='CSV with initial_hr, prior_hr_slope, and eight music metadata fields')
    p.add_argument('--model', type=Path, default=Path(__file__).with_name('hr_model.joblib'))
    args = p.parse_args()
    if args.predict:
        artifact = joblib.load(args.model)
        x = pd.read_csv(args.predict)[artifact['features']].apply(pd.to_numeric, errors='raise')
        if np.isinf(x.to_numpy()).any() or not np.isfinite(x[STATE].to_numpy()).all() or (x.initial_hr <= 0).any():
            raise ValueError('Invalid HR state or infinite feature')
        pd.DataFrame({'predicted_within_trial_hr_change_bpm': artifact['model'].predict(x)}).to_csv(args.output, index=False)
    else:
        if args.archive is None:
            p.error('--archive required for training')
        frame, audit = load_trials(args.archive)
        report = {'audit': audit, 'seed': 42, 'windows_seconds_from_first_hr_sample': {'input': [0, 10], 'outcome': [30, 40]},
                  'not_pre_post_music': True, 'fit_partition': 'train only', 'audio_features': AUDIO, 'state_features': STATE,
                  'limitation': 'Trial-relative observational change with movement confounding and whole-track metadata, not excerpt acoustic extraction or causal response. Song-held-out and participant-held-out are separate evaluations.'}
        args.output.mkdir(parents=True, exist_ok=True)
        for group in ['participant', 'track_id']:
            result, models, assignments = evaluate(frame, group)
            report[group+'_held_out'] = result
            assignments.to_csv(args.output/f'hr_{group}_splits.csv', index=False)
            if group == 'participant':
                joblib.dump(models['state_plus_audio'], args.output/'hr_model.joblib', compress=3)
        (args.output/'hr_metrics.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report, indent=2))
