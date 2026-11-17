from __future__ import annotations

import logging
import math
import threading
from typing import override

import shiboken6
from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QResizeEvent, QTextCursor, QTextOption, Qt
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import TextBrowser
from core.compute_worker import callCompute
from core.markdown_renderer import renderMarkdown, renderMarkdownBlocks


__all__ = ['ChattingViewer', 'renderMarkdown']


_MARKDOWN_STYLE = (
    '<style>'
    'body{color:inherit;}'
    'pre{white-space:pre-wrap; word-wrap:break-word; background:rgba(128,128,128,.16); padding:8px; border-radius:4px;}'
    'code{font-family:monospace;}'
    'table{border-collapse:collapse;} th,td{border:1px solid #666; padding:4px 8px;}'
    '.math-inline{vertical-align:middle; margin:0 2px;}'
    '.math-block{display:block; max-width:100%; margin:8px auto;}'
    '.math-inline,.math-block{font-family:serif; color:inherit;}'
    '.latex-output{margin:4px 0;}'
    '</style>'
)


class ChattingViewer(QWidget):
    finished = Signal()
    charReceived = Signal(int)
    markdownReady = Signal(int, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._markdown = ''
        self._append_buffer = ''
        self._stream_finished = False
        self._desired_generation = 0
        self._render_running = False
        self._render_pending = False
        self._render_finish = False
        self._rendered_blocks: list[str] = []
        self._block_positions: list[int] = []
        self.markdownReady.connect(
            self._applyMarkdownBlocks,
            Qt.ConnectionType.QueuedConnection,
        )

        self._append_timer = QTimer(self)
        self._append_timer.setSingleShot(True)
        self._append_timer.timeout.connect(self._drainAppendBuffer)

        self.browser = TextBrowser(self)
        self.browser.setReadOnly(True)
        self.browser.setOpenExternalLinks(True)
        self.browser.setLineWrapMode(TextBrowser.LineWrapMode.WidgetWidth)
        self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.browser.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.browser.setStyleSheet(
            'background: transparent; border: none; padding: 0px; font-size: 15px;'
        )

        text_option = self.browser.document().defaultTextOption()
        text_option.setWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self.browser.document().setDefaultTextOption(text_option)
        self.browser.document().setDocumentMargin(4)
        self.browser.document().setDefaultStyleSheet(_MARKDOWN_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.browser)

    def reset(self) -> None:
        self._append_timer.stop()
        self._markdown = ''
        self._append_buffer = ''
        self._stream_finished = False
        self._desired_generation += 1
        self._render_pending = False
        self._render_finish = False
        self._rendered_blocks = []
        self._block_positions = []
        self.browser.clear()
        self._fitDocument()

    def toMarkdown(self) -> str:
        return self._markdown

    def appendChunk(self, chunk_content: str) -> None:
        if not chunk_content:
            return
        self._stream_finished = False
        self._append_buffer += chunk_content
        if not self._append_timer.isActive():
            self._append_timer.start(80 if len(self._markdown) <= 8192 else 250)

    def finishStream(self) -> None:
        self._stream_finished = True
        self._append_timer.stop()
        self._consumeAppendBuffer(len(self._append_buffer))
        if not self._markdown:
            self._stream_finished = False
            self.finished.emit()
            return
        self._render_finish = True
        self._requestMarkdownRender()

    def _drainAppendBuffer(self) -> None:
        if self._append_buffer:
            self._consumeAppendBuffer(len(self._append_buffer))
            self._requestMarkdownRender()

        if self._stream_finished and not self._markdown:
            self._stream_finished = False
            self.finished.emit()

    def _consumeAppendBuffer(self, length: int) -> None:
        if length <= 0:
            return
        chunk = self._append_buffer[:length]
        self._append_buffer = self._append_buffer[length:]
        self._markdown += chunk
        self.charReceived.emit(len(chunk))

    def _requestMarkdownRender(self) -> None:
        self._desired_generation += 1
        self._render_pending = True
        if not self._render_running:
            self._startMarkdownRender()

    def _startMarkdownRender(self) -> None:
        self._render_running = True
        self._render_pending = False
        generation = self._desired_generation
        markdown = self._markdown
        math_color = self.browser.palette().text().color().name()

        def _render() -> None:
            try:
                result = callCompute(
                    'render_markdown_blocks',
                    {'markdown': markdown, 'math_color': math_color},
                )
                blocks = (
                    result
                    if isinstance(result, list)
                    else renderMarkdownBlocks(markdown, math_color)
                )
            except Exception as error:
                logging.getLogger(__name__).exception(error)
                blocks = renderMarkdownBlocks(markdown, math_color)
            if not shiboken6.isValid(self):
                return
            try:
                self.markdownReady.emit(generation, blocks)
            except RuntimeError:
                return

        threading.Thread(target=_render, daemon=True).start()

    def _applyMarkdownBlocks(self, generation: int, blocks: object) -> None:
        self._render_running = False
        if generation == self._desired_generation and isinstance(blocks, list):
            self._updateBrowserBlocks([str(block) for block in blocks])
        if self._render_pending:
            self._startMarkdownRender()
        if (
            self._render_finish
            and not self._render_pending
            and not self._render_running
        ):
            self._render_finish = False
            self._stream_finished = False
            self.finished.emit()

    def _updateBrowserBlocks(self, blocks: list[str]) -> None:
        old = self._rendered_blocks
        common = 0
        while (
            common < len(old) and common < len(blocks) and old[common] == blocks[common]
        ):
            common += 1
        document = self.browser.document()
        cursor = QTextCursor(document)
        if common < len(old):
            cursor.setPosition(self._block_positions[common])
            cursor.movePosition(
                QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor
            )
            cursor.removeSelectedText()
            del self._rendered_blocks[common:]
            del self._block_positions[common:]
        else:
            cursor.movePosition(QTextCursor.MoveOperation.End)
        for block in blocks[common:]:
            self._block_positions.append(cursor.position())
            cursor.insertHtml(block)
            self._rendered_blocks.append(block)
        text_option = self.browser.document().defaultTextOption()
        text_option.setWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self.browser.document().setDefaultTextOption(text_option)
        self._enableCodeBlockWrapping()
        self._fitDocument()

    def _enableCodeBlockWrapping(self) -> None:
        block = self.browser.document().begin()
        while block.isValid():
            block_format = block.blockFormat()
            if block_format.nonBreakableLines():
                block_format.setNonBreakableLines(False)
                cursor = QTextCursor(block)
                cursor.setBlockFormat(block_format)
            block = block.next()

    def _fitDocument(self) -> None:
        content_width = max(120, self.width())
        self.browser.resize(content_width, self.browser.height())
        viewport_width = max(80, self.browser.viewport().width())
        document = self.browser.document()
        document.setTextWidth(viewport_width)
        height = math.ceil(document.size().height())
        height += self.browser.frameWidth() * 2 + 4
        self.browser.setFixedHeight(max(24, height))
        self.updateGeometry()

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fitDocument()
