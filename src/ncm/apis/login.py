from __future__ import annotations

from base64 import b64encode
from time import time
from urllib.parse import urlencode
from uuid import uuid4

from . import eapi
from .exception import LoginFailedException
from .. import CLIENT_OS, getCurrentSession, writeLoginInfo
from ..utils.crypto import _hash_hex_digest
from ..utils.security import cloudmusic_dll_encode_id

QRCODE_LOGIN_URL = 'https://st.music.163.com/st/platform/scanlogin'
QRCODE_CLIENT_TYPE = 5


def loginLogout() -> dict:
    """log out current session."""
    return eapi('/api/logout', {})


def loginRefreshToken() -> dict:
    """refresh login token."""
    return eapi('/api/login/token/refresh', {})


def loginQrcodeUnikey(dtype=QRCODE_CLIENT_TYPE) -> dict:
    """get qrcode login unikey (pc client api).

    - scan url is built by getLoginQRCodeUrl.
    - requires netease cloud music mobile app to scan.
    - login status must be polled via loginQrcodeCheck.

    Args:
        dtype: qrcode client type. 5 is the windows client default.

    Returns:
        dict
    """
    return eapi(
        '/api/login/qrcode/unikey',
        {
            'type': str(dtype),
        },
    )


def loginQrcodeCheck(unikey, type=QRCODE_CLIENT_TYPE) -> dict:
    """check qrcode login status (pc client api).

    Args:
        unikey: qrcode unikey.
        type: qrcode client type. 5 is the windows client default.

    Returns:
        dict
    """
    return eapi(
        '/api/login/qrcode/client/login',
        {
            'type': type,
            'key': str(unikey),
        },
    )


def getCurrentLoginStatus() -> dict:
    """get current login status (pc client api)."""
    return eapi('/api/w/nuser/account/get', {})


def loginViaCookie(MUSIC_U='', **kwargs) -> dict:
    """login via cookie.

    Args:
        MUSIC_U: cookie value. defaults to ''.

    Returns:
        dict
    """
    session = getCurrentSession()
    session.cookies.update({'MUSIC_U': MUSIC_U, **kwargs})
    resp = getCurrentLoginStatus()
    writeLoginInfo(resp)
    return {'code': 200, 'result': session.login_info}


def loginViaCellphone(
    phone='',
    password='',
    passwordHash='',
    captcha='',
    ctcode=86,
    remeberLogin=True,
    session=None,
) -> dict:
    """login via phone number (pc client api).

    if both password and passwordHash provided, password takes precedence.
    if both captcha and password provided, captcha takes precedence.

    Args:
        phone: phone number.
        ctcode: country code. defaults to 86.
        remeberLogin: auto-login flag. false may cause permission issues.
        password: plaintext password.
        passwordHash: md5 password hash.
        captcha: sms code. requires prior setSendRegisterVerificationCodeViaCellphone.

    Raises:
        LoginFailedException: on login failure.

    Returns:
        dict
    """
    path = '/api/w/login/cellphone'
    session = session or getCurrentSession()
    if password:
        passwordHash = _hash_hex_digest(password)

    if not (passwordHash or captcha):
        raise LoginFailedException('no password or captcha provided')

    auth_token = (
        {'password': str(passwordHash)} if not captcha else {'captcha': str(captcha)}
    )

    login_status = eapi(
        path,
        {
            'type': '1',
            'phone': str(phone),
            'remember': str(remeberLogin).lower(),
            'countrycode': str(ctcode),
            'checkToken': '',
            **auth_token,
        },
        session=session,
    )

    writeLoginInfo(login_status)
    return {'code': 200, 'result': session.login_info}


def getLoginQRCodeUrl(unikey: str) -> str:
    """build the pc client scan login url from unikey.

    Args:
        unikey: from loginQrcodeUnikey.

    Returns:
        str: qrcode url
    """
    chain_id = 'v1_%s_%s_login_%d' % (
        getCurrentSession().deviceId,
        CLIENT_OS,
        int(time() * 1000),
    )
    return '%s?%s' % (
        QRCODE_LOGIN_URL,
        urlencode(
            {
                'codekey': unikey,
                'chainId': chain_id,
                'hdw_device': CLIENT_OS,
                'hdw_appid': CLIENT_OS,
                'hitExp': '1',
            }
        ),
    )


def setSendRegisterVerificationCodeViaCellphone(cell: str, ctcode=86) -> dict:
    """send sms verification code (pc client api). max 5 times per 24h.

    Args:
        cell: phone number.
        ctcode: country code. defaults to 86.

    Returns:
        dict
    """
    return eapi(
        '/api/sms/captcha/sent',
        {
            'cellphone': str(cell),
            'ctcode': ctcode,
        },
    )


def getRegisterVerificationStatusViaCellphone(
    cell: str, captcha: str, ctcode=86
) -> dict:
    """check sms code correctness (pc client api).

    Args:
        cell: phone number.
        captcha: verification code.
        ctcode: country code. defaults to 86.

    Returns:
        dict
    """
    return eapi(
        '/api/sms/captcha/verify',
        {
            'cellphone': str(cell),
            'captcha': str(captcha),
            'ctcode': ctcode,
        },
    )



def loginViaAnonymousAccount(deviceId=None, session=None) -> dict:
    """anonymous login (pc client api).

    Args:
        deviceId: device id. defaults to session device id.

    Returns:
        dict
    """
    session = session or getCurrentSession()
    if not deviceId:
        deviceId = session.deviceId
    username = b64encode(
        ('%s %s' % (deviceId, cloudmusic_dll_encode_id(deviceId))).encode()
    ).decode()
    login_status = eapi(
        '/api/register/anonimous',
        {'username': username, 'nonce': str(uuid4())},
        session=session,
    )
    assert login_status['code'] == 200, 'anonymous login failed'
    writeLoginInfo(
        {
            **login_status,
            'profile': {'nickname': '', **login_status},
            'account': {'id': login_status['userId'], **login_status},
        }
    )
    return session.login_info
