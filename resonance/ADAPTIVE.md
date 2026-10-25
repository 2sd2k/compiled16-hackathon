# Connected response-learning adapter

The controller now connects to a persistent, per-person response learner. It
continues to own baseline collection, elevated-state detection, Stop, nurse
presence, silence and session caps. Existing DEAM-arousal ordering remains the
cold-start prior within the calm list. This change builds on `chc166/dev`
revision `446f7d3fae59eb06e28ec08fc09feca34c307a4b`, including CSV uploads and cross-platform keyboard support.

Jamendo local playback and live Garmin input are the defaults. A plain launcher
starts live readings with fixed ranking; supplying a volunteer profile and audio
approvals enables live learning. `--simulated` explicitly starts the adaptive
simulator, and `--classic` starts the fixed-ranking simulator. `--live --classic`
keeps fixed ranking on the watch. Live Garmin sessions and interactive
simulations can both learn; they use separate checkpoints and explicit source labels.
Recorded replay never learns responses to newly selected songs. Legacy Spotify
simulation support remains opt-in with its separate feature schema.

## Live Garmin learning

```sh
python resonance/launch.py --live --patient-id volunteer-01 --approve-calm
```

Use a different profile ID per volunteer. `--approve-calm` approves the currently
available calm tracks; `--approved-tracks FILE` limits playback to an explicit
profile allowlist. The launcher stores live checkpoints under a hash of the
profile in `data/raw/adaptive-live-jamendo-*.json`, separate from simulations.
It refuses to reuse an existing backend with a different source, profile, or
learning mode. Stop that backend with Ctrl+C before switching.

Direct controller equivalent:

```sh
python resonance/controller.py --source garmin --music jamendo --patient-id volunteer-01 --approve-calm --adaptive-state data/raw/live-volunteer-01.json
```

Garmin notifications are consumed at most once. Repeated poll calls cannot count
a packet twice. Missing, stale, invalid, or no-contact readings invalidate windows.
Live samples carry packet IDs and monotonic receipt times. Controller intervals
must stay within 0.5–1.5 s; 30-sample windows allow at most 2 s total timing drift.
Receipt gaps over 2 s and playback-window misalignment are rejected. Reconnection
stays bound to the selected sensor. These are engineering quality gates, not
clinically validated criteria. No clinical benefit is claimed.

The operator can enter a volunteer's comfort rating (1–5) during the response
window using `POST /feedback` with `session_id`, `exposure_id`, and `rating`.
One rating per exposure is accepted; identical retries are harmless and changed
or late feedback is rejected. A rating never bypasses signal/playback gates.
If provided, reward is `(rating - 3) / 2`; otherwise the existing HR-distance
proxy is used. Both reward source and input provenance appear in the observer.

## Run the connected loop

From the repository root, with Python 3.12:

```sh
pip install -r resonance/requirements-adaptive.txt
python resonance/controller.py --music jamendo --source sim --sim-response --demo-approve-calm --patient-id sim-patient-01 --adaptive-state data/raw/adaptive-patient-01.json
```

This is one process: the adapter is already connected inside the controller.
The optional console remains at `http://127.0.0.1:8765`. For the Jamendo command above, the controller must have
the existing local audio cache in `data/raw/audio`; the repository does not
contain the MP3s. With no eligible cached tracks it stays silent. Existing
`resonance/fetch_audio.py` downloads the current calm-list audio cache; no dataset is downloaded
by starting the adapter.

`--demo-approve-calm` explicitly approves the selected catalog’s available calm list **for simulation**.
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
accelerated speeds. The console's **Back to live Garmin** button reconnects the
selected watch, starts a new baseline at 1× timing, and preserves a configured
live learner. A simulation learner never trains on those live readings. Use
`--simulated` at startup for the synthetic test flow.

## External HTTP adapter (alternative to embedded mode)

```sh
python resonance/controller.py --music jamendo --source sim --sim-response --demo-approve-calm --patient-id sim-patient-01 --external-selection
python resonance/adaptive_adapter.py --mode simulated --music jamendo --url http://127.0.0.1:8765 --state data/raw/adaptive-patient-01.json
```

For live external mode, use `--source garmin` plus a profile and approvals on the controller,
and `--mode live --music jamendo` with a separate checkpoint on the adapter.
For Spotify simulation external mode, use `--music spotify` in both commands and a separate checkpoint.
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
  `jamendo:<id>` or `spotify:<id>`, with catalog-specific feature allowlists and preferences.
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
poll time. Live Garmin input is connected through `garmin_source.py`.

## Observation and learning protocol

The console's response observer reads the existing `/state` snapshot. It shows
patient/session and confirmed exposure IDs, update count, the last response's
pre/post means, observed HR change, reward, and rejection/waiting reason. The
forecast shown is the pre-trend extrapolation defined below, not the separate
Music-Mouv model. Stored observations from an older session remain explicitly
labeled with their original live/simulated provenance when a recorded file is replayed.
External-adapter mode exposes telemetry and selection but does not publish that
external process's checkpoint summary; the observer does not invent zero updates
or response values when that summary is unavailable.

Spotify startup now confirms the requested track through playback state before
emitting `playback_started`; a successful play request alone is insufficient.
Stop invalidates in-flight starts/fades. A remote pause still depends on Spotify
API response latency; this is not a claim of instantaneous remote silence.

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
contain observations for their declared live or simulated mode and stay in gitignored
`data/raw/`, not in commits. An embedded checkpoint/contract error leaves the
adaptive path silent until restarted; it does not silently fall back to a
second selection owner.

When no comfort rating is supplied, the exploratory HR proxy compares observed distance from the personal reference
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

Per-person online ridge regression uses nine catalog-specific audio fields plus initial
HR deviation and pre-slope. Fixed, bounded feature scales are versioned as
`native-acoustics-state/1` or `spotify-descriptors-state/1`. A shrinkage estimate of observed reward for each
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
pytest -q resonance/test_reconciliation.py resonance/test_spotify.py
node --test resonance/test_observer.cjs
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
