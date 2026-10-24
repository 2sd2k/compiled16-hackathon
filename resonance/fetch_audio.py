"""Download the calm-list audio the controller plays (first ~90 s of each track, ~90 MB total).

Audio is not committed to the repo, so run this once after cloning. It reads the calm list from
app/data/music_library.json and saves data/raw/audio/<song_id>.mp3 from Jamendo. Files already
present are skipped. Standard library only.

Usage: python resonance/fetch_audio.py          (calm list only)
       python resonance/fetch_audio.py --all    (all 590 tracks, ~800 MB)
"""
import json
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIBRARY = REPO / "app" / "data" / "music_library.json"
AUDIO_DIR = REPO / "data" / "raw" / "audio"
MAX_BYTES = 1_400_000  # same ~90 s cap the library features were computed on


def fetch(track):
    path = AUDIO_DIR / f"{track['song_id']}.mp3"
    if path.exists() and path.stat().st_size > 50_000:
        return "cached"
    req = urllib.request.Request(track["stream_url"], headers={
        "User-Agent": "resonance-hackathon-fetch", "Range": f"bytes=0-{MAX_BYTES - 1}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        if not resp.headers.get("Content-Type", "").startswith("audio"):
            raise RuntimeError(f"not audio ({resp.headers.get('Content-Type')})")
        path.write_bytes(resp.read(MAX_BYTES))
    return "downloaded"


def main():
    tracks = json.loads(LIBRARY.read_text(encoding="utf-8"))["tracks"]
    if "--all" not in sys.argv:
        tracks = [t for t in tracks if t.get("calm")]
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    counts = {"cached": 0, "downloaded": 0, "failed": 0}
    for i, t in enumerate(tracks, 1):
        try:
            counts[fetch(t)] += 1
        except Exception as e:  # keep going; the controller skips missing files
            counts["failed"] += 1
            print(f"  failed {t['song_id']}: {e}")
        print(f"\r{i}/{len(tracks)} tracks", end="", flush=True)
    print(f"\ndone: {counts['downloaded']} downloaded, {counts['cached']} already here, "
          f"{counts['failed']} failed -> {AUDIO_DIR}")


if __name__ == "__main__":
    main()
