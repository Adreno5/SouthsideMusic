from __future__ import annotations

import array
import ctypes
from dataclasses import dataclass
import io
import json
import logging
from math import gcd
import struct
import subprocess
from queue import Empty, Full, Queue
import sys
import time
import numpy as np
import psutil
import sounddevice as sd
from pathlib import Path
from imports import (
    DB_CHANGED,
    QObject,
    QEasingCurve,
    QPropertyAnimation,
    QTimer,
    Signal,
    Slot,
    event_bus,
    Property,
)
from services.events.events import BEAT_POINT, COLLECT_DEBUG_INFO, EMIT_DEBUG_INFO
from typing import Any, Callable, Optional, override
import threading
from scipy.fft import rfft, rfftfreq
from scipy.signal import resample_poly
from imports import MessageBox
from core.config import cfg
from core.pcm_timeline import PcmTimeline
from core.beat import BeatDetector, BeatFrame

from pydub.utils import fsdecode, audioop, get_prober_name, mediainfo_json
from pydub.exceptions import CouldntDecodeError
from pydub import AudioSegment
from collections import namedtuple, OrderedDict

_AUDIO_DECODE_CACHE: OrderedDict[str, AudioSegment] = OrderedDict()
_AUDIO_CACHE_LOCK = threading.Lock()
_AUDIO_CACHE_MAX = 10
_MAX_HAAS_DELAY_MS = 30
_MIN_AUDIBLE_PITCH_SHIFT = 0.25
_REVERB_DELAY_MS = (29, 43, 61, 79)
_REVERB_TAP_GAINS = (0.42, 0.31, 0.22, 0.15)
_REVERB_GAIN_COMPENSATION = 0.18
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
        pass
    return 0.0


def _getCpuLoad() -> float:
    try:
        return max(0.0, min(100.0, float(psutil.cpu_percent(interval=None))))
    except Exception:
        return 0.0


def cacheDecodedAudio(key: str, segment: AudioSegment) -> None:
    with _AUDIO_CACHE_LOCK:
        _AUDIO_DECODE_CACHE[key] = segment
        _AUDIO_DECODE_CACHE.move_to_end(key)
        while len(_AUDIO_DECODE_CACHE) > _AUDIO_CACHE_MAX:
            _AUDIO_DECODE_CACHE.popitem(last=False)


def getCachedAudio(key: str) -> Optional[AudioSegment]:
    with _AUDIO_CACHE_LOCK:
        seg = _AUDIO_DECODE_CACHE.get(key)
        if seg is not None:
            _AUDIO_DECODE_CACHE.move_to_end(key)
        return seg


WavSubChunk = namedtuple('WavSubChunk', ['id', 'position', 'size'])


def extractWavHeaders(data):
    # def search_subchunk(data, subchunk_id):
    pos = 12  # The size of the RIFF chunk descriptor
    subchunks = []
    while pos + 8 <= len(data) and len(subchunks) < 10:
        subchunk_id = data[pos : pos + 4]
        subchunk_size = struct.unpack_from('<I', data[pos + 4 : pos + 8])[0]
        subchunks.append(WavSubChunk(subchunk_id, pos, subchunk_size))
        if subchunk_id == b'data':
            # 'data' is the last subchunk
            break
        pos += subchunk_size + 8

    return subchunks


def fixWavHeaders(data):
    headers = extractWavHeaders(data)
    if not headers or headers[-1].id != b'data':
        return

    # TODO: Handle huge files in some other way
    if len(data) > 2**32:
        raise CouldntDecodeError('Unable to process >4GB files')

    # Set the file size in the RIFF chunk descriptor
    data[4:8] = struct.pack('<I', len(data) - 8)

    # Set the data size in the data subchunk
    pos = headers[-1].position
    data[pos + 4 : pos + 8] = struct.pack('<I', len(data) - pos - 8)


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


class PatchedAudioSegment(AudioSegment):
    _logger = logging.getLogger(__name__)

    @override
    @classmethod
    def from_file(
        cls,
        file: bytes | str | Path | io.BytesIO,
    ) -> 'PatchedAudioSegment':
        filename: str | None
        stdin_parameter = None
        stdin_data = None

        if isinstance(file, bytes):
            filename = None
            stdin_data = file
        else:
            try:
                filename = fsdecode(file)
            except TypeError:
                filename = None
                if isinstance(file, io.BytesIO):
                    file.seek(0)
                    stdin_data = file.read()

        conversion_command = [
            cls.converter,
            '-y',
        ]

        if filename:
            conversion_command += ['-i', filename]
        else:
            stdin_parameter = subprocess.PIPE
            conversion_command += ['-i', 'pipe:0']

        info = None
        if filename:
            info = mediainfo_json(filename, read_ahead_limit=-1)
        elif stdin_data is not None:
            probe_command = [
                get_prober_name(),
                '-of',
                'json',
                '-v',
                'info',
                '-show_format',
                '-show_streams',
                'pipe:0',
            ]
            probe = subprocess.Popen(
                probe_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            probe_out, _probe_err = probe.communicate(input=stdin_data)
            if probe.returncode == 0 and probe_out:
                info = json.loads(probe_out.decode('utf-8', 'ignore'))

        if info:
            audio_streams = [x for x in info['streams'] if x['codec_type'] == 'audio']
            # This is a workaround for some ffprobe versions that always say
            # that mp3/mp4/aac/webm/ogg files contain fltp samples
            audio_codec = audio_streams[0].get('codec_name')
            if audio_streams[0].get('sample_fmt') == 'fltp' and audio_codec in [
                'mp3',
                'mp4',
                'aac',
                'webm',
                'ogg',
            ]:
                bits_per_sample = 16
            else:
                bits_per_sample = int(
                    audio_streams[0].get('bits_per_sample')
                    or audio_streams[0].get('bits_per_raw_sample')
                    or 0
                )
            if bits_per_sample <= 0:
                acodec = None
            elif bits_per_sample == 8:
                acodec = 'pcm_u8'
            else:
                acodec = 'pcm_s%dle' % bits_per_sample

            if acodec is not None:
                conversion_command += ['-acodec', acodec]

        conversion_command += [
            '-vn',  # Drop any video streams if there are any
            '-f',
            'wav',
        ]

        conversion_command += ['-']

        p = subprocess.Popen(
            conversion_command,
            stdin=stdin_parameter,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        p_out, p_err = p.communicate(input=stdin_data)

        cls._logger.debug(conversion_command)

        if p.returncode != 0 or len(p_out) == 0:
            raise CouldntDecodeError(
                'Decoding failed. ffmpeg returned error code: {0}\n\nOutput from ffmpeg/avlib:\n\n{1}'.format(
                    p.returncode, p_err.decode(errors='ignore')
                )
            )

        p_out = bytearray(p_out)
        fixWavHeaders(p_out)
        p_out = bytes(p_out)
        obj = cls(p_out)

        return obj

    @override
    def set_channels(self, channels):
        if channels == self.channels:
            return self

        data = self._data
        assert data is not None, 'AudioSegment._data is None'

        if channels == 2 and self.channels == 1:
            converted = audioop.tostereo(data, self.sample_width, 1, 1)
            frame_width = self.frame_width * 2
        elif channels == 1 and self.channels == 2:
            converted = audioop.tomono(data, self.sample_width, 0.5, 0.5)
            frame_width = self.frame_width // 2
        elif channels == 1:
            channels_data = [seg.get_array_of_samples() for seg in self.split_to_mono()]
            frame_count = int(self.frame_count())
            converted = array.array(
                channels_data[0].typecode, b'\0' * (frame_count * self.sample_width)
            )
            for raw_channel_data in channels_data:
                for i in range(frame_count):
                    converted[i] += raw_channel_data[i] // self.channels
            frame_width = self.frame_width // self.channels
        elif self.channels == 1:
            dup_channels = [self for _ in range(channels)]
            return PatchedAudioSegment.from_mono_audiosegments(*dup_channels)
        else:
            raise ValueError(
                'AudioSegment.set_channels only supports mono-to-multi channel and multi-to-mono channel conversion'
            )

        return self._spawn(
            data=converted, overrides={'channels': channels, 'frame_width': frame_width}
        )


def decodeAudioWithSidecar(
    file: bytes | str | Path | io.BytesIO,
    sidecar: Any | None = None,
    *,
    timeout: float = 90.0,
) -> PatchedAudioSegment:
    if sidecar is None:
        return PatchedAudioSegment.from_file(file)

    payload: dict[str, object]
    if isinstance(file, bytes):
        payload = {'data': file}
    elif isinstance(file, io.BytesIO):
        file.seek(0)
        payload = {'data': file.read()}
    else:
        payload = {'path': str(file)}

    try:
        decoded = sidecar.call('decode_audio', payload, timeout=timeout)
    except Exception as e:
        logging.getLogger(__name__).debug('sidecar audio decode failed: %s', e)
        decoded = None

    if isinstance(decoded, bytes):
        return PatchedAudioSegment(decoded)
    return PatchedAudioSegment.from_file(file)


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

    def __init__(self, parent=None, devices: list[DevicesInfo] | None = None):
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
        self.stream: Optional[sd.OutputStream] = None
        self.volume_gain: float = 1.0
        self.loudness_gain: float = 1.0
        self._volume_anim: Optional[QPropertyAnimation] = None
        self._gain_anim: Optional[QPropertyAnimation] = None
        self._speed_anim: Optional[QPropertyAnimation] = None
        self._pitch_anim: Optional[QPropertyAnimation] = None
        self._speed_animating_flag: bool = False

        self.fft_enabled = True
        self.fft_size = int(cfg.fft_size)
        self._analysis_generation = 0
        self._analysis_sequence = 0
        self._beatFramesReady.connect(self._publishBeatFrames)

        self.play_speed = cfg.play_speed
        self.play_pitch = cfg.play_pitch

        self._BLOCK_SIZE = 2048

        self._audio_queue: Queue[tuple[np.ndarray, int, float | None] | None] = Queue(
            maxsize=_PRODUCER_QUEUE_BLOCKS
        )
        self._producer_running = False
        self._producer_thread: Optional[threading.Thread] = None
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
        self._wsola_output_buffer: np.ndarray | None = None
        self._wsola_tail: np.ndarray | None = None
        self._wsola_buffer_start_index: float = 0.0
        self._wsola_next_source_index: float = 0.0
        self._wsola_speed: float = 1.0
        self._stereo_tail: np.ndarray | None = None
        self._reverb_tail: np.ndarray | None = None
        self._growing_file_path: Path | None = None
        self._growing_file_complete = True
        self._growing_file_size = 0
        self._growing_file_last_decode = 0.0
        self._growing_stream_mode = False
        self._callback_events_lock = threading.Lock()
        self._pending_full_finished = False
        self._pending_ending_no_sound = False

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
            tuple[int, int, int, np.ndarray, np.ndarray] | None
        ] = Queue(maxsize=8)
        self.fft_thread_running = True
        self.fft_thread = threading.Thread(target=self._fft_worker, daemon=True)
        self.fft_thread.start()

        self._telemetry_timer = QTimer(self)
        self._telemetry_timer.timeout.connect(self._emitPlaybackTelemetry)
        self._telemetry_timer.start(100)

        event_bus.subscribe(COLLECT_DEBUG_INFO, self.emitDebugInfo)

    def emitDebugInfo(self):
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
        samples_raw = np.array(audio.get_array_of_samples(), dtype=np.float32)
        max_val = np.iinfo(audio.array_type).max if audio.sample_width != 4 else 2**31
        normalized = samples_raw / max_val

        if audio.channels <= 1:
            return normalized.reshape(-1, 1)

        frame_count = len(samples_raw) // audio.channels
        multi = normalized.reshape(frame_count, audio.channels)
        # Always force stereo: mix >2ch down to stereo, pass stereo through
        if audio.channels == 2:
            return multi
        left = multi[:, ::2].mean(axis=1)
        right = multi[:, 1::2].mean(axis=1)
        return np.stack((left, right), axis=1)

    def _resetStereoEffect(self) -> None:
        self._stereo_tail = None

    def _applyStereoEffect(self, mono_chunk: np.ndarray) -> np.ndarray:
        stereo_chunk = np.repeat(mono_chunk.reshape(-1, 1), 2, axis=1)
        if not cfg.stereo or cfg.stereo_haas_index == 0 or len(mono_chunk) == 0:
            self._resetStereoEffect()
            return stereo_chunk

        delay_ms = min(max(0, cfg.stereo_haas_index), _MAX_HAAS_DELAY_MS)
        if delay_ms == 0:
            self._resetStereoEffect()
            return stereo_chunk
        delay = min(
            max(1, int(self.sample_rate * delay_ms / 1000)),
            max(1, len(self.samples) // 8),
        )
        mono = mono_chunk.astype(np.float32, copy=False)
        tail = self._stereo_tail
        if tail is None:
            tail = np.zeros(delay, dtype=np.float32)
        elif len(tail) < delay:
            tail = np.concatenate((np.zeros(delay - len(tail), dtype=np.float32), tail))
        else:
            tail = tail[-delay:]

        history = np.concatenate((tail, mono))
        stereo_chunk[:, 1] = history[: len(mono)] * 0.82
        self._stereo_tail = history[-delay:].copy()

        return stereo_chunk

    def _resetReverb(self) -> None:
        self._reverb_tail = None

    def _applyReverb(self, chunk: np.ndarray) -> np.ndarray:
        if not cfg.enable_reverb or cfg.reverb_intensity == 0 or len(chunk) == 0:
            return chunk

        delays = [max(1, int(self.sample_rate * ms / 1000)) for ms in _REVERB_DELAY_MS]
        max_delay = max(delays)
        channels = chunk.shape[1] if chunk.ndim == 2 else 1
        tail = self._reverb_tail
        if tail is None or tail.ndim != 2 or tail.shape[1] != channels:
            tail = np.zeros((max_delay, channels), dtype=np.float32)
        elif len(tail) < max_delay:
            padding = np.zeros((max_delay - len(tail), channels), dtype=np.float32)
            tail = np.concatenate((padding, tail), axis=0)
        else:
            tail = tail[-max_delay:]

        dry = chunk.reshape(-1, channels).astype(np.float32, copy=False)
        history = np.concatenate((tail, dry), axis=0)
        start = len(tail)
        end = start + len(dry)
        wet = np.zeros_like(dry)
        for delay, tap_gain in zip(delays, _REVERB_TAP_GAINS):
            wet += history[start - delay : end - delay] * tap_gain

        mix = cfg.reverb_intensity
        gain = 1.0 + mix * _REVERB_GAIN_COMPENSATION
        out = (dry * (1.0 - mix * 0.25) + wet * (mix * 0.55)) * gain
        self._reverb_tail = history[-max_delay:].copy()
        return out.astype(np.float32, copy=False)

    def _remapChannels(self, target_channels: int) -> None:
        if self.samples.ndim != 2 or self.channels == target_channels:
            self.channels = target_channels if self.samples.ndim == 2 else 1
            self.output_channels = self.channels
            return

        if target_channels <= 1:
            self.samples = self.samples.mean(axis=1, keepdims=True)
        elif target_channels == 2:
            left = self.samples[:, ::2]
            right = self.samples[:, 1::2]
            mono = self.samples.mean(axis=1)
            left_mix = left.mean(axis=1) if left.shape[1] > 0 else mono
            right_mix = right.mean(axis=1) if right.shape[1] > 0 else mono
            self.samples = np.stack((left_mix, right_mix), axis=1)
        else:
            self.samples = self.samples[:, :target_channels]

        self.channels = self.samples.shape[1]
        self.output_channels = self.channels

    def _resetGrowingFile(self) -> None:
        self._growing_file_path = None
        self._growing_file_complete = True
        self._growing_file_size = 0
        self._growing_file_last_decode = 0.0
        self._growing_stream_mode = False

    def _decodeFile(self, file_path: Path) -> PatchedAudioSegment:
        return PatchedAudioSegment.from_file(str(file_path))

    @classmethod
    def prepareBuffer(cls, audio: PatchedAudioSegment) -> PreparedAudioBuffer:
        samples = cls._prepareSamples(audio)
        channels = samples.shape[1] if samples.ndim == 2 else 1
        return PreparedAudioBuffer(samples, audio.frame_rate, channels)

    def _applyPreparedBuffer(self, prepared: PreparedAudioBuffer) -> None:
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
        self._producer_target_lead = _PRODUCER_EARLY_LEAD
        self._resetWsola()
        self._resetStereoEffect()
        self._resetReverb()
        self._resetBeatAnalysis()
        self._playback_time = 0.0
        self.is_playing = False
        self.is_paused = False

    def _applyAudio(self, audio: PatchedAudioSegment) -> None:
        self._applyPreparedBuffer(self.prepareBuffer(audio))

    def load(self, audio: PatchedAudioSegment) -> None:
        with self._lock:
            self._stopProducer()
            self.stop()
            if self.stream:
                self.stream.close()
                self.stream = None

            self._applyAudio(audio)
            self._resetGrowingFile()

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
        self._resetWsola()
        self._resetStereoEffect()
        self._resetReverb()
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
            self._resetWsola()
            self._resetBeatAnalysis()
            self._resetStereoEffect()
            self._resetReverb()
            self.current_index = frame
            self._clearQueue()
            if self.is_playing or (self.stream is not None and self.stream.active):
                self._startProducer()
            return True

    def beginQueuedTrack(self, origin: int, frames: int, gain: float) -> None:
        """Change the displayed track, leaving queued PCM and DSP untouched."""
        with self._lock:
            self._track_origin = origin
            self._track_frames = frames
            self._queued_restore = None
            self.loudness_gain = gain
            self._playback_time = (self.current_index - origin) / self.sample_rate
            self._smooth_position_end = self._playback_time
            self._smooth_position_start = self._playback_time

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
            self.stop(clear_growing_file=False)
            if self.stream:
                self.stream.close()
                self.stream = None

            self._applyAudio(audio)
            self._growing_file_path = file_path
            self._growing_file_complete = complete
            self._growing_file_size = file_size
            self._growing_file_last_decode = time.perf_counter()
            self._growing_stream_mode = False
        return audio

    def loadGrowingStream(
        self,
        file_path: Path,
        sample_rate: int,
        channels: int,
    ) -> None:
        with self._lock:
            self._stopProducer()
            self.stop(clear_growing_file=False)
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
            self._producer_target_lead = _PRODUCER_EARLY_LEAD
            self._resetWsola()
            self._resetStereoEffect()
            self._resetReverb()
            self._resetBeatAnalysis()
            self._playback_time = 0.0
            self.is_playing = False
            self.is_paused = False
            self._growing_file_path = file_path
            self._growing_file_complete = False
            self._growing_file_size = 0
            self._growing_file_last_decode = time.perf_counter()
            self._growing_stream_mode = True

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
            if len(self.samples) == 0:
                self.samples = chunk
            else:
                self.samples = np.concatenate((self.samples, chunk), axis=0)
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

    def setSampleRate(self, rate: int):
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
                    pass
                self.stream = None
            self.sample_rate = rate
            self._clearQueue()
            self._resetBeatAnalysis()
            self._resetWsola()
            self._resetStereoEffect()
            self._resetReverb()
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
            if len(samples) <= old_len and not force:
                return False
            if self.sample_rate != audio.frame_rate and old_len > 0:
                return False
            self.sample_rate = audio.frame_rate
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
                self.sample_rate = audio.frame_rate
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
                self._resetWsola()
                self._resetStereoEffect()
                self._resetReverb()
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
                self._resetWsola()
                self._resetStereoEffect()
                self._resetReverb()
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
            self._resetWsola()
            self._resetStereoEffect()
            self._resetReverb()
            self._resetBeatAnalysis()
            self._playback_time = 0.0
            self.is_playing = False
            self.is_paused = False
            if clear_growing_file:
                self._resetGrowingFile()

    def setPosition(self, seconds: float) -> None:
        self._last_seek_at = time.perf_counter()
        if self._timeline is not None:
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
            self._resetWsola()
            self._resetBeatAnalysis()
            self._resetStereoEffect()
            self._resetReverb()
            self._clearQueue()
            if self.is_playing:
                self._startProducer()

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
            self._resetWsola()
            self._resetStereoEffect()
            self._resetReverb()
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
            self._resetWsola()
            self._resetStereoEffect()
            self._resetReverb()
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
            self._resetWsola()
            self._resetStereoEffect()
            self._resetReverb()
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
                pass
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
        self.beatDataReset.emit()

    @Slot(int, object)
    def _publishBeatFrames(self, generation: int, frames: list[BeatFrame]) -> None:
        if generation != self._analysis_generation:
            return
        if not cfg.beat_detection_enabled:
            self.beatDataReady.emit(0.0, False)
            return
        for beat in frames:
            self.beatDataReady.emit(beat.intensity, beat.is_point)
            if beat.is_point:
                event_bus.emit(BEAT_POINT)

    def _fft_worker(self) -> None:
        sample_history = np.zeros(0, dtype=np.float32)
        detector = BeatDetector()
        generation = -1
        sample_rate = 0
        beat_enabled = False
        while self.fft_thread_running:
            packet = self.fft_queue.get()
            if packet is None:
                break
            packet_generation, _packet_sequence, rate, chunk, beat_samples = packet
            if packet_generation != self._analysis_generation:
                continue
            if packet_generation != generation or rate != sample_rate:
                sample_history = np.zeros(0, dtype=np.float32)
                detector.reset()
            generation = packet_generation
            sample_rate = rate
            if beat_enabled != cfg.beat_detection_enabled:
                detector.reset()
            beat_enabled = cfg.beat_detection_enabled

            if beat_enabled:
                beat_frames = detector.process(
                    beat_samples,
                    sample_rate,
                    sensitivity=cfg.beat_detection_sensitivity,
                    smoothing=cfg.beat_detection_smoothing,
                    hop_seconds=cfg.beat_detection_hop_seconds,
                    min_interval=cfg.beat_detection_min_interval,
                    low_hz=cfg.beat_detection_low_hz,
                    high_hz=cfg.beat_detection_high_hz,
                    point_threshold=cfg.beat_detection_point_threshold,
                )
            else:
                beat_frames = [BeatFrame(0.0, 0.0, False)]
            self._beatFramesReady.emit(generation, beat_frames)

            if not self.fft_enabled:
                continue

            fft_size = max(1, int(self.fft_size))
            sample_history = np.concatenate((sample_history, chunk))
            if len(sample_history) > fft_size:
                sample_history = sample_history[-fft_size:]

            if len(sample_history) < fft_size:
                fft_input = np.zeros(fft_size, dtype=np.float32)
                fft_input[-len(sample_history) :] = sample_history
            else:
                fft_input = sample_history

            window = np.hanning(fft_size)
            windowed = fft_input * window
            fft_result = rfft(windowed)
            fft_vals = np.abs(np.asarray(fft_result, dtype=np.complex128))
            fft_freqs = rfftfreq(fft_size, 1 / sample_rate)

            self.fftDataReady.emit(fft_freqs, fft_vals)

    def stop_fft_thread(self, timeout: float = 0.5):
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
        self._telemetry_timer.stop()
        self.stop()
        self.stop_fft_thread()
        with self._lock:
            if self.stream:
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None
            self._clearQueue()

    def _resetWsola(self) -> None:
        self._wsola_output_buffer = None
        self._wsola_tail = None
        self._wsola_buffer_start_index = 0.0
        self._wsola_next_source_index = 0.0
        self._wsola_speed = 1.0

    def _pitchRatio(self) -> float:
        if abs(self.play_pitch) < _MIN_AUDIBLE_PITCH_SHIFT:
            return 1.0
        return 2 ** (self.play_pitch / 12.0)

    def _wsolaHopSize(self) -> int:
        return max(256, self.sample_rate // 43)

    def _wsolaSearchSize(self, hop: int) -> int:
        return min(hop // 2, max(32, self.sample_rate // 125))

    def _wsolaReadSource(self, start_idx: int, frames: int) -> np.ndarray:
        n = self._sampleCount()
        if n == 0 or frames <= 0:
            return np.zeros((0, self.channels), dtype=np.float32)

        start_idx = max(0, min(start_idx, n))
        end_idx = min(start_idx + frames, n)
        segment = self._readSamples(start_idx, end_idx).copy()
        if len(segment) >= frames:
            return segment.astype(np.float32, copy=False)

        if len(segment) > 0:
            pad_frame = segment[-1:]
        else:
            pad_frame = self._readSamples(n - 1, n)
        padding = np.repeat(pad_frame, frames - len(segment), axis=0)
        return np.concatenate((segment, padding), axis=0).astype(np.float32, copy=False)

    def _wsolaFindStart(self, ideal_start: int, overlap: int, search: int) -> int:
        tail = self._wsola_tail
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
        scores -= center_bias * 0.12
        return min_start + int(np.argmax(scores))

    def _wsolaResetFor(self, start_idx: int, speed: float) -> None:
        self._wsola_output_buffer = np.zeros((0, self.channels), dtype=np.float32)
        self._wsola_tail = None
        self._wsola_buffer_start_index = float(start_idx)
        self._wsola_next_source_index = float(start_idx)
        self._wsola_speed = speed

    def _wsolaNeedsReset(self, start_idx: int, speed: float) -> bool:
        if self._wsola_output_buffer is None:
            return True
        if self._wsola_output_buffer.ndim != 2:
            return True
        if self._wsola_output_buffer.shape[1] != self.channels:
            return True
        if abs(speed - self._wsola_speed) >= 1e-6:
            return True

        expected_start = int(round(self._wsola_buffer_start_index))
        return abs(start_idx - expected_start) > 16

    def _wsolaAppendFrame(self, speed: float, hop: int, search: int) -> bool:
        output_buffer = self._wsola_output_buffer
        if output_buffer is None:
            return False

        frame_size = hop * 2
        if self._wsola_tail is None:
            source_start = int(round(self._wsola_next_source_index))
            segment = self._wsolaReadSource(source_start, frame_size)
            if len(segment) == 0:
                return False

            self._wsola_output_buffer = np.concatenate(
                (output_buffer, segment[:hop]), axis=0
            )
            self._wsola_tail = segment[hop:frame_size].copy()
            self._wsola_next_source_index += hop * speed
            return True

        if self._wsola_next_source_index >= self._sampleCount():
            self._wsola_output_buffer = np.concatenate(
                (output_buffer, self._wsola_tail), axis=0
            )
            self._wsola_tail = None
            self._wsola_next_source_index = float(self._sampleCount())
            return True

        ideal_start = int(round(self._wsola_next_source_index))
        source_start = self._wsolaFindStart(ideal_start, hop, search)
        segment = self._wsolaReadSource(source_start, frame_size)
        if len(segment) == 0:
            return False

        fade_in = (
            0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, hop, dtype=np.float32))
        ).reshape(-1, 1)
        mixed = self._wsola_tail * (1.0 - fade_in) + segment[:hop] * fade_in
        self._wsola_output_buffer = np.concatenate((output_buffer, mixed), axis=0)
        self._wsola_tail = segment[hop:frame_size].copy()
        self._wsola_next_source_index += hop * speed
        return True

    def _readWsola(self, start_idx: int, frames: int, speed: float) -> np.ndarray:
        n = self._sampleCount()
        if n == 0 or start_idx >= n:
            return np.zeros((0, self.channels), dtype=np.float32)

        if abs(speed - 1.0) < 1e-6:
            self._resetWsola()
            return self._readSamples(start_idx, start_idx + frames).copy()

        if frames <= 0:
            return np.zeros((0, self.channels), dtype=np.float32)

        start_idx = max(0, start_idx)
        if self._wsolaNeedsReset(start_idx, speed):
            self._wsolaResetFor(start_idx, speed)

        hop = self._wsolaHopSize()
        if hop <= 0:
            return self._readSamples(start_idx, start_idx + frames).copy()

        search = self._wsolaSearchSize(hop)
        while (
            self._wsola_output_buffer is not None
            and len(self._wsola_output_buffer) < frames
        ):
            if not self._wsolaAppendFrame(speed, hop, search):
                break

        buffer = self._wsola_output_buffer
        if buffer is None or len(buffer) == 0:
            return np.zeros((0, self.channels), dtype=np.float32)

        out = buffer[:frames].copy()
        if len(buffer) > frames:
            self._wsola_output_buffer = buffer[frames:].copy()
        else:
            self._wsola_output_buffer = np.zeros((0, self.channels), dtype=np.float32)
        self._wsola_buffer_start_index += len(out) * speed

        return out.astype(np.float32, copy=False)

    def _sourceFramesFor(self, start_idx: int, frames: int, speed: float) -> int:
        n = self._sampleCount()
        if n == 0 or start_idx >= n:
            return 0
        src_frames = max(1, int(round(frames * speed)))
        return min(src_frames, n - start_idx)

    def _speedSourceFrames(self, start_idx: int, frames: int) -> int:
        return self._sourceFramesFor(start_idx, frames, self.play_speed)

    def _resampleToFrames(self, chunk: np.ndarray, frames: int) -> np.ndarray:
        if len(chunk) == frames:
            return chunk.astype(np.float32, copy=False)
        if len(chunk) == 0:
            return np.zeros((0, self.channels), dtype=np.float32)
        if len(chunk) == 1:
            return np.repeat(chunk, frames, axis=0).astype(np.float32, copy=False)

        factor = gcd(len(chunk), frames)
        up = frames // factor
        down = len(chunk) // factor
        out = resample_poly(chunk, up, down, axis=0, padtype='line').astype(
            np.float32, copy=False
        )
        if len(out) > frames:
            return out[:frames]
        if len(out) < frames:
            pad = np.repeat(out[-1:], frames - len(out), axis=0)
            out = np.concatenate((out, pad), axis=0)
        return out

    def _readSpeed(self, start_idx: int, frames: int) -> tuple[np.ndarray, int]:
        n = self._sampleCount()
        speed = self.play_speed
        if n == 0:
            return np.zeros((0, self.channels), dtype=np.float32), 0

        pitch_ratio = self._pitchRatio()
        if abs(speed - 1.0) < 1e-6 and abs(pitch_ratio - 1.0) < 1e-6:
            self._resetWsola()
            src_frames = self._speedSourceFrames(start_idx, frames)
            return self._readSamples(start_idx, start_idx + frames).copy(), src_frames

        if self._speed_animating_flag:
            return self._readSpeedResample(start_idx, frames, speed)

        intermediate_frames = max(1, int(round(frames * pitch_ratio)))
        tempo_speed = speed / pitch_ratio
        chunk = self._readWsola(start_idx, intermediate_frames, tempo_speed)
        if abs(pitch_ratio - 1.0) >= 1e-6:
            chunk = self._resampleToFrames(chunk, frames)

        src_frames = self._sourceFramesFor(start_idx, intermediate_frames, tempo_speed)
        return chunk, src_frames

    def _readSpeedResample(
        self, start_idx: int, frames: int, speed: float
    ) -> tuple[np.ndarray, int]:
        n = self._sampleCount()
        src_frames = max(1, int(round(frames * speed)))
        end_idx = min(start_idx + src_frames, n)
        src_region = self._readSamples(start_idx, end_idx)
        if len(src_region) < 2:
            return (
                np.zeros((frames, self.channels), dtype=np.float32),
                src_frames,
            )
        src_x = np.linspace(0.0, 1.0, len(src_region), dtype=np.float64)
        dst_x = np.linspace(0.0, 1.0, frames, dtype=np.float64)
        ch = min(self.channels, src_region.shape[1])
        result = np.zeros((frames, ch), dtype=np.float32)
        for c in range(ch):
            result[:, c] = np.interp(dst_x, src_x, src_region[:, c]).astype(
                np.float32, copy=False
            )
        return result, src_frames

    def _audio_callback(self, outdata, frames, _time_info, _status):
        with self._lock:
            self._renderAudio(outdata, frames, _time_info, _status)

    def _renderAudio(self, outdata, frames, _time_info, _status):
        generation = self._analysis_generation
        self._analysis_sequence += 1
        sequence = self._analysis_sequence
        sample_rate = self.sample_rate
        outdata[:] = 0
        if _status.output_underflow:
            self._output_underflows += 1
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
            if self.stream:
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None
            self._queueCallbackEvent('full_finished')
            raise sd.CallbackStop

        chunk, src_frames, block_gain = item
        copy_len = min(len(chunk), frames)
        gain = self.volume_gain * (
            self.loudness_gain if block_gain is None else block_gain
        )
        played_chunk = chunk[:copy_len, : self.output_channels] * gain
        np.clip(
            played_chunk,
            -1.0,
            (61.0 + cfg.target_lufs) * 3.0,
            out=played_chunk,
        )
        outdata[:copy_len, : self.output_channels] = played_chunk

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

        if not finished:
            monitor_chunk = (
                played_chunk.mean(axis=1) if played_chunk.ndim == 2 else played_chunk
            )
            rms = np.sqrt(np.mean(monitor_chunk**2))
            if rms > 0:
                self.db = 20 * np.log10(rms)
            else:
                self.db = -100

            if not growing_file_incomplete and self._timeline is None:
                remain = self.getLength() - self._playback_time
                if (
                    (remain < cfg.skip_remain_time)
                    if cfg.skip_remain_time < 60
                    else True
                ) and cfg.skip_nosound:
                    if self.db < cfg.skip_threshold:
                        skip_nosound = True

            if self.fft_enabled or cfg.beat_detection_enabled:
                try:
                    self.fft_queue.put_nowait(
                        (
                            generation,
                            sequence,
                            sample_rate,
                            monitor_chunk,
                            chunk[:copy_len, : self.output_channels],
                        )
                    )
                except Full:
                    pass

        if finished:
            self.is_playing = False
            self.is_paused = False
            if self.stream:
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None
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
            if event_name == 'full_finished':
                self._pending_full_finished = True
            elif event_name == 'ending_no_sound':
                self._pending_ending_no_sound = True

    def _emitPlaybackTelemetry(self) -> None:
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

    def _clearQueue(self) -> None:
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except Empty:
                break
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
        if self._sampleCount() == 0:
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
        progress = self.current_index / self._sampleCount()

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

                    start_idx = int(self._producer_index)
                    chunk, src_frames = self._readSpeed(start_idx, self._BLOCK_SIZE)
                    if len(chunk) == 0:
                        waiting_for_growing_file = (
                            self._growing_file_path is not None
                            and not self._growing_file_complete
                        )
                        break

                    if self.channels == 1:
                        out = self._applyStereoEffect(chunk[:, 0])
                    else:
                        if not cfg.stereo:
                            mono = chunk.mean(axis=1, keepdims=True)
                            out = np.repeat(mono, 2, axis=1)
                        else:
                            out = chunk[:, :2].copy()

                    out = out.astype(np.float32, copy=False)
                    out = self._applyReverb(out)

                    next_index = min(start_idx + src_frames, self._sampleCount())

                try:
                    with self._lock:
                        if producer_seq != self._producer_seq:
                            break
                        block_gain = 1.0 if self._timeline is not None else None
                        self._audio_queue.put((out, src_frames, block_gain), timeout=0)
                        self._producer_index = next_index
                except Full:
                    time.sleep(0.02)
                    break
                else:
                    with self._lock:
                        if (
                            not self._producer_running
                            or producer_seq != self._producer_seq
                        ):
                            break
                        self._producer_index = next_index
                        batch_end_index = max(batch_end_index, self._producer_index)
                        self._prepared_end_index = max(
                            self._prepared_end_index,
                            self._producer_index,
                        )
                        produced_blocks += 1

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

        if not finished or producer_seq != self._producer_seq:
            return
        try:
            self._audio_queue.put(None, timeout=0)
        except Full:
            pass

    def setOutputDevice(self, device: DevicesInfo):
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
                    pass
                self.stream = None

            self._device_id = device.index

            if was_playing:
                self._startProducer()
                self._startStream()

    def getCurrentOutputDevice(self) -> Optional[DevicesInfo]:
        devices = getAudioDevices()
        for dev in devices:
            if dev.index == self._device_id:
                return dev
        return devices[0] if devices else None
