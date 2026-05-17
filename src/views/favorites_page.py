from __future__ import annotations

import threading

from core.app_context import AppContext
from imports import (
    PLAYLIST_CHANGED,
    PLAY_STORABLE,
    PushButton,
    QSize,
    Qt,
    QTimer,
    event_bus,
)
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

from core.models import CloudFolderInfo, LocalFolderInfo, SongStorable
from core.favorites import loadFavorites, saveFavorites, favs
from core.backend import get_backend

from views.playing_page import PlayingPage
from views.song_card import CloudFavoriteSongCard, FavoriteSongCard, _SongCardItem


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

        self._song_cards: list[_SongCardItem] = []
        self._lazy_timer = QTimer(self)
        self._lazy_timer.timeout.connect(self._checkVisibleCards)
        self._lazy_timer.start(50)

        self.is_cloud = False
        self.curr_folder: LocalFolderInfo | None = None
        self.curr_cloud_folder: CloudFolderInfo | None = None
        self.curr_cloud_songs: list[SongStorable] = []
        self._cloud_loading = False

    @property
    def _dp(self):
        return self.ctx.dp

    @property
    def _mwindow(self):
        return self.ctx.mwindow

    @property
    def _plp(self):
        return self.ctx.plp

    @property
    def _pm(self):
        return self.ctx.playing_manager

    def _songs(self) -> list[SongStorable]:
        if self.is_cloud:
            return self.curr_cloud_songs
        elif self.curr_folder:
            return self.curr_folder['songs']
        return []

    def setDisplayFolder(self, folder: LocalFolderInfo | CloudFolderInfo):
        if 'id' in folder and 'image_url' in folder and 'songs' not in folder:
            self.is_cloud = True
            self.curr_cloud_folder = folder
            self.curr_folder = None
            self._loadCloudTracks(folder)
        else:
            self.is_cloud = False
            self.curr_folder = folder
            self.curr_cloud_folder = None
            self.curr_cloud_songs = []
            self.refresh()

    def _loadCloudTracks(self, folder: CloudFolderInfo):
        if self._cloud_loading:
            return
        self._cloud_loading = True
        result: list[SongStorable] = []
        folder_id = folder['id']
        mwindow = self._mwindow

        def _fetch():
            nonlocal result
            result = get_backend().get_playlist_tracks(folder_id)
            mwindow.addScheduledTask(_apply)

        def _apply():
            self._cloud_loading = False
            if (
                self.is_cloud
                and self.curr_cloud_folder
                and self.curr_cloud_folder['id'] == folder_id
            ):
                self.curr_cloud_songs = result
                self.refresh()

        threading.Thread(target=_fetch, daemon=True).start()

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
        if not self.is_cloud:
            loadFavorites()

        self._song_cards = []
        self.song_viewer.clear()

        if self.is_cloud and self.curr_cloud_folder:
            self.title_label.setText(self.curr_cloud_folder['folder_name'])
            songs = self.curr_cloud_songs
        elif self.curr_folder:
            self.title_label.setText(self.curr_folder['folder_name'])
            songs = self.curr_folder['songs']
        else:
            return

        for song in songs:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, song)
            item.setSizeHint(QSize(0, 62))

            if self.is_cloud:
                card = CloudFavoriteSongCard(
                    song,
                    self._dp,
                    self._mwindow,
                    self._plp,
                    remove_callback=lambda s=song: self.deleteCloudSong(s),
                    lazy=True,
                )
            else:
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
        if self.is_cloud:
            return
        if not self.curr_folder:
            return
        songs = self.curr_folder['songs']
        try:
            old_index = songs.index(song)
        except ValueError:
            return

        new_index = old_index + delta
        if new_index < 0 or new_index >= len(songs):
            return

        current_song = None
        if 0 <= self._pm.current_index < len(songs):
            current_song = songs[self._pm.current_index]

        songs[old_index], songs[new_index] = (
            songs[new_index],
            songs[old_index],
        )
        if current_song is not None:
            self._pm.current_index = songs.index(current_song)
        self._pm.refreshRandom()

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

            curr_folder_name = (
                self.curr_folder['folder_name'] if self.curr_folder else ''
            )
            for folder in favs:
                if folder['folder_name'] == curr_folder_name:
                    self.curr_folder = folder
                    break

            self.refresh()
            InfoBar.success(
                'Song deleted', f'Song {song_name} deleted', parent=self._mwindow
            )

    def deleteCloudSong(self, song_storable: SongStorable):
        if not self.curr_cloud_folder:
            return
        song_name = song_storable.name
        folder_name = self.curr_cloud_folder['folder_name']

        reply = QMessageBox.question(
            self._mwindow,
            'Confirm Delete',
            f"Are you sure you want to delete song {song_name} from cloud folder '{folder_name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            get_backend().edit_playlist(
                'del', song_storable.id, self.curr_cloud_folder['id']
            )
            self.curr_cloud_songs = [
                s for s in self.curr_cloud_songs if s.id != song_storable.id
            ]
            self.refresh()
            InfoBar.success(
                'Song deleted',
                f'Song {song_name} removed from cloud folder',
                parent=self._mwindow,
            )

    def replacePlaylist(self):
        self._pm.playlist.clear()
        for song in self._songs():
            self._pm.playlist.append(song)
        self._pm.refreshRandom()
        event_bus.emit(PLAYLIST_CHANGED)
        folder_name = (
            self.curr_cloud_folder['folder_name']
            if self.is_cloud and self.curr_cloud_folder
            else self.curr_folder['folder_name']
            if self.curr_folder
            else ''
        )
        InfoBar.success(
            'Playlist replaced',
            f'Playlist replaced with {folder_name}',
            parent=self._mwindow,
        )

    def addFolderToPlaylist(self):
        added_count = 0
        for song in self._songs():
            if not any(s.name == song.name for s in self._pm.playlist):
                self._pm.playlist.append(song)
                added_count += 1

        if added_count > 0:
            event_bus.emit(PLAYLIST_CHANGED)
            InfoBar.success(
                'Songs added',
                f'Added {added_count} songs from favorites to playlist',
                parent=self._mwindow,
            )

        self._pm.refreshRandom()

    def _onSongClicked(self, storable: SongStorable, item: QListWidgetItem):
        self.song_viewer.setCurrentItem(item)
        event_bus.emit(PLAY_STORABLE, storable)
