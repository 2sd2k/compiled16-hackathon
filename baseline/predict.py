"""Inference needs acoustic features in the exact training representation, no labels."""
import argparse
from pathlib import Path
import joblib
import pandas as pd
from data import validate_features


def predict(frame, artifact):
    x = validate_features(frame, artifact['features'])
    pred = artifact['model'].predict(x)
    result = pd.DataFrame(pred, columns=['predicted_'+t for t in artifact['targets']], index=frame.index)
    if 'song_id' in frame:
        result.insert(0, 'song_id', frame.song_id)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', type=Path, default=Path(__file__).with_name('model.joblib'))
    args = parser.parse_args()
    # Load only trusted artifacts: joblib/pickle can execute code.
    predict(pd.read_csv(args.input), joblib.load(args.model)).to_csv(args.output, index=False)
