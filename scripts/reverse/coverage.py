from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'src'))

from ncm.utils import _hex_compose
from ncm.utils.crypto import _eapi_decrypt

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
    '/api/v3/song/detail',
    '/api/song/enhance/player/url/v1',
    '/api/song/lyric/v1',
    '/api/v1/resource/comments/R_SO_4_',
    '/api/resource/comments/add',
    '/api/cloudsearch/pc',
    '/api/pc/page/rcmd/resource/show',
    '/api/v1/discovery/simiSong',
    '/api/w/v1/user/detail/',
    '/api/user/playlist',
    '/api/v1/discovery/recommend/songs',
    '/api/v1/discovery/recommend/resource',
    '/api/feedback/weblog',
    '/api/v6/playlist/detail',
    '/api/playlist/create',
    '/api/playlist/delete',
    '/api/v1/playlist/manipulate/tracks',
    '/api/playmode/intelligence/list',
    '/api/v1/radio/get',
]


def main() -> None:
    capture = sys.argv[1]
    observed: dict[str, int] = {}
    with open(capture, encoding='utf-8') as f:
        for line in f:
            if 'params=' not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            m = re.search(r'params=([0-9A-Fa-f]+)', rec.get('body') or '')
            if not m:
                continue
            try:
                raw = _eapi_decrypt(_hex_compose(m.group(1))).decode('utf-8', 'replace')
            except Exception:
                continue
            env = raw.split('-36cd479b6b5-')[0]
            observed[env] = observed.get(env, 0) + 1

    print('observed eapi envelopes: %d' % len(observed))
    print('--- in-use coverage ---')
    for route in IN_USE:
        n = observed.get(route, 0)
        status = ('OBSERVED x%d' % n) if n else 'not-observed'
        print('  %-46s %s' % (route, status))
    print('--- observed, not in in-use list ---')
    for env in sorted(observed):
        if env not in IN_USE:
            print('  %-46s x%d' % (env, observed[env]))


if __name__ == '__main__':
    main()
