from __future__ import annotations

import logging

from core.app_context import AppContext
from imports import LANGUAGE_CHANGED, ComboBox, QSize, QTimer, Signal, tr
from imports import QPixmap
from imports import (
    QAbstractItemView,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
    event_bus,
)
from views.folder_card import SearchCloudFolderCard
from views.list_widget import SListWidget

from core.downloader import asyncTask
from core.models import SearchCloudFolderInfo, SearchSongInfo
from core.backend import getBackend

from views.song_card import SearchSongCard


class SearchPage(QWidget):
    fetchedSongs = Signal(list)
    fetchedPlaylists = Signal(list)

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._logger = logging.getLogger(__name__)
        self.ctx = ctx
        lw = ctx.launch_window
        if lw:
            lw.top('Initializing search page...')
        self.setObjectName('search_page')
        self.img_card_map: dict[str, SearchSongCard] = {}

        self.searching = False
        self.curr_offset = 0
        self.last_search = ''

        global_layout = QVBoxLayout()

        if self.ctx.cfg.search_type not in ('Songs', 'Playlists'):
            self.ctx.cfg.search_type = 'Songs'
        self.search_type = ComboBox()
        self._refreshSearchTypeBox()
        self.search_type.currentIndexChanged.connect(self.searchTypeChanged)
        global_layout.addWidget(self.search_type)

        if lw:
            lw.top('  creating search input')

        if lw:
            lw.top('  creating results list')
        self.lst = SListWidget()
        self.lst.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.lst.verticalScrollBar().setSingleStep(14)
        self.lst.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.lst.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        global_layout.addWidget(self.lst)
        self.setLayout(global_layout)
        self.fetchedSongs.connect(self.addSongs)
        self.fetchedPlaylists.connect(self.addPlaylists)

        if lw:
            lw.top('  starting scroll monitor')
        self.check_timer = QTimer(self)
        self.check_timer.timeout.connect(self.checkRect)
        self.check_timer.start(50)

        self.cards: list[SearchSongCard | SearchCloudFolderCard] = []
        event_bus.subscribe(LANGUAGE_CHANGED, self._refreshSearchTypeBox)

    @property
    def _mwindow(self):
        return self.ctx.main_window

    def checkRect(self) -> None:
        if not self.cards:
            return

        for i, card in enumerate(self.cards):
            item = self.lst.item(i)
            if item is None:
                continue

            item_rect = self.lst.visualItemRect(item)
            viewport_rect = self.lst.viewport().rect()

            if viewport_rect.intersects(item_rect) and not card.load:
                card.loadDetailAndImage()

        bar = self.lst.verticalScrollBar()
        if (
            self.ctx.main_window
            and bar.value() >= bar.maximum() - 5
            and not self.searching
            and self.ctx.main_window.contents_widget.currentWidget() == self
        ):
            self._logger.info('load more')
            self.search(self.ctx.main_window.search_input.text(), self.curr_offset)

    def setImage_(self, byte: bytes, ca: SearchSongCard):
        ca.img_label.setPixmap(QPixmap(byte))

    def _refreshSearchTypeBox(self) -> None:
        current = self.ctx.cfg.search_type
        data = self.search_type.currentData()
        if data in ('Songs', 'Playlists'):
            current = data
        self.search_type.blockSignals(True)
        self.search_type.clear()
        label_keys = {
            'Songs': 'search_page.search_type.songs',
            'Playlists': 'search_page.search_type.playlists',
        }
        for search_type in ('Songs', 'Playlists'):
            self.search_type.addItem(tr(label_keys[search_type]), userData=search_type)
        index = self.search_type.findData(current)
        self.search_type.setCurrentIndex(max(index, 0))
        self.search_type.blockSignals(False)

    def searchTypeChanged(self, *_args: object) -> None:
        search_type = self.search_type.currentData()
        if search_type not in ('Songs', 'Playlists'):
            return
        self.ctx.cfg.search_type = search_type
        if self.last_search:
            self.search(self.last_search)

    def search(self, keywords: str, offset: int = 0) -> None:
        self.last_search = keywords
        self.searching = True
        search_type = self.ctx.cfg.search_type

        bar = self.lst.verticalScrollBar()
        bar.setValue(bar.minimum())

        if offset == 0:
            self.curr_offset = 0
            self.lst.clear()
            self.cards.clear()
            self.img_card_map.clear()

        result: list[SearchSongInfo] | list[SearchCloudFolderInfo] = []

        def _do():
            nonlocal result
            if search_type == 'Songs':
                result = getBackend().searchSong(keywords, offset=offset)
            else:
                result = getBackend().searchPlaylist(keywords, offset=offset)

            def _apply() -> None:
                if search_type == 'Songs':
                    self.addSongs(result) # type: ignore
                else:
                    self.addPlaylists(result) # type: ignore

                self.curr_offset += len(result)
                self.searching = False

            self.ctx.addScheduledTask(_apply)

        asyncTask(_do, (), self._mwindow)

    def addPlaylists(self, result: list[SearchCloudFolderInfo]) -> None:
        for i, playlist in enumerate(result):
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 150))
            self.lst.addItem(item)
            content_widget = SearchCloudFolderCard(playlist, self.lst.width(), self.ctx)
            self.lst.setItemWidget(item, content_widget)
            self.cards.append(content_widget)

    def addSongs(self, result: list[SearchSongInfo]) -> None:
        for i, song in enumerate(result):
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 150))
            self.lst.addItem(item)
            content_widget = SearchSongCard(
                song,
                lambda c: self._mwindow.play(c),
                self._mwindow,  # type: ignore
            )
            self.lst.setItemWidget(item, content_widget)
            self.cards.append(content_widget)
            content_widget.load = False
