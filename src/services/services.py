import logging
import os
import threading
import time

from services.events import SECOND_TICK
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.app_context import AppContext
from core.backend import getBackend
from core.config import cfg, saveConfig
from core.dialogs import getTextLineedit
from core.downloader import asyncTask
from imports import (
    BACKGROUND_RATIO_CHANGED,
    CLOUD_ADD_TO_LOCAL,
    CLOUD_REMOVE_FOLDER,
    CLOUD_RENAME_FOLDER,
    LOCAL_ADD_TO_CLOUD,
    MWINDOW_REFRESH_FOLDERS,
    PRE_THEME_CHANGED,
    REFRESH_RATE_CHANGED,
    LOCAL_REMOVE_FOLDER,
    LOCAL_RENAME_FOLDER,
    REPAINT,
    SONG_CHANGED,
    REPAINT_EVENT_INTERVAL,
    REPAINT_ALWAYS,
    InfoBar,
    MessageBox,
    QObject,
    QTimer,
    event_bus,
    tr,
)
from core import theme
from core.favorites import favorites_manager

from views.folder_card import CloudFolderCard, LocalFolderCard


class EventsServices(QObject):
    def __init__(self, ctx: 'AppContext') -> None:
        super().__init__()
        self.ctx = ctx
        self._app = ctx.app

        self._start_session_refresher()

        self.refresh_rate = max(60, self._app.primaryScreen().refreshRate() / 2)
        self.last_repaint = time.perf_counter_ns()
        self.last_always_repaint = time.perf_counter_ns()
        self.repaint_timer = QTimer(self)
        self.repaint_timer.timeout.connect(self._emitRepaint)
        self.repaint_timer.start(int(1000 / self.refresh_rate))

        self.repaint_always_timer = QTimer(self)
        self.repaint_always_timer.timeout.connect(self._emitAlwaysRepaint)
        self.repaint_always_timer.start(int(1000 / self.refresh_rate))
        self._app.primaryScreen().refreshRateChanged.connect(
            lambda: event_bus.emit(REFRESH_RATE_CHANGED)
        )
        event_bus.subscribe(REFRESH_RATE_CHANGED, self._onRefreshRateChanged)

        self.sec_timer = QTimer(self)
        self.sec_timer.timeout.connect(lambda: event_bus.emit(SECOND_TICK))
        self.sec_timer.start(1000)

        def _startListen():
            theme.getDarkdetect().listener(
                lambda t: event_bus.emit(PRE_THEME_CHANGED, t)
            )

        threading.Thread(target=_startListen, daemon=True).start()

        self.pids_collect_timer = QTimer(self)
        self.pids_collect_timer.timeout.connect(self.collectPids)
        self.pids_collect_timer.start(1000)

        self.last_interval = 0

        event_bus.subscribe(
            SONG_CHANGED, lambda s: event_bus.emit(BACKGROUND_RATIO_CHANGED)
        )
        event_bus.subscribe(LOCAL_REMOVE_FOLDER, self.localRemoveFolder)
        event_bus.subscribe(LOCAL_RENAME_FOLDER, self.localRenameFolder)
        event_bus.subscribe(LOCAL_ADD_TO_CLOUD, self.localAddToCloud)
        event_bus.subscribe(CLOUD_REMOVE_FOLDER, self.cloudRemoveFolder)
        event_bus.subscribe(CLOUD_RENAME_FOLDER, self.cloudRenameFolder)
        event_bus.subscribe(CLOUD_ADD_TO_LOCAL, self.cloudAddToLocal)
        event_bus.subscribe(REPAINT_EVENT_INTERVAL, self._setRepaintInterval)

    def _setRepaintInterval(self, interval):
        if int(interval) == self.last_interval:
            return
        self.last_interval = interval
        self.repaint_timer.setInterval(int(interval))

    def updateMemoryUsage(self):
        if not self.ctx.debugging:
            return

    def collectPids(self):
        if not self.ctx.debugging:
            return

        result: dict[str, int] = {}
        c = self.ctx
        ws_handler = c.ws_handler._ft_json_sender._process
        playing_manager = c.playing_manager._ft_worker._process
        result['main'] = os.getpid()
        if ws_handler:
            result['ws json sender'] = ws_handler.pid
        if playing_manager:
            result['ffmpeg decode'] = playing_manager.pid
        self.ctx.process_pids = result.copy()

    def cloudAddToLocal(self, card: CloudFolderCard):
        folder_name = card.folder.folder_name
        favorites_manager.addFolder(folder_name)

        def _add():
            response = getBackend().getPlaylistTracks(str(card.folder.id))
            for song in reversed(response):
                favorites_manager.addSong(folder_name, song)

        def _finished():
            event_bus.emit(MWINDOW_REFRESH_FOLDERS)
            self.ctx.addScheduledTask(
                lambda: InfoBar.success(
                    tr('events_services.imported_successfully'),
                    tr(
                        'events_services.folder_added_to_local',
                        folder_name=folder_name,
                    ),
                    duration=5000,
                    parent=self.ctx.main_window,
                )
            )

        asyncTask(_add, (), self, _finished)

    def localAddToCloud(self, card: LocalFolderCard):
        folder_name = card.folder.folder_name

        def _add():
            id_ = getBackend().createPlaylist(folder_name)
            getBackend().editPlaylist(
                'add', [song.id for song in reversed(card.folder.songs)], id_
            )

        def _finished():
            event_bus.emit(MWINDOW_REFRESH_FOLDERS)
            self.ctx.addScheduledTask(
                lambda: InfoBar.success(
                    tr('events_services.imported_successfully'),
                    tr(
                        'events_services.folder_added_to_cloud',
                        folder_name=folder_name,
                    ),
                    duration=5000,
                    parent=self.ctx.main_window,
                )
            )

        asyncTask(_add, (), self, _finished)

    def _confirmRemoveFolder(self, folder_name: str) -> bool:
        dialog = MessageBox(
            tr('events_services.remove_folder'),
            tr(
                'events_services.are_you_sure_to_remove_folder',
                folder_name=folder_name,
            ),
            self.ctx.main_window,
        )
        dialog.yesButton.setText(tr('events_services.remove'))
        dialog.cancelButton.setText(tr('events_services.cancel'))
        dialog.yesButton.setStyleSheet(
            dialog.yesButton.styleSheet()
            + 'PrimaryPushButton { color: white; background: #c42b1c; border: none; }'
            'PrimaryPushButton:hover { background: #d13438; border: none; }'
            'PrimaryPushButton:pressed { background: #a4262c; border: none; }'
        )
        return bool(dialog.exec())

    def cloudRemoveFolder(self, card: CloudFolderCard) -> None:
        confirmed = self._confirmRemoveFolder(card.folder.folder_name)
        if confirmed:
            getBackend().removePlaylist(card.folder.id)
            event_bus.emit(MWINDOW_REFRESH_FOLDERS)

    def cloudRenameFolder(self, card: CloudFolderCard):
        new_name = getTextLineedit(
            'events_services.rename_folder',
            'events_services.enter_new_name_of_your_folder',
            'events_services.my_folder',
            self.ctx.main_window,
        )
        if not new_name:
            return
        folder_name = card.folder.folder_name
        folder_id = card.folder.id

        def _rename():
            songs = getBackend().getPlaylistTracks(folder_id)
            new_id = getBackend().createPlaylist(new_name)
            getBackend().editPlaylist(
                'add', [song.id for song in reversed(songs)], new_id
            )
            getBackend().removePlaylist(folder_id)

        def _finished():
            event_bus.emit(MWINDOW_REFRESH_FOLDERS)
            self.ctx.addScheduledTask(
                lambda: InfoBar.success(
                    tr('events_services.renamed_successfully'),
                    tr(
                        'events_services.folder_renamed_to',
                        folder_name=folder_name,
                        new_name=new_name,
                    ),
                    duration=5000,
                    parent=self.ctx.main_window,
                )
            )

        asyncTask(_rename, (), self, _finished)

    def localRemoveFolder(self, card: LocalFolderCard) -> None:
        confirmed = self._confirmRemoveFolder(card.folder.folder_name)
        if confirmed:
            favorites_manager.removeFolder(card.folder.folder_name)
            event_bus.emit(MWINDOW_REFRESH_FOLDERS)

    def localRenameFolder(self, card: LocalFolderCard):
        new = getTextLineedit(
            'events_services.rename_folder',
            'events_services.enter_new_name_of_your_folder',
            'events_services.my_folder',
            self.ctx.main_window,
        )
        if new:
            favorites_manager.renameFolder(card.folder.folder_name, new)
            event_bus.emit(MWINDOW_REFRESH_FOLDERS)

    def _onRefreshRateChanged(self):
        self.refresh_rate = max(60, self._app.primaryScreen().refreshRate() / 2)

        self.repaint_timer.setInterval(int(1000 / self.refresh_rate))

    def _emitRepaint(self) -> None:
        now = time.perf_counter_ns()
        elapsed = min((now - self.last_repaint) / 1_000_000_000, 0.1)
        self.last_repaint = now
        multiple_factor = elapsed * self.refresh_rate
        event_bus.emit(REPAINT, multiple_factor)

    def _emitAlwaysRepaint(self) -> None:
        now = time.perf_counter_ns()
        elapsed = min((now - self.last_always_repaint) / 1_000_000_000, 0.1)
        self.last_always_repaint = now
        multiple_factor = elapsed * self.refresh_rate
        event_bus.emit(REPAINT_ALWAYS, multiple_factor)

    @staticmethod
    def _start_session_refresher() -> None:
        _logger = logging.getLogger(__name__)
        _stop_event = threading.Event()

        def _loop() -> None:
            while not _stop_event.wait(60):
                try:
                    snapshot = getBackend().refreshSessionIfNeeded()
                    if snapshot is not None:
                        _logger.info('session token near expiry, refreshing...')
                        cfg.session = snapshot.session
                        cfg.login_status = snapshot.login_status
                        saveConfig()
                        _logger.info('session token refreshed and saved')
                except Exception:
                    _logger.exception('session refresher error')

        threading.Thread(target=_loop, daemon=True).start()
        _logger.info('session refresher started')
