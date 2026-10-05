from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QLabel

from core.frame_profiler import frame_profiler
from services.events import event_bus
from services.events.events import (
    _100MS_TICK,
    COLLECT_DEBUG_INFO,
    EMIT_DEBUG_INFO,
    SECOND_TICK,
)

if TYPE_CHECKING:
    from core.app_context import AppContext


class Debugging(QObject):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx

        self.infos: list[dict[str, list[str]]] = []

        self.content_labels: list[QLabel] = []
        self._collecting = False
        self._enabled = False
        self._sources = (
            ctx.player.emitDebugInfo,
            ctx.playing_manager.emitDebugInfo,
            ctx.ws_server.emitDebugInfo,
            ctx.ws_handler.emitDebugInfo,
            ctx.desktop_lyrics_page.viewer.emitDebugInfo,
            ctx.main_window.controller.emitDebugInfo,
        )

    def setEnabled(self, enabled: bool) -> None:
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if enabled:
            event_bus.subscribe(EMIT_DEBUG_INFO, self.onDebugInfo)
            for source in self._sources:
                event_bus.subscribe(COLLECT_DEBUG_INFO, source)
            event_bus.subscribe(SECOND_TICK, self.ctx.events_service.collectPids)
        else:
            self.setCollecting(False)
            self.ctx.debugging = False
            frame_profiler.setEnabled(False)
            event_bus.unsubscribe(EMIT_DEBUG_INFO, self.onDebugInfo)
            for source in self._sources:
                event_bus.unsubscribe(COLLECT_DEBUG_INFO, source)
            event_bus.unsubscribe(SECOND_TICK, self.ctx.events_service.collectPids)
            self.infos.clear()

    def isCollecting(self) -> bool:
        return self._collecting

    def setCollecting(self, collecting: bool) -> None:
        collecting = collecting and self._enabled
        if collecting == self._collecting:
            return
        self._collecting = collecting
        if collecting:
            event_bus.subscribe(_100MS_TICK, self.collectInfo)
        else:
            event_bus.unsubscribe(_100MS_TICK, self.collectInfo)

    def toggle(self) -> None:
        if not self._enabled or not self.ctx.config.debug_mode:
            return
        if not frame_profiler.enabled:
            self.setCollecting(True)
            self.ctx.debugging = True
            frame_profiler.setEnabled(True)
            self.collectInfo()
        else:
            self.setCollecting(False)
            self.ctx.debugging = False
            frame_profiler.setEnabled(False)

    def onDebugInfo(self, name: str, info: list[str]) -> None:
        if self._collecting:
            self.infos.append({name: info})

    def collectInfo(self) -> None:
        if not self._collecting:
            return
        self.infos.clear()
        event_bus.emit(COLLECT_DEBUG_INFO)
