from __future__ import annotations

from json import dumps
import json

from . import weapi


def getUserDetail(user_id=0) -> dict:
    """get user detail (web api).

    Args:
        user_id: user id. defaults to 0.

    Returns:
        dict
    """
    return weapi('/weapi/v1/user/detail/%s' % user_id, {})


def getUserPlaylists(user_id, offset=0, limit=1001) -> dict:
    """get user's playlists (web api).

    Args:
        user_id: user id. defaults to 0.
        offset: offset. defaults to 0.
        limit: page size. defaults to 1001.

    Returns:
        dict
    """
    return weapi(
        '/weapi/user/playlist',
        {
            'offset': str(offset),
            'limit': str(limit),
            'uid': str(user_id),
        },
    )


def getUserAlbumSubs(limit=30) -> dict:
    """get user's subscribed albums (web api).

    Args:
        limit: page size. defaults to 30.

    Returns:
        dict
    """
    return weapi('/weapi/album/sublist', {'limit': str(limit)})


def getUserArtistSubs(limit=30) -> dict:
    """get user's subscribed artists (web api).

    Args:
        limit: page size. defaults to 30.

    Returns:
        dict
    """
    return weapi('/weapi/artist/sublist', {'limit': str(limit)})


SIGNIN_TYPE_MOBILE = 0
"""mobile daily check-in, +4 exp"""
SIGNIN_TYPE_WEB = 1
"""web daily check-in, +1 exp"""


def setSignin(dtype=0) -> dict:
    """daily check-in (mobile/pc api).

    Args:
        dtype: SIGNIN_TYPE_MOBILE or SIGNIN_TYPE_WEB. defaults to mobile.

    Returns:
        dict
    """
    return weapi('/weapi/point/dailyTask', {'type': str(dtype)})


def setWeblog(log: dict) -> dict:
    """send user behavior log (mobile/pc api).

    Args:
        logs: operation record dict.

    Returns:
        dict
    """
    return weapi('/weapi/feedback/weblog', {'logs': dumps([log])})


def getDailyRecommend():
    """get daily recommend songs (web api).

    Returns:
        dict
    """
    return weapi('/weapi/v2/discovery/recommend/songs', {})


def getDailyRecommendResource() -> dict:
    """get daily recommend playlists (web api).

    Returns:
        dict
    """
    return weapi('/weapi/v1/discovery/recommend/resource', {})
