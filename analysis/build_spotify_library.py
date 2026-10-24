"""Build the Spotify song library from the Music-Mouv dataset (the songs with Spotify track IDs).

Music-Mouv (homepages.loria.fr/gbonnin/music-mouv) records participants listening to songs they
chose before starting to walk. Each trial carries the Spotify track ID, name, artist and Spotify's
published descriptors (tempo, energy, valence, acousticness, ...), plus the listener's SAM ratings.
No audio is included and none is downloaded: playback goes through the Spotify app.

Calm list (same intent as analysis/classify_calm.py, using Spotify's descriptors):
  - felt tempo about 55-70 bpm (readings of 110-140 count at half time), and
  - low energy (Spotify 'energy' <= 0.5, its perceptual intensity measure).
Ranking uses energy as the arousal signal (lower first). These are engineering heuristics.

Writes app/data/spotify_library.json.

Usage: python -I analysis/build_spotify_library.py data/raw/music-mouv/music-mouv.zip
"""
import json
import statistics
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "app" / "data" / "spotify_library.json"
CALM_BPM = (55, 70)
MAX_ENERGY = 0.5
NUMERIC = ("danceability", "energy", "key", "loudness", "mode", "speechiness", "accousticness",
           "instrumentalness", "liveness", "valence", "tempo", "duration_ms")
KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def window_mean(hr, times, lo, hi):
    vals = [h for h, t in zip(hr, times) if lo <= t < hi and h and h > 0]
    return statistics.mean(vals) if len(vals) >= 5 else None


def main(archive):
    tracks = {}
    with zipfile.ZipFile(archive) as z:
        for name in sorted(n for n in z.namelist() if n.endswith(".json")):
            person = json.loads(z.read(name))
            for trial, block in person.items():
                if not isinstance(block, dict) or "music_data" not in block:
                    continue
                sp = (block.get("music_data") or {}).get("spotify_data") or {}
                tid = sp.get("track_id")
                if not tid:
                    continue
                t = tracks.setdefault(tid, {
                    "track_id": tid, "title": (sp.get("track_name") or "").strip(),
                    "artist": (sp.get("artist") or "").strip(),
                    "style": block["music_data"].get("music_style"),
                    **{k: num(sp.get(k)) for k in NUMERIC},
                    "listens": 0, "sam_arousal": [], "sam_pleasure": [], "hr_change": []})
                t["listens"] += 1
                iv = block.get("interview") or {}
                for src, dst in (("emotion_sam_arousal", "sam_arousal"), ("emotion_sam_pleasure", "sam_pleasure")):
                    if num(iv.get(src)) is not None:
                        t[dst].append(num(iv[src]))
                hr = ((block.get("watch_data") or {}).get("heart_rate") or {})
                if hr.get("value") and hr.get("time_s"):
                    a = window_mean(hr["value"], hr["time_s"], 0, 10)
                    b = window_mean(hr["value"], hr["time_s"], 30, 40)
                    if a and b:
                        t["hr_change"].append(b - a)

    out = []
    for t in tracks.values():
        for k in ("sam_arousal", "sam_pleasure", "hr_change"):
            v = t.pop(k)
            t[f"{k}_mean"] = round(statistics.mean(v), 2) if v else None
        tempo, energy = t["tempo"], t["energy"]
        t["felt_bpm"] = round(tempo / 2, 1) if tempo and tempo >= 2 * CALM_BPM[0] else tempo
        t["key_name"] = f"{KEYS[int(t['key'])]} {'major' if t['mode'] == 1 else 'minor'}" if t["key"] is not None and t["key"] >= 0 else ""
        slow = t["felt_bpm"] is not None and CALM_BPM[0] <= t["felt_bpm"] <= CALM_BPM[1]
        low = energy is not None and energy <= MAX_ENERGY
        t["calm"] = bool(slow and low)
        t["calm_reason"] = (f"~{t['felt_bpm']:.0f} bpm felt, energy {energy:.2f}" if t["calm"] else
                            ("energy too high" if not low else "tempo outside 55-70 bpm"))
        t["uri"] = f"spotify:track:{t['track_id']}"
        out.append(t)
    out.sort(key=lambda t: (not t["calm"], t["energy"] if t["energy"] is not None else 9))
    OUT.write_text(json.dumps({"source": "Music-Mouv (Spotify descriptors); no audio included",
                               "calm_rule": __doc__.split("Ranking")[0].split("Calm list")[1].strip(),
                               "tracks": out}, indent=1), encoding="utf-8")
    calm = [t for t in out if t["calm"]]
    print(f"{len(out)} unique Spotify tracks, {len(calm)} on the calm list -> {OUT}")
    energies = sorted(t["energy"] for t in out if t["energy"] is not None)
    print("energy quartiles:", [round(energies[int(len(energies) * q)], 2) for q in (0.1, 0.25, 0.5, 0.75)])
    for t in calm[:15]:
        print(f"  {t['energy']:.2f} {t['felt_bpm']:>5} {t['title'][:32]} - {t['artist'][:20]} ({t['style']})")


if __name__ == "__main__":
    main(sys.argv[1])
