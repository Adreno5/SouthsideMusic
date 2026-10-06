from __future__ import annotations

import array
import io
import json
import logging
import struct
import subprocess
import threading
import time
import wave
from collections import namedtuple
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast, override
from weakref import WeakValueDictionary

from pydub import AudioSegment
from pydub.exceptions import CouldntDecodeError
from pydub.utils import audioop, db_to_float, fsdecode, get_prober_name

from core.pcm_buffer import PcmBuffer, PcmFile

_AUDIO_DECODE_CACHE: WeakValueDictionary[str, AudioSegment] = WeakValueDictionary()
_AUDIO_CACHE_LOCK = threading.Lock()
_logger = logging.getLogger(__name__)


def cacheDecodedAudio(key: str, segment: AudioSegment) -> None:
    with _AUDIO_CACHE_LOCK:
        _AUDIO_DECODE_CACHE[key] = segment


def getCachedAudio(key: str) -> AudioSegment | None:
    with _AUDIO_CACHE_LOCK:
        return _AUDIO_DECODE_CACHE.get(key)


WavSubChunk = namedtuple('WavSubChunk', ['id', 'position', 'size'])


def extractWavHeaders(data: bytes | bytearray) -> list[WavSubChunk]:

    pos = 12
    subchunks: list[WavSubChunk] = []
    while pos + 8 <= len(data) and len(subchunks) < 10:
        subchunk_id = data[pos : pos + 4]
        subchunk_size = struct.unpack_from('<I', data[pos + 4 : pos + 8])[0]
        subchunks.append(WavSubChunk(subchunk_id, pos, subchunk_size))
        if subchunk_id == b'data':
            break
        pos += subchunk_size + 8

    return subchunks


def fixWavHeaders(data: bytearray) -> None:
    headers = extractWavHeaders(data)
    if not headers or headers[-1].id != b'data':
        return

    if len(data) > 2**32:
        raise CouldntDecodeError('Unable to process >4GB files')

    data[4:8] = struct.pack('<I', len(data) - 8)

    pos = headers[-1].position
    data[pos + 4 : pos + 8] = struct.pack('<I', len(data) - pos - 8)


class PatchedAudioSegment(AudioSegment):
    _logger = logging.getLogger(__name__)

    @override
    @classmethod
    def from_file(
        cls,
        file: bytes | str | Path | io.BytesIO,
        *,
        timeout: float = 90.0,
    ) -> PatchedAudioSegment:
        deadline = time.monotonic() + timeout
        filename: str | None
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
            conversion_command += ['-i', 'pipe:0']

        info = None
        if filename or stdin_data is not None:
            probe_command = [
                get_prober_name(),
                '-of',
                'json',
                '-v',
                'info',
                '-show_format',
                '-show_streams',
                filename or 'pipe:0',
            ]
            probe = subprocess.run(
                probe_command,
                input=stdin_data,
                capture_output=True,
                check=False,
                timeout=max(0.0, deadline - time.monotonic()),
            )
            if probe.returncode == 0 and probe.stdout:
                info = json.loads(probe.stdout.decode('utf-8', 'ignore'))

        if info:
            audio_streams = [x for x in info['streams'] if x['codec_type'] == 'audio']

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
                    bits_per_sample = {
                        'u8': 8,
                        's16': 16,
                        's32': 32,
                        's64': 64,
                        'flt': 32,
                        'dbl': 64,
                    }.get(audio_streams[0].get('sample_fmt', '').rstrip('p'), 0)
            if bits_per_sample <= 0:
                acodec = None
            elif bits_per_sample == 8:
                acodec = 'pcm_u8'
            else:
                acodec = f'pcm_s{bits_per_sample}le'

            if acodec is not None:
                conversion_command += ['-acodec', acodec]

        conversion_command += [
            '-vn',
            '-f',
            'wav',
        ]

        conversion_command += ['-']

        decoded_file = PcmFile()
        result = subprocess.run(
            conversion_command,
            input=stdin_data,
            stdout=decoded_file.file,
            stderr=subprocess.PIPE,
            check=False,
            timeout=max(0.0, deadline - time.monotonic()),
        )

        cls._logger.debug(conversion_command)

        if result.returncode != 0 or decoded_file.file.seek(0, 2) == 0:
            raise CouldntDecodeError(
                'Decoding failed. ffmpeg returned error code: {}\n\nOutput from ffmpeg/avlib:\n\n{}'.format(
                    result.returncode, result.stderr.decode(errors='ignore')
                )
            )

        decoded_file.file.seek(0)
        storage = PcmFile()
        with wave.open(decoded_file.file, 'rb') as reader:
            channels = reader.getnchannels()
            sample_width = reader.getsampwidth()
            frame_rate = reader.getframerate()
            frames = 0
            while raw := reader.readframes(65536):
                if sample_width == 1:
                    raw = audioop.bias(raw, 1, -128)
                elif sample_width == 3:
                    raw = cls(
                        data=raw,
                        sample_width=3,
                        frame_rate=frame_rate,
                        channels=channels,
                    ).raw_data
                storage.append(raw)
                frames += len(raw) // (
                    channels * (4 if sample_width == 3 else sample_width)
                )
        return FileAudioSegment(
            storage,
            frames,
            4 if sample_width == 3 else sample_width,
            frame_rate,
            channels,
        )

    @override
    def set_channels(self, channels: int) -> PatchedAudioSegment:
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


class FileAudioSegment(PatchedAudioSegment):
    def __init__(
        self,
        storage: PcmFile,
        frames: int,
        sample_width: int,
        frame_rate: int,
        channels: int,
    ) -> None:
        self.storage = storage
        self.frames = frames
        self.prepared_pcm: PcmBuffer | None = None
        super().__init__(
            data=b'',
            sample_width=sample_width,
            frame_rate=frame_rate,
            channels=channels,
        )

    @property
    def _data(self) -> bytes:
        return self.readPcm(0, self.frames)

    @_data.setter
    def _data(self, value: bytes) -> None:
        if value:
            raise ValueError('File PCM cannot be replaced with an in-memory buffer')

    @override
    def frame_count(self, ms: float | None = None) -> float:
        return ms * self.frame_rate / 1000 if ms is not None else float(self.frames)

    def readPcm(self, start: int, stop: int) -> bytes:
        start = max(0, min(start, self.frames))
        stop = max(start, min(stop, self.frames))
        return self.storage.read(
            start * self.frame_width, (stop - start) * self.frame_width
        )

    @override
    def apply_gain(self, volume_change: float) -> FileAudioSegment:
        storage = PcmFile()
        gain = db_to_float(volume_change)
        for start in range(0, self.frames, 65536):
            storage.append(
                audioop.mul(self.readPcm(start, start + 65536), self.sample_width, gain)
            )
        return FileAudioSegment(
            storage, self.frames, self.sample_width, self.frame_rate, self.channels
        )

    @override
    def __getitem__(
        self, millisecond: int | slice
    ) -> PatchedAudioSegment | Generator[PatchedAudioSegment]:
        if isinstance(millisecond, slice):
            if millisecond.step:
                return (
                    cast(PatchedAudioSegment, self[start : start + millisecond.step])
                    for start in range(*millisecond.indices(len(self)))
                )
            start_ms = millisecond.start if millisecond.start is not None else 0
            stop_ms = millisecond.stop if millisecond.stop is not None else len(self)
        else:
            start_ms, stop_ms = millisecond, millisecond + 1
        start = self._parse_position(min(start_ms, len(self)))
        stop = self._parse_position(min(stop_ms, len(self)))
        raw = self.readPcm(start, stop)
        missing = (stop - start) * self.frame_width - len(raw)
        if missing > 0:
            raw += b'\0' * missing
        return PatchedAudioSegment(
            data=raw,
            sample_width=self.sample_width,
            frame_rate=self.frame_rate,
            channels=self.channels,
        )

    @override
    def _spawn(
        self, data: Any, overrides: dict[str, Any] | None = None
    ) -> PatchedAudioSegment:
        return PatchedAudioSegment(
            data=data,
            metadata={
                'sample_width': self.sample_width,
                'frame_rate': self.frame_rate,
                'frame_width': self.frame_width,
                'channels': self.channels,
                **(overrides or {}),
            },
        )


def decodeAudioWithSidecar(
    file: bytes | str | Path | io.BytesIO,
    sidecar: Any | None = None,
    *,
    timeout: float = 90.0,
) -> PatchedAudioSegment:
    return PatchedAudioSegment.from_file(file, timeout=timeout)
