from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core.lyrics import LRCLyricParser  # noqa: E402
from views.lyrics_viewer import LyricsViewer  # noqa: E402


class TranslationLookup:
    _TRANSLATION_TIME_TOLERANCE = LyricsViewer._TRANSLATION_TIME_TOLERANCE
    _timeKey = LyricsViewer._timeKey
    _timesClose = LyricsViewer._timesClose
    _translationLookupKey = LyricsViewer._translationLookupKey
    _ensureTranslationLookup = LyricsViewer._ensureTranslationLookup
    _translationTimeForLine = LyricsViewer._translationTimeForLine
    _translationTextForLine = LyricsViewer._translationTextForLine

    def __init__(self, original: str, translated: str) -> None:
        self._mgr = LRCLyricParser()
        self._mgr.cur = original
        self._mgr.parse()
        self._transmgr = LRCLyricParser()
        self._transmgr.cur = translated
        self._transmgr.parse()
        self._translation_lookup_key: tuple[int, int] | None = None
        self._translation_by_time: dict[int, str] = {}
        self._shifted_translation_by_time: dict[int, str] = {}
        self._translation_timing_shifted = False


def test_translation_with_three_leading_empty_timestamps() -> None:
    original = (
        '[00:01.000]Show me love\n'
        "[00:02.000]Don't need no money\n"
        "[00:03.000]Don't need nobody\n"
        '[00:04.000]Just need your body\n'
        '[00:05.000]Show me love\n'
        '[00:06.000]So sweet like honey\n'
    )
    translated = (
        '[00:01.000]\n'
        '[00:02.000]\n'
        '[00:03.000]\n'
        '[00:04.000]向我展示爱\n'
        '[00:05.000]不需要金钱\n'
        '[00:06.000]不需要他人\n'
    )
    lookup = TranslationLookup(original, translated)

    assert [lookup._translationTextForLine(line) for line in lookup._mgr.parsed] == [
        '向我展示爱',
        '不需要金钱',
        '不需要他人',
        '',
        '',
        '',
    ]

    lookup._transmgr.cur = translated.replace('[00:02.000]\n', '')
    lookup._transmgr.parse()
    assert lookup._translationTextForLine(lookup._mgr.parsed[0]) == ''
    assert lookup._translationTextForLine(lookup._mgr.parsed[3]) == '向我展示爱'
