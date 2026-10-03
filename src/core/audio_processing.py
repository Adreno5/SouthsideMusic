from __future__ import annotations

from dataclasses import dataclass
from math import gcd

import numpy as np
from scipy.signal import resample_poly

from core.pcm_timeline import PcmTimeline
from core.wsola import WsolaStretcher

_MAX_HAAS_DELAY_MS = 30
_MIN_AUDIBLE_PITCH_SHIFT = 0.25
_REVERB_DELAY_MS = (29, 43, 61, 79)
_REVERB_TAP_GAINS = (0.42, 0.31, 0.22, 0.15)
_REVERB_GAIN_COMPENSATION = 0.18


@dataclass(frozen=True)
class AudioProcessingSettings:
    play_speed: float
    play_pitch: float
    speed_animating: bool
    stereo: bool
    stereo_haas_index: int
    enable_reverb: bool
    reverb_intensity: float


class AudioProcessor:
    def __init__(self) -> None:
        self.samples = np.empty((0, 1), dtype=np.float32)
        self.timeline: PcmTimeline | None = None
        self.sample_rate = 0
        self.channels = 1
        self.settings = AudioProcessingSettings(1.0, 0.0, False, True, 0, False, 0.0)
        self._wsola = WsolaStretcher(self._readSamples, self._sampleCount)
        self._stereo_tail: np.ndarray | None = None
        self._reverb_tail: np.ndarray | None = None

    def _sampleCount(self) -> int:
        return self.timeline.end if self.timeline is not None else len(self.samples)

    def _readSamples(self, start: int, stop: int) -> np.ndarray:
        if self.timeline is not None:
            return self.timeline.read(start, stop)
        return self.samples[start:stop]

    def render(self, start: int, frames: int) -> tuple[np.ndarray, int]:
        chunk, source_frames = self._readSpeed(start, frames)
        if self.channels == 1:
            out = self._applyStereoEffect(chunk[:, 0])
        elif not self.settings.stereo:
            out = np.repeat(chunk.mean(axis=1, keepdims=True), 2, axis=1)
        else:
            out = chunk[:, :2]
        return self._applyReverb(out.astype(np.float32, copy=False)), source_frames

    def _resetStereoEffect(self) -> None:
        self._stereo_tail = None

    def _applyStereoEffect(self, mono_chunk: np.ndarray) -> np.ndarray:
        stereo_chunk = np.repeat(mono_chunk.reshape(-1, 1), 2, axis=1)
        if (
            not self.settings.stereo
            or self.settings.stereo_haas_index == 0
            or len(mono_chunk) == 0
        ):
            self._resetStereoEffect()
            return stereo_chunk

        delay_ms = min(max(0, self.settings.stereo_haas_index), _MAX_HAAS_DELAY_MS)
        if delay_ms == 0:
            self._resetStereoEffect()
            return stereo_chunk
        delay = min(
            max(1, int(self.sample_rate * delay_ms / 1000)),
            max(1, len(self.samples) // 8),
        )
        mono = mono_chunk.astype(np.float32, copy=False)
        tail = self._stereo_tail
        if tail is None:
            tail = np.zeros(delay, dtype=np.float32)
        elif len(tail) < delay:
            tail = np.concatenate((np.zeros(delay - len(tail), dtype=np.float32), tail))
        else:
            tail = tail[-delay:]

        history = np.concatenate((tail, mono))
        stereo_chunk[:, 1] = history[: len(mono)] * 0.82
        self._stereo_tail = history[-delay:].copy()

        return stereo_chunk

    def _resetReverb(self) -> None:
        self._reverb_tail = None

    def _applyReverb(self, chunk: np.ndarray) -> np.ndarray:
        if (
            not self.settings.enable_reverb
            or self.settings.reverb_intensity == 0
            or len(chunk) == 0
        ):
            return chunk

        delays = [max(1, int(self.sample_rate * ms / 1000)) for ms in _REVERB_DELAY_MS]
        max_delay = max(delays)
        channels = chunk.shape[1] if chunk.ndim == 2 else 1
        tail = self._reverb_tail
        if tail is None or tail.ndim != 2 or tail.shape[1] != channels:
            tail = np.zeros((max_delay, channels), dtype=np.float32)
        elif len(tail) < max_delay:
            padding = np.zeros((max_delay - len(tail), channels), dtype=np.float32)
            tail = np.concatenate((padding, tail), axis=0)
        else:
            tail = tail[-max_delay:]

        dry = chunk.reshape(-1, channels).astype(np.float32, copy=False)
        history = np.concatenate((tail, dry), axis=0)
        start = len(tail)
        end = start + len(dry)
        wet = np.zeros_like(dry)
        for delay, tap_gain in zip(delays, _REVERB_TAP_GAINS):
            wet += history[start - delay : end - delay] * tap_gain

        mix = self.settings.reverb_intensity
        gain = 1.0 + mix * _REVERB_GAIN_COMPENSATION
        out = (dry * (1.0 - mix * 0.25) + wet * (mix * 0.55)) * gain
        self._reverb_tail = history[-max_delay:].copy()
        return out.astype(np.float32, copy=False)

    def _resetWsola(self) -> None:
        self._wsola.reset()

    def _pitchRatio(self) -> float:
        if abs(self.settings.play_pitch) < _MIN_AUDIBLE_PITCH_SHIFT:
            return 1.0
        return 2 ** (self.settings.play_pitch / 12.0)

    def _readWsola(self, start_idx: int, frames: int, speed: float) -> np.ndarray:
        return self._wsola.read(
            start_idx, frames, speed, self.sample_rate, self.channels
        )

    def _sourceFramesFor(self, start_idx: int, frames: int, speed: float) -> int:
        n = self._sampleCount()
        if n == 0 or start_idx >= n:
            return 0
        src_frames = max(1, round(frames * speed))
        return min(src_frames, n - start_idx)

    def _speedSourceFrames(self, start_idx: int, frames: int) -> int:
        return self._sourceFramesFor(start_idx, frames, self.settings.play_speed)

    def _resampleToFrames(self, chunk: np.ndarray, frames: int) -> np.ndarray:
        if len(chunk) == frames:
            return chunk.astype(np.float32, copy=False)
        if len(chunk) == 0:
            return np.zeros((0, self.channels), dtype=np.float32)
        if len(chunk) == 1:
            return np.repeat(chunk, frames, axis=0).astype(np.float32, copy=False)

        factor = gcd(len(chunk), frames)
        up = frames // factor
        down = len(chunk) // factor
        out = resample_poly(chunk, up, down, axis=0, padtype='line').astype(
            np.float32, copy=False
        )
        if len(out) > frames:
            return out[:frames]
        if len(out) < frames:
            pad = np.repeat(out[-1:], frames - len(out), axis=0)
            out = np.concatenate((out, pad), axis=0)
        return out

    def _readSpeed(self, start_idx: int, frames: int) -> tuple[np.ndarray, int]:
        n = self._sampleCount()
        speed = self.settings.play_speed
        if n == 0:
            return np.zeros((0, self.channels), dtype=np.float32), 0

        pitch_ratio = self._pitchRatio()
        if abs(speed - 1.0) < 1e-6 and abs(pitch_ratio - 1.0) < 1e-6:
            self._resetWsola()
            src_frames = self._speedSourceFrames(start_idx, frames)
            return self._readSamples(start_idx, start_idx + frames).copy(), src_frames

        if self.settings.speed_animating:
            return self._readSpeedResample(start_idx, frames, speed)

        intermediate_frames = max(1, round(frames * pitch_ratio))
        tempo_speed = speed / pitch_ratio
        chunk = self._readWsola(start_idx, intermediate_frames, tempo_speed)
        if abs(pitch_ratio - 1.0) >= 1e-6:
            chunk = self._resampleToFrames(chunk, frames)

        src_frames = self._sourceFramesFor(start_idx, intermediate_frames, tempo_speed)
        return chunk, src_frames

    def _readSpeedResample(
        self, start_idx: int, frames: int, speed: float
    ) -> tuple[np.ndarray, int]:
        n = self._sampleCount()
        src_frames = max(1, round(frames * speed))
        end_idx = min(start_idx + src_frames, n)
        src_region = self._readSamples(start_idx, end_idx)
        if len(src_region) < 2:
            return (
                np.zeros((frames, self.channels), dtype=np.float32),
                src_frames,
            )
        src_x = np.linspace(0.0, 1.0, len(src_region), dtype=np.float64)
        dst_x = np.linspace(0.0, 1.0, frames, dtype=np.float64)
        ch = min(self.channels, src_region.shape[1])
        result = np.zeros((frames, ch), dtype=np.float32)
        for c in range(ch):
            result[:, c] = np.interp(dst_x, src_x, src_region[:, c]).astype(
                np.float32, copy=False
            )
        return result, src_frames
