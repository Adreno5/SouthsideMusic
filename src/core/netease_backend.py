from __future__ import annotations

import json
import logging
from typing import Literal

import pyncm
from pyncm import apis

from core.models import (
    AlbumInfo,
    ArtistInfo,
    CloudFolderInfo,
    MusicServiceBackend,
    PrivilegeInfo,
    SearchSongInfo,
    SongInfo,
    SongStorable,
    TrackAudioInfo,
    TrackDetailInfo,
    TrackLyricsInfo,
    SearchCloudFolderInfo,
    getCachedHashes,
)

_logger = logging.getLogger(__name__)


class NeteaseCloudMusicBackend(MusicServiceBackend):
    def searchSong(
        self, keywords: str, offset: int = 0, limit: int = 30
    ) -> list[SearchSongInfo]:
        resp = apis.cloudsearch.getSearchResult(
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

    def searchPlaylist(
        self, keywords: str, offset: int = 0, limit: int = 30
    ) -> list[SearchCloudFolderInfo]:
        resp = apis.cloudsearch.getSearchResult(
            keywords, stype=1000, limit=limit, offset=offset
        )
        assert isinstance(resp, dict), 'Invalid search response'

        playlists: list[SearchCloudFolderInfo] = []
        for playlist_dict in resp.get('result', {}).get('playlists', []):
            playlists.append(
                SearchCloudFolderInfo(
                    folder_name=playlist_dict['name'],
                    image_url=playlist_dict['coverImgUrl'],
                    id=str(playlist_dict['id']),
                    author=playlist_dict['creator']['nickname'],
                )
            )
        return playlists

    def getTrackDetail(self, track_id: int | str) -> TrackDetailInfo:
        response = apis.track.getTrackDetail(song_ids=[track_id])
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

    def getTrackAudio(
        self, track_id: int | str, bitrate: int = 999000
    ) -> TrackAudioInfo:
        resp = apis.track.getTrackAudio([str(track_id)], bitrate=bitrate)
        if isinstance(resp, bytes):
            resp = json.loads(resp.decode())
        assert isinstance(resp, dict), 'Invalid track audio response'
        url = resp['data'][0]['url']  # type: ignore
        return TrackAudioInfo(url=url)

    def getTrackLyrics(self, track_id: int | str) -> TrackLyricsInfo:
        data = apis.track.getTrackLyricsNew(str(track_id))
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

    def userPrivilegeLevel(self) -> int:
        return pyncm.getCurrentSession().vipType

    def userAnonymous(self) -> bool:
        return bool(pyncm.getCurrentSession().is_anonymous)

    def getUserPlaylists(self) -> list[CloudFolderInfo]:
        with pyncm.getCurrentSession() as session:
            response = apis.user.getUserPlaylists(session.uid)
            assert isinstance(response, dict), 'Invaild Response'
            assert not session.is_anonymous, 'Anonymous Account'

            data = response['playlist']  # type: ignore

            return [
                CloudFolderInfo(
                    folder_name=p['name'], image_url=p['coverImgUrl'], id=str(p['id'])
                )
                for p in data
            ]

    def createPlaylist(self, name: str) -> str:
        with pyncm.getCurrentSession():
            response = apis.playlist.setCreatePlaylist(name, False)
            assert isinstance(response, dict), 'Invalid Response'
            return str(response['id'])  # type: ignore

    def removePlaylist(self, id: str) -> None:
        with pyncm.getCurrentSession():
            apis.playlist.setRemovePlaylist(id)  # type: ignore

    def editPlaylist(
        self,
        option: Literal['add'] | Literal['del'],
        song_ids: list[str],
        folder_id: str,
    ) -> bool:
        with pyncm.getCurrentSession():
            result = apis.playlist.setManipulatePlaylistTracks(
                song_ids, folder_id, op=option
            )
            assert isinstance(result, dict), 'Invalid Response'
            if result.get('code') != 200:
                _logger.warning('edit_playlist(%s) failed: %s', option, result)
                return False
            return True

    def getPlaylistTracks(self, playlist_id: str) -> list[SongStorable]:
        with pyncm.getCurrentSession():
            response = apis.playlist.getPlaylistAllTracks(int(playlist_id))
            assert isinstance(response, dict), 'Invalid Response'
            assert response.get('code') == 200, f'API Error: {response}'
            songs = response['songs']  # type: ignore
            result: list[SongStorable] = []
            for s in songs:
                artist_names = [a['name'] for a in (s.get('ar') or [])]
                cached = getCachedHashes(str(s['id']))
                storable = SongStorable(
                    info=SongInfo(
                        name=s['name'],
                        artists='/'.join(artist_names),
                        id=str(s['id']),
                        privilege=-1,
                    ),
                    image=None,
                    image_cache_hash=cached.get('image_cache_hash', ''),
                    content_cache_hash=cached.get('content_cache_hash', ''),
                )
                result.append(storable)
            return result

    def getUserVipType(self) -> int | str:
        return pyncm.getCurrentSession().vipType
