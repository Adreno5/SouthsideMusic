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
    PreparedAudioBuffer,
    cacheDecodedAudio,
    decodeAudioWithSidecar,
    getCachedAudio,
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
_STREAM_ANALYSIS_SECONDS = 30
_LYRIC_TIME_RE = re.compile(r'\[(\d+):(\d+(?:\.\d+)?)\]')
# Keep the queued crossfade PCM out of the way while the user is seeking.
_SEEK_CROSSFADE_DEBOUNCE_SECONDS = 1.0


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
        self.heart_mode = False
        self._heart_mode_playlist_id: str | None = None
        self._heart_mode_loading = False
        self.personal_fm = False
        self._personal_fm_loading = False
        self._private_radar_loading = False
        self._similar_songs_loading = False
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
        self._next_song_buffer: PreparedAudioBuffer | None = None
        self._queued_selection: PlaySelection | None = None
        self._queued_boundary: tuple[int, int] | None = None
        self._queued_frames = 0
        self._queued_crossfade_enabled = False
        self._transition_end: int | None = None
        self._gain_cache: dict[str, float] = {}
        self._play_seq = 0
        self._preload_download_seq = 0
        self._preload_download_song_id: str | None = None
        self._pending_play_selection: PlaySelection | None = None
        self._pending_crossfade_selection: PlaySelection | None = None
        self._crossfade_debounce_timer = QTimer()
        self._crossfade_debounce_timer.setSingleShot(True)
        self._crossfade_debounce_timer.setInterval(
            int(_SEEK_CROSSFADE_DEBOUNCE_SECONDS * 1000)
        )
        self._crossfade_debounce_timer.timeout.connect(self._flushDeferredCrossfade)
        self._ft_worker = FreeThreadedJsonSender(logger=self._logger)
        self._stream_processes: set[subprocess.Popen[bytes]] = set()
        self._stream_process_lock = threading.Lock()
        self._stream_analysis_tail: np.ndarray | None = None
        self._workers_shutdown = False
        self.crossfading = False
        self._play_storable_time: float = timeLib.time()
        self._last_storable: SongStorable | None = None

        if ctx is not None:
            self._bindEvents()
            ctx.player.seekRequested.connect(self._seekQueuedPlayback)
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
        crossfade_info = self.crossfade_info
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
                f'heart_mode={self.heart_mode}',
                f'personal_fm={self.personal_fm}',
                f'private_radar_loading={self._private_radar_loading}',
                f'similar_songs_loading={self._similar_songs_loading}',
                f'reserved_next={self._reserved_next is not None}',
                f'preload_triggered={self._preload_triggered}',
                f'next_song_audio={self.next_song_audio is not None}',
                f'crossfade_enabled={cfg.enable_crossfade}',
                f'crossfading={self.crossfading}',
                f'crossfade_info={crossfade_info is not None}',
                f'queued_selection={self._queued_selection is not None}',
                f'queued_boundary={self._queued_boundary}',
                f'pending_play={self._pending_play_selection is not None}',
                f'last_play={self._play_storable_time}',
            ],
        )
        event_bus.emit(
            EMIT_DEBUG_INFO,
            'Crossfade',
            crossfade_info.debugInfo()
            if crossfade_info is not None
            else ['crossfade_info=None'],
        )

    def playlistChanged(self):
        self.refreshRandom()
        self.clearReservedNext()
        self.clearPreload()

    def _emitError(self, title: str, message: str) -> None:
        event_bus.emit(PLAYBACK_ERROR, title, message)

    def onSongFinish(self) -> None:
        if self.crossfading:
            return
        self.playNext(False)

    def _schedule(self, func: Callable, *args) -> None:
        self.ctx.addScheduledTask(func, *args)  # type: ignore

    def shutdownWorkers(self) -> None:
        if self._workers_shutdown:
            return
        self._workers_shutdown = True
        self._logger.info('shutting down workers')
        self._play_seq += 1
        self._preload_download_seq += 1
        self._pending_play_selection = None
        self.current_song = None
        self.current_song_audio = None
        self.clearPreload()
        self._terminateStreamProcesses()
        self._ft_worker.shutdown()

    def stopPlaybackForShutdown(self) -> None:
        player = self._player
        if player is not None:
            player.stop()

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
        self.heart_mode = False
        self._heart_mode_playlist_id = None
        self._heart_mode_loading = False
        self.personal_fm = False
        self._personal_fm_loading = False
        self._private_radar_loading = False
        self._similar_songs_loading = False
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
        self.next_song_audio = None
        self.next_song_gain = None
        self.crossfade_info = None
        self.next_song_selection = None
        self._next_song_buffer = None
        self._crossfade_debounce_timer.stop()
        self._pending_crossfade_selection = None
        if self._queued_selection is not None and self._player is not None:
            self._player.cancelQueuedTrack()
        self._queued_selection = None
        self._queued_boundary = None
        self._preload_download_seq += 1
        self._preload_download_song_id = None
        self._pending_play_selection = None

    def _cancelCrossfadePlayback(self) -> None:
        if self._player is not None:
            self._player.cancelQueuedTrack()
        self._queued_selection = None
        self._queued_boundary = None
        self._transition_end = None
        self.crossfading = False
        event_bus.emit(FINISH_CROSSFADE)

    def _seekQueuedPlayback(self, seconds: float) -> None:
        player = self._player
        audio = self.current_song_audio
        if player is None or not isinstance(audio, AudioSegment_):
            return
        was_playing = player.isPlaying()
        rate = player.sample_rate
        gain = player.loudness_gain
        # Restoring the pre-splice PCM keeps the live timeline usable, so the
        # seek can reuse it instead of reloading the track and tearing the
        # output stream down.
        reusable = player.cancelQueuedTrack()
        self._cancelCrossfadePlayback()
        self.clearPreload()
        position = max(0.0, min(seconds, player.getLength()))
        if reusable and player.seekTimeline(position):
            self._preload_triggered = True
            self.preloadNextSong()
            return
        prepared = AudioPlayer.convertBuffer(AudioPlayer.prepareBuffer(audio), rate)
        player.loadPrepared(prepared)
        player.setGain(gain)
        position = max(0.0, min(seconds, player.getLength()))
        if was_playing:
            player.playFromPosition(position)
        else:
            player.setPosition(position)
            player.is_paused = True
        self._preload_triggered = True
        self.preloadNextSong()

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
        player = self._player
        if player is None:
            return
        if self._transition_end is not None:
            if player.current_index >= self._transition_end:
                self._transition_end = None
                self.crossfading = False
                event_bus.emit(FINISH_CROSSFADE)
        selection = self._queued_selection
        boundary = self._queued_boundary
        if selection is None or boundary is None:
            return
        if player.current_index >= boundary[0]:
            self._beginQueuedSong(selection, boundary)
        elif self._queued_crossfade_enabled != cfg.enable_crossfade:
            player.cancelQueuedTrack()
            self._queued_selection = None
            self._queued_boundary = None
            self._queuePreloadedSong(selection)

    def _beginQueuedSong(
        self, selection: PlaySelection, boundary: tuple[int, int]
    ) -> None:
        player = self._player
        audio = self.next_song_audio
        gain = self.next_song_gain
        if player is None or audio is None or gain is None:
            return
        if not self.isSelectionCurrent(selection):
            self.clearPreload()
            return
        frames = self._queued_frames
        self._queued_selection = None
        self._queued_boundary = None
        self.current_index = selection.index
        self.clearReservedNext()
        self.current_song = selection.song
        self.current_song_audio = audio
        self._play_seq += 1
        player.beginQueuedTrack(boundary[0], frames, gain)
        self.total_length = frames / player.sample_rate
        self.crossfading = boundary[1] > player.current_index
        self._transition_end = boundary[1] if self.crossfading else None
        if self.crossfading:
            event_bus.emit(START_CROSSFADE)
        result: dict[str, object] = {'audio': audio}
        self._loadPlaybackImage(selection.song, result)
        self._finishPlaybackLoad(
            selection.song, self._play_seq, result, player.is_paused, False, audio
        )

    def _computeCrossfadeInfo(
        self,
        current_audio: AudioSegment_ | None,
        next_audio: AudioSegment_,
        current_song_id: str | None = None,
        next_song_id: str | None = None,
        current_duration_seconds: float | None = None,
        current_gain: float = 1.0,
        next_gain: float = 1.0,
    ) -> CrossFadeInfo | None:
        if not cfg.enable_crossfade:
            self._logger.info('crossfade skipped -> disabled')
            return None
        if current_audio is None:
            self._logger.info('crossfade skipped -> current audio missing')
            return None
        rate = self._player.sample_rate if self._player is not None else current_audio.frame_rate
        current_audio = current_audio.set_frame_rate(rate).set_channels(2)
        next_audio = next_audio.set_frame_rate(rate).set_channels(2)
        crossfade_seconds = self._lyricCrossfadeSeconds()
        worker_info = self._computeCrossfadeInfoInWorker(
            current_audio,
            next_audio,
            crossfade_seconds,
            current_song_id,
            next_song_id,
            current_duration_seconds,
            current_gain,
            next_gain,
        )
        if worker_info is not None:
            info = worker_info
        else:
            try:
                info = getCrossfade(
                    current_audio,
                    next_audio,
                    crossfade_seconds,
                    cfg.crossfade_strength,
                    current_song_id=current_song_id,
                    next_song_id=next_song_id,
                    max_duration=cfg.crossfade_max_duration,
                    curve=cfg.crossfade_curve,
                    bpm_window=cfg.crossfade_bpm_window,
                    tempo_match=cfg.crossfade_tempo_match,
                    key_match=cfg.crossfade_key_match,
                    agc=cfg.crossfade_agc,
                    current_duration_seconds=current_duration_seconds,
                    current_gain=current_gain,
                    next_gain=next_gain,
                )
            except Exception as error:
                self._logger.exception('failed to compute crossfade timing: %s', error)
                return None
        self._logger.info(
            'crossfade computed -> '
            f'start={info.start_seconds:.3f}s '
            f'fade={info.fade_seconds:.3f}s '
            f'end={info.end_seconds:.3f}s '
            f'speed={info.target_speed:.2f}x '
            f'ending={info.ending_type} '
            f'key={info.current_key}->{info.next_key} '
            f'key_compat={info.key_compatibility:.2f} '
            f'transition={info.transition_type} '
            f'timbre={info.timbre_similarity:.2f}'
        )
        if info.fade_seconds <= 0:
            self._logger.info('crossfade skipped -> fade=0.000s')
            return None
        return info

    def _computeCrossfadeInfoInWorker(
        self,
        current_audio: AudioSegment_,
        next_audio: AudioSegment_,
        crossfade_seconds: float,
        current_song_id: str | None,
        next_song_id: str | None,
        current_duration_seconds: float | None,
        current_gain: float,
        next_gain: float,
    ) -> CrossFadeInfo | None:
        payload: dict[str, object] = {
            'current': current_audio.raw_data,
            'next': next_audio.raw_data,
            'current_sample_width': current_audio.sample_width,
            'current_sample_rate': current_audio.frame_rate,
            'current_channels': current_audio.channels,
            'next_sample_width': next_audio.sample_width,
            'next_sample_rate': next_audio.frame_rate,
            'next_channels': next_audio.channels,
            'crossfade_seconds': crossfade_seconds,
            'strength': cfg.crossfade_strength,
            'current_song_id': current_song_id,
            'next_song_id': next_song_id,
            'max_duration': cfg.crossfade_max_duration,
            'curve': cfg.crossfade_curve,
            'bpm_window': cfg.crossfade_bpm_window,
            'tempo_match': cfg.crossfade_tempo_match,
            'key_match': cfg.crossfade_key_match,
            'agc': cfg.crossfade_agc,
            'current_duration_seconds': current_duration_seconds,
            'current_gain': current_gain,
            'next_gain': next_gain,
        }
        result = self._ft_worker.call('crossfade', payload, timeout=60.0)
        if not isinstance(result, dict):
            return None
        samples_data = result.get('samples')
        samples_shape = result.get('samples_shape')
        if not isinstance(samples_data, bytes) or not isinstance(samples_shape, tuple):
            return None
        try:
            samples = np.frombuffer(samples_data, dtype=np.float32).reshape(
                tuple(int(value) for value in samples_shape)
            )
            return CrossFadeInfo(
                start_seconds=float(result['start_seconds']),
                fade_seconds=float(result['fade_seconds']),
                end_seconds=float(result['end_seconds']),
                sample_rate=int(result['sample_rate']),
                channels=int(result['channels']),
                samples=samples,
                target_speed=float(result.get('target_speed', 1.0)),
                ending_type=str(result.get('ending_type', '')),
                current_key=str(result.get('current_key', '')),
                next_key=str(result.get('next_key', '')),
                key_compatibility=float(result.get('key_compatibility', 0.0)),
                fade_out_profile=tuple(
                    float(value) for value in result.get('fade_out_profile', ())
                ),
                fade_in_profile=tuple(
                    float(value) for value in result.get('fade_in_profile', ())
                ),
                transition_type=str(result.get('transition_type', 'smart_crossfade')),
                timbre_similarity=float(result.get('timbre_similarity', 0.0)),
                beat_phase=float(result.get('beat_phase', 0.0)),
            )
        except (KeyError, TypeError, ValueError):
            self._logger.exception('invalid crossfade worker result')
            return None

    def _lyricCrossfadeSeconds(self) -> float:
        song = self.current_song
        if song is None:
            return 0.0
        total_seconds = self._storableDuration(song, self.total_length)
        if total_seconds <= 0:
            return 0.0
        try:
            lyrics = song.getLyrics()
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

    def getDisplayPosition(self) -> float:
        player = self._player
        return player.getPosition() if player is not None else 0.0

    def getDisplaySmoothPosition(self) -> float:
        player = self._player
        return player.getSmoothPosition() if player is not None else 0.0

    def getDisplayLength(self) -> float:
        player = self._player
        return player.getLength() if player is not None else 0.0

    def getDisplayLoadedTime(self) -> float:
        player = self._player
        return player.getLoadedTime() if player is not None else 0.0

    def getDisplayPreparedLead(self) -> float:
        player = self._player
        return player._producerPreparedLead() if player is not None else 0.0

    def setPlaySpeed(self, speed: float) -> None:
        player = self._player
        if player is not None:
            player.stopPlaySpeedAnimation()
            player.setPlaySpeed(speed)

    def setPlayPitch(self, pitch: float) -> None:
        player = self._player
        if player is not None:
            player.setPlayPitch(pitch)

    def restartPlaybackEffects(self) -> None:
        player = self._player
        if player is not None:
            player.restartProducer()

    def _deferCrossfadeQueue(self, player: AudioPlayer) -> bool:
        """Hold the crossfade PCM back until one second after the last seek."""
        elapsed = timeLib.perf_counter() - player.getLastSeekTime()
        if elapsed >= _SEEK_CROSSFADE_DEBOUNCE_SECONDS:
            return False
        remaining = _SEEK_CROSSFADE_DEBOUNCE_SECONDS - elapsed
        self._crossfade_debounce_timer.start(max(1, int(remaining * 1000)))
        return True

    def _flushDeferredCrossfade(self) -> None:
        selection = self._pending_crossfade_selection
        self._pending_crossfade_selection = None
        if selection is None:
            return
        self._queuePreloadedSong(selection)

    def _queuePreloadedSong(self, selection: PlaySelection) -> None:
        player = self._player
        following = self._next_song_buffer
        if (
            player is None
            or following is None
            or not self.isSelectionCurrent(selection)
            or selection != self.next_song_selection
            or self._queued_selection is not None
        ):
            return
        if self._deferCrossfadeQueue(player):
            self._pending_crossfade_selection = selection
            return
        info = self.crossfade_info if cfg.enable_crossfade else None
        transition = None
        start = player.getLength()
        if info is not None and info.fade_seconds > 0:
            transition = PreparedAudioBuffer(
                info.samples, info.sample_rate, info.channels
            )
            start = info.start_seconds
        boundary = player.queueNext(
            start, transition, following, self.next_song_gain or 1.0
        )
        if boundary is None and transition is not None:
            # Late analysis must not rewrite PCM that has reached the DAC.
            boundary = player.queueNext(
                player.getLength(), None, following, self.next_song_gain or 1.0
            )
        if boundary is None:
            self._logger.info('next PCM arrived too late to queue before EOF')
            return
        self._queued_selection = selection
        self._queued_boundary = boundary
        self._queued_crossfade_enabled = cfg.enable_crossfade
        self._queued_frames = round(
            len(following.samples) * player.sample_rate / following.sample_rate
        )
        self._logger.info('queued next PCM -> start=%s end=%s', *boundary)

    def _onSongChangedEvent(self, _song_storable: SongStorable) -> None:
        if self.personal_fm and self.current_index >= len(self.playlist) - 3:
            self._appendPersonalFMSongsAsync()
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
                        song_bytes = next_song.getMusicBytes()
                    else:
                        with lock:
                            song_bytes = next_song.getMusicBytes()
                    cache_key = next_song.content_cache_hash
                    cached = getCachedAudio(cache_key) if cache_key else None
                    if cached is not None:
                        audio = cached
                    else:
                        audio = decodeAudioWithSidecar(
                            song_bytes,
                            self._ft_worker,
                        )
                        if cache_key:
                            cacheDecodedAudio(cache_key, audio)
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
                if not self._hasLoadedLoudnessGain(next_song):
                    gain = self._computeLoudnessGain(
                        cfg.target_lufs,
                        self.next_song_audio,  # type: ignore[arg-type]
                    )
                    self._setStorableLoudness(next_song, cfg.target_lufs, gain)
                else:
                    gain = next_song.loudness_gain
                self.next_song_gain = gain
                current_id = (
                    self.current_song.content_cache_hash
                    if self.current_song is not None
                    else None
                )
                self.crossfade_info = self._computeCrossfadeInfo(
                    self.current_song_audio,
                    self.next_song_audio,  # type: ignore
                    current_song_id=current_id or None,
                    next_song_id=next_song.content_cache_hash or None,
                    current_gain=(
                        self._player.loudness_gain if self._player is not None else 1.0
                    ),
                    next_gain=gain,
                )

                next_buffer = AudioPlayer.prepareBuffer(audio)  # type: ignore
                if not _is_preload_current():
                    self._logger.info('discarding stale prepared crossfade buffers')
                    return
                self._next_song_buffer = next_buffer

                self._logger.debug(
                    f'preload -> gain {self.next_song_gain} {cfg.target_lufs=}'
                )
                self._logger.info('preloaded')
                self._logger.debug(
                    f'(Types) {type(self.next_song_audio)=} {type(self.next_song_gain)=}'
                )

                self.next_song_selection = selection
                self.preloaded = True
                self._schedule(self._queuePreloadedSong, selection)

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
        if self.heart_mode and self.current_index >= len(self.playlist) - 3:
            self._appendHeartModeSongsAsync()
        if self.personal_fm and self.current_index >= len(self.playlist) - 3:
            self._appendPersonalFMSongsAsync()

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
            self._logger.info('bypassing in-progress preload for manual next')
            self._preload_download_seq += 1
            self._preload_download_song_id = None
            self._pending_play_selection = None

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

    def _heartModeSeed(self) -> SongStorable | None:
        if self.current_song is not None:
            return self.current_song
        if 0 <= self.current_index < len(self.playlist):
            return self.playlist[self.current_index]
        if self.playlist:
            return self.playlist[0]
        return None

    def _appendHeartModeSongsAsync(self, count: int = 20) -> None:
        if (
            not self.heart_mode
            or self._heart_mode_playlist_id is None
            or self._heart_mode_loading
        ):
            return
        seed = self._heartModeSeed()
        if seed is None:
            return
        playlist_id = self._heart_mode_playlist_id
        self._heart_mode_loading = True

        def _load() -> None:
            try:
                songs = getBackend().getHeartModeSongs(
                    seed.id,
                    playlist_id,
                    start_music_id=seed.id,
                    count=count,
                )
            except Exception as e:
                self._logger.exception(e)
                return

            def _apply() -> None:
                if not self.heart_mode or self._heart_mode_playlist_id != playlist_id:
                    return
                existing_ids = {str(song.id) for song in self.playlist}
                added = [song for song in songs if str(song.id) not in existing_ids]
                if not added:
                    return
                self.playlist.extend(added)
                self.refreshRandom()
                event_bus.emit(PLAYLIST_CHANGED)

            self._schedule(_apply)

        asyncTask(
            _load,
            (),
            self._mwindow_obj,
            finished=lambda: setattr(self, '_heart_mode_loading', False),
        )

    def _appendPersonalFMSongsAsync(self) -> None:
        if not self.personal_fm or self._personal_fm_loading:
            return
        self._personal_fm_loading = True

        def _load() -> None:
            try:
                songs = getBackend().getPersonalFMSongs()
            except Exception as e:
                self._logger.exception(e)
                return

            def _apply() -> None:
                if not self.personal_fm:
                    return
                existing_ids = {str(song.id) for song in self.playlist}
                added = [song for song in songs if str(song.id) not in existing_ids]
                if not added:
                    return
                self.playlist.extend(added)
                self.refreshRandom()
                event_bus.emit(PLAYLIST_CHANGED)

            self._schedule(_apply)

        asyncTask(
            _load,
            (),
            self._mwindow_obj,
            finished=lambda: setattr(self, '_personal_fm_loading', False),
        )

    def startHeartMode(self) -> None:
        seed = self._heartModeSeed()

        def _emit_error(title: str, message: str) -> None:
            self._schedule(self._emitError, title, message)

        def _load(seed_song: SongStorable | None) -> None:
            try:
                backend = getBackend()
                if not backend.loggedIn():
                    _emit_error(
                        tr('home_page.heart_mode'),
                        tr('home_page.heart_mode_login_required'),
                    )
                    return

                liked_playlist = backend.getLikedPlaylist()
                if liked_playlist is None:
                    _emit_error(
                        tr('home_page.heart_mode'),
                        tr('home_page.heart_mode_no_seed'),
                    )
                    return

                seed = seed_song
                if seed is None:
                    liked_songs = backend.getPlaylistTracks(liked_playlist.id)
                    seed = liked_songs[0] if liked_songs else None
                if seed is None:
                    daily_songs = backend.getDailyRecommendSongs()
                    seed = daily_songs[0] if daily_songs else None
                if seed is None:
                    _emit_error(
                        tr('home_page.heart_mode'),
                        tr('home_page.heart_mode_no_seed'),
                    )
                    return

                songs = backend.getHeartModeSongs(
                    seed.id,
                    liked_playlist.id,
                    start_music_id=seed.id,
                    count=24,
                )
                if not songs:
                    _emit_error(
                        tr('home_page.heart_mode'),
                        tr('home_page.heart_mode_empty'),
                    )
                    return

                if all(str(song.id) != str(seed.id) for song in songs):
                    songs.insert(0, seed)

                def _apply() -> None:
                    self.heart_mode = True
                    self._heart_mode_playlist_id = liked_playlist.id
                    self.personal_fm = False
                    self._personal_fm_loading = False
                    self._private_radar_loading = False
                    self._similar_songs_loading = False
                    self._cancelCrossfadePlayback()
                    self.clearPreload()
                    self.playlist = songs
                    self.refreshRandom()
                    self.clearReservedNext()
                    self.current_index = 0
                    event_bus.emit(PLAYLIST_CHANGED)
                    self.playSongAtIndex(0)

                self._schedule(_apply)
            except Exception as e:
                self._logger.exception(e)
                _emit_error(
                    tr('home_page.heart_mode'),
                    tr('home_page.heart_mode_failed'),
                )

        asyncTask(_load, (seed,), self._mwindow_obj)

    def startPersonalFM(self) -> None:
        if self._personal_fm_loading:
            return
        self._personal_fm_loading = True

        def _emit_error(title: str, message: str) -> None:
            self._schedule(self._emitError, title, message)

        def _load() -> None:
            try:
                backend = getBackend()
                if not backend.loggedIn():
                    _emit_error(
                        tr('home_page.private_roam'),
                        tr('home_page.private_roam_login_required'),
                    )
                    return

                songs = backend.getPersonalFMSongs()
                if not songs:
                    _emit_error(
                        tr('home_page.private_roam'),
                        tr('home_page.private_roam_empty'),
                    )
                    return

                def _apply() -> None:
                    self.heart_mode = False
                    self._heart_mode_playlist_id = None
                    self._heart_mode_loading = False
                    self.personal_fm = True
                    self._private_radar_loading = False
                    self._similar_songs_loading = False
                    self._cancelCrossfadePlayback()
                    self.clearPreload()
                    self.playlist = songs
                    self.refreshRandom()
                    self.clearReservedNext()
                    self.current_index = 0
                    event_bus.emit(PLAYLIST_CHANGED)
                    self.playSongAtIndex(0)

                self._schedule(_apply)
            except Exception as e:
                self._logger.exception(e)
                _emit_error(
                    tr('home_page.private_roam'),
                    tr('home_page.private_roam_failed'),
                )

        asyncTask(
            _load,
            (),
            self._mwindow_obj,
            finished=lambda: setattr(self, '_personal_fm_loading', False),
        )

    def startPrivateRadar(self) -> None:
        if self._private_radar_loading:
            return
        self._private_radar_loading = True

        def _emit_error(title: str, message: str) -> None:
            self._schedule(self._emitError, title, message)

        def _load() -> None:
            try:
                backend = getBackend()
                if not backend.loggedIn():
                    _emit_error(
                        tr('home_page.private_radar'),
                        tr('home_page.private_radar_login_required'),
                    )
                    return

                songs = backend.getPrivateRadarSongs()
                if not songs:
                    _emit_error(
                        tr('home_page.private_radar'),
                        tr('home_page.private_radar_empty'),
                    )
                    return

                def _apply() -> None:
                    self.heart_mode = False
                    self._heart_mode_playlist_id = None
                    self._heart_mode_loading = False
                    self.personal_fm = False
                    self._personal_fm_loading = False
                    self._similar_songs_loading = False
                    self._cancelCrossfadePlayback()
                    self.clearPreload()
                    self.playlist = songs
                    self.refreshRandom()
                    self.clearReservedNext()
                    self.current_index = 0
                    event_bus.emit(PLAYLIST_CHANGED)
                    self.playSongAtIndex(0)

                self._schedule(_apply)
            except Exception as e:
                self._logger.exception(e)
                _emit_error(
                    tr('home_page.private_radar'),
                    tr('home_page.private_radar_failed'),
                )

        asyncTask(
            _load,
            (),
            self._mwindow_obj,
            finished=lambda: setattr(self, '_private_radar_loading', False),
        )

    def startSimilarSongs(self) -> None:
        if self._similar_songs_loading:
            return
        self._similar_songs_loading = True

        def _emit_error(title: str, message: str) -> None:
            self._schedule(self._emitError, title, message)

        def _load() -> None:
            try:
                backend = getBackend()
                if not backend.loggedIn():
                    _emit_error(
                        tr('home_page.similar_songs'),
                        tr('home_page.similar_songs_login_required'),
                    )
                    return

                songs = backend.getSimilarFMSongs()
                if not songs:
                    _emit_error(
                        tr('home_page.similar_songs'),
                        tr('home_page.similar_songs_empty'),
                    )
                    return

                def _apply() -> None:
                    self.heart_mode = False
                    self._heart_mode_playlist_id = None
                    self._heart_mode_loading = False
                    self.personal_fm = False
                    self._personal_fm_loading = False
                    self._private_radar_loading = False
                    self._cancelCrossfadePlayback()
                    self.clearPreload()
                    self.playlist = songs
                    self.refreshRandom()
                    self.clearReservedNext()
                    self.current_index = 0
                    event_bus.emit(PLAYLIST_CHANGED)
                    self.playSongAtIndex(0)

                self._schedule(_apply)
            except Exception as e:
                self._logger.exception(e)
                _emit_error(
                    tr('home_page.similar_songs'),
                    tr('home_page.similar_songs_failed'),
                )

        asyncTask(
            _load,
            (),
            self._mwindow_obj,
            finished=lambda: setattr(self, '_similar_songs_loading', False),
        )

    def _storable_asset_missing(self, song_storable: SongStorable) -> tuple[bool, bool]:
        backend = getBackend()
        return not song_storable.imageCached(), not song_storable.audioCached(
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
                    song_storable._writeCache(
                        image_bytes, IMAGE_DATA_DIR, 'image_cache_hash'
                    )
                    image_just_persisted = True

                if music_missing:
                    if not music_bytes:
                        return False
                    song_storable._writeCache(
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

        music_bytes = song_storable.getMusicBytes()
        cache_key = song_storable.content_cache_hash
        cached = getCachedAudio(cache_key) if cache_key else None
        if cached is not None:
            return cached
        audio = decodeAudioWithSidecar(music_bytes, self._ft_worker)
        if cache_key:
            cacheDecodedAudio(cache_key, audio)
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
            image_bytes = song_storable.getImageBytes()
            result['image'] = image_bytes
            result['avg_color'] = self._averageColorFromBytes(image_bytes)
        except FileNotFoundError as e:
            self._logger.warning('skipping missing playback image: %s', e)
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
        self._compute_gain_async(song_storable, gain_audio, play_seq)
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

        song_storable.incrementCount(1)

        def _logAction():
            backend = getBackend()
            if self._last_storable:
                backend.recordPlayed(
                    self._last_storable.id,
                    self._last_storable.name,
                    timeLib.time() - self._play_storable_time,
                )
                self._logger.info(f'logged playback action id={self._last_storable.id}')
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
        self._stream_analysis_tail = None

        class PrepareInfo(TypedDict):
            error: Optional[str]
            image: Optional[bytes]
            music_url: Optional[str]

        prepared: PrepareInfo = PrepareInfo(error=None, image=None, music_url=None)
        result: dict[str, object] = {}
        analysis_chunks: list[np.ndarray] = []
        analysis_frames = 0
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

        def _refresh_current_audio() -> None:
            if not _is_current():
                return
            try:
                audio = self._loadStorableAudio(song_storable)
            except Exception:
                self._logger.exception('failed to load completed streaming audio')
                return

            analysis_audio: AudioSegment_ | None = None
            analysis_duration = self._player.getLength() if self._player else 0.0
            tail = self._stream_analysis_tail
            if tail is not None and len(tail) > 0:
                pcm = np.clip(tail, -1.0, 1.0)
                pcm = (pcm * (2**31 - 1)).astype('<i4', copy=False)
                analysis_audio = AudioSegment_(
                    data=pcm.tobytes(),
                    sample_width=4,
                    frame_rate=_STREAM_SAMPLE_RATE,
                    channels=_STREAM_CHANNELS,
                )

            next_audio = self.next_song_audio
            next_selection = self.next_song_selection
            computed_crossfade: CrossFadeInfo | None = None
            prepared_next_buffer: PreparedAudioBuffer | None = None
            if (
                isinstance(next_audio, AudioSegment_)
                and next_selection is not None
                and self.isSelectionCurrent(next_selection)
            ):
                computed_crossfade = self._computeCrossfadeInfo(
                    analysis_audio if analysis_audio is not None else audio,
                    next_audio,
                    current_song_id=song_storable.content_cache_hash or None,
                    next_song_id=next_selection.song.content_cache_hash or None,
                    current_duration_seconds=(
                        analysis_duration if analysis_audio is not None else None
                    ),
                )
                prepared_next_buffer = AudioPlayer.prepareBuffer(next_audio)

            def _apply() -> None:
                if not _is_current():
                    return
                self.current_song_audio = audio
                self._compute_gain_async(song_storable, audio, play_seq)
                if (
                    prepared_next_buffer is not None
                    and self.next_song_audio is next_audio
                    and self.next_song_selection is next_selection
                ):
                    self.crossfade_info = computed_crossfade
                    self._next_song_buffer = prepared_next_buffer
                    if next_selection is not None:
                        self._queuePreloadedSong(next_selection)

            self._schedule(_apply)

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
            if player.getLength() <= 0:
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
            nonlocal analysis_frames
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
                    valid_len = len(pcm_data) - (len(pcm_data) % (_STREAM_CHANNELS * 4))
                    chunk = (
                        np.frombuffer(pcm_data[:valid_len], dtype='<f4')
                        .reshape(-1, _STREAM_CHANNELS)
                        .astype(np.float32, copy=True)
                    )
                    loaded_time = player.appendGrowingStreamPcm(
                        path,
                        pcm_data,
                        _STREAM_CHANNELS,
                    )
                    analysis_chunks.append(chunk)
                    analysis_frames += len(chunk)
                    max_frames = _STREAM_ANALYSIS_SECONDS * _STREAM_SAMPLE_RATE
                    if analysis_frames > max_frames:
                        trim = analysis_frames - max_frames
                        while trim > 0 and analysis_chunks:
                            first = analysis_chunks[0]
                            if len(first) <= trim:
                                trim -= len(first)
                                analysis_frames -= len(first)
                                analysis_chunks.pop(0)
                            else:
                                analysis_chunks[0] = first[trim:]
                                analysis_frames -= trim
                                trim = 0
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
                        song_storable.cacheAudio(music_bytes)
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
                self._stream_analysis_tail = (
                    np.concatenate(analysis_chunks, axis=0) if analysis_chunks else None
                )
                if state['started']:
                    self.total_length = self._storableDuration(
                        song_storable,
                        player.getLength(),
                    )
                else:
                    _try_start_playback(path)
                threading.Thread(
                    target=_refresh_current_audio,
                    daemon=True,
                ).start()
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
                song_storable.cacheImage(image_bytes)
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
        self.clearPreload()
        song_storable = self._selectStorableForPlayback(song_storable)
        self._logger.debug(f'{song_storable.target_lufs=} {cfg.target_lufs=}')

        player = self._player
        if player is None:
            return

        self._last_storable = self.current_song

        self._cancelCrossfadePlayback()
        image_missing, music_missing = self._storable_asset_missing(song_storable)
        player.stop()
        player.setVolume(1.0)
        self.current_song = song_storable
        self.current_song_audio = None
        self.total_length = self._storableDuration(song_storable)
        self._play_seq += 1
        play_seq = self._play_seq

        def _is_current_playback() -> bool:
            return play_seq == self._play_seq and self.current_song is song_storable

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
                lambda: (
                    self.playStorable(
                        song_storable,
                        preloaded_audio,
                        restore_position,
                        pause_after_load,
                        mark_loaded,
                    )
                    if _is_current_playback()
                    else None
                ),
            ):
                return

        result: dict[str, object] = {}

        def _prepare() -> None:
            if not _is_current_playback():
                return
            mwindow = self._mwindow_obj
            if mwindow is not None:
                mwindow._loading_song = True
            audio = self._loadStorableAudio(song_storable, preloaded_audio)

            if mwindow is not None:
                mwindow._loading_song = False

            if not _is_current_playback():
                return
            result['audio'] = audio
            self.current_song_audio = audio
            player.load(audio)
            if not _is_current_playback():
                return
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
        lyrics = song_storable.getLyrics()
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
        play_seq: int,
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

            def _apply() -> None:
                if play_seq == self._play_seq and self.current_song is song_storable:
                    player.animateLoudnessGain(gain)

            self.ctx.addScheduledTask(_apply)  # type: ignore

        threading.Thread(target=_compute_and_apply, daemon=True).start()

    def _download_update_lyrics(self, song_storable: SongStorable) -> None:
        lyric_target = song_storable
        lyric_result: TrackLyricsInfo | None = None

        def _download() -> None:
            nonlocal lyric_result
            need_yrc = song_storable.yrcLyricsMissing()
            need_translated_lyric = song_storable.translatedLyricsMissing()
            need_ytlrc = song_storable.ytlrcMissing()
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
                lyrics = lyric_target.getLyrics()
                mgr.cur = lyrics['lyric'] or '[00:00.000]'
                ymgr.cur = lyrics['yrc_lyric']
                ytlrc = lyrics.get('ytlrc_lyric', '')
                if ymgr.cur and ytlrc:
                    transmgr.cur = ytlrc
                else:
                    transmgr.cur = lyrics['translated_lyric'] or '[00:00.000]'
            else:
                lyrics = lyric_target.getLyrics()
                lyric = lyrics['lyric'] or lyric_result.lyric or '[00:00.000]'
                translated_lyric = (
                    lyrics['translated_lyric'] or lyric_result.translated_lyric or ''
                )
                yrc_lyric = lyrics['yrc_lyric'] or lyric_result.yrc_lyric or ''
                ytlrc = lyrics.get('ytlrc_lyric', '') or lyric_result.ytlrc_lyric or ''
                mgr.cur = lyric
                ymgr.cur = yrc_lyric
                if ymgr.cur and ytlrc:
                    transmgr.cur = ytlrc
                else:
                    transmgr.cur = translated_lyric or '[00:00.000]'
                lyric_target.writeLyrics(
                    mgr.cur,
                    translated_lyric,
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
            audio = decodeAudioWithSidecar(music_bytes, self._ft_worker)
        else:
            with lock:
                audio = decodeAudioWithSidecar(music_bytes, self._ft_worker)

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
