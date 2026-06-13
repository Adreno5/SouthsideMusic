from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal
import base64
import hashlib
import json
import os
import shutil

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
DATA_DIR = os.path.join(_PROJECT_ROOT, 'data')
MUSIC_DATA_DIR = os.path.join(DATA_DIR, 'music')
IMAGE_DATA_DIR = os.path.join(DATA_DIR, 'image')
LYRIC_DATA_DIR = os.path.join(DATA_DIR, 'lyrics')
LEGACY_CACHE_DIR = os.path.join(_PROJECT_ROOT, 'cache')
LEGACY_MUSIC_CACHE_DIR = os.path.join(LEGACY_CACHE_DIR, 'music')
LEGACY_IMAGE_CACHE_DIR = os.path.join(LEGACY_CACHE_DIR, 'image')

_CACHE_INDEX_PATH = os.path.join(DATA_DIR, 'cache_index.json')
_cache_index: dict[str, dict[str, str]] = {}
_cache_index_loaded: bool = False


def _load_cache_index() -> dict[str, dict[str, str]]:
    global _cache_index, _cache_index_loaded
    if _cache_index_loaded:
        return _cache_index
    _cache_index_loaded = True
    if not os.path.exists(_CACHE_INDEX_PATH):
        return _cache_index
    with open(_CACHE_INDEX_PATH, 'r', encoding='utf-8') as f:
        loaded = json.load(f)
    if isinstance(loaded, dict):
        _cache_index = loaded
    return _cache_index


def _save_cache_index() -> None:
    global _cache_index
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(_CACHE_INDEX_PATH, 'w', encoding='utf-8') as f:
        json.dump(_cache_index or {}, f, ensure_ascii=False, indent=2)


def _update_cache_index(
    song_id: str, image_hash: str = '', audio_hash: str = ''
) -> None:
    global _cache_index
    idx = _load_cache_index()
    entry = idx.get(song_id, {})
    if image_hash:
        entry['image_cache_hash'] = image_hash
    if audio_hash:
        entry['content_cache_hash'] = audio_hash
    if entry:
        idx[song_id] = entry
        _save_cache_index()


def getCachedHashes(song_id: str) -> dict[str, str]:
    return _load_cache_index().get(song_id, {})


@dataclass
class SongInfo:
    name: str
    artists: str
    id: str
    privilege: int


@dataclass
class SongDetail:
    image_url: str


class SongStorable:
    name: str
    artists: str
    id: str
    loudness_gain: float
    target_lufs: int
    image_cache_hash: str = ''
    content_cache_hash: str = ''
    lyric_cache_hash: str = ''
    loggedin_when_download: bool = False
    viptype_when_download: int = 0

    def __init__(
        self,
        info: SongInfo,
        image: bytes | None = None,
        music_bin: bytes | None = None,
        lyric: str = '',
        translated_lyric: str = '',
        yrc_lyric: str = '',
        gain: float = 1.0,
        target_lufs: int = -16,
        image_cache_hash: str = '',
        content_cache_hash: str = '',
        lyric_cache_hash: str = '',
        loggedin_when_download: bool = False,
        viptype_when_download: int = 0,
    ) -> None:
        self.name = info.name
        self.artists = info.artists
        self.id = info.id

        if isinstance(image, bytes):
            self._write_cache(image, IMAGE_DATA_DIR, 'image_cache_hash')
        else:
            self.image_cache_hash = image_cache_hash

        if isinstance(music_bin, bytes):
            self._write_cache(music_bin, MUSIC_DATA_DIR, 'content_cache_hash')
        else:
            self.content_cache_hash = content_cache_hash

        self.lyric_cache_hash = lyric_cache_hash
        if lyric or translated_lyric or yrc_lyric:
            self.write_lyrics(lyric, translated_lyric, yrc_lyric)
        self.loudness_gain = gain
        self.target_lufs = target_lufs
        self.loggedin_when_download = loggedin_when_download
        self.viptype_when_download = viptype_when_download

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SongStorable):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)

    def _write_cache(self, data: bytes, cache_dir: str, hash_attr: str) -> str:
        os.makedirs(cache_dir, exist_ok=True)
        cache_hash = hashlib.sha256(data).hexdigest()
        cache_path = os.path.join(cache_dir, cache_hash)
        if not os.path.exists(cache_path):
            with open(cache_path, 'wb') as f:
                f.write(data)
        setattr(self, hash_attr, cache_hash)
        if self.id:
            if hash_attr == 'image_cache_hash':
                _update_cache_index(self.id, image_hash=cache_hash)
            elif hash_attr == 'content_cache_hash':
                _update_cache_index(self.id, audio_hash=cache_hash)
        return cache_hash

    @staticmethod
    def _ensure_cache_dirs() -> None:
        os.makedirs(MUSIC_DATA_DIR, exist_ok=True)
        os.makedirs(IMAGE_DATA_DIR, exist_ok=True)
        os.makedirs(LYRIC_DATA_DIR, exist_ok=True)

    @staticmethod
    def _get_cache_path(cache_dir: str, cache_hash: str) -> str:
        return os.path.join(cache_dir, cache_hash)

    @staticmethod
    def _get_legacy_cache_path(cache_dir: str, cache_hash: str) -> str:
        legacy_dir = (
            LEGACY_IMAGE_CACHE_DIR
            if cache_dir == IMAGE_DATA_DIR
            else LEGACY_MUSIC_CACHE_DIR
        )
        return os.path.join(legacy_dir, cache_hash)

    def _read_cache(self, cache_hash: str, cache_dir: str) -> bytes | None:
        if not cache_hash:
            return None
        cache_path = self._get_cache_path(cache_dir, cache_hash)
        if os.path.exists(cache_path):
            with open(cache_path, 'rb') as f:
                return f.read()
        legacy_path = self._get_legacy_cache_path(cache_dir, cache_hash)
        if os.path.exists(legacy_path):
            shutil.move(legacy_path, cache_path)
            with open(cache_path, 'rb') as f:
                return f.read()
        return None

    def image_cached(self) -> bool:
        self._ensure_cache_fields()
        return bool(self.image_cache_hash) and os.path.exists(
            self._get_cache_path(IMAGE_DATA_DIR, self.image_cache_hash)
        )

    def audio_cached(self, logged_in: bool, vip_type: int) -> bool:
        self._ensure_cache_fields()
        if (
            logged_in != self.loggedin_when_download
            or vip_type != self.viptype_when_download
        ):
            self.loggedin_when_download = logged_in
            self.viptype_when_download = vip_type
            return False
        return bool(self.content_cache_hash) and os.path.exists(
            self._get_cache_path(MUSIC_DATA_DIR, self.content_cache_hash)
        )

    def cache_image(self, data: bytes) -> str:
        return self._write_cache(data, IMAGE_DATA_DIR, 'image_cache_hash')

    def cache_audio(self, data: bytes) -> str:
        return self._write_cache(data, MUSIC_DATA_DIR, 'content_cache_hash')

    def _ensure_cache_fields(self) -> None:
        if not hasattr(self, 'image_cache_hash'):
            self.image_cache_hash = ''
        if not hasattr(self, 'content_cache_hash'):
            self.content_cache_hash = ''
        if not hasattr(self, 'lyric_cache_hash'):
            self.lyric_cache_hash = ''
            lyric = self.__dict__.get('lyric', '')
            translated_lyric = self.__dict__.get('translated_lyric', '')
            yrc_lyric = self.__dict__.get('yrc_lyric', '')
            if lyric or translated_lyric or yrc_lyric:
                self.write_lyrics(lyric, translated_lyric, yrc_lyric)
        self.__dict__.pop('lyric', None)
        self.__dict__.pop('translated_lyric', None)
        self.__dict__.pop('yrc_lyric', None)

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self._ensure_cache_fields()

    def get_image_bytes(self) -> bytes:
        self._ensure_cache_fields()
        result = self._read_cache(self.image_cache_hash, IMAGE_DATA_DIR)
        if result is not None:
            return result
        raise FileNotFoundError(
            f'Image cache not found for {self.name}: hash={self.image_cache_hash}'
        )

    def get_music_bytes(self) -> bytes:
        self._ensure_cache_fields()
        result = self._read_cache(self.content_cache_hash, MUSIC_DATA_DIR)
        if result is not None:
            return result
        raise FileNotFoundError(
            f'Music cache not found for {self.name}: hash={self.content_cache_hash}'
        )

    def get_lyric_path(self) -> str:
        self._ensure_cache_fields()
        cache_name = self.lyric_cache_hash or f'{self.id}.json'
        return os.path.join(LYRIC_DATA_DIR, cache_name)

    def get_lyrics(self) -> dict[str, str]:
        self._ensure_cache_fields()
        path = self.get_lyric_path()
        if not os.path.exists(path):
            return {
                'lyric': '',
                'translated_lyric': '',
                'yrc_lyric': '',
                'ytlrc_lyric': '',
            }
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return {
            'lyric': data.get('lyric', ''),
            'translated_lyric': data.get('translated_lyric', ''),
            'yrc_lyric': data.get('yrc_lyric', ''),
            'ytlrc_lyric': data.get('ytlrc_lyric', ''),
        }

    @property
    def lyric(self) -> str:
        return self.get_lyrics()['lyric']

    @property
    def translated_lyric(self) -> str:
        return self.get_lyrics()['translated_lyric']

    @property
    def yrc_lyric(self) -> str:
        return self.get_lyrics()['yrc_lyric']

    def write_lyrics(
        self,
        lyric: str = '',
        translated_lyric: str = '',
        yrc_lyric: str = '',
        ytlrc_lyric: str = '',
    ) -> None:
        os.makedirs(LYRIC_DATA_DIR, exist_ok=True)
        self.lyric_cache_hash = f'{self.id}.json'
        path = self.get_lyric_path()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(
                {
                    'lyric': lyric,
                    'translated_lyric': translated_lyric,
                    'yrc_lyric': yrc_lyric,
                    'ytlrc_lyric': ytlrc_lyric,
                    'has_yrc_lyric': bool(yrc_lyric),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

    def lyrics_missing(self) -> bool:
        self._ensure_cache_fields()
        return not self.lyric_cache_hash or not os.path.exists(self.get_lyric_path())

    def yrc_lyrics_missing(self) -> bool:
        self._ensure_cache_fields()
        if self.lyrics_missing():
            return True
        try:
            with open(self.get_lyric_path(), 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return True
        return not data.get('has_yrc_lyric')

    def ytlrc_missing(self) -> bool:
        self._ensure_cache_fields()
        if self.lyrics_missing():
            return True
        try:
            with open(self.get_lyric_path(), 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return True
        return not data.get('ytlrc_lyric')
        self._ensure_cache_fields()
        if self.lyrics_missing():
            return True
        try:
            with open(self.get_lyric_path(), 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return True
        return not data.get('has_yrc_lyric')

    def ensure_cached_assets(self, logged_in: bool, vip_type: int) -> bool:
        self._ensure_cache_fields()
        return not (self.image_cached() and self.audio_cached(logged_in, vip_type))

    def toObject(self) -> dict[str, object]:
        return {
            'name': self.name,
            'artists': self.artists,
            'id': self.id,
            'image_cache_hash': self.image_cache_hash,
            'content_cache_hash': self.content_cache_hash,
            'lyric_cache_hash': self.lyric_cache_hash,
            'gain': self.loudness_gain,
            'target_lufs': self.target_lufs,
            'loggedin_when_download': self.loggedin_when_download,
            'viptype_when_download': self.viptype_when_download,
        }

    @staticmethod
    def fromObject(obj: dict[str, object]) -> 'SongStorable':
        image_bytes = None
        music_bytes = None
        image_cache_hash: str = obj.get('image_cache_hash', '')  # type: ignore[assignment]
        content_cache_hash: str = obj.get('content_cache_hash', '')  # type: ignore[assignment]
        lyric_cache_hash: str = obj.get('lyric_cache_hash', '')  # type: ignore[assignment]

        old_image_b64 = obj.get('image_base64')
        old_content_b64 = obj.get('content_base64')

        if old_image_b64:
            assert isinstance(old_image_b64, str)
            image_bytes = base64.b64decode(old_image_b64)
            image_cache_hash = ''
        if old_content_b64:
            assert isinstance(old_content_b64, str)
            music_bytes = base64.b64decode(old_content_b64)
            content_cache_hash = ''

        return SongStorable(
            info=SongInfo(
                name=str(obj.get('name', '')),
                artists=str(obj.get('artists', '')),
                id=str(obj.get('id', '')),
                privilege=-1,
            ),
            image=image_bytes,
            music_bin=music_bytes,
            image_cache_hash=image_cache_hash,
            content_cache_hash=content_cache_hash,
            lyric=str(obj.get('lyric', '')),
            translated_lyric=str(obj.get('translated_lyric', '')),
            yrc_lyric=str(obj.get('yrc_lyric', '')),
            lyric_cache_hash=lyric_cache_hash,
            gain=float(obj.get('gain', 1.0)),  # type: ignore[arg-type]
            target_lufs=int(obj.get('target_lufs', -16)),  # type: ignore[arg-type]
            loggedin_when_download=bool(obj.get('loggedin_when_download', False)),
            viptype_when_download=int(obj.get('viptype_when_download', 0)),  # type: ignore[arg-type]
        )


@dataclass
class LocalFolderInfo:
    folder_name: str
    songs: list[SongStorable]


@dataclass
class CloudFolderInfo:
    folder_name: str
    image_url: str
    id: str


@dataclass
class SearchCloudFolderInfo:
    folder_name: str
    image_url: str
    id: str
    author: str


@dataclass
class ArtistInfo:
    id: int
    name: str
    avatar_url: str


@dataclass
class AlbumInfo:
    id: int
    name: str
    cover_url: str


@dataclass
class PrivilegeInfo:
    fee: int
    max_br: int
    is_vip_only: bool


@dataclass
class SearchSongInfo:
    id: int | str
    name: str
    artists: list[ArtistInfo]
    album: AlbumInfo
    privilege: PrivilegeInfo
    duration: int


@dataclass
class TrackDetailInfo:
    cover_url: str
    album_name: str
    cd: str
    track_no: int
    publish_time: int


@dataclass
class TrackAudioInfo:
    url: str


@dataclass
class TrackLyricsInfo:
    lyric: str
    translated_lyric: str
    yrc_lyric: str
    ytlrc_lyric: str


class MusicServiceBackend(ABC):
    @abstractmethod
    def searchSong(
        self, keywords: str, offset: int = 0, limit: int = 30
    ) -> list[SearchSongInfo]: ...

    @abstractmethod
    def searchPlaylist(
        self, keywords: str, offset: int = 0, limit: int = 30
    ) -> list[SearchCloudFolderInfo]: ...

    @abstractmethod
    def getTrackDetail(self, track_id: int | str) -> TrackDetailInfo: ...

    @abstractmethod
    def getTrackAudio(
        self, track_id: int | str, bitrate: int = 999000
    ) -> TrackAudioInfo: ...

    @abstractmethod
    def getTrackLyrics(self, track_id: int | str) -> TrackLyricsInfo: ...

    @abstractmethod
    def userPrivilegeLevel(self) -> int: ...

    @abstractmethod
    def getUserPlaylists(self) -> list[CloudFolderInfo]: ...

    @abstractmethod
    def userAnonymous(self) -> bool: ...

    @abstractmethod
    def createPlaylist(self, name: str) -> str: ...

    @abstractmethod
    def removePlaylist(self, id: str) -> None: ...

    @abstractmethod
    def editPlaylist(
        self, option: Literal['add', 'del'], song_ids: list[str], folder_id: str
    ) -> bool: ...

    @abstractmethod
    def getPlaylistTracks(self, playlist_id: str) -> list[SongStorable]: ...

    @abstractmethod
    def getUserVipType(self) -> int | str: ...
