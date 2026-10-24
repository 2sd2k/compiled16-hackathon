"""External check of Josh's DEAM emotion model on our music library, against HKU956 listener ratings.

Steps:
1. Retrain the model with Josh's own baseline/train.py (training data and split unchanged), saved
   to analysis/out/deam_model/ so no externally supplied pickle is loaded.
2. Check how far the library's features sit outside DEAM's training range (domain shift).
3. Predict arousal/valence for each library track and compare (Spearman) with the mean
   self-reported arousal/valence of HKU956 listeners for the same songs. Those ratings were never
   used in training. Compare with our simple features and the calm-list label.
4. Cross-validated test of whether predicted arousal adds to onset density + loudness + brightness.

Writes analysis/out/deam_on_hku_validation.json and adds predictions to the library JSON.

Usage: python -I analysis/validate_deam_on_hku.py <path to unzipped HKU956 folder>
"""
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import KFold, cross_val_score

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "analysis" / "out"
LIBRARY = REPO / "app" / "data" / "music_library.json"
sys.path.insert(0, str(REPO / "baseline"))


def boot_rho(x, y, n=2000, seed=0):
    rng = np.random.default_rng(seed)
    x, y = np.asarray(x), np.asarray(y)
    rhos = []
    for _ in range(n):
        i = rng.integers(0, len(x), len(x))
        rhos.append(spearmanr(x[i], y[i]).statistic)
    rho = spearmanr(x, y)
    return {"rho": round(float(rho.statistic), 3), "p": float(rho.pvalue),
            "ci95": [round(float(np.percentile(rhos, 2.5)), 3), round(float(np.percentile(rhos, 97.5)), 3)],
            "n": int(len(x))}


def main(hku_root):
    from train import run  # Josh's training entry point

    model_dir = OUT / "deam_model"
    report_train = run(REPO, model_dir)
    artifact = joblib.load(model_dir / "model.joblib")  # produced just above by this script

    deam = pd.read_csv(REPO / "data" / "deam" / "deam_song_level_features.csv")
    ours = pd.read_csv(OUT / "library_deam_features.csv")
    feats = artifact["features"]
    lo, hi = deam[feats].quantile(0.01), deam[feats].quantile(0.99)
    outside = ((ours[feats] < lo) | (ours[feats] > hi)).mean()
    shift = {
        "median_fraction_of_tracks_outside_deam_1_99pct_per_feature": round(float(outside.median()), 3),
        "features_with_over_half_tracks_outside": int((outside > 0.5).sum()),
        "worst_features": outside.sort_values(ascending=False).head(8).round(2).to_dict(),
    }

    pred = artifact["model"].predict(ours[feats])
    ours["pred_arousal"], ours["pred_valence"] = pred[:, 0], pred[:, 1]

    hku_root = Path(hku_root)
    if (hku_root / "HKU956").is_dir():
        hku_root = hku_root / "HKU956"
    av = pd.read_csv(hku_root / "3. AV_ratings.csv")
    rated = av.groupby("song_id").agg(hku_arousal=("arousal_rating", "mean"),
                                      hku_valence=("valence_rating", "mean"),
                                      raters=("participant_id", "nunique")).reset_index()
    lib = pd.DataFrame(json.loads(LIBRARY.read_text(encoding="utf-8"))["tracks"])
    lib["song_id"] = lib.song_id.astype(int)
    ours["song_id"] = ours.song_id.astype(int)
    df = lib.merge(ours[["song_id", "pred_arousal", "pred_valence"]], on="song_id").merge(rated, on="song_id")

    results = {"training_reproduction": report_train["test"], "domain_shift": shift, "correlations": {}}
    for subset, d in [("all_rated_songs", df), ("songs_with_2plus_raters", df[df.raters >= 2])]:
        c = {}
        for target in ("hku_arousal", "hku_valence"):
            c[target] = {
                "josh_pred_arousal": boot_rho(d.pred_arousal, d[target]),
                "josh_pred_valence": boot_rho(d.pred_valence, d[target]),
                "onsets_per_s": boot_rho(d.onsets_per_s, d[target]),
                "loudness_dbfs": boot_rho(d.loudness_dbfs, d[target]),
                "brightness_hz": boot_rho(d.brightness_hz, d[target]),
                "calm_label": boot_rho(d.calm.astype(int), d[target]),
            }
        results["correlations"][subset] = c

    # Does predicted arousal add to simple features? 5-fold CV R^2 on rated songs.
    base_cols = ["onsets_per_s", "loudness_dbfs", "brightness_hz"]
    kf = KFold(5, shuffle=True, random_state=0)
    y = df.hku_arousal
    r2_base = cross_val_score(LinearRegression(), df[base_cols], y, cv=kf, scoring="r2")
    r2_plus = cross_val_score(LinearRegression(), df[base_cols + ["pred_arousal"]], y, cv=kf, scoring="r2")
    r2_only = cross_val_score(LinearRegression(), df[["pred_arousal"]], y, cv=kf, scoring="r2")
    results["cv_r2_hku_arousal"] = {
        "simple_features": round(float(r2_base.mean()), 3),
        "simple_plus_josh_pred_arousal": round(float(r2_plus.mean()), 3),
        "josh_pred_arousal_only": round(float(r2_only.mean()), 3),
    }
    results["calm_vs_not_mean_hku_arousal"] = {
        "calm": round(float(df[df.calm].hku_arousal.mean()), 2),
        "not_calm": round(float(df[~df.calm].hku_arousal.mean()), 2),
        "pred_arousal_calm": round(float(df[df.calm].pred_arousal.mean()), 2),
        "pred_arousal_not_calm": round(float(df[~df.calm].pred_arousal.mean()), 2),
    }

    (OUT / "deam_on_hku_validation.json").write_text(json.dumps(results, indent=2))
    print(json.dumps({k: v for k, v in results.items() if k != "training_reproduction"}, indent=2))

    # Store predictions on the library for the controller (used only if the check supports it).
    data = json.loads(LIBRARY.read_text(encoding="utf-8"))
    by_id = ours.set_index("song_id")[["pred_arousal", "pred_valence"]].round(3).to_dict("index")
    for t in data["tracks"]:
        p = by_id.get(int(t["song_id"]))
        if p:
            t["deam_pred_arousal"], t["deam_pred_valence"] = p["pred_arousal"], p["pred_valence"]
    LIBRARY.write_text(json.dumps(data, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
