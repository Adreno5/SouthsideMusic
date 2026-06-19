from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.app_context import AppContext
from services.events import event_bus
from imports import QObject, QTimer, QLabel
from services.events.events import COLLECT_DEBUG_INFO, EMIT_DEBUG_INFO
import logging

_logger = logging.getLogger(__name__)

class Debugging(QObject):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        
        self.infos: list[
            dict[str, list[str]]
        ] = []  # [ {'debug info source name': ['line1', 'line2', ...]}, ... ]

        self.content_labels: list[QLabel] = []

        self.collect_timer = QTimer(self)
        self.collect_timer.timeout.connect(self.collectInfo)

        event_bus.subscribe(EMIT_DEBUG_INFO, self.onDebugInfo)

    def toggle(self):
        if not self.collect_timer.isActive():
            self.collect_timer.start(20)
            self.ctx.debugging = True
            self.collectInfo()
        else:
            self.collect_timer.stop()
            self.ctx.debugging = False

    def onDebugInfo(self, name: str, info: list[str]):
        self.infos.append({name: info})

    def collectInfo(self):
        self.infos.clear()
        event_bus.emit(COLLECT_DEBUG_INFO)
