from __future__ import annotations

import json

from . import eapi, getCurrentSession

LEVEL_BY_BITRATE = {
    96000: 'standard',
    128000: 'higher',
    192000: 'exhigh',
    320000: 'exhigh',
    999000: 'lossless',
    1900000: 'hires',
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


def getTrackAudio(song_ids: list, bitrate=320000, encodeType='aac') -> dict:
    """get track audio urls (pc client api).

    Args:
        song_ids: track ids, up to 1000 per call.
        bitrate: 96k/320k/320k+ lossless/sq. defaults to 320000.
        encodeType: 'aac' etc. ignored at high bitrate.

    Returns:
        dict
    """
    ids = song_ids if isinstance(song_ids, list) else [song_ids]
    return getTrackAudioV1(
        ids,
        level=LEVEL_BY_BITRATE.get(int(bitrate), 'exhigh'),
        encodeType=encodeType,
    )


def getTrackAudioV1(song_ids: list, level='standard', encodeType='flac') -> dict:
    """get track audio urls v1 (pc client api).

    Args:
        song_ids: track ids, up to 1000 per call.
        level: 'standard' / 'exhigh' / 'lossless' / 'hires'.
        encodeType: defaults to 'flac'. ignored at high level.

    Returns:
        dict
    """
    ids = song_ids if isinstance(song_ids, list) else [song_ids]
    return eapi(
        '/api/song/enhance/player/url/v1',
        {
            'ids': ids,
            'encodeType': str(encodeType),
            'level': str(level),
        },
    )


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
            'cp': False,
            'lv': 0,
            'tv': 0,
            'rv': 0,
            'kv': 0,
            'yv': 0,
            'ytv': 0,
            'yrv': 0,
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
