import numpy as np
import sounddevice as sd
from pydub import AudioSegment
from pathlib import Path
from PySide6.QtCore import QObject, Signal
from typing import Optional
import threading
from scipy.fft import rfft, rfftfreq

class AudioPlayer(QObject):
    onFinished = Signal()
    positionChanged = Signal(float)
    fftDataReady = Signal(np.ndarray, np.ndarray)  # (freqs, magnitudes)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.samples: np.ndarray = np.array([], dtype=np.float32)
        self.sample_rate: int = 88200
        self.channels: int = 1

        self.current_index: int = 0
        self.is_playing: bool = False
        self.is_paused: bool = False
        self.stream: Optional[sd.OutputStream] = None
        self.volume_gain: float = 1.0

        self.fft_enabled = True
        self.fft_size = 1024

        self._lock = threading.RLock()

    def load(self, audio: AudioSegment) -> None:
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

    def load_from_file(self, file_path: Path) -> None:
        audio = AudioSegment.from_file(str(file_path))
        self.load(audio)

    def load_from_bytes(self, data: bytes) -> None:
        audio = AudioSegment.from_file(data, format='mp3')
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

    def set_position(self, seconds: float) -> None:
        with self._lock:
            if len(self.samples) == 0:
                return
            target_index = int(seconds * self.sample_rate)
            target_index = max(0, min(target_index, len(self.samples) - 1))
            self.current_index = target_index

    def get_position(self) -> float:
        return self.current_index / self.sample_rate if self.sample_rate > 0 else 0.0

    def get_length(self) -> float:
        return len(self.samples) / self.sample_rate if self.sample_rate > 0 else 0.0

    def set_volume(self, volume: float) -> None:
        self.volume_gain = max(0.0, min(1.0, volume))

    def get_busy(self) -> bool:
        return self.is_playing or (self.stream is not None and self.stream.active)

    def _start_stream(self):
        if self.stream is None:
            self.stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                callback=self._audio_callback,
                blocksize=2048,
                dtype='float32'
            )
        self.stream.start()

    def set_gain_factor(self, gain: float):
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
                np.clip(out, -1.0, 1.0, out=out)
                outdata[:copy_len] = out.reshape(-1, 1)
            if copy_len < frames:
                outdata[copy_len:] = 0

            self.current_index += copy_len

            if self.current_index >= len(self.samples):
                self.is_playing = False
                self.is_paused = False
                self.onFinished.emit()
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