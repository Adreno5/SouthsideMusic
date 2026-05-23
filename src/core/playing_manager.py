from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Literal

import numpy as np

from core import http_utils as requests
from core.audio_player import (
    PatchedAudioSegment as AudioSegment_,
    cache_decoded_audio,
    get_cached_audio,
)
from core.backend import get_backend
from core.config import cfg
from core.downloader import doWithMultiThreading, downloadWithMultiThreading
from core.favorites import saveFavorites
from core.image import getAverageColorFromBytes
from core.loudness import getAdjustedGainFactor
from core.models import (
    IMAGE_DATA_DIR,
    MUSIC_DATA_DIR,
    SearchSongInfo,
    SongStorable,
    TrackLyricsInfo,
)
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

_AUDIO_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
}


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
            for favorite in folder['songs']:
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

        song_id = str(info['id'])
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
                or not isinstance(lyrics_data, dict)
                or not isinstance(music, bytes)
            ):
                return
            if len(music) == 0 or prepared.get('_playing'):
                return
            prepared['_playing'] = True

            storable = SongStorable(
                info={
                    'name': info['name'],
                    'artists': '、'.join(a['name'] for a in info['artists']),
                    'id': str(info['id']),
                    'privilege': info['privilege']['fee'],
                },
                image=image,
                music_bin=music,
                lyric=lyrics_data.get('lyric', ''),
                translated_lyric=lyrics_data.get('translated_lyric', ''),
                yrc_lyric=lyrics_data.get('yrc_lyric', ''),
            )

            self.playlist.insert(self.current_index + 1, storable)
            self.refreshRandom()
            event_bus.emit(PLAYLIST_CHANGED)
            self.playStorable(storable)

        def _prepare() -> None:
            try:
                prepared['image'] = requests.get(image_url).content
                prepared['lyrics'] = get_backend().get_track_lyrics(song_id)
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
                audio = get_backend().get_track_audio(song_id, bitrate=info['privilege']['max_br'])
                prepared['music_url'] = audio['url']
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

            def _on_downloaded(data: bytes) -> None:
                if not _is_current_search():
                    return
                prepared['music'] = data
                _try_play()

            downloadWithMultiThreading(
                music_url,
                _AUDIO_HEADERS,
                None,
                self._mwindow_obj,
                _on_downloaded,
            )

        doWithMultiThreading(_prepare, (), self._mwindow_obj, _on_prepared)
        doWithMultiThreading(
            _process_audio, (), self._mwindow_obj, _on_audio_prepare_done
        )

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

                def _after_download(success: bool) -> None:
                    if not success:
                        self._logger.warning('failed to download next song for preload')
                        return
                    if not _is_preload_current():
                        self._logger.info('discarding stale preload download')
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

                self.next_song_audio = audio # type: ignore
                if next_song.target_lufs != cfg.target_lufs:
                    gain = getAdjustedGainFactor(cfg.target_lufs, self.next_song_audio) # type: ignore
                    self._setStorableLoudness(next_song, cfg.target_lufs, gain)
                else:
                    gain = next_song.loudness_gain
                self.next_song_gain = gain

                self._logger.debug(
                    f'preload -> applying gain {self.next_song_gain} {cfg.target_lufs=}'
                )
                self.next_song_audio = self.next_song_audio.apply_gain( # type: ignore
                    20 * np.log10(self.next_song_gain)
                )

                self._logger.info('preloaded')
                self._logger.debug(
                    f'(Types) {type(self.next_song_audio)=} {type(self.next_song_gain)=}'
                )

                self.next_song_selection = selection
                self.preloaded = True

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
        song_storable._ensure_cache_fields()
        image_missing = not song_storable.image_cache_hash or not os.path.exists(
            os.path.join(IMAGE_DATA_DIR, song_storable.image_cache_hash)
        )
        music_missing = not song_storable.content_cache_hash or not os.path.exists(
            os.path.join(MUSIC_DATA_DIR, song_storable.content_cache_hash)
        )
        return image_missing, music_missing

    def _write_storable_asset(self, cache_dir: str, data: bytes) -> str:
        os.makedirs(cache_dir, exist_ok=True)
        cache_hash = hashlib.sha256(data).hexdigest()
        cache_path = os.path.join(cache_dir, cache_hash)
        if not os.path.exists(cache_path):
            with open(cache_path, 'wb') as f:
                f.write(data)
        return cache_hash

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
                    detail = get_backend().get_track_detail(song_storable.id)
                    image_url = detail['cover_url']
                    prepared['image'] = requests.get(image_url).content

                if music_missing:
                    audio = get_backend().get_track_audio(
                        str(song_storable.id),
                        bitrate=3200 * 1000,
                    )
                    self._logger.debug(f'{audio["url"]=}')
                    prepared['music_url'] = audio['url']

            except Exception as e:
                prepared['error'] = str(e)

        def _persist_assets(music_bytes: bytes | None = None) -> bool:
            try:
                image_just_persisted = False
                if image_missing:
                    image_bytes = prepared.get('image')
                    if not isinstance(image_bytes, bytes) or not image_bytes:
                        return False
                    song_storable.image_cache_hash = self._write_storable_asset(
                        IMAGE_DATA_DIR,
                        image_bytes,
                    )
                    image_just_persisted = True

                if music_missing:
                    if not music_bytes:
                        return False
                    song_storable.content_cache_hash = self._write_storable_asset(
                        MUSIC_DATA_DIR,
                        music_bytes,
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
                downloadWithMultiThreading(
                    music_url,
                    _AUDIO_HEADERS,
                    None,
                    self._mwindow_obj,
                    _play_after_persist,
                )
            else:
                _play_after_persist()

        doWithMultiThreading(_prepare, (), self._mwindow_obj, _on_prepared)

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
            audio = cached
        else:
            audio = AudioSegment_.from_file(io.BytesIO(music_bytes))
            if cache_key:
                cache_decoded_audio(cache_key, audio)

        if song_storable.target_lufs != cfg.target_lufs:
            gain = getAdjustedGainFactor(cfg.target_lufs, audio)
            self._setStorableLoudness(song_storable, cfg.target_lufs, gain)
        else:
            gain = song_storable.loudness_gain
        return audio.apply_gain(20 * np.log10(gain))

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

        doWithMultiThreading(_prepare, (), self._mwindow_obj, _finish)

    def _download_update_lyrics(self, song_storable: SongStorable) -> None:
        lyric_target = song_storable
        lyric_result: TrackLyricsInfo | None = None

        def _download() -> None:
            nonlocal lyric_result
            if not song_storable.yrc_lyrics_missing():
                return
            try:
                lyric_result = get_backend().get_track_lyrics(song_storable.id)
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
                transmgr.cur = lyrics['translated_lyric'] or '[00:00.000]'
                ymgr.cur = lyrics['yrc_lyric']
            else:
                mgr.cur = lyric_result.get('lyric', '[00:00.000]') or '[00:00.000]'
                transmgr.cur = (
                    lyric_result.get('translated_lyric', '[00:00.000]') or '[00:00.000]'
                )
                ymgr.cur = lyric_result.get('yrc_lyric', '')
                lyric_target.write_lyrics(
                    mgr.cur,
                    transmgr.cur if transmgr.cur != '[00:00.000]' else '',
                    ymgr.cur,
                )
                saveFavorites()

            mgr.parse()
            transmgr.parse()
            ymgr.parse()
            event_bus.emit(PLAYBACK_LYRICS_UPDATED, lyric_target)

        doWithMultiThreading(_download, (), self._mwindow_obj, _apply)

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
