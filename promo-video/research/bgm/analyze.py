"""librosa BGM analysis for the SouthsideMusic promo (video-shotcraft methodology).

Pipeline (references/music-beat-sync.md):
  - HPSS percussive stem, librosa beat_track at tightness=400 for the beat anchors
  - grid = least squares over the beat sequence, fitted in 64-beat windows because
    library tracks can drift a few tenths of a percent (cat-walk shifts ~1.5 BPM)
  - transient triage: adaptive median/MAD thresholds per band
      kick 40-160 Hz | snare 150-500 Hz body AND 1-3 kHz crack | hihat 6-14 kHz
  - structure: per-beat RMS in dB -> recursive mean-split sections
  - acceptance: transient coverage / precision / mean abs error / drift
  - frames only in 'timeline' (@60fps), BGM starts on film frame 0

Usage (system python has librosa 1.0.0; else uv run --with librosa --with scipy):
  python analyze.py analyze
  python analyze.py bars --track house-vibez --from 40 --to 70
  python analyze.py timeline --track house-vibez --clip-start-sec 40.55 --length-sec 23.2 \
      --sections brand:0,8 hero:8,24 climb:24,40 peak:40,48 --out analysis.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import librosa
import numpy as np
from scipy.ndimage import maximum_filter, median_filter
from scipy.signal import butter, sosfilt

FPS = 60
HOP = 256
N_FFT = 1024
FIT_TOL_SEC = 0.030
WIN_BEATS = 64
WIN_STEP = 32

KICK_BAND = (40.0, 160.0)
SNARE_BODY = (150.0, 500.0)
SNARE_CRACK = (1000.0, 3000.0)
HIHAT_BAND = (6000.0, 14000.0)

DEFAULT_BGM_DIR = r'C:/Users/Adreno/.agents/skills/video-shotcraft/assets/audio/bgm'


def clean(obj):
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [clean(v) for v in obj.tolist()]
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def load_audio(path: Path) -> tuple[np.ndarray, int]:
    y, sr = librosa.load(str(path), sr=None, mono=True)
    return np.asarray(y, dtype=np.float32), int(sr)


def percussive(y: np.ndarray, sr: int) -> np.ndarray:
    chunk = int(30 * sr)
    ov = int(2 * sr)
    n = len(y)
    if n <= chunk + 2 * ov:
        return librosa.effects.hpss(y, n_fft=N_FFT)[1]
    out = np.zeros_like(y)
    pos = 0
    while pos < n:
        start = max(0, pos - ov // 2)
        end = min(n, pos + chunk + ov // 2)
        seg = y[start:end]
        if len(seg) < 4 * N_FFT:
            out[pos:] = y[pos:]
            break
        seg_perc = librosa.effects.hpss(seg, n_fft=N_FFT)[1]
        keep_end = min(n, pos + chunk)
        out[pos:keep_end] = seg_perc[pos - start : keep_end - start]
        pos = keep_end
    return out


def band_env(y: np.ndarray, sr: int, lo: float, hi: float) -> np.ndarray:
    hi = min(hi, sr / 2 * 0.98)
    sos = butter(4, [lo, hi], btype='band', fs=sr, output='sos')
    env = librosa.onset.onset_strength(
        y=sosfilt(sos, y), sr=sr, hop_length=HOP, n_fft=N_FFT
    )
    p = float(np.percentile(env, 99.5))
    if p <= 0:
        p = float(env.max()) or 1.0
    return np.clip(env / p, 0.0, 1.5)


def adaptive_peaks(
    env: np.ndarray, sr: int, delta: float, min_gap_s: float, base_win_s: float = 0.5
) -> list[tuple[float, float]]:
    fps = sr / HOP
    win = int(base_win_s * fps) | 1
    base = median_filter(env, size=win, mode='nearest')
    mad = median_filter(np.abs(env - base), size=win, mode='nearest') + 1e-6
    thr = base + delta * mad + 0.01
    peak = maximum_filter(env, size=3, mode='nearest')
    cand = np.flatnonzero((env >= peak) & (env > thr))
    gap = int(min_gap_s * fps)
    out: list[int] = []
    for i in cand:
        if not out or i - out[-1] >= gap:
            out.append(int(i))
        elif env[i] > env[out[-1]]:
            out[-1] = int(i)
    return [(float(i) * HOP / sr, float(env[i])) for i in out]


def detect_drums(y_perc: np.ndarray, sr: int) -> dict:
    kick = band_env(y_perc, sr, *KICK_BAND)
    body = band_env(y_perc, sr, *SNARE_BODY)
    crack = band_env(y_perc, sr, *SNARE_CRACK)
    snare = np.sqrt(np.clip(body, 0, None) * np.clip(crack, 0, None))
    snare = np.clip(snare / max(float(np.percentile(snare, 99.5)), 1e-9), 0, 1.5)
    hat = band_env(y_perc, sr, *HIHAT_BAND)
    return {
        'kick': adaptive_peaks(kick, sr, 5.0, 0.18),
        'snare': adaptive_peaks(snare, sr, 5.0, 0.12),
        'hihat': adaptive_peaks(hat, sr, 5.0, 0.05),
    }


def fit_grid(beat_times: np.ndarray) -> dict:
    n = np.arange(len(beat_times), dtype=float)
    A = np.vstack([n, np.ones_like(n)]).T
    (T, t0), *_ = np.linalg.lstsq(A, beat_times, rcond=None)
    res = beat_times - (t0 + n * T)
    slope = float(np.linalg.lstsq(A, res, rcond=None)[0][0]) if len(res) > 2 else 0.0
    return {
        'T': float(T),
        't0': float(t0),
        'bpm': float(60.0 / T),
        'residual_ms_mean': float(np.abs(res).mean() * 1000),
        'residual_ms_max': float(np.abs(res).max() * 1000),
        'drift_ms_total': float(slope * (len(beat_times) - 1) * 1000),
        'beat_count': int(len(beat_times)),
    }


def grid_from(phase: float, T: float, t_from: float, t_to: float) -> np.ndarray:
    phase = phase % T
    n0 = math.ceil((t_from - phase) / T)
    n1 = math.floor((t_to - phase) / T)
    return phase + np.arange(n0, n1 + 1, dtype=float) * T


def score_grid(
    grid: np.ndarray, backbone: np.ndarray, tol: float = FIT_TOL_SEC
) -> dict:
    if grid.size < 4 or backbone.size == 0:
        return {'coverage': 0.0, 'precision': 0.0, 'f1': 0.0, 'mean_abs_ms': 999.0}
    d_t = np.abs(backbone[:, None] - grid[None, :]).min(axis=1)
    coverage = float((d_t <= tol).mean())
    d_g = np.abs(grid[:, None] - backbone[None, :]).min(axis=1)
    precision = float((d_g <= tol).mean())
    matched = d_t[d_t <= tol]
    f1 = (
        0.0
        if coverage + precision == 0
        else 2 * coverage * precision / (coverage + precision)
    )
    return {
        'coverage': coverage,
        'precision': precision,
        'f1': f1,
        'mean_abs_ms': float(matched.mean() * 1000) if matched.size else 999.0,
    }


def window_grids(beat_times: np.ndarray, backbone: np.ndarray) -> list[dict]:
    rows = []
    starts = list(range(0, max(1, len(beat_times) - WIN_BEATS + 1), WIN_STEP))
    if not starts:
        starts = [0]
    for s in starts:
        seg = beat_times[s : s + WIN_BEATS]
        if len(seg) < 16:
            continue
        f = fit_grid(seg)
        grid = grid_from(f['t0'], f['T'], seg[0] - f['T'], seg[-1] + f['T'])
        sc = score_grid(
            grid, backbone[(backbone >= seg[0] - 1e-6) & (backbone <= seg[-1] + 1e-6)]
        )
        rows.append({
            't_start': round(float(seg[0]), 3),
            'bpm': round(f['bpm'], 3),
            'residual_ms_mean': round(f['residual_ms_mean'], 2),
            'residual_ms_max': round(f['residual_ms_max'], 2),
            'coverage': round(sc['coverage'], 3),
            'precision': round(sc['precision'], 3),
            'f1': round(sc['f1'], 3),
            'mean_abs_ms': round(sc['mean_abs_ms'], 2),
        })
    return rows


def band_share(y: np.ndarray, sr: int, bands: list[tuple[float, float]]) -> list[float]:
    spec = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    total = float(spec.sum()) or 1.0
    return [float(spec[(freqs >= lo) & (freqs < hi)].sum() / total) for lo, hi in bands]


def segment_energy(
    beat_db: np.ndarray, min_len: int = 12, gain_db: float = 1.6
) -> list[dict]:
    if len(beat_db) < 2 * min_len:
        return []
    segs = [(0, int(len(beat_db)))]
    changed = True
    while changed:
        changed = False
        best = None
        for si, (a, b) in enumerate(segs):
            if b - a < 2 * min_len:
                continue
            arr = beat_db[a:b]
            csum = np.concatenate([[0.0], np.cumsum(arr)])
            n = len(arr)
            for i in range(min_len, n - min_len + 1):
                gain = abs(csum[i] / i - (csum[-1] - csum[i]) / (n - i))
                if best is None or gain > best[0]:
                    best = (gain, si, a + i)
        if best is not None and best[0] >= gain_db:
            _, si, cut = best
            a, b = segs[si]
            segs[si : si + 1] = [(a, cut), (cut, b)]
            changed = True
    med = float(np.median(beat_db))
    out = []
    for idx, (a, b) in enumerate(segs):
        mean_db = float(beat_db[a:b].mean())
        level = 2 if mean_db >= med + 1.2 else (0 if mean_db <= med - 1.2 else 1)
        name = {0: 'low', 1: 'groove', 2: 'peak'}[level]
        if idx == 0 and (b - a) <= 24 and level <= 1:
            name = 'intro'
        if idx == len(segs) - 1 and level == 0:
            name = 'outro'
        out.append({
            'level': level,
            'name': name,
            'start_beat': int(a),
            'end_beat': int(b),
            'beats': int(b - a),
            'mean_db': round(mean_db, 2),
            'rel_db': round(mean_db - med, 2),
        })
    return out


def analyze_track(path: Path, name: str) -> dict:
    y, sr = load_audio(path)
    duration = float(len(y) / sr)
    y_perc = percussive(y, sr)

    tempo_scalar, beat_times = librosa.beat.beat_track(
        y=y_perc, sr=sr, hop_length=HOP, tightness=400, trim=False, units='time'
    )
    beats = np.asarray(beat_times, dtype=float)

    drums = detect_drums(y_perc, sr)
    kick_t = np.array([t for t, _ in drums['kick']])
    snare_t = np.array([t for t, _ in drums['snare']])
    backbone = np.array(
        sorted({round(x, 4) for x in np.concatenate([kick_t, snare_t])})
    )

    wins = window_grids(beats, backbone)
    bpms = [w['bpm'] for w in wins] or [float(np.atleast_1d(tempo_scalar)[0])]

    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP)[0]
    rms_times = librosa.times_like(rms, sr=sr, hop_length=HOP)
    bounds = np.concatenate([beats, [beats[-1] + (beats[1] - beats[0])]])
    beat_rms = np.zeros(len(beats))
    for i in range(len(beats)):
        a = np.searchsorted(rms_times, bounds[i], side='left')
        b = np.searchsorted(rms_times, bounds[i + 1], side='left')
        seg = rms[a : max(a + 1, b)]
        beat_rms[i] = float(np.mean(seg)) if seg.size else 0.0
    beat_db = 20 * np.log10(np.maximum(beat_rms, 1e-9))

    def near(onsets: list[tuple[float, float]], t: float, win: float = 0.035) -> float:
        vals = [s for tt, s in onsets if abs(tt - t) <= win]
        return float(max(vals)) if vals else 0.0

    beat_table = [
        {
            't': round(float(b), 4),
            'k': round(near(drums['kick'], b), 3),
            's': round(near(drums['snare'], b), 3),
            'h': round(near(drums['hihat'], b), 3),
            'r': round(float(beat_db[i]), 2),
        }
        for i, b in enumerate(beats)
    ]

    sections = segment_energy(beat_db)
    for s in sections:
        a, b = s['start_beat'], min(s['end_beat'], len(beats))
        t0 = beats[a]
        t1 = beats[b] if b < len(beats) else beats[-1]
        span = max(t1 - t0, 1e-6)
        s['kick_per_sec'] = round(
            float(np.sum((kick_t >= t0) & (kick_t < t1)) / span), 3
        )
        s['snare_per_sec'] = round(
            float(np.sum((snare_t >= t0) & (snare_t < t1)) / span), 3
        )
        s['start_sec'] = round(float(t0), 3)
        s['end_sec'] = round(float(t1), 3)

    fof = (
        float(np.mean([np.abs(kick_t - b).min() <= FIT_TOL_SEC for b in beats]))
        if kick_t.size
        else 0.0
    )
    back_idx = [i for i in range(len(beats)) if i % 4 in (1, 3)]
    backbeat = (
        float(
            np.mean([
                np.abs(snare_t - beats[i]).min() <= 0.045
                for b_i in [0]
                for i in back_idx
            ])
        )
        if snare_t.size
        else 0.0
    )
    low, mid, high = band_share(y, sr, [(20, 160), (160, 2000), (6000, 16000)])
    perc_ratio = float(
        np.sum(y_perc.astype(np.float64) ** 2)
        / max(np.sum(y.astype(np.float64) ** 2), 1e-9)
    )
    rms_db = 20 * np.log10(np.maximum(rms, 1e-9))
    smooth8 = np.convolve(rms, np.ones(8) / 8.0, mode='same')
    contrast = float(
        20
        * np.log10(
            max(np.percentile(smooth8, 95), 1e-9)
            / max(np.percentile(smooth8, 10), 1e-9)
        )
    )

    return {
        'name': name,
        'file': str(path).replace('\\', '/'),
        'sample_rate': sr,
        'duration_sec': round(duration, 3),
        'tempo': {
            'bpm': round(
                float(np.median(np.diff(beats)) and 60.0 / np.median(np.diff(beats))), 3
            ),
            'bpm_windowed_median': round(float(np.median(bpms)), 3),
            'bpm_window_first': bpms[0],
            'bpm_window_last': bpms[-1],
            'bpm_window_spread': round(float(max(bpms) - min(bpms)), 3),
            'librosa_scalar': round(float(np.atleast_1d(tempo_scalar)[0]), 3),
            'beat_count': int(len(beats)),
            'beat_interval_median_sec': round(float(np.median(np.diff(beats))), 6),
        },
        'grid': {
            'method': 'least squares over beat anchors, 64-beat windows / 32-beat step',
            'windows': wins,
            'residual_ms_median': round(
                float(np.median([w['residual_ms_mean'] for w in wins])), 2
            )
            if wins
            else None,
            'residual_ms_max': round(
                float(max([w['residual_ms_max'] for w in wins])), 2
            )
            if wins
            else None,
            'coverage_median': round(float(np.median([w['coverage'] for w in wins])), 3)
            if wins
            else None,
            'precision_median': round(
                float(np.median([w['precision'] for w in wins])), 3
            )
            if wins
            else None,
            'f1_median': round(float(np.median([w['f1'] for w in wins])), 3)
            if wins
            else None,
            'mean_abs_ms_median': round(
                float(np.median([w['mean_abs_ms'] for w in wins])), 2
            )
            if wins
            else None,
        },
        'drums': {
            'kick': {
                'count': len(drums['kick']),
                'per_sec': round(len(drums['kick']) / duration, 3),
                'onsets': [[round(t, 4), round(s, 3)] for t, s in drums['kick']],
            },
            'snare': {
                'count': len(drums['snare']),
                'per_sec': round(len(drums['snare']) / duration, 3),
                'onsets': [[round(t, 4), round(s, 3)] for t, s in drums['snare']],
            },
            'hihat': {
                'count': len(drums['hihat']),
                'per_sec': round(len(drums['hihat']) / duration, 3),
                'onsets': [[round(t, 4), round(s, 3)] for t, s in drums['hihat']],
            },
        },
        'descriptors': {
            'four_on_the_floor': round(fof, 3),
            'backbeat_2_4': round(backbeat, 3),
            'percussive_ratio': round(perc_ratio, 3),
            'low_share': round(low, 3),
            'mid_share': round(mid, 3),
            'high_share': round(high, 3),
            'rms_mean_db': round(float(rms_db.mean()), 2),
            'energy_contrast_db': round(contrast, 2),
            'spectral_centroid_hz': round(
                float(
                    librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=HOP).mean()
                ),
                1,
            ),
        },
        'beats_sec': [round(float(b), 4) for b in beats],
        'beat_table': beat_table,
        'sections': sections,
    }


def cmd_analyze(args: argparse.Namespace) -> None:
    files = sorted(Path(args.bgm_dir).glob('*.mp3'))
    if args.only:
        files = [f for f in files if args.only in f.stem]
    if not files:
        raise SystemExit('no mp3')
    out = {
        'fps': FPS,
        'hop_length': HOP,
        'n_fft': N_FFT,
        'fit_tol_sec': FIT_TOL_SEC,
        'window': {'beats': WIN_BEATS, 'step': WIN_STEP},
        'bands': {
            'kick': KICK_BAND,
            'snare_body': SNARE_BODY,
            'snare_crack': SNARE_CRACK,
            'hihat': HIHAT_BAND,
        },
        'librosa_version': librosa.__version__,
        'tracks': {},
    }
    for f in files:
        print(f'[analyze] {f.name} ...', flush=True)
        res = analyze_track(f, f.stem)
        out['tracks'][f.stem] = res
        t, g, d = res['tempo'], res['grid'], res['descriptors']
        print(
            f'  bpm={t["bpm"]:.2f} (win {t["bpm_window_first"]:.2f}->{t["bpm_window_last"]:.2f}, '
            f'spread {t["bpm_window_spread"]:.2f}) drum={res["drums"]["kick"]["count"]}/'
            f'{res["drums"]["snare"]["count"]}/{res["drums"]["hihat"]["count"]} '
            f'fof={d["four_on_the_floor"]:.2f} back={d["backbeat_2_4"]:.2f} '
            f'perc={d["percussive_ratio"]:.2f} low={d["low_share"]:.2f} contrast={d["energy_contrast_db"]:.1f}\n'
            f'  grid: res={g["residual_ms_median"]}ms cov={g["coverage_median"]} '
            f'prec={g["precision_median"]} f1={g["f1_median"]} err={g["mean_abs_ms_median"]}ms',
            flush=True,
        )
        for s in res['sections']:
            print(
                f'    {s["name"]:>6} b{s["start_beat"]:>3}-{s["end_beat"]:<3} '
                f'{s["start_sec"]:7.2f}-{s["end_sec"]:7.2f}s {s["rel_db"]:+.1f}dB kick/s={s["kick_per_sec"]:.2f}',
                flush=True,
            )
    Path(args.out).write_text(json.dumps(clean(out), indent=1), encoding='utf-8')
    print(f'[analyze] wrote {args.out}')


def cmd_bars(args: argparse.Namespace) -> None:
    raw = json.loads(Path(args.raw).read_text(encoding='utf-8'))
    tr = raw['tracks'][args.track]
    beats = tr['beats_sec']
    table = tr['beat_table']
    start = next(i for i, b in enumerate(beats) if b >= args.from_sec - 1e-6)
    end = next((i for i, b in enumerate(beats) if b >= args.to_sec - 1e-6), len(beats))
    print(
        f'{args.track}: bars of 4 beats, {beats[start]:.2f}-{beats[min(end, len(beats) - 1)]:.2f}s'
    )
    print(' bar  beat0     t0      dur    rms_dB  kick snare hat')
    for i in range(start, min(end, len(beats)), 4):
        seg = table[i : i + 4]
        if len(seg) < 2:
            break
        dur = beats[min(i + 4, len(beats) - 1)] - beats[i]
        r = float(np.mean([s['r'] for s in seg]))
        k = sum(1 for s in seg if s['k'] > 0)
        sn = sum(1 for s in seg if s['s'] > 0)
        h = sum(1 for s in seg if s['h'] > 0)
        print(
            f' {i // 4:>3}  b{i:>3}  {beats[i]:7.2f}  {dur:5.2f}  {r:6.1f}   {k}    {sn}    {h}'
        )


def cmd_timeline(args: argparse.Namespace) -> None:
    raw = json.loads(Path(args.raw).read_text(encoding='utf-8'))
    tr = raw['tracks'][args.track]
    beats = np.array(tr['beats_sec'], dtype=float)

    clip_start_i = int(np.argmin(np.abs(beats - args.clip_start_sec)))
    clip_start = float(beats[clip_start_i])
    if abs(clip_start - args.clip_start_sec) > 0.08:
        raise SystemExit(
            f'clip start {args.clip_start_sec} not on a beat (nearest {clip_start:.3f})'
        )
    clip_end = clip_start + args.length_sec

    ctx = beats[(beats >= clip_start - 1e-3) & (beats <= clip_end + 1e-3)]
    fit = fit_grid(ctx)
    grid = grid_from(fit['t0'], fit['T'], clip_start - 1e-6, clip_end + 1e-6)
    grid = grid[grid >= clip_start - 1e-6]
    if grid.size < 8:
        raise SystemExit('grid too short')

    def f(t: float) -> int:
        return int(round((float(t) - clip_start) * FPS))

    beat_frames = [f(t) for t in grid]

    # keep only onsets that sit on a grid subdivision; the rest is attack smear
    # or bass leakage in the low band (see docs: "candidate pool, not triggers")
    SUBDIV = {'kick': (0.0, 0.5), 'snare': (0.0, 0.5), 'hihat': (0.0, 0.25, 0.5, 0.75)}
    T = fit['T']
    drum_frames, phase_stats = {}, {}
    for key in ('kick', 'snare', 'hihat'):
        kept, offs, total = [], [], 0
        for t, _s in tr['drums'][key]['onsets']:
            if not (clip_start - T / 2 <= t <= grid[-1] + T / 2):
                continue
            total += 1
            ph = ((t - clip_start) / T) % 1.0
            dists = [(min(abs(ph - s), 1 - abs(ph - s)), s) for s in SUBDIV[key]]
            d, s = min(dists)
            if d <= 0.09:
                fr = f(t)
                if 0 <= fr <= f(grid[-1]):
                    kept.append(fr)
                    offs.append((ph - s) * T * 1000)
        drum_frames[key] = sorted(set(kept))
        phase_stats[key] = {
            'candidates': total,
            'kept': len(kept),
            'dropped': total - len(kept),
            'median_offset_ms': round(float(np.median(offs)), 1) if offs else None,
        }

    sections = []
    for spec in args.sections:
        name, rng = spec.split(':')
        a, b = (int(x) for x in rng.split(','))
        if b >= len(beat_frames):
            raise SystemExit(
                f'section beat {b} outside clip ({len(beat_frames)} beats)'
            )
        sections.append({
            'name': name,
            'start_beat': a,
            'end_beat': b,
            'startSec': round(float(grid[a] - clip_start), 4),
            'endSec': round(float(grid[b] - clip_start), 4),
            'startFrame': int(beat_frames[a]),
            'endFrame': int(beat_frames[b]),
        })

    # per-beat strength from the raw beat table, resampled onto the local grid
    table = np.array([[d['k'], d['s'], d['h'], d['r']] for d in tr['beat_table']])
    raw_beats = np.array(tr['beats_sec'])
    strength = []
    for gb in grid:
        j = int(np.argmin(np.abs(raw_beats - gb)))
        if abs(raw_beats[j] - gb) <= 0.06:
            strength.append(table[j, 0] + 0.55 * table[j, 1] + 0.15 * table[j, 2])
        else:
            strength.append(0.0)
    if args.hits:
        hits = [int(x) for x in args.hits.split(',')]
    else:
        hits = []
        for i in sorted(range(len(strength)), key=lambda i: strength[i], reverse=True):
            if strength[i] <= 0:
                continue
            if all(abs(i - j) >= args.min_hit_gap_beats for j in hits):
                hits.append(i)
            if len(hits) >= 3:
                break
        hits = sorted(hits)

    payload = {
        'track': tr['name'],
        'file': tr['file'],
        'sampleRate': tr['sample_rate'],
        'bpm': round(60.0 / fit['T'], 4),
        'beatOffsetFrames': 0,
        'durationSec': round(float(grid[-1] - clip_start), 4),
        'section': sections[0],
        'sections': sections,
        'beats': beat_frames,
        'beatIntervalFrames': round(fit['T'] * FPS, 4),
        'drumPhase': phase_stats,
        'kicks': drum_frames['kick'],
        'snares': drum_frames['snare'],
        'hihats': drum_frames['hihat'],
        'strongHits': [beat_frames[i] for i in hits],
        'strongHitBeats': hits,
        'clip': {
            'sourceStartSec': round(clip_start, 4),
            'sourceEndSec': round(float(grid[-1]), 4),
            'lengthSec': round(float(grid[-1] - clip_start), 4),
            'beatCount': int(len(grid)),
            'startBeatIndexInTrack': clip_start_i,
        },
        'grid': {
            'bpm': round(60.0 / fit['T'], 4),
            'periodSec': round(fit['T'], 6),
            't0SourceSec': round(fit['t0'], 6),
            'clipFitResidualMsMean': round(fit['residual_ms_mean'], 3),
            'clipFitResidualMsMax': round(fit['residual_ms_max'], 3),
            'contextBeats': int(len(ctx)),
            'transientCoverage': tr['grid']['coverage_median'],
            'transientPrecision': tr['grid']['precision_median'],
            'f1': tr['grid']['f1_median'],
            'meanAbsMs': tr['grid']['mean_abs_ms_median'],
            'windowedResidualMs': tr['grid']['residual_ms_median'],
            'note': 'coverage/precision are 64-beat window medians over the whole track; the clip grid uses a local fit with context beats',
        },
        'sourceTrackDurationSec': tr['duration_sec'],
        'params': {
            'hop_length': raw['hop_length'],
            'n_fft': raw['n_fft'],
            'fps': raw['fps'],
            'fit_tol_sec': raw['fit_tol_sec'],
            'bands': raw['bands'],
            'librosa': raw.get('librosa_version'),
            'window': raw['window'],
            'drumOnsets': 'band-split adaptive peaks; kept only within +/-0.09 beat of {0,0.5} (kick, snare) or {0,0.25,0.5,0.75} (hihat); dropped onsets are attack smear or bass leakage',
            'audio': 'mono, native 44.1 kHz, librosa.load(sr=None), onset detection on HPSS percussive stem',
        },
        'provenance': {
            'script': 'promo-video/research/bgm/analyze.py',
            'methodology': 'video-shotcraft references/music-beat-sync.md sections 1-4',
            'frameBase': 'film frame 0 = clip start, BGM starts at frame 0, beatOffsetFrames = 0',
        },
    }
    Path(args.out).write_text(json.dumps(clean(payload), indent=1), encoding='utf-8')
    print(
        json.dumps(
            clean({
                k: payload[k]
                for k in (
                    'track',
                    'bpm',
                    'durationSec',
                    'beats',
                    'strongHitBeats',
                    'strongHits',
                    'sections',
                    'clip',
                )
            }),
            indent=1,
        )[:5000]
    )
    print('[timeline] wrote', args.out)


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)

    a = sub.add_parser('analyze')
    a.add_argument('--bgm-dir', default=DEFAULT_BGM_DIR)
    a.add_argument('--only', default=None)
    a.add_argument('--out', default='analysis.raw.json')
    a.set_defaults(func=cmd_analyze)

    b = sub.add_parser('bars')
    b.add_argument('--raw', default='analysis.raw.json')
    b.add_argument('--track', required=True)
    b.add_argument('--from', dest='from_sec', type=float, default=0.0)
    b.add_argument('--to', dest='to_sec', type=float, default=1e9)
    b.set_defaults(func=cmd_bars)

    t = sub.add_parser('timeline')
    t.add_argument('--raw', default='analysis.raw.json')
    t.add_argument('--track', required=True)
    t.add_argument('--clip-start-sec', type=float, required=True)
    t.add_argument('--length-sec', type=float, required=True)
    t.add_argument('--sections', nargs=4, required=True)
    t.add_argument('--hits', default=None)
    t.add_argument('--min-hit-gap-beats', type=int, default=16)
    t.add_argument('--out', default='analysis.json')
    t.set_defaults(func=cmd_timeline)

    args = p.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
