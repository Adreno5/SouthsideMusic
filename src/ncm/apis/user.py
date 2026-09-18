from __future__ import annotations

from json import dumps

from . import eapi


def getUserDetail(user_id=0) -> dict:
    """get user detail (pc client api).

    Args:
        user_id: user id. defaults to 0.

    Returns:
        dict
    """
    return eapi('/api/v1/user/detail/%s' % user_id, {})


def getUserPlaylists(user_id, offset=0, limit=1001) -> dict:
    """get user's playlists (pc client api).

    Args:
        user_id: user id. defaults to 0.
        offset: offset. defaults to 0.
        limit: page size. defaults to 1001.

    Returns:
        dict
    """
    return eapi(
        '/api/user/playlist',
        {
            'offset': str(offset),
            'limit': str(limit),
            'uid': str(user_id),
            'includeVideo': 'true',
        },
    )


def getUserAlbumSubs(limit=30) -> dict:
    """get user's subscribed albums (pc client api).

    Args:
        limit: page size. defaults to 30.

    Returns:
        dict
    """
    return eapi('/api/album/sublist', {'limit': str(limit)})


def getUserArtistSubs(limit=30) -> dict:
    """get user's subscribed artists (pc client api).

    Args:
        limit: page size. defaults to 30.

    Returns:
        dict
    """
    return eapi('/api/artist/sublist', {'limit': str(limit)})


SIGNIN_TYPE_MOBILE = 0
"""mobile daily check-in, +4 exp"""
SIGNIN_TYPE_WEB = 1
"""web daily check-in, +1 exp"""


def setSignin(dtype=0) -> dict:
    """daily check-in (pc client api).

    Args:
        dtype: SIGNIN_TYPE_MOBILE or SIGNIN_TYPE_WEB. defaults to mobile.

    Returns:
        dict
    """
    return eapi('/api/point/dailyTask', {'type': str(dtype)})


def setWeblog(log: dict) -> dict:
    """send user behavior log (pc client api).

    Args:
        logs: operation record dict.

    Returns:
        dict
    """
    return eapi('/api/feedback/weblog', {'logs': dumps([log])})


def getDailyRecommend() -> dict:
    """get daily recommend songs (pc client api).

    Returns:
        dict
    """
    return eapi('/api/v1/discovery/recommend/songs', {})


def getDailyRecommendResource() -> dict:
    """get daily recommend playlists (pc client api).

    Returns:
        dict
    """
    return eapi('/api/v1/discovery/recommend/resource', {})
