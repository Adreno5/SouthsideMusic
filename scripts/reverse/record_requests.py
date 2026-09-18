from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'src'))

import ncm
from ncm import apis

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'requests.jsonl')
SECRET_KEYS = (
    'MUSIC_U', '__csrf', 'deviceId', 'clientSign', 'NMTID', 'WNMCID',
    'uid', 'userid', 'userId', 'nickname', 'phone', 'cellphone',
)
RECORDS: list[dict] = []


def redact(obj):
    if isinstance(obj, dict):
        return {k: ('<redacted>' if k in SECRET_KEYS else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def install_hook() -> None:
    original = apis.eapi

    def wrapper(path, data, session=None, method='POST'):
        resp = original(path, data, session=session, method=method)
        s = session or ncm.getCurrentSession()
        try:
            cookie_names = sorted({ck.name for ck in s.cookies})
        except Exception:
            cookie_names = []
        RECORDS.append({
            'path': path,
            'method': method,
            'url': 'https://%s/eapi/%s' % (apis.API_HOST, path[5:]),
            'host': apis.API_HOST,
            'envelope_fields': sorted(s.eapi_config.keys()),
            'cookie_names': cookie_names,
            'params': redact({k: v for k, v in data.items() if k != 'header'}),
            'code': resp.get('code') if isinstance(resp, dict) else None,
            'resp_top_keys': sorted(resp.keys()) if isinstance(resp, dict) else None,
        })
        return resp

    apis.eapi = wrapper
    for name in ('login', 'track', 'cloudsearch', 'recommend', 'user', 'playlist', 'playmode', 'radio'):
        mod = getattr(apis, name, None)
        if mod is not None and hasattr(mod, 'eapi'):
            mod.eapi = wrapper


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
    capture = sys.argv[1] if len(sys.argv) > 1 else ''
    install_hook()
    ncm.setNewSession()
    s = ncm.getCurrentSession()
    s.deviceId = ncm.generateDeviceId()

    music_u = extract_music_u(capture) if capture and os.path.exists(capture) else ''
    if music_u:
        apis.login.loginViaCookie(MUSIC_U=music_u)
        print('session = captured cookie | logged_in = %s' % s.logged_in)
    else:
        apis.login.loginViaAnonymousAccount()
        print('session = anonymous | logged_in = %s' % s.logged_in)

    def run(name, fn):
        try:
            r = fn()
            code = r.get('code') if isinstance(r, dict) else 'n/a'
            print('  %-26s code=%s' % (name, code))
            return r
        except Exception as exc:
            print('  %-26s ERROR %s' % (name, type(exc).__name__))
            return None

    print('--- in-use calls (read-only) ---')
    run('getCurrentLoginStatus', apis.login.getCurrentLoginStatus)
    run('getUserDetail', lambda: apis.user.getUserDetail(s.uid))
    run('getSearchResult', lambda: apis.cloudsearch.getSearchResult('海阔天空', limit=3))
    run('getTrackDetail', lambda: apis.track.getTrackDetail([347230]))
    run('getTrackAudio', lambda: apis.track.getTrackAudio([347230]))
    run('getTrackLyricsNew', lambda: apis.track.getTrackLyricsNew('347230'))
    run('getComments', lambda: apis.track.getComments('347230', 0, 5))
    run('getPcRecommendResource', apis.recommend.getPcRecommendResource)
    run('getSimilarSongs', lambda: apis.recommend.getSimilarSongs(347230, limit=5))
    run('getDailyRecommend', apis.user.getDailyRecommend)
    run('getDailyRecommendResource', apis.user.getDailyRecommendResource)
    run('getPersonalFM', apis.radio.getPersonalFM)
    run('setWeblog', lambda: apis.user.setWeblog({'action': 'test', 'json': {}}))
    pl = run('getUserPlaylists', lambda: apis.user.getUserPlaylists(s.uid))
    if isinstance(pl, dict):
        playlists = pl.get('playlist') or []
        if playlists:
            pid = playlists[0].get('id')
            run('getPlaylistInfoEapi', lambda: apis.playlist.getPlaylistInfoEapi(pid, 5, 8))
            run('getPlaylistAllTracks', lambda: apis.playlist.getPlaylistAllTracks(pid, 0, 5))
            run('getIntelligenceList', lambda: apis.playmode.getIntelligenceList(347230, pid, count=20))

    with open(OUT, 'w', encoding='utf-8') as f:
        for rec in RECORDS:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    print('recorded %d real requests -> %s' % (len(RECORDS), OUT))


if __name__ == '__main__':
    main()
