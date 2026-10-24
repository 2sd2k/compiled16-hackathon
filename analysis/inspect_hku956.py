"""Go/no-go audit of the HKU956 dataset for the HR-response model (PRODUCT-BRIEF.md step 1).

Reads every listening record, checks signal lengths, IBI coverage and play_duration
agreement, and measures how much HR moves within a record. Writes a per-record CSV
and a JSON summary to analysis/out/.

Usage: python -I analysis/inspect_hku956.py <path to unzipped HKU956 folder>
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RATES = {"HR": 1, "BVP": 64, "EDA": 4, "TEMP": 4}
OUT = Path(__file__).resolve().parent / "out"


def read_values(path):
    # Files are a single headerless column of values; no timestamps.
    return pd.read_csv(path, header=None).iloc[:, 0].to_numpy(dtype=float)


def window_mean(hr, start, end):
    seg = hr[start:end]
    return float(np.mean(seg)) if len(seg) else np.nan


def audit_record(sig_dir, fname):
    rec = {}
    for sig, rate in RATES.items():
        p = sig_dir / sig / fname
        rec[f"{sig}_s"] = len(read_values(p)) / rate if p.exists() else np.nan
    hr = read_values(sig_dir / "HR" / fname)
    ibi_path = sig_dir / "IBI" / fname
    ibi = read_values(ibi_path) if ibi_path.exists() and ibi_path.stat().st_size else np.array([])
    rec["ibi_n"] = len(ibi)
    rec["ibi_sum_s"] = float(ibi.sum())
    rec["ibi_out_of_range"] = int(((ibi < 0.3) | (ibi > 2.0)).sum())
    if len(ibi) > 1:
        rec["ibi_jump_gt20pct"] = int((np.abs(np.diff(ibi)) / ibi[:-1] > 0.2).sum())
    else:
        rec["ibi_jump_gt20pct"] = 0
    rec["hr_min"] = float(hr.min())
    rec["hr_max"] = float(hr.max())
    rec["hr_mean"] = float(hr.mean())
    rec["hr_implausible"] = int(((hr < 40) | (hr > 200)).sum())
    # Candidate target: late-window mean minus early-window mean (both in-exposure).
    rec["hr_first10"] = window_mean(hr, 0, 10)
    rec["hr_last10"] = window_mean(hr, len(hr) - 10, len(hr))
    rec["hr_delta_last_minus_first"] = rec["hr_last10"] - rec["hr_first10"]
    return rec


def continuity_check(ok, sig_root):
    """Files carry no timestamps, so test whether consecutive records are back-to-back.

    Compares the jump from the last seconds of record k to the first seconds of record k+1
    against the change over the same spacing inside a record. Skin temperature drifts slowly,
    so a large TEMP jump across the boundary implies an unrecorded gap (browsing, surveys).
    """
    cross_hr, cross_temp, within_hr, within_temp = [], [], [], []
    for pid, g in ok.sort_values("song_no").groupby("participant_id"):
        sig_dir = sig_root / pid
        prev = None
        for r in g.itertuples(index=False):
            fname = f"{r.song_no}_{r.song_id}.csv"
            hr = read_values(sig_dir / "HR" / fname)
            temp = read_values(sig_dir / "TEMP" / fname)
            if len(hr) >= 20:
                within_hr.append(abs(hr[10:15].mean() - hr[0:5].mean()))
            if len(temp) >= 80:
                within_temp.append(abs(temp[40:60].mean() - temp[0:20].mean()))
            if prev is not None and r.song_no == prev[0] + 1:
                cross_hr.append(abs(hr[0:5].mean() - prev[1][-5:].mean()))
                cross_temp.append(abs(temp[0:20].mean() - prev[2][-20:].mean()))
            prev = (r.song_no, hr, temp)

    def q(x):
        return {"median": float(np.median(x)), "p90": float(np.percentile(x, 90))}

    return {
        "pairs": len(cross_hr),
        "hr_abs_jump_across_boundary_bpm": q(cross_hr),
        "hr_abs_change_within_record_10s_bpm": q(within_hr),
        "temp_abs_jump_across_boundary_C": q(cross_temp),
        "temp_abs_change_within_record_10s_C": q(within_temp),
    }


def main(root):
    root = Path(root)
    if (root / "HKU956").is_dir():
        root = root / "HKU956"
    av = pd.read_csv(root / "3. AV_ratings.csv")
    audio = pd.read_csv(root / "2. original_song_audio.csv")
    sig_root = root / "1. physiological_signals"

    rows = []
    for r in av.itertuples(index=False):
        sig_dir = sig_root / r.participant_id
        fname = f"{r.song_no}_{r.song_id}.csv"
        row = {"participant_id": r.participant_id, "song_no": r.song_no, "song_id": r.song_id,
               "play_duration": r.play_duration, "has_files": (sig_dir / "HR" / fname).exists()}
        if row["has_files"]:
            row.update(audit_record(sig_dir, fname))
        rows.append(row)
    df = pd.DataFrame(rows)

    # Signal files that have no AV_ratings row.
    rated = set(zip(df.participant_id, df.song_no.astype(str) + "_" + df.song_id.astype(str) + ".csv"))
    orphan = [(p.name, f.name) for p in sig_root.iterdir() for f in (p / "HR").iterdir()
              if (p.name, f.name) not in rated]

    ok = df[df.has_files]
    dur = ok["HR_s"]
    coverage = (ok["ibi_sum_s"] / ok["BVP_s"]).clip(upper=1.5)
    per_p = ok.groupby("participant_id").size()
    song_counts = df.groupby("song_id").participant_id.nunique()
    order_gaps = sum(
        len(set(range(g.song_no.max() + 1)) - set(g.song_no)) for _, g in df.groupby("participant_id")
    )

    summary = {
        "records_in_ratings": int(len(df)),
        "records_with_signal_files": int(df.has_files.sum()),
        "signal_files_without_rating": len(orphan),
        "participants": int(df.participant_id.nunique()),
        "records_per_participant": {"min": int(per_p.min()), "median": float(per_p.median()), "max": int(per_p.max())},
        "song_no_gaps_total": int(order_gaps),
        "unique_songs_rated": int(df.song_id.nunique()),
        "unique_songs_in_audio_csv": int(audio.song_id.nunique()),
        "songs_heard_by_2plus_participants": int((song_counts >= 2).sum()),
        "max_participants_per_song": int(song_counts.max()),
        "record_duration_s": {q: float(dur.quantile(v)) for q, v in
                              [("p05", .05), ("p25", .25), ("median", .5), ("p75", .75), ("p95", .95)]},
        "records_ge_60s": int((dur >= 60).sum()),
        "records_ge_90s": int((dur >= 90).sum()),
        "signal_length_mismatch_gt2s": int(((ok[["BVP_s", "EDA_s", "TEMP_s"]].sub(dur, axis=0)).abs() > 2).any(axis=1).sum()),
        "hr_vs_play_duration_diff_s": {"median": float((dur - ok.play_duration).median()),
                                       "abs_gt10s": int(((dur - ok.play_duration).abs() > 10).sum())},
        "ibi_coverage_fraction": {"median": float(coverage.median()), "p25": float(coverage.quantile(.25)),
                                  "records_ge_50pct": int((coverage >= .5).sum())},
        "ibi_out_of_range_total": int(ok.ibi_out_of_range.sum()),
        "hr_implausible_samples_total": int(ok.hr_implausible.sum()),
        "hr_delta_last10_minus_first10_bpm": {
            "mean": float(ok.hr_delta_last_minus_first.mean()),
            "sd": float(ok.hr_delta_last_minus_first.std()),
            "p05": float(ok.hr_delta_last_minus_first.quantile(.05)),
            "p95": float(ok.hr_delta_last_minus_first.quantile(.95)),
            "abs_ge_5bpm": int((ok.hr_delta_last_minus_first.abs() >= 5).sum()),
        },
    }

    summary["between_record_continuity"] = continuity_check(ok, sig_root)

    OUT.mkdir(exist_ok=True)
    df.to_csv(OUT / "hku956_records.csv", index=False)
    (OUT / "hku956_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main(sys.argv[1])
