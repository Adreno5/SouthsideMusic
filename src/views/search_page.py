from __future__ import annotations

import logging

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
    ListWidget,
    PrimaryPushButton,
    SubtitleLabel,
)

import pyncm
from pyncm import apis
from utils.loading_util import doWithMultiThreading
from utils.base.base_util import SongInfo

from views.song_card import SongCard
from MusicLibrary.kuGouMusicApi import KuGouMusicApi

class SearchPage(QWidget):
    resultGot = Signal(list)

    def __init__(self, mwindow, launchwindow=None) -> None:
        super().__init__()
        self._logger = logging.getLogger(__name__)
        lw = launchwindow
        if lw:
            lw.top('Initializing search page...')
        self._mwindow = mwindow
        self.setObjectName('search_page')
        self.img_card_map: dict[str, SongCard] = {}

        if lw:
            lw.top('  creating search input')
        global_layout = QVBoxLayout()
        top_layout = QHBoxLayout()
        self.inputer = LineEdit()
        self.search_btn = PrimaryPushButton(FluentIcon.SEARCH, 'Search')
        self.search_btn.clicked.connect(self.search)
        self.inputer.returnPressed.connect(self.search)
        top_layout.addWidget(self.inputer)
        top_layout.addWidget(self.search_btn)
        global_layout.addLayout(top_layout)

        if lw:
            lw.top('  creating results list')
        self.lst = ListWidget()
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

    def checkRect(self) -> None:
        for i, card in enumerate(self.cards):
            item = self.lst.item(i)
            if item is None:
                continue

            item_rect = self.lst.visualItemRect(item)
            viewport_rect = self.lst.viewport().rect()

            if viewport_rect.intersects(item_rect) and not card.load:
                self._logger.debug(f'loading {card.info['name']}')
                card.loadDetailAndImage()

    def setImage_(self, byte: bytes, ca: SongCard):
        ca.img_label.setPixmap(QPixmap(byte))

    def search(self) -> None:
        if not self.inputer.text().strip():
            InfoBar.warning(
                'Search failed', 'the keyword is empty!', parent=self._mwindow
            )
            return

        if self.search_btn.isEnabled() is False:
            return

        self.search_btn.setEnabled(False)
        self.lst.clear()
        self.cards.clear()
        self.img_card_map.clear()

        result: list[SongInfo] = []

        def _do():
            nonlocal result
            with pyncm.GetCurrentSession():
                resp = apis.cloudsearch.GetSearchResult(self.inputer.text()) # type: ignore
            assert isinstance(resp, dict), 'Invalid response'

            result = [SongInfo(
                name=songdict['name'],
                artists='、'.join(art['name'] for art in songdict['ar']),
                id=songdict['id'],
                privilege=songdict['fee'],
            ) for songdict in resp['result']['songs']] # type: ignore
            print(result)

        def _finish():
            nonlocal result

            self.search_btn.setEnabled(True)

            self.resultGot.emit(result)

        doWithMultiThreading(_do, (), self._mwindow, _finish)

    def addSongs(self, result: list[SongInfo]) -> None:
        for i, song in enumerate(result):
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 150))
            self.lst.addItem(item)
            content_widget = SongCard(
                song, lambda c: self._mwindow.play(c), self._mwindow
            )
            self.lst.setItemWidget(item, content_widget)
            self.cards.append(content_widget)
            content_widget.load = False
