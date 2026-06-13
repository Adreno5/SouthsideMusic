from __future__ import annotations

import base64
import io
import logging
import os
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Literal

import numpy as np

import requests
from core.audio_player import (
    PatchedAudioSegment as AudioSegment_,
    cache_decoded_audio,
    get_cached_audio,
)
from core.backend import getBackend
from core.config import cfg
from core.downloader import asyncDownload, asyncTask
from core.favorites import saveFavorites
from core.image import getAverageColorFromBytes
from core.loudness import getAdjustedGainFactor
from core.models import (
    IMAGE_DATA_DIR,
    MUSIC_DATA_DIR,
    SearchSongInfo,
    SongInfo,
    SongStorable,
    TrackLyricsInfo,
)
from core.stream_decoder import M4ANotStreamable, StreamDecoder
from imports import QTimer
from core.weighted_random import AdvancedRandom
from services.events.event_bus import event_bus
from services.events.events import (
    ENDING_NO_SOUND,
    IMAGE_ASSET_PERSISTED,
    PLAY_CONTINUE_LAST_SONG,
    PLAY_PLAYLIST_STORABLE,
    PLAY_SEARCH_SONG,
    PLAY_SONG_AT_INDEX,
    PLAY_START_PLAYLIST,
    PLAY_STATE_CHANGED,
    PLAY_STORABLE,
    PLAYBACK_ERROR,
    PLAYBACK_IMAGE_LOADED,
    PLAYBACK_LYRICS_UPDATED,
    PLAYBACK_SONG_LOADING,
    PLAYLAST,
    PLAYLIST_CHANGED,
    PLAYNEXT,
    SONG_CHANGED,
    SONG_FINISH,
)

if TYPE_CHECKING:
    from core.app_context import AppContext


PlayMode = Literal['Repeat one', 'Repeat list', 'Shuffle', 'Play in order']


@dataclass(frozen=True)
class PlaySelection:
    index: int
    song: SongStorable
    mode: PlayMode
    by_user: bool
    base_index: int


class PlayingManager:
    def __init__(self, ctx: AppContext | None = None) -> None:
        self.ctx = ctx
        self.playlist: list[SongStorable] = []
        self.current_index = -1
        self.total_length = 0.0
        self.preloaded = False

        self._logger = logging.getLogger(__name__)
        self._randomer = AdvancedRandom[SongStorable]()
        self._reserved_next: PlaySelection | None = None
        self._preload_triggered = False
        self.next_song_audio: AudioSegment_ | None = None
        self.next_song_gain: float | None = None
        self.next_song_selection: PlaySelection | None = None
        self.current_song: SongStorable | None = None
        self._gain_cache: dict[str, float] = {}
        self._play_seq = 0
        self._pending_search_id: str | None = None
        self._preload_download_seq = 0
        self._preload_download_song_id: str | None = None
        self._pending_play_selection: PlaySelection | None = None
        self._stream_state: tuple[StreamDecoder, SongStorable] | None = None
        self._stream_song_id = ''
        self._stream_bitrate = 0
        self._stream_duration_ms = 0
        self._seek_generation = 0
        self._pending_seek_seconds = 0.0

        if ctx is not None:
            self._bindEvents()

    @property
    def _app(self):
        return self.ctx.app if self.ctx is not None else None

    @property
    def _player(self):
        return self.ctx.player if self.ctx is not None else None

    @property
    def _mwindow_obj(self):
        return self.ctx.main_window if self.ctx is not None else None

    @property
    def _fp(self):
        return self.ctx.favorites_page if self.ctx is not None else None

    @property
    def _lock(self):
        return self.ctx.lock if self.ctx is not None else None

    @property
    def _favs_ref(self):
        return self.ctx.favs if self.ctx is not None else []

    @property
    def play_mode(self) -> PlayMode:
        if self.ctx is not None and self.ctx.setting_page:
            mode = self.ctx.setting_page.play_method_box.currentText()
            if mode in ('Repeat one', 'Repeat list', 'Shuffle', 'Play in order'):
                return mode
        return cfg.play_method

    def _bindEvents(self) -> None:
        event_bus.subscribe(SONG_CHANGED, self._onSongChangedEvent)
        event_bus.subscribe(SONG_FINISH, lambda: self.playNext(False))
        event_bus.subscribe(PLAYNEXT, lambda: self.playNext(True))
        event_bus.subscribe(PLAYLAST, self.playLast)
        event_bus.subscribe(PLAY_SEARCH_SONG, self.playSearchSong)
        event_bus.subscribe(PLAY_STORABLE, self.playStorable)
        event_bus.subscribe(PLAY_PLAYLIST_STORABLE, self.playPlaylistStorable)
        event_bus.subscribe(PLAY_SONG_AT_INDEX, self.playSongAtIndex)
        event_bus.subscribe(PLAY_START_PLAYLIST, self.startPlaylist)
        event_bus.subscribe(PLAY_CONTINUE_LAST_SONG, self.continueLastSong)
        event_bus.subscribe(PLAYLIST_CHANGED, self.playlistChanged)

        player = self._player
        if player is not None:
            player.seekRequested.connect(self._handle_stream_seek)

    def playlistChanged(self):
        self.refreshRandom()
        self.clearReservedNext()
        self.clearPreload()

    def _emitError(self, title: str, message: str) -> None:
        event_bus.emit(PLAYBACK_ERROR, title, message)

    def _schedule(self, func: Callable, *args) -> None:
        mwindow = self._mwindow_obj
        if mwindow is not None:
            mwindow.addScheduledTask(func, *args)
        else:
            func(*args)

    def setPlaylist(self, playlist: list[SongStorable]) -> None:
        self.playlist = playlist
        self.refreshRandom()
        self.clearReservedNext()
        event_bus.emit(PLAYLIST_CHANGED)

    def refreshRandom(self) -> None:
        self._randomer.init(self.playlist)

    def setCurrentIndex(self, index: int) -> None:
        self.current_index = index
        self.clearReservedNext()

    def clearReservedNext(self) -> None:
        self._reserved_next = None

    def clearPreload(self) -> None:
        self._preload_triggered = False
        self.next_song_audio = None
        self.next_song_gain = None
        self.next_song_selection = None
        self._preload_download_seq += 1
        self._preload_download_song_id = None
        self._pending_play_selection = None

    def isSelectionCurrent(self, selection: PlaySelection | None) -> bool:
        if selection is None:
            return False
        if self.current_index != selection.base_index:
            return False
        if selection.index < 0 or selection.index >= len(self.playlist):
            return False
        return self.playlist[selection.index] is selection.song

    def getNextSelection(
        self,
        mode: PlayMode,
        by_user: bool = False,
        reserve: bool = False,
    ) -> PlaySelection | None:
        if (
            self._reserved_next is not None
            and self._reserved_next.mode == mode
            and self._reserved_next.by_user == by_user
            and self.isSelectionCurrent(self._reserved_next)
        ):
            return self._reserved_next

        selection = self._buildNextSelection(mode, by_user)
        if reserve:
            self._reserved_next = selection
        return selection

    def getNextSong(
        self,
        mode: PlayMode,
        by_user: bool = False,
        reserve: bool = False,
    ) -> SongStorable | None:
        selection = self.getNextSelection(mode, by_user, reserve)
        return selection.song if selection is not None else None

    def consumeNextSelection(
        self,
        mode: PlayMode,
        by_user: bool = False,
    ) -> PlaySelection | None:
        selection = self.getNextSelection(mode, by_user)
        if selection is None:
            return None

        self.current_index = selection.index
        self.clearReservedNext()
        return selection

    def getPreviousSelection(self, mode: PlayMode) -> PlaySelection | None:
        if not self.playlist or self.current_index < 0:
            return None

        if self.current_index > 0:
            index = self.current_index - 1
        elif mode == 'Repeat list':
            index = len(self.playlist) - 1
        else:
            return None

        return PlaySelection(
            index=index,
            song=self.playlist[index],
            mode=mode,
            by_user=True,
            base_index=self.current_index,
        )

    def consumePreviousSelection(self, mode: PlayMode) -> PlaySelection | None:
        selection = self.getPreviousSelection(mode)
        if selection is None:
            return None

        self.current_index = selection.index
        self.clearReservedNext()
        return selection

    def _buildNextSelection(
        self,
        mode: PlayMode,
        by_user: bool,
    ) -> PlaySelection | None:
        if not self.playlist:
            return None

        if self.current_index < 0 or self.current_index >= len(self.playlist):
            if mode == 'Play in order':
                return None
            return self._makeSelection(0, mode, by_user)

        if mode == 'Repeat one' and not by_user:
            return self._makeSelection(self.current_index, mode, by_user)

        if mode == 'Shuffle':
            return self._makeShuffleSelection(mode, by_user)

        next_index = self.current_index + 1
        if next_index < len(self.playlist):
            return self._makeSelection(next_index, mode, by_user)

        if mode == 'Repeat list':
            return self._makeSelection(0, mode, by_user)

        return None

    def _makeShuffleSelection(
        self,
        mode: PlayMode,
        by_user: bool,
    ) -> PlaySelection | None:
        if len(self.playlist) == 1:
            return self._makeSelection(0, mode, by_user)

        start_index = self.current_index
        for _ in range(len(self.playlist) * 2):
            song = self._randomer.random()
            try:
                index = self.playlist.index(song)
            except ValueError:
                self.refreshRandom()
                continue
            if index != start_index:
                return self._makeSelection(index, mode, by_user)

        index = (start_index + 1) % len(self.playlist)
        return self._makeSelection(index, mode, by_user)

    def _makeSelection(
        self,
        index: int,
        mode: PlayMode,
        by_user: bool,
    ) -> PlaySelection:
        return PlaySelection(
            index=index,
            song=self.playlist[index],
            mode=mode,
            by_user=by_user,
            base_index=self.current_index,
        )

    def onEndingNoSound(self) -> None:
        if cfg.skip_nosound:
            event_bus.emit(ENDING_NO_SOUND)

    def _onSongChangedEvent(self, _song_storable: SongStorable) -> None:
        player = self._player
        if player is None or not player.isPlaying():
            return
        if (
            not self._preload_triggered
            and self.getNextSong(
                self.play_mode,
                reserve=True,
            )
            is not None
        ):
            self._preload_triggered = True
            self.preloadNextSong()
        if self.getNextSong(self.play_mode) is None:
            self.preloaded = True

    def _setStorableLoudness(
        self,
        song_storable: SongStorable,
        target_lufs: int,
        gain: float,
    ) -> None:
        favorites_changed = False
        song_id = str(song_storable.id)

        for folder in self._favs_ref:
            for favorite in folder.songs:
                if str(favorite.id) != song_id:
                    continue
                if (
                    favorite.target_lufs == target_lufs
                    and favorite.loudness_gain == gain
                ):
                    continue
                favorite.target_lufs = target_lufs
                favorite.loudness_gain = gain
                favorites_changed = True

        song_storable.target_lufs = target_lufs
        song_storable.loudness_gain = gain

        if favorites_changed:
            saveFavorites()

    def playSearchSong(self, info: SearchSongInfo, image_url: str) -> None:
        player = self._player
        if player is not None and player.isPlaying():
            player.stop()

        song_id = str(info.id)
        self._pending_search_id = song_id
        event_bus.emit(PLAYBACK_SONG_LOADING, info)

        prepared: dict[str, object] = {}

        def _is_current_search() -> bool:
            return self._pending_search_id == song_id

        def _try_play() -> None:
            if prepared.get('music_error'):
                prepared['_playing'] = True
                self._emitError('Playback failed', 'Failed to get song audio URL.')
                return

            image = prepared.get('image')
            lyrics_data = prepared.get('lyrics')
            music = prepared.get('music')
            if (
                not isinstance(image, bytes)
                or not isinstance(lyrics_data, TrackLyricsInfo)
                or not isinstance(music, str)
            ):
                return
            if prepared.get('_playing'):
                return
            prepared['_playing'] = True

            storable = SongStorable(
                info=SongInfo(
                    name=info.name,
                    artists='、'.join(a.name for a in info.artists),
                    id=str(info.id),
                    privilege=info.privilege.fee,
                ),
                image=image,
                music_bin=None,
                lyric=lyrics_data.lyric or '',
                translated_lyric=lyrics_data.translated_lyric or '',
                yrc_lyric=lyrics_data.yrc_lyric or '',
            )

            self.playlist.insert(self.current_index + 1, storable)
            self.refreshRandom()
            event_bus.emit(PLAYLIST_CHANGED)
            self.playStreamingSong(
                storable, music, image, info.privilege.max_br, info.duration
            )

        def _prepare() -> None:
            try:
                prepared['image'] = requests.get(image_url).content
                prepared['lyrics'] = getBackend().getTrackLyrics(song_id)
            except Exception as e:
                prepared['error'] = str(e)

        def _on_prepared() -> None:
            if not _is_current_search():
                return

            if prepared.get('error'):
                self._logger.error('Asset download failed: %s', prepared['error'])
                self._emitError('Playback failed', 'Failed to download song assets.')
                return

            image = prepared.get('image')
            if isinstance(image, bytes):
                event_bus.emit(
                    PLAYBACK_IMAGE_LOADED,
                    info,
                    image,
                    getAverageColorFromBytes(image),
                )

            _try_play()

        def _process_audio() -> None:
            try:
                audio = getBackend().getTrackAudio(
                    song_id, bitrate=info.privilege.max_br
                )
                prepared['music_url'] = audio.url
            except Exception as e:
                prepared['music_error'] = str(e)

        def _on_audio_prepare_done() -> None:
            if not _is_current_search():
                return
            if prepared.get('music_error'):
                self._emitError('Playback failed', 'Failed to get song audio URL.')
                return

            music_url = prepared.get('music_url')
            if not isinstance(music_url, str) or not music_url:
                return
            prepared['music'] = music_url
            _try_play()

        asyncTask(_prepare, (), self._mwindow_obj, _on_prepared)
        asyncTask(_process_audio, (), self._mwindow_obj, _on_audio_prepare_done)

    def preloadNextSong(self) -> None:
        if len(self.playlist) <= 1:
            return

        selection = self.getNextSelection(self.play_mode, reserve=True)
        if selection is None:
            return

        try:
            self.preloaded = False
            self._logger.info('preloading')

            next_song = selection.song
            self._logger.debug(next_song)

            def _is_preload_current() -> bool:
                return self.isSelectionCurrent(selection)

            def _start_preload(redownload_on_failure: bool = True) -> None:
                threading.Thread(
                    target=lambda: _preload(redownload_on_failure),
                    daemon=True,
                ).start()

            def _download_then_preload(
                image_missing: bool, music_missing: bool
            ) -> None:
                self._logger.info('downloading next song before preload')
                self.next_song_audio = None
                self.next_song_gain = None
                self.next_song_selection = None

                self._preload_download_seq += 1
                download_seq = self._preload_download_seq
                self._preload_download_song_id = str(next_song.id)

                def _after_download(success: bool) -> None:
                    if download_seq != self._preload_download_seq:
                        self._logger.info('discarding stale preload download')
                        return
                    self._preload_download_song_id = None
                    if not success:
                        self._logger.warning('failed to download next song for preload')
                        if self._pending_play_selection:
                            sel = self._pending_play_selection
                            self._pending_play_selection = None
                            self._emitError(
                                'Playback failed',
                                'Failed to download song assets.',
                            )
                        return
                    _start_preload(False)

                self._downloadStorableMissingAssets(
                    next_song,
                    image_missing,
                    music_missing,
                    _after_download,
                )

            def _preload(redownload_on_failure: bool) -> None:
                if not _is_preload_current():
                    self._logger.info('discarding stale preload')
                    return
                try:
                    lock = self._lock
                    if lock is None:
                        song_bytes = next_song.get_music_bytes()
                    else:
                        with lock:
                            song_bytes = next_song.get_music_bytes()
                    cache_key = next_song.content_cache_hash
                    cached = get_cached_audio(cache_key) if cache_key else None
                    if cached is not None:
                        audio = cached
                    else:
                        audio = AudioSegment_.from_file(io.BytesIO(song_bytes))
                        if cache_key:
                            cache_decoded_audio(cache_key, audio)
                except Exception as e:
                    next_song.content_cache_hash = ''
                    saveFavorites()
                    self.next_song_audio = None
                    self.next_song_gain = None
                    self.next_song_selection = None
                    self._logger.warning(
                        f'skipping preload because cached audio is invalid: {e}'
                    )
                    if redownload_on_failure:
                        self._schedule(self.preloadNextSong)
                    return

                if not _is_preload_current():
                    self._logger.info('discarding stale preload')
                    return

                self.next_song_audio = audio  # type: ignore
                if next_song.target_lufs != cfg.target_lufs:
                    gain = getAdjustedGainFactor(cfg.target_lufs, self.next_song_audio)  # type: ignore
                    self._setStorableLoudness(next_song, cfg.target_lufs, gain)
                else:
                    gain = next_song.loudness_gain
                self.next_song_gain = gain

                self._logger.debug(
                    f'preload -> gain {self.next_song_gain} {cfg.target_lufs=}'
                )
                self._logger.info('preloaded')
                self._logger.debug(
                    f'(Types) {type(self.next_song_audio)=} {type(self.next_song_gain)=}'
                )

                self.next_song_selection = selection
                self.preloaded = True

                if self._pending_play_selection:
                    sel = self._pending_play_selection
                    self._pending_play_selection = None
                    self.current_index = sel.index
                    self.clearReservedNext()
                    self._schedule(self.playPreloadedSong, sel)

            image_missing, music_missing = self._storable_asset_missing(next_song)
            if image_missing or music_missing:
                _download_then_preload(image_missing, music_missing)
            else:
                _start_preload()
        finally:
            self._logger.debug('started preload thread')

    def playNext(self, byuser: bool) -> None:
        self._logger.debug(
            f'(Types) {type(self.next_song_audio)=} {type(self.next_song_gain)=}'
        )

        selection = self.getNextSelection(self.play_mode, by_user=byuser)
        if selection is None:
            self._emitError('Warning', 'This song is the last song in the playlist.')
            player = self._player
            if player is not None:
                player.setPosition(0)
            return

        if (
            self._preload_download_song_id is not None
            and str(selection.song.id) == self._preload_download_song_id
            and byuser
        ):
            self._logger.info('deferring next to preload download completion')
            self._pending_play_selection = selection
            event_bus.emit(PLAYBACK_SONG_LOADING, selection.song)
            return

        if (
            isinstance(self.next_song_audio, AudioSegment_)
            and isinstance(self.next_song_gain, float)
            and self.next_song_selection == selection
            and self.isSelectionCurrent(selection)
        ):
            consumed = self.consumeNextSelection(self.play_mode, by_user=byuser)
            if consumed is None:
                return
            self.playPreloadedSong(consumed)
            return

        consumed = self.consumeNextSelection(self.play_mode, by_user=byuser)
        if consumed is None:
            return
        self.playSongAtIndex(consumed.index)

    def playPreloadedSong(self, selection: PlaySelection) -> None:
        if (not isinstance(self.next_song_audio, AudioSegment_)) or (
            not isinstance(self.next_song_gain, float)
        ):
            self._logger.error(
                f'cant play preloaded song: (Types) {type(self.next_song_audio)=} {type(self.next_song_gain)=}'
            )
            return

        self._logger.info('using preloaded song')
        self.playStorable(selection.song, preloaded_audio=self.next_song_audio)

    def playLast(self) -> None:
        selection = self.consumePreviousSelection(self.play_mode)
        if selection is None:
            self._emitError(
                'Warning',
                'This song is the first song in the playlist.',
            )
            player = self._player
            if player is not None:
                player.setPosition(0)
            return

        self.clearPreload()
        self.playSongAtIndex(selection.index)

    def continueLastSong(self, index: int) -> None:
        if index < 0 or index >= len(self.playlist):
            return

        self.current_index = index
        song_storable = self.playlist[index]
        self.playStorable(
            song_storable,
            restore_position=cfg.last_playing_time,
            pause_after_load=True,
            mark_loaded=True,
        )

    def playSongAtIndex(self, index: int) -> None:
        if index < 0 or index >= len(self.playlist):
            return

        self.current_index = index
        self.playStorable(self.playlist[index])

    def playPlaylistStorable(self, storable: SongStorable) -> None:
        try:
            self.current_index = self.playlist.index(storable)
        except ValueError:
            return
        self.playStorable(storable)

    def _storable_asset_missing(self, song_storable: SongStorable) -> tuple[bool, bool]:
        backend = getBackend()
        return not song_storable.image_cached(), not song_storable.audio_cached(
            not backend.userAnonymous(), int(backend.getUserVipType())
        )

    def _downloadStorableMissingAssets(
        self,
        song_storable: SongStorable,
        image_missing: bool,
        music_missing: bool,
        finished: Callable[[bool], None],
    ) -> None:
        prepared: dict[str, bytes | str] = {}

        def _prepare() -> None:
            try:
                if image_missing:
                    detail = getBackend().getTrackDetail(song_storable.id)
                    image_url = detail.cover_url
                    prepared['image'] = requests.get(image_url).content

                if music_missing:
                    audio = getBackend().getTrackAudio(
                        str(song_storable.id),
                        bitrate=3200 * 1000,
                    )
                    self._logger.debug(f'{audio.url=}')
                prepared['music_url'] = audio.url

            except Exception as e:
                prepared['error'] = str(e)

        def _persist_assets(music_bytes: bytes | None = None) -> bool:
            try:
                image_just_persisted = False
                if image_missing:
                    image_bytes = prepared.get('image')
                    if not isinstance(image_bytes, bytes) or not image_bytes:
                        return False
                    song_storable._write_cache(
                        image_bytes, IMAGE_DATA_DIR, 'image_cache_hash'
                    )
                    image_just_persisted = True

                if music_missing:
                    if not music_bytes:
                        return False
                    song_storable._write_cache(
                        music_bytes, MUSIC_DATA_DIR, 'content_cache_hash'
                    )

                saveFavorites()
                if image_just_persisted:
                    event_bus.emit(IMAGE_ASSET_PERSISTED, song_storable)
                return True
            except Exception:
                self._logger.exception('failed to persist downloaded storable assets')
                return False

        def _play_after_persist(music_bytes: bytes | None = None) -> None:
            finished(_persist_assets(music_bytes))

        def _on_prepared() -> None:
            if prepared.get('error'):
                self._logger.warning(
                    f'failed to prepare storable asset download: {prepared["error"]}'
                )
                finished(False)
                return

            if music_missing:
                music_url = prepared.get('music_url')
                if not isinstance(music_url, str) or not music_url:
                    finished(False)
                    return
                asyncDownload(
                    music_url,
                    {},
                    None,
                    self._mwindow_obj,
                    _play_after_persist,
                )
            else:
                _play_after_persist()

        asyncTask(_prepare, (), self._mwindow_obj, _on_prepared)

    def _downloadMissingStorableAssets(
        self,
        song_storable: SongStorable,
        image_missing: bool,
        music_missing: bool,
        after_download: Callable[[], None] | None = None,
    ) -> None:
        player = self._player
        if player is not None:
            player.stop()
        self.current_song = song_storable
        event_bus.emit(PLAYBACK_SONG_LOADING, song_storable)

        def _play_after_download(success: bool) -> None:
            if not success:
                self._emitError(
                    'Playback failed',
                    'Failed to download missing cached files.',
                )
                return
            if after_download is not None:
                after_download()
            else:
                self.playStorable(song_storable)

        self._downloadStorableMissingAssets(
            song_storable,
            image_missing,
            music_missing,
            _play_after_download,
        )

    def ensureAssets(
        self,
        song_storable: SongStorable,
        after_download: Callable[[], None] | None = None,
    ) -> bool:
        image_missing, music_missing = self._storable_asset_missing(song_storable)
        if image_missing or music_missing:
            if (
                self._preload_download_song_id is not None
                and str(song_storable.id) == self._preload_download_song_id
            ):
                self._logger.info(
                    'ensureAssets: reusing in-progress preload download for %s',
                    song_storable.id,
                )
                self._pending_play_selection = PlaySelection(
                    index=self.current_index,
                    song=song_storable,
                    mode=self.play_mode,
                    by_user=True,
                    base_index=self.current_index,
                )
                event_bus.emit(PLAYBACK_SONG_LOADING, song_storable)
                return False
            self._downloadMissingStorableAssets(
                song_storable,
                image_missing,
                music_missing,
                after_download,
            )
            return False
        return True

    def _loadStorableAudio(
        self,
        song_storable: SongStorable,
        preloaded_audio: AudioSegment_ | None = None,
    ) -> AudioSegment_ | Any:
        if preloaded_audio is not None:
            return preloaded_audio

        music_bytes = song_storable.get_music_bytes()
        cache_key = song_storable.content_cache_hash
        cached = get_cached_audio(cache_key) if cache_key else None
        if cached is not None:
            return cached
        audio = AudioSegment_.from_file(io.BytesIO(music_bytes))
        if cache_key:
            cache_decoded_audio(cache_key, audio)
        return audio

    def playStorable(
        self,
        song_storable: SongStorable,
        preloaded_audio: AudioSegment_ | None = None,
        restore_position: float | None = None,
        pause_after_load: bool = False,
        mark_loaded: bool = False,
    ) -> None:
        self._logger.debug(f'{song_storable.target_lufs=} {cfg.target_lufs=}')

        if not self.ensureAssets(
            song_storable,
            lambda: self.playStorable(
                song_storable,
                preloaded_audio,
                restore_position,
                pause_after_load,
                mark_loaded,
            ),
        ):
            return

        player = self._player
        if player is None:
            return

        player.stop()
        self.current_song = song_storable
        self._play_seq += 1
        play_seq = self._play_seq
        event_bus.emit(PLAYBACK_SONG_LOADING, song_storable)
        app = self._app
        if app is not None:
            app.processEvents()

        result: dict[str, object] = {}

        def _prepare() -> None:
            mwindow = self._mwindow_obj
            if mwindow is not None:
                mwindow._loading_song = True
            audio = self._loadStorableAudio(song_storable, preloaded_audio)

            if mwindow is not None:
                mwindow._loading_song = False

            result['audio'] = audio
            player.load(audio)
            self.total_length = player.getLength()
            if not player.isPlaying():
                player.play()

            if (
                song_storable.target_lufs == cfg.target_lufs
                and song_storable.loudness_gain != 1.0
            ):
                player.setGain(song_storable.loudness_gain)

            if restore_position is not None:
                player.setPosition(restore_position)
            if pause_after_load:
                player.pause()

            try:
                image_bytes = song_storable.get_image_bytes()
                result['image'] = image_bytes
                result['avg_color'] = getAverageColorFromBytes(image_bytes)
            except Exception:
                self._logger.exception('failed to load image bytes for playback')

        def _finish() -> None:
            if play_seq != self._play_seq or self.current_song is not song_storable:
                return

            self.clearPreload()
            if mark_loaded:
                self.preloaded = True

            if song_storable not in self.playlist:
                self.playlist.insert(self.current_index, song_storable)
                self.refreshRandom()

            event_bus.emit(PLAYLIST_CHANGED)

            self._show_original_lyrics(song_storable)

            self._compute_gain_async(song_storable, result.get('audio'))  # type: ignore

            self._download_update_lyrics(song_storable)

            image_bytes = result.get('image')
            avg_color = result.get('avg_color')
            if isinstance(image_bytes, bytes):
                event_bus.emit(
                    PLAYBACK_IMAGE_LOADED,
                    song_storable,
                    image_bytes,
                    avg_color,
                )

            event_bus.emit(SONG_CHANGED, song_storable)
            event_bus.emit(PLAY_STATE_CHANGED, not pause_after_load)

        asyncTask(_prepare, (), self._mwindow_obj, _finish)

    def _show_original_lyrics(self, song_storable: SongStorable) -> None:
        if not self.ctx:
            return
        lyrics = song_storable.get_lyrics()
        self.ctx.mgr.cur = lyrics['lyric'] or '[00:00.000]'
        self.ctx.transmgr.cur = ''
        self.ctx.ymgr.cur = ''
        self.ctx.mgr.parse()
        self.ctx.transmgr.parse()
        self.ctx.ymgr.parse()
        event_bus.emit(PLAYBACK_LYRICS_UPDATED, song_storable)

    def playStreamingSong(
        self,
        song_storable: SongStorable,
        url: str,
        image: bytes,
        bitrate: int,
        duration_ms: int,
    ) -> None:
        player = self._player
        if player is None:
            return

        player.stop()
        self.current_song = song_storable
        event_bus.emit(PLAYBACK_SONG_LOADING, song_storable)
        app = self._app
        if app is not None:
            app.processEvents()

        try:
            decoder = StreamDecoder(url)
            decoder.start()
        except M4ANotStreamable:
            self._logger.info('M4A not streamable, falling back to full download')
            self._fallback_full_download(song_storable, url, image)
            return

        player.startStreaming(decoder, 44100, 2)
        player.play()

        self._stream_state = (decoder, song_storable)
        self._stream_song_id = song_storable.id
        self._stream_bitrate = bitrate
        self._stream_duration_ms = duration_ms

        try:
            avg_color = getAverageColorFromBytes(image)
            event_bus.emit(PLAYBACK_IMAGE_LOADED, song_storable, image, avg_color)
        except Exception:
            self._logger.exception('failed to load image for streaming playback')

        self._show_original_lyrics(song_storable)
        self._download_update_lyrics(song_storable)
        event_bus.emit(SONG_CHANGED, song_storable)
        event_bus.emit(PLAY_STATE_CHANGED, True)

        def _on_finished() -> None:
            self._save_stream_cache()
            try:
                player.onFullFinished.disconnect(_on_finished)
            except TypeError:
                pass

        player.onFullFinished.connect(_on_finished)

    def _fallback_full_download(
        self, song_storable: SongStorable, url: str, image: bytes
    ) -> None:
        def _on_downloaded(data: bytes) -> None:
            song_storable._write_cache(data, MUSIC_DATA_DIR, 'content_cache_hash')
            self._logger.info('full download fallback completed, playing')
            self.playStorable(song_storable)

        asyncDownload(url, {}, None, self._mwindow_obj, _on_downloaded)

    def _handle_stream_seek(self, seconds: float) -> None:
        self._pending_seek_seconds = seconds
        self._seek_generation += 1
        gen = self._seek_generation

        def _delayed() -> None:
            if self._seek_generation != gen:
                return
            self._do_stream_seek(self._pending_seek_seconds)

        QTimer.singleShot(500, _delayed)

    def _do_stream_seek(self, seconds: float) -> None:
        state = self._stream_state
        if state is None:
            return
        decoder, storable = state
        song_id = self._stream_song_id
        bitrate = self._stream_bitrate
        duration_ms = self._stream_duration_ms

        if not song_id or duration_ms <= 0:
            return

        player = self._player
        if player is None:
            return

        player.stop()
        decoder.stop()

        try:
            audio = getBackend().getTrackAudio(song_id, bitrate)
        except Exception:
            self._logger.exception('failed to get fresh audio URL for seek')
            return

        try:
            probe = requests.head(audio.url, allow_redirects=True, timeout=10)
            probe.raise_for_status()
            total_size = int(probe.headers.get('content-length', 0))
        except Exception:
            self._logger.exception('failed to probe file size for seek')
            total_size = 0

        if total_size > 0:
            duration_sec = duration_ms / 1000.0
            bytes_per_sec = total_size / duration_sec
            start_byte = int(seconds * bytes_per_sec)
        else:
            start_byte = 0

        self._logger.info(
            'stream seek to %.1fs -> byte offset %d (size=%d)',
            seconds,
            start_byte,
            total_size,
        )

        new_decoder = StreamDecoder(audio.url, duration_sec=duration_ms / 1000.0)
        new_decoder.reset_for_seek(start_byte)
        new_decoder.start(block=False)

        player.resetStream(new_decoder)
        player.play()

        self._stream_state = (new_decoder, storable)
        self._stream_song_id = song_id

    def _save_stream_cache(self) -> None:
        state = self._stream_state
        self._stream_state = None
        if state is None:
            return

        decoder, storable = state
        temp_path = decoder.temp_path

        if not temp_path or not os.path.exists(temp_path):
            try:
                decoder.stop()
            except Exception:
                pass
            return

        try:
            with open(temp_path, 'rb') as f:
                music_bytes = f.read()
        except Exception:
            self._logger.exception('failed to read stream temp file')
            return

        decoder.stop()
        player = self._player
        if player is not None:
            player.endStream()

        try:
            storable._write_cache(music_bytes, MUSIC_DATA_DIR, 'content_cache_hash')
            self._logger.info('stream cache saved for %s', storable.id)
        except Exception:
            self._logger.exception('failed to save stream cache')

    def _compute_gain_async(
        self,
        song_storable: SongStorable,
        raw_audio: AudioSegment_ | None,
    ) -> None:
        if raw_audio is None:
            return
        if song_storable.target_lufs == cfg.target_lufs:
            return

        def _compute_and_apply() -> None:
            player = self._player
            if player is None:
                return
            gain = getAdjustedGainFactor(cfg.target_lufs, raw_audio)
            self._setStorableLoudness(song_storable, cfg.target_lufs, gain)
            if self.current_song is song_storable:
                if self._mwindow_obj is not None:
                    self._mwindow_obj.addScheduledTask(
                        lambda g=gain: player.animateLoudnessGain(g)
                    )

        threading.Thread(target=_compute_and_apply, daemon=True).start()

    def _download_update_lyrics(self, song_storable: SongStorable) -> None:
        lyric_target = song_storable
        lyric_result: TrackLyricsInfo | None = None

        def _download() -> None:
            nonlocal lyric_result
            need_yrc = song_storable.yrc_lyrics_missing()
            need_ytlrc = song_storable.ytlrc_missing()
            if not need_yrc and not need_ytlrc:
                return
            try:
                lyric_result = getBackend().getTrackLyrics(song_storable.id)
            except Exception:
                self._logger.exception(
                    'failed to download lyrics for storable playback'
                )
                lyric_result = None

        def _apply() -> None:
            if self.current_song is not lyric_target:
                return

            if self.ctx:
                mgr = self.ctx.mgr
                transmgr = self.ctx.transmgr
                ymgr = self.ctx.ymgr

            if lyric_result is None:
                lyrics = lyric_target.get_lyrics()
                mgr.cur = lyrics['lyric'] or '[00:00.000]'
                ymgr.cur = lyrics['yrc_lyric']
                ytlrc = lyrics.get('ytlrc_lyric', '')
                if ymgr.cur and ytlrc:
                    transmgr.cur = ytlrc
                else:
                    transmgr.cur = lyrics['translated_lyric'] or '[00:00.000]'
            else:
                mgr.cur = lyric_result.lyric or '[00:00.000]'
                ymgr.cur = lyric_result.yrc_lyric or ''
                ytlrc = lyric_result.ytlrc_lyric or ''
                if ymgr.cur and ytlrc:
                    transmgr.cur = ytlrc
                else:
                    transmgr.cur = lyric_result.translated_lyric or '[00:00.000]'
                lyric_target.write_lyrics(
                    mgr.cur,
                    transmgr.cur if transmgr.cur != '[00:00.000]' else '',
                    ymgr.cur,
                    ytlrc,
                )
                saveFavorites()

            mgr.parse()
            transmgr.parse()
            ymgr.parse()
            event_bus.emit(PLAYBACK_LYRICS_UPDATED, lyric_target)

        asyncTask(_download, (), self._mwindow_obj, _apply)

    def loadMusicFromBase64(self, content_base64: str, gain: float) -> None:
        self.loadMusicFromBytes(base64.b64decode(content_base64), gain)

    def loadMusicFromBytes(self, music_bytes: bytes, gain: float) -> None:
        self._logger.debug(f'loading data {len(music_bytes)}')
        lock = self._lock
        if lock is None:
            audio = AudioSegment_.from_file(io.BytesIO(music_bytes))
        else:
            with lock:
                audio = AudioSegment_.from_file(io.BytesIO(music_bytes))

        self._logger.debug(f'applying gain {gain} {cfg.target_lufs=}')
        audio = audio.apply_gain(20 * np.log10(gain))

        player = self._player
        if player is None:
            return
        player.load(audio)
        self.total_length = player.getLength()

    def startPlaylist(self) -> None:
        if self._fp is None:
            return
        self._fp.addFolderToPlaylist()

        self.current_index = 0
        self.playSongAtIndex(0)

        player = self._player
        if player is not None and not player.isPlaying():
            player.play()
