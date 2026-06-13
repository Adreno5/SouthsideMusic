from __future__ import annotations
from typing import TYPE_CHECKING

from imports import QPaintEvent, Qt
from imports import QHBoxLayout, QVBoxLayout
from qfluentwidgets import CaptionLabel, FluentStyleSheet
from qframelesswindow import TitleBar

if TYPE_CHECKING:
    pass


class SouthsideMusicTitleBar(TitleBar):
    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedHeight(48)
        self.hBoxLayout.removeWidget(self.minBtn)
        self.hBoxLayout.removeWidget(self.maxBtn)
        self.hBoxLayout.removeWidget(self.closeBtn)

        self.titleLabel = CaptionLabel(self)
        self.hBoxLayout.insertWidget(
            0,
            self.titleLabel,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        self.titleLabel.setObjectName('titleLabel')
        self.window().windowTitleChanged.connect(self.setTitle)

        self.vBoxLayout = QVBoxLayout()
        self.buttonLayout = QHBoxLayout()
        self.buttonLayout.setSpacing(0)
        self.buttonLayout.setContentsMargins(0, 0, 0, 0)
        self.buttonLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.buttonLayout.addWidget(self.minBtn)
        self.buttonLayout.addWidget(self.maxBtn)
        self.buttonLayout.addWidget(self.closeBtn)
        self.vBoxLayout.addLayout(self.buttonLayout)
        self.vBoxLayout.addStretch(1)
        self.hBoxLayout.addLayout(self.vBoxLayout, 0)

        FluentStyleSheet.FLUENT_WINDOW.apply(self)

    def setTitle(self, title):
        self.titleLabel.setText(title)
        self.titleLabel.adjustSize()

    def canDrag(self, pos):
        search_input = getattr(self.window(), 'search_input', None)
        if search_input and search_input.isVisible():
            window_pos = self.mapTo(self.window(), pos)
            if search_input.geometry().contains(window_pos):
                return False
        return super().canDrag(pos)

    def paintEvent(self, event: QPaintEvent) -> None:
        pass
