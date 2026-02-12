from abc import ABC, abstractmethod
from typing import TypedDict
import base64

class SongInfo(TypedDict):
    name: str
    artists: str
    id: str
    privilege: int

class SongDetail(TypedDict):
    image_url: str

class SongStorable:
    class SongStorableDict(TypedDict):
        name: str
        artists: str
        id: str
        image_base64: str
        content_base64: str
        lyric: str
        translated_lyric: str
        gain: float

    name: str
    artists: str
    id: str
    image_base64: str
    content_base64: str
    lyric: str
    translated_lyric: str

    loudness_gain: float

    def __init__(self, info: SongInfo, image: bytes, music_bin: bytes, lyric: str = '', translated_lyric: str = '', gain: float=1.0) -> None:
        self.name = info['name']
        self.artists = info['artists']
        self.id = info['id']
        self.image_base64 = base64.b64encode(image).decode()
        self.content_base64 = base64.b64encode(music_bin).decode()
        self.lyric = lyric
        self.translated_lyric = translated_lyric
        self.loudness_gain = gain

    def toObject(self) -> SongStorableDict:
        return {
            'name': self.name,
            'artists': self.artists,
            'id': self.id,
            'image_base64': self.image_base64,
            'content_base64': self.content_base64,
            'lyric': self.lyric,
            'translated_lyric': self.translated_lyric,
            'gain': self.loudness_gain
        }
    
    @staticmethod
    def fromObject(obj: SongStorableDict) -> 'SongStorable':
        return SongStorable(
            info={
                'name': obj['name'],
                'artists': obj['artists'],
                'id': obj['id'],
                'privilege': -1
            },
            image=base64.b64decode(obj['image_base64']),
            music_bin=base64.b64decode(obj['content_base64']),
            lyric=obj['lyric'],
            gain=obj.get('gain', 1.0)
        )

class FolderInfo(TypedDict):
    folder_name: str
    songs: list[SongStorable]

class BaseLyricUtil(ABC):
    @abstractmethod
    def init(self) -> None: ...
    @abstractmethod
    def search(self, keyword: str) -> list[SongInfo]: ...