from __future__ import annotations

from collections.abc import Callable

import numpy as np

_MIN_HOP = 256
_HOP_DIVISOR = 43
_MIN_SEARCH = 32
_SEARCH_DIVISOR = 125
_CENTER_BIAS = 0.12
_RESET_TOLERANCE = 16

ReadSamples = Callable[[int, int], np.ndarray]
SampleCount = Callable[[], int]


def hopSize(sample_rate: int) -> int:
    return max(_MIN_HOP, sample_rate // _HOP_DIVISOR)


def searchSize(hop: int, sample_rate: int) -> int:
    return min(hop // 2, max(_MIN_SEARCH, sample_rate // _SEARCH_DIVISOR))


class WsolaStretcher:
    def __init__(self, readSamples: ReadSamples, sampleCount: SampleCount) -> None:
        self._readSamples = readSamples
        self._sampleCount = sampleCount
        self._output_buffer: np.ndarray | None = None
        self._tail: np.ndarray | None = None
        self._buffer_start_index = 0.0
        self._next_source_index = 0.0
        self._speed = 1.0

    def reset(self) -> None:
        self._output_buffer = None
        self._tail = None
        self._buffer_start_index = 0.0
        self._next_source_index = 0.0
        self._speed = 1.0

    def needsReset(self, start_index: int, speed: float, channels: int) -> bool:
        if self._output_buffer is None:
            return True
        if self._output_buffer.ndim != 2:
            return True
        if self._output_buffer.shape[1] != channels:
            return True
        if abs(speed - self._speed) >= 1e-6:
            return True
        expected_start = int(round(self._buffer_start_index))
        return abs(start_index - expected_start) > _RESET_TOLERANCE

    def resetFor(self, start_index: int, speed: float, channels: int) -> None:
        self._output_buffer = np.zeros((0, channels), dtype=np.float32)
        self._tail = None
        self._buffer_start_index = float(start_index)
        self._next_source_index = float(start_index)
        self._speed = speed

    def _readFrame(self, start: int, frames: int, channels: int) -> np.ndarray:
        n = self._sampleCount()
        if n == 0 or frames <= 0:
            return np.zeros((0, channels), dtype=np.float32)

        start = max(0, min(start, n))
        end = min(start + frames, n)
        segment = self._readSamples(start, end)
        if len(segment) >= frames:
            return segment.astype(np.float32, copy=False)

        if len(segment) > 0:
            pad_frame = segment[-1:]
        else:
            pad_frame = self._readSamples(n - 1, n)
        padding = np.repeat(pad_frame, frames - len(segment), axis=0)
        return np.concatenate((segment, padding), axis=0).astype(np.float32, copy=False)

    def _findStart(self, ideal_start: int, overlap: int, search: int) -> int:
        tail = self._tail
        n = self._sampleCount()
        if tail is None or len(tail) < overlap or n <= overlap:
            return max(0, min(ideal_start, n))

        min_start = max(0, ideal_start - search)
        max_start = min(n - overlap, ideal_start + search)
        if max_start < min_start:
            return max(0, min(ideal_start, n))

        tail_segment = tail[:overlap].astype(np.float32, copy=False)
        tail_segment = tail_segment - tail_segment.mean(axis=0, keepdims=True)
        channel_energy = np.sum(tail_segment * tail_segment, axis=0)
        channel = int(np.argmax(channel_energy))
        tail_channel = tail_segment[:, channel]
        tail_power = float(np.sqrt(channel_energy[channel]))
        if tail_power < 1e-6:
            return max(0, min(ideal_start, n))

        source = self._readSamples(min_start, max_start + overlap)[:, channel]
        windows = np.lib.stride_tricks.sliding_window_view(source, overlap)
        centered = windows - windows.mean(axis=1, keepdims=True)
        powers = np.sqrt(np.sum(centered * centered, axis=1))
        scores = centered @ tail_channel
        scores /= np.maximum(powers * tail_power, 1e-6)
        positions = np.arange(len(scores), dtype=np.float32) + min_start
        center_bias = np.abs(positions - ideal_start) / max(1, search)
        scores -= center_bias * _CENTER_BIAS
        return min_start + int(np.argmax(scores))

    def readHop(
        self, ideal_source_index: float, sample_rate: int, channels: int
    ) -> np.ndarray:
        hop = hopSize(sample_rate)
        if hop <= 0:
            return np.zeros((0, channels), dtype=np.float32)
        frame_size = hop * 2

        if self._tail is None:
            segment = self._readFrame(
                int(round(ideal_source_index)), frame_size, channels
            )
            if len(segment) == 0:
                return np.zeros((0, channels), dtype=np.float32)
            self._tail = segment[hop:frame_size].copy()
            return segment[:hop].astype(np.float32, copy=False)

        if ideal_source_index >= self._sampleCount():
            tail = self._tail
            self._tail = None
            return tail

        search = searchSize(hop, sample_rate)
        source_start = self._findStart(int(round(ideal_source_index)), hop, search)
        segment = self._readFrame(source_start, frame_size, channels)
        if len(segment) == 0:
            return np.zeros((0, channels), dtype=np.float32)

        fade_in = (
            0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, hop, dtype=np.float32))
        ).reshape(-1, 1)
        mixed = self._tail * (1.0 - fade_in) + segment[:hop] * fade_in
        self._tail = segment[hop:frame_size].copy()
        return mixed.astype(np.float32, copy=False)

    def read(
        self,
        start_index: int,
        frames: int,
        speed: float,
        sample_rate: int,
        channels: int,
    ) -> np.ndarray:
        n = self._sampleCount()
        if n == 0 or start_index >= n:
            return np.zeros((0, channels), dtype=np.float32)

        if abs(speed - 1.0) < 1e-6:
            self.reset()
            return self._readSamples(start_index, start_index + frames).copy()

        if frames <= 0:
            return np.zeros((0, channels), dtype=np.float32)

        start_index = max(0, start_index)
        if self.needsReset(start_index, speed, channels):
            self.resetFor(start_index, speed, channels)

        hop = hopSize(sample_rate)
        if hop <= 0:
            return self._readSamples(start_index, start_index + frames).copy()

        while self._output_buffer is not None and len(self._output_buffer) < frames:
            chunk = self.readHop(self._next_source_index, sample_rate, channels)
            if len(chunk) == 0:
                break
            self._output_buffer = np.concatenate((self._output_buffer, chunk), axis=0)
            self._next_source_index += hop * speed

        buffer = self._output_buffer
        if buffer is None or len(buffer) == 0:
            return np.zeros((0, channels), dtype=np.float32)

        out = buffer[:frames].copy()
        self._output_buffer = (
            buffer[frames:].copy()
            if len(buffer) > frames
            else np.zeros((0, channels), dtype=np.float32)
        )
        self._buffer_start_index += len(out) * speed
        return out.astype(np.float32, copy=False)

    def warp(
        self, source_positions: np.ndarray, sample_rate: int, channels: int
    ) -> np.ndarray:
        self.reset()
        total = len(source_positions)
        hop = hopSize(sample_rate)
        if total <= 0 or hop <= 0:
            return np.zeros((0, channels), dtype=np.float32)

        chunks: list[np.ndarray] = []
        for index in range(0, total, hop):
            chunk = self.readHop(float(source_positions[index]), sample_rate, channels)
            if len(chunk) == 0:
                break
            chunks.append(chunk)
        if not chunks:
            return np.zeros((0, channels), dtype=np.float32)

        rendered = np.concatenate(chunks, axis=0)
        if len(rendered) < total:
            rendered = np.concatenate(
                (rendered, np.repeat(rendered[-1:], total - len(rendered), axis=0)),
                axis=0,
            )
        return rendered[:total].astype(np.float32, copy=True)
