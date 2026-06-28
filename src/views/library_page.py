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
    POST_PLAY_STORABLE,
    ComboBox,
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
    TransparentToolButton,
    bindText,
    event_bus,
    tr,
)
from qfluentwidgets import LineEdit, FluentIcon
from views.song_card import FavoriteSongCard
import logging


class LibraryPage(SScrollArea):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self._logger = logging.getLogger(__name__)
        self.ctx = ctx
        self._song_cards: list[FavoriteSongCard] = []

        contents_widget = QWidget()
        self.contents_layout = QVBoxLayout()
        contents_widget.setLayout(self.contents_layout)

        title_label = TitleLabel('')
        bindText(title_label, 'library_page.title')
        self.contents_layout.addWidget(title_label)

        number_layout = QHBoxLayout()
        self.search_input = LineEdit()
        self.search_input.textChanged.connect(self.search)
        number_layout.addWidget(self.search_input)
        self.sort_box = ComboBox()
        self._refreshSortBox()
        self.sort_box.currentIndexChanged.connect(self.sortSongs)
        number_layout.addWidget(self.sort_box)
        refresh_button = TransparentToolButton(FluentIcon.SYNC)
        refresh_button.clicked.connect(lambda: self.fetchSongs(force=True))
        number_layout.addWidget(refresh_button)
        number_layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        )
        prefix = SubtitleLabel('')
        bindText(prefix, 'library_page.number_prefix')
        suffix = SubtitleLabel('')
        bindText(suffix, 'library_page.number_suffix')
        number_layout.addWidget(prefix)
        self.viewer = NumberViewer(self.ctx.harmony_font_family, self.ctx, 18, 0.7)
        number_layout.addWidget(self.viewer)
        number_layout.addWidget(suffix)
        self.contents_layout.addLayout(number_layout)

        self.cards_layout = SFlowLayout(isTight=True, yAnimations=True)
        self.cards_layout.setAnimation(350)
        self.contents_layout.addLayout(self.cards_layout)

        self.contents_layout.addSpacerItem(
            QSpacerItem(
                0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
        )

        self._lazy_timer = QTimer(self)
        self._lazy_timer.timeout.connect(self._checkVisibleCards)
        self._lazy_timer.start(50)

        self._debounce_timer = QTimer(self)
        self._debounce_timer.timeout.connect(self.searchSongs)
        self._debounce_timer.setSingleShot(True)

        self.loaded = False

        self.setWidgetResizable(True)
        self.setWidget(contents_widget)

        event_bus.subscribe(POST_PLAY_STORABLE, self.sortSongs)

    def search(self) -> None:
        self._debounce_timer.start(500)

    def searchSongs(self) -> None:
        self._lazy_timer.stop()
        keyword = self.search_input.text().strip().casefold()
        visible_count = 0
        contents_widget = self.widget()
        if contents_widget is not None:
            contents_widget.setUpdatesEnabled(False)

        try:
            for card in self._song_cards:
                visible = not keyword or keyword in self._searchTextForCard(card)
                if card.isVisible() != visible:
                    card.setVisible(visible)
                if visible:
                    visible_count += 1
        finally:
            if contents_widget is not None:
                contents_widget.setUpdatesEnabled(True)

        self.viewer.setText(str(visible_count))
        self._refreshCardsLayout()

    def sortSongs(self, _=None) -> None:
        self._song_cards.sort(key=self._sortKey, reverse=self._sortReversed())
        self._rebuildCardsLayout()
        self.searchSongs()

    def _refreshSortBox(self) -> None:
        items = (
            ('name_asc', 'library_page.sort.name_asc'),
            ('name_desc', 'library_page.sort.name_desc'),
            ('artist_asc', 'library_page.sort.artist_asc'),
            ('artist_desc', 'library_page.sort.artist_desc'),
            ('name_length_asc', 'library_page.sort.name_length_asc'),
            ('name_length_desc', 'library_page.sort.name_length_desc'),
            ('count_asc', 'library_page.sort.count_asc'),
            ('count_desc', 'library_page.sort.count_desc'),
        )
        self.sort_box.blockSignals(True)
        self.sort_box.clear()
        for value, key in items:
            self.sort_box.addItem(tr(key), userData=value)
        self.sort_box.setCurrentIndex(0)
        self.sort_box.blockSignals(False)

    def _sortKey(self, card: FavoriteSongCard) -> tuple[object, str]:
        mode = self.sort_box.currentData()
        song = card.storable
        if mode in ('artist_asc', 'artist_desc'):
            return (self._songArtistsText(song), song.name.casefold())
        if mode in ('name_length_asc', 'name_length_desc'):
            return (len(song.name), song.name.casefold())
        if mode in ('count_asc', 'count_desc'):
            return (song.count, song.name.casefold())
        return (song.name.casefold(), self._songArtistsText(song))

    def _sortReversed(self) -> bool:
        mode = self.sort_box.currentData()
        return mode in ('name_desc', 'artist_desc', 'name_length_desc', 'count_desc')

    def _refreshCardsLayout(self) -> None:
        self.cards_layout.invalidate()
        self.cards_layout.setGeometry(self.cards_layout.geometry())
        QTimer.singleShot(
            0,
            lambda: self.cards_layout.setGeometry(self.cards_layout.geometry()),
        )
        self._lazy_timer.start(50)

    def _rebuildCardsLayout(self) -> None:
        for index, card in enumerate(self._song_cards):
            self.cards_layout.moveWidget(card, index)
        self._refreshCardsLayout()

    def _searchTextForCard(self, card: FavoriteSongCard) -> str:
        text = getattr(card, '_library_search_text', '')
        if isinstance(text, str) and text:
            return text

        text = self._songSearchText(card.storable)
        card._library_search_text = text  # type: ignore[attr-defined]
        return text

    def _songSearchText(self, song: SongStorable) -> str:
        artists = self._songArtistsText(song)
        return f'{song.name} {artists}'.casefold()

    def _songArtistsText(self, song: SongStorable) -> str:
        return ' '.join(artist.name for artist in song.artists).casefold()

    def fetchSongs(self, force: bool = False):
        if self.loaded and not force:
            return
        self.loaded = True

        self.viewer.setText('0')
        self.viewer.y_map.clear()

        removeWidgets(self.cards_layout)
        self._song_cards = []

        songs: list[SongStorable] = []

        for folder in favorites_manager.folders:
            if isinstance(folder, CloudFolderInfo):
                continue
            for s in folder.songs:
                if s in songs:
                    continue
                songs.append(s)

        for song in songs:
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
            card._library_search_text = self._songSearchText(song)  # type: ignore[attr-defined]
            card.clicked.connect(self._playSong)
            card.queued.connect(self._queueSong)
            self._song_cards.append(card)

        self.sortSongs()

        self.viewer.setText(str(len(songs)))

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
