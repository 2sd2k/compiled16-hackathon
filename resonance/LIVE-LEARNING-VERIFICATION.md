# Live Garmin learning verification

## Merge verification for main `bb2722f`

Merged the teammate's Garmin-default startup and return-to-live console action.
Jamendo remains the default catalog. Plain startup shows live readings with fixed
ranking; an explicit profile and approvals enable live adaptive learning.
`--simulated` starts the simulated adaptive test flow; `--classic` starts its
fixed-ranking alternative. `--live --classic` retains live fixed ranking.

Post-merge checks: **77 Python integration tests, 7 frontend tests, and 5 Garmin
packet-parser tests passed (89 total)**. New checks cover all startup variants,
profile checkpoint selection, and return from replay to the pinned live watch
with the existing live learner. The frontend test verifies its return button
sends `source:live`. Whitespace checks pass. Hardware limitations below still apply.

## Behavior

Jamendo is now the default audio catalog. The controller can learn per-volunteer
preferences during live Garmin playback or during simulation. Live and simulated
checkpoints cannot be interchanged. Recorded replay remains ineligible for new
response learning.

```sh
python resonance/launch.py --live --patient-id volunteer-01 --approve-calm
```

Use a distinct volunteer ID for each person. The approval flag allows the
available calm catalog; `--approved-tracks FILE` permits a narrower profile list.
The launcher now refuses to silently reopen a simulator, different profile,
different explicitly selected watch, or different learning mode. Stop the old
backend with Ctrl+C before switching. `--live --classic` retains fixed ranking.

Learning requires a baseline, stable pre-playback readings, confirmed local
audio, and a complete post-playback window. Each BLE packet is consumed at most
once. Live receipt times and controller tick intervals are checked. Missing
readings, Stop, nurse presence, clock gaps and control changes reject interrupted
windows. Reconnects remain pinned to the same sensor.

The console accepts one optional comfort rating per exposure. Valid comfort
feedback takes priority over the HR proxy, while still requiring an intact
response window. The observer labels live versus simulated outcomes and shows
the reward source. HR proxy outcomes do not establish comfort or causation.

## Evidence

- Initial regression run: 3 failures and 7 setup errors, covering repeated BLE
  samples, unavailable live mode, and launcher reuse. Test checkpoint: `e8db8dc`.
- Full Python run after implementation: 67 passed across live learning,
  reconciliation, adaptive integration, Spotify and Garmin bridge suites.
- Two additional focused tests passed: live launcher command construction and
  reconnect refusal for a different watch. No implementation changed afterward.
- Frontend suite: 7 passed, including live provenance and actual feedback request
  construction in the mocked console. observer.js line/branch/function coverage:
  100%.
- Garmin packet parser: 5 passed.
- Python review found and verified fixes for sensor identity on reconnect,
  feedback after invalidation, and launcher mode/device mismatches. Final review:
  no remaining HIGH or CRITICAL findings.

## Limits and self-evaluation

No physical Garmin connection or audible playback was exercised in this session.
BLE packets, playback confirmations and clock progression were faked. Browser
behavior was checked with a mocked DOM, not visual browser inspection. Tests used
local Python 3.9 / NumPy 2.0.2; the documented deployment target is Python 3.12.
Conservative timing rules can reject Bluetooth-jittered windows; check the actual
watch before relying on the demo. No efficacy claim is supported by these tests.

Self-evaluation: accuracy 4/5 (automated evidence, hardware unverified),
completeness 4/5 (implemented loop and feedback; device trial remains), clarity
4/5 (source/reward labels explicit; some rejection codes remain technical),
actionability 4/5 (one launch command; requires approved local audio and watch),
conciseness 4/5 (details isolated here). Overall 4.0/5. No critical gaps in the
verified software scope. Highest-impact next check: run the command on the
watch-connected computer, confirm live readings, and complete one uninterrupted
playback window. This assessment should match the user's expectation of working
software with hardware verification stated explicitly.
