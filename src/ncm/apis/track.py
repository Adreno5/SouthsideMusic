from __future__ import annotations

import json

from . import eapi, getCurrentSession
from ..utils import _random_string


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
            'c': json.dumps([{'id': str(id)} for id in ids]),
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
    return eapi(
        '/api/song/enhance/player/url',
        {
            'ids': ids,
            'encodeType': str(encodeType),
            'br': str(bitrate),
        },
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


def getTrackDownloadURL(song_id: int | str, bitrate=999000) -> dict:
    """get download url (pc client api).

    Args:
        song_id: track id.
        bitrate: defaults to 999000.

    Returns:
        dict
    """
    return eapi(
        '/api/song/enhance/download/url',
        {
            'id': str(song_id),
            'br': str(bitrate),
        },
    )


def getTrackDownloadURLV1(song_id: int, level='standard') -> dict:
    """get download url v1 (pc client api).

    Args:
        song_id: track id.
        level: 'standard' / 'exhigh' / 'lossless' / 'hires'.

    Returns:
        dict
    """
    return eapi(
        '/api/song/enhance/download/url/v1',
        {
            'id': '%s_0' % song_id,
            'level': str(level),
        },
    )


def getTrackLyrics(song_id: int, lv=-1, tv=-1, rv=-1) -> dict:
    """get track lyrics (pc client api). pass -1 for latest version.

    Args:
        song_id: track id.
        lv: lyric version, -1 for latest.
        tv: translation version, -1 for latest.
        rv: romanization version, -1 for latest.

    Returns:
        dict
    """
    return eapi(
        '/api/song/lyric',
        {
            'id': str(song_id),
            'lv': str(lv),
            'tv': str(tv),
            'rv': str(rv),
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


def setLikeTrack(trackId, like=True, userid=0, e_r=True) -> dict:
    """like/unlike a track (pc client api).

    Args:
        trackId: track id.
        like: true to like, false to unlike. defaults to true.
        userid: defaults to 0.

    Returns:
        dict
    """
    return eapi(
        '/api/song/like',
        {
            'trackId': str(trackId),
            'userid': str(userid),
            'like': str(like).lower(),
            'e_r': str(e_r).lower(),
        },
    )


DEFAULT_AUDIO_MATCHER_SESSION_ID = _random_string(16)


def getMatchTrackByFP(
    audioFP: str, duration: float, sessionId=DEFAULT_AUDIO_MATCHER_SESSION_ID
) -> dict:
    """audio fingerprint matching (pc client api).

    Args:
        audioFP: base64-encoded afp. see https://github.com/mos9527/ncm-afp
        duration: fp duration in seconds.
        sessionId: defaults to random.

    Returns:
        dict
    """
    return eapi(
        '/api/music/audio/match',
        {
            'algorithmCode': 'shazam_v2',
            'sessionId': sessionId,
            'duration': float(duration),
            'from': 'recognize-song',
            'times': '1',
            'decrypt': '1',
            'rawdata': audioFP,
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
