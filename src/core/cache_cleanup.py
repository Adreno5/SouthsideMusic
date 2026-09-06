from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import time

_logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
DATA_DIR = os.path.join(_PROJECT_ROOT, 'data')
MUSIC_DATA_DIR = os.path.join(DATA_DIR, 'music')
IMAGE_DATA_DIR = os.path.join(DATA_DIR, 'image')
COVER_DATA_DIR = os.path.join(DATA_DIR, 'cover')
TEMP_DATA_DIR = os.path.join(DATA_DIR, 'temp')

DEFAULT_DATA_CACHE_MAX_BYTES = 4 * 1024 * 1024 * 1024
# Cache entries can remain referenced by the currently playing song even when
# their mtime is old; a short age window caused visible covers to disappear.
DEFAULT_DATA_CACHE_MAX_AGE_MINUTES = 24 * 60
DEFAULT_DATA_CLEANUP_INTERVAL_SECONDS = 5 * 60
DEFAULT_TEMP_CACHE_MAX_AGE_MINUTES = 5

_RECENT_FILE_GRACE_SECONDS = 5 * 60
_CACHE_DIRS = (MUSIC_DATA_DIR, IMAGE_DATA_DIR, COVER_DATA_DIR)
_PROTECTED_DATA_FILES = (
    os.path.join(DATA_DIR, 'count.json'),
    os.path.join(DATA_DIR, 'cache_index.json'),
)


@dataclass(frozen=True)
class CacheCleanupResult:
    removed_count: int = 0
    removed_bytes: int = 0
    remaining_bytes: int = 0
    skipped_count: int = 0


@dataclass(frozen=True)
class _CacheFile:
    path: str
    size: int
    mtime: float


def touchCacheFile(path: str) -> None:
    """Mark a cache file as recently used."""
    try:
        os.utime(path, None)
    except OSError:
        pass


def cleanupDataFolder(
    max_bytes: int = DEFAULT_DATA_CACHE_MAX_BYTES,
    max_age_minutes: int = DEFAULT_DATA_CACHE_MAX_AGE_MINUTES,
    temp_max_age_minutes: int = DEFAULT_TEMP_CACHE_MAX_AGE_MINUTES,
) -> CacheCleanupResult:
    """Trim redownloadable files in data/."""
    now = time.time()
    max_bytes = max(0, int(max_bytes))
    max_age_seconds = max(0, int(max_age_minutes)) * 60
    temp_max_age_seconds = max(0, int(temp_max_age_minutes)) * 60
    cache_files = _iterCacheFiles(_CACHE_DIRS)
    temp_files = _iterCacheFiles((TEMP_DATA_DIR,))

    removed_paths: set[str] = set()
    removed_count = 0
    removed_bytes = 0
    skipped_count = 0
    remaining_bytes = sum(file.size for file in cache_files)

    def removeFile(file: _CacheFile, reduce_remaining: bool) -> None:
        nonlocal removed_count, removed_bytes, skipped_count, remaining_bytes
        if file.path in removed_paths:
            return
        if _isProtectedDataFile(file.path):
            return
        try:
            os.remove(file.path)
        except FileNotFoundError:
            removed_paths.add(file.path)
            return
        except PermissionError:
            skipped_count += 1
            return
        except OSError as e:
            skipped_count += 1
            _logger.debug(f'failed to remove cache file {file.path}: {e}')
            return

        removed_paths.add(file.path)
        removed_count += 1
        removed_bytes += file.size
        if reduce_remaining:
            remaining_bytes = max(0, remaining_bytes - file.size)

    if temp_max_age_seconds > 0:
        for file in temp_files:
            if _fileAge(now, file) >= temp_max_age_seconds:
                removeFile(file, reduce_remaining=False)

    if max_age_seconds > 0:
        for file in sorted(cache_files, key=lambda item: item.mtime):
            if _fileAge(now, file) >= max_age_seconds and _fileAge(
                now, file
            ) >= _RECENT_FILE_GRACE_SECONDS:
                removeFile(file, reduce_remaining=True)

    if remaining_bytes > max_bytes:
        for file in sorted(cache_files, key=lambda item: item.mtime):
            if remaining_bytes <= max_bytes:
                break
            if file.path in removed_paths:
                continue
            if _fileAge(now, file) < _RECENT_FILE_GRACE_SECONDS:
                skipped_count += 1
                continue
            removeFile(file, reduce_remaining=True)

    result = CacheCleanupResult(
        removed_count=removed_count,
        removed_bytes=removed_bytes,
        remaining_bytes=remaining_bytes,
        skipped_count=skipped_count,
    )
    if result.removed_count:
        _logger.info(
            'data cleanup removed %s files, freed %.1f MiB, remaining %.1f MiB',
            result.removed_count,
            result.removed_bytes / 1024 / 1024,
            result.remaining_bytes / 1024 / 1024,
        )
    return result


def _iterCacheFiles(cache_dirs: tuple[str, ...]) -> list[_CacheFile]:
    result: list[_CacheFile] = []
    for cache_dir in cache_dirs:
        if not os.path.isdir(cache_dir):
            continue
        for root, _dirs, files in os.walk(cache_dir):
            for file in files:
                path = os.path.join(root, file)
                if _isProtectedDataFile(path):
                    continue
                try:
                    stat_result = os.stat(path)
                except OSError:
                    continue
                result.append(
                    _CacheFile(
                        path=path,
                        size=max(0, stat_result.st_size),
                        mtime=stat_result.st_mtime,
                    )
                )
    return result


def _fileAge(now: float, file: _CacheFile) -> float:
    return max(0.0, now - file.mtime)


def _isProtectedDataFile(path: str) -> bool:
    normalized = os.path.normcase(os.path.abspath(path))
    return any(
        normalized == os.path.normcase(os.path.abspath(protected))
        for protected in _PROTECTED_DATA_FILES
    )
