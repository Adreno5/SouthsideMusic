from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core import models as models_module  # noqa: E402
from core import whisper_lyrics  # noqa: E402
from core.app_context import AppContext  # noqa: E402
from core.llm import ONERAD_SYSTEM_PROMPT, TOOL_USAGE  # noqa: E402
from core.llm_tools import KNOWN_TOOLS, LLMToolRunner, llmToolSchemas  # noqa: E402
from core.lyrics import LyricManager  # noqa: E402
from core.models import ArtistInfo, SongInfo, SongStorable  # noqa: E402
from services.events.event_bus import event_bus  # noqa: E402
from services.events import Events  # noqa: E402

LYRIC_TOOLS = (
    'get_current_lyrics',
    'set_current_lyrics',
    'set_translation_enabled',
    'refresh_lyrics',
)

TEST_SONG_ID = '__test_llm_lyric_tools__'
ORIGINAL = '[00:01.000]hello world\n[00:03.000]second line\n'
TRANSLATED = '[00:01.000]你好世界\n[00:03.000]第二行\n'


class _FakePlayingManager:
    def __init__(self, song: SongStorable, lyrics_manager: LyricManager) -> None:
        self.current_song = song
        self.playlist: list[SongStorable] = []
        self.lyrics_manager = lyrics_manager

    def applyCurrentLyrics(self, payload: dict[str, object]) -> None:
        self.lyrics_manager.applyParsedLyrics(payload, 0)


class _FakeContext:
    def __init__(self, song: SongStorable) -> None:
        self.lyrics_manager = LyricManager()
        self.playing_manager = _FakePlayingManager(song, self.lyrics_manager)
        self.mgr = self.lyrics_manager.lrc
        self.ymgr = self.lyrics_manager.yrc
        self.transmgr = self.lyrics_manager.translation
        self.player = None
        self.llm_song_handles: dict[str, Any] = {}
        self.llm_folder_handles: dict[str, Any] = {}

    def addScheduledTask(self, task: Any, *args: Any, **kwargs: Any) -> None:
        task(*args, **kwargs)


def _test_song() -> SongStorable:
    return SongStorable(
        SongInfo(
            name='test song',
            artists=[ArtistInfo(id=1, name='test artist')],
            id=TEST_SONG_ID,
            privilege=-1,
        )
    )


def test_lyric_tools_are_registered_everywhere() -> None:
    schema_names = [schema['function']['name'] for schema in llmToolSchemas()]
    for name in LYRIC_TOOLS:
        assert name in TOOL_USAGE
        assert name in KNOWN_TOOLS
        assert name in schema_names
        assert name in ONERAD_SYSTEM_PROMPT


def test_every_usage_entry_has_a_schema() -> None:
    schema_names = {schema['function']['name'] for schema in llmToolSchemas()}
    assert schema_names == set(TOOL_USAGE)


def test_set_current_lyrics_requires_lyric() -> None:
    schemas = {schema['function']['name']: schema for schema in llmToolSchemas()}
    parameters = schemas['set_current_lyrics']['function']['parameters']
    assert parameters['required'] == ['lyric']
    assert sorted(parameters['properties']) == [
        'lyric',
        'translated_lyric',
        'yrc_lyric',
    ]
    assert '_required' not in str(llmToolSchemas())


def test_set_current_lyrics_stores_translation_and_notifies() -> None:
    song = _test_song()
    ctx = _FakeContext(song)
    runner = LLMToolRunner(
        cast(AppContext, ctx), allow_actions=True, require_usage=False
    )
    notified: list[SongStorable] = []
    listener = notified.append
    event_bus.subscribe(Events.PLAYBACK_LYRICS_UPDATED, listener)
    try:
        raw = runner.runTool(
            'set_current_lyrics',
            {'lyric': ORIGINAL, 'translated_lyric': TRANSLATED},
        )
        result = json.loads(raw)
        assert result['updated'] is True
        assert result['has_translation'] is True
        assert song.getLyrics()['translated_lyric'] == TRANSLATED
        assert song.translated_lyric
        assert notified == [song]
        assert bool(notified[0].translated_lyric)
        assert ctx.transmgr.cur == TRANSLATED
        assert [line.content for line in ctx.transmgr.parsed] == [
            '你好世界',
            '第二行',
        ]
        assert [line.content for line in ctx.mgr.parsed] == [
            'hello world',
            'second line',
        ]
    finally:
        event_bus.unsubscribe(Events.PLAYBACK_LYRICS_UPDATED, listener)
        os.remove(song.getLyricPath())


def test_set_current_lyrics_keeps_the_stored_translation() -> None:
    song = _test_song()
    song.writeLyrics(ORIGINAL, TRANSLATED)
    ctx = _FakeContext(song)
    runner = LLMToolRunner(
        cast(AppContext, ctx), allow_actions=True, require_usage=False
    )
    try:
        raw = runner.runTool(
            'set_current_lyrics',
            {'lyric': '[00:01.000]fixed line\n[00:03.000]second line\n'},
        )
        assert json.loads(raw)['has_translation'] is True
        assert song.translated_lyric == TRANSLATED
    finally:
        os.remove(song.getLyricPath())


def test_set_current_lyrics_does_not_copy_recognition_timings() -> None:
    song = _test_song()
    result = {
        'segments': [
            {'words': [{'word': ' hello', 'start': 1.0, 'end': 2.0}]},
        ]
    }
    recognized = whisper_lyrics.whisperResultToLyrics(result)
    with tempfile.TemporaryDirectory() as temp:
        with (
            patch.object(models_module, 'LYRIC_DATA_DIR', temp),
            patch.object(whisper_lyrics, 'WHISPER_DATA_DIR', Path(temp)),
        ):
            whisper_lyrics.writeWhisperLyrics(song, 'tiny', result)
            song.writeLyrics(recognized['lyric'], '', recognized['yrc_lyric'])
            assert song.getStoredLyrics()['yrc_lyric'] == ''
            ctx = _FakeContext(song)
            runner = LLMToolRunner(
                cast(AppContext, ctx), allow_actions=True, require_usage=False
            )
            runner.runTool('set_current_lyrics', {'lyric': ORIGINAL})
            stored = song.getStoredLyrics()
            assert stored['lyric'] == ORIGINAL
            assert stored['translated_lyric'] == ''
            assert stored['yrc_lyric'] == ''


def main() -> None:
    checks = sorted(name for name in globals() if name.startswith('test_'))
    for name in checks:
        globals()[name]()
    print(f'test_llm_lyric_tools: {len(checks)} checks passed')


if __name__ == '__main__':
    main()
