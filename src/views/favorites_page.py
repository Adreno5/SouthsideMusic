from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
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
    ListWidget,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    TitleLabel,
)

from utils.base.base_util import FolderInfo, SongStorable
from utils.favorite_util import loadFavorites, saveFavorites, favs
from utils.icon_util import bindIcon
from views.song_card import FavoriteSongCard


class FavoritesPage(QWidget):
    def __init__(self, dp, sidebar, mwindow, launchwindow=None) -> None:
        super().__init__()
        lw = launchwindow
        if lw:
            lw.top("Initializing favorites page...")
        self._dp = dp
        self._sidebar = sidebar
        self._mwindow = mwindow
        self.setObjectName("favorites_page")

        if lw:
            lw.top("  building toolbar")
        global_layout = QVBoxLayout(self)
        top_layout = FlowLayout()
        top_layout.addWidget(TitleLabel("Favorites"))
        self.refresh_btn = PrimaryPushButton(FluentIcon.SYNC, "Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        top_layout.addWidget(self.refresh_btn)
        self.newfolder_btn = PushButton(FluentIcon.ADD, "New Folder")
        self.newfolder_btn.clicked.connect(self.newFolder)
        top_layout.addWidget(self.newfolder_btn)
        self.deletefolder_btn = PushButton(FluentIcon.DELETE, "Delete Folder")
        self.deletefolder_btn.clicked.connect(self.deleteFolder)
        top_layout.addWidget(self.deletefolder_btn)
        self.renamefolder_btn = PushButton(FluentIcon.EDIT, "Rename Folder")
        self.renamefolder_btn.clicked.connect(self.renameFolder)
        top_layout.addWidget(self.renamefolder_btn)
        global_layout.addLayout(top_layout)

        if lw:
            lw.top("  setting up folder selector")
        bottom_layout = QHBoxLayout()
        left_layout = QVBoxLayout()
        self.folder_selector = ListWidget()
        self.folder_selector.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.folder_selector.itemClicked.connect(self.viewSongs)
        left_layout.addWidget(self.folder_selector, 1)
        self.addplaylist_btn = PushButton("Add selected folder to playlist")
        bindIcon(self.addplaylist_btn, "pl")
        self.addplaylist_btn.clicked.connect(self.addFolderToPlaylist)
        left_layout.addWidget(self.addplaylist_btn)
        self.addall_btn = PrimaryPushButton("Add all folder to playlist")
        bindIcon(self.addall_btn, "pl", "light")
        self.addall_btn.clicked.connect(self.addAllToPlaylist)
        left_layout.addWidget(self.addall_btn)
        bottom_layout.addLayout(left_layout, 3)
        bottom_layout.addWidget(QLabel(">"), alignment=Qt.AlignmentFlag.AlignVCenter)

        if lw:
            lw.top("  setting up song viewer")
        right_layout = QVBoxLayout()
        self.song_viewer = ListWidget()
        self.song_viewer.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        right_layout.addWidget(self.song_viewer, 1)
        bottom_layout.addLayout(right_layout, 7)
        global_layout.addLayout(bottom_layout)
        self.setLayout(global_layout)

        self._song_cards: list[FavoriteSongCard] = []
        self._lazy_timer = QTimer(self)
        self._lazy_timer.timeout.connect(self._checkVisibleCards)
        self._lazy_timer.start(50)

    def renameFolder(self):
        from utils.dialog_util import get_text_lineedit

        got = get_text_lineedit(
            "Rename Folder",
            "Enter new folder name:",
            self.folder_selector.selectedItems()[0].text(),
            self._mwindow,
        )

        if got:
            favs = self._get_favs()
            for i, folder in enumerate(favs):
                if (
                    folder["folder_name"]
                    == self.folder_selector.selectedItems()[0].text()
                ):
                    favs[i]["folder_name"] = got
                    break
            saveFavorites()
            self.refresh()

    def _get_favs(self):
        return favs

    def viewSongs(self, i: QListWidgetItem):
        self._song_cards = []
        favs = loadFavorites()
        self.song_viewer.clear()

        for f in favs:
            if i.text() == f["folder_name"]:
                folder_name = i.text()
                songs = f["songs"]
                for song in songs:
                    item = QListWidgetItem()
                    item.setData(Qt.ItemDataRole.UserRole, song)
                    item.setSizeHint(QSize(0, 62))
                    card = FavoriteSongCard(
                        song,
                        self._dp,
                        self._mwindow,
                        self._sidebar,
                        remove_callback=lambda s=song, fn=folder_name: self.deleteSong(
                            fn, s
                        ),
                        move_callback=lambda d, s=song, fn=folder_name: self.moveSong(
                            fn, s, d
                        ),
                        lazy=True,
                    )
                    card.clicked.connect(
                        lambda s, it=item: self.song_viewer.setCurrentItem(it)
                    )
                    self.song_viewer.addItem(item)
                    self.song_viewer.setItemWidget(item, card)
                    self._song_cards.append(card)

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

    def moveSong(self, folder_name: str, song_storable: SongStorable, delta: int):
        favs = self._get_favs()
        for folder in favs:
            if folder["folder_name"] != folder_name:
                continue

            songs = folder["songs"]
            try:
                old_index = songs.index(song_storable)
            except ValueError:
                return

            new_index = old_index + delta
            if new_index < 0 or new_index >= len(songs):
                return

            songs[old_index], songs[new_index] = songs[new_index], songs[old_index]
            saveFavorites()
            selected = self.folder_selector.currentItem()
            if selected:
                self.viewSongs(selected)
                self.song_viewer.setCurrentRow(new_index)
            return

    def newFolder(self):
        from utils.base.base_util import FolderInfo

        favs = loadFavorites()

        name, ok = QInputDialog.getText(
            self._mwindow, "New Folder", "Enter folder name:"
        )
        if ok and name:
            if not name.strip():
                InfoBar.warning(
                    "Invalid name", "Folder name cannot be empty", parent=self._mwindow
                )
                return
            for folder in favs:
                if folder["folder_name"] == name:
                    InfoBar.warning(
                        "Duplicate", "Folder already exists", parent=self._mwindow
                    )
                    return
            favs.append(FolderInfo(folder_name=name, songs=[]))
            saveFavorites()
            self.refresh()
            InfoBar.success(
                "Folder created", f"Folder {name} created", parent=self._mwindow
            )

    def deleteFolder(self):
        favs = loadFavorites()
        selected = self.folder_selector.currentItem()
        if not selected:
            InfoBar.warning(
                "No selection", "Please select a folder to delete", parent=self._mwindow
            )
            return

        folder_name = selected.text()
        reply = QMessageBox.question(
            self._mwindow,
            "Confirm Delete",
            f"Are you sure you want to delete folder {folder_name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            favs = [f for f in favs if f["folder_name"] != folder_name]
            saveFavorites()
            self.refresh()
            InfoBar.success(
                "Folder deleted", f"Folder {folder_name} deleted", parent=self._mwindow
            )

    def deleteSong(self, folder_name: str, song_storable: SongStorable):
        favs = loadFavorites()
        song_name = song_storable.name

        reply = QMessageBox.question(
            self._mwindow,
            "Confirm Delete",
            f"Are you sure you want to delete song {song_name} from folder {folder_name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            for folder in favs:
                if folder["folder_name"] == folder_name:
                    folder["songs"] = [
                        s for s in folder["songs"] if s.name != song_name
                    ]
                    break
            saveFavorites()
            selected = self.folder_selector.currentItem()
            if selected:
                self.viewSongs(selected)
            InfoBar.success(
                "Song deleted", f"Song {song_name} deleted", parent=self._mwindow
            )

    def addFolderToPlaylist(self):
        favs = loadFavorites()
        selected_folder = self.folder_selector.currentItem()
        if not selected_folder:
            InfoBar.warning(
                "No folder selected",
                "Please select a folder first",
                parent=self._mwindow,
            )
            return

        folder_name = selected_folder.text()

        target_folder = None
        for folder in favs:
            if folder["folder_name"] == folder_name:
                target_folder = folder
                break

        if not target_folder or not target_folder["songs"]:
            InfoBar.warning(
                "Empty folder", f"Folder {folder_name} is empty", parent=self._mwindow
            )
            return

        if not self._dp:
            InfoBar.error(
                "Playlist not available",
                "Playlist page not initialized",
                parent=self._mwindow,
            )
            return

        added_count = 0
        for song in target_folder["songs"]:
            if not any(s.name == song.name for s in self._dp.playlist):
                self._dp.playlist.append(song)
                self._sidebar.addSongCardToList(song)
                added_count += 1

        if added_count > 0:
            InfoBar.success(
                "Songs added",
                f"Added {added_count} songs from folder {folder_name} to playlist",
                parent=self._mwindow,
            )
        else:
            InfoBar.info(
                "No new songs",
                f"All songs from folder {folder_name} already in playlist",
                parent=self._mwindow,
            )

        self._dp.song_randomer.init(self._dp.playlist)

    def addAllToPlaylist(self):
        favs = loadFavorites()

        for folder in favs:
            for song in folder["songs"]:
                if not any(s.name == song.name for s in self._dp.playlist):
                    self._dp.playlist.append(song)
                    self._sidebar.addSongCardToList(song)
        InfoBar.success(
            "Songs added",
            "Added all songs from favorites to playlist",
            parent=self._mwindow,
        )

        self._dp.song_randomer.init(self._dp.playlist)

    def refresh(self):
        favs = loadFavorites()

        self.folder_selector.clear()
        self.song_viewer.clear()

        for folder in favs:
            self.folder_selector.addItem(folder["folder_name"])
