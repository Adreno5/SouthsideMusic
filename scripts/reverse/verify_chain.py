from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'src'))

import ncm
from ncm import apis


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
    print('token source = capture (value not printed) | found = %s | len = %d' % (bool(music_u), len(music_u)))
    if not music_u:
        return

    ncm.setNewSession()
    apis.login.loginViaCookie(MUSIC_U=music_u)
    s = ncm.getCurrentSession()
    print('logged_in = %s | vipType = %s' % (s.logged_in, s.vipType))
    uid = s.uid
    print('uid resolved = %s' % bool(uid))

    def show(name, fn):
        try:
            r = fn()
            code = r.get('code') if isinstance(r, dict) else 'n/a'
            n = None
            for k in ('songs', 'playlists', 'data', 'result'):
                v = r.get(k) if isinstance(r, dict) else None
                if isinstance(v, list):
                    n = len(v)
                    break
            print('  %-26s code=%-5s items=%s' % (name, code, n))
            return r
        except Exception as exc:
            print('  %-26s ERROR %s' % (name, type(exc).__name__))
            return None

    print('--- in-use chain (read-only) ---')
    show('getCurrentLoginStatus', apis.login.getCurrentLoginStatus)
    show('getUserDetail', lambda: apis.user.getUserDetail(uid))
    show('getSearchResult', lambda: apis.cloudsearch.getSearchResult('海阔天空', limit=3))
    show('getTrackDetail', lambda: apis.track.getTrackDetail([347230]))
    show('getTrackAudio', lambda: apis.track.getTrackAudio([347230]))
    show('getTrackLyricsNew', lambda: apis.track.getTrackLyricsNew('347230'))
    show('getComments', lambda: apis.track.getComments('347230', 0, 5))
    show('getPcRecommendResource', apis.recommend.getPcRecommendResource)
    show('getSimilarSongs', lambda: apis.recommend.getSimilarSongs(347230, limit=5))
    show('getDailyRecommend', apis.user.getDailyRecommend)
    show('getDailyRecommendResource', apis.user.getDailyRecommendResource)
    show('getPersonalFM', apis.radio.getPersonalFM)
    show('getIntelligenceList', lambda: apis.playmode.getIntelligenceList(347230, 1))
    show('setWeblog', lambda: apis.user.setWeblog({'action': 'test', 'json': {}}))
    pl = show('getUserPlaylists', lambda: apis.user.getUserPlaylists(uid))
    if isinstance(pl, dict):
        playlists = pl.get('playlist') or []
        if playlists:
            pid = playlists[0].get('id')
            show('getPlaylistInfoEapi', lambda: apis.playlist.getPlaylistInfoEapi(pid, 5, 8))
            show('getPlaylistAllTracks', lambda: apis.playlist.getPlaylistAllTracks(pid, 0, 5))

    print('--- logout path (anonymous session only) ---')
    ncm.setNewSession()
    anon = ncm.getCurrentSession()
    anon.deviceId = ncm.generateDeviceId()
    try:
        apis.login.loginViaAnonymousAccount()
        print('  loginViaAnonymousAccount    logged_in=%s' % anon.logged_in)
    except Exception as exc:
        print('  loginViaAnonymousAccount    ERROR %s' % type(exc).__name__)
    show('loginLogout(anon)', apis.login.loginLogout)


if __name__ == '__main__':
    main()
