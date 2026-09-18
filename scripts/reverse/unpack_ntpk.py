from __future__ import annotations

import argparse
import io
import os
import re
import sys
import zipfile

ROUTE_RE = re.compile(rb'["\'`]((?:/api|/eapi|/weapi|/linux)[A-Za-z0-9_\-/\.{}\%\$:]{2,120})["\'`]')


def find_zip_offset(data: bytes) -> int:
    for sig in (b'PK\x03\x04', b'PK\x05\x06'):
        idx = data.find(sig)
        if idx >= 0:
            return idx
    return -1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--ntpk', required=True)
    ap.add_argument('--out', default='')
    ap.add_argument('--routes', action='store_true')
    args = ap.parse_args()

    with open(args.ntpk, 'rb') as f:
        data = f.read()
    print('file size: %d' % len(data))

    offset = find_zip_offset(data)
    print('zip offset: %d' % offset)
    if offset < 0:
        return

    stripped = data[offset:]
    if not zipfile.is_zipfile(io.BytesIO(stripped)):
        print('not a zip after strip')
        return

    zf = zipfile.ZipFile(io.BytesIO(stripped))
    names = zf.namelist()
    print('entries: %d' % len(names))
    by_ext: dict[str, int] = {}
    for n in names:
        ext = os.path.splitext(n)[1].lower() or '<none>'
        by_ext[ext] = by_ext.get(ext, 0) + 1
    for ext, cnt in sorted(by_ext.items(), key=lambda kv: -kv[1])[:15]:
        print('  %-10s %d' % (ext, cnt))

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        zf.extractall(args.out)
        print('extracted to %s' % args.out)

    if args.routes:
        routes: dict[str, set[str]] = {}
        for n in names:
            if not n.lower().endswith(('.js', '.json', '.html', '.ts')):
                continue
            try:
                blob = zf.read(n)
            except Exception:
                continue
            for m in ROUTE_RE.finditer(blob):
                route = m.group(1).decode('utf-8', 'replace')
                routes.setdefault(route, set()).add(n)
        print('unique routes: %d' % len(routes))
        for route in sorted(routes):
            srcs = sorted(routes[route])[:2]
            print('  %-58s %s' % (route, ','.join(srcs)))


if __name__ == '__main__':
    sys.exit(main())
