from __future__ import annotations

from base64 import b64encode

from . import eapi, weapi
from .exception import LoginFailedException
from .. import writeLoginInfo, getCurrentSession
from ..utils import _generate_s_device_id, _generate_chain_id
from ..utils.crypto import _hash_hex_digest
from ..utils.security import cloudmusic_dll_encode_id


def loginLogout() -> dict:
    """log out current session."""
    return weapi('/weapi/logout', {})


def loginRefreshToken() -> dict:
    """refresh login token."""
    return eapi('/eapi/login/token/refresh', {})


def loginQrcodeUnikey(dtype=1) -> dict:
    """get qrcode login unikey.

    - uuid applies to: music.163.com/login?codekey={uuid}
    - requires netease cloud music mobile app to scan.
    - login status must be polled via loginQrcodeCheck.

    Args:
        dtype: unknown. defaults to 1.
        noCheckToken: skip token check. defaults to true.

    Returns:
        dict
    """
    return eapi(
        '/eapi/login/qrcode/unikey',
        {
            'type': str(dtype),
            'noCheckToken': True,
        },
    )


def loginQrcodeCheck(unikey, type=1) -> dict:
    """check qrcode login status.

    Args:
        unikey: qrcode unikey.
        type: unknown. defaults to 1.
        noCheckToken: skip token check. defaults to true.

    Returns:
        dict
    """
    return eapi(
        '/eapi/login/qrcode/client/login',
        {
            'type': type,
            'noCheckToken': True,
            'key': str(unikey),
        },
    )


def loginTypeSwitch() -> dict:
    """switch login type."""
    return weapi('/weapi/logout', {})


def getCurrentLoginStatus() -> dict:
    """get current login status (web api)."""
    return weapi('/weapi/w/nuser/account/get', {})


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
    path = '/eapi/w/login/cellphone'
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


def loginViaEmail(
    email='', password='', passwordHash='', remeberLogin=True, session=None
) -> dict:
    """login via email (web api).

    if both password and passwordHash provided, password takes precedence.

    Args:
        email: email address.
        remeberLogin: auto-login flag.
        password: plaintext password.
        passwordHash: md5 password hash.

    Raises:
        LoginFailedException: on login failure.

    Returns:
        dict
    """
    path = '/eapi/login'
    session = session or getCurrentSession()
    if password:
        passwordHash = _hash_hex_digest(password)

    if not passwordHash:
        raise LoginFailedException('no password provided')

    login_status = eapi(
        path,
        {
            'type': '1',
            'username': str(email),
            'remember': str(remeberLogin).lower(),
            'password': str(passwordHash),
        },
        session=session,
    )

    writeLoginInfo(login_status)
    return {'code': 200, 'result': session.login_info}


def getLoginQRCodeUrl(unikey: str) -> str:
    """build qrcode login url from unikey.

    Args:
        unikey: from loginQrcodeUnikey.

    Returns:
        str: qrcode url
    """
    s_device_id = getCurrentSession().cookies.get('sDeviceId')
    if not s_device_id:
        s_device_id = _generate_s_device_id()
    chain_id = _generate_chain_id(s_device_id)
    return f'http://music.163.com/login?codekey={unikey}&chainId={chain_id}'


def setSendRegisterVerificationCodeViaCellphone(cell: str, ctcode=86) -> dict:
    """send sms verification code (web api). max 5 times per 24h.

    Args:
        cell: phone number.
        ctcode: country code. defaults to 86.

    Returns:
        dict
    """
    return weapi(
        '/weapi/sms/captcha/sent',
        {
            'cellphone': str(cell),
            'ctcode': ctcode,
        },
    )


def getRegisterVerificationStatusViaCellphone(
    cell: str, captcha: str, ctcode=86
) -> dict:
    """check sms code correctness (web api).

    Args:
        cell: phone number.
        captcha: verification code.
        ctcode: country code. defaults to 86.

    Returns:
        dict
    """
    return weapi(
        '/weapi/sms/captcha/verify',
        {
            'cellphone': str(cell),
            'captcha': str(captcha),
            'ctcode': ctcode,
        },
    )


def setRegisterAccountViaCellphone(
    cell: str, captcha: str, nickname: str, password: str
) -> dict:
    """register via phone number (web api).

    requires prior setSendRegisterVerificationCodeViaCellphone.
    also used for password reset.
    current session logs into the new account on success.

    Args:
        cell: phone number.
        captcha: verification code.
        nickname: display name.
        password: password.

    Returns:
        dict
    """
    return weapi(
        '/weapi/w/register/cellphone',
        {
            'captcha': str(captcha),
            'nickname': str(nickname),
            'password': _hash_hex_digest(password),
            'phone': str(cell),
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
    login_status = weapi(
        '/api/register/anonimous', {'username': username}, session=session
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


def checkIsCellphoneRegistered(cell: str, prefix=86) -> dict:
    """check if a phone number is registered (mobile api).

    Args:
        cell: phone number.
        prefix: country code. defaults to 86.

    Returns:
        dict
    """
    return eapi(
        '/eapi/cellphone/existence/check',
        {
            'cellphone': cell,
            'countrycode': prefix,
        },
    )
