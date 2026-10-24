"""Label each library track calm / not calm and record the reason. Updates app/data/music_library.json.

The brief's calm list is "slow, steady, low-intensity tracks (roughly 55-70 bpm)" (Bernardi 2006).
Automatic tempo is ambiguous on this library: librosa's estimate leans toward ~120 bpm, so slow
pieces often read at double time, and ambient pieces have no steady beat at all. So calm means:
  - sparse notes (onset density <= 2.5/s, about the bottom 15% of the library), and either
  - a steady pulse whose felt tempo is 55-70 bpm (readings of 110-140 count at half time), or
  - ambient texture with almost no note onsets (<= 1.5/s), where tempo is not meaningful.
Onset density and the half-time reading are engineering heuristics, not trial-tested measures.

Usage: python -I analysis/classify_calm.py
"""
import json
from pathlib import Path

LIBRARY = Path(__file__).resolve().parent.parent / "app" / "data" / "music_library.json"
CALM_BPM = (55, 70)
SPARSE = 2.5
AMBIENT = 1.5
COLLECTION_LICENSE = "CC (collection described as CC-BY by Hu et al. 2022; per-track license not verified)"


def felt_bpm(bpm):
    return round(bpm / 2, 1) if bpm >= 2 * CALM_BPM[0] else bpm


def classify(t):
    t["felt_bpm"] = felt_bpm(t["bpm"])
    sparse = t["onsets_per_s"] <= SPARSE
    pulse = t["bpm_reliable"] and CALM_BPM[0] <= t["felt_bpm"] <= CALM_BPM[1]
    if sparse and t["onsets_per_s"] <= AMBIENT:
        t["calm"], t["calm_reason"] = True, f"ambient: {t['onsets_per_s']} note onsets/s, no meaningful beat"
    elif sparse and pulse:
        half = " (half-time reading)" if t["felt_bpm"] != t["bpm"] else ""
        t["calm"], t["calm_reason"] = True, f"slow pulse ~{t['felt_bpm']:.0f} bpm{half}, sparse notes"
    else:
        t["calm"], t["calm_reason"] = False, ("notes too dense" if not sparse else "tempo outside 55-70 bpm")
    if not t.get("license"):
        t["license"] = COLLECTION_LICENSE
    return t


def main():
    data = json.loads(LIBRARY.read_text(encoding="utf-8"))
    data["tracks"] = [classify(t) for t in data["tracks"]]
    data["calm_rule"] = __doc__.split("Usage:")[0].strip()
    LIBRARY.write_text(json.dumps(data, indent=1), encoding="utf-8")
    calm = [t for t in data["tracks"] if t["calm"]]
    print(f"{len(calm)} of {len(data['tracks'])} tracks on the calm list "
          f"({sum(r['calm_reason'].startswith('ambient') for r in calm)} ambient)")


if __name__ == "__main__":
    main()
