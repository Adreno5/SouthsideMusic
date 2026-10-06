from __future__ import annotations

import io
import os
import tempfile
import threading
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import numpy as np


class PcmFile:
    def __init__(self) -> None:
        self._files = ExitStack()
        directory = Path(__file__).resolve().parents[2] / 'data' / 'pcm'
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory) as file:
            self.file = self._files.enter_context(
                io.BufferedRandom(io.FileIO(os.dup(file.fileno()), 'r+'))
            )
        self.lock = threading.RLock()

    def __del__(self) -> None:
        self._files.close()

    def read(self, start: int, size: int) -> bytes:
        with self.lock:
            self.file.seek(start)
            return self.file.read(size)

    def append(self, data: bytes) -> None:
        with self.lock:
            self.file.seek(0, os.SEEK_END)
            self.file.write(data)
            self.file.flush()


@dataclass
class PcmBuffer:
    storage: PcmFile
    channels: int
    frames: int = 0
    offset: int = 0

    @property
    def shape(self) -> tuple[int, int]:
        return self.frames, self.channels

    @property
    def ndim(self) -> int:
        return 2

    def __len__(self) -> int:
        return self.frames

    def __getitem__(self, key: slice) -> np.ndarray:
        start, stop, step = key.indices(self.frames)
        if step != 1:
            raise ValueError('PCM reads require consecutive frames')
        raw = self.storage.read(
            (self.offset + start) * self.channels * 4,
            max(0, stop - start) * self.channels * 4,
        )
        return np.frombuffer(raw, dtype='<f4').reshape(-1, self.channels)

    def view(self, start: int, stop: int) -> PcmBuffer:
        start = max(0, min(start, self.frames))
        stop = max(start, min(stop, self.frames))
        return PcmBuffer(self.storage, self.channels, stop - start, self.offset + start)

    def append(self, samples: np.ndarray) -> None:
        if self.offset or samples.ndim != 2 or samples.shape[1] != self.channels:
            raise ValueError('Invalid PCM append')
        self.storage.append(np.asarray(samples, dtype='<f4').tobytes())
        self.frames += len(samples)

    @classmethod
    def fromSamples(cls, samples: np.ndarray) -> PcmBuffer:
        result = cls(PcmFile(), samples.shape[1])
        for start in range(0, len(samples), 65536):
            result.append(samples[start : start + 65536])
        return result
