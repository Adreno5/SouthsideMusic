from __future__ import annotations

import logging

from typing import Callable, cast

from openai import OpenAI

from core.app_context import AppContext

from imports import (
    BACKGROUND_RATIO_CHANGED,
    DB_CHANGED,
    LANGUAGE_CHANGED,
    LUFS_TARGET_CHANGED,
    POST_THEME_CHANGED,
    WEBSOCKET_CONNECTED,
    WEBSOCKET_DISCONNECTED,
    InfoBar,
    QEasingCurve,
    QPropertyAnimation,
    Property,
    Qt,
    Signal,
    event_bus,
    bindText,
    refreshBoundTexts,
    tr,
)
from imports import QColor
from imports import (
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
    QSpacerItem,
    QTimer
)
from qfluentwidgets import (
    CardWidget,
    CheckBox,
    ComboBox,
    FluentIcon,
    LineEdit,
    PasswordLineEdit,
    PushButton,
    Slider,
    SubtitleLabel,
    TitleLabel,
    TransparentPushButton,
)

from core.icons import bindIcon
from core import theme
from core.audio_player import getAudioDevices
from core.config import cfg, decryptSecret, encryptSecret, saveConfig
from core.downloader import asyncTask
from core.i18n import Language, setLanguage
from core.ws_server import (
    WebSocketServer,
    QObjectHandler,
)

from views.list_widget import SScrollArea
from views.number_viewer import NumberViewer, SettableNumberViewer


class SectionContainer(QWidget):
    expandedChanged = Signal(str, bool)

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

        self.title_l = TitleLabel()
        self.desc_l = QLabel()
        bindText(self.title_l, title)
        bindText(self.desc_l, description)
        desc_l = self.desc_l
        desc_l.setWordWrap(True)
        desc_l.setStyleSheet(f'color: {"#A8A8A8" if theme.isDark() else "#666666"};')
        header_layout.addWidget(self.title_l)
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

    def isExpanded(self) -> bool:
        return self._expanded

    @property
    def title(self) -> str:
        return self._title

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
        self.expandedChanged.emit(self._title, expanded)
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
        self.now_volume = QLabel(tr('setting_page.current_volume_db_value', value=0))

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
            if cfg.setting_section_expanded.get(section.title, False):
                continue
            section.collapseNow()
        self._syncSectionExpandedConfig()
        self.options_layout.addStretch(1)

        self.scroller.setWidget(self.options_widget)
        self.scroller.setWidgetResizable(True)

        global_layout.addWidget(self.scroller)

        self.setLayout(global_layout)

        event_bus.subscribe(WEBSOCKET_CONNECTED, self._onWsConnected)
        event_bus.subscribe(WEBSOCKET_DISCONNECTED, self._onWsDisconnected)
        event_bus.subscribe(POST_THEME_CHANGED, self.updateTheme)
        event_bus.subscribe(LANGUAGE_CHANGED, self.updateLanguage)
        event_bus.subscribe(
            DB_CHANGED,
            lambda v: self.now_volume.setText(
                tr('setting_page.current_volume_db_value', value=f'{v:.1f}')
            ),
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

    def updateLanguage(self) -> None:
        refreshBoundTexts()
        self._refreshLanguageBox()
        self._refreshPlayMethodBox()
        self._refreshConnectionStatus()
        if hasattr(self, 'target_lufs_label'):
            self.target_lufs_label.setText(
                tr('setting_page.target_lufs_value', value=cfg.target_lufs)
            )

    def _initOptions(self) -> None:
        lw = self._launchwindow
        if lw:
            lw.push('Setting up sidebar options...')

        self.addSection(
            'setting_page.app', 'setting_page.language_and_application_behavior'
        )

        self.language_box = ComboBox()
        self._refreshLanguageBox()
        self.language_box.currentIndexChanged.connect(self._onLanguageChanged)
        self.addSetting(
            'setting_page.language',
            'setting_page.change_the_display_language_immediately',
            self.language_box,
        )
        self.addNumberSetting(
            'setting_page.download_concurrent_threads',
            'setting_page.download_concurrent_threads_description',
            1, 128, 1, 'download_concurrent_threads'
        )

        self.addSection(
            'setting_page.playing',
            'setting_page.playback_order_stereo_output_speed_and_skip_behavior',
        )

        self.play_method_box = ComboBox()
        self._refreshPlayMethodBox()
        self.play_method_box.currentIndexChanged.connect(self._onPlayMethodChanged)
        self.addSetting(
            'setting_page.play_order',
            'setting_page.the_order_of_play',
            self.play_method_box,
        )

        self.addCheckSetting(
            'setting_page.enable_stereo',
            'setting_page.enable_stereo_effect',
            'stereo',
            lambda: self._player.restartProducer(),
        )
        self.addNumberSetting(
            'setting_page.stereo_haas_index_ms',
            'setting_page.adjust_the_right_channel_delay_of_stereo_haas_effect',
            0,
            200,
            5,
            'stereo_haas_index',
            lambda val: self._player.restartProducer(),
        )
        self.addCheckSetting(
            'setting_page.enable_reverb',
            'setting_page.enable_reverb_effect',
            'enable_reverb',
            lambda: self._player.restartProducer(),
        )
        self.addNumberSetting(
            'setting_page.reverb_intensity',
            'setting_page.adjust_the_strength_of_the_reverb_effect',
            0,
            3,
            0.05,
            'reverb_intensity',
            lambda val: self._player.restartProducer(),
        )
        self.addCheckSetting(
            'setting_page.smart_skip',
            'setting_page.skip_the_no_sound_section_when_song_ends',
            'skip_nosound',
        )
        self.addNumberSetting(
            'setting_page.playback_speed',
            'setting_page.speed_of_playing',
            0.1,
            3,
            0.1,
            'play_speed',
            lambda val: self._player.setPlaySpeed(val),
        )
        self.addNumberSetting(
            'setting_page.playback_pitch',
            'setting_page.pitch_shift_in_semitones',
            -12,
            12,
            0.1,
            'play_pitch',
            lambda val: self._player.setPlayPitch(val),
        )
        self.addNumberSetting(
            'setting_page.skip_threshold',
            'setting_page.the_threshold_of_the_skip',
            -100,
            0,
            1,
            'skip_threshold',
        )
        self.addSetting(
            'setting_page.current_volume',
            'setting_page.live_playback_volume_in_db',
            self.now_volume,
        )

        self.addNumberSetting(
            'setting_page.remain_time_to_skip',
            'setting_page.start_detecting_volume_during_the_remaining_specified_seconds',
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
            'setting_page.output_device',
            'setting_page.the_device_to_output_audio',
            self.device_selector,
        )

        if lw:
            lw.top('Setting up LLM options...')
        self.addSection(
            'setting_page.llm',
            'setting_page.llm_provider_model_and_authentication',
        )

        self.llm_base_url_edit = LineEdit()
        self.llm_base_url_edit.setFixedWidth(360)
        self.llm_base_url_edit.setText(cfg.llm_base_url)
        self.llm_base_url_edit.editingFinished.connect(self._onLlmBaseUrlChanged)
        self.addSetting(
            'setting_page.llm_base_url',
            'setting_page.openai_compatible_base_url',
            self.llm_base_url_edit,
        )

        self.llm_api_key_edit = PasswordLineEdit()
        self.llm_api_key_edit.setFixedWidth(360)
        self.llm_api_key_edit.setText(decryptSecret(cfg.llm_api_key_encrypted))
        self.llm_api_key_edit.editingFinished.connect(self._onLlmApiKeyChanged)
        self.addSetting(
            'setting_page.llm_api_key',
            'setting_page.llm_api_key_stored_encrypted',
            self.llm_api_key_edit,
        )

        self.llm_model_box = ComboBox()
        self.llm_model_box.setFixedWidth(260)
        self._refreshLlmModelBox()
        self.llm_model_box.currentIndexChanged.connect(self._onLlmModelChanged)
        self.addSetting(
            'setting_page.llm_model',
            'setting_page.select_model_after_refreshing_models',
            self.llm_model_box,
        )

        self.llm_refresh_models_btn = PushButton(FluentIcon.SYNC, '')
        bindText(self.llm_refresh_models_btn, 'setting_page.refresh_models')
        self.llm_refresh_models_btn.clicked.connect(self.refreshLlmModels)
        self.addSetting(
            'setting_page.llm_refresh_models',
            'setting_page.fetch_models_from_the_configured_base_url',
            self.llm_refresh_models_btn,
        )

        if lw:
            lw.top('Setting up window options...')
        self.addSection(
            'setting_page.window', 'setting_page.theme_sensitive_background_mixing'
        )
        self.addNumberSetting(
            'setting_page.window_background_mix_ratio',
            'setting_page.larger_value_make_color_of_backgound_nearly_to_image_of_playing_song',
            0,
            1,
            0.05,
            'background_ratio',
            lambda v: self._onBackgroundRatioChanged(v),
        )

        if lw:
            lw.top('Setting up lyrics options...')
        self.addSection(
            'setting_page.lyrics',
            'setting_page.smoothing_controls_for_the_main_lyrics_animation',
        )
        self.addNumberSetting(
            'setting_page.lyrics_smooth_factor',
            'setting_page.larger_value_means_a_more_sudden_change',
            0,
            1,
            0.002,
            'lyrics_smooth_factor',
        )
        self.addNumberSetting(
            'setting_page.acceleration_smooth_factor',
            'setting_page.smaller_value_means_a_more_bounce_effect',
            0,
            1,
            0.002,
            'acceleration_smooth_factor',
        )

        if lw:
            lw.top('Setting up Desktop lyrics options...')

        self.addSection(
            'setting_page.desktop_lyrics',
            'setting_page.floating_lyrics_window_controls',
        )
        dsp = self._dsp
        assert dsp is not None, (
            'Desktop lyrics page must be initialized before settings'
        )
        self.desktop_lyrics_box = CheckBox()
        bindText(self.desktop_lyrics_box, 'setting_page.enable_desktop_lyrics')
        self.desktop_lyrics_box.checkStateChanged.connect(
            lambda: self._onDesktopLyricsEnableChanged()
        )
        self.desktop_lyrics_box.setChecked(cfg.enable_desktop_lyrics)
        self.addSetting(
            'setting_page.enable_desktop_lyrics',
            'setting_page.show_lyrics_in_a_floating_always_on_top_window',
            self.desktop_lyrics_box,
        )
        self.desktop_lyrics_reset_pos = PushButton(FluentIcon.SYNC, '')
        bindText(self.desktop_lyrics_reset_pos, 'setting_page.reset_position')
        self.desktop_lyrics_reset_pos.clicked.connect(dsp.onResetPos)
        self.addSetting(
            'setting_page.reset_position',
            'setting_page.move_the_desktop_lyrics_window_back_to_the_origin',
            self.desktop_lyrics_reset_pos,
        )

        if lw:
            lw.top('Setting up FFT options...')
        self.addSection(
            'setting_page.fft',
            'setting_page.frequency_visualization_tuning_for_local_and_client_output',
        )
        self.enableFFT_box = CheckBox()
        bindText(self.enableFFT_box, 'setting_page.enable_frequency_graphics')
        self.enableFFT_box.setChecked(cfg.enable_fft)
        self.addSetting(
            'setting_page.frequency_graphics',
            'setting_page.enable_fft_driven_visual_effects',
            self.enableFFT_box,
        )
        self.addNumberSetting(
            'setting_page.fft_filtering_window_size',
            'setting_page.larger_value_means_more_smoothing',
            1,
            200,
            1,
            'fft_filtering_windowsize',
        )
        self.addNumberSetting(
            'setting_page.fft_smoothing_factor',
            'setting_page.larger_value_means_a_more_sudden_change',
            0.01,
            1.0,
            0.05,
            'fft_factor',
        )
        self.addNumberSetting(
            'setting_page.southside_music_side_fft_multiple_factor',
            'setting_page.larger_value_means_more_intense_changing_only_on_southside_music_side',
            0,
            15.0,
            0.1,
            'cfft_multiple',
        )
        self.addNumberSetting(
            'setting_page.southside_client_side_fft_multiple_factor',
            'setting_page.larger_value_means_more_intense_changing_only_on_southside_client_side',
            00,
            15.0,
            0.5,
            'sfft_multiple',
        )

        if lw:
            lw.top('Setting up loudness balance...')
        self.addSection(
            'setting_page.loudness',
            'setting_page.target_volume_normalization_for_playback',
        )
        self.target_lufs = Slider(Qt.Orientation.Horizontal)
        self.target_lufs.valueChanged.connect(self.onTargetLUFSChanged)
        self.target_lufs.wheelEvent = lambda e: e.ignore()
        self.target_lufs.sliderReleased.connect(self.onSliderReleased)
        self.target_lufs.setRange(-60, 0)
        self.target_lufs.setSingleStep(1)
        self.target_lufs.setValue(cfg.target_lufs)
        self.target_lufs_label = SubtitleLabel(
            tr('setting_page.target_lufs_value')
        )
        self.addSetting(
            'setting_page.target_lufs',
            'setting_page.restart_to_apply_loudness_changes',
            self.target_lufs,
        )
        middle_widget = QWidget()
        middle_layout = QHBoxLayout()
        middle_layout.addWidget(self.target_lufs_label)
        self.target_lufs_viewer = NumberViewer(self.ctx.harmony_font_family, self.ctx)
        self.target_lufs_viewer.setText(str(cfg.target_lufs))
        middle_layout.addWidget(self.target_lufs_viewer)
        middle_layout.addSpacerItem(QSpacerItem(10, 10, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        middle_layout.setSpacing(8)
        middle_widget.setLayout(middle_layout)
        self.addSeparateWidget(middle_widget)
        self.addInfoBlock(
            'setting_page.reference',
            'setting_page.range_60_quietest_0_loudest_recommend_16_18_youtube_14_lufs_netflix_27',
        )

        if lw:
            lw.top('Setting up connection options...')
        self.addSection(
            'setting_page.connection',
            'setting_page.southside_client_websocket_status_and_controls',
        )
        self.southsideclient_status_label = SubtitleLabel()
        self.addSeparateWidget(self.southsideclient_status_label)

        self.status_widget = QWidget()
        status_layout = QHBoxLayout()
        prefix_label = QLabel('')
        bindText(prefix_label, 'setting_page.sent_size')
        self.sent_label = NumberViewer(self.ctx.harmony_font_family, self.ctx)
        self.update_statuses_timer = QTimer(self)
        status_layout.addWidget(prefix_label)
        status_layout.addWidget(self.sent_label)
        status_layout.addWidget(QLabel('MB'))
        status_layout.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        prefix_label = QLabel('')
        bindText(prefix_label, 'setting_page.received_size')
        self.received_label = NumberViewer(self.ctx.harmony_font_family, self.ctx)
        status_layout.addWidget(prefix_label)
        status_layout.addWidget(self.received_label)
        status_layout.addWidget(QLabel('KB'))
        status_layout.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        status_layout.setSpacing(8)
        self.status_widget.setLayout(status_layout)
        self.addSeparateWidget(self.status_widget)

        prefix_label = QLabel('')
        bindText(prefix_label, 'setting_page.latency')
        self.latency_label = NumberViewer(self.ctx.harmony_font_family, self.ctx)
        status_layout.addWidget(prefix_label)
        status_layout.addWidget(self.latency_label)
        status_layout.addWidget(QLabel('ms'))
        status_layout.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        status_layout.setSpacing(8)
        self.status_widget.setLayout(status_layout)
        self.addSeparateWidget(self.status_widget)

        self.update_statuses_timer.timeout.connect(lambda: self.sent_label.setText(f'{self.ctx.ws_handler.sent:.2f}'))
        self.update_statuses_timer.timeout.connect(lambda: self.received_label.setText(f'{self.ctx.ws_handler.received:.2f}'))
        self.update_statuses_timer.timeout.connect(lambda: self.latency_label.setText(f'{self.ctx.ws_handler.ping:.2f}'))
        self.update_statuses_timer.start(200)

        self.disconnect_btn = TransparentPushButton('')
        bindText(self.disconnect_btn, 'setting_page.disconnect')
        bindIcon(self.disconnect_btn, 'disc')
        self.disconnect_btn.clicked.connect(self.disconnectFromSouthsideClient)
        self.disconnect_btn.setEnabled(False)
        self.connect_btn = TransparentPushButton('')
        bindText(self.connect_btn, 'setting_page.try_connect')
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
        self._refreshConnectionStatus()

        for slider in self.findChildren(QSlider):
            slider.wheelEvent = lambda e: e.ignore()  # type: ignore[method-assign]

    def deviceChanged(self, idx: int):
        try:
            device = getAudioDevices()[idx]
            self._player.setOutputDevice(device)
            if self._mwindow:
                InfoBar.success(
                    tr('setting_page.device_changed'),
                    tr(
                        'setting_page.changed_output_device_to_device',
                        device=device.display_name,
                    ),
                    duration=3000,
                    parent=self._mwindow,
                )
            cfg.output_device_index = idx
        except Exception:
            pass

    def onSliderReleased(self):
        InfoBar.info(
            tr('setting_page.need_restart'),
            tr('setting_page.restart_the_application_to_apply_the_new_lufs'),
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
        box = SettableNumberViewer(self.ctx.harmony_font_family, self.ctx)
        box.setRange(min, max_v)
        box.setSingleStep(step)
        box.setValue(getattr(cfg, configurationName))

        def _valueChanged(value: float | int):
            setattr(cfg, configurationName, value)
            if onChanged:
                onChanged(value)

        box.valueChanged.connect(_valueChanged)
        self.addSetting(title, description, box)

    def addCheckSetting(
        self,
        title: str,
        description: str,
        configurationName: str,
        onChanged: Callable[[], None] | None = None,
    ) -> None:
        box = CheckBox()
        bindText(box, title)

        def __valueChanged():
            setattr(cfg, configurationName, box.checkState() == Qt.CheckState.Checked)
            if onChanged:
                onChanged()

        box.stateChanged.connect(__valueChanged)
        box.setChecked(getattr(cfg, configurationName))
        self.addSetting(title, description, box)

    def addSection(self, title: str, description: str) -> None:
        if self._section_count:
            self.options_layout.addSpacing(12)
        section = SectionContainer(title, description)
        section.expandedChanged.connect(self._onSectionExpandedChanged)
        self.options_layout.addWidget(section)
        self._current_section = section
        self._sections.append(section)
        self._section_count += 1

    def _onSectionExpandedChanged(self, title: str, expanded: bool) -> None:
        cfg.setting_section_expanded[title] = expanded

    def _syncSectionExpandedConfig(self) -> None:
        cfg.setting_section_expanded = {
            section.title: section.isExpanded() for section in self._sections
        }

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
        name_l = QLabel()
        bindText(name_l, name)
        name_l.setStyleSheet('font-weight: bold;')
        name_l.setWordWrap(True)
        desc_l = QLabel()
        bindText(desc_l, description)
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
        title_l = QLabel()
        bindText(title_l, title)
        title_l.setStyleSheet('font-weight: bold;')
        body_l = QLabel()
        bindText(body_l, text)
        body_l.setWordWrap(True)
        body_l.setStyleSheet(f'color: {"#A8A8A8" if theme.isDark() else "#666666"};')
        layout.addWidget(title_l)
        layout.addWidget(body_l)
        card.setLayout(layout)
        self._addOptionWidget(card)

    def _onBackgroundRatioChanged(self, v):
        event_bus.emit(BACKGROUND_RATIO_CHANGED)

    def _onWsConnected(self):
        self._refreshConnectionStatus(True)

    def _onWsDisconnected(self):
        self._refreshConnectionStatus(False)

    def _onVolumeChanged(self, volume: float):
        self.now_volume.setText(
            tr(
                'setting_page.current_volume_db_value',
                value=(round(volume * 10) / 10) if volume != float('-inf') else '-inf',
            )
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
        self._ws_handler.onDisconnected.emit()

        self.disconnect_btn.setEnabled(False)
        self.connect_btn.setEnabled(True)

    def connectToSouthsideClient(self) -> None:
        self._ws_server = WebSocketServer(port=15489)
        self.ctx.ws_server = self._ws_server
        main_window = getattr(self.ctx, 'main_window', None)
        if main_window is not None:
            main_window._ws_server = self._ws_server
        self._ws_server.start()

        self.connect_btn.setEnabled(False)

    def onTargetLUFSChanged(self, value: int):
        cfg.target_lufs = value
        if hasattr(self, 'target_lufs_viewer'):
            self.target_lufs_viewer.setText(f'{value}')
        event_bus.emit(LUFS_TARGET_CHANGED, value)

    def _refreshLanguageBox(self) -> None:
        if not hasattr(self, 'language_box'):
            return
        self.language_box.blockSignals(True)
        self.language_box.clear()
        for code in ('en_US', 'zh_CN'):
            self.language_box.addItem(tr(f'language.{code}'), userData=code)
        index = self.language_box.findData(cfg.language)
        self.language_box.setCurrentIndex(max(index, 0))
        self.language_box.blockSignals(False)

    def _onLanguageChanged(self, *_args: object) -> None:
        code = self.language_box.currentData()
        if code not in ('en_US', 'zh_CN'):
            return
        if cfg.language == code:
            return
        setLanguage(cast(Language, code))
        saveConfig()
        event_bus.emit(LANGUAGE_CHANGED)

    def _refreshPlayMethodBox(self) -> None:
        current = cfg.play_method
        if hasattr(self, 'play_method_box'):
            data = self.play_method_box.currentData()
            if data in ('Repeat one', 'Repeat list', 'Shuffle', 'Play in order'):
                current = data
            self.play_method_box.blockSignals(True)
            self.play_method_box.clear()
            label_keys = {
                'Repeat one': 'setting_page.play_method.repeat_one',
                'Repeat list': 'setting_page.play_method.repeat_list',
                'Shuffle': 'setting_page.play_method.shuffle',
                'Play in order': 'setting_page.play_method.play_in_order',
            }
            for mode in ('Repeat one', 'Repeat list', 'Shuffle', 'Play in order'):
                self.play_method_box.addItem(tr(label_keys[mode]), userData=mode)
            index = self.play_method_box.findData(current)
            self.play_method_box.setCurrentIndex(max(index, 0))
            self.play_method_box.blockSignals(False)

    def _onPlayMethodChanged(self) -> None:
        mode = self.play_method_box.currentData()
        if mode in ('Repeat one', 'Repeat list', 'Shuffle', 'Play in order'):
            cfg.play_method = mode

    def _onLlmBaseUrlChanged(self) -> None:
        cfg.llm_base_url = self.llm_base_url_edit.text().strip().rstrip('/')

    def _onLlmApiKeyChanged(self) -> None:
        cfg.llm_api_key_encrypted = encryptSecret(self.llm_api_key_edit.text().strip())

    def _refreshLlmModelBox(self, models: list[str] | None = None) -> None:
        current = cfg.llm_model
        if models is None:
            models = [current] if current else []

        self.llm_model_box.blockSignals(True)
        self.llm_model_box.clear()
        for model in models:
            self.llm_model_box.addItem(model, userData=model)
        index = self.llm_model_box.findData(current)
        self.llm_model_box.setCurrentIndex(index if index >= 0 else 0)
        self.llm_model_box.blockSignals(False)

    def _onLlmModelChanged(self) -> None:
        model = self.llm_model_box.currentData()
        if isinstance(model, str):
            cfg.llm_model = model

    def refreshLlmModels(self) -> None:
        self._onLlmBaseUrlChanged()
        self._onLlmApiKeyChanged()
        if not cfg.llm_base_url:
            InfoBar.error(
                tr('setting_page.llm_models_refresh_failed'),
                tr('setting_page.llm_base_url_required'),
                duration=5000,
                parent=self._mwindow,
            )
            return
        self.llm_refresh_models_btn.setEnabled(False)
        models: list[str] = []
        error: Exception | None = None

        def _fetch() -> None:
            nonlocal error, models
            try:
                client = OpenAI(
                    api_key=decryptSecret(cfg.llm_api_key_encrypted) or 'unused',
                    base_url=cfg.llm_base_url,
                    timeout=20,
                )
                models = sorted(
                    {model.id for model in client.models.list().data if model.id},
                    key=str.lower,
                )
            except Exception as e:
                error = e
                self._logger.exception(e)

        def _finish() -> None:
            self.llm_refresh_models_btn.setEnabled(True)
            if error is not None:
                InfoBar.error(
                    tr('setting_page.llm_models_refresh_failed'),
                    str(error),
                    duration=5000,
                    parent=self._mwindow,
                )
                return
            if cfg.llm_model not in models and models:
                cfg.llm_model = models[0]
            self._refreshLlmModelBox(models)
            InfoBar.success(
                tr('setting_page.llm_models_refreshed'),
                tr('setting_page.loaded_model_count', count=len(models)),
                duration=3000,
                parent=self._mwindow,
            )

        asyncTask(_fetch, (), self._mwindow, _finish)

    def _refreshConnectionStatus(self, connected: bool | None = None) -> None:
        if connected is None:
            connected = self._ws_handler.is_open
        status = (
            tr('setting_page.connected')
            if connected
            else tr('setting_page.disconnected')
        )
        color = 'green' if connected else 'red'
        self.disconnect_btn.setEnabled(connected)
        self.connect_btn.setEnabled(not connected)
        self.southsideclient_status_label.setText(
            tr(
                'setting_page.connection_status_span_style_color_color_status_span',
                color=color,
                status=status,
            )
        )
        self.status_widget.setVisible(connected)
