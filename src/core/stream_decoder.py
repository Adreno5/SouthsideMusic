from __future__ import annotations

import logging
import os
import queue
import subprocess
import tempfile
import threading

import numpy as np

from core.downloader import downloadStream

_logger = logging.getLogger(__name__)

FIRST_CHUNK_TIMEOUT = 5.0
PCM_CHUNK_SIZE = 4096


class M4ANotStreamable(Exception):
    pass


class StreamDecoder:
    def __init__(
        self,
        url: str,
        sample_rate: int = 44100,
        channels: int = 2,
        duration_sec: float | None = None,
    ) -> None:
        self._url = url
        self._sample_rate = sample_rate
        self._channels = channels
        self._duration_sec = duration_sec
        self._pcm_queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=64)
        self._running = False
        self._error: Exception | None = None
        self._temp_path = ''
        self._total_frames = 0
        self._buffered_frames = 0
        self._bytes_per_frame = channels * 4
        self._first_chunk_ready = threading.Event()
        self._total_size = 0
        self._start_byte = 0
        self._downloaded_bytes = 0

    @property
    def temp_path(self) -> str:
        return self._temp_path

    @property
    def total_frames(self) -> int:
        return self._total_frames

    @property
    def buffered_frames(self) -> int:
        return self._buffered_frames

    def start(self, block: bool = True) -> None:
        fd, self._temp_path = tempfile.mkstemp(suffix='.tmp')
        os.close(fd)
        self._running = True
        self._first_chunk_ready.clear()

        self._download_thread = threading.Thread(
            target=self._download_loop, daemon=True
        )
        self._decode_thread = threading.Thread(target=self._decode_loop, daemon=True)
        self._download_thread.start()
        self._decode_thread.start()

        if block and not self._first_chunk_ready.wait(FIRST_CHUNK_TIMEOUT):
            self._running = False
            if self._error is None:
                self._error = M4ANotStreamable()
            raise M4ANotStreamable()

    def is_buffering(self) -> bool:
        return self._buffered_frames < self._sample_rate

    def next_chunk(self) -> np.ndarray | None:
        while True:
            try:
                chunk = self._pcm_queue.get(timeout=0.1)
                if chunk is None:
                    return None
                self._buffered_frames -= chunk.shape[0]
                return chunk
            except queue.Empty:
                if self._error is not None:
                    raise self._error

    def stop(self) -> None:
        self._running = False
        if self._temp_path and os.path.exists(self._temp_path):
            try:
                os.remove(self._temp_path)
            except OSError:
                pass

    @property
    def buffer_ratio(self) -> float:
        if self._total_size > 0:
            return min(1.0, self._downloaded_bytes / self._total_size)
        return 0.0

    def reset_for_seek(self, start_byte: int) -> None:
        self._running = False
        if self._temp_path and os.path.exists(self._temp_path):
            try:
                os.remove(self._temp_path)
            except OSError:
                pass

        self._start_byte = start_byte
        self._pcm_queue = queue.Queue(maxsize=64)
        self._total_frames = 0
        self._buffered_frames = 0
        self._error = None
        self._first_chunk_ready.clear()
        self._total_size = 0
        self._downloaded_bytes = 0

    def _download_loop(self) -> None:
        def _progress(downloaded: int, total: int) -> None:
            self._downloaded_bytes = downloaded

        ok, total_size = downloadStream(
            self._url, self._temp_path, _progress, self._start_byte
        )
        if not ok:
            self._error = RuntimeError('stream download failed')
            self._pcm_queue.put(None)
            return
        self._total_size = max(self._total_size, total_size)

    def _decode_loop(self) -> None:
        import time

        time.sleep(0.3)

        if not os.path.exists(self._temp_path):
            self._error = RuntimeError('temp file not created by download thread')
            self._pcm_queue.put(None)
            return

        cmd = [
            'ffmpeg',
            '-v',
            '0',
            '-i',
            self._temp_path,
            '-f',
            'f32le',
            '-acodec',
            'pcm_f32le',
            '-ar',
            str(self._sample_rate),
            '-ac',
            str(self._channels),
            'pipe:1',
        ]

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout = proc.stdout
        if stdout is None:
            self._error = RuntimeError('ffmpeg stdout is None')
            self._pcm_queue.put(None)
            return

        chunk_bytes = PCM_CHUNK_SIZE * self._bytes_per_frame

        while self._running:
            raw = stdout.read(chunk_bytes)
            if not raw:
                break

            self._first_chunk_ready.set()

            samples = np.frombuffer(raw, dtype=np.float32)
            n_frames = len(samples) // self._channels
            if n_frames == 0:
                continue
            samples = samples[: n_frames * self._channels].reshape(
                n_frames, self._channels
            )
            self._total_frames += n_frames
            self._buffered_frames += n_frames
            self._pcm_queue.put(samples)

        self._pcm_queue.put(None)
        stdout.close()
        proc.wait()
