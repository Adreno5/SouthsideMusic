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
from scipy.interpolate import CubicSpline
import logging

_logger = logging.getLogger(__name__)

BPM_MIN = 50.0
BPM_MAX = 210.0

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


def _classify_ending(samples: np.ndarray, sample_rate: int) -> EndingType:
    tail_sec = min(10.0, len(samples) / sample_rate)
    tail_frames = int(tail_sec * sample_rate)
    if tail_frames < sample_rate:
        return EndingType.FADE_OUT
    tail = samples[-tail_frames:]

    block_size = max(1, sample_rate // 10)
    usable = len(tail) // block_size * block_size
    if usable <= 0:
        return EndingType.FADE_OUT
    blocks = tail[:usable].reshape(-1, block_size)
    block_rms = np.sqrt(np.mean(blocks * blocks, axis=1))

    first_quarter = block_rms[: len(block_rms) // 4]
    last_quarter = block_rms[-(len(block_rms) // 4) :]
    if len(first_quarter) == 0 or len(last_quarter) == 0:
        return EndingType.FADE_OUT

    first_mean = float(np.mean(first_quarter))
    last_mean = float(np.mean(last_quarter))
    decay_ratio = last_mean / max(first_mean, 1e-6)

    high_band = tail[:, 0] if tail.ndim == 2 else tail
    fft_size = min(len(high_band), 4096)
    spectrum = np.abs(np.fft.rfft(high_band[:fft_size]))
    nyquist_bin = len(spectrum)
    high_start = nyquist_bin * 6 // 10
    low_energy = float(np.sum(spectrum[:high_start] ** 2))
    high_energy = float(np.sum(spectrum[high_start:] ** 2))
    hf_ratio = high_energy / max(low_energy + high_energy, 1e-6)

    if decay_ratio > 0.7:
        if hf_ratio > 0.35:
            return EndingType.LIVE
        return EndingType.SUSTAINED
    if decay_ratio < 0.15:
        return EndingType.ABRUPT
    return EndingType.FADE_OUT


def _detect_key(samples: np.ndarray, sample_rate: int) -> str:
    analysis_frames = min(len(samples), sample_rate * 30)
    if analysis_frames < sample_rate * 3:
        return ''

    mono = np.mean(samples[:analysis_frames], axis=1).astype(np.float64)
    mono -= float(np.mean(mono))
    peak = float(np.max(np.abs(mono)))
    if peak < 1e-5:
        return ''
    mono /= peak

    chromagram = _compute_chromagram(mono, sample_rate)
    if chromagram is None or len(chromagram) == 0:
        return ''

    avg_chroma = np.mean(chromagram, axis=0)
    if float(np.max(avg_chroma)) < 1e-6:
        return ''

    best_corr = -2.0
    best_note = 0
    best_is_minor = False

    for shift in range(12):
        rolled = np.roll(avg_chroma, -shift)
        corr_major = float(np.corrcoef(rolled, _KS_PROFILES_MAJOR)[0, 1])
        corr_minor = float(np.corrcoef(rolled, _KS_PROFILES_MINOR)[0, 1])
        if corr_major > best_corr:
            best_corr = corr_major
            best_note = shift
            best_is_minor = False
        if corr_minor > best_corr:
            best_corr = corr_minor
            best_note = shift
            best_is_minor = True

    note = _NOTE_NAMES[best_note]
    if best_is_minor:
        return f'{note}m'
    return note


def _compute_chromagram(
    mono: np.ndarray, sample_rate: int, hop_length: int = 4096
) -> np.ndarray | None:
    n_fft = hop_length * 2
    n_frames = max(1, (len(mono) - n_fft) // hop_length + 1)
    if n_frames < 3:
        return None

    chromagram = np.zeros((n_frames, 12), dtype=np.float64)
    window = np.hanning(n_fft)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
    valid = (freqs >= 27.5) & (freqs <= 4200.0)
    pitches = 12.0 * np.log2(freqs[valid] / 440.0) + 69.0
    chroma_bins = np.rint(pitches).astype(np.intp) % 12

    for i in range(n_frames):
        start = i * hop_length
        frame = mono[start : start + n_fft] * window
        spectrum = np.abs(np.fft.rfft(frame)) ** 2
        chromagram[i] = np.bincount(
            chroma_bins,
            weights=spectrum[valid],
            minlength=12,
        )

    return chromagram


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
            'structural-transition-v3',
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

    ending_type = _classify_ending(current_samples, sample_rate)
    current_key = (
        _detect_key(current_analysis_samples, sample_rate) if key_match else ''
    )
    next_key = _detect_key(next_samples, sample_rate) if key_match else ''
    key_compat = _key_compatibility(current_key, next_key)
    timbre_similarity = _timbre_similarity(current_samples, next_samples, sample_rate)
    _logger.debug(
        'crossfade ending=%s key=%s->%s compat=%.2f timbre=%.2f',
        ending_type.value,
        current_key,
        next_key,
        key_compat,
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
        current_samples,
        next_samples,
        sample_rate,
        crossfade_seconds,
        strength,
        max_duration,
        ending_type,
        current_bpm,
        next_bpm,
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

    start_frame = len(current_samples) - fade_frames
    start_seconds = max(0.0, current_duration - fade_frames / sample_rate)
    current_tail = _apply_speed_transition(
        current_samples[start_frame:],
        target_speed,
        fade_frames,
    )
    next_head = next_samples[:fade_frames]
    fade_out, fade_in = _select_fade_curve(curve, fade_frames)
    fade_out_profile, fade_in_profile = _make_fade_profiles(
        current_tail,
        next_head,
        sample_rate,
        curve,
    )
    if curve == 'smart':
        fade_out, fade_in = _transition_fades(transition_type, fade_frames, beat_phase)
    mixed = current_tail * fade_out * _clamp(
        current_gain, 0.0, 4.0
    ) + next_head * fade_in * _clamp(next_gain, 0.0, 4.0)
    if agc:
        mixed = _apply_agc(mixed, sample_rate)
    mixed = _limit_samples(mixed)
    fade_seconds = fade_frames / sample_rate

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
    current_samples: np.ndarray,
    next_samples: np.ndarray,
    sample_rate: int,
    crossfade_seconds: float,
    strength: float,
    max_duration: float = 24.0,
    ending_type: EndingType | None = None,
    current_bpm: float = 0.0,
    next_bpm: float = 0.0,
) -> int:
    requested_seconds = _adaptive_crossfade_seconds(
        current_samples,
        next_samples,
        sample_rate,
        crossfade_seconds,
        strength,
        max_duration,
        ending_type,
        current_bpm,
        next_bpm,
    )
    requested_frames = int(round(requested_seconds * sample_rate))
    return min(requested_frames, len(current_samples), len(next_samples))


def _adaptive_crossfade_seconds(
    current_samples: np.ndarray,
    next_samples: np.ndarray,
    sample_rate: int,
    crossfade_seconds: float,
    strength: float,
    max_seconds: float = 24.0,
    ending_type: EndingType | None = None,
    current_bpm: float = 0.0,
    next_bpm: float = 0.0,
) -> float:
    max_seconds = min(
        max_seconds,
        len(current_samples) / sample_rate,
        len(next_samples) / sample_rate,
    )
    if max_seconds <= 0:
        return 0.0
    tail_seconds = _active_tail_seconds(current_samples, sample_rate, max_seconds)
    intro_seconds = _active_intro_seconds(next_samples, sample_rate, max_seconds)
    base_seconds = max(2.0, min(8.0, (tail_seconds + intro_seconds) * 0.5))
    if ending_type == EndingType.ABRUPT:
        base_seconds = max(base_seconds, 8.0)
    elif ending_type == EndingType.FADE_OUT:
        base_seconds = min(base_seconds, 4.0)
    elif ending_type == EndingType.LIVE:
        base_seconds = min(base_seconds, 2.0)
    elif ending_type == EndingType.SUSTAINED:
        base_seconds = max(base_seconds, 6.0)
    if crossfade_seconds > 0:
        base_seconds = max(base_seconds * 0.75, crossfade_seconds * strength)
    else:
        base_seconds *= 0.5 + strength * 0.5

    bpm = current_bpm if current_bpm > 0 else next_bpm
    if bpm > 0:
        beat_seconds = 60.0 / bpm
        phrase_seconds = beat_seconds * 4.0
        phrases = max(1, min(4, round(base_seconds / phrase_seconds)))
        base_seconds = phrase_seconds * phrases
    return min(max_seconds, base_seconds)


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
    positive = energy[energy > 0]
    if len(positive) == 0:
        return min(max_seconds, 3.0)
    threshold = float(np.percentile(positive, 15))
    active = np.flatnonzero(energy >= threshold)
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
    positive = energy[energy > 0]
    if len(positive) == 0:
        return min(max_seconds, 3.0)
    threshold = float(np.percentile(positive, 15))
    active = np.flatnonzero(energy >= threshold)
    if len(active) == 0:
        return min(max_seconds, 3.0)
    return min(max_seconds, (int(active[-1]) + 1) * window / sample_rate)


def _window_energy(samples: np.ndarray, window: int) -> np.ndarray:
    mono = np.mean(samples, axis=1)
    usable = len(mono) // window * window
    if usable <= 0:
        return np.array([], dtype=np.float32)
    frames = mono[:usable].reshape(-1, window)
    return np.sqrt(np.mean(frames * frames, axis=1)).astype(np.float32)


def _equal_power_fades(frames: int) -> tuple[np.ndarray, np.ndarray]:
    if frames <= 1:
        fade_out = np.zeros((frames, 1), dtype=np.float32)
        fade_in = np.ones((frames, 1), dtype=np.float32)
        return fade_out, fade_in

    progress = np.linspace(0.0, 1.0, frames, dtype=np.float32).reshape(-1, 1)
    fade_out = np.cos(progress * pi / 2).astype(np.float32, copy=False)
    fade_in = np.sin(progress * pi / 2).astype(np.float32, copy=False)
    return fade_out, fade_in


def _sigmoid_fades(
    frames: int, steepness: float = 4.0
) -> tuple[np.ndarray, np.ndarray]:
    if frames <= 1:
        fade_out = np.zeros((frames, 1), dtype=np.float32)
        fade_in = np.ones((frames, 1), dtype=np.float32)
        return fade_out, fade_in

    x = np.linspace(-steepness, steepness, frames, dtype=np.float32)
    fade_in = 1.0 / (1.0 + np.exp(-x))
    fade_in = (fade_in - fade_in[0]) / (fade_in[-1] - fade_in[0])
    fade_in = fade_in.reshape(-1, 1)
    fade_out = 1.0 - fade_in
    return fade_out.astype(np.float32, copy=False), fade_in.astype(
        np.float32, copy=False
    )


def _select_fade_curve(curve: str, frames: int) -> tuple[np.ndarray, np.ndarray]:
    if curve == 'sigmoid':
        return _sigmoid_fades(frames)
    if curve == 'linear':
        progress = np.linspace(0.0, 1.0, frames, dtype=np.float32).reshape(-1, 1)
        return 1.0 - progress, progress
    return _equal_power_fades(frames)


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


def _transition_fades(
    transition_type: str, frames: int, beat_phase: float
) -> tuple[np.ndarray, np.ndarray]:
    if transition_type == 'beat_cut' and frames > 1:
        progress = np.linspace(0.0, 1.0, frames, dtype=np.float32)
        center = 0.35 + beat_phase * 0.3
        fade_in = 1.0 / (1.0 + np.exp(-(progress - center) * 28.0))
        fade_in = fade_in.reshape(-1, 1).astype(np.float32, copy=False)
        return 1.0 - fade_in, fade_in
    if transition_type == 'texture_bridge' and frames > 1:
        progress = np.linspace(0.0, 1.0, frames, dtype=np.float32).reshape(-1, 1)
        return np.sqrt(1.0 - progress), progress**0.75
    return _equal_power_fades(frames)


def _make_fade_profiles(
    current_tail: np.ndarray,
    next_head: np.ndarray,
    sample_rate: int,
    curve: str,
    points: int = 9,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Create slowly varying gains that compensate for real song energy."""
    if curve != 'smart' or len(current_tail) < 2 or len(next_head) < 2:
        fade_out, fade_in = _select_fade_curve(
            curve, min(len(current_tail), len(next_head))
        )
        return tuple(
            float(v) for v in fade_out[:: max(1, len(fade_out) // 9), 0]
        ), tuple(float(v) for v in fade_in[:: max(1, len(fade_in) // 9), 0])

    frames = min(len(current_tail), len(next_head))
    fade_out, fade_in = _equal_power_fades(frames)
    block = max(256, int(sample_rate * 0.08))
    count = max(2, min(points, int(np.ceil(frames / block)) + 1))
    positions = np.linspace(0, frames - 1, count).astype(np.intp)
    rms_current = np.sqrt(np.mean(current_tail[:frames] ** 2, axis=1))
    rms_next = np.sqrt(np.mean(next_head[:frames] ** 2, axis=1))
    source_x = np.arange(frames, dtype=np.float32)
    control_x = positions.astype(np.float32)
    current = np.interp(control_x, source_x, rms_current)
    following = np.interp(control_x, source_x, rms_next)
    floor = max(float(np.percentile(np.concatenate((current, following)), 30)), 1e-4)
    current /= floor
    following /= floor
    out = fade_out[positions, 0].astype(np.float64)
    incoming = fade_in[positions, 0].astype(np.float64)
    combined = np.sqrt((out * current) ** 2 + (incoming * following) ** 2)
    target = np.maximum(combined[0], combined[-1])
    compensation = np.sqrt(target / np.maximum(combined, 1e-4))
    compensation = np.clip(compensation, 0.72, 1.18)
    out *= compensation
    incoming *= compensation
    out = np.clip(out, 0.0, 1.0)
    incoming = np.clip(incoming, 0.0, 1.0)
    out[0], incoming[0] = 1.0, 0.0
    out[-1], incoming[-1] = 0.0, 1.0
    return tuple(float(v) for v in out), tuple(float(v) for v in incoming)


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
) -> np.ndarray:
    if target_speed == 1.0 or frames <= 1:
        return samples.copy().astype(np.float32, copy=False)

    src = np.arange(frames, dtype=np.float64)
    mapped = src + (target_speed - 1.0) * src * src / (2.0 * frames)
    mapped = np.clip(mapped, 0.0, float(frames - 1))

    result = np.zeros((frames, samples.shape[1]), dtype=np.float32)
    for ch in range(samples.shape[1]):
        orig = samples[:frames, ch].astype(np.float64)
        cs = CubicSpline(src, orig)
        result[:, ch] = cs(mapped).astype(np.float32)
    return result
