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

TRACE_STRENGTHS = np.round(np.linspace(0.1, 1.0, 8), 2)
GRID_SECONDS = (2.0, 4.0, 6.0, 8.0, 10.0, 14.0)


def main() -> None:
    parser = argparse.ArgumentParser(description='sweep crossfade parameters')
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--seconds', type=float, default=8.0)
    args = parser.parse_args()

    lab.style()
    paths = lab.songPaths(args.seed)
    current = lab.loadSong(paths[0])
    following = lab.loadSong(paths[1])
    pair = f'{lab.songName(paths[0])} -> {lab.songName(paths[1])}'

    trace = []
    for index, strength in enumerate(TRACE_STRENGTHS, start=1):
        info = lab.crossfade(current, following, args.seconds, strength)
        dip = lab.dipDb(info)
        trace.append((strength, info.fade_seconds, dip, info.transition_type))
        print(
            f'iter {index:02d}  strength={strength:.2f}  fade={info.fade_seconds:.2f}s'
            f'  dip={dip:+.2f} dB  {info.transition_type}'
        )

    grid = np.zeros((len(GRID_SECONDS), len(TRACE_STRENGTHS)), dtype=np.float64)
    for row, seconds in enumerate(GRID_SECONDS):
        for column, strength in enumerate(TRACE_STRENGTHS):
            grid[row, column] = lab.dipDb(
                lab.crossfade(current, following, seconds, strength)
            )
        print(f'grid row {row + 1}/{len(GRID_SECONDS)}  seconds={seconds:.1f}  done')

    strengths = [entry[0] for entry in trace]
    fades = [entry[1] for entry in trace]
    dips = [entry[2] for entry in trace]
    best_trace = np.argmax(dips)
    best_row, best_column = np.unravel_index(np.argmax(grid), grid.shape)

    fig, axes = plt.subplots(2, 2, figsize=(14.0, 9.0))
    fig.suptitle(
        f'crossfade parameter sweep · {pair} · {len(trace)} trace steps · '
        f'{grid.size} candidates',
        color=lab.TEXT,
        fontsize=13,
    )

    ax = axes[0][0]
    ax.plot(strengths, dips, color=lab.ACCENT, marker='o', markersize=4, linewidth=1.2)
    ax.scatter(
        [strengths[best_trace]],
        [dips[best_trace]],
        color=lab.WARN,
        zorder=3,
        label=f'best {dips[best_trace]:+.2f} dB',
    )
    ax.set_title(f'trace · continuity loss @ {args.seconds:.1f}s')
    ax.set_xlabel('crossfade strength')
    ax.set_ylabel('dip (dB)')
    ax.grid(True, alpha=0.2)
    lab.legend(ax)

    ax = axes[0][1]
    ax.plot(strengths, fades, color=lab.WARN, marker='s', markersize=4, linewidth=1.2)
    ax.set_title('trace · adaptive fade length')
    ax.set_xlabel('crossfade strength')
    ax.set_ylabel('fade (s)', color=lab.WARN)
    ax.grid(True, alpha=0.2)

    ax = axes[1][0]
    image = ax.imshow(grid, cmap='magma', aspect='auto')
    ax.set_xticks(range(len(TRACE_STRENGTHS)))
    ax.set_xticklabels([f'{value:.2f}' for value in TRACE_STRENGTHS])
    ax.set_yticks(range(len(GRID_SECONDS)))
    ax.set_yticklabels([f'{value:.0f}s' for value in GRID_SECONDS])
    ax.set_xlabel('crossfade strength')
    ax.set_ylabel('requested seconds')
    ax.scatter(
        [best_column],
        [best_row],
        marker='s',
        s=90,
        facecolor='none',
        edgecolor=lab.ACCENT,
        linewidth=1.6,
    )
    ax.set_title(f'search space · best {grid[best_row, best_column]:+.2f} dB')
    fig.colorbar(image, ax=ax, label='dip (dB)')

    ax = axes[1][1]
    ax.axis('off')
    log = [
        f'pair        {pair}',
        f'base        {args.seconds:.2f}s @ fixed strength ramp',
        '',
        'iteration log',
    ]
    for index, entry in enumerate(trace, start=1):
        log.append(
            f'  {index:02d}  s={entry[0]:.2f}  fade={entry[1]:.2f}s'
            f'  dip={entry[2]:+.2f}  {entry[3]}'
        )
    log += [
        '',
        f'winner      strength={TRACE_STRENGTHS[best_column]:.2f}'
        f' seconds={GRID_SECONDS[best_row]:.0f}',
        f'            dip={grid[best_row, best_column]:+.3f} dB',
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
    ax.set_title('iteration log')

    fig.tight_layout()
    lab.save(fig, f'sweep_{lab.songName(paths[0])}_{lab.songName(paths[1])}.png')


if __name__ == '__main__':
    main()
