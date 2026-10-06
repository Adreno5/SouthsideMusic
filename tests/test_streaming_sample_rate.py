from __future__ import annotations

import io
import logging
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import Mock, patch

import numpy as np
import requests
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core.models import TrackAudioInfo  # noqa: E402
from core.netease_backend import NeteaseCloudMusicBackend  # noqa: E402
from core.playing_manager import AudioSegment_, PlayingManager  # noqa: E402


def checkBackendMetadata() -> None:
    backend = NeteaseCloudMusicBackend()
    for sr in (44100, 96000, 192000, None):
        with patch(
            'core.netease_backend.apis.track.getTrackAudio',
            return_value={'data': [{'url': 'audio', 'sr': sr}]},
        ):
            result = backend.getTrackAudio(1)
            assert result.url == 'audio'
            assert result.sample_rate == (sr or 0)


def checkStreaming(
    source_rate: int,
    device_rate: int,
    metadata_rate: int | None = None,
    duration: float = 0.2,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / 'source.flac'
        frequency = source_rate // 3 if source_rate > 48000 else 1000
        subprocess.run(
            [
                AudioSegment_.converter,
                '-v',
                'error',
                '-f',
                'lavfi',
                '-i',
                f'sine=frequency={frequency}:sample_rate={source_rate}:duration={duration}',
                '-ac',
                '2',
                str(source),
            ],
            check=True,
            timeout=10,
        )
        encoded = source.read_bytes()
        response = requests.Response()
        response.status_code = 200
        response.headers['content-length'] = str(len(encoded))
        response.raw = io.BytesIO(encoded)
        tasks: Queue[tuple[Callable[..., Any], tuple[Any, ...]]] = Queue()
        chunks: list[np.ndarray] = []
        song = SimpleNamespace(id=1, content_cache_hash='', cacheAudio=Mock())
        player = SimpleNamespace(
            _device_id=0,
            sample_rate=0,
            is_paused=False,
            play=Mock(),
            finishGrowingStream=Mock(),
        )

        def load(path: Path, rate: int, channels: int) -> None:
            player.sample_rate = rate
            assert channels == 2

        def append(path: Path, data: bytes, channels: int) -> float:
            chunks.append(np.frombuffer(data, dtype='<f4').reshape(-1, channels))
            return length()

        def length() -> float:
            return sum(len(chunk) for chunk in chunks) / player.sample_rate

        player.loadGrowingStream = load
        player.appendGrowingStreamPcm = append
        player.getLength = length
        next_selection = SimpleNamespace(song=song)
        analysis = Mock(return_value=None)
        manager = SimpleNamespace(
            _player=player,
            _play_seq=1,
            _request_br=3200000,
            ctx=SimpleNamespace(config=SimpleNamespace(target_request_br=3200000)),
            current_song=song,
            current_song_audio=None,
            _mwindow_obj=None,
            _logger=logging.getLogger(__name__),
            next_song_audio=AudioSegment_.silent(duration=200, frame_rate=source_rate),
            next_song_selection=next_selection,
            isSelectionCurrent=lambda selection: True,
            _computeCrossfadeInfo=analysis,
            _queuePreloadedSong=Mock(),
            _compute_gain_async=Mock(),
            _schedule=lambda fn, *args: tasks.put((fn, args)),
            _registerStreamProcess=Mock(),
            _unregisterStreamProcess=Mock(),
            _loadStorableAudio=lambda storable: AudioSegment_.from_file(str(source)),
            _storableDuration=lambda storable, duration: duration,
            _applyStoredLoudnessGain=Mock(),
            _loadPlaybackImage=Mock(),
            _finishPlaybackLoad=Mock(),
            _emitError=Mock(),
        )

        def checkDevice(**settings: Any) -> None:
            if settings['samplerate'] != device_rate:
                raise sd.PortAudioError('Unsupported sample rate')

        def runTask(
            fn: Callable[..., Any], args: tuple, window: Any, done: Callable[[], None]
        ) -> None:
            fn(*args)
            done()

        audio = TrackAudioInfo(
            url=str(source),
            sample_rate=source_rate if metadata_rate is None else metadata_rate,
        )
        with (
            patch('core.playing_manager.MUSIC_DATA_DIR', directory),
            patch(
                'core.playing_manager.getBackend',
                return_value=SimpleNamespace(
                    getTrackAudio=lambda *a, **k: audio,
                    getSongQualityPrivilege=lambda *a: SimpleNamespace(max_br=3200000),
                ),
            ),
            patch('core.playing_manager.asyncTask', side_effect=runTask),
            patch('core.playing_manager.requests.get', return_value=response),
            patch('core.playing_manager.saveFavorites'),
            patch('core.playing_manager.event_bus.emit'),
            patch(
                'core.playing_manager.sd.query_devices',
                return_value={
                    'max_output_channels': 2,
                    'default_samplerate': device_rate,
                },
            ),
            patch(
                'core.playing_manager.sd.check_output_settings', side_effect=checkDevice
            ),
        ):
            PlayingManager._playDownloadingStorable(manager, song, False, 1, False)
            deadline = time.monotonic() + 15
            while manager.current_song_audio is None:
                assert time.monotonic() < deadline, 'Streaming did not finish'
                fn, args = tasks.get(timeout=10)
                fn(*args)
                assert not manager._emitError.called

        samples = np.concatenate(chunks)
        assert player.sample_rate == device_rate
        assert abs(len(samples) - round(device_rate * duration)) <= 1
        assert abs(length() - duration) < 1 / device_rate
        assert analysis.call_args.args[0].frame_rate == device_rate
        assert len(manager._stream_analysis_tail) == min(len(samples), device_rate * 30)
        assert song.cacheAudio.call_args.args[0] == encoded
        assert player.play.called and player.finishGrowingStream.called
        if source_rate == device_rate and source_rate > 48000:
            spectrum = abs(np.fft.rfft(samples[:, 0]))
            peak = np.argmax(spectrum) * device_rate / len(samples)
            assert abs(peak - frequency) < 10
            assert peak > 22050
        print(f'streaming {source_rate} -> {device_rate} Hz passed')


def main() -> None:
    checkBackendMetadata()
    for rate in (44100, 48000, 96000, 192000):
        checkStreaming(rate, rate)
    checkStreaming(96000, 48000)
    checkStreaming(192000, 192000, metadata_rate=0)
    checkStreaming(96000, 96000, duration=31.2)


if __name__ == '__main__':
    main()
