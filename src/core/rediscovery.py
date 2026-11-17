from __future__ import annotations

import json
import logging
import math
import os
import random
import threading
import time
from typing import Literal

from core.deferred_writes import getDeferredWriter
from core.models import DATA_DIR, SongStorable, _load_count

RediscoveryMode = Literal['balanced', 'rare', 'forgotten']

LISTENING_HISTORY_FILE = os.path.join(DATA_DIR, 'listening_history.json')
_history_lock = threading.RLock()
_logger = logging.getLogger(__name__)
_history_cache: dict[str, float] | None = None
_history_cache_key: tuple[str, int, int] | None = None
_history_writer = getDeferredWriter('listening-history')


def getListeningHistory() -> dict[str, float]:
    global _history_cache, _history_cache_key
    key = _fileKey(LISTENING_HISTORY_FILE)
    with _history_lock:
        if _history_cache is not None and key == _history_cache_key:
            return dict(_history_cache)
    try:
        with open(LISTENING_HISTORY_FILE, 'r', encoding='utf-8') as stream:
            data = json.load(stream)
    except FileNotFoundError:
        with _history_lock:
            _history_cache = {}
            _history_cache_key = None
        return {}
    except (OSError, ValueError):
        _logger.exception('Failed to load listening history')
        return {}
    if not isinstance(data, dict):
        with _history_lock:
            _history_cache = {}
            _history_cache_key = key
        return {}
    history = {
        str(song_id): float(timestamp)
        for song_id, timestamp in data.items()
        if isinstance(timestamp, (int, float))
        and math.isfinite(timestamp)
        and timestamp > 0
    }
    with _history_lock:
        _history_cache = dict(history)
        _history_cache_key = key
    return history


def recordListening(song: SongStorable) -> None:
    global _history_cache
    with _history_lock:
        history = getListeningHistory()
        history[str(song.id)] = time.time()
        _history_cache = dict(history)
        path = LISTENING_HISTORY_FILE
        _history_writer.submit(
            lambda data=dict(history), target=path: _writeHistory(data, target)
        )


def _writeHistory(
    history: dict[str, float], path: str = LISTENING_HISTORY_FILE
) -> None:
    global _history_cache, _history_cache_key
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary_path = path + '.tmp'
    try:
        with open(temporary_path, 'w', encoding='utf-8') as stream:
            json.dump(history, stream, ensure_ascii=False, indent=2)
        os.replace(temporary_path, path)
        with _history_lock:
            _history_cache = dict(history)
            _history_cache_key = _fileKey(path)
    except OSError:
        _logger.exception('Failed to save listening history')


def _fileKey(path: str) -> tuple[str, int, int] | None:
    try:
        stat_result = os.stat(path)
    except OSError:
        return None
    return path, stat_result.st_mtime_ns, stat_result.st_size


def getRediscoverySongs(
    songs: list[SongStorable],
    mode: RediscoveryMode = 'balanced',
    limit: int | None = 20,
    previous_ids: set[str] | None = None,
    current_id: str | None = None,
) -> list[SongStorable]:
    if limit is not None and limit <= 0:
        return []
    history = getListeningHistory()
    try:
        counts = _load_count()
    except OSError:
        _logger.exception('Failed to load playback counts')
        counts = {}
    unique: dict[str, SongStorable] = {}
    for song in songs:
        song_id = str(song.id)
        if not song_id or song_id in unique:
            continue
        song.count = max(0, counts.get(song_id, song.count))
        if mode == 'forgotten' and song.count == 0 and song_id not in history:
            continue
        unique[song_id] = song
    now = time.time()
    ranked: list[tuple[tuple[float, ...], SongStorable]] = []
    for song_id, song in unique.items():
        last_listened = history.get(song_id, 0)
        days = max(0.0, (now - last_listened) / 86400) if last_listened else 14.0
        recent = float(bool(last_listened) and days < 1)
        previous = float(song_id in (previous_ids or set()))
        current = float(song_id == current_id)
        jitter = random.random()
        priority: tuple[float, ...]
        if mode == 'rare':
            priority = (current, recent, float(song.count), previous, jitter)
        elif mode == 'forgotten':
            priority = (current, recent, previous, -min(days, 3650), jitter)
        else:
            weight = (1 + min(days, 90) / 30) / math.sqrt(song.count + 1)
            priority = (
                current,
                recent,
                previous,
                -math.log(max(jitter, 1e-12)) / weight,
            )
        ranked.append((priority, song))
    ranked.sort(key=lambda item: item[0])
    return [song for _, song in ranked[:limit]]
