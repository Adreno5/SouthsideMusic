from __future__ import annotations


from . import eapi


def getIntelligenceList(
    song_id: int | str,
    playlist_id: int | str,
    start_music_id: int | str | None = None,
    count: int = 20,
) -> dict:
    """Get intelligent heart-mode recommendations."""
    seed_id = str(start_music_id or song_id)
    return eapi(
        '/api/playmode/intelligence/list',
        {
            'songId': str(song_id),
            'playlistId': str(playlist_id),
            'startMusicId': seed_id,
            'type': 'fromPlayOne',
            'count': str(count),
        },
    )
