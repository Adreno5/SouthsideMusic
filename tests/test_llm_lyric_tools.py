from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core.app_context import AppContext  # noqa: E402
from core.llm import ONERAD_SYSTEM_PROMPT, TOOL_USAGE  # noqa: E402
from core.llm_tools import KNOWN_TOOLS, LLMToolRunner, llmToolSchemas  # noqa: E402
from core.lyrics import LRCLyricParser, YRCLyricParser  # noqa: E402
from core.models import ArtistInfo, SongInfo, SongStorable  # noqa: E402
from services.events.event_bus import event_bus  # noqa: E402
from services.events.events import PLAYBACK_LYRICS_UPDATED  # noqa: E402

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
    def __init__(self, song: SongStorable) -> None:
        self.current_song = song
        self.playlist: list[SongStorable] = []


class _FakeContext:
    def __init__(self, song: SongStorable) -> None:
        self.playing_manager = _FakePlayingManager(song)
        self.mgr = LRCLyricParser()
        self.ymgr = YRCLyricParser()
        self.transmgr = LRCLyricParser()
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
    event_bus.subscribe(PLAYBACK_LYRICS_UPDATED, listener)
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
        event_bus.unsubscribe(PLAYBACK_LYRICS_UPDATED, listener)
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


def main() -> None:
    checks = sorted(name for name in globals() if name.startswith('test_'))
    for name in checks:
        globals()[name]()
    print(f'test_llm_lyric_tools: {len(checks)} checks passed')


if __name__ == '__main__':
    main()
