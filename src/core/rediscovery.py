from __future__ import annotations

import json
import logging
import math
import os
import random
import threading
import time
from typing import Literal

from core.models import DATA_DIR, SongStorable, _load_count

RediscoveryMode = Literal['balanced', 'rare', 'forgotten']

LISTENING_HISTORY_FILE = os.path.join(DATA_DIR, 'listening_history.json')
_history_lock = threading.Lock()
_logger = logging.getLogger(__name__)


def getListeningHistory() -> dict[str, float]:
    try:
        with open(LISTENING_HISTORY_FILE, 'r', encoding='utf-8') as stream:
            data = json.load(stream)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        _logger.exception('Failed to load listening history')
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(song_id): float(timestamp)
        for song_id, timestamp in data.items()
        if isinstance(timestamp, (int, float))
        and math.isfinite(timestamp)
        and timestamp > 0
    }


def recordListening(song: SongStorable) -> None:
    with _history_lock:
        history = getListeningHistory()
        history[str(song.id)] = time.time()
        temporary_path = LISTENING_HISTORY_FILE + '.tmp'
        try:
            os.makedirs(os.path.dirname(LISTENING_HISTORY_FILE), exist_ok=True)
            with open(temporary_path, 'w', encoding='utf-8') as stream:
                json.dump(history, stream, ensure_ascii=False, indent=2)
            os.replace(temporary_path, LISTENING_HISTORY_FILE)
        except OSError:
            _logger.exception('Failed to save listening history')


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
