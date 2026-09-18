from __future__ import annotations

from . import eapi


def getMVDetail(mv_id: str) -> dict:
    """get mv detail (pc client api).

    Args:
        mv_id: mv id.

    Returns:
        dict
    """
    return eapi('/api/v1/mv/detail', {'id': str(mv_id)})


def getMVResource(mv_id: str, res=1080) -> dict:
    """get mv video/audio url (pc client api).

    Args:
        mv_id: mv id.
        res: 240 / 480 / 720 / 1080. defaults to 1080.

    Returns:
        dict
    """
    return eapi(
        '/api/song/enhance/play/mv/url',
        {
            'id': str(mv_id),
            'r': str(res),
        },
    )


def getMVComments(mv_id: str, offset=0, limit=20, total=False) -> dict:
    """get mv comments (pc client api).

    Args:
        mv_id: mv id.
        offset: offset. defaults to 0.
        limit: page size. defaults to 20.
        total: fetch all. defaults to false.

    Returns:
        dict
    """
    return eapi(
        '/api/v1/resource/comments/R_MV_5_%s' % mv_id,
        {
            'rid': 'R_MV_5_%s' % mv_id,
            'offset': str(offset),
            'total': str(total).lower(),
            'limit': str(limit),
        },
    )
