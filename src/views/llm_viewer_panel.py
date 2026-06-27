from __future__ import annotations

import json
import logging
import math
import threading
from typing import Callable

import clipboard

from core import theme
from core.app_context import AppContext
from core.config import cfg, saveConfig
from core.i18n import bindText, tr
from core.icons import bindIcon
from core.llm_tools import LLMToolRunner, llmToolSchemas
from imports import (
    CardWidget,
    ComboBox,
    FluentIcon,
    QAbstractAnimation,
    QEasingCurve,
    QFrame,
    QHBoxLayout,
    QKeyEvent,
    QLabel,
    QMouseEvent,
    QPropertyAnimation,
    QRect,
    QSizePolicy,
    QSpacerItem,
    QTextCursor,
    QTimer,
    QVBoxLayout,
    QWidget,
    Qt,
    TextBrowser,
    TextEdit,
    TransparentPushButton,
    TransparentToolButton,
)
from views.animated_layout import SFlowLayout
from views.chatting_viewer import ChattingViewer
from views.list_widget import SScrollArea, SSmoothDelegate
from views.number_viewer import NumberViewer


LLM_VIEWER_WIDTH = 475
LLM_WINDOW_WIDTH_DELTA = 350


class LLMMessageCard(CardWidget):
    def __init__(
        self,
        index: int,
        content: str,
        on_edit: Callable[[int], None],
    ) -> None:
        super().__init__()
        self.message_index = index
        self.setProperty('llmMessageIndex', index)
        self.setFixedWidth(LLM_VIEWER_WIDTH - 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        label = QLabel(content)
        label.setWordWrap(True)
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 8, 12, 8)
        layout.addWidget(label)
        self.setLayout(layout)

        self._on_edit = on_edit

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_edit(self.message_index)
        return super().mouseReleaseEvent(event)


class LLMViewerPanel(QFrame):
    def __init__(self, ctx: AppContext, parent: QWidget) -> None:
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self.ctx = ctx
        self._mwindow = parent
        self.expanded = cfg.llm_viewer_expanded
        self.animating = False
        self.llm_streaming = False
        self.llm_messages: list[dict[str, str]] = []
        self.llm_stream_viewer: ChattingViewer | None = None
        self.llm_typing_viewers: set[ChattingViewer] = set()
        self.llm_stream_thread: threading.Thread | None = None
        self.llm_cancel_event: threading.Event | None = None
        self.llm_pending_plan: str = ''
        self.llm_pending_tools: list[dict[str, object]] = []
        self.llm_tool_cards: dict[str, tuple[CardWidget, QLabel]] = {}
        self.llm_confirm_card: CardWidget | None = None
        self.llm_confirm_buttons: list[TransparentPushButton] = []
        self.llm_generation = 0
        self.llm_editing_message_index: int | None = None
        self.llm_chars = 0
        self.llm_tool_calls = 0

        self.setFixedWidth(LLM_VIEWER_WIDTH if self.expanded else 0)
        self.setObjectName('llm_viewer_panel')
        self.setStyleSheet(
            'QFrame#llm_viewer_panel { '
            'border-left: 1px solid rgba(128, 128, 128, 60); }'
        )
        llm_panel_layout = QVBoxLayout()
        llm_panel_layout.setContentsMargins(0, 48, 0, 0)
        llm_panel_layout.setSpacing(8)
        self.setLayout(llm_panel_layout)

        self.llm_clear_btn = TransparentToolButton()
        self.llm_clear_btn.setFixedSize(32, 32)
        self.llm_clear_btn.setToolTip(tr('main_window.llm_clear_chat'))
        bindIcon(self.llm_clear_btn, 'chat_add')
        self.llm_clear_btn.clicked.connect(self.clearLLMChat)

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
        llm_input_layout = QVBoxLayout()
        llm_input_layout.setContentsMargins(6, 0, 6, 6)
        llm_input_layout.setSpacing(4)

        self.info_widget = QWidget()
        info_layout = QHBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        self.info_widget.setLayout(info_layout)
        info_layout.setSpacing(6)

        self.chars_viewer = NumberViewer(
            self.ctx.harmony_font_family, self.ctx, 15, 0.17
        )
        info_layout.addWidget(self.chars_viewer)
        suffix_label = QLabel('')
        bindText(suffix_label, 'main_window.char_outputed_suffix')
        info_layout.addWidget(suffix_label)

        info_layout.addSpacerItem(
            QSpacerItem(
                15,
                0,
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Minimum,
            )
        )

        self.tools_viewer = NumberViewer(
            self.ctx.harmony_font_family, self.ctx, 15, 0.17
        )
        info_layout.addWidget(self.tools_viewer)
        suffix_label = QLabel('')
        bindText(suffix_label, 'main_window.tool_calls_suffix')
        info_layout.addWidget(suffix_label)

        info_layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        )
        self.info_widget.hide()
        llm_input_layout.addWidget(self.info_widget)

        self.llm_input_widget.setLayout(llm_input_layout)
        self.llm_input = TextEdit()
        self.llm_input.setProperty('viewerRole', 'block')
        self.llm_input.setLineWrapMode(TextBrowser.LineWrapMode.WidgetWidth)
        self.llm_input.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._refreshInputStyle()
        self.llm_input.setFixedHeight(40)
        self.llm_shifting: bool = False
        self.llm_input.setPlaceholderText(tr('main_window.llm_ask_onerad'))
        self.input_origin_press = self.llm_input.keyPressEvent
        self.input_origin_release = self.llm_input.keyReleaseEvent
        self.llm_input.keyPressEvent = self.handleLLMInputKeyPress
        self.llm_input.keyReleaseEvent = self.handleLLMInputKeyRelease
        self.llm_model_box = ComboBox()
        self.refreshLLMModelBox()
        self.llm_model_box.currentIndexChanged.connect(self._onLLMModelChanged)
        self.llm_send_btn = TransparentToolButton(FluentIcon.SEND)
        self.llm_input.scrollDelegate = SSmoothDelegate(self.llm_input)  # type: ignore
        self.llm_send_btn.setFixedSize(32, 32)
        self.llm_send_btn.clicked.connect(self.onLLMSendButtonClicked)
        buttons_layout = QHBoxLayout()
        llm_input_layout.addWidget(self.llm_input)
        buttons_layout.addWidget(self.llm_model_box)
        buttons_layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        )
        buttons_layout.addWidget(self.llm_send_btn)
        llm_input_layout.addLayout(buttons_layout)
        llm_panel_layout.addWidget(self.llm_input_widget)

        self.scroll_timer = QTimer(self)
        self.scroll_timer.timeout.connect(self._scrollLLMChatToBottom)

    def _refreshInputStyle(self) -> None:
        self.llm_input.setStyleSheet(
            (
                'TextEdit { background: #101010; border: 1px solid #303030; '
                'border-radius: 6px; padding: 6px; }'
            )
            if theme.isDark()
            else (
                'TextEdit { background: #ffffff; border: 1px solid #dddddd; '
                'border-radius: 6px; padding: 6px; }'
            )
        )

    def onPostThemeChanged(self) -> None:
        self._refreshInputStyle()

    def handleLLMInputKeyPress(self, event: QKeyEvent) -> None:
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

    def handleLLMInputKeyRelease(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Shift:
            self.llm_shifting = False
        self.input_origin_release(event)

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

    def toggleExpand(self) -> None:
        if self.animating:
            return
        self.expanded = not self.expanded
        self.animating = True

        start_width = self.width()
        end_width = LLM_VIEWER_WIDTH if self.expanded else 0
        window_delta = (
            LLM_WINDOW_WIDTH_DELTA if self.expanded else -LLM_WINDOW_WIDTH_DELTA
        )
        if self.expanded:
            self.show()

        anim = QPropertyAnimation(self, b'minimumWidth', self)
        anim.setDuration(200)
        anim.setEasingCurve(
            QEasingCurve.Type.OutCirc if self.expanded else QEasingCurve.Type.InCirc
        )
        anim.setStartValue(start_width)
        anim.setEndValue(end_width)

        width_anim = QPropertyAnimation(self, b'maximumWidth', self)
        width_anim.setDuration(200)
        width_anim.setEasingCurve(anim.easingCurve())
        width_anim.setStartValue(start_width)
        width_anim.setEndValue(end_width)

        window_anim: QPropertyAnimation | None = None
        if not self._mwindow.isMaximized():
            geometry = self._mwindow.geometry()
            window_anim = QPropertyAnimation(self._mwindow, b'geometry', self._mwindow)
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
            self.animating = False
            self.setFixedWidth(end_width)
            cfg.llm_viewer_expanded = self.expanded
            if not self.expanded:
                self.hide()

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
        if not self.info_widget.isVisible():
            self.info_widget.show()

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

        if self.llm_editing_message_index is not None:
            index = self.llm_editing_message_index
            self.llm_editing_message_index = None
            self._truncateLLMChatFrom(index)
            self._sendLLMMessageText(message)
            return

        self._sendLLMMessageText(message)

    def _sendLLMMessageText(self, message: str) -> None:
        self.llm_generation += 1
        generation = self.llm_generation
        cancel_event = threading.Event()
        self.llm_tool_cards.clear()
        self.llm_streaming = True
        self.llm_cancel_event = cancel_event
        self._setLLMSendButtonStreaming(True)

        user_index = len(self.llm_messages)
        user_card = LLMMessageCard(user_index, message, self.editLLMMessage)
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
                    self._tagLLMStreamViewer(response_index)
                    self._finishLLMViewer(generation)
                    self._setLLMMessageContent(response_index, response)
                    self._addLLMCopyButton(response_index)
                    self.llm_streaming = False
                    self.llm_cancel_event = None
                    self.llm_tool_cards.clear()
                    self._setLLMSendButtonStreaming(False)
                    self.llm_input.setFocus()

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
                    self._tagLLMStreamViewer(response_index)
                    self._finishLLMViewer(generation)
                    self._setLLMMessageContent(response_index, f'Error: {error_text}')
                    self._addLLMCopyButton(response_index)
                    self.llm_streaming = False
                    self.llm_cancel_event = None
                    self.llm_tool_cards.clear()
                    self._setLLMSendButtonStreaming(False)
                    self.llm_input.setFocus()

                    self._scrollLLMChatToBottom()

                self.ctx.addScheduledTask(_error)

        self.llm_stream_thread = threading.Thread(target=_run, daemon=True)
        self.llm_stream_thread.start()

    def _executePendingLLMTools(self, message: str) -> None:
        tools = self._orderedPendingLLMTools(self.llm_pending_tools.copy())
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

        user_index = len(self.llm_messages)
        user_card = LLMMessageCard(user_index, message, self.editLLMMessage)
        self._insertBeforeStretch(user_card)
        self.llm_messages.append({'role': 'user', 'content': message})
        self.llm_messages.append({'role': 'assistant', 'content': ''})
        response_index = len(self.llm_messages) - 1

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

                summary = self._formatLLMToolExecutionSummary(results)

                def _done() -> None:
                    if not self._isCurrentLLMGeneration(generation):
                        return
                    self._appendLLMChunk(generation, summary)
                    self._tagLLMStreamViewer(response_index)
                    self._finishLLMViewer(generation)
                    self._setLLMMessageContent(response_index, summary)
                    self._addLLMCopyButton(response_index)
                    self.llm_streaming = False
                    self.llm_cancel_event = None
                    self.llm_tool_cards.clear()
                    self._setLLMSendButtonStreaming(False)
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
                    self._tagLLMStreamViewer(response_index)
                    self._finishLLMViewer(generation)
                    self._setLLMMessageContent(response_index, f'Error: {error_text}')
                    self._addLLMCopyButton(response_index)
                    self.llm_streaming = False
                    self.llm_cancel_event = None
                    self.llm_tool_cards.clear()
                    self._setLLMSendButtonStreaming(False)
                    self._scrollLLMChatToBottom()

                self.ctx.addScheduledTask(_error)

        self.llm_stream_thread = threading.Thread(target=_run, daemon=True)
        self.llm_stream_thread.start()

    def _formatLLMToolExecutionSummary(self, results: list[str]) -> str:
        if not results:
            return tr('main_window.llm_no_tools_executed')
        errors: list[str] = []
        for result in results:
            try:
                payload = json.loads(result)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get('error'):
                errors.append(str(payload['error']))
        if errors:
            return tr('main_window.llm_tools_failed', error=errors[0])
        return tr('main_window.llm_done')

    def _orderedPendingLLMTools(
        self,
        tools: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        grouped: dict[object, list[dict[str, object]]] = {}
        for item in tools:
            folder = self._llmFavoriteToolFolder(item)
            if folder is None:
                continue
            grouped.setdefault(folder, []).append(item)

        for items in grouped.values():
            items.reverse()

        ordered: list[dict[str, object]] = []
        for item in tools:
            folder = self._llmFavoriteToolFolder(item)
            if folder is None:
                ordered.append(item)
            else:
                ordered.append(grouped[folder].pop(0))
        return ordered

    def _llmFavoriteToolFolder(self, item: dict[str, object]) -> str | None:
        if str(item.get('name', '')) != 'favorite_song':
            return None
        arguments = item.get('arguments', {})
        if not isinstance(arguments, dict):
            return None
        folder = arguments.get('folder')
        if not isinstance(folder, str):
            return None
        return folder

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
        self.llm_stream_viewer = None
        self.llm_typing_viewers.clear()
        self._setLLMSendButtonStreaming(False)
        self.scroll_timer.stop()

    def editLLMMessage(self, index: int) -> None:
        if self.llm_streaming:
            return
        if not (0 <= index < len(self.llm_messages)):
            return
        message = self.llm_messages[index]
        if message.get('role') != 'user':
            return
        self.llm_editing_message_index = index
        self.llm_input.setPlainText(message.get('content', ''))
        self.llm_input.setFocus()
        self.llm_input.moveCursor(QTextCursor.MoveOperation.End)

    def _truncateLLMChatFrom(self, index: int) -> None:
        self.llm_generation += 1
        self.llm_messages = self.llm_messages[:index]
        self.llm_stream_viewer = None
        self.llm_typing_viewers.clear()
        self.llm_cancel_event = None
        self.llm_tool_cards.clear()
        self._clearPendingLLMTools()

        remove_from = self.llm_chat_layout.count()
        for layout_index in range(self.llm_chat_layout.count()):
            item = self.llm_chat_layout.itemAt(layout_index)
            widget = item.widget() if item is not None else None
            message_index = widget.property('llmMessageIndex') if widget else None
            if isinstance(message_index, int) and message_index >= index:
                remove_from = layout_index
                break

        while self.llm_chat_layout.count() > remove_from:
            item = self.llm_chat_layout.takeAt(remove_from)
            if item is None:
                break
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                self._clearLayout(child_layout)

    def _clearLayout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                self._clearLayout(child_layout)

    def copyLLMMarkdown(self) -> None:
        parts: list[str] = []
        for browser in self.llm_chat_widget.findChildren(TextBrowser):
            markdown = browser.toMarkdown().strip()
            if markdown:
                parts.append(markdown)
        if not parts:
            return
        clipboard.copy('\n\n'.join(parts))

    def _addLLMCopyButton(self, message_index: int) -> None:
        wrapper = QWidget()
        wrapper.setProperty('llmMessageIndex', message_index)
        wrapper.setFixedWidth(LLM_VIEWER_WIDTH - 20)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)
        button = TransparentToolButton(FluentIcon.COPY)
        button.setFixedSize(28, 28)
        button.setToolTip(tr('main_window.llm_copy_chat'))
        button.clicked.connect(self.copyLLMMarkdown)
        layout.addWidget(button)
        wrapper.setLayout(layout)
        self._insertBeforeStretch(wrapper)

    def _setLLMSendButtonStreaming(self, streaming: bool) -> None:
        if streaming:
            bindIcon(self.llm_send_btn, 'stop_gen')
            self.llm_send_btn.setToolTip(tr('main_window.llm_stop'))
        else:
            self.llm_send_btn.setIcon(FluentIcon.SEND)
            self.llm_send_btn.setToolTip(tr('main_window.llm_send'))

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

        title_label = QLabel(tr('main_window.llm_needs_confirmation'))
        title_label.setWordWrap(False)
        plan_label = QLabel(plan.strip() or tr('main_window.llm_confirm_then_execute'))
        plan_label.setWordWrap(True)

        tools_text = self._formatLLMPendingTools(tools)
        tools_label = QLabel()
        tools_label.setWordWrap(False)
        tools_label.setToolTip(tools_text)
        tools_label.setText(self._elideLLMToolText(tools_label, tools_text))

        confirm_btn = TransparentPushButton(tr('main_window.llm_confirm_execute'))
        cancel_btn = TransparentPushButton(tr('main_window.llm_cancel'))
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
        self._executePendingLLMTools(tr('main_window.llm_confirm_execute'))

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
            return tr('main_window.llm_no_pending_tools_parsed')
        return tr('main_window.llm_tools_prefix') + ', '.join(names)

    def _addLLMToolCard(
        self,
        generation: int,
        run_id: str,
        name: str,
        content: str,
    ) -> None:
        if not self._isCurrentLLMGeneration(generation):
            return
        self.llm_tool_calls += 0.5
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
            self.llm_stream_viewer.charReceived.connect(
                lambda length: setattr(self, 'llm_chars', self.llm_chars + length)
            )
            viewer = self.llm_stream_viewer
            self.llm_typing_viewers.add(viewer)
            viewer.finished.connect(
                lambda g=generation, v=viewer: self._onLLMViewerFinished(g, v)
            )
            self.llm_stream_viewer.setFixedWidth(LLM_VIEWER_WIDTH - 20)
            self._insertBeforeStretch(self.llm_stream_viewer)
        self.llm_stream_viewer.appendChunk(chunk)

    def _tagLLMStreamViewer(self, index: int) -> None:
        if self.llm_stream_viewer is not None:
            self.llm_stream_viewer.setProperty('llmMessageIndex', index)

    def _finishLLMViewer(self, generation: int | None = None) -> None:
        if generation is not None and not self._isCurrentLLMGeneration(generation):
            return
        if self.llm_stream_viewer is None:
            return
        viewer = self.llm_stream_viewer
        self.llm_stream_viewer = None
        viewer.finishStream()

    def _onLLMViewerFinished(
        self,
        generation: int,
        viewer: ChattingViewer,
    ) -> None:
        if not self._isCurrentLLMGeneration(generation):
            return
        self.llm_typing_viewers.discard(viewer)
        if not self.llm_streaming:
            if not self.llm_typing_viewers:
                self.scroll_timer.stop()
        self._scrollLLMChatToBottom()

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

        self.chars_viewer.setText(str(self.llm_chars))
        self.tools_viewer.setText(str(math.floor(self.llm_tool_calls)))

    def clearLLMChat(self) -> None:
        if self.info_widget.isVisible():
            self.info_widget.hide()
            self.llm_chars = 0
            self.llm_tool_calls = 0

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
        self.llm_typing_viewers.clear()
        self.llm_cancel_event = None
        self.llm_tool_cards.clear()
        self._clearPendingLLMTools()
