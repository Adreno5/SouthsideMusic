from __future__ import annotations

import json
import urllib.parse
from random import randrange

from typing import Any

from requests.models import Response

from .. import getCurrentSession, logger
from ..utils.crypto import _eapi_decrypt, _eapi_encrypt, _weapi_encrypt, _abroad_decrypt
from .exception import LoginRequiredException


LOGIN_REQUIRED = LoginRequiredException('login required')


def _parse_response(rsp):
    try:
        text = rsp.text if isinstance(rsp, Response) else rsp
        text = text.decode() if not isinstance(text, str) else text
        payload = json.loads(text.strip('\x10'))
        if 'abroad' in payload and payload['abroad']:
            logger.warning('detected abroad payload, response format may differ')
            real_payload = _abroad_decrypt(payload['result'])
            payload = {'result': json.loads(real_payload)}
            payload['abroad'] = True
        return payload
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        try:
            content = rsp.content if isinstance(rsp, Response) else rsp
            return json.loads(content.decode())
        except Exception:
            pass
        logger.error('response is not valid json: %s', e)
        logger.error('response: %s', rsp)
        return rsp


def weapi(url, data, session=None, method='POST') -> Any:
    """weapi request (web/miniprogram/mobile APIs)."""
    session = session or getCurrentSession()
    payload = json.dumps({**data, 'csrf_token': session.csrf_token})
    encrypted = _weapi_encrypt(payload)
    rsp = session.request(
        method,
        url.replace('/api/', '/weapi/'),
        params={'csrf_token': session.csrf_token},
        data=encrypted,
        headers={'User-Agent': session.UA_DEFAULT, 'Referer': 'https://music.163.com'},
        cookies={**session.eapi_config},
    )
    return _parse_response(rsp)


def eapi(url, data, session=None, method='POST') -> Any:
    """eapi request (desktop client APIs)."""
    session = session or getCurrentSession()
    payload = {
        **data,
        'header': json.dumps(
            {
                **session.eapi_config,
                'requestId': str(randrange(20000000, 30000000)),
            }
        ),
    }
    api_path = urllib.parse.urlparse(url).path.replace('/eapi/', '/api/')
    digest = _eapi_encrypt(api_path, json.dumps(payload))
    rsp = session.request(
        method,
        url,
        headers={'User-Agent': session.UA_EAPI, 'Referer': ''},
        cookies={**session.eapi_config},
        data={**digest},
    )
    content = rsp.content
    try:
        decrypted = bytes(_eapi_decrypt(content)).decode()
        return json.loads(decrypted.strip('\x10'))
    except Exception:
        try:
            return json.loads(content.decode())
        except Exception:
            pass
        return content


from . import (  # noqa: E402
    artist as artist,
    miniprograms as miniprograms,
    album as album,
    cloud as cloud,
    cloudsearch as cloudsearch,
    login as login,
    playlist as playlist,
    track as track,
    user as user,
    video as video,
)
