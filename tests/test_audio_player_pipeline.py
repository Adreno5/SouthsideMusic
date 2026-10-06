from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import numpy as np
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtWidgets import QApplication

from core.audio_analysis import SpectrumAnalyzer
from core.audio_player import AudioPlayer, DevicesInfo, PreparedAudioBuffer
from core.audio_processing import AudioProcessor
from core.config import cfg


class OutputStream:
    def __init__(self, **settings: Any) -> None:
        self.active = False
        self.closed = False

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False

    def abort(self) -> None:
        self.active = False

    def close(self) -> None:
        self.closed = True
        self.active = False


def waitUntil(predicate: Callable[[], bool], app: QApplication) -> None:
    deadline = time.perf_counter() + 3.0
    while not predicate() and time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.001)
    assert predicate()


def checkSpectrum() -> None:
    rng = np.random.default_rng(42)
    analyzer = SpectrumAnalyzer()
    for rate in (44100, 96000):
        analyzer.reset()
        history = np.empty(0, dtype=np.float32)
        for size in (4096, 4097, 31, 8192, 1, 2048):
            for count in (0, 1, 37, 512, 4096, 9000):
                samples = rng.normal(size=count).astype(np.float32)
                history = np.concatenate((history, samples))[-size:]
                padded = np.zeros(size, dtype=np.float32)
                if len(history):
                    padded[-len(history) :] = history
                freqs, magnitudes = analyzer.process(samples, size, rate)
                expected = np.abs(np.fft.rfft(padded * np.hanning(size)))
                np.testing.assert_allclose(magnitudes, expected, atol=1e-12)
                np.testing.assert_array_equal(freqs, np.fft.rfftfreq(size, 1.0 / rate))
                assert freqs.dtype == magnitudes.dtype == np.float64
                retained = magnitudes.copy()
                analyzer.process(np.empty(0, np.float32), size, rate)
                np.testing.assert_array_equal(magnitudes, retained)
    frequencies, _ = analyzer.process(np.zeros(100), 4096, 48000)
    window, scratch, storage = analyzer._window, analyzer._input, analyzer._history
    next_frequencies, _ = analyzer.process(np.ones(100), 4096, 48000)
    assert next_frequencies is frequencies
    assert analyzer._window is window
    assert analyzer._input is scratch
    assert analyzer._history is storage
    analyzer.reset()
    _, silent = analyzer.process(np.empty(0, np.float32), 4096, 48000)
    np.testing.assert_array_equal(silent, 0.0)


def checkCallback(player: AudioPlayer) -> None:
    player.stop_fft_thread()
    source = np.full((44100 * 10, 2), 0.4, np.float32)
    player.loadPrepared(PreparedAudioBuffer(source, 44100, 2))
    player.volume_gain = 0.5
    player.loudness_gain = 0.25
    time_info = SimpleNamespace(outputBufferDacTime=0.0, currentTime=0.0)
    status = SimpleNamespace(output_underflow=False)
    out = np.empty((4096, 2), np.float32)
    for _ in range(16):
        player._audio_queue.put((source[:4096].copy(), 4096, None))
        player._audio_callback(out, 4096, time_info, status)
        np.testing.assert_allclose(out, 0.05)
    np.testing.assert_array_equal(source, np.float32(0.4))
    assert player.current_index == 16 * 4096
    assert abs(player.db - 20 * np.log10(0.05)) < 1e-5
    packets = list(player.fft_queue.queue)
    assert len(packets) == player.fft_queue.maxsize
    assert all(packet is not None for packet in packets)
    assert [packet[1] for packet in packets if packet is not None] == list(
        range(player._analysis_sequence - 7, player._analysis_sequence + 1)
    )
    last_packet = packets[-1]
    assert last_packet is not None
    spectrum_input = last_packet[4].copy()
    out.fill(0.0)
    np.testing.assert_array_equal(last_packet[4], spectrum_input)
    player._audio_callback(out, 4096, time_info, status)
    np.testing.assert_array_equal(out, 0.0)
    assert player._queue_underruns == 1

    player.loadPrepared(PreparedAudioBuffer(source, 44100, 2))
    previous = player.stream
    assert previous is not None
    previous.start()
    player._audio_queue.put(None)
    try:
        player._audio_callback(out, 4096, time_info, status)
    except sd.CallbackStop:
        pass
    else:
        raise AssertionError('EOF must stop the callback')
    assert player.stream is None and not previous.closed
    player._closeFinishedStreams()
    assert not previous.closed
    previous.active = False
    player._emitPlaybackTelemetry()
    assert previous.closed


def checkProducerIsolation(player: AudioPlayer, app: QApplication) -> None:
    source = np.full((44100 * 2, 2), 0.1, np.float32)
    player.loadPrepared(PreparedAudioBuffer(source, 44100, 2))
    entered = threading.Event()
    release = threading.Event()
    original_render = AudioProcessor.render

    def stalledRender(
        processor: AudioProcessor, start: int, frames: int
    ) -> tuple[np.ndarray, int]:
        if processor.samples is source:
            entered.set()
            assert release.wait(3.0)
        return original_render(processor, start, frames)

    with (
        patch.object(AudioProcessor, 'render', stalledRender),
        ThreadPoolExecutor(max_workers=1) as callbacks,
    ):
        player.volume_gain = player.loudness_gain = 1.0
        player._startProducer()
        old_thread = player._producer_thread
        try:
            assert entered.wait(2.0)
            player._audio_queue.put((source[:4096].copy(), 4096, None))
            callback = callbacks.submit(
                player._audio_callback,
                np.empty((4096, 2), np.float32),
                4096,
                SimpleNamespace(outputBufferDacTime=0.0, currentTime=0.0),
                SimpleNamespace(output_underflow=False),
            )
            callback.result(timeout=1.0)
            replacement = np.full((44100 * 2, 2), 0.2, np.float32)
            player.loadPrepared(PreparedAudioBuffer(replacement, 44100, 2))
            player._startProducer()
            waitUntil(lambda: not player._audio_queue.empty(), app)
        finally:
            release.set()
            if old_thread is not None:
                old_thread.join(2.0)
                assert not old_thread.is_alive()
            player._stopProducer()
        for packet in list(player._audio_queue.queue):
            if packet is not None:
                np.testing.assert_array_equal(packet[0], np.float32(0.2))


def checkStaleSpectrum(player: AudioPlayer, app: QApplication) -> None:
    frames: list[np.ndarray] = []
    player.fftDataReady.connect(
        lambda _frequencies, magnitudes: frames.append(magnitudes)
    )
    old_generation = player._analysis_generation
    pending = threading.Thread(
        target=lambda: player._spectrumReady.emit(
            old_generation, np.ones(5), np.ones(5), np.ones(5)
        )
    )
    pending.start()
    pending.join(2.0)
    assert not pending.is_alive()
    source = np.ones((8192, 2), np.float32)
    player.loadPrepared(PreparedAudioBuffer(source, 48000, 2))
    app.processEvents()
    assert not frames
    player._publishSpectrum(
        player._analysis_generation, np.ones(5), np.ones(5), np.ones(5)
    )
    assert len(frames) == 1
    player.fft_enabled = False
    player._publishSpectrum(
        player._analysis_generation, np.ones(5), np.ones(5), np.ones(5)
    )
    assert len(frames) == 1
    player.fft_enabled = True
    app.processEvents()


def checkAnalysisWorker(app: QApplication) -> None:
    player = AudioPlayer(devices=[DevicesInfo('Test', 0)])
    spectra: list[np.ndarray] = []
    beats: list[float] = []
    player.fftDataReady.connect(
        lambda _frequencies, magnitudes: spectra.append(magnitudes)
    )
    player.beatDataReady.connect(lambda intensity, _point: beats.append(intensity))
    original_process = SpectrumAnalyzer.process
    failed = False

    def interruptedAnalysis(
        analyzer: SpectrumAnalyzer, samples: np.ndarray, size: int, rate: int
    ) -> tuple[np.ndarray, np.ndarray]:
        nonlocal failed
        if not failed:
            failed = True
            raise ValueError('Interrupted analysis')
        return original_process(analyzer, samples, size, rate)

    try:
        source = np.zeros((4096, 2), np.float32)
        with (
            patch.object(SpectrumAnalyzer, 'process', interruptedAnalysis),
            patch.object(player._logger, 'exception') as errors,
        ):
            for sequence in (1, 2):
                player.fft_queue.put((
                    player._analysis_generation,
                    sequence,
                    44100,
                    time.perf_counter(),
                    source.mean(axis=1),
                    source,
                    source.mean(axis=1),
                ))
            waitUntil(lambda: bool(spectra), app)
            errors.assert_called_once_with('spectrum analysis failed')
            assert player.fft_thread.is_alive()
        beats.clear()
        with patch.object(cfg, 'beat_detection_enabled', True):
            player._resetBeatAnalysis()
            player.fft_queue.put((
                player._analysis_generation,
                3,
                44100,
                time.perf_counter(),
                source.mean(axis=1),
                source,
                source.mean(axis=1),
            ))
            waitUntil(lambda: bool(beats), app)
            assert all(intensity == 0.0 for intensity in beats)
    finally:
        player.shutdown()
        app.processEvents()
    assert not player.fft_thread.is_alive()


def main() -> None:
    app = QApplication.instance() or QApplication([])
    checkSpectrum()
    with (
        patch('core.audio_player.sd.OutputStream', OutputStream),
        patch.object(cfg, 'skip_nosound', False),
        patch.object(cfg, 'stereo', True),
        patch.object(cfg, 'stereo_haas_index', 0),
        patch.object(cfg, 'enable_reverb', False),
        patch.object(cfg, 'play_speed', 1.0),
        patch.object(cfg, 'play_pitch', 0.0),
        patch.object(cfg, 'beat_detection_enabled', False),
    ):
        player = AudioPlayer(devices=[DevicesInfo('Test', 0)])
        try:
            checkCallback(player)
            checkProducerIsolation(player, app)
            checkStaleSpectrum(player, app)
        finally:
            player.shutdown()
            app.processEvents()
        assert not player.fft_thread.is_alive()
        assert not player._finished_streams
        checkAnalysisWorker(app)
    print('Audio pipeline checks passed')


if __name__ == '__main__':
    main()
