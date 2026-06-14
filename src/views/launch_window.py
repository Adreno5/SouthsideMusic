from __future__ import annotations

from imports import Qt
from imports import QLabel, QVBoxLayout, QWidget, event_bus
from services.events.events import COLLECT_DEBUG_INFO, EMIT_DEBUG_INFO
from qfluentwidgets import TitleLabel
import hPyT

from core import theme
import time

from core.dialogs import SubtitleLabel


class LaunchWindow(QWidget):
    def __init__(self, app):
        super().__init__()
        self._app = app
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setFixedSize(app.primaryScreen().size() * 0.25)
        hPyT.window_frame.center(self)

        self._stack: list[str] = []

        launchlayout = QVBoxLayout()
        launchlayout.addWidget(
            TitleLabel('Southside Music'),
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
        )
        self.subtitlel = SubtitleLabel('')
        launchlayout.addWidget(
            self.subtitlel,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        )
        self.sublabel = QLabel('Launching...')
        launchlayout.addWidget(
            self.sublabel,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        )
        self.setLayout(launchlayout)

        self.setStyleSheet(
            f'QWidget {{ background-color: {"#FFFFFF" if theme.isLight() else "#000000"} }} QLabel {{ color: {"white" if theme.isDark() else "black"}; }}'
        )

        self.show()
        event_bus.subscribe(COLLECT_DEBUG_INFO, self.emitDebugInfo)

    def push(self, text: str):
        self._stack.append(text)
        self.updateLabel()

    def top(self, text: str):
        self.push(text)

    def pop(self):
        if len(self._stack) > 0:
            self._stack.pop()
            self.updateLabel()

    def subtitle(self, text: str):
        self.subtitlel.setText(text)
        self.push(text)
        time.sleep(0.05)

    def clear(self):
        pass

    def emitDebugInfo(self):
        event_bus.emit(
            EMIT_DEBUG_INFO,
            'Launch Window',
            [
                f'stack_size={len(self._stack)}',
                f'last_5={self._stack[-5:] if len(self._stack) > 0 else None}',
            ],
        )

    def updateLabel(self):
        text = (
            '\n'.join(self._stack[-5:])
            if len(self._stack) > 5
            else '\n'.join(self._stack)
        )
        if len(self._stack) > 5:
            text = '...\n' + text
        self.sublabel.setText(text)
        self._app.processEvents()
