from __future__ import annotations

import array
import ctypes
from dataclasses import dataclass
import io
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
    Signal,
    event_bus,
    Property,
)
from services.events.events import COLLECT_DEBUG_INFO, EMIT_DEBUG_INFO
from typing import Optional, override
import threading
from scipy.fft import rfft, rfftfreq
from scipy.signal import resample_poly
from imports import QMessageBox
from core.config import cfg

from pydub.utils import fsdecode, audioop, mediainfo_json
from pydub.exceptions import CouldntDecodeError
from pydub import AudioSegment
from collections import namedtuple, OrderedDict

_AUDIO_DECODE_CACHE: OrderedDict[str, AudioSegment] = OrderedDict()
_AUDIO_CACHE_LOCK = threading.Lock()
_AUDIO_CACHE_MAX = 10
_REVERB_DELAY_MS = (29, 43, 61, 79)
_REVERB_TAP_GAINS = (0.42, 0.31, 0.22, 0.15)
_PRODUCER_QUEUE_BLOCKS = 32768
_PRODUCER_PROGRESS_BOOST_RATIO = 0.2
_PRODUCER_EARLY_LEAD = 5.0
_PRODUCER_EARLY_STRESSED_LEAD = 3.0
_PRODUCER_EARLY_IDLE_LEAD = 8.0
_PRODUCER_LATE_LEAD = 90.0
_PRODUCER_LATE_STRESSED_LEAD = 25.0
_PRODUCER_LATE_IDLE_LEAD = 120.0


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


def _get_memory_load() -> float:
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


def _get_cpu_load() -> float:
    try:
        return max(0.0, min(100.0, float(psutil.cpu_percent(interval=None))))
    except Exception:
        return 0.0


def cache_decoded_audio(key: str, segment: AudioSegment) -> None:
    with _AUDIO_CACHE_LOCK:
        _AUDIO_DECODE_CACHE[key] = segment
        _AUDIO_DECODE_CACHE.move_to_end(key)
        while len(_AUDIO_DECODE_CACHE) > _AUDIO_CACHE_MAX:
            _AUDIO_DECODE_CACHE.popitem(last=False)


def get_cached_audio(key: str) -> Optional[AudioSegment]:
    with _AUDIO_CACHE_LOCK:
        seg = _AUDIO_DECODE_CACHE.get(key)
        if seg is not None:
            _AUDIO_DECODE_CACHE.move_to_end(key)
        return seg


WavSubChunk = namedtuple('WavSubChunk', ['id', 'position', 'size'])


def extract_wav_headers(data):
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


def fix_wav_headers(data):
    headers = extract_wav_headers(data)
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
    print(devices)
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
        file: io.BytesIO,
    ):
        orig_file = file
        try:
            filename = fsdecode(file)
        except TypeError:
            filename = None

        conversion_command = [
            cls.converter,
            '-y',
        ]

        if filename:
            conversion_command += ['-i', filename]
            stdin_parameter = None
            stdin_data = None
        else:
            conversion_command += [
                '-read_ahead_limit',
                '-1',
                '-i',
                'cache:pipe:0',
            ]
            stdin_parameter = subprocess.PIPE
            stdin_data = file.read()

        info = mediainfo_json(orig_file, read_ahead_limit=-1)
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
                bits_per_sample = audio_streams[0]['bits_per_sample']
            if bits_per_sample == 8:
                acodec = 'pcm_u8'
            else:
                acodec = 'pcm_s%dle' % bits_per_sample

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
        fix_wav_headers(p_out)
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


class AudioPlayer(QObject):
    onFullFinished = Signal()
    onEndingNoSound = Signal()
    positionChanged = Signal(float)
    fftDataReady = Signal(np.ndarray, np.ndarray)  # (freqs, magnitudes)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)

        self.samples: np.ndarray = np.zeros((0, 1), dtype=np.float32)
        self.sample_rate: int = 88200
        self.channels: int = 1
        self.output_channels: int = 1

        self.db: float = 0

        self.current_index: int = 0
        self._playback_time: float = 0.0
        self.is_playing: bool = False
        self.is_paused: bool = False
        self.stream: Optional[sd.OutputStream] = None
        self.volume_gain: float = 1.0
        self.loudness_gain: float = 1.0
        self._gain_anim: Optional[QPropertyAnimation] = None

        self.fft_enabled = True
        self.fft_size = 1024

        self.play_speed = cfg.play_speed
        self.play_pitch = cfg.play_pitch

        self._BLOCK_SIZE = 2048

        self._audio_queue: Queue[tuple[np.ndarray, int] | None] = Queue(
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

        self._lock = threading.RLock()
        devices = getAudioDevices()
        if len(devices) == 0:
            self._logger.error('no devices found')
            QMessageBox.critical(
                None,
                'Error ',
                'No any device can be used to play audio on your computer!',
                QMessageBox.StandardButton.Ok,
            )
            sys.exit(1)
        self._device_id: int = devices[0].index
        self.fft_queue = Queue(maxsize=8)
        self.fft_thread_running = True
        self.fft_thread = threading.Thread(target=self._fft_worker, daemon=True)
        self.fft_thread.start()

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
                f'producer_cpu_load={self._producer_cpu_load:.1f}',
                f'producer_memory_load={self._producer_memory_load:.1f}',
                f'producer_target_lead={self._producer_target_lead:.2f}',
                f'prepared_lead={self._producer_prepared_lead():.2f}',
                f'growing_file={self._growing_file_path is not None}',
                f'growing_file_complete={self._growing_file_complete}',
            ],
        )

    def _prepare_samples(self, audio: PatchedAudioSegment) -> np.ndarray:
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

    def _reset_stereo_effect(self) -> None:
        self._stereo_tail = None

    def _apply_stereo_effect(self, mono_chunk: np.ndarray) -> np.ndarray:
        stereo_chunk = np.repeat(mono_chunk.reshape(-1, 1), 2, axis=1)
        if not cfg.stereo or cfg.stereo_haas_index == 0 or len(mono_chunk) == 0:
            self._reset_stereo_effect()
            return stereo_chunk

        delay_ms = cfg.stereo_haas_index
        delay = min(
            max(1, int(self.sample_rate * delay_ms / 1000)),
            max(1, len(self.samples) // 8),
        )
        mono = mono_chunk.astype(np.float32, copy=False)
        tail = self._stereo_tail
        if tail is None:
            tail = np.zeros(delay, dtype=np.float32)
        elif len(tail) < delay:
            tail = np.concatenate(
                (np.zeros(delay - len(tail), dtype=np.float32), tail)
            )
        else:
            tail = tail[-delay:]

        history = np.concatenate((tail, mono))
        stereo_chunk[:, 1] = history[: len(mono)] * 0.82
        self._stereo_tail = history[-delay:].copy()

        return stereo_chunk

    def _reset_reverb(self) -> None:
        self._reverb_tail = None

    def _apply_reverb(self, chunk: np.ndarray) -> np.ndarray:
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
        out = dry * (1.0 - mix * 0.25) + wet * (mix * 0.55)
        self._reverb_tail = history[-max_delay:].copy()
        return out.astype(np.float32, copy=False)

    def _remap_channels(self, target_channels: int) -> None:
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

    def _reset_growing_file(self) -> None:
        self._growing_file_path = None
        self._growing_file_complete = True
        self._growing_file_size = 0
        self._growing_file_last_decode = 0.0

    def _decode_file(self, file_path: Path) -> PatchedAudioSegment:
        with open(str(file_path), 'rb') as f:
            return PatchedAudioSegment.from_file(io.BytesIO(f.read()))

    def _apply_audio(self, audio: PatchedAudioSegment) -> None:
        self.sample_rate = audio.frame_rate
        self.samples = self._prepare_samples(audio)
        self.channels = self.samples.shape[1] if self.samples.ndim == 2 else 1
        self.output_channels = 2

        self.current_index = 0
        self._producer_index = 0
        self._prepared_start_index = 0
        self._prepared_end_index = 0
        self._producer_target_lead = _PRODUCER_EARLY_LEAD
        self._reset_wsola()
        self._reset_stereo_effect()
        self._reset_reverb()
        self._playback_time = 0.0
        self.is_playing = False
        self.is_paused = False

    def load(self, audio: PatchedAudioSegment) -> None:
        with self._lock:
            self._stop_producer()
            self.stop()
            if self.stream:
                self.stream.close()
                self.stream = None

            self._apply_audio(audio)
            self._reset_growing_file()

    def loadFromFile(self, file_path: Path) -> None:
        audio = self._decode_file(file_path)
        self.load(audio)

    def loadFromBytes(self, data: bytes) -> None:
        audio = PatchedAudioSegment.from_file(io.BytesIO(data))
        self.load(audio)

    def loadGrowingFile(
        self,
        file_path: Path,
        complete: bool = False,
    ) -> PatchedAudioSegment:
        audio = self._decode_file(file_path)
        file_size = file_path.stat().st_size
        with self._lock:
            self._stop_producer()
            self.stop(clear_growing_file=False)
            if self.stream:
                self.stream.close()
                self.stream = None

            self._apply_audio(audio)
            self._growing_file_path = file_path
            self._growing_file_complete = complete
            self._growing_file_size = file_size
            self._growing_file_last_decode = time.perf_counter()
        return audio

    def refreshGrowingFile(self, force: bool = False) -> bool:
        with self._lock:
            file_path = self._growing_file_path
            last_decode = self._growing_file_last_decode
            old_size = self._growing_file_size
            old_len = len(self.samples)

        if file_path is None:
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
            audio = self._decode_file(file_path)
            samples = self._prepare_samples(audio)
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
            samples = self._prepare_samples(audio)

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
                self._reset_growing_file()
                return True

        refreshed = self.refreshGrowingFile(force=True)
        with self._lock:
            if self._growing_file_path == file_path:
                self._reset_growing_file()
        return refreshed

    def play(self) -> None:
        with self._lock:
            if len(self.samples) == 0:
                return
            if self.is_paused:
                self._reset_wsola()
                self._reset_stereo_effect()
                self._reset_reverb()
                self._clear_queue()
                self._start_producer()
                self._start_stream()
                self.is_playing = True
                self.is_paused = False
            else:
                self.stop(clear_growing_file=self._growing_file_path is None)
                self.current_index = 0
                self._producer_index = 0
                self._prepared_start_index = 0
                self._prepared_end_index = 0
                self._reset_wsola()
                self._reset_stereo_effect()
                self._reset_reverb()
                self._clear_queue()
                self._start_producer()
                self._start_stream()
                self.is_playing = True
                self.is_paused = False

    def pause(self) -> None:
        with self._lock:
            self._stop_producer()
            self._clear_queue()
            if self.stream and self.stream.active:
                self.stream.stop()
            self.is_playing = False
            self.is_paused = True

    def resume(self) -> None:
        self.play()

    def stop(self, clear_growing_file: bool = True) -> None:
        with self._lock:
            self.stopGainAnimation()
            self._stop_producer()
            if self.stream and self.stream.active:
                self.stream.stop()
            self.current_index = 0
            self._producer_index = 0
            self._prepared_start_index = 0
            self._prepared_end_index = 0
            self._producer_target_lead = _PRODUCER_EARLY_LEAD
            self._reset_wsola()
            self._reset_stereo_effect()
            self._reset_reverb()
            self._playback_time = 0.0
            self.is_playing = False
            self.is_paused = False
            if clear_growing_file:
                self._reset_growing_file()

    def setPosition(self, seconds: float) -> None:
        with self._lock:
            if len(self.samples) == 0:
                return
            self._stop_producer()
            self._playback_time = max(0.0, seconds)
            self.current_index = int(self._playback_time * self.sample_rate)
            self._producer_index = self.current_index
            self._prepared_start_index = self.current_index
            self._prepared_end_index = self.current_index
            self._producer_target_lead = self._producer_desired_lead()
            self._reset_wsola()
            self._reset_stereo_effect()
            self._reset_reverb()
            self._clear_queue()
            if self.is_playing:
                self._start_producer()

    def getPosition(self) -> float:
        return round(self._playback_time, 2)

    def getLength(self) -> float:
        return len(self.samples) / self.sample_rate if self.sample_rate > 0 else 0.0

    def getLoadedTime(self) -> float:
        return self.getLength()

    def getPreparedTimeSection(self) -> tuple[float, float]:
        if self.sample_rate <= 0:
            return 0.0, 0.0
        with self._lock:
            start = min(self._prepared_start_index, self._prepared_end_index)
            end = max(self._prepared_start_index, self._prepared_end_index)
            return start / self.sample_rate, end / self.sample_rate

    def setVolume(self, volume: float) -> None:
        self.volume_gain = max(0.0, min(1.0, volume))

    def setPlaySpeed(self, speed: float) -> None:
        with self._lock:
            speed = max(0.1, speed)
            if abs(speed - self.play_speed) < 1e-6:
                return
            was_playing = self.is_playing
            if was_playing:
                self._stop_producer()
            self.play_speed = speed
            self._reset_wsola()
            self._reset_stereo_effect()
            self._reset_reverb()
            if was_playing:
                self._clear_queue()
                self._start_producer()

    def setPlayPitch(self, pitch: float) -> None:
        with self._lock:
            pitch = max(-12.0, min(12.0, pitch))
            if abs(pitch - self.play_pitch) < 1e-6:
                return
            was_playing = self.is_playing
            if was_playing:
                self._stop_producer()
            self.play_pitch = pitch
            self._reset_wsola()
            self._reset_stereo_effect()
            self._reset_reverb()
            if was_playing:
                self._clear_queue()
                self._start_producer()

    def restartProducer(self) -> None:
        with self._lock:
            if len(self.samples) == 0:
                return
            was_playing = self.is_playing or (
                self.stream is not None and self.stream.active
            )
            if was_playing:
                self._stop_producer()
            self._reset_wsola()
            self._reset_stereo_effect()
            self._reset_reverb()
            self._clear_queue()
            self._producer_target_lead = self._producer_desired_lead()
            if was_playing:
                self._start_producer()

    def isPlaying(self) -> bool:
        if self.is_playing:
            return True
        if self.stream is not None:
            try:
                return self.stream.active
            except Exception:
                pass
        return False

    def _stream_sample_rate(self) -> int:
        return int(self.sample_rate * self.play_speed)

    def _start_stream(self):
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

    def stopGainAnimation(self) -> None:
        if self._gain_anim is not None:
            self._gain_anim.stop()
            self._gain_anim = None

    @Property(float)
    def _loudnessGain(self) -> float:
        return self.loudness_gain

    @_loudnessGain.setter
    def loudnessGain(self, value: float) -> None:
        self.loudness_gain = value

    def _fft_worker(self):
        while self.fft_thread_running:
            chunk = self.fft_queue.get()
            if chunk is None:
                break

            if len(chunk) < self.fft_size:
                padded = np.zeros(self.fft_size, dtype=np.float32)
                padded[: len(chunk)] = chunk
                chunk = padded
            else:
                chunk = chunk[: self.fft_size]

            window = np.hanning(len(chunk))
            windowed = chunk * window
            fft_result = rfft(windowed)
            fft_vals = np.abs(np.asarray(fft_result, dtype=np.complex128))
            fft_freqs = rfftfreq(len(chunk), 1 / self.sample_rate)

            self.fftDataReady.emit(fft_freqs, fft_vals)

    def stop_fft_thread(self):
        self.fft_thread_running = False
        self.fft_queue.put(None)
        if self.fft_thread.is_alive():
            self.fft_thread.join(timeout=0)

    def _reset_wsola(self) -> None:
        self._wsola_output_buffer = None
        self._wsola_tail = None
        self._wsola_buffer_start_index = 0.0
        self._wsola_next_source_index = 0.0
        self._wsola_speed = 1.0

    def _pitch_ratio(self) -> float:
        return 2 ** (self.play_pitch / 12.0)

    def _wsola_hop_size(self) -> int:
        return max(256, self.sample_rate // 43)

    def _wsola_search_size(self, hop: int) -> int:
        return min(hop // 2, max(32, self.sample_rate // 125))

    def _wsola_read_source(self, start_idx: int, frames: int) -> np.ndarray:
        n = len(self.samples)
        if n == 0 or frames <= 0:
            return np.zeros((0, self.channels), dtype=np.float32)

        start_idx = max(0, min(start_idx, n))
        end_idx = min(start_idx + frames, n)
        segment = self.samples[start_idx:end_idx].copy()
        if len(segment) >= frames:
            return segment.astype(np.float32, copy=False)

        if len(segment) > 0:
            pad_frame = segment[-1:]
        else:
            pad_frame = self.samples[-1:]
        padding = np.repeat(pad_frame, frames - len(segment), axis=0)
        return np.concatenate((segment, padding), axis=0).astype(
            np.float32, copy=False
        )

    def _wsola_find_start(
        self, ideal_start: int, overlap: int, search: int
    ) -> int:
        tail = self._wsola_tail
        n = len(self.samples)
        if tail is None or len(tail) < overlap or n <= overlap:
            return max(0, min(ideal_start, n))

        min_start = max(0, ideal_start - search)
        max_start = min(n - overlap, ideal_start + search)
        if max_start < min_start:
            return max(0, min(ideal_start, n))

        tail_mono = tail[:overlap].mean(axis=1).astype(np.float32, copy=False)
        tail_mono = tail_mono - tail_mono.mean()
        tail_power = float(np.sqrt(np.sum(tail_mono * tail_mono)))
        if tail_power < 1e-6:
            return max(0, min(ideal_start, n))

        mono = self.samples[min_start : max_start + overlap].mean(axis=1)
        windows = np.lib.stride_tricks.sliding_window_view(mono, overlap)
        centered = windows - windows.mean(axis=1, keepdims=True)
        powers = np.sqrt(np.sum(centered * centered, axis=1))
        scores = centered @ tail_mono
        scores /= np.maximum(powers * tail_power, 1e-6)
        positions = np.arange(len(scores), dtype=np.float32) + min_start
        center_bias = np.abs(positions - ideal_start) / max(1, search)
        scores -= center_bias * 0.03
        return min_start + int(np.argmax(scores))

    def _wsola_reset_for(self, start_idx: int, speed: float) -> None:
        self._wsola_output_buffer = np.zeros((0, self.channels), dtype=np.float32)
        self._wsola_tail = None
        self._wsola_buffer_start_index = float(start_idx)
        self._wsola_next_source_index = float(start_idx)
        self._wsola_speed = speed

    def _wsola_needs_reset(self, start_idx: int, speed: float) -> bool:
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

    def _wsola_append_frame(self, speed: float, hop: int, search: int) -> bool:
        output_buffer = self._wsola_output_buffer
        if output_buffer is None:
            return False

        frame_size = hop * 2
        if self._wsola_tail is None:
            source_start = int(round(self._wsola_next_source_index))
            segment = self._wsola_read_source(source_start, frame_size)
            if len(segment) == 0:
                return False

            self._wsola_output_buffer = np.concatenate(
                (output_buffer, segment[:hop]), axis=0
            )
            self._wsola_tail = segment[hop:frame_size].copy()
            self._wsola_next_source_index += hop * speed
            return True

        if self._wsola_next_source_index >= len(self.samples):
            self._wsola_output_buffer = np.concatenate(
                (output_buffer, self._wsola_tail), axis=0
            )
            self._wsola_tail = None
            self._wsola_next_source_index = float(len(self.samples))
            return True

        ideal_start = int(round(self._wsola_next_source_index))
        source_start = self._wsola_find_start(ideal_start, hop, search)
        segment = self._wsola_read_source(source_start, frame_size)
        if len(segment) == 0:
            return False

        fade_in = np.linspace(0.0, 1.0, hop, dtype=np.float32).reshape(-1, 1)
        mixed = self._wsola_tail * (1.0 - fade_in) + segment[:hop] * fade_in
        self._wsola_output_buffer = np.concatenate((output_buffer, mixed), axis=0)
        self._wsola_tail = segment[hop:frame_size].copy()
        self._wsola_next_source_index += hop * speed
        return True

    def _read_wsola(self, start_idx: int, frames: int, speed: float) -> np.ndarray:
        n = len(self.samples)
        if n == 0 or start_idx >= n:
            return np.zeros((0, self.channels), dtype=np.float32)

        if abs(speed - 1.0) < 1e-6:
            self._reset_wsola()
            return self.samples[start_idx : start_idx + frames].copy()

        if frames <= 0:
            return np.zeros((0, self.channels), dtype=np.float32)

        start_idx = max(0, start_idx)
        if self._wsola_needs_reset(start_idx, speed):
            self._wsola_reset_for(start_idx, speed)

        hop = self._wsola_hop_size()
        if hop <= 0:
            return self.samples[start_idx : start_idx + frames].copy()

        search = self._wsola_search_size(hop)
        while (
            self._wsola_output_buffer is not None
            and len(self._wsola_output_buffer) < frames
        ):
            if not self._wsola_append_frame(speed, hop, search):
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

    def _source_frames_for(self, start_idx: int, frames: int, speed: float) -> int:
        n = len(self.samples)
        if n == 0 or start_idx >= n:
            return 0
        src_frames = max(1, int(round(frames * speed)))
        return min(src_frames, n - start_idx)

    def _speed_source_frames(self, start_idx: int, frames: int) -> int:
        return self._source_frames_for(start_idx, frames, self.play_speed)

    def _resample_to_frames(self, chunk: np.ndarray, frames: int) -> np.ndarray:
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

    def _read_speed(self, start_idx: int, frames: int) -> tuple[np.ndarray, int]:
        n = len(self.samples)
        speed = self.play_speed
        if n == 0:
            return np.zeros((0, self.channels), dtype=np.float32), 0

        pitch_ratio = self._pitch_ratio()
        if abs(speed - 1.0) < 1e-6 and abs(pitch_ratio - 1.0) < 1e-6:
            self._reset_wsola()
            src_frames = self._speed_source_frames(start_idx, frames)
            return self.samples[start_idx : start_idx + frames].copy(), src_frames

        intermediate_frames = max(1, int(round(frames * pitch_ratio)))
        tempo_speed = speed / pitch_ratio
        chunk = self._read_wsola(start_idx, intermediate_frames, tempo_speed)
        if abs(pitch_ratio - 1.0) >= 1e-6:
            chunk = self._resample_to_frames(chunk, frames)

        src_frames = self._source_frames_for(start_idx, intermediate_frames, tempo_speed)
        return chunk, src_frames

    def _audio_callback(self, outdata, frames, _time_info, _status):
        outdata[:] = 0
        try:
            item = self._audio_queue.get_nowait()
        except Empty:
            return

        if item is None:
            self.is_playing = False
            self.is_paused = False
            if self.stream:
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None
            self.onFullFinished.emit()
            raise sd.CallbackStop

        chunk, src_frames = item
        copy_len = min(len(chunk), frames)
        gain = self.volume_gain * self.loudness_gain
        played_chunk = chunk[:copy_len, : self.output_channels] * gain
        np.clip(
            played_chunk,
            -1.0,
            (61.0 + cfg.target_lufs) * 3.0,
            out=played_chunk,
        )
        outdata[:copy_len, : self.output_channels] = played_chunk

        self.current_index = min(self.current_index + src_frames, len(self.samples))
        self._playback_time = self.current_index / self.sample_rate

        waiting_for_file = (
            self._growing_file_path is not None
            and not self._growing_file_complete
            and self.current_index >= len(self.samples)
        )
        finished = self.current_index >= len(self.samples) and not waiting_for_file
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

            remain = self.getLength() - self._playback_time
            if (
                (remain < cfg.skip_remain_time) if cfg.skip_remain_time < 60 else True
            ) and cfg.skip_nosound:
                if self.db < cfg.skip_threshold:
                    skip_nosound = True

            if self.fft_enabled:
                try:
                    self.fft_queue.put_nowait(monitor_chunk)
                except Full:
                    pass

        self.positionChanged.emit(self._playback_time)
        event_bus.emit(DB_CHANGED, self.db)

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
            self.onFullFinished.emit()
            raise sd.CallbackStop

        if skip_nosound:
            self.onEndingNoSound.emit()
            self.is_playing = False
            self.is_paused = False
            self._logger.info(f'skip {self.db=}')
            raise sd.CallbackStop

    def _clear_queue(self) -> None:
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except Empty:
                break
        self._producer_index = self.current_index
        self._prepared_start_index = self.current_index
        self._prepared_end_index = self.current_index

    def _start_producer(self) -> None:
        self._producer_running = True
        self._producer_seq += 1
        producer_seq = self._producer_seq
        self._producer_last_resource_sample = time.perf_counter()
        _get_cpu_load()
        self._producer_thread = threading.Thread(
            target=lambda: self._producer_loop(producer_seq), daemon=True
        )
        self._producer_thread.start()

    def _stop_producer(self) -> None:
        self._producer_running = False
        self._producer_seq += 1
        if self._producer_thread is not None and self._producer_thread.is_alive():
            self._producer_thread.join(timeout=0)
        self._producer_thread = None

    def _producer_prepared_lead(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return max(
            0.0, (self._prepared_end_index - self.current_index) / self.sample_rate
        )

    def _producer_desired_lead(self) -> float:
        if len(self.samples) == 0:
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
        progress = self.current_index / len(self.samples)

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

    def _sample_producer_resources(self) -> None:
        now = time.perf_counter()
        elapsed = now - self._producer_last_resource_sample
        if elapsed < 0.75:
            return

        cpu_load = _get_cpu_load()
        if self._producer_cpu_load == 0.0:
            self._producer_cpu_load = cpu_load
        else:
            self._producer_cpu_load += (cpu_load - self._producer_cpu_load) * 0.35
        self._producer_memory_load = _get_memory_load()
        self._producer_last_resource_sample = now

    def _waiting_for_growing_file(self) -> bool:
        with self._lock:
            return (
                self._growing_file_path is not None
                and not self._growing_file_complete
                and self._producer_index >= len(self.samples)
            )

    def _producer_loop(self, producer_seq: int) -> None:
        finished = False
        while self._producer_running and producer_seq == self._producer_seq:
            self._sample_producer_resources()

            target_lead = self._producer_desired_lead()
            self._producer_target_lead += (
                target_lead - self._producer_target_lead
            ) * 0.2

            lead = self._producer_prepared_lead()
            if lead >= max(0.8, self._producer_target_lead * 0.45):
                time.sleep(0.02)
                continue

            with self._lock:
                batch_start_index = max(
                    self._prepared_start_index,
                    min(self._producer_index, self._prepared_end_index),
                )
                batch_end_index = self._prepared_end_index

            waiting_for_growing_file = False
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
                        or len(self.samples) == 0
                    ):
                        break
                    if self._producer_index >= len(self.samples):
                        waiting_for_growing_file = (
                            self._growing_file_path is not None
                            and not self._growing_file_complete
                        )
                        if waiting_for_growing_file:
                            break
                        finished = True
                        break

                    start_idx = int(self._producer_index)
                    chunk, src_frames = self._read_speed(start_idx, self._BLOCK_SIZE)
                    if len(chunk) == 0:
                        waiting_for_growing_file = (
                            self._growing_file_path is not None
                            and not self._growing_file_complete
                        )
                        break

                    if self.channels == 1:
                        out = self._apply_stereo_effect(chunk[:, 0])
                    else:
                        if not cfg.stereo:
                            mono = chunk.mean(axis=1, keepdims=True)
                            out = np.repeat(mono, 2, axis=1)
                        else:
                            out = chunk[:, :2].copy()

                    out = out.astype(np.float32, copy=False)
                    out = self._apply_reverb(out)

                    next_index = min(start_idx + src_frames, len(self.samples))

                try:
                    self._audio_queue.put((out, src_frames), timeout=0)
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

                if finished:
                    break

                time.sleep(0.001)

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

            if waiting_for_growing_file or self._waiting_for_growing_file():
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
            self._stop_producer()
            if self.stream:
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None

            self._device_id = device.index

            if was_playing:
                self._start_producer()
                self._start_stream()

    def getCurrentOutputDevice(self) -> Optional[DevicesInfo]:
        devices = getAudioDevices()
        for dev in devices:
            if dev.index == self._device_id:
                return dev
        return devices[0] if devices else None
