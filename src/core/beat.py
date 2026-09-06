from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import exp, sqrt

import numpy as np
from scipy.fft import rfft, rfftfreq
from scipy.ndimage import maximum_filter1d, median_filter


@dataclass(frozen=True)
class BeatFrame:
    time: float
    intensity: float
    is_point: bool


class BeatDetector:
    """Detect drum onsets from PCM with a fixed analysis hop."""

    HOP_SECONDS = 0.01
    _MIN_INTERVAL = 0.18

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._sample_rate = 0
        self._limits = (40.0, 180.0)
        self._buffer = np.empty((0, 1), dtype=np.float32)
        self._window = np.empty(0)
        self._hop = 0
        self._hop_seconds = self.HOP_SECONDS
        self._min_interval = self._MIN_INTERVAL
        self._samples_seen = 0
        self._bands: list[np.ndarray] = []
        self._spectra: deque[np.ndarray] = deque(maxlen=3)
        self._harmonic_history: deque[np.ndarray] = deque(maxlen=21)
        self._energies: deque[np.ndarray] = deque(maxlen=5)
        self._energy_history: deque[np.ndarray] = deque(maxlen=500)
        self._background: np.ndarray | None = None
        self._novelties: deque[float] = deque(maxlen=3)
        self._noise_history: deque[float] = deque(maxlen=150)
        self._peaks: deque[float] = deque(maxlen=16)
        self._accepted_points: deque[tuple[float, float]] = deque(maxlen=12)
        self._candidate_events: deque[float] = deque(maxlen=64)
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
        """Consume mono/stereo PCM and return one result per analysis hop."""
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
        size = max(128, 2 ** round(np.log2(sample_rate * 0.046)))
        self._hop = max(1, round(sample_rate * self._hop_seconds))
        self._window = np.hanning(size)
        self._buffer = np.empty((0, channels), dtype=np.float32)
        freqs = rfftfreq(size, 1.0 / sample_rate)
        low, high = limits
        edges = (low, high, max(450.0, high + 100.0), 2200.0, 8000.0)
        self._bands = [
            (freqs >= start) & (freqs < end) for start, end in zip(edges, edges[1:])
        ]

    def _spectralNovelty(self, frame: np.ndarray) -> float:
        spectra = np.abs(rfft(frame * self._window[:, None], axis=0))
        spectrum = np.sqrt(np.mean(spectra**2, axis=1)) * (2.0 / self._window.sum())
        energies = np.array(
            [float(np.linalg.norm(spectrum[band])) for band in self._bands]
        )
        if self._background is None:
            self._background = spectrum.copy()
        background = self._background
        self._background = background * 0.98 + spectrum * 0.02
        self._spectra.append(spectrum)
        self._harmonic_history.append(spectrum)
        self._energies.append(energies)
        self._energy_history.append(energies.copy())
        if len(self._energies) < 5:
            return 0.0

        level = max(float(np.max(spectrum)), float(np.max(background)))
        if level < 1e-5:
            return 0.0
        previous = maximum_filter1d(self._spectra[0], size=3)
        floor = max(level * 0.01, 1e-6)
        difference = np.maximum(spectrum - previous, 0.0)
        flux = np.log1p(difference / np.maximum(background, floor))
        harmonic = np.median(np.asarray(self._harmonic_history), axis=0)
        percussive = median_filter(spectrum, size=9)
        flux *= percussive**2 / (percussive**2 + (harmonic * 2.0) ** 2 + floor**2)
        flux[spectrum < floor] = 0.0
        band_flux = np.array(
            [float(np.mean(flux[band])) if band.any() else 0.0 for band in self._bands]
        )
        widths = np.array(
            [
                float(np.sum(difference[band]) ** 2)
                / max(
                    float(np.sum(difference[band] ** 2)) * np.count_nonzero(band),
                    1e-12,
                )
                for band in self._bands
            ]
        )
        flatness = np.array(
            [
                self._spectralFlatness(difference[band]) if band.any() else 0.0
                for band in self._bands
            ]
        )

        energy_floor = max(level * 0.02, 1e-6)
        attack = np.log((energies + energy_floor) / (self._energies[-3] + energy_floor))
        previous_attack = np.log(
            (self._energies[-3] + energy_floor) / (self._energies[0] + energy_floor)
        )
        sharpness = np.maximum(attack - previous_attack, 0.0)
        attack_gate = np.clip((attack - 0.08) / 0.25, 0.0, 1.0)
        sharp_gate = np.clip((sharpness - 0.04) / 0.2, 0.0, 1.0)
        evidence = band_flux * attack_gate * sharp_gate
        low_width = np.clip((widths[0] - 0.3) / 0.3, 0.0, 1.0)
        low_flat = np.clip((flatness[0] - 0.18) / 0.42, 0.0, 1.0)
        upper_support = np.clip(
            (sqrt(evidence[1] * evidence[2]) - 0.04) / 0.14, 0.0, 1.0
        )
        upper_kick_support = upper_support
        kick_shape = 0.2 * low_width + 0.8 * low_flat
        kick = evidence[0] * kick_shape * (0.005 + 0.995 * upper_support)
        upper_kick = (
            evidence[1]
            * 0.5
            * upper_kick_support
            * np.clip((widths[1] - 0.25) / 0.35, 0.0, 1.0)
        )
        snare = sqrt(evidence[2] * evidence[3]) * np.clip(
            (min(widths[2:]) - 0.35) / 0.25, 0.0, 1.0
        )
        return float(max(kick, upper_kick, snare))

    @staticmethod
    def _spectralFlatness(values: np.ndarray) -> float:
        positive = np.asarray(values, dtype=np.float64)
        if positive.size == 0:
            return 0.0
        positive = np.maximum(positive, 1e-12)
        return float(np.exp(np.mean(np.log(positive))) / np.mean(positive))

    def _pickPeak(
        self, novelty: float, sensitivity: float, smoothing: float, threshold: float
    ) -> BeatFrame:
        hop_seconds = self._hop / self._sample_rate
        timestamp = (self._samples_seen + len(self._window) - self._hop) / (
            self._sample_rate
        )
        release = 0.10 + float(np.clip(smoothing, 0.0, 0.99)) * 0.24
        self._intensity *= exp(-hop_seconds / release)
        self._novelties.append(novelty)
        is_point = False
        if len(self._novelties) == 3 and len(self._noise_history) >= 45:
            left, peak, right = self._novelties
            if peak > left and peak >= right:
                history = np.asarray(self._noise_history)
                baseline = float(np.median(history)) if len(history) else 0.0
                deviation = (
                    float(np.median(abs(history - baseline))) if len(history) else 0.0
                )
                floor = max(0.035, baseline + deviation * 3.0)
                if peak > floor:
                    reference = max(
                        float(np.median(self._peaks)) if self._peaks else peak,
                        0.12,
                    )
                    raw_score = min(
                        1.0,
                        sqrt((peak - floor) / reference) * max(0.0, sensitivity),
                    )
                    self._candidate_events.append(timestamp)
                    while (
                        self._candidate_events
                        and timestamp - self._candidate_events[0] > 0.9
                    ):
                        self._candidate_events.popleft()

                    rhythm_confidence, rhythm_factor = self._rhythmSupport(timestamp)
                    density = len(self._candidate_events)
                    if rhythm_confidence < 0.45 and density > 5:
                        crowding = min(0.86, 0.18 * (density - 5))
                        rhythm_factor *= 1.0 - crowding
                    score = raw_score * rhythm_factor

                    attack = 0.24 + 0.20 * (1.0 - np.clip(smoothing, 0.0, 0.99))
                    self._intensity += (score - self._intensity) * attack

                    if timestamp - self._last_point >= self._min_interval:
                        density_gate = 1.0 + min(2.5, max(0, density - 5) * 0.3)
                        effective_threshold = max(0.01, threshold) * density_gate * (
                            1.0 - 0.30 * rhythm_confidence
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
        if timestamp - self._last_rhythm_update >= 0.12:
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
            ratios = (1.0, 1.5, 2.0, 2.5)
            if self._rhythm_confidence >= 0.72:
                ratios = (0.5, 1.0, 1.5, 2.0, 2.5)
            error = min(
                abs(elapsed - period * ratio) / (period * ratio) for ratio in ratios
            )
            phase = float(np.clip(error / 0.16, 0.0, 1.0))
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

        ratios = (1.0, 1.5, 2.0)
        error = min(
            abs(elapsed - period * ratio) / (period * ratio) for ratio in ratios
        )
        phase = float(np.clip(error / 0.38, 0.0, 1.0))
        factor = 1.0 - confidence * 0.62 * phase
        return confidence, max(0.30, factor)

    def _estimateRhythm(self) -> tuple[float, float]:
        if len(self._energy_history) < 180:
            return 0.0, 0.0
        energies = np.asarray(self._energy_history, dtype=np.float64)
        weights = np.array([2.0, 1.2, 1.0, 0.8], dtype=np.float64)
        signal = np.log1p(energies @ weights)
        signal -= median_filter(signal, size=31, mode='nearest')
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
        confidence = float(np.clip((scores[lag] - 0.12) / 0.5, 0.0, 1.0))
        return lag * self._hop_seconds, confidence
