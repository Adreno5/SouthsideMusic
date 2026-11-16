from __future__ import annotations

import base64
import logging
import math
import re
from functools import lru_cache
from html import escape
from typing import Any, Sequence, override

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QResizeEvent, QTextCursor, QTextOption, Qt
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget
from markdown_it import MarkdownIt
from markdown_it.rules_inline.state_inline import StateInline
from markdown_it.token import Token
from qfluentwidgets import TextBrowser
from ziafont import config as ziafont_config
from ziamath import Latex


_MATH_PATTERN = re.compile(
    r'(?s)(\$\$.*?\$\$|(?<!\\)\$[^$\n]+?(?<!\\)\$|\\\[.*?\\\]|\\\(.*?\\\))'
)
_MARKDOWN = MarkdownIt('commonmark', {'html': False, 'breaks': True}).enable([
    'table',
    'strikethrough',
])
_DEFAULT_FENCE_RENDERER = _MARKDOWN.renderer.rules['fence']
_LATEX_FENCE_LANGUAGES = frozenset({'latex', 'math', 'tex'})
logging.getLogger('markdown_it').setLevel(logging.WARNING)
ziafont_config.svg2 = False


def _formulaParts(value: str) -> tuple[str, bool]:
    if value.startswith('$$') and value.endswith('$$'):
        return value[2:-2].strip(), True
    if value.startswith('\\[') and value.endswith('\\]'):
        return value[2:-2].strip(), True
    if value.startswith('$') and value.endswith('$'):
        return value[1:-1].strip(), False
    if value.startswith('\\(') and value.endswith('\\)'):
        return value[2:-2].strip(), False
    return value.strip(), False


def _parseMath(state: StateInline, silent: bool) -> bool:
    match = _MATH_PATTERN.match(state.src, state.pos, state.posMax)
    if match is None or not _formulaParts(match.group(0))[0]:
        return False
    if not silent:
        state.push('math', '', 0).content = match.group(0)
    state.pos = match.end()
    return True


@lru_cache(maxsize=128)
def _renderLatex(formula: str, is_block: bool, color: str) -> str:
    formula_class = 'math-block' if is_block else 'math-inline'
    try:
        svg = Latex(
            formula,
            size=15,
            color=color,
            inline=not is_block,
        ).svg()
        encoded_svg = base64.b64encode(svg.encode('utf-8')).decode('ascii')
        return (
            f'<img class="{formula_class}" '
            f'src="data:image/svg+xml;base64,{encoded_svg}" '
            f'alt="{escape(formula, quote=True)}">'
        )
    except Exception:
        logging.getLogger(__name__).warning(
            'Could not render LaTeX formula: %s', formula, exc_info=True
        )
        return f'<span class="{formula_class}">{escape(formula)}</span>'


def renderMarkdown(markdown: str, math_color: str = '#ffffff') -> str:
    return _MARKDOWN.render(markdown, {'math_color': math_color})


def _renderMath(
    tokens: Sequence[Token],
    idx: int,
    options: dict[str, Any],
    env: dict[str, Any],
) -> str:
    formula, is_block = _formulaParts(tokens[idx].content)
    return _renderLatex(formula, is_block, str(env.get('math_color', '#ffffff')))


def _renderImage(
    tokens: Sequence[Token],
    idx: int,
    options: dict[str, Any],
    env: dict[str, Any],
) -> str:
    for child in tokens[idx].children or []:
        if child.type == 'math':
            child.type = 'text'
    return _MARKDOWN.renderer.image(tokens, idx, options, env)


def _renderFence(
    tokens: Sequence[Token],
    idx: int,
    options: dict[str, Any],
    env: dict[str, Any],
) -> str:
    token = tokens[idx]
    info = (token.info or '').strip()
    language = info.split(maxsplit=1)[0].lower() if info else ''
    if language not in _LATEX_FENCE_LANGUAGES:
        return _DEFAULT_FENCE_RENDERER(tokens, idx, options, env)
    math_color = str(env.get('math_color', '#ffffff'))
    return (
        f'<div class="latex-output">{renderMarkdown(token.content, math_color)}</div>'
    )


_MARKDOWN.inline.ruler.before('escape', 'math', _parseMath)
_MARKDOWN.renderer.rules['math'] = _renderMath
_MARKDOWN.renderer.rules['image'] = _renderImage
_MARKDOWN.renderer.rules['fence'] = _renderFence


class ChattingViewer(QWidget):
    finished = Signal()
    charReceived = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._markdown = ''
        self._append_buffer = ''
        self._stream_finished = False

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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.browser)

    def reset(self) -> None:
        self._append_timer.stop()
        self._markdown = ''
        self._append_buffer = ''
        self._stream_finished = False
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
            self._append_timer.start(80)

    def finishStream(self) -> None:
        self._stream_finished = True
        self._append_timer.stop()
        self._consumeAppendBuffer(len(self._append_buffer))
        self._renderMarkdown()
        self._stream_finished = False
        self.finished.emit()

    def _drainAppendBuffer(self) -> None:
        if self._append_buffer:
            self._consumeAppendBuffer(len(self._append_buffer))
            self._renderMarkdown()

        if self._stream_finished:
            self._stream_finished = False
            self.finished.emit()

    def _consumeAppendBuffer(self, length: int) -> None:
        if length <= 0:
            return
        chunk = self._append_buffer[:length]
        self._append_buffer = self._append_buffer[length:]
        self._markdown += chunk
        self.charReceived.emit(len(chunk))

    def _renderMarkdown(self) -> None:
        math_color = self.browser.palette().text().color().name()
        self.browser.setHtml(
            '<style>'
            'body{color:inherit;}'
            'pre{white-space:pre-wrap; word-wrap:break-word; background:rgba(128,128,128,.16); padding:8px; border-radius:4px;}'
            'code{font-family:monospace;}'
            'table{border-collapse:collapse;} th,td{border:1px solid #666; padding:4px 8px;}'
            '.math-inline{vertical-align:middle; margin:0 2px;}'
            '.math-block{display:block; max-width:100%; margin:8px auto;}'
            '.math-inline,.math-block{font-family:serif; color:inherit;}'
            '.latex-output{margin:4px 0;}'
            '</style>' + renderMarkdown(self._markdown, math_color)
        )
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
