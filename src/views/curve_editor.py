import math
from bisect import bisect_left
from itertools import pairwise
from typing import override

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget
from qfluentwidgets import qconfig, themeColor

from core import theme
from core.color import mixColor
from core.config import cfg
from core.models import CurveInfo
from core.smooth import EaseInOutTimer, EaseOutTimer
from services.events import (
    BACKGROUND_RATIO_CHANGED,
    POST_THEME_CHANGED,
    SONG_CHANGED,
    event_bus,
)


class CurveEditor(QOpenGLWidget):
    pointsChanged = Signal(list)
    edited = Signal(CurveInfo)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        surface_format = self.format()
        surface_format.setAlphaBufferSize(8)
        surface_format.setSamples(4)
        self.setFormat(surface_format)
        self.setMinimumSize(160, 100)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._points: list[tuple[EaseOutTimer, EaseOutTimer, EaseInOutTimer]] = []
        self._selected_point = -1
        self._hovered_point = -1
        self._dragged_point = -1
        self._drag_offset = QPointF()
        self._animation_timer = QTimer(self)
        self._animation_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._animation_timer.setInterval(16)
        self._animation_timer.timeout.connect(self._repaintTick)

        self.setPoints([(0.0, 0.0), (1.0, 1.0)])
        for event in (POST_THEME_CHANGED, BACKGROUND_RATIO_CHANGED, SONG_CHANGED):
            event_bus.subscribe(event, self._onThemeChanged)
        qconfig.themeColorChanged.connect(self._onThemeChanged)

    @override
    def sizeHint(self) -> QSize:
        return QSize(400, 200)

    def getPoints(self) -> list[tuple[float, float]]:
        return [(x.target_value, y.target_value) for x, y, _ in self._points]

    def setPoints(self, points: list[tuple[float, float]]) -> None:
        values: dict[float, float] = {}
        for x, y in points:
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError('Point coordinates must be finite')
            values[max(0.0, min(1.0, x))] = max(0.0, min(1.0, y))
        ordered_points = sorted(values.items())
        if ordered_points == self.getPoints():
            return
        self._points = [self._newPoint(x, y) for x, y in ordered_points]
        self._selected_point = -1
        self._hovered_point = -1
        self._dragged_point = -1
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._startAnimation()
        self.pointsChanged.emit(self.getPoints())

    def _newPoint(
        self, x: float, y: float
    ) -> tuple[EaseOutTimer, EaseOutTimer, EaseInOutTimer]:
        x_timer = EaseOutTimer(0.12, 3)
        y_timer = EaseOutTimer(0.12, 3)
        radius_timer = EaseInOutTimer(0.18, 3)
        x_timer.target_value = x
        x_timer.current_value = x
        y_timer.target_value = y
        y_timer.current_value = y
        radius_timer.target_value = 4.5
        return x_timer, y_timer, radius_timer

    def _plotRect(self) -> QRectF:
        return QRectF(18, 18, max(1, self.width() - 36), max(1, self.height() - 36))

    def _toWidget(self, x: float, y: float) -> QPointF:
        rect = self._plotRect()
        return QPointF(
            rect.left() + x * rect.width(), rect.bottom() - y * rect.height()
        )

    def _fromWidget(self, position: QPointF) -> QPointF:
        rect = self._plotRect()
        return QPointF(
            max(0.0, min(1.0, (position.x() - rect.left()) / rect.width())),
            max(0.0, min(1.0, (rect.bottom() - position.y()) / rect.height())),
        )

    def _pointAt(self, position: QPointF) -> int:
        closest = -1
        distance = 12.0**2
        for index, (x, y, _) in enumerate(self._points):
            offset = self._toWidget(x.current_value, y.current_value) - position
            point_distance = offset.x() ** 2 + offset.y() ** 2
            if point_distance <= distance:
                closest = index
                distance = point_distance
        return closest

    def _setHoveredPoint(self, index: int) -> None:
        self._hovered_point = index
        for point_index, (_, _, radius) in enumerate(self._points):
            radius.target_value = (
                8.0
                if point_index == self._dragged_point
                else 6.5
                if point_index == index
                else 4.5
            )
        self.setCursor(
            Qt.CursorShape.ClosedHandCursor
            if self._dragged_point >= 0
            else Qt.CursorShape.OpenHandCursor
            if index >= 0
            else Qt.CursorShape.CrossCursor
        )
        self._startAnimation()

    def _movePoint(self, position: QPointF) -> None:
        index = self._dragged_point
        if index < 0:
            return
        point = self._fromWidget(position - self._drag_offset)
        left = (
            math.nextafter(self._points[index - 1][0].target_value, 1.0)
            if index > 0
            else 0.0
        )
        right = (
            math.nextafter(self._points[index + 1][0].target_value, 0.0)
            if index + 1 < len(self._points)
            else 1.0
        )
        x, y, _ = self._points[index]
        new_x = max(left, min(right, point.x()))
        if (new_x, point.y()) == (x.target_value, y.target_value):
            return
        x.target_value = new_x
        y.target_value = point.y()
        self._startAnimation()
        self.pointsChanged.emit(self.getPoints())

    def _removePoint(self, index: int) -> None:
        self._points.pop(index)
        self._selected_point = -1
        self._dragged_point = -1
        self._setHoveredPoint(-1)
        self.pointsChanged.emit(self.getPoints())

    def _startAnimation(self) -> None:
        if not self._animation_timer.isActive():
            self._animation_timer.start()
        self.update()

    def _repaintTick(self) -> None:
        self.update()
        if not any(timer.is_animating for point in self._points for timer in point):
            self._animation_timer.stop()

    def _onThemeChanged(self, *_args: object) -> None:
        self.update()

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        index = self._pointAt(event.position())
        if event.button() == Qt.MouseButton.RightButton and index >= 0:
            self._removePoint(index)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if index < 0:
            if not self._plotRect().contains(event.position()):
                self._selected_point = -1
                self.update()
                event.accept()
                return
            point = self._fromWidget(event.position())
            positions = [x.target_value for x, _, _ in self._points]
            index = bisect_left(positions, point.x())
            if index < len(positions) and positions[index] == point.x():
                self._points[index][1].target_value = point.y()
                self._points[index][1].current_value = point.y()
            else:
                self._points.insert(index, self._newPoint(point.x(), point.y()))
            self.pointsChanged.emit(self.getPoints())
        self._selected_point = index
        self._dragged_point = index
        x, y, _ = self._points[index]
        self._drag_offset = event.position() - self._toWidget(
            x.current_value, y.current_value
        )
        self._setHoveredPoint(index)
        event.accept()

    @override
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragged_point >= 0:
            self._movePoint(event.position())
        else:
            index = self._pointAt(event.position())
            if index != self._hovered_point:
                self._setHoveredPoint(index)
        event.accept()

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragged_point >= 0:
            self._movePoint(event.position())
            self._dragged_point = -1
            self._setHoveredPoint(self._pointAt(event.position()))
            event.accept()
        else:
            super().mouseReleaseEvent(event)
        self.edited.emit(CurveInfo(points=self.getPoints()))

    @override
    def leaveEvent(self, event: QEvent) -> None:
        self._setHoveredPoint(-1)
        super().leaveEvent(event)

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self._selected_point >= 0:
                self._removePoint(self._selected_point)
            event.accept()
            return
        super().keyPressEvent(event)

    def _curvePath(self, positions: list[QPointF]) -> QPainterPath:
        widths = [right.x() - left.x() for left, right in pairwise(positions)]
        slopes = [
            (right.y() - left.y()) / width if width > 0 else 0.0
            for left, right, width in zip(positions, positions[1:], widths)
        ]
        tangents = [slopes[0]]
        for index in range(1, len(positions) - 1):
            before, after = slopes[index - 1], slopes[index]
            if before * after <= 0:
                tangents.append(0.0)
            else:
                weight_before = 2 * widths[index] + widths[index - 1]
                weight_after = widths[index] + 2 * widths[index - 1]
                tangents.append(
                    (weight_before + weight_after)
                    / (weight_before / before + weight_after / after)
                )
        tangents.append(slopes[-1])
        path = QPainterPath(positions[0])
        for index, (left, right) in enumerate(pairwise(positions)):
            width = widths[index] / 3
            path.cubicTo(
                QPointF(left.x() + width, left.y() + tangents[index] * width),
                QPointF(right.x() - width, right.y() - tangents[index + 1] * width),
                right,
            )
        return path

    @override
    def paintGL(self) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        is_dark = theme.isDark()
        foreground = QColor(255, 255, 255) if is_dark else QColor(0, 0, 0)
        foreground.setAlpha(48)
        accent = themeColor()
        background = QColor(40, 40, 40) if is_dark else QColor(230, 230, 230)
        song_theme = getattr(self.window(), 'song_theme', None)
        if isinstance(song_theme, QColor):
            background = mixColor(
                background, song_theme, 1 - cfg.background_ratio * 0.5
            )
        background.setAlpha(255)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(self.rect()), 10, 10)

        rect = self._plotRect()
        grid_color = QColor(foreground)
        grid_color.setAlpha(18)
        painter.setPen(QPen(grid_color, 1))
        for division in range(5):
            x = rect.left() + rect.width() * division / 4
            y = rect.top() + rect.height() * division / 4
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

        positions = [
            self._toWidget(x.current_value, y.current_value) for x, y, _ in self._points
        ]
        ordered_positions = sorted(positions, key=lambda point: point.x())
        if len(ordered_positions) >= 2:
            path = self._curvePath(ordered_positions)
            fill_path = QPainterPath(path)
            fill_path.lineTo(ordered_positions[-1].x(), rect.bottom())
            fill_path.lineTo(ordered_positions[0].x(), rect.bottom())
            fill_path.closeSubpath()
            gradient = QLinearGradient(0, path.boundingRect().top(), 0, rect.bottom())
            gradient.setColorAt(0, foreground)
            transparent = QColor(foreground)
            transparent.setAlpha(0)
            gradient.setColorAt(1, transparent)
            painter.save()
            painter.setClipRect(rect)
            painter.setClipPath(fill_path, Qt.ClipOperation.IntersectClip)
            painter.fillRect(rect, gradient)
            painter.restore()
            painter.setPen(QPen(foreground, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        for index, (position, (_, _, radius_timer)) in enumerate(
            zip(positions, self._points)
        ):
            radius = radius_timer.current_value
            active = index in (
                self._hovered_point,
                self._selected_point,
                self._dragged_point,
            )
            if active:
                halo = QColor(accent)
                halo.setAlpha(24)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(halo)
                painter.drawEllipse(position, radius + 4, radius + 4)
            painter.setPen(QPen(accent, 1.8))
            painter.setBrush(foreground if active else background)
            painter.drawEllipse(position, radius, radius)
        painter.end()
