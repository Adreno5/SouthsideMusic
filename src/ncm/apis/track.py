from __future__ import annotations

import json

from . import eapi, getCurrentSession

LEVEL_BY_BITRATE = {
    64000: '64aac',
    96000: 'standard',
    128000: 'standard',
    192000: 'higher',
    320000: 'exhigh',
    999000: 'lossless',
    1900000: 'hires',
    1999000: 'hires',
    2999000: 'dolby',
    3999000: 'jyeffect',
    4999000: 'jymaster',
    5999000: 'sky',
    6999000: 'vivid',
}


def getTrackDetail(song_ids: list) -> dict:
    """get track detail (pc client api).

    Args:
        song_ids: track ids, up to 1000 per call.

    Returns:
        dict
    """
    ids = song_ids if isinstance(song_ids, list) else [song_ids]
    return eapi(
        '/api/v3/song/detail',
        {
            'c': json.dumps([{'id': str(id), 'v': 0} for id in ids]),
            'trialMode': '-1',
        },
    )


def getTrackAudio(
    song_ids: list[int | str] | int | str,
    bitrate: int = 320000,
    encodeType: str = 'aac',
) -> dict:
    ids = song_ids if isinstance(song_ids, list) else [song_ids]
    return getTrackAudioV1(
        ids,
        level=LEVEL_BY_BITRATE.get(int(bitrate), 'exhigh'),
        encodeType=encodeType,
    )


def getTrackAudioV1(
    song_ids: list[int | str] | int | str,
    level: str = 'standard',
    encodeType: str = 'flac',
    immerseType: str = 'c51',
    trialMode: int = -1,
) -> dict:
    ids = song_ids if isinstance(song_ids, list) else [song_ids]
    return eapi(
        '/api/song/enhance/player/url/v1',
        {
            'ids': ids,
            'encodeType': str(encodeType),
            'level': str(level),
            'immerseType': immerseType,
            'trialMode': trialMode,
        },
    )


def getTrackAudioDownload(
    song_id: int | str,
    level: str = 'standard',
    immerseType: str = 'c51',
) -> dict:
    return eapi(
        '/api/song/enhance/download/url/v1',
        {'id': str(song_id), 'level': level, 'immerseType': immerseType},
    )


def getTrackQuality(song_id: int | str, immerseType: str = 'c51') -> dict:
    return eapi(
        '/api/song/music/detail/get',
        {'songId': str(song_id), 'immerseType': immerseType},
    )


def getTrackPrivilege(
    song_ids: list[int | str] | int | str, trialMode: int | None = None
) -> dict:
    ids = song_ids if isinstance(song_ids, list) else [song_ids]
    data: dict = {'ids': json.dumps(ids)}
    if trialMode is not None:
        data['trialMode'] = trialMode
    return eapi('/api/song/enhance/privilege', data)


def getTrackLyricsNew(song_id: str) -> dict:
    """get track lyrics v2 with word-by-word lines (pc client api).

    Args:
        song_id: track id.

    Returns:
        dict
    """
    return eapi(
        '/api/song/lyric/v1',
        {
            'id': str(song_id),
            'cp': True,
            'lv': 1,
            'tv': 1,
            'rv': 1,
            'kv': 1,
            'yv': 1,
            'ytv': 1,
            'yrv': 1,
        },
    )


def getComments(id: str, offset: int = 0, limit: int = 20) -> dict:
    """get comments of a song (pc client api).

    Args:
        id: song id
        offset: comment offset
        limit: page size

    Returns:
        dict
    """
    return eapi(
        '/api/v1/resource/comments/R_SO_4_%s' % id,
        {
            'rid': str(id),
            'offset': str(offset),
            'total': 'true',
            'limit': str(limit),
            'beforeTime': '0',
        },
    )


def addComment(id: str, content: str) -> dict:
    """add a comment for a song (pc client api).

    Args:
        id: song id
        content: comment content

    Returns:
        dict
    """
    return eapi(
        '/api/resource/comments/add',
        {
            'checkToken': getCurrentSession().cookies.get(
                'WM_NIKE', 'not logged in!!!!!'
            ),
            'content': content,
            'threadId': f'R_SO_4_{id}',
        },
    )
