from __future__ import annotations

import json
import logging
import re
from bisect import bisect_right
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.models import SongStorable


@dataclass
class LyricInfo:
    time: float
    content: str
    isMetadata: bool = False
    track_index: int = -1
    song_time: float = 0.0
    translation: str = ''


@dataclass
class YRCCharInfo:
    start: float
    duration: float
    char: str


@dataclass
class YRCLyricInfo:
    time: float
    duration: float
    content: str
    chars: list[YRCCharInfo] = field(default_factory=list)
    isMetadata: bool = False
    track_index: int = -1
    song_time: float = 0.0
    translation: str = ''


_LRC_TIME_RE = re.compile(r'^\[(\d+):(\d+)[.:](\d+)\]')


def _try_parse_lrc_line(line: str) -> LyricInfo | None:
    m = _LRC_TIME_RE.match(line)
    if not m:
        return None
    minutes = int(m.group(1))
    seconds = int(m.group(2))
    ms_raw = m.group(3).ljust(3, '0')[:3]
    ms = int(ms_raw)
    time = minutes * 60 + seconds + ms / 1000
    content = line[m.end() :]
    if not content:
        return None
    return LyricInfo(time=time, content=content)


def _is_metadata_tag(line: str) -> bool:
    return bool(re.match(r'^\[(?:by|ar|al|ti|offset|length|re|ve):', line))


def _is_json_metadata(line: str) -> bool:
    if not line.startswith('{'):
        return False
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return False
    return isinstance(obj, dict) and 't' in obj and 'c' in obj


def _try_parse_json_metadata_line(line: str) -> LyricInfo | None:
    if not line.startswith('{'):
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or 't' not in obj or 'c' not in obj:
        return None
    cells = obj.get('c')
    if not isinstance(cells, list):
        return None
    content = ''.join(
        cell.get('tx', '') for cell in cells if isinstance(cell, dict)
    ).strip()
    if not content:
        return None
    content = content.replace(': ', '：').replace(':', '：')
    return LyricInfo(time=float(obj['t']) / 1000, content=content, isMetadata=True)


_YRC_LINE_RE = re.compile(r'^\[(\d+),(\d+)\](.*)$')

_YRC_CHAR_RE = re.compile(r'\((\d+),(\d+),(-?\d+)\)([^()]*)')


def _try_parse_yrc_line(line: str) -> YRCLyricInfo | None:
    m = _YRC_LINE_RE.match(line)
    if not m:
        return None
    line_start = int(m.group(1)) / 1000
    line_duration = int(m.group(2)) / 1000
    chars_part = m.group(3)

    chars: list[YRCCharInfo] = []
    content_builder: list[str] = []
    for cm in _YRC_CHAR_RE.finditer(chars_part):
        ch_start = int(cm.group(1)) / 1000
        ch_duration = int(cm.group(2)) / 1000
        ch_text = cm.group(4)
        if not ch_text:
            continue
        content_builder.append(ch_text)
        chars.append(YRCCharInfo(start=ch_start, duration=ch_duration, char=ch_text))

    content = ''.join(content_builder)
    if not content or not chars:
        return None
    return YRCLyricInfo(
        time=line_start, duration=line_duration, content=content, chars=chars
    )


class YRCLyricParser:
    def __init__(self) -> None:
        self._logger = logging.getLogger(__name__)
        self.cur: str = ''
        self.parsed: list[YRCLyricInfo] = []
        self._has_yrc_timing = False

    def hasYrcTiming(self) -> bool:
        return self._has_yrc_timing

    def setParsed(
        self,
        parsed: list[YRCLyricInfo],
        cur: str | None = None,
        has_yrc_timing: bool | None = None,
    ) -> None:
        self._getOffsetedLyric.cache_clear()
        self._getCurrentLyric.cache_clear()
        self._getCurrentLyricIndex.cache_clear()
        if cur is not None:
            self.cur = cur
        self.parsed = sorted(parsed, key=lambda x: x.time)
        self._has_yrc_timing = (
            any(line.chars for line in self.parsed)
            if has_yrc_timing is None
            else has_yrc_timing
        )

    def getCurrentLyric(self, time: float) -> YRCLyricInfo:
        return self._getCurrentLyric(time)

    @lru_cache
    def _getCurrentLyric(self, time: float) -> YRCLyricInfo:
        if not self.parsed:
            return YRCLyricInfo(time=0, duration=0, content='', chars=[])

        if self.parsed[0].time > time:
            return YRCLyricInfo(time=0, duration=0, content='', chars=[])

        for i, line in enumerate(self.parsed):
            if line.time > time:
                return self.parsed[i - 1]

        return self.parsed[-1]

    def getOffsetedLyric(self, time: float, offset_index: int) -> YRCLyricInfo:
        return self._getOffsetedLyric(time, offset_index)

    @lru_cache
    def _getOffsetedLyric(self, time: float, offset_index: int) -> YRCLyricInfo:
        if not self.parsed:
            return YRCLyricInfo(time=0, duration=0, content='', chars=[])

        if self.parsed[0].time > time:
            return YRCLyricInfo(time=0, duration=0, content='', chars=[])

        for i, line in enumerate(self.parsed):
            if line.time > time:
                target_index = i - 1 + offset_index
                if target_index < 0 or target_index >= len(self.parsed):
                    return YRCLyricInfo(time=0, duration=0, content='', chars=[])
                return self.parsed[target_index]

        return YRCLyricInfo(time=0, duration=0, content='', chars=[])

    def getCurrentIndex(self, time: float) -> int:
        return self._getCurrentLyricIndex(time)

    @lru_cache
    def _getCurrentLyricIndex(self, time: float) -> int:
        if not self.parsed:
            return -1

        if self.parsed[0].time > time:
            return -1

        for i, line in enumerate(self.parsed):
            if line.time > time:
                return i - 1

        return len(self.parsed) - 1

    def parse(self) -> None:
        self._getOffsetedLyric.cache_clear()
        self._getCurrentLyric.cache_clear()
        self._getCurrentLyricIndex.cache_clear()

        parsed: list[YRCLyricInfo] = []
        has_yrc_timing = False

        if not self.cur:
            self.parsed = parsed
            self._has_yrc_timing = has_yrc_timing
            return

        for line in self.cur.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            if _is_metadata_tag(stripped):
                continue

            metadata = _try_parse_json_metadata_line(stripped)
            if metadata is not None:
                parsed.append(
                    YRCLyricInfo(
                        time=metadata.time,
                        duration=0,
                        content=metadata.content,
                        chars=[],
                        isMetadata=True,
                    )
                )
                continue

            info = _try_parse_yrc_line(stripped)
            if info is not None:
                parsed.append(info)
                if info.chars:
                    has_yrc_timing = True

        parsed.sort(key=lambda x: x.time)
        self.parsed = parsed
        self._has_yrc_timing = has_yrc_timing
        self._logger.info(f'parsed {len(parsed)} YRC lines')


class LRCLyricParser:
    def __init__(self) -> None:
        self._logger = logging.getLogger(__name__)
        self.cur: str = ''
        self.parsed: list[LyricInfo] = []
        self.empty_times: list[float] = []
        self.version: int = 0

    def setParsed(
        self,
        parsed: list[LyricInfo],
        cur: str | None = None,
        empty_times: list[float] | None = None,
    ) -> None:
        self._getOffsetedLyric.cache_clear()
        self._getCurrentLyric.cache_clear()
        self._getCurrentLyricIndex.cache_clear()
        if cur is not None:
            self.cur = cur
        self.parsed = sorted(parsed, key=lambda x: x.time)
        self.empty_times = list(empty_times or [])
        self.version += 1

    def getCurrentLyric(self, time: float) -> LyricInfo:
        return self._getCurrentLyric(time)

    @lru_cache
    def _getCurrentLyric(self, time: float) -> LyricInfo:
        if not self.parsed:
            return LyricInfo(time=0, content='')

        if self.parsed[0].time > time:
            return LyricInfo(time=0, content='')

        for i, line in enumerate(self.parsed):
            if line.time > time:
                return self.parsed[i - 1]

        return self.parsed[-1]

    def getOffsetedLyric(self, time: float, offset_index: int) -> LyricInfo:
        return self._getOffsetedLyric(time, offset_index)

    @lru_cache
    def _getOffsetedLyric(self, time: float, offset_index: int) -> LyricInfo:
        if not self.parsed:
            return LyricInfo(time=0, content='')

        if self.parsed[0].time > time:
            return LyricInfo(time=0, content='')

        for i, line in enumerate(self.parsed):
            if line.time > time:
                target_index = i - 1 + offset_index
                if target_index < 0 or target_index >= len(self.parsed):
                    return LyricInfo(time=0, content='')
                return self.parsed[target_index]

        return LyricInfo(time=0, content='')

    def getCurrentIndex(self, time: float) -> int:
        return self._getCurrentLyricIndex(time)

    @lru_cache
    def _getCurrentLyricIndex(self, time: float) -> int:
        if not self.parsed:
            return -1

        if self.parsed[0].time > time:
            return -1

        for i, line in enumerate(self.parsed):
            if line.time > time:
                return i - 1

        return len(self.parsed) - 1

    def parse(self) -> None:
        self._getOffsetedLyric.cache_clear()
        self._getCurrentLyric.cache_clear()
        self._getCurrentLyricIndex.cache_clear()

        parsed: list[LyricInfo] = []
        empty_times: list[float] = []
        self.version += 1

        if not self.cur:
            self.parsed = parsed
            self.empty_times = empty_times
            return

        for line in self.cur.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            if _is_metadata_tag(stripped):
                continue

            if _is_json_metadata(stripped):
                continue

            m = _LRC_TIME_RE.match(stripped)
            if m and not stripped[m.end() :].strip():
                minutes = int(m.group(1))
                seconds = int(m.group(2))
                ms_raw = m.group(3).ljust(3, '0')[:3]
                ms = int(ms_raw)
                empty_times.append(minutes * 60 + seconds + ms / 1000)
                continue

            info = _try_parse_lrc_line(stripped)
            if info is not None:
                parsed.append(info)

        parsed.sort(key=lambda x: x.time)
        self.parsed = parsed
        self.empty_times = empty_times
        self._logger.info(f'parsed {len(parsed)} lines')


@dataclass
class LyricTrack:
    song: SongStorable
    duration: float
    lines: list[LyricInfo | YRCLyricInfo] = field(default_factory=list)
    offset: float = 0.0
    start_index: int = 0
    end_index: int = 0


class LyricManager:
    def __init__(self) -> None:
        self.lrc = LRCLyricParser()
        self.yrc = YRCLyricParser()
        self.translation = LRCLyricParser()
        self.tracks: list[LyricTrack] = []
        self.current_track = -1
        self.parsed: list[LyricInfo | YRCLyricInfo] = []
        self._times: list[float] = []

    def startSong(
        self,
        song: SongStorable,
        duration: float,
        continuous: bool,
        restart: bool = False,
    ) -> None:
        if not continuous:
            self.tracks.clear()
            self.current_track = -1
        if (
            restart
            and self.current_track >= 0
            and self.tracks[self.current_track].song.id == song.id
        ):
            self.tracks[self.current_track].duration = duration
        elif (
            self.current_track + 1 < len(self.tracks)
            and self.tracks[self.current_track + 1].song.id == song.id
        ):
            self.current_track += 1
            self.tracks[self.current_track].song = song
            self.tracks[self.current_track].duration = duration
        else:
            del self.tracks[self.current_track + 1 :]
            self.tracks.append(LyricTrack(song, duration))
            self.current_track = len(self.tracks) - 1
        self._rebuild()

    def applyLyrics(
        self, lyric: str, yrc_lyric: str, translated_lyric: str, duration: float
    ) -> None:
        self.lrc.cur = lyric or yrc_lyric
        self.yrc.cur = yrc_lyric
        self.translation.cur = translated_lyric
        self.lrc.parse()
        self.yrc.parse()
        self.translation.parse()
        if self.current_track < 0:
            return
        track = self.tracks[self.current_track]
        track.duration = duration
        track.lines = self._mixedLines(self.lrc, self.yrc, self.translation)
        self._rebuild()

    def syncParsed(self) -> None:
        if self.current_track < 0:
            return
        self.tracks[self.current_track].lines = self._mixedLines(
            self.lrc, self.yrc, self.translation
        )
        self._rebuild()

    def setPreview(
        self, song: SongStorable | None, duration: float, lyrics: dict[str, str]
    ) -> None:
        del self.tracks[self.current_track + 1 :]
        if song is not None and self.current_track >= 0:
            lrc = LRCLyricParser()
            yrc = YRCLyricParser()
            translation = LRCLyricParser()
            lrc.cur = lyrics['lyric'] or lyrics['yrc_lyric']
            yrc.cur = lyrics['yrc_lyric']
            translation.cur = lyrics['translated_lyric']
            lrc.parse()
            yrc.parse()
            translation.parse()
            self.tracks.append(
                LyricTrack(song, duration, self._mixedLines(lrc, yrc, translation))
            )
        self._rebuild()

    def timelinePosition(self, position: float) -> float:
        if self.current_track < 0:
            return position
        return self.tracks[self.current_track].offset + position

    def getCurrentIndex(self, position: float) -> int:
        if self.current_track < 0:
            return -1
        track = self.tracks[self.current_track]
        return max(
            track.start_index,
            bisect_right(self._times, position, track.start_index, track.end_index) - 1,
        )

    def _mixedLines(
        self, lrc: LRCLyricParser, yrc: YRCLyricParser, translation: LRCLyricParser
    ) -> list[LyricInfo | YRCLyricInfo]:
        lines: list[LyricInfo | YRCLyricInfo] = (
            list(yrc.parsed) if yrc.hasYrcTiming() else list(lrc.parsed)
        )
        for text in (lrc.cur, yrc.cur):
            for raw_line in text.splitlines():
                metadata = _try_parse_json_metadata_line(raw_line.strip())
                if metadata is not None and not any(
                    line.isMetadata
                    and line.time == metadata.time
                    and line.content == metadata.content
                    for line in lines
                ):
                    lines.append(metadata)
        original_lines = [line for line in lrc.parsed if not line.isMetadata]
        translated_lines = [line for line in translation.parsed if not line.isMetadata]
        missing = len(original_lines) - len(translated_lines)
        shifted = (
            bool(translated_lines)
            and missing > 0
            and all(
                any(abs(empty - line.time) <= 0.02 for empty in translation.empty_times)
                for line in original_lines[:missing]
            )
            and all(
                abs(original.time - translated.time) <= 0.02
                for original, translated in zip(
                    original_lines[missing:], translated_lines
                )
            )
        )
        translated = (
            {
                round(original.time * 1000): translated.content
                for original, translated in zip(original_lines, translated_lines)
            }
            if shifted
            else {round(line.time * 1000): line.content for line in translated_lines}
        )
        for i, line in enumerate(lines):
            text = translated.get(round(line.time * 1000), '')
            if not text and not shifted:
                text = next(
                    (
                        translated.content
                        for translated in translated_lines
                        if abs(translated.time - line.time) <= 0.02
                    ),
                    '',
                )
            if not text and isinstance(line, YRCLyricInfo):
                original = lrc.getCurrentLyric(line.time)
                text = translated.get(round(original.time * 1000), '')
            lines[i] = replace(line, translation='' if line.isMetadata else text)
        return sorted(lines, key=lambda line: line.time)

    def _rebuild(self) -> None:
        self.parsed = []
        offset = 0.0
        for index, track in enumerate(self.tracks):
            track.offset = offset
            track.start_index = len(self.parsed)
            artists = '、'.join(artist.name for artist in track.song.artists)
            title = f'{track.song.name} - {artists}' if artists else track.song.name
            self.parsed.append(
                LyricInfo(offset, title, isMetadata=True, track_index=index)
            )
            for line in track.lines:
                shifted = replace(
                    line,
                    time=line.time + offset,
                    song_time=line.time,
                    track_index=index,
                )
                if isinstance(shifted, YRCLyricInfo):
                    shifted.chars = [
                        replace(char, start=char.start + offset)
                        for char in shifted.chars
                    ]
                self.parsed.append(shifted)
            track.end_index = len(self.parsed)
            offset += max(
                track.duration,
                max(
                    (
                        line.time + getattr(line, 'duration', 0.0)
                        for line in track.lines
                    ),
                    default=0.0,
                ),
                0.001,
            )
        self._times = [line.time for line in self.parsed]
