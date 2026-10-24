# Connected response-learning adapter

The controller now connects to a persistent, per-person response learner. It
continues to own baseline collection, elevated-state detection, Stop, nurse
presence, silence and session caps. Existing DEAM-arousal ordering remains the
cold-start prior within the calm list. This change builds on `chc166/dev`
revision `446f7d3fae59eb06e28ec08fc09feca34c307a4b`, including CSV uploads and cross-platform keyboard support.

The new learning/control path currently accepts **interactive simulated HR
only**. Recorded HR cannot label responses to newly selected songs. It is not a
validated treatment or comfort predictor; the earlier real-data benchmark did
not show added HR-prediction value from audio features.

## Run the connected loop

From the repository root, with Python 3.12:

```sh
pip install -r resonance/requirements-adaptive.txt
python resonance/controller.py --source sim --sim-response --demo-approve-calm --patient-id sim-patient-01 --adaptive-state data/raw/adaptive-patient-01.json
```

This is one process: the adapter is already connected inside the controller.
The optional console remains at `http://127.0.0.1:8765`. The controller must have
the existing local audio cache in `data/raw/audio`; the repository does not
contain the MP3s. With no eligible cached tracks it stays silent. Existing
`resonance/fetch_audio.py` downloads the current calm-list audio cache; no dataset is downloaded
by starting the adapter.

`--demo-approve-calm` explicitly approves the cached calm list **for simulation**.
For an explicit patient/profile allowlist, replace that flag with
`--approved-tracks path/to/approvals.json`, whose format is:

```json
{"sim-patient-01": {"jamendo:YOUR_ACTUAL_LIBRARY_ID": 1.0}}
```

Use real IDs from `app/data/music_library.json`; the placeholder above is not a
playable track. Preferences are [0,1]. Only the highest preference tier is
explored. An acoustic `calm` label is not patient approval. Existing global
preference/exclusion filtering remains in effect before this additional list.

`--sim-response` enables deliberately invented patient-by-song HR effects,
natural drift and seeded noise. `--no-audio-effect` instead enables a matched
synthetic control where song choice has no HR effect. Hidden response parameters
live in `response_simulator.py` and are not exported to the learner. Omitting
both keeps the original rise/rest simulator, which does not react to songs.
`--seed` controls synthetic noise and exploration; patient/song effects remain
stable for a patient identity. Never interpret synthetic performance as clinical
evidence. No labels are fabricated for DEAM, HKU956 or Music-Mouv.

Stop and nurse inputs retain their existing UI/keyboard behavior. Source
rise/rest and threshold changes invalidate unfinished response windows. Nurse
exit in adaptive mode opens a new eligible decision rather than silently
resuming the previous observation. Adaptive audio playback requires `--speed 1`;
use the offline virtual-clock benchmark below for faster experiments.

Console CSV upload/replay remains available. Switching sources creates a new
session and invalidates unfinished windows while retaining learned person
models. Replay is logging-only for the learner; it can run at the existing
accelerated speeds. Returning to simulated input restores the configured
synthetic response mode and normal 1× timing.

## External HTTP adapter (alternative to embedded mode)

```sh
python resonance/controller.py --source sim --sim-response --demo-approve-calm --patient-id sim-patient-01 --external-selection
python resonance/adaptive_adapter.py --url http://127.0.0.1:8765 --state data/raw/adaptive-patient-01.json
```

Choose one mode/one checkpoint writer. Embedded mode rejects external selection
requests. The external adapter polls every 0.5 s, but learns from unique 1-Hz
sample IDs, never poll count or repeated HR values. Its `--once` option performs
one poll for diagnostics. It never calls `/input` or toggles Stop/nurse controls.
An interrupted connection does not authorize a selection; stale requests are
rejected by the controller.

## Versioned controller contract

`contract_version` is `resonance-adaptive/1`. Legacy display fields remain.

- **GET `/state`:** patient/session IDs; source kind; raw and smoothed HR;
  sample index/source time; signal/end/control flags; active exposure ID;
  a selection opportunity containing an opaque decision ID, control revision,
  catalog hash and eligible candidates. Candidate IDs are namespaced
  `jamendo:<id>`, with a strict acoustic-feature allowlist and preferences.
- **GET `/telemetry?session_id=...&after_seq=N`:** ordered structured samples,
  playback requests, mixer starts, ends/interruptions, source and control events.
  Responses include latest sequence and an explicit `gap` flag when history
  was evicted. The 4,096-record ring is separate from the human-readable log.
- **POST `/select`:** version, patient/session IDs, unique `request_id`, decision
  ID, control revision, namespaced song ID, `policy: adaptive-v1` and selection
  probability. The controller rechecks controls, eligibility and cached audio
  under its lock. Invalid/stale requests return 409. Identical retries return
  the original acknowledgement without replay; changed payloads using the same
  request ID are rejected. Accepted means queued, not confirmed playback.

Playback notifications originate from the player. A requested track cannot
teach until the mixer has actually started it. Stop cancels in-flight fades;
a superseded request or decoder failure never produces a successful learning
window. The adapter records actual source sample time, not HTTP or wall-clock
poll time. No real wearable input is connected by this change.

## Observation and learning protocol

Each exposure has a globally unique ID within its session. The adapter requires:

1. 30 consecutive 1-Hz raw samples in `[-30,0)` before acknowledged mixer start,
   silent and free of control/source changes. Baseline must be established.
2. At least 60 s since the last simulator rise/rest command, plus a stable pre-trend: absolute fitted slope at most 0.05 BPM/second. This is an
   unvalidated demo exclusion rule, not a clinical threshold. If HR is already
   materially changing, the adapter waits rather than crediting natural recovery.
3. 30 consecutive raw samples in `[30,60)` after mixer start, with the same
   exposure and baseline. The first 30 s cover fade/settling transitions.
4. No Stop, nurse presence, source command, threshold change, invalid signal,
   replacement, source end or telemetry gap during the observation.

The update occurs after the last sample in that response window. It does not
force a track switch; new rankings apply at the next controller opportunity.
Incomplete/interrupted windows are recorded with rejection reasons, never
imputed. A checkpoint commits model changes, consumed exposure IDs and cursors
in a single atomic JSON replacement; retries/restarts cannot apply an exposure
twice. A new controller session invalidates old pending windows while retaining
the person's learned model. Use one process per checkpoint file. Checkpoints
contain simulated observations and are intended to stay in gitignored
`data/raw/`, not in commits. An embedded checkpoint/contract error leaves the
adaptive path silent until restarted; it does not silently fall back to a
second selection owner.

The exploratory reward compares observed distance from the personal reference
to a **pre-trend-only forecast**:

```
forecast = pre_mean_hr + 60 * pre_slope
reward = clip((abs(forecast - reference_hr)
               - abs(post_mean_hr - reference_hr)) / 10, -1, 1)
```

The 60 s is the separation between the pre/post window midpoints. This simple
linear extrapolation is an engineering comparator, not a fitted causal
counterfactual; stability gating and no-effect controls help expose confounding.
The objective is not the lowest HR: excessive movement below reference can
receive negative reward. HR is not a comfort measurement. Explicit patient
preferences/exclusions remain separate inputs.

Per-person online ridge regression uses nine native acoustic fields plus initial
HR deviation and pre-slope. Fixed, bounded feature scales are versioned as
`native-acoustics-state/1`. A shrinkage estimate of observed reward for each
person/song complements the shared acoustic estimate. Ten-percent epsilon-greedy
exploration is limited to approved candidates tied for highest preference.
Choices log their selection probability. A weak ordering prior retains the
existing DEAM-arousal ranking until observations outweigh it.

No HKU HR aggregate, listener count, song/patient ID or post-exposure value is a
regression predictor. Song IDs index per-song response history; they are not
numeric acoustic features. This layer does not change the fixed DEAM model or
infer matching songs by numeric ID across datasets. This branch already contains
verified library DEAM features from the upstream integration.

## Tests and synthetic comparison

```sh
pytest -q resonance/test_adaptive_integration.py
python resonance/benchmark_adaptive.py --output resonance/adaptive_benchmark.json
```

The benchmark uses the actual controller/adapter, real catalog descriptors,
virtual playback acknowledgements and the explicitly synthetic response source.
It compares adaptive selection to fixed DEAM-arousal ordering with matched
noise seeds and identical observation gates, including a no-audio-effect
condition. It does not play real audio or train on hidden simulator parameters.
The JSON report contains the exact run settings and results; interpret it as
software verification only. The real audio path is separately tested with the
pygame mixer and SDL's dummy audio driver, checking start acknowledgement and
Stop during fade-in.

The committed small benchmark uses three synthetic patient profiles and eight
exposures each, separately for two policies and two conditions (96 completed
observation windows). Final-half mean reward was 0.471 for adaptive selection
versus 0.499 for the fixed prior under invented song effects. Under the
no-audio-effect control both were 0.012. This run does **not** establish a
performance advantage for adaptation; exploration and limited observations can
reduce short-run reward. It verifies the connected mechanics and exposes the
limits of observational reward. Keep the saved report alongside any demo claims.
