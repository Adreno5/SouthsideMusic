from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import exp, sqrt

import numpy as np
from scipy.fft import rfft, rfftfreq
from scipy.ndimage import maximum_filter1d, median_filter

_WINDOW_SECONDS = 0.046
_BAND_EDGES_HZ = (450.0, 2200.0, 8000.0)
_PULSE_WEIGHTS = np.array([2.0, 1.2, 1.0, 0.8])
_HARMONIC_FRAMES = 21
_PERCUSSIVE_RADIUS = 4
_BACKGROUND_ALPHA = 0.02
_SPECTRUM_FLOOR_RATIO = 0.01
_ENERGY_FLOOR_RATIO = 0.02
_SILENCE_LEVEL = 0.002
_ATTACK_OFFSET = 0.08
_ATTACK_SPAN = 0.25
_SHARPNESS_OFFSET = 0.04
_SHARPNESS_SPAN = 0.2
_KICK_WIDTH_OFFSET = 0.3
_KICK_WIDTH_SPAN = 0.3
_KICK_FLATNESS_OFFSET = 0.18
_KICK_FLATNESS_SPAN = 0.42
_KICK_FLATNESS_WEIGHT = 0.8
_UPPER_SUPPORT_OFFSET = 0.04
_UPPER_SUPPORT_SPAN = 0.14
_UPPER_KICK_WIDTH_OFFSET = 0.25
_UPPER_KICK_WIDTH_SPAN = 0.35
_SNARE_WIDTH_OFFSET = 0.35
_SNARE_WIDTH_SPAN = 0.25
_POINT_FLOOR = 0.035
_POINT_DEVIATION = 3.0
_POINT_REFERENCE_FLOOR = 0.12
_NOISE_FRAMES = 150
_NOISE_WARMUP = 5
_CANDIDATE_SECONDS = 0.9
_CANDIDATE_FRAMES = 64
_POINT_HISTORY = 12
_PEAK_HISTORY = 16
_ONSET_LEAD_SECONDS = 0.003
_RHYTHM_UPDATE_SECONDS = 0.12
_RHYTHM_PULSES = 180
_RHYTHM_TREND_FRAMES = 31
_RHYTHM_RATIOS = (1.0, 1.5, 2.0, 2.5)
_RHYTHM_RATIOS_WIDE = (0.5, 1.0, 1.5, 2.0, 2.5)
_RHYTHM_FAST_RATIOS = (1.0, 1.5, 2.0)
_RHYTHM_CONFIDENCE_SCALE = 0.5
_RHYTHM_PHASE_SPAN = 0.16
_RHYTHM_FALLBACK_PHASE_SPAN = 0.38


@dataclass(frozen=True)
class BeatFrame:
    time: float
    intensity: float
    is_point: bool


class BeatDetector:
    HOP_SECONDS = 0.01
    _MIN_INTERVAL = 0.18

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._sample_rate = 0
        self._limits = (40.0, 180.0)
        self._buffer = np.empty((0, 1), dtype=np.float32)
        self._window = np.empty(0, dtype=np.float32)
        self._hop = 0
        self._hop_seconds = self.HOP_SECONDS
        self._min_interval = self._MIN_INTERVAL
        self._time_origin = 0
        self._samples_seen = 0
        self._bands: list[np.ndarray] = []
        self._background: np.ndarray | None = None
        self._spectra: deque[np.ndarray] = deque(maxlen=3)
        self._harmonics: deque[np.ndarray] = deque(maxlen=_HARMONIC_FRAMES)
        self._energies: deque[np.ndarray] = deque(maxlen=5)
        self._pulses: deque[float] = deque(maxlen=500)
        self._novelties: deque[float] = deque(maxlen=3)
        self._noise_history: deque[float] = deque(maxlen=_NOISE_FRAMES)
        self._peaks: deque[float] = deque(maxlen=_PEAK_HISTORY)
        self._accepted_points: deque[tuple[float, float]] = deque(maxlen=_POINT_HISTORY)
        self._candidate_events: deque[float] = deque(maxlen=_CANDIDATE_FRAMES)
        self._last_point = -10.0
        self._intensity = 0.0
        self._rhythm_period = 0.0
        self._rhythm_confidence = 0.0
        self._last_rhythm_update = -10.0

    def process(
        self,
        samples: np.ndarray,
        sample_rate: int,
        *,
        sensitivity: float = 1.0,
        smoothing: float = 0.35,
        low_hz: float = 40.0,
        high_hz: float = 180.0,
        point_threshold: float = 0.25,
        hop_seconds: float = HOP_SECONDS,
        min_interval: float = _MIN_INTERVAL,
    ) -> list[BeatFrame]:
        audio = np.asarray(samples, dtype=np.float32)
        if audio.ndim == 1:
            audio = audio[:, None]
        if audio.ndim != 2 or audio.shape[1] == 0 or sample_rate <= 0:
            self.reset()
            return []
        if len(audio) == 0:
            return []
        if not np.isfinite(audio).all():
            self.reset()
            return []

        hop_seconds = float(np.clip(hop_seconds, 0.005, 0.05))
        min_interval = float(np.clip(min_interval, 0.08, 1.0))
        limits = (max(25.0, low_hz), max(low_hz + 30.0, high_hz))
        if (
            sample_rate != self._sample_rate
            or audio.shape[1] != self._buffer.shape[1]
            or limits != self._limits
            or hop_seconds != self._hop_seconds
        ):
            self._configure(
                sample_rate, audio.shape[1], limits, hop_seconds, min_interval
            )
        elif min_interval != self._min_interval:
            self._min_interval = min_interval
            self._accepted_points.clear()
            self._candidate_events.clear()
            self._last_point = -10.0
        self._buffer = np.concatenate((self._buffer, audio))

        results = []
        size = len(self._window)
        consumed = 0
        while len(self._buffer) - consumed >= size:
            frame = self._buffer[consumed : consumed + size]
            self._samples_seen += self._hop
            novelty = self._spectralNovelty(frame)
            results.append(
                self._pickPeak(novelty, sensitivity, smoothing, point_threshold)
            )
            consumed += self._hop
        self._buffer = self._buffer[consumed:].copy()
        return results

    def _configure(
        self,
        sample_rate: int,
        channels: int,
        limits: tuple[float, float],
        hop_seconds: float,
        min_interval: float,
    ) -> None:
        self.reset()
        self._sample_rate = sample_rate
        self._limits = limits
        self._hop_seconds = hop_seconds
        self._min_interval = min_interval
        size = max(128, 2 ** round(np.log2(sample_rate * _WINDOW_SECONDS)))
        self._hop = max(1, round(sample_rate * hop_seconds))
        self._window = np.hanning(size)
        self._buffer = np.empty((0, channels), dtype=np.float32)
        self._time_origin = (
            self._hop - size // 2 + round(_ONSET_LEAD_SECONDS * sample_rate)
        )
        freqs = rfftfreq(size, 1.0 / sample_rate)
        low, high = limits
        edges = (low, high, *[edge for edge in _BAND_EDGES_HZ if edge > high])
        self._bands = [
            (freqs >= start) & (freqs < end) for start, end in zip(edges, edges[1:])
        ]

    def _spectralNovelty(self, frame: np.ndarray) -> float:
        spectra = np.abs(rfft(frame * self._window[:, None], axis=0))
        spectrum = np.sqrt(np.mean(spectra**2, axis=1)) * (
            2.0 / float(self._window.sum())
        )
        energies = np.array([
            float(np.linalg.norm(spectrum[band])) for band in self._bands
        ])
        if self._background is None:
            self._background = spectrum.copy()
        background = self._background
        self._background = (
            background * (1.0 - _BACKGROUND_ALPHA) + spectrum * _BACKGROUND_ALPHA
        )
        self._spectra.append(spectrum)
        self._harmonics.append(spectrum)
        self._energies.append(energies)
        self._pulses.append(float(energies @ _PULSE_WEIGHTS))
        if len(self._energies) < 5:
            return 0.0

        level = max(float(np.max(spectrum)), float(np.max(background)))
        if level < _SILENCE_LEVEL:
            return 0.0
        previous = maximum_filter1d(self._spectra[0], size=3)
        floor = max(level * _SPECTRUM_FLOOR_RATIO, 1e-6)
        difference = np.maximum(spectrum - previous, 0.0)
        flux = np.log1p(difference / np.maximum(background, floor))
        harmonic = np.min(np.asarray(self._harmonics), axis=0)
        percussive = median_filter(spectrum, size=_PERCUSSIVE_RADIUS * 2 + 1)
        flux *= percussive**2 / (percussive**2 + (harmonic * 2.0) ** 2 + floor**2)
        flux[spectrum < floor] = 0.0

        band_flux = np.array([
            float(np.mean(flux[band])) if band.any() else 0.0 for band in self._bands
        ])
        widths = np.array([
            float(np.sum(difference[band]) ** 2)
            / max(
                float(np.sum(difference[band] ** 2)) * np.count_nonzero(band),
                1e-12,
            )
            for band in self._bands
        ])
        flatness = np.array([
            self._spectralFlatness(difference[band]) if band.any() else 0.0
            for band in self._bands
        ])

        energy_floor = max(level * _ENERGY_FLOOR_RATIO, 1e-6)
        attack = np.log((energies + energy_floor) / (self._energies[-3] + energy_floor))
        previous_attack = np.log(
            (self._energies[-3] + energy_floor) / (self._energies[0] + energy_floor)
        )
        sharpness = np.maximum(attack - previous_attack, 0.0)
        attack_gate = np.clip((attack - _ATTACK_OFFSET) / _ATTACK_SPAN, 0.0, 1.0)
        sharp_gate = np.clip(
            (sharpness - _SHARPNESS_OFFSET) / _SHARPNESS_SPAN, 0.0, 1.0
        )
        evidence = band_flux * attack_gate * sharp_gate
        low_width = np.clip(
            (widths[0] - _KICK_WIDTH_OFFSET) / _KICK_WIDTH_SPAN, 0.0, 1.0
        )
        low_flat = np.clip(
            (flatness[0] - _KICK_FLATNESS_OFFSET) / _KICK_FLATNESS_SPAN, 0.0, 1.0
        )
        upper_support = np.clip(
            (sqrt(evidence[1] * evidence[2]) - _UPPER_SUPPORT_OFFSET)
            / _UPPER_SUPPORT_SPAN,
            0.0,
            1.0,
        )
        kick_shape = (
            1.0 - _KICK_FLATNESS_WEIGHT
        ) * low_width + _KICK_FLATNESS_WEIGHT * low_flat
        kick = evidence[0] * kick_shape * (0.005 + 0.995 * upper_support)
        upper_kick = (
            evidence[1]
            * 0.5
            * upper_support
            * np.clip(
                (widths[1] - _UPPER_KICK_WIDTH_OFFSET) / _UPPER_KICK_WIDTH_SPAN,
                0.0,
                1.0,
            )
        )
        snare = sqrt(evidence[2] * evidence[3]) * np.clip(
            (min(widths[2:]) - _SNARE_WIDTH_OFFSET) / _SNARE_WIDTH_SPAN, 0.0, 1.0
        )
        return float(max(kick, upper_kick, snare))

    @staticmethod
    def _spectralFlatness(values: np.ndarray) -> float:
        positive = np.maximum(np.asarray(values, dtype=np.float64), 1e-12)
        if positive.size == 0:
            return 0.0
        return float(np.exp(np.mean(np.log(positive))) / np.mean(positive))

    def _pickPeak(
        self, novelty: float, sensitivity: float, smoothing: float, threshold: float
    ) -> BeatFrame:
        hop_seconds = self._hop / self._sample_rate
        timestamp = (self._samples_seen - self._time_origin) / self._sample_rate
        release = 0.10 + float(np.clip(smoothing, 0.0, 0.99)) * 0.24
        self._intensity *= exp(-hop_seconds / release)
        self._novelties.append(novelty)
        is_point = False
        if len(self._novelties) == 3 and len(self._noise_history) >= _NOISE_WARMUP:
            left, peak, right = self._novelties
            if peak > left and peak >= right:
                history = np.asarray(self._noise_history)
                baseline = float(np.median(history))
                deviation = float(np.median(np.abs(history - baseline)))
                floor = max(_POINT_FLOOR, baseline + deviation * _POINT_DEVIATION)
                if peak > floor:
                    reference = max(
                        float(np.median(self._peaks)) if self._peaks else peak,
                        _POINT_REFERENCE_FLOOR,
                    )
                    score = min(
                        1.0,
                        sqrt((peak - floor) / reference) * max(0.0, sensitivity),
                    )
                    self._candidate_events.append(timestamp)
                    while (
                        self._candidate_events
                        and timestamp - self._candidate_events[0] > _CANDIDATE_SECONDS
                    ):
                        self._candidate_events.popleft()

                    rhythm_confidence, rhythm_factor = self._rhythmSupport(timestamp)
                    density = len(self._candidate_events)
                    if rhythm_confidence < 0.45 and density > 5:
                        crowding = min(0.86, 0.18 * (density - 5))
                        rhythm_factor *= 1.0 - crowding
                    score *= rhythm_factor

                    attack = 0.24 + 0.20 * (1.0 - float(np.clip(smoothing, 0.0, 0.99)))
                    self._intensity += (score - self._intensity) * attack

                    if timestamp - self._last_point >= self._min_interval:
                        density_gate = 1.0 + min(2.5, max(0, density - 5) * 0.3)
                        effective_threshold = (
                            max(0.01, threshold)
                            * density_gate
                            * (1.0 - 0.30 * rhythm_confidence)
                        )
                        is_point = score >= effective_threshold
                        if is_point:
                            self._last_point = timestamp
                            self._peaks.append(peak)
                            if self._accepted_points:
                                interval = timestamp - self._accepted_points[-1][0]
                                if interval >= self._min_interval:
                                    self._accepted_points.append((timestamp, score))
                            else:
                                self._accepted_points.append((timestamp, score))
        self._noise_history.append(novelty)
        if self._intensity < 0.005:
            self._intensity = 0.0
        return BeatFrame(timestamp, self._intensity, is_point)

    def _rhythmSupport(self, timestamp: float) -> tuple[float, float]:
        if timestamp - self._last_rhythm_update >= _RHYTHM_UPDATE_SECONDS:
            period, confidence = self._estimateRhythm()
            if period > 0.0:
                if self._rhythm_period > 0.0:
                    relative_change = (
                        abs(period - self._rhythm_period) / self._rhythm_period
                    )
                    if (
                        relative_change > 0.35
                        and confidence < self._rhythm_confidence + 0.15
                    ):
                        period = self._rhythm_period
                if self._rhythm_period > 0.0:
                    self._rhythm_period = self._rhythm_period * 0.7 + period * 0.3
                    self._rhythm_confidence = max(
                        self._rhythm_confidence * 0.7, confidence
                    )
                else:
                    self._rhythm_period = period
                    self._rhythm_confidence = confidence
            self._last_rhythm_update = timestamp

        if self._rhythm_period > 0.0 and self._rhythm_confidence >= 0.3:
            if not self._accepted_points:
                return self._rhythm_confidence, 1.0
            elapsed = timestamp - self._accepted_points[-1][0]
            if elapsed < self._min_interval:
                return self._rhythm_confidence, 0.22
            period = self._rhythm_period
            ratios: tuple[float, ...] = _RHYTHM_RATIOS
            if self._rhythm_confidence >= 0.72:
                ratios = _RHYTHM_RATIOS_WIDE
            error = min(
                abs(elapsed - period * ratio) / (period * ratio) for ratio in ratios
            )
            phase = float(np.clip(error / _RHYTHM_PHASE_SPAN, 0.0, 1.0))
            factor = 1.0 - self._rhythm_confidence * 0.94 * phase
            return self._rhythm_confidence, max(0.04, factor)

        if len(self._accepted_points) < 3:
            return self._rhythm_confidence, 1.0
        intervals = np.diff(np.asarray([point[0] for point in self._accepted_points]))
        intervals = intervals[(intervals >= self._min_interval) & (intervals <= 1.5)]
        if len(intervals) < 2:
            return 0.0, 1.0
        period = float(np.median(intervals))
        if period <= 0.0:
            return 0.0, 1.0
        deviation = float(np.median(np.abs(intervals - period))) / period
        confidence = float(np.clip(1.0 - deviation / 0.30, 0.0, 1.0))
        elapsed = timestamp - self._accepted_points[-1][0]
        if elapsed < self._min_interval:
            return confidence, 0.35

        error = min(
            abs(elapsed - period * ratio) / (period * ratio)
            for ratio in _RHYTHM_FAST_RATIOS
        )
        phase = float(np.clip(error / _RHYTHM_FALLBACK_PHASE_SPAN, 0.0, 1.0))
        factor = 1.0 - confidence * 0.62 * phase
        return confidence, max(0.30, factor)

    def _estimateRhythm(self) -> tuple[float, float]:
        if len(self._pulses) < _RHYTHM_PULSES:
            return 0.0, 0.0
        signal = np.log1p(np.fromiter(self._pulses, dtype=np.float64))
        signal -= median_filter(signal, size=_RHYTHM_TREND_FRAMES, mode='nearest')
        signal = np.maximum(signal, 0.0)
        if float(np.max(signal)) <= 1e-8:
            return 0.0, 0.0
        signal -= float(np.mean(signal))
        norm = float(np.linalg.norm(signal))
        if norm <= 1e-8:
            return 0.0, 0.0
        min_lag = max(24, round(0.24 / self._hop_seconds))
        max_lag = min(len(signal) // 2, round(0.6 / self._hop_seconds))
        if max_lag <= min_lag:
            return 0.0, 0.0
        scores = np.zeros(max_lag + 1, dtype=np.float64)
        for lag in range(min_lag, max_lag + 1):
            left = signal[:-lag]
            right = signal[lag:]
            denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
            if denominator > 1e-8:
                scores[lag] = float(np.dot(left, right) / denominator)
        best = float(np.max(scores))
        if best < 0.16:
            return 0.0, 0.0
        candidates = [
            lag
            for lag in range(min_lag, max_lag + 1)
            if scores[lag] >= best * 0.9
            and scores[lag] >= scores[max(min_lag, lag - 1)]
            and scores[lag] >= scores[min(max_lag, lag + 1)]
        ]
        lag = min(candidates) if candidates else int(np.argmax(scores))
        confidence = float(
            np.clip((scores[lag] - 0.12) / _RHYTHM_CONFIDENCE_SCALE, 0.0, 1.0)
        )
        return lag * self._hop_seconds, confidence
