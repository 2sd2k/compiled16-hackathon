"""Deliberately invented song-response mechanism, separate from the adaptive learner.

Not fitted to patient physiology. Hidden effects are never exported in /state.
"""
import hashlib
import math
import random


class ResponseSimSource:
    interactive_simulation = True
    label = 'SIMULATED song-response experiment (invented effects)'

    def __init__(self, patient_id='sim-patient-01', seed=42, no_audio_effect=False, base=72):
        self.base, self.target, self.lift, self.effect = base, 0.0, 0.0, 0.0
        self._patient, self._seed = patient_id, seed
        self._no_effect, self._song, self._age, self._t = no_audio_effect, None, 0, 0
        self._rng = random.Random(seed)
        if no_audio_effect:
            self.label = 'SIMULATED no-audio-effect control (invented drift/noise)'

    def set(self, mode):
        self.target = 18.0 if mode == 'rise' else 0.0

    def playback(self, song_id):
        self._song, self._age = song_id, 0

    def next(self):
        self._t += 1
        self._age += 1
        self.lift += max(-.6, min(1.0, self.target-self.lift))
        gain = 0
        if self._song and not self._no_effect and self._age > 10:
            digest = hashlib.sha256((self._patient+':'+self._song).encode()).digest()
            gain = 2+6*int.from_bytes(digest[:2], 'big')/65535
        # Song effect relaxes when audio stops, independently of natural drift.
        goal = -gain if self.target > 0 else 0
        self.effect += max(-.2, min(.2, goal-self.effect))
        drift = .5*math.sin(self._t/90)
        return round(self.base+self.lift+self.effect+drift+self._rng.gauss(0, .35), 2)
