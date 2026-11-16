from __future__ import annotations

import sys
import json
from io import StringIO
import tempfile
import unittest
from pathlib import Path
from collections.abc import Callable
from typing import Any, override
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core import whisper_lyrics as whisper
from core.config import cfg
from core.lyric_formats import parseYrc
from core.lyrics import YRCLyricParser
from core.models import ArtistInfo, SongInfo, SongStorable


class WhisperLyricsTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        patch.object(whisper, 'WHISPER_DATA_DIR', Path(self.temp.name)).start()
        patch.object(cfg, 'whisper_model', 'tiny', create=True).start()
        patch.object(cfg, 'whisper_enabled', False, create=True).start()
        self.song = SongStorable(
            SongInfo(
                name='Local song',
                artists=[ArtistInfo(id=1, name='Singer')],
                id='123',
                privilege=0,
                duration=10000,
            )
        )
        self.result = {
            'segments': [
                {
                    'start': 1.0,
                    'end': 3.5,
                    'text': ' Hello world!',
                    'words': [
                        {'word': ' Hello', 'start': 1.25, 'end': 1.7},
                        {'word': ' world!', 'start': 2.1, 'end': 3.25},
                    ],
                }
            ]
        }

    def test_word_timestamps_round_trip_into_existing_lyric_objects(self) -> None:
        lyrics = whisper.whisperResultToLyrics(self.result)
        parser = YRCLyricParser()
        parser.cur = lyrics['yrc_lyric']
        parser.parse()
        self.assertTrue(parser.hasYrcTiming())
        self.assertEqual(len(parser.parsed), 1)
        line = parser.parsed[0]
        self.assertEqual(line.content, 'Hello world!')
        self.assertAlmostEqual(line.time, 1.25)
        self.assertAlmostEqual(line.duration, 2.0)
        self.assertEqual([word.char for word in line.chars], ['Hello', ' world!'])
        self.assertAlmostEqual(line.chars[1].start, 2.1)
        self.assertAlmostEqual(line.chars[1].duration, 1.15)
        self.assertEqual(lyrics['translated_lyric'], '')
        self.assertEqual(lyrics['ytlrc_lyric'], '')
        self.assertIn('[00:01.250]Hello world!', lyrics['lyric'])

    def test_cjk_words_preserve_whisper_timing(self) -> None:
        self.result['segments'][0]['words'] = [
            {'word': '你好', 'start': 1.0, 'end': 1.5},
            {'word': '世界', 'start': 1.6, 'end': 2.0},
        ]
        lyrics = whisper.whisperResultToLyrics(self.result)
        self.assertEqual(
            lyrics['yrc_lyric'], '[1000,1000](1000,500,0)你好(1600,400,0)世界'
        )

    def test_zero_duration_and_overlapping_words_keep_all_recognized_text(self) -> None:
        self.result['segments'][0]['words'] = [
            {'word': ' One', 'start': 1.0, 'end': 4.0},
            {'word': ' two', 'start': 1.8, 'end': 1.9},
            {'word': ' three', 'start': 2.0, 'end': 2.0},
            {'word': ' four', 'start': 2.0, 'end': 3.0},
        ]
        lyrics = whisper.whisperResultToLyrics(self.result)
        parser = YRCLyricParser()
        parser.cur = lyrics['yrc_lyric']
        parser.parse()
        self.assertEqual(parser.parsed[0].content, 'One two three four')
        self.assertEqual(parser.parsed[0].duration, 3.0)
        self.assertEqual(parser.parsed[0].chars[1].start, 1.8)
        self.assertEqual(parser.parsed[0].chars[2].duration, 0.0)

    def test_missing_and_invalid_word_timing_is_not_fabricated(self) -> None:
        self.result['segments'].append({'start': 4, 'end': 5, 'text': 'Untimed'})
        self.result['segments'][0]['words'].extend([
            {'word': 'bad', 'start': float('nan'), 'end': 9},
            {'word': 'reverse', 'start': 8, 'end': 7},
        ])
        lyrics = whisper.whisperResultToLyrics(self.result)
        self.assertNotIn('Untimed', lyrics['lyric'])
        self.assertNotIn('bad', lyrics['lyric'])
        self.assertNotIn('reverse', lyrics['lyric'])

    def test_long_segments_split_without_losing_words_or_changing_timestamps(
        self,
    ) -> None:
        words = [
            {
                'word': f' word{index}',
                'start': 30 + index * 0.4,
                'end': 30 + index * 0.4 + (0.32 if index % 2 else 0.4),
            }
            for index in range(80)
        ]
        result = {'segments': [self.result['segments'][0], {'words': words}]}
        lyrics = whisper.whisperResultToLyrics(result)
        lines = parseYrc(lyrics['yrc_lyric'])
        self.assertEqual(lines[0].text, 'Hello world!')
        self.assertGreater(len(lines), 8)
        self.assertTrue(all(len(line.text) <= 56 for line in lines))
        self.assertTrue(all(line.end - line.start <= 6500 for line in lines))
        self.assertEqual(
            [
                (word.text.strip(), word.start, word.end)
                for line in lines[1:]
                for word in line.words
            ],
            [
                (
                    word['word'].strip(),
                    round(word['start'] * 1000),
                    round(word['end'] * 1000),
                )
                for word in words
            ],
        )
        self.assertEqual(len(lyrics['lyric'].splitlines()), len(lines))

    def test_pauses_and_sentence_punctuation_form_lyric_lines(self) -> None:
        lyrics = whisper.whisperResultToLyrics({
            'segments': [
                {
                    'words': [
                        {'word': 'We', 'start': 0.0, 'end': 0.5},
                        {'word': ' wait', 'start': 0.5, 'end': 1.0},
                        {'word': ' You', 'start': 1.8, 'end': 2.3},
                        {'word': ' sing?', 'start': 2.3, 'end': 3.0},
                        {'word': ' Then', 'start': 3.0, 'end': 3.5},
                        {'word': ' dance', 'start': 3.5, 'end': 4.0},
                    ]
                }
            ]
        })
        lines = parseYrc(lyrics['yrc_lyric'])
        self.assertEqual(
            [line.text for line in lines], ['We wait', 'You sing?', 'Then dance']
        )
        self.assertEqual([line.start for line in lines], [0, 1800, 3000])

    def test_long_line_prefers_a_comma_boundary(self) -> None:
        text = (
            'Every night we walk together, the lights are shining over the quiet town'
        )
        lyrics = whisper.whisperResultToLyrics({
            'segments': [
                {
                    'words': [
                        {
                            'word': ' ' + word,
                            'start': index * 0.3,
                            'end': (index + 1) * 0.3,
                        }
                        for index, word in enumerate(text.split())
                    ]
                }
            ]
        })
        lines = parseYrc(lyrics['yrc_lyric'])
        self.assertEqual(lines[0].text, 'Every night we walk together,')
        self.assertEqual(' '.join(line.text for line in lines), text)

    def test_continuous_cjk_lyrics_use_shorter_lines_without_splitting_words(
        self,
    ) -> None:
        words = ['星光', '落在', '安静', '街道', '我们', '慢慢', '走过'] * 8
        lyrics = whisper.whisperResultToLyrics({
            'segments': [
                {
                    'words': [
                        {'word': word, 'start': index * 0.15, 'end': (index + 1) * 0.15}
                        for index, word in enumerate(words)
                    ]
                }
            ]
        })
        lines = parseYrc(lyrics['yrc_lyric'])
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(len(line.text) <= 28 for line in lines))
        self.assertEqual([word.text for line in lines for word in line.words], words)

    def test_slow_words_split_by_duration_without_retiming_a_held_note(self) -> None:
        words = [
            {'word': ' ' + text, 'start': index * 2.5, 'end': (index + 1) * 2.5}
            for index, text in enumerate(['we', 'sing', 'on', 'and', 'on', 'again'])
        ]
        words.append({'word': ' Stay', 'start': 15.0, 'end': 25.0})
        lines = parseYrc(
            whisper.whisperResultToLyrics({'segments': [{'words': words}]})['yrc_lyric']
        )
        self.assertEqual(
            [line.text for line in lines], ['we sing', 'on and', 'on again', 'Stay']
        )
        self.assertEqual(
            (lines[-1].words[0].start, lines[-1].words[0].end), (15000, 25000)
        )

    def test_old_long_line_cache_is_reflowed_without_running_whisper(self) -> None:
        text = ' '.join(f'word{index}' for index in range(60))
        path = whisper._lyricCachePath(self.song, 'tiny')
        path.parent.mkdir(parents=True)
        data = {
            'pipeline': whisper.WHISPER_PIPELINE_VERSION,
            'complete': True,
            'model': 'tiny',
            'song_id': str(self.song.id),
            'audio_hash': self.song.content_cache_hash,
            'lyrics': {
                'lyric': '[00:00.000]' + text,
                'yrc_lyric': '[0,24000]'
                + ''.join(
                    f'({index * 400},400,0) {word}'
                    for index, word in enumerate(text.split())
                ),
                'translated_lyric': '',
                'ytlrc_lyric': '',
            },
        }
        path.write_text(json.dumps(data), encoding='utf-8')
        before = path.read_bytes()
        with patch.object(whisper, '_runWorker') as worker:
            lyrics = whisper.transcribeWhisper(self.song, 'tiny', lambda: False)
        worker.assert_not_called()
        self.assertIsNotNone(lyrics)
        lines = parseYrc(lyrics['yrc_lyric'])
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(len(line.text) <= 56 for line in lines))
        self.assertEqual(' '.join(line.text for line in lines), text)
        self.assertEqual(whisper._splitWhisperLines(lines), lines)
        self.assertTrue(whisper.hasWhisperLyrics(self.song, 'tiny'))
        self.assertEqual(path.read_bytes(), before)

    def test_models_have_distinct_multilingual_repositories(self) -> None:
        names = {model.name for model in whisper.WHISPER_MODELS}
        self.assertEqual(
            names, {'tiny', 'base', 'small', 'medium', 'large-v3', 'turbo'}
        )
        for model in whisper.WHISPER_MODELS:
            self.assertGreater(model.size_mb, 0)
            self.assertNotIn('.en', model.repository)

    def test_auto_model_uses_hardware_recommendation(self) -> None:
        hardware = whisper.WhisperHardware(
            runtime_package='onnxruntime',
            runtime_label='ONNX Runtime CPU',
            device='cpu',
            recommended_model='small',
            description='CPU',
        )
        with patch.object(whisper, 'getWhisperHardware', return_value=hardware):
            self.assertEqual(whisper.getWhisperModel('auto').name, 'small')
            self.assertEqual(whisper.getWhisperModel('base').name, 'base')
            self.assertEqual(whisper.getWhisperModel('invalid').name, 'small')

    def test_whisper_mode_hides_network_lyrics_without_altering_them(self) -> None:
        with patch.object(SongStorable, 'getLyricPath') as network_path:
            cfg.whisper_enabled = True
            self.assertEqual(self.song.getLyrics()['lyric'], '')
            network_path.assert_not_called()

    def test_offline_mode_does_not_start_network_lyric_tasks(self) -> None:
        from core import lyric_sources

        cfg.whisper_enabled = True
        with patch.object(lyric_sources, '_tasks') as tasks:
            self.assertEqual(
                list(lyric_sources.iterLyricUpdates('Song', 'Singer', '123', 10000)),
                [],
            )
            tasks.assert_not_called()

    def test_caches_are_separate_for_each_model(self) -> None:
        whisper.writeWhisperLyrics(self.song, 'tiny', self.result)
        self.assertTrue(whisper.hasWhisperLyrics(self.song, 'tiny'))
        self.assertFalse(whisper.hasWhisperLyrics(self.song, 'base'))
        cfg.whisper_enabled = True
        self.assertIn('Hello', self.song.getLyrics()['lyric'])
        cfg.whisper_model = 'base'
        self.assertEqual(self.song.getLyrics()['lyric'], '')

    def test_empty_recognition_is_cached(self) -> None:
        whisper.writeWhisperLyrics(self.song, 'tiny', {'segments': []})
        self.assertTrue(whisper.hasWhisperLyrics(self.song, 'tiny'))
        self.assertEqual(whisper.readWhisperLyrics(self.song)['lyric'], '')

    def test_previous_pipeline_cache_is_ignored(self) -> None:
        path = whisper._lyricCachePath(self.song, 'tiny')
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({
                'pipeline': 4,
                'complete': True,
                'model': 'tiny',
                'song_id': str(self.song.id),
                'audio_hash': self.song.content_cache_hash,
                'lyrics': whisper.whisperResultToLyrics(self.result),
            }),
            encoding='utf-8',
        )
        self.assertFalse(whisper.hasWhisperLyrics(self.song, 'tiny'))
        self.assertEqual(whisper.readWhisperLyrics(self.song, 'tiny')['lyric'], '')

    def test_partial_recognition_cache_is_not_marked_complete(self) -> None:
        whisper.writeWhisperLyrics(self.song, 'tiny', self.result, complete=False)
        self.assertFalse(whisper.hasWhisperLyrics(self.song, 'tiny'))
        self.assertIn('Hello', whisper.readWhisperLyrics(self.song, 'tiny')['lyric'])

    def test_worker_forwards_replacements_before_process_exit(self) -> None:
        segment = {'start': 1, 'end': 2, 'text': 'word', 'words': []}
        records = [{'segments': [segment]}, {'segments': []}]
        lines = '\n'.join(json.dumps(record) for record in records) + '\n'
        process = MagicMock()
        process.stdout = StringIO(lines)
        process.returncode = 0
        process.poll.return_value = 0
        process.__enter__.return_value = process
        segments: list[dict[str, object]] = []

        def receive(result: dict[str, Any]) -> None:
            process.wait.assert_not_called()
            segments.append(result)

        with patch.object(whisper.subprocess, 'Popen', return_value=process):
            output = whisper._runWorker([], on_result=receive)

        self.assertEqual(segments, records)
        self.assertEqual(output, lines)

    def test_changed_audio_invalidates_recognition(self) -> None:
        self.song.content_cache_hash = 'first-audio'
        whisper.writeWhisperLyrics(self.song, 'tiny', self.result)
        self.song.content_cache_hash = 'replacement-audio'
        self.assertFalse(whisper.hasWhisperLyrics(self.song, 'tiny'))

    def test_cancelled_recognition_never_starts_a_worker(self) -> None:
        with patch.object(whisper, '_runWorker') as worker:
            self.assertIsNone(
                whisper.transcribeWhisper(self.song, 'tiny', lambda: True)
            )
            worker.assert_not_called()

    def test_transcription_emits_cumulative_lyrics_before_completion(self) -> None:
        segments = [
            {
                'start': 0.0,
                'end': 1.0,
                'text': ' First',
                'words': [{'word': ' First', 'start': 0.0, 'end': 0.5}],
            },
            {
                'start': 1.0,
                'end': 2.0,
                'text': ' second',
                'words': [{'word': ' second', 'start': 1.0, 'end': 1.5}],
            },
        ]
        updates: list[dict[str, str]] = []

        def run_worker(
            arguments: list[str],
            cancelled: Callable[[], bool],
            on_result: Callable[[dict[str, Any]], None],
        ) -> str:
            on_result({'segments': segments[:1]})
            self.assertFalse(whisper.hasWhisperLyrics(self.song, 'tiny'))
            self.assertEqual(
                len(
                    whisper.readWhisperLyrics(self.song, 'tiny')[
                        'yrc_lyric'
                    ].splitlines()
                ),
                1,
            )
            on_result({'segments': segments})
            self.assertFalse(whisper.hasWhisperLyrics(self.song, 'tiny'))
            self.assertEqual(
                len(
                    whisper.readWhisperLyrics(self.song, 'tiny')[
                        'yrc_lyric'
                    ].splitlines()
                ),
                2,
            )
            Path(arguments[-1]).write_text(
                json.dumps({'segments': segments}), encoding='utf-8'
            )
            return ''

        with (
            patch.object(whisper, 'isWhisperReady', return_value=True),
            patch.object(SongStorable, 'getMusicPath', return_value='local.wav'),
            patch.object(whisper, '_runWorker', side_effect=run_worker),
        ):
            result = whisper.transcribeWhisper(
                self.song, 'tiny', lambda: False, updates.append
            )

        self.assertIsNotNone(result)
        self.assertEqual(len(updates), 2)
        self.assertEqual(len(updates[0]['yrc_lyric'].splitlines()), 1)
        self.assertEqual(len(updates[1]['yrc_lyric'].splitlines()), 2)
        self.assertTrue(whisper.hasWhisperLyrics(self.song, 'tiny'))
        self.assertEqual(result['yrc_lyric'], updates[-1]['yrc_lyric'])

    def test_stream_replaces_mistakes_and_retracts_empty_results(self) -> None:
        updates: list[dict[str, str]] = []

        def run_worker(
            arguments: list[str],
            cancelled: Callable[[], bool],
            on_result: Callable[[dict[str, Any]], None],
        ) -> str:
            on_result(self.result)
            on_result({'segments': []})
            corrected = {
                'segments': [
                    {
                        'words': [{'word': 'Corrected', 'start': 1.0, 'end': 3.0}],
                    }
                ],
            }
            on_result(corrected)
            self.assertFalse(whisper.hasWhisperLyrics(self.song, 'tiny'))
            self.assertNotIn('Hello', updates[-1]['lyric'])
            self.assertEqual(len(updates[-1]['yrc_lyric'].splitlines()), 1)
            Path(arguments[-1]).write_text(json.dumps(corrected), encoding='utf-8')
            return ''

        with (
            patch.object(whisper, 'isWhisperReady', return_value=True),
            patch.object(SongStorable, 'getMusicPath', return_value='local.wav'),
            patch.object(whisper, '_runWorker', side_effect=run_worker),
        ):
            result = whisper.transcribeWhisper(
                self.song, 'tiny', lambda: False, updates.append
            )
        self.assertEqual(len(updates), 3)
        self.assertEqual(updates[1]['yrc_lyric'], '')
        self.assertEqual(result, updates[-1])
        self.assertTrue(whisper.hasWhisperLyrics(self.song, 'tiny'))

    def test_incomplete_model_cannot_enable_whisper(self) -> None:
        model_path = Path(self.temp.name) / 'models' / 'tiny'
        model_path.mkdir(parents=True)
        (model_path / 'config.json').write_text('{}', encoding='utf-8')
        self.assertFalse(whisper.isWhisperReady('tiny'))

    def test_old_runtime_marker_requires_reinstallation(self) -> None:
        runtime_path = whisper._runtimePath()
        runtime_path.mkdir(parents=True)
        for package in ('faster_whisper', 'ctranslate2', 'onnxruntime', 'av'):
            package_path = runtime_path / package
            package_path.mkdir()
            (package_path / '__init__.py').write_text('', encoding='utf-8')
        (runtime_path / '.ready.json').write_text(
            json.dumps({
                'python': whisper.sys.implementation.cache_tag,
                'device': whisper.getWhisperHardware().device,
            }),
            encoding='utf-8',
        )
        self.assertFalse(whisper._runtimeReady())

    def test_partial_download_is_rejected(self) -> None:
        destination = Path(self.temp.name) / 'model.bin'
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.headers = {'content-length': '10'}
        response.iter_content.return_value = [b'partial']
        with patch.object(whisper.requests, 'get', return_value=response):
            with self.assertRaises(ValueError):
                whisper._downloadFile('https://example.com/model.bin', destination)
        self.assertFalse(destination.exists())


if __name__ == '__main__':
    unittest.main()
