from __future__ import annotations

import json

from .. import eapi


def getSportsFMRecommendations(limit=3, bpm: int = 50, e_r=True) -> dict:
    """get sports fm track recommendations (mobile).

    Args:
        limit: count. defaults to 3.
        bpm: steps per minute. defaults to 50.
        e_r: unknown. defaults to true.

    Returns:
        dict
    """
    return eapi(
        '/eapi/radio/sport/get',
        {
            'limit': str(limit),
            'bpm': str(bpm),
            'e_r': str(e_r).lower(),
        },
    )


def getCalculatedSportsFMStatus(
    distance=0, maxbpm=0, time=0, songList=None, steps=0, bpm=0, e_r=True
) -> dict:
    """calculate sports fm status (mobile).

    Args:
        distance: distance. defaults to 0.
        maxbpm: max bpm. defaults to 0.
        time: duration in seconds. defaults to 0.
        songList: played song list. defaults to None.
        steps: steps taken. defaults to 0.
        bpm: ending bpm. defaults to 0.
        e_r: unknown. defaults to true.

    Returns:
        dict
    """
    return eapi(
        '/eapi/radio/sport/calculate',
        {
            'distance': str(distance),
            'maxbpm': str(maxbpm),
            'time': str(time),
            'songList': json.dumps(songList or []),
            'steps': str(steps),
            'bpm': str(bpm),
            'e_r': str(e_r).lower(),
        },
    )
