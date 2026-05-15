from __future__ import annotations

import io
import logging

from typing import Callable, TYPE_CHECKING, cast

if TYPE_CHECKING:
    from views.main_window import MainWindow
    from views.playing_page import PlayingPage
    from core.audio_player import AudioPlayer
    from core.ws_server import WebSocketHandler, WebSocketServer, QObjectHandler
    from views.desktop_lyrics import DesktopLyricsPage


import numpy as np
from imports import (
    BACKGROUND_RATIO_CHANGED,
    LUFS_TARGET_CHANGED,
    POST_THEME_CHANGED,
    WEBSOCKET_CONNECTED,
    WEBSOCKET_DISCONNECTED,
    InfoBar,
    QEasingCurve,
    Qt,
    QTimer,
    SmoothScrollArea,
    event_bus,
)
from imports import QColor
from imports import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CardWidget,
    CheckBox,
    ComboBox,
    DoubleSpinBox,
    Slider,
    SubtitleLabel,
    TitleLabel,
    TransparentPushButton,
)

from core.models import SongStorable
from core.icons import bindIcon
from core import theme as darkdetect
from core.downloader import doWithMultiThreading
from core.loudness import getAdjustedGainFactor
from core.audio_player import AudioSegment, AudioPlayer
from core.config import cfg
from views.song_card import DummyCard
from core.ws_server import (
    WebSocketServer,
    QObjectHandler,
)


class SettingPage(QWidget):
    def __init__(
        self,
        dsp: DesktopLyricsPage,
        dp: PlayingPage | None = None,
        mwindow: MainWindow | None = None,
        player: AudioPlayer | None = None,
        ws_server=None,
        ws_handler=None,
        app=None,
        launchwindow=None
    ):
        super().__init__()
        self._logger = logging.getLogger(__name__)
        if launchwindow:
            self._launchwindow = launchwindow
        else:
            self._launchwindow = None
        self._dp: PlayingPage = dp  # type: ignore
        self._mwindow: MainWindow = mwindow  # type: ignore
        self._player: AudioPlayer = player  # type: ignore
        self._ws_server: WebSocketServer = ws_server  # type: ignore
        self._ws_handler: QObjectHandler = ws_handler  # type: ignore
        self._dsp: DesktopLyricsPage = dsp
        self._app = app

        global_layout = QVBoxLayout()

        self.scroller = SmoothScrollArea()
        self.scroller.setScrollAnimation(Qt.Orientation.Vertical, 450, QEasingCurve.Type.OutCubic)

        self.setObjectName('SettingPage')
        self.updateTheme()
        self.options_layout = QGridLayout()
        self.options_widget = QWidget()
        self.options_widget.setLayout(self.options_layout)
        self._initOptions()
        
        self.scroller.setWidget(self.options_widget)
        self.scroller.setWidgetResizable(True)

        global_layout.addWidget(self.scroller)

        self.setLayout(global_layout)

        event_bus.subscribe(WEBSOCKET_CONNECTED, self._onWsConnected)
        event_bus.subscribe(WEBSOCKET_DISCONNECTED, self._onWsDisconnected)
        event_bus.subscribe(POST_THEME_CHANGED, self.updateTheme)

    def updateTheme(self) -> None:
        self.setStyleSheet(
            f'background: #{"000000" if darkdetect.isDark() else "FFFFFF"}'
        )

    def _initOptions(self) -> None:
        lw = self._launchwindow
        if lw:
            lw.push('Setting up sidebar options...')

        self.addSeparateWidget(TitleLabel('Playing'))

        self.play_method_box = ComboBox()
        self.play_method_box.addItems(
            ['Repeat one', 'Repeat list', 'Shuffle', 'Play in order']
        )
        self.play_method_box.setCurrentText('Repeat list')
        self.addSetting('Play order', 'the order of play', self.play_method_box)

        self.addCheckSetting('Enable Stereo', 'enable stereo effect', 'stereo')
        self.addCheckSetting(
            'Smart Skip', 'Skip the no sound section when song ends', 'skip_nosound'
        )
        self.addNumberSetting(
            'Playback Speed',
            'speed of playing',
            0.1,
            3,
            0.1,
            'play_speed',
            lambda val: self._player.setPlaySpeed(val),
        )
        self.addNumberSetting(
            'Skip Threshold', 'the threshold of the skip', -100, 0, 1, 'skip_threshold'
        )
        self.now_volume = QLabel('Current volume(db): 0')
        self.addSeparateWidget(self.now_volume)

        self.addNumberSetting(
            'Remain time to Skip',
            'start detecting volume during the remaining specified seconds',
            1,
            60,
            1,
            'skip_remain_time',
        )

        if lw:
            lw.top('Setting up window options...')
        self.addSeparateWidget(TitleLabel('Window'))
        self.addNumberSetting(
            'Window Background Mix Ratio',
            'larger value make color of backgound nearly to image of playing song',
            0,
            1,
            0.05,
            'background_ratio',
            lambda v: self._onBackgroundRatioChanged(v),
        )

        if lw:
            lw.top('Setting up lyrics options...')
        self.addSeparateWidget(TitleLabel('Lyrics'))
        self.addNumberSetting(
            'Lyrics Smooth Factor',
            'larger value means a more sudden change',
            0,
            1,
            0.01,
            'lyrics_smooth_factor',
        )
        self.addNumberSetting(
            'Acceleration Smooth Factor',
            'smaller value means a more bounce effect',
            0,
            1,
            0.01,
            'acceleration_smooth_factor',
        )

        if lw:
            lw.top('Setting up Desktop lyrics options...')

        self.addSeparateWidget(TitleLabel('Desktop Lyrics'))
        self.addSeparateWidget(self._dsp.inputer)
        self.addSeparateWidget(self._dsp.reset_pos)

        if lw:
            lw.top('Setting up FFT options...')
        self.addSeparateWidget(TitleLabel('FFT'))
        self.enableFFT_box = CheckBox('Enable Frequency Graphics')
        self.addSeparateWidget(self.enableFFT_box)
        self.enableFFT_box.setChecked(cfg.enable_fft)
        self.addNumberSetting(
            'FFT Filtering Window size',
            'larger value means more smoothing',
            1,
            200,
            1,
            'fft_filtering_windowsize',
        )
        self.addNumberSetting(
            'FFT Smoothing Factor',
            'larger value means a more sudden change',
            0.01,
            1.0,
            0.05,
            'fft_factor',
        )
        self.addNumberSetting(
            'SouthsideMusic side FFT Multiple Factor',
            'larger value means more intense changing(only on SouthsideMusic side)',
            0,
            15.0,
            0.05,
            'cfft_multiple',
        )
        self.addNumberSetting(
            'SouthsideClient side FFT Multiple Factor',
            'larger value means more intense changing(only on SouthsideClient side)',
            00,
            15.0,
            0.05,
            'sfft_multiple',
        )

        if lw:
            lw.top('Setting up loudness balance...')
        self.addSeparateWidget(TitleLabel('Loudness Balance'))
        self.target_lufs = Slider(Qt.Orientation.Horizontal)
        self.target_lufs.valueChanged.connect(self.onTargetLUFSChanged)
        self.target_lufs.wheelEvent = lambda e: e.ignore()
        self.target_lufs.sliderReleased.connect(self.onSliderReleased)
        self.target_lufs.setRange(-60, 0)
        self.target_lufs.setSingleStep(1)
        self.target_lufs.setValue(cfg.target_lufs)
        self.addSeparateWidget(self.target_lufs)
        self.target_lufs_label = SubtitleLabel(f'Target LUFS: {cfg.target_lufs}')
        self.addSeparateWidget(self.target_lufs_label)
        self.addSeparateWidget(
            QLabel(
                'Target LUFS Help:'
                '\nRange: -60(quietest)~0(loudest)'
                '\nRecommend: -16~-18'
                '\nReference:'
                '\nYoutube : -14LUFS'
                '\nNetflix : -27LUFS'
                '\nTikTok / Instagram Reels : -13LUFS'
                '\nApple Music (Video) : -16LUFS'
                '\nSpotify (Video): -14LUFS : -16LUFS'
            )
        )

        if lw:
            lw.top('Setting up connection options...')
        self.addSeparateWidget(QLabel())
        self.addSeparateWidget(TitleLabel('Connection'))
        self.southsideclient_status_label = SubtitleLabel(
            "Connection Status: <span style='color: red;'>Disconnected</span>"
        )
        self.addSeparateWidget(self.southsideclient_status_label)
        self.disconnect_btn = TransparentPushButton('Disconnect')
        bindIcon(self.disconnect_btn, 'disc')
        self.disconnect_btn.clicked.connect(self.disconnectFromSouthsideClient)
        self.disconnect_btn.setEnabled(False)
        self.addSeparateWidget(self.disconnect_btn)
        self.connect_btn = TransparentPushButton('Try connect')
        bindIcon(self.connect_btn, 'cnnt')
        self.connect_btn.clicked.connect(self.connectToSouthsideClient)
        self.connect_btn.setEnabled(False)
        self.addSeparateWidget(self.connect_btn)

        for slider in self.findChildren(QSlider):
            slider.wheelEvent = lambda e: e.ignore()  # type: ignore[method-assign]

    def onSliderReleased(self):
        InfoBar.info(
            'Need Restart',
            'Restart the application to apply the new LUFS',
            duration=7000,
            parent=self._mwindow
        )

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
        name_l.setStyleSheet('font-weight: bold;')
        name_l.setWordWrap(True)
        top_layout.addWidget(name_l)
        top_layout.addWidget(widget)
        global_layout.addLayout(top_layout)
        desc_l = QLabel(description)
        desc_l.setWordWrap(True)
        global_layout.addWidget(desc_l)
        card.setLayout(global_layout)
        self.options_layout.addWidget(card, self.options_layout.rowCount(), 0, 2, 2)

    def _onBackgroundRatioChanged(self, v):
        event_bus.emit(BACKGROUND_RATIO_CHANGED)

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
            f'Current volume(db): {(round(volume * 10) / 10) if volume != float("-inf") else "-inf"}'
        )

    def _patched_paint_event(self, card: CardWidget, e):
        from PySide6.QtGui import QPainter, QPainterPath

        painter = QPainter(card)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)

        w, h = card.width(), card.height()
        r = card.getBorderRadius()
        d = 2 * r

        isDark = darkdetect.isDark()

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
        self.options_layout.addWidget(widget, self.options_layout.rowCount(), 0, 1, 2)

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

    def onTargetLUFSChanged(self, value: int):
        cfg.target_lufs = value
        if hasattr(self, 'target_lufs_label'):
            self.target_lufs_label.setText(f'Target LUFS: {value}')
        event_bus.emit(LUFS_TARGET_CHANGED, value)
