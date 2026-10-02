from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget
from qfluentwidgets import (
    MessageBoxBase,
    CardWidget,
    IndeterminateProgressRing,
    SubtitleLabel,
    TitleLabel,
    CaptionLabel,
    InfoBar,
    FluentLabelBase,
    InfoBarPosition,
)

from core.app_context import AppContext
from core.backend import getBackend
from core.downloader import asyncTask
from core.i18n import tr
from core.models import QualityLevelInfo
from core import theme as themeModule
from services.events import event_bus, REQUEST_BR_CHANGED
from views.list_widget import SListWidget, SScrollArea
from imports import QVBoxLayout, QHBoxLayout, QSpacerItem, QSizePolicy


class QualityCard(CardWidget):
    def __init__(self, parent, level: QualityLevelInfo, cur_br: int, ctx: AppContext):
        super().__init__()
        self.level = level
        self.cur_br = cur_br
        self.ctx = ctx
        layout = QHBoxLayout()

        left = QVBoxLayout()
        self.tl = TitleLabel(tr(f'quality_display.{level.br}'))
        self.cl = CaptionLabel(f'{str(level.br)[0:-3]} kHZ')
        left.addWidget(self.tl)
        left.addWidget(self.cl)
        layout.addLayout(left)
        layout.addSpacerItem(
            QSpacerItem(
                0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding
            )
        )

        self.setLayout(layout)

        self.setClickEnabled(level.charge_type)
        self.clicked.connect(self._clickedOn)
        event_bus.subscribe(REQUEST_BR_CHANGED, self._syncQualityLevel)
        self._syncQualityLevel(self.ctx.playing_manager.getCurrentRequestBr())

    def _syncQualityLevel(self, br):
        c = (
            (QColor(255, 255, 255) if themeModule.isDark() else QColor(0, 0, 0))
            if br != self.level.br
            else (themeModule.getSystemThemeColor())
        )
        self.tl.setTextColor(c, c)
        self.cl.setTextColor(c, c)

    def _clickedOn(self):
        if self.level.br == self.cur_br:
            return
        InfoBar.info(
            tr('quality_dialog.switch_title'),
            tr(
                'quality_dialog.switch_content',
                quality=tr(f'quality_display.{self.level.br}'),
            ),
            duration=5000,
            position=InfoBarPosition.TOP,
            parent=self.ctx.main_window,
        )
        self.ctx.config.target_request_br = self.level.br
        self.ctx.playing_manager.setRequestBr(self.level.br)


class QualityDialog(MessageBoxBase):
    def __init__(self, parent: QWidget, ctx: AppContext):
        super().__init__(parent)
        self.ctx = ctx
        self.yesButton.hide()

        self.inter = IndeterminateProgressRing()
        self.inter.setFixedSize(64, 64)

        self.viewLayout.addWidget(self.inter)
        self.loadQualities()

        self.tl = TitleLabel(tr('quality_dialog.p_title'))
        self.tl.hide()
        self.viewLayout.addWidget(self.tl)

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
            qualities = getBackend().getSongQualityPrivilege(cur.id)
            self.scroll_area.show()
            for q in qualities.levels:
                self.ctx.addScheduledTask(
                    lambda nq=q: self.card_layout.addWidget(
                        QualityCard(
                            self.card_container,
                            nq,
                            self.ctx.playing_manager.getCurrentRequestBr(),
                            self.ctx,
                        )
                    )
                )
            self.inter.hide()
            self.tl.show()

        asyncTask(_load, (), self)
