from __future__ import annotations

import json
from random import randrange

from typing import Any

from .. import API_HOST, getCurrentSession
from ..utils.crypto import _eapi_decrypt, _eapi_encrypt
from .exception import LoginRequiredException


LOGIN_REQUIRED = LoginRequiredException('login required')


def eapi(path, data, session=None, method='POST') -> Any:
    """eapi request (windows desktop client APIs)."""
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
    digest = _eapi_encrypt(path, json.dumps(payload))
    rsp = session.request(
        method,
        'https://%s/eapi/%s' % (API_HOST, path[5:]),
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
    playmode as playmode,
    playlist as playlist,
    radio as radio,
    recommend as recommend,
    track as track,
    user as user,
    video as video,
)
