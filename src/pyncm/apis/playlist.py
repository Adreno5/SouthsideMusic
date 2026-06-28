from __future__ import annotations

import json

from . import eapi, weapi


def getPlaylistInfo(playlist_id, offset=0, total=True, limit=1000) -> dict:
    """get playlist detail (web api).

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
    return weapi(
        '/weapi/v6/playlist/detail',
        {
            'id': str(playlist_id),
            'offset': str(offset),
            'total': str(total).lower(),
            'limit': str(limit),
            'n': str(limit),
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


def getPlaylistComments(playlist_id: str, offset=0, limit=20, beforeTime=0) -> dict:
    """get playlist comments (web api).

    Args:
        playlist_id: playlist id.
        offset: time offset. defaults to 0.
        limit: page size. defaults to 20.
        beforeTime: timestamp in seconds. defaults to 0.

    Returns:
        dict
    """
    return weapi(
        '/v1/resource/comments/A_PL_0_%s' % playlist_id,
        {
            'rid': str(playlist_id),
            'limit': str(limit),
            'offset': str(offset),
            'beforeTime': str(beforeTime * 1000),
        },
    )


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
    return weapi(
        '/weapi/v1/playlist/manipulate/tracks',
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
        '/eapi/playlist/create',
        {
            'name': str(name),
            'privacy': str(1 if privacy else 0),
        },
    )


def setRemovePlaylist(ids: list, self=True) -> dict:
    """delete playlist (mobile api).

    Args:
        ids: playlist ids.
        self: unknown. defaults to true.

    Returns:
        dict
    """
    ids = ids if isinstance(ids, list) else [ids]
    return eapi(
        '/eapi/playlist/remove',
        {
            'ids': str(ids),
            'self': str(self),
        },
    )
