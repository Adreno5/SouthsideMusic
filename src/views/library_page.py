from core.app_context import AppContext
from core.favorites import favorites_manager
from core.models import CloudFolderInfo, SongStorable
from core.qt_utils import removeWidgets
from views.animated_layout import SFlowLayout
from views.list_widget import SScrollArea
from views.number_viewer import NumberViewer
from imports import (
    PLAYLIST_CHANGED,
    PLAY_STORABLE,
    QHBoxLayout,
    QPoint,
    QRect,
    QSizePolicy,
    QSpacerItem,
    QTimer,
    QWidget,
    QVBoxLayout,
    SubtitleLabel,
    TitleLabel,
    bindText,
    event_bus,
)
from views.song_card import FavoriteSongCard
import logging


class LibraryPage(SScrollArea):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self._logger = logging.getLogger(__name__)
        self.ctx = ctx
        self._song_cards: list[FavoriteSongCard] = []
        self._fetch_seq = 0

        contents_widget = QWidget()
        self.contents_layout = QVBoxLayout()
        contents_widget.setLayout(self.contents_layout)

        title_label = TitleLabel('')
        bindText(title_label, 'library_page.title')
        self.contents_layout.addWidget(title_label)

        number_layout = QHBoxLayout()
        prefix = SubtitleLabel('')
        bindText(prefix, 'library_page.number_prefix')
        suffix = SubtitleLabel('')
        bindText(suffix, 'library_page.number_suffix')
        number_layout.addWidget(prefix)
        self.viewer = NumberViewer(self.ctx.harmony_font_family, self.ctx, 18, 0.7)
        number_layout.addWidget(self.viewer)
        number_layout.addWidget(suffix)
        self.contents_layout.addLayout(number_layout)

        self.cards_layout = SFlowLayout(yAnimations=True)
        self.cards_layout.setAnimation(100)
        self.contents_layout.addLayout(self.cards_layout)

        self.contents_layout.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding))

        self._lazy_timer = QTimer(self)
        self._lazy_timer.timeout.connect(self._checkVisibleCards)
        self._lazy_timer.start(50)

        self.setWidgetResizable(True)
        self.setWidget(contents_widget)

    def fetchSongs(self):
        self._fetch_seq += 1
        fetch_seq = self._fetch_seq

        self.viewer.setText('0')
        self.viewer.y_map.clear()

        removeWidgets(self.cards_layout)
        self._song_cards = []

        songs: list[SongStorable] = []

        for folder in favorites_manager.folders:
            if isinstance(folder, CloudFolderInfo):
                continue
            songs.extend(folder.songs)

        def add(index: int = 0) -> None:
            if fetch_seq != self._fetch_seq or index >= len(songs):
                return
            
            self.viewer.setText(f'{index + 1}')

            song = songs[index]
            card = FavoriteSongCard(
                song,
                self.ctx.playing_page,
                self.ctx.main_window,
                self.ctx.playlist_page,
                None,
                None,
                None,
                lazy=True,
                sortable=False,
            )
            card.clicked.connect(self._playSong)
            card.queued.connect(self._queueSong)
            self.cards_layout.insertWidget(0, card)
            self._song_cards.append(card)

            self._checkVisibleCards()
            QTimer.singleShot(1, lambda: add(index + 1))

        add()

    def _checkVisibleCards(self) -> None:
        viewport_rect = self.viewport().rect()
        valid_cards: list[FavoriteSongCard] = []
        for card in self._song_cards:
            try:
                if not card.isVisible():
                    valid_cards.append(card)
                    continue
                top_left = card.mapTo(self.viewport(), QPoint(0, 0))
                card_rect = QRect(top_left, card.size())
            except RuntimeError:
                continue

            valid_cards.append(card)
            if not card.load and viewport_rect.intersects(card_rect):
                card.loadDetailAndImage()
        self._song_cards = valid_cards

    def _playSong(self, song: SongStorable) -> None:
        event_bus.emit(PLAY_STORABLE, song)

    def _queueSong(self, song: SongStorable) -> None:
        playlist = self.ctx.playing_manager.playlist
        insert_index = self.ctx.playing_manager.current_index + 2
        playlist.insert(insert_index, song)
        event_bus.emit(PLAYLIST_CHANGED)
