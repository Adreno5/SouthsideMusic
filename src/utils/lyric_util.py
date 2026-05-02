from functools import lru_cache
import json
import logging
import re
from typing import TypedDict


class LyricInfo(TypedDict):
    time: float
    content: str


_LRC_TIME_RE = re.compile(r"^\[(\d+):(\d+)[.:](\d+)\]")


def _try_parse_lrc_line(line: str) -> LyricInfo | None:
    m = _LRC_TIME_RE.match(line)
    if not m:
        return None
    minutes = int(m.group(1))
    seconds = int(m.group(2))
    ms_raw = m.group(3).ljust(3, "0")[:3]
    ms = int(ms_raw)
    time = minutes * 60 + seconds + ms / 1000
    content = line[m.end() :]
    if not content:
        return None
    return LyricInfo(time=time, content=content)


def _is_metadata_tag(line: str) -> bool:
    return bool(re.match(r"^\[(?:by|ar|al|ti|offset|length|re|ve):", line))


def _is_json_metadata(line: str) -> bool:
    if not line.startswith("{"):
        return False
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return False
    return isinstance(obj, dict) and "t" in obj and "c" in obj


class LRCLyricParser:
    def __init__(self) -> None:
        self.cur: str = ""
        self.parsed: list[LyricInfo] = []

    def getCurrentLyric(self, time: float) -> LyricInfo:
        return self._getCurrentLyric(time)
    
    @lru_cache
    def _getCurrentLyric(self, time: float) -> LyricInfo:
        if not self.parsed:
            return LyricInfo(time=0, content="")

        if self.parsed[0]["time"] > time:
            return LyricInfo(time=0, content="")

        for i, l in enumerate(self.parsed):
            if l["time"] > time:
                return self.parsed[i - 1]

        return self.parsed[-1]

    def getOffsetedLyric(self, time: float, offset_index: int) -> LyricInfo:
        return self._getOffsetedLyric(time, offset_index)

    @lru_cache
    def _getOffsetedLyric(self, time: float, offset_index: int) -> LyricInfo:
        if not self.parsed:
            return LyricInfo(time=0, content="")

        if self.parsed[0]["time"] > time:
            return LyricInfo(time=0, content="")

        for i, l in enumerate(self.parsed):
            if l["time"] > time:
                target_index = i - 1 + offset_index
                if target_index < 0 or target_index >= len(self.parsed):
                    return LyricInfo(time=0, content="")
                return self.parsed[target_index]

        return LyricInfo(time=0, content="")

    def parse(self) -> None:
        if not self.cur:
            return
        
        self._getOffsetedLyric.cache_clear()
        self._getCurrentLyric.cache_clear()

        self.parsed.clear()

        for line in self.cur.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            if _is_metadata_tag(stripped):
                continue

            if _is_json_metadata(stripped):
                continue

            info = _try_parse_lrc_line(stripped)
            if info is not None:
                self.parsed.append(info)

        self.parsed.sort(key=lambda x: x["time"])
        logging.info(f"parsed {len(self.parsed)} lines")
