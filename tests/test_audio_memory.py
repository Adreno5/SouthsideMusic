from __future__ import annotations

import base64
import gc
import io
import subprocess
import sys
import tempfile
import time
import tracemalloc
import unittest
import wave
import weakref
from math import gcd
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtWidgets import QApplication
from scipy.io import wavfile
from scipy.signal import resample_poly

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from test_audio_player_sample_rate import OutputStream

from core.audio_decode import (
    FileAudioSegment,
    PatchedAudioSegment,
    cacheDecodedAudio,
    decodeAudioWithSidecar,
    getCachedAudio,
)
from core.audio_player import AudioPlayer, DevicesInfo, PreparedAudioBuffer
from core.audio_processing import AudioProcessingSettings, AudioProcessor
from core.config import cfg
from core.crossfade import getCrossfade
from core.free_threaded_worker import FreeThreadedJsonSender
from core.loudness import Meter, getAdjustedGainFactor
from core.models import SongStorable
from core.pcm_buffer import PcmBuffer
from core.pcm_timeline import PcmTimeline
from core.playing_manager import PlayingManager


class AudioMemoryTests(unittest.TestCase):
    def _audio(self, rate: int, width: int, channels: int = 2) -> FileAudioSegment:
        rng = np.random.default_rng(123)
        samples = rng.integers(
            -(2 ** (width * 8 - 2)),
            2 ** (width * 8 - 2),
            size=rate * 2 * channels,
            dtype={1: np.int8, 2: np.int16, 4: np.int32}[width],
        )
        audio = PatchedAudioSegment(
            data=samples.tobytes(),
            sample_width=width,
            frame_rate=rate,
            channels=channels,
        )
        output = io.BytesIO()
        audio.export(output, format='wav')
        decoded = PatchedAudioSegment.from_file(output)
        self.assertIsInstance(decoded, FileAudioSegment)
        self.assertEqual(decoded.raw_data, audio.raw_data)
        return decoded

    def testDecodeAndPreparationPreserveSamples(self) -> None:
        for width in (1, 2, 4):
            with self.subTest(width=width):
                audio = self._audio(48000, width)
                prepared = AudioPlayer.prepareBuffer(audio)
                self.assertIsInstance(prepared.samples, PcmBuffer)
                expected = AudioPlayer._prepareSamples(
                    PatchedAudioSegment(
                        data=audio.raw_data,
                        sample_width=width,
                        frame_rate=audio.frame_rate,
                        channels=audio.channels,
                    )
                )
                np.testing.assert_array_equal(prepared.samples[:], expected)
                self.assertIs(
                    AudioPlayer.prepareBuffer(audio).samples, prepared.samples
                )
                self.assertEqual(audio[-500:].raw_data, audio.readPcm(72000, 96000))

    def testDecodeDoesNotSendEntireAudioThroughWorker(self) -> None:
        audio = PatchedAudioSegment.silent(duration=100, frame_rate=48000)
        output = io.BytesIO()
        audio.export(output, format='wav')
        worker = Mock()
        decoded = decodeAudioWithSidecar(output, worker)
        self.assertIsInstance(decoded, FileAudioSegment)
        worker.call.assert_not_called()

    def testDecodePreserves24BitAndFloatSources(self) -> None:
        rng = np.random.default_rng(15)
        samples = rng.integers(-(2**23), 2**23, (48000, 2), dtype=np.int32)
        raw = samples.astype('<i4').view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
        output = io.BytesIO()
        with wave.open(output, 'wb') as writer:
            writer.setnchannels(2)
            writer.setsampwidth(3)
            writer.setframerate(48000)
            writer.writeframes(raw)
        decoded = PatchedAudioSegment.from_file(output)
        expected = PatchedAudioSegment(
            data=raw, sample_width=3, frame_rate=48000, channels=2
        )
        self.assertEqual(decoded.sample_width, 4)
        self.assertEqual(decoded.raw_data, expected.raw_data)

        floats = np.array([[-0.75, 0.25], [0.5, -0.125]], dtype=np.float32)
        output = io.BytesIO()
        wavfile.write(output, 48000, floats)
        decoded = PatchedAudioSegment.from_file(output)
        self.assertEqual(decoded.sample_width, 4)
        actual = np.frombuffer(decoded.raw_data, dtype='<i4').reshape(-1, 2) / 2**31
        np.testing.assert_array_equal(actual, floats)

    def testDecodeHonorsTimeoutAndReleasesOutput(self) -> None:
        with self.assertRaises(subprocess.TimeoutExpired):
            decodeAudioWithSidecar(b'', timeout=0)
        with patch(
            'core.audio_decode.subprocess.run',
            side_effect=[
                subprocess.CompletedProcess([], 0, b'{}', b''),
                subprocess.TimeoutExpired('ffmpeg', 0.25),
            ],
        ) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                decodeAudioWithSidecar(b'', timeout=0.25)
            self.assertEqual(run.call_count, 2)
            timeouts = [call.kwargs['timeout'] for call in run.call_args_list]
            self.assertGreater(timeouts[1], 0)
            self.assertLessEqual(timeouts[1], timeouts[0])
            self.assertLessEqual(timeouts[0], 0.25)
            output = run.call_args_list[1].kwargs['stdout']
        gc.collect()
        self.assertTrue(output.closed)

    def testFileGainPreservesSamplesWithoutFullRead(self) -> None:
        for width in (1, 2, 4):
            audio = self._audio(48000, width)
            normal = PatchedAudioSegment(
                data=audio.raw_data,
                sample_width=width,
                frame_rate=audio.frame_rate,
                channels=audio.channels,
            )
            for gain in (-6.0, 6.0):
                with self.subTest(width=width, gain=gain):
                    with patch.object(audio, 'readPcm', wraps=audio.readPcm) as reads:
                        actual = audio.apply_gain(gain)
                    self.assertIsInstance(actual, FileAudioSegment)
                    self.assertEqual(actual.raw_data, normal.apply_gain(gain).raw_data)
                    self.assertTrue(
                        all(
                            stop - start <= 65536
                            for start, stop in (
                                call.args for call in reads.call_args_list
                            )
                        )
                    )

    def testBoundedCrossfadePreservesAbsoluteTimingAndSamples(self) -> None:
        app = QApplication.instance() or QApplication([])
        rate = 16000
        samples = (np.sin(np.arange(rate * 40) * 2 * np.pi * 440 / rate) * 8000).astype(
            '<i2'
        )
        normal = PatchedAudioSegment(
            data=np.repeat(samples[:, None], 2, axis=1).tobytes(),
            sample_width=2,
            frame_rate=rate,
            channels=2,
        )
        output = io.BytesIO()
        normal.export(output, format='wav')
        disk = PatchedAudioSegment.from_file(output)
        manager = PlayingManager(None)
        self.addCleanup(manager._crossfade_debounce_timer.stop)
        self.addCleanup(manager._ft_worker.shutdown)
        manager._logger = Mock()
        manager.ctx = SimpleNamespace(player=SimpleNamespace(sample_rate=rate))
        manager._lyricCrossfadeSeconds = Mock(return_value=4.0)
        manager._computeCrossfadeInfoInWorker = Mock(return_value=None)
        with (
            patch.object(cfg, 'enable_crossfade', True),
            patch.object(cfg, 'crossfade_strength', 0.5),
            patch.object(cfg, 'crossfade_max_duration', 24.0),
            patch.object(cfg, 'crossfade_curve', 'equal_power'),
            patch.object(cfg, 'crossfade_bpm_window', 15),
            patch.object(cfg, 'crossfade_tempo_match', False),
            patch.object(cfg, 'crossfade_key_match', False),
            patch.object(cfg, 'crossfade_agc', False),
        ):
            expected = getCrossfade(
                normal,
                normal,
                4.0,
                0.5,
                tempo_match=False,
                current_gain=0.8,
                next_gain=0.6,
            )
            with patch.object(disk, 'readPcm', wraps=disk.readPcm) as reads:
                actual = manager._computeCrossfadeInfo(
                    disk, disk, current_gain=0.8, next_gain=0.6
                )
            self.assertIsNotNone(actual)
            self.assertEqual(actual.start_seconds, expected.start_seconds)
            self.assertGreater(actual.start_seconds, 30)
            self.assertEqual(actual.fade_seconds, expected.fade_seconds)
            np.testing.assert_array_equal(actual.samples, expected.samples)
            self.assertTrue(
                all(
                    stop - start <= rate * 30
                    for start, stop in (call.args for call in reads.call_args_list)
                )
            )
            worker_args = manager._computeCrossfadeInfoInWorker.call_args.args
            self.assertEqual(len(worker_args[0]), 30000)
            self.assertEqual(len(worker_args[1]), 30000)
            self.assertEqual(worker_args[5], 40.0)
        app.processEvents()

    def testResamplingAcrossChunkBoundaries(self) -> None:
        rng = np.random.default_rng(10)
        for source_rate, target_rate in (
            (44100, 48000),
            (96000, 44100),
            (192000, 48000),
        ):
            with self.subTest(source=source_rate, target=target_rate):
                samples = rng.normal(0, 0.1, (source_rate * 2 + 57, 2)).astype(
                    np.float32
                )
                source = PcmBuffer.fromSamples(samples)
                prepared = AudioPlayer.convertBuffer(
                    PreparedAudioBuffer(source, source_rate, 2), target_rate
                )
                factor = gcd(source_rate, target_rate)
                expected = resample_poly(
                    samples, target_rate // factor, source_rate // factor, axis=0
                )
                np.testing.assert_allclose(prepared.samples[:], expected, atol=1e-7)

    def testDiskSamplesPreserveSpeedPitchAndEffects(self) -> None:
        samples = (
            np.random.default_rng(13).normal(0, 0.05, (44100 * 4, 2)).astype(np.float32)
        )
        processors = [AudioProcessor(), AudioProcessor()]
        for processor, source in zip(
            processors, (samples, PcmBuffer.fromSamples(samples)), strict=True
        ):
            processor.samples = source
            processor.sample_rate = 44100
            processor.channels = 2
            processor.settings = AudioProcessingSettings(
                1.25, 3.0, False, True, 0, True, 0.2
            )
        start = 0
        for _ in range(40):
            expected, frames = processors[0].render(start, 2048)
            actual, actual_frames = processors[1].render(start, 2048)
            self.assertEqual(frames, actual_frames)
            np.testing.assert_array_equal(actual, expected)
            start += frames

    def testTimelineSharesFilesAndRestoresOriginalTail(self) -> None:
        source = PcmBuffer.fromSamples(np.full((200000, 2), 0.5, np.float32))
        next_source = PcmBuffer.fromSamples(np.full((150000, 2), 0.2, np.float32))
        timeline = PcmTimeline(2)
        timeline.append(source, 0.8)
        restore = timeline.slice(160000, 200000)
        tail = PcmTimeline(2, 160000)
        tail.append(next_source, 0.5)
        timeline.replaceFrom(160000, tail)
        self.assertIs(timeline.blocks[0].samples.storage, source.storage)
        self.assertIs(timeline.blocks[-1].samples.storage, next_source.storage)
        np.testing.assert_allclose(timeline.read(159990, 160000), 0.4)
        np.testing.assert_allclose(timeline.read(160000, 160010), 0.1)
        timeline.replaceFrom(160000, restore)
        np.testing.assert_allclose(timeline.read(159990, 160010), 0.4)
        timeline.discardBefore(160000)
        self.assertEqual(timeline.end, 200000)

    def testCacheAndViewsReleaseTemporaryFiles(self) -> None:
        audio = self._audio(44100, 2)
        cacheDecodedAudio('memory-test', audio)
        audio_ref = weakref.ref(audio)
        self.assertIs(getCachedAudio('memory-test'), audio)
        source = AudioPlayer.prepareBuffer(audio).samples
        storage_ref = weakref.ref(source.storage)
        view = source.view(100, 200)
        file = source.storage.file
        del audio, source
        gc.collect()
        self.assertIsNone(audio_ref())
        self.assertIsNone(getCachedAudio('memory-test'))
        self.assertIsNotNone(storage_ref())
        self.assertFalse(file.closed)
        self.assertEqual(view[:].shape, (100, 2))
        del view
        gc.collect()
        self.assertIsNone(storage_ref())
        self.assertTrue(file.closed)

    def testMusicPathMigratesLegacyCacheWithoutReadingWholeFile(self) -> None:
        directory = Path(__file__).resolve().parents[1] / 'data' / 'pcm'
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            root = Path(temporary).resolve()
            self.assertTrue(root.is_relative_to(directory.resolve()))
            legacy = root / 'legacy'
            legacy.mkdir()
            original = legacy / 'test-song'
            original.write_bytes(b'cached compressed audio')
            song = object.__new__(SongStorable)
            song.id = 'test-song'
            song.name = 'Test'
            song.content_cache_hash = 'test-song'
            with (
                patch('core.models.MUSIC_DATA_DIR', str(root / 'music')),
                patch('core.models.LEGACY_MUSIC_CACHE_DIR', str(legacy)),
                patch('core.models.IMAGE_DATA_DIR', str(root / 'images')),
                patch('core.models.LYRIC_DATA_DIR', str(root / 'lyrics')),
            ):
                with patch(
                    'builtins.open',
                    side_effect=AssertionError('Playback must pass a path to ffmpeg'),
                ):
                    path = song.getMusicPath()
                self.assertEqual(Path(path), root / 'music' / 'test-song')
                self.assertFalse(original.exists())
                self.assertEqual(song.getMusicBytes(), b'cached compressed audio')
                Path(path).unlink()
                with self.assertRaises(FileNotFoundError):
                    song.getMusicPath()

    def testFilePlaybackQueueAndScrub(self) -> None:
        app = QApplication.instance() or QApplication([])
        audio = self._audio(96000, 2)
        following = self._audio(48000, 2)
        prepared = AudioPlayer.prepareBuffer(audio)
        with patch('core.audio_player.sd.OutputStream', OutputStream):
            player = AudioPlayer(devices=[DevicesInfo('Test', 0)])
            player._startProducer = Mock()
            try:
                player.loadPrepared(prepared)
                player.setGain(0.75)
                boundary = player.queueNext(
                    1.5, None, AudioPlayer.prepareBuffer(following), 0.5
                )
                self.assertIsNotNone(boundary)
                self.assertTrue(player.beginScrub(0.5, audio))
                output = np.zeros((2048, 2), np.float32)
                for block in range(32):
                    start = 48000 + block * 2048
                    with patch.object(
                        prepared.samples.storage,
                        'read',
                        side_effect=AssertionError(
                            'Audio callback must not read files'
                        ),
                    ):
                        player._renderAudio(
                            output,
                            2048,
                            SimpleNamespace(outputBufferDacTime=0, currentTime=0),
                            SimpleNamespace(output_underflow=False),
                        )
                    np.testing.assert_allclose(
                        output, prepared.samples[start : start + 2048] * 0.75
                    )
                    if (block + 1) % 8 == 0:
                        player._emitPlaybackTelemetry()
                self.assertEqual(player._scrub_frame, 48000 + 32 * 2048)
                player._scrub_buffer = None
                with patch.object(
                    prepared.samples.storage,
                    'read',
                    side_effect=AssertionError('Audio callback must not read files'),
                ):
                    player._renderAudio(
                        output,
                        2048,
                        SimpleNamespace(outputBufferDacTime=0, currentTime=0),
                        SimpleNamespace(output_underflow=False),
                    )
                np.testing.assert_array_equal(output, 0)
                player.endScrub(0.5)
                self.assertTrue(player.cancelQueuedTrack())
                np.testing.assert_allclose(
                    player._timeline.read(150000, 150010),
                    prepared.samples[150000:150010] * 0.75,
                )
            finally:
                player.shutdown()
                app.processEvents()

    def testLoudnessReadsBoundedChunks(self) -> None:
        for width, channels in ((2, 1), (2, 2), (4, 2)):
            with self.subTest(width=width, channels=channels):
                audio = self._audio(96000, width, channels)
                normal = PatchedAudioSegment(
                    data=audio.raw_data,
                    sample_width=width,
                    frame_rate=audio.frame_rate,
                    channels=channels,
                )
                expected = getAdjustedGainFactor(-14, normal)
                with patch.object(audio, 'readPcm', wraps=audio.readPcm) as reads:
                    actual = getAdjustedGainFactor(-14, audio)
                self.assertAlmostEqual(actual, expected, places=6)
                self.assertTrue(reads.call_args_list)
                self.assertTrue(
                    all(
                        stop - start <= 65536
                        for start, stop in (call.args for call in reads.call_args_list)
                    )
                )

    def testSilenceAndShortLoudness(self) -> None:
        self.assertTrue(
            np.isneginf(
                Meter(48000).integratedLoudness(np.zeros((48000, 2), np.float32))
            )
        )
        with self.assertRaises(ValueError):
            Meter(48000).integratedLoudness(np.zeros((100, 2), np.float32))

    def testWorkerReleasesLastResponseWhileIdle(self) -> None:
        worker = FreeThreadedJsonSender(max_workers=2)
        try:
            if worker.call('base64_decode', {'data': ''}) is None:
                self.skipTest('Free-threaded Python is unavailable')
            tracemalloc.start(1)
            payload = base64.b64encode(b'x' * (4 * 1024**2)).decode('ascii')
            decoded = worker.call('base64_decode', {'data': payload}, timeout=10)
            self.assertEqual(len(decoded), 4 * 1024**2)
            del payload, decoded
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                gc.collect()
                if tracemalloc.get_traced_memory()[0] < 1024**2:
                    break
                time.sleep(0.01)
            self.assertLess(tracemalloc.get_traced_memory()[0], 1024**2)
        finally:
            tracemalloc.stop()
            worker.shutdown()


if __name__ == '__main__':
    unittest.main()
