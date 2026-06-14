from typing import TYPE_CHECKING

from PySide6.QtGui import QCloseEvent, QHideEvent, QShowEvent

if TYPE_CHECKING:
    from core.app_context import AppContext
from services.events import event_bus
from imports import QWidget, QTimer, QVBoxLayout, QLabel
from services.events.events import COLLECT_DEBUG_INFO, EMIT_DEBUG_INFO
from views.list_widget import SScrollArea
import logging

_logger = logging.getLogger(__name__)


class DebugWindow(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle('Debugging')
        self.setFixedSize(ctx.app.primaryScreen().size() * 0.4)

        _layout = QVBoxLayout()

        self.scroll_area = SScrollArea()
        content_widget = QWidget()
        self.content_layout = QVBoxLayout()
        self.content_layout.setSpacing(40)
        content_widget.setLayout(self.content_layout)
        self.scroll_area.setWidget(content_widget)
        self.scroll_area.setWidgetResizable(True)

        _layout.addWidget(self.scroll_area)

        self.setLayout(_layout)

        self.infos: list[
            dict[str, list[str]]
        ] = []  # [ {'debug info source name': ['line1', 'line2', ...]}, ... ]

        self.content_labels: list[QLabel] = []

        self.collect_timer = QTimer(self)
        self.collect_timer.timeout.connect(self.collectInfo)

        event_bus.subscribe(EMIT_DEBUG_INFO, self.onDebugInfo)

    def showEvent(self, event: QShowEvent) -> None:
        if not self.collect_timer.isActive():
            self.collect_timer.start(0)
        return super().showEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:
        if self.collect_timer.isActive():
            self.collect_timer.stop()
        return super().hideEvent(event)

    def onDebugInfo(self, name: str, info: list[str]):
        self.infos.append({name: info})

    def collectInfo(self):
        event_bus.emit(COLLECT_DEBUG_INFO)

        while len(self.content_labels) < len(self.infos):
            label = QLabel()
            self.content_layout.addWidget(label)
            self.content_labels.append(label)

        for i, (label, info_dict) in enumerate(zip(self.content_labels, self.infos)):
            label.setText(
                f'{i + 1}. | {", ".join(info_dict.keys())} |\n'
                + '\n'.join(line for lines in info_dict.values() for line in lines)
            )
            label.show()

        for extra_idx in range(len(self.infos), len(self.content_labels)):
            self.content_labels[extra_idx].hide()

        self.infos.clear()

    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()
        self.hide()
