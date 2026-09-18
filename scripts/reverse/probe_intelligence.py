from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'src'))

import ncm
from ncm import apis
from ncm.apis import eapi


def extract_music_u(path: str) -> str:
    last = ''
    with open(path, encoding='utf-8') as f:
        for line in f:
            if 'MUSIC_U=' not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            m = re.search(r'MUSIC_U=([0-9A-Fa-f]+)', rec.get('cookie') or '')
            if m:
                last = m.group(1)
    return last


def main() -> None:
    capture = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'capture.jsonl'
    )
    music_u = extract_music_u(capture)
    if not music_u:
        print('no token')
        return
    ncm.setNewSession()
    apis.login.loginViaCookie(MUSIC_U=music_u)
    s = ncm.getCurrentSession()
    if not s.logged_in:
        print('login failed')
        return

    pls = apis.user.getUserPlaylists(s.uid).get('playlist') or []
    if not pls:
        print('no playlists')
        return
    pid = pls[0]['id']
    detail = apis.playlist.getPlaylistInfoEapi(pid, 50, 8)
    track_ids = [t['id'] for t in (detail.get('playlist', {}).get('tracks') or [])]
    if not track_ids:
        print('no tracks in playlist')
        return
    sid = track_ids[0]
    print('probe playlist=%s tracks=%d seed=%s' % (bool(pid), len(track_ids), bool(sid)))

    variants = {
        'A str values, no songIds': {
            'songId': str(sid), 'playlistId': str(pid), 'startMusicId': str(sid),
            'type': 'fromPlayOne', 'count': '20',
        },
        'B numbers, no songIds': {
            'songId': sid, 'playlistId': pid, 'startMusicId': sid,
            'type': 'fromPlayOne', 'count': 20,
        },
        'C numbers + sid field': {
            'songId': sid, 'playlistId': pid, 'startMusicId': sid,
            'type': 'fromPlayOne', 'count': 20, 'sid': str(sid),
        },
        'D numbers + fromPlayAll': {
            'songId': sid, 'playlistId': pid, 'startMusicId': sid,
            'type': 'fromPlayAll', 'count': 20,
        },
        'E query form numbers': {
            'songId': str(sid), 'playlistId': str(pid), 'startMusicId': str(sid),
            'type': 'fromPlayOne', 'count': str(len(track_ids)),
        },
    }
    for name, params in variants.items():
        try:
            r = eapi('/api/playmode/intelligence/list', params)
            code = r.get('code') if isinstance(r, dict) else 'n/a'
            extra = ''
            if isinstance(r, dict) and r.get('data'):
                extra = ' data=%d' % len(r['data'])
            print('  %-28s code=%s%s' % (name, code, extra))
        except Exception as exc:
            print('  %-28s ERROR %s' % (name, type(exc).__name__))


if __name__ == '__main__':
    main()
