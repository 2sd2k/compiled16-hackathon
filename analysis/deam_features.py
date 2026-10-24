"""Compute DEAM-style openSMILE features for the library so Josh's DEAM emotion model can score it.

DEAM's song-level table (data/deam/deam_song_level_features.csv) holds 520 columns:
65 ComParE low-level descriptors + 65 deltas, each summarized per 0.5 s window (amean, stddev),
then over the clip (mean_over_time, std_over_time). This rebuilds that layout from the cached
audio, over the first 45 s of each track (DEAM's clip length).

It is an approximation: DEAM used the ComParE 2013 config at 44.1 kHz on 45 s excerpts taken
from inside each song; here it is opensmile's ComParE_2016 set on the start of each track.

Writes analysis/out/library_deam_features.csv.

Usage: python -I analysis/deam_features.py
"""
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
LIBRARY = REPO / "app" / "data" / "music_library.json"
AUDIO_DIR = REPO / "data" / "raw" / "audio"
OUT = REPO / "analysis" / "out" / "library_deam_features.csv"
CLIP_S = 45
FRAMES_PER_WINDOW = 50  # 10 ms LLD frames -> 0.5 s windows


def song_features(song_id):
    import librosa
    import opensmile

    y, sr = librosa.load(AUDIO_DIR / f"{song_id}.mp3", sr=44100, mono=True, duration=CLIP_S)
    lld = opensmile.Smile(feature_set=opensmile.FeatureSet.ComParE_2016,
                          feature_level=opensmile.FeatureLevel.LowLevelDescriptors).process_signal(y, sr)
    de = opensmile.Smile(feature_set=opensmile.FeatureSet.ComParE_2016,
                         feature_level=opensmile.FeatureLevel.LowLevelDescriptors_Deltas).process_signal(y, sr)
    frames = pd.concat([lld.reset_index(drop=True), de.reset_index(drop=True)], axis=1)
    # The 2013 config DEAM used names MFCCs pcm_fftMag_mfcc_*; ComParE_2016 calls them mfcc_*.
    frames.columns = [c.replace("mfcc_sma", "pcm_fftMag_mfcc_sma") for c in frames.columns]
    window = np.arange(len(frames)) // FRAMES_PER_WINDOW
    per_window = frames.groupby(window).agg(["mean", "std"])
    per_window = per_window[per_window.index < window.max()]  # drop the partial last window
    row = {"song_id": song_id}
    for (name, stat), col in per_window.items():
        label = "amean" if stat == "mean" else "stddev"
        row[f"{name}_{label}__mean_over_time"] = float(col.mean())
        row[f"{name}_{label}__std_over_time"] = float(col.std())
    return row


def main():
    ids = [t["song_id"] for t in json.loads(LIBRARY.read_text(encoding="utf-8"))["tracks"]]
    with ProcessPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(song_features, ids, chunksize=4))
    pd.DataFrame(rows).to_csv(OUT, index=False)
    print(f"{len(rows)} tracks x {len(rows[0]) - 1} features -> {OUT}")


if __name__ == "__main__":
    main()
