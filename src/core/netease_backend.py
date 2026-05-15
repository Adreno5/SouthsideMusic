from __future__ import annotations

import logging

import pyncm
from pyncm import apis

from core.models import (
    AlbumInfo,
    ArtistInfo,
    MusicServiceBackend,
    PrivilegeInfo,
    SearchSongInfo,
    TrackAudioInfo,
    TrackDetailInfo,
    TrackLyricsInfo,
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
        resp = apis.track.GetTrackAudio(
            [str(track_id)], bitrate=bitrate
        )
        assert isinstance(resp, dict), 'Invalid track audio response'
        url = resp['data'][0]['url']  # type: ignore
        return TrackAudioInfo(url=url)

    def get_track_lyrics(self, track_id: int | str) -> TrackLyricsInfo:
        data = apis.track.GetTrackLyricsNew(str(track_id))
        assert isinstance(data, dict), 'Invalid track lyrics response'

        lyric = data.get('lrc', {}).get('lyric', '')

        tlyric = data.get('tlyric')
        if isinstance(tlyric, dict):
            translated_lyric = '\n'.join(
                tlyric.get('lyric', '').splitlines()[1:]
            )
        else:
            translated_lyric = ''

        yrc_lyric = data.get('yrc', {}).get('lyric', '')

        return TrackLyricsInfo(
            lyric=lyric,
            translated_lyric=translated_lyric,
            yrc_lyric=yrc_lyric,
        )

    def user_privilege_level(self) -> int:
        return pyncm.GetCurrentSession().vipType
