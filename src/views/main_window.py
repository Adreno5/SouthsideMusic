from __future__ import annotations

import json
import logging

import sys
import threading
import time
from typing import Any, Callable

from imports import (
    LYRIC_LINE_CHANGED,
    REFRESH_RATE_CHANGED,
    REPAINT,
    SONG_CHANGED,
    SWITCH_PAGE,
    WEBSOCKET_CONNECTED,
    WEBSOCKET_DISCONNECTED,
    QApplication,
    Qt,
    QTimer,
    Signal,
    QMutex,
    event_bus,
)
from imports import QCloseEvent, QColor, QIcon, QKeyEvent, QPainter
from imports import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    FluentIconBase,
    InfoBar,
    InfoBarPosition,
    NavigationInterface,
    NavigationItemPosition,
    NavigationTreeWidget,
    qrouter,
)
from qfluentwidgets.window.fluent_window import FluentWindowBase

from utils import darkdetect_util
from utils.base.base_util import SongStorable
from utils.color_util import mixColor
from utils.config_util import saveConfig, cfg
from utils.favorite_util import saveFavorites, favs
from utils.icon_util import getQIcon
from utils.loading_util import doWithMultiThreading
from views.playing_page import PlayingPage
from views.song_card import DummyCard, SongCard
from views.title_bar import SouthsideMusicTitleBar


class MainWindow(FluentWindowBase):
    scheduledTaskRequested = Signal()

    def __init__(
        self,
        app: QApplication,
        dp: PlayingPage,
        sp,
        dsp,
        fp,
        sep,
        sidebar,
        player,
        ws_server,
        ws_handler,
        launchwindow,
        parent=None,
    ):
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self._app = app
        self._dp = dp
        self._sp = sp
        self._dsp = dsp
        self._fp = fp
        self._sep = sep
        self._sidebar = sidebar
        self._player = player
        self._ws_server = ws_server
        self._ws_handler = ws_handler
        self._launchwindow = launchwindow
        self._loading_song: bool = False

        self._scheduled_tasks: list[
            tuple[Callable, tuple[Any, ...], dict[str, Any]]
        ] = []
        self._scheduled_tasks_lock = threading.Lock()
        self.scheduledTaskRequested.connect(self._runScheduledTasks)
        self.setTitleBar(SouthsideMusicTitleBar(self))

        self.navigationInterface = NavigationInterface(self, showReturnButton=True)
        self.widgetLayout = QVBoxLayout()

        contents_layout = QHBoxLayout()

        left_layout = QVBoxLayout()

        self.song_theme: QColor | None = None

        self.hBoxLayout.addWidget(self.navigationInterface)
        self.hBoxLayout.addLayout(self.widgetLayout)
        self.hBoxLayout.setStretchFactor(self.widgetLayout, 1)

        left_layout.addWidget(self.stackedWidget)
        contents_layout.setContentsMargins(0, 48, 0, 0)

        left_layout.addWidget(dp.controller, alignment=Qt.AlignmentFlag.AlignHCenter)
        contents_layout.addLayout(left_layout)
        contents_layout.addWidget(sidebar)
        self.widgetLayout.addLayout(contents_layout)

        self.navigationInterface.displayModeChanged.connect(self.titleBar.raise_)
        self.titleBar.raise_()

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

        self.addSubInterface(
            sp,
            getQIcon('music'),
            'Search',
        )
        self.addSubInterface(
            dp,
            getQIcon('studio'),
            'Playing',
        )
        self.addSubInterface(
            dsp,
            getQIcon('island'),
            'Desktop Lyrics',
        )
        self.addSubInterface(
            fp,
            getQIcon('fav'),
            'Favorites',
            NavigationItemPosition.BOTTOM,
        )
        self.addSubInterface(
            sep,
            getQIcon('session'),
            'Session',
            NavigationItemPosition.BOTTOM,
        )

        if cfg.window_width == 0 and cfg.window_height == 0:
            self.resize(app.primaryScreen().size() * 0.65)

            cfg.window_x = self.x()
            cfg.window_y = self.y()
            cfg.window_width = self.width()
            cfg.window_height = self.height()
        else:
            self.move(cfg.window_x, cfg.window_y)
            self.resize(cfg.window_width, 0)

            if cfg.window_maximized:
                QTimer.singleShot(500, self.showMaximized)

        QTimer.singleShot(1750, ws_server.start)

        self.refresh_rate = max(60, app.primaryScreen().refreshRate() / 2)
        self._logger.info(f'{self.refresh_rate=}')

        self.delta = 1 / self.refresh_rate

        event_bus.subscribe(SONG_CHANGED, self._onSongChanged)
        event_bus.subscribe(LYRIC_LINE_CHANGED, self._onLyricChanged)
        event_bus.subscribe(REFRESH_RATE_CHANGED, self._onRefreshRateChanged)
        event_bus.subscribe(REPAINT, self.updateDatas)

        self.stackedWidget.currentChanged.connect(self._syncTitleBarOnNavigation)

    def switchTo(self, interface: QWidget):
        event_bus.emit(SWITCH_PAGE, interface)
        return super().switchTo(interface)

    def _onRefreshRateChanged(self):
        self.refresh_rate = max(60, self._app.primaryScreen().refreshRate() / 2)
        self._logger.info(f'{self.refresh_rate=}')

        self.delta = 1 / self.refresh_rate

    def updateDatas(self):
        now = time.perf_counter_ns()
        _elapsed: float = (now - self.last_draw) / 1_000_000_000
        self.last_draw = now
        multiple_factor = _elapsed * self.refresh_rate / 2

        self.bar_height += ((5 if cfg.show_progress else 0) - self.bar_height) * multiple_factor * 0.3
        self.bar_height = min(4, self.bar_height)

        if cfg.show_progress:
            self.draw_progress += (cfg.progress - self.draw_progress) * multiple_factor * 0.9
        else:
            self.draw_progress = 0

        if self.bar_height > 0:
            if cfg.progress_inter:
                if self.right < 0 or self.right > self.width():
                    self.rmotion *= -1
                    if self.right < 0:
                        self.right = 0
                    elif self.right > self.width():
                        self.right = self.width()
                if self.left < 0 or self.left > self.width():
                    self.lmotion *= -1
                    if self.left < 0:
                        self.left = 0
                    elif self.left > self.width():
                        self.left = self.width()
                if abs(self.left - self.right) < 300:
                    self.lmotion *= -1
                    self.rmotion *= -1
                self.right += int(self.rmotion * multiple_factor)
                self.left += int(self.lmotion * multiple_factor * 1.25)

            self.repaint()

    def _syncTitleBarOnNavigation(self, _index: int):
        if not isinstance(self.titleBar, SouthsideMusicTitleBar):
            return
        if self.stackedWidget.currentWidget() is self._dp:
            self.titleBar.song_title.clear()
            self.titleBar.lyric_label.clear()
            self.titleBar.fm_label.clear()
        elif isinstance(self._dp.cur, DummyCard):
            self.titleBar.song_title.setText(self._dp.cur.storable.name)
            self.titleBar.lyric_label.setText(self._dp.cur.storable.artists)
            pixmap = self._dp.img_label.pixmap()
            if pixmap and not pixmap.isNull():
                self.titleBar.fm_label.setPixmap(
                    pixmap.scaled(
                        40,
                        40,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )

    def _onSongChanged(self, song_storable):
        if not isinstance(self.titleBar, SouthsideMusicTitleBar):
            return
        if self.stackedWidget.currentWidget() is self._dp:
            self.titleBar.song_title.clear()
            self.titleBar.lyric_label.clear()
            self.titleBar.fm_label.clear()
            return
        self.titleBar.song_title.setText(song_storable.name)
        self.titleBar.lyric_label.setText(song_storable.artists)
        pixmap = self._dp.img_label.pixmap()
        if pixmap and not pixmap.isNull():
            self.titleBar.fm_label.setPixmap(
                pixmap.scaled(
                    40,
                    40,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def _onLyricChanged(self, lyric_data: dict):
        if not isinstance(self.titleBar, SouthsideMusicTitleBar):
            return
        if self.stackedWidget.currentWidget() is self._dp:
            return
        content = lyric_data.get('content', '')
        self.titleBar.lyric_label.setText(
            content if content else self._dp.artists_label.text()
        )

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

    def addSubInterface(
        self,
        interface: QWidget,
        icon: FluentIconBase | QIcon | str,
        text: str,
        position=NavigationItemPosition.TOP,
        parent=None,
        isTransparent=False,
    ) -> NavigationTreeWidget:
        if not interface.objectName():
            raise ValueError('The object name of `interface` can\'t be empty string.')

        parentRouteKey = parent
        if parent and isinstance(parent, QWidget):
            parentRouteKey = parent.objectName()
            if not parentRouteKey:
                raise ValueError('The object name of `parent` can\'t be empty string.')

        interface.setProperty('isStackedTransparent', isTransparent)
        self.stackedWidget.addWidget(interface)

        routeKey = interface.objectName()
        item = self.navigationInterface.addItem(
            routeKey=routeKey,
            icon=icon,
            text=text,
            onClick=lambda: self.switchTo(interface),
            position=position,
            tooltip=text,
            parentRouteKey=parentRouteKey,  # type: ignore
        )

        if self.stackedWidget.count() == 1:
            self.stackedWidget.currentChanged.connect(self._onCurrentInterfaceChanged)
            self.navigationInterface.setCurrentItem(routeKey)
            qrouter.setDefaultRouteKey(self.stackedWidget, routeKey)  # type: ignore

        self._updateStackedBackground()

        return item

    def play(self, card: SongCard):
        self._logger.debug(card.info['id'])

        self._dp.cur = None

        self._dp.cur = card # type: ignore
        self._dp.init()

    def init(self) -> None:
        self._launchwindow.clear()
        self._launchwindow.push('Initializing main window...')
        last_playlist: list[SongStorable] = []
        last_playing_index = -1

        def _init():
            self._launchwindow.push('Initializing services...')

            self._sidebar.play_method_box.setCurrentText(cfg.play_method)

            nonlocal last_playlist, last_playing_index

            if cfg.last_playlist:
                last_playlist = cfg.last_playlist
                last_playing_index = cfg.last_playing_index
                self._dp.playlist.extend(last_playlist)

        def _finish_init():
            if last_playlist:
                self._launchwindow.top('restore playlist...')
                for storable in last_playlist:
                    self._sidebar.addSongCardToList(storable)
                if 0 <= last_playing_index < len(last_playlist):
                    self._launchwindow.top('continue last song...')

                    def _continue():
                        self._dp.continueLastSong(cfg.last_playing_index)

                    self.addScheduledTask(_continue)

            self._launchwindow.top('refreshing login information')
            self._sep.refreshInformations()

            def _show():
                self.show()
                self.raise_()

                event_bus._lw = None
                self._launchwindow.deleteLater()

            self.addScheduledTask(_show)

        doWithMultiThreading(_init, (), self, finished=_finish_init)

        InfoBar.info(
            'Initialization',
            f'Loaded {len(favs)} folders',
            parent=self,
            duration=2000,
        )

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

        cfg.play_method = self._sidebar.play_method_box.currentText()
        cfg.window_x = self.x() + (
            253 if self._dp.controller.expand_btn.text() == 'Collapse' else 0
        )
        cfg.window_y = self.y()
        cfg.window_width = self.width() - (
            505 if self._dp.controller.expand_btn.text() == 'Collapse' else 0
        )
        cfg.window_height = self.height()
        cfg.window_maximized = self.isMaximized()

        saveConfig()
        saveFavorites()

        self._ws_server.stop()
        self._ws_server.join()
        self._player.stop()

        sys.exit(0)

    def resizeEvent(self, e):
        self.titleBar.move(46, 0)
        self.titleBar.resize(self.width() - 46, self.titleBar.height())

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
                        'option': f'{'disable' if not self._sidebar.enableFFT_box.isChecked() else 'enable'}_fft'
                    }
                )
            ),
        )

        self._dp.sendSongFMAndInfo()

        self.connected = True

        self._sidebar.disconnect_btn.setEnabled(True)
        self._sidebar.connect_btn.setEnabled(False)

        event_bus.emit(WEBSOCKET_CONNECTED)

    def onWebsocketDisconnected(self):
        InfoBar.warning(
            'SouthsideClient connection',
            'SouthsideMusic was been disconnected from SouthsidClient',
            duration=5000,
            parent=self,
        )

        self.connected = False

        self._sidebar.connect_btn.setEnabled(True)
        self._sidebar.disconnect_btn.setEnabled(False)

        event_bus.emit(WEBSOCKET_DISCONNECTED)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space:
            self._dp.controller.toggle()
            event.accept()
        else:
            return super().keyPressEvent(event)

    def paintEvent(self, e):
        if not self.song_theme:
            self.song_theme = QColor(self.backgroundColor)

        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(
            mixColor(
                self.song_theme, QColor(self.backgroundColor), cfg.background_ratio
            )
        )
        painter.drawRect(self.rect())

        if cfg.show_progress or self.bar_height > 0.1:
            painter.setBrush(
                mixColor(
                    self.song_theme,
                    QColor(255, 255, 255)
                    if darkdetect_util.isDark()
                    else QColor(0, 0, 0),
                    cfg.background_ratio,
                )
            )
            if cfg.progress_inter:
                painter.drawRect(
                    self.left, 0, self.right - self.left, int(self.bar_height)
                )
            else:
                painter.drawRect(
                    0, 0, int(self.width() * self.draw_progress), int(self.bar_height)
                )
