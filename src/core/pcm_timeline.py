from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PcmBlock:
    start: int
    samples: np.ndarray

    @property
    def end(self) -> int:
        return self.start + len(self.samples)


class PcmTimeline:
    """An absolute-frame PCM sequence whose consumed blocks can be released."""

    def __init__(self, channels: int, start: int = 0) -> None:
        self.channels = channels
        self.end = start
        self.blocks: deque[PcmBlock] = deque()

    def append(self, samples: np.ndarray, gain: float = 1.0) -> None:
        if samples.ndim != 2 or samples.shape[1] != self.channels:
            raise ValueError('PCM channel count does not match the timeline')
        for offset in range(0, len(samples), 65536):
            # Own each block so releasing a prefix also releases its allocation.
            chunk = (samples[offset : offset + 65536] * gain).astype(
                np.float32, copy=True
            )
            self.blocks.append(PcmBlock(self.end, chunk))
            self.end += len(chunk)

    def read(self, start: int, stop: int) -> np.ndarray:
        stop = min(stop, self.end)
        if stop <= start:
            return np.zeros((0, self.channels), dtype=np.float32)
        result = np.empty((stop - start, self.channels), dtype=np.float32)
        copied = 0
        for block in self.blocks:
            if block.end <= start:
                continue
            if block.start >= stop:
                break
            left, right = max(start, block.start), min(stop, block.end)
            result[left - start : right - start] = block.samples[
                left - block.start : right - block.start
            ]
            copied += right - left
        if copied != len(result):
            raise ValueError('Requested PCM has already been released')
        return result

    def replaceFrom(self, start: int, tail: PcmTimeline) -> None:
        if start > self.end or tail.channels != self.channels:
            raise ValueError('Invalid PCM splice')
        if tail.blocks and tail.blocks[0].start != start:
            raise ValueError('PCM splice must use matching absolute frames')
        while self.blocks and self.blocks[-1].start >= start:
            self.blocks.pop()
        if self.blocks and self.blocks[-1].end > start:
            block = self.blocks.pop()
            self.blocks.append(
                PcmBlock(block.start, block.samples[: start - block.start].copy())
            )
        self.blocks.extend(tail.blocks)
        self.end = tail.end

    def discardBefore(self, frame: int) -> None:
        while self.blocks and self.blocks[0].end <= frame:
            self.blocks.popleft()
