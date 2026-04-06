
import base64
import json
import os
from utils.lyrics.base_util import FolderInfo, SongInfo, SongStorable

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