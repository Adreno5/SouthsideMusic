from __future__ import annotations

import datetime
import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, override

import numpy as np
import sounddevice as sd

from core.favorites import favorites_manager
from core.lyric_sources import LyricCandidate
from core.models import (
    AlbumInfo,
    ArtistInfo,
    BackendAccountInfo,
    BackendSessionSnapshot,
    CloudFolderInfo,
    Comment,
    CommentInfo,
    PrivilegeInfo,
    QualityLevelInfo,
    QualityPrivilegeInfo,
    SearchCloudFolderInfo,
    SearchSongInfo,
    SongInfo,
    SongStorable,
    TrackAudioInfo,
    TrackDetailInfo,
    TrackLyricsInfo,
    UserInfo,
)
from core.netease_backend import NeteaseCloudMusicBackend


ORIGINAL_LYRICS = '[00:00.500]Hello world\n[00:02.000]Southside music\n'
TRANSLATED_LYRICS = '[00:00.500]你好世界\n[00:02.000]南方音乐\n'
WORD_LYRICS = (
    '[500,1000](500,500,0)Hello (1000,500,0)world\n'
    '[2000,1500](2000,700,0)Southside (2700,800,0)music\n'
)


class FixtureBackend(NeteaseCloudMusicBackend):
    def __init__(self, root: Path, media_url: str) -> None:
        self.root = root
        self.media_url = media_url
        self.online = False
        self.logged_in = True
        self.records: list[tuple[str, Any]] = []
        self.cloud: dict[str, list[str]] = {'100': ['90001', '90002']}
        self.songs: list[SongStorable] = []
        cover = (root / 'fixtures' / 'cover.png').read_bytes()
        for index in range(36):
            song = SongStorable(
                SongInfo(
                    f'Harness Song {index + 1:02}',
                    [ArtistInfo(1, 'Harness Artist')],
                    str(90001 + index),
                    -1,
                    12000,
                ),
                image=cover,
                music_bin=(root / 'fixtures' / f'track-{index % 3}.flac').read_bytes(),
                lyric=ORIGINAL_LYRICS if index != 2 else '',
                translated_lyric=TRANSLATED_LYRICS if index != 2 else '',
                yrc_lyric=WORD_LYRICS if index == 1 else '',
            )
            self.songs.append(song)
        if not (root / 'favorites.json').exists():
            favorites_manager.addFolder('Harness Library')
            for song in reversed(self.songs[:6]):
                favorites_manager.addSong('Harness Library', song)

    @override
    def getCurrentLoginStatus(self) -> dict:
        return {'account': {'id': 1}, 'profile': {'nickname': 'Harness User'}}

    @override
    def writeLoginInfo(self, login_status: dict | None) -> None:
        self.records.append(('login_info', login_status))

    @override
    def currentSessionIsAnonymous(self) -> bool:
        return not self.logged_in

    @override
    def loadSession(self, session: str) -> None:
        self.records.append(('load_session', session))

    @override
    def dumpSession(self) -> str:
        return ''

    @override
    def setRandomDeviceId(self) -> None:
        pass

    @override
    def getSessionBindings(self) -> list[dict[str, Any]]:
        return []

    @override
    def refreshSessionIfNeeded(self, expiry_window_seconds: int = 300) -> None:
        return None

    @override
    def getAccountInfo(self) -> BackendAccountInfo:
        return BackendAccountInfo(
            1, 'Harness User', self.media_url + '/cover.png', self.logged_in, 0
        )

    @override
    def loggedIn(self) -> bool:
        return self.logged_in

    @override
    def getUserVipType(self) -> int:
        return 0

    @override
    def userPrivilegeLevel(self) -> int:
        return 0

    @override
    def logout(self) -> BackendSessionSnapshot:
        self.logged_in = False
        return BackendSessionSnapshot('', None)

    @override
    def searchSong(
        self, keywords: str, offset: int = 0, limit: int = 30
    ) -> list[SearchSongInfo]:
        if self.online:
            return super().searchSong(keywords, offset, limit)
        self.records.append(('search', {'query': keywords, 'offset': offset}))
        if keywords == 'no-results':
            return []
        return [
            SearchSongInfo(
                song.id,
                song.name,
                song.artists,
                AlbumInfo(1, 'Harness Album', self.media_url + '/cover.png'),
                PrivilegeInfo(0, 999000, False),
                12000,
            )
            for song in self.songs[offset : offset + limit]
        ]

    @override
    def searchPlaylist(
        self, keywords: str, offset: int = 0, limit: int = 30
    ) -> list[SearchCloudFolderInfo]:
        if self.online:
            return super().searchPlaylist(keywords, offset, limit)
        return (
            [
                SearchCloudFolderInfo(
                    'Harness Cloud',
                    self.media_url + '/cover.png',
                    '100',
                    'Harness User',
                )
            ]
            if offset == 0
            else []
        )

    @override
    def getTrackDetail(self, track_id: int | str) -> TrackDetailInfo:
        song = self._song(track_id)
        return TrackDetailInfo(
            self.media_url + '/cover.png',
            'Harness Album',
            '1',
            1,
            1700000000000,
            song.artists,
            song.duration,
            song.name,
        )

    @override
    def getTrackAudio(
        self, track_id: int | str, bitrate: int = 999000
    ) -> TrackAudioInfo:
        index = (int(track_id) - 90001) % 3
        return TrackAudioInfo(f'{self.media_url}/track-{index}.flac', 44100)

    @override
    def getTrackLyrics(self, track_id: int | str) -> TrackLyricsInfo:
        stored = self._song(track_id).getLyrics()
        return TrackLyricsInfo(
            stored['lyric'], stored['translated_lyric'], stored['yrc_lyric'], ''
        )

    @override
    def getUserPlaylists(self) -> list[CloudFolderInfo]:
        return [
            CloudFolderInfo(
                'Harness Cloud', self.media_url + '/cover.png', key, len(value)
            )
            for key, value in self.cloud.items()
        ]

    @override
    def getLikedPlaylist(self) -> CloudFolderInfo:
        return self.getUserPlaylists()[0]

    @override
    def getPlaylistTracks(self, playlist_id: str) -> list[SongStorable]:
        return [self._song(key) for key in self.cloud.get(str(playlist_id), [])]

    @override
    def createPlaylist(self, name: str) -> str:
        key = str(100 + len(self.cloud))
        self.cloud[key] = []
        self.records.append(('create_playlist', name))
        return key

    @override
    def removePlaylist(self, id: str) -> None:
        del self.cloud[id]

    @override
    def editPlaylist(
        self, option: Literal['add', 'del'], song_ids: list[str], folder_id: str
    ) -> bool:
        target = self.cloud[folder_id]
        for song_id in song_ids:
            if option == 'add' and song_id not in target:
                target.append(song_id)
            elif option == 'del' and song_id in target:
                target.remove(song_id)
        return True

    @override
    def getDailyRecommendSongs(self) -> list[SongStorable]:
        return self.songs[:3]

    @override
    def getDailyRecommendFolders(self) -> list[CloudFolderInfo]:
        return self.getUserPlaylists()

    @override
    def getHeartModeSongs(
        self,
        seed_song_id: int | str,
        playlist_id: int | str,
        start_music_id: int | str | None = None,
        count: int = 20,
    ) -> list[SongStorable]:
        return self.songs[:count]

    @override
    def getPersonalFMSongs(self) -> list[SongStorable]:
        return self.songs[3:6]

    @override
    def getPrivateRadarSongs(self) -> list[SongStorable]:
        return self.songs[6:9]

    @override
    def getSimilarFMSongs(self) -> list[SongStorable]:
        return self.songs[9:12]

    @override
    def recordPlayed(self, song: SongStorable, played: float) -> None:
        self.records.append(('played', (song.id, played)))

    @override
    def recordPlay(self, song: SongStorable) -> None:
        self.records.append(('play', song.id))

    @override
    def getComments(
        self,
        song_id: str,
        page: int = 1,
        limit: int = 20,
        sort: Literal['recommend', 'time', 'hot'] = 'time',
        cursor: str = '-1',
    ) -> CommentInfo:
        comments = [
            Comment(
                str(index),
                UserInfo('1', self.media_url + '/cover.png', 'Harness User'),
                f'Harness comment {index}',
                3,
                datetime.datetime(2026, 11, 17),
                [],
            )
            for index in range((page - 1) * limit, min(page * limit, 22))
        ]
        return CommentInfo(comments, 22, str(page))

    @override
    def addComment(self, song_id: str, content: str) -> None:
        self.records.append(('comment', (song_id, content)))

    @override
    def getSongQualityPrivilege(self, song_id: str) -> QualityPrivilegeInfo:
        return QualityPrivilegeInfo(
            999000,
            'Lossless',
            [
                QualityLevelInfo(320000, False, 'High'),
                QualityLevelInfo(999000, False, 'Lossless'),
            ],
        )

    def _song(self, song_id: int | str) -> SongStorable:
        return next(song for song in self.songs if song.id == str(song_id))


def fixtureLyrics(*args: Any, **kwargs: Any) -> Iterator[LyricCandidate]:
    yield LyricCandidate(
        'Harness', ORIGINAL_LYRICS, WORD_LYRICS, TRANSLATED_LYRICS, True
    )


class VirtualOutputStream:
    streams: list[VirtualOutputStream] = []

    def __init__(self, **settings: Any) -> None:
        self.active = False
        self.closed = False
        self.settings = settings
        self.blocks: list[np.ndarray] = []
        self.errors: list[str] = []
        self.frames = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.streams.append(self)

    def start(self) -> None:
        if self.active:
            return
        self.active = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        frames = int(self.settings.get('blocksize', 2048))
        rate = int(self.settings['samplerate'])
        callback: Callable[..., None] = self.settings['callback']
        while not self._stop.is_set():
            started = time.monotonic()
            output = np.zeros((frames, int(self.settings['channels'])), np.float32)
            try:
                callback(
                    output,
                    frames,
                    SimpleNamespace(
                        outputBufferDacTime=self.frames / rate,
                        currentTime=self.frames / rate,
                    ),
                    sd.CallbackFlags(),
                )
            except sd.CallbackStop:
                break
            except Exception as error:
                self.errors.append(str(error))
                break
            self.frames += frames
            if len(self.blocks) < 256:
                self.blocks.append(output.copy())
            self._stop.wait(max(0, frames / rate - (time.monotonic() - started)))
        self.active = False

    def stop(self) -> None:
        self.active = False
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def abort(self) -> None:
        self.stop()

    def close(self) -> None:
        self.stop()
        self.closed = True


def virtualDevices(device: Any = None, kind: Any = None) -> Any:
    info = {
        'name': 'Harness virtual output',
        'max_output_channels': 2,
        'max_input_channels': 0,
        'default_samplerate': 44100,
        'hostapi': 0,
        'default_low_output_latency': 0.01,
        'default_high_output_latency': 0.1,
    }
    return [info] if device is None and kind is None else info


class FakeSmtc:
    def __init__(self) -> None:
        self.updates = 0
        self.timeline: Any = None
        self.button_callback: Any = None
        self.position_callback: Any = None
        self.display_updater = SimpleNamespace(
            music_properties=SimpleNamespace(title='', artist='', album_title=''),
            update=self._update,
            clear_all=self._clear,
        )

    def _update(self) -> None:
        self.updates += 1

    def _clear(self) -> None:
        self.display_updater.music_properties.title = ''

    def add_button_pressed(self, callback: Any) -> int:
        self.button_callback = callback
        return 1

    def add_playback_position_change_requested(self, callback: Any) -> int:
        self.position_callback = callback
        return 2

    def update_timeline_properties(self, timeline: Any) -> None:
        self.timeline = timeline


def saveFixtureState(root: Path, backend: FixtureBackend) -> None:
    (root / 'fixture-state.json').write_text(
        json.dumps(
            {'records': backend.records, 'cloud': backend.cloud}, ensure_ascii=False
        ),
        encoding='utf-8',
    )
