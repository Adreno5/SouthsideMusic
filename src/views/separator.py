from typing import override

from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtGui import QPaintEvent, QPainter, QColor
from PySide6.QtCore import Qt
from core import theme


class Separator(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(
            QColor(255, 255, 255, 120) if theme.isDark() else QColor(0, 0, 0, 120)
        )
        line_width = max(0, self.width() - 4)
        if line_width:
            painter.drawRoundedRect(2, 12, line_width, 2, 1, 1)
        painter.end()
