/* Display only: all selection, prediction and learning remain in the backend. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.ResonanceObserver = api;
})(globalThis, function () {
  const missing = '—';
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const bpm = value => finite(value) ? `${value.toFixed(2)} bpm` : missing;
  const text = value => value == null || value === '' ? missing : String(value);

  function view(state) {
    const adaptive = state.adaptive || {};
    const observation = adaptive.last_observation;
    const pre = observation?.context?.mean;
    const post = observation?.later_hr;
    const accepted = observation?.updated === true;
    let status = 'Waiting for eligible playback';
    if (state.stopped) status = 'Stopped';
    else if (state.nurse_present) status = 'Nurse present';
    else if (adaptive.error) status = 'Learning paused';
    else if (state.source_kind === 'recorded') status = 'Replay only';
    else if (!state.learning_enabled) status = 'Learning disabled';
    else if (adaptive.external) status = 'External adaptive layer';
    else if (adaptive.updating) status = 'Updating response model';
    else if (state.active_exposure_id) status = 'Observing response';
    return {
      status,
      mode: state.source_kind === 'live' ? 'Live Garmin' : state.source_kind === 'recorded' ? 'Recorded replay' : state.simulated ? 'Simulation' : missing,
      patient: text(state.patient_id), session: text(state.session_id),
      exposure: text(state.active_exposure_id), updates: text(adaptive.updates),
      provenance: !observation ? 'No response recorded' : accepted
        ? observation.source_kind === 'live' ? 'Live response' : 'Simulated response' : 'Rejected observation',
      scope: !observation ? missing : observation.session_id === state.session_id ? 'Current session' : 'Previous session',
      song: text(observation?.song_id),
      pre: bpm(pre), post: bpm(post),
      delta: accepted && finite(pre) && finite(post) ? bpm(post-pre) : missing,
      forecast: bpm(observation?.state_only_trend_forecast),
      reward: accepted && finite(observation?.reward) ? observation.reward.toFixed(3) : missing,
      rewardSource: !accepted ? missing : observation.reward_source === 'comfort'
        ? 'Comfort rating' : 'HR proxy (comfort unknown)',
      comfort: finite(observation?.comfort_rating) ? `${observation.comfort_rating} / 5` : missing,
      reason: text(adaptive.error || observation?.reason || adaptive.waiting_reason),
    };
  }

  function render(state, doc = globalThis.document) {
    for (const [key, value] of Object.entries(view(state))) {
      const element = doc.getElementById(`obs-${key}`);
      if (element) element.textContent = value;
    }
  }
  return {view, render};
});
