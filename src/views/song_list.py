from __future__ import annotations

import time
from collections.abc import Callable, ValuesView
from typing import override

from PySide6.QtCore import QModelIndex, QSize, QTimer
from PySide6.QtGui import QResizeEvent, QShowEvent
from PySide6.QtWidgets import QListWidget, QStyleOptionViewItem
from qfluentwidgets import ListItemDelegate

from core.models import SongStorable
from views.list_widget import SListWidget
from views.song_card import _SongCardItem


class _SongListDelegate(ListItemDelegate):
    @override
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(0, 62)


class SongListWidget(SListWidget):
    def __init__(self, create_card: Callable[[SongStorable], _SongCardItem]) -> None:
        super().__init__()
        self._create_card = create_card
        self._songs: list[SongStorable] = []
        self._cards: dict[int, _SongCardItem] = {}
        self._card_timer = QTimer(self)
        self._card_timer.setSingleShot(True)
        self._card_timer.timeout.connect(self._syncCards)
        self.setItemDelegate(_SongListDelegate(self))
        self.setUniformItemSizes(True)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.verticalScrollBar().valueChanged.connect(self._queueCards)
        self.verticalScrollBar().rangeChanged.connect(self._queueCards)

    def setSongs(self, songs: list[SongStorable]) -> None:
        for row in tuple(self._cards):
            self._removeCard(row)
        self.clear()
        self._songs = songs
        self.addItems([''] * len(songs))
        self.scrollToTop()
        self._queueCards()

    def cards(self) -> ValuesView[_SongCardItem]:
        return self._cards.values()

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._queueCards()

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._queueCards()

    def _queueCards(self) -> None:
        if self.isVisible() and not self._card_timer.isActive():
            self._card_timer.start(0)

    def _removeCard(self, row: int) -> None:
        card = self._cards.pop(row)
        card.releaseResources()
        card.hide()
        self.removeItemWidget(self.item(row))

    def _syncCards(self) -> None:
        if not self.isVisible() or not self._songs:
            return
        viewport = self.viewport().rect()
        start, end = 0, len(self._songs)
        while start < end:
            middle = (start + end) // 2
            if self.visualItemRect(self.item(middle)).bottom() < viewport.top():
                start = middle + 1
            else:
                end = middle
        end = start
        while end < len(self._songs):
            if self.visualItemRect(self.item(end)).top() > viewport.bottom():
                break
            end += 1

        rows = range(max(0, start - 1), min(len(self._songs), end + 1))
        for row in tuple(self._cards):
            if row not in rows:
                self._removeCard(row)
        deadline = time.perf_counter() + 0.006
        for row in rows:
            if row not in self._cards:
                if time.perf_counter() >= deadline:
                    self._card_timer.start(1)
                    break
                card = self._create_card(self._songs[row])
                self._cards[row] = card
                QListWidget.setItemWidget(self, self.item(row), card)
            if start <= row < end and not self._cards[row].load:
                self._cards[row].loadDetailAndImage()
