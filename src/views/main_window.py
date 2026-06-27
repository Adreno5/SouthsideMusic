from __future__ import annotations

import logging

import threading
import time

from core.app_context import AppContext

from core.backend import getBackend
from core.dialogs import getTextLineedit
from core.qt_utils import toQtInt
from core.smooth import EaseOutTimer
from imports import (
    BACKGROUND_RATIO_CHANGED,
    QLabel,
    ENDING_NO_SOUND,
    LANGUAGE_CHANGED,
    MWINDOW_REFRESH_FOLDERS,
    PLAY_CONTINUE_LAST_SONG,
    PLAY_STORABLE,
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
    Path,
    QAbstractAnimation,
    QEasingCurve,
    QFont,
    QFontMetricsF,
    QIcon,
    QFrame,
    QListWidget,
    QListWidgetItem,
    QPropertyAnimation,
    QRect,
    QSize,
    QStackedWidget,
    QWheelEvent,
    Qt,
    QTimer,
    TransparentPushButton,
    TransparentToolButton,
    bindText,
    event_bus,
    tr,
    TextEdit,
)
from imports import QCloseEvent, QColor, QKeyEvent, QPainter
from views.animated_layout import SFlowLayout
from views.list_widget import SListWidget, SScrollArea, SSmoothDelegate
from imports import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CardWidget,
    ComboBox,
    InfoBar,
)
from qfluentwidgets.window.fluent_window import FluentWindowBase

from core import theme
from core.models import CloudFolderInfo, LocalFolderInfo, SongInfo, SongStorable
from core.color import mixColor
from core.config import saveConfig, cfg
from core.favorites import favorites_manager, saveFavorites
from core.icons import bindIcon
from core.downloader import asyncTask
from core.llm_tools import LLMToolRunner, llmToolSchemas
from views.folder_card import CloudFolderCard, LocalFolderCard
from views.chatting_viewer import ChattingViewer
from views.line_edit import SearchLineEdit
from views.playing_controller import PlayingController
from views.song_card import SearchSongCard
from views.title_bar import SouthsideMusicTitleBar


class DebugOverlay(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.title_ft = QFont(ctx.harmony_font_family, 10, QFont.Weight.Bold)
        self.content_ft = QFont(ctx.harmony_font_family, 7, QFont.Weight.Normal)
        self.title_height = int(QFontMetricsF(self.title_ft).height())
        self.content_height = int(QFontMetricsF(self.content_ft).height())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.hide()

        self.offset_timer = EaseOutTimer(0.2, 2)

    def wheelEvent(self, event: QWheelEvent) -> None:
        self.offset_timer.target_value += event.angleDelta().y()
        return super().wheelEvent(event)

    def refresh(self) -> None:
        self.setVisible(self.ctx.debugging)
        if self.ctx.debugging:
            self.raise_()
            self.update()

    def paintEvent(self, event) -> None:
        if not self.ctx.debugging:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(
            QColor(255, 255, 255, 45) if theme.isLight() else QColor(0, 0, 0, 70)
        )

        painter.drawRect(self.rect())

        painter.setPen(QColor(255, 255, 255) if theme.isDark() else QColor(0, 0, 0))

        y = 50 + int(self.offset_timer.current_value)
        painter.setFont(self.title_ft)
        for info in self.ctx.debugging_obj.infos:
            name, lines = next(iter(info.items()))
            painter.setFont(self.title_ft)
            painter.drawText(10, y, name)
            y += self.title_height + 10
            painter.setFont(self.content_ft)
            for line in lines:
                painter.drawText(20, y, line)
                y += self.content_height + 1

        painter.end()


LLM_VIEWER_WIDTH = 475
LLM_WINDOW_WIDTH_DELTA = 350


class MainWindow(FluentWindowBase):
    def __init__(
        self,
        ctx: AppContext,
        parent=None,
    ):
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self.ctx = ctx
        ctx.main_window = self  # type: ignore
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
        self.llm_viewer_expanded = cfg.llm_viewer_expanded
        self.llm_viewer_animating = False
        self.llm_streaming = False
        self.llm_messages: list[dict[str, str]] = []
        self.llm_stream_viewer: ChattingViewer | None = None
        self.llm_stream_thread: threading.Thread | None = None
        self.llm_cancel_event: threading.Event | None = None
        self.llm_pending_plan: str = ''
        self.llm_pending_tools: list[dict[str, object]] = []
        self.llm_tool_cards: dict[str, tuple[CardWidget, QLabel]] = {}
        self.llm_confirm_card: CardWidget | None = None
        self.llm_confirm_buttons: list[TransparentPushButton] = []
        self.llm_generation = 0

        self.setWindowIcon(
            QIcon(str(Path(__file__).resolve().parent.parent.parent / 'icon.png'))
        )

        self.contents_widget = QStackedWidget()
        for w in [self._fp, self._sp, self._stp, self._sep]:
            if ctx.launch_window:
                ctx.launch_window.push(f'Adding {w} to stacked widget...')
            self.contents_widget.addWidget(w)

        self.contents_widget.currentChanged.connect(self.onStackedWidgetChanged)

        self.setTitleBar(SouthsideMusicTitleBar(self))
        self.llm_clear_btn = TransparentToolButton()
        self.llm_clear_btn.setFixedSize(32, 32)
        self.llm_clear_btn.setToolTip('Clear chat')
        bindIcon(self.llm_clear_btn, 'chat_add')
        self.llm_clear_btn.clicked.connect(self.clearLLMChat)
        self.llm_viewer_btn = TransparentToolButton(FluentIcon.CHAT)
        self.llm_viewer_btn.setFixedSize(32, 32)
        self.llm_viewer_btn.setToolTip('Onerad')
        self.llm_viewer_btn.clicked.connect(self.toggleLLMViewerExpand)
        self.titleBar.buttonLayout.insertWidget(0, self.llm_viewer_btn)  # type: ignore

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
        ctx.player.positionChanged.connect(ctx.playing_manager.onPlayerPositionChanged)

        if ctx.launch_window:
            ctx.launch_window.top('  Wiring signal connections...')

        self.controller.setParent(self)

        self.folders_list = SListWidget()
        self.folders_list.itemClicked.connect(self._onFolderItemClicked)
        self.folders_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._folder_header_items: list[tuple[QListWidgetItem, str]] = []

        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(0, 48, 0, 52)

        self.settings_btn = TransparentPushButton('')
        bindText(self.settings_btn, 'main_window.settings')
        bindIcon(self.settings_btn, 'settings')
        self.session_btn = TransparentPushButton('')
        bindText(self.session_btn, 'main_window.account')
        bindIcon(self.session_btn, 'session')
        self.settings_btn.clicked.connect(self._onSettingsClicked)
        self.session_btn.clicked.connect(self._onSessionClicked)

        self.refresh_button = TransparentPushButton(FluentIcon.SYNC, '')
        bindText(self.refresh_button, 'main_window.refresh')
        self.refresh_button.clicked.connect(lambda: self.refreshFolders())

        left_layout.addWidget(self.refresh_button)
        left_layout.addWidget(self.folders_list, 1)
        left_layout.addWidget(self.settings_btn)
        left_layout.addWidget(self.session_btn)

        self.hBoxLayout.addLayout(left_layout, 1)
        self.hBoxLayout.addWidget(contents_widget, 5)
        self.llm_viewer_panel = QFrame()
        self.llm_viewer_panel.setFixedWidth(
            LLM_VIEWER_WIDTH if self.llm_viewer_expanded else 0
        )
        self.llm_viewer_panel.setVisible(self.llm_viewer_expanded)
        self.llm_viewer_panel.setObjectName('llm_viewer_panel')
        self.llm_viewer_panel.setStyleSheet(
            'QFrame#llm_viewer_panel { border-left: 1px solid rgba(128, 128, 128, 60); }'
        )
        llm_panel_layout = QVBoxLayout()
        llm_panel_layout.setContentsMargins(0, 48, 0, 52)
        llm_panel_layout.setSpacing(8)
        self.llm_viewer_panel.setLayout(llm_panel_layout)

        llm_header = QWidget()
        llm_header_layout = QHBoxLayout()
        llm_header_layout.setContentsMargins(6, 0, 6, 0)
        llm_header_layout.addStretch(1)
        llm_header_layout.addWidget(self.llm_clear_btn)
        llm_header.setLayout(llm_header_layout)
        llm_panel_layout.addWidget(llm_header)

        self.llm_chat_scroller = SScrollArea()
        self.llm_chat_scroller.setWidgetResizable(True)
        self.llm_chat_widget = QWidget()
        self.llm_chat_widget.setFixedWidth(LLM_VIEWER_WIDTH)
        self.llm_chat_layout = SFlowLayout(needAni=True)
        self.llm_chat_layout.setAnimation(400, QEasingCurve.Type.OutCubic)
        self.llm_chat_layout.setContentsMargins(10, 8, 10, 8)
        self.llm_chat_layout.setSpacing(8)
        self.llm_chat_layout.addStretch(1)
        self.llm_chat_widget.setLayout(self.llm_chat_layout)
        self.llm_chat_scroller.setWidget(self.llm_chat_widget)
        llm_panel_layout.addWidget(self.llm_chat_scroller, 1)

        self.llm_input_widget = QWidget()
        llm_input_layout = QHBoxLayout()
        llm_input_layout.setContentsMargins(6, 0, 6, 0)
        llm_input_layout.setSpacing(4)
        self.llm_input_widget.setLayout(llm_input_layout)
        self.llm_input = TextEdit()
        self.llm_input.setFixedHeight(40)
        self.llm_shifting: bool = False
        self.llm_input.setPlaceholderText('Ask Onerad')
        self.input_origin_press = self.llm_input.keyPressEvent
        self.input_origin_release = self.llm_input.keyReleaseEvent
        self.llm_input.keyPressEvent = self.handleLLMInputKeyPress
        self.llm_input.keyReleaseEvent = self.handleLLMInputKeyRelease
        self.llm_model_box = ComboBox()
        self.llm_model_box.setFixedWidth(150)
        self.refreshLLMModelBox()
        self.llm_model_box.currentIndexChanged.connect(self._onLLMModelChanged)
        self.llm_send_btn = TransparentToolButton(FluentIcon.SEND)
        self.llm_input.scrollDelegate = SSmoothDelegate(self.llm_input)  # type: ignore
        self.llm_send_btn.setFixedSize(32, 32)
        self.llm_send_btn.clicked.connect(self.onLLMSendButtonClicked)
        llm_input_layout.addWidget(self.llm_model_box)
        llm_input_layout.addWidget(self.llm_input, 1)
        llm_input_layout.addWidget(self.llm_send_btn)
        llm_panel_layout.addWidget(self.llm_input_widget)
        self.hBoxLayout.addWidget(self.llm_viewer_panel, 0)

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
            self.resize(
                cfg.window_width
                + (LLM_WINDOW_WIDTH_DELTA if self.llm_viewer_expanded else 0),
                cfg.window_height,
            )

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

        self.debug_overlay = DebugOverlay(ctx, self)
        geo = self.rect()
        geo.setWidth(int(self.width() * 0.25))
        self.debug_overlay.setGeometry(geo)
        self.debug_overlay.raise_()

        self.scroll_timer = QTimer(self)
        self.scroll_timer.timeout.connect(self._scrollLLMChatToBottom)

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
        event_bus.subscribe(LANGUAGE_CHANGED, self.updateLanguage)

    def handleLLMInputKeyPress(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Return and not self.llm_shifting:
            self.onLLMSendButtonClicked()
        elif event.key() == Qt.Key.Key_Shift:
            self.llm_shifting = True
            self.llm_input.setFixedHeight(
                40
                + min(
                    150,
                    30
                    * max(
                        0, len(self.llm_input.toPlainText().strip().splitlines()) - 1
                    ),
                )
            )
            self.input_origin_press(event)
        else:
            self.llm_input.setFixedHeight(
                40
                + min(
                    150,
                    30
                    * max(
                        0, len(self.llm_input.toPlainText().strip().splitlines()) - 1
                    ),
                )
            )
            self.input_origin_press(event)

    def handleLLMInputKeyRelease(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Shift:
            self.llm_shifting = False
        self.input_origin_release(event)

    def updateLanguage(self) -> None:
        for item, key in self._folder_header_items:
            item.setText(tr(key))

    def refreshLLMModelBox(self) -> None:
        if not hasattr(self, 'llm_model_box'):
            return
        options: list[tuple[str, str, str]] = []
        display_counts: dict[str, int] = {}
        for provider in cfg.llm_providers:
            provider_name = str(provider.get('name', '')).strip()
            models = provider.get('models', [])
            if not provider_name or not isinstance(models, list):
                continue
            for item in models:
                if not isinstance(item, dict):
                    continue
                model_id = str(item.get('id', '')).strip()
                display_name = str(item.get('display_name', '')).strip()
                if not model_id or not display_name:
                    continue
                options.append((provider_name, model_id, display_name))
                display_counts[display_name] = display_counts.get(display_name, 0) + 1

        self.llm_model_box.blockSignals(True)
        self.llm_model_box.clear()
        for provider_name, model_id, display_name in options:
            text = (
                f'{display_name} ({provider_name})'
                if display_counts.get(display_name, 0) > 1
                else display_name
            )
            self.llm_model_box.addItem(text, userData=(provider_name, model_id))
        current = (cfg.llm_current_provider, cfg.llm_current_model)
        index = self.llm_model_box.findData(current)
        if index < 0 and self.llm_model_box.count():
            index = 0
            data = self.llm_model_box.itemData(index)
            if isinstance(data, tuple) and len(data) == 2:
                cfg.llm_current_provider = str(data[0])
                cfg.llm_current_model = str(data[1])
        self.llm_model_box.setCurrentIndex(index if index >= 0 else -1)
        self.llm_model_box.blockSignals(False)

    def _onLLMModelChanged(self) -> None:
        data = self.llm_model_box.currentData()
        if not isinstance(data, tuple) or len(data) != 2:
            return
        cfg.llm_current_provider = str(data[0])
        cfg.llm_current_model = str(data[1])
        self._syncLegacyLlmConfig()
        saveConfig()

    def _syncLegacyLlmConfig(self) -> None:
        for provider in cfg.llm_providers:
            if str(provider.get('name', '')) != cfg.llm_current_provider:
                continue
            cfg.llm_base_url = str(provider.get('base_url', ''))
            cfg.llm_api_key_encrypted = str(provider.get('api_key_encrypted', ''))
            cfg.llm_model = cfg.llm_current_model
            return

    def onStackedWidgetChanged(self):
        if self.dp_expanded and not self.dp_animating:
            self.togglePlayingPageExpand()

    def _onSettingsClicked(self):
        self.contents_widget.setCurrentWidget(self._stp)

    def _onSessionClicked(self):
        self.contents_widget.setCurrentWidget(self._sep)

    def search(self):
        if not self.search_input.text().strip():
            InfoBar.warning(
                tr('main_window.search_failed'),
                tr('main_window.the_keyword_is_empty'),
                parent=self,
            )
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

    def toggleLLMViewerExpand(self) -> None:
        if self.llm_viewer_animating:
            return
        self.llm_viewer_expanded = not self.llm_viewer_expanded
        self.llm_viewer_animating = True

        start_width = self.llm_viewer_panel.width()
        end_width = LLM_VIEWER_WIDTH if self.llm_viewer_expanded else 0
        window_delta = (
            LLM_WINDOW_WIDTH_DELTA
            if self.llm_viewer_expanded
            else -LLM_WINDOW_WIDTH_DELTA
        )
        if self.llm_viewer_expanded:
            self.llm_viewer_panel.show()

        anim = QPropertyAnimation(self.llm_viewer_panel, b'minimumWidth', self)
        anim.setDuration(200)
        anim.setEasingCurve(
            QEasingCurve.Type.OutCirc
            if self.llm_viewer_expanded
            else QEasingCurve.Type.InCirc
        )
        anim.setStartValue(start_width)
        anim.setEndValue(end_width)

        width_anim = QPropertyAnimation(self.llm_viewer_panel, b'maximumWidth', self)
        width_anim.setDuration(200)
        width_anim.setEasingCurve(anim.easingCurve())
        width_anim.setStartValue(start_width)
        width_anim.setEndValue(end_width)

        window_anim: QPropertyAnimation | None = None
        if not self.isMaximized():
            geometry = self.geometry()
            window_anim = QPropertyAnimation(self, b'geometry', self)
            window_anim.setDuration(200)
            window_anim.setEasingCurve(anim.easingCurve())
            window_anim.setStartValue(geometry)
            window_anim.setEndValue(
                QRect(
                    geometry.x(),
                    geometry.y(),
                    geometry.width() + window_delta,
                    geometry.height(),
                )
            )

        def fini() -> None:
            self.llm_viewer_animating = False
            self.llm_viewer_panel.setFixedWidth(end_width)
            cfg.llm_viewer_expanded = self.llm_viewer_expanded
            if not self.llm_viewer_expanded:
                self.llm_viewer_panel.hide()

        anim.finished.connect(fini)
        width_anim.finished.connect(fini)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        width_anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        if window_anim is not None:
            window_anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def onLLMSendButtonClicked(self) -> None:
        if self.llm_streaming:
            self.stopLLMMessage()
            return
        self.sendLLMMessage()

    def sendLLMMessage(self) -> None:
        message = self.llm_input.toPlainText().strip()
        self.llm_input.clear()
        self.llm_input.setFixedHeight(40)
        if not message or self.llm_streaming:
            return

        if self.llm_pending_tools and self._isLLMConfirmMessage(message):
            self._executePendingLLMTools(message)
            return

        if self.llm_pending_tools:
            self._clearPendingLLMTools()

        self.llm_generation += 1
        generation = self.llm_generation
        cancel_event = threading.Event()
        self.llm_tool_cards.clear()
        self.llm_streaming = True
        self.llm_cancel_event = cancel_event
        self.llm_input.clear()
        self._setLLMSendButtonStreaming(True)

        user_card = CardWidget()
        user_card.setFixedWidth(LLM_VIEWER_WIDTH - 20)
        user_label = QLabel(message)
        user_label.setWordWrap(True)
        user_layout = QVBoxLayout()
        user_layout.setContentsMargins(12, 8, 12, 8)
        user_layout.addWidget(user_label)
        user_card.setLayout(user_layout)
        self._insertBeforeStretch(user_card)

        self.llm_messages.append({'role': 'user', 'content': message})
        self.llm_messages.append({'role': 'assistant', 'content': ''})
        response_index = len(self.llm_messages) - 1

        history = self.llm_messages.copy()
        history.pop()
        history.pop()

        def _run() -> None:
            response_parts: list[str] = []
            runner = LLMToolRunner(
                self.ctx,
                lambda run_id, name, content: self._onLLMToolMessage(
                    generation,
                    run_id,
                    name,
                    content,
                ),
                lambda plan, tools: self._setPendingLLMTools(
                    generation,
                    plan,
                    tools,
                ),
            )

            def _flush_post_actions() -> None:
                try:
                    runner.flushPostActions()
                except Exception as e:
                    self._logger.exception(e)

            try:
                self.ctx.addScheduledTask(lambda: self.scroll_timer.start(200))
                for chunk in self.ctx.llm.streamChat(
                    message,
                    history,
                    tools=llmToolSchemas(),
                    tool_runner=runner.runTool,
                    after_tool_round=_flush_post_actions,
                    cancel_event=cancel_event,
                ):
                    response_parts.append(chunk)
                    self.ctx.addScheduledTask(self._appendLLMChunk, generation, chunk)
                response = ''.join(response_parts)

                def _done() -> None:
                    if not self._isCurrentLLMGeneration(generation):
                        return
                    self._finishLLMViewer(generation)
                    self._setLLMMessageContent(response_index, response)
                    self.llm_stream_viewer = None
                    self.llm_streaming = False
                    self.llm_cancel_event = None
                    self.llm_tool_cards.clear()
                    self._setLLMSendButtonStreaming(False)
                    self.llm_input.setFocus()

                    self.scroll_timer.stop()
                    self._scrollLLMChatToBottom()

                self.ctx.addScheduledTask(_done)
            except Exception as e:
                self._logger.exception(e)
                _flush_post_actions()
                error_text = str(e)

                def _error() -> None:
                    if not self._isCurrentLLMGeneration(generation):
                        return
                    self._appendLLMChunk(generation, f'\n\nError: {error_text}')
                    self._finishLLMViewer(generation)
                    self._setLLMMessageContent(response_index, f'Error: {error_text}')
                    self.llm_stream_viewer = None
                    self.llm_streaming = False
                    self.llm_cancel_event = None
                    self.llm_tool_cards.clear()
                    self._setLLMSendButtonStreaming(False)
                    self.llm_input.setFocus()

                    self.scroll_timer.stop()
                    self._scrollLLMChatToBottom()

                self.ctx.addScheduledTask(_error)

        self.llm_stream_thread = threading.Thread(target=_run, daemon=True)
        self.llm_stream_thread.start()

    def _executePendingLLMTools(self, message: str) -> None:
        tools = self.llm_pending_tools.copy()
        if not tools:
            return
        if self.llm_cancel_event is not None:
            self.llm_cancel_event.set()
        self.llm_generation += 1
        generation = self.llm_generation
        cancel_event = threading.Event()
        self._clearPendingLLMTools()
        self.llm_tool_cards.clear()
        self.llm_input.clear()
        self.llm_streaming = True
        self.llm_cancel_event = cancel_event
        self._setLLMSendButtonStreaming(True)

        user_card = CardWidget()
        user_card.setFixedWidth(LLM_VIEWER_WIDTH - 20)
        user_label = QLabel(message)
        user_label.setWordWrap(True)
        user_layout = QVBoxLayout()
        user_layout.setContentsMargins(12, 8, 12, 8)
        user_layout.addWidget(user_label)
        user_card.setLayout(user_layout)
        self._insertBeforeStretch(user_card)

        def _run() -> None:
            runner = LLMToolRunner(
                self.ctx,
                lambda run_id, name, content: self._onLLMToolMessage(
                    generation,
                    run_id,
                    name,
                    content,
                ),
                allow_actions=True,
                require_usage=False,
            )
            results: list[str] = []

            def _flush_post_actions() -> None:
                try:
                    runner.flushPostActions()
                except Exception as e:
                    self._logger.exception(e)

            try:
                for item in tools:
                    if cancel_event.is_set():
                        break
                    name = str(item.get('name', ''))
                    arguments = item.get('arguments', {})
                    if not isinstance(arguments, dict):
                        arguments = {}
                    results.append(runner.runTool(name, arguments))
                    _flush_post_actions()

                summary = 'Done.' if results else 'No tools executed.'

                def _done() -> None:
                    if not self._isCurrentLLMGeneration(generation):
                        return
                    self._appendLLMChunk(generation, summary)
                    self._finishLLMViewer(generation)
                    self.llm_messages.append({'role': 'user', 'content': message})
                    self.llm_messages.append({'role': 'assistant', 'content': summary})
                    self.llm_stream_viewer = None
                    self.llm_streaming = False
                    self.llm_cancel_event = None
                    self.llm_tool_cards.clear()
                    self._setLLMSendButtonStreaming(False)
                    self.scroll_timer.stop()
                    self._scrollLLMChatToBottom()

                self.ctx.addScheduledTask(_done)
            except Exception as e:
                self._logger.exception(e)
                _flush_post_actions()
                error_text = str(e)

                def _error() -> None:
                    if not self._isCurrentLLMGeneration(generation):
                        return
                    self._appendLLMChunk(generation, f'Error: {error_text}')
                    self._finishLLMViewer(generation)
                    self.llm_stream_viewer = None
                    self.llm_streaming = False
                    self.llm_cancel_event = None
                    self.llm_tool_cards.clear()
                    self._setLLMSendButtonStreaming(False)
                    self.scroll_timer.stop()
                    self._scrollLLMChatToBottom()

                self.ctx.addScheduledTask(_error)

        self.llm_stream_thread = threading.Thread(target=_run, daemon=True)
        self.llm_stream_thread.start()

    def _isLLMConfirmMessage(self, message: str) -> bool:
        text = message.strip().lower()
        return text in {
            'ok',
            'yes',
            'y',
            'confirm',
            'confirmed',
            'go',
            'run',
            'execute',
            '好',
            '好的',
            '确认',
            '可以',
            '执行',
            '开始',
        }

    def stopLLMMessage(self) -> None:
        if self.llm_cancel_event is not None:
            self.llm_cancel_event.set()
        self.llm_generation += 1
        self.llm_streaming = False
        self.llm_cancel_event = None
        self.llm_tool_cards.clear()
        self._clearPendingLLMTools()
        self._finishLLMViewer()
        self._setLLMSendButtonStreaming(False)
        self.scroll_timer.stop()

    def _setLLMSendButtonStreaming(self, streaming: bool) -> None:
        if streaming:
            bindIcon(self.llm_send_btn, 'stop_gen')
            self.llm_send_btn.setToolTip('Stop')
        else:
            self.llm_send_btn.setIcon(FluentIcon.SEND)
            self.llm_send_btn.setToolTip('Send')

    def _onLLMToolMessage(
        self,
        generation: int,
        run_id: str,
        name: str,
        content: str,
    ) -> None:
        self.ctx.addScheduledTask(
            self._addLLMToolCard,
            generation,
            run_id,
            name,
            content,
        )

    def _setPendingLLMTools(
        self,
        generation: int,
        plan: str,
        tools: list[dict[str, object]],
    ) -> None:
        self.ctx.addScheduledTask(
            self._applyPendingLLMTools,
            generation,
            plan,
            tools,
        )

    def _applyPendingLLMTools(
        self,
        generation: int,
        plan: str,
        tools: list[dict[str, object]],
    ) -> None:
        if not self._isCurrentLLMGeneration(generation):
            return
        self.llm_pending_plan = plan
        self.llm_pending_tools = tools
        self._addLLMConfirmCard(generation, plan, tools)

    def _addLLMConfirmCard(
        self,
        generation: int,
        plan: str,
        tools: list[dict[str, object]],
    ) -> None:
        if not self._isCurrentLLMGeneration(generation):
            return
        self._finishLLMViewer(generation)
        self._removeLLMConfirmCard()

        card = CardWidget()
        card.setFixedWidth(LLM_VIEWER_WIDTH - 20)

        title_label = QLabel('需要确认')
        title_label.setWordWrap(False)
        plan_label = QLabel(plan.strip() or '确认后执行这些操作。')
        plan_label.setWordWrap(True)

        tools_text = self._formatLLMPendingTools(tools)
        tools_label = QLabel()
        tools_label.setWordWrap(False)
        tools_label.setToolTip(tools_text)
        tools_label.setText(self._elideLLMToolText(tools_label, tools_text))

        confirm_btn = TransparentPushButton('确认执行')
        cancel_btn = TransparentPushButton('取消')
        confirm_btn.setEnabled(bool(tools))
        confirm_btn.clicked.connect(self._confirmPendingLLMTools)
        cancel_btn.clicked.connect(self._cancelPendingLLMTools)
        self.llm_confirm_buttons = [confirm_btn, cancel_btn]

        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addStretch(1)
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(confirm_btn)

        layout = QVBoxLayout()
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)
        layout.addWidget(title_label)
        layout.addWidget(plan_label)
        layout.addWidget(tools_label)
        layout.addLayout(button_layout)
        card.setLayout(layout)

        self.llm_confirm_card = card
        self._insertBeforeStretch(card)
        self._scrollLLMChatToBottom()

    def _confirmPendingLLMTools(self) -> None:
        for button in self.llm_confirm_buttons:
            button.setEnabled(False)
        self._executePendingLLMTools('确认执行')

    def _cancelPendingLLMTools(self) -> None:
        self._clearPendingLLMTools()
        self._scrollLLMChatToBottom()

    def _clearPendingLLMTools(self) -> None:
        self.llm_pending_plan = ''
        self.llm_pending_tools.clear()
        self._removeLLMConfirmCard()

    def _removeLLMConfirmCard(self) -> None:
        if self.llm_confirm_card is None:
            self.llm_confirm_buttons.clear()
            return
        self.llm_chat_layout.removeWidget(self.llm_confirm_card)
        self.llm_confirm_card.deleteLater()
        self.llm_confirm_card = None
        self.llm_confirm_buttons.clear()

    def _formatLLMPendingTools(self, tools: list[dict[str, object]]) -> str:
        names = [str(item.get('name', '')).strip() for item in tools]
        names = [name for name in names if name]
        if not names:
            return '无待执行工具'
        return '工具: ' + ', '.join(names)

    def _addLLMToolCard(
        self,
        generation: int,
        run_id: str,
        name: str,
        content: str,
    ) -> None:
        if not self._isCurrentLLMGeneration(generation):
            return
        card_data = self.llm_tool_cards.get(run_id)
        if card_data is None:
            self._finishLLMViewer(generation)
            card = CardWidget()
            card.setFixedWidth(LLM_VIEWER_WIDTH - 20)
            label = QLabel()
            label.setWordWrap(False)
            layout = QVBoxLayout()
            layout.setContentsMargins(12, 8, 12, 8)
            layout.addWidget(label)
            card.setLayout(layout)
            self.llm_tool_cards[run_id] = (card, label)
            self._insertBeforeStretch(card)
        else:
            card, label = card_data

        full_text = f'{name}: {content}'.replace('\n', ' ')
        label.setToolTip(full_text)
        label.setText(self._elideLLMToolText(label, full_text))
        if content != 'running':
            self.llm_tool_cards.pop(run_id, None)
        self._scrollLLMChatToBottom()

    def _appendLLMChunk(self, generation: int, chunk: str) -> None:
        if not self._isCurrentLLMGeneration(generation):
            return
        if self.llm_stream_viewer is None:
            self.llm_stream_viewer = ChattingViewer()
            self.llm_stream_viewer.setFixedWidth(LLM_VIEWER_WIDTH - 20)
            self._insertBeforeStretch(self.llm_stream_viewer)
        self.llm_stream_viewer.appendChunk(chunk)

    def _finishLLMViewer(self, generation: int | None = None) -> None:
        if generation is not None and not self._isCurrentLLMGeneration(generation):
            return
        if self.llm_stream_viewer is None:
            return
        self.llm_stream_viewer.finishStream()
        self.llm_stream_viewer = None

    def _isCurrentLLMGeneration(self, generation: int) -> bool:
        return generation == self.llm_generation

    def _setLLMMessageContent(self, index: int, content: str) -> None:
        if 0 <= index < len(self.llm_messages):
            self.llm_messages[index]['content'] = content

    def _elideLLMToolText(self, label: QLabel, text: str) -> str:
        width = max(40, LLM_VIEWER_WIDTH - 48)
        return label.fontMetrics().elidedText(
            text,
            Qt.TextElideMode.ElideRight,
            width,
        )

    def _insertBeforeStretch(self, widget: QWidget) -> None:
        self.llm_chat_layout.addWidget(widget)

    def _scrollLLMChatToBottom(self) -> None:
        bar = self.llm_chat_scroller.verticalScrollBar()
        self.llm_chat_scroller.delegate.vScrollBar.scrollValue(
            bar.maximum() - bar.value()
        )

    def clearLLMChat(self) -> None:
        if self.llm_streaming:
            return
        self.llm_generation += 1
        while self.llm_chat_layout.count() > 0:
            item = self.llm_chat_layout.takeAt(0)
            widget = item.widget()  # type: ignore
            if widget is not None:
                widget.deleteLater()
        self.llm_messages.clear()
        self.llm_stream_viewer = None
        self.llm_cancel_event = None
        self.llm_tool_cards.clear()
        self._clearPendingLLMTools()

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

    def updateDatas(self, multiple_factor: float = 1.0) -> None:
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
            return

        if self.ctx.debugging:
            self.debug_overlay.refresh()

    def addScheduledTask(self, task, *args, **kwargs) -> None:
        self.ctx.addScheduledTask(task, *args, **kwargs)

    def play(self, card: SearchSongCard) -> None:
        self._logger.debug(card.info.id)
        storable = SongStorable(
            info=SongInfo(
                name=card.info.name,
                artists=card.info.artists,
                id=str(card.info.id),
                privilege=card.info.privilege.fee,
                duration=card.info.duration,
            )
        )
        event_bus.emit(PLAY_STORABLE, storable)

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

        def _finish_init():
            if last_playlist:
                self._launchwindow.top('restore playlist...')
                self._dp.playlist = list(last_playlist)
                if 0 <= last_playing_index < len(last_playlist):
                    self._launchwindow.top('continue last song...')

                    def _continue():
                        event_bus.emit(PLAY_CONTINUE_LAST_SONG, cfg.last_playing_index)

                    self.ctx.addScheduledTask(_continue)

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

            self.ctx.addScheduledTask(_show)

        asyncTask(_init, (), self, finished=_finish_init)

    def refreshFolders(self):
        self._fp.displayEmpty()
        open_folder = self._fp.curr_folder or self._fp.curr_cloud_folder

        self.refresh_button.setEnabled(False)
        self.folders_list.clear()
        self._folder_header_items.clear()

        local_item = QListWidgetItem(tr('main_window.local'))
        self._folder_header_items.append((local_item, 'main_window.local'))
        self.folders_list.addItem(local_item)

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
            and isinstance(open_folder, LocalFolderInfo)
            and open_folder in favorites_manager.folders
        ):
            self._fp.setDisplayFolder(open_folder)

        item = QListWidgetItem()
        widget = TransparentPushButton(FluentIcon.ADD_TO, '')
        bindText(widget, 'main_window.add_folder')
        widget.clicked.connect(self.onAddLocalFolder)
        item.setSizeHint(widget.sizeHint())
        self.folders_list.addItem(item)
        self.folders_list.setItemWidget(item, widget)

        def _cloud():
            self.ctx.addScheduledTask(
                lambda: self._addFolderHeader('main_window.cloud')
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

                if (
                    open_folder
                    and isinstance(open_folder, CloudFolderInfo)
                    and open_folder in playlists
                ):
                    self._fp.setDisplayFolder(open_folder)

                item = QListWidgetItem()
                widget = TransparentPushButton(FluentIcon.ADD_TO, '')
                bindText(widget, 'main_window.add_folder')
                widget.clicked.connect(self.onAddCloudFolder)
                item.setSizeHint(widget.sizeHint())
                self.folders_list.addItem(item)
                self.folders_list.setItemWidget(item, widget)

                self.refresh_button.setEnabled(True)

            self.ctx.addScheduledTask(add)

        if not getBackend().userAnonymous():
            asyncTask(_cloud, (), self)

        saveFavorites()

    def _addFolderHeader(self, key: str) -> None:
        item = QListWidgetItem(tr(key))
        self._folder_header_items.append((item, key))
        self.folders_list.addItem(item)

    def _openFolder(self, folder):
        self.contents_widget.setCurrentWidget(self._fp)
        self._fp.updateGeometry()
        self._fp.setDisplayFolder(folder)

    def onAddCloudFolder(self):
        name = getTextLineedit(
            tr('main_window.add_new_folder'),
            tr('main_window.enter_name_of_your_new_folder'),
            tr('main_window.my_folder'),
            self,
        )
        if name:
            getBackend().createPlaylist(name)
            self.refreshFolders()

    def onAddLocalFolder(self):
        name = getTextLineedit(
            tr('main_window.add_new_folder'),
            tr('main_window.enter_name_of_your_new_folder'),
            tr('main_window.my_folder'),
            self,
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
        e.accept()
        if self.closing:
            return
        self.closing = True

        self.hide()
        playing_manager = getattr(self.ctx, 'playing_manager', None)
        if playing_manager is not None:
            playing_manager.shutdownWorkers()
        shutdown_player = getattr(self._player, 'shutdown', None)
        if shutdown_player is not None:
            shutdown_player()
        else:
            self._player.stop()

        self._ws_server.stop(shutdown_json_sender=True)

        cfg.last_playlist = self._dp.playlist.copy()
        cfg.last_playing_index = self._dp.current_index
        cfg.last_playing_time = self._player.getPosition()

        cfg.window_x = self.x()
        cfg.window_y = self.y()
        cfg.window_width = self.width() - (
            LLM_WINDOW_WIDTH_DELTA if self.llm_viewer_expanded else 0
        )
        cfg.window_height = self.height()
        cfg.window_maximized = self.isMaximized()
        cfg.llm_viewer_expanded = self.llm_viewer_expanded

        saveConfig()
        saveFavorites()

        self._app.quit()

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

        if hasattr(self, 'debug_overlay'):
            geo = self.rect()
            geo.setWidth(int(self.width() * 0.25))
            self.debug_overlay.setGeometry(geo)
            self.debug_overlay.raise_()

    def onWebsocketConnected(self):
        InfoBar.success(
            tr('main_window.southside_client_connection'),
            tr('main_window.southside_music_was_connected_to_southsidclient'),
            duration=5000,
            parent=self,
        )
        QTimer.singleShot(
            500,
            lambda: self._ws_handler.sendJson(
                {
                    'option': f'{"disable" if not self._stp.enableFFT_box.isChecked() else "enable"}_fft'
                }
            ),
        )
        QTimer.singleShot(500, self._dp.sendSongFMAndInfo)

        self.connected = True

        self._stp.disconnect_btn.setEnabled(True)
        self._stp.connect_btn.setEnabled(False)

        event_bus.emit(WEBSOCKET_CONNECTED)

    def onWebsocketDisconnected(self):
        InfoBar.warning(
            tr('main_window.southside_client_connection'),
            tr('main_window.southside_music_was_been_disconnected_from_southsidclient'),
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
        elif event.key() == Qt.Key.Key_F3:
            self.ctx.debugging_obj.toggle()
            self.debug_overlay.refresh()
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
                    0,
                    0,
                    toQtInt(self.width() * self.draw_progress),
                    toQtInt(self.bar_height),
                )
            painter.setPen(QColor(255, 255, 255) if theme.isDark() else QColor(0, 0, 0))

        painter.end()
