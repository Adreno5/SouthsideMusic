from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtGui import QMouseEvent, Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon,
    IconWidget,
    SubtitleLabel,
)

from core.i18n import bindText

if TYPE_CHECKING:
    from core.app_context import AppContext


class RediscoveryCard(CardWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setClickEnabled(True)
        self.setFixedHeight(84)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 10)
        layout.setSpacing(16)
        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)
        title = SubtitleLabel(self)
        bindText(title, 'rediscovery.title')
        text_layout.addWidget(title)
        subtitle = CaptionLabel(self)
        bindText(subtitle, 'rediscovery.entry_description')
        subtitle.setWordWrap(True)
        text_layout.addWidget(subtitle)
        layout.addLayout(text_layout, 1)
        hint = BodyLabel(self)
        bindText(hint, 'rediscovery.entry_button')
        layout.addWidget(hint)
        arrow = IconWidget(FluentIcon.CHEVRON_RIGHT, self)
        arrow.setFixedSize(16, 16)
        layout.addWidget(arrow)

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.ctx.main_window.openRediscovery()
        super().mousePressEvent(event)
