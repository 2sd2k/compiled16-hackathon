"""Cancellable audio fades with playback acknowledgements for observation windows."""
from pathlib import Path
import threading
import time
import uuid
import pygame

AUDIO_DIR = Path(__file__).resolve().parents[1]/'data/raw/audio'
VOLUME = .6


class Player:
    def __init__(self, audio_dir=AUDIO_DIR, file_extension='.mp3'):
        pygame.mixer.init()
        self.audio_dir, self.track, self.on_event = Path(audio_dir), None, None
        self.file_extension = file_extension
        self._lock = threading.Lock()
        self._exposure = None
        self._ended_reported = False

    def _notify(self, kind, track, exposure, **details):
        if self.on_event:
            self.on_event(kind, track, exposure, **details)

    def _ramp(self, target, seconds, token):
        start = pygame.mixer.music.get_volume()
        steps = max(1, int(seconds/.05))
        for i in range(1, steps+1):
            if self._exposure != token:
                return False
            pygame.mixer.music.set_volume(start+(target-start)*i/steps)
            time.sleep(.05)
        return True

    def play(self, track, exposure_id=None):
        if self.track is not None and not self._ended_reported:
            self._notify('playback_interrupted', self.track, self._exposure, reason='replaced')
        token = exposure_id or str(uuid.uuid4())
        self.track, self._exposure = track, token
        self._ended_reported = False
        self._started = time.monotonic()
        self._notify('playback_requested', track, token)
        threading.Thread(target=self._play, args=(track, token), daemon=True).start()
        return token

    def _play(self, track, token):
        with self._lock:
            try:
                if pygame.mixer.music.get_busy() and not self._ramp(0, 2.5, token):
                    return
                if self._exposure != token or self.track is not track:
                    return
                pygame.mixer.music.load(str(self.audio_dir/f"{track['song_id']}{self.file_extension}"))
                if self._exposure != token:
                    return
                pygame.mixer.music.set_volume(0)
                pygame.mixer.music.play()
                if self._exposure != token:
                    pygame.mixer.music.stop()
                    return
                self._started = time.monotonic()
                self._notify('playback_started', track, token)
                self._ramp(VOLUME, 5, token)
            except pygame.error:
                if self._exposure == token:
                    self.track = None
                    self._exposure = None
                self._notify('playback_interrupted', track, token, reason='audio_error')

    def silence(self, fade_s=3):
        track, token = self.track, self._exposure
        self.track, self._exposure = None, None  # immediately cancel any in-flight fade/start
        if track is not None and not self._ended_reported:
            self._notify('playback_interrupted', track, token, reason='silence', fade_s=fade_s)
        threading.Thread(target=self._silence, args=(fade_s,), daemon=True).start()

    def _silence(self, fade_s):
        with self._lock:
            if self.track is not None:
                return
            if pygame.mixer.music.get_busy():
                self._ramp(0, fade_s, None)
            if self.track is None:
                pygame.mixer.music.stop()

    def ended(self):
        ended = (self.track is not None and time.monotonic()-self._started > 10
                 and not pygame.mixer.music.get_busy())
        if ended and not self._ended_reported:
            self._ended_reported = True
            self._notify('playback_ended', self.track, self._exposure, reason='track_ended')
        return ended
