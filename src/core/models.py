from __future__ import annotations

from abc import ABC, abstractmethod
from typing import NotRequired, TypedDict
import base64
import hashlib
import json
import logging
import os
import shutil

DATA_DIR = './data'
MUSIC_DATA_DIR = os.path.join(DATA_DIR, 'music')
IMAGE_DATA_DIR = os.path.join(DATA_DIR, 'image')
LYRIC_DATA_DIR = os.path.join(DATA_DIR, 'lyrics')
LEGACY_CACHE_DIR = './cache'
LEGACY_MUSIC_CACHE_DIR = os.path.join(LEGACY_CACHE_DIR, 'music')
LEGACY_IMAGE_CACHE_DIR = os.path.join(LEGACY_CACHE_DIR, 'image')


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
        image_cache_hash: str
        content_cache_hash: str
        lyric_cache_hash: str
        gain: float
        target_lufs: int

    name: str
    artists: str
    id: str
    loudness_gain: float
    target_lufs: int
    image_cache_hash: str = ''
    content_cache_hash: str = ''
    lyric_cache_hash: str = ''

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
    ) -> None:
        self.name = info['name']
        self.artists = info['artists']
        self.id = info['id']

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

    def _write_cache(self, data: bytes, cache_dir: str, hash_attr: str) -> str:
        os.makedirs(cache_dir, exist_ok=True)
        cache_hash = hashlib.sha256(data).hexdigest()
        cache_path = os.path.join(cache_dir, cache_hash)
        if not os.path.exists(cache_path):
            with open(cache_path, 'wb') as f:
                f.write(data)
        setattr(self, hash_attr, cache_hash)
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
            return {'lyric': '', 'translated_lyric': '', 'yrc_lyric': ''}
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return {
            'lyric': data.get('lyric', ''),
            'translated_lyric': data.get('translated_lyric', ''),
            'yrc_lyric': data.get('yrc_lyric', ''),
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

    def ensure_cached_assets(self) -> bool:
        self._ensure_cache_fields()
        changed = False
        for attr, cache_dir in [
            ('image_cache_hash', IMAGE_DATA_DIR),
            ('content_cache_hash', MUSIC_DATA_DIR),
        ]:
            if self._read_cache(getattr(self, attr), cache_dir) is None:
                changed = True
        return changed

    def toObject(self) -> SongStorableDict:
        return {
            'name': self.name,
            'artists': self.artists,
            'id': self.id,
            'image_cache_hash': self.image_cache_hash,
            'content_cache_hash': self.content_cache_hash,
            'lyric_cache_hash': self.lyric_cache_hash,
            'gain': self.loudness_gain,
            'target_lufs': self.target_lufs,
        }

    @staticmethod
    def fromObject(obj: SongStorableDict) -> 'SongStorable':
        image_bytes = None
        music_bytes = None
        image_cache_hash = obj.get('image_cache_hash', '')
        content_cache_hash = obj.get('content_cache_hash', '')
        lyric_cache_hash = obj.get('lyric_cache_hash', '')  # type: ignore[typeddict-unknown-key]

        old_image_b64 = obj.get('image_base64')  # type: ignore[typeddict-unknown-key]
        old_content_b64 = obj.get('content_base64')  # type: ignore[typeddict-unknown-key]

        if old_image_b64:
            assert isinstance(old_image_b64, str)
            image_bytes = base64.b64decode(old_image_b64)
            image_cache_hash = ''
        if old_content_b64:
            assert isinstance(old_content_b64, str)
            music_bytes = base64.b64decode(old_content_b64)
            content_cache_hash = ''

        return SongStorable(
            info={
                'name': obj['name'],
                'artists': obj['artists'],
                'id': obj['id'],
                'privilege': -1,
            },
            image=image_bytes,
            music_bin=music_bytes,
            image_cache_hash=image_cache_hash,
            content_cache_hash=content_cache_hash,
            lyric=obj.get('lyric', ''),
            translated_lyric=obj.get('translated_lyric', ''),
            yrc_lyric=obj.get('yrc_lyric', ''),  # type: ignore[typeddict-unknown-key]
            lyric_cache_hash=lyric_cache_hash,
            gain=obj.get('gain', 1.0),
            target_lufs=obj.get('target_lufs', -16),
        )


class FolderInfo(TypedDict):
    folder_name: str
    songs: list[SongStorable]
