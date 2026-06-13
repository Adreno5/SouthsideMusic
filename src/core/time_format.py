from dataclasses import dataclass
from functools import lru_cache
import threading

_lock = threading.Lock()


@dataclass
class TimeInfo:
    minutes: int
    seconds: int
    millionsecs: int


def float2time(time: float) -> TimeInfo:
    with _lock:
        return _float2time(time)


@lru_cache
def _float2time(time: float) -> TimeInfo:
    minutes = int(time // 60)
    seconds = int(time % 60)
    millionsecs = int(round((time - int(time)) * 1000))
    return TimeInfo(minutes=minutes, seconds=seconds, millionsecs=millionsecs)
