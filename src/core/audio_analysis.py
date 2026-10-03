from __future__ import annotations

import numpy as np
from scipy.fft import rfft, rfftfreq


class SpectrumAnalyzer:
    def __init__(self) -> None:
        self._history = np.empty(0, dtype=np.float32)
        self._window = np.empty(0, dtype=np.float64)
        self._input = np.empty(0, dtype=np.float64)
        self._frequencies = np.empty(0, dtype=np.float64)
        self._sample_rate = 0
        self._write_index = 0
        self._count = 0

    def reset(self) -> None:
        self._history.fill(0)
        self._write_index = 0
        self._count = 0

    def process(
        self, samples: np.ndarray, size: int, sample_rate: int
    ) -> tuple[np.ndarray, np.ndarray]:
        size = max(1, int(size))
        size_changed = size != len(self._history)
        if size_changed:
            previous = np.concatenate((
                self._history[self._write_index :],
                self._history[: self._write_index],
            ))
            count = min(self._count, size)
            self._history = np.zeros(size, dtype=np.float32)
            if count:
                self._history[-count:] = previous[-count:]
            self._count = count
            self._write_index = 0
            self._window = np.hanning(size)
            self._input = np.empty(size, dtype=np.float64)
        if size_changed or sample_rate != self._sample_rate:
            self._frequencies = rfftfreq(size, 1.0 / sample_rate)
            self._frequencies.flags.writeable = False
            self._sample_rate = sample_rate

        count = len(samples)
        if count >= size:
            self._history[:] = samples[-size:]
            self._write_index = 0
            self._count = size
        elif count:
            index = self._write_index
            first = min(count, size - index)
            self._history[index : index + first] = samples[:first]
            remaining = count - first
            if remaining:
                self._history[:remaining] = samples[first:]
            self._write_index = (index + count) % size
            self._count = min(size, self._count + count)

        index = self._write_index
        first = size - index
        np.multiply(
            self._history[index:], self._window[:first], out=self._input[:first]
        )
        if index:
            np.multiply(
                self._history[:index], self._window[first:], out=self._input[first:]
            )
        magnitudes = np.abs(rfft(self._input, overwrite_x=True))
        return self._frequencies, magnitudes
