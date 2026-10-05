from __future__ import annotations

import time
from typing import TYPE_CHECKING, cast, override

from PySide6.QtCore import QTimer
from PySide6.QtGui import QHideEvent, QShowEvent, Qt
from PySide6.QtWidgets import QHBoxLayout, QListWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FlowLayout,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    TitleLabel,
    TransparentToolButton,
)

from core.favorites import favorites_manager
from core.i18n import bindText, tr
from core.models import CloudFolderInfo, LocalFolderInfo, SongStorable
from core.qt_utils import clearListWidget
from core.rediscovery import RediscoveryMode, getListeningHistory, getRediscoverySongs
from services.events import (
    FAVORITES_CHANGED,
    LANGUAGE_CHANGED,
    PLAYLIST_CHANGED,
    event_bus,
)
from services.events.events import _50MS_TICK
from views.list_widget import SListWidget, setTransparentBackground
from views.song_card import _SongCardItem

if TYPE_CHECKING:
    from core.app_context import AppContext

LIST_BUILD_BATCH_SIZE = 40


class RediscoveryPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setObjectName('rediscovery_page')
        setTransparentBackground(self)
        self._folder: LocalFolderInfo | CloudFolderInfo | None = None
        self._cloud_songs: list[SongStorable] = []
        self._songs: list[SongStorable] = []
        self._source_ids: tuple[str, ...] = ()
        self._song_cards: list[_SongCardItem] = []
        self._date_labels: list[tuple[CaptionLabel, str]] = []
        self._history: dict[str, float] = {}
        self._next_song_index = 0
        self._building_batch = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 12)
        layout.setSpacing(10)
        header = QHBoxLayout()
        self.back_button = TransparentToolButton(FluentIcon.LEFT_ARROW, self)
        self.back_button.setFixedSize(32, 32)
        self.back_button.clicked.connect(self.goBack)
        header.addWidget(self.back_button)
        title = TitleLabel(self)
        bindText(title, 'rediscovery.title')
        header.addWidget(title, 1)
        layout.addLayout(header)
        self.scope_label = CaptionLabel(self)
        self.scope_label.setWordWrap(True)
        layout.addWidget(self.scope_label)
        self.description = BodyLabel(self)
        self.description.setWordWrap(True)
        layout.addWidget(self.description)

        actions = FlowLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setHorizontalSpacing(8)
        actions.setVerticalSpacing(8)
        self.mode_box = ComboBox(self)
        self.mode_box.setMinimumWidth(130)
        self._updateModes()
        actions.addWidget(self.mode_box)
        self.play_button = PrimaryPushButton(FluentIcon.PLAY_SOLID, '', self)
        bindText(self.play_button, 'rediscovery.play')
        actions.addWidget(self.play_button)
        self.refresh_button = PushButton(FluentIcon.SYNC, '', self)
        bindText(self.refresh_button, 'rediscovery.refresh')
        actions.addWidget(self.refresh_button)
        layout.addLayout(actions)
        self.song_viewer = SListWidget(self)
        layout.addWidget(self.song_viewer, 1)
        self.summary = CaptionLabel(self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.timeout.connect(self._appendSongBatch)
        self.mode_box.currentIndexChanged.connect(self.refresh)
        self.play_button.clicked.connect(lambda: self.play())
        self.refresh_button.clicked.connect(lambda: self.refresh(rotate=True))
        scrollbar = self.song_viewer.verticalScrollBar()
        scrollbar.valueChanged.connect(self._queueSongBatch)
        scrollbar.rangeChanged.connect(self._queueSongBatch)
        event_bus.subscribe(_50MS_TICK, self._checkVisibleCards)
        event_bus.subscribe(FAVORITES_CHANGED, self._favoritesChanged)
        event_bus.subscribe(LANGUAGE_CHANGED, self._languageChanged)
        self._updateTexts()

    def setSource(
        self,
        folder: LocalFolderInfo | CloudFolderInfo | None = None,
        cloud_songs: list[SongStorable] | None = None,
    ) -> None:
        self._folder = folder
        self._cloud_songs = list(cloud_songs or [])
        self.refresh()

    def _sourceSongs(self) -> list[SongStorable]:
        if isinstance(self._folder, CloudFolderInfo):
            return self._cloud_songs
        if self._folder is not None:
            folder_name = self._folder.folder_name
            return next(
                (
                    folder.songs
                    for folder in favorites_manager.folders
                    if folder.folder_name == folder_name
                ),
                [],
            )
        return [song for folder in favorites_manager.folders for song in folder.songs]

    def _updateModes(self) -> None:
        mode = self.mode_box.currentData() or 'balanced'
        self.mode_box.blockSignals(True)
        self.mode_box.clear()
        for value in ('balanced', 'rare', 'forgotten'):
            self.mode_box.addItem(tr(f'rediscovery.{value}'), userData=value)
        self.mode_box.setCurrentIndex(self.mode_box.findData(mode))
        self.mode_box.blockSignals(False)

    def _languageChanged(self, *_args: object) -> None:
        self._updateModes()
        self._updateTexts()
        for label, song_id in self._date_labels:
            self._updateDate(label, song_id)

    def _favoritesChanged(self, *_args: object) -> None:
        if isinstance(self._folder, CloudFolderInfo):
            return
        if self.isVisible():
            self.refresh()

    def refresh(self, _index: int = 0, *, rotate: bool = False) -> None:
        self._load_timer.stop()
        self._building_batch = True
        source = self._sourceSongs()
        current = self.ctx.playing_manager.current_song
        previous_ids = {str(song.id) for song in self._songs[:40]} if rotate else None
        self._songs = getRediscoverySongs(
            source,
            cast(RediscoveryMode, self.mode_box.currentData()),
            limit=None,
            previous_ids=previous_ids,
            current_id=str(current.id) if current else None,
        )
        self._source_ids = tuple(str(song.id) for song in source)
        self._history = getListeningHistory()
        clearListWidget(self.song_viewer)
        self._song_cards.clear()
        self._date_labels.clear()
        self._next_song_index = 0
        self._appendSongBatch()

    def _appendSongBatch(self) -> None:
        self._building_batch = True
        end = min(self._next_song_index + LIST_BUILD_BATCH_SIZE, len(self._songs))
        for index in range(self._next_song_index, end):
            song = self._songs[index]
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, song)
            card = _SongCardItem(
                song,
                self.ctx.playing_page,
                self.ctx.main_window,
                self.ctx.playlist_page,
                lazy=True,
                sortable=False,
            )
            card.setMinimumHeight(70)
            card.clicked.connect(lambda _song, i=index: self.play(i))
            card.queued.connect(self._queueSong)
            date_label = CaptionLabel(card)
            date_label.setFixedWidth(130)
            date_label.setWordWrap(True)
            self._updateDate(date_label, str(song.id))
            cast(QHBoxLayout, card.layout()).addWidget(date_label)
            self.song_viewer.addItem(item)
            self.song_viewer.setItemWidget(item, card)
            self._song_cards.append(card)
            self._date_labels.append((date_label, str(song.id)))
        self._next_song_index = end
        self._building_batch = False
        self.song_viewer.doItemsLayout()
        self._updateTexts()
        self._queueSongBatch()

    def _updateDate(self, label: CaptionLabel, song_id: str) -> None:
        timestamp = self._history.get(song_id, 0)
        if timestamp:
            days = max(0, int((time.time() - timestamp) / 86400))
            label.setText(
                tr('rediscovery.days_ago', days=days)
                if days
                else tr('rediscovery.today')
            )
        else:
            label.setText(tr('rediscovery.unknown_date'))

    def _updateTexts(self) -> None:
        mode = self.mode_box.currentData()
        bindText(self.description, f'rediscovery.{mode}_description')
        self.back_button.setToolTip(tr('rediscovery.back'))
        if self._folder is None:
            bindText(self.scope_label, 'rediscovery.all_favorites')
        else:
            bindText(
                self.scope_label,
                'rediscovery.folder_scope',
                name=self._folder.folder_name,
            )
        self.summary.setToolTip(tr('rediscovery.history_tip'))
        if self._songs:
            bindText(
                self.summary,
                'rediscovery.loaded_all'
                if self._next_song_index == len(self._songs)
                else 'rediscovery.loaded',
                loaded=self._next_song_index,
                count=len(self._songs),
            )
        else:
            bindText(
                self.summary,
                'rediscovery.no_history'
                if mode == 'forgotten'
                else 'rediscovery.empty',
            )
        self.play_button.setEnabled(bool(self._songs))

    def _queueSongBatch(self, *_args: int) -> None:
        if (
            self._building_batch
            or not self.isVisible()
            or self._next_song_index >= len(self._songs)
        ):
            return
        scrollbar = self.song_viewer.verticalScrollBar()
        if (
            scrollbar.maximum() - scrollbar.value()
            <= self.song_viewer.viewport().height() * 2
            and not self._load_timer.isActive()
        ):
            self._load_timer.start(16)

    def _checkVisibleCards(self) -> None:
        if not self.isVisible():
            return
        viewport = self.song_viewer.viewport()
        visible = viewport.rect().adjusted(0, -viewport.height(), 0, viewport.height())
        start = 0
        end = self.song_viewer.count()
        while start < end:
            middle = (start + end) // 2
            item = self.song_viewer.item(middle)
            if item is None:
                break
            if self.song_viewer.visualItemRect(item).bottom() < visible.top():
                start = middle + 1
            else:
                end = middle
        for index in range(start, self.song_viewer.count()):
            item = self.song_viewer.item(index)
            if item is None:
                continue
            rect = self.song_viewer.visualItemRect(item)
            if rect.top() > visible.bottom():
                break
            card = self._song_cards[index]
            if not card.load and visible.intersects(rect):
                card.loadDetailAndImage()
        self._queueSongBatch()

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._queueSongBatch()

    @override
    def hideEvent(self, event: QHideEvent) -> None:
        self._load_timer.stop()
        super().hideEvent(event)

    def play(self, index: int = 0) -> None:
        source = self._sourceSongs()
        if tuple(str(song.id) for song in source) != self._source_ids:
            self.refresh()
            index = 0
        if not self._songs:
            return
        self.ctx.playing_manager.setPlaylist(list(self._songs))
        self.ctx.playing_manager.playSongAtIndex(index)

    def _queueSong(self, song: SongStorable) -> None:
        manager = self.ctx.playing_manager
        manager.playlist.insert(max(0, manager.current_index + 1), song)
        event_bus.emit(PLAYLIST_CHANGED)

    def goBack(self) -> None:
        target = (
            self.ctx.favorites_page if self._folder is not None else self.ctx.home_page
        )
        self.ctx.main_window.contents_widget.setCurrentWidget(target)
