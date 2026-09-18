from __future__ import annotations
# Inspiration from https://github.com/oguzhan-yilmaz/pyCrossfade

import base64
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import os
from math import pi

import numpy as np
from pydub import AudioSegment
import logging

from core.wsola import WsolaStretcher

_logger = logging.getLogger(__name__)

BPM_MIN = 50.0
BPM_MAX = 210.0

_ANALYSIS_BLOCK_MS = 10
_SILENCE_FLOOR_DB = -45.0
_SILENCE_ABS_LEVEL = 1e-4
_BODY_LEVEL = 0.35
_LIVE_HF_RATIO = 0.35
_SMART_TILT = 1.5
_ABRUPT_TILT = 2.0
_MAX_LEAD_COVER_SECONDS = 6.0

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_CROSSFADE_CACHE_DIR = os.path.join(_PROJECT_ROOT, 'data', 'crossfade_cache')


class EndingType(str, Enum):
    FADE_OUT = 'fade_out'
    ABRUPT = 'abrupt'
    SUSTAINED = 'sustained'
    LIVE = 'live'


@dataclass
class CrossFadeInfo:
    start_seconds: float
    fade_seconds: float
    end_seconds: float
    sample_rate: int
    channels: int
    samples: np.ndarray
    target_speed: float = 1.0
    ending_type: str = ''
    current_key: str = ''
    next_key: str = ''
    key_compatibility: float = 0.0
    fade_out_profile: tuple[float, ...] = ()
    fade_in_profile: tuple[float, ...] = ()
    transition_type: str = 'smart_crossfade'
    timbre_similarity: float = 0.0
    beat_phase: float = 0.0

    def debugInfo(self) -> list[str]:
        samples_shape = tuple(int(value) for value in self.samples.shape)
        peak = float(np.max(np.abs(self.samples))) if self.samples.size else 0.0
        return [
            f'start_seconds={self.start_seconds:.3f}',
            f'fade_seconds={self.fade_seconds:.3f}',
            f'end_seconds={self.end_seconds:.3f}',
            f'sample_rate={self.sample_rate}',
            f'channels={self.channels}',
            f'samples_shape={samples_shape}',
            f'samples_peak={peak:.4f}',
            f'target_speed={self.target_speed:.4f}',
            f'ending_type={self.ending_type or None}',
            f'current_key={self.current_key or None}',
            f'next_key={self.next_key or None}',
            f'key_compatibility={self.key_compatibility:.3f}',
            f'transition_type={self.transition_type}',
            f'timbre_similarity={self.timbre_similarity:.3f}',
            f'beat_phase={self.beat_phase:.3f}',
        ]

    def to_dict(self) -> dict:
        samples_b64 = base64.b64encode(self.samples.tobytes()).decode('ascii')
        return {
            'start_seconds': self.start_seconds,
            'fade_seconds': self.fade_seconds,
            'end_seconds': self.end_seconds,
            'sample_rate': self.sample_rate,
            'channels': self.channels,
            'samples_b64': samples_b64,
            'samples_dtype': str(self.samples.dtype),
            'samples_shape': list(self.samples.shape),
            'target_speed': self.target_speed,
            'ending_type': self.ending_type,
            'current_key': self.current_key,
            'next_key': self.next_key,
            'key_compatibility': self.key_compatibility,
            'fade_out_profile': list(self.fade_out_profile),
            'fade_in_profile': list(self.fade_in_profile),
            'transition_type': self.transition_type,
            'timbre_similarity': self.timbre_similarity,
            'beat_phase': self.beat_phase,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CrossFadeInfo:
        raw = base64.b64decode(d['samples_b64'])
        shape = tuple(d['samples_shape'])
        samples = np.frombuffer(raw, dtype=d['samples_dtype']).reshape(shape)
        return cls(
            start_seconds=d['start_seconds'],
            fade_seconds=d['fade_seconds'],
            end_seconds=d['end_seconds'],
            sample_rate=d['sample_rate'],
            channels=d['channels'],
            samples=samples,
            target_speed=d.get('target_speed', 1.0),
            ending_type=d.get('ending_type', ''),
            current_key=d.get('current_key', ''),
            next_key=d.get('next_key', ''),
            key_compatibility=d.get('key_compatibility', 0.0),
            fade_out_profile=tuple(float(v) for v in d.get('fade_out_profile', ())),
            fade_in_profile=tuple(float(v) for v in d.get('fade_in_profile', ())),
            transition_type=d.get('transition_type', 'smart_crossfade'),
            timbre_similarity=float(d.get('timbre_similarity', 0.0)),
            beat_phase=float(d.get('beat_phase', 0.0)),
        )

    def cache_path(self, token: str) -> str:
        os.makedirs(_CROSSFADE_CACHE_DIR, exist_ok=True)
        return os.path.join(_CROSSFADE_CACHE_DIR, f'{token}.json')

    def save_to_cache(self, token: str) -> None:
        try:
            path = self.cache_path(token)
            with open(path, 'w', encoding='utf-8') as f:
                payload = self.to_dict()
                payload['cache_token'] = token
                json.dump(payload, f)
        except Exception:
            _logger.debug('failed to save crossfade cache', exc_info=True)

    @classmethod
    def load_from_cache(
        cls, token: str, sample_rate: int, channels: int
    ) -> CrossFadeInfo | None:
        try:
            path = os.path.join(_CROSSFADE_CACHE_DIR, f'{token}.json')
            if not os.path.exists(path):
                return None
            with open(path, 'r', encoding='utf-8') as f:
                d = json.load(f)
            if d.get('cache_token') != token:
                return None
            info = cls.from_dict(d)
            if info.sample_rate != sample_rate or info.channels != channels:
                return None
            return info
        except Exception:
            return None


_CAMELOT_MAP: dict[str, str] = {
    'C': '8B',
    'B#': '8B',
    'G': '9B',
    'D': '10B',
    'A': '11B',
    'E': '12B',
    'B': '1B',
    'Cb': '1B',
    'F#': '2B',
    'Gb': '2B',
    'C#': '3B',
    'Db': '3B',
    'G#': '4B',
    'Ab': '4B',
    'D#': '5B',
    'Eb': '5B',
    'A#': '6B',
    'Bb': '6B',
    'F': '7B',
    'Am': '8A',
    'Em': '9A',
    'Bm': '10A',
    'F#m': '11A',
    'Gbm': '11A',
    'C#m': '12A',
    'Dbm': '12A',
    'G#m': '1A',
    'Abm': '1A',
    'D#m': '2A',
    'Ebm': '2A',
    'A#m': '3A',
    'Bbm': '3A',
    'Fm': '4A',
    'Cm': '5A',
    'Gm': '6A',
    'Dm': '7A',
}

_KS_PROFILES_MAJOR = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
_KS_PROFILES_MINOR = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)

_NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

_CHROMA_HOP = 2048
_CHROMA_FFT = _CHROMA_HOP * 2
_CHROMA_LOW_HZ = 55.0
_CHROMA_HIGH_HZ = 4000.0
_CHROMA_BASS_LOW_HZ = 55.0
_CHROMA_BASS_HIGH_HZ = 250.0
_CHROMA_GATE = 0.2
_KEY_WINDOW_SECONDS = 12.0
_KEY_SEGMENT_SECONDS = 5.0
_KEY_SEGMENT_STEP_SECONDS = 2.5
_KEY_FOCUS_FLOOR = 0.5
_KEY_BASS_WEIGHT = 0.4
_KEY_MARGIN_SPREAD = 0.15

_KEY_PROFILES = np.vstack(
    (
        np.stack([np.roll(_KS_PROFILES_MAJOR, shift) for shift in range(12)]),
        np.stack([np.roll(_KS_PROFILES_MINOR, shift) for shift in range(12)]),
    )
).astype(np.float64)
_KEY_PROFILES_CENTERED = _KEY_PROFILES - _KEY_PROFILES.mean(axis=1, keepdims=True)
_KEY_PROFILE_NORMS = np.maximum(np.linalg.norm(_KEY_PROFILES_CENTERED, axis=1), 1e-9)


def _block_rms(
    samples: np.ndarray,
    sample_rate: int,
    block_ms: int = _ANALYSIS_BLOCK_MS,
) -> tuple[np.ndarray, int]:
    block = max(1, int(round(sample_rate * block_ms / 1000.0)))
    usable = len(samples) // block
    if usable <= 0:
        return np.zeros(0, dtype=np.float32), block
    frames = samples[-usable * block :].reshape(usable, block, -1)
    rms = np.sqrt(np.mean(frames * frames, axis=(1, 2)))
    return rms.astype(np.float32, copy=False), block


def _silence_span(
    samples: np.ndarray,
    sample_rate: int,
    floor_db: float = _SILENCE_FLOOR_DB,
) -> tuple[int, int]:
    total = len(samples)
    if total <= 0:
        return 0, 0
    rms, block = _block_rms(samples, sample_rate)
    if len(rms) == 0:
        return total, 0
    reference = float(np.percentile(rms, 90))
    if reference < _SILENCE_ABS_LEVEL:
        return total, 0
    floor = max(reference * 10.0 ** (floor_db / 20.0), _SILENCE_ABS_LEVEL)
    loud = np.flatnonzero(rms >= floor)
    if len(loud) == 0:
        return total, 0
    prefix = total - len(rms) * block
    lead = prefix + int(loud[0]) * block
    tail = total - (int(loud[-1]) + 1) * block
    return lead, max(0, tail)


def _high_band_ratio(samples: np.ndarray, sample_rate: int) -> float:
    window_frames = min(len(samples), sample_rate * 2)
    mono = samples[-window_frames:] if window_frames > 0 else samples
    if mono.ndim == 2:
        mono = mono[:, 0]
    frame_size = min(len(mono), 4096)
    if frame_size < 256:
        return 0.0
    window = np.hanning(frame_size)
    starts = np.linspace(0, len(mono) - frame_size, 9).astype(np.intp)
    split = frame_size // 2 * 6 // 10
    ratios: list[float] = []
    for start in starts:
        spectrum = np.abs(np.fft.rfft(mono[start : start + frame_size] * window))
        power = spectrum * spectrum
        low = float(np.sum(power[:split]))
        high = float(np.sum(power[split:]))
        ratios.append(high / max(low + high, 1e-12))
    return float(np.median(ratios))


def _classify_ending(samples: np.ndarray, sample_rate: int) -> EndingType:
    tail_seconds = min(12.0, len(samples) / sample_rate)
    tail_frames = int(tail_seconds * sample_rate)
    if tail_frames < sample_rate:
        return EndingType.FADE_OUT
    tail = samples[-tail_frames:]
    lead, trailing = _silence_span(tail, sample_rate)
    if lead >= len(tail):
        return EndingType.FADE_OUT
    active = tail[: len(tail) - trailing]
    rms, block = _block_rms(active, sample_rate)
    if len(rms) < 4:
        return EndingType.FADE_OUT
    body = float(np.percentile(rms, 85))
    if body < _SILENCE_ABS_LEVEL:
        return EndingType.FADE_OUT

    level = rms / body
    loud = np.flatnonzero(level >= _BODY_LEVEL)
    if len(loud) == 0:
        return EndingType.FADE_OUT
    release_seconds = (len(level) - 1 - int(loud[-1])) * block / sample_rate
    edge_blocks = max(1, int(round(0.3 * sample_rate / block)))
    edge_level = float(np.median(level[-edge_blocks:]))

    if release_seconds <= 0.35 and edge_level >= _BODY_LEVEL:
        if _high_band_ratio(active, sample_rate) >= _LIVE_HF_RATIO:
            return EndingType.LIVE
        return EndingType.ABRUPT
    if release_seconds >= 1.2:
        return EndingType.FADE_OUT
    if edge_level < 0.2:
        return EndingType.FADE_OUT
    return EndingType.SUSTAINED


def _detect_key(
    samples: np.ndarray, sample_rate: int, focus: str = 'end'
) -> tuple[str, float]:
    window = int(_KEY_WINDOW_SECONDS * sample_rate)
    if len(samples) > window:
        samples = samples[-window:] if focus == 'end' else samples[:window]
    if len(samples) < sample_rate // 2:
        return '', 0.0

    mono = np.mean(samples, axis=1).astype(np.float64)
    mono -= float(np.mean(mono))
    rms = float(np.sqrt(np.mean(mono * mono)))
    if rms < 1e-6:
        return '', 0.0
    mono /= rms

    folded = _compute_chromagram(mono, sample_rate)
    if folded is None:
        return '', 0.0
    chroma, bass = folded

    frames = len(chroma)
    segment_frames = max(
        1, min(frames, int(_KEY_SEGMENT_SECONDS * sample_rate / _CHROMA_HOP))
    )
    step = max(1, int(_KEY_SEGMENT_STEP_SECONDS * sample_rate / _CHROMA_HOP))
    if frames <= segment_frames:
        starts = np.array([0], dtype=np.intp)
    else:
        count = max(2, int(np.ceil((frames - segment_frames) / step)) + 1)
        starts = np.linspace(0, frames - segment_frames, count).astype(np.intp)

    proximity = np.linspace(_KEY_FOCUS_FLOOR, 1.0, len(starts)) ** 2
    if focus == 'start':
        proximity = proximity[::-1]
    proximity = proximity / float(np.sum(proximity))

    feature = np.zeros(12, dtype=np.float64)
    for start, weight in zip(starts, proximity):
        span = slice(int(start), int(start) + segment_frames)
        feature += weight * chroma[span].mean(axis=0)
        feature += weight * _KEY_BASS_WEIGHT * bass[span].mean(axis=0)

    scores = _profileScores(feature)
    order = np.argsort(scores)[::-1]
    best = float(scores[order[0]])
    margin = best - float(scores[order[1]])
    confidence = float(np.clip(margin / _KEY_MARGIN_SPREAD, 0.0, 1.0)) * float(
        np.clip(best, 0.0, 1.0)
    )

    note = _NOTE_NAMES[int(order[0]) % 12]
    if order[0] >= 12:
        return f'{note}m', confidence
    return note, confidence


def _profileScores(chroma: np.ndarray) -> np.ndarray:
    centered = chroma - float(np.mean(chroma))
    norm = float(np.linalg.norm(centered))
    if norm < 1e-9:
        return np.zeros(_KEY_PROFILES.shape[0], dtype=np.float64)
    return (_KEY_PROFILES_CENTERED @ centered) / (_KEY_PROFILE_NORMS * norm)


def _compute_chromagram(
    mono: np.ndarray, sample_rate: int, hop_length: int = _CHROMA_HOP
) -> tuple[np.ndarray, np.ndarray] | None:
    n_fft = hop_length * 2
    frames = (len(mono) - n_fft) // hop_length + 1
    if frames < 4:
        return None

    window = np.hanning(n_fft)
    view = np.lib.stride_tricks.sliding_window_view(mono, n_fft)[::hop_length][:frames]
    frame_rms = np.sqrt(np.mean(view * view, axis=1))
    spectrum = np.abs(np.fft.rfft(view * window, axis=1))
    del view

    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
    chroma = _foldPitchClasses(spectrum, freqs, _CHROMA_LOW_HZ, _CHROMA_HIGH_HZ)
    bass = _foldPitchClasses(spectrum, freqs, _CHROMA_BASS_LOW_HZ, _CHROMA_BASS_HIGH_HZ)

    gate = frame_rms >= max(float(np.median(frame_rms)) * _CHROMA_GATE, 1e-7)
    chroma[~gate] = 0.0
    bass[~gate] = 0.0
    return chroma, bass


def _foldPitchClasses(
    spectrum: np.ndarray,
    freqs: np.ndarray,
    low_hz: float,
    high_hz: float,
) -> np.ndarray:
    valid = np.flatnonzero((freqs >= low_hz) & (freqs <= high_hz))
    if len(valid) == 0:
        return np.zeros((len(spectrum), 12), dtype=np.float64)
    pitches = np.rint(12.0 * np.log2(freqs[valid] / 440.0) + 69.0).astype(np.intp) % 12
    fold = np.zeros((len(valid), 12), dtype=np.float32)
    fold[np.arange(len(valid)), pitches] = 1.0
    folded = (spectrum[:, valid] @ fold).astype(np.float64)
    norm = np.linalg.norm(folded, axis=1, keepdims=True)
    return folded / np.maximum(norm, 1e-9)


def _key_compatibility(key1: str, key2: str) -> float:
    if not key1 or not key2:
        return 0.0

    camelot1 = _get_camelot(key1)
    camelot2 = _get_camelot(key2)
    if not camelot1 or not camelot2:
        return 0.0

    num1 = int(camelot1[:-1])
    mode1 = camelot1[-1]
    num2 = int(camelot2[:-1])
    mode2 = camelot2[-1]

    if camelot1 == camelot2:
        return 1.0

    num_diff = abs(num1 - num2)
    if num_diff > 6:
        num_diff = 12 - num_diff

    if num_diff == 0 and mode1 != mode2:
        return 0.85
    if num_diff == 1 and mode1 == mode2:
        return 0.75
    if num_diff == 1 and mode1 != mode2:
        return 0.65
    if num_diff == 2:
        return 0.40
    return 0.10


def key_pitch_shift(key1: str, key2: str) -> float:
    """Return the smallest semitone shift that aligns two detected keys."""
    if not key1 or not key2:
        return 0.0
    note1 = key1.removesuffix('m')
    note2 = key2.removesuffix('m')
    try:
        first = _NOTE_NAMES.index(note1)
        second = _NOTE_NAMES.index(note2)
    except ValueError:
        return 0.0
    shift = (first - second) % 12
    if shift > 6:
        shift -= 12
    return float(shift)


def _get_camelot(key: str) -> str:
    return _CAMELOT_MAP.get(key, '')


def _cache_token(
    current_id: str,
    next_id: str,
    sample_rate: int,
    channels: int,
    crossfade_seconds: float,
    crossfade_strength: float,
    max_duration: float,
    curve: str,
    bpm_window: int,
    tempo_match: bool,
    key_match: bool,
    agc: bool,
    current_gain: float,
    next_gain: float,
) -> str:
    payload = '|'.join(
        (
            current_id,
            next_id,
            'structural-transition-v5',
            str(sample_rate),
            str(channels),
            f'{crossfade_seconds:.6f}',
            f'{crossfade_strength:.6f}',
            f'{max_duration:.6f}',
            curve,
            str(bpm_window),
            str(tempo_match),
            str(key_match),
            str(agc),
            f'{current_gain:.6f}',
            f'{next_gain:.6f}',
        )
    ).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def getCrossfade(
    current: AudioSegment,
    next: AudioSegment,
    crossfade_seconds: float,
    crossfade_strength: float,
    *,
    current_song_id: str | None = None,
    next_song_id: str | None = None,
    max_duration: float = 24.0,
    curve: str = 'equal_power',
    bpm_window: int = 15,
    tempo_match: bool = True,
    key_match: bool = False,
    agc: bool = False,
    current_duration_seconds: float | None = None,
    current_gain: float = 1.0,
    next_gain: float = 1.0,
) -> CrossFadeInfo:
    strength = _clamp(crossfade_strength, 0.0, 1.0)
    sample_rate = current.frame_rate
    channels = _target_channels(current, next)

    cache_token: str | None = None
    use_cache = (
        current_song_id is not None
        and next_song_id is not None
        and current_duration_seconds is None
    )
    if use_cache:
        assert current_song_id is not None and next_song_id is not None
        cache_token = _cache_token(
            current_song_id,
            next_song_id,
            sample_rate,
            channels,
            crossfade_seconds,
            strength,
            max_duration,
            curve,
            bpm_window,
            tempo_match,
            key_match,
            agc,
            current_gain,
            next_gain,
        )
        cached = CrossFadeInfo.load_from_cache(cache_token, sample_rate, channels)
        if cached is not None and cached.fade_seconds > 0:
            _logger.debug('crossfade loaded from cache')
            return cached

    window_seconds = max(
        30,
        int(round(max_duration)),
        int(round(bpm_window)),
    )
    window_ms = min(
        window_seconds * 1000,
        len(current),
        len(next),
    )
    current_duration = (
        current_duration_seconds
        if current_duration_seconds is not None
        else len(current) / 1000.0
    )
    current_tail = current[-window_ms:]
    current_analysis = current[:window_ms]
    next_head = next[:window_ms]
    current_samples = _segment_to_samples(current_tail, sample_rate, channels)  # type: ignore
    current_analysis_samples = _segment_to_samples(
        current_analysis,  # type: ignore
        sample_rate,
        channels,  # type: ignore
    )
    next_samples = _segment_to_samples(next_head, sample_rate, channels)  # type: ignore

    _, tail_silence_frames = _silence_span(current_samples, sample_rate)
    lead_silence_frames, _ = _silence_span(next_samples, sample_rate)
    if lead_silence_frames >= len(next_samples):
        lead_silence_frames = 0
    tail_silence_frames = min(
        tail_silence_frames, max(0, len(current_samples) - sample_rate // 2)
    )
    content_frames = len(current_samples) - tail_silence_frames
    content_samples = current_samples[:content_frames]
    content_duration = current_duration - tail_silence_frames / sample_rate
    lead_seconds = lead_silence_frames / sample_rate

    ending_type = _classify_ending(current_samples, sample_rate)
    if key_match:
        current_key, current_key_confidence = _detect_key(
            content_samples, sample_rate, 'end'
        )
        next_key, next_key_confidence = _detect_key(next_samples, sample_rate, 'start')
    else:
        current_key, current_key_confidence = '', 0.0
        next_key, next_key_confidence = '', 0.0
    key_compat = _key_compatibility(current_key, next_key)
    if key_compat > 0.0:
        key_compat *= 0.5 + 0.5 * min(current_key_confidence, next_key_confidence)
    timbre_similarity = _timbre_similarity(current_samples, next_samples, sample_rate)
    _logger.debug(
        'crossfade ending=%s key=%s->%s compat=%.2f conf=%.2f/%.2f timbre=%.2f',
        ending_type.value,
        current_key,
        next_key,
        key_compat,
        current_key_confidence,
        next_key_confidence,
        timbre_similarity,
    )

    current_bpm = (
        _detect_bpm_with_cache(
            current_analysis_samples, sample_rate, current_song_id, bpm_window
        )
        if tempo_match
        else 0.0
    )
    next_bpm = (
        _detect_bpm_with_cache(next_samples, sample_rate, next_song_id, bpm_window)
        if tempo_match
        else 0.0
    )
    target_speed = (
        _tempo_transition_speed(current_bpm, next_bpm) if tempo_match else 1.0
    )
    _logger.debug(
        'crossfade bpm current=%.2f next=%.2f speed=%.3f',
        current_bpm,
        next_bpm,
        target_speed,
    )

    fade_frames = _fade_frames(
        content_samples,
        next_samples,
        sample_rate,
        crossfade_seconds,
        strength,
        max_duration,
        ending_type,
        current_bpm,
        next_bpm,
        lead_seconds,
    )
    transition_type = _select_transition_type(
        ending_type,
        key_compat,
        timbre_similarity,
        current_bpm,
        next_bpm,
    )
    beat_phase = _detect_beat_phase(current_samples, sample_rate, current_bpm)

    if fade_frames <= 0:
        return CrossFadeInfo(
            start_seconds=current_duration,
            fade_seconds=0.0,
            end_seconds=0.0,
            sample_rate=sample_rate,
            channels=channels,
            samples=np.zeros((0, channels), dtype=np.float32),
            target_speed=target_speed,
            ending_type=ending_type.value,
            current_key=current_key,
            next_key=next_key,
            key_compatibility=key_compat,
            transition_type=transition_type,
            timbre_similarity=timbre_similarity,
        )

    window_start = content_frames - fade_frames
    start_seconds = max(0.0, content_duration - fade_frames / sample_rate)
    outgoing = _apply_speed_transition(
        content_samples,
        target_speed,
        fade_frames,
        window_start,
        sample_rate,
    )
    incoming = next_samples[:fade_frames]
    fade_out, fade_in = _build_fades(
        curve,
        ending_type,
        transition_type,
        fade_frames,
        beat_phase,
    )
    mixed = outgoing * fade_out * _clamp(
        current_gain, 0.0, 4.0
    ) + incoming * fade_in * _clamp(next_gain, 0.0, 4.0)
    if agc:
        mixed = _apply_agc(mixed, sample_rate)
    mixed = _limit_samples(mixed)
    fade_seconds = fade_frames / sample_rate
    fade_out_profile = _fade_profile(fade_out)
    fade_in_profile = _fade_profile(fade_in)

    info = CrossFadeInfo(
        start_seconds=start_seconds,
        fade_seconds=fade_seconds,
        end_seconds=fade_seconds,
        sample_rate=sample_rate,
        channels=channels,
        samples=mixed,
        target_speed=target_speed,
        ending_type=ending_type.value,
        current_key=current_key,
        next_key=next_key,
        key_compatibility=key_compat,
        fade_out_profile=fade_out_profile,
        fade_in_profile=fade_in_profile,
        transition_type=transition_type,
        timbre_similarity=timbre_similarity,
        beat_phase=beat_phase,
    )

    if cache_token is not None and current_duration_seconds is None:
        info.save_to_cache(cache_token)

    return info


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
    content_samples: np.ndarray,
    next_samples: np.ndarray,
    sample_rate: int,
    crossfade_seconds: float,
    strength: float,
    max_duration: float = 24.0,
    ending_type: EndingType | None = None,
    current_bpm: float = 0.0,
    next_bpm: float = 0.0,
    lead_seconds: float = 0.0,
) -> int:
    requested_seconds = _adaptive_crossfade_seconds(
        content_samples,
        next_samples,
        sample_rate,
        crossfade_seconds,
        strength,
        max_duration,
        ending_type,
        current_bpm,
        next_bpm,
        lead_seconds,
    )
    requested_frames = int(round(requested_seconds * sample_rate))
    return min(requested_frames, len(content_samples), len(next_samples))


def _adaptive_crossfade_seconds(
    content_samples: np.ndarray,
    next_samples: np.ndarray,
    sample_rate: int,
    crossfade_seconds: float,
    strength: float,
    max_seconds: float = 24.0,
    ending_type: EndingType | None = None,
    current_bpm: float = 0.0,
    next_bpm: float = 0.0,
    lead_seconds: float = 0.0,
) -> float:
    available = min(
        max_seconds,
        len(content_samples) / sample_rate,
        len(next_samples) / sample_rate,
    )
    if available <= 0:
        return 0.0

    base_seconds = 2.0 + 5.0 * strength
    if ending_type is EndingType.ABRUPT:
        base_seconds = max(base_seconds, 3.0 + 5.0 * strength)
    elif ending_type is EndingType.SUSTAINED:
        base_seconds = max(base_seconds, 2.5 + 3.0 * strength)
    elif ending_type is EndingType.FADE_OUT:
        base_seconds = min(base_seconds, 2.0 + 3.0 * strength)
    elif ending_type is EndingType.LIVE:
        base_seconds = min(base_seconds, 1.5 + 2.0 * strength)
    if crossfade_seconds > 0:
        base_seconds = max(
            base_seconds,
            min(crossfade_seconds, 8.0) * (0.5 + 0.5 * strength),
        )

    bpm = current_bpm if current_bpm > 0 else next_bpm
    if bpm > 0:
        beat_seconds = 60.0 / bpm
        phrase_seconds = beat_seconds * 4.0
        phrases = max(1, min(4, round(base_seconds / phrase_seconds)))
        base_seconds = phrase_seconds * phrases

    if lead_seconds > 0:
        base_seconds = max(
            base_seconds,
            min(lead_seconds + 1.5, _MAX_LEAD_COVER_SECONDS),
        )

    return min(available, max(2.0, base_seconds))


def _power_tilt_fades(frames: int, tilt: float) -> tuple[np.ndarray, np.ndarray]:
    if frames <= 1:
        return (
            np.zeros((max(frames, 0), 1), dtype=np.float32),
            np.ones((max(frames, 0), 1), dtype=np.float32),
        )

    progress = np.linspace(0.0, 1.0, frames, dtype=np.float32)
    exponent = max(tilt, 1.0)
    fade_out = np.clip(np.cos(progress * pi / 2.0), 0.0, 1.0).astype(np.float32)
    fade_in = np.clip(np.sin(progress * pi / 2.0), 0.0, 1.0).astype(np.float32)
    fade_out = fade_out**exponent
    fade_in = fade_in ** (1.0 / exponent)
    power = np.maximum(np.sqrt(fade_out * fade_out + fade_in * fade_in), 1e-6)
    fade_out = (fade_out / power).reshape(-1, 1)
    fade_in = (fade_in / power).reshape(-1, 1)
    return fade_out.astype(np.float32), fade_in.astype(np.float32)


def _sigmoid_values(frames: int) -> np.ndarray:
    if frames <= 1:
        return np.ones(max(frames, 0), dtype=np.float32)
    x = np.linspace(-4.0, 4.0, frames, dtype=np.float32)
    values = 1.0 / (1.0 + np.exp(-x))
    return ((values - values[0]) / (values[-1] - values[0])).astype(np.float32)


def _fade_in_curve(curve: str, tilt: float, frames: int) -> np.ndarray:
    if curve == 'linear':
        return np.linspace(0.0, 1.0, frames, dtype=np.float32).reshape(-1, 1)
    if curve == 'sigmoid':
        return _sigmoid_values(frames).reshape(-1, 1)
    return _power_tilt_fades(frames, tilt)[1]


def _fade_out_curve(curve: str, tilt: float, frames: int) -> np.ndarray:
    if curve in ('linear', 'sigmoid'):
        return 1.0 - _fade_in_curve(curve, tilt, frames)
    return _power_tilt_fades(frames, tilt)[0]


def _select_fade_curve(curve: str, frames: int) -> tuple[np.ndarray, np.ndarray]:
    return _fade_out_curve(curve, 1.0, frames), _fade_in_curve(curve, 1.0, frames)


def _smart_tilt(transition_type: str, ending_type: EndingType) -> float:
    if transition_type == 'harmonic_blend':
        return 1.05
    if ending_type is EndingType.ABRUPT:
        return _ABRUPT_TILT
    if ending_type is EndingType.LIVE:
        return 1.3
    if ending_type is EndingType.FADE_OUT:
        return 1.15
    return _SMART_TILT


def _fade_profile(values: np.ndarray, points: int = 9) -> tuple[float, ...]:
    if len(values) == 0:
        return ()
    stride = max(1, len(values) // points)
    sampled = values[::stride, 0][:points]
    return tuple(float(value) for value in sampled)


def _timbre_similarity(
    current: np.ndarray, following: np.ndarray, sample_rate: int
) -> float:
    """Compare broad spectral shape without depending on absolute loudness."""
    analysis_frames = min(len(current), len(following), sample_rate * 8)
    if analysis_frames < sample_rate:
        return 0.0

    def _spectral_signature(samples: np.ndarray) -> np.ndarray:
        mono = np.mean(samples, axis=1)
        frame_size = min(4096, len(mono))
        starts = np.linspace(0, len(mono) - frame_size, 12).astype(np.intp)
        signature = np.zeros(12, dtype=np.float64)
        frequencies = np.fft.rfftfreq(frame_size, 1.0 / sample_rate)
        bands = np.geomspace(50.0, min(16000.0, sample_rate * 0.48), 13)
        window = np.hanning(frame_size)
        for start in starts:
            spectrum = np.abs(np.fft.rfft(mono[start : start + frame_size] * window))
            for index in range(12):
                mask = (frequencies >= bands[index]) & (frequencies < bands[index + 1])
                if np.any(mask):
                    signature[index] += float(np.mean(spectrum[mask] ** 2))
        signature = np.log1p(signature)
        signature -= float(np.mean(signature))
        norm = float(np.linalg.norm(signature))
        return signature / norm if norm > 1e-8 else signature

    current_signature = _spectral_signature(current[-analysis_frames:])
    next_signature = _spectral_signature(following[:analysis_frames])
    similarity = float(np.dot(current_signature, next_signature))
    return _clamp((similarity + 1.0) * 0.5, 0.0, 1.0)


def _detect_beat_phase(samples: np.ndarray, sample_rate: int, bpm: float) -> float:
    """Return the normalized distance from the tail to its next likely beat."""
    if bpm <= 0 or len(samples) < sample_rate * 2:
        return 0.0
    envelope_rate = 200
    mono = np.mean(samples, axis=1).astype(np.float64)
    envelope = _onset_envelope(mono, sample_rate, envelope_rate)
    period = int(round(envelope_rate * 60.0 / bpm))
    if period < 2 or len(envelope) < period * 2:
        return 0.0
    scores = np.array(
        [float(np.sum(envelope[offset::period])) for offset in range(period)]
    )
    strongest = int(np.argmax(scores))
    tail_phase = (len(envelope) - 1 - strongest) % period
    return float((period - tail_phase) % period) / period


def _select_transition_type(
    ending_type: EndingType,
    key_compatibility: float,
    timbre_similarity: float,
    current_bpm: float,
    next_bpm: float,
) -> str:
    tempo_ratio = _tempo_transition_speed(current_bpm, next_bpm)
    tempo_compatible = current_bpm > 0 and next_bpm > 0 and tempo_ratio != 1.0
    if key_compatibility >= 0.65 and timbre_similarity >= 0.55:
        return 'harmonic_blend'
    if tempo_compatible and ending_type in (EndingType.ABRUPT, EndingType.LIVE):
        return 'beat_cut'
    if timbre_similarity >= 0.72:
        return 'texture_bridge'
    return 'smart_crossfade'


_SIMPLE_CURVES = ('equal_power', 'sigmoid', 'linear')


def _build_fades(
    curve: str,
    ending_type: EndingType,
    transition_type: str,
    frames: int,
    beat_phase: float,
) -> tuple[np.ndarray, np.ndarray]:
    if curve in _SIMPLE_CURVES:
        return _select_fade_curve(curve, frames)
    if frames <= 1:
        return _select_fade_curve(curve, frames)
    if transition_type == 'beat_cut':
        progress = np.linspace(0.0, 1.0, frames, dtype=np.float32)
        center = 0.35 + beat_phase * 0.3
        fade_in = 1.0 / (1.0 + np.exp(-(progress - center) * 28.0))
        fade_in = fade_in.reshape(-1, 1).astype(np.float32, copy=False)
        return (1.0 - fade_in).astype(np.float32, copy=False), fade_in
    if transition_type == 'texture_bridge':
        progress = np.linspace(0.0, 1.0, frames, dtype=np.float32).reshape(-1, 1)
        return (
            np.sqrt(1.0 - progress).astype(np.float32, copy=False),
            (progress**0.75).astype(np.float32, copy=False),
        )
    return _power_tilt_fades(frames, _smart_tilt(transition_type, ending_type))


def _apply_agc(
    mixed: np.ndarray, sample_rate: int, threshold_db: float = 2.0
) -> np.ndarray:
    if len(mixed) < sample_rate // 5:
        return mixed
    block_size = sample_rate // 10
    usable = len(mixed) // block_size * block_size
    if usable <= 0:
        return mixed
    blocks = mixed[:usable].reshape(-1, block_size)
    block_rms = np.sqrt(np.mean(blocks * blocks, axis=1))
    rms_max = float(np.max(block_rms))
    rms_min = float(np.min(block_rms))
    if rms_max < 1e-6:
        return mixed
    dip_ratio = rms_min / rms_max
    threshold_linear = 10.0 ** (-threshold_db / 20.0)
    if dip_ratio >= threshold_linear:
        return mixed
    # The transition must join the two unmodified tracks at both endpoints.
    # A one-way gain ramp fixes the middle dip but leaves its boosted final
    # sample beside the next player's unboosted first sample.
    peak_gain = (1.0 / max(dip_ratio, 0.01)) ** 0.3
    progress = np.linspace(0.0, pi, len(mixed), dtype=np.float32)
    gain_curve = 1.0 + (peak_gain - 1.0) * np.sin(progress)
    gain_curve[0] = 1.0
    gain_curve[-1] = 1.0
    return (mixed * gain_curve.reshape(-1, 1)).astype(np.float32, copy=False)


def _limit_samples(samples: np.ndarray) -> np.ndarray:
    if len(samples) == 0:
        return samples.astype(np.float32, copy=False)

    peak = float(np.max(np.abs(samples)))
    if peak > 1.0:
        samples = samples / peak
    return samples.astype(np.float32, copy=False)


def _detect_bpm(
    samples: np.ndarray, sample_rate: int, analysis_seconds: int = 15
) -> float:
    analysis_seconds = max(4, int(round(analysis_seconds)))
    analysis_frames = min(len(samples), sample_rate * analysis_seconds)
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

    corr = _fft_autocorrelation(envelope)
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


def _fft_autocorrelation(signal: np.ndarray) -> np.ndarray:
    n = len(signal)
    fft_size = 1 << (2 * n - 1 - 1).bit_length()
    spectrum = np.fft.rfft(signal, n=fft_size)
    corr = np.fft.irfft(spectrum * spectrum.conj(), n=fft_size)
    return corr[:n]


_bpm_cache: dict[tuple[str, int, int], float] = {}


def _detect_bpm_with_cache(
    samples: np.ndarray,
    sample_rate: int,
    song_id: str | None,
    analysis_seconds: int = 15,
) -> float:
    analysis_seconds = max(4, int(round(analysis_seconds)))
    if song_id:
        key = (song_id, sample_rate, analysis_seconds)
        cached = _bpm_cache.get(key)
        if cached is not None:
            return cached
    try:
        bpm = _detect_bpm(samples, sample_rate, analysis_seconds)
    except Exception:
        _logger.exception('BPM detection failed')
        bpm = 0.0
    if song_id and bpm > 0:
        _bpm_cache[(song_id, sample_rate, analysis_seconds)] = bpm
    return bpm


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
    window_start: int,
    sample_rate: int,
) -> np.ndarray:
    if target_speed == 1.0 or frames <= 1:
        return samples[window_start : window_start + frames].copy()

    offsets = np.arange(frames, dtype=np.float64)
    positions = (
        window_start
        + offsets
        + (target_speed - 1.0) * offsets * offsets / (2.0 * frames)
    )
    positions = np.clip(positions, float(window_start), float(len(samples) - 1))

    stretcher = WsolaStretcher(
        lambda start, stop: samples[start:stop], lambda: len(samples)
    )
    return stretcher.warp(positions, sample_rate, samples.shape[1])
