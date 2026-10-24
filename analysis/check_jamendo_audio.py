"""Check which HKU956 songs are still streamable from Jamendo (the original HKU mp3 host is gone).

Sends one HEAD request per track, sequentially, and writes analysis/out/jamendo_availability.csv.
Downloads no audio.

Usage: python -I analysis/check_jamendo_audio.py <path to unzipped HKU956 folder>
"""
import csv
import sys
import time
import urllib.request
from pathlib import Path

URL = "https://mp3l.jamendo.com/?trackid={}&format=mp31"
OUT = Path(__file__).resolve().parent / "out" / "jamendo_availability.csv"


def head(track_id):
    req = urllib.request.Request(URL.format(track_id), method="HEAD", headers={"User-Agent": "hku956-audit"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.headers.get("Content-Length", "")
    except urllib.error.HTTPError as e:
        return e.code, "", ""
    except Exception as e:  # network error, timeout
        return 0, type(e).__name__, ""


def main(root):
    root = Path(root)
    if (root / "HKU956").is_dir():
        root = root / "HKU956"
    with open(root / "2. original_song_audio.csv", newline="") as f:
        ids = [row["song_id"] for row in csv.DictReader(f)]
    OUT.parent.mkdir(exist_ok=True)
    ok = 0
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["song_id", "status", "content_type", "content_length"])
        for i, tid in enumerate(ids, 1):
            status, ctype, clen = head(tid)
            ok += status == 200 and ctype.startswith("audio")
            w.writerow([tid, status, ctype, clen])
            if i % 100 == 0:
                print(f"{i}/{len(ids)} checked, {ok} available", flush=True)
            time.sleep(0.2)
    print(f"done: {ok}/{len(ids)} tracks available as audio")


if __name__ == "__main__":
    main(sys.argv[1])
