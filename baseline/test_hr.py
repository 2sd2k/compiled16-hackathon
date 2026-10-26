"""Synthetic arrays below test window logic; they are never model training data."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np
import pandas as pd
from train_hr import AUDIO, evaluate, trial_row
from data import split_indices


def trial():
    return {'music_data': {'spotify_data': {'track_id': 'track', **{k: 1 for k in AUDIO}}},
            'watch_data': {'heart_rate': {'time_s': list(range(100, 151)), 'value': list(np.arange(51)+70)}}}


def test_windows_target_and_no_future_input():
    value = trial()
    row, reason = trial_row('person', 'test_1', value)
    assert reason is None
    assert row['initial_hr'] == 74.5
    assert row['hr_change'] == 30
    assert np.isclose(row['prior_hr_slope'], 1)
    value['watch_data']['heart_rate']['value'][30:40] = [1000]*10
    changed, _ = trial_row('person', 'test_1', value)
    assert changed['initial_hr'] == row['initial_hr']
    assert changed['prior_hr_slope'] == row['prior_hr_slope']
    assert changed['hr_change'] != row['hr_change']


def test_missing_outcomes_and_missing_audio_not_fabricated():
    value = trial()
    value['watch_data']['heart_rate']['value'][35] = None
    assert trial_row('p', 'test_1', value)[1] == 'missing_or_nonpositive_hr'
    assert trial_row('p', 'test_0s', {})[1] == 'no_music_track_id'


def test_all_trials_of_person_stay_together():
    groups = np.repeat([f'p{i}' for i in range(35)], 7)
    splits = split_indices(groups)
    for person in np.unique(groups):
        assert sum(person in groups[idx] for idx in splits.values()) == 1


def test_null_hr_arrays_are_excluded():
    value = trial()
    value['watch_data']['heart_rate'] = {'time_s': None, 'value': None}
    assert trial_row('p', 'test_1', value)[1] == 'invalid_or_short_timestamps'


def test_no_change_baseline_scores_zero_on_each_held_out_partition():
    count = 60
    frame = pd.DataFrame({
        'participant': np.repeat([f'p{i}' for i in range(30)], 2),
        'track_id': np.repeat([f't{i}' for i in range(20)], 3),
        'trial': [f'test_{i}' for i in range(count)],
        'initial_hr': np.linspace(65, 95, count),
        'prior_hr_slope': np.sin(np.arange(count)) / 10,
        'hr_change': np.linspace(2, 8, count),
        **{name: np.linspace(0.1, 0.9, count) for name in AUDIO},
    })
    for group_column in ['participant', 'track_id']:
        report, _, assignments = evaluate(frame, group_column)
        test = frame.loc[assignments.split == 'test', 'hr_change'].to_numpy()
        expected_mae = np.abs(test).mean()
        expected_r2 = 1 - np.square(test).sum() / np.square(test - test.mean()).sum()
        assert np.isclose(report['test']['no_change']['mae_bpm'], expected_mae)
        assert np.isclose(report['test']['no_change']['r2'], expected_r2)
        assert not np.isclose(report['test']['no_change']['mae_bpm'],
                              report['test']['train_mean']['mae_bpm'])
