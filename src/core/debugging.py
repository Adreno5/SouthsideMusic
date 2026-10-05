from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QLabel

from core.frame_profiler import frame_profiler
from services.events import event_bus
from services.events.events import _100MS_TICK, COLLECT_DEBUG_INFO, EMIT_DEBUG_INFO

if TYPE_CHECKING:
    from core.app_context import AppContext

_logger = logging.getLogger(__name__)


class Debugging(QObject):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx

        self.infos: list[
            dict[str, list[str]]
        ] = []  # [ {'debug info source name': ['line1', 'line2', ...]}, ... ]

        self.content_labels: list[QLabel] = []
        self._collecting = False

        event_bus.subscribe(EMIT_DEBUG_INFO, self.onDebugInfo)

    def isCollecting(self) -> bool:
        return self._collecting

    def setCollecting(self, collecting: bool) -> None:
        if collecting == self._collecting:
            return
        self._collecting = collecting
        if collecting:
            event_bus.subscribe(_100MS_TICK, self.collectInfo)
        else:
            event_bus.unsubscribe(_100MS_TICK, self.collectInfo)

    def toggle(self) -> None:
        if not frame_profiler.enabled:
            self.setCollecting(True)
            self.ctx.debugging = True
            frame_profiler.setEnabled(True)
            self.collectInfo()
        else:
            self.setCollecting(False)
            self.ctx.debugging = False
            frame_profiler.setEnabled(False)

    def onDebugInfo(self, name: str, info: list[str]):
        self.infos.append({name: info})

    def collectInfo(self):
        self.infos.clear()
        event_bus.emit(COLLECT_DEBUG_INFO)
