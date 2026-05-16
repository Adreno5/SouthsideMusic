from __future__ import annotations

from core.app_context import AppContext
from imports import PushButton, QSize, Qt, QTimer
from imports import (
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    FluentIcon,
    FlowLayout,
    InfoBar,
    PrimaryPushButton,
    TitleLabel,
)
from views.list_widget import SListWidget

from core.models import FolderInfo, SongStorable
from core.favorites import loadFavorites, saveFavorites, favs

from views.playing_page import PlayingPage
from views.song_card import FavoriteSongCard


class FavoritesPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        lw = ctx.launchwindow
        if lw:
            lw.top('Initializing favorites page...')
        self.setObjectName('favorites_page')

        global_layout = QVBoxLayout(self)

        self.title_label = TitleLabel('None')
        buttons_layout = FlowLayout()
        self.reppl_btn = PushButton('Replace Playlist')
        self.reppl_btn.clicked.connect(self.replacePlaylist)
        buttons_layout.addWidget(self.reppl_btn)
        self.addpl_btn = PushButton('Add to Playlist')
        self.addpl_btn.clicked.connect(self.addFolderToPlaylist)
        buttons_layout.addWidget(self.addpl_btn)

        global_layout.addWidget(self.title_label)
        global_layout.addLayout(buttons_layout)

        self.song_viewer = SListWidget()
        self.song_viewer.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        global_layout.addWidget(self.song_viewer, 1)

        self.curr_folder: FolderInfo = FolderInfo(folder_name='None', songs=[])

        self._song_cards: list[FavoriteSongCard] = []
        self._lazy_timer = QTimer(self)
        self._lazy_timer.timeout.connect(self._checkVisibleCards)
        self._lazy_timer.start(50)

    @property
    def _dp(self):
        return self.ctx.dp

    @property
    def _mwindow(self):
        return self.ctx.mwindow

    @property
    def _plp(self):
        return self.ctx.plp

    def setDisplayFolder(self, folder: FolderInfo):
        self.curr_folder = folder
        self.refresh()

    def _get_favs(self):
        return favs

    def _checkVisibleCards(self):
        for card in self._song_cards:
            if card.load:
                continue
            idx = self._song_cards.index(card)
            item = self.song_viewer.item(idx)
            if item is None:
                continue
            item_rect = self.song_viewer.visualItemRect(item)
            viewport_rect = self.song_viewer.viewport().rect()
            if viewport_rect.intersects(item_rect):
                card.loadDetailAndImage()

    def refresh(self):
        loadFavorites()
        self._song_cards = []
        self.song_viewer.clear()

        self.title_label.setText(self.curr_folder['folder_name'])

        for song in self.curr_folder['songs']:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, song)
            item.setSizeHint(QSize(0, 62))
            card = FavoriteSongCard(
                song,
                self._dp,
                self._mwindow,
                self._plp,
                remove_callback=lambda s=song: self.deleteSong(s),
                move_callback=self.moveSong,
                lazy=True,
            )
            card.clicked.connect(lambda s, it=item: self._onSongClicked(s, it))
            self.song_viewer.addItem(item)
            self.song_viewer.setItemWidget(item, card)
            self._song_cards.append(card)

    def moveSong(self, song: SongStorable, delta: int):
        songs = self.curr_folder['songs']
        try:
            old_index = songs.index(song)
        except ValueError:
            return

        new_index = old_index + delta
        if new_index < 0 or new_index >= len(songs):
            return

        current_song = None
        if 0 <= self._dp.current_index < len(songs):
            current_song = songs[self._dp.current_index]

        songs[old_index], songs[new_index] = (
            songs[new_index],
            songs[old_index],
        )
        if current_song is not None:
            self._dp.current_index = songs.index(current_song)
        self._dp.playing_manager.refreshRandom()

        self.refresh()
        saveFavorites()

    def deleteSong(self, song_storable: SongStorable):
        favs = loadFavorites()
        song_name = song_storable.name

        reply = QMessageBox.question(
            self._mwindow,
            'Confirm Delete',
            f'Are you sure you want to delete song {song_name} from favorites?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            for folder in favs:
                folder['songs'] = [s for s in folder['songs'] if s.name != song_name]
            saveFavorites()

            curr_folder_name = self.curr_folder['folder_name']
            for folder in favs:
                if folder['folder_name'] == curr_folder_name:
                    self.curr_folder = folder
                    break

            self.refresh()
            InfoBar.success(
                'Song deleted', f'Song {song_name} deleted', parent=self._mwindow
            )

    def replacePlaylist(self):
        self._dp.playlist.clear()
        for song in self.curr_folder['songs']:
            self._dp.playlist.append(song)
        self._dp.playing_manager.refreshRandom()
        self._plp.refreshPlaylistWidget()
        InfoBar.success(
            'Playlist replaced',
            f'Playlist replaced with {self.curr_folder["folder_name"]}',
            parent=self._mwindow,
        )

    def addFolderToPlaylist(self):
        added_count = 0
        for song in self.curr_folder['songs']:
            if not any(s.name == song.name for s in self._dp.playlist):
                self._dp.playlist.append(song)
                self._plp.addSongCardToList(song)
                added_count += 1

        if added_count > 0:
            InfoBar.success(
                'Songs added',
                f'Added {added_count} songs from favorites to playlist',
                parent=self._mwindow,
            )

        self._dp.playing_manager.refreshRandom()

    def _onSongClicked(self, storable: SongStorable, item: QListWidgetItem):
        self.song_viewer.setCurrentItem(item)
        if self._dp:
            self._dp.playStorable(storable)
