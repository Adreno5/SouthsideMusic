from abc import ABC, abstractmethod
from typing import NotRequired, TypedDict
import base64
import hashlib
import os
import shutil

DATA_DIR = "./data"
MUSIC_DATA_DIR = os.path.join(DATA_DIR, "music")
IMAGE_DATA_DIR = os.path.join(DATA_DIR, "image")
LEGACY_CACHE_DIR = "./cache"
LEGACY_MUSIC_CACHE_DIR = os.path.join(LEGACY_CACHE_DIR, "music")
LEGACY_IMAGE_CACHE_DIR = os.path.join(LEGACY_CACHE_DIR, "image")


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
        image_base64: NotRequired[str]
        content_base64: NotRequired[str]
        image_cache_hash: str
        content_cache_hash: str
        lyric: str
        translated_lyric: str
        y_lyric: str
        y_lyric_unavailable: NotRequired[bool]
        gain: float
        target_lufs: int

    name: str
    artists: str
    id: str
    image_base64: str
    content_base64: str
    lyric: str
    y_lyric: str
    y_lyric_unavailable: bool
    translated_lyric: str

    loudness_gain: float
    target_lufs: int
    image_cache_hash: str
    content_cache_hash: str

    def __init__(
        self,
        info: SongInfo,
        image: bytes | None = None,
        music_bin: bytes | None = None,
        lyric: str = "",
        translated_lyric: str = "",
        gain: float = 1.0,
        target_lufs: int = -16,
        image_base64: str | None = None,
        content_base64: str | None = None,
        image_cache_hash: str = "",
        content_cache_hash: str = "",
        y_lyric: str = "",
        y_lyric_unavailable: bool = False,
    ) -> None:
        self.name = info["name"]
        self.artists = info["artists"]
        self.id = info["id"]
        if image_base64 is not None:
            self.image_base64 = image_base64
            self.image_cache_hash = image_cache_hash
        elif isinstance(image, bytes):
            self.image_base64 = base64.b64encode(image).decode()
            self.image_cache_hash = hashlib.sha256(image).hexdigest()
        else:
            raise ValueError("image or image_base64 is required")

        if content_base64 is not None:
            self.content_base64 = content_base64
            self.content_cache_hash = content_cache_hash
        elif isinstance(music_bin, bytes):
            self.content_base64 = base64.b64encode(music_bin).decode()
            self.content_cache_hash = hashlib.sha256(music_bin).hexdigest()
        else:
            raise ValueError("music_bin or content_base64 is required")

        self.lyric = lyric
        self.translated_lyric = translated_lyric
        self.y_lyric = y_lyric
        self.y_lyric_unavailable = y_lyric_unavailable
        self.loudness_gain = gain
        self.target_lufs = target_lufs

    @staticmethod
    def _ensure_cache_dirs() -> None:
        os.makedirs(MUSIC_DATA_DIR, exist_ok=True)
        os.makedirs(IMAGE_DATA_DIR, exist_ok=True)

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

    def _load_or_create_cached_bytes(
        self, base64_data: str, cache_hash: str, cache_dir: str
    ) -> tuple[bytes, str]:
        self._ensure_cache_dirs()

        if cache_hash:
            cache_path = self._get_cache_path(cache_dir, cache_hash)
            if os.path.exists(cache_path):
                with open(cache_path, "rb") as f:
                    return f.read(), cache_hash
            legacy_cache_path = self._get_legacy_cache_path(cache_dir, cache_hash)
            if os.path.exists(legacy_cache_path):
                shutil.move(legacy_cache_path, cache_path)
                with open(cache_path, "rb") as f:
                    return f.read(), cache_hash

        if not base64_data:
            return b"", ""

        data = base64.b64decode(base64_data)
        actual_hash = hashlib.sha256(data).hexdigest()
        cache_path = self._get_cache_path(cache_dir, actual_hash)
        if not os.path.exists(cache_path):
            with open(cache_path, "wb") as f:
                f.write(data)
        return data, actual_hash

    def _ensure_cache_fields(self) -> None:
        if not hasattr(self, "image_cache_hash"):
            self.image_cache_hash = ""
        if not hasattr(self, "content_cache_hash"):
            self.content_cache_hash = ""
        if not hasattr(self, "y_lyric"):
            self.y_lyric = ""
        if not hasattr(self, "y_lyric_unavailable"):
            self.y_lyric_unavailable = False

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self._ensure_cache_fields()

    def get_image_bytes(self) -> bytes:
        self._ensure_cache_fields()
        image_bytes, actual_hash = self._load_or_create_cached_bytes(
            self.image_base64, self.image_cache_hash, IMAGE_DATA_DIR
        )
        self.image_cache_hash = actual_hash
        return image_bytes

    def get_music_bytes(self) -> bytes:
        self._ensure_cache_fields()
        music_bytes, actual_hash = self._load_or_create_cached_bytes(
            self.content_base64, self.content_cache_hash, MUSIC_DATA_DIR
        )
        self.content_cache_hash = actual_hash
        return music_bytes

    def ensure_cached_assets(self) -> bool:
        self._ensure_cache_fields()
        original_image_hash = self.image_cache_hash
        original_content_hash = self.content_cache_hash

        self.get_image_bytes()
        self.get_music_bytes()

        return (
            original_image_hash != self.image_cache_hash
            or original_content_hash != self.content_cache_hash
        )

    def toObject(self) -> SongStorableDict:
        return {
            "name": self.name,
            "artists": self.artists,
            "id": self.id,
            "image_base64": self.image_base64,
            "content_base64": self.content_base64,
            "image_cache_hash": self.image_cache_hash,
            "content_cache_hash": self.content_cache_hash,
            "lyric": self.lyric,
            "translated_lyric": self.translated_lyric,
            "gain": self.loudness_gain,
            "target_lufs": self.target_lufs,
            "y_lyric": self.y_lyric,
            "y_lyric_unavailable": self.y_lyric_unavailable,
        }

    @staticmethod
    def fromObject(obj: SongStorableDict) -> "SongStorable":
        return SongStorable(
            info={
                "name": obj["name"],
                "artists": obj["artists"],
                "id": obj["id"],
                "privilege": -1,
            },
            image_base64=obj.get("image_base64", ""),
            content_base64=obj.get("content_base64", ""),
            image_cache_hash=obj.get("image_cache_hash", ""),
            content_cache_hash=obj.get("content_cache_hash", ""),
            lyric=obj["lyric"],
            translated_lyric=obj.get("translated_lyric", ""),
            y_lyric=obj.get("y_lyric", ""),
            y_lyric_unavailable=obj.get("y_lyric_unavailable", False),
            gain=obj.get("gain", 1.0),
            target_lufs=obj.get("target_lufs", -16),
        )


class FolderInfo(TypedDict):
    folder_name: str
    songs: list[SongStorable]


class BaseLyricUtil(ABC):
    @abstractmethod
    def init(self) -> None: ...
    @abstractmethod
    def search(self, keyword: str) -> list[SongInfo]: ...
