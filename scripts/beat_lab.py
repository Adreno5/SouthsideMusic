import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _beat_old import BeatDetector as BeatDetectorOld
from core.beat import BeatDetector

SR = 44100
TOLERANCE = 0.05


def _decay(n: int, tau: float) -> np.ndarray:
    return np.exp(-np.arange(n) / (tau * SR))


def kick(gain: float = 1.0) -> np.ndarray:
    n = round(0.18 * SR)
    t = np.arange(n) / SR
    freq = 165.0 * np.exp(-t / 0.03) + 45.0
    body = np.sin(np.cumsum(2.0 * np.pi * freq / SR)) * _decay(n, 0.055)
    attack = np.zeros(n)
    attack[: round(0.004 * SR)] = np.hanning(round(0.004 * SR))
    return gain * (0.85 * body + 0.35 * attack).astype(np.float32)


def snare(gain: float = 1.0) -> np.ndarray:
    rng = np.random.default_rng(7)
    n = round(0.15 * SR)
    noise = rng.normal(0.0, 1.0, n).astype(np.float32)
    noise = noise - np.concatenate(([0.0], noise[:-1])) * 0.9
    tone = np.sin(2.0 * np.pi * 190.0 * np.arange(n) / SR)
    return gain * (0.7 * noise * _decay(n, 0.035) + 0.3 * tone * _decay(n, 0.02))


def hat(gain: float = 0.6) -> np.ndarray:
    rng = np.random.default_rng(11)
    n = round(0.05 * SR)
    noise = rng.normal(0.0, 1.0, n).astype(np.float32)
    noise = noise - np.concatenate(([0.0], noise[:-1]))
    return gain * noise * _decay(n, 0.008)


def pad(seconds: float, gain: float = 0.25, root: float = 110.0) -> np.ndarray:
    n = round(seconds * SR)
    t = np.arange(n) / SR
    ratios = np.array([1.0, 1.5, 2.0, 3.0])[:, None]
    tone = np.sin(2.0 * np.pi * root * ratios * t[None, :]).sum(axis=0)
    return gain * (tone / 4.0).astype(np.float32)


def melody(seconds: float, bpm: float, gain: float = 0.3) -> np.ndarray:
    n = round(seconds * SR)
    t = np.arange(n) / SR
    beat = 60.0 / bpm
    notes = [0, 3, 5, 7, 5, 3, 0, -2]
    output = np.zeros(n, dtype=np.float32)
    index = 0
    at = 0.0
    while at < seconds:
        length = beat / 2
        start = round(at * SR)
        stop = min(n, start + round(length * SR))
        if stop > start:
            span = t[start:stop] - at
            freq = 220.0 * 2 ** (notes[index % len(notes)] / 12.0)
            vibrato = 1.0 + 0.01 * np.sin(2.0 * np.pi * 5.5 * span)
            wave = np.sin(2.0 * np.pi * freq * span * vibrato)
            wave += 0.4 * np.sin(4.0 * np.pi * freq * span * vibrato)
            envelope = np.minimum(1.0, span / 0.01) * np.exp(-span / (length * 1.4))
            output[start:stop] += (wave * envelope * gain).astype(np.float32)
        at += length
        index += 1
    return output


def pattern(
    bpm: float,
    bars: int = 6,
    kicks: bool = True,
    snares: bool = True,
    hats: bool = True,
    subdivision: int = 2,
    bed: bool = False,
    tune: bool = False,
    gain: float = 1.0,
    preroll: float = 0.2,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    beat = 60.0 / bpm
    total = preroll + bars * 4 * beat + 0.5
    n = round(total * SR)
    mix = np.zeros(n, dtype=np.float32)
    if bed:
        mix += pad(total)
    if tune:
        mix += melody(total, bpm)
    truth: dict[str, list[float]] = {'kick': [], 'snare': [], 'hat': []}
    rng = np.random.default_rng(3)

    def add(sound: np.ndarray, at: float, key: str) -> None:
        start = round(at * SR)
        stop = min(n, start + len(sound))
        if stop <= start:
            return
        mix[start:stop] += sound[: stop - start]
        truth[key].append(at)

    for bar in range(bars):
        for step in range(4 * subdivision):
            at = preroll + bar * 4 * beat + step * beat / subdivision
            if hats and step % 2 == 0:
                add(hat(), at, 'hat')
            if step % subdivision:
                continue
            beat_index = step // subdivision
            if kicks and beat_index % 2 == 0:
                add(kick(), at, 'kick')
            if snares and beat_index % 2 == 1:
                add(snare(), at, 'snare')
    mix *= gain
    mix += rng.normal(0.0, 0.0015, n).astype(np.float32)
    peak = np.abs(mix).max()
    if peak > 1.0:
        mix /= peak
    return mix, {key: np.array(value) for key, value in truth.items()}


def _recall(detected: np.ndarray, truth: np.ndarray, tolerance: float) -> float:
    if len(truth) == 0:
        return 0.0
    distances = np.abs(detected[None, :] - truth[:, None])
    return np.mean(np.min(distances, axis=1) <= tolerance).item()


def score(
    frames,
    truth: dict[str, np.ndarray],
    kinds: tuple[str, ...],
    seconds: float,
    tolerance: float = TOLERANCE,
) -> dict:
    detected = np.array([frame.time for frame in frames if frame.is_point])
    target = (
        np.sort(np.concatenate([truth[kind] for kind in kinds]))
        if kinds
        else np.zeros(0)
    )
    row = {
        'detected': len(detected),
        'truth': len(target),
        'per_minute': round(len(detected) / seconds * 60.0, 1),
    }
    if len(target):
        distances = np.abs(detected[None, :] - target[:, None])
        nearest = np.min(distances, axis=1)
        offsets = detected[np.argmin(distances, axis=1)] - target
        recall = np.mean(nearest <= tolerance).item()
        precision = np.mean(
            [np.min(np.abs(target - at)) <= tolerance for at in detected]
        ).item()
        row['precision'] = round(precision, 3)
        row['recall'] = round(recall, 3)
        row['f1'] = (
            round(2 * precision * recall / (precision + recall), 3)
            if precision + recall
            else 0.0
        )
        matched = offsets[nearest <= tolerance]
        row['latency_ms'] = (
            round(np.median(matched).item() * 1000.0, 1) if len(matched) else None
        )
        if len(kinds) > 1:
            row['by_type'] = {
                kind: round(_recall(detected, truth[kind], tolerance), 3)
                for kind in kinds
            }
    return row


CASES: list[tuple[str, dict, tuple[str, ...]]] = [
    ('mix_90', {'bpm': 90}, ('kick', 'snare')),
    ('mix_120', {'bpm': 120}, ('kick', 'snare')),
    ('mix_128', {'bpm': 128}, ('kick', 'snare')),
    ('mix_150', {'bpm': 150}, ('kick', 'snare')),
    ('mix_16th', {'bpm': 120, 'subdivision': 4}, ('kick', 'snare')),
    ('mix_bed', {'bpm': 128, 'bed': True}, ('kick', 'snare')),
    ('mix_full', {'bpm': 120, 'bed': True, 'tune': True}, ('kick', 'snare')),
    ('mix_quiet', {'bpm': 120, 'gain': 0.12}, ('kick', 'snare')),
    ('mix_hot', {'bpm': 120, 'gain': 3.0}, ('kick', 'snare')),
    ('kick_only', {'bpm': 120, 'snares': False, 'hats': False}, ('kick',)),
    ('snare_only', {'bpm': 120, 'kicks': False, 'hats': False}, ('snare',)),
    ('hat_only', {'bpm': 120, 'kicks': False, 'snares': False}, ('hat',)),
    (
        'ballad_76',
        {'bpm': 76, 'subdivision': 4, 'bed': True, 'tune': True},
        ('kick', 'snare'),
    ),
    (
        'bed_tune',
        {
            'bpm': 120,
            'kicks': False,
            'snares': False,
            'hats': False,
            'bed': True,
            'tune': True,
        },
        (),
    ),
]


def evaluate(detector, kwargs: dict, kinds: tuple[str, ...]) -> dict:
    mix, truth = pattern(**kwargs)
    seconds = len(mix) / SR
    start = time.perf_counter()
    frames = detector.process(mix, SR, point_threshold=0.25)
    elapsed = time.perf_counter() - start
    row = score(frames, truth, kinds, seconds)
    row['ms_per_hop'] = round(elapsed / max(1, len(frames)) * 1000.0, 3)
    return row


def cell(row: dict) -> str:
    return (
        f'f1={row.get("f1")!s:<5} p={row.get("precision")!s:<5} r={row.get("recall")!s:<5} '
        f'n={row["detected"]:>3}/{row["truth"]:<3} lat={row.get("latency_ms")!s:<7} '
        f'{row["ms_per_hop"]:>6}ms'
    )


def decode(path: Path, seconds: float) -> tuple[np.ndarray, int]:
    from pydub import AudioSegment

    audio = AudioSegment.from_file(str(path), format='m4a')[: round(seconds * 1000)]
    samples = np.array(audio.get_array_of_samples(), dtype=np.float32) / 32768.0
    frames = samples.reshape(-1, audio.channels).mean(axis=1)
    return frames, audio.frame_rate


def run_stream(detector, samples: np.ndarray, sample_rate: int, chunk: int) -> list:
    frames = []
    for start in range(0, len(samples), chunk):
        frames.extend(detector.process(samples[start : start + chunk], sample_rate))
    return frames


def median_interval(points: np.ndarray) -> float:
    if len(points) < 2:
        return 0.0
    return float(np.median(np.diff(points)))


def short_intervals(points: np.ndarray) -> int:
    if len(points) < 2:
        return 0
    return int(np.sum(np.diff(points) < 0.30))


def mean_intensity(frames: list) -> float:
    if not frames:
        return 0.0
    return float(np.mean([frame.intensity for frame in frames]))


def track_check(count: int = 4, seconds: float = 45.0) -> None:
    music = Path(__file__).resolve().parent.parent / 'data' / 'music'
    print(
        f'{"track":<10} {"pts o/n":>9} {"med int o/n":>13} {"short int o/n":>13} '
        f'{"intensity o/n":>14} {"ms/hop o/n":>12} {"chunk gap":>10}'
    )
    done = 0
    for path in sorted(music.iterdir()):
        if done >= count:
            break
        try:
            samples, rate = decode(path, seconds)
        except Exception:
            continue
        done += 1
        old = BeatDetectorOld()
        start = time.perf_counter()
        old_frames = old.process(samples, rate, point_threshold=0.25)
        old_ms = (time.perf_counter() - start) / max(1, len(old_frames)) * 1000.0
        new = BeatDetector()
        start = time.perf_counter()
        new_frames = new.process(samples, rate, point_threshold=0.25)
        new_ms = (time.perf_counter() - start) / max(1, len(new_frames)) * 1000.0
        streamed = run_stream(BeatDetector(), samples, rate, 4096)
        old_points = np.array([f.time for f in old_frames if f.is_point])
        new_points = np.array([f.time for f in new_frames if f.is_point])
        stream_points = np.array([f.time for f in streamed if f.is_point])
        gap = (
            float(np.max(np.abs(new_points - stream_points)))
            if len(new_points) == len(stream_points)
            else -1.0
        )
        print(
            f'{path.name[:8]:<10} {len(old_points):>4}/{len(new_points):<4} '
            f'{median_interval(old_points):>6.3f}/{median_interval(new_points):<6.3f} '
            f'{short_intervals(old_points):>6}/{short_intervals(new_points):<6} '
            f'{mean_intensity(old_frames):>6.3f}/{mean_intensity(new_frames):<6.3f} '
            f'{old_ms:>5.2f}/{new_ms:<5.2f} {gap * 1000:>9.1f}ms'
        )


def main() -> None:
    if '--tracks' in sys.argv:
        track_check()
        return
    print(f'{"case":<12} {"OLD":<63} NEW')
    for name, kwargs, kinds in CASES:
        old = evaluate(BeatDetectorOld(), kwargs, kinds)
        new = evaluate(BeatDetector(), kwargs, kinds)
        flag = (
            '' if (old.get('f1') or 0) <= (new.get('f1') or 0) else '  <-- regression'
        )
        print(f'{name:<12} {cell(old):<63} {cell(new)}{flag}')


if __name__ == '__main__':
    main()
