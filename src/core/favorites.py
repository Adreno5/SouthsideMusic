import json
import logging
import os
import shutil
import stat
import threading

from core.models import (
    DATA_DIR,
    LocalFolderInfo,
    IMAGE_DATA_DIR,
    LEGACY_CACHE_DIR,
    LEGACY_IMAGE_CACHE_DIR,
    LEGACY_MUSIC_CACHE_DIR,
    LYRIC_DATA_DIR,
    MUSIC_DATA_DIR,
    SongStorable,
)
from qfluentwidgets import MessageBoxBase, SubtitleLabel
from views.list_widget import SListWidget
from imports import QHBoxLayout, QLabel, QListWidget, QVBoxLayout

_logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_FAVORITES_PATH = os.path.join(_PROJECT_ROOT, 'favorites.json')


class FavoritesManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.folders: list[LocalFolderInfo] = []

    def load(self) -> None:
        _ensure_dirs()
        raw = _restore_old_format()

        folders: list[LocalFolderInfo] = []
        for folder in raw:
            songs: list[SongStorable] = []
            for song_obj in folder.get('songs', []):
                try:
                    storable = SongStorable.fromObject(song_obj)
                except Exception:
                    _logger.exception(
                        'Failed to restore song in folder \'%s\'', folder['folder_name']
                    )
                    continue
                songs.append(storable)
            folders.append(
                LocalFolderInfo(folder_name=folder['folder_name'], songs=songs)
            )

        with self._lock:
            self.folders.clear()
            self.folders.extend(folders)

    def _save(self) -> None:
        with self._lock:
            data: list[dict] = []
            for folder in self.folders:
                songs = [song.toObject() for song in folder.songs]
                data.append({'folder_name': folder.folder_name, 'songs': songs})
            _write_raw(data)

    def addFolder(self, folder_name: str) -> LocalFolderInfo:
        with self._lock:
            folder = LocalFolderInfo(folder_name=folder_name, songs=[])
            self.folders.append(folder)
            self._save()
            return folder

    def removeFolder(self, folder_name: str) -> bool:
        with self._lock:
            for i, f in enumerate(self.folders):
                if f.folder_name == folder_name:
                    self.folders.pop(i)
                    self._save()
                    return True
        return False

    def renameFolder(self, old_name: str, new_name: str) -> bool:
        with self._lock:
            for f in self.folders:
                if f.folder_name == old_name:
                    f.folder_name = new_name
                    self._save()
                    return True
        return False

    def addSong(self, folder_name: str, song: SongStorable) -> bool:
        with self._lock:
            for f in self.folders:
                if f.folder_name == folder_name:
                    f.songs.insert(0, song)
                    self._save()
                    return True
        return False

    def removeSong(self, song_name: str) -> None:
        with self._lock:
            changed = False
            for f in self.folders:
                before = len(f.songs)
                f.songs = [s for s in f.songs if s.name != song_name]
                if len(f.songs) < before:
                    changed = True
            if changed:
                self._save()

    def moveSong(self, folder_name: str, song: SongStorable, delta: int) -> bool:
        with self._lock:
            for f in self.folders:
                if f.folder_name != folder_name:
                    continue
                try:
                    idx = f.songs.index(song)
                except ValueError:
                    return False
                new_idx = idx + delta
                if 0 <= new_idx < len(f.songs):
                    f.songs[idx], f.songs[new_idx] = (
                        f.songs[new_idx],
                        f.songs[idx],
                    )
                    self._save()
                    return True
        return False

    def updateSongInFolder(
        self,
        folder_name: str,
        song_id: str,
        target_lufs: int,
        volume: float,
        play_speed: float,
        stereo: bool,
    ) -> bool:
        with self._lock:
            for f in self.folders:
                if f.folder_name != folder_name:
                    continue
                for s in f.songs:
                    if str(s.id) == song_id:
                        s.target_lufs = target_lufs
                        self._save()
                        return True
        return False


favorites_manager = FavoritesManager()


def loadFavorites() -> list[LocalFolderInfo]:
    favorites_manager.load()
    return favorites_manager.folders


def saveFavorites() -> None:
    if favorites_manager.folders:
        favorites_manager._save()


def _ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(MUSIC_DATA_DIR, exist_ok=True)
    os.makedirs(IMAGE_DATA_DIR, exist_ok=True)
    os.makedirs(LYRIC_DATA_DIR, exist_ok=True)


def _restore_old_format() -> list[dict]:
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
        return []

    with open(_FAVORITES_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)

    data_dirty = False
    normalized: list[dict] = []

    for folder in data:
        songs: list[dict] = []
        for song_obj in folder.get('songs', []):
            storable = SongStorable.fromObject(song_obj)
            had_embedded = bool(
                song_obj.get('image_base64') or song_obj.get('content_base64')
            )
            songs.append(storable.toObject())  # type: ignore
            data_dirty = data_dirty or had_embedded
        normalized.append({'folder_name': folder['folder_name'], 'songs': songs})

    if data_dirty:
        _write_raw(normalized)

    return normalized


def _write_raw(data: list[dict]) -> None:
    if os.path.exists(_FAVORITES_PATH):
        os.chmod(_FAVORITES_PATH, stat.S_IWRITE)
    with open(_FAVORITES_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


class FavoriteSelectionDialog(MessageBoxBase):
    def __init__(self, parent, favs):
        super().__init__(parent)
        self.title_label = SubtitleLabel('Add Songs from Favorites')
        self.viewLayout.addWidget(self.title_label)

        content_layout = QHBoxLayout()

        folder_layout = QVBoxLayout()
        folder_layout.addWidget(QLabel('Folders:'))
        self.folder_list = SListWidget()
        self.folder_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        folder_layout.addWidget(self.folder_list)

        song_layout = QVBoxLayout()
        song_layout.addWidget(QLabel('Songs:'))
        self.song_list = SListWidget()
        self.song_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        song_layout.addWidget(self.song_list)

        content_layout.addLayout(folder_layout)
        content_layout.addLayout(song_layout)
        self.viewLayout.addLayout(content_layout)

        self.loadFolders(favs)
        self.folder_list.itemClicked.connect(self.onFolderSelected)

    def loadFolders(self, favs):
        self.folder_list.clear()
        self.song_list.clear()
        for folder in favs:
            self.folder_list.addItem(folder.folder_name)

    def onFolderSelected(self, item, favs):
        self.song_list.clear()
        folder_name = item.text()
        for folder in favs:
            if folder.folder_name == folder_name:
                for song in folder.songs:
                    self.song_list.addItem(song.name)
                break

    def getSelectedSong(self, favs):
        folder_item = self.folder_list.currentItem()
        song_item = self.song_list.currentItem()

        if not folder_item or not song_item:
            return None

        folder_name = folder_item.text()
        song_name = song_item.text()

        for folder in favs:
            if folder.folder_name == folder_name:
                for song in folder.songs:
                    if song.name == song_name:
                        return song

        return None


def getFavoriteSong(mwindow, favs) -> SongStorable | None:
    box = FavoriteSelectionDialog(mwindow, favs)
    reply = box.exec()
    selected = box.getSelectedSong(favs)
    if reply and selected:
        return selected
    return None
