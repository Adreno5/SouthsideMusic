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
    return eapi('/api/w/v1/user/detail/%s' % user_id, {'all': 'true', 'userId': str(user_id)})


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
    return eapi('/api/v3/discovery/recommend/songs', {'limit': '30'})


def getDailyRecommendResource() -> dict:
    """get daily recommend playlists (pc client api).

    Returns:
        dict
    """
    return eapi('/api/v1/discovery/recommend/resource', {})
