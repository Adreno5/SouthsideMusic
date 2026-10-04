from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from math import gcd
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any

import numpy as np
import psutil
import sounddevice as sd
from pydub.exceptions import CouldntDecodeError
from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QTimer,
    Signal,
    Slot,
)
from qfluentwidgets import MessageBox
from scipy.signal import resample_poly

from core.audio_analysis import SpectrumAnalyzer
from core.audio_decode import (
    PatchedAudioSegment,
    cacheDecodedAudio,
    decodeAudioWithSidecar,
    extractWavHeaders,
    fixWavHeaders,
    getCachedAudio,
)
from core.audio_processing import AudioProcessingSettings, AudioProcessor
from core.beat import BeatDetector, BeatFrame
from core.config import cfg
from core.pcm_timeline import PcmTimeline
from services.events import DB_CHANGED, event_bus
from services.events.events import (
    _100MS_TICK,
    BEAT_POINT,
    COLLECT_DEBUG_INFO,
    EMIT_DEBUG_INFO,
)

__all__ = [
    'AudioPlayer',
    'DevicesInfo',
    'PatchedAudioSegment',
    'PreparedAudioBuffer',
    'cacheDecodedAudio',
    'decodeAudioWithSidecar',
    'extractWavHeaders',
    'fixWavHeaders',
    'getAudioDevices',
    'getCachedAudio',
]

_logger = logging.getLogger(__name__)
_PRODUCER_QUEUE_BLOCKS = 32768
_PRODUCER_PROGRESS_BOOST_RATIO = 0.2
_PRODUCER_EARLY_LEAD = 5.0
_PRODUCER_EARLY_STRESSED_LEAD = 3.0
_PRODUCER_EARLY_IDLE_LEAD = 8.0
_PRODUCER_LATE_LEAD = 90.0
_PRODUCER_LATE_STRESSED_LEAD = 25.0
_PRODUCER_LATE_IDLE_LEAD = 120.0
_PRODUCER_REFILL_RATIO = 0.75
_PRODUCER_MIN_REFILL_LEAD = 2.0
_PRODUCER_YIELD_BLOCKS = 32


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ('dwLength', ctypes.c_ulong),
        ('dwMemoryLoad', ctypes.c_ulong),
        ('ullTotalPhys', ctypes.c_ulonglong),
        ('ullAvailPhys', ctypes.c_ulonglong),
        ('ullTotalPageFile', ctypes.c_ulonglong),
        ('ullAvailPageFile', ctypes.c_ulonglong),
        ('ullTotalVirtual', ctypes.c_ulonglong),
        ('ullAvailVirtual', ctypes.c_ulonglong),
        ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
    ]


def _getMemoryLoad() -> float:
    windll = getattr(ctypes, 'windll', None)
    if windll is None:
        return 0.0
    try:
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        if windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return float(status.dwMemoryLoad)
    except Exception:
        _logger.debug('failed to sample memory load', exc_info=True)
    return 0.0


def _getCpuLoad() -> float:
    try:
        return max(0.0, min(100.0, float(psutil.cpu_percent(interval=None))))
    except Exception:
        _logger.debug('failed to sample CPU load', exc_info=True)
        return 0.0


@dataclass
class DevicesInfo:
    display_name: str
    index: int


def getAudioDevices() -> list[DevicesInfo]:
    devices = sd.query_devices()
    result: list[DevicesInfo] = []
    for i, dev in enumerate(devices):
        if dev['max_output_channels'] > 0:
            result.append(DevicesInfo(display_name=dev['name'], index=i))
    return result


@dataclass(frozen=True)
class PreparedAudioBuffer:
    samples: np.ndarray
    sample_rate: int
    channels: int


class AudioPlayer(QObject):
    onFullFinished = Signal()
    onEndingNoSound = Signal()
    positionChanged = Signal(float)
    seekRequested = Signal(float)
    fftDataReady = Signal(np.ndarray, np.ndarray)  # (freqs, magnitudes)
    beatDataReady = Signal(float, bool)
    beatDataReset = Signal()
    _beatFramesReady = Signal(int, object)
    _spectrumReady = Signal(int, object, object)

    def __init__(
        self, parent: QObject | None = None, devices: list[DevicesInfo] | None = None
    ) -> None:
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)

        self.samples: np.ndarray = np.zeros((0, 1), dtype=np.float32)
        self._timeline: PcmTimeline | None = None
        self._queued_restore: PcmTimeline | None = None
        self._queued_start = 0
        self._track_origin = 0
        self._track_frames: int | None = None
        self.sample_rate: int = 88200
        self.channels: int = 1
        self.output_channels: int = 1

        self.db: float = 0

        self.current_index: int = 0
        self._playback_time: float = 0.0
        self._last_seek_at: float = float('-inf')
        self._smooth_position_start: float = 0.0
        self._smooth_position_end: float = 0.0
        self._smooth_position_started_at: float = 0.0
        self._smooth_position_duration: float = 0.0
        self.is_playing: bool = False
        self.is_paused: bool = False
        self.stream: sd.OutputStream | None = None
        self.volume_gain: float = 1.0
        self.loudness_gain: float = 1.0
        self._volume_anim: QPropertyAnimation | None = None
        self._gain_anim: QPropertyAnimation | None = None
        self._speed_anim: QPropertyAnimation | None = None
        self._pitch_anim: QPropertyAnimation | None = None
        self._speed_animating_flag: bool = False

        self.fft_enabled = True
        self.fft_size = int(cfg.fft_size)
        self._analysis_generation = 0
        self._analysis_sequence = 0
        self._beatFramesReady.connect(self._publishBeatFrames)
        self._spectrumReady.connect(self._publishSpectrum)

        self.play_speed = cfg.play_speed
        self.play_pitch = cfg.play_pitch

        self._BLOCK_SIZE = 4096

        self._audio_queue: Queue[tuple[np.ndarray, int, float | None] | None] = Queue(
            maxsize=_PRODUCER_QUEUE_BLOCKS
        )
        self._scrub_samples: np.ndarray | None = None
        self._scrub_scale = 1.0
        self._scrub_frame = 0
        self._scrub_was_playing = False
        self._scrub_was_paused = False
        self._scrub_handoff = False
        self._producer_running = False
        self._producer_thread: threading.Thread | None = None
        self._producer_seq = 0
        self._producer_index: int = 0
        self._prepared_start_index: int = 0
        self._prepared_end_index: int = 0
        self._producer_cpu_load: float = 0.0
        self._producer_memory_load: float = 0.0
        self._producer_target_lead: float = _PRODUCER_EARLY_LEAD
        self._producer_last_resource_sample = 0.0
        self._queue_underruns = 0
        self._output_underflows = 0
        self._growing_file_path: Path | None = None
        self._growing_file_complete = True
        self._growing_file_size = 0
        self._growing_file_last_decode = 0.0
        self._growing_stream_mode = False
        self._growing_stream_buffer: np.ndarray | None = None
        self._callback_events_lock = threading.Lock()
        self._pending_full_finished = False
        self._pending_ending_no_sound = False
        self._finished_streams: list[sd.OutputStream] = []

        self._lock = threading.RLock()
        devices = devices if devices is not None else getAudioDevices()
        if len(devices) == 0:
            self._logger.error('no devices found')
            dialog = MessageBox(
                'Error ',
                'No any device can be used to play audio on your computer!',
                None,
            )
            dialog.cancelButton.hide()
            dialog.yesButton.setText('OK')
            dialog.exec()
            sys.exit(1)
        self._device_id: int = devices[0].index
        self.fft_queue: Queue[
            tuple[int, int, int, float, np.ndarray, np.ndarray] | None
        ] = Queue(maxsize=8)
        self.fft_thread_running = True
        self.fft_thread = threading.Thread(target=self._fft_worker, daemon=True)
        self.fft_thread.start()

        event_bus.subscribe(_100MS_TICK, self._emitPlaybackTelemetry)

        event_bus.subscribe(COLLECT_DEBUG_INFO, self.emitDebugInfo)

    def emitDebugInfo(self) -> None:
        event_bus.emit(
            EMIT_DEBUG_INFO,
            'AudioPlayer',
            [
                f'is_playing={self.is_playing}',
                f'is_paused={self.is_paused}',
                f'current_index={self.current_index}',
                f'playback_time={self._playback_time:.3f}',
                f'play_speed={self.play_speed:.2f}',
                f'play_pitch={self.play_pitch:.2f}',
                f'volume_gain={self.volume_gain:.3f}',
                f'loudness_gain={self.loudness_gain:.3f}',
                f'db={self.db}',
                f'sample_rate={self.sample_rate}',
                f'channels={self.channels}',
                f'output_channels={self.output_channels}',
                f'fft_enabled={self.fft_enabled}',
                f'fft_size={self.fft_size}',
                f'stereo_haas_index={cfg.stereo_haas_index}',
                f'enable_reverb={cfg.enable_reverb}',
                f'reverb_intensity={cfg.reverb_intensity}',
                f'device_id={self._device_id}',
                f'audio_qsize={self._audio_queue.qsize()}',
                f'fft_qsize={self.fft_queue.qsize()}',
                f'producer_running={self._producer_running}',
                f'producer_thread_alive={self._producerThreadAlive()}',
                f'producer_cpu_load={self._producer_cpu_load:.1f}',
                f'producer_memory_load={self._producer_memory_load:.1f}',
                f'producer_target_lead={self._producer_target_lead:.2f}',
                f'prepared_lead={self._producerPreparedLead():.2f}',
                f'queue_underruns={self._queue_underruns}',
                f'output_underflows={self._output_underflows}',
                f'growing_file={self._growing_file_path is not None}',
                f'growing_file_complete={self._growing_file_complete}',
                f'growing_stream_mode={self._growing_stream_mode}',
            ],
        )

    @staticmethod
    def _prepareSamples(audio: PatchedAudioSegment) -> np.ndarray:
        samples_raw = np.frombuffer(audio.raw_data, dtype=audio.array_type).astype(
            np.float32
        )
        max_val = np.iinfo(audio.array_type).max if audio.sample_width != 4 else 2**31
        np.divide(samples_raw, max_val, out=samples_raw)

        if audio.channels <= 1:
            return samples_raw.reshape(-1, 1)

        frame_count = len(samples_raw) // audio.channels
        multi = samples_raw.reshape(frame_count, audio.channels)
        # Always force stereo: mix >2ch down to stereo, pass stereo through
        if audio.channels == 2:
            return multi
        left = multi[:, ::2].mean(axis=1)
        right = multi[:, 1::2].mean(axis=1)
        return np.stack((left, right), axis=1)

    def _resetGrowingFile(self) -> None:
        self._growing_file_path = None
        self._growing_file_complete = True
        self._growing_file_size = 0
        self._growing_file_last_decode = 0.0
        self._growing_stream_mode = False
        self._growing_stream_buffer = None

    def _decodeFile(self, file_path: Path) -> PatchedAudioSegment:
        return PatchedAudioSegment.from_file(str(file_path))

    @classmethod
    def prepareBuffer(cls, audio: PatchedAudioSegment) -> PreparedAudioBuffer:
        samples = cls._prepareSamples(audio)
        channels = samples.shape[1] if samples.ndim == 2 else 1
        return PreparedAudioBuffer(samples, audio.frame_rate, channels)

    def _applyPreparedBuffer(self, prepared: PreparedAudioBuffer) -> None:
        self._scrub_samples = None
        self._scrub_handoff = False
        self._timeline = None
        self._queued_restore = None
        self._track_origin = 0
        self._track_frames = None
        self.sample_rate = prepared.sample_rate
        self.samples = prepared.samples
        self.channels = prepared.channels
        self.output_channels = 2

        self.current_index = 0
        self._producer_index = 0
        self._prepared_start_index = 0
        self._prepared_end_index = 0
        self._clearQueue()
        self._producer_target_lead = _PRODUCER_EARLY_LEAD
        self._resetBeatAnalysis()
        self._playback_time = 0.0
        self.is_playing = False
        self.is_paused = False

    def _applyAudio(self, audio: PatchedAudioSegment) -> None:
        self._applyPreparedBuffer(self.prepareBuffer(audio))

    def _reloadPreparedBuffer(
        self, prepared: PreparedAudioBuffer, position: float | None = None
    ) -> None:
        was_playing = self.is_playing or (
            self.stream is not None and self.stream.active
        )
        was_paused = self.is_paused
        if position is None:
            position = self._getExactPosition()
        self._stopProducer()
        if self.stream is not None:
            self.stream.abort()
            self.stream.close()
            self.stream = None
        self._applyPreparedBuffer(prepared)
        self.current_index = min(
            round(max(0.0, position) * self.sample_rate), len(self.samples)
        )
        self._playback_time = self.current_index / self.sample_rate
        self._smooth_position_start = self._playback_time
        self._smooth_position_end = self._playback_time
        self._clearQueue()
        self.is_paused = was_paused
        self._ensureStream()
        if was_playing:
            self.is_playing = True
            self.is_paused = False
            self._startProducer()
            self._startStream()

    def load(self, audio: PatchedAudioSegment) -> None:
        with self._lock:
            self._stopProducer()
            self.stop(drain_stream=False)
            if self.stream:
                self.stream.close()
                self.stream = None

            self._applyAudio(audio)
            self._resetGrowingFile()
            self._ensureStream()

    def loadPrepared(self, prepared: PreparedAudioBuffer) -> None:
        with self._lock:
            self._stopProducer()
            # The stream is closed and rebuilt right after this, so there is no
            # point draining its queue first.
            self.stop(drain_stream=False)
            if self.stream:
                self.stream.close()
                self.stream = None

            self._applyPreparedBuffer(prepared)
            self._resetGrowingFile()
            self._ensureStream()

    @staticmethod
    def convertBuffer(
        prepared: PreparedAudioBuffer, sample_rate: int, channels: int = 2
    ) -> PreparedAudioBuffer:
        samples = prepared.samples
        if prepared.sample_rate != sample_rate and len(samples):
            factor = gcd(prepared.sample_rate, sample_rate)
            samples = resample_poly(
                samples, sample_rate // factor, prepared.sample_rate // factor, axis=0
            ).astype(np.float32)
        if samples.shape[1] != channels:
            mono = samples.mean(axis=1, keepdims=True)
            samples = np.repeat(mono, channels, axis=1)
        return PreparedAudioBuffer(samples, sample_rate, channels)

    def _sampleCount(self) -> int:
        return self._timeline.end if self._timeline is not None else len(self.samples)

    def _readSamples(self, start: int, stop: int) -> np.ndarray:
        if self._timeline is not None:
            return self._timeline.read(start, stop)
        return self.samples[start:stop]

    def queueNext(
        self,
        start_seconds: float,
        transition: PreparedAudioBuffer | None,
        following: PreparedAudioBuffer,
        next_gain: float,
    ) -> tuple[int, int] | None:
        """Splice future PCM without stopping the stream or draining its queue."""
        with self._lock:
            if self._growing_file_path is not None:
                return None
            source = self.samples
            timeline = self._timeline
            seq = self._producer_seq
            rate = self.sample_rate
            origin = self._track_origin
            track_end = origin + round(self.getLength() * rate)
            gain = self.loudness_gain
        following = self.convertBuffer(following, rate)
        if transition is not None:
            transition = self.convertBuffer(transition, rate)
        start = min(track_end, origin + round(start_seconds * rate))
        fade_frames = len(transition.samples) if transition is not None else 0
        if start < origin or fade_frames > len(following.samples):
            return None
        tail = PcmTimeline(2, start)
        if transition is not None:
            tail.append(transition.samples)
        tail.append(following.samples[fade_frames:], next_gain)
        base = timeline
        if base is None:
            base = PcmTimeline(2)
            prepared = self.convertBuffer(
                PreparedAudioBuffer(source, rate, source.shape[1]), rate
            )
            base.append(prepared.samples, gain)
        with self._lock:
            if (
                seq != self._producer_seq
                or source is not self.samples
                or timeline is not self._timeline
                or self.current_index + self._BLOCK_SIZE * 2 >= start
            ):
                return None
            restore = PcmTimeline(2, start)
            restore.append(base.read(start, track_end))
            self._queued_restore = restore
            self._queued_start = start
            base.replaceFrom(start, tail)
            self._timeline = base
            if self._track_frames is None:
                self._track_frames = len(source)
            self.samples = np.zeros((0, 2), dtype=np.float32)
            self.channels = 2
            self._rebuildFutureQueue(start)
            return start, start + fade_frames

    def _rebuildFutureQueue(self, splice: int) -> None:
        # Keep playable blocks ahead of the DAC; only discard the stale suffix.
        self._stopProducer()
        frontier = self.current_index
        guard = max(frontier, splice - self.sample_rate // 2)
        with self._audio_queue.mutex:
            kept = []
            for item in self._audio_queue.queue:
                if item is None or frontier + item[1] > guard:
                    break
                kept.append(item)
                frontier += item[1]
            self._audio_queue.queue.clear()
            self._audio_queue.queue.extend(kept)
        self._producer_index = frontier
        self._prepared_end_index = frontier
        if self.is_playing:
            self._startProducer()

    def cancelQueuedTrack(self) -> bool:
        """Drop any queued splice and report whether the timeline is seekable."""
        with self._lock:
            if self._timeline is None:
                return False
            restore = self._queued_restore
            self._queued_restore = None
            if restore is None:
                # Nothing spliced: the timeline already holds the current track.
                return True
            if self.current_index < self._queued_start:
                self._timeline.replaceFrom(self._queued_start, restore)
                self._rebuildFutureQueue(self._queued_start)
                return True
            # The splice already reached the DAC; the tail is another track now.
            return False

    def seekTimeline(self, seconds: float) -> bool:
        """Move the play head inside the live timeline without rebuilding it."""
        self._last_seek_at = time.perf_counter()
        with self._lock:
            timeline = self._timeline
            if timeline is None or self.sample_rate <= 0 or not timeline.blocks:
                return False
            origin = self._track_origin
            frames = self._track_frames
            if frames is None:
                frames = timeline.end - origin
            frame = origin + round(max(0.0, seconds) * self.sample_rate)
            frame = max(origin, min(frame, origin + frames, timeline.end))
            if frame < timeline.blocks[0].start:
                return False

            self._stopProducer()
            self._playback_time = (frame - origin) / self.sample_rate
            self._smooth_position_start = self._playback_time
            self._smooth_position_end = self._playback_time
            self._producer_target_lead = self._producerDesiredLead()
            self._resetBeatAnalysis()
            self.current_index = frame
            self._clearQueue()
            if self.is_playing or (self.stream is not None and self.stream.active):
                self._startProducer()
            if self._scrub_samples is not None:
                self._scrub_handoff = True
            return True

    def beginQueuedTrack(
        self,
        origin: int,
        frames: int,
        gain: float,
        transition_end: int,
    ) -> tuple[int, int]:
        with self._lock:
            self._track_origin = origin
            self._track_frames = frames
            self._queued_restore = None
            self.loudness_gain = gain
            self._playback_time = (self.current_index - origin) / self.sample_rate
            self._smooth_position_end = self._playback_time
            self._smooth_position_start = self._playback_time
            return origin, transition_end

    def loadFromFile(self, file_path: Path) -> None:
        audio = self._decodeFile(file_path)
        self.load(audio)

    def loadFromBytes(self, data: bytes) -> None:
        audio = PatchedAudioSegment.from_file(data)
        self.load(audio)

    def loadGrowingFile(
        self,
        file_path: Path,
        complete: bool = False,
    ) -> PatchedAudioSegment:
        audio = self._decodeFile(file_path)
        file_size = file_path.stat().st_size
        with self._lock:
            self._stopProducer()
            self.stop(clear_growing_file=False, drain_stream=False)
            if self.stream:
                self.stream.close()
                self.stream = None

            self._applyAudio(audio)
            self._growing_file_path = file_path
            self._growing_file_complete = complete
            self._growing_file_size = file_size
            self._growing_file_last_decode = time.perf_counter()
            self._growing_stream_mode = False
            self._ensureStream()
        return audio

    def loadGrowingStream(
        self,
        file_path: Path,
        sample_rate: int,
        channels: int,
    ) -> None:
        with self._lock:
            self._stopProducer()
            self.stop(clear_growing_file=False, drain_stream=False)
            if self.stream:
                self.stream.close()
                self.stream = None

            self._timeline = None
            self._queued_restore = None
            self._track_origin = 0
            self._track_frames = None
            self.sample_rate = sample_rate
            self.samples = np.zeros((0, channels), dtype=np.float32)
            self.channels = channels
            self.output_channels = channels
            self.current_index = 0
            self._producer_index = 0
            self._prepared_start_index = 0
            self._prepared_end_index = 0
            self._clearQueue()
            self._producer_target_lead = _PRODUCER_EARLY_LEAD
            self._resetBeatAnalysis()
            self._playback_time = 0.0
            self.is_playing = False
            self.is_paused = False
            self._growing_file_path = file_path
            self._growing_file_complete = False
            self._growing_file_size = 0
            self._growing_file_last_decode = time.perf_counter()
            self._growing_stream_mode = True
            self._growing_stream_buffer = None
            self._ensureStream()

    def appendGrowingStreamPcm(
        self,
        file_path: Path,
        pcm_data: bytes,
        channels: int,
    ) -> float:
        frame_width = channels * 4
        valid_len = len(pcm_data) - (len(pcm_data) % frame_width)
        if valid_len <= 0:
            return self.getLength()

        chunk = np.frombuffer(pcm_data[:valid_len], dtype='<f4').reshape(-1, channels)
        chunk = chunk.astype(np.float32, copy=True)
        with self._lock:
            if self._growing_file_path != file_path or self._growing_file_complete:
                return self.getLength()
            previous = self.samples
            previous_length = len(previous)
            buffer = self._growing_stream_buffer
            required_length = previous_length + len(chunk)
            if buffer is None or required_length > len(buffer):
                capacity = max(required_length, self.sample_rate * 8)
                if buffer is not None:
                    capacity = max(capacity, len(buffer) * 2)
                expanded = np.empty((capacity, channels), dtype=np.float32)
            else:
                expanded = None
        if expanded is not None:
            expanded[:previous_length] = previous
        with self._lock:
            if self._growing_file_path != file_path or self._growing_file_complete:
                return self.getLength()
            if expanded is not None:
                buffer = expanded
                self._growing_stream_buffer = buffer
            assert buffer is not None
            buffer[previous_length:required_length] = chunk
            self.samples = buffer[:required_length]
            self._growing_file_size += valid_len
            self._growing_file_last_decode = time.perf_counter()
            return self.getLength()

    def finishGrowingStream(self, file_path: Path) -> bool:
        with self._lock:
            if self._growing_file_path != file_path:
                return False
            self._growing_file_complete = True
            self._growing_file_path = None
            self._growing_stream_mode = False
            self._growing_file_last_decode = 0.0
            return True

    def setSampleRate(self, rate: int) -> None:
        with self._lock:
            if rate == self.sample_rate:
                return
            was_playing = self.is_playing or (
                self.stream is not None and self.stream.active
            )
            self._stopProducer()
            if self.stream:
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception:
                    self._logger.debug('failed to close audio stream', exc_info=True)
                self.stream = None
            self.sample_rate = rate
            self._clearQueue()
            self._resetBeatAnalysis()
            if was_playing:
                self._startProducer()
                self._startStream()

    def refreshGrowingFile(self, force: bool = False) -> bool:
        with self._lock:
            file_path = self._growing_file_path
            last_decode = self._growing_file_last_decode
            old_size = self._growing_file_size
            old_len = len(self.samples)
            stream_mode = self._growing_stream_mode

        if file_path is None or stream_mode:
            return False

        now = time.perf_counter()
        if not force and now - last_decode < 0.35:
            return False

        try:
            file_size = file_path.stat().st_size
        except OSError:
            return False

        if not force and file_size <= old_size:
            with self._lock:
                if file_path == self._growing_file_path:
                    self._growing_file_last_decode = now
            return False

        try:
            audio = self._decodeFile(file_path)
            samples = self._prepareSamples(audio)
        except CouldntDecodeError:
            with self._lock:
                if file_path == self._growing_file_path:
                    self._growing_file_last_decode = now
            return False
        except Exception:
            with self._lock:
                if file_path == self._growing_file_path:
                    self._growing_file_last_decode = now
            self._logger.exception('failed to refresh growing audio file')
            return False

        with self._lock:
            if file_path != self._growing_file_path:
                return False
            self._growing_file_size = file_size
            self._growing_file_last_decode = now
            if (
                audio.frame_rate == self.sample_rate
                and len(samples) <= old_len
                and not force
            ):
                return False
            channels = samples.shape[1] if samples.ndim == 2 else 1
            if self.sample_rate != audio.frame_rate or self.channels != channels:
                self._reloadPreparedBuffer(
                    PreparedAudioBuffer(samples, audio.frame_rate, channels)
                )
                return True
            self.samples = samples
            self.channels = self.samples.shape[1] if self.samples.ndim == 2 else 1
            if self.stream is None:
                self.output_channels = 2
            return force or len(self.samples) > old_len

    def finishGrowingFile(
        self,
        file_path: Path,
        audio: PatchedAudioSegment | None = None,
    ) -> bool:
        if audio is not None:
            samples = self._prepareSamples(audio)

            with self._lock:
                if self._growing_file_path != file_path:
                    return False
                channels = samples.shape[1] if samples.ndim == 2 else 1
                if self.sample_rate != audio.frame_rate or self.channels != channels:
                    self._reloadPreparedBuffer(
                        PreparedAudioBuffer(samples, audio.frame_rate, channels)
                    )
                    self._resetGrowingFile()
                    return True
                self.samples = samples
                self.channels = self.samples.shape[1] if self.samples.ndim == 2 else 1
                if self.stream is None:
                    self.output_channels = 2
                self.current_index = min(self.current_index, len(self.samples))
                self._producer_index = min(self._producer_index, len(self.samples))
                self._prepared_start_index = min(
                    self._prepared_start_index, len(self.samples)
                )
                self._prepared_end_index = min(
                    self._prepared_end_index, len(self.samples)
                )
                self._resetGrowingFile()
                return True

        refreshed = self.refreshGrowingFile(force=True)
        with self._lock:
            if self._growing_file_path == file_path:
                self._resetGrowingFile()
        return refreshed

    def play(self) -> None:
        if self._timeline is not None and not self.is_paused:
            self.seekRequested.emit(0.0)
            return
        with self._lock:
            if self._sampleCount() == 0:
                return
            if self.is_paused:
                self._clearQueue()
                self._startProducer()
                self._startStream()
                self.is_playing = True
                self.is_paused = False
            else:
                self.stop(clear_growing_file=self._growing_file_path is None)
                self.current_index = 0
                self._producer_index = 0
                self._prepared_start_index = 0
                self._prepared_end_index = 0
                self._clearQueue()
                self._startProducer()
                self._startStream()
                self.is_playing = True
                self.is_paused = False

    def playFromPosition(self, seconds: float) -> None:
        self._last_seek_at = time.perf_counter()
        with self._lock:
            if self._sampleCount() == 0:
                return
            self.stop(clear_growing_file=self._growing_file_path is None)
            self._playback_time = max(0.0, seconds)
            self.current_index = min(
                int(self._playback_time * self.sample_rate), self._sampleCount()
            )
            self._producer_index = self.current_index
            self._prepared_start_index = self.current_index
            self._prepared_end_index = self.current_index
            self._producer_target_lead = self._producerDesiredLead()
            self._clearQueue()
            self._startProducer()

        deadline = time.perf_counter() + 0.25
        while self._audio_queue.empty() and time.perf_counter() < deadline:
            time.sleep(0.001)

        with self._lock:
            if not self._producer_running or self._sampleCount() == 0:
                return
            self._startStream()
            self.is_playing = True
            self.is_paused = False

    def playFromLivePosition(self, position_provider: Callable[[], float]) -> None:
        """Start playback aligned with another advancing player."""
        with self._lock:
            if self._sampleCount() == 0:
                return
            self.stop(clear_growing_file=self._growing_file_path is None)
            self._playback_time = max(0.0, position_provider())
            self.current_index = min(
                int(self._playback_time * self.sample_rate), self._sampleCount()
            )
            self._producer_index = self.current_index
            self._prepared_start_index = self.current_index
            self._prepared_end_index = self.current_index
            self._producer_target_lead = self._producerDesiredLead()
            self._clearQueue()
            started_at = time.perf_counter()
            self._startProducer()

        deadline = time.perf_counter() + 0.25
        while self._audio_queue.empty() and time.perf_counter() < deadline:
            time.sleep(0.001)
        startup_seconds = time.perf_counter() - started_at

        with self._lock:
            if not self._producer_running or self._sampleCount() == 0:
                return
            self._stopProducer()
            self._playback_time = max(
                0.0,
                position_provider() + startup_seconds * self.play_speed,
            )
            self.current_index = min(
                int(self._playback_time * self.sample_rate), self._sampleCount()
            )
            self._producer_index = self.current_index
            self._prepared_start_index = self.current_index
            self._prepared_end_index = self.current_index
            self._clearQueue()
            self._startProducer()

        deadline = time.perf_counter() + 0.25
        while self._audio_queue.empty() and time.perf_counter() < deadline:
            time.sleep(0.001)

        with self._lock:
            if not self._producer_running or self._sampleCount() == 0:
                return
            self._startStream()
            self.is_playing = True
            self.is_paused = False

    def pause(self) -> None:
        with self._lock:
            self._scrub_samples = None
            self._scrub_handoff = False
            self._stopProducer()
            self._clearQueue()
            if self.stream and self.stream.active:
                self.stream.stop()
            self.is_playing = False
            self.is_paused = True
            self._resetBeatAnalysis()

    def resume(self) -> None:
        self.play()

    def stop(self, clear_growing_file: bool = True, drain_stream: bool = True) -> None:
        with self._lock:
            self._scrub_samples = None
            self._scrub_handoff = False
            self.stopVolumeAnimation()
            self.stopGainAnimation()
            self._stopProducer()
            if self.stream and self.stream.active:
                # Draining waits for the queued audio to play out, which can block
                # for hundreds of milliseconds; abort when the stream is about to
                # be torn down anyway.
                if drain_stream:
                    self.stream.stop()
                else:
                    self.stream.abort()
            self.current_index = 0
            self._producer_index = 0
            self._prepared_start_index = 0
            self._prepared_end_index = 0
            self._producer_target_lead = _PRODUCER_EARLY_LEAD
            self._resetBeatAnalysis()
            self._playback_time = 0.0
            self.is_playing = False
            self.is_paused = False
            if clear_growing_file:
                self._resetGrowingFile()

    def setPosition(self, seconds: float) -> None:
        self._last_seek_at = time.perf_counter()
        if self._timeline is not None:
            if self._scrub_samples is not None:
                self._scrub_handoff = False
            self.seekRequested.emit(seconds)
            return
        with self._lock:
            if self._sampleCount() == 0:
                return
            self._stopProducer()
            self._playback_time = max(0.0, seconds)
            self.current_index = int(self._playback_time * self.sample_rate)
            self._producer_index = self.current_index
            self._prepared_start_index = self.current_index
            self._prepared_end_index = self.current_index
            self._producer_target_lead = self._producerDesiredLead()
            self._resetBeatAnalysis()
            self._clearQueue()
            if self.is_playing:
                self._startProducer()

    def replacePreparedForSeek(
        self, prepared: PreparedAudioBuffer, seconds: float, was_playing: bool
    ) -> bool:
        with self._lock:
            if self.stream is not None and prepared.sample_rate != self.sample_rate:
                return False
            preview = self._scrub_samples
            preview_scale = self._scrub_scale
            preview_frame = self._scrub_frame
            output_channels = self.output_channels
            self._stopProducer()
            self._applyPreparedBuffer(prepared)
            if self.stream is not None:
                self.output_channels = output_channels
            self.current_index = min(
                round(max(0.0, seconds) * self.sample_rate), len(self.samples)
            )
            self._playback_time = self.current_index / self.sample_rate
            self._smooth_position_start = self._playback_time
            self._smooth_position_end = self._playback_time
            self._clearQueue()
            self._producer_target_lead = self._producerDesiredLead()
            self.is_playing = was_playing
            self.is_paused = not was_playing
            if was_playing:
                if preview is not None:
                    self._scrub_samples = preview
                    self._scrub_scale = preview_scale
                    self._scrub_frame = preview_frame
                    self._scrub_handoff = True
                self._startProducer()
                if self.stream is None or not self.stream.active:
                    self._startStream()
            return True

    def beginScrub(
        self, seconds: float, audio: PatchedAudioSegment | None = None
    ) -> bool:
        with self._lock:
            if self._sampleCount() == 0:
                return False
            if self._timeline is not None:
                if (
                    audio is None
                    or audio.frame_rate != self.sample_rate
                    or audio.sample_width not in (1, 2, 4)
                ):
                    return False
                dtype = {1: np.int8, 2: np.int16, 4: np.int32}[audio.sample_width]
                source = np.frombuffer(audio.raw_data, dtype=dtype).reshape(
                    -1, audio.channels
                )
                scale = (
                    float(2**31)
                    if audio.sample_width == 4
                    else float(2 ** (audio.sample_width * 8 - 1) - 1)
                )
            else:
                source = self.samples
                scale = 1.0
            if len(source) == 0:
                return False
            self._scrub_was_playing = self.isPlaying()
            self._scrub_was_paused = self.is_paused
            self._stopProducer()
            self._clearQueue()
            self._scrub_samples = source
            self._scrub_scale = scale
            self._scrub_handoff = False
            self.scrubTo(seconds)
            if not self._scrub_was_playing:
                self._startStream()
            self.is_playing = True
            self.is_paused = False
            return True

    def scrubTo(self, seconds: float) -> None:
        with self._lock:
            if self._growing_file_path is not None and self._timeline is None:
                self._scrub_samples = self.samples
            source = self._scrub_samples
            if source is None:
                return
            self._last_seek_at = time.perf_counter()
            self._scrub_frame = max(
                0, min(round(seconds * self.sample_rate), len(source) - 1)
            )
            self._playback_time = self._scrub_frame / self.sample_rate
            self._smooth_position_start = self._playback_time
            self._smooth_position_end = self._playback_time

    def endScrub(self, seconds: float) -> None:
        with self._lock:
            if self._scrub_samples is None:
                return
            self.scrubTo(seconds)
            if self._scrub_was_playing:
                self._scrub_handoff = True
            else:
                self._scrub_samples = None
                self._scrub_handoff = False
                if self.stream is not None and self.stream.active:
                    self.stream.abort()
                self.is_playing = False
                self.is_paused = self._scrub_was_paused
        self.setPosition(seconds)

    def getPosition(self) -> float:
        return round(self._playback_time, 2)

    def getLastSeekTime(self) -> float:
        """Monotonic time of the most recent seek request, or -inf if none."""
        return self._last_seek_at

    def getSmoothPosition(self) -> float:
        """Return playback position interpolated within the current audio block."""
        position = self._playback_time
        if (
            not self.is_playing
            or abs(position - self._smooth_position_end) > 1e-6
            or self._smooth_position_duration <= 0
        ):
            return position
        progress = (time.perf_counter() - self._smooth_position_started_at) / (
            self._smooth_position_duration
        )
        progress = max(0.0, min(1.0, progress))
        return (
            self._smooth_position_start
            + (self._smooth_position_end - self._smooth_position_start) * progress
        )

    def _getExactPosition(self) -> float:
        return self._playback_time

    def getLength(self) -> float:
        frames = self._track_frames
        if frames is None:
            frames = len(self.samples)
        return frames / self.sample_rate if self.sample_rate > 0 else 0.0

    def getLoadedTime(self) -> float:
        return self.getLength()

    def getPreparedTimeSection(self) -> tuple[float, float]:
        if self.sample_rate <= 0:
            return 0.0, 0.0
        with self._lock:
            start = min(self._prepared_start_index, self._prepared_end_index)
            end = max(self._prepared_start_index, self._prepared_end_index)
            return (
                max(0.0, (start - self._track_origin) / self.sample_rate),
                min(self.getLength(), (end - self._track_origin) / self.sample_rate),
            )

    def setVolume(self, volume: float) -> None:
        self.volume_gain = max(0.0, min(1.0, volume))

    def setPlaySpeed(self, speed: float) -> None:
        with self._lock:
            speed = max(0.1, speed)
            if abs(speed - self.play_speed) < 1e-6:
                return
            was_playing = self.is_playing
            if was_playing:
                self._stopProducer()
            self.play_speed = speed
            if was_playing:
                self._clearQueue()
                self._startProducer()

    def setPlayPitch(self, pitch: float) -> None:
        with self._lock:
            pitch = max(-12.0, min(12.0, pitch))
            if abs(pitch - self.play_pitch) < 1e-6:
                return
            was_playing = self.is_playing
            if was_playing:
                self._stopProducer()
            self.play_pitch = pitch
            if was_playing:
                self._clearQueue()
                self._startProducer()

    def restartProducer(self) -> None:
        with self._lock:
            if self._sampleCount() == 0:
                return
            was_playing = self.is_playing or (
                self.stream is not None and self.stream.active
            )
            if was_playing:
                self._stopProducer()
            self._clearQueue()
            self._producer_target_lead = self._producerDesiredLead()
            if was_playing:
                self._startProducer()

    def animatePlayPitch(self, target: float, duration_ms: int) -> None:
        if (
            self._pitch_anim is not None
            and self._pitch_anim.state() == QPropertyAnimation.State.Running
        ):
            self._pitch_anim.stop()
        self._pitch_anim = QPropertyAnimation(self, b'animPlayPitch')
        self._pitch_anim.setStartValue(self.play_pitch)
        self._pitch_anim.setEndValue(max(-12.0, min(12.0, target)))
        self._pitch_anim.setDuration(max(1, duration_ms))
        self._pitch_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._pitch_anim.start()

    def stopPlayPitchAnimation(self) -> None:
        if self._pitch_anim is not None:
            self._pitch_anim.stop()
            self._pitch_anim = None

    def isPlaying(self) -> bool:
        if self.is_playing:
            return True
        if self.stream is not None:
            try:
                return self.stream.active
            except Exception:
                self._logger.debug('failed to query audio stream', exc_info=True)
        return False

    def _streamSampleRate(self) -> int:
        return int(self.sample_rate * self.play_speed)

    def prepareStream(self) -> None:
        with self._lock:
            self._ensureStream()

    def _ensureStream(self) -> None:
        if self.stream is None:
            channels = self.output_channels
            try:
                self.stream = sd.OutputStream(
                    samplerate=self.sample_rate,
                    channels=channels,
                    callback=self._audio_callback,
                    blocksize=self._BLOCK_SIZE,
                    dtype='float32',
                    device=self._device_id,
                )
            except sd.PortAudioError:
                channels = 1
                self.stream = sd.OutputStream(
                    samplerate=self.sample_rate,
                    channels=channels,
                    callback=self._audio_callback,
                    blocksize=self._BLOCK_SIZE,
                    dtype='float32',
                    device=self._device_id,
                )
            self.output_channels = channels

    def _startStream(self) -> None:
        self._ensureStream()
        if self.stream is not None:
            self.stream.start()

    def setGain(self, gain: float):
        with self._lock:
            self.loudness_gain = max(0.0, gain)

    def animateLoudnessGain(self, target: float, duration_ms: int = 600) -> None:
        if (
            self._gain_anim is not None
            and self._gain_anim.state() == QPropertyAnimation.State.Running
        ):
            self._gain_anim.stop()
        self._gain_anim = QPropertyAnimation(self, b'loudnessGain')
        self._gain_anim.setStartValue(self.loudness_gain)
        self._gain_anim.setEndValue(target)
        self._gain_anim.setDuration(duration_ms)
        self._gain_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._gain_anim.start()

    def animateVolume(self, target: float, duration_ms: int = 600) -> None:
        self.animateVolumeCurve(
            target, duration_ms, 'equal_power', target > self.volume_gain
        )

    def animateVolumeCurve(
        self,
        target: float,
        duration_ms: int = 600,
        curve: str = 'equal_power',
        fade_in: bool = True,
    ) -> None:
        if (
            self._volume_anim is not None
            and self._volume_anim.state() == QPropertyAnimation.State.Running
        ):
            self._volume_anim.stop()
        self._volume_anim = QPropertyAnimation(self, b'volumeGain')
        self._volume_anim.setStartValue(self.volume_gain)
        self._volume_anim.setEndValue(max(0.0, min(1.0, target)))
        self._volume_anim.setDuration(duration_ms)
        if curve == 'linear':
            self._volume_anim.setEasingCurve(QEasingCurve.Type.Linear)
        elif curve == 'sigmoid':
            self._volume_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        else:
            start = self.volume_gain
            end = max(0.0, min(1.0, target))
            values = (
                ((0.0, 0.0), (0.25, 0.3827), (0.5, 0.7071), (0.75, 0.9239), (1.0, 1.0))
                if fade_in
                else (
                    (0.0, 1.0),
                    (0.25, 0.9239),
                    (0.5, 0.7071),
                    (0.75, 0.3827),
                    (1.0, 0.0),
                )
            )
            for position, value in values[1:-1]:
                self._volume_anim.setKeyValueAt(position, start + (end - start) * value)
        self._volume_anim.start()

    def animateVolumeProfile(
        self, profile: tuple[float, ...], duration_ms: int = 600
    ) -> None:
        """Animate through an analysis-derived gain profile."""
        if len(profile) < 2:
            return
        if (
            self._volume_anim is not None
            and self._volume_anim.state() == QPropertyAnimation.State.Running
        ):
            self._volume_anim.stop()
        self._volume_anim = QPropertyAnimation(self, b'volumeGain')
        self._volume_anim.setStartValue(max(0.0, min(1.0, profile[0])))
        self._volume_anim.setEndValue(max(0.0, min(1.0, profile[-1])))
        self._volume_anim.setDuration(duration_ms)
        for index, value in enumerate(profile[1:-1], start=1):
            self._volume_anim.setKeyValueAt(
                index / (len(profile) - 1), max(0.0, min(1.0, value))
            )
        self._volume_anim.start()

    def stopVolumeAnimation(self) -> None:
        if self._volume_anim is not None:
            self._volume_anim.stop()
            self._volume_anim = None

    def stopGainAnimation(self) -> None:
        if self._gain_anim is not None:
            self._gain_anim.stop()
            self._gain_anim = None

    def stopPlaySpeedAnimation(self) -> None:
        if self._speed_anim is not None:
            self._speed_anim.stop()
            self._speed_anim = None
        self._speed_animating_flag = False

    @Property(float)
    def _volumeGain(self) -> float:
        return self.volume_gain

    @_volumeGain.setter
    def volumeGain(self, value: float) -> None:
        self.setVolume(value)

    @Property(float)
    def _loudnessGain(self) -> float:
        return self.loudness_gain

    @_loudnessGain.setter
    def loudnessGain(self, value: float) -> None:
        self.loudness_gain = value

    @Property(float)
    def _animPlaySpeed(self) -> float:
        return self.play_speed

    @_animPlaySpeed.setter
    def animPlaySpeed(self, value: float) -> None:
        self.play_speed = max(0.1, value)

    def animatePlaySpeed(self, target: float, duration_ms: int) -> None:
        if (
            self._speed_anim is not None
            and self._speed_anim.state() == QPropertyAnimation.State.Running
        ):
            self._speed_anim.stop()
        self._speed_animating_flag = True
        self._speed_anim = QPropertyAnimation(self, b'animPlaySpeed')
        self._speed_anim.setStartValue(self.play_speed)
        self._speed_anim.setEndValue(max(0.1, target))
        self._speed_anim.setDuration(duration_ms)
        self._speed_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._speed_anim.finished.connect(self._onSpeedAnimFinished)
        self._speed_anim.start()

    def _onSpeedAnimFinished(self) -> None:
        self._speed_animating_flag = False

    @Property(float)
    def _animPlayPitch(self) -> float:
        return self.play_pitch

    @_animPlayPitch.setter
    def animPlayPitch(self, value: float) -> None:
        self.play_pitch = max(-12.0, min(12.0, value))

    def _resetBeatAnalysis(self) -> None:
        self._analysis_generation += 1
        with self._callback_events_lock:
            self._pending_full_finished = False
            self._pending_ending_no_sound = False
        with self.fft_queue.mutex:
            self.fft_queue.queue.clear()
            self.fft_queue.not_full.notify_all()
        self.beatDataReset.emit()

    @Slot(int, object, object)
    def _publishSpectrum(
        self, generation: int, frequencies: np.ndarray, magnitudes: np.ndarray
    ) -> None:
        if generation == self._analysis_generation and self.fft_enabled:
            self.fftDataReady.emit(frequencies, magnitudes)

    @Slot(int, object)
    def _publishBeatFrames(
        self, generation: int, frames: list[tuple[BeatFrame, float]]
    ) -> None:
        if generation != self._analysis_generation:
            return
        if not cfg.beat_detection_enabled:
            self.beatDataReady.emit(0.0, False)
            return
        for beat, presentation_time in frames:
            self.beatDataReady.emit(beat.intensity, beat.is_point)
            if beat.is_point:
                delay_ms = max(
                    0, round((presentation_time - time.perf_counter()) * 1000)
                )
                if delay_ms == 0:
                    self._publishBeatPoint(generation)
                else:
                    QTimer.singleShot(
                        delay_ms,
                        lambda g=generation: self._publishBeatPoint(g),
                    )

    def _publishBeatPoint(self, generation: int) -> None:
        if generation != self._analysis_generation or not cfg.beat_detection_enabled:
            return
        event_bus.emit(BEAT_POINT)

    def _fft_worker(self) -> None:
        spectrum = SpectrumAnalyzer()
        detector = BeatDetector()
        generation = -1
        sample_rate = 0
        last_sequence = -1
        analyzed_samples = 0
        beat_enabled = False
        detector_settings: tuple[float, int, int, int] | None = None
        while self.fft_thread_running:
            packet = self.fft_queue.get()
            if packet is None or not self.fft_thread_running:
                break
            (
                packet_generation,
                packet_sequence,
                rate,
                presentation_time,
                chunk,
                beat_samples,
            ) = packet
            if packet_generation != self._analysis_generation:
                continue
            if (
                packet_generation != generation
                or rate != sample_rate
                or (last_sequence >= 0 and packet_sequence != last_sequence + 1)
            ):
                spectrum.reset()
                detector.reset()
                analyzed_samples = 0
            generation = packet_generation
            sample_rate = rate
            last_sequence = packet_sequence
            hop_seconds = cfg.beat_detection_hop_seconds
            low_hz = cfg.beat_detection_low_hz
            high_hz = cfg.beat_detection_high_hz
            settings = (hop_seconds, low_hz, high_hz, beat_samples.shape[1])
            if (
                beat_enabled != cfg.beat_detection_enabled
                or settings != detector_settings
            ):
                detector.reset()
                analyzed_samples = 0
            beat_enabled = cfg.beat_detection_enabled
            detector_settings = settings

            try:
                if beat_enabled:
                    beat_frames = detector.process(
                        beat_samples,
                        sample_rate,
                        sensitivity=cfg.beat_detection_sensitivity,
                        smoothing=cfg.beat_detection_smoothing,
                        hop_seconds=hop_seconds,
                        min_interval=cfg.beat_detection_min_interval,
                        low_hz=low_hz,
                        high_hz=high_hz,
                        point_threshold=cfg.beat_detection_point_threshold,
                    )
                else:
                    beat_frames = [BeatFrame(0.0, 0.0, False)]
            except Exception:
                self._logger.exception('beat analysis failed')
                detector.reset()
                analyzed_samples = 0
                beat_frames = []
            block_start = analyzed_samples / max(1, sample_rate)
            analyzed_samples += len(beat_samples)
            if not self.fft_thread_running:
                break
            self._beatFramesReady.emit(
                generation,
                [
                    (beat, presentation_time + beat.time - block_start)
                    for beat in beat_frames
                ],
            )

            if not self.fft_enabled:
                spectrum.reset()
                continue

            try:
                fft_freqs, fft_vals = spectrum.process(
                    chunk, self.fft_size, sample_rate
                )
            except Exception:
                self._logger.exception('spectrum analysis failed')
                spectrum.reset()
                continue
            if not self.fft_thread_running:
                break
            self._spectrumReady.emit(generation, fft_freqs, fft_vals)

    def stop_fft_thread(self, timeout: float = 0.5) -> None:
        self.fft_thread_running = False
        try:
            self.fft_queue.put_nowait(None)
        except Full:
            try:
                self.fft_queue.get_nowait()
            except Empty:
                pass
            try:
                self.fft_queue.put_nowait(None)
            except Full:
                pass
        if self.fft_thread.is_alive():
            self.fft_thread.join(timeout=timeout)

    def shutdown(self) -> None:
        self._logger.info('shutting down')
        event_bus.unsubscribe(_100MS_TICK, self._emitPlaybackTelemetry)
        event_bus.unsubscribe(COLLECT_DEBUG_INFO, self.emitDebugInfo)
        self.stop()
        self.stop_fft_thread()
        self._closeFinishedStreams(wait=True)
        with self._lock:
            if self.stream:
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception:
                    self._logger.debug('failed to close audio stream', exc_info=True)
                self.stream = None
            self._clearQueue()

    def _audio_callback(
        self, outdata: np.ndarray, frames: int, _time_info: Any, _status: Any
    ) -> None:
        with self._lock:
            self._renderAudio(outdata, frames, _time_info, _status)

    def _renderAudio(
        self, outdata: np.ndarray, frames: int, _time_info: Any, _status: Any
    ) -> None:
        generation = self._analysis_generation
        presentation_time = time.perf_counter() + max(
            0.0, _time_info.outputBufferDacTime - _time_info.currentTime
        )
        self._analysis_sequence += 1
        sequence = self._analysis_sequence
        sample_rate = self.sample_rate
        outdata[:] = 0
        if _status.output_underflow:
            self._output_underflows += 1
        source = self._scrub_samples
        if source is not None:
            if not self._scrub_handoff or self._audio_queue.empty():
                start = self._scrub_frame
                raw = source[start : start + frames]
                copy_len = len(raw)
                if copy_len:
                    chunk = raw.astype(np.float32, copy=False) / self._scrub_scale
                    if chunk.shape[1] == 1 and self.output_channels == 2:
                        chunk = np.repeat(chunk, 2, axis=1)
                    elif chunk.shape[1] > 2:
                        chunk = np.stack(
                            (chunk[:, ::2].mean(axis=1), chunk[:, 1::2].mean(axis=1)),
                            axis=1,
                        )
                    if self.output_channels == 1 and chunk.shape[1] > 1:
                        chunk = chunk.mean(axis=1, keepdims=True)
                    elif not cfg.stereo and chunk.shape[1] == 2:
                        chunk = np.repeat(chunk.mean(axis=1, keepdims=True), 2, axis=1)
                    played_chunk = chunk * self.volume_gain * self.loudness_gain
                    np.clip(
                        played_chunk,
                        -1.0,
                        (61.0 + cfg.target_lufs) * 3.0,
                        out=played_chunk,
                    )
                    outdata[:copy_len, : self.output_channels] = played_chunk[
                        :, : self.output_channels
                    ]
                    self._scrub_frame += copy_len
                    self._smooth_position_start = start / self.sample_rate
                    self._playback_time = self._scrub_frame / self.sample_rate
                    self._smooth_position_end = self._playback_time
                    self._smooth_position_started_at = time.perf_counter()
                    self._smooth_position_duration = frames / self.sample_rate
                return
            self._scrub_samples = None
            self._scrub_handoff = False
        try:
            item = self._audio_queue.get_nowait()
        except Empty:
            self._queue_underruns += 1
            return

        if item is None:
            if self._growing_file_path is not None and not self._growing_file_complete:
                return
            self.is_playing = False
            self.is_paused = False
            self._queueCallbackEvent('full_finished')
            raise sd.CallbackStop

        chunk, src_frames, block_gain = item
        copy_len = min(len(chunk), frames)
        gain = self.volume_gain * (
            self.loudness_gain if block_gain is None else block_gain
        )
        played_chunk = outdata[:copy_len, : self.output_channels]
        np.multiply(chunk[:copy_len, : self.output_channels], gain, out=played_chunk)
        np.clip(
            played_chunk,
            -1.0,
            (61.0 + cfg.target_lufs) * 3.0,
            out=played_chunk,
        )

        smooth_position_start = (
            self.current_index - self._track_origin
        ) / self.sample_rate
        self.current_index = min(self.current_index + src_frames, self._sampleCount())
        self._playback_time = (
            self.current_index - self._track_origin
        ) / self.sample_rate
        self._smooth_position_start = smooth_position_start
        self._smooth_position_end = self._playback_time
        self._smooth_position_started_at = time.perf_counter()
        self._smooth_position_duration = frames / max(1, self._streamSampleRate())

        growing_file_incomplete = (
            self._growing_file_path is not None and not self._growing_file_complete
        )
        waiting_for_file = growing_file_incomplete and self.current_index >= len(
            self.samples
        )
        finished = self.current_index >= self._sampleCount() and not waiting_for_file
        skip_nosound = False
        monitor_chunk = (
            played_chunk.mean(axis=1) if played_chunk.ndim == 2 else played_chunk
        )

        if not finished:
            rms = np.sqrt(np.mean(monitor_chunk**2))
            if rms > 0:
                self.db = 20 * np.log10(rms)
            else:
                self.db = -100

            if not growing_file_incomplete and self._timeline is None:
                remain = self.getLength() - self._playback_time
                if (
                    (
                        (remain < cfg.skip_remain_time)
                        if cfg.skip_remain_time < 60
                        else True
                    )
                    and cfg.skip_nosound
                    and self.db < cfg.skip_threshold
                ):
                    skip_nosound = True

        if copy_len > 0 and (self.fft_enabled or cfg.beat_detection_enabled):
            packet = (
                generation,
                sequence,
                sample_rate,
                presentation_time,
                monitor_chunk,
                chunk[:copy_len, : self.output_channels],
            )
            try:
                self.fft_queue.put_nowait(packet)
            except Full:
                try:
                    self.fft_queue.get_nowait()
                except Empty:
                    pass
                try:
                    self.fft_queue.put_nowait(packet)
                except Full:
                    pass

        if finished:
            self.is_playing = False
            self.is_paused = False
            self._queueCallbackEvent('full_finished')
            raise sd.CallbackStop

        if skip_nosound:
            self.is_playing = False
            self.is_paused = False
            self._logger.info(f'skip {self.db=}')
            self._queueCallbackEvent('ending_no_sound')
            raise sd.CallbackStop

    def _queueCallbackEvent(self, event_name: str) -> None:
        with self._callback_events_lock:
            if self.stream is not None:
                self._finished_streams.append(self.stream)
                self.stream = None
            if event_name == 'full_finished':
                self._pending_full_finished = True
            elif event_name == 'ending_no_sound':
                self._pending_ending_no_sound = True

    def _emitPlaybackTelemetry(self) -> None:
        self._closeFinishedStreams()
        self.positionChanged.emit(self._playback_time)
        if self.is_playing:
            event_bus.emit(DB_CHANGED, self, self.db)

        with self._callback_events_lock:
            full_finished = self._pending_full_finished
            ending_no_sound = self._pending_ending_no_sound
            self._pending_full_finished = False
            self._pending_ending_no_sound = False

        if full_finished:
            self.onFullFinished.emit()
        if ending_no_sound:
            self.onEndingNoSound.emit()

    def _closeFinishedStreams(self, wait: bool = False) -> None:
        with self._callback_events_lock:
            streams = self._finished_streams
            self._finished_streams = []
        for stream in streams:
            try:
                if stream.active:
                    if not wait:
                        with self._callback_events_lock:
                            self._finished_streams.append(stream)
                        continue
                    stream.stop()
                stream.close()
            except Exception:
                self._logger.exception('failed to close finished audio stream')

    def _clearQueue(self) -> None:
        with self._audio_queue.mutex:
            self._audio_queue.queue.clear()
            self._audio_queue.not_full.notify_all()
        self._producer_index = self.current_index
        self._prepared_start_index = self.current_index
        self._prepared_end_index = self.current_index

    def _startProducer(self) -> None:
        self._producer_running = True
        self._producer_seq += 1
        producer_seq = self._producer_seq
        self._producer_last_resource_sample = time.perf_counter()
        self._producer_thread = threading.Thread(
            target=lambda: self._producerLoop(producer_seq), daemon=True
        )
        self._producer_thread.start()

    def _stopProducer(self) -> None:
        self._producer_running = False
        self._producer_seq += 1
        if self._producer_thread is not None and self._producer_thread.is_alive():
            self._producer_thread.join(timeout=0)
        self._producer_thread = None

    def _producerPreparedLead(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return max(
            0.0, (self._prepared_end_index - self.current_index) / self.sample_rate
        )

    def _producerThreadAlive(self) -> bool:
        return self._producer_thread is not None and self._producer_thread.is_alive()

    def _producerDesiredLead(self) -> float:
        sample_count = self._sampleCount()
        if sample_count == 0:
            return _PRODUCER_EARLY_LEAD

        resources_sampled = self._producer_memory_load > 0.0
        stressed = self._producer_cpu_load > 70.0 or (
            resources_sampled and self._producer_memory_load > 88.0
        )
        idle = (
            resources_sampled
            and self._producer_cpu_load < 35.0
            and self._producer_memory_load < 75.0
        )
        progress = self.current_index / sample_count

        if progress < _PRODUCER_PROGRESS_BOOST_RATIO:
            if stressed:
                return _PRODUCER_EARLY_STRESSED_LEAD
            if idle:
                return _PRODUCER_EARLY_IDLE_LEAD
            return _PRODUCER_EARLY_LEAD

        if stressed:
            return _PRODUCER_LATE_STRESSED_LEAD
        if idle:
            return _PRODUCER_LATE_IDLE_LEAD
        return _PRODUCER_LATE_LEAD

    def _sampleProducerResources(self, force: bool = False) -> None:
        now = time.perf_counter()
        elapsed = now - self._producer_last_resource_sample
        if not force and elapsed < 0.75:
            return

        cpu_load = _getCpuLoad()
        if self._producer_cpu_load == 0.0:
            self._producer_cpu_load = cpu_load
        else:
            self._producer_cpu_load += (cpu_load - self._producer_cpu_load) * 0.35
        self._producer_memory_load = _getMemoryLoad()
        self._producer_last_resource_sample = now

    def _waitingForGrowingFile(self) -> bool:
        with self._lock:
            return (
                self._growing_file_path is not None
                and not self._growing_file_complete
                and self._producer_index >= self._sampleCount()
            )

    def _producerLoop(self, producer_seq: int) -> None:
        processor = AudioProcessor()
        finished = False
        _getCpuLoad()
        while self._producer_running and producer_seq == self._producer_seq:
            with self._lock:
                if self._timeline is not None:
                    self._timeline.discardBefore(
                        min(self.current_index, self._producer_index)
                        - self.sample_rate * 2
                    )
            self._sampleProducerResources()

            target_lead = self._producerDesiredLead()
            self._producer_target_lead += (
                target_lead - self._producer_target_lead
            ) * 0.2

            lead = self._producerPreparedLead()
            if lead >= max(
                _PRODUCER_MIN_REFILL_LEAD,
                self._producer_target_lead * _PRODUCER_REFILL_RATIO,
            ):
                time.sleep(0.02)
                continue

            with self._lock:
                batch_start_index = max(
                    self._prepared_start_index,
                    min(self._producer_index, self._prepared_end_index),
                )
                batch_end_index = self._prepared_end_index

            waiting_for_growing_file = False
            produced_blocks = 0
            while self._producer_running and producer_seq == self._producer_seq:
                if self.sample_rate <= 0:
                    break
                if (
                    batch_end_index - self.current_index
                ) / self.sample_rate >= self._producer_target_lead:
                    break

                with self._lock:
                    if (
                        not self._producer_running
                        or producer_seq != self._producer_seq
                        or self._sampleCount() == 0
                    ):
                        break
                    if self._producer_index >= self._sampleCount():
                        waiting_for_growing_file = (
                            self._growing_file_path is not None
                            and not self._growing_file_complete
                        )
                        if waiting_for_growing_file:
                            break
                        finished = True
                        break

                    if self._growing_stream_mode and (
                        self._sampleCount() - self._producer_index
                        < max(
                            self._BLOCK_SIZE, round(self._BLOCK_SIZE * self.play_speed)
                        )
                    ):
                        waiting_for_growing_file = True
                        break

                    start_idx = int(self._producer_index)
                    processor.samples = self.samples
                    timeline = self._timeline
                    if timeline is None:
                        processor.timeline = None
                    else:
                        snapshot = PcmTimeline(timeline.channels)
                        snapshot.blocks = timeline.blocks.copy()
                        snapshot.end = timeline.end
                        processor.timeline = snapshot
                    processor.sample_rate = self.sample_rate
                    processor.channels = self.channels
                    processor.settings = AudioProcessingSettings(
                        play_speed=self.play_speed,
                        play_pitch=self.play_pitch,
                        speed_animating=self._speed_animating_flag,
                        stereo=cfg.stereo,
                        stereo_haas_index=cfg.stereo_haas_index,
                        enable_reverb=cfg.enable_reverb,
                        reverb_intensity=cfg.reverb_intensity,
                    )

                out, src_frames = processor.render(start_idx, self._BLOCK_SIZE)

                try:
                    with self._lock:
                        if (
                            not self._producer_running
                            or producer_seq != self._producer_seq
                        ):
                            break
                        if len(out) == 0:
                            waiting_for_growing_file = (
                                self._growing_file_path is not None
                                and not self._growing_file_complete
                            )
                            break
                        next_index = min(start_idx + src_frames, self._sampleCount())
                        block_gain = 1.0 if self._timeline is not None else None
                        self._audio_queue.put_nowait((out, src_frames, block_gain))
                        self._producer_index = next_index
                        batch_end_index = max(batch_end_index, next_index)
                        self._prepared_end_index = max(
                            self._prepared_end_index, next_index
                        )
                        produced_blocks += 1
                except Full:
                    time.sleep(0.02)
                    break

                if finished:
                    break

                if produced_blocks % _PRODUCER_YIELD_BLOCKS == 0:
                    self._sampleProducerResources()
                    target_lead = self._producerDesiredLead()
                    self._producer_target_lead += (
                        target_lead - self._producer_target_lead
                    ) * 0.2
                    time.sleep(0)

            self._sampleProducerResources(force=self._producer_memory_load == 0.0)

            with self._lock:
                if (
                    self._producer_running
                    and producer_seq == self._producer_seq
                    and batch_end_index > self._prepared_end_index
                ):
                    self._prepared_start_index = batch_start_index
                    self._prepared_end_index = batch_end_index

            if finished:
                break

            if waiting_for_growing_file or self._waitingForGrowingFile():
                self.refreshGrowingFile()
                time.sleep(0.05)
                continue

            time.sleep(0.02)

        with self._lock:
            if not finished or producer_seq != self._producer_seq:
                return
            try:
                self._audio_queue.put_nowait(None)
            except Full:
                pass

    def setOutputDevice(self, device: DevicesInfo) -> None:
        with self._lock:
            was_playing = self.is_playing or (
                self.stream is not None and self.stream.active
            )
            self._stopProducer()
            if self.stream:
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception:
                    self._logger.debug('failed to close audio stream', exc_info=True)
                self.stream = None

            self._device_id = device.index

            if was_playing:
                self._startProducer()
                self._startStream()

    def getCurrentOutputDevice(self) -> DevicesInfo | None:
        devices = getAudioDevices()
        for dev in devices:
            if dev.index == self._device_id:
                return dev
        return devices[0] if devices else None
