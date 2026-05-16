from __future__ import annotations

import logging

from core import theme
from core.app_context import AppContext

import darkdetect

from core.config import cfg

from core.color import mixColor
from imports import (
    BACKGROUND_RATIO_CHANGED,
    POST_THEME_CHANGED,
    SONG_CHANGED,
    QColor,
    QPaintEvent,
    QPainter,
    QPen,
    QSize,
    Qt,
    QTimer,
    event_bus,
)
from imports import (
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QStackedWidget,
)
from qfluentwidgets import Pivot, SmoothScrollArea
from views.list_widget import SListWidget
from qfluentwidgets import InfoBar, TransparentPushButton
from core.models import SongStorable
from core.icons import bindIcon
from views.song_card import DummyCard, PlaylistSongCard
from views.setting_page import SettingPage

WHITE = QColor(255, 255, 255, 100)
BLACK = QColor(0, 0, 0, 100)

class PlaylistPage(QWidget):
    def __init__(
        self,
        ctx: AppContext,
    ):
        super().__init__()
        self._logger = logging.getLogger(__name__)
        self.ctx = ctx
        if ctx.launchwindow:
            ctx.launchwindow.top('Initializing sidebar...')
            self._launchwindow = ctx.launchwindow
        else:
            self._launchwindow = None
        self._ws_server: WebSocketServer = ctx.ws_server  # type: ignore
        self._ws_handler: QObjectHandler = ctx.ws_handler  # type: ignore
        self._app = ctx.app

        self.setObjectName('PlaylistPage')
        self.setFixedWidth(500)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        self.lst_interface = QWidget()
        self.lst_layout = QVBoxLayout()
        self.lst = SListWidget()
        self.lst.setFixedWidth(500)
        self.lst.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.lst_layout.addWidget(self.lst)

        self._song_cards: list[PlaylistSongCard] = []
        self._lazy_timer = QTimer(self)
        self._lazy_timer.timeout.connect(self._checkVisibleCards)
        self._lazy_timer.start(50)

        btn_layout = QHBoxLayout()
        self.removeall_btn = TransparentPushButton('Remove All')
        bindIcon(self.removeall_btn, 'clearall')
        self.removeall_btn.clicked.connect(self.removeAllSongs)
        btn_layout.addWidget(self.removeall_btn)
        self.lst_layout.addLayout(btn_layout)
        self.lst_interface.setLayout(self.lst_layout)

        layout.addWidget(self.lst_interface)

        self.setLayout(layout)
        self.hide()

        self.bg_color = QColor(0, 0, 0)

        event_bus.subscribe(SONG_CHANGED, self._onSongChanged)
        event_bus.subscribe(SONG_CHANGED, self._updateDatas)
        event_bus.subscribe(POST_THEME_CHANGED, self._updateDatas)
        event_bus.subscribe(BACKGROUND_RATIO_CHANGED, self._updateDatas)

    @property
    def _dp(self):
        return self.ctx.dp

    @property
    def _mwindow(self):
        return self.ctx.mwindow

    @property
    def _player(self):
        return self.ctx.player

    def _updateDatas(self, song: SongStorable | None = None):
        if self._mwindow:
            self.bg_color = mixColor(
                QColor(40, 40, 40) if darkdetect.isDark() else QColor(230, 230, 230),
                self._mwindow.song_theme
                if self._mwindow.song_theme
                else QColor(0, 0, 0),
                1 - cfg.background_ratio * 0.5,
            )
        else:
            self.bg_color = (
                QColor(40, 40, 40) if darkdetect.isDark() else QColor(230, 230, 230)
            )

        self.update()

    def _onSongChanged(self, _song_storable):
        self._syncPlaylistSelection()

    def _syncPlaylistSelection(self):
        if not self._dp.cur:
            return
        if not hasattr(self._dp.cur, 'storable'):
            return
        storable = self._dp.cur.storable
        for i, song in enumerate(self._dp.playlist):
            if song.name == storable.name:
                self.lst.setCurrentRow(i)
                return

    def removeAllSongs(self) -> None:
        self._dp.playlist.clear()
        if isinstance(self._dp.cur, DummyCard) and isinstance(
            self._dp.cur.storable, SongStorable
        ):
            self._dp.playlist.append(self._dp.cur.storable)

        self.refreshPlaylistWidget()

        InfoBar.success(
            'Removed', 'Removed all songs', duration=1500, parent=self._mwindow
        )

    def addSongCardToList(self, song: SongStorable) -> QListWidgetItem:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, song)
        item.setSizeHint(QSize(0, 62))
        card = PlaylistSongCard(
            song, self._dp, mwindow=self._mwindow, plp=self, lazy=True
        )
        card.clicked.connect(lambda s, it=item: self._dp.onPlaylistCardClicked(s, it))
        self.lst.addItem(item)
        self.lst.setItemWidget(item, card)
        self._song_cards.append(card)
        return item

    def _checkVisibleCards(self):
        for card in list(self._song_cards):
            try:
                card.objectName()
            except RuntimeError:
                continue
            if card.load:
                continue
            idx = self._song_cards.index(card)
            item = self.lst.item(idx)
            if item is None:
                continue
            item_rect = self.lst.visualItemRect(item)
            viewport_rect = self.lst.viewport().rect()
            if viewport_rect.intersects(item_rect):
                card.loadDetailAndImage()

    def refreshPlaylistWidget(self):
        val = self.lst.verticalScrollBar().value()
        self._song_cards = []
        self.lst.clear()

        for song in self._dp.playlist:
            self.addSongCardToList(song)

        self._dp._preload_triggered = False
        self.lst.verticalScrollBar().setValue(val)

    def movePlaylistSong(self, song: SongStorable, delta: int):
        playlist = self._dp.playlist
        try:
            old_index = playlist.index(song)
        except ValueError:
            return

        new_index = old_index + delta
        if new_index < 0 or new_index >= len(playlist):
            return

        current_song = None
        if 0 <= self._dp.current_index < len(playlist):
            current_song = playlist[self._dp.current_index]

        playlist[old_index], playlist[new_index] = (
            playlist[new_index],
            playlist[old_index],
        )
        if current_song is not None:
            self._dp.current_index = playlist.index(current_song)
        self._dp.playing_manager.refreshRandom()

        self.refreshPlaylistWidget()
        self.lst.setCurrentRow(new_index)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setPen(QPen(WHITE if theme.isDark() else BLACK, 1))
        painter.setBrush(self.bg_color)
        painter.drawRoundedRect(self.rect(), 10, 10)
