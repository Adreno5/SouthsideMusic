from __future__ import annotations

import sys
import threading
import unittest
from collections.abc import Callable
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from typing import Any, override
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core.config import cfg
from core.crossfade import CrossFadeInfo
from core.models import ArtistInfo, SongInfo, SongStorable
from core.playing_manager import AudioSegment_, PlayingManager, PlaySelection


class PreloadRaceTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.addCleanup(patch.stopall)
        self.app = QApplication.instance() or QApplication([])
        patch.object(cfg, 'enable_crossfade', True).start()
        patch.object(cfg, 'whisper_enabled', False).start()
        patch.object(cfg, 'play_method', 'Repeat list').start()
        self.manager = PlayingManager(None)
        self.addCleanup(self.manager._crossfade_debounce_timer.stop)
        self.addCleanup(self.manager._ft_worker.shutdown)
        self.addCleanup(self.manager._whisper_executor.shutdown)
        self.tasks: Queue[tuple[Callable[..., None], tuple[Any, ...]]] = Queue()
        self.manager.ctx = SimpleNamespace(
            player=None,
            lock=None,
            favs=[],
            setting_page=None,
            addScheduledTask=lambda fn, *args: self.tasks.put((fn, args)),
        )
        self.song = self._song('1')
        self.next_song = self._song('2')
        self.manager.playlist = [self.song, self.next_song]
        self.manager.current_index = 0
        self.manager.current_song = self.song
        self.audio = AudioSegment_.silent(duration=1000, frame_rate=8000)
        self.manager.current_song_audio = self.audio
        self.crossfade = CrossFadeInfo(
            0.5, 0.5, 0.5, 8000, 2, np.zeros((4000, 2), dtype=np.float32)
        )
        self.manager._computeCrossfadeInfoInWorker = Mock(return_value=self.crossfade)
        self.manager._computeLoudnessGain = Mock(return_value=0.5)
        self.queued: list[PlaySelection] = []
        self.manager._queuePreloadedSong = self.queued.append
        patch.object(SongStorable, 'getMusicPath', return_value='local.flac').start()
        patch.object(
            self.manager, '_storable_asset_missing', return_value=(False, False)
        ).start()
        self.cached = patch(
            'core.playing_manager.getCachedAudio', return_value=self.audio
        ).start()
        patch('core.playing_manager.scheduleFavoritesSave').start()
        self.workers: list[threading.Thread] = []
        self.errors: list[Exception] = []
        self.ready = threading.Event()
        self.resume = threading.Event()
        thread_type = threading.Thread

        def createThread(target: Callable[[], None], daemon: bool) -> threading.Thread:
            def run() -> None:
                try:
                    target()
                except Exception as error:
                    self.errors.append(error)

            worker = thread_type(target=run, daemon=daemon)
            self.workers.append(worker)
            return worker

        patch('core.playing_manager.threading.Thread', createThread).start()
        self.addCleanup(self._finishWorkers)

    def _song(self, song_id: str) -> SongStorable:
        song = SongStorable(
            SongInfo(
                name='Test song',
                artists=[ArtistInfo(id=1, name='Singer')],
                id=song_id,
                privilege=0,
                duration=1000,
            )
        )
        song.content_cache_hash = song_id
        return song

    def _joinWorkers(self) -> None:
        for worker in self.workers:
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive(), 'Preload worker did not finish')
        self.assertEqual(self.errors, [], 'Preload raised an unhandled exception')

    def _finishWorkers(self) -> None:
        self.resume.set()
        for worker in self.workers:
            worker.join(timeout=5)

    def _applyTasks(self) -> None:
        while not self.tasks.empty():
            fn, args = self.tasks.get_nowait()
            fn(*args)

    def _blockedGain(self, target_lufs: float, audio: AudioSegment_) -> float:
        self.ready.set()
        if not self.resume.wait(timeout=5):
            raise TimeoutError('Loudness calculation was not released')
        return 0.5

    def testCancelDuringLoudnessDoesNotRaiseOrPublishStaleAudio(self) -> None:
        self.manager._computeLoudnessGain.side_effect = self._blockedGain
        self.manager.preloadNextSong()
        self.assertTrue(self.ready.wait(timeout=5))
        self.manager.clearPreload()
        self.resume.set()
        self._joinWorkers()
        self._applyTasks()
        self.assertIsNone(self.manager.next_song_audio)
        self.assertIsNone(self.manager.next_song_gain)
        self.assertIsNone(self.manager.crossfade_info)
        self.assertIsNone(self.manager._next_song_buffer)
        self.assertFalse(self.manager.preloaded)
        self.assertEqual(self.queued, [])

    def testSuccessfulPreloadPublishesCompleteStateOnMainThread(self) -> None:
        self.manager.preloadNextSong()
        self._joinWorkers()
        self.assertIsNone(self.manager.next_song_audio)
        self.assertIsNone(self.manager.next_song_gain)
        self.assertIsNone(self.manager.crossfade_info)
        self.assertIsNone(self.manager._next_song_buffer)
        self.assertFalse(self.manager.preloaded)
        self._applyTasks()
        self.assertIs(self.manager.next_song_audio, self.audio)
        self.assertEqual(self.manager.next_song_gain, 0.5)
        self.assertIs(self.manager.crossfade_info, self.crossfade)
        self.assertEqual(self.manager._next_song_buffer.samples.shape, (8000, 1))
        self.assertEqual(self.manager.next_song_selection.song, self.next_song)
        self.assertTrue(self.manager.preloaded)
        self.assertEqual(self.queued, [self.manager.next_song_selection])

    def testCancelledCompletedPreloadIsNotApplied(self) -> None:
        self.manager.preloadNextSong()
        self._joinWorkers()
        self.manager.clearPreload()
        self._applyTasks()
        self.assertIsNone(self.manager.next_song_audio)
        self.assertIsNone(self.manager.next_song_gain)
        self.assertIsNone(self.manager._next_song_buffer)
        self.assertEqual(self.queued, [])

    def testCurrentAudioArrivingDuringLoudnessIsUsedForCrossfade(self) -> None:
        self.manager.current_song_audio = None
        self.manager._computeLoudnessGain.side_effect = self._blockedGain
        self.manager.preloadNextSong()
        self.assertTrue(self.ready.wait(timeout=5))
        self.manager.current_song_audio = self.audio
        self.resume.set()
        self._joinWorkers()
        self._applyTasks()
        self.assertIs(self.manager.crossfade_info, self.crossfade)

    def testNewerPreloadReplacesOlderResultForSameSelection(self) -> None:
        self.manager.preloadNextSong()
        self._joinWorkers()
        newer_audio = AudioSegment_.silent(duration=2000, frame_rate=8000)
        self.cached.return_value = newer_audio
        self.manager.preloadNextSong()
        self._joinWorkers()
        self._applyTasks()
        self.assertIs(self.manager.next_song_audio, newer_audio)
        self.assertEqual(self.manager._next_song_buffer.samples.shape, (16000, 1))
        self.assertEqual(self.queued, [self.manager.next_song_selection])

    def testDownloadedAudioStillCompletesPreload(self) -> None:
        with (
            patch.object(
                self.manager, '_storable_asset_missing', return_value=(True, True)
            ),
            patch.object(self.manager, '_downloadStorableMissingAssets') as download,
        ):
            self.manager.preloadNextSong()
            self.assertEqual(self.manager._preload_download_song_id, '2')
            self.assertEqual(self.workers, [])
            download.call_args.args[3](True)
            self._joinWorkers()
            self._applyTasks()
        self.assertIs(self.manager.next_song_audio, self.audio)
        self.assertEqual(self.manager.next_song_gain, 0.5)
        self.assertIsNone(self.manager._preload_download_song_id)
        self.assertTrue(self.manager.preloaded)
        self.assertEqual(self.queued, [self.manager.next_song_selection])

    def testCancelledDownloadCannotStartPreloadOrClearNewDownload(self) -> None:
        with (
            patch.object(
                self.manager, '_storable_asset_missing', return_value=(True, True)
            ),
            patch.object(self.manager, '_downloadStorableMissingAssets') as download,
        ):
            self.manager.preloadNextSong()
            self.manager.clearPreload()
            self.manager._preload_download_song_id = '3'
            for success in (True, False):
                download.call_args.args[3](success)
                self.assertEqual(self.manager._preload_download_song_id, '3')
        self.assertEqual(self.workers, [])
        self.assertIsNone(self.manager.next_song_audio)
        self.assertEqual(self.queued, [])

    def testStaleDecodeFailureDoesNotClearNewerPreload(self) -> None:
        self.cached.return_value = None

        def failDecode(*args: Any) -> AudioSegment_:
            self.ready.set()
            if not self.resume.wait(timeout=5):
                raise TimeoutError('Audio decode was not released')
            raise ValueError('Invalid cached audio')

        with patch('core.playing_manager.decodeAudioWithSidecar', failDecode):
            self.manager.preloadNextSong()
            self.assertTrue(self.ready.wait(timeout=5))
            self.manager.clearPreload()
            self.manager.next_song_audio = self.audio
            self.manager.next_song_gain = 0.75
            self.manager.crossfade_info = self.crossfade
            self.resume.set()
            self._joinWorkers()
            self._applyTasks()
        self.assertIs(self.manager.next_song_audio, self.audio)
        self.assertEqual(self.manager.next_song_gain, 0.75)
        self.assertIs(self.manager.crossfade_info, self.crossfade)
        self.assertEqual(self.next_song.content_cache_hash, '2')
        self.assertEqual(self.queued, [])


if __name__ == '__main__':
    unittest.main()
