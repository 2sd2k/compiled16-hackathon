"""Versioned telemetry/selection hooks; the controller remains the playback authority."""
import collections
import hashlib
import json
import math
import queue
import uuid
try:
    from .adaptive_adapter import CONTRACT, AUDIO
except ImportError:
    from adaptive_adapter import CONTRACT, AUDIO


def song_id(value):
    value = str(value)
    if value.startswith('jamendo:'):
        return value
    if ':' in value or not value:
        raise ValueError('Expected a Jamendo library song ID')
    return 'jamendo:'+value


class AdaptiveControllerMixin:
    def _adaptive_init(self, patient_id, adapter, external_selection, approved_ids):
        self.patient_id, self.session_id = patient_id, str(uuid.uuid4())
        self.adapter, self.external_selection = adapter, external_selection
        self.selection_enabled = bool(adapter or external_selection) and bool(getattr(self.source, 'interactive_simulation', False))
        self.approved_ids = {song_id(k): float(v) for k, v in (approved_ids or {}).items()}
        if any(not math.isfinite(v) or not 0 <= v <= 1 for v in self.approved_ids.values()):
            raise ValueError('Approval preferences must be [0,1]')
        self._seq = self.control_revision = self.source_revision = 0
        self._source_changed_at = 0
        self._telemetry = collections.deque(maxlen=4096)
        self._player_events = queue.SimpleQueue()
        self._active_exposure = None
        self._audio_busy_until = -1
        self._opportunity = None
        self._selections = collections.OrderedDict()
        self._playback_metadata = {}
        self._known_playbacks = set()
        self._adaptive_fault = None
        self.signal_ok = False
        self.player.on_event = self._queue_player_event
        self._baseline_samples = []

    def _queue_player_event(self, kind, track, exposure_id, **details):
        # Worker never takes the controller lock; callback time is source time.
        self._player_events.put((self.t, kind, track, exposure_id, details))

    def _emit(self, kind, sample_index=None, **fields):
        self._seq += 1
        record = {'contract_version': CONTRACT, 'seq': self._seq, 'type': kind,
                  'patient_id': self.patient_id, 'session_id': self.session_id,
                  'sample_index': self.t if sample_index is None else sample_index,
                  'sample_time_s': float(self.t if sample_index is None else sample_index),
                  'source_kind': 'simulated' if getattr(self.source, 'interactive_simulation', False) else 'recorded',
                  'simulated': bool(getattr(self.source, 'interactive_simulation', False)),
                  'source_revision': self.source_revision, 'source_changed_at': self._source_changed_at, **fields}
        self._telemetry.append(record)
        return record

    def _drain_player_events(self):
        while not self._player_events.empty():
            index, kind, track, exposure, details = self._player_events.get()
            if kind == 'playback_requested':
                self._known_playbacks.add(exposure)
            elif exposure not in self._known_playbacks:
                continue  # late worker callback from a previous source/session
            metadata = self._playback_metadata.get(exposure, {})
            if kind == 'playback_started':
                if self.stopped or self.nurse or self.done or self.baseline is None or not self.signal_ok or self.t < self.pause_until:
                    self.player.silence(fade_s=.3)
                    kind, details = 'playback_interrupted', {'reason': 'controls_changed_before_start'}
                else:
                    self._active_exposure = exposure
            was_active = self._active_exposure == exposure
            if kind in ('playback_ended', 'playback_interrupted'):
                if was_active:
                    self._active_exposure = None
                self._audio_busy_until = max(self._audio_busy_until, self.t+math.ceil(details.get('fade_s', 0)))
            if (kind == 'playback_started' or (kind in ('playback_ended', 'playback_interrupted') and was_active)) and hasattr(self.source, 'playback'):
                self.source.playback(song_id(track['song_id']) if kind == 'playback_started' else None)
            # Transport only the permitted acoustic fields, not aggregate HKU outcomes.
            acoustic = {k: track.get(k) for k in AUDIO}
            self._emit(kind, sample_index=index, exposure_id=exposure, song_id=song_id(track['song_id']),
                       track=acoustic, baseline=self.baseline, **metadata, **details)

    def _control_changed(self, action, source=False):
        self.control_revision += 1
        self._opportunity = None
        if source:
            self.source_revision += 1
            self._source_changed_at = self.t
        self._emit('source_changed' if source else 'control_changed', action=action)

    def _sample_event(self, value):
        self._emit('sample', hr_raw_bpm=value if self.signal_ok else None,
                   hr_smoothed_bpm=self.smooth[-1] if self.smooth else None, baseline=self.baseline,
                   signal_ok=self.signal_ok, stopped=self.stopped, nurse_present=self.nurse,
                   audio_busy=bool(self._active_exposure or self.t < self._audio_busy_until),
                   exposure_id=self._active_exposure, source_revision=self.source_revision,
                   source_changed_at=self._source_changed_at)

    def _candidate(self, track):
        sid = song_id(track['song_id'])
        return {'song_id': sid, 'preference': self.approved_ids[sid], **{k: track.get(k) for k in AUDIO}}

    def _offer_selection(self, choices):
        if self._adaptive_fault:
            return
        choices = [t for t in choices if song_id(t['song_id']) in self.approved_ids]
        if not choices:
            self._opportunity = None
            return
        if self._opportunity is None:
            candidates = [self._candidate(t) for t in choices]
            digest = hashlib.sha256(json.dumps(candidates, sort_keys=True).encode()).hexdigest()
            self._opportunity = {'decision_id': str(uuid.uuid4()), 'control_revision': self.control_revision,
                                 'candidates': candidates, 'catalog_version': digest}

    def _adaptive_run(self):
        if self.adapter and not self._adaptive_fault:
            try:
                with self.lock:
                    batch = self._telemetry_unlocked(self.adapter.cursor(self.session_id))
                    snapshot = self.snapshot()
                self.adapter.ingest(batch)
                request = self.adapter.choose(snapshot)
                if request:
                    with self.lock:
                        try:
                            self._select_unlocked(request)
                        except ValueError:
                            pass  # controls can change while the adapter is computing
            except (OSError, ValueError, KeyError, TypeError) as error:
                with self.lock:
                    self._adaptive_fault = str(error)
                    self._opportunity = None
                    self.player.silence(fade_s=.3)
                    self._emit('adapter_error', reason=str(error))
                    self.log(f"Adaptive adapter paused after an error: {error}")

    def _telemetry_unlocked(self, after):
        earliest = self._telemetry[0]['seq'] if self._telemetry else self._seq+1
        gap = after < earliest-1 or after > self._seq
        return {'contract_version': CONTRACT, 'session_id': self.session_id, 'patient_id': self.patient_id,
                'latest_seq': self._seq, 'gap': gap,
                'records': [] if gap else [r for r in self._telemetry if r['seq'] > after]}

    def telemetry(self, after=0, session_id=None):
        with self.lock:
            if session_id is not None and session_id != self.session_id:
                raise ValueError('Session changed')
            if after < 0:
                raise ValueError('Cursor must be nonnegative')
            return self._telemetry_unlocked(after)

    def select(self, request):
        with self.lock:
            if self.adapter is not None:
                raise ValueError('Embedded adapter owns selection; external selection is disabled')
            return self._select_unlocked(request)

    def _select_unlocked(self, request):
        request = dict(request)
        request_id = request.get('request_id')
        if not isinstance(request_id, str) or not request_id:
            raise ValueError('request_id required')
        if request_id in self._selections:
            previous, result = self._selections[request_id]
            if request != previous:
                raise ValueError('request_id reused with a different payload')
            return result
        opportunity = self._opportunity
        if (request.get('contract_version') != CONTRACT or request.get('session_id') != self.session_id or
                request.get('patient_id') != self.patient_id or request.get('policy') != 'adaptive-v1'):
            raise ValueError('Invalid selection identity/contract/policy')
        if (not self.selection_enabled or not opportunity or self.stopped or self.nurse or self.done or
                not self.signal_ok or self.baseline is None or not self.elevated or self.t < self.pause_until or
                self.player.track is not None):
            raise ValueError('Controller does not permit playback')
        if (request.get('decision_id') != opportunity['decision_id'] or
                request.get('control_revision') != self.control_revision):
            raise ValueError('Stale selection decision')
        eligible = {t['song_id'] for t in opportunity['candidates']}
        selected_id = request.get('song_id')
        if selected_id not in eligible or selected_id not in self.approved_ids:
            raise ValueError('Song not approved/eligible')
        probability = request.get('selection_probability')
        if isinstance(probability, bool) or not isinstance(probability, (int, float)) or not 0 < probability <= 1:
            raise ValueError('Invalid selection probability')
        track = next((t for t in self.calm if song_id(t['song_id']) == selected_id), None)
        if track is None or not self._track_available(track):
            raise ValueError('Selected audio is not locally available')
        exposure = str(uuid.uuid4())
        self._playback_metadata[exposure] = {'decision_id': opportunity['decision_id'], 'policy': 'adaptive-v1',
                                           'selection_probability': probability}
        self._opportunity = None
        self.recent.append(track['song_id'])
        self.player.play(track, exposure_id=exposure)
        self.log(f"Adaptive selection → {track.get('title') or selected_id} (simulated response learning).")
        result = {'accepted': True, 'exposure_id': exposure, 'request_id': request_id, 'playback_confirmed': False}
        self._selections[request_id] = (request, result)
        if len(self._selections) > 1024:
            self._selections.popitem(last=False)
        return result

    def _adaptive_snapshot(self):
        if not self.elevated or self.stopped or self.nurse or self.done or self.t < self.pause_until:
            self._opportunity = None
        return {'contract_version': CONTRACT, 'patient_id': self.patient_id, 'session_id': self.session_id,
                'source_kind': 'simulated' if getattr(self.source, 'interactive_simulation', False) else 'recorded',
                'sample_index': self.t, 'sample_time_s': float(self.t), 'sample_period_s': 1.0,
                'hr_raw_bpm': self.hr[-1] if self.hr else None,
                'hr_smoothed_bpm': self.smooth[-1] if self.smooth else None,
                'signal_ok': self.signal_ok, 'source_ended': self.done, 'stopped': self.stopped,
                'source_revision': self.source_revision, 'source_changed_at': self._source_changed_at,
                'nurse_present': self.nurse, 'selection_opportunity': self._opportunity,
                'active_exposure_id': self._active_exposure, 'learning_enabled': self.selection_enabled,
                'adaptive': {**(self.adapter.summary(self.patient_id, nonblocking=True) if self.adapter else {'external': self.external_selection}),
                             'error': self._adaptive_fault}}
