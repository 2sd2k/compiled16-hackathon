"""Write labeled fake heart-rate datasets (t,hr at 1 Hz, the hr_test.py CSV format).

Each file has a matching labels file (start_s,end_s,label) so the same data can score the rule.
These are synthetic: never present them as real recordings.

Usage: python resonance/make_fake_hr.py
"""
import csv
import math
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent / "fake_data"

# name -> list of (start_s, end_s, label, bpm above baseline at the end of the phase)
SCENARIOS = {
    "fake_stress_episode": [(0, 180, "rest", 0), (180, 240, "rising", 18), (240, 420, "stress", 18),
                            (420, 660, "recovery", 2), (660, 780, "rest", 0)],
    "fake_mild_rise": [(0, 180, "rest", 0), (180, 300, "rising", 8), (300, 480, "mild", 8),
                       (480, 600, "rest", 0)],
    "fake_resting_only": [(0, 600, "rest", 0)],
}


def build(phases, base=72.0, seed=1):
    rng = random.Random(seed)
    hr, lift = [], 0.0
    for start, end, _, target in phases:
        start_lift = lift
        for t in range(start, end):
            frac = (t - start + 1) / (end - start)
            lift = start_lift + (target - start_lift) * frac
            hr.append(round(base + lift + rng.gauss(0, 1.0) + 0.8 * math.sin(t / 9), 1))
    return hr


def main():
    OUT.mkdir(exist_ok=True)
    for i, (name, phases) in enumerate(SCENARIOS.items()):
        hr = build(phases, seed=i + 1)
        with open(OUT / f"{name}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t", "hr"])
            w.writerows(enumerate(hr))
        with open(OUT / f"{name}_labels.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["start_s", "end_s", "label"])
            w.writerows((s, e, lab) for s, e, lab, _ in phases)
        print(f"wrote {name}.csv ({len(hr)} s)")


if __name__ == "__main__":
    main()
