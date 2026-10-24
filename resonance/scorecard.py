"""Grade the controller's actions on the labeled fake heart-rate datasets, silently and fast.

Runs the real Controller tick by tick with a stand-in player (no audio, clips "end" after 90 s of
data time), then compares what the room did with each dataset's phase labels. This measures
whether the system acts correctly and on time. The fake heart rate does not respond to the music,
so it says nothing about whether music lowers heart rate.

Usage: python resonance/scorecard.py
"""
import collections
import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import controller as c  # noqa: E402

CLIP_S = 90  # Jamendo clips; Spotify tracks use their own duration_ms
MUSIC = "jamendo" if "--jamendo" in sys.argv else "spotify"


class NullPlayer:
    """Same interface as controller.Player, counting data seconds instead of playing audio."""

    def __init__(self):
        self.track, self.ctrl, self.started = None, None, 0

    def play(self, track):
        self.track, self.started = track, self.ctrl.t

    def silence(self, fade_s=0):
        self.track = None

    def ended(self):
        length = ((self.track or {}).get("duration_ms") or CLIP_S * 1000) / 1000
        return self.track is not None and self.ctrl.t - self.started >= length


def run(csv_path, labels_path, threshold=10):
    player = NullPlayer()
    ctrl = c.Controller(c.make_source(f"csv:{csv_path}"), threshold=threshold, player=player, quiet=True,
                        music=MUSIC)
    player.ctrl = ctrl
    timeline = []  # (t, room, song_id or None)
    while not ctrl.done:
        ctrl.tick()
        if not ctrl.done:
            timeline.append((ctrl.t, ctrl.room_state(), player.track["song_id"] if player.track else None))
    with open(labels_path, newline="") as f:
        phases = [(int(r["start_s"]), int(r["end_s"]), r["label"]) for r in csv.DictReader(f)]
    return ctrl, timeline, phases


def report(name, ctrl, timeline, phases):
    print(f"\n=== {name} (FAKE data, {len(timeline)} s) ===")
    print(f"baseline {ctrl.baseline:.0f} bpm, threshold +{ctrl.thr} bpm")
    by_t = {t: (room, song) for t, room, song in timeline}
    print(f"{'phase':<10}{'seconds':>9}{'music':>8}{'elevated':>10}")
    for start, end, label in phases:
        ts = [t for t in range(start + 1, end + 1) if t in by_t]
        if not ts:
            continue
        music = sum(by_t[t][1] is not None for t in ts) / len(ts)
        elev = sum(by_t[t][0] == "Elevated" for t in ts) / len(ts)
        print(f"{label:<10}{len(ts):>9}{music:>8.0%}{elev:>10.0%}")

    rise = next((s for s, _, lab in phases if lab in ("rising", "stress", "mild")), None)
    first_music = next((t for t, _, song in timeline if song), None)
    rest_music = sum(1 for t, _, song in timeline if song and any(
        s < t <= e and lab == "rest" and s < (rise or 10**9) for s, e, lab in phases))
    tracks = [song for _, (_, song) in sorted(by_t.items())]
    played = [k for k, _ in collections.Counter(s for s in tracks if s).most_common()]
    if rise is None:
        print(f"verdict: resting only -> music during rest: {rest_music} s "
              f"({'PASS, no false alarm' if not first_music else 'FAIL, false alarm'})")
    else:
        lat = f"{first_music - rise} s after the rise began" if first_music else "never"
        print(f"music started {lat}; music before the rise: {rest_music} s; distinct tracks: {len(played)}")
    titles = {t["song_id"]: (t.get("title") or t["song_id"]) for t in ctrl.calm}
    if played:
        print("tracks: " + "; ".join(titles.get(s, s) for s in played))
    for e in reversed(ctrl.events):
        if any(k in e["msg"] for k in ("Elevated", "silence", "Resting", "fades", "playing")):
            print(f"  - {e['msg'][:150]}")


def sweep(datasets):
    """Threshold trade-off: earlier detection and catching mild rises vs false alarms at rest."""
    print("\n=== threshold sweep (FAKE data) ===")
    print(f"{'threshold':<11}{'stress: music starts':>22}{'mild +8 rise caught':>21}{'rest-only false alarm':>23}")
    for thr in (6, 8, 10, 12, 15):
        cells = []
        for name in ("fake_stress_episode", "fake_mild_rise", "fake_resting_only"):
            ctrl, timeline, phases = run(*datasets[name], threshold=thr)
            rise = next((s for s, _, lab in phases if lab != "rest"), None)
            first = next((t for t, _, song in timeline if song), None)
            if name == "fake_stress_episode":
                cells.append(f"{first - rise} s after rise" if first else "never")
            elif name == "fake_mild_rise":
                cells.append("yes" if first else "no")
            else:
                cells.append(f"{sum(1 for _, _, s in timeline if s)} s music")
        print(f"+{thr:<10}{cells[0]:>22}{cells[1]:>21}{cells[2]:>23}")


def main():
    datasets = {}
    for csv_path in sorted((HERE / "fake_data").glob("fake_*[!s].csv")):
        labels = csv_path.with_name(csv_path.stem + "_labels.csv")
        if labels.exists():
            datasets[csv_path.stem] = (csv_path, labels)
            report(csv_path.stem, *run(csv_path, labels))
    sweep(datasets)


if __name__ == "__main__":
    main()
