from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from itertools import pairwise
from math import ceil, exp, pi, sqrt

import numpy as np
from scipy.fft import rfft, rfftfreq
from scipy.ndimage import maximum_filter1d, median_filter

_WINDOW_SECONDS = 0.046
_HISTORY_SECONDS = 6.0
_TEMPO_UPDATE_SECONDS = 0.25
_MIN_PERIOD = 60.0 / 210.0
_MAX_PERIOD = 60.0 / 50.0
_ONSET_GAP_SECONDS = 0.19
_SILENCE_LEVEL = 0.002
_ATTACK_OFFSET = 0.08
_ATTACK_SPAN = 0.25
_SHARPNESS_OFFSET = 0.04
_SHARPNESS_SPAN = 0.2
_KICK_WIDTH_OFFSET = 0.3
_KICK_WIDTH_SPAN = 0.3
_KICK_FLATNESS_OFFSET = 0.18
_KICK_FLATNESS_SPAN = 0.42
_UPPER_SUPPORT_OFFSET = 0.04
_UPPER_SUPPORT_SPAN = 0.14
_UPPER_KICK_WIDTH_OFFSET = 0.25
_UPPER_KICK_WIDTH_SPAN = 0.35
_SNARE_WIDTH_OFFSET = 0.35
_SNARE_WIDTH_SPAN = 0.25


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
        self._window = np.empty(0, dtype=np.float64)
        self._hop = 0
        self._hop_seconds = self.HOP_SECONDS
        self._min_interval = self._MIN_INTERVAL
        self._samples_seen = 0
        self._bands: list[np.ndarray] = []
        self._background: np.ndarray | None = None
        self._spectra: deque[np.ndarray] = deque(maxlen=3)
        self._harmonics: deque[np.ndarray] = deque(maxlen=21)
        self._energies: deque[np.ndarray] = deque(maxlen=5)
        self._novelties: deque[float] = deque(maxlen=3)
        self._noise_history: deque[float] = deque(maxlen=150)
        self._peaks: deque[float] = deque(maxlen=16)
        self._candidate_events: deque[float] = deque(maxlen=64)
        self._tempo_history: deque[float] = deque()
        self._meter_history: deque[float] = deque()
        self._meter_attack = 0.0
        self._events: deque[tuple[float, float]] = deque(maxlen=160)
        self._off_grid: float | None = None
        self._last_peak = -10.0
        self._last_onset = -10.0
        self._last_point = -10.0
        self._last_tempo_update = -10.0
        self._period = 0.0
        self._next_beat = 0.0
        self._confidence = 0.0
        self._missed = 0
        self._intensity = 0.0

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
        low = max(25.0, float(low_hz))
        limits = (low, max(low + 30.0, float(high_hz)))
        if (
            sample_rate != self._sample_rate
            or audio.shape[1] != self._buffer.shape[1]
            or limits != self._limits
            or hop_seconds != self._hop_seconds
        ):
            self._configure(
                sample_rate, audio.shape[1], limits, hop_seconds, min_interval
            )
        else:
            self._min_interval = min_interval

        self._buffer = np.concatenate((self._buffer, audio))
        results: list[BeatFrame] = []
        size = len(self._window)
        consumed = 0
        while len(self._buffer) - consumed >= size:
            frame = self._buffer[consumed : consumed + size]
            timestamp = (self._samples_seen + size * 0.5) / sample_rate
            novelty = self._onsetStrength(frame)
            beat = self._finishFrame(
                timestamp, novelty, sensitivity, smoothing, point_threshold
            )
            if beat is not None:
                results.append(beat)
            consumed += self._hop
            self._samples_seen += self._hop
        self._buffer = self._buffer[consumed:].copy()
        return results

    def getRhythm(self) -> tuple[float, float, float]:
        if self._period <= 0.0 or self._confidence < 0.3 or self._sample_rate <= 0:
            return 0.0, 0.0, 0.0
        end = (self._samples_seen + len(self._buffer)) / self._sample_rate
        next_beat = (
            self._next_beat
            + max(0, ceil((end - self._next_beat) / self._period)) * self._period
        )
        return self._period, next_beat, self._confidence

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
        duration = self._hop / sample_rate
        self._tempo_history = deque(maxlen=max(60, ceil(_HISTORY_SECONDS / duration)))
        self._meter_history = deque(maxlen=self._tempo_history.maxlen)
        freqs = rfftfreq(size, 1.0 / sample_rate)
        low, high = limits
        middle = max(450.0, high + 250.0)
        upper = max(2200.0, middle + 1500.0)
        edges = (low, high, middle, upper, max(8000.0, upper + 5800.0))
        self._bands = [
            (freqs >= start) & (freqs < end) for start, end in pairwise(edges)
        ]

    def _onsetStrength(self, frame: np.ndarray) -> float:
        self._meter_attack = 0.0
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
        self._background = background * 0.98 + spectrum * 0.02
        self._spectra.append(spectrum)
        self._harmonics.append(spectrum)
        self._energies.append(energies)
        if len(self._energies) < 5:
            return 0.0

        level = max(float(np.max(spectrum)), float(np.max(background)))
        if level < _SILENCE_LEVEL:
            return 0.0
        previous = maximum_filter1d(self._spectra[0], size=3)
        floor = max(level * 0.01, 1e-6)
        difference = np.maximum(spectrum - previous, 0.0)
        flux = np.log1p(difference / np.maximum(background, floor))
        relative_flux = np.log1p(difference / np.maximum(spectrum, floor))
        low_attack = (
            float(np.mean(relative_flux[self._bands[0]]))
            if self._bands[0].any()
            else 0.0
        )
        upper_attack = (
            float(np.mean(relative_flux[self._bands[3]]))
            if self._bands[3].any()
            else 0.0
        )
        broadband = (
            0.55
            * float(np.clip((low_attack - 0.10) / 0.35, 0.0, 1.0))
            * float(np.clip((upper_attack - 0.35) / 0.3, 0.0, 1.0))
        )
        self._meter_attack = broadband
        harmonic = np.min(np.asarray(self._harmonics), axis=0)
        percussive = median_filter(spectrum, size=9)
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
            self._spectralFlatness(difference[band]) for band in self._bands
        ])

        energy_floor = max(level * 0.02, 1e-6)
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
        kick_shape = 0.2 * low_width + 0.8 * low_flat
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
        return float(max(kick, upper_kick, snare, broadband))

    @staticmethod
    def _spectralFlatness(values: np.ndarray) -> float:
        positive = np.maximum(np.asarray(values, dtype=np.float64), 1e-12)
        if positive.size == 0:
            return 0.0
        return float(np.exp(np.mean(np.log(positive))) / np.mean(positive))

    def _finishFrame(
        self,
        timestamp: float,
        novelty: float,
        sensitivity: float,
        smoothing: float,
        point_threshold: float,
    ) -> BeatFrame | None:
        hop_seconds = self._hop / self._sample_rate
        time = timestamp - hop_seconds
        self._novelties.append(novelty)
        history = np.asarray(self._noise_history)
        baseline = float(np.median(history)) if len(history) else 0.0
        deviation = (
            float(np.median(np.abs(history - baseline))) if len(history) else 0.0
        )
        floor = max(0.035, baseline + deviation * 3.0)
        self._tempo_history.append(
            min(2.0, max(0.0, novelty - floor) / max(0.12, floor))
        )
        self._meter_history.append(self._meter_attack)
        smoothing = float(np.clip(smoothing, 0.0, 0.99))
        release = 0.10 + smoothing * 0.24
        self._intensity *= exp(-hop_seconds / release)
        threshold = float(np.clip(point_threshold, 0.01, 1.0))
        is_point = False
        candidate = False
        new_event = False
        score = 0.0
        peak = 0.0
        if len(self._novelties) == 3 and len(history) >= 5:
            left, peak, right = self._novelties
            if peak > left and peak >= right and peak > floor:
                reference = max(
                    float(np.median(self._peaks)) if self._peaks else peak,
                    0.12,
                )
                raw_score = min(1.0, sqrt((peak - floor) / reference))
                score = min(1.0, raw_score * max(0.0, sensitivity))
                candidate = True
                self._candidate_events.append(time)
                while self._candidate_events and time - self._candidate_events[0] > 0.9:
                    self._candidate_events.popleft()
                if raw_score >= 0.25 and time - self._last_peak >= _ONSET_GAP_SECONDS:
                    self._events.append((time, raw_score))
                    self._last_peak = time
                    self._last_onset = time
                    new_event = True
                attack = 0.24 + 0.20 * (1.0 - smoothing)
                self._intensity += (score - self._intensity) * attack

        while self._events and time - self._events[0][0] > _HISTORY_SECONDS:
            self._events.popleft()
        self._updateTempo(time)
        if self._period > 0.0 and self._confidence >= 0.3:
            tolerance = max(
                1.5 * hop_seconds,
                min(0.08, self._period * 0.12),
            )
            while self._next_beat + tolerance < time:
                self._next_beat += self._period
                self._missed += 1
                self._confidence *= 0.65 if self._missed >= 2 else 0.8
            if (
                candidate
                and score >= threshold
                and abs(time - self._next_beat) <= tolerance
            ):
                correction = (time - self._next_beat) * 0.15
                self._next_beat += self._period + correction
                self._missed = 0
                self._off_grid = None
                self._confidence = min(1.0, self._confidence + 0.08)
                if time - self._last_point >= self._min_interval:
                    is_point = True
                    self._last_point = time
            elif new_event and score >= max(0.35, threshold) and self._missed >= 1:
                previous = self._off_grid
                if previous is not None:
                    new_period = time - previous
                    if _MIN_PERIOD <= new_period <= _MAX_PERIOD:
                        self._period = new_period
                        self._next_beat = time + new_period
                        self._confidence = max(0.55, self._confidence * 0.8)
                        self._missed = 0
                        if time - self._last_point >= self._min_interval:
                            is_point = True
                            self._last_point = time
                self._off_grid = None if is_point else time
            if self._confidence < 0.25:
                self._period = 0.0

        if (
            not is_point
            and candidate
            and (self._period == 0.0 or self._confidence < 0.35)
            and time - self._last_point >= max(self._min_interval, _MIN_PERIOD * 0.94)
        ):
            density = len(self._candidate_events)
            crowding = 1.0
            if density > 5:
                crowding = 1.0 - min(0.86, 0.18 * (density - 5))
            density_gate = 1.0 + min(2.5, max(0, density - 5) * 0.3)
            required = threshold * density_gate
            if time - self._last_point > _MAX_PERIOD + 0.1:
                required = max(0.6, required)
            if score * crowding >= required:
                is_point = True
                self._last_point = time

        if is_point:
            self._peaks.append(peak)
        if self._period == 0.0:
            self._off_grid = None

        self._noise_history.append(novelty)
        if self._intensity < 0.005:
            self._intensity = 0.0
        return (
            BeatFrame(time, self._intensity, is_point)
            if len(self._novelties) == 3
            else None
        )

    def _updateTempo(self, timestamp: float) -> None:
        if timestamp - self._last_tempo_update < _TEMPO_UPDATE_SECONDS:
            return
        self._last_tempo_update = timestamp
        stale_limit = max(1.2, self._period * 2.0)
        if self._last_onset >= 0.0 and timestamp - self._last_onset > stale_limit:
            self._period = 0.0
            self._confidence = 0.0
            self._missed = 0
            self._events.clear()
            self._tempo_history.clear()
            self._meter_history.clear()
            self._off_grid = None
            return

        period, phase, confidence = self._estimateTempo()
        if confidence < 0.35:
            self._confidence *= 0.94
        elif (
            self._period == 0.0
            or self._confidence < 0.28
            or (
                abs(period - self._period) / self._period > 0.1
                and self._missed >= 2
                and confidence >= 0.5
            )
        ):
            if confidence >= 0.45:
                self._period = period
                self._confidence = confidence
                self._next_beat = (
                    phase + ceil((timestamp - 0.05 - phase) / period) * period
                )
                self._missed = 0
        elif abs(period - self._period) / self._period <= 0.1:
            candidate_beat = phase + round((self._next_beat - phase) / period) * period
            correction = float(np.clip(candidate_beat - self._next_beat, -0.04, 0.04))
            self._next_beat += correction * 0.2
            self._period = self._period * 0.85 + period * 0.15
            self._confidence = self._confidence * 0.8 + confidence * 0.2
        else:
            self._confidence *= 0.96
        if self._confidence < 0.25:
            self._period = 0.0

    def _estimateTempo(self) -> tuple[float, float, float]:
        duration = self._hop / self._sample_rate
        n = len(self._tempo_history)
        if n * duration < 2.5 or len(self._events) < 4:
            return 0.0, 0.0, 0.0
        signal = np.asarray(self._tempo_history).copy()
        signal -= float(np.mean(signal))
        signal *= np.linspace(0.5, 1.0, n)
        if float(np.dot(signal, signal)) < 0.05:
            return 0.0, 0.0, 0.0

        min_lag = max(1, round(_MIN_PERIOD / duration))
        max_lag = min(round(_MAX_PERIOD / duration), n // 3)
        if max_lag < min_lag:
            return 0.0, 0.0, 0.0
        correlations = np.zeros(max_lag + 2, dtype=np.float64)
        for lag in range(min_lag, max_lag + 1):
            left = signal[:-lag]
            right = signal[lag:]
            denominator = sqrt(float(np.dot(left, left) * np.dot(right, right)))
            if denominator > 1e-8:
                correlations[lag] = float(np.dot(left, right)) / denominator

        event_times = np.fromiter(
            (event[0] for event in self._events), dtype=np.float64
        )
        event_weights = np.fromiter(
            (event[1] for event in self._events), dtype=np.float64
        )
        total_weight = float(np.sum(event_weights))
        gaps = event_times[None, :] - event_times[:, None]
        pair_weights = event_weights[None, :] * event_weights[:, None]
        best_score = 0.0
        best_lag = 0
        best_period = 0.0
        best_phase = 0.0
        for lag in range(min_lag, max_lag + 1):
            correlation = correlations[lag]
            if (
                correlation < 0.2
                or correlation < correlations[lag - 1]
                or correlation < correlations[lag + 1]
            ):
                continue
            period = lag * duration
            multiples = np.rint(gaps / period)
            intervals = gaps / np.maximum(multiples, 1.0)
            valid = (
                (multiples >= 1.0)
                & (multiples <= 3.0)
                & (np.abs(intervals - period) <= period * 0.12)
            )
            if np.count_nonzero(valid) >= 3:
                period = float(
                    np.average(intervals[valid], weights=pair_weights[valid])
                )
            period = float(np.clip(period, _MIN_PERIOD, _MAX_PERIOD))
            phasor = np.dot(event_weights, np.exp(2j * pi * event_times / period))
            concentration = abs(phasor) / max(total_weight, 1e-8)
            score = correlation * 0.77 + concentration * 0.23
            if self._period > 0.0:
                difference = abs(period - self._period) / self._period
                if difference < 0.08:
                    score += 0.04 * self._confidence
            if score > best_score + 0.025 or (
                score >= best_score - 0.025
                and (best_period == 0.0 or period < best_period)
            ):
                best_score = score
                best_lag = lag
                best_period = period
                best_phase = float(np.angle(phasor) % (2.0 * pi)) * period / (2.0 * pi)
        if best_lag > 0 and best_lag * 2 <= max_lag:
            meter = np.asarray(self._meter_history)
            meter_total = float(np.sum(meter))
            if meter_total > 0.1:
                meter_signal = (meter - float(np.mean(meter))) * np.linspace(
                    0.5, 1.0, n
                )
                meter_correlations = []
                meter_phases = []
                relative_times = np.arange(n) * duration
                for lag in (best_lag, best_lag * 2):
                    left = meter_signal[:-lag]
                    right = meter_signal[lag:]
                    denominator = sqrt(float(np.dot(left, left) * np.dot(right, right)))
                    meter_correlations.append(
                        float(np.dot(left, right)) / denominator
                        if denominator > 1e-8
                        else 0.0
                    )
                    period = lag * duration
                    phasor = np.dot(meter, np.exp(2j * pi * relative_times / period))
                    meter_phases.append(abs(phasor) / meter_total)
                if (
                    correlations[best_lag] < 0.78
                    and meter_correlations[1] > meter_correlations[0] + 0.1
                    and meter_phases[1] >= meter_phases[0] * 0.85
                    and correlations[best_lag * 2] >= 0.25
                ):
                    best_period = min(best_period * 2.0, _MAX_PERIOD)
                    start_time = (
                        self._samples_seen + len(self._window) * 0.5
                    ) / self._sample_rate - (n - 1) * duration
                    phasor = np.dot(
                        meter,
                        np.exp(2j * pi * (start_time + relative_times) / best_period),
                    )
                    best_phase = (
                        float(np.angle(phasor) % (2.0 * pi)) * best_period / (2.0 * pi)
                    )
                    best_score = max(
                        best_score,
                        correlations[best_lag * 2] * 0.77
                        + meter_phases[1] * 0.23
                        + 0.05,
                    )
        if best_score < 0.53:
            return 0.0, 0.0, 0.0
        confidence = float(np.clip((best_score - 0.32) / 0.5, 0.0, 1.0))
        return best_period, best_phase, confidence
