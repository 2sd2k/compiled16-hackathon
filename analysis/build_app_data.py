"""Turn the team's filtered record list into replay sessions for the front end.

Input (the hand-off from the filtering team):
    data/filtered/records.csv with at least participant_id, song_no, song_id.
    Optional column keep (1/0, true/false) to drop rows without deleting them.
    If the file does not exist, every audited HKU956 record is used.

Output: app/data/sessions.json, one HR stream per participant, made by joining that
participant's kept records in listening order. The gaps between songs were not recorded,
so each join is marked as a boundary.

Usage: python -I analysis/build_app_data.py <path to unzipped HKU956 folder>
"""
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FILTERED = REPO / "data" / "filtered" / "records.csv"
AUDITED = REPO / "analysis" / "out" / "hku956_records.csv"
OUT = REPO / "app" / "data" / "sessions.json"


def kept(row):
    v = str(row.get("keep", "1")).strip().lower()
    return v not in ("0", "false", "no", "")


def load_records():
    if FILTERED.exists():
        with open(FILTERED, newline="", encoding="utf-8-sig") as f:
            rows = [r for r in csv.DictReader(f) if kept(r)]
        return rows, str(FILTERED.relative_to(REPO)).replace("\\", "/")
    with open(AUDITED, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["has_files"] == "True"]
    return rows, "all audited HKU956 records (no data/filtered/records.csv yet)"


def main(root):
    root = Path(root)
    if (root / "HKU956").is_dir():
        root = root / "HKU956"
    sig_root = root / "1. physiological_signals"
    rows, source = load_records()

    by_pid = {}
    for r in rows:
        by_pid.setdefault(r["participant_id"], []).append((int(r["song_no"]), r["song_id"]))

    sessions, missing = [], 0
    for pid in sorted(by_pid):
        hr, segments = [], []
        for song_no, song_id in sorted(set(by_pid[pid])):
            path = sig_root / pid / "HR" / f"{song_no}_{song_id}.csv"
            if not path.exists():
                missing += 1
                continue
            with open(path) as f:
                vals = [round(float(line), 2) for line in f if line.strip()]
            segments.append({"song_no": song_no, "song_id": song_id, "start": len(hr), "n": len(vals)})
            hr.extend(vals)
        if hr:
            sessions.append({"participant_id": pid, "hr": hr, "segments": segments})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": source,
        "dataset": "HKU956 (Hu, Li & Liu 2022), CC BY-NC 4.0",
        "hr_rate_hz": 1,
        "note": "Recorded while each participant listened to songs they chose. Gaps between songs were not recorded.",
        "sessions": sessions,
    }), encoding="utf-8")
    print(f"{len(sessions)} sessions, {sum(len(s['segments']) for s in sessions)} records "
          f"from {source}; {missing} listed records had no signal file")


if __name__ == "__main__":
    main(sys.argv[1])
