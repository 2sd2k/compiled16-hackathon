import json
from pathlib import Path
import sys
import joblib
import numpy as np
import pandas as pd
import pytest
sys.path.insert(0, str(Path(__file__).parent))
from data import acoustic_columns, load_data, split_indices, validate_features
from predict import predict
from ranking import rank_music, simulated_hr_hook

HERE = Path(__file__).parent


@pytest.fixture(scope='module')
def dataset():
    return load_data()


def test_annotations_and_schema(dataset):
    frame, x, y, groups, audit = dataset
    assert len(frame) == 1802 and x.shape[1] == 520
    assert audit['annotations_match']
    assert not any('arousal' in c or 'valence' in c or c == 'song_id' for c in x)
    assert y.shape == (1802, 2)


def test_split_reproducible_disjoint_with_duplicates(dataset):
    frame, x, y, groups, audit = dataset
    splits = split_indices(groups)
    assert all(np.array_equal(v, split_indices(groups)[k]) for k, v in splits.items())
    assert sorted(np.concatenate(list(splits.values()))) == list(range(len(frame)))
    keys = list(splits)
    for i, key in enumerate(keys):
        for other in keys[i+1:]:
            assert not set(frame.song_id.iloc[splits[key]]) & set(frame.song_id.iloc[splits[other]])
            assert not set(groups[splits[key]]) & set(groups[splits[other]])
    synthetic = np.array([0, 0, *range(1, 100)])
    s = split_indices(synthetic)
    assert any({0, 1} <= set(indices) for indices in s.values())


def test_reject_unknown_and_missing_features(dataset):
    frame, x, *_ = dataset
    with pytest.raises(ValueError, match='non-acoustic'):
        acoustic_columns(frame.assign(patient_outcome=1))
    with pytest.raises(ValueError, match='Missing'):
        validate_features(x.iloc[:, 1:], list(x))
    bad = x.iloc[:1].copy()
    bad.iloc[0, 0] = np.inf
    with pytest.raises(ValueError, match='Infinite'):
        validate_features(bad, list(x))


def test_roundtrip_and_no_label_dependency(dataset, tmp_path):
    frame, x, *_ = dataset
    artifact = joblib.load(HERE/'model.joblib')
    expected = predict(frame.iloc[:5], artifact)
    stripped = predict(frame.iloc[:5].drop(columns=['arousal_mean', 'valence_mean', 'arousal_std', 'valence_std']), artifact)
    pd.testing.assert_frame_equal(expected, stripped)
    joblib.dump(artifact, tmp_path/'roundtrip.joblib')
    pd.testing.assert_frame_equal(expected, predict(frame.iloc[:5], joblib.load(tmp_path/'roundtrip.joblib')))


def test_saved_metrics_and_training_only_scaling(dataset):
    from sklearn.metrics import mean_absolute_error, r2_score
    frame, x, y, groups, audit = dataset
    report = json.loads((HERE/'metrics.json').read_text())
    artifact = joblib.load(HERE/'model.joblib')
    splits = split_indices(groups)
    stored = pd.read_csv(HERE/'splits.csv')
    for part, indices in splits.items():
        assert set(stored.loc[stored.split == part, 'song_id']) == set(frame.song_id.iloc[indices])
    pred = artifact['model'].predict(x.iloc[splits['test']])
    for i, target in enumerate(artifact['targets']):
        result = report['test'][report['selected']][target]
        assert result['mae'] == pytest.approx(mean_absolute_error(y[splits['test'], i], pred[:, i]))
        assert result['r2'] == pytest.approx(r2_score(y[splits['test'], i], pred[:, i]))
    imputer = artifact['model'].steps[0][1]
    np.testing.assert_allclose(imputer.statistics_, x.iloc[splits['train']].median())
    if 'standardscaler' in artifact['model'].named_steps:
        scaler = artifact['model'].named_steps['standardscaler']
        np.testing.assert_allclose(scaler.mean_, x.iloc[splits['train']].mean(), rtol=1e-10, atol=1e-10)


def test_preference_overrides_affect_and_exclusions():
    candidates = [{'song_id': 1, 'predicted_arousal_mean': 3, 'predicted_valence_mean': 6.5},
                  {'song_id': 2, 'predicted_arousal_mean': 8, 'predicted_valence_mean': 2}]
    assert rank_music(candidates, {1: .2, 2: 1})[0]['song_id'] == 2
    assert rank_music(candidates, {}, excluded=[1, 2]) == []
    assert rank_music(candidates, {})[0]['song_id'] == 1
    with pytest.raises(ValueError):
        rank_music(candidates, {1: float('nan')})


@pytest.mark.parametrize('kwargs,action', [({'stop': True}, 'stop'), ({'comfortable': False}, 'stop'), ({'care_present': True}, 'pause'), ({'signal_ok': False}, 'hold')])
def test_controller_constraints(kwargs, action):
    assert simulated_hr_hook(85, 70, **kwargs)['action'] == action


def test_controller_simulation_only():
    with pytest.raises(ValueError, match='Simulation only'):
        simulated_hr_hook(85, 70, simulated=False)
    assert simulated_hr_hook(float('nan'), 70)['action'] == 'hold'
    assert simulated_hr_hook(85, 70)['target_arousal'] < simulated_hr_hook(70, 70)['target_arousal']
    assert simulated_hr_hook(85, 70, stop=True, care_present=True)['action'] == 'stop'


def test_reject_annotation_mismatch(dataset, tmp_path):
    import shutil
    from data import ROOT
    frame = dataset[0].copy()
    source = tmp_path/'data/deam'
    source.mkdir(parents=True)
    shutil.copy(ROOT/'data/deam/annotations.zip', source/'annotations.zip')
    frame.loc[0, 'arousal_mean'] += .1
    frame.to_csv(source/'deam_song_level_features.csv', index=False)
    with pytest.raises(ValueError, match='disagree'):
        load_data(tmp_path)


def test_reject_disguised_target_copy(dataset, tmp_path):
    import shutil
    from data import ROOT
    frame = dataset[0].copy()
    source = tmp_path/'data/deam'
    source.mkdir(parents=True)
    shutil.copy(ROOT/'data/deam/annotations.zip', source/'annotations.zip')
    frame['pcm_fake__mean_over_time'] = 2 * frame.arousal_mean + 3
    frame.to_csv(source/'deam_song_level_features.csv', index=False)
    with pytest.raises(ValueError, match='apparent copy'):
        load_data(tmp_path)
