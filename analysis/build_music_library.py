"""Build the Resonance music library from the HKU956 Jamendo tracks.

For each track: download the first ~90 s from Jamendo (cached in data/raw/audio/, gitignored),
extract tempo, key/mode, loudness, dynamics, brightness, band balance and onset density,
read title/artist/license from the Jamendo track page, and join the HR change observed
when HKU956 participants heard it.

Writes analysis/out/music_features.csv and app/data/music_library.json.

Usage: python -I analysis/build_music_library.py <path to unzipped HKU956 folder>
"""
import csv
import json
import re
import sys
import urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
AUDIO_DIR = REPO / "data" / "raw" / "audio"
OUT_CSV = REPO / "analysis" / "out" / "music_features.csv"
OUT_JSON = REPO / "app" / "data" / "music_library.json"
RECORDS = REPO / "analysis" / "out" / "hku956_records.csv"

STREAM = "https://mp3l.jamendo.com/?trackid={}&format=mp31"
PAGE = "https://www.jamendo.com/track/{}"
MAX_BYTES = 1_400_000  # ~90 s of the 96-128 kbps stream
SECONDS = 90
SR = 22050
UA = {"User-Agent": "resonance-hackathon-library-builder"}

KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Krumhansl-Kessler key profiles.
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def fetch_audio(track_id):
    path = AUDIO_DIR / f"{track_id}.mp3"
    if path.exists() and path.stat().st_size > 50_000:
        return path
    req = urllib.request.Request(STREAM.format(track_id), headers={**UA, "Range": f"bytes=0-{MAX_BYTES - 1}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        if not resp.headers.get("Content-Type", "").startswith("audio"):
            raise RuntimeError(f"not audio: {resp.headers.get('Content-Type')}")
        data = resp.read(MAX_BYTES)  # stop at the cap even if Range is ignored
    path.write_bytes(data)
    return path


def fetch_meta(track_id):
    meta = {"title": "", "artist": "", "license": "", "license_url": "", "page_url": PAGE.format(track_id)}
    try:
        req = urllib.request.Request(PAGE.format(track_id), headers=UA)
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", "replace")
            meta["page_url"] = resp.geturl()
    except Exception:
        return meta
    m = re.search(r'og:title" content="([^"]*)"', html)
    if m:
        # "Artist - Title | Jamendo Music | ..."
        head = m.group(1).split(" | ")[0]
        artist, _, title = head.partition(" - ")
        meta["artist"], meta["title"] = artist.strip(), title.strip()
    m = re.search(r"creativecommons\.org/licenses/([a-z-]+)/([0-9.]+)", html)
    if m:
        meta["license"] = f"CC {m.group(1).upper()} {m.group(2)}"
        meta["license_url"] = f"https://creativecommons.org/licenses/{m.group(1)}/{m.group(2)}/"
    return meta


def extract(path):
    import librosa

    y, sr = librosa.load(path, sr=SR, mono=True, duration=SECONDS)
    feats = {"analyzed_s": round(len(y) / sr, 1)}

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    # librosa.beat.beat_track segfaults in its numba code on this machine, so estimate tempo
    # from onset autocorrelation and score how steady the local tempo is over the excerpt.
    tempo = float(librosa.feature.tempo(onset_envelope=onset_env, sr=sr)[0])
    local = librosa.feature.tempo(onset_envelope=onset_env, sr=sr, aggregate=None)
    octaves = np.log2(local / tempo)
    # Half- or double-time readings count as the same pulse.
    same_pulse = (np.abs(octaves - np.round(octaves)) < 0.1) & (np.abs(np.round(octaves)) <= 1)
    stability = float(same_pulse.mean())
    feats["bpm"] = round(tempo, 1)
    feats["tempo_stability"] = round(stability, 3)
    # Tempo estimates are unreliable on rubato/ambient music; flag rather than trust.
    feats["bpm_reliable"] = bool(stability >= 0.6)

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
    scores = [(np.corrcoef(np.roll(MAJOR, k), chroma)[0, 1], KEYS[k], "major") for k in range(12)]
    scores += [(np.corrcoef(np.roll(MINOR, k), chroma)[0, 1], KEYS[k], "minor") for k in range(12)]
    scores.sort(reverse=True)
    feats["key"], feats["mode"] = scores[0][1], scores[0][2]
    feats["key_confidence"] = round(float(scores[0][0] - scores[1][0]), 3)

    rms = librosa.feature.rms(y=y)[0]
    rms_db = librosa.amplitude_to_db(rms + 1e-10, ref=1.0)
    voiced = rms_db[rms_db > rms_db.max() - 60]  # ignore digital silence
    feats["loudness_dbfs"] = round(float(np.mean(voiced)), 2)
    feats["dynamics_db"] = round(float(np.std(voiced)), 2)

    feats["brightness_hz"] = round(float(librosa.feature.spectral_centroid(y=y, sr=sr).mean()), 1)

    spec = np.abs(librosa.stft(y)) ** 2
    freqs = librosa.fft_frequencies(sr=sr)
    total = spec.sum() + 1e-12
    feats["bass_share"] = round(float(spec[freqs < 250].sum() / total), 3)
    feats["mid_share"] = round(float(spec[(freqs >= 250) & (freqs < 4000)].sum() / total), 3)
    feats["treble_share"] = round(float(spec[freqs >= 4000].sum() / total), 3)

    onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr)
    feats["onsets_per_s"] = round(len(onsets) / max(feats["analyzed_s"], 1), 2)
    return feats


def process(track_id):
    row = {"song_id": track_id, "status": "ok", "error": ""}
    try:
        path = fetch_audio(track_id)
        row.update(extract(path))
    except Exception as e:
        row["status"], row["error"] = "failed", f"{type(e).__name__}: {e}"[:200]
    row.update(fetch_meta(track_id))
    return row


def observed_response():
    """Per-song HR change seen in HKU956 (observational: self-selected, ~50 s plays)."""
    out = {}
    if not RECORDS.exists():
        return out
    with open(RECORDS, newline="") as f:
        for r in csv.DictReader(f):
            if r.get("has_files") != "True":
                continue
            out.setdefault(r["song_id"], []).append(float(r["hr_delta_last_minus_first"]))
    return {k: {"hku_listeners": len(v), "hku_hr_delta_mean": round(float(np.mean(v)), 2)} for k, v in out.items()}


def main(root):
    root = Path(root)
    if (root / "HKU956").is_dir():
        root = root / "HKU956"
    with open(root / "2. original_song_audio.csv", newline="") as f:
        ids = [r["song_id"] for r in csv.DictReader(f)]
    with open(root / "3. AV_ratings.csv", newline="") as f:
        ratings = {}
        for r in csv.DictReader(f):
            ratings.setdefault(r["song_id"], []).append((float(r["valence_rating"]), float(r["arousal_rating"])))

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    obs = observed_response()

    rows = []
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(process, tid) for tid in ids]
        for i, fut in enumerate(as_completed(futures), 1):
            rows.append(fut.result())
            if i % 25 == 0:
                print(f"{i}/{len(ids)} processed, {sum(r['status'] == 'ok' for r in rows)} ok", flush=True)

    for r in rows:
        r.update(obs.get(r["song_id"], {"hku_listeners": 0, "hku_hr_delta_mean": None}))
        rs = ratings.get(r["song_id"], [])
        r["hku_valence_mean"] = round(float(np.mean([v for v, _ in rs])), 2) if rs else None
        r["hku_arousal_mean"] = round(float(np.mean([a for _, a in rs])), 2) if rs else None
        r["stream_url"] = STREAM.format(r["song_id"])

    rows.sort(key=lambda r: int(r["song_id"]))
    fields = sorted({k for r in rows for k in r}, key=lambda k: (k != "song_id", k))
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    good = [r for r in rows if r["status"] == "ok"]
    OUT_JSON.write_text(json.dumps({
        "source": "HKU956 / Jamendo tracks; features from the first 90 s (librosa)",
        "tracks": good,
    }, indent=1), encoding="utf-8")
    print(f"done: {len(good)}/{len(rows)} tracks with features -> {OUT_JSON}")


if __name__ == "__main__":
    main(sys.argv[1])
