
from functools import lru_cache
import threading

from imports import * # type: ignore

_lock = threading.Lock()

def remove_widgets(layout: QLayout) -> None:
    if layout is None:
        return
        
    for w in layout.findChildren(QWidget):
        w.deleteLater()

def toQtInt(value: float | int) -> int:
    with _lock:
        return _toQtInt(value)

@lru_cache
def _toQtInt(value: float | int) -> int:
    import math as _math
    from functools import lru_cache as _lru_cache

    _QT_INT_MIN = -(2**31)
    _QT_INT_MAX = 2**31 - 1

    if not _math.isfinite(value):
        return 0
    return max(_QT_INT_MIN, min(_QT_INT_MAX, int(value)))