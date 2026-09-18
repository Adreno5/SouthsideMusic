from __future__ import annotations

import argparse
import os
import re
import sys


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--pattern', required=True)
    ap.add_argument('--window', type=int, default=160)
    ap.add_argument('--max-hits', type=int, default=4)
    ap.add_argument('--exts', default='.js,.html,.json')
    args = ap.parse_args()

    exts = tuple(e.strip() for e in args.exts.split(',') if e.strip())
    rx = re.compile(args.pattern.encode('utf-8'), re.IGNORECASE)
    total = 0
    for dirpath, _dirnames, filenames in os.walk(args.root):
        for fn in filenames:
            if not fn.lower().endswith(exts):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, 'rb') as f:
                    blob = f.read()
            except OSError:
                continue
            hits = list(rx.finditer(blob))
            if not hits:
                continue
            rel = os.path.relpath(path, args.root)
            print('=== %s (%d hits) ===' % (rel, len(hits)))
            for m in hits[: args.max_hits]:
                s = max(0, m.start() - args.window)
                e = min(len(blob), m.end() + args.window)
                chunk = blob[s:e].decode('utf-8', 'replace')
                chunk = re.sub(r'\s+', ' ', chunk)
                print('  @%d  %s' % (m.start(), chunk))
            total += len(hits)
    print('TOTAL HITS: %d' % total)


if __name__ == '__main__':
    sys.exit(main())
