from __future__ import annotations

import argparse
import os
import sys

import numpy as np
from matplotlib import pyplot as plt

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src')
)

import crossfade_lab as lab


def main() -> None:
    parser = argparse.ArgumentParser(description='render one crossfade probe stage set')
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--seconds', type=float, default=8.0)
    parser.add_argument('--strength', type=float, default=0.6)
    args = parser.parse_args()

    lab.style()
    paths = lab.songPaths(args.seed)
    current = lab.loadSong(paths[0])
    following = lab.loadSong(paths[1])
    info = lab.crossfade(current, following, args.seconds, args.strength)
    fade = max(info.fade_seconds, 0.001)
    dip = lab.dipDb(info)
    pair = f'{lab.songName(paths[0])} -> {lab.songName(paths[1])}'
    print(f'{pair}  fade={fade:.2f}s  {info.transition_type}  dip={dip:+.2f} dB')

    fig, axes = plt.subplots(3, 2, figsize=(14.0, 9.4))
    fig.suptitle(
        f'crossfade optimization probe · {pair} · pass 1/4 · strength {args.strength:.2f}',
        color=lab.TEXT,
        fontsize=13,
    )

    span = min(4.0 + fade, len(current) / 1000.0, len(following) / 1000.0)
    outgoing = lab.samplesOf(
        lab.segmentWindow(current, span, tail=True), info.sample_rate, info.channels
    )
    incoming = lab.samplesOf(
        lab.segmentWindow(following, span), info.sample_rate, info.channels
    )

    ax = axes[0][0]
    lab.plotEnvelope(ax, outgoing, info.sample_rate, lab.ACCENT, offset=-span)
    ax.axvspan(-fade, 0.0, color=lab.WARN, alpha=0.14)
    ax.axvline(0.0, color=lab.MUTED, linewidth=0.8, linestyle=':')
    ax.set_title(f'pass 1 · outgoing tail energy · ending={info.ending_type}')
    ax.set_ylabel('dBFS')
    ax.set_xlabel('seconds before join')

    ax = axes[0][1]
    lab.plotEnvelope(ax, incoming, info.sample_rate, lab.WARN)
    ax.axvspan(0.0, fade, color=lab.ACCENT, alpha=0.14)
    ax.axvline(0.0, color=lab.MUTED, linewidth=0.8, linestyle=':')
    ax.set_title(f'pass 2 · incoming head energy · lead={fade:.2f}s')
    ax.set_ylabel('dBFS')
    ax.set_xlabel('seconds after join')

    ax = axes[1][0]
    out_profile = np.asarray(info.fade_out_profile or (0.0,), dtype=np.float64)
    in_profile = np.asarray(info.fade_in_profile or (0.0,), dtype=np.float64)
    steps = np.linspace(0.0, fade, len(out_profile))
    residual = np.sqrt(out_profile**2 + in_profile**2)
    ax.step(steps, out_profile, where='post', color=lab.ACCENT, label='fade out')
    ax.step(steps, in_profile, where='post', color=lab.WARN, label='fade in')
    ax.plot(steps, residual, color=lab.MUTED, linestyle='--', label='sqrt(out^2+in^2)')
    ax.set_title(f'pass 3 · fade curve refine · {info.transition_type}')
    ax.set_xlabel('seconds into fade')
    ax.set_ylabel('gain')
    ax.grid(True, alpha=0.2)
    lab.legend(ax)

    ax = axes[1][1]
    lab.plotEnvelope(ax, info.samples, info.sample_rate, lab.ACCENT)
    ax.set_title(f'pass 3 · mixed continuity · dip={dip:+.2f} dB')
    ax.set_xlabel('seconds into fade')
    ax.set_ylabel('dBFS')

    ax = axes[2][0]
    signal = lab.mono(info.samples)
    if signal.size > 128:
        ax.specgram(signal, NFFT=1024, Fs=info.sample_rate, noverlap=512, cmap='magma')
    ax.set_title('pass 4 · spectral overlap of the blend')
    ax.set_xlabel('seconds into fade')
    ax.set_ylabel('Hz')

    ax = axes[2][1]
    ax.axis('off')
    log = [
        f'pair            {pair}',
        f'requested       {args.seconds:.2f}s @ strength {args.strength:.2f}',
        f'settled         {fade:.3f}s @ speed {info.target_speed:.4f}',
        *info.debugInfo(),
        f'continuity dip  {dip:+.3f} dB',
        f'window          {info.start_seconds:.2f}s -> {info.start_seconds + fade:.2f}s',
    ]
    ax.text(
        0.0,
        1.0,
        '\n'.join(log),
        va='top',
        family='monospace',
        fontsize=8.5,
        color=lab.MUTED,
    )
    ax.set_title('pass log')

    fig.tight_layout()
    lab.save(fig, f'probe_{lab.songName(paths[0])}_{lab.songName(paths[1])}.png')


if __name__ == '__main__':
    main()
