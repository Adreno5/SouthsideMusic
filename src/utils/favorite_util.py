import json
import logging
import os

import shutil
import stat
from utils.base.base_util import (
    DATA_DIR,
    FolderInfo,
    IMAGE_DATA_DIR,
    LEGACY_CACHE_DIR,
    LEGACY_IMAGE_CACHE_DIR,
    LEGACY_MUSIC_CACHE_DIR,
    LYRIC_DATA_DIR,
    MUSIC_DATA_DIR,
    SongStorable,
)
from qfluentwidgets import ListWidget, MessageBoxBase, SubtitleLabel
from imports import QHBoxLayout, QLabel, QListWidget, QVBoxLayout

_logger = logging.getLogger(__name__)


_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_FAVORITES_PATH = os.path.join(_PROJECT_ROOT, "favorites.json")

favs: list[FolderInfo] = []


def _ensure_favorite_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(MUSIC_DATA_DIR, exist_ok=True)
    os.makedirs(IMAGE_DATA_DIR, exist_ok=True)
    os.makedirs(LYRIC_DATA_DIR, exist_ok=True)


def _to_favorite_song_object(song: SongStorable):
    return song.toObject()


def _write_favorites_data(data: list[dict]) -> None:
    if os.path.exists(_FAVORITES_PATH):
        os.chmod(_FAVORITES_PATH, stat.S_IWRITE)
    with open(_FAVORITES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def restoreOldFavoritesFormat() -> None:
    _ensure_favorite_dirs()

    if os.path.exists(LEGACY_MUSIC_CACHE_DIR):
        for item in os.listdir(LEGACY_MUSIC_CACHE_DIR):
            src = os.path.join(LEGACY_MUSIC_CACHE_DIR, item)
            dst = os.path.join(MUSIC_DATA_DIR, item)
            if os.path.isfile(src) and not os.path.exists(dst):
                shutil.move(src, dst)

    if os.path.exists(LEGACY_IMAGE_CACHE_DIR):
        for item in os.listdir(LEGACY_IMAGE_CACHE_DIR):
            src = os.path.join(LEGACY_IMAGE_CACHE_DIR, item)
            dst = os.path.join(IMAGE_DATA_DIR, item)
            if os.path.isfile(src) and not os.path.exists(dst):
                shutil.move(src, dst)

    if os.path.exists(LEGACY_CACHE_DIR):
        for root, dirs, files in os.walk(LEGACY_CACHE_DIR, topdown=False):
            for file in files:
                path = os.path.join(root, file)
                if os.path.exists(path):
                    os.remove(path)
            for directory in dirs:
                path = os.path.join(root, directory)
                if os.path.isdir(path):
                    os.rmdir(path)
        if os.path.isdir(LEGACY_CACHE_DIR):
            os.rmdir(LEGACY_CACHE_DIR)

    if not os.path.exists(_FAVORITES_PATH):
        with open(_FAVORITES_PATH, "w", encoding="utf-8") as f:
            f.write("[]")
        return

    with open(_FAVORITES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    data_dirty = False
    normalized_data: list[dict] = []

    for folder in data:
        normalized_songs = []
        for song in folder["songs"]:
            storable = SongStorable.fromObject(song)
            had_embedded_data = bool(
                song.get("image_base64") or song.get("content_base64")
            )
            normalized_songs.append(_to_favorite_song_object(storable))
            data_dirty = data_dirty or had_embedded_data

        normalized_data.append(
            {"folder_name": folder["folder_name"], "songs": normalized_songs}
        )

    if data_dirty:
        _write_favorites_data(normalized_data)


class FavoriteSelectionDialog(MessageBoxBase):
    def __init__(self, parent, favs):
        super().__init__(parent)
        # Title
        self.title_label = SubtitleLabel("Add Songs from Favorites")
        self.viewLayout.addWidget(self.title_label)

        # Horizontal layout for folder list and song list
        content_layout = QHBoxLayout()

        # Left: folder list
        folder_layout = QVBoxLayout()
        folder_layout.addWidget(QLabel("Folders:"))
        self.folder_list = ListWidget()
        self.folder_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        folder_layout.addWidget(self.folder_list)

        # Right: song list
        song_layout = QVBoxLayout()
        song_layout.addWidget(QLabel("Songs:"))
        self.song_list = ListWidget()
        self.song_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        song_layout.addWidget(self.song_list)

        content_layout.addLayout(folder_layout)
        content_layout.addLayout(song_layout)

        self.viewLayout.addLayout(content_layout)

        # Load folders
        self.loadFolders(favs)

        # Connect signals
        self.folder_list.itemClicked.connect(self.onFolderSelected)

    def loadFolders(self, favs):
        self.folder_list.clear()
        self.song_list.clear()

        for folder in favs:
            self.folder_list.addItem(folder["folder_name"])

    def onFolderSelected(self, item, favs):
        self.song_list.clear()

        folder_name = item.text()
        for folder in favs:
            if folder["folder_name"] == folder_name:
                for song in folder["songs"]:
                    self.song_list.addItem(song.name)
                break

    def getSelectedSong(self, favs):
        """Return list of selected SongStorable objects"""
        folder_item = self.folder_list.currentItem()
        song_item = self.song_list.currentItem()

        if not folder_item or not song_item:
            return None

        folder_name = folder_item.text()
        song_name = song_item.text()

        for folder in favs:
            if folder["folder_name"] == folder_name:
                for song in folder["songs"]:
                    if song.name == song_name:
                        return song

        return None


def getFavoriteSong(mwindow, favs) -> SongStorable | None:
    box = FavoriteSelectionDialog(mwindow, favs)
    reply = box.exec()
    selected = box.getSelectedSong(favs)

    if reply and selected:
        return selected
    else:
        return None


def loadFavorites() -> list[FolderInfo]:
    global favs
    restoreOldFavoritesFormat()

    with open(_FAVORITES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

        favs.clear()
        for folder in data:
            songs = []
            for song in folder["songs"]:
                storable = SongStorable.fromObject(song)
                songs.append(storable)

            folder_info = FolderInfo(folder_name=folder["folder_name"], songs=songs)
            favs.append(folder_info)
    return favs


def saveFavorites() -> None:
    global favs

    if not favs:
        _logger.warning("[favorites] saveFavorites called with EMPTY favs! skipping")
        return

    _ensure_favorite_dirs()
    data: list[dict] = []

    for folder in favs:
        songs = []
        for song in folder["songs"]:
            songs.append(_to_favorite_song_object(song))

        data.append({"folder_name": folder["folder_name"], "songs": songs})

    _write_favorites_data(data)
