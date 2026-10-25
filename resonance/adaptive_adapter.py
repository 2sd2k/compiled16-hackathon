"""Mode-isolated live/simulated response learner and resumable controller adapter.

Run externally: python resonance/adaptive_adapter.py --url http://127.0.0.1:8765
Or use controller.py --adaptive-state PATH for the connected, screenless loop.
"""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import numpy as np

CONTRACT = 'resonance-adaptive/1'
SCHEMA = 'native-acoustics-state/1'
AUDIO = ['felt_bpm', 'loudness_dbfs', 'dynamics_db', 'brightness_hz', 'bass_share',
         'mid_share', 'treble_share', 'onsets_per_s', 'bpm_reliable']
SCALES = np.array([100, 60, 20, 5000, 1, 1, 1, 10, 1])
# Spotify songs (Music-Mouv) carry Spotify's published descriptors instead of waveform features.
# Same width, so the model shape is unchanged; a separate schema keeps checkpoints from mixing them.
SPOTIFY_SCHEMA = 'spotify-descriptors-state/1'
SPOTIFY_AUDIO = ['felt_bpm', 'energy', 'loudness', 'accousticness', 'instrumentalness',
                 'valence', 'danceability', 'speechiness', 'liveness']
SPOTIFY_SCALES = np.array([100, 1, 60, 1, 1, 1, 1, 1, 1])
SCHEMAS = {'jamendo': SCHEMA, 'spotify': SPOTIFY_SCHEMA}
DIM = 1+len(AUDIO)+2


def audio_fields(song_id):
    return SPOTIFY_AUDIO if str(song_id).startswith('spotify:') else AUDIO


def vector(track, context):
    # Explicit allowlist: never include HKU HR outcomes, IDs or post-exposure data.
    fields = audio_fields(track.get('song_id', ''))
    scales = SPOTIFY_SCALES if fields is SPOTIFY_AUDIO else SCALES
    values = np.array([float(track[k]) for k in fields], dtype=float)
    result = np.r_[1, np.clip(values/scales, -2, 2),
                   np.clip((context['mean']-context['baseline'])/20, -2, 2),
                   np.clip(context['slope']/.1, -2, 2)]
    if not np.isfinite(result).all():
        raise ValueError('Nonfinite acoustic/context features')
    return result


class AdaptiveAdapter:
    def __init__(self, path=None, epsilon=.1, seed=42, catalog='jamendo', mode='simulated'):
        if mode not in ('simulated', 'live'):
            raise ValueError('Unsupported learning mode')
        self.mode = mode
        self.path = Path(path) if path else None
        self.lock = threading.RLock()
        self.waiting_reason = 'waiting_for_controller'
        self.schema = SCHEMAS[catalog]
        if self.path and self.path.exists():
            self.state = json.loads(self.path.read_text())
            if self.state.get('contract') != CONTRACT or self.state.get('feature_schema') != self.schema:
                raise ValueError('Incompatible adapter checkpoint (different contract or music catalog)')
            if self.state.get('simulated') is not (mode == 'simulated'):
                raise ValueError('Checkpoint learning mode does not match source mode')
            if not 0 <= self.state.get('epsilon', -1) <= 1:
                raise ValueError('Invalid simulation checkpoint parameters')
            for model in self.state['models'].values():
                a, b = np.asarray(model['A']), np.asarray(model['b'])
                if a.shape != (DIM, DIM) or b.shape != (DIM,) or not np.isfinite(a).all() or not np.isfinite(b).all() or not np.allclose(a, a.T):
                    raise ValueError('Invalid checkpoint model dimensions/values')
                np.linalg.cholesky(a)
        else:
            if not 0 <= epsilon <= 1:
                raise ValueError('epsilon must be [0,1]')
            self.state = {'contract': CONTRACT, 'feature_schema': self.schema, 'simulated': mode == 'simulated',
                          'epsilon': epsilon, 'seed': seed, 'models': {}, 'sessions': {},
                          'observations': {}, 'decisions': {}}

    def _save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name+'.tmp')
            with temporary.open('w') as f:
                json.dump(self.state, f, allow_nan=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, self.path)

    def _check_catalog(self, sid):
        catalog = str(sid).partition(':')[0]
        if SCHEMAS.get(catalog) != self.schema:
            raise ValueError('Music catalog does not match adapter checkpoint schema')

    def _transaction(self, function):
        with self.lock:
            before = copy.deepcopy(self.state)
            try:
                result = function()
                self._save()  # model, consumed exposure IDs and cursor commit together
                return result
            except Exception:
                self.state = before
                raise

    def _session(self, sid, patient):
        if sid not in self.state['sessions']:
            for previous in self.state['sessions'].values():
                if previous['patient_id'] == patient:
                    for exposure in list(previous['pending']):
                        self._finish(previous, exposure, 'controller_session_changed')
                    previous['samples'] = []
            self.state['sessions'][sid] = {'patient_id': patient, 'cursor': 0, 'samples': [], 'pending': {}}
        session = self.state['sessions'][sid]
        if session['patient_id'] != patient:
            raise ValueError('Session cannot change patient identity')
        return session

    def cursor(self, session_id):
        with self.lock:
            return self.state['sessions'].get(session_id, {}).get('cursor', 0)

    def _model(self, patient):
        if patient not in self.state['models']:
            self.state['models'][patient] = {'A': np.eye(DIM).tolist(), 'b': np.zeros(DIM).tolist(), 'updates': 0, 'arms': {}}
        return self.state['models'][patient]

    @staticmethod
    def _valid_timing(samples):
        times = [s['sample_time_s'] for s in samples]
        if any(s.get('source_kind') == 'live' for s in samples):
            ids = [s.get('measurement_id') for s in samples]
            received = [s.get('received_time_s') for s in samples]
            return (all(s.get('source_kind') == 'live' for s in samples)
                    and None not in ids and len(set(ids)) == len(ids)
                    and all(isinstance(t, (int, float)) and math.isfinite(t) for t in received)
                    and all(0 <= t-r <= 3 for t, r in zip(times, received))
                    and all(.5 <= d <= 1.5 for d in np.diff(times))
                    and all(0 < d <= 2 for d in np.diff(received))
                    and abs(times[-1]-times[0]-(len(times)-1)) <= 2)
        return np.allclose(np.diff(times), 1, atol=1e-6, rtol=0)

    @staticmethod
    def _context(samples, end, baseline):
        pre = [s for s in samples if end-30 <= s['sample_index'] < end]
        if len(pre) != 30 or [s['sample_index'] for s in pre] != list(range(end-30, end)):
            raise ValueError('incomplete_pre_window')
        if not AdaptiveAdapter._valid_timing(pre):
            raise ValueError('non_1hz_pre_window')
        if any(not s['signal_ok'] or s['stopped'] or s['nurse_present'] or s['audio_busy'] for s in pre):
            raise ValueError('contaminated_pre_window')
        if len({s['source_revision'] for s in pre}) != 1:
            raise ValueError('source_command_in_pre_window')
        if end-pre[-1].get('source_changed_at', 0) < 60:
            raise ValueError('source_command_settling_period')
        values = np.array([s['hr_raw_bpm'] for s in pre], dtype=float)
        if baseline is None or not math.isfinite(baseline) or baseline <= 0 or not np.isfinite(values).all():
            raise ValueError('invalid_reference_or_hr')
        times = np.array([s['sample_time_s'] for s in pre], dtype=float)
        slope = float(np.polyfit(times-times[0], values, 1)[0])
        if abs(slope) > .05:  # unvalidated demo stability gate, prespecified
            raise ValueError('pre_existing_trend')
        return {'mean': float(values.mean()), 'slope': slope, 'baseline': baseline, 'samples': 30,
                'source_revision': pre[-1]['source_revision'],
                'midpoint_time_s': float(np.mean([s['sample_time_s'] for s in pre]))}

    def _finish(self, session, exposure, reason=None):
        p = session['pending'].pop(exposure)
        key = p['session_id']+':'+exposure+':window_v1'
        if key in self.state['observations']:
            return
        result = {'patient_id': p['patient_id'], 'session_id': p['session_id'], 'exposure_id': exposure,
                  'song_id': p['song_id'], 'context': p.get('context'), 'updated': False,
                  'source_kind': p.get('source_kind', 'simulated'),
                  'simulated': p.get('source_kind', 'simulated') == 'simulated',
                  'reason': reason, 'pre_samples': p.get('context', {}).get('samples', 0), 'post_samples': 0}
        if reason is None:
            post = [s for s in session['samples'] if p['start']+30 <= s['sample_index'] < p['start']+60]
            result['post_samples'] = len(post)
            expected = list(range(p['start']+30, p['start']+60))
            if len(post) != 30 or [s['sample_index'] for s in post] != expected:
                result['reason'] = 'incomplete_response_window'
            elif not self._valid_timing(post):
                result['reason'] = 'non_1hz_response_window'
            elif p.get('source_kind') == 'live' and (post[0]['sample_time_s'] < p['start_time_s']+29
                                                    or post[-1]['sample_time_s'] > p['start_time_s']+62):
                result['reason'] = 'misaligned_live_response_window'
            elif any(not s['signal_ok'] or s['exposure_id'] != exposure for s in post):
                result['reason'] = 'invalid_response_window'
            else:
                context = p['context']
                later = float(np.mean([s['hr_raw_bpm'] for s in post]))
                # Pre midpoint -15.5 s -> post midpoint 44.5 s: 60 s separation.
                separation = float(np.mean([s['sample_time_s'] for s in post]))-context['midpoint_time_s']
                forecast = context['mean']+separation*context['slope']
                hr_reward = float(np.clip((abs(forecast-context['baseline'])-abs(later-context['baseline']))/10, -1, 1))
                rating = p.get('comfort_rating')
                reward = (rating-3)/2 if rating is not None else hr_reward
                model = self._model(p['patient_id'])
                x = np.array(p['x'])
                model['A'] = (np.array(model['A'])+np.outer(x, x)).tolist()
                model['b'] = (np.array(model['b'])+reward*x).tolist()
                model['updates'] += 1
                arm = model.setdefault('arms', {}).setdefault(p['song_id'], {'sum': 0.0, 'count': 0})
                arm['sum'] += reward
                arm['count'] += 1
                result.update(updated=True, reward=reward, later_hr=later, state_only_trend_forecast=forecast,
                              reward_source='comfort' if rating is not None else 'hr_proxy',
                              comfort_rating=rating, hr_proxy_reward=hr_reward)
        self.state['observations'][key] = result

    def ingest(self, batch):
        def apply():
            if batch['contract_version'] != CONTRACT:
                raise ValueError('Unsupported telemetry contract')
            sid, patient = batch['session_id'], batch['patient_id']
            session = self._session(sid, patient)
            if batch.get('gap'):
                for exposure in list(session['pending']):
                    self._finish(session, exposure, 'telemetry_gap')
                session['samples'] = []
                session['cursor'] = batch['latest_seq']
                return
            for record in batch['records']:
                if record['session_id'] != sid or record['patient_id'] != patient:
                    raise ValueError('Telemetry identity mismatch')
                if record.get('song_id') is not None:
                    self._check_catalog(record['song_id'])
                seq = record['seq']
                if seq <= session['cursor']:
                    continue
                if seq != session['cursor']+1:
                    for exposure in list(session['pending']):
                        self._finish(session, exposure, 'telemetry_gap')
                    session['samples'] = []
                session['cursor'] = seq
                kind = record['type']
                if kind == 'sample':
                    session['samples'].append(record)
                    session['samples'] = session['samples'][-180:]
                    for exposure, pending in list(session['pending'].items()):
                        if (not record.get('learning_enabled', True) or not record['signal_ok'] or record['stopped'] or record['nurse_present'] or
                                record['baseline'] != pending['context']['baseline'] or
                                record['source_revision'] != pending['context']['source_revision']):
                            self._finish(session, exposure, 'interrupted_or_reference_changed')
                        elif record['sample_index'] >= pending['start']+59:
                            self._finish(session, exposure)
                elif kind == 'playback_started' and record.get('policy') == 'adaptive-v1':
                    exposure = record['exposure_id']
                    key = sid+':'+exposure+':window_v1'
                    if key in self.state['observations'] or exposure in session['pending']:
                        continue
                    pending = {'patient_id': patient, 'session_id': sid, 'song_id': record['song_id'],
                               'source_kind': record.get('source_kind'),
                               'start': record['sample_index']}
                    session['pending'][exposure] = pending
                    try:
                        if not record.get('learning_enabled', True):
                            raise ValueError('learning_disabled')
                        if record['source_kind'] != self.mode or record['simulated'] != (self.mode == 'simulated'):
                            raise ValueError('source_mode_mismatch')
                        pending['source_kind'] = record['source_kind']
                        pending['start_time_s'] = record['sample_time_s']
                        if pending['start']-record.get('source_changed_at', 0) < 60:
                            raise ValueError('source_command_settling_period')
                        context = self._context(session['samples'], pending['start'], record['baseline'])
                        if context['source_revision'] != record['source_revision']:
                            raise ValueError('source_changed_at_playback_start')
                        pending['context'] = context
                        pending['x'] = vector({**record['track'], 'song_id': record['song_id']}, context).tolist()
                    except (ValueError, KeyError, TypeError) as error:
                        self._finish(session, exposure, str(error))
                elif kind == 'comfort_feedback':
                    pending = session['pending'].get(record['exposure_id'])
                    rating = record.get('rating')
                    if pending and isinstance(rating, int) and not isinstance(rating, bool) and 1 <= rating <= 5:
                        pending.setdefault('comfort_rating', rating)
                elif kind in ('playback_ended', 'playback_interrupted'):
                    if record['exposure_id'] in session['pending']:
                        self._finish(session, record['exposure_id'], record.get('reason', kind))
                elif kind in ('control_changed', 'source_changed', 'source_ended'):
                    for exposure in list(session['pending']):
                        self._finish(session, exposure, kind)
        return self._transaction(apply)

    def choose(self, snapshot):
        if snapshot.get('contract_version') != CONTRACT:
            raise ValueError('Controller does not support the adaptive contract')
        if not snapshot.get('selection_opportunity'):
            self.waiting_reason = 'waiting_for_controller'
            return None
        def apply():
            if snapshot.get('contract_version') != CONTRACT:
                raise ValueError('Controller does not support the adaptive contract')
            opportunity = snapshot.get('selection_opportunity')
            if (not opportunity or snapshot.get('source_kind') != self.mode or snapshot.get('simulated') != (self.mode == 'simulated')
                    or snapshot['stopped'] or snapshot['nurse_present'] or snapshot['source_ended']):
                return None
            sid, patient = snapshot['session_id'], snapshot['patient_id']
            session = self._session(sid, patient)
            try:
                if snapshot['sample_index']-snapshot.get('source_changed_at', 0) < 60:
                    raise ValueError('source_command_settling_period')
                context = self._context(session['samples'], snapshot['sample_index'], snapshot['baseline'])
                if context['source_revision'] != snapshot['source_revision']:
                    raise ValueError('source_changed_before_selection')
            except ValueError as error:
                self.waiting_reason = str(error)
                return None
            self.waiting_reason = 'ready'
            candidates = opportunity['candidates']
            if not candidates:
                return None
            for track in candidates:
                self._check_catalog(track['song_id'])
            key = sid+':'+opportunity['decision_id']
            if key in self.state['decisions']:
                return self.state['decisions'][key]
            top = max(t['preference'] for t in candidates)
            candidates = [t for t in candidates if t['preference'] == top]
            model = self._model(patient)
            theta = np.linalg.solve(np.array(model['A']), np.array(model['b']))
            scored = []
            for i, track in enumerate(candidates):
                try:
                    x = vector(track, context)
                except (KeyError, ValueError, TypeError):
                    continue
                arm = model.get('arms', {}).get(track['song_id'], {'sum': 0, 'count': 0})
                prediction = (5*float(theta @ x)+arm['sum'])/(5+arm['count'])
                scored.append((track, prediction+.02/(i+1)))
            if not scored:
                return None
            greedy = int(np.argmax([score for _, score in scored]))
            choice_number = sum(r['patient_id'] == patient for r in self.state['decisions'].values())
            seed_key = f"{self.state['seed']}:{patient}:{choice_number}"
            seed = int(hashlib.sha256(seed_key.encode()).hexdigest()[:16], 16)
            rng = np.random.default_rng(seed)
            exploring = rng.random() < self.state['epsilon']
            selected = int(rng.integers(len(scored))) if exploring else greedy
            probability = self.state['epsilon']/len(scored)+(1-self.state['epsilon'] if selected == greedy else 0)
            request = {'contract_version': CONTRACT, 'patient_id': patient, 'session_id': sid,
                       'request_id': key, 'decision_id': opportunity['decision_id'],
                       'control_revision': opportunity['control_revision'], 'song_id': scored[selected][0]['song_id'],
                       'policy': 'adaptive-v1', 'selection_probability': probability}
            self.state['decisions'][key] = request
            return request
        return self._transaction(apply)

    def summary(self, patient, nonblocking=False):
        acquired = self.lock.acquire(blocking=not nonblocking)
        if not acquired:
            return {'updating': True}
        try:
            observations = [o for o in self.state['observations'].values() if o['patient_id'] == patient]
            return {'updates': self.state['models'].get(patient, {}).get('updates', 0),
                    'observations': len(observations), 'waiting_reason': self.waiting_reason,
                    'last_observation': observations[-1] if observations else None}
        finally:
            self.lock.release()


def poll_controller(adapter, url):
    """One poll; identical polls and restart replay cannot teach twice."""
    if urlparse(url).scheme != 'http' or urlparse(url).hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('Adapter supports a local HTTP controller only')
    def get(path):
        with urlopen(url.rstrip('/')+path, timeout=3) as response:
            return json.load(response)
    snapshot = get('/state')
    sid = snapshot['session_id']
    query = urlencode({'session_id': sid, 'after_seq': adapter.cursor(sid)})
    batch = get('/telemetry?'+query)
    adapter.ingest(batch)
    request = adapter.choose(snapshot)
    if request:
        try:
            with urlopen(Request(url.rstrip('/')+'/select', data=json.dumps(request).encode(),
                                 headers={'Content-Type': 'application/json'}), timeout=3) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code != 409:
                raise
            return {'accepted': False, 'reason': 'controller_state_changed'}
    return {'accepted': False, 'reason': 'waiting_or_ineligible'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', default='http://127.0.0.1:8765')
    p.add_argument('--state', type=Path, default=Path('data/raw/adaptive-state.json'))
    p.add_argument('--once', action='store_true')
    p.add_argument('--music', choices=tuple(SCHEMAS), default='jamendo',
                   help='Feature schema must match the controller music catalog')
    p.add_argument('--mode', choices=('simulated', 'live'), default='simulated')
    args = p.parse_args()
    adapter = AdaptiveAdapter(args.state, catalog=args.music, mode=args.mode)
    while True:
        try:
            print(json.dumps(poll_controller(adapter, args.url)), flush=True)
        except (OSError, ValueError, KeyError) as error:
            print(json.dumps({'error': str(error)}), flush=True)
        if args.once:
            break
        time.sleep(.5)
