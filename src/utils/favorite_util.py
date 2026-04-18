
import base64
import json
import os
from utils.base.base_util import FolderInfo, SongInfo, SongStorable
from qfluentwidgets import * # type: ignore
from PySide6.QtWidgets import * # type: ignore

class FavoriteSelectionDialog(MessageBoxBase):
    def __init__(self, parent, favs):
        super().__init__(parent)
        # Title
        self.title_label = SubtitleLabel('Add Songs from Favorites')
        self.viewLayout.addWidget(self.title_label)

        # Horizontal layout for folder list and song list
        content_layout = QHBoxLayout()

        # Left: folder list
        folder_layout = QVBoxLayout()
        folder_layout.addWidget(QLabel('Folders:'))
        self.folder_list = ListWidget()
        self.folder_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        folder_layout.addWidget(self.folder_list)

        # Right: song list
        song_layout = QVBoxLayout()
        song_layout.addWidget(QLabel('Songs:'))
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
            self.folder_list.addItem(folder['folder_name'])

    def onFolderSelected(self, item, favs):
        self.song_list.clear()

        folder_name = item.text()
        for folder in favs:
            if folder['folder_name'] == folder_name:
                for song in folder['songs']:
                    self.song_list.addItem(song.name)
                break

    def getSelectedSong(self, favs):
        '''Return list of selected SongStorable objects'''
        folder_item = self.folder_list.currentItem()
        song_item = self.song_list.currentItem()

        if not folder_item or not song_item:
            return None

        folder_name = folder_item.text()
        song_name = song_item.text()

        for folder in favs:
            if folder['folder_name'] == folder_name:
                for song in folder['songs']:
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
    if not os.path.exists('./favorites.json'):
        with open('./favorites.json', 'w', encoding='utf-8') as f:
            f.write('[]')
        return []
    
    with open('./favorites.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

        result: list[FolderInfo] = []

        for folder in data:
            folder_info = FolderInfo(
                folder_name=folder['folder_name'],
                songs=[
                    SongStorable(
                        info=SongInfo(
                            name=song['name'],
                            artists=song['artists'],
                            id=song['id'],
                            privilege=-1
                        ),
                        image=base64.b64decode(song['image_base64']),
                        music_bin=base64.b64decode(song['content_base64']),
                        lyric=song.get('lyric', song.get('lyric_base64', '')),
                        translated_lyric=song.get('translated_lyric', song.get('translated_lyric_base64', '')),
                        gain=song.get('gain', 1.0)
                    ) for song in folder['songs']
                ]
            )

            result.append(folder_info)

        return result
    
def loadFavoritesWithLaunching(launchwindow) -> list[FolderInfo]:
    if not os.path.exists('./favorites.json'):
        with open('./favorites.json', 'w', encoding='utf-8') as f:
            f.write('[]')
        return []
    
    with open('./favorites.json', 'r', encoding='utf-8') as f:
        launchwindow.setStatusText('Initializing...\n  Loading favorites...\n    Parsing file...', sleep=False)
        data = json.load(f)

        result: list[FolderInfo] = []
        length = len(data)

        for i, folder in enumerate(data):
            launchwindow.setStatusText(f'Initializing...\n  Loading favorites...\n    Parsing file...\n    Loading folder...({i + 1}/{length})')

            songs = []
            songlength = len(folder['songs'])
            for i2, song in enumerate(folder['songs']):
                launchwindow.setStatusText(f'Initializing...\n  Loading favorites...\n    Parsing file...\n    Loading folder...({i + 1}/{length})\n      Loading song...({i2 + 1}/{songlength})', sleep=False)
                songs.append(SongStorable(
                    info=SongInfo(
                        name=song['name'],
                        artists=song['artists'],
                        id=song['id'],
                        privilege=-1
                    ),
                    image=base64.b64decode(song['image_base64']),
                    music_bin=base64.b64decode(song['content_base64']),
                    lyric=song.get('lyric', song.get('lyric_base64', '')),
                    translated_lyric=song.get('translated_lyric', song.get('translated_lyric_base64', '')),
                    gain=song.get('gain', 1.0)
                ) )

            folder_info = FolderInfo(
                folder_name=folder['folder_name'],
                songs=songs
            )

            result.append(folder_info)

        return result
    
def saveFavorites(source: list[FolderInfo]) -> None:
    data = [
        {
            'folder_name': folder['folder_name'],
            'songs': [
                song.toObject() for song in folder['songs']
            ]
        } for folder in source
    ]

    with open('./favorites.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)