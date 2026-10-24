"""Strict acoustic schema, annotation audit, and reproducible grouped song split."""
import hashlib
import io
from pathlib import Path
import re
import zipfile
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupShuffleSplit

TARGETS = ['arousal_mean', 'valence_mean']
ANNOTATIONS = ['valence_mean', 'valence_std', 'arousal_mean', 'arousal_std']
PATTERN = re.compile(r'^(F0final|voicingFinalUnclipped|jitterLocal|jitterDDP|shimmerLocal|logHNR|audspec|audSpec|pcm_).+__(mean|std)_over_time$')
ROOT = Path(__file__).resolve().parents[1]


def acoustic_columns(frame):
    columns = [c for c in frame if c not in ['song_id', *ANNOTATIONS]]
    if not columns or any(not PATTERN.fullmatch(c) for c in columns):
        raise ValueError('Unexpected non-acoustic column; reject possible annotation/metadata leakage')
    return columns


def validate_features(frame, columns):
    missing = set(columns) - set(frame)
    if missing:
        raise ValueError(f'Missing {len(missing)} required acoustic features')
    x = frame.loc[:, columns].apply(pd.to_numeric, errors='raise')
    if np.isinf(x.to_numpy()).any():
        raise ValueError('Infinite acoustic feature')
    return x


def load_data(root=ROOT):
    source = Path(root) / 'data/deam'
    frame = pd.read_csv(source / 'deam_song_level_features.csv').sort_values('song_id').reset_index(drop=True)
    if frame.song_id.isna().any() or frame.song_id.duplicated().any():
        raise ValueError('Missing or duplicate song_id')
    if not np.equal(frame.song_id, frame.song_id.astype(int)).all():
        raise ValueError('Nonintegral song_id')
    columns = acoustic_columns(frame)
    x = validate_features(frame, columns)
    if x.isna().all().any():
        raise ValueError('Entirely missing acoustic feature')
    with zipfile.ZipFile(source / 'annotations.zip') as archive:
        names = sorted(n for n in archive.namelist() if n.startswith('annotations/annotations averaged per song/song_level/static_') and n.endswith('.csv'))
        if len(names) != 2:
            raise ValueError('Expected two averaged static annotation files')
        labels = pd.concat([pd.read_csv(io.BytesIO(archive.read(n)), skipinitialspace=True)[['song_id', *ANNOTATIONS]] for n in names])
    if labels.song_id.duplicated().any() or set(labels.song_id) != set(frame.song_id):
        raise ValueError('Annotation and feature song IDs do not match one-to-one')
    aligned = labels.set_index('song_id').loc[frame.song_id, ANNOTATIONS].to_numpy()
    if not np.allclose(aligned, frame[ANNOTATIONS].to_numpy(), atol=1e-8, rtol=0):
        raise ValueError('Embedded labels disagree with separate annotations')
    y = frame[TARGETS].to_numpy()
    if not np.isfinite(y).all() or ((y < 1) | (y > 9)).any():
        raise ValueError('Invalid 1–9 static target')
    # Reject exact or affine target copies hidden under plausible acoustic names.
    for target in TARGETS:
        corr = x.corrwith(frame[target]).abs()
        if (corr > 0.999999).any():
            raise ValueError(f'Acoustic column is an apparent copy of {target}')
    groups = pd.util.hash_pandas_object(x, index=False).to_numpy()
    audit = {'rows': len(frame), 'acoustic_features': len(columns), 'missing_acoustic_values': int(x.isna().sum().sum()),
             'exact_duplicate_feature_rows': int(x.duplicated().sum()), 'annotation_files': names,
             'annotations_match': True, 'excluded_predictors': ['song_id', *ANNOTATIONS],
             'sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [source/'annotations.zip', source/'deam_song_level_features.csv']}}
    return frame, x, y, groups, audit


def split_indices(groups, seed=42):
    indices = np.arange(len(groups))
    train_val, test = next(GroupShuffleSplit(n_splits=1, test_size=.15, random_state=seed).split(indices, groups=groups))
    tr, va = next(GroupShuffleSplit(n_splits=1, test_size=.15/.85, random_state=seed).split(train_val, groups=groups[train_val]))
    result = {'train': train_val[tr], 'validation': train_val[va], 'test': test}
    for a, b in [('train', 'validation'), ('train', 'test'), ('validation', 'test')]:
        if set(groups[result[a]]) & set(groups[result[b]]):
            raise ValueError('Duplicate acoustic vectors cross splits')
    return result
