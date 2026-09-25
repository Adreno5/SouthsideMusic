from __future__ import annotations

import ctypes
import datetime
import logging
import os
import threading
import winreg
from typing import TYPE_CHECKING

from core.models import IMAGE_DATA_DIR, SongStorable, TrackDetailInfo
from imports import QObject, QTimer
from services.events.event_bus import event_bus
from services.events.events import (
    PLAY_STATE_CHANGED,
    PLAYLAST,
    PLAYNEXT,
    SONG_CHANGED,
)
from winrt.windows.media import (
    MediaPlaybackStatus,
    MediaPlaybackType,
    MediaPlaybackAutoRepeatMode,
    PlaybackPositionChangeRequestedEventArgs,
    SystemMediaTransportControls,
    SystemMediaTransportControlsButton,
    SystemMediaTransportControlsButtonPressedEventArgs,
    SystemMediaTransportControlsDisplayUpdater,
    SystemMediaTransportControlsTimelineProperties,
    interop,
)
from winrt.windows.storage.streams import (
    DataWriter,
    InMemoryRandomAccessStream,
    RandomAccessStreamReference,
)

if TYPE_CHECKING:
    from core.app_context import AppContext

_logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_ICON_PATH = os.path.join(_PROJECT_ROOT, 'icons', 'app.ico')
_LAUNCHER_RELATIVE_PATH = os.path.join('launcher', 'Launch.exe')
_LAUNCHER_PATH = os.path.join(_PROJECT_ROOT, _LAUNCHER_RELATIVE_PATH)
_INSTALL_PATH_KEY = r'SOFTWARE\Southside Music'

APP_USER_MODEL_ID = 'Adreno9135.SouthsideMusic'
APP_DISPLAY_NAME = 'Southside Music'


def _appUserModelId() -> str:
    for candidate in _launcherCandidates():
        if os.path.exists(candidate):
            return candidate
    return APP_USER_MODEL_ID


def _launcherCandidates() -> list[str]:
    candidates = [_LAUNCHER_PATH]
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _INSTALL_PATH_KEY) as handle:
            install_path = str(winreg.QueryValueEx(handle, 'InstallPath')[0])
        candidates.append(os.path.join(install_path, _LAUNCHER_RELATIVE_PATH))
    except OSError:
        pass
    return candidates


def initAppIdentity() -> None:
    try:
        aumid = _appUserModelId()
        shell32 = ctypes.windll.shell32
        shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
        shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
        shell32.SetCurrentProcessExplicitAppUserModelID(aumid)
        key = rf'Software\Classes\AppUserModelId\{aumid}'
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key) as handle:
            winreg.SetValueEx(handle, 'DisplayName', 0, winreg.REG_SZ, APP_DISPLAY_NAME)
            winreg.SetValueEx(handle, 'IconUri', 0, winreg.REG_SZ, _ICON_PATH)
    except Exception as e:
        _logger.exception(e)


def _thumbnailReference(song: SongStorable) -> RandomAccessStreamReference | None:
    if not song.image_cache_hash:
        return None
    path = os.path.join(IMAGE_DATA_DIR, song.image_cache_hash)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'rb') as handle:
            data = handle.read()
        memory = InMemoryRandomAccessStream()
        writer = DataWriter(memory)
        writer.write_bytes(data)
        writer.store_async().get_results()
        writer.detach_stream()
        memory.seek(0)
        return RandomAccessStreamReference.create_from_stream(memory)
    except Exception as e:
        _logger.exception(e)
        return None


class SmtcController(QObject):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._smtc: SystemMediaTransportControls | None = None
        self._cover: RandomAccessStreamReference | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        event_bus.subscribe(SONG_CHANGED, self._onSongChanged)
        event_bus.subscribe(PLAY_STATE_CHANGED, self._onPlayStateChanged)

    def _tick(self) -> None:
        self._updateTimeline()
        self._updatePlayMode()

    def setEnabled(self, enabled: bool) -> None:
        if enabled and self._smtc is None:
            self._create()
        if self._smtc is None:
            return
        self._smtc.is_enabled = enabled
        if not enabled:
            self._timer.stop()
            return
        self._updateMetadata()
        self._updateTimeline()
        self._updatePlayMode()
        self._setStatus(self.ctx.player.isPlaying())
        self._timer.start()

    def _create(self) -> None:
        window = self.ctx.main_window
        if window is None:
            _logger.warning('main window is missing, smtc stays off')
            return
        try:
            smtc = interop.get_for_window(int(window.winId()))
        except Exception as e:
            _logger.exception(e)
            return
        for attribute in (
            'is_enabled',
            'is_play_enabled',
            'is_pause_enabled',
            'is_next_enabled',
            'is_previous_enabled',
            'is_stop_enabled',
        ):
            setattr(smtc, attribute, True)
        smtc.add_button_pressed(self._onButtonPressed)
        smtc.add_playback_position_change_requested(self._onPositionRequested)
        self._smtc = smtc

    def _currentSong(self) -> SongStorable | None:
        manager = self.ctx.playing_manager
        if manager is None:
            return None
        playlist = manager.playlist
        index = manager.current_index
        if 0 <= index < len(playlist):
            return playlist[index]
        return None

    def _updateMetadata(self) -> None:
        if self._smtc is None:
            return
        updater: SystemMediaTransportControlsDisplayUpdater = self._smtc.display_updater
        song = self._currentSong()
        if song is None:
            updater.clear_all()
            updater.update()
            self._cover = None
            return
        updater.type = MediaPlaybackType.MUSIC
        updater.app_media_id = str(song.id)
        properties = updater.music_properties
        artists = ', '.join(artist.name for artist in song.artists)
        properties.title = song.name
        properties.artist = artists
        properties.album_artist = artists
        properties.album_title = ''
        properties.track_number = 0
        thumbnail = _thumbnailReference(song)
        if thumbnail is not None:
            self._cover = thumbnail
            updater.thumbnail = thumbnail
        updater.update()
        self._loadDetail(song)

    def _loadDetail(self, song: SongStorable) -> None:
        def _fetch() -> None:
            try:
                from core.backend import getBackend

                detail = getBackend().getTrackDetail(song.id)
            except Exception as e:
                _logger.exception(e)
                return
            self.ctx.addScheduledTask(self._applyDetail, song, detail)

        threading.Thread(target=_fetch, daemon=True).start()

    def _applyDetail(self, song: SongStorable, detail: TrackDetailInfo) -> None:
        if self._smtc is None or self._currentSong() != song:
            return
        updater = self._smtc.display_updater
        updater.music_properties.album_title = detail.album_name
        updater.music_properties.track_number = max(0, detail.track_no)
        updater.update()

    def _updateTimeline(self) -> None:
        if self._smtc is None:
            return
        duration = self.ctx.player.getLength()
        if duration <= 0:
            return
        position = min(max(self.ctx.player.getPosition(), 0.0), duration)
        timeline = SystemMediaTransportControlsTimelineProperties()
        timeline.start_time = datetime.timedelta(0)
        timeline.end_time = datetime.timedelta(seconds=duration)
        timeline.position = datetime.timedelta(seconds=position)
        timeline.min_seek_time = datetime.timedelta(0)
        timeline.max_seek_time = datetime.timedelta(seconds=duration)
        self._smtc.update_timeline_properties(timeline)

    def _updatePlayMode(self) -> None:
        manager = self.ctx.playing_manager
        if self._smtc is None or manager is None:
            return
        mode = manager.play_mode
        self._smtc.shuffle_enabled = mode == 'Shuffle'
        if mode == 'Repeat one':
            self._smtc.auto_repeat_mode = MediaPlaybackAutoRepeatMode.TRACK
        elif mode == 'Repeat list':
            self._smtc.auto_repeat_mode = MediaPlaybackAutoRepeatMode.LIST
        else:
            self._smtc.auto_repeat_mode = MediaPlaybackAutoRepeatMode.NONE

    def _setStatus(self, is_playing: bool) -> None:
        if self._smtc is None:
            return
        self._smtc.playback_status = (
            MediaPlaybackStatus.PLAYING if is_playing else MediaPlaybackStatus.PAUSED
        )

    def _setPlaying(self, playing: bool) -> None:
        if self.ctx.player.isPlaying() == playing:
            return
        controller = getattr(self.ctx.main_window, 'controller', None)
        if controller is None:
            return
        controller.toggle()

    def _handleButton(self, button: SystemMediaTransportControlsButton) -> None:
        if button in (
            SystemMediaTransportControlsButton.PLAY,
            SystemMediaTransportControlsButton.PAUSE,
            SystemMediaTransportControlsButton.STOP,
        ):
            self._setPlaying(button == SystemMediaTransportControlsButton.PLAY)
        elif button == SystemMediaTransportControlsButton.NEXT:
            event_bus.emit(PLAYNEXT)
        elif button == SystemMediaTransportControlsButton.PREVIOUS:
            event_bus.emit(PLAYLAST)

    def _onButtonPressed(
        self,
        _sender: object,
        args: SystemMediaTransportControlsButtonPressedEventArgs,
    ) -> None:
        self.ctx.addScheduledTask(self._handleButton, args.button)

    def _onPositionRequested(
        self,
        _sender: object,
        args: PlaybackPositionChangeRequestedEventArgs,
    ) -> None:
        seconds = args.requested_playback_position.total_seconds()
        self.ctx.addScheduledTask(self.ctx.player.setPosition, seconds)

    def _onSongChanged(self, _song_storable: SongStorable | None = None) -> None:
        self._updateMetadata()
        self._updateTimeline()

    def _onPlayStateChanged(self, is_playing: bool) -> None:
        self._setStatus(is_playing)
        self._updateTimeline()
