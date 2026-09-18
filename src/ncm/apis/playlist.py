from __future__ import annotations

import json

from . import eapi


def getPlaylistInfo(playlist_id, offset=0, total=True, limit=1000) -> dict:
    """get playlist detail (pc client api).

    shows playlist name but not full tracks in one call.
    use getPlaylistAllTracks for complete track list.

    Args:
        playlist_id: playlist id.
        offset: unused.
        total: unused.
        limit: unused.

    Returns:
        dict
    """
    return eapi(
        '/api/v6/playlist/detail',
        {
            'id': str(playlist_id),
            'n': str(limit),
            's': '8',
        },
    )


def getPlaylistInfoEapi(playlist_id, n=100000, s=8) -> dict:
    """get playlist detail (pc client api)."""
    return eapi(
        '/api/v6/playlist/detail',
        {
            'id': str(playlist_id),
            'n': str(n),
            's': str(s),
            'newStyle': 'true',
        },
    )


def getPlaylistAllTracks(playlist_id, offset=0, limit=1000) -> dict:
    """get all tracks from a playlist.

    Args:
        playlist_id: playlist id.
        offset: offset. defaults to 0.
        limit: page size. defaults to 1000.

    Returns:
        dict
    """
    data = getPlaylistInfo(playlist_id, offset, True, limit)
    trackIds = [track['id'] for track in data['playlist']['trackIds']]
    id = trackIds[offset : offset + limit]
    from .track import getTrackDetail

    return getTrackDetail(id)


def setManipulatePlaylistTracks(
    trackIds, playlistId, op='add', imme=True, e_r=True
) -> dict:
    """add/delete tracks in a playlist (pc client api).

    Args:
        trackIds: track ids to operate on.
        playlistId: playlist id.
        op: 'add' or 'del'. defaults to 'add'.
        imme: unknown. defaults to true.

    Returns:
        dict
    """
    trackIds = trackIds if isinstance(trackIds, list) else [trackIds]
    return eapi(
        '/api/v1/playlist/manipulate/tracks',
        {
            'trackIds': json.dumps(trackIds),
            'pid': str(playlistId),
            'op': op,
            'imme': str(imme).lower(),
        },
    )


def setCreatePlaylist(name: str, privacy=False) -> dict:
    """create a new playlist (pc client api).

    Args:
        name: playlist name.
        privacy: whether to make it private. defaults to false.

    Returns:
        dict
    """
    return eapi(
        '/api/playlist/create',
        {
            'name': str(name),
            'privacy': str(1 if privacy else 0),
        },
    )


def setRemovePlaylist(ids: list, self=True) -> dict:
    """delete playlist (pc client api).

    Args:
        ids: playlist ids.
        self: unknown. defaults to true.

    Returns:
        dict
    """
    ids = ids if isinstance(ids, list) else [ids]
    return eapi(
        '/api/playlist/delete',
        {
            'ids': json.dumps(ids),
            'self': str(self),
        },
    )
