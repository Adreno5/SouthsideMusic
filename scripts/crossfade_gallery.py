from __future__ import annotations

import argparse
import os
import sys

import numpy as np
from matplotlib import pyplot as plt
from matplotlib.gridspec import GridSpec

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src')
)

import crossfade_lab as lab

MAX_PAIRS = 6


def main() -> None:
    parser = argparse.ArgumentParser(description='gallery of crossfade candidates')
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--pairs', type=int, default=MAX_PAIRS)
    parser.add_argument('--seconds', type=float, default=8.0)
    parser.add_argument('--strength', type=float, default=0.6)
    args = parser.parse_args()
    if args.pairs > MAX_PAIRS:
        raise SystemExit(f'at most {MAX_PAIRS} pairs per gallery')

    lab.style()
    paths = lab.songPaths(args.seed)
    if len(paths) < args.pairs * 2:
        raise SystemExit(f'need {args.pairs * 2} songs in {lab.MUSIC_DIR}')

    results = []
    for index in range(args.pairs):
        current = lab.loadSong(paths[index * 2])
        following = lab.loadSong(paths[index * 2 + 1])
        info = lab.crossfade(current, following, args.seconds, args.strength)
        dip = lab.dipDb(info)
        results.append((paths[index * 2], paths[index * 2 + 1], info, dip))
        print(
            f'candidate {index + 1:02d}  {lab.songName(paths[index * 2])}'
            f' -> {lab.songName(paths[index * 2 + 1])}  fade={info.fade_seconds:.2f}s'
            f'  dip={dip:+.2f} dB  {info.transition_type}'
        )

    dips = [entry[3] for entry in results]
    best_index = np.argmax(dips)
    figure = plt.figure(figsize=(15.0, 10.6))
    grid = GridSpec(3, 3, figure=figure, height_ratios=(1.0, 1.0, 0.9), hspace=0.6)
    figure.suptitle(
        f'crossfade candidate gallery · seed {args.seed} · {args.seconds:.1f}s'
        f' @ strength {args.strength:.2f} · {len(results)} candidates',
        color=lab.TEXT,
        fontsize=13,
        y=0.98,
    )

    for index, (current_path, next_path, info, dip) in enumerate(results):
        row, column = divmod(index, 3)
        ax = figure.add_subplot(grid[row, column])
        envelope = lab.plotEnvelope(ax, info.samples, info.sample_rate, lab.ACCENT)
        fade_in = np.asarray(info.fade_in_profile or (0.0,), dtype=np.float64)
        if envelope.size:
            high = np.max(envelope) - 1.0
            low = min(high - 6.0, max(-58.0, np.min(envelope)))
            ax.plot(
                np.linspace(0.0, info.fade_seconds, len(fade_in)),
                low + fade_in * (high - low),
                color=lab.WARN,
                linewidth=1.0,
            )
        ax.set_xticks([])
        ax.set_title(
            f'cand {index + 1:02d} · {info.transition_type} · {info.fade_seconds:.1f}s',
            fontsize=9,
        )
        ax.text(
            0.03,
            0.08,
            f'dip {dip:+.2f} dB · key {info.current_key or "?"} -> {info.next_key or "?"}',
            transform=ax.transAxes,
            fontsize=7,
            color=lab.MUTED,
        )
        if index == best_index:
            for spine in ax.spines.values():
                spine.set_color(lab.WARN)
                spine.set_linewidth(1.6)

    ax = figure.add_subplot(grid[2, 0])
    timbre = [entry[2].timbre_similarity for entry in results]
    keys = [entry[2].key_compatibility for entry in results]
    ax.scatter(
        timbre, keys, c=dips, cmap='magma', s=46, edgecolor=lab.MUTED, linewidth=0.5
    )
    for index, (x_value, y_value) in enumerate(zip(timbre, keys)):
        ax.annotate(f'{index + 1:02d}', (x_value, y_value), fontsize=7, color=lab.TEXT)
    ax.set_title('candidate pool · timbre vs key', fontsize=9)
    ax.set_xlabel('timbre similarity')
    ax.set_ylabel('key compatibility')
    ax.grid(True, alpha=0.2)

    ax = figure.add_subplot(grid[2, 1])
    steps = np.arange(1, len(dips) + 1)
    ax.plot(steps, dips, color=lab.MUTED, marker='o', markersize=3, label='dip')
    ax.step(
        steps,
        np.maximum.accumulate(dips),
        where='post',
        color=lab.WARN,
        label='best-so-far',
    )
    ax.set_title('generation 1 · best-so-far continuity', fontsize=9)
    ax.set_xlabel('candidate')
    ax.set_ylabel('dip (dB)')
    ax.grid(True, alpha=0.2)
    lab.legend(ax)

    ax = figure.add_subplot(grid[2, 2])
    ax.axis('off')
    log = [
        f'seed        {args.seed}',
        f'pool        {len(results)} / {len(paths) // 2} pairs',
        f'params      {args.seconds:.2f}s @ strength {args.strength:.2f}',
        '',
        'candidate log',
    ]
    for index, (current_path, next_path, info, dip) in enumerate(results):
        log.append(
            f'  {index + 1:02d}  {lab.songName(current_path)}->{lab.songName(next_path)}'
            f'  {info.transition_type[:9]}  {dip:+.2f} dB'
        )
    log += [
        '',
        f'selected    cand {best_index + 1:02d}  best dip {dips[best_index]:+.3f} dB',
        f'            {results[best_index][2].transition_type}'
        f' @ {results[best_index][2].fade_seconds:.3f}s',
    ]
    ax.text(
        0.0,
        1.0,
        '\n'.join(log),
        va='top',
        family='monospace',
        fontsize=8.0,
        color=lab.MUTED,
    )
    ax.set_title('pool log', fontsize=9)

    lab.save(fig=figure, name=f'gallery_seed{args.seed}_x{len(results)}.png')


if __name__ == '__main__':
    main()
