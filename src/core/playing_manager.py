from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Literal, Optional, TypedDict
import time as timeLib

import numpy as np

import requests
from core.audio_player import (
    AudioPlayer,
    PatchedAudioSegment as AudioSegment_,
    cache_decoded_audio,
    decode_audio_with_sidecar,
    get_cached_audio,
    getAudioDevices,
)
from core.backend import getBackend
from core.config import cfg
from core.crossfade import CrossFadeInfo, getCrossfade
from core.downloader import asyncTask, asyncDownload
from core.favorites import saveFavorites
from core.free_threaded_worker import FreeThreadedJsonSender
from core.image import getAverageColorFromBytes
from core.loudness import getAdjustedGainFactor
from core.models import (
    IMAGE_DATA_DIR,
    MUSIC_DATA_DIR,
    SongStorable,
    TrackLyricsInfo,
)
from core.netease_backend import NeteaseCloudMusicBackend
from core.weighted_random import AdvancedRandom
from services.events.event_bus import event_bus
from services.events.events import (
    COLLECT_DEBUG_INFO,
    EMIT_DEBUG_INFO,
    ENDING_NO_SOUND,
    FINISH_CROSSFADE,
    IMAGE_ASSET_PERSISTED,
    PLAY_CONTINUE_LAST_SONG,
    PLAY_PLAYLIST_STORABLE,
    PLAY_SONG_AT_INDEX,
    PLAY_START_PLAYLIST,
    PLAY_STATE_CHANGED,
    PLAY_STORABLE,
    POST_PLAY_STORABLE,
    PLAYBACK_ERROR,
    PLAYBACK_IMAGE_LOADED,
    PLAYBACK_LYRICS_UPDATED,
    PLAYBACK_SONG_LOADING,
    PLAYLAST,
    PLAYLIST_CHANGED,
    PLAYNEXT,
    SONG_CHANGED,
    SONG_FINISH,
    START_CROSSFADE,
    START_PROGRESS_LOADING,
    STOP_PROGRESS_LOADING,
    UPDATE_LOADING_PROGRESS,
)
from imports import QTimer, tr

if TYPE_CHECKING:
    from core.app_context import AppContext


PlayMode = Literal['Repeat one', 'Repeat list', 'Shuffle', 'Play in order']

_AUDIO_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
}
_STREAM_SAMPLE_RATE = 44100
_STREAM_CHANNELS = 2
_STREAM_PCM_READ_BYTES = _STREAM_SAMPLE_RATE * _STREAM_CHANNELS * 4
_STREAM_PLAY_MIN_SECONDS = 5.0
_LYRIC_TIME_RE = re.compile(r'\[(\d+):(\d+(?:\.\d+)?)\]')


@dataclass(frozen=True)
class PlaySelection:
    index: int
    song: SongStorable
    mode: PlayMode
    by_user: bool
    base_index: int


class PlayingManager:
    def __init__(self, ctx: AppContext) -> None:
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
        self.crossfade_info: CrossFadeInfo | None = None
        self.next_song_selection: PlaySelection | None = None
        self.current_song_audio: AudioSegment_ | None = None
        self.current_song: SongStorable | None = None
        self._crossfade_player: AudioPlayer | None = None
        self._crossfade_selection: PlaySelection | None = None
        self._crossfade_result: dict[str, object] | None = None
        self._crossfade_gain_audio: AudioSegment_ | None = None
        self._crossfade_play_seq = 0
        self._crossfade_generation = 0
        self._crossfade_started = False
        self._gain_cache: dict[str, float] = {}
        self._play_seq = 0
        self._preload_download_seq = 0
        self._preload_download_song_id: str | None = None
        self._pending_play_selection: PlaySelection | None = None
        self._ft_worker = FreeThreadedJsonSender(logger=self._logger)
        self._stream_processes: set[subprocess.Popen[bytes]] = set()
        self._stream_process_lock = threading.Lock()
        self.crossfading = False
        self._play_storable_time: float = timeLib.time()
        self._last_storable: SongStorable | None = None

        if ctx is not None:
            self._bindEvents()
            ctx.app.aboutToQuit.connect(self.shutdownWorkers)
            threading.Thread(
                target=self._warmFreeThreadedWorker,
                daemon=True,
                name='southside-ft-worker-warmup',
            ).start()

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
            mode = self.ctx.setting_page.play_method_box.currentData()
            if mode in ('Repeat one', 'Repeat list', 'Shuffle', 'Play in order'):
                return mode
        return cfg.play_method

    def _bindEvents(self) -> None:
        event_bus.subscribe(SONG_CHANGED, self._onSongChangedEvent)
        event_bus.subscribe(SONG_FINISH, self.onSongFinish)
        event_bus.subscribe(PLAYNEXT, lambda: self.playNext(True))
        event_bus.subscribe(PLAYLAST, self.playLast)
        event_bus.subscribe(PLAY_STORABLE, self.playStorable)
        event_bus.subscribe(PLAY_PLAYLIST_STORABLE, self.playPlaylistStorable)
        event_bus.subscribe(PLAY_SONG_AT_INDEX, self.playSongAtIndex)
        event_bus.subscribe(PLAY_START_PLAYLIST, self.startPlaylist)
        event_bus.subscribe(PLAY_CONTINUE_LAST_SONG, self.continueLastSong)
        event_bus.subscribe(PLAYLIST_CHANGED, self.playlistChanged)
        event_bus.subscribe(COLLECT_DEBUG_INFO, self.emitDebugInfo)

    def emitDebugInfo(self):
        event_bus.emit(
            EMIT_DEBUG_INFO,
            'PlayingManager',
            [
                f'playlist_size={len(self.playlist)}',
                f'current_index={self.current_index}',
                f'total_length={self.total_length:.1f}',
                f'preloaded={self.preloaded}',
                f'current_song={self.current_song.name if self.current_song else None}',
                f'gain_cache={len(self._gain_cache)}',
                f'play_mode={self.play_mode}',
                f'reserved_next={self._reserved_next is not None}',
                f'preload_triggered={self._preload_triggered}',
                f'next_song_audio={self.next_song_audio is not None}',
                f'pending_play={self._pending_play_selection is not None}',
                f'last_play={self._play_storable_time}',
            ],
        )

    def playlistChanged(self):
        self.refreshRandom()
        self.clearReservedNext()
        self.clearPreload()

    def _emitError(self, title: str, message: str) -> None:
        event_bus.emit(PLAYBACK_ERROR, title, message)

    def onSongFinish(self) -> None:
        if self._crossfade_player is not None:
            return
        self.playNext(False)

    def _schedule(self, func: Callable, *args) -> None:
        self.ctx.addScheduledTask(func, *args)  # type: ignore

    def shutdownWorkers(self) -> None:
        self._play_seq += 1
        self._preload_download_seq += 1
        self._pending_play_selection = None
        self.current_song = None
        self.current_song_audio = None
        self._crossfade_generation += 1
        self._shutdownCrossfadePlayer()
        self._clearCrossfadePlaybackLoad()
        self.clearPreload()
        self._terminateStreamProcesses()
        self._ft_worker.shutdown()

    def _registerStreamProcess(self, process: subprocess.Popen[bytes]) -> None:
        with self._stream_process_lock:
            self._stream_processes.add(process)

    def _unregisterStreamProcess(self, process: subprocess.Popen[bytes]) -> None:
        with self._stream_process_lock:
            self._stream_processes.discard(process)

    def _terminateStreamProcesses(self) -> None:
        with self._stream_process_lock:
            processes = list(self._stream_processes)
            self._stream_processes.clear()
        for process in processes:
            self._terminateProcess(process)

    @staticmethod
    def _terminateProcess(
        process: subprocess.Popen[bytes],
        timeout: float = 0.5,
    ) -> None:
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
                try:
                    process.wait(timeout=timeout)
                except Exception:
                    pass
            except Exception:
                pass
        for pipe in (process.stdin, process.stdout, process.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except Exception:
                pass

    def _warmFreeThreadedWorker(self) -> None:
        self._callFreeThreadedWorker('base64_decode', {'data': ''}, timeout=10.0)

    def _callFreeThreadedWorker(
        self,
        op: str,
        payload: dict[str, object],
        timeout: float,
    ) -> object | None:
        try:
            return self._ft_worker.call(op, payload, timeout=timeout)
        except Exception as e:
            self._logger.debug('free-threaded worker %s failed: %s', op, e)
            return None

    def _averageColorFromBytes(self, image_bytes: bytes) -> list[float]:
        allow_local_fallback = threading.current_thread() is not threading.main_thread()
        timeout = 1.5 if allow_local_fallback else 0.1
        if allow_local_fallback or self._ft_worker.is_running():
            result = self._callFreeThreadedWorker(
                'average_color',
                {'image': image_bytes},
                timeout=timeout,
            )
            if isinstance(result, (list, tuple)) and len(result) >= 3:
                return [float(result[0]), float(result[1]), float(result[2])]
        if allow_local_fallback:
            return getAverageColorFromBytes(image_bytes)
        return [128, 128, 128]

    def _computeLoudnessGain(
        self,
        target_lufs: float,
        audio: AudioSegment_,
    ) -> float:
        try:
            samples = audio.get_array_of_samples()
            result = self._callFreeThreadedWorker(
                'loudness_gain',
                {
                    'target_lufs': float(target_lufs),
                    'samples': samples.tobytes(),
                    'sample_width': int(audio.sample_width),
                    'frame_rate': int(audio.frame_rate),
                },
                timeout=30.0,
            )
            if isinstance(result, (int, float)) and np.isfinite(result):
                return float(result)
        except Exception as e:
            self._logger.debug('free-threaded loudness payload failed: %s', e)
        return getAdjustedGainFactor(target_lufs, audio)

    def _decodeBase64(self, data: str) -> bytes:
        result = self._callFreeThreadedWorker(
            'base64_decode',
            {'data': data},
            timeout=10.0,
        )
        if isinstance(result, bytes):
            return result
        return base64.b64decode(data)

    def setPlaylist(self, playlist: list[SongStorable]) -> None:
        current_song = self.current_song
        self._cancelCrossfadePlayback()
        self.clearPreload()
        self.playlist = playlist
        self.refreshRandom()
        self.clearReservedNext()
        if current_song not in playlist:
            self.current_index = -1
            self.current_song = None
            self.current_song_audio = None
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
        self._crossfade_started = False
        self.next_song_audio = None
        self.next_song_gain = None
        self.crossfade_info = None
        self.next_song_selection = None
        self._clearCrossfadePlaybackLoad()
        self._preload_download_seq += 1
        self._preload_download_song_id = None
        self._pending_play_selection = None

    def _cancelCrossfadePlayback(self) -> None:
        self._crossfade_generation += 1
        self.crossfading = False
        self._shutdownCrossfadePlayer()
        self._clearCrossfadePlaybackLoad()

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

    def onPlayerPositionChanged(self, position: float) -> None:
        if self.crossfading:
            return
        if self._crossfade_started:
            return
        if not self._canStartCrossfade():
            return
        if self.crossfade_info is None or self.next_song_selection is None:
            return
        if position < self.crossfade_info.start_seconds:
            return
        self._crossfade_started = True
        self.playNext(False)

    def _computeCrossfadeInfo(
        self,
        current_audio: AudioSegment_ | None,
        next_audio: AudioSegment_,
    ) -> CrossFadeInfo | None:
        if not cfg.enable_crossfade:
            self._logger.info('crossfade skipped -> disabled')
            return None
        if current_audio is None:
            self._logger.info('crossfade skipped -> current audio missing')
            return None
        try:
            info = getCrossfade(
                current_audio,
                next_audio,
                self._lyricCrossfadeSeconds(),
                cfg.crossfade_strength,
            )
        except Exception:
            self._logger.exception('failed to compute crossfade timing')
            return None
        self._logger.info(
            'crossfade computed -> '
            f'start={info.start_seconds:.3f}s '
            f'fade={info.fade_seconds:.3f}s '
            f'end={info.end_seconds:.3f}s '
            f'speed={info.target_speed:.2f}x'
        )
        if info.fade_seconds <= 0:
            self._logger.info('crossfade skipped -> fade=0.000s')
            return None
        return info

    def _lyricCrossfadeSeconds(self) -> float:
        song = self.current_song
        if song is None:
            return 0.0
        total_seconds = self._storableDuration(song, self.total_length)
        if total_seconds <= 0:
            return 0.0
        try:
            lyrics = song.get_lyrics()
        except Exception:
            self._logger.exception('failed to read lyrics for crossfade timing')
            return 0.0
        last_seconds = 0.0
        for text in (
            lyrics.get('yrc_lyric', ''),
            lyrics.get('lyric', ''),
            lyrics.get('ytlrc_lyric', ''),
            lyrics.get('translated_lyric', ''),
        ):
            for match in _LYRIC_TIME_RE.finditer(text):
                seconds = int(match.group(1)) * 60 + float(match.group(2))
                last_seconds = max(last_seconds, seconds)
        if last_seconds <= 0 or last_seconds >= total_seconds:
            return 0.0
        return max(2.0, min(12.0, total_seconds - last_seconds))

    def _canStartCrossfade(self) -> bool:
        player = self._player
        return (
            cfg.enable_crossfade
            and cfg.crossfade_strength > 0
            and player is not None
            and player.isPlaying()
            and isinstance(self.next_song_audio, AudioSegment_)
            and isinstance(self.next_song_gain, float)
            and self.crossfade_info is not None
            and self.crossfade_info.fade_seconds > 0
            and self.next_song_selection is not None
        )

    def getDisplayPosition(self) -> float:
        if self.crossfading and self._crossfade_player is not None:
            return self._crossfade_player.getPosition()
        player = self._player
        return player.getPosition() if player is not None else 0.0

    def getDisplayLength(self) -> float:
        if self.crossfading and self._crossfade_player is not None:
            return self._crossfade_player.getLength()
        player = self._player
        return player.getLength() if player is not None else 0.0

    def getDisplayLoadedTime(self) -> float:
        if self.crossfading and self._crossfade_player is not None:
            return self._crossfade_player.getLoadedTime()
        player = self._player
        return player.getLoadedTime() if player is not None else 0.0

    def _shutdownCrossfadePlayer(self) -> None:
        player = self._crossfade_player
        self._crossfade_player = None
        self._crossfade_selection = None
        if player is None:
            return
        try:
            event_bus.unsubscribe(COLLECT_DEBUG_INFO, player.emitDebugInfo)
            player.shutdown()
        except Exception:
            self._logger.exception('failed to shutdown crossfade player')

    def _clearCrossfadePlaybackLoad(self) -> None:
        self._crossfade_result = None
        self._crossfade_gain_audio = None
        self._crossfade_play_seq = 0

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
            for favorite in folder.songs:  # type: ignore
                if str(favorite.id) != song_id:
                    continue
                if (
                    favorite.target_lufs == target_lufs
                    and favorite.loudness_gain == gain
                    and favorite.loaded_loudness_gain
                ):
                    continue
                favorite.target_lufs = target_lufs
                favorite.loudness_gain = gain
                favorite.loaded_loudness_gain = True
                favorites_changed = True

        song_storable.target_lufs = target_lufs
        song_storable.loudness_gain = gain
        song_storable.loaded_loudness_gain = True

        if favorites_changed:
            saveFavorites()

    @staticmethod
    def _hasLoadedLoudnessGain(song_storable: SongStorable) -> bool:
        return (
            song_storable.loaded_loudness_gain
            and song_storable.target_lufs == cfg.target_lufs
        )

    def _applyStoredLoudnessGain(self, song_storable: SongStorable) -> None:
        player = self._player
        if player is None:
            return
        gain = (
            song_storable.loudness_gain
            if self._hasLoadedLoudnessGain(song_storable)
            else 1.0
        )
        player.setGain(gain)

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
                        self._preload_triggered = False
                        if self._pending_play_selection:
                            self._pending_play_selection = None
                            self._emitError(
                                tr('playing_manager.playback_failed'),
                                tr('playing_manager.failed_to_download_song_assets'),
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
                        audio = decode_audio_with_sidecar(
                            song_bytes,
                            self._ft_worker,
                        )
                        if cache_key:
                            cache_decoded_audio(cache_key, audio)
                except Exception as e:
                    next_song.content_cache_hash = ''
                    saveFavorites()
                    self.next_song_audio = None
                    self.next_song_gain = None
                    self.crossfade_info = None
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
                self.crossfade_info = self._computeCrossfadeInfo(
                    self.current_song_audio,
                    self.next_song_audio,  # type: ignore
                )
                if not self._hasLoadedLoudnessGain(next_song):
                    gain = self._computeLoudnessGain(
                        cfg.target_lufs,
                        self.next_song_audio,  # type: ignore[arg-type]
                    )
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
            self._emitError(
                tr('playing_manager.warning'),
                tr('playing_manager.last_song_in_playlist'),
            )
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
        if self._canStartCrossfade():
            self._startCrossfade(selection)
            return
        self.playStorable(selection.song, preloaded_audio=self.next_song_audio)

    def _startCrossfade(self, selection: PlaySelection) -> None:
        player = self._player
        audio = self.next_song_audio
        gain = self.next_song_gain
        info = self.crossfade_info
        current_audio = self.current_song_audio
        selection_ready = (
            0 <= selection.index < len(self.playlist)
            and self.playlist[selection.index] is selection.song
            and self.current_index in (selection.base_index, selection.index)
        )
        if (
            player is None
            or not selection_ready
            or self.next_song_selection != selection
            or not isinstance(audio, AudioSegment_)
            or not isinstance(gain, float)
            or not isinstance(current_audio, AudioSegment_)
            or info is None
        ):
            self.playStorable(selection.song, preloaded_audio=audio)
            return

        event_bus.emit(START_CROSSFADE)
        self.crossfading = True
        self._shutdownCrossfadePlayer()
        self._crossfade_generation += 1
        generation = self._crossfade_generation
        duration_ms = max(1, int(info.fade_seconds * 1000))
        current_position = player.getPosition()
        play_speed = player.play_speed
        play_pitch = player.play_pitch

        crossfade_player = AudioPlayer()
        devices = getAudioDevices()
        if 0 <= cfg.output_device_index < len(devices):
            crossfade_player.setOutputDevice(devices[cfg.output_device_index])
        crossfade_player.setVolume(player.volume_gain)
        crossfade_player.setGain(player.loudness_gain)
        crossfade_player.setPlaySpeed(play_speed)
        crossfade_player.setPlayPitch(play_pitch)
        crossfade_player.load(current_audio)
        crossfade_player.play()
        crossfade_player.setPosition(current_position)
        self._crossfade_player = crossfade_player
        self._crossfade_selection = selection

        self.current_song = selection.song
        self.current_song_audio = audio
        self._play_seq += 1
        play_seq = self._play_seq
        player.stopPlaySpeedAnimation()
        player.setPlaySpeed(play_speed)
        player.setPlayPitch(play_pitch)
        player.load(audio)
        self.total_length = self._storableDuration(
            selection.song,
            player.getLength(),
        )
        player.setGain(gain)
        player.setVolume(0.0)
        player.play()
        result: dict[str, object] = {'audio': audio}
        self._loadPlaybackImage(selection.song, result)
        self._crossfade_result = result
        self._crossfade_gain_audio = audio
        self._crossfade_play_seq = play_seq

        crossfade_player.animateVolume(0.0, duration_ms)
        player.animateVolume(1.0, duration_ms)
        if info.target_speed != 1.0:
            crossfade_player.animatePlaySpeed(
                play_speed * info.target_speed, duration_ms
            )

        def _finish() -> None:
            self._finishCrossfade(selection, generation, play_seq)

        QTimer.singleShot(duration_ms, _finish)

    def _finishCrossfade(
        self,
        selection: PlaySelection,
        generation: int,
        play_seq: int,
    ) -> None:
        if generation != self._crossfade_generation:
            return
        if play_seq != self._play_seq:
            return
        if self._crossfade_selection != selection:
            return

        crossfade_player = self._crossfade_player
        player = self._player
        if crossfade_player is not None:
            crossfade_player.stopPlaySpeedAnimation()
            self._shutdownCrossfadePlayer()
        if player is not None:
            player.stopVolumeAnimation()
            player.setVolume(1.0)
        result = self._crossfade_result or {}
        gain_audio = self._crossfade_gain_audio
        play_seq = self._crossfade_play_seq
        self._crossfade_result = None
        self._crossfade_gain_audio = None
        self._crossfade_play_seq = 0
        self._finishPlaybackLoad(
            selection.song,
            play_seq,
            result,
            False,
            False,
            gain_audio,
        )
        event_bus.emit(FINISH_CROSSFADE)
        self.crossfading = False

    def playLast(self) -> None:
        selection = self.consumePreviousSelection(self.play_mode)
        if selection is None:
            self._emitError(
                tr('playing_manager.warning'),
                tr('playing_manager.first_song_in_playlist'),
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

        self._cancelCrossfadePlayback()
        self.clearPreload()
        self.current_index = index
        self.playStorable(self.playlist[index])

    def playPlaylistStorable(self, storable: SongStorable) -> None:
        try:
            self.current_index = self.playlist.index(storable)
        except ValueError:
            return
        self._cancelCrossfadePlayback()
        self.clearPreload()
        self.playStorable(storable)

    def _storable_asset_missing(self, song_storable: SongStorable) -> tuple[bool, bool]:
        backend = getBackend()
        return not song_storable.image_cached(), not song_storable.audio_cached(
            backend.loggedIn(), int(backend.getUserVipType())
        )

    def _downloadStorableMissingAssets(
        self,
        song_storable: SongStorable,
        image_missing: bool,
        music_missing: bool,
        finished: Callable[[bool], None],
    ) -> None:
        class PrepareInfo(TypedDict):
            error: Optional[str]
            image: Optional[bytes]
            music_url: Optional[str]

        prepared: PrepareInfo = PrepareInfo(error=None, image=None, music_url=None)

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
                    self._logger.warning(
                        'preload download: backend returned empty music URL '
                        '(song %s may not support requested bitrate 3200k)',
                        song_storable.id,
                    )
                    finished(False)
                    return
                asyncDownload(
                    music_url,
                    _AUDIO_HEADERS,
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
                    tr('playing_manager.playback_failed'),
                    tr('playing_manager.failed_to_download_missing_cached_files'),
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
        audio = decode_audio_with_sidecar(music_bytes, self._ft_worker)
        if cache_key:
            cache_decoded_audio(cache_key, audio)
        return audio

    def _storableDuration(
        self,
        song_storable: SongStorable,
        fallback: float = 0.0,
    ) -> float:
        if song_storable.duration > 0:
            return song_storable.duration / 1000
        return fallback

    def _loadPlaybackImage(
        self,
        song_storable: SongStorable,
        result: dict[str, object],
    ) -> None:
        try:
            image_bytes = song_storable.get_image_bytes()
            result['image'] = image_bytes
            result['avg_color'] = self._averageColorFromBytes(image_bytes)
        except Exception:
            self._logger.exception('failed to load image bytes for playback')

    def _finishPlaybackLoad(
        self,
        song_storable: SongStorable,
        play_seq: int,
        result: dict[str, object],
        pause_after_load: bool,
        mark_loaded: bool,
        gain_audio: AudioSegment_ | None,
    ) -> None:
        if play_seq != self._play_seq or self.current_song is not song_storable:
            return

        self.clearPreload()
        if mark_loaded:
            self.preloaded = True

        playlist_changed = False
        if song_storable not in self.playlist:
            self.playlist.insert(self.current_index, song_storable)
            self.refreshRandom()
            playlist_changed = True

        if playlist_changed:
            event_bus.emit(PLAYLIST_CHANGED)
        self._show_original_lyrics(song_storable)
        self._compute_gain_async(song_storable, gain_audio)
        self._download_update_lyrics(song_storable)

        event_bus.emit(SONG_CHANGED, song_storable)
        image_bytes = result.get('image')
        avg_color = result.get('avg_color')
        if isinstance(image_bytes, bytes):
            event_bus.emit(
                PLAYBACK_IMAGE_LOADED,
                song_storable,
                image_bytes,
                avg_color,
            )
        event_bus.emit(PLAY_STATE_CHANGED, not pause_after_load)
        event_bus.emit(POST_PLAY_STORABLE, song_storable)

        song_storable.increment_count(1)

        def _logAction():
            backend = getBackend()
            if isinstance(backend, NeteaseCloudMusicBackend):
                if self._last_storable:
                    backend.recordPlayed(
                        self._last_storable.id,
                        self._last_storable.name,
                        timeLib.time() - self._play_storable_time,
                    )
                    self._logger.info(
                        f'logged playback action id={self._last_storable.id}'
                    )
                backend.recordPlay(song_storable.id)
                self._logger.info(f'logged start play action id={song_storable.id}')

            self._play_storable_time = timeLib.time()
            self._last_storable = song_storable

        asyncTask(_logAction, (), self._mwindow_obj)

    def _playDownloadingStorable(
        self,
        song_storable: SongStorable,
        image_missing: bool,
        play_seq: int,
        mark_loaded: bool,
    ) -> None:
        class PrepareInfo(TypedDict):
            error: Optional[str]
            image: Optional[bytes]
            music_url: Optional[str]

        prepared: PrepareInfo = PrepareInfo(error=None, image=None, music_url=None)
        result: dict[str, object] = {}
        state = {
            'started': False,
            'starting': False,
            'download_success': False,
            'cancelled': False,
        }
        download_done = threading.Event()
        temp_path: Path | None = None

        def _is_current() -> bool:
            return play_seq == self._play_seq and self.current_song is song_storable

        def _cleanup(path: Path) -> None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                self._logger.exception('failed to delete stream temp file')

        def _try_start_playback(path: Path) -> None:
            state['starting'] = False
            if state['started'] or not _is_current():
                return
            player = self._player
            if player is None:
                return
            if player.getLoadedTime() <= 0:
                return

            state['started'] = True
            self.total_length = self._storableDuration(
                song_storable,
                player.getLength(),
            )
            self._applyStoredLoudnessGain(song_storable)
            player.play()
            self._loadPlaybackImage(song_storable, result)
            self._finishPlaybackLoad(
                song_storable,
                play_seq,
                result,
                False,
                mark_loaded,
                None,
            )

        def _schedule_start(path: Path) -> None:
            if state['started'] or state['starting']:
                return
            state['starting'] = True
            self._schedule(_try_start_playback, path)

        def _emit_progress(progress: float) -> None:
            if _is_current():
                event_bus.emit(UPDATE_LOADING_PROGRESS, progress)

        def _stop_progress() -> None:
            if _is_current():
                event_bus.emit(STOP_PROGRESS_LOADING)

        def _on_progress(downloaded: int, total_size: int) -> None:
            if total_size > 0:
                self._schedule(
                    _emit_progress,
                    min(1.0, downloaded / total_size),
                )

        def _cancel_process(process: subprocess.Popen[bytes]) -> None:
            state['cancelled'] = True
            self._terminateProcess(process, timeout=0.3)
            self._unregisterStreamProcess(process)

        def _decode_stream(path: Path, process: subprocess.Popen[bytes]) -> None:
            success = False
            try:
                stdout = process.stdout
                if stdout is None:
                    _cancel_process(process)
                    download_done.wait()
                    return
                while True:
                    pcm_data = stdout.read(_STREAM_PCM_READ_BYTES)
                    if not pcm_data:
                        break
                    player = self._player
                    if player is None or not _is_current():
                        _cancel_process(process)
                        download_done.wait()
                        return
                    loaded_time = player.appendGrowingStreamPcm(
                        path,
                        pcm_data,
                        _STREAM_CHANNELS,
                    )
                    if loaded_time >= _STREAM_PLAY_MIN_SECONDS:
                        _schedule_start(path)
                returncode = process.wait()
                download_done.wait()
                success = returncode == 0 and state['download_success']
            except Exception:
                self._logger.exception('failed to decode streaming audio')
            finally:
                if process.poll() is not None:
                    self._unregisterStreamProcess(process)
                self._schedule(_on_stream_finished, path, success)

        def _download(
            path: Path,
            music_url: str,
            process: subprocess.Popen[bytes],
        ) -> None:
            success = False
            downloaded = 0
            total_size = 0
            try:
                with requests.get(
                    music_url,
                    headers=_AUDIO_HEADERS,
                    stream=True,
                    timeout=30,
                ) as response:
                    response.raise_for_status()
                    content_length = response.headers.get('content-length')
                    if content_length:
                        total_size = int(content_length)

                    stdin = process.stdin
                    with open(path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=65536):
                            if not chunk:
                                continue
                            if not _is_current():
                                state['cancelled'] = True
                                return
                            f.write(chunk)
                            f.flush()
                            if stdin is not None:
                                try:
                                    stdin.write(chunk)
                                    stdin.flush()
                                except (BrokenPipeError, OSError):
                                    stdin = None
                            downloaded += len(chunk)
                            _on_progress(downloaded, total_size)

                success = True
                if success:
                    try:
                        with open(path, 'rb') as f:
                            music_bytes = f.read()
                        song_storable.cache_audio(music_bytes)
                        saveFavorites()
                    except Exception:
                        self._logger.exception(
                            'failed to persist complete streaming audio'
                        )
                        success = False
            except Exception:
                if not state['cancelled']:
                    self._logger.exception('failed to download streaming audio')
            finally:
                state['download_success'] = success
                try:
                    if process.stdin is not None:
                        process.stdin.close()
                except OSError:
                    pass
                if not success:
                    _cancel_process(process)
                download_done.set()

        def _on_stream_finished(path: Path, success: bool) -> None:
            _stop_progress()
            player = self._player
            if not success:
                if _is_current() and state['started'] and player is not None:
                    player.finishGrowingStream(path)
                _cleanup(path)
                if _is_current() and not state['started']:
                    if state['download_success']:
                        self.playStorable(song_storable)
                    else:
                        self._emitError(
                            tr('playing_manager.playback_failed'),
                            tr(
                                'playing_manager.failed_to_download_missing_cached_files'
                            ),
                        )
                return

            if _is_current() and player is not None:
                player.finishGrowingStream(path)
                if state['started']:
                    self.total_length = self._storableDuration(
                        song_storable,
                        player.getLength(),
                    )
                else:
                    _try_start_playback(path)
            _cleanup(path)

        def _prepare() -> None:
            try:
                if image_missing:
                    detail = getBackend().getTrackDetail(song_storable.id)
                    image_bytes = requests.get(detail.cover_url).content
                    prepared['image'] = image_bytes
                audio = getBackend().getTrackAudio(
                    str(song_storable.id),
                    bitrate=3200 * 1000,
                )
                prepared['music_url'] = audio.url
            except Exception as e:
                prepared['error'] = str(e)

        def _on_prepared() -> None:
            nonlocal temp_path
            if not _is_current():
                return
            if prepared.get('error'):
                self._logger.warning(
                    f'failed to prepare streaming playback: {prepared["error"]}'
                )
                self._emitError(
                    tr('playing_manager.playback_failed'),
                    tr('playing_manager.failed_to_download_missing_cached_files'),
                )
                return

            if image_missing:
                image_bytes = prepared.get('image')
                if not isinstance(image_bytes, bytes) or not image_bytes:
                    self._emitError(
                        tr('playing_manager.playback_failed'),
                        tr('playing_manager.failed_to_download_missing_cached_files'),
                    )
                    return
                song_storable.cache_image(image_bytes)
                saveFavorites()
                event_bus.emit(IMAGE_ASSET_PERSISTED, song_storable)

            music_url = prepared.get('music_url')
            if not isinstance(music_url, str) or not music_url:
                self._emitError(
                    tr('playing_manager.playback_failed'),
                    tr('playing_manager.failed_to_download_missing_cached_files'),
                )
                return

            os.makedirs(MUSIC_DATA_DIR, exist_ok=True)
            fd, raw_path = tempfile.mkstemp(
                prefix='stream_', suffix='.part', dir=MUSIC_DATA_DIR
            )
            os.close(fd)
            temp_path = Path(raw_path)
            event_bus.emit(START_PROGRESS_LOADING)
            event_bus.emit(UPDATE_LOADING_PROGRESS, 0.0)
            player = self._player
            if player is None:
                _cleanup(temp_path)
                return
            try:
                player.loadGrowingStream(
                    temp_path,
                    _STREAM_SAMPLE_RATE,
                    _STREAM_CHANNELS,
                )
                process = subprocess.Popen(
                    [
                        AudioSegment_.converter,
                        '-hide_banner',
                        '-loglevel',
                        'error',
                        '-i',
                        'pipe:0',
                        '-vn',
                        '-f',
                        'f32le',
                        '-acodec',
                        'pcm_f32le',
                        '-ac',
                        str(_STREAM_CHANNELS),
                        '-ar',
                        str(_STREAM_SAMPLE_RATE),
                        'pipe:1',
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                self._registerStreamProcess(process)
            except Exception:
                _cleanup(temp_path)
                self._logger.exception('failed to start streaming decoder')
                self._emitError(
                    tr('playing_manager.playback_failed'),
                    tr('playing_manager.failed_to_start_streaming_playback'),
                )
                return
            threading.Thread(
                target=lambda: _decode_stream(temp_path, process),  # type: ignore
                daemon=True,
            ).start()
            threading.Thread(
                target=lambda: _download(temp_path, music_url, process),  # type: ignore
                daemon=True,
            ).start()

        asyncTask(_prepare, (), self._mwindow_obj, _on_prepared)

    def _selectStorableForPlayback(self, song_storable: SongStorable) -> SongStorable:
        if 0 <= self.current_index < len(self.playlist):
            current_song = self.playlist[self.current_index]
            if current_song == song_storable:
                return current_song

        for index, song in enumerate(self.playlist):
            if song == song_storable:
                self.current_index = index
                return song

        insert_index = self.current_index + 1
        insert_index = max(0, min(insert_index, len(self.playlist)))
        self.playlist.insert(insert_index, song_storable)
        self.current_index = insert_index
        self.refreshRandom()
        event_bus.emit(PLAYLIST_CHANGED)
        return song_storable

    def playStorable(
        self,
        song_storable: SongStorable,
        preloaded_audio: AudioSegment_ | None = None,
        restore_position: float | None = None,
        pause_after_load: bool = False,
        mark_loaded: bool = False,
    ) -> None:
        if preloaded_audio is None:
            self.clearPreload()
        song_storable = self._selectStorableForPlayback(song_storable)
        self._logger.debug(f'{song_storable.target_lufs=} {cfg.target_lufs=}')

        player = self._player
        if player is None:
            return

        self._last_storable = self.current_song

        if preloaded_audio is None:
            self._cancelCrossfadePlayback()
        image_missing, music_missing = self._storable_asset_missing(song_storable)
        player.stop()
        player.setVolume(1.0)
        self.current_song = song_storable
        self.total_length = self._storableDuration(song_storable)
        self._play_seq += 1
        play_seq = self._play_seq
        event_bus.emit(STOP_PROGRESS_LOADING)
        event_bus.emit(PLAYBACK_SONG_LOADING, song_storable)
        app = self._app
        if app is not None:
            app.processEvents()

        if (
            music_missing
            and preloaded_audio is None
            and restore_position is None
            and not pause_after_load
        ):
            self._playDownloadingStorable(
                song_storable,
                image_missing,
                play_seq,
                mark_loaded,
            )
            return

        if image_missing or music_missing:
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

        result: dict[str, object] = {}

        def _prepare() -> None:
            mwindow = self._mwindow_obj
            if mwindow is not None:
                mwindow._loading_song = True
            audio = self._loadStorableAudio(song_storable, preloaded_audio)

            if mwindow is not None:
                mwindow._loading_song = False

            result['audio'] = audio
            self.current_song_audio = audio
            player.load(audio)
            self.total_length = self._storableDuration(
                song_storable,
                player.getLength(),
            )
            self._applyStoredLoudnessGain(song_storable)
            if not player.isPlaying():
                player.play()

            if restore_position is not None:
                player.setPosition(restore_position)
            if pause_after_load:
                player.pause()

            self._loadPlaybackImage(song_storable, result)

        def _finish() -> None:
            self._finishPlaybackLoad(
                song_storable,
                play_seq,
                result,
                pause_after_load,
                mark_loaded,
                result.get('audio'),  # type: ignore[arg-type]
            )

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

    def _compute_gain_async(
        self,
        song_storable: SongStorable,
        raw_audio: AudioSegment_ | None,
    ) -> None:
        if raw_audio is None:
            return
        if self._hasLoadedLoudnessGain(song_storable):
            return

        def _compute_and_apply() -> None:
            player = self._player
            if player is None:
                return
            gain = self._computeLoudnessGain(cfg.target_lufs, raw_audio)
            self._setStorableLoudness(song_storable, cfg.target_lufs, gain)
            if self.current_song is song_storable:
                self.ctx.addScheduledTask(  # type: ignore
                    lambda g=gain: player.animateLoudnessGain(g)
                )

        threading.Thread(target=_compute_and_apply, daemon=True).start()

    def _download_update_lyrics(self, song_storable: SongStorable) -> None:
        lyric_target = song_storable
        lyric_result: TrackLyricsInfo | None = None

        def _download() -> None:
            nonlocal lyric_result
            need_yrc = song_storable.yrc_lyrics_missing()
            need_translated_lyric = song_storable.translated_lyrics_missing()
            need_ytlrc = song_storable.ytlrc_missing()
            if not need_yrc and not need_translated_lyric and not need_ytlrc:
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
        self.loadMusicFromBytes(self._decodeBase64(content_base64), gain)

    def loadMusicFromBytes(self, music_bytes: bytes, gain: float) -> None:
        self._logger.debug(f'loading data {len(music_bytes)}')
        lock = self._lock
        if lock is None:
            audio = decode_audio_with_sidecar(music_bytes, self._ft_worker)
        else:
            with lock:
                audio = decode_audio_with_sidecar(music_bytes, self._ft_worker)

        self._logger.debug(f'applying gain {gain} {cfg.target_lufs=}')
        audio = audio.apply_gain(20 * np.log10(gain))

        player = self._player
        if player is None:
            return
        self.current_song_audio = audio
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
