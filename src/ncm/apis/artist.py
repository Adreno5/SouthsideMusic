from __future__ import annotations

from . import eapi


def getArtistAlbums(artist_id: str, offset=0, total=True, limit=1000) -> dict:
    """get artist's albums (pc client api).

    Args:
        artist_id: artist id.
        offset: offset. defaults to 0.
        total: fetch all. defaults to true.
        limit: page size. defaults to 1000.

    Returns:
        dict
    """
    return eapi(
        '/api/artist/albums/%s' % artist_id,
        {
            'offset': str(offset),
            'total': str(total).lower(),
            'limit': str(limit),
        },
    )


def getArtistTracks(
    artist_id: str, offset=0, total=True, limit=1000, order='hot'
) -> dict:
    """get artist's tracks sorted by order (pc client api).

    Args:
        artist_id: artist id.
        offset: offset. defaults to 0.
        total: fetch all. defaults to true.
        limit: page size. defaults to 1000.
        order: 'hot' (popularity) or 'time' (newest). defaults to 'hot'.

    Returns:
        dict
    """
    return eapi(
        '/api/v1/artist/songs',
        {
            'id': str(artist_id),
            'offset': str(offset),
            'total': str(total).lower(),
            'limit': str(limit),
            'order': str(order),
        },
    )


def getArtistDetails(artist_id: str) -> dict:
    """get artist detail (pc client api).

    Args:
        artist_id: artist id.

    Returns:
        dict
    """
    return eapi('/api/artist/head/info/get', {'id': str(artist_id)})
