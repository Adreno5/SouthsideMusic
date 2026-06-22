from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import math
import os

import threading
from typing import Callable, Dict, Optional
from core.config import cfg

from imports import (
    START_INTER_LOADING,
    START_PROGRESS_LOADING,
    STOP_INTER_LOADING,
    STOP_PROGRESS_LOADING,
    UPDATE_LOADING_PROGRESS,
    QObject,
    Signal,
    event_bus,
)
import requests


class DownloadingManager(QObject):
    downloadStarted = Signal()
    receiveProgress = Signal(float)
    workerFinished = Signal(bytes)
    downloadFinished = Signal(bytes)

    MAX_CHUNK_THREADS = int(cfg.download_concurrent_threads)
    MIN_CHUNK_SIZE = 4096

    def __init__(
        self,
        parent=None,
        url: str = '',
        headers: Optional[Dict] = None,
        data: Optional[Dict] = None,
    ):
        super().__init__(parent)
        self.url = url
        self.headers = headers.copy() if headers else {}
        self.data = data
        self._worker = _DownloadWorker(
            self.url,
            self.headers,
            self.data,
            self.MAX_CHUNK_THREADS,
            self.MIN_CHUNK_SIZE,
            self.receiveProgress.emit,
        )
        self._thread: threading.Thread | None = None
        self.receiveProgress.connect(self.progress)
        self.workerFinished.connect(self._finish_download)

    def start(self):
        self.downloadStarted.emit()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self.workerFinished.emit(self._worker.run())

    def progress(self, progress: float):
        event_bus.emit(UPDATE_LOADING_PROGRESS, max(0.0, min(1.0, progress)))

    def _finish_download(self, data: bytes):
        if data:
            event_bus.emit(UPDATE_LOADING_PROGRESS, 1.0)
        event_bus.emit(STOP_PROGRESS_LOADING)
        self.downloadFinished.emit(data)


class _DownloadWorker:
    def __init__(
        self,
        url: str,
        headers: Dict,
        data: Optional[Dict],
        max_chunk_threads: int,
        min_chunk_size: int,
        progress: Callable[[float], None],
    ):
        self.url = url
        self.headers = headers.copy()
        self.data = data
        self.max_chunk_threads = max_chunk_threads
        self.min_chunk_size = min_chunk_size
        self.progress = progress

    def run(self) -> bytes:
        try:
            total_length, accept_ranges = self._probe_download()
            if total_length <= 0 or not accept_ranges:
                return self._download_single(total_length)

            return self._download_chunks(total_length)
        except Exception:
            return bytes()

    def _probe_download(self) -> tuple[int, bool]:
        response = requests.head(
            self.url,
            headers=self.headers,
            timeout=10,
            allow_redirects=True,
        )
        response.raise_for_status()
        total_length = int(response.headers.get('content-length', 0))
        accept_ranges = response.headers.get('accept-ranges', '').lower() == 'bytes'
        return total_length, accept_ranges

    def _download_single(self, total_length: int) -> bytes:
        response = requests.get(
            self.url,
            headers=self.headers,
            data=self.data,
            stream=True,
            timeout=30,
        )
        response.raise_for_status()

        if total_length <= 0:
            total_length = int(response.headers.get('content-length', 0))

        downloaded = 0
        final_data = bytearray()
        for chunk in response.iter_content(chunk_size=self.min_chunk_size):
            if not chunk:
                continue

            final_data.extend(chunk)
            downloaded += len(chunk)
            if total_length > 0:
                self.progress(downloaded / total_length)

        return bytes(final_data)

    def _download_chunks(self, total_length: int) -> bytes:
        chunk_size = max(
            self.min_chunk_size,
            math.ceil(total_length / self.max_chunk_threads),
        )
        ranges: list[tuple[int, int]] = []
        start = 0
        while start < total_length:
            end = min(start + chunk_size - 1, total_length - 1)
            ranges.append((start, end))
            start = end + 1

        progress_by_chunk = [0] * len(ranges)
        progress_lock = threading.Lock()

        def on_chunk_progress(index: int, bytes_downloaded: int):
            with progress_lock:
                progress_by_chunk[index] = bytes_downloaded
                downloaded = sum(progress_by_chunk)
            self.progress(downloaded / total_length)

        with ThreadPoolExecutor(max_workers=len(ranges)) as executor:
            futures = {
                executor.submit(
                    self._download_range,
                    index,
                    start,
                    end,
                    on_chunk_progress,
                ): index
                for index, (start, end) in enumerate(ranges)
            }
            results: list[bytes] = [b''] * len(ranges)
            for future in as_completed(futures):
                index = futures[future]
                results[index] = future.result()

        return b''.join(results)

    def _download_range(
        self,
        index: int,
        start: int,
        end: int,
        progress: Callable[[int, int], None],
    ) -> bytes:
        headers = self.headers.copy()
        headers['Range'] = f'bytes={start}-{end}'
        response = requests.get(
            self.url,
            headers=headers,
            data=self.data,
            stream=True,
            timeout=30,
        )
        response.raise_for_status()

        chunk_data = bytearray()
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue

            chunk_data.extend(chunk)
            progress(index, len(chunk_data))

        expected = end - start + 1
        if len(chunk_data) != expected:
            raise ValueError(
                f'Chunk size mismatch: expected {expected}, got {len(chunk_data)}'
            )
        return bytes(chunk_data)


class TaskManager(QObject):
    taskFinished = Signal()

    def __init__(
        self,
        task: Callable,
        args: tuple,
        parent=None,
    ):
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self.task = task
        self.args = args
        self._thread: threading.Thread | None = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self.task(*self.args)
        except Exception as e:
            self._logger.exception('Background task failed')
            raise e
        finally:
            self.taskFinished.emit()


def asyncDownload(
    url: str,
    headers: Optional[Dict] = None,
    data: Optional[Dict] = None,
    parent=None,
    finished: Optional[Callable[[bytes], None]] = None,
) -> DownloadingManager:
    event_bus.emit(UPDATE_LOADING_PROGRESS, 0)
    event_bus.emit(START_PROGRESS_LOADING)
    box = DownloadingManager(parent, url, headers, data)

    def __finish(data: bytes):
        if finished:
            finished(data)

    box.downloadFinished.connect(__finish)
    box.start()
    return box


def asyncTask(
    task: Callable,
    args: tuple,
    parent,
    finished: Optional[Callable[[], None]] = None,
) -> TaskManager:
    event_bus.emit(START_INTER_LOADING)

    manager = TaskManager(task, args, parent)

    def __finish():
        def _apply_finish() -> None:
            if finished:
                finished()
            event_bus.emit(STOP_INTER_LOADING)

        ctx = getattr(parent, 'ctx', None)
        if ctx is not None:
            ctx.addScheduledTask(_apply_finish)
            return
        add_scheduled = getattr(parent, 'addScheduledTask', None)
        if add_scheduled is not None:
            add_scheduled(_apply_finish)
            return
        _apply_finish()

    manager.taskFinished.connect(__finish)
    manager.start()
    return manager


_stream_logger = logging.getLogger(__name__)

_MIN_STREAM_CHUNK = 4096


def downloadStream(
    url: str,
    dest_path: str,
    on_progress: Callable[[int, int], None] | None = None,
    start_byte: int = 0,
    headers: Optional[Dict] = None,
    data: Optional[Dict] = None,
) -> tuple[bool, int]:
    request_headers = headers.copy() if headers else {}

    try:
        probe = requests.head(
            url,
            headers=request_headers,
            timeout=10,
            allow_redirects=True,
        )
        probe.raise_for_status()
        total_length = int(probe.headers.get('content-length', 0))
        accept_ranges = probe.headers.get('accept-ranges', '').lower() == 'bytes'

        if total_length <= 0 or not accept_ranges:
            return _download_stream_single(
                url, dest_path, on_progress, start_byte, request_headers, data
            )

        remaining = total_length - start_byte
        if remaining <= 0:
            return True, total_length

        num_threads = min(
            int(cfg.download_concurrent_threads),
            max(1, math.ceil(remaining / _MIN_STREAM_CHUNK)),
        )
        chunk_size = math.ceil(remaining / num_threads)
        ranges: list[tuple[int, int]] = []
        for i in range(num_threads):
            chunk_start = start_byte + i * chunk_size
            chunk_end = min(chunk_start + chunk_size - 1, total_length - 1)
            if chunk_start <= chunk_end:
                ranges.append((chunk_start, chunk_end))

        if not os.path.exists(dest_path):
            with open(dest_path, 'wb') as f:
                f.truncate(total_length)
        else:
            with open(dest_path, 'r+b') as f:
                f.truncate(total_length)

        progress_by_chunk = [0] * len(ranges)
        progress_lock = threading.Lock()
        errors: list[Exception] = []

        def _dl_range(index: int, start: int, end: int) -> None:
            try:
                hdrs = request_headers.copy()
                hdrs['Range'] = f'bytes={start}-{end}'
                resp = requests.get(
                    url, headers=hdrs, data=data, stream=True, timeout=30
                )
                resp.raise_for_status()
                with open(dest_path, 'r+b') as f:
                    f.seek(start)
                    written = 0
                    for chunk in resp.iter_content(chunk_size=65536):
                        if not chunk:
                            continue
                        f.write(chunk)
                        written += len(chunk)
                        with progress_lock:
                            progress_by_chunk[index] = written
                            if on_progress:
                                on_progress(
                                    start_byte + sum(progress_by_chunk),
                                    total_length,
                                )
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=len(ranges)) as executor:
            futures = [
                executor.submit(_dl_range, i, s, e)
                for i, (s, e) in enumerate(ranges)
            ]
            for f in futures:
                f.result()

        if errors:
            raise errors[0]

        return True, total_length
    except Exception:
        _stream_logger.exception('stream download failed')
        return False, 0


def _download_stream_single(
    url: str,
    dest_path: str,
    on_progress: Callable[[int, int], None] | None,
    start_byte: int,
    request_headers: Dict,
    data: Optional[Dict],
) -> tuple[bool, int]:
    if start_byte > 0:
        request_headers = request_headers.copy()
        request_headers['Range'] = f'bytes={start_byte}-'

    downloaded = start_byte
    total_length = 0

    response = requests.get(
        url,
        headers=request_headers,
        data=data,
        stream=True,
        timeout=30,
    )
    response.raise_for_status()

    content_length = response.headers.get('content-length')
    if content_length:
        total_length = int(content_length) + start_byte

    mode = 'ab' if start_byte > 0 else 'wb'
    with open(dest_path, mode) as f:
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            f.write(chunk)
            f.flush()
            downloaded += len(chunk)
            if on_progress:
                on_progress(downloaded, total_length)
    return True, total_length
