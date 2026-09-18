from __future__ import annotations

import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'src'))

from ncm.utils import _hex_compose
from ncm.utils.crypto import _eapi_decrypt

REPORT = os.path.join(HERE, 'inuse_params.txt')

IN_USE = [
    '/api/login/qrcode/unikey',
    '/api/login/qrcode/client/login',
    '/api/w/nuser/account/get',
    '/api/login/token/refresh',
    '/api/logout',
    '/api/w/login/cellphone',
    '/api/register/anonimous',
    '/api/sms/captcha/sent',
    '/api/sms/captcha/verify',
    '/api/cellphone/existence/check',
    '/api/v3/song/detail',
    '/api/song/enhance/player/url/v1',
    '/api/song/lyric/v1',
    '/api/v1/resource/comments',
    '/api/resource/comments/add',
    '/api/cloudsearch/pc',
    '/api/pc/page/rcmd/resource/show',
    '/api/v1/discovery/simiSong',
    '/api/v1/discovery/recommend/songs',
    '/api/v3/discovery/recommend/songs',
    '/api/v1/discovery/recommend/resource',
    '/api/user/playlist',
    '/api/v6/playlist/detail',
    '/api/playlist/create',
    '/api/playlist/delete',
    '/api/playlist/manipulate/tracks',
    '/api/v1/playlist/manipulate/tracks',
    '/api/playmode/intelligence/list',
    '/api/v1/radio/get',
    '/api/feedback/weblog',
    '/api/w/v1/user/detail/',
]

SECRET_PARAM_KEYS = (
    'phone', 'cellphone', 'password', 'passwordHash', 'captcha', 'username',
    'nonce', 'uid', 'userid', 'userId', 'header', 'token', 'checkToken',
    'MUSIC_U', 'deviceId', 'clientSign',
)


def redact(obj):
    if isinstance(obj, dict):
        return {k: ('<redacted>' if k in SECRET_PARAM_KEYS else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def main() -> None:
    files = sorted(glob.glob(os.path.join(HERE, 'capture.*.jsonl')))
    lines: list[str] = ['# 真实客户端抓包中命中「在用接口」的逐条报文', '']
    lines.append('来源文件：' + ', '.join(os.path.basename(f) for f in files))
    lines.append('')

    seen: dict[str, int] = {}
    for path in files:
        with open(path, encoding='utf-8') as f:
            for raw in f:
                raw = raw.strip()
                if 'params=' not in raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                body = rec.get('body') or ''
                m = re.search(r'params=([0-9A-Fa-f]+)', body)
                if not m:
                    continue
                try:
                    envelope_text = _eapi_decrypt(_hex_compose(m.group(1))).decode('utf-8', 'replace')
                except Exception:
                    continue
                envelope = envelope_text.split('-36cd479b6b5-')[0]
                hit = None
                for route in IN_USE:
                    if envelope.startswith(route):
                        hit = route
                        break
                if hit is None:
                    continue
                seen[hit] = seen.get(hit, 0) + 1

                parts = envelope_text.split('-36cd479b6b5-')
                params_text = parts[1] if len(parts) == 3 else ''
                try:
                    params_pretty = json.dumps(redact(json.loads(params_text)), ensure_ascii=False)
                except Exception:
                    params_pretty = params_text[:400]

                resp_plain = ''
                resp_hex = rec.get('responseHex')
                if resp_hex:
                    try:
                        resp_plain = _eapi_decrypt(_hex_compose(resp_hex)).decode('utf-8', 'replace')
                    except Exception:
                        resp_plain = ''
                    resp_plain = re.sub(r'"MUSIC_U":"[^"]*"', '"MUSIC_U":"<redacted>"', resp_plain)

                lines.append('## %s' % envelope)
                lines.append('- host: %s' % rec.get('url', '').split('/eapi/')[0])
                lines.append('- url: %s' % rec.get('url'))
                lines.append('- method: %s' % rec.get('method'))
                lines.append('- params(明文, 脱敏): %s' % params_pretty[:900])
                lines.append('- response(明文, 截断): %s' % (resp_plain[:400] if resp_plain else '(未解出)'))
                lines.append('')

    lines.append('## 命中计数')
    for route in IN_USE:
        if seen.get(route):
            lines.append('- %s x%d' % (route, seen[route]))

    with open(REPORT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print('wrote report to %s (%d lines)' % (REPORT, len(lines)))


if __name__ == '__main__':
    main()
