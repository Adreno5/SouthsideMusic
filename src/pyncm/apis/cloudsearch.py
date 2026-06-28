from __future__ import annotations

from . import eapi

SONG = 1
ALBUM = 10
ARTIST = 100
PLAYLIST = 1000
USER = 1002
MV = 1004
LYRICS = 1006
DJ = 1009
VIDEO = 1014


def getSearchResult(keyword: str, stype=SONG, limit=30, offset=0) -> dict:
    """search (pc client api).

    Args:
        keyword: search query.
        stype: cloudsearch.SONG / ALBUM / ARTIST / PLAYLIST / USER / MV / LYRICS / DJ / VIDEO.
        limit: page size. defaults to 30.
        offset: offset. defaults to 0.

    Returns:
        dict
    """
    return eapi(
        '/eapi/cloudsearch/pc',
        {
            's': str(keyword),
            'type': str(stype),
            'limit': str(limit),
            'offset': str(offset),
        },
    )
