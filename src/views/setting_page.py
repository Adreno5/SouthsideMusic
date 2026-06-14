from __future__ import annotations

import logging

from typing import Callable

from core.app_context import AppContext

from imports import (
    BACKGROUND_RATIO_CHANGED,
    DB_CHANGED,
    LUFS_TARGET_CHANGED,
    POST_THEME_CHANGED,
    WEBSOCKET_CONNECTED,
    WEBSOCKET_DISCONNECTED,
    InfoBar,
    QEasingCurve,
    QPropertyAnimation,
    Property,
    Qt,
    event_bus,
)
from services.events.events import COLLECT_DEBUG_INFO, EMIT_DEBUG_INFO
from imports import QColor
from imports import (
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
    FluentIcon,
    PushButton,
    Slider,
    SubtitleLabel,
    TitleLabel,
    TransparentPushButton,
)

from core.icons import bindIcon
from core import theme
from core.audio_player import getAudioDevices
from core.config import cfg
from core.ws_server import (
    WebSocketServer,
    QObjectHandler,
)

from views.list_widget import SScrollArea


class SectionContainer(QWidget):
    def __init__(self, title: str, description: str, parent=None) -> None:
        super().__init__(parent)
        self._title = title
        self._expanded = True
        self._content_height = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.header = QWidget()
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        header_layout = QVBoxLayout(self.header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(2)

        title_l = TitleLabel(title)
        desc_l = QLabel(description)
        desc_l.setWordWrap(True)
        desc_l.setStyleSheet(f'color: {"#A8A8A8" if theme.isDark() else "#666666"};')
        header_layout.addWidget(title_l)
        header_layout.addWidget(desc_l)

        self.content_view = QWidget()
        self.content_view.setMaximumHeight(16777215)
        self.content_view.setMinimumHeight(0)

        self.content_widget = QWidget(self.content_view)
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(10)

        self.overlay = QWidget(self.content_view)
        self.overlay.setStyleSheet('background: black;')
        self.overlay.hide()

        self.anim = QPropertyAnimation(self, b'contentHeight')
        self.anim.setDuration(800)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.anim.finished.connect(self._onAnimFinished)

        layout.addWidget(self.header)
        layout.addWidget(self.content_view)
        self.header.mousePressEvent = lambda e: self._onHeaderClicked()

    def addWidget(self, widget: QWidget) -> None:
        self.content_layout.addWidget(widget)
        if self._expanded:
            self.content_view.setFixedHeight(self._contentHeightHint())
        self._syncContentGeometry()

    def collapseNow(self) -> None:
        self._expanded = False
        self.content_view.setFixedHeight(0)
        self.overlay.setGeometry(self.content_view.rect())
        self.overlay.hide()
        self._syncContentGeometry()

    def _onHeaderClicked(self) -> None:
        self.toggle()

    def toggle(self) -> None:
        self.setExpanded(not self._expanded)

    def setExpanded(self, expanded: bool) -> None:
        if (
            expanded == self._expanded
            and self.anim.state() != QPropertyAnimation.State.Running
        ):
            return
        self._expanded = expanded
        self._content_height = self._contentHeightHint()
        self.content_view.setFixedHeight(max(0, self.content_view.height()))
        if expanded:
            self.content_widget.move(0, 0)
        else:
            self.content_widget.move(
                0, self.content_view.height() - self._content_height
            )
        self._syncContentGeometry()
        self.overlay.setGeometry(self.content_view.rect())
        self.overlay.hide()

        self.anim.stop()
        self.anim.setStartValue(self.content_view.height())
        self.anim.setEndValue(self._content_height if expanded else 0)
        self.anim.start()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._syncContentGeometry()

    def _syncContentGeometry(self) -> None:
        height = self._contentHeightHint()
        y = 0 if self._expanded else self.content_view.height() - height
        self.content_widget.setGeometry(0, y, self.content_view.width(), height)
        self.overlay.setGeometry(self.content_view.rect())

    def _contentHeightHint(self) -> int:
        return max(
            self.content_widget.sizeHint().height(),
            self.content_layout.sizeHint().height(),
        )

    def _onAnimFinished(self) -> None:
        if self._expanded:
            self.content_view.setFixedHeight(self._content_height)
            self.overlay.hide()

    def getContentHeight(self) -> int:
        return self.content_view.height()

    def setContentHeight(self, value: int) -> None:
        self.content_view.setFixedHeight(max(0, value))
        self._syncContentGeometry()

    contentHeight = Property(int, getContentHeight, setContentHeight)


class SettingPage(QWidget):
    def __init__(
        self,
        ctx: AppContext,
    ):
        super().__init__()
        self.now_volume = QLabel('Current volume(db): 0')

        self._logger = logging.getLogger(__name__)
        self.ctx = ctx
        self._launchwindow = ctx.launch_window
        self._ws_server: WebSocketServer = ctx.ws_server  # type: ignore
        self._ws_handler: QObjectHandler = ctx.ws_handler  # type: ignore
        self._app = ctx.app

        global_layout = QVBoxLayout()

        self.scroller = SScrollArea()

        self.setObjectName('SettingPage')
        self.updateTheme()
        self.options_layout = QVBoxLayout()
        self.options_layout.setContentsMargins(24, 24, 24, 24)
        self.options_layout.setSpacing(10)
        self._section_count = 0
        self._current_section: SectionContainer | None = None
        self._sections: list[SectionContainer] = []
        self.options_widget = QWidget()
        self.options_widget.setLayout(self.options_layout)
        self._initOptions()
        for section in self._sections:
            section.collapseNow()
        self.options_layout.addStretch(1)

        self.scroller.setWidget(self.options_widget)
        self.scroller.setWidgetResizable(True)

        global_layout.addWidget(self.scroller)

        self.setLayout(global_layout)

        event_bus.subscribe(WEBSOCKET_CONNECTED, self._onWsConnected)
        event_bus.subscribe(WEBSOCKET_DISCONNECTED, self._onWsDisconnected)
        event_bus.subscribe(POST_THEME_CHANGED, self.updateTheme)
        event_bus.subscribe(
            DB_CHANGED,
            lambda v: self.now_volume.setText(f'Current volume(db): {int(v)}'),
        )
        event_bus.subscribe(COLLECT_DEBUG_INFO, self.emitDebugInfo)

    def emitDebugInfo(self):
        event_bus.emit(
            EMIT_DEBUG_INFO,
            'Setting Page',
            [
                f'background_ratio={cfg.background_ratio:.2f}',
                f'play_speed={cfg.play_speed:.2f}',
                f'stereo={cfg.stereo}',
                f'stereo_haas_index={cfg.stereo_haas_index}',
                f'enable_reverb={cfg.enable_reverb}',
                f'reverb_intensity={cfg.reverb_intensity}',
                f'skip_nosound={cfg.skip_nosound}',
                f'enable_fft={cfg.enable_fft}',
                f'target_lufs={cfg.target_lufs}',
                f'output_device_index={cfg.output_device_index}',
                f'enable_desktop_lyrics={cfg.enable_desktop_lyrics}',
                f'ws_server_running={self._ws_server.is_alive() if self._ws_server else False}',
            ],
        )

    @property
    def _dp(self):
        return self.ctx.playing_page

    @property
    def _mwindow(self):
        return self.ctx.main_window

    @property
    def _player(self):
        return self.ctx.player

    @property
    def _dsp(self):
        return self.ctx.desktop_lyrics_page

    def updateTheme(self) -> None:
        self.setStyleSheet(f'background: #{"000000" if theme.isDark() else "FFFFFF"}')

    def _initOptions(self) -> None:
        lw = self._launchwindow
        if lw:
            lw.push('Setting up sidebar options...')

        self.addSection(
            'Playing', 'Playback order, stereo output, speed and skip behavior.'
        )

        self.play_method_box = ComboBox()
        self.play_method_box.addItems(
            ['Repeat one', 'Repeat list', 'Shuffle', 'Play in order']
        )
        self.play_method_box.setCurrentText('Repeat list')
        self.addSetting('Play order', 'the order of play', self.play_method_box)

        self.addCheckSetting('Enable Stereo', 'enable stereo effect', 'stereo')
        self.addNumberSetting(
            'Stereo Haas Index',
            'adjust the right-channel delay of stereo Haas effect',
            0,
            30,
            1,
            'stereo_haas_index',
        )
        self.addCheckSetting('Enable Reverb', 'enable reverb effect', 'enable_reverb')
        self.addNumberSetting(
            'Reverb Intensity',
            'adjust the strength of the reverb effect',
            0,
            30,
            1,
            'reverb_intensity',
        )
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
        self.addSetting('Current Volume', 'live playback volume in db', self.now_volume)

        self.addNumberSetting(
            'Remain time to Skip',
            'start detecting volume during the remaining specified seconds',
            1,
            60,
            1,
            'skip_remain_time',
        )

        self.device_selector = ComboBox()
        self.device_selector.addItems(
            [f'{obj.index + 1}. {obj.display_name}' for obj in getAudioDevices()]
        )
        self.device_selector.setCurrentIndex(self._player._device_id)
        self.device_selector.currentIndexChanged.connect(self.deviceChanged)
        self.device_selector.setCurrentIndex(cfg.output_device_index)
        self.addSetting(
            'Output Device', 'the device to output audio', self.device_selector
        )

        if lw:
            lw.top('Setting up window options...')
        self.addSection('Window', 'Theme-sensitive background mixing.')
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
        self.addSection('Lyrics', 'Smoothing controls for the main lyrics animation.')
        self.addNumberSetting(
            'Lyrics Smooth Factor',
            'larger value means a more sudden change',
            0,
            1,
            0.002,
            'lyrics_smooth_factor',
        )
        self.addNumberSetting(
            'Acceleration Smooth Factor',
            'smaller value means a more bounce effect',
            0,
            1,
            0.002,
            'acceleration_smooth_factor',
        )

        if lw:
            lw.top('Setting up Desktop lyrics options...')

        self.addSection('Desktop Lyrics', 'Floating lyrics window controls.')
        dsp = self._dsp
        assert dsp is not None, (
            'Desktop lyrics page must be initialized before settings'
        )
        self.desktop_lyrics_box = CheckBox('Enable Desktop Lyrics')
        self.desktop_lyrics_box.checkStateChanged.connect(
            lambda: self._onDesktopLyricsEnableChanged()
        )
        self.desktop_lyrics_box.setChecked(cfg.enable_desktop_lyrics)
        self.addSetting(
            'Enable Desktop Lyrics',
            'show lyrics in a floating always-on-top window',
            self.desktop_lyrics_box,
        )
        self.desktop_lyrics_reset_pos = PushButton(FluentIcon.SYNC, 'Reset Position')
        self.desktop_lyrics_reset_pos.clicked.connect(dsp.onResetPos)
        self.addSetting(
            'Reset Position',
            'move the desktop lyrics window back to the origin',
            self.desktop_lyrics_reset_pos,
        )

        if lw:
            lw.top('Setting up FFT options...')
        self.addSection(
            'FFT', 'Frequency visualization tuning for local and client output.'
        )
        self.enableFFT_box = CheckBox('Enable Frequency Graphics')
        self.enableFFT_box.setChecked(cfg.enable_fft)
        self.addSetting(
            'Frequency Graphics',
            'enable FFT-driven visual effects',
            self.enableFFT_box,
        )
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
        self.addSection('Loudness', 'Target volume normalization for playback.')
        self.target_lufs = Slider(Qt.Orientation.Horizontal)
        self.target_lufs.valueChanged.connect(self.onTargetLUFSChanged)
        self.target_lufs.wheelEvent = lambda e: e.ignore()
        self.target_lufs.sliderReleased.connect(self.onSliderReleased)
        self.target_lufs.setRange(-60, 0)
        self.target_lufs.setSingleStep(1)
        self.target_lufs.setValue(cfg.target_lufs)
        self.target_lufs_label = SubtitleLabel(f'Target LUFS: {cfg.target_lufs}')
        self.addSetting(
            'Target LUFS', 'restart to apply loudness changes', self.target_lufs
        )
        self.addSeparateWidget(self.target_lufs_label)
        self.addInfoBlock(
            'Reference',
            'Range: -60(quietest)~0(loudest)\n'
            'Recommend: -16~-18\n'
            'Youtube: -14 LUFS\n'
            'Netflix: -27 LUFS\n'
            'TikTok / Instagram Reels: -13 LUFS\n'
            'Apple Music (Video): -16 LUFS\n'
            'Spotify (Video): -14 LUFS / -16 LUFS',
        )

        if lw:
            lw.top('Setting up connection options...')
        self.addSection('Connection', 'SouthsideClient websocket status and controls.')
        self.southsideclient_status_label = SubtitleLabel(
            "Connection Status: <span style='color: red;'>Disconnected</span>"
        )
        self.addSeparateWidget(self.southsideclient_status_label)
        self.disconnect_btn = TransparentPushButton('Disconnect')
        bindIcon(self.disconnect_btn, 'disc')
        self.disconnect_btn.clicked.connect(self.disconnectFromSouthsideClient)
        self.disconnect_btn.setEnabled(False)
        self.connect_btn = TransparentPushButton('Try connect')
        bindIcon(self.connect_btn, 'cnnt')
        self.connect_btn.clicked.connect(self.connectToSouthsideClient)
        self.connect_btn.setEnabled(False)
        connection_buttons = QWidget()
        connection_layout = QHBoxLayout()
        connection_layout.setContentsMargins(0, 0, 0, 0)
        connection_layout.addWidget(self.disconnect_btn)
        connection_layout.addWidget(self.connect_btn)
        connection_layout.addStretch(1)
        connection_buttons.setLayout(connection_layout)
        self.addSeparateWidget(connection_buttons)

        for slider in self.findChildren(QSlider):
            slider.wheelEvent = lambda e: e.ignore()  # type: ignore[method-assign]

    def deviceChanged(self, idx: int):
        try:
            device = getAudioDevices()[idx]
            self._player.setOutputDevice(device)
            if self._mwindow:
                InfoBar.success(
                    'Device changed',
                    f'changed output deivce to {device.display_name}',
                    duration=3000,
                    parent=self._mwindow,
                )
            cfg.output_device_index = idx
        except:
            pass

    def onSliderReleased(self):
        InfoBar.info(
            'Need Restart',
            'Restart the application to apply the new LUFS',
            duration=7000,
            parent=self._mwindow,
        )

    def addNumberSetting(
        self,
        title: str,
        description: str,
        min: float | int,
        max_v: float | int,
        step: float | int,
        configurationName: str,
        onChanged: Callable[[float], None] | None = None,
    ) -> None:
        box = DoubleSpinBox()
        box.setRange(min, max_v)
        box.setValue(getattr(cfg, configurationName))
        box.setSingleStep(step)
        box.setDecimals(max(int(len(str(step)) - 2), 0))

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

    def addSection(self, title: str, description: str) -> None:
        if self._section_count:
            self.options_layout.addSpacing(12)
        section = SectionContainer(title, description)
        self.options_layout.addWidget(section)
        self._current_section = section
        self._sections.append(section)
        self._section_count += 1

    def _addOptionWidget(self, widget: QWidget) -> None:
        if self._current_section is None:
            self.options_layout.addWidget(widget)
            return
        self._current_section.addWidget(widget)

    def _createTransparentCard(self) -> CardWidget:
        card = CardWidget()
        card.paintEvent = lambda e: self._patched_paint_event(card, e)
        card.setBackgroundColor(QColor(255, 255, 255, 0))
        card._normalBackgroundColor = lambda: QColor(255, 255, 255, 0)
        card._hoverBackgroundColor = lambda: QColor(255, 255, 255, 0)
        card._pressedBackgroundColor = lambda: QColor(255, 255, 255, 0)
        card._focusInBackgroundColor = lambda: QColor(255, 255, 255, 0)
        return card

    def addSetting(self, name: str, description: str, widget: QWidget) -> None:
        card = self._createTransparentCard()
        global_layout = QHBoxLayout()
        global_layout.setContentsMargins(16, 12, 16, 12)
        global_layout.setSpacing(18)
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(4)
        name_l = QLabel(name)
        name_l.setStyleSheet('font-weight: bold;')
        name_l.setWordWrap(True)
        desc_l = QLabel(description)
        desc_l.setWordWrap(True)
        desc_l.setStyleSheet(f'color: {"#A8A8A8" if theme.isDark() else "#666666"};')
        text_layout.addWidget(name_l)
        text_layout.addWidget(desc_l)
        global_layout.addLayout(text_layout, 1)
        global_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignRight)
        card.setLayout(global_layout)
        self._addOptionWidget(card)

    def addInfoBlock(self, title: str, text: str) -> None:
        card = self._createTransparentCard()
        layout = QVBoxLayout()
        layout.setContentsMargins(16, 12, 16, 12)
        title_l = QLabel(title)
        title_l.setStyleSheet('font-weight: bold;')
        body_l = QLabel(text)
        body_l.setWordWrap(True)
        body_l.setStyleSheet(f'color: {"#A8A8A8" if theme.isDark() else "#666666"};')
        layout.addWidget(title_l)
        layout.addWidget(body_l)
        card.setLayout(layout)
        self._addOptionWidget(card)

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

    def _onDesktopLyricsEnableChanged(self) -> None:
        self._dsp.setLyricsVisible(self.desktop_lyrics_box.isChecked())

    def _patched_paint_event(self, card: CardWidget, e):
        from PySide6.QtGui import QPainter, QPainterPath

        painter = QPainter(card)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)

        w, h = card.width(), card.height()
        r = card.getBorderRadius()
        d = 2 * r

        isDark = theme.isDark()

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
        self._addOptionWidget(widget)

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
