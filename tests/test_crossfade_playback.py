from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core.audio_player import (
    AudioPlayer,
    DevicesInfo,
    PreparedAudioBuffer,
)
from core.playing_manager import (
    AudioSegment_,
    PlayingManager,
    PlaySelection,
)
from imports import QApplication
from services.events import REQUEST_BR_CHANGED


class OutputStream:
    def __init__(self, **settings: Any) -> None:
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


def checkQualitySync(max_br: int, stale: bool = False) -> None:
    tasks: list[Callable[[], None]] = []

    def deferTask(fn: Callable[[], None], *args: Any) -> None:
        tasks.append(fn)

    with (
        patch('core.audio_player.sd.OutputStream', OutputStream),
        patch('core.playing_manager.asyncTask', deferTask),
        patch('core.playing_manager.getBackend') as backend,
        patch('core.playing_manager.event_bus.emit') as emit,
    ):
        backend.return_value.getSongQualityPrivilege.return_value.max_br = max_br
        player = AudioPlayer(devices=[DevicesInfo('Test', 0)])
        player._startProducer = lambda: None
        manager = PlayingManager(None)
        manager.ctx = SimpleNamespace(
            player=player,
            config=SimpleNamespace(target_request_br=3200000),
            main_window=None,
            setting_page=None,
        )
        manager._loadPlaybackImage = Mock()
        manager._show_original_lyrics = Mock()
        manager._compute_gain_async = Mock()
        manager._download_update_lyrics = Mock()
        manager._playDownloadingStorable = Mock()
        manager.preloadNextSong = Mock()
        manager._schedule = lambda fn, *args: fn(*args)
        try:
            rate = 44100
            player.loadPrepared(
                PreparedAudioBuffer(np.full((rate * 10, 2), 0.6, np.float32), rate, 2)
            )
            following = PreparedAudioBuffer(
                np.full((rate * 6, 2), 0.4, np.float32), rate, 2
            )
            transition = PreparedAudioBuffer(
                np.repeat(
                    np.linspace(0.6, 0.4, rate * 3, dtype=np.float32)[:, None],
                    2,
                    axis=1,
                ),
                rate,
                2,
            )
            boundary = player.queueNext(7, transition, following, 1.0)
            assert boundary is not None
            manager.playlist = [Mock(id=1, duration=10000), Mock(id=2, duration=6000)]
            manager.current_index = 0
            manager.current_song = manager.playlist[0]
            selection = PlaySelection(1, manager.playlist[1], 'Repeat list', False, 0)
            manager.next_song_selection = selection
            manager.next_song_audio = AudioSegment_.silent(duration=6000)
            manager.next_song_gain = 1.0
            manager._next_song_buffer = following
            manager._queued_selection = selection
            manager._queued_boundary = boundary
            manager._queued_frames = rate * 6
            player.current_index = boundary[0] + 2048
            player.is_playing = True
            stream = player.stream
            assert stream is not None
            stream.start()
            manager.onPlayerPositionChanged(player.getPosition())
            assert manager.crossfading
            timeline = player._timeline
            play_seq = manager._play_seq
            emit.reset_mock()
            manager._preload_triggered = True
            manager._preload_download_song_id = '3'
            download_seq = manager._preload_download_seq
            if stale:
                manager._play_seq += 1
            tasks[0]()
            assert player.stream is stream and not stream.closed
            assert player.is_playing and manager.crossfading
            assert player._timeline is timeline
            assert manager._transition_end == boundary[1]
            assert manager._play_seq == play_seq + int(stale)
            manager._playDownloadingStorable.assert_not_called()
            expected_br = 3200000 if stale else min(max_br, 3200000)
            assert manager.getCurrentRequestBr() == expected_br
            if expected_br != 3200000:
                emit.assert_called_once_with(REQUEST_BR_CHANGED, expected_br)
                assert manager._preload_download_seq == download_seq + 1
                assert manager._preload_download_song_id is None
                manager.preloadNextSong.assert_called_once()
            else:
                emit.assert_not_called()
                assert manager._preload_download_seq == download_seq
                manager.preloadNextSong.assert_not_called()
            player._audio_queue.put((transition.samples[2048:4096], 2048, 1.0))
            output = np.empty((2048, 2), dtype=np.float32)
            player._renderAudio(
                output,
                2048,
                SimpleNamespace(outputBufferDacTime=0, currentTime=0),
                SimpleNamespace(output_underflow=False),
            )
            np.testing.assert_allclose(output, transition.samples[2048:4096])
            if not stale:
                manager.setRequestBr(128000)
                assert not manager.crossfading
                assert stream.closed
                manager._playDownloadingStorable.assert_called_once()
        finally:
            manager._crossfade_debounce_timer.stop()
            manager._ft_worker.shutdown()
            player.shutdown()


def main() -> None:
    app = QApplication.instance() or QApplication([])
    for max_br in (320000, 3200000, 4000000):
        checkQualitySync(max_br)
    checkQualitySync(320000, stale=True)
    app.processEvents()
    print('Crossfade playback checks passed')


if __name__ == '__main__':
    main()
