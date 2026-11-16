from __future__ import annotations

import sys
import unittest
from concurrent.futures import Future
from pathlib import Path
from typing import cast, override
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core.config import cfg
from core.lyric_sources import LyricCandidate
from core.lyrics import LyricManager
from core.models import ArtistInfo, SongInfo, SongStorable
from core.playing_manager import PlayingManager


class WhisperPlaybackTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.addCleanup(patch.stopall)
        patch.object(cfg, 'whisper_enabled', True, create=True).start()
        patch.object(cfg, 'whisper_model', 'tiny', create=True).start()
        patch.object(cfg, 'enable_lyric_handoff', False).start()
        self.manager = PlayingManager(cast(object, None))
        self.manager.ctx = MagicMock()
        self.manager._whisper_executor.shutdown(wait=False)
        self.executor = MagicMock()
        self.manager._whisper_executor = self.executor
        self.song = self._song('1')
        self.next_song = self._song('2')
        self.manager.current_song = self.song
        self.manager._whisper_next_song = self.next_song
        self.future: Future[dict[str, str] | None] = Future()
        self.executor.submit.return_value = self.future
        patch('core.playing_manager.isWhisperReady', return_value=True).start()
        self.cached = patch(
            'core.playing_manager.hasWhisperLyrics', return_value=False
        ).start()
        patch.object(SongStorable, 'getMusicPath', return_value='local.flac').start()

    def _song(self, song_id: str) -> SongStorable:
        return SongStorable(
            SongInfo(
                name='Local song',
                artists=[ArtistInfo(id=1, name='Singer')],
                id=song_id,
                privilege=0,
                duration=10000,
            )
        )

    def test_current_lyrics_use_local_recognition_without_online_loader(self) -> None:
        with patch('core.playing_manager.iterLyricUpdates') as online:
            self.manager._download_update_lyrics(self.song)
            self.executor.submit.assert_called_once()
            online.assert_not_called()

    def test_duplicate_requests_share_current_recognition(self) -> None:
        self.manager._queueWhisperLyrics(self.song)
        self.manager._queueWhisperLyrics(self.song)
        self.executor.submit.assert_called_once()

    def test_streamed_lyrics_are_scheduled_as_soon_as_a_segment_arrives(self) -> None:
        self.manager._queueWhisperLyrics(self.song)
        on_update = self.executor.submit.call_args.args[4]
        lyrics = {
            'lyric': '[00:01.000]First',
            'yrc_lyric': '[1000,500](1000,500,0)First',
            'translated_lyric': '',
            'ytlrc_lyric': '',
        }
        with patch.object(self.manager, '_applyWhisperLyrics') as apply:
            on_update(lyrics)
            self.manager.ctx.addScheduledTask.assert_called_once_with(
                apply,
                self.song,
                'tiny',
                self.manager._whisper_generation,
                self.song.content_cache_hash,
                lyrics,
            )

    def test_streamed_lyrics_apply_on_main_thread_for_current_song(self) -> None:
        lyrics = {
            'lyric': '[00:01.000]First',
            'yrc_lyric': '[1000,500](1000,500,0)First',
            'translated_lyric': '',
            'ytlrc_lyric': '',
        }
        with (
            patch.object(self.manager, '_applyLyricTexts', return_value=True) as apply,
            patch('core.playing_manager.event_bus.emit') as emit,
        ):
            self.manager._applyWhisperLyrics(
                self.song,
                'tiny',
                self.manager._whisper_generation,
                self.song.content_cache_hash,
                lyrics,
            )
        apply.assert_called_once_with(
            self.song,
            lyrics['lyric'],
            lyrics['yrc_lyric'],
            lyrics['translated_lyric'],
            'Whisper',
        )
        emit.assert_called_once()

    def test_next_song_waits_until_current_lyrics_are_cached(self) -> None:
        self.manager._queueWhisperLyrics(self.next_song)
        self.executor.submit.assert_not_called()
        self.cached.side_effect = lambda song, model: song is self.song
        self.manager._queueWhisperLyrics(self.next_song)
        self.executor.submit.assert_called_once()

    def test_revised_and_retracted_yrc_updates_the_playing_lyrics(self) -> None:
        lyrics_manager = LyricManager()
        lyrics_manager.startSong(self.song, 10.0, continuous=False)
        self.manager.ctx.lyrics_manager = lyrics_manager
        with patch('core.playing_manager.event_bus.emit'):
            for text in ('Wrong', 'Corrected', ''):
                self.manager._applyWhisperLyrics(
                    self.song,
                    'tiny',
                    self.manager._whisper_generation,
                    self.song.content_cache_hash,
                    {
                        'lyric': f'[00:01.000]{text}' if text else '',
                        'yrc_lyric': f'[1000,500](1000,500,0){text}' if text else '',
                        'translated_lyric': '',
                        'ytlrc_lyric': '',
                    },
                )
                self.assertEqual(
                    [line.content for line in lyrics_manager.yrc.parsed],
                    [text] if text else [],
                )
                self.assertEqual(
                    [line.content for line in lyrics_manager.tracks[0].lines],
                    [text] if text else [],
                )

    def test_next_song_prefetch_does_not_require_lyric_handoff(self) -> None:
        self.cached.side_effect = lambda song, model: song is self.song
        with patch.object(self.manager, 'getNextSong', return_value=self.next_song):
            self.manager.refreshLyricPreview()
        self.executor.submit.assert_called_once()
        self.assertEqual(self.executor.submit.call_args.args[1], self.next_song)

    def test_skipping_song_cancels_stale_inference(self) -> None:
        self.manager._queueWhisperLyrics(self.song)
        cancelled = self.executor.submit.call_args.args[3]
        self.assertFalse(cancelled())
        self.manager.current_song = self.next_song
        self.assertTrue(cancelled())

    def test_prefetched_song_continues_when_it_becomes_current(self) -> None:
        self.cached.side_effect = lambda song, model: song is self.song
        self.manager._queueWhisperLyrics(self.next_song)
        cancelled = self.executor.submit.call_args.args[3]
        self.manager.current_song = self.next_song
        self.manager._whisper_next_song = None
        self.assertFalse(cancelled())

    def test_model_switch_cancels_running_inference(self) -> None:
        self.manager._queueWhisperLyrics(self.song)
        cancelled = self.executor.submit.call_args.args[3]
        cfg.whisper_model = 'base'
        self.assertTrue(cancelled())

    def test_disabling_whisper_rejects_late_worker_result(self) -> None:
        self.manager._queueWhisperLyrics(self.song)
        self.future.set_result({'lyric': 'recognized'})
        cfg.whisper_enabled = False
        with patch.object(self.manager, '_show_original_lyrics') as show:
            self.manager._finishWhisperLyrics(
                self.song, 'tiny', self.manager._whisper_generation, self.future
            )
            show.assert_not_called()

    def test_online_result_cannot_override_whisper_mode(self) -> None:
        candidate = LyricCandidate('online', '[00:01.000]Online', '', '', False)
        with patch.object(SongStorable, 'writeLyrics') as write:
            self.manager._applyLyricUpdate(self.song, candidate)
            write.assert_not_called()
            self.manager.ctx.lyrics_manager.applyLyrics.assert_not_called()


if __name__ == '__main__':
    unittest.main()
