from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import threading
from typing import Any, Literal
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
COUNT_FILE = os.path.join(DATA_DIR, 'count.json')
_count_lock = threading.Lock()

_CACHE_INDEX_PATH = os.path.join(DATA_DIR, 'cache_index.json')
_cache_index: dict[str, dict[str, str]] = {}
_cache_index_loaded: bool = False


def _loadCacheIndex() -> dict[str, dict[str, str]]:
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


def _saveCacheIndex() -> None:
    global _cache_index
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(_CACHE_INDEX_PATH, 'w', encoding='utf-8') as f:
        json.dump(_cache_index or {}, f, ensure_ascii=False, indent=2)


def _updateCacheIndex(
    song_id: str, image_hash: str = '', audio_hash: str = ''
) -> None:
    global _cache_index
    idx = _loadCacheIndex()
    entry = idx.get(song_id, {})
    if image_hash:
        entry['image_cache_hash'] = image_hash
    if audio_hash:
        entry['content_cache_hash'] = audio_hash
    if entry:
        idx[song_id] = entry
        _saveCacheIndex()


def getCachedHashes(song_id: str) -> dict[str, str]:
    return _loadCacheIndex().get(song_id, {})


@dataclass
class ArtistInfo:
    id: int
    name: str


@dataclass
class SongDetail:
    image_url: str


@dataclass
class SongInfo:
    name: str
    artists: list[ArtistInfo]
    id: str
    privilege: int
    duration: int = 0


def _artist_to_object(artist: ArtistInfo) -> dict[str, object]:
    return {
        'id': artist.id,
        'name': artist.name,
    }


def _artist_from_object(obj: object) -> ArtistInfo | None:
    if isinstance(obj, ArtistInfo):
        return obj
    if isinstance(obj, str):
        return ArtistInfo(id=0, name=obj)
    if not isinstance(obj, dict):
        return None

    try:
        artist_id = int(obj.get('id', 0))
    except (TypeError, ValueError):
        artist_id = 0
    return ArtistInfo(id=artist_id, name=str(obj.get('name', '')))


def _artistsFromObject(obj: object) -> list[ArtistInfo]:
    if not isinstance(obj, list):
        return []
    return [
        artist
        for artist in (_artist_from_object(item) for item in obj)
        if artist is not None
    ]


def _intFromObject(obj: object, default: int = 0) -> int:
    try:
        return int(obj)  # type: ignore
    except (TypeError, ValueError):
        return default


def _song_id_from_object(obj: object) -> str:
    return str(obj or '')


def _load_count() -> dict[str, int]:
    if not os.path.exists(COUNT_FILE):
        return {}
    with open(COUNT_FILE, 'r', encoding='utf-8') as fp:
        content = fp.read().strip()
    if not content:
        return {}
    try:
        obj = json.loads(content)
    except json.JSONDecodeError:
        try:
            obj, _ = json.JSONDecoder().raw_decode(content)
        except json.JSONDecodeError:
            return {}
    if not isinstance(obj, dict):
        return {}
    return {str(song_id): _intFromObject(count) for song_id, count in obj.items()}


def _normalize_count(obj: dict[str, int]) -> dict[str, int]:
    result: dict[str, int] = {}
    for song_id, count in obj.items():
        key = _song_id_from_object(song_id)
        if not key:
            continue
        result[key] = result.get(key, 0) + _intFromObject(count)
    return result


def _save_count(obj: dict[str, int]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(COUNT_FILE, 'w', encoding='utf-8') as fp:
        json.dump(_normalize_count(obj), fp, indent=4)


class SongStorable:
    name: str
    artists: list[ArtistInfo]
    id: str
    loudness_gain: float
    target_lufs: int
    loaded_loudness_gain: bool = False
    image_cache_hash: str = ''
    content_cache_hash: str = ''
    lyric_cache_hash: str = ''
    loggedin_when_download: bool = False
    viptype_when_download: int = 0
    duration: int = 0
    count: int = 0

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
        loaded_loudness_gain: bool = False,
        image_cache_hash: str = '',
        content_cache_hash: str = '',
        lyric_cache_hash: str = '',
        loggedin_when_download: bool = False,
        viptype_when_download: int = 0,
    ) -> None:
        self.name = info.name
        self.artists = info.artists
        self.id = _song_id_from_object(info.id)
        self.duration = max(0, _intFromObject(info.duration))
        self._ensureArtists()

        if isinstance(image, bytes):
            self._writeCache(image, IMAGE_DATA_DIR, 'image_cache_hash')
        else:
            self.image_cache_hash = image_cache_hash

        if isinstance(music_bin, bytes):
            self._writeCache(music_bin, MUSIC_DATA_DIR, 'content_cache_hash')
        else:
            self.content_cache_hash = content_cache_hash

        self.lyric_cache_hash = lyric_cache_hash
        if lyric or translated_lyric or yrc_lyric:
            self.writeLyrics(lyric, translated_lyric, yrc_lyric)
        self.loudness_gain = gain
        self.target_lufs = target_lufs
        self.loaded_loudness_gain = loaded_loudness_gain
        self.loggedin_when_download = loggedin_when_download
        self.viptype_when_download = viptype_when_download
        self.count = _load_count().get(self.id, 0)

    def _ensureCount(self) -> None:
        with _count_lock:
            obj = _load_count()
            if self.id not in obj:
                obj[self.id] = 0
            _save_count(obj)

    def incrementCount(self, count: int) -> None:
        with _count_lock:
            obj = _load_count()
            key = _song_id_from_object(self.id)
            obj[key] = obj.get(key, 0) + count
            self.count = obj[key]
            _save_count(obj)

        from imports import STORABLE_COUNT_CHANGED, event_bus

        event_bus.emit(STORABLE_COUNT_CHANGED, self)

    def _ensureArtists(self) -> None:
        if (self.artists and self.duration > 0) or not self.id:
            return
        try:
            from core.backend import getBackend

            detail = getBackend().getTrackDetail(self.id)
            if not self.artists:
                self.artists = detail.artists
            if self.duration <= 0:
                self.duration = max(0, _intFromObject(detail.duration))
        except Exception:
            if not self.artists:
                self.artists = []

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SongStorable):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)

    def _writeCache(self, data: bytes, cache_dir: str, hash_attr: str) -> str:
        os.makedirs(cache_dir, exist_ok=True)
        cache_hash = hashlib.sha256(data).hexdigest()
        cache_path = os.path.join(cache_dir, cache_hash)
        if not os.path.exists(cache_path):
            with open(cache_path, 'wb') as f:
                f.write(data)
        setattr(self, hash_attr, cache_hash)
        if self.id:
            if hash_attr == 'image_cache_hash':
                _updateCacheIndex(self.id, image_hash=cache_hash)
            elif hash_attr == 'content_cache_hash':
                _updateCacheIndex(self.id, audio_hash=cache_hash)
        return cache_hash

    @staticmethod
    def _ensureCacheDirs() -> None:
        os.makedirs(MUSIC_DATA_DIR, exist_ok=True)
        os.makedirs(IMAGE_DATA_DIR, exist_ok=True)
        os.makedirs(LYRIC_DATA_DIR, exist_ok=True)

    @staticmethod
    def _getCachePath(cache_dir: str, cache_hash: str) -> str:
        return os.path.join(cache_dir, cache_hash)

    @staticmethod
    def _getLegacyCachePath(cache_dir: str, cache_hash: str) -> str:
        legacy_dir = (
            LEGACY_IMAGE_CACHE_DIR
            if cache_dir == IMAGE_DATA_DIR
            else LEGACY_MUSIC_CACHE_DIR
        )
        return os.path.join(legacy_dir, cache_hash)

    def _readCache(self, cache_hash: str, cache_dir: str) -> bytes | None:
        if not cache_hash:
            return None
        cache_path = self._getCachePath(cache_dir, cache_hash)
        if os.path.exists(cache_path):
            with open(cache_path, 'rb') as f:
                return f.read()
        legacy_path = self._getLegacyCachePath(cache_dir, cache_hash)
        if os.path.exists(legacy_path):
            shutil.move(legacy_path, cache_path)
            with open(cache_path, 'rb') as f:
                return f.read()
        return None

    def imageCached(self) -> bool:
        self._ensureCacheFields()
        return bool(self.image_cache_hash) and os.path.exists(
            self._getCachePath(IMAGE_DATA_DIR, self.image_cache_hash)
        )

    def audioCached(self, logged_in: bool, vip_type: int) -> bool:
        self._ensureCacheFields()
        if (
            logged_in != self.loggedin_when_download
            or vip_type != self.viptype_when_download
        ):
            self.loggedin_when_download = logged_in
            self.viptype_when_download = vip_type
            return False
        return bool(self.content_cache_hash) and os.path.exists(
            self._getCachePath(MUSIC_DATA_DIR, self.content_cache_hash)
        )

    def cacheImage(self, data: bytes) -> str:
        return self._writeCache(data, IMAGE_DATA_DIR, 'image_cache_hash')

    def cacheAudio(self, data: bytes) -> str:
        return self._writeCache(data, MUSIC_DATA_DIR, 'content_cache_hash')

    def _ensureCacheFields(self) -> None:
        self.id = _song_id_from_object(self.id)
        if not hasattr(self, 'image_cache_hash'):
            self.image_cache_hash = ''
        if not hasattr(self, 'content_cache_hash'):
            self.content_cache_hash = ''
        if not hasattr(self, 'duration'):
            self.duration = 0
        if 'loaded_loudness_gain' not in self.__dict__:
            self.loaded_loudness_gain = bool(
                self.__dict__.pop('loudness_analyzed', False)
            )
        else:
            self.__dict__.pop('loudness_analyzed', None)
        if not hasattr(self, 'lyric_cache_hash'):
            self.lyric_cache_hash = ''
            lyric = self.__dict__.get('lyric', '')
            translated_lyric = self.__dict__.get('translated_lyric', '')
            yrc_lyric = self.__dict__.get('yrc_lyric', '')
            if lyric or translated_lyric or yrc_lyric:
                self.writeLyrics(lyric, translated_lyric, yrc_lyric)
        self.__dict__.pop('lyric', None)
        self.__dict__.pop('translated_lyric', None)
        self.__dict__.pop('yrc_lyric', None)

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self._ensureCacheFields()

    def getImageBytes(self) -> bytes:
        self._ensureCacheFields()
        result = self._readCache(self.image_cache_hash, IMAGE_DATA_DIR)
        if result is not None:
            return result
        raise FileNotFoundError(
            f'Image cache not found for {self.name}: hash={self.image_cache_hash}'
        )

    def getMusicBytes(self) -> bytes:
        self._ensureCacheFields()
        result = self._readCache(self.content_cache_hash, MUSIC_DATA_DIR)
        if result is not None:
            return result
        raise FileNotFoundError(
            f'Music cache not found for {self.name}: hash={self.content_cache_hash}'
        )

    def getLyricPath(self) -> str:
        self._ensureCacheFields()
        cache_name = self.lyric_cache_hash or f'{self.id}.json'
        return os.path.join(LYRIC_DATA_DIR, cache_name)

    def getLyrics(self) -> dict[str, str]:
        self._ensureCacheFields()
        path = self.getLyricPath()
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
        return self.getLyrics()['lyric']

    @property
    def translated_lyric(self) -> str:
        return self.getLyrics()['translated_lyric']

    @property
    def yrc_lyric(self) -> str:
        return self.getLyrics()['yrc_lyric']

    def writeLyrics(
        self,
        lyric: str = '',
        translated_lyric: str = '',
        yrc_lyric: str = '',
        ytlrc_lyric: str = '',
    ) -> None:
        os.makedirs(LYRIC_DATA_DIR, exist_ok=True)
        self.lyric_cache_hash = f'{self.id}.json'
        path = self.getLyricPath()
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

    def lyricsMissing(self) -> bool:
        self._ensureCacheFields()
        return not self.lyric_cache_hash or not os.path.exists(self.getLyricPath())

    def yrcLyricsMissing(self) -> bool:
        self._ensureCacheFields()
        if self.lyricsMissing():
            return True
        try:
            with open(self.getLyricPath(), 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return True
        return not data.get('has_yrc_lyric')

    def translatedLyricsMissing(self) -> bool:
        self._ensureCacheFields()
        if self.lyricsMissing():
            return True
        try:
            with open(self.getLyricPath(), 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return True
        return not data.get('translated_lyric')

    def ytlrcMissing(self) -> bool:
        self._ensureCacheFields()
        if self.lyricsMissing():
            return True
        try:
            with open(self.getLyricPath(), 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return True
        return not data.get('ytlrc_lyric')
        self._ensureCacheFields()
        if self.lyricsMissing():
            return True
        try:
            with open(self.getLyricPath(), 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return True
        return not data.get('has_yrc_lyric')

    def ensureCachedAssets(self, logged_in: bool, vip_type: int) -> bool:
        self._ensureCacheFields()
        return not (self.imageCached() and self.audioCached(logged_in, vip_type))

    def toObject(self) -> dict[str, object]:
        return {
            'name': self.name,
            'artists': [_artist_to_object(artist) for artist in self.artists],
            'id': self.id,
            'image_cache_hash': self.image_cache_hash,
            'content_cache_hash': self.content_cache_hash,
            'lyric_cache_hash': self.lyric_cache_hash,
            'gain': self.loudness_gain,
            'target_lufs': self.target_lufs,
            'loaded_loudness_gain': self.loaded_loudness_gain,
            'loggedin_when_download': self.loggedin_when_download,
            'viptype_when_download': self.viptype_when_download,
            'duration': self.duration,
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
        loaded_loudness_gain = obj.get(
            'loaded_loudness_gain',
            obj.get('loudness_analyzed', False),
        )

        return SongStorable(
            info=SongInfo(
                name=str(obj.get('name', '')),
                artists=_artistsFromObject(obj.get('artists', [])),
                id=str(obj.get('id', '')),
                privilege=-1,
                duration=_intFromObject(obj.get('duration', 0)),
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
            loaded_loudness_gain=bool(loaded_loudness_gain),
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
    song_count: int | None = None


@dataclass
class SearchCloudFolderInfo:
    folder_name: str
    image_url: str
    id: str
    author: str


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
    artists: list[ArtistInfo]
    duration: int = 0
    name: str = ''
    aliases: list[str] = field(default_factory=list)
    display_tags: list[str] = field(default_factory=list)
    entertainment_tags: list[str] = field(default_factory=list)
    award_tags: list[str] = field(default_factory=list)
    mark_tags: list[str] = field(default_factory=list)
    song_feature: Any | None = None


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
    def loggedIn(self) -> bool: ...

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

    @abstractmethod
    def getDailyRecommendSongs(self) -> list[SongStorable]: ...

    @abstractmethod
    def getDailyRecommendFolders(self) -> list[CloudFolderInfo]: ...
