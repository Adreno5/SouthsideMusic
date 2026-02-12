
from functools import lru_cache
import logging
from typing import TypedDict


class LyricInfo(TypedDict):
    time: float
    content: str

class LRCLyricManager:
    def __init__(self) -> None:
        self.cur: str = ''
        self.parsed: list[LyricInfo] = []

    def getCurrentLyric(self, time: float) -> LyricInfo:
        if len(self.parsed) > 0:
            if self.parsed[0]['time'] > time:
                return LyricInfo(time=0, content='')

        for i, l in enumerate(self.parsed):
            if l['time'] > time:
                return self.parsed[max(0, i - 1)]
            
        return LyricInfo(time=0, content='')
    
    def getOffsetedLyric(self, time: float, offset_index: int) -> LyricInfo:
        if len(self.parsed) > 0:
            if self.parsed[0]['time'] > time:
                return LyricInfo(time=0, content='')

        for i, l in enumerate(self.parsed):
            if l['time'] > time:
                target_index = i - 1 + offset_index
                if target_index < 0:
                    return LyricInfo(time=0, content='')
                elif target_index >= len(self.parsed):
                    return LyricInfo(time=0, content='')
                else:
                    return self.parsed[target_index]
            
        return LyricInfo(time=0, content='')

    def parse(self):
        if not self.cur: return

        self.parsed.clear()

        for idx, line in enumerate(self.cur.splitlines()):
            if 'by' in line:
                continue
            
            try:
                info = LyricInfo(time=0, content=line.split(']')[-1])
                time_text = line.removeprefix('[').split(']')[0]
                if time_text.count(':') == 1:
                    time_m = int(time_text.split(':')[0])
                    time_s = int(time_text.split(':')[-1].split('.')[0])
                    time_ms = int(time_text.split('.')[-1])

                    info['time'] = time_m * 60 + time_s + time_ms / 1000

                    self.parsed.append(info)
                elif time_text.count(':') == 2:
                    time_m = int(time_text.split(':')[0])
                    time_s = int(time_text.split(':')[-2])
                    time_ms = int(time_text.split(':')[-1])

                    info['time'] = time_m * 60 + time_s + time_ms / 100

                    self.parsed.append(info)
            except Exception as e:
                logging.error(f'error parsing {line}: {e}')

        logging.info(f'parsed {len(self.parsed)} lines')