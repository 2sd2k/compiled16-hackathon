"""Paired, explicitly synthetic end-to-end benchmark; virtual playback, no audio.

Uses the real controller/adapter and the private ResponseSimSource mechanism.
It does not read hidden gains or invent training labels outside that source.
"""
import argparse
import json
from pathlib import Path
import statistics
import uuid
from unittest.mock import patch
import controller as c
from adaptive_adapter import AdaptiveAdapter
from adaptive_controller import song_id
from response_simulator import ResponseSimSource


class VirtualPlayer:
    def __init__(self):
        self.track, self.on_event, self.exposure = None, None, None
    def play(self, track, exposure_id=None):
        self.track, self.exposure = track, exposure_id or str(uuid.uuid4())
        self.on_event('playback_requested', track, self.exposure)
        self.on_event('playback_started', track, self.exposure)
    def silence(self, fade_s=0):
        if self.track:
            self.on_event('playback_interrupted', self.track, self.exposure, reason='silence', fade_s=fade_s)
        self.track = None
    def ended(self):
        return False


class FixedPriorAdapter(AdaptiveAdapter):
    def choose(self, snapshot):
        request = super().choose(snapshot)  # identical window/eligibility gate
        if request:
            candidates = snapshot['selection_opportunity']['candidates']
            top = max(t['preference'] for t in candidates)
            request['song_id'] = next(t['song_id'] for t in candidates if t['preference'] == top)
            request['selection_probability'] = 1.0
        return request


def benchmark(episodes=8, seeds=3, count=4):
    library = json.loads(c.LIBRARY.read_text())['tracks']
    candidates = [{**t, 'predicted_arousal_mean': t['deam_pred_arousal'],
                   'predicted_valence_mean': t['deam_pred_valence']} for t in library if t.get('calm')]
    songs = c.rank_music(candidates, {}, target_arousal=c.TARGET_AROUSAL, valence_weight=0)[:count]
    approvals = {song_id(t['song_id']): .5 for t in songs}
    runs = []
    for seed in range(seeds):
        patient = f'sim-benchmark-patient-{seed}'
        for no_effect in (False, True):
            for name, adapter_type in [('adaptive', AdaptiveAdapter), ('fixed_arousal_prior', FixedPriorAdapter)]:
                adapter = adapter_type(epsilon=.2 if name == 'adaptive' else 0, seed=seed)
                rewards = []
                for episode in range(episodes):
                    source = ResponseSimSource(patient, seed=1000*seed+episode, no_audio_effect=no_effect)
                    with patch.object(c, 'load_calm_list', return_value=songs), patch.object(c.Controller, '_track_available', return_value=True):
                        ctrl = c.Controller(source, 10, player=VirtualPlayer(), quiet=True,
                                            patient_id=patient, adapter=adapter, approved_ids=approvals)
                        for tick in range(320):
                            if tick == 120:
                                ctrl.input('rise')
                            ctrl.tick()
                            summary = adapter.summary(patient)
                            if summary['updates'] == episode+1:
                                rewards.append(summary['last_observation']['reward'])
                                break
                        else:
                            raise RuntimeError(f'No complete window: {name}, seed={seed}, episode={episode}, {adapter.summary(patient)}')
                runs.append({'seed': seed, 'condition': 'no_audio_effect' if no_effect else 'invented_song_effect',
                             'policy': name, 'updates': len(rewards), 'mean_reward': statistics.mean(rewards),
                             'final_half_reward': statistics.mean(rewards[episodes//2:])})
    summary = {}
    for condition in ['invented_song_effect', 'no_audio_effect']:
        summary[condition] = {policy: statistics.mean(r['final_half_reward'] for r in runs if r['condition'] == condition and r['policy'] == policy)
                              for policy in ['adaptive', 'fixed_arousal_prior']}
    return {'simulated': True, 'virtual_playback': True, 'clinical_evidence': False,
            'description': 'Software integration benchmark with invented patient/song effects, drift and noise. Fixed comparator uses the existing DEAM arousal ordering with identical observation gates.',
            'episodes_per_patient': episodes, 'seeds': seeds, 'approved_song_ids': list(approvals),
            'metric': 'mean trend-adjusted reference-distance reward in final half of episodes; higher is better',
            'summary': summary, 'runs': runs}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=Path(__file__).with_name('adaptive_benchmark.json'))
    p.add_argument('--episodes', type=int, default=8)
    p.add_argument('--seeds', type=int, default=3)
    args = p.parse_args()
    if args.episodes < 2 or args.seeds < 1:
        p.error('Need at least two episodes and one seed')
    report = benchmark(args.episodes, args.seeds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report['summary'], indent=2))
