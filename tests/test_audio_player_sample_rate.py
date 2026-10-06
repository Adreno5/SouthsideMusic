from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.audio_player import (  # noqa: E402
    AudioPlayer,
    DevicesInfo,
    PatchedAudioSegment,
    PreparedAudioBuffer,
)


class OutputStream:
    def __init__(self, **settings: Any) -> None:
        self.samplerate = settings['samplerate']
        self.active = False
        self.closed = False

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False

    def abort(self) -> None:
        self.active = False

    def close(self) -> None:
        self.closed = True


def audio(rate: int, seconds: int = 5) -> PatchedAudioSegment:
    return PatchedAudioSegment.silent(duration=seconds * 1000, frame_rate=rate)


def checkLoads(player: AudioPlayer, directory: Path) -> None:
    for rate in (44100, 48000, 96000, 192000):
        previous = player.stream
        player._audio_queue.put(None)
        player.load(audio(rate))
        assert player.sample_rate == rate
        assert player.stream is not None and player.stream.samplerate == rate
        assert not player.stream.active
        assert player._audio_queue.empty()
        assert player.getLength() == 5
        if previous is not None:
            assert previous.closed
        player.loadPrepared(AudioPlayer.prepareBuffer(audio(rate)))
        assert player.stream is not None and player.stream.samplerate == rate
        assert player.sample_rate == rate
        player._audio_queue.put(None)
        player.loadGrowingStream(directory / 'stream', rate, 2)
        assert player.stream is not None and player.stream.samplerate == rate
        assert player._audio_queue.empty()
        pcm = np.zeros((rate, 2), dtype=np.float32)
        player.appendGrowingStreamPcm(directory / 'stream', pcm.tobytes(), 2)
        assert player.getLength() == 1


def checkGrowingReload(player: AudioPlayer, directory: Path) -> None:
    path = directory / 'growing.wav'
    for paused in (False, True):
        audio(44100).export(path, format='wav').close()
        player.loadGrowingFile(path)
        assert player.stream is not None and player.stream.samplerate == 44100
        player.setPosition(1.25)
        player.is_playing = not paused
        player.is_paused = paused
        if not paused:
            player.stream.start()
        previous = player.stream
        player._audio_queue.put(None)
        audio(96000).export(path, format='wav').close()
        assert player.refreshGrowingFile(force=True)
        assert previous.closed
        assert player.sample_rate == 96000
        assert player.stream is not None and player.stream.samplerate == 96000
        assert player.stream.active == (not paused)
        assert player.is_paused == paused
        assert player.current_index == 120000
        assert player._getExactPosition() == 1.25
        assert player._producer_index == 120000
        assert player._audio_queue.empty()
        previous = player.stream
        assert player.finishGrowingFile(path, audio(192000))
        assert previous.closed
        assert player.sample_rate == 192000
        assert player.stream is not None and player.stream.samplerate == 192000
        assert player.stream.active == (not paused)
        assert player.is_paused == paused
        assert player.current_index == 240000
        assert player._getExactPosition() == 1.25
        assert player._growing_file_path is None


def checkQueuedSwitch(
    player: AudioPlayer, rate: int, fade: bool, delayed: bool = False
) -> None:
    player.load(audio(44100, 10))
    phase = np.arange(rate * 6, dtype=np.float64) * (2 * np.pi * rate / 3 / rate)
    samples = np.repeat((np.sin(phase) * 0.1)[:, None], 2, axis=1).astype(np.float32)
    following = PreparedAudioBuffer(samples, rate, 2)
    transition = (
        PreparedAudioBuffer(np.full((44100 * 3, 2), 0.2, np.float32), 44100, 2)
        if fade
        else None
    )
    boundary = player.queueNext(7, transition, following, 0.5)
    assert boundary is not None
    origin, end = boundary
    offset = 44100 * 5 // 2 if delayed else 4410
    player.current_index = origin + offset
    player._playback_time = player.current_index / 44100
    assert player._timeline is not None
    if delayed:
        player._timeline.discardBefore(player.current_index - 44100 * 2)
    previous = player.stream
    assert previous is not None
    previous.start()
    player.is_playing = True
    new_boundary = player.beginQueuedTrack(origin, 44100 * 6, 0.5, end)
    assert player.stream is previous and player.stream.samplerate == 44100
    assert not previous.closed
    assert player.is_playing and player.stream.active
    assert abs(player._getExactPosition() - offset / 44100) < 1 / 44100
    assert abs(player.getLength() - 6) < 1 / 44100
    assert player._timeline is not None
    assert new_boundary == boundary
    assert player._track_origin == origin
    if fade:
        np.testing.assert_allclose(
            player._timeline.read(player.current_index, player.current_index + 100),
            0.2,
            atol=0.001,
        )
    converted = AudioPlayer.convertBuffer(following, 44100)
    fade_frames = end - origin
    np.testing.assert_allclose(
        player._timeline.read(end + 100, end + 200),
        converted.samples[fade_frames + 100 : fade_frames + 200] * 0.5,
        atol=0.001,
    )


def main() -> None:
    app = QApplication.instance() or QApplication([])
    with (
        tempfile.TemporaryDirectory() as directory,
        patch('core.audio_player.sd.OutputStream', OutputStream),
    ):
        player = AudioPlayer(devices=[DevicesInfo('Test', 0)])
        player._startProducer = lambda: None
        try:
            checkLoads(player, Path(directory))
            checkGrowingReload(player, Path(directory))
            for rate in (44100, 48000, 96000, 192000):
                for fade in (False, True):
                    checkQueuedSwitch(player, rate, fade)
            checkQueuedSwitch(player, 96000, True, delayed=True)
        finally:
            player.shutdown()
            app.processEvents()
    print('Audio player sample-rate checks passed')


if __name__ == '__main__':
    main()
