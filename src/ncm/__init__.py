from __future__ import annotations

import json
import logging
import os
from threading import current_thread
from time import time
from typing import Any

import requests

from .utils import _hex_compose, _random_string
from .utils.crypto import _eapi_decrypt, _eapi_encrypt

"""ncm - netease cloud music python api / download tool.

usage::

    >>> from ncm import apis
    >>> apis.loginViaCellphone(phone='...', password='...', ctcode=86)
    >>> apis.track.getTrackAudio(29732235)
    {'data': [{'id': 29732235, 'url': 'http://...', ...}]}
    >>> apis.track.getTrackDetail(29732235)
    {'songs': [{'name': 'Supernova', 'id': 29732235, ...}]}
    >>> apis.track.getComments(29732235)
    {'isMusician': False, 'userId': -1, ...}

all api requests go through a singleton ncm.Session::

    >>> session = ncm.getCurrentSession()
    >>> ncm.setCurrentSession(session)
    >>> ncm.setNewSession()

session serialization::

    >>> save = ncm.dumpSessionAsString()
    >>> ncm.setNewSession(ncm.loadSessionFromString(save))

notes:
    - (PR#11) overseas users may get 460 'cheating' errors.
      add header: X-Real-IP = 118.88.88.88
"""

__VERSION_MAJOR__ = 1
__VERSION_MINOR__ = 8
__VERSION_PATCH__ = 1

__version__ = '%s.%s.%s' % (__VERSION_MAJOR__, __VERSION_MINOR__, __VERSION_PATCH__)

logger = logging.getLogger('ncm.api')
if 'PYNCM_DEBUG' in os.environ:
    debug_level = os.environ['PYNCM_DEBUG'].upper()
    if debug_level not in {'CRITICAL', 'DEBUG', 'ERROR', 'FATAL', 'INFO', 'WARNING'}:
        debug_level = 'DEBUG'
    logging.basicConfig(
        level=debug_level, format='[%(levelname).4s] %(name)s %(message)s'
    )

API_HOST = 'interfacepc.music.163.com'
CLIENT_OS = 'pc'
CLIENT_APPVER = '3.1.40.205461'
CLIENT_UA = (
    'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Safari/537.36 Chrome/91.0.4472.164 NeteaseMusicDesktop/3.1.40.205461'
)
DEVICE_ID_CHARS = '0123456789ABCDEF'


def _osVersion() -> str:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r'SOFTWARE\Microsoft\Windows NT\CurrentVersion',
        ) as key:
            product = str(winreg.QueryValueEx(key, 'ProductName')[0])
            build = str(winreg.QueryValueEx(key, 'CurrentBuildNumber')[0])
        name = product.replace('Windows 10', 'Windows 11') if int(build) >= 22000 else product
        return 'Microsoft-%s-build-%s-64bit' % (name.replace(' ', '-'), build)
    except OSError:
        return ''


def _eapiConfig(deviceId: str, clientSign: str = '') -> dict:
    config = {
        'os': CLIENT_OS,
        'appver': CLIENT_APPVER,
        'osver': _osVersion(),
        'deviceId': str(deviceId),
    }
    if clientSign:
        config['clientSign'] = str(clientSign)
    return config


def generateDeviceId() -> str:
    return _random_string(52, DEVICE_ID_CHARS)


DEVICE_ID_DEFAULT = generateDeviceId()
SESSION_STACK: dict = {}


class Session(requests.Session):
    """session managing netease cloud music login state and api requests.

    http config is the same as requests.Session. e.g.::

        getCurrentSession().headers['X-Real-IP'] = '1.1.1.1'
        getCurrentSession().force_http = True

    session can be used as a context manager::

        loginViaEmail(...)
        session = createNewSession()
        with session:
            LoginViaCellPhone(...)

    each thread has its own session stack set via `with`.
    """

    HOST = 'music.163.com'
    UA_DEFAULT = (
        'Mozilla/5.0 (linux@github.com/mos9527/ncm) Chrome/PyNCM.%s' % __version__
    )
    UA_EAPI = CLIENT_UA
    UA_LINUX_API = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/60.0.3112.90 Safari/537.36'
    force_http = False

    def __enter__(self):
        SESSION_STACK.setdefault(current_thread(), list())
        SESSION_STACK[current_thread()].append(self)
        return super().__enter__()

    def __exit__(self, *args) -> None:
        SESSION_STACK[current_thread()].pop()
        return super().__exit__(*args)

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': self.UA_DEFAULT,
            'Referer': self.HOST,
        }
        self.login_info: dict[str, Any] = {
            'success': False,
            'tick': time(),
            'content': None,
        }
        self.eapi_config = _eapiConfig(DEVICE_ID_DEFAULT)
        self.csrf_token = ''

    @property
    def deviceId(self):
        return self.eapi_config['deviceId']

    @deviceId.setter
    def deviceId(self, v):
        self.eapi_config['deviceId'] = str(v)

    @property
    def uid(self):
        return self.login_info['content']['account']['id'] if self.logged_in else 0  # type: ignore

    @property
    def nickname(self):
        return (
            self.login_info['content']['profile']['nickname'] if self.logged_in else ''  # type: ignore
        )

    @property
    def lastIP(self):
        return (
            self.login_info['content']['profile']['lastLoginIP']  # type: ignore
            if self.logged_in
            else ''
        )

    @property
    def vipType(self):
        return (
            self.login_info['content']['profile']['vipType']  # type: ignore
            if self.logged_in and not self.is_anonymous
            else 0
        )

    @property
    def logged_in(self):
        return self.login_info['success']

    @property
    def is_anonymous(self):
        return self.logged_in and not self.nickname

    @property
    def bindings(self):
        content = self.login_info.get('content')
        if not isinstance(content, dict):
            return []
        bindings = content.get('bindings')
        if not isinstance(bindings, list):
            return []
        return bindings

    def request(self, method: str, url: str, *a, **k) -> requests.Response:  # type: ignore[override]
        if url[:4] != 'http':
            url = 'https://%s%s' % (self.HOST, url)
        if self.force_http:
            url = url.replace('https:', 'http:')
        return super().request(method, url, *a, **k)

    _session_info = {
        'eapi_config': (
            lambda self: getattr(self, 'eapi_config'),
            lambda self, v: setattr(self, 'eapi_config', v),
        ),
        'login_info': (
            lambda self: getattr(self, 'login_info'),
            lambda self, v: setattr(self, 'login_info', v),
        ),
        'csrf_token': (
            lambda self: getattr(self, 'csrf_token'),
            lambda self, v: setattr(self, 'csrf_token', v),
        ),
        'cookies': (
            lambda self: [
                {'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path}
                for c in getattr(self, 'cookies')
            ],
            lambda self, cookies: [
                getattr(self, 'cookies').set(**cookie) for cookie in cookies
            ],
        ),
    }

    def dump(self) -> dict:
        return {
            name: self._session_info[name][0](self)
            for name in self._session_info.keys()
        }

    def load(self, dumped):
        for k, v in dumped.items():
            self._session_info[k][1](self, v)
        stored = self.eapi_config if isinstance(self.eapi_config, dict) else {}
        self.eapi_config = _eapiConfig(
            stored.get('deviceId') or DEVICE_ID_DEFAULT,
            stored.get('clientSign') or '',
        )
        return True


class SessionManager:
    """ncm session singleton storage."""

    def __init__(self) -> None:
        self.session = Session()

    def get(self):
        if SESSION_STACK.get(current_thread(), None):
            return SESSION_STACK[current_thread()][-1]
        return self.session

    def set(self, session):
        if SESSION_STACK.get(current_thread(), None):
            raise Exception('current session is in `with` block, cannot reassign.')
        self.session = session

    @staticmethod
    def stringify_legacy(session: Session) -> str:
        return _eapi_encrypt('pyncm', json.dumps(session.dump()))['params']

    @staticmethod
    def parse_legacy(dump: str) -> Session:
        session = Session()
        raw = _hex_compose(dump)
        raw = _eapi_decrypt(raw).decode()
        parts = raw.split('-36cd479b6b5-')
        assert parts[0] == 'pyncm'
        session.load(json.loads(parts[1]))
        return session

    @staticmethod
    def stringify(session: Session) -> str:
        from json import dumps
        from zlib import compress
        from base64 import b64encode

        return 'PYNCM' + b64encode(compress(dumps(session.dump()).encode())).decode()

    @staticmethod
    def parse(dump: str) -> Session:
        if dump[:5] == 'PYNCM':
            from json import loads
            from zlib import decompress
            from base64 import b64decode

            session = Session()
            session.load(loads(decompress(b64decode(dump[5:])).decode()))
            return session
        else:
            return SessionManager.parse_legacy(dump)


sessionManager = SessionManager()


def getCurrentSession() -> Session:
    return sessionManager.get()


def setCurrentSession(session: Session):
    sessionManager.set(session)


def setNewSession():
    sessionManager.set(Session())


def createNewSession() -> Session:
    return Session()


def loadSessionFromString(dump: str) -> Session:
    return SessionManager.parse(dump)


def dumpSessionAsString(session: Session) -> str:
    return SessionManager.stringify(session)


def writeLoginInfo(content):
    if isinstance(content, bytes):
        try:
            content = json.loads(content)
        except Exception:
            pass
    sessionManager.session.login_info = {'tick': time(), 'content': content}  # type: ignore
    if isinstance(content, bytes) or not content.get('code') == 200:  # type: ignore
        sessionManager.session.login_info['success'] = False
        raise Exception(content)
    sessionManager.session.login_info['success'] = True
    sessionManager.session.csrf_token = sessionManager.session.cookies.get('__csrf')  # type: ignore
