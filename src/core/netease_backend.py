from __future__ import annotations

import logging
from typing import Literal

import pyncm
from pyncm import apis

from core.models import (
    AlbumInfo,
    ArtistInfo,
    CloudFolderInfo,
    LocalFolderInfo,
    MusicServiceBackend,
    PrivilegeInfo,
    SearchSongInfo,
    SongStorable,
    TrackAudioInfo,
    TrackDetailInfo,
    TrackLyricsInfo,
    get_cached_hashes,
)

_logger = logging.getLogger(__name__)


class NeteaseCloudMusicBackend(MusicServiceBackend):
    def search(
        self, keywords: str, offset: int = 0, limit: int = 30
    ) -> list[SearchSongInfo]:
        resp = apis.cloudsearch.GetSearchResult(
            keywords, stype=1, limit=limit, offset=offset
        )
        assert isinstance(resp, dict), 'Invalid search response'

        songs: list[SearchSongInfo] = []
        for songdict in resp.get('result', {}).get('songs', []):
            artists = [
                ArtistInfo(
                    id=art.get('id', 0),
                    name=art.get('name', ''),
                    avatar_url='',
                )
                for art in songdict.get('ar', [])
            ]
            al = songdict.get('al', {})
            album = AlbumInfo(
                id=al.get('id', 0),
                name=al.get('name', ''),
                cover_url=al.get('picUrl', ''),
            )
            privilege_raw = songdict.get('privilege', {})
            privilege = PrivilegeInfo(
                fee=songdict.get('fee', 0),
                max_br=privilege_raw.get('maxbr', 0),
                is_vip_only=songdict.get('fee', 0) not in (0, 8),
            )
            songs.append(
                SearchSongInfo(
                    id=songdict['id'],
                    name=songdict['name'],
                    artists=artists,
                    album=album,
                    privilege=privilege,
                    duration=songdict.get('dt', 0),
                )
            )
        return songs

    def get_track_detail(self, track_id: int | str) -> TrackDetailInfo:
        response = apis.track.GetTrackDetail(song_ids=[track_id])
        assert isinstance(response, dict), 'Invalid track detail response'
        detail = response['songs'][0]  # type: ignore
        al = detail.get('al', {})
        return TrackDetailInfo(
            cover_url=al.get('picUrl', ''),
            album_name=al.get('name', ''),
            cd=detail.get('cd', '1'),
            track_no=detail.get('no', 1),
            publish_time=detail.get('publishTime', 0),
        )

    def get_track_audio(
        self, track_id: int | str, bitrate: int = 999000
    ) -> TrackAudioInfo:
        resp = apis.track.GetTrackAudio([str(track_id)], bitrate=bitrate)
        assert isinstance(resp, dict), 'Invalid track audio response'
        url = resp['data'][0]['url']  # type: ignore
        return TrackAudioInfo(url=url)

    def get_track_lyrics(self, track_id: int | str) -> TrackLyricsInfo:
        data = apis.track.GetTrackLyricsNew(str(track_id))
        assert isinstance(data, dict), 'Invalid track lyrics response'

        lyric = data.get('lrc', {}).get('lyric', '')

        tlyric = data.get('tlyric')
        if isinstance(tlyric, dict):
            translated_lyric = tlyric.get('lyric', '')
        else:
            translated_lyric = ''

        yrc_lyric = data.get('yrc', {}).get('lyric', '')
        ytlrc_lyric = data.get('ytlrc', {}).get('lyric', '')

        return TrackLyricsInfo(
            lyric=lyric,
            translated_lyric=translated_lyric,
            yrc_lyric=yrc_lyric,
            ytlrc_lyric=ytlrc_lyric,
        )

    def user_privilege_level(self) -> int:
        return pyncm.GetCurrentSession().vipType

    def user_anonymous(self) -> bool:
        return bool(pyncm.GetCurrentSession().is_anonymous)

    def get_user_playlists(self) -> list[CloudFolderInfo]:
        with pyncm.GetCurrentSession() as session:
            response = apis.user.GetUserPlaylists(session.uid)
            assert isinstance(response, dict), 'Invaild Response'
            assert not session.is_anonymous, 'Anonymous Account'

            data = response['playlist']  # type: ignore

            return [
                CloudFolderInfo(
                    folder_name=p['name'], image_url=p['coverImgUrl'], id=str(p['id'])
                )
                for p in data
            ]

    def create_playlist(self, name: str, privacy: bool) -> None:
        with pyncm.GetCurrentSession():
            apis.playlist.SetCreatePlaylist(name, privacy)

    def remove_playlist(self, id: str) -> None:
        with pyncm.GetCurrentSession():
            apis.playlist.SetRemovePlaylist(id)  # type: ignore

    def edit_playlist(
        self, option: Literal['add'] | Literal['del'], song_id: str, folder_id: str
    ) -> bool:
        with pyncm.GetCurrentSession():
            result = apis.playlist.SetManipulatePlaylistTracks(
                [song_id], folder_id, op=option
            )
            assert isinstance(result, dict), 'Invalid Response'
            if result.get('code') != 200:
                _logger.warning('edit_playlist(%s) failed: %s', option, result)
                return False
            return True

    def get_playlist_tracks(self, playlist_id: str) -> list[SongStorable]:
        with pyncm.GetCurrentSession():
            response = apis.playlist.GetPlaylistAllTracks(int(playlist_id))
            assert isinstance(response, dict), 'Invalid Response'
            assert response.get('code') == 200, f'API Error: {response}'
            songs = response['songs']  # type: ignore
            result: list[SongStorable] = []
            for s in songs:
                artist_names = [a['name'] for a in (s.get('ar') or [])]
                cached = get_cached_hashes(str(s['id']))
                storable = SongStorable(
                    info={
                        'name': s['name'],
                        'artists': '/'.join(artist_names),
                        'id': str(s['id']),
                        'privilege': -1,
                    },
                    image=None,
                    image_cache_hash=cached.get('image_cache_hash', ''),
                    content_cache_hash=cached.get('content_cache_hash', ''),
                )
                result.append(storable)
            return result
        
    def get_user_vip_type(self) -> int | str:
        return pyncm.GetCurrentSession().vipType
