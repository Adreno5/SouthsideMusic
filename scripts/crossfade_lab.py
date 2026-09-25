from __future__ import annotations

import os
import random
import sys

import numpy as np
from matplotlib import pyplot as plt
from pydub import AudioSegment

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src')
)

from core.crossfade import CrossFadeInfo, getCrossfade

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
MUSIC_DIR = os.path.join(ROOT, 'data', 'music')
OUT_DIR = os.path.join(ROOT, 'data', 'crossfade_plots')

BG = '#0d1117'
GRID = '#21262d'
TEXT = '#e6edf3'
MUTED = '#8b949e'
ACCENT = '#4fd1c5'
WARN = '#f0883e'


def songPaths(seed: int = 0) -> list[str]:
    try:
        names = sorted(os.listdir(MUSIC_DIR))
    except OSError as error:
        raise SystemExit(f'cannot list {MUSIC_DIR}: {error}') from error
    files = [
        os.path.join(MUSIC_DIR, name)
        for name in names
        if os.path.isfile(os.path.join(MUSIC_DIR, name))
    ]
    if len(files) < 2:
        raise SystemExit(f'need at least two songs in {MUSIC_DIR}')
    random.Random(seed).shuffle(files)
    return files


def songName(path: str) -> str:
    return os.path.basename(path)[:10]


def loadSong(path: str) -> AudioSegment:
    try:
        return AudioSegment.from_file(path)
    except Exception as error:
        raise SystemExit(f'cannot decode {path}: {error}') from error


def segmentWindow(
    segment: AudioSegment, seconds: float, tail: bool = False
) -> AudioSegment:
    span = int(round(seconds * 1000.0))
    if len(segment) <= span:
        return segment
    return segment[-span:] if tail else segment[:span]


def crossfade(
    current: AudioSegment, following: AudioSegment, seconds: float, strength: float
) -> CrossFadeInfo:
    try:
        return getCrossfade(current, following, seconds, strength, key_match=True)
    except Exception as error:
        raise SystemExit(f'crossfade analysis failed: {error}') from error


def samplesOf(segment: AudioSegment, sample_rate: int, channels: int) -> np.ndarray:
    prepared = segment.set_frame_rate(sample_rate).set_channels(channels)
    raw = np.asarray(prepared.get_array_of_samples(), dtype=np.float32)
    if raw.size == 0:
        return np.zeros((0, channels), dtype=np.float32)
    peak = float(np.iinfo(prepared.array_type).max)
    data = (raw / peak).astype(np.float32)
    if channels <= 1:
        return data.reshape(-1, 1)
    return data[: len(data) // channels * channels].reshape(-1, channels)


def mono(samples: np.ndarray) -> np.ndarray:
    if samples.ndim == 2:
        return np.mean(samples, axis=1)
    return samples


def envelopeDb(
    samples: np.ndarray, sample_rate: int, window_ms: int = 20
) -> np.ndarray:
    step = max(1, int(round(sample_rate * window_ms / 1000.0)))
    usable = len(samples) // step * step
    if usable <= 0:
        return np.zeros(0, dtype=np.float32)
    block = samples[:usable]
    if block.ndim == 1:
        block = block.reshape(-1, 1)
    frames = block.reshape(-1, step, block.shape[1])
    rms = np.sqrt(np.mean(frames * frames, axis=(1, 2)))
    return (20.0 * np.log10(np.maximum(rms, 1e-6))).astype(np.float32)


def dipDb(info: CrossFadeInfo) -> float:
    envelope = envelopeDb(info.samples, info.sample_rate, 10)
    margin = max(1, len(envelope) // 5)
    if len(envelope) < margin * 3:
        return 0.0
    edge = float(max(np.max(envelope[:2]), np.max(envelope[-2:])))
    return float(np.min(envelope[margin:-margin])) - edge


def plotEnvelope(
    ax,
    samples: np.ndarray,
    sample_rate: int,
    color: str = ACCENT,
    window_ms: int = 10,
    offset: float = 0.0,
) -> np.ndarray:
    envelope = envelopeDb(samples, sample_rate, window_ms)
    times = offset + np.arange(len(envelope)) * window_ms / 1000.0
    ax.plot(times, envelope, color=color, linewidth=1.0)
    ax.set_ylim(-60.0, 2.0)
    ax.grid(True, alpha=0.2)
    return envelope


def style() -> None:
    plt.style.use('dark_background')
    plt.rcParams.update(
        {
            'figure.facecolor': BG,
            'axes.facecolor': BG,
            'axes.edgecolor': GRID,
            'axes.labelcolor': MUTED,
            'axes.titlecolor': TEXT,
            'grid.color': GRID,
            'xtick.color': MUTED,
            'ytick.color': MUTED,
            'text.color': MUTED,
            'figure.dpi': 130,
        }
    )


def legend(ax) -> None:
    ax.legend(
        facecolor=BG, edgecolor=GRID, labelcolor=MUTED, fontsize=8, framealpha=0.9
    )


def save(fig, name: str) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    fig.savefig(path, facecolor=BG, bbox_inches='tight')
    plt.close(fig)
    print(f'saved {path}')
    return path


if __name__ == '__main__':
    tone = np.sin(2.0 * np.pi * 440.0 * np.arange(48000) / 48000.0).astype(np.float32)
    loud = float(envelopeDb(tone.reshape(-1, 1), 48000, 10)[1])
    quiet = float(envelopeDb((tone * 0.1).reshape(-1, 1), 48000, 10)[1])
    assert abs((loud - quiet) - 20.0) < 0.5, f'level mismatch {loud} {quiet}'
    assert envelopeDb(np.zeros((0, 1), dtype=np.float32), 48000).size == 0
    print('crossfade_lab self-check ok')
