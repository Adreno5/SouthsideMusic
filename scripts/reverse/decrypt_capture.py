from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'src'))

from ncm.utils import _hex_compose, _hash_hex_digest
from ncm.utils.crypto import _eapi_decrypt

SECRET_COOKIE_KEYS = (
    'MUSIC_U',
    '__csrf',
    'deviceId',
    'clientSign',
    'NMTID',
    'WNMCID',
    'MUSIC_A',
    '__SNM',
)


def redact_cookie(cookie: str) -> str:
    parts = []
    for chunk in (cookie or '').split(';'):
        chunk = chunk.strip()
        if not chunk:
            continue
        name = chunk.split('=', 1)[0]
        if name in SECRET_COOKIE_KEYS:
            parts.append('%s=<redacted:%d>' % (name, len(chunk)))
        else:
            parts.append(chunk)
    return '; '.join(parts)


SENSITIVE_PARAM_KEYS = (
    'phone', 'cellphone', 'password', 'passwordHash', 'captcha', 'username',
    'nonce', 'uid', 'userid', 'userId', 'header', 'token', 'checkToken',
)


def redact_params(obj):
    if isinstance(obj, dict):
        return {
            k: ('<redacted>' if k in SENSITIVE_PARAM_KEYS else redact_params(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact_params(v) for v in obj]
    return obj


def decrypt_params(hexblob: str) -> tuple[str, str, bool]:
    raw = _eapi_decrypt(_hex_compose(hexblob)).decode('utf-8', 'replace')
    pieces = raw.split('-36cd479b6b5-')
    if len(pieces) != 3:
        return raw, '', False
    url, text, digest = pieces
    expect = _hash_hex_digest('nobody%suse%smd5forencrypt' % (url, text))
    return url, text, expect == digest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--capture', required=True)
    ap.add_argument('--limit', type=int, default=40)
    ap.add_argument('--full', action='store_true')
    ap.add_argument('--filter', default='')
    args = ap.parse_args()

    seen = set()
    shown = 0
    with open(args.capture, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get('ev') not in ('multi_add', 'perform'):
                continue
            url = rec.get('url') or ''
            if args.filter and not any(s and s in url for s in args.filter.split(',')):
                continue
            m = re.search(r'params=([0-9A-Fa-f]+)', rec.get('body') or '')
            print('=' * 90)
            print('URL      : %s' % url)
            print('METHOD   : %s' % rec.get('method'))
            if not m:
                print('BODY     : (no params= field, %d bytes)' % len(rec.get('body') or ''))
                continue
            try:
                plain_url, text, ok = decrypt_params(m.group(1))
            except Exception as exc:
                print('DECRYPT  : FAILED %s' % exc)
                continue
            key = (url, text)
            if key in seen and not args.full:
                print('(duplicate, skipped)')
                continue
            seen.add(key)
            print('ENVELOPE : %s' % plain_url)
            print('DIGEST OK: %s' % ok)
            try:
                pretty = json.dumps(redact_params(json.loads(text)), ensure_ascii=False, indent=2)
            except Exception:
                pretty = text
            print('PARAMS   :')
            print(pretty[:4000])
            shown += 1
            if shown >= args.limit:
                break


if __name__ == '__main__':
    import io

    _buf = io.StringIO()
    _real = sys.stdout
    sys.stdout = _buf
    try:
        _rc = main()
    finally:
        sys.stdout = _real
    _report = _buf.getvalue()
    _dest = os.environ.get('CAPTURE_REPORT', '')
    if _dest:
        with open(_dest, 'w', encoding='utf-8') as _f:
            _f.write(_report)
        _real.write('wrote %d chars to %s\n' % (len(_report), _dest))
    else:
        _real.write(_report)
    sys.exit(_rc)
