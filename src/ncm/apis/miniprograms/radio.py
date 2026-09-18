from __future__ import annotations

from .. import eapi


def getMoreRadioContent(limit=3, e_r=True) -> dict:
    """fetch more fm content (pc client).

    Args:
        limit: count. defaults to 3.
        e_r: unknown. defaults to true.

    Returns:
        dict
    """
    return eapi(
        '/api/v1/radio/get',
        {
            'limit': str(limit),
            'e_r': str(e_r).lower(),
        },
    )


def setSkipRadioContent(songId, time=0, alg='itembased', e_r=True) -> dict:
    """skip fm track (pc client).

    Args:
        songId: track id.
        time: playback duration. defaults to 0.
        alg: unknown. defaults to 'itembased'.

    Returns:
        dict
    """
    return eapi(
        '/api/v1/radio/get',
        {
            'e_r': str(e_r).lower(),
            'songId': str(songId),
            'alg': str(alg),
            'time': str(time),
        },
    )


def setLikeRadioContent(
    trackId, like=True, time='0', alg='itembased', e_r=True
) -> dict:
    """like/unlike fm track (pc client).

    Args:
        trackId: track id.
        like: like or unlike. defaults to true.
        time: playback duration. defaults to 0.
        alg: unknown. defaults to 'itembased'.

    Returns:
        dict
    """
    return eapi(
        '/api/v1/radio/like',
        {
            'e_r': str(e_r).lower(),
            'like': str(like).lower(),
            'trackId': str(trackId),
            'alg': str(alg),
            'time': str(time),
        },
    )


def setTrashRadioContent(songId, time='0', alg='itembased', e_r=True) -> dict:
    """trash fm track (pc client).

    Args:
        songId: track id.
        time: playback duration. defaults to 0.
        alg: unknown. defaults to 'itembased'.

    Returns:
        dict
    """
    return eapi(
        '/api/v1/radio/trash/add',
        {
            'e_r': str(e_r).lower(),
            'songId': str(songId),
            'alg': str(alg),
            'time': str(time),
        },
    )
