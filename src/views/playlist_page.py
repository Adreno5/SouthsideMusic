from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core import theme
from core.app_context import AppContext

from core.config import cfg

from core.color import mixColor
from imports import (
    BACKGROUND_RATIO_CHANGED,
    PLAY_PLAYLIST_STORABLE,
    PLAYLIST_CHANGED,
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
    bindText,
    tr,
)
from imports import (
    MessageBox,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
)
from views.list_widget import SListWidget
from qfluentwidgets import InfoBar, TransparentPushButton
from core.models import SongStorable
from core.icons import bindIcon
from views.song_card import DummyCard, PlaylistSongCard, SONG_CARD_HEIGHT

if TYPE_CHECKING:
    from core.ws_server import QObjectHandler, WebSocketServer

WHITE = QColor(255, 255, 255, 100)
BLACK = QColor(0, 0, 0, 100)
LIST_BUILD_BATCH_SIZE = 40


class PlaylistPage(QWidget):
    def __init__(
        self,
        ctx: AppContext,
    ):
        super().__init__()
        self._logger = logging.getLogger(__name__)
        self.ctx = ctx
        if ctx.launch_window:
            ctx.launch_window.top(tr('playlist_page.initializing_sidebar'))
            self._launchwindow = ctx.launch_window
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
        self._playlist_refresh_seq = 0
        self._lazy_timer = QTimer(self)
        self._lazy_timer.timeout.connect(self._checkVisibleCards)
        self._lazy_timer.start(50)

        btn_layout = QHBoxLayout()
        self.removeall_btn = TransparentPushButton('')
        bindText(self.removeall_btn, 'playlist_page.remove_all')
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
        event_bus.subscribe(PLAYLIST_CHANGED, self.refreshPlaylistWidget)
        event_bus.subscribe(POST_THEME_CHANGED, self._updateDatas)
        event_bus.subscribe(BACKGROUND_RATIO_CHANGED, self._updateDatas)

    @property
    def _dp(self):
        return self.ctx.playing_page

    @property
    def _mwindow(self):
        return self.ctx.main_window

    @property
    def _player(self):
        return self.ctx.player

    @property
    def _pm(self):
        return self.ctx.playing_manager

    def _updateDatas(self, song: SongStorable | None = None):
        if self._mwindow:
            self.bg_color = mixColor(
                QColor(40, 40, 40) if theme.isDark() else QColor(230, 230, 230),
                self._mwindow.song_theme
                if self._mwindow.song_theme
                else QColor(0, 0, 0),
                1 - cfg.background_ratio * 0.5,
            )
        else:
            self.bg_color = (
                QColor(40, 40, 40) if theme.isDark() else QColor(230, 230, 230)
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
        for i, song in enumerate(self._pm.playlist):
            if song.id == storable.id:
                self.lst.setCurrentRow(i)
                return

    def removeAllSongs(self) -> None:
        dialog = MessageBox(
            tr('playlist_page.confirm_delete'),
            tr('playlist_page.are_you_sure_you_want_to_remove_all_songs_from_playlist'),
            self._mwindow,
        )
        dialog.cancelButton.setText(tr('playlist_page.cancel'))
        dialog.yesButton.setText(tr('playlist_page.delete'))
        if not dialog.exec():
            return

        self._pm.playlist.clear()
        if isinstance(self._dp.cur, DummyCard) and isinstance(
            self._dp.cur.storable, SongStorable
        ):
            self._pm.playlist.append(self._dp.cur.storable)

        self.refreshPlaylistWidget()

        InfoBar.success(
            tr('playlist_page.removed'),
            tr('playlist_page.removed_all_songs'),
            duration=1500,
            parent=self._mwindow,
        )

    def addSongCardToList(self, song: SongStorable) -> QListWidgetItem:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, song)
        item.setSizeHint(QSize(0, SONG_CARD_HEIGHT))
        card = PlaylistSongCard(
            song, self._dp, mwindow=self._mwindow, plp=self, lazy=True
        )
        card.clicked.connect(lambda s: self._onSongClicked(s))
        card.queued.connect(lambda s: self._onSongClicked(s))
        self.lst.addItem(item)
        self.lst.setItemWidget(item, card)
        self._song_cards.append(card)
        return item

    def _onSongClicked(self, storable: SongStorable):
        event_bus.emit(PLAY_PLAYLIST_STORABLE, storable)

    def _checkVisibleCards(self):
        for idx, card in enumerate(list(self._song_cards)):
            try:
                card.objectName()
            except RuntimeError:
                continue
            if card.load:
                continue
            item = self.lst.item(idx)
            if item is None:
                continue
            item_rect = self.lst.visualItemRect(item)
            viewport_rect = self.lst.viewport().rect()
            if viewport_rect.intersects(item_rect):
                card.loadDetailAndImage()

    def refreshPlaylistWidget(self):
        self._playlist_refresh_seq += 1
        refresh_seq = self._playlist_refresh_seq
        val = self.lst.verticalScrollBar().value()
        songs = list(self._pm.playlist)
        self._song_cards = []
        self.lst.clear()
        self._pm.clearPreload()

        self._appendPlaylistBatch(refresh_seq, songs, 0, val)

    def _appendPlaylistBatch(
        self,
        refresh_seq: int,
        songs: list[SongStorable],
        start: int,
        scroll_value: int,
    ) -> None:
        if refresh_seq != self._playlist_refresh_seq:
            return

        end = min(start + LIST_BUILD_BATCH_SIZE, len(songs))
        for song in songs[start:end]:
            self.addSongCardToList(song)

        if end < len(songs):
            QTimer.singleShot(
                1,
                lambda: self._appendPlaylistBatch(
                    refresh_seq,
                    songs,
                    end,
                    scroll_value,
                ),
            )
            return

        self.lst.verticalScrollBar().setValue(scroll_value)
        self._syncPlaylistSelection()

    def movePlaylistSong(self, song: SongStorable, delta: int):
        playlist = self._pm.playlist
        try:
            old_index = playlist.index(song)
        except ValueError:
            return

        new_index = old_index + delta
        if new_index < 0 or new_index >= len(playlist):
            return

        current_song = None
        if 0 <= self._pm.current_index < len(playlist):
            current_song = playlist[self._pm.current_index]

        playlist[old_index], playlist[new_index] = (
            playlist[new_index],
            playlist[old_index],
        )
        if current_song is not None:
            self._pm.current_index = playlist.index(current_song)

        event_bus.emit(PLAYLIST_CHANGED)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setPen(QPen(WHITE if theme.isDark() else BLACK, 1))
        painter.setBrush(self.bg_color)
        painter.drawRoundedRect(self.rect(), 10, 10)
