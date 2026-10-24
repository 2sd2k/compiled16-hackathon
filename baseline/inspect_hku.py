"""Audit HKU956 directly from its official ZIP; do not invent acoustic matches."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile
import numpy as np
import pandas as pd


def inspect(path):
    with zipfile.ZipFile(path) as archive:
        records = pd.read_csv(io.BytesIO(archive.read('HKU956/3. AV_ratings.csv')))
        audio = pd.read_csv(io.BytesIO(archive.read('HKU956/2. original_song_audio.csv')))
        counts = {'missing_hr': 0, 'invalid_hr': 0, 'shorter_than_60_samples': 0, 'at_least_60_samples': 0}
        lengths = []
        for row in records.itertuples():
            name = f'HKU956/1. physiological_signals/{row.participant_id}/HR/{row.song_no}_{row.song_id}.csv'
            if name not in archive.namelist():
                counts['missing_hr'] += 1
                continue
            hr = np.loadtxt(io.BytesIO(archive.read(name)), delimiter=',', ndmin=1)
            lengths.append(len(hr))
            if not np.isfinite(hr).all() or (hr <= 0).any():
                counts['invalid_hr'] += 1
            elif len(hr) < 60:
                counts['shorter_than_60_samples'] += 1
            else:
                counts['at_least_60_samples'] += 1
        return {'source': 'https://doi.org/10.25442/hku.21080821', 'archive_sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                'listening_records': len(records), 'participants': int(records.participant_id.nunique()), 'unique_listened_song_ids': int(records.song_id.nunique()),
                'audio_link_rows': len(audio), 'bundled_mp3_files': sum(n.lower().endswith('.mp3') for n in archive.namelist()),
                'hr_sample_counts': {'min': min(lengths), 'max': max(lengths), 'median': float(np.median(lengths))},
                'hr_audit': counts, 'license': 'CC BY-NC 4.0',
                'decision': 'Audio link manifest is not audio. Acquire and verify actual stimuli and alignment before an acoustic-HR model. No DEAM song-ID join or synthetic labels.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('hku_audit.json'))
    args = parser.parse_args()
    args.output.write_text(json.dumps(inspect(args.archive), indent=2)+'\n')
