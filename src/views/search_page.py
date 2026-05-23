from __future__ import annotations

import logging

from core.app_context import AppContext
from imports import QSize, Qt, QTimer, Signal
from imports import QPixmap
from imports import (
    QAbstractItemView,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    FluentIcon,
    InfoBar,
    LineEdit,
    PrimaryPushButton,
    SubtitleLabel,
)
from views.list_widget import SListWidget

from core.downloader import doWithMultiThreading
from core.models import SearchSongInfo
from core.backend import get_backend

from views.main_window import MainWindow
from views.song_card import SongCard


class SearchPage(QWidget):
    resultGot = Signal(list)

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._logger = logging.getLogger(__name__)
        self.ctx = ctx
        lw = ctx.launch_window
        if lw:
            lw.top('Initializing search page...')
        self.setObjectName('search_page')
        self.img_card_map: dict[str, SongCard] = {}

        self.searching = False
        self.curr_offset = 0

        if lw:
            lw.top('  creating search input')
        global_layout = QVBoxLayout()

        if lw:
            lw.top('  creating results list')
        self.lst = SListWidget()
        self.lst.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.lst.verticalScrollBar().setSingleStep(14)
        self.lst.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.lst.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        global_layout.addWidget(self.lst)
        self.setLayout(global_layout)
        self.resultGot.connect(self.addSongs)

        if lw:
            lw.top('  starting scroll monitor')
        self.check_timer = QTimer(self)
        self.check_timer.timeout.connect(self.checkRect)
        self.check_timer.start(50)

        self.cards: list[SongCard] = []

    @property
    def _mwindow(self):
        return self.ctx.main_window

    def checkRect(self) -> None:
        for i, card in enumerate(self.cards):
            item = self.lst.item(i)
            if item is None:
                continue

            item_rect = self.lst.visualItemRect(item)
            viewport_rect = self.lst.viewport().rect()

            if viewport_rect.intersects(item_rect) and not card.load:
                self._logger.debug(f'loading {card.info["name"]}')
                card.loadDetailAndImage()

        bar = self.lst.verticalScrollBar()
        if (
            self.ctx.main_window
            and bar.value() >= bar.maximum() - 5
            and not self.searching
            and self.ctx.main_window.contents_widget.currentWidget() == self
        ):
            self._logger.info(f'load more')
            self.search(self.ctx.main_window.search_input.text(), self.curr_offset)

    def setImage_(self, byte: bytes, ca: SongCard):
        ca.img_label.setPixmap(QPixmap(byte))

    def search(self, keywords: str, offset: int = 0) -> None:
        self.searching = True

        if offset == 0:
            self.lst.clear()
            self.cards.clear()
            self.img_card_map.clear()

        result: list[SearchSongInfo] = []

        def _do():
            nonlocal result
            result = get_backend().search(keywords, offset=offset)

        def _finish():
            nonlocal result

            self.resultGot.emit(result)

        doWithMultiThreading(_do, (), self._mwindow, _finish)

    def addSongs(self, result: list[SearchSongInfo]) -> None:
        for i, song in enumerate(result):
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 150))
            self.lst.addItem(item)
            content_widget = SongCard(
                song,
                lambda c: self._mwindow.play(c),
                self._mwindow,  # type: ignore
            )
            self.lst.setItemWidget(item, content_widget)
            self.cards.append(content_widget)
            content_widget.load = False

        self.curr_offset += len(result)

        self.searching = False
