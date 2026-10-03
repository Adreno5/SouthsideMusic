from __future__ import annotations

import array
import io
import json
import logging
import struct
import subprocess
import threading
from collections import OrderedDict, namedtuple
from pathlib import Path
from typing import Any, override

from pydub import AudioSegment
from pydub.exceptions import CouldntDecodeError
from pydub.utils import audioop, fsdecode, get_prober_name, mediainfo_json

_AUDIO_DECODE_CACHE: OrderedDict[str, AudioSegment] = OrderedDict()
_AUDIO_CACHE_LOCK = threading.Lock()
_AUDIO_CACHE_MAX = 10
_logger = logging.getLogger(__name__)


def cacheDecodedAudio(key: str, segment: AudioSegment) -> None:
    with _AUDIO_CACHE_LOCK:
        _AUDIO_DECODE_CACHE[key] = segment
        _AUDIO_DECODE_CACHE.move_to_end(key)
        while len(_AUDIO_DECODE_CACHE) > _AUDIO_CACHE_MAX:
            _AUDIO_DECODE_CACHE.popitem(last=False)


def getCachedAudio(key: str) -> AudioSegment | None:
    with _AUDIO_CACHE_LOCK:
        seg = _AUDIO_DECODE_CACHE.get(key)
        if seg is not None:
            _AUDIO_DECODE_CACHE.move_to_end(key)
        return seg


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
    ) -> PatchedAudioSegment:
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
                acodec = f'pcm_s{bits_per_sample}le'

            if acodec is not None:
                conversion_command += ['-acodec', acodec]

        conversion_command += [
            '-vn',
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
                'Decoding failed. ffmpeg returned error code: {}\n\nOutput from ffmpeg/avlib:\n\n{}'.format(
                    p.returncode, p_err.decode(errors='ignore')
                )
            )

        wav_data = bytearray(p_out)
        fixWavHeaders(wav_data)
        obj = cls(bytes(wav_data))

        return obj

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
    except Exception:
        _logger.debug('sidecar audio decode failed', exc_info=True)
        decoded = None

    if isinstance(decoded, bytes):
        return PatchedAudioSegment(decoded)
    return PatchedAudioSegment.from_file(file)
