from functools import lru_cache
import threading

from imports import *  # type: ignore

_lock = threading.Lock()


def removeWidgets(layout: QLayout) -> None:
    if layout is None:
        return

    while layout.count():
        item = layout.takeAt(0)
        if not item:
            continue
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        elif item.layout() is not None:
            removeWidgets(item.layout())

def toQtInt(value: float | int) -> int:
    with _lock:
        return _toQtInt(value)


@lru_cache
def _toQtInt(value: float | int) -> int:
    import math as _math

    _QT_INT_MIN = -(2**31)
    _QT_INT_MAX = 2**31 - 1

    if not _math.isfinite(value):
        return 0
    return max(_QT_INT_MIN, min(_QT_INT_MAX, int(value)))
