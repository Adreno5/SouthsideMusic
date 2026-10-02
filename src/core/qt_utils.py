from functools import lru_cache
import threading

from imports import QLayout, QListWidget, QWidget

_lock = threading.Lock()


def removeWidgets(layout: QLayout) -> None:
    if layout is None:
        return

    while layout.count():
        item = layout.takeAt(0)
        if not item:
            continue
        if not hasattr(item, 'widget'):
            continue
        widget = item.widget()
        if widget is not None:
            releaseWidget(widget)
        elif item.layout() is not None:
            removeWidgets(item.layout())


def releaseWidget(widget: QWidget) -> None:
    release = getattr(widget, 'releaseResources', None)
    if release is not None:
        release()
    widget.setParent(None)
    widget.deleteLater()


def clearListWidget(list_widget: QListWidget) -> None:
    for i in range(list_widget.count()):
        item = list_widget.item(i)
        if item is None:
            continue
        widget = list_widget.itemWidget(item)
        if widget is None:
            continue
        list_widget.removeItemWidget(item)
        releaseWidget(widget)
    list_widget.clear()


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
