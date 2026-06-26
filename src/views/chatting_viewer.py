from __future__ import annotations

import html
import re

from typing import Callable, Literal, override

from imports import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QResizeEvent,
    QSizePolicy,
    QTableWidgetItem,
    Qt,
    QVBoxLayout,
    QWidget,
    QLayoutItem,
    QTimer,
    QLayout,
)
from qfluentwidgets import TableWidget, TextEdit

from core import theme
from views.animated_layout import SVAnimatedLayout

Mode = Literal[
    'text',
    'inline_code',
    'code_block',
    'latex_inline',
    'latex_block',
    'table',
]


class ChattingViewer(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cur_widget: QLabel | TextEdit | TableWidget | None = None
        self.cur_text = ''

        self._mode: Mode = 'text'
        self._buffer = ''
        self._current_layout: QLayout | None = None
        self._block_widgets: list[QWidget] = []
        self._latex_end = ''
        self._table_lines: list[str] = []
        self._at_line_start = True

        self._append_buffer = ''
        self._append_buffer_timer = QTimer(self)
        self._append_buffer_timer.timeout.connect(self._flush_append_buffer)

        self._layout = QVBoxLayout()
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self.setLayout(self._layout)

    def reset(self) -> None:
        self._clear_layout(self._layout)
        self.cur_widget = None
        self.cur_text = ''
        self._mode = 'text'
        self._buffer = ''
        self._current_layout = None
        self._block_widgets.clear()
        self._latex_end = ''
        self._table_lines.clear()
        self._at_line_start = True

    def _flush_append_buffer(self):
        for char in self._append_buffer:
            self._buffer += char
            self._drain_buffer(False)
        self._append_buffer = ''

    def appendChunk(self, chunk_content: str) -> None:
        if not chunk_content:
            return
        self._append_buffer += chunk_content
        if not self._append_buffer_timer.isActive():
            self._append_buffer_timer.start(210)

    def finishStream(self) -> None:
        self._flush_append_buffer()
        self._drain_buffer(True)
        if self._append_buffer_timer.isActive():
            self._append_buffer_timer.stop()
        if self._mode == 'table':
            self._finish_table()
        elif self._mode in ('inline_code', 'code_block', 'latex_inline', 'latex_block'):
            self._mode = 'text'
            self.cur_widget = None
            self.cur_text = ''

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._sync_block_widths()

    def _drain_buffer(self, final: bool) -> None:
        while self._buffer:
            changed = False
            if self._mode == 'text':
                changed = self._drain_text(final)
            elif self._mode == 'inline_code':
                changed = self._drain_until('`', final, self._append_inline_code)
            elif self._mode == 'code_block':
                changed = self._drain_until('```', final, self._append_code_block)
            elif self._mode in ('latex_inline', 'latex_block'):
                changed = self._drain_until(
                    self._latex_end,
                    final,
                    self._append_latex,
                )
            elif self._mode == 'table':
                changed = self._drain_table(final)

            if not changed:
                break

    def _drain_text(self, final: bool) -> bool:
        if not final and self._should_wait_for_text_token():
            return False

        index, token = self._find_next_text_token()
        if index is None:
            safe_length = len(self._buffer) if final else self._safe_text_length()
            if safe_length <= 0:
                return False
            self._append_normal_text(self._buffer[:safe_length])
            self._buffer = self._buffer[safe_length:]
            return True

        if index > 0:
            self._append_normal_text(self._buffer[:index])
            self._buffer = self._buffer[index:]
            return True

        if token == 'table':
            self._mode = 'table'
            self.cur_widget = None
            self.cur_text = ''
            return True
        if token == 'code_block':
            self._buffer = self._buffer[3:]
            self._start_code_block()
            return True
        if token == 'latex_block_dollar':
            self._buffer = self._buffer[2:]
            self._start_latex('latex_block', '$$')
            return True
        if token == 'latex_block_bracket':
            self._buffer = self._buffer[2:]
            self._start_latex('latex_block', '\\]')
            return True
        if token == 'latex_inline_bracket':
            self._buffer = self._buffer[2:]
            self._start_latex('latex_inline', '\\)')
            return True
        if token == 'inline_code':
            self._buffer = self._buffer[1:]
            self._start_inline_code()
            return True
        if token == 'latex_inline_dollar':
            self._buffer = self._buffer[1:]
            self._start_latex('latex_inline', '$')
            return True
        return False

    def _drain_until(
        self,
        marker: str,
        final: bool,
        append: Callable[[str], None],
    ) -> bool:
        index = self._buffer.find(marker)
        if index >= 0:
            append(self._buffer[:index])
            self._buffer = self._buffer[index + len(marker) :]
            self._mode = 'text'
            self.cur_widget = None
            self.cur_text = ''
            return True

        safe_length = len(self._buffer) if final else self._safe_marker_length(marker)
        if safe_length <= 0:
            return False
        append(self._buffer[:safe_length])
        self._buffer = self._buffer[safe_length:]
        return True

    def _drain_table(self, final: bool) -> bool:
        if self._table_lines and self._buffer and not self._buffer.startswith('|'):
            self._finish_table()
            return True

        newline_index = self._buffer.find('\n')
        if newline_index < 0:
            if not final:
                return False
            if self._buffer:
                self._table_lines.append(self._buffer)
                self._buffer = ''
            self._finish_table()
            return True

        line = self._buffer[:newline_index]
        if not line.startswith('|'):
            self._finish_table()
            return True

        self._table_lines.append(line)
        self._buffer = self._buffer[newline_index + 1 :]
        return True

    def _find_next_text_token(self) -> tuple[int | None, str | None]:
        candidates: list[tuple[int, str]] = []
        table_index = self._find_table_index()
        if table_index is not None:
            candidates.append((table_index, 'table'))

        for marker, token in (
            ('```', 'code_block'),
            ('$$', 'latex_block_dollar'),
            ('\\[', 'latex_block_bracket'),
            ('\\(', 'latex_inline_bracket'),
            ('`', 'inline_code'),
            ('$', 'latex_inline_dollar'),
        ):
            index = self._buffer.find(marker)
            if index >= 0:
                candidates.append((index, token))

        if not candidates:
            return None, None
        return min(candidates, key=lambda item: item[0])

    def _find_table_index(self) -> int | None:
        if self._at_line_start and self._buffer.startswith('|'):
            return 0
        index = self._buffer.find('\n|')
        if index >= 0:
            return index + 1
        return None

    def _should_wait_for_text_token(self) -> bool:
        return self._buffer in ('`', '``', '$', '\\')

    def _safe_text_length(self) -> int:
        keep = 0
        for marker in ('``', '`', '$', '\\'):
            if self._buffer.endswith(marker):
                keep = max(keep, len(marker))
        return len(self._buffer) - keep

    def _safe_marker_length(self, marker: str) -> int:
        keep = 0
        max_tail = min(len(marker) - 1, len(self._buffer))
        for length in range(1, max_tail + 1):
            if marker.startswith(self._buffer[-length:]):
                keep = length
        return len(self._buffer) - keep

    def _start_inline_code(self) -> None:
        self._mode = 'inline_code'
        self.cur_text = ''
        self.cur_widget = self._create_label('code')

    def _start_code_block(self) -> None:
        self._mode = 'code_block'
        self.cur_text = '```'
        self.cur_widget = self._create_text_edit()
        self._update_text_edit(self._code_block_markdown())

    def _start_latex(self, mode: Mode, end_marker: str) -> None:
        self._mode = mode
        self._latex_end = end_marker
        self.cur_text = ''
        self.cur_widget = self._create_text_edit()
        self._update_text_edit(' ')

    def _append_normal_text(self, text: str) -> None:
        if not text:
            return
        if not isinstance(self.cur_widget, QLabel) or self.cur_widget.property(
            'viewerRole'
        ) == 'code':
            self.cur_widget = self._create_label('text')
            self.cur_text = ''

        self.cur_text += text
        self.cur_widget.setText(self._inline_markdown_to_html(self.cur_text))
        self._update_line_state(text)

    def _append_inline_code(self, text: str) -> None:
        if not isinstance(self.cur_widget, QLabel):
            self.cur_widget = self._create_label('code')
            self.cur_text = ''
        self.cur_text += text
        self.cur_widget.setText(self.cur_text or ' ')
        self._update_line_state(text)

    def _append_code_block(self, text: str) -> None:
        if not isinstance(self.cur_widget, TextEdit):
            self.cur_widget = self._create_text_edit()
            self.cur_text = '```'
        self.cur_text += text
        self._update_text_edit(self._code_block_markdown())

    def _append_latex(self, text: str) -> None:
        if not isinstance(self.cur_widget, TextEdit):
            self.cur_widget = self._create_text_edit()
            self.cur_text = ''
        self.cur_text += text
        self._update_text_edit(self.cur_text or ' ')
        self._update_line_state(text)

    def _finish_table(self) -> None:
        lines = [line for line in self._table_lines if line.strip()]
        self._table_lines.clear()
        self._mode = 'text'
        self.cur_widget = None
        self.cur_text = ''

        if not lines:
            return
        if not self._is_markdown_table(lines):
            self._append_normal_text('\n'.join(lines) + '\n')
            return

        headers = self._split_table_row(lines[0])
        rows = [self._split_table_row(line) for line in lines[2:]]
        column_count = max(len(headers), *(len(row) for row in rows), 1)
        headers.extend([''] * (column_count - len(headers)))

        table = TableWidget()
        table.setColumnCount(column_count)
        table.setRowCount(len(rows))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setWordWrap(True)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        for row_index, row in enumerate(rows):
            row.extend([''] * (column_count - len(row)))
            for column_index, value in enumerate(row):
                item = QTableWidgetItem()
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row_index, column_index, item)
                table.setCellWidget(
                    row_index,
                    column_index,
                    self._create_table_cell_label(value),
                )

        self._fit_table(table)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._add_block_widget(table)
        self.cur_widget = table
        self._at_line_start = True

    def _create_label(self, role: Literal['text', 'code']) -> QLabel:
        flow = self._ensure_layout()
        label = QLabel()
        label.setProperty('viewerRole', role)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        label.setMaximumWidth(self._content_width())
        if role == 'code':
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setStyleSheet(self._code_label_style())
        else:
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setOpenExternalLinks(True)
            label.setStyleSheet('font-size: 15px; line-height: 1.4;')
        flow.addWidget(label)
        return label

    def _create_text_edit(self) -> TextEdit:
        editor = TextEdit()
        editor.setReadOnly(True)
        editor.setLineWrapMode(TextEdit.LineWrapMode.WidgetWidth)
        editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        editor.setStyleSheet(self._text_edit_style())
        self._add_block_widget(editor)
        return editor

    def _add_block_widget(self, widget: QWidget) -> None:
        self._current_layout = None
        flow = self._new_layout()
        flow.addWidget(widget)
        self._block_widgets.append(widget)
        self._sync_block_width(widget)
        self._current_layout = None

    def _ensure_layout(self) -> QLayout:
        if self._current_layout is None:
            self._current_layout = self._new_layout()
        return self._current_layout

    def _new_layout(self) -> QLayout:
        flow = SVAnimatedLayout()
        flow.setContentsMargins(0, 0, 0, 0)
        self._layout.addLayout(flow)
        return flow

    def _update_text_edit(self, markdown: str) -> None:
        if not isinstance(self.cur_widget, TextEdit):
            return
        self.cur_widget.setMarkdown(markdown)
        self._fit_text_edit(self.cur_widget)

    def _code_block_markdown(self) -> str:
        if self.cur_text.endswith('```') and len(self.cur_text) > 3:
            return self.cur_text
        return f'{self.cur_text}\n```'

    def _fit_text_edit(self, editor: TextEdit) -> None:
        self._sync_block_width(editor)
        document = editor.document()
        document.setTextWidth(max(80, editor.viewport().width()))
        height = int(document.size().height()) + 22
        editor.setFixedHeight(min(max(48, height), 520))

    def _sync_block_widths(self) -> None:
        for widget in self._block_widgets:
            self._sync_block_width(widget)
            if isinstance(widget, TextEdit):
                self._fit_text_edit(widget)
            elif isinstance(widget, TableWidget):
                self._fit_table(widget)

        for label in self.findChildren(QLabel):
            label.setMaximumWidth(self._content_width())

    def _sync_block_width(self, widget: QWidget) -> None:
        widget.setFixedWidth(self._content_width())

    def _fit_table(self, table: TableWidget) -> None:
        self._sync_block_width(table)
        table.resizeRowsToContents()
        height = table.horizontalHeader().height() + table.frameWidth() * 2 + 6
        for row in range(table.rowCount()):
            height += table.rowHeight(row)
        table.setFixedHeight(max(82, height))

    def _content_width(self) -> int:
        return max(160, self.width() - 8)

    def _update_line_state(self, text: str) -> None:
        if not text:
            return
        newline_index = text.rfind('\n')
        if newline_index >= 0:
            self._at_line_start = newline_index == len(text) - 1
        else:
            self._at_line_start = False

    def _inline_markdown_to_html(self, text: str) -> str:
        lines = self._drop_horizontal_rule_lines(text.splitlines())
        rendered = [self._render_inline_line(line) for line in lines]
        return '<br>'.join(rendered)

    def _drop_horizontal_rule_lines(self, lines: list[str]) -> list[str]:
        result: list[str] = []
        skipping_rule_gap = False
        for line in lines:
            if self._is_horizontal_rule_line(line):
                while result and not result[-1].strip():
                    result.pop()
                skipping_rule_gap = True
                continue
            if skipping_rule_gap and not line.strip():
                continue
            skipping_rule_gap = False
            result.append(html.escape(line))
        return result or ['']

    def _is_horizontal_rule_line(self, line: str) -> bool:
        value = line.strip()
        if len(value) < 3:
            return False
        return value == value[0] * len(value) and value[0] in '-*_'

    def _render_inline_line(self, line: str) -> str:
        heading = re.match(r'^(#{1,6})\s+(.+)$', line)
        if heading:
            level = len(heading.group(1))
            size = max(16, 24 - level * 2)
            return (
                f'<span style="font-size:{size}px; font-weight:600">'
                f'{self._render_inline_tokens(heading.group(2))}</span>'
            )

        if line.startswith(('- ', '* ')):
            return f'&bull; {self._render_inline_tokens(line[2:])}'

        numbered = re.match(r'^(\d+)\.\s+(.+)$', line)
        if numbered:
            return (
                f'{numbered.group(1)}. '
                f'{self._render_inline_tokens(numbered.group(2))}'
            )

        return self._render_inline_tokens(line)

    def _render_inline_tokens(self, text: str) -> str:
        text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
        text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
        text = re.sub(r'__([^_]+)__', r'<b>\1</b>', text)
        text = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<i>\1</i>', text)
        text = re.sub(r'(?<!_)_([^_]+)_(?!_)', r'<i>\1</i>', text)
        return text

    def _create_table_cell_label(self, markdown: str) -> QLabel:
        label = QLabel(self._render_inline_tokens(html.escape(markdown)))
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setOpenExternalLinks(True)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setContentsMargins(8, 6, 8, 6)
        label.setStyleSheet('background: transparent; font-size: 15px;')
        return label

    def _is_markdown_table(self, lines: list[str]) -> bool:
        if len(lines) < 2:
            return False
        separator = self._split_table_row(lines[1])
        if not separator:
            return False
        return all(self._is_table_separator_cell(cell) for cell in separator)

    def _is_table_separator_cell(self, cell: str) -> bool:
        value = cell.strip()
        if len(value.replace(':', '')) < 3:
            return False
        return all(char in '-:' for char in value)

    def _split_table_row(self, line: str) -> list[str]:
        value = line.strip()
        if value.startswith('|'):
            value = value[1:]
        if value.endswith('|'):
            value = value[:-1]
        return [cell.strip() for cell in value.split('|')]

    def _code_label_style(self) -> str:
        if theme.isDark():
            return (
                'background: #1f1f1f; border: 1px solid #3a3a3a; '
                'border-radius: 5px; padding: 2px 6px; font-family: Consolas;'
            )
        return (
            'background: #f1f1f1; border: 1px solid #d9d9d9; '
            'border-radius: 5px; padding: 2px 6px; font-family: Consolas;'
        )

    def _text_edit_style(self) -> str:
        if theme.isDark():
            return (
                'TextEdit { background: #101010; border: 1px solid #303030; '
                'border-radius: 6px; padding: 6px; }'
            )
        return (
            'TextEdit { background: #ffffff; border: 1px solid #dddddd; '
            'border-radius: 6px; padding: 6px; }'
        )

    def _clear_layout(self, layout: QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if not isinstance(item, QLayoutItem):
                continue
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                self._clear_layout(child_layout)  # type: ignore[arg-type]


ChattingViewer = ChattingViewer
