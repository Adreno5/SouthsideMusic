from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import QWidget
from qfluentwidgets import (
    MessageBoxBase,
    CardWidget,
    IndeterminateProgressRing,
    SubtitleLabel,
    TitleLabel,
    CaptionLabel,
)

from core.app_context import AppContext
from core.backend import getBackend
from core.downloader import asyncTask
from core.i18n import tr
from core.models import QualityLevelInfo
from views.list_widget import SListWidget, SScrollArea
from imports import QVBoxLayout, QHBoxLayout, QSpacerItem, QSizePolicy


class QualityCard(CardWidget):
    def __init__(self, parent, level: QualityLevelInfo):
        super().__init__()
        layout = QHBoxLayout()

        left = QVBoxLayout()
        left.addWidget(TitleLabel(tr(f'quality_display.{level.rate}')))
        left.addWidget(CaptionLabel(f'{str(level.rate)[0:-3]} kHZ'))
        layout.addLayout(left)
        layout.addSpacerItem(
            QSpacerItem(
                0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding
            )
        )

        self.setLayout(layout)


class QualityDialog(MessageBoxBase):
    def __init__(self, parent=None, ctx: AppContext | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.yesButton.hide()

        self.inter = IndeterminateProgressRing()
        self.inter.setFixedSize(64, 64)

        self.viewLayout.addWidget(self.inter)
        self.loadQualities()

        self.scroll_area = SScrollArea()
        self.card_container = QWidget()
        self.card_layout = QVBoxLayout()
        self.card_container.setLayout(self.card_layout)
        self.scroll_area.setWidget(self.card_container)
        self.scroll_area.setWidgetResizable(True)
        self.viewLayout.addWidget(self.scroll_area)
        self.scroll_area.hide()
        self.scroll_area.setFixedSize(self.parentWidget().size() * 0.5)

    def loadQualities(self):
        def _load():
            def _returnNull():
                self.inter.hide()
                self.viewLayout.addWidget(SubtitleLabel(tr('quality_dialog.null_song')))

            cur = self.ctx.playing_manager.current_song
            if cur is None:
                self.ctx.addScheduledTask(_returnNull)
                return
            qualities = getBackend().getSongPrivilege(cur.id)
            self.inter.hide()
            self.scroll_area.show()
            for q in qualities.levels:
                self.ctx.addScheduledTask(
                    lambda q=q: self.card_layout.addWidget(
                        QualityCard(self.card_container, q)
                    )
                )

        asyncTask(_load, (), self)
