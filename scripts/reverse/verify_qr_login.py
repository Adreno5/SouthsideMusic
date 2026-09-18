from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'src'))

import ncm
from ncm import apis

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
QR_PATH = os.path.join(OUT_DIR, 'login_qr.png')


def render_qr(url: str) -> str:
    try:
        import qrcode
    except Exception:
        return ''
    qrcode.make(url).save(QR_PATH)
    return QR_PATH


def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 200.0
    ncm.setNewSession()
    s = ncm.getCurrentSession()
    s.deviceId = ncm.generateDeviceId()

    r = apis.login.loginQrcodeUnikey()
    code = r.get('code') if isinstance(r, dict) else None
    unikey = r.get('unikey') if isinstance(r, dict) else None
    print('unikey code = %s | unikey present = %s' % (code, bool(unikey)))
    if not unikey:
        print('unikey failed: %s' % str(r)[:300])
        return

    url = apis.login.getLoginQRCodeUrl(unikey)
    print('QR URL = %s' % url)
    print('QR image = %s' % (render_qr(url) or '(qrcode lib unavailable)'))
    print('>>> 用手机网易云音乐扫这个二维码，注意手机端提示文案 <<<', flush=True)

    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        chk = apis.login.loginQrcodeCheck(unikey)
        c = chk.get('code') if isinstance(chk, dict) else None
        if c != last:
            print('  poll code = %s' % c, flush=True)
            last = c
        if c == 803:
            break
        time.sleep(3)

    if last != 803:
        print('RESULT: 未在 %.0fs 内确认（最后 code=%s）' % (seconds, last))
        return

    apis.login.writeLoginInfo(apis.login.getCurrentLoginStatus())
    s = ncm.getCurrentSession()
    print('RESULT: 二维码登录成功 | logged_in=%s | vipType=%s' % (s.logged_in, s.vipType))

    def show(name, fn):
        try:
            r2 = fn()
            c2 = r2.get('code') if isinstance(r2, dict) else 'n/a'
            n = None
            for k in ('songs', 'playlists', 'data', 'result'):
                v = r2.get(k) if isinstance(r2, dict) else None
                if isinstance(v, list):
                    n = len(v)
                    break
            print('  %-24s code=%-5s items=%s' % (name, c2, n))
            return r2
        except Exception as exc:
            print('  %-24s ERROR %s' % (name, type(exc).__name__))
            return None

    print('--- 全新会话上的完整链路 ---')
    show('getSearchResult', lambda: apis.cloudsearch.getSearchResult('海阔天空', limit=3))
    show('getTrackAudio', lambda: apis.track.getTrackAudio([347230]))
    show('getTrackLyricsNew', lambda: apis.track.getTrackLyricsNew('347230'))
    show('getUserPlaylists', lambda: apis.user.getUserPlaylists(s.uid))
    show('getDailyRecommend', apis.user.getDailyRecommend)
    show('getPersonalFM', apis.radio.getPersonalFM)
    show('loginLogout', apis.login.loginLogout)


if __name__ == '__main__':
    main()
