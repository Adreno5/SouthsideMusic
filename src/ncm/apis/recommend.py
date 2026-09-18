from __future__ import annotations

from . import eapi


def getPcRecommendResource() -> dict:
    """Get PC homepage recommendation resources."""
    return eapi('/api/pc/page/rcmd/resource/show', {})


def getSimilarSongs(song_id: int | str, limit: int = 50, offset: int = 0) -> dict:
    """Get songs similar to a seed song."""
    return eapi(
        '/api/v1/discovery/simiSong',
        {
            'songid': str(song_id),
            'limit': str(limit),
            'offset': str(offset),
        },
    )
