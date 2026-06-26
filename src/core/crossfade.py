from __future__ import annotations
# from https://github.com/oguzhan-yilmaz/pyCrossfade

from dataclasses import dataclass
from math import pi

import numpy as np
from pydub import AudioSegment
import logging

_logger = logging.getLogger(__name__)


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
    if current_bpm > 0 and next_bpm > 0:
        target_speed = _clamp(next_bpm / current_bpm, 0.1, 10.0)
    else:
        target_speed = 1.0

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
    requested_seconds = max(0.0, crossfade_seconds) * strength
    requested_frames = int(round(requested_seconds * sample_rate))
    return min(requested_frames, len(current_samples), len(next_samples))


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
    analysis_frames = min(len(samples), sample_rate * 20)
    if analysis_frames < sample_rate:
        return 0.0

    mono = np.mean(samples[:analysis_frames], axis=1).astype(np.float64)

    onset = np.abs(np.diff(mono))

    target_rate = 200
    decimate_factor = max(1, sample_rate // target_rate)
    trimmed = onset[: len(onset) // decimate_factor * decimate_factor]
    onset_dec = trimmed.reshape(-1, decimate_factor).max(axis=1).astype(np.float64)

    window = max(1, int(target_rate * 0.03))
    onset_dec = _moving_average(onset_dec, window)

    effective_sr = sample_rate / decimate_factor

    corr = np.correlate(onset_dec, onset_dec, mode='full')
    corr = corr[len(corr) // 2 + 1 :]

    if len(corr) < 2:
        return 0.0

    min_lag = int(effective_sr * 60 / 200)
    max_lag = int(effective_sr * 60 / 40)
    min_lag = max(1, min_lag)
    max_lag = min(len(corr), max_lag)
    if max_lag <= min_lag:
        return 0.0

    region = corr[min_lag : max_lag + 1]
    peak_idx = int(np.argmax(region)) + min_lag
    bpm = 60.0 * effective_sr / peak_idx
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
