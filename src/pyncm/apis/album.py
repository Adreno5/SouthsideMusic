from __future__ import annotations

from . import weapi


def getAlbumInfo(album_id: str) -> dict:
    """get album info (web api).

    Args:
        album_id: album id.

    Returns:
        dict
    """
    return weapi('/weapi/v1/album/%s' % album_id, {})


def getAlbumComments(album_id: str, offset=0, limit=20, beforeTime=0) -> dict:
    """get album comments (web api).

    Args:
        album_id: album id.
        offset: time offset. defaults to 0.
        limit: page size. defaults to 20.
        beforeTime: timestamp in seconds. defaults to 0.

    Returns:
        dict
    """
    return weapi(
        '/weapi/v1/resource/comments/R_AL_3_%s' % album_id,
        {
            'rid': 'R_AL_3_%s' % album_id,
            'offset': str(offset),
            'total': 'true',
            'limit': str(limit),
            'beforeTime': str(beforeTime * 1000),
        },
    )
