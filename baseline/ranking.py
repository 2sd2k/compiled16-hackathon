"""Transparent preference-first heuristic; not a learned clinical response model."""
import math


def rank_music(candidates, preferences, excluded=(), target_arousal=3.0, target_valence=6.5, valence_weight=1.0):
    """Candidates contain song_id and predicted_arousal_mean/predicted_valence_mean.

    Preferences map song IDs to [0,1]; omitted preference is unknown (0.5).
    Higher stated preference ranks first; affect-distance breaks preference ties.
    Targets and formula are configurable engineering priors on DEAM's 1–9 scale.
    valence_weight scales the valence term; 0 ranks on arousal only (predicted valence did not
    track listener valence on the HKU956/Jamendo library; see analysis/out/deam_on_hku_validation.json).
    """
    if not all(math.isfinite(v) and 1 <= v <= 9 for v in [target_arousal, target_valence]):
        raise ValueError('Affect targets must be finite in [1,9]')
    if not math.isfinite(valence_weight) or valence_weight < 0:
        raise ValueError('valence_weight must be finite and non-negative')
    results = []
    for song in candidates:
        sid = song['song_id']
        if sid in excluded:
            continue
        preference = preferences.get(sid, .5)
        a, v = song['predicted_arousal_mean'], song['predicted_valence_mean']
        if not all(math.isfinite(n) for n in [preference, a, v]) or not 0 <= preference <= 1:
            raise ValueError('Invalid preference or affect prediction')
        # Clip only for ranking; raw inference and evaluation remain unclipped.
        a, v = min(9, max(1, a)), min(9, max(1, v))
        distance = ((a-target_arousal)/8)**2 + valence_weight*((v-target_valence)/8)**2
        results.append({**song, 'preference': preference, 'preference_known': sid in preferences,
                        'affect_distance': distance, 'policy': 'preference_then_affect_heuristic'})
    return sorted(results, key=lambda row: (-row['preference'], row['affect_distance'], str(row['song_id'])))


def simulated_hr_hook(hr, reference_hr, *, stop=False, comfortable=True, care_present=False, signal_ok=True, simulated=True):
    """Stateless simulation hook: no device, playback, or physiological-effect prediction.

    Reference HR is person-specific. The 10 BPM rule and targets are unvalidated
    demo parameters, not clinical thresholds; this hook refuses real telemetry.
    """
    if not simulated:
        raise ValueError('Simulation only; real wearable control is not implemented')
    base = {'simulated': True, 'clinical_use': False}
    if stop or not comfortable:
        return {**base, 'action': 'stop'}
    if care_present:
        return {**base, 'action': 'pause'}
    if not signal_ok or hr is None or reference_hr is None:
        return {**base, 'action': 'hold'}
    if not all(math.isfinite(n) and n > 0 for n in [hr, reference_hr]):
        return {**base, 'action': 'hold'}
    elevated = hr-reference_hr > 10
    return {**base, 'action': 'rank', 'target_arousal': 2.5 if elevated else 3.5, 'target_valence': 6.5}
