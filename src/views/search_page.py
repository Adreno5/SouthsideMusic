from __future__ import annotations

import logging
import time

from core.app_context import AppContext
from PySide6.QtCore import QSize, QTimer, Signal
from qfluentwidgets import ComboBox
from core.i18n import tr
from services.events import Events, event_bus
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)
from views.folder_card import SearchCloudFolderCard
from views.list_widget import SListWidget

from core.downloader import asyncTask
from core.models import SearchCloudFolderInfo, SearchSongInfo
from core.backend import getBackend
from core.qt_utils import clearListWidget

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
            lw.subtitle('Initializing search page...')
        self.setObjectName('search_page')
        self.img_card_map: dict[str, SearchSongCard] = {}

        self.searching = False
        self.can_load_more = True
        self.curr_offset = 0
        self.last_search = ''
        self._search_seq = 0
        self._batch_results: list[SearchSongInfo] | list[SearchCloudFolderInfo] = []
        self._batch_type = 'Songs'
        self._batch_index = 0
        self._batch_offset = 0
        self._batch_seq = 0
        self._batch_timer = QTimer(self)
        self._batch_timer.setSingleShot(True)
        self._batch_timer.timeout.connect(self._appendResultsBatch)

        global_layout = QVBoxLayout()

        if self.ctx.config.search_type not in ('Songs', 'Playlists'):
            self.ctx.config.search_type = 'Songs'
        self.search_type = ComboBox()
        self._refreshSearchTypeBox()
        self.search_type.currentIndexChanged.connect(self.searchTypeChanged)
        global_layout.addWidget(self.search_type)

        if lw:
            lw.subtitle('  creating search input')

        if lw:
            lw.subtitle('  creating results list')
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
            lw.subtitle('  starting scroll monitor')
        event_bus.subscribe(Events.TICK_50MS, self.checkRect)

        self.cards: list[SearchSongCard | SearchCloudFolderCard] = []
        event_bus.subscribe(Events.LANGUAGE_CHANGED, self._refreshSearchTypeBox)

    @property
    def _mwindow(self):
        return self.ctx.main_window

    def checkRect(self) -> None:
        if not self.cards:
            return

        start = 0
        end = self.lst.count()
        viewport_rect = self.lst.viewport().rect()
        while start < end:
            middle = (start + end) // 2
            item = self.lst.item(middle)
            if item is None:
                break
            if self.lst.visualItemRect(item).bottom() < viewport_rect.top():
                start = middle + 1
            else:
                end = middle
        for index in range(start, self.lst.count()):
            item = self.lst.item(index)
            if item is None or index >= len(self.cards):
                continue
            item_rect = self.lst.visualItemRect(item)
            if item_rect.top() > viewport_rect.bottom():
                break
            card = self.cards[index]
            if viewport_rect.intersects(item_rect) and not card.load:
                card.loadDetailAndImage()

        bar = self.lst.verticalScrollBar()
        if (
            self.ctx.main_window
            and bar.value() >= bar.maximum() - 5
            and not self.searching
            and self.ctx.main_window.contents_widget.currentWidget() == self
            and self.can_load_more
        ):
            self._logger.info('load more')
            self.search(self.ctx.main_window.search_input.text(), self.curr_offset)

    def setImage_(self, byte: bytes, ca: SearchSongCard):
        ca.img_label.setPixmap(QPixmap(byte))

    def _refreshSearchTypeBox(self) -> None:
        current = self.ctx.config.search_type
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
        self.ctx.config.search_type = search_type
        if self.last_search:
            self.search(self.last_search)

    def search(self, keywords: str, offset: int = 0) -> None:
        self._search_seq += 1
        search_seq = self._search_seq
        self.last_search = keywords
        self.searching = True
        search_type = self.ctx.config.search_type
        self._batch_timer.stop()

        bar = self.lst.verticalScrollBar()
        bar.setValue(bar.minimum())

        if offset == 0:
            self.curr_offset = 0
            clearListWidget(self.lst)
            self.cards.clear()
            self.img_card_map.clear()

        def _do():
            try:
                if search_type == 'Songs':
                    result = getBackend().searchSong(keywords, offset=offset)
                else:
                    result = getBackend().searchPlaylist(keywords, offset=offset)
            except Exception as error:
                self._logger.exception(error)
                result = []
            self.ctx.addScheduledTask(
                self._applySearchResults,
                search_seq,
                search_type,
                result,
                offset,
            )

        asyncTask(_do, (), self._mwindow)

    def _applySearchResults(
        self,
        search_seq: int,
        search_type: str,
        result: list[SearchSongInfo] | list[SearchCloudFolderInfo],
        offset: int,
    ) -> None:
        if search_seq != self._search_seq:
            return
        self.can_load_more = bool(result)
        self._batch_results = result
        self._batch_type = search_type
        self._batch_index = 0
        self._batch_offset = offset
        self._batch_seq = search_seq
        if result:
            self._appendResultsBatch()
        else:
            self.searching = False

    def _appendResultsBatch(self) -> None:
        if self._batch_seq != self._search_seq:
            return
        deadline = time.perf_counter() + 0.008
        end = len(self._batch_results)
        while self._batch_index < end:
            if self._batch_type == 'Songs':
                self._addSongResult(self._batch_results[self._batch_index])  # type: ignore[arg-type]
            else:
                self._addPlaylistResult(self._batch_results[self._batch_index])  # type: ignore[arg-type]
            self._batch_index += 1
            if time.perf_counter() >= deadline:
                break
        if self._batch_index < end:
            self._batch_timer.start(1)
            return
        self.curr_offset += len(self._batch_results)
        self.searching = False
        self._batch_results = []
        self._checkVisibleCards()

    def _checkVisibleCards(self) -> None:
        self.checkRect()

    def addPlaylists(self, result: list[SearchCloudFolderInfo]) -> None:
        for playlist in result:
            self._addPlaylistResult(playlist)

    def addSongs(self, result: list[SearchSongInfo]) -> None:
        for song in result:
            self._addSongResult(song)

    def _addPlaylistResult(self, playlist: SearchCloudFolderInfo) -> None:
        item = QListWidgetItem()
        item.setSizeHint(QSize(0, 150))
        self.lst.addItem(item)
        content_widget = SearchCloudFolderCard(playlist, self.lst.width(), self.ctx)
        self.lst.setItemWidget(item, content_widget)
        self.cards.append(content_widget)

    def _addSongResult(self, song: SearchSongInfo) -> None:
        item = QListWidgetItem()
        content_widget = SearchSongCard(song, lambda c: self._mwindow.play(c), self.ctx)
        self.lst.addItem(item)
        self.lst.setItemWidget(item, content_widget)
        self.cards.append(content_widget)
        content_widget.load = False
