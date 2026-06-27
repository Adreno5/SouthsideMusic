from __future__ import annotations
# from https://github.com/oguzhan-yilmaz/pyCrossfade

from dataclasses import dataclass
from math import pi

import numpy as np
from pydub import AudioSegment
import logging

_logger = logging.getLogger(__name__)

BPM_MIN = 50.0
BPM_MAX = 210.0


@dataclass
class CrossFadeInfo:
    start_seconds: float
    fade_seconds: float
    end_seconds: float
    sample_rate: int
    channels: int
    samples: np.ndarray
    target_speed: float = 1.0


def getCrossfade(
    current: AudioSegment,
    next: AudioSegment,
    crossfade_seconds: float,
    crossfade_strength: float,
) -> CrossFadeInfo:
    strength = _clamp(crossfade_strength, 0.0, 1.0)
    sample_rate = current.frame_rate
    channels = _target_channels(current, next)

    current_samples = _segment_to_samples(current, sample_rate, channels)
    next_samples = _segment_to_samples(next, sample_rate, channels)
    fade_frames = _fade_frames(
        current_samples,
        next_samples,
        sample_rate,
        crossfade_seconds,
        strength,
    )

    current_bpm = _detect_bpm(current_samples, sample_rate)
    next_bpm = _detect_bpm(next_samples, sample_rate)
    target_speed = _tempo_transition_speed(current_bpm, next_bpm)
    _logger.debug(
        'crossfade bpm current=%.2f next=%.2f speed=%.3f',
        current_bpm,
        next_bpm,
        target_speed,
    )

    if fade_frames <= 0:
        return CrossFadeInfo(
            start_seconds=len(current_samples) / sample_rate,
            fade_seconds=0.0,
            end_seconds=0.0,
            sample_rate=sample_rate,
            channels=channels,
            samples=np.zeros((0, channels), dtype=np.float32),
            target_speed=target_speed,
        )

    start_frame = len(current_samples) - fade_frames
    current_tail = _apply_speed_transition(
        current_samples[start_frame:],
        target_speed,
        fade_frames,
    )
    next_head = next_samples[:fade_frames]
    fade_out, fade_in = _equal_power_fades(fade_frames)
    mixed = current_tail * fade_out + next_head * fade_in
    mixed = _limit_samples(mixed)
    fade_seconds = fade_frames / sample_rate

    return CrossFadeInfo(
        start_seconds=start_frame / sample_rate,
        fade_seconds=fade_seconds,
        end_seconds=fade_seconds,
        sample_rate=sample_rate,
        channels=channels,
        samples=mixed,
        target_speed=target_speed,
    )


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _tempo_transition_speed(current_bpm: float, next_bpm: float) -> float:
    if current_bpm <= 0 or next_bpm <= 0:
        return 1.0

    ratio = next_bpm / current_bpm
    while ratio < 0.75:
        ratio *= 2
    while ratio > 1.5:
        ratio /= 2

    if ratio < 0.85 or ratio > 1.15:
        return 1.0
    return ratio


def _target_channels(current: AudioSegment, next: AudioSegment) -> int:
    if current.channels > 1 or next.channels > 1:
        return 2
    return 1


def _segment_to_samples(
    segment: AudioSegment,
    sample_rate: int,
    channels: int,
) -> np.ndarray:
    prepared = segment
    if prepared.frame_rate != sample_rate:
        prepared = prepared.set_frame_rate(sample_rate)
    if prepared.channels != channels:
        prepared = prepared.set_channels(channels)

    samples_raw = np.array(prepared.get_array_of_samples(), dtype=np.float32)
    if len(samples_raw) == 0:
        return np.zeros((0, channels), dtype=np.float32)

    max_val = np.iinfo(prepared.array_type).max if prepared.sample_width != 4 else 2**31
    normalized = samples_raw / max_val
    if channels <= 1:
        return normalized.reshape(-1, 1).astype(np.float32, copy=False)

    frame_count = len(normalized) // channels
    return (
        normalized[: frame_count * channels]
        .reshape(frame_count, channels)
        .astype(
            np.float32,
            copy=False,
        )
    )


def _fade_frames(
    current_samples: np.ndarray,
    next_samples: np.ndarray,
    sample_rate: int,
    crossfade_seconds: float,
    strength: float,
) -> int:
    requested_seconds = _adaptive_crossfade_seconds(
        current_samples,
        next_samples,
        sample_rate,
        crossfade_seconds,
        strength,
    )
    requested_frames = int(round(requested_seconds * sample_rate))
    return min(requested_frames, len(current_samples), len(next_samples))


def _adaptive_crossfade_seconds(
    current_samples: np.ndarray,
    next_samples: np.ndarray,
    sample_rate: int,
    crossfade_seconds: float,
    strength: float,
) -> float:
    max_seconds = min(
        12.0,
        len(current_samples) / sample_rate,
        len(next_samples) / sample_rate,
    )
    if max_seconds <= 0:
        return 0.0
    if crossfade_seconds > 0:
        return min(max_seconds, max(0.0, crossfade_seconds) * strength)

    tail_seconds = _active_tail_seconds(current_samples, sample_rate, max_seconds)
    intro_seconds = _active_intro_seconds(next_samples, sample_rate, max_seconds)
    base_seconds = max(2.0, min(8.0, (tail_seconds + intro_seconds) * 0.5))
    return min(max_seconds, base_seconds * (0.5 + strength * 0.5))


def _active_tail_seconds(
    samples: np.ndarray,
    sample_rate: int,
    max_seconds: float,
) -> float:
    frames = min(len(samples), int(max_seconds * sample_rate))
    if frames <= 0:
        return 0.0
    tail = samples[-frames:]
    window = max(1, sample_rate // 10)
    energy = _window_energy(tail, window)
    if len(energy) == 0:
        return 0.0
    peak = max(float(np.max(energy)), 1e-6)
    active = np.flatnonzero(energy >= peak * 0.08)
    if len(active) == 0:
        return min(max_seconds, 3.0)
    return min(max_seconds, (len(energy) - int(active[0])) * window / sample_rate)


def _active_intro_seconds(
    samples: np.ndarray,
    sample_rate: int,
    max_seconds: float,
) -> float:
    frames = min(len(samples), int(max_seconds * sample_rate))
    if frames <= 0:
        return 0.0
    intro = samples[:frames]
    window = max(1, sample_rate // 10)
    energy = _window_energy(intro, window)
    if len(energy) == 0:
        return 0.0
    peak = max(float(np.max(energy)), 1e-6)
    active = np.flatnonzero(energy >= peak * 0.08)
    if len(active) == 0:
        return min(max_seconds, 3.0)
    return min(max_seconds, (int(active[-1]) + 1) * window / sample_rate)


def _window_energy(samples: np.ndarray, window: int) -> np.ndarray:
    mono = np.mean(np.abs(samples), axis=1)
    usable = len(mono) // window * window
    if usable <= 0:
        return np.array([], dtype=np.float32)
    return mono[:usable].reshape(-1, window).mean(axis=1)


def _equal_power_fades(frames: int) -> tuple[np.ndarray, np.ndarray]:
    if frames <= 1:
        fade_out = np.zeros((frames, 1), dtype=np.float32)
        fade_in = np.ones((frames, 1), dtype=np.float32)
        return fade_out, fade_in

    progress = np.linspace(0.0, 1.0, frames, dtype=np.float32).reshape(-1, 1)
    fade_out = np.cos(progress * pi / 2).astype(np.float32, copy=False)
    fade_in = np.sin(progress * pi / 2).astype(np.float32, copy=False)
    return fade_out, fade_in


def _limit_samples(samples: np.ndarray) -> np.ndarray:
    if len(samples) == 0:
        return samples.astype(np.float32, copy=False)

    peak = float(np.max(np.abs(samples)))
    if peak > 1.0:
        samples = samples / peak
    return samples.astype(np.float32, copy=False)


def _detect_bpm(samples: np.ndarray, sample_rate: int) -> float:
    analysis_frames = min(len(samples), sample_rate * 45)
    if analysis_frames < sample_rate * 4:
        return 0.0

    mono = np.mean(samples[:analysis_frames], axis=1).astype(np.float64)
    mono -= float(np.mean(mono))
    peak = float(np.max(np.abs(mono)))
    if peak < 1e-5:
        return 0.0
    mono /= peak

    envelope_rate = 200
    envelope = _onset_envelope(mono, sample_rate, envelope_rate)
    if len(envelope) < envelope_rate * 4:
        return 0.0
    envelope -= float(np.mean(envelope))
    envelope = np.maximum(envelope, 0.0)
    energy = float(np.sum(envelope * envelope))
    if energy < 1e-6:
        return 0.0

    corr = np.correlate(envelope, envelope, mode='full')
    corr = corr[len(corr) // 2 + 1 :]
    if len(corr) < 2:
        return 0.0
    corr /= max(float(np.max(corr)), 1e-6)

    min_lag = int(envelope_rate * 60 / BPM_MAX)
    max_lag = int(envelope_rate * 60 / BPM_MIN)
    min_lag = max(1, min_lag)
    max_lag = min(len(corr), max_lag)
    if max_lag <= min_lag:
        return 0.0

    candidates = _tempo_candidates(corr, min_lag, max_lag, envelope_rate)
    if not candidates:
        return 0.0
    return _canonical_bpm(_select_tempo(candidates))


def _onset_envelope(
    mono: np.ndarray,
    sample_rate: int,
    envelope_rate: int,
) -> np.ndarray:
    hop = max(1, sample_rate // envelope_rate)
    usable = len(mono) // hop * hop
    if usable <= hop:
        return np.array([], dtype=np.float64)

    frames = mono[:usable].reshape(-1, hop)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    flux = np.maximum(np.diff(rms, prepend=rms[0]), 0.0)
    return _moving_average(flux, max(1, int(envelope_rate * 0.04)))


def _tempo_candidates(
    corr: np.ndarray,
    min_lag: int,
    max_lag: int,
    envelope_rate: int,
) -> list[tuple[float, float]]:
    scores: list[tuple[float, float]] = []
    lag_span = max(1, max_lag - min_lag)
    for lag in range(min_lag, max_lag + 1):
        score = float(corr[lag])
        if lag > min_lag:
            score += float(corr[lag - 1]) * 0.25
        if lag + 1 < len(corr):
            score += float(corr[lag + 1]) * 0.25
        score *= 1.0 + (max_lag - lag) / lag_span * 0.35
        bpm = 60.0 * envelope_rate / lag
        scores.append((score, bpm))
    scores.sort(reverse=True, key=lambda item: item[0])
    return scores[:8]


def _select_tempo(candidates: list[tuple[float, float]]) -> float:
    best_score, best_bpm = candidates[0]
    octave_min = best_bpm * 1.85
    octave_max = best_bpm * 2.15
    octave_candidates = [
        (score, bpm)
        for score, bpm in candidates[1:]
        if octave_min <= bpm <= octave_max and bpm <= BPM_MAX * 1.03
    ]
    if not octave_candidates:
        return best_bpm

    octave_score, octave_bpm = max(octave_candidates, key=lambda item: item[0])
    if octave_score >= best_score * 0.88:
        return octave_bpm
    return best_bpm


def _canonical_bpm(bpm: float) -> float:
    while bpm < BPM_MIN:
        bpm *= 2.0
    while bpm > BPM_MAX * 1.03:
        bpm /= 2.0
    return float(bpm)


def _moving_average(data: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return data
    kernel = np.ones(window, dtype=np.float64) / window
    return np.convolve(data, kernel, mode='same')


def _apply_speed_transition(
    samples: np.ndarray,
    target_speed: float,
    frames: int,
) -> np.ndarray:
    if target_speed == 1.0 or frames <= 1:
        return samples.copy().astype(np.float32, copy=False)

    src = np.arange(frames, dtype=np.float64)
    mapped = src + (target_speed - 1.0) * src * src / (2.0 * frames)
    mapped = np.clip(mapped, 0.0, float(frames - 1))

    result = np.zeros((frames, samples.shape[1]), dtype=np.float32)
    for ch in range(samples.shape[1]):
        result[:, ch] = np.interp(
            mapped, src, samples[:frames, ch].astype(np.float64)
        ).astype(np.float32)
    return result
