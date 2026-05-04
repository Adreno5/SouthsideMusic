from __future__ import annotations

from imports import Qt
from imports import QLabel, QVBoxLayout, QWidget
from qfluentwidgets import TitleLabel
import hPyT

from utils import darkdetect_util as darkdetect
import time

from utils.dialog_util import SubtitleLabel

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
            TitleLabel("Southside Music"),
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
        )
        self.subtitlel = SubtitleLabel('')
        launchlayout.addWidget(
            self.subtitlel,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        )
        self.sublabel = QLabel("Launching...")
        launchlayout.addWidget(
            self.sublabel,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        )
        self.setLayout(launchlayout)

        self.setStyleSheet(
            f"QWidget {{ background-color: {'#FFFFFF' if darkdetect.isLight() else '#000000'} }} QLabel {{ color: {'white' if darkdetect.isDark() else 'black'}; }}"
        )

        self.show()

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

    def updateLabel(self):
        text = (
            "\n".join(self._stack[-5:])
            if len(self._stack) > 5
            else "\n".join(self._stack)
        )
        if len(self._stack) > 5:
            text = "...\n" + text
        self.sublabel.setText(text)
        self._app.processEvents()
        time.sleep(0.02)
