import array
import io
import logging
import struct
import subprocess
from queue import Empty, Full, Queue
import sys
import numpy as np
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
from typing import Optional, TypedDict, override
import threading
from scipy.fft import rfft, rfftfreq
from imports import QMessageBox
from core.config import cfg

from pydub.utils import fsdecode, audioop, mediainfo_json
from pydub.exceptions import CouldntDecodeError
from pydub import AudioSegment
from collections import namedtuple, OrderedDict

_AUDIO_DECODE_CACHE: OrderedDict[str, AudioSegment] = OrderedDict()
_AUDIO_CACHE_LOCK = threading.Lock()
_AUDIO_CACHE_MAX = 10


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


class DevicesInfo(TypedDict):
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

        self._BLOCK_SIZE = 768

        self._audio_queue: Queue[np.ndarray | None] = Queue(maxsize=32)
        self._producer_running = False
        self._producer_thread: Optional[threading.Thread] = None
        self._producer_index: int = 0

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
        self._device_id: int = devices[0]['index']
        self.fft_queue = Queue(maxsize=8)
        self.fft_thread_running = True
        self.fft_thread = threading.Thread(target=self._fft_worker, daemon=True)
        self.fft_thread.start()

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

    def _apply_stereo_effect(
        self, mono_chunk: np.ndarray, absolute_start: int
    ) -> np.ndarray:
        stereo_chunk = np.repeat(mono_chunk.reshape(-1, 1), 2, axis=1)
        if not cfg.stereo or len(mono_chunk) == 0:
            return stereo_chunk

        delay = min(max(1, self.sample_rate // 200), max(1, len(self.samples) // 8))
        delayed_indices = np.arange(len(mono_chunk)) + absolute_start - delay
        valid = (delayed_indices >= 0) & (delayed_indices < len(self.samples))

        right = stereo_chunk[:, 1]
        if np.any(valid):
            right[valid] = self.samples[delayed_indices[valid], 0]
        right[~valid] = 0.0
        right *= 0.82

        return stereo_chunk

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

    def load(self, audio: PatchedAudioSegment) -> None:
        with self._lock:
            self._stop_producer()
            self.stop()
            if self.stream:
                self.stream.close()
                self.stream = None

            self.sample_rate = audio.frame_rate
            self.samples = self._prepare_samples(audio)
            self.channels = self.samples.shape[1] if self.samples.ndim == 2 else 1
            self.output_channels = 2

            self.current_index = 0
            self._producer_index = 0
            self._playback_time = 0.0
            self.is_playing = False
            self.is_paused = False

    def loadFromFile(self, file_path: Path) -> None:
        with open(str(file_path), 'rb') as f:
            audio = PatchedAudioSegment.from_file(io.BytesIO(f.read()))
        self.load(audio)

    def loadFromBytes(self, data: bytes) -> None:
        audio = PatchedAudioSegment.from_file(io.BytesIO(data))
        self.load(audio)

    def play(self) -> None:
        with self._lock:
            if len(self.samples) == 0:
                return
            if self.is_paused:
                self._clear_queue()
                self._start_producer()
                self._start_stream()
                self.is_playing = True
                self.is_paused = False
            else:
                self.stop()
                self.current_index = 0
                self._producer_index = 0
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

    def stop(self) -> None:
        with self._lock:
            self.stopGainAnimation()
            self._stop_producer()
            if self.stream and self.stream.active:
                self.stream.stop()
            self.current_index = 0
            self._producer_index = 0
            self._playback_time = 0.0
            self.is_playing = False
            self.is_paused = False

    def setPosition(self, seconds: float) -> None:
        with self._lock:
            if len(self.samples) == 0:
                return
            self._stop_producer()
            self._playback_time = max(0.0, seconds)
            self.current_index = int(self._playback_time * self.sample_rate)
            self._producer_index = self.current_index
            self._clear_queue()
            if self.is_playing:
                self._start_producer()

    def getPosition(self) -> float:
        return round(self._playback_time, 2)

    def getLength(self) -> float:
        return len(self.samples) / self.sample_rate if self.sample_rate > 0 else 0.0

    def setVolume(self, volume: float) -> None:
        self.volume_gain = max(0.0, min(1.0, volume))

    def setPlaySpeed(self, speed: float) -> None:
        with self._lock:
            self.play_speed = max(0.1, speed)

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
    def loudnessGain(self) -> float:
        return self.loudness_gain

    @loudnessGain.setter
    def setLoudnessGain(self, value: float) -> None:
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
            self.fft_thread.join(timeout=1.0)

    def _read_speed(self, start_idx: int, frames: int) -> np.ndarray:
        n = len(self.samples)
        speed = self.play_speed
        if n == 0:
            return np.zeros((0, self.channels), dtype=np.float32)
        if abs(speed - 1.0) < 1e-6:
            return self.samples[start_idx : start_idx + frames].copy()

        src_frames = int(round(frames * speed))
        src_end = min(start_idx + src_frames, n)
        src_len = src_end - start_idx
        if src_len <= 0:
            return np.zeros((0, self.channels), dtype=np.float32)

        src = self.samples[start_idx:src_end]
        src_indices = np.linspace(0, src_len - 1, frames, dtype=np.float32)
        low = src_indices.astype(np.intp)
        high = np.minimum(low + 1, src_len - 1)
        frac = (src_indices - low).astype(np.float32)
        frac = frac.reshape(-1, 1)

        out = src[low] * (1 - frac) + src[high] * frac
        return out

    def _audio_callback(self, outdata, frames, _time_info, _status):
        outdata[:] = 0
        try:
            chunk = self._audio_queue.get_nowait()
        except Empty:
            return

        if chunk is None:
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

        copy_len = min(len(chunk), frames)
        outdata[:copy_len, : self.output_channels] = chunk[
            :copy_len, : self.output_channels
        ]

        src_frames = int(round(frames * self.play_speed))
        self.current_index = min(self.current_index + src_frames, len(self.samples))
        self._playback_time = self.current_index / self.sample_rate

        finished = self.current_index >= len(self.samples)
        skip_nosound = False

        if not finished:
            monitor_chunk = (
                chunk[:copy_len].mean(axis=1) if chunk.ndim == 2 else chunk[:copy_len]
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

    def _start_producer(self) -> None:
        self._producer_running = True
        self._producer_thread = threading.Thread(
            target=self._producer_loop, daemon=True
        )
        self._producer_thread.start()

    def _stop_producer(self) -> None:
        self._producer_running = False
        if self._producer_thread is not None and self._producer_thread.is_alive():
            self._producer_thread.join(timeout=0.1)
        self._producer_thread = None

    def _producer_loop(self) -> None:
        while self._producer_running:
            with self._lock:
                if (
                    not self._producer_running
                    or len(self.samples) == 0
                    or self._producer_index >= len(self.samples)
                ):
                    break

                chunk = self._read_speed(int(self._producer_index), self._BLOCK_SIZE)
                if len(chunk) == 0:
                    break

                if self.channels == 1:
                    out = self._apply_stereo_effect(
                        chunk[:, 0], int(self._producer_index)
                    )
                else:
                    if not cfg.stereo:
                        mono = chunk.mean(axis=1, keepdims=True)
                        out = np.repeat(mono, 2, axis=1)
                    else:
                        out = chunk[:, :2].copy()

                out = out.astype(np.float32, copy=False)
                out *= self.volume_gain * self.loudness_gain
                np.clip(out, -1.0, (61.0 + cfg.target_lufs) * 3.0, out=out)

                src_frames = int(round(self._BLOCK_SIZE * self.play_speed))
                self._producer_index = min(
                    self._producer_index + src_frames, len(self.samples)
                )

            try:
                self._audio_queue.put(out, timeout=0.1)
            except Full:
                pass

        try:
            self._audio_queue.put(None, timeout=0.1)
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

            self._device_id = device['index']

            if was_playing:
                self._start_producer()
                self._start_stream()

    def getCurrentOutputDevice(self) -> Optional[DevicesInfo]:
        devices = getAudioDevices()
        for dev in devices:
            if dev['index'] == self._device_id:
                return dev
        return devices[0] if devices else None
