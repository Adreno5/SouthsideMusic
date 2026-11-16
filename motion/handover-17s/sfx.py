"""Score for HANDOVER 鈥?one room, five materials, a pulse that locks with the picture.

Reads the composition's own event list (window.__events / window.__score) so the
hits land where the moves land. Writes sfx.wav, mastered to -16 LUFS.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import numpy as np

HERE = pathlib.Path(__file__).parent
SR = 48000
rng = np.random.default_rng(20261018)


def db(x: float) -> float:
    return 10 ** (x / 20)


def bus(n: int) -> np.ndarray:
    return np.zeros((n, 2), dtype=np.float64)


def place(
    target: np.ndarray, sig: np.ndarray, t: float, gain: float = 1.0, pan: float = 0.0
) -> None:
    i = int(round(t * SR))
    if i >= target.shape[0] or i + sig.shape[0] <= 0:
        return
    if i < 0:
        sig = sig[-i:]
        i = 0
    end = min(target.shape[0], i + sig.shape[0])
    seg = sig[: end - i]
    left = np.sqrt(0.5 * (1.0 - pan))
    r = np.sqrt(0.5 * (1.0 + pan))
    target[i:end, 0] += seg * gain * left * 1.4142
    target[i:end, 1] += seg * gain * r * 1.4142


def tvec(dur: float) -> np.ndarray:
    return np.arange(int(dur * SR)) / SR


def fade(sig: np.ndarray, a: float = 0.002, r: float = 0.01) -> np.ndarray:
    n = sig.shape[0]
    na, nr = max(1, int(a * SR)), max(1, int(r * SR))
    e = np.ones(n)
    e[:na] = np.linspace(0, 1, na)
    e[-nr:] *= np.linspace(1, 0, nr)
    return sig * e


def band(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    X[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(X, len(x))


def wood(freq: float = 1200.0, dur: float = 0.11, decay: float = 42.0) -> np.ndarray:
    t = tvec(dur)
    body = np.sin(2 * np.pi * freq * t) * np.exp(-decay * t)
    body += 0.5 * np.sin(2 * np.pi * freq * 2.31 * t) * np.exp(-decay * 1.6 * t)
    n = rng.normal(0, 1, len(t)) * np.exp(-90 * t)
    n = band(n, 900, 7000) * 0.9
    return fade(body * 0.7 + n)


def metal(freq: float = 420.0, dur: float = 0.9, decay: float = 5.0) -> np.ndarray:
    t = tvec(dur)
    sig = np.zeros(len(t))
    for k, (m, a) in enumerate((
        (1, 1.0),
        (2.76, 0.55),
        (5.4, 0.3),
        (8.93, 0.18),
        (13.3, 0.1),
    )):
        sig += (
            a
            * np.sin(2 * np.pi * freq * m * t + rng.random() * 6)
            * np.exp(-decay * (1 + k * 0.55) * t)
        )
    att = rng.normal(0, 1, len(t)) * np.exp(-140 * t)
    return fade(sig * 0.5 + band(att, 1500, 12000) * 0.8, 0.001, 0.05)


def thump(f0: float = 96.0, f1: float = 42.0, dur: float = 0.5) -> np.ndarray:
    t = tvec(dur)
    f = f1 + (f0 - f1) * np.exp(-9 * t)
    ph = 2 * np.pi * np.cumsum(f) / SR
    sig = np.sin(ph) * np.exp(-6.5 * t)
    click = band(rng.normal(0, 1, len(t)) * np.exp(-120 * t), 120, 4000) * 0.5
    return fade(sig + click, 0.001, 0.08)


def air(
    dur: float = 0.5, lo: float = 400.0, hi: float = 6000.0, peak: float = 0.4
) -> np.ndarray:
    t = tvec(dur)
    n = band(rng.normal(0, 1, len(t)), lo, hi)
    env = np.exp(-2.2 * t) * (1 - np.exp(-160 * t))
    return fade(n / (np.max(np.abs(n)) + 1e-9) * env)


def sweep(
    dur: float = 0.9, f0: float = 140.0, f1: float = 420.0, rise: bool = True
) -> np.ndarray:
    t = tvec(dur)
    k = np.linspace(0, 1, len(t)) ** 0.8
    f = f0 + (f1 - f0) * (k if rise else 1 - k)
    ph = 2 * np.pi * np.cumsum(f) / SR
    sig = np.sin(ph) * 0.5 + 0.5 * np.sin(2 * ph)
    env = np.minimum(1, t / 0.05) * np.exp(-1.6 * t)
    n = band(rng.normal(0, 1, len(t)), 300, 5000) * 0.35
    return fade((sig + n) * env)


def room_ir(dur: float = 1.5, decay: float = 4.0) -> np.ndarray:
    t = tvec(dur)
    n = rng.normal(0, 1, (len(t), 2))
    n = np.stack([band(n[:, 0], 120, 7500), band(n[:, 1], 120, 7500)], axis=1)
    env = np.exp(-decay * t)[:, None]
    ir = n * env
    ir[: int(0.012 * SR)] *= np.linspace(0, 1, int(0.012 * SR))[:, None]
    return ir / np.sqrt(np.sum(ir**2, axis=0, keepdims=True))


def convolve(x: np.ndarray, ir: np.ndarray) -> np.ndarray:
    n = x.shape[0] + ir.shape[0] - 1
    size = 1 << (n - 1).bit_length()
    out = np.zeros((n, 2))
    for ch in range(2):
        Xf = np.fft.rfft(x[:, ch], size)
        Hf = np.fft.rfft(ir[:, ch], size)
        out[:, ch] = np.fft.irfft(Xf * Hf, size)[:n]
    return out


KICK_BPM = 110.0
BEAT = 60.0 / KICK_BPM


def bed(n: int, score: dict) -> np.ndarray:
    out = bus(n)
    b_seg = score['bSeg']

    t = 1.05
    while t < 12.15:
        place(out, thump(80, 38, 0.45), t, db(-21))
        if t > 2.30:
            place(out, air(0.09, 2500, 9000), t + BEAT / 2, db(-34), 0.25)
        t += BEAT

    # B's own pulse: enters at 1.05, steps its period on every snap, lands on the grid at 3.30
    for k, (t0, period) in enumerate(b_seg):
        t1 = b_seg[k + 1][0] if k + 1 < len(b_seg) else 3.36
        tt = t0
        while tt < t1:
            place(out, air(0.07, 5200, 12000), tt, db(-33), -0.3)
            place(out, wood(2600, 0.05, 90), tt, db(-36), -0.3)
            tt += period
    # locked pulse after the tempo gate: the same material as the kick's ghost
    tt = 3.36
    while tt < 12.15:
        place(out, air(0.06, 4200, 12000), tt + BEAT / 2, db(-31), 0.3)
        tt += BEAT / 2

    # drone: two detuned lows, tightening as the fade resolves
    t = tvec(n / SR)
    lift = np.clip((t - 1.0) / 8.2, 0, 1)
    drift = np.sin(2 * np.pi * 0.07 * t) * 0.4
    root = 55.0 * (1 + 0.006 * lift + 0.002 * drift)
    ph = 2 * np.pi * np.cumsum(root) / SR
    dr = (np.sin(ph) + 0.5 * np.sin(ph * 1.5 + 0.4) + 0.28 * np.sin(ph * 2.005)) * 0.5
    veil = np.clip(t / 1.4, 0, 1) * (1 - np.clip((t - 12.3) / 1.1, 0, 1))
    dr = dr * veil * db(-27)
    out[:, 0] += dr
    out[:, 1] += np.sin(ph + 0.9) * veil * db(-29) * 0.5
    return out


def main() -> None:
    score = json.loads(pathlib.Path(HERE / 'score.json').read_text(encoding='utf-8'))
    dur = float(score['dur'])
    n = int((dur + 1.6) * SR)
    hits = bus(n)

    def ev(kind: str, t: float) -> None:
        g = db(-9)
        if kind == 'land':
            place(hits, thump(120, 52, 0.5), t, g)
            place(hits, wood(900, 0.16, 30), t, g * 0.7)
        elif kind == 'creep':
            place(hits, air(0.22, 260, 2600), t, db(-26))
            place(hits, wood(300, 0.1, 55), t, db(-30))
        elif kind == 'snap':
            place(hits, wood(2400, 0.06, 95), t, db(-12))
            place(hits, air(0.09, 3000, 11000), t, db(-22), 0.2)
        elif kind == 'unroll':
            place(hits, air(0.55, 500, 5200), t, db(-20), -0.2)
            place(hits, sweep(0.55, 220, 520), t, db(-28))
        elif kind == 'detent':
            place(hits, metal(360, 0.45, 9), t, db(-11))
            place(hits, thump(110, 46, 0.35), t, db(-14))
        elif kind == 'stamp':
            place(hits, air(0.2, 300, 8000), t, db(-13))
            place(hits, wood(520, 0.14, 34), t, db(-12))
            place(hits, thump(90, 60, 0.3), t, db(-16))
        elif kind == 'disc':
            place(hits, sweep(1.0, 70, 240), t, db(-17))
            place(hits, air(1.1, 180, 2200), t, db(-24))
        elif kind == 'slide':
            place(hits, air(0.4, 900, 3600), t, db(-30), -0.1)
        elif kind == 'lock':
            place(hits, wood(1500, 0.12, 52), t, db(-10))
            place(hits, metal(860, 0.55, 8), t, db(-19))
        elif kind == 'sink':
            place(hits, sweep(0.7, 220, 64, rise=False), t, db(-21))
            place(hits, air(0.7, 200, 1800), t, db(-26))
        elif kind == 'grip':
            place(hits, air(0.26, 500, 3400), t, db(-27), 0.3)
            place(hits, wood(260, 0.12, 60), t, db(-31))
        elif kind == 'drag':
            place(hits, air(0.6, 300, 2600), t, db(-25), -0.2)
        elif kind == 'strain':
            place(hits, sweep(1.5, 90, 150), t, db(-26))
            place(hits, air(1.5, 200, 1400), t, db(-31), 0.2)
        elif kind == 'twang':
            place(hits, metal(300, 1.5, 3.2), t, db(-8))
            place(hits, thump(150, 40, 0.7), t, db(-10))
            place(hits, air(0.5, 1200, 12000), t, db(-16))
        elif kind == 'clunk':
            place(hits, thump(140, 44, 0.8), t, db(-9))
            place(hits, metal(180, 0.7, 6), t, db(-15))
            place(hits, wood(760, 0.18, 26), t, db(-13))
        elif kind == 'release':
            place(hits, air(0.45, 400, 3000), t, db(-28))
        elif kind == 'retract':
            place(hits, metal(240, 0.5, 8), t, db(-16))
            place(hits, wood(680, 0.13, 30), t, db(-14))
        elif kind == 'swell':
            place(hits, air(1.2, 120, 2600), t, db(-22))
            place(hits, sweep(1.2, 60, 120), t, db(-24))
        elif kind == 'mark':
            place(hits, thump(110, 34, 1.1), t, db(-7))
            place(hits, air(0.3, 200, 9000), t, db(-12))
            place(hits, metal(300, 1.8, 2.4), t, db(-14))

    for e in score['hit']:
        ev(e['kind'], e['t'])

    ir = room_ir(1.6, 3.6)
    wet = convolve(hits, ir)
    dry = np.zeros_like(wet)
    dry[: hits.shape[0]] = hits
    hit_mix = dry * 0.82 + wet * 0.42

    music = bed(n, score)
    duck = np.ones(n)
    for e in score['hit']:
        i = int(e['t'] * SR)
        ln = int(0.42 * SR)
        if i + ln < n:
            env = np.linspace(0, 1, ln) ** 0.6
            duck[i : i + ln] = np.minimum(duck[i : i + ln], 1 - 0.55 * (1 - env))
    gate = np.ones(n)
    fade_n = int(0.09 * SR)
    for t0, t1 in score.get('quiet', []):
        i0, i1 = max(0, int(t0 * SR)), min(n, int(t1 * SR))
        if i1 <= i0:
            continue
        gate[i0:i1] = 0.0
        a0 = max(0, i0 - fade_n)
        gate[a0:i0] = np.minimum(gate[a0:i0], np.linspace(1, 0, i0 - a0))
        b1 = min(n, i1 + fade_n)
        gate[i1:b1] = np.minimum(gate[i1:b1], np.linspace(0, 1, b1 - i1))
    music *= (duck * gate)[:, None]

    hit_mix = hit_mix[:n]
    mix = (hit_mix + music)[: int(dur * SR)]
    peak = np.max(np.abs(mix))
    mix = np.tanh(mix / peak * 0.92) / 0.92 * 0.72
    raw = HERE / 'sfx_raw.wav'
    write_wav(raw, mix)

    probe = subprocess.run(
        [
            'ffmpeg',
            '-hide_banner',
            '-nostats',
            '-i',
            str(raw),
            '-af',
            'loudnorm=print_format=json',
            '-f',
            'null',
            '-',
        ],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    body = probe.stderr[probe.stderr.rindex('{') :]
    info = json.loads(body[: body.rindex('}') + 1])
    gain = float(info['input_i']) * -1 - 16.0
    out = mix * db(gain)
    out = np.tanh(out * 0.85) / 0.85
    ceiling = db(-7.5)
    if np.max(np.abs(out)) > ceiling:
        out *= ceiling / np.max(np.abs(out))
    write_wav(HERE / 'sfx.wav', out)
    print(
        f'loudness in {info["input_i"]} LUFS -> gain {gain:+.1f} dB, dur {dur}s, '
        f'peak {20 * np.log10(np.max(np.abs(out)) + 1e-9):.1f} dBFS, events {len(score["hit"])}'
    )


def write_wav(path: pathlib.Path, x: np.ndarray) -> None:
    import struct

    data = np.clip(x, -1, 1)
    pcm = (data * 32767).astype('<i2').tobytes()
    ch, sr, bits = 2, SR, 16
    block = ch * bits // 8
    with open(path, 'wb') as f:
        f.write(b'RIFF')
        f.write(struct.pack('<I', 36 + len(pcm)))
        f.write(b'WAVE')
        f.write(b'fmt ')
        f.write(struct.pack('<IHHIIHH', 16, 1, ch, sr, sr * block, block, bits))
        f.write(b'data')
        f.write(struct.pack('<I', len(pcm)))
        f.write(pcm)


if __name__ == '__main__':
    main()
