import array
import logging
import math
import struct
import subprocess
from colorama import Fore, Style
import numpy as np
import sounddevice as sd
from pathlib import Path
from PySide6.QtCore import QObject, Signal
from typing import Optional, override
import threading
from scipy.fft import rfft, rfftfreq
from utils.config_util import cfg

from pydub.utils import mediainfo_json, fsdecode, _fd_or_path_or_tempfile, audioop
from pydub.logging_utils import log_conversion
from pydub.exceptions import CouldntDecodeError
from pydub import AudioSegment
from collections import namedtuple

WavSubChunk = namedtuple('WavSubChunk', ['id', 'position', 'size'])
def extract_wav_headers(data):
    # def search_subchunk(data, subchunk_id):
    pos = 12  # The size of the RIFF chunk descriptor
    subchunks = []
    while pos + 8 <= len(data) and len(subchunks) < 10:
        subchunk_id = data[pos:pos + 4]
        subchunk_size = struct.unpack_from('<I', data[pos + 4:pos + 8])[0]
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
        raise CouldntDecodeError("Unable to process >4GB files")

    # Set the file size in the RIFF chunk descriptor
    data[4:8] = struct.pack('<I', len(data) - 8)

    # Set the data size in the data subchunk
    pos = headers[-1].position
    data[pos + 4:pos + 8] = struct.pack('<I', len(data) - pos - 8)

class PatchedAudioSegment(AudioSegment):
    def __init__(self, data=None, *args, **kwargs):
        super().__init__(data, *args, **kwargs)
        self.converter = r'ffmpeg\bin\ffmpeg.exe'
        self.ffmpeg = r'ffmpeg\bin\ffmpeg.exe'

    @override
    @classmethod
    def from_file(cls, file, format=None, codec=None, parameters=None, start_second=None, duration=None, **kwargs):
        logging.debug(f'{Fore.LIGHTGREEN_EX}[{file}]/[PatchedAudioSegment]{Style.RESET_ALL} patching')
        orig_file = file
        try:
            filename = fsdecode(file)
        except TypeError:
            filename = None
        file, close_file = _fd_or_path_or_tempfile(file, 'rb', tempfile=False)

        if format:
            format = format.lower()
            format = {
                'm4a': 'mp4',
                'wave': 'wav',
            }.get(format, format)

        def is_format(f):
            f = f.lower()
            if format == f:
                return True

            if filename:
                return filename.lower().endswith('.{0}'.format(f))

            return False

        if is_format('wav'):
            try:
                if start_second is None and duration is None:
                    return cls._from_safe_wav(file)
                elif start_second is not None and duration is None:
                    return cls._from_safe_wav(file)[start_second*1000:]
                elif start_second is None and duration is not None:
                    return cls._from_safe_wav(file)[:duration*1000]
                else:
                    return cls._from_safe_wav(file)[start_second*1000:(start_second+duration)*1000] # type: ignore
            except:
                file.seek(0) # type: ignore
        elif is_format('raw') or is_format('pcm'):
            sample_width = kwargs['sample_width']
            frame_rate = kwargs['frame_rate']
            channels = kwargs['channels']
            metadata = {
                'sample_width': sample_width,
                'frame_rate': frame_rate,
                'channels': channels,
                'frame_width': channels * sample_width
            }
            if start_second is None and duration is None:
                return cls(data=file.read(), metadata=metadata) # type: ignore
            elif start_second is not None and duration is None:
                return cls(data=file.read(), metadata=metadata)[start_second*1000:] # type: ignore
            elif start_second is None and duration is not None:
                return cls(data=file.read(), metadata=metadata)[:duration*1000] # type: ignore
            else:
                return cls(data=file.read(), metadata=metadata)[start_second*1000:(start_second+duration)*1000] # type: ignore

        conversion_command = [r'ffmpeg\bin\ffmpeg.exe',
                              '-y',  # always overwrite existing files
                              ]

        # If format is not defined
        # ffmpeg/avconv will detect it automatically
        if format:
            conversion_command += ['-f', format]

        if codec:
            # force audio decoder
            conversion_command += ['-acodec', codec]

        read_ahead_limit = kwargs.get('read_ahead_limit', -1)
        if filename:
            conversion_command += ['-i', filename]
            stdin_parameter = None
            stdin_data = None
        else:
            conversion_command += ['-read_ahead_limit', str(read_ahead_limit),
                                    '-i', 'cache:pipe:0']
            stdin_parameter = subprocess.PIPE
            stdin_data = file.read() # type: ignore

        if codec:
            info = None
        else:
            info = mediainfo_json(orig_file, read_ahead_limit=read_ahead_limit)
        if info:
            audio_streams = [x for x in info['streams']
                             if x['codec_type'] == 'audio']
            # This is a workaround for some ffprobe versions that always say
            # that mp3/mp4/aac/webm/ogg files contain fltp samples
            audio_codec = audio_streams[0].get('codec_name')
            if (audio_streams[0].get('sample_fmt') == 'fltp' and
                    audio_codec in ['mp3', 'mp4', 'aac', 'webm', 'ogg']):
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
            '-f', 'wav'  # output options (filename last)
        ]

        if start_second is not None:
            conversion_command += ['-ss', str(start_second)]

        if duration is not None:
            conversion_command += ['-t', str(duration)]

        conversion_command += ['-']

        if parameters is not None:
            # extend arguments with arbitrary set
            conversion_command.extend(parameters)

        log_conversion(conversion_command)

        p = subprocess.Popen(conversion_command, stdin=stdin_parameter,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        p_out, p_err = p.communicate(input=stdin_data)

        if p.returncode != 0 or len(p_out) == 0:
            if close_file:
                file.close() # type: ignore
            raise CouldntDecodeError(
                'Decoding failed. ffmpeg returned error code: {0}\n\nOutput from ffmpeg/avlib:\n\n{1}'.format(
                    p.returncode, p_err.decode(errors='ignore') ))

        p_out = bytearray(p_out)
        fix_wav_headers(p_out)
        p_out = bytes(p_out)
        obj = cls(p_out)

        if close_file:
            file.close() # type: ignore

        if start_second is None and duration is None:
            return obj
        elif start_second is not None and duration is None:
            return obj[0:]
        elif start_second is None and duration is not None:
            return obj[:duration * 1000]
        else:
            return obj[0:duration * 1000] # type: ignore
    
    @override
    def set_channels(self, channels):
        if channels == self.channels:
            return self

        if channels == 2 and self.channels == 1:
            fn = audioop.tostereo
            frame_width = self.frame_width * 2
            fac = 1
            converted = fn(self._data, self.sample_width, fac, fac) # type: ignore
        elif channels == 1 and self.channels == 2:
            fn = audioop.tomono
            frame_width = self.frame_width // 2
            fac = 0.5
            converted = fn(self._data, self.sample_width, fac, fac) # type: ignore
        elif channels == 1:
            channels_data = [seg.get_array_of_samples() for seg in self.split_to_mono()]
            frame_count = int(self.frame_count())
            converted = array.array(
                channels_data[0].typecode,
                b'\0' * (frame_count * self.sample_width)
            )
            for raw_channel_data in channels_data:
                for i in range(frame_count):
                    converted[i] += raw_channel_data[i] // self.channels
            frame_width = self.frame_width // self.channels
        elif self.channels == 1:
            dup_channels = [self for iChannel in range(channels)]
            return PatchedAudioSegment.from_mono_audiosegments(*dup_channels)
        else:
            raise ValueError(
                "AudioSegment.set_channels only supports mono-to-multi channel and multi-to-mono channel conversion")

        return self._spawn(data=converted,
                           overrides={
                               'channels': channels,
                               'frame_width': frame_width})

class AudioPlayer(QObject):
    onFullFinished = Signal()
    onEndingNoSound = Signal()
    positionChanged = Signal(float)
    fftDataReady = Signal(np.ndarray, np.ndarray)  # (freqs, magnitudes)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.samples: np.ndarray = np.array([], dtype=np.float32)
        self.sample_rate: int = 88200
        self.channels: int = 1

        self.db: float = 0

        self.current_index: int = 0
        self.is_playing: bool = False
        self.is_paused: bool = False
        self.stream: Optional[sd.OutputStream] = None
        self.volume_gain: float = 1.0

        self.fft_enabled = True
        self.fft_size = 1024

        self.play_speed = 1.0

        self._lock = threading.RLock()

    def load(self, audio: PatchedAudioSegment) -> None:
        with self._lock:
            self.stop()
            if self.stream:
                self.stream.close()
                self.stream = None

            audio = audio.set_channels(1)
            self.sample_rate = audio.frame_rate

            samples_raw = np.array(audio.get_array_of_samples(), dtype=np.float32) # type: ignore
            max_val = np.iinfo(audio.array_type).max if audio.sample_width != 4 else 2**31
            self.samples = samples_raw / max_val

            self.current_index = 0
            self.is_playing = False
            self.is_paused = False

    def loadFromFile(self, file_path: Path) -> None:
        audio = PatchedAudioSegment.from_file(str(file_path))
        self.load(audio)

    def loadFromBytes(self, data: bytes) -> None:
        audio = PatchedAudioSegment.from_file(data, format='mp3')
        self.load(audio)

    def play(self) -> None:
        with self._lock:
            if len(self.samples) == 0:
                return
            if self.is_paused:
                self._start_stream()
                self.is_playing = True
                self.is_paused = False
            else:
                self.stop()
                self.current_index = 0
                self._start_stream()
                self.is_playing = True
                self.is_paused = False

    def pause(self) -> None:
        with self._lock:
            if self.stream and self.stream.active:
                self.stream.stop()
            self.is_playing = False
            self.is_paused = True

    def resume(self) -> None:
        self.play()

    def stop(self) -> None:
        with self._lock:
            if self.stream and self.stream.active:
                self.stream.stop()
            self.current_index = 0
            self.is_playing = False
            self.is_paused = False

    def setPosition(self, seconds: float) -> None:
        with self._lock:
            if len(self.samples) == 0:
                return
            target_index = int(seconds * self.sample_rate)
            target_index = max(0, min(target_index, len(self.samples) - 1))
            self.current_index = target_index

    def getPosition(self) -> float:
        return self.current_index / self.sample_rate if self.sample_rate > 0 else 0.0

    def getLength(self) -> float:
        return len(self.samples) / self.sample_rate if self.sample_rate > 0 else 0.0

    def setVolume(self, volume: float) -> None:
        self.volume_gain = max(0.0, min(1.0, volume))

    def isPlaying(self) -> bool:
        return self.is_playing or (self.stream is not None and self.stream.active)

    def _start_stream(self):
        if self.stream is None:
            self.stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                callback=self._audio_callback,
                blocksize=736,
                dtype='float32'
            )
        self.stream.start()

    def setGain(self, gain: float):
        with self._lock:
            self.volume_gain = max(0.0, gain)

    def _audio_callback(self, outdata, frames, time, status):
        with self._lock:
            start = self.current_index
            end = start + frames
            chunk = self.samples[start:end]
            copy_len = len(chunk)

            if copy_len > 0:
                out = chunk * self.volume_gain
                np.clip(out, -1.0, (61.0 + cfg.target_lufs) * 3.0, out=out)
                outdata[:copy_len] = out.reshape(-1, 1)
            if copy_len < frames:
                outdata[copy_len:] = 0

            self.current_index += math.ceil(copy_len * self.play_speed)

            if self.current_index >= len(self.samples):
                self.is_playing = False
                self.is_paused = False
                self.onFullFinished.emit()
                raise sd.CallbackStop
            
            rms = np.sqrt(np.mean(chunk**2))
            if rms > 0:
                self.db = 20 * np.log10(rms)
            else:
                self.db = -float('inf')
            
            remain = (len(self.samples) - self.current_index) / self.sample_rate
            if ((remain < cfg.skip_remain_time) if cfg.skip_remain_time < 60 else True) and copy_len > 0 and cfg.skip_nosound:
                if self.db < cfg.skip_threshold:
                    self.onEndingNoSound.emit()
                    self.is_playing = False
                    self.is_paused = False
                    logging.info(f'skip {self.db=}')
                    raise sd.CallbackStop
            
            if self.fft_enabled:
                chunk_raw = self.samples[start:end]
                if len(chunk_raw) < self.fft_size:
                    chunk_pad = np.zeros(self.fft_size, dtype=np.float32)
                    chunk_pad[:len(chunk_raw)] = chunk_raw
                else:
                    chunk_pad = chunk_raw[:self.fft_size]

                window = np.hanning(len(chunk_pad))
                chunk_windowed = chunk_pad * window
                
                fft_vals = np.abs(rfft(chunk_windowed)) # type: ignore
                fft_freqs = rfftfreq(len(chunk_pad), 1/self.sample_rate)
                
                self.fftDataReady.emit(fft_freqs, fft_vals)