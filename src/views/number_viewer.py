from __future__ import annotations

from typing import Any, override

from PySide6.QtCore import QPoint, QRect, QSize, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QHideEvent,
    QImage,
    QPainter,
    QRegion,
    QShowEvent,
    QWheelEvent,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget

from core import theme
from core.app_context import AppContext
from core.smooth import EaseOutTimer
from services.events import event_bus
from services.events.events import REPAINT


class NumberViewer(QOpenGLWidget):
    def __init__(
        self,
        font: str,
        ctx: AppContext,
        point_size: int = 14,
        animation_time: float = 0.3,
        power_number: int = 3,
    ) -> None:
        super().__init__()
        surface_format = self.format()
        surface_format.setSwapInterval(0)
        surface_format.setAlphaBufferSize(8)
        surface_format.setSamples(4)
        self.setFormat(surface_format)

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

        self._bg_color = self.palette().window().color()
        self._bg_color.setAlpha(255)
        self._bg_key: tuple[object, ...] = ()
        self._known_ancestors: list[QWidget] = []
        self._color_ancestors: list[Any] = []

        for char in self.numbers:
            self.width_map[char] = self.metri.horizontalAdvance(char)

        event_bus.subscribe(REPAINT, self.updateDatas)

    def updateDatas(self, _: float) -> None:
        background_key = self._backgroundKey()
        if background_key != self._bg_key:
            self._bg_key = background_key
            if self.isVisible():
                self.update()

        for i, char in enumerate(self.cur_text):
            if not self.width_map.get(char):
                self.width_map[char] = self.metri.horizontalAdvance(char)
            if char not in self.numbers:
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

    @override
    def showEvent(self, event: QShowEvent) -> None:
        self.updateGeometry()
        return super().showEvent(event)

    @override
    def hideEvent(self, event: QHideEvent) -> None:
        self.updateGeometry()
        return super().hideEvent(event)

    def setText(self, text: str) -> None:
        self.cur_text = text

    @override
    def sizeHint(self) -> QSize:
        return QSize(int(self.width_timer.current_value), int(self.full_height))

    @override
    def paintGL(self) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._backgroundColor())
        painter.setRenderHints(
            QPainter.RenderHint.TextAntialiasing | QPainter.RenderHint.Antialiasing
        )
        painter.setFont(self.ft)

        text_top = max(0.0, (self.height() - self.full_height) / 2)
        baseline = text_top + self.metri.ascent()
        x = 0.0
        for pos, char in enumerate(self.cur_text):
            if char not in self.width_map:
                self.width_map[char] = self.metri.horizontalAdvance(char)
            width = self.width_map[char]
            painter.setClipRect(
                int(x),
                int(text_top),
                int(width),
                int(self.full_height + 1),
            )
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

    def _viewAncestors(self) -> list[QWidget]:
        ancestors: list[QWidget] = []
        widget = self.parentWidget()
        while widget is not None:
            ancestors.append(widget)
            widget = widget.parentWidget()
        return ancestors

    def _backgroundKey(self) -> tuple[object, ...]:
        window = getattr(self.ctx, 'main_window', None)
        song_theme = getattr(window, 'song_theme', None)
        config = getattr(self.ctx, 'config', None)
        ancestors = self._viewAncestors()
        if ancestors != self._known_ancestors:
            self._known_ancestors = ancestors
            self._color_ancestors = [
                widget for widget in ancestors if hasattr(widget, 'backgroundColor')
            ]
        return (
            self.size(),
            self.pos(),
            tuple(widget.size() for widget in ancestors),
            tuple(widget.backgroundColor.rgba() for widget in self._color_ancestors),
            theme.isDark(),
            song_theme.rgba() if isinstance(song_theme, QColor) else 0,
            getattr(config, 'background_ratio', 0),
        )

    def _backgroundColor(self) -> QColor:
        if self.parentWidget() is None:
            return self._bg_color

        image = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        try:
            center = self.rect().center()
            for widget in reversed(self._viewAncestors()):
                point = self.mapTo(widget, center)
                widget.render(
                    painter,
                    QPoint(),
                    QRegion(QRect(point, QSize(1, 1))),
                    QWidget.RenderFlag.DrawWindowBackground
                    if widget.isWindow()
                    else QWidget.RenderFlag(0),
                )
        except Exception:  # noqa: BLE001
            painter.end()
            return self._bg_color
        painter.end()

        color = image.pixelColor(0, 0)
        if color.alpha() == 0:
            return self._bg_color
        color.setAlpha(255)
        self._bg_color = color
        return color


class SettableNumberViewer(NumberViewer):
    valueChanged = Signal(float)

    def __init__(self, font: str, ctx: AppContext) -> None:
        super().__init__(font, ctx, 22)

    def setRange(self, min: float, max: float) -> None:
        self.min = min
        self.max = max

    def setValue(self, value: float) -> None:
        self.value = self.clamp(value)
        self.cur_text = self._format_value(self.value)

    def setSingleStep(self, step: float) -> None:
        self.step = step

    def clamp(self, value: float) -> float:
        return max(self.min, min(self.max, round(value / self.step) * self.step))

    def _decimal_places(self) -> int:
        step_str = str(self.step)
        if '.' in step_str:
            return len(step_str.split('.')[1])
        return 0

    def _format_value(self, value: float) -> str:
        return f'{value:.{self._decimal_places()}f}'

    @override
    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.angleDelta().y() > 0:
            self.value = self.clamp(self.value + self.step)
        else:
            self.value = self.clamp(self.value - self.step)
        self.cur_text = self._format_value(self.value)
        self.updateGeometry()
        self.valueChanged.emit(self.value)
        event.accept()
