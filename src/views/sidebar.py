from __future__ import annotations

import io
import logging

from typing import Callable, TYPE_CHECKING, cast, cast as _cast

if TYPE_CHECKING:
    from views.main_window import MainWindow
    from views.playing_page import PlayingPage
    from utils.play_util import AudioPlayer
    from utils.websocket_util import WebSocketHandler

import numpy as np
from imports import (
    LUFS_TARGET_CHANGED,
    SONG_CHANGED,
    VOLUME_CHANGED,
    WEBSOCKET_CONNECTED,
    WEBSOCKET_DISCONNECTED,
    QSize,
    Qt,
    QTimer,
    event_bus,
)
from imports import QColor
from imports import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CardWidget,
    CheckBox,
    ComboBox,
    DoubleSpinBox,
    InfoBar,
    ListWidget,
    Pivot,
    PrimaryPushButton,
    PushButton,
    Slider,
    SmoothScrollArea,
    SubtitleLabel,
    TitleLabel,
    TransparentPushButton,
)

from utils.base.base_util import SongStorable
from utils.icon_util import bindIcon
from utils import darkdetect_util as darkdetect
from utils.loading_util import doWithMultiThreading
from utils.loudness_balance_util import getAdjustedGainFactor
from utils.play_util import AudioSegment, AudioPlayer
from utils.config_util import cfg
from views.song_card import DummyCard, PlaylistSongCard
from utils.websocket_util import (
    WebSocketServer,
    WebSocketHandler,
    QObjectHandler,
    ws_server,
    ws_handler,
)


class Sidebar(QWidget):
    def __init__(
        self,
        dp: PlayingPage | None = None,
        mwindow: MainWindow | None = None,
        player: AudioPlayer | None = None,
        ws_server=None,
        ws_handler=None,
        app=None,
        launchwindow=None,
    ):
        super().__init__()
        self._logger = logging.getLogger(__name__)
        if launchwindow:
            launchwindow.top("Initializing sidebar...")
            self._launchwindow = launchwindow
        else:
            self._launchwindow = None
        self._dp: PlayingPage = dp  # type: ignore
        self._mwindow: MainWindow = mwindow  # type: ignore
        self._player: AudioPlayer = player  # type: ignore
        self._ws_server: WebSocketServer = ws_server  # type: ignore
        self._ws_handler: QObjectHandler = ws_handler  # type: ignore
        self._app = app

        self.setFixedWidth(500)

        layout = QVBoxLayout()
        self.pivot = Pivot(self)
        self.stacked_widget = QStackedWidget(self)
        layout.addWidget(self.pivot)
        layout.addWidget(self.stacked_widget)
        layout.setContentsMargins(30, 0, 30, 30)

        self.lst_interface = QWidget()
        self.lst_layout = QVBoxLayout()
        self.lst = ListWidget()
        self.lst.setFixedWidth(500)
        self.lst.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.lst_layout.addWidget(self.lst)

        self._song_cards: list[PlaylistSongCard] = []
        self._lazy_timer = QTimer(self)
        self._lazy_timer.timeout.connect(self._checkVisibleCards)
        self._lazy_timer.start(50)

        btn_layout = QHBoxLayout()
        self.removeall_btn = TransparentPushButton("Remove All")
        bindIcon(self.removeall_btn, "clearall")
        self.removeall_btn.clicked.connect(self.removeAllSongs)
        btn_layout.addWidget(self.removeall_btn)
        self.lst_layout.addLayout(btn_layout)
        self.lst_interface.setLayout(self.lst_layout)

        self.playing_scrollarea = SmoothScrollArea()
        self.options_interface = QWidget()
        self.updateTheme()
        self.playing_layout = QGridLayout()
        self.options_interface.setLayout(self.playing_layout)
        self.playing_scrollarea.setWidget(self.options_interface)
        self.playing_scrollarea.setWidgetResizable(True)

        self._initOptions()

        self.addSubInterface(self.lst_interface, "playlist_listwidget", "Playlist")
        self.addSubInterface(self.playing_scrollarea, "options_interface", "Options")
        self.stacked_widget.setCurrentWidget(self.lst_interface)
        self.pivot.setCurrentItem("playlist_listwidget")
        self.pivot.currentItemChanged.connect(
            lambda k: self.stacked_widget.setCurrentWidget(self.findChild(QWidget, k))  # type: ignore[arg-type]
        )

        self.setLayout(layout)
        self.hide()

    def addSubInterface(self, widget: QWidget, objectName, text):
        widget.setObjectName(objectName)
        self.stacked_widget.addWidget(widget)
        self.pivot.addItem(routeKey=objectName, text=text)

    def updateTheme(self) -> None:
        self.options_interface.setStyleSheet(
            f"background: #{'000000' if darkdetect.isDark() else 'FFFFFF'}"
        )

    def _initOptions(self) -> None:
        lw = self._launchwindow
        if lw:
            lw.push("Setting up sidebar options...")

        self.addSeparateWidget(TitleLabel("Playing"))

        self.play_method_box = ComboBox()
        self.play_method_box.addItems(
            ["Repeat one", "Repeat list", "Shuffle", "Play in order"]
        )
        self.play_method_box.setCurrentText("Repeat list")
        self.addSetting("Play order", "the order of play", self.play_method_box)

        self.addCheckSetting("Enable Stereo", "enable stereo effect", "stereo")
        self.addCheckSetting(
            "Smart Skip", "Skip the no sound section when song ends", "skip_nosound"
        )
        self.addNumberSetting(
            "Playback Speed",
            "speed of playing",
            0.1,
            3,
            0.1,
            "play_speed",
            lambda val: self._player.setPlaySpeed(val),
        )
        self.addNumberSetting(
            "Skip Threshold", "the threshold of the skip", -100, 0, 1, "skip_threshold"
        )
        self.now_volume = QLabel(f"Current volume(db): {0}")
        self.addSeparateWidget(self.now_volume)

        self.addNumberSetting(
            "Remain time to Skip",
            "start detecting volume during the remaining specified seconds",
            1,
            60,
            1,
            "skip_remain_time",
        )

        if lw:
            lw.top("Setting up window options...")
        self.addSeparateWidget(TitleLabel("Window"))
        self.addNumberSetting(
            "Window Background Mix Ratio",
            "larger value make color of backgound nearly to image of playing song",
            0,
            1,
            0.05,
            "background_ratio",
            lambda v: self._mwindow.repaint(),
        )

        if lw:
            lw.top("Setting up lyrics options...")
        self.addSeparateWidget(TitleLabel("Lyrics"))
        self.addNumberSetting(
            "Lyrics Smooth Factor",
            "larger value means a more sudden change",
            0,
            1,
            0.01,
            "lyrics_smooth_factor",
        )
        self.addNumberSetting(
            "Acceleration Smooth Factor",
            "smaller value means a more bounce effect",
            0,
            1,
            0.01,
            "acceleration_smooth_factor",
        )

        if lw:
            lw.top("Setting up FFT options...")
        self.addSeparateWidget(TitleLabel("FFT"))
        self.enableFFT_box = CheckBox("Enable Frequency Graphics")
        self.addSeparateWidget(self.enableFFT_box)
        self.enableFFT_box.setChecked(cfg.enable_fft)
        self.addNumberSetting(
            "FFT Filtering Window size",
            "larger value means more smoothing",
            1,
            200,
            1,
            "fft_filtering_windowsize",
        )
        self.addNumberSetting(
            "FFT Smoothing Factor",
            "larger value means a more sudden change",
            0.01,
            1.0,
            0.05,
            "fft_factor",
        )
        self.addNumberSetting(
            "SouthsideMusic side FFT Multiple Factor",
            "larger value means more intense changing(only on SouthsideMusic side)",
            0,
            15.0,
            0.05,
            "cfft_multiple",
        )
        self.addNumberSetting(
            "SouthsideClient side FFT Multiple Factor",
            "larger value means more intense changing(only on SouthsideClient side)",
            00,
            15.0,
            0.05,
            "sfft_multiple",
        )

        if lw:
            lw.top("Setting up loudness balance...")
        self.addSeparateWidget(TitleLabel("Loudness Balance"))
        self.target_lufs = Slider(Qt.Orientation.Horizontal)
        self.target_lufs.setRange(-60, 0)
        self.target_lufs.setSingleStep(1)
        self.target_lufs.valueChanged.connect(self.onTargetLUFSChanged)
        self.target_lufs.setValue(cfg.target_lufs)
        self.addSeparateWidget(self.target_lufs)
        self.target_lufs_label = SubtitleLabel(f"Target LUFS: {cfg.target_lufs}")
        self.addSeparateWidget(self.target_lufs_label)
        self.addSeparateWidget(
            QLabel(
                "Target LUFS Help:\nRange: -60(quietest)~0(loudest)\nRecommend: -16~-18"
                "\nReference:\nYoutube > -14LUFS\nNetflix > -27LUFS\nTikTok / Instagram Reels > -13LUFS\nApple Music (Video) > -16LUFS"
                "\nSpotify (Video): -14LUFS / -16LUFS"
            )
        )

        if lw:
            lw.top("Setting up connection options...")
        self.addSeparateWidget(QLabel())
        self.addSeparateWidget(TitleLabel("SouthsideClient Connection"))
        self.southsideclient_status_label = SubtitleLabel(
            "Connection Status: <span style='color: red;'>Disconnected</span>"
        )
        self.addSeparateWidget(self.southsideclient_status_label)
        self.disconnect_btn = TransparentPushButton("Disconnect")
        bindIcon(self.disconnect_btn, "disc")
        self.disconnect_btn.clicked.connect(self.disconnectFromSouthsideClient)
        self.disconnect_btn.setEnabled(False)
        self.addSeparateWidget(self.disconnect_btn)
        self.connect_btn = TransparentPushButton("Try connect")
        bindIcon(self.connect_btn, "cnnt")
        self.connect_btn.clicked.connect(self.connectToSouthsideClient)
        self.connect_btn.setEnabled(False)
        self.addSeparateWidget(self.connect_btn)

        for slider in self.options_interface.findChildren(QSlider):
            slider.wheelEvent = lambda e: e.ignore()  # type: ignore[method-assign]

    def addNumberSetting(
        self,
        title: str,
        description: str,
        min: float | int,
        max: float | int,
        step: float | int,
        configurationName: str,
        onChanged: Callable[[float], None] | None = None,
    ) -> None:
        box = DoubleSpinBox()
        box.setRange(min, max)
        box.setValue(getattr(cfg, configurationName))
        box.setSingleStep(step)

        def _valueChanged(value: float | int):
            setattr(cfg, configurationName, value)
            if onChanged:
                onChanged(value)

        box.valueChanged.connect(_valueChanged)
        self.addSetting(title, description, box)

    def addCheckSetting(
        self, title: str, description: str, configurationName: str
    ) -> None:
        box = CheckBox(title)

        def __valueChanged():
            setattr(cfg, configurationName, box.checkState() == Qt.CheckState.Checked)

        box.stateChanged.connect(__valueChanged)
        box.setChecked(getattr(cfg, configurationName))
        self.addSetting(title, description, box)

    def addSetting(self, name: str, description: str, widget: QWidget) -> None:
        card = CardWidget()
        card.paintEvent = lambda e: self._patched_paint_event(card, e)
        card.setBackgroundColor(QColor(255, 255, 255, 0))
        card._normalBackgroundColor = lambda: QColor(255, 255, 255, 0)
        card._hoverBackgroundColor = lambda: QColor(255, 255, 255, 0)
        card._pressedBackgroundColor = lambda: QColor(255, 255, 255, 0)
        card._focusInBackgroundColor = lambda: QColor(255, 255, 255, 0)
        global_layout = QVBoxLayout()
        top_layout = QHBoxLayout()
        name_l = QLabel(name)
        name_l.setStyleSheet("font-weight: bold;")
        name_l.setWordWrap(True)
        top_layout.addWidget(name_l)
        top_layout.addWidget(widget)
        global_layout.addLayout(top_layout)
        desc_l = QLabel(description)
        desc_l.setWordWrap(True)
        global_layout.addWidget(desc_l)
        card.setLayout(global_layout)
        self.playing_layout.addWidget(card, self.playing_layout.rowCount(), 0, 2, 2)

        event_bus.subscribe(SONG_CHANGED, self._onSongChanged)
        event_bus.subscribe(WEBSOCKET_CONNECTED, self._onWsConnected)
        event_bus.subscribe(WEBSOCKET_DISCONNECTED, self._onWsDisconnected)
        event_bus.subscribe(VOLUME_CHANGED, self._onVolumeChanged)

    def _onSongChanged(self, _song_storable):
        self._syncPlaylistSelection()

    def _onWsConnected(self):
        self.southsideclient_status_label.setText(
            "Connection Status: <span style='color: green;'>Connected</span>"
        )

    def _onWsDisconnected(self):
        self.southsideclient_status_label.setText(
            "Connection Status: <span style='color: red;'>Disconnected</span>"
        )

    def _onVolumeChanged(self, volume: float):
        self.now_volume.setText(
            f"Current volume(db): {(round(volume * 10) / 10) if volume != float('-inf') else '-inf'}"
        )

    def _syncPlaylistSelection(self):
        if not self._dp.cur:
            return
        if not hasattr(self._dp.cur, "storable"):
            return
        storable = self._dp.cur.storable
        for i, song in enumerate(self._dp.playlist):
            if song.name == storable.name:
                self.lst.setCurrentRow(i)
                return

    def _patched_paint_event(self, card: CardWidget, e):
        from PySide6.QtGui import QPainter, QPainterPath, QPen
        from qfluentwidgets import isDarkTheme

        painter = QPainter(card)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)

        w, h = card.width(), card.height()
        r = card.getBorderRadius()
        d = 2 * r

        isDark = isDarkTheme()

        path = QPainterPath()
        path.arcMoveTo(1, h - d - 1, d, d, 240)
        path.arcTo(1, h - d - 1, d, d, 225, -60)
        path.lineTo(1, r)
        path.arcTo(1, 1, d, d, -180, -90)
        path.lineTo(w - r, 1)
        path.arcTo(w - d - 1, 1, d, d, 90, -90)
        path.lineTo(w - 1, h - r)
        path.arcTo(w - d - 1, h - d - 1, d, d, 0, -60)

        topBorderColor = QColor(0, 0, 0, 0)
        if isDark:
            topBorderColor = QColor(255, 255, 255, 11)
            if card.isPressed:
                topBorderColor = QColor(255, 255, 255, 34)
            elif card.isHover:
                topBorderColor = QColor(255, 255, 255, 30)
        else:
            topBorderColor = QColor(0, 0, 0, 28)

        painter.strokePath(path, topBorderColor)

        path = QPainterPath()
        path.arcMoveTo(1, h - d - 1, d, d, 240)
        path.arcTo(1, h - d - 1, d, d, 240, 30)
        path.lineTo(w - r - 1, h - 1)
        path.arcTo(w - d - 1, h - d - 1, d, d, 270, 30)

        bottomBorderColor = topBorderColor
        if not isDark and card.isHover and not card.isPressed:
            bottomBorderColor = QColor(0, 0, 0, 27)

        painter.strokePath(path, bottomBorderColor)

        painter.setPen(Qt.PenStyle.NoPen)
        rect = card.rect().adjusted(1, 1, -1, -1)
        painter.setBrush(card.backgroundColor)
        painter.drawRoundedRect(rect, r, r)

    def addSeparateWidget(self, widget: QWidget) -> None:
        self.playing_layout.addWidget(widget, self.playing_layout.rowCount(), 0, 1, 2)

    def disconnectFromSouthsideClient(self):
        self._ws_server.tryGetHandler()
        self._ws_server.stop()
        self._ws_server.join()
        self._ws_handler.onDisconnected.emit()

        self.disconnect_btn.setEnabled(False)
        self.connect_btn.setEnabled(True)

    def connectToSouthsideClient(self):
        _ws_server = WebSocketServer(port=15489)
        _ws_server.start()

        self.connect_btn.setEnabled(False)

    def removeAllSongs(self) -> None:
        self._dp.playlist.clear()
        if isinstance(self._dp.cur, DummyCard) and isinstance(
            self._dp.cur.storable, SongStorable
        ):
            self._dp.playlist.append(self._dp.cur.storable)

        self.refreshPlaylistWidget()

        InfoBar.success(
            "Removed", "Removed all songs", duration=1500, parent=self._mwindow
        )

    def addSongCardToList(self, song: SongStorable) -> QListWidgetItem:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, song)
        item.setSizeHint(QSize(0, 62))
        card = PlaylistSongCard(
            song, self._dp, mwindow=self._mwindow, sidebar=self, lazy=True
        )
        card.clicked.connect(lambda s, it=item: self._dp.onPlaylistCardClicked(s, it))
        self.lst.addItem(item)
        self.lst.setItemWidget(item, card)
        self._song_cards.append(card)
        return item

    def _checkVisibleCards(self):
        for card in self._song_cards:
            if card.load:
                continue
            idx = self._song_cards.index(card)
            item = self.lst.item(idx)
            if item is None:
                continue
            item_rect = self.lst.visualItemRect(item)
            viewport_rect = self.lst.viewport().rect()
            if viewport_rect.intersects(item_rect):
                card.loadDetailAndImage()

    def refreshPlaylistWidget(self):
        val = self.lst.verticalScrollBar().value()
        self._song_cards = []
        self.lst.clear()

        for song in self._dp.playlist:
            self.addSongCardToList(song)

        self._dp._preload_triggered = False
        self.lst.verticalScrollBar().setValue(val)

    def movePlaylistSong(self, song: SongStorable, delta: int):
        playlist = self._dp.playlist
        try:
            old_index = playlist.index(song)
        except ValueError:
            return

        new_index = old_index + delta
        if new_index < 0 or new_index >= len(playlist):
            return

        current_song = None
        if 0 <= self._dp.current_index < len(playlist):
            current_song = playlist[self._dp.current_index]

        playlist[old_index], playlist[new_index] = (
            playlist[new_index],
            playlist[old_index],
        )
        if current_song is not None:
            self._dp.current_index = playlist.index(current_song)
        self._dp.song_randomer.init(playlist)

        self.refreshPlaylistWidget()
        self.lst.setCurrentRow(new_index)

    def applyNewLUFS(self):
        self._dp.lufs_changed_timer.stop()
        self.target_lufs.hide()

        self.target_lufs_label.setText("Reapplying")

        result: dict[str, object] = {}

        def _apply():
            if not isinstance(self._dp.cur, DummyCard):
                return
            if not hasattr(self._dp.cur, "storable"):
                return

            storable = self._dp.cur.storable
            audio: AudioSegment = AudioSegment.from_file(
                io.BytesIO(storable.get_music_bytes())
            )
            self._logger.debug("new lufs -> applying gain")
            gain = getAdjustedGainFactor(cfg.target_lufs, audio)
            result["storable"] = storable
            result["gain"] = gain
            result["target_lufs"] = cfg.target_lufs
            result["position"] = self._player.getPosition()
            result["playing"] = self._player.isPlaying()

        def _finish():
            storable = result.get("storable")
            if not isinstance(storable, SongStorable):
                self.target_lufs_label.setText(f"Target LUFS: {cfg.target_lufs}")
                self.target_lufs.show()
                return
            if (
                not isinstance(self._dp.cur, DummyCard)
                or self._dp.cur.storable is not storable
            ):
                self.target_lufs_label.setText(f"Target LUFS: {cfg.target_lufs}")
                self.target_lufs.show()
                return

            storable.loudness_gain = cast(float, result["gain"])
            storable.target_lufs = cast(int, result["target_lufs"])
            position = cast(float, result["position"])
            playingnow = cast(bool, result["playing"])

            def _apply_playback_update():
                self._dp.playStorable(storable)
                if playingnow:
                    self._player.play()
                self.target_lufs_label.setText(f"Target LUFS: {cfg.target_lufs}")
                self._player.setPosition(position)
                from PySide6.QtCore import QTimer

                QTimer.singleShot(250, self._dp.preloadNextSong)
                self.target_lufs.show()

            self._mwindow.addScheduledTask(_apply_playback_update)

        doWithMultiThreading(_apply, (), self._mwindow, _finish)

    def onTargetLUFSChanged(self, value: int):
        cfg.target_lufs = value
        if hasattr(self, "target_lufs_label"):
            self.target_lufs_label.setText(f"Target LUFS: {value}")
            self._dp.lufs_changed_timer.start(1000)
        event_bus.emit(LUFS_TARGET_CHANGED, value)
