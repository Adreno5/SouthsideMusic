from PySide6.QtCore import QSize
from PySide6.QtGui import QHideEvent, QPaintEvent, QShowEvent, QWheelEvent

from core.app_context import AppContext
from core.smooth import EaseOutTimer
from imports import QTimer, QWidget, QFont, QPainter, QFontMetricsF, Signal
from services.events import event_bus
from services.events.events import REPAINT


class NumberViewer(QWidget):
    def __init__(
        self,
        font: str,
        ctx: AppContext,
        point_size=14,
        animation_time: float = 0.3,
        power_number: int = 3,
    ):
        super().__init__()
        self.ft = QFont(font, point_size)
        self.metri = QFontMetricsF(self.ft)
        self.ctx = ctx

        self.animation_time = animation_time
        self.power_number = power_number

        self.cur_text: str = ''
        self.numbers = '1234567890'
        self.y_map: dict[int, EaseOutTimer] = {}
        self.width_map: dict[str, float] = {}
        self.full_height = self.metri.ascent() + self.metri.descent()

        self.width_timer = EaseOutTimer(0.3, 2)

        for char in self.numbers:
            self.width_map[char] = self.metri.horizontalAdvance(char)

        event_bus.subscribe(REPAINT, self.updateDatas)

    def updateDatas(self, _):
        for i, char in enumerate(self.cur_text):
            if not self.width_map.get(char):
                self.width_map[char] = self.metri.horizontalAdvance(char)
            if not char in self.numbers:
                continue
            digit = int(char)
            if not self.y_map.get(i):
                self.y_map[i] = EaseOutTimer(self.animation_time, self.power_number)
            self.y_map[i].target_value = self.full_height * digit

        self.width_timer.target_value = self.metri.horizontalAdvance(self.cur_text)

        if self.width_timer.is_animating:
            self.updateGeometry()

        if all(not timer.is_animating for timer in self.y_map.values()):
            return

        if self.isVisible():
            self.update()

    def showEvent(self, event: QShowEvent) -> None:
        self.updateGeometry()
        return super().showEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:
        self.updateGeometry()
        return super().hideEvent(event)

    def setText(self, text: str):
        self.cur_text = text

    def sizeHint(self) -> QSize:
        return QSize(int(self.width_timer.current_value), int(self.full_height))

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.TextAntialiasing | QPainter.RenderHint.Antialiasing
        )
        painter.setFont(self.ft)

        baseline = self.metri.ascent()
        x = 0
        for pos, char in enumerate(self.cur_text):
            if char not in self.width_map:
                self.width_map[char] = self.metri.horizontalAdvance(char)
            width = self.width_map[char]
            painter.setClipRect(int(x), 0, int(width), self.height())
            if char not in self.numbers:
                painter.drawText(int(x), int(baseline), char)
                x += width
                continue
            if pos not in self.y_map:
                self.y_map[pos] = EaseOutTimer(0.3, 3)
            for digit in range(10):
                painter.drawText(
                    int(x),
                    int(
                        baseline
                        + self.y_map[pos].current_value
                        + -digit * self.full_height
                    ),
                    str(digit),
                )
            x += width

        painter.end()


class SettableNumberViewer(NumberViewer):
    valueChanged = Signal(float)

    def __init__(self, font: str, ctx: AppContext):
        super().__init__(font, ctx, 22)

    def setRange(self, min, max):
        self.min = min
        self.max = max

    def setValue(self, value):
        self.value = self.clamp(value)
        self.cur_text = self._format_value(self.value)

    def setSingleStep(self, step):
        self.step = step

    def clamp(self, value):
        return max(self.min, min(self.max, round(value / self.step) * self.step))

    def _decimal_places(self) -> int:
        step_str = str(self.step)
        if '.' in step_str:
            return len(step_str.split('.')[1])
        return 0

    def _format_value(self, value: float) -> str:
        return f'{value:.{self._decimal_places()}f}'

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.angleDelta().y() > 0:
            self.value = self.clamp(self.value + self.step)
        else:
            self.value = self.clamp(self.value - self.step)
        self.cur_text = self._format_value(self.value)
        self.updateGeometry()
        self.valueChanged.emit(self.value)
        event.accept()
