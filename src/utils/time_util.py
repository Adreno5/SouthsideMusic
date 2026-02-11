from typing import TypedDict


class TimeInfo(TypedDict):
    minutes: int
    seconds: int
    millionsecs: int

def float2time(time: float) -> TimeInfo:
    minutes = int(time // 60)
    seconds = int(time % 60)
    millionsecs = int(round((time - int(time)) * 1000))
    return TimeInfo(minutes=minutes, seconds=seconds, millionsecs=millionsecs)