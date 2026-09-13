from __future__ import annotations

import base64
import math
import logging
import re
from functools import lru_cache
from html import escape

from typing import Any, Sequence, override

from imports import (
    QResizeEvent,
    QSizePolicy,
    QTextCursor,
    QTextOption,
    QTimer,
    Qt,
    QVBoxLayout,
    Signal,
    QWidget,
)
from qfluentwidgets import TextBrowser
from markdown_it import MarkdownIt
from markdown_it.token import Token
from ziafont import config as ziafont_config
from ziamath import Latex


_FENCE_PATTERN = re.compile(r'^[ \t]{0,3}(`{3,}|~{3,})')
_INLINE_CODE_PATTERN = re.compile(r'(?<!\\)(`+)(?:(?!\1)[^\n])*?\1')
_MATH_PATTERN = re.compile(
    r'(?s)(\$\$.*?\$\$|(?<!\\)\$[^$\n]+?(?<!\\)\$|\\\[.*?\\\]|\\\(.*?\\\))'
)
_MARKDOWN = MarkdownIt('commonmark', {'html': False, 'breaks': True}).enable(
    ['table', 'strikethrough']
)
_DEFAULT_FENCE_RENDERER = _MARKDOWN.renderer.rules['fence']
_LATEX_FENCE_LANGUAGES = frozenset({'latex', 'math', 'tex'})
logging.getLogger('markdown_it').setLevel(logging.WARNING)
ziafont_config.svg2 = False


def _codeRanges(markdown: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    lines = markdown.splitlines(keepends=True)
    offset = 0
    fence_start: int | None = None
    fence_char = ''
    fence_length = 0

    for line in lines:
        line_text = line.rstrip('\r\n')
        if fence_start is not None:
            closing_pattern = re.compile(
                rf'^[ \t]{{0,3}}{re.escape(fence_char)}{{{fence_length},}}[ \t]*$'
            )
            if closing_pattern.match(line_text):
                ranges.append((fence_start, offset + len(line)))
                fence_start = None
            offset += len(line)
            continue

        fence_match = _FENCE_PATTERN.match(line_text)
        if fence_match:
            marker = fence_match.group(1)
            fence_start = offset
            fence_char = marker[0]
            fence_length = len(marker)
        else:
            ranges.extend(
                (offset + match.start(), offset + match.end())
                for match in _INLINE_CODE_PATTERN.finditer(line_text)
            )
        offset += len(line)

    if fence_start is not None:
        ranges.append((fence_start, len(markdown)))
    return ranges


def _overlapsCode(start: int, end: int, code_ranges: list[tuple[int, int]]) -> bool:
    return any(
        start < range_end and end > range_start
        for range_start, range_end in code_ranges
    )


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


def _extractMath(markdown: str) -> tuple[str, list[tuple[str, str, bool]]]:
    code_ranges = _codeRanges(markdown)
    replacements: list[tuple[int, int, str, bool]] = []

    for match in _MATH_PATTERN.finditer(markdown):
        if _overlapsCode(match.start(), match.end(), code_ranges):
            continue
        formula, is_block = _formulaParts(match.group(0))
        if formula:
            replacements.append((match.start(), match.end(), formula, is_block))

    replacements.sort(key=lambda item: item[0])
    parts: list[str] = []
    formulas: list[tuple[str, str, bool]] = []
    offset = 0
    for start, end, formula, is_block in replacements:
        if start < offset:
            continue
        token = f'SOUTHSIDEMATHTOKEN{len(formulas)}'
        parts.append(markdown[offset:start])
        parts.append(token)
        formulas.append((token, formula, is_block))
        offset = end
    parts.append(markdown[offset:])
    return ''.join(parts), formulas


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
    """Render chat Markdown with tables, fenced code, links, and LaTeX."""
    protected_markdown, formulas = _extractMath(markdown)
    rendered = _MARKDOWN.render(protected_markdown)
    for token, formula, is_block in formulas:
        rendered = rendered.replace(
            token,
            _renderLatex(formula, is_block, math_color),
        )
    return rendered


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
        """Return the original Markdown backing this viewer."""
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
        self.browser.setFixedWidth(content_width)
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
