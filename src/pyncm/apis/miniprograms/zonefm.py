from __future__ import annotations

from .. import eapi


def getFmZoneInfo(limit=3, zone='CLASSICAL', e_r=True) -> dict:
    """get zone fm content.

    known zones: CLASSICAL.

    Args:
        limit: count. defaults to 3.
        zone: zone name. defaults to 'CLASSICAL'.
        e_r: unknown. defaults to true.

    Returns:
        dict
    """
    return eapi(
        '/eapi/zone/fm/get',
        {
            'limit': str(limit),
            'zone': zone,
            'e_r': str(e_r).lower(),
        },
    )


def setSkipFmTrack(songId, zone='CLASSICAL', e_r=True) -> dict:
    """skip zone fm track.

    Args:
        songId: track id.
        zone: zone name. defaults to 'CLASSICAL'.
        e_r: unknown. defaults to true.

    Returns:
        dict
    """
    return eapi(
        '/eapi/zone/fm/skip',
        {
            'songId': str(songId),
            'zone': zone,
            'e_r': str(e_r).lower(),
            'alg': 'CLSalternate',
            'time': '0',
        },
    )
