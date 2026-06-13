from __future__ import annotations

import json
import logging

import sys
import threading
import time
from typing import Any, Callable

from core.app_context import AppContext

from core.backend import getBackend
from core.dialogs import getTextLineedit
from imports import (
    BACKGROUND_RATIO_CHANGED,
    ENDING_NO_SOUND,
    MWINDOW_REFRESH_FOLDERS,
    PLAY_CONTINUE_LAST_SONG,
    PLAY_SEARCH_SONG,
    REFRESH_RATE_CHANGED,
    REPAINT,
    SONG_FINISH,
    START_INTER_LOADING,
    START_PROGRESS_LOADING,
    STOP_INTER_LOADING,
    STOP_PROGRESS_LOADING,
    UPDATE_LOADING_PROGRESS,
    VIEW_FOLDER,
    WEBSOCKET_CONNECTED,
    WEBSOCKET_DISCONNECTED,
    FluentIcon,
    QAbstractAnimation,
    QEasingCurve,
    QFont,
    QListWidget,
    QListWidgetItem,
    QPropertyAnimation,
    QRect,
    QSize,
    QStackedWidget,
    Qt,
    QTimer,
    Signal,
    TransparentPushButton,
    event_bus,
)
from imports import QCloseEvent, QColor, QKeyEvent, QPainter
from views.list_widget import SListWidget
from imports import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    InfoBar,
)
from qfluentwidgets.window.fluent_window import FluentWindowBase

from core import theme
from core.models import CloudFolderInfo, LocalFolderInfo, SongStorable
from core.color import mixColor
from core.config import saveConfig, cfg
from core.favorites import favorites_manager, saveFavorites
from core.icons import bindIcon
from core.downloader import asyncTask
from views.folder_card import CloudFolderCard, LocalFolderCard
from views.line_edit import SearchLineEdit
from views.playing_controller import PlayingController
from views.song_card import SearchSongCard
from views.title_bar import SouthsideMusicTitleBar


class MainWindow(FluentWindowBase):
    scheduledTaskRequested = Signal()

    def __init__(
        self,
        ctx: AppContext,
        parent=None,
    ):
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self.ctx = ctx
        ctx.main_window = self
        self._app = ctx.app
        self._dp = ctx.playing_page
        self._sp = ctx.search_page
        self._dsp = ctx.desktop_lyrics_page
        self._fp = ctx.favorites_page
        self._sep = ctx.session_page
        self._player = ctx.player
        self._ws_server = ctx.ws_server
        self._ws_handler = ctx.ws_handler
        self._launchwindow = ctx.launch_window
        self._loading_song: bool = False
        self._stp = ctx.setting_page
        self._plp = ctx.playlist_page

        self.contents_widget = QStackedWidget()
        for w in [self._fp, self._sp, self._stp, self._sep]:
            if ctx.launch_window:
                ctx.launch_window.push(f'Adding {w} to stacked widget...')
            self.contents_widget.addWidget(w)

        self.contents_widget.currentChanged.connect(self.onStackedWidgetChanged)

        self._scheduled_tasks: list[
            tuple[Callable, tuple[Any, ...], dict[str, Any]]
        ] = []
        self._scheduled_tasks_lock = threading.Lock()
        self.scheduledTaskRequested.connect(self._runScheduledTasks)
        self.setTitleBar(SouthsideMusicTitleBar(self))

        contents_layout = QHBoxLayout()
        contents_widget = QWidget(self)
        contents_widget.setLayout(contents_layout)
        contents_layout.addWidget(self.contents_widget)

        contents_widget.setContentsMargins(0, 0, 0, 52)

        self.loading_tasks: int = 0
        self.loading_inter: bool = False
        self.loading_progressing: bool = False
        self.loading_progress: float = 0
        self.loading_ft = QFont(ctx.harmony_font_family)

        self.song_theme: QColor | None = None

        contents_layout.setContentsMargins(0, 48, 0, 0)

        self.controller = PlayingController(ctx)
        ctx.player.onFullFinished.connect(lambda: event_bus.emit(SONG_FINISH))
        ctx.player.onEndingNoSound.connect(ctx.playing_manager.onEndingNoSound)

        if ctx.launch_window:
            ctx.launch_window.top('  Wiring signal connections...')

        self.controller.setParent(self)

        self.folders_list = SListWidget()
        self.folders_list.itemClicked.connect(self._onFolderItemClicked)
        self.folders_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)

        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(0, 48, 0, 52)

        self.settings_btn = TransparentPushButton('Settings')
        bindIcon(self.settings_btn, 'settings')
        self.session_btn = TransparentPushButton('Account')
        bindIcon(self.session_btn, 'session')
        self.settings_btn.clicked.connect(self._onSettingsClicked)
        self.session_btn.clicked.connect(self._onSessionClicked)

        self.refresh_button = TransparentPushButton(FluentIcon.SYNC, 'Refresh')
        self.refresh_button.clicked.connect(lambda: self.refreshFolders())

        left_layout.addWidget(self.refresh_button)
        left_layout.addWidget(self.folders_list, 1)
        left_layout.addWidget(self.settings_btn)
        left_layout.addWidget(self.session_btn)

        self.hBoxLayout.addLayout(left_layout, 1)
        self.hBoxLayout.addWidget(contents_widget, 5)

        self.search_input = SearchLineEdit(self, ctx.harmony_font_family)
        self.search_input.returnPressed.connect(self.search)
        self.search_input.setParent(self)
        self.search_input.setFixedHeight(self.titleBar.height() - 15)
        self.search_input.move(
            self.minimumWidth() // 2,
            int((self.titleBar.height() - self.search_input.height()) * 0.5),
        )
        self.search_input.setFixedWidth(self.width() - self.minimumWidth())

        self.titleBar.raise_()
        self.search_input.raise_()

        self.closing = False
        self.connected = False

        self.draw_progress: float = 0
        self.bar_height: float = 0
        self.left: int = 5
        self.right: int = 150
        self.lmotion: int = 20
        self.rmotion: int = 20
        self.last_draw: int = time.perf_counter_ns()

        self.setWindowTitle('Southside Music')

        QTimer.singleShot(1750, ctx.ws_server.start)

        self.refresh_rate = max(60, ctx.app.primaryScreen().refreshRate() / 2)
        self._logger.info(f'{self.refresh_rate=}')

        self.delta = 1 / self.refresh_rate

        if self.ctx.dependences_available:
            self.show()

        self.setMinimumSize(ctx.app.primaryScreen().size() * 0.4)

        if cfg.window_width == 0 and cfg.window_height == 0:
            self.resize(ctx.app.primaryScreen().size() * 0.65)

            cfg.window_x = self.x()
            cfg.window_y = self.y()
            cfg.window_width = self.width()
            cfg.window_height = self.height()
        else:
            self.move(cfg.window_x, cfg.window_y)
            self.resize(cfg.window_width, cfg.window_height)

            if cfg.window_maximized:
                self.showMaximized()

        self.controller.setFixedSize(max(1, self.width()), 52)
        self.controller.move(0, self.height() - self.controller.height())

        self.dp_expanded = False
        self.dp_animating = False
        self._dp.setParent(self)
        self._dp.hide()
        self._dp.setFixedSize(self.size() - QSize(0, 100))
        self._dp.move(0, 48)
        self.controller.raise_()
        self.controller.show()

        self.pl_expanded = False
        self.pl_animating = False
        self._plp.setParent(self)
        self._plp.hide()
        self._plp.setFixedSize(int(self.width() * 0.45), self.height() - 110)
        self._plp.move(self.width() - 5 - self._plp.width(), 53)
        self.controller.raise_()
        self.controller.show()

        event_bus.subscribe(REFRESH_RATE_CHANGED, self._onRefreshRateChanged)
        event_bus.subscribe(REPAINT, self.updateDatas)
        event_bus.subscribe(START_INTER_LOADING, self.onStartInterLoading)
        event_bus.subscribe(STOP_INTER_LOADING, self.onStopInterLoading)
        event_bus.subscribe(STOP_PROGRESS_LOADING, self.onStopProgressLoading)
        event_bus.subscribe(START_PROGRESS_LOADING, self.onStartProgressLoading)
        event_bus.subscribe(UPDATE_LOADING_PROGRESS, self.onUpdateLoadingProgress)
        event_bus.subscribe(ENDING_NO_SOUND, lambda: event_bus.emit(SONG_FINISH))
        event_bus.subscribe(BACKGROUND_RATIO_CHANGED, self.update)
        event_bus.subscribe(VIEW_FOLDER, self.onViewFolder)
        event_bus.subscribe(MWINDOW_REFRESH_FOLDERS, self.refreshFolders)

    def onStackedWidgetChanged(self):
        if self.dp_expanded and not self.dp_animating:
            self.togglePlayingPageExpand()

    def _onSettingsClicked(self):
        self.contents_widget.setCurrentWidget(self._stp)

    def _onSessionClicked(self):
        self.contents_widget.setCurrentWidget(self._sep)

    def search(self):
        if not self.search_input.text().strip():
            InfoBar.warning('Search failed', 'the keyword is empty!', parent=self)
            return
        else:
            self.contents_widget.setCurrentWidget(self._sp)

        if self._sp.searching:
            return

        self._sp.search(self.search_input.text())

    def onViewFolder(self, folder: LocalFolderInfo | CloudFolderInfo):
        self.contents_widget.setCurrentWidget(self._fp)
        self._fp.setDisplayFolder(folder)

    def togglePlaylistExpand(self):
        self.pl_expanded = not self.pl_expanded
        self.pl_animating = True

        anim = QPropertyAnimation(self._plp, b'geometry', self)
        anim.setDuration(200)
        anim.setEasingCurve(
            QEasingCurve.Type.OutCirc if self.pl_expanded else QEasingCurve.Type.InCirc
        )

        r = self._plp.rect()
        if self.pl_expanded:
            self._plp.show()
            anim.setStartValue(QRect(self.width() + 5, 53, r.width(), r.height()))
            anim.setEndValue(
                QRect(self.width() - 5 - r.width(), 53, r.width(), r.height())
            )
        else:
            QTimer.singleShot(200, self._plp.hide)
            anim.setStartValue(
                QRect(self.width() - 5 - r.width(), 53, r.width(), r.height())
            )
            anim.setEndValue(QRect(self.width() + 5, 53, r.width(), r.height()))

        def fini():
            self.pl_animating = False

        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

        QTimer.singleShot(225, fini)

    def togglePlayingPageExpand(self):
        self.dp_expanded = not self.dp_expanded
        self.dp_animating = True

        anim = QPropertyAnimation(self._dp, b'geometry', self)
        anim.setDuration(200)
        anim.setEasingCurve(
            QEasingCurve.Type.OutCirc if self.dp_expanded else QEasingCurve.Type.InCirc
        )

        if self.dp_expanded:
            self._dp.show()
            anim.setStartValue(QRect(0, self.height(), self.width(), self.height()))
            anim.setEndValue(QRect(0, 48, self.width(), self.height()))
        else:
            QTimer.singleShot(200, self._dp.hide)
            anim.setStartValue(QRect(0, 48, self.width(), self.height()))
            anim.setEndValue(QRect(0, self.height(), self.width(), self.height()))

        def fini():
            self.dp_animating = False

        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

        if self.dp_expanded:
            self.controller.hideLyrics()
        else:
            self.controller.showLyrics()

        QTimer.singleShot(225, fini)

    def onStartInterLoading(self):
        self.loading_tasks += 1
        if not self.loading_progressing:
            self.loading_inter = True

    def onStopInterLoading(self):
        self.loading_tasks -= 1
        if self.loading_tasks <= 0:
            self.loading_tasks = 0
            self.loading_inter = False

    def onStopProgressLoading(self):
        self.loading_progressing = False
        if self.loading_tasks > 0:
            self.loading_inter = True
        else:
            self.loading_inter = False

    def onStartProgressLoading(self):
        self.loading_progressing = True
        if self.loading_tasks > 0:
            self.loading_inter = False

    def onUpdateLoadingProgress(self, progress: float):
        self.loading_progress = progress

    def _onRefreshRateChanged(self):
        self.refresh_rate = max(60, self._app.primaryScreen().refreshRate() / 2)
        self._logger.info(f'{self.refresh_rate=}')

        self.delta = 1 / self.refresh_rate

    def updateDatas(self):
        now = time.perf_counter_ns()
        _elapsed: float = (now - self.last_draw) / 1_000_000_000
        self.last_draw = now
        multiple_factor = _elapsed * self.refresh_rate / 2

        loading = self.loading_progressing or self.loading_tasks > 0

        self.bar_height += (
            ((5 if loading else 0) - self.bar_height) * multiple_factor * 0.3
        )
        self.bar_height = min(4, self.bar_height)

        if loading:
            self.draw_progress += (
                (self.loading_progress - self.draw_progress) * multiple_factor * 0.9
            )
        else:
            self.draw_progress = 0

        if self.bar_height > 0:
            if self.loading_inter:
                new_right = self.right + int(self.rmotion * multiple_factor)
                new_left = self.left + int(self.lmotion * multiple_factor * 1.25)

                if new_right > self.width():
                    self.rmotion = -abs(self.rmotion)
                elif new_right < 0:
                    self.rmotion = abs(self.rmotion)

                if new_left > self.width():
                    self.lmotion = -abs(self.lmotion)
                elif new_left < 0:
                    self.lmotion = abs(self.lmotion)

                self.right += int(self.rmotion * multiple_factor)
                self.left += int(self.lmotion * multiple_factor * 1.25)

                self.right = max(0, min(self.width(), self.right))
                self.left = max(0, min(self.width(), self.left))

            self.update()

    def addScheduledTask(self, task: Callable, *args, **kwargs) -> None:
        with self._scheduled_tasks_lock:
            self._scheduled_tasks.append((task, args, kwargs))
        self.scheduledTaskRequested.emit()

    def _runScheduledTasks(self) -> None:
        while True:
            with self._scheduled_tasks_lock:
                if not self._scheduled_tasks:
                    return
                task, args, kwargs = self._scheduled_tasks.pop(0)
            try:
                task(*args, **kwargs)
            except Exception as e:
                self._logger.exception('scheduled task failed')
                raise e

    def play(self, card: SearchSongCard):
        self._logger.debug(card.info.id)
        event_bus.emit(PLAY_SEARCH_SONG, card.info, card.detail.image_url)

    def init(self) -> None:
        self._launchwindow.clear()
        self._launchwindow.push('Initializing main window...')
        last_playlist: list[SongStorable] = []
        last_playing_index = -1

        def _init():
            nonlocal last_playlist, last_playing_index

            if cfg.last_playlist:
                last_playlist = cfg.last_playlist
                last_playing_index = cfg.last_playing_index
                self._dp.playlist.extend(last_playlist)

        def _finish_init():
            if last_playlist:
                self._launchwindow.top('restore playlist...')
                if 0 <= last_playing_index < len(last_playlist):
                    self._launchwindow.top('continue last song...')

                    def _continue():
                        event_bus.emit(PLAY_CONTINUE_LAST_SONG, cfg.last_playing_index)

                    self.addScheduledTask(_continue)

            self._launchwindow.top('refreshing login information')
            self._sep.refreshInformations()

            def _show():
                self.show()
                self.raise_()

                event_bus._lw = None
                self._launchwindow.deleteLater()

                self.refreshFolders()
                if favorites_manager.folders:
                    self._fp.setDisplayFolder(favorites_manager.folders[0])

            self.addScheduledTask(_show)

        asyncTask(_init, (), self, finished=_finish_init)

    def refreshFolders(self):
        self._fp.displayEmpty()
        open_folder = self._fp.curr_folder or self._fp.curr_cloud_folder

        self.refresh_button.setEnabled(False)
        self.folders_list.clear()

        self.folders_list.addItem(QListWidgetItem('Local'))

        for folder in favorites_manager.folders:
            card = LocalFolderCard(folder, self.folders_list.width())
            card.clicked.connect(lambda f=folder: self._openFolder(f))
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, folder)
            item.setSizeHint(card.sizeHint())
            self.folders_list.addItem(item)
            self.folders_list.setItemWidget(item, card)

        if (
            open_folder
            and not open_folder.get('id')
            and open_folder in favorites_manager.folders
        ):
            self._fp.setDisplayFolder(open_folder)

        item = QListWidgetItem()
        widget = TransparentPushButton(FluentIcon.ADD_TO, 'Add folder')
        widget.clicked.connect(self.onAddLocalFolder)
        item.setSizeHint(widget.sizeHint())
        self.folders_list.addItem(item)
        self.folders_list.setItemWidget(item, widget)

        def _cloud():
            self.addScheduledTask(
                lambda: self.folders_list.addItem(QListWidgetItem('Cloud'))
            )
            playlists = getBackend().getUserPlaylists()

            def add():
                nonlocal playlists
                for inf in playlists:
                    card = CloudFolderCard(inf, self.folders_list.width(), self.ctx)
                    card.clicked.connect(lambda f=inf: self._openFolder(f))
                    item = QListWidgetItem()
                    item.setData(Qt.ItemDataRole.UserRole, inf)
                    item.setSizeHint(card.sizeHint())
                    self.folders_list.addItem(item)
                    self.folders_list.setItemWidget(item, card)

                if open_folder and open_folder.get('id') and open_folder in playlists:
                    self._fp.setDisplayFolder(open_folder)

                item = QListWidgetItem()
                widget = TransparentPushButton(FluentIcon.ADD_TO, 'Add folder')
                widget.clicked.connect(self.onAddCloudFolder)
                item.setSizeHint(widget.sizeHint())
                self.folders_list.addItem(item)
                self.folders_list.setItemWidget(item, widget)

                self.refresh_button.setEnabled(True)

            self.addScheduledTask(add)

        if not getBackend().userAnonymous():
            asyncTask(_cloud, (), self)

        saveFavorites()

    def _openFolder(self, folder):
        self.contents_widget.setCurrentWidget(self._fp)
        self._fp.updateGeometry()
        self._fp.setDisplayFolder(folder)

    def onAddCloudFolder(self):
        name = getTextLineedit(
            'Add New Folder', 'enter name of your new folder', 'my folder', self
        )
        if name:
            getBackend().createPlaylist(name)
            self.refreshFolders()

    def onAddLocalFolder(self):
        name = getTextLineedit(
            'Add New Folder', 'enter name of your new folder', 'my folder', self
        )
        if name:
            new = favorites_manager.addFolder(name)
            self.refreshFolders()
            self._fp.setDisplayFolder(new)

    def _onFolderItemClicked(self, item: QListWidgetItem):
        self.contents_widget.setCurrentWidget(self._fp)
        self._fp.updateGeometry()
        folder = item.data(Qt.ItemDataRole.UserRole)
        if folder is not None:
            self._fp.setDisplayFolder(folder)

    def closeEvent(self, e: QCloseEvent):
        e.ignore()
        self.closing = True

        self.hide()
        self._player.stop()

        self._ws_server.stop()
        self._ws_server.join()

        cfg.last_playlist = self._dp.playlist.copy()
        cfg.last_playing_index = self._dp.current_index
        cfg.last_playing_time = self._player.getPosition()

        cfg.window_x = self.x()
        cfg.window_y = self.y()
        cfg.window_width = self.width()
        cfg.window_height = self.height()
        cfg.window_maximized = self.isMaximized()

        saveConfig()
        saveFavorites()

        self._ws_server.stop()
        self._ws_server.join()
        self._player.stop()

        sys.exit(0)

    def resizeEvent(self, e):
        self.titleBar.move(20, 0)
        self.titleBar.resize(self.width() - 20, self.titleBar.height())

        if hasattr(self, 'controller'):
            self.controller.setFixedSize(max(1, self.width()), 52)
            self.controller.move(0, self.height() - self.controller.height())

        if hasattr(self, '_dp'):
            self._dp.setFixedSize(self.size() - QSize(0, 100))
            self._dp.move(0, 48)

        if hasattr(self, '_plp'):
            self._plp.setFixedSize(int(self.width() * 0.45), self.height() - 110)

        if hasattr(self, 'controller'):
            self.controller.raise_()

        if hasattr(self, 'search_input'):
            self.search_input.move(
                self.minimumWidth() // 2,
                int((self.titleBar.height() - self.search_input.height()) * 0.5),
            )
            self.search_input.setFixedWidth(self.width() - self.minimumWidth())
            self.search_input.raise_()

    def onWebsocketConnected(self):
        InfoBar.success(
            'SouthsideClient connection',
            'SouthsideMusic was connected to SouthsidClient',
            duration=5000,
            parent=self,
        )
        QTimer.singleShot(
            500,
            lambda: self._ws_handler.send(
                json.dumps(
                    {
                        'option': f'{'disable' if not self._stp.enableFFT_box.isChecked() else 'enable'}_fft'
                    }
                )
            ),
        )

        self._dp.sendSongFMAndInfo()

        self.connected = True

        self._stp.disconnect_btn.setEnabled(True)
        self._stp.connect_btn.setEnabled(False)

        event_bus.emit(WEBSOCKET_CONNECTED)

    def onWebsocketDisconnected(self):
        InfoBar.warning(
            'SouthsideClient connection',
            'SouthsideMusic was been disconnected from SouthsidClient',
            duration=5000,
            parent=self,
        )

        self.connected = False

        self._stp.connect_btn.setEnabled(True)
        self._stp.disconnect_btn.setEnabled(False)

        event_bus.emit(WEBSOCKET_DISCONNECTED)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space:
            self.controller.toggle()
            event.accept()
        else:
            return super().keyPressEvent(event)

    def paintEvent(self, e):
        super().paintEvent(e)

        if not self.song_theme:
            self.song_theme = QColor(self.backgroundColor)

        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setFont(self.loading_ft)
        painter.setBrush(
            mixColor(
                self.song_theme, QColor(self.backgroundColor), cfg.background_ratio
            )
        )
        painter.drawRect(self.rect())

        loading = self.loading_progressing or self.loading_tasks > 0

        if loading or self.bar_height > 0.1:
            painter.setBrush(
                mixColor(
                    self.song_theme,
                    QColor(255, 255, 255) if theme.isDark() else QColor(0, 0, 0),
                    cfg.background_ratio,
                )
            )
            if self.loading_inter:
                painter.drawRect(
                    self.left, 0, self.right - self.left, int(self.bar_height)
                )
            else:
                painter.drawRect(
                    0, 0, int(self.width() * self.draw_progress), int(self.bar_height)
                )
            painter.setPen(QColor(255, 255, 255) if theme.isDark() else QColor(0, 0, 0))

            painter.drawText(
                4,
                int(self.bar_height + 18),
                f'Loading... ({f'{round(self.loading_progress * 100, 2)}%' if self.loading_progressing else self.loading_tasks})',
            )

        painter.end()
