from __future__ import annotations
# from https://github.com/oguzhan-yilmaz/pyCrossfade

from dataclasses import dataclass
from math import pi

import numpy as np
from pydub import AudioSegment


@dataclass
class CrossFadeInfo:
    start_seconds: float
    fade_seconds: float
    end_seconds: float
    sample_rate: int
    channels: int
    samples: np.ndarray


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

    if fade_frames <= 0:
        return CrossFadeInfo(
            start_seconds=len(current_samples) / sample_rate,
            fade_seconds=0.0,
            end_seconds=0.0,
            sample_rate=sample_rate,
            channels=channels,
            samples=np.zeros((0, channels), dtype=np.float32),
        )

    start_frame = len(current_samples) - fade_frames
    current_tail = current_samples[start_frame:]
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

    max_val = (
        np.iinfo(prepared.array_type).max
        if prepared.sample_width != 4
        else 2**31
    )
    normalized = samples_raw / max_val
    if channels <= 1:
        return normalized.reshape(-1, 1).astype(np.float32, copy=False)

    frame_count = len(normalized) // channels
    return normalized[: frame_count * channels].reshape(frame_count, channels).astype(
        np.float32,
        copy=False,
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
