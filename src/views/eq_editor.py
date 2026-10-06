import math
from collections import deque
from typing import override

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QResizeEvent,
)
from PySide6.QtWidgets import QWidget
from qfluentwidgets import themeColor

from core import theme
from core.audio_processing import sampleEqualizerCurve
from core.color import mixColor
from core.config import DEFAULT_EQ_BANDS, cfg
from core.smooth import EaseInOutTimer, EaseOutTimer
from services.events import PLAY_STATE_CHANGED, REPAINT, SONG_CHANGED, event_bus
from views.curve_editor import CurveEditor


class EQEditor(CurveEditor):
    bandsChanged = Signal(list)

    MIN_FREQUENCY = 20.0
    MAX_FREQUENCY = 20000.0
    MIN_GAIN = -20.0
    MAX_GAIN = 20.0
    GAIN_TICKS = (-20.0, -10.0, 0.0, 10.0, 20.0)
    PLOT_LEFT = 40.0
    PLOT_TOP = 14.0
    PLOT_RIGHT = 14.0
    LABEL_GAP = 3.0
    LABEL_ROW_GAP = 1.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fft_frequencies = np.geomspace(
            self.MIN_FREQUENCY, self.MAX_FREQUENCY, 768
        )
        self._fft_frequency_edges = np.concatenate((
            [self.MIN_FREQUENCY],
            np.sqrt(self._fft_frequencies[:-1] * self._fft_frequencies[1:]),
            [self.MAX_FREQUENCY],
        ))
        self._fft_bin_edges = np.zeros(len(self._fft_frequency_edges), dtype=int)
        self._fft_levels = np.zeros((2, len(self._fft_frequencies)))
        self._fft_bin_frequencies = np.empty(0)
        self._fft_magnitudes = np.empty((2, 0))
        self._fft_smoothed = np.empty((2, 0))
        self._fft_draw = np.zeros_like(self._fft_levels)
        self._fft_weights = np.empty(0)
        refresh_rate = max(60.0, self.screen().refreshRate() / 2)
        self._fft_norm_buffer: deque[float] = deque(
            maxlen=max(1, int(refresh_rate * cfg.fft_buffer_seconds))
        )
        self._fft_norm_timer = EaseOutTimer(0.5, 2)
        self._inset_timer = EaseInOutTimer(0.25, 3)
        self._inset_timer.target_value = self._targetInset()
        self._inset_timer.current_value = self._targetInset()
        self._inset_animation = QTimer(self)
        self._inset_animation.setTimerType(Qt.TimerType.PreciseTimer)
        self._inset_animation.setInterval(16)
        self._inset_animation.timeout.connect(self._updateInsetTick)
        self.pointsChanged.connect(self._emitBands)
        self.pointsChanged.connect(self._updateInset)
        self.setBands(list(DEFAULT_EQ_BANDS))
        event_bus.subscribe(PLAY_STATE_CHANGED, self._resetFFT)
        event_bus.subscribe(SONG_CHANGED, self._resetFFT)
        event_bus.subscribe(REPAINT, self._updateFFT)

    def updateFFTData(
        self,
        frequencies: np.ndarray,
        original_magnitudes: np.ndarray,
        processed_magnitudes: np.ndarray,
    ) -> None:
        if not self.isVisible() or len(frequencies) < 2:
            return
        if len(frequencies) != self._fft_smoothed.shape[1]:
            self._resetFFT()
            self._fft_smoothed = np.zeros((2, len(frequencies)))
            self._fft_weights = (
                np.abs(np.arange(len(frequencies)) - int(len(frequencies) * 0.015))
                + 1.0
            ) * 1.05
        self._fft_bin_frequencies = frequencies
        self._fft_bin_edges = np.searchsorted(frequencies, self._fft_frequency_edges)
        self._fft_magnitudes = np.stack((original_magnitudes, processed_magnitudes))

    def _updateFFT(self, multiple_factor: float = 1.0) -> None:
        if not self.isVisible() or self._fft_magnitudes.size == 0:
            return
        if (
            not np.any(self._fft_magnitudes)
            and not np.any(self._fft_smoothed > 0.0001)
            and not np.any(self._fft_draw > 0.001)
        ):
            if np.any(self._fft_levels):
                self._fft_levels.fill(0.0)
                self.update()
            return
        self._fft_smoothed += (self._fft_magnitudes - self._fft_smoothed) * (
            1 - pow(1 - cfg.fft_factor, multiple_factor)
        )
        window_size = int(cfg.fft_filtering_windowsize)
        kernel = np.ones(window_size) / window_size
        final_magnitudes = np.stack([
            np.convolve(magnitudes, kernel, mode='same')
            for magnitudes in self._fft_smoothed
        ])
        final_magnitudes *= self._fft_weights
        display_magnitudes = np.stack([
            np.interp(
                self._fft_frequencies,
                self._fft_bin_frequencies,
                magnitudes,
                left=0.0,
                right=0.0,
            )
            for magnitudes in final_magnitudes
        ])
        occupied = np.diff(self._fft_bin_edges) > 0
        if np.any(occupied):
            display_magnitudes[:, occupied] = np.maximum.reduceat(
                final_magnitudes[:, : self._fft_bin_edges[-1]],
                self._fft_bin_edges[:-1][occupied],
                axis=1,
            )
        self._fft_norm_buffer.append(max(float(np.max(display_magnitudes[0])), 1e-6))
        self._fft_norm_timer.target_value = max(self._fft_norm_buffer)
        if len(self._fft_norm_buffer) == 1:
            self._fft_norm_timer.current_value = self._fft_norm_timer.target_value
        display_magnitudes /= max(self._fft_norm_timer.current_value, 1e-6)
        np.maximum(display_magnitudes, self._fft_draw, out=self._fft_draw)
        self._fft_draw *= pow(0.93, multiple_factor)
        self._fft_levels[:] = np.power(self._fft_draw, 0.75)
        self._fft_levels[1] = (
            self._fft_levels[0] + (self._fft_levels[1] - self._fft_levels[0]) * 2.5
        )
        self._fft_levels *= 0.35
        np.maximum(self._fft_levels, 0.0, out=self._fft_levels)
        peak = float(np.max(self._fft_levels))
        if peak > 0.55:
            self._fft_levels *= 0.55 / peak
        self.update()

    def _resetFFT(self, *_args: object) -> None:
        self._fft_levels.fill(0.0)
        self._fft_magnitudes.fill(0.0)
        self._fft_smoothed.fill(0.0)
        self._fft_draw.fill(0.0)
        self._fft_norm_buffer.clear()
        self._fft_norm_timer.reset()
        self.update()

    def getBands(self) -> list[tuple[float, float]]:
        return [
            (self.normalizedToFrequency(x), self.normalizedToGain(y))
            for x, y in self.getPoints()
        ]

    def setBands(self, bands: list[tuple[float, float]]) -> None:
        self.setPoints([
            (self.frequencyToNormalized(frequency), self.gainToNormalized(gain))
            for frequency, gain in bands
        ])

    def normalizedToFrequency(self, value: float) -> float:
        ratio = self.MAX_FREQUENCY / self.MIN_FREQUENCY
        return self.MIN_FREQUENCY * pow(ratio, self._clamp(value))

    def frequencyToNormalized(self, frequency: float) -> float:
        ratio = self.MAX_FREQUENCY / self.MIN_FREQUENCY
        clamped = min(max(frequency, self.MIN_FREQUENCY), self.MAX_FREQUENCY)
        return math.log(clamped / self.MIN_FREQUENCY, ratio)

    def normalizedToGain(self, value: float) -> float:
        return self.MIN_GAIN + self._clamp(value) * (self.MAX_GAIN - self.MIN_GAIN)

    def gainToNormalized(self, gain: float) -> float:
        clamped = min(max(gain, self.MIN_GAIN), self.MAX_GAIN)
        return (clamped - self.MIN_GAIN) / (self.MAX_GAIN - self.MIN_GAIN)

    def _emitBands(self, _points: list[tuple[float, float]]) -> None:
        self.bandsChanged.emit(self.getBands())

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, value))

    def _labelFont(self) -> QFont:
        font = QFont(self.font())
        font.setPointSizeF(8.0)
        return font

    def _labelMetrics(self) -> QFontMetricsF:
        return QFontMetricsF(self._labelFont(), self)

    def _labelLayout(self) -> list[tuple[str, float, float, int]]:
        metrics = self._labelMetrics()
        row_height = metrics.height() + self.LABEL_ROW_GAP
        width = max(1.0, self.width() - self.PLOT_LEFT - self.PLOT_RIGHT)
        placed: list[QRectF] = []
        layout: list[tuple[str, float, float, int]] = []
        for x_timer, _, _ in self._points:
            value = x_timer.current_value
            text = self._formatFrequency(self.normalizedToFrequency(value))
            label_width = metrics.horizontalAdvance(text)
            center = self.PLOT_LEFT + value * width
            minimum_left = self.PLOT_LEFT - 4.0
            maximum_left = max(minimum_left, self.width() - label_width - 2.0)
            label_left = min(max(center - label_width / 2, minimum_left), maximum_left)
            row = 0
            box = QRectF(label_left, 0.0, label_width, metrics.height())
            while True:
                box.moveTop(row * row_height)
                if not any(box.intersects(other) for other in placed):
                    break
                row += 1
            placed.append(QRectF(box))
            layout.append((text, label_left, label_width, row))
        return layout

    def _labelRowHeight(self) -> float:
        return self._labelMetrics().height() + self.LABEL_ROW_GAP

    def _labelRows(self) -> int:
        return max((row for _, _, _, row in self._labelLayout()), default=-1) + 1

    def _targetInset(self) -> float:
        return self.LABEL_GAP + 3.0 + self._labelRows() * self._labelRowHeight()

    def _bottomInset(self) -> float:
        return max(0.0, self._inset_timer.current_value)

    def _updateInset(self, *_args: object) -> None:
        target = self._targetInset()
        if target == self._inset_timer.target_value:
            return
        self._inset_timer.target_value = target
        if not self._inset_animation.isActive():
            self._inset_animation.start()

    def _updateInsetTick(self) -> None:
        self.update()
        if not self._inset_timer.is_animating:
            self._inset_animation.stop()

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._updateInset()

    @override
    def _plotRect(self) -> QRectF:
        return QRectF(
            self.PLOT_LEFT,
            self.PLOT_TOP,
            max(1.0, self.width() - self.PLOT_LEFT - self.PLOT_RIGHT),
            max(1.0, self.height() - self.PLOT_TOP - self._bottomInset()),
        )

    @override
    def _movePoint(self, position: QPointF) -> None:
        index = self._dragged_point
        if index < 0:
            return
        point = self._fromWidget(position - self._drag_offset)
        x, y, _ = self._points[index]
        if index == 0:
            new_x = 0.0
        elif index == len(self._points) - 1:
            new_x = 1.0
        else:
            left = math.nextafter(self._points[index - 1][0].target_value, 1.0)
            right = math.nextafter(self._points[index + 1][0].target_value, 0.0)
            new_x = max(left, min(right, point.x()))
        if (new_x, point.y()) == (x.target_value, y.target_value):
            return
        x.target_value = new_x
        y.target_value = point.y()
        self._startAnimation()
        self.pointsChanged.emit(self.getPoints())

    @override
    def _removePoint(self, index: int) -> None:
        if index in (0, len(self._points) - 1):
            return
        super()._removePoint(index)

    @override
    def _curvePath(self, positions: list[QPointF]) -> QPainterPath:
        rect = self._plotRect()
        bands = [
            (
                self.normalizedToFrequency((point.x() - rect.left()) / rect.width()),
                self.normalizedToGain((rect.bottom() - point.y()) / rect.height()),
            )
            for point in positions
        ]
        band_frequencies = np.array([frequency for frequency, _ in bands])
        frequencies = self._fft_frequencies[
            ~np.isin(self._fft_frequencies, band_frequencies)
        ]
        gains = sampleEqualizerCurve(bands, frequencies)
        frequencies = np.concatenate((frequencies, band_frequencies))
        gains = np.concatenate((gains, np.array([gain for _, gain in bands])))
        order = np.argsort(frequencies, kind='stable')
        path = QPainterPath()
        for index, (frequency, gain) in enumerate(
            zip(frequencies[order], gains[order])
        ):
            point = self._toWidget(
                self.frequencyToNormalized(float(frequency)),
                self.gainToNormalized(float(gain)),
            )
            if index == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        return path

    @override
    def paintGL(self) -> None:
        super().paintGL()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        foreground = QColor(255, 255, 255) if theme.isDark() else QColor(0, 0, 0)
        rect = self._plotRect()
        if np.any(self._fft_levels > 0.001):
            paths: list[QPainterPath] = []
            for levels in self._fft_levels:
                path = QPainterPath()
                for index, level in enumerate(levels):
                    point = QPointF(
                        rect.left() + index / (len(levels) - 1) * rect.width(),
                        rect.bottom() - float(level) * rect.height(),
                    )
                    if index == 0:
                        path.moveTo(point)
                    else:
                        path.lineTo(point)
                paths.append(path)
            accent = mixColor(themeColor(), foreground, 0.75)
            fill_path = QPainterPath(paths[1])
            fill_path.lineTo(rect.right(), rect.bottom())
            fill_path.lineTo(rect.left(), rect.bottom())
            fill_path.closeSubpath()
            gradient = QLinearGradient(
                QPointF(rect.left(), paths[1].boundingRect().top()),
                rect.bottomLeft(),
            )
            gradient.setColorAt(0.0, accent)
            transparent = QColor(accent)
            transparent.setAlpha(0)
            gradient.setColorAt(1.0, transparent)
            painter.save()
            painter.setClipRect(rect)
            painter.fillPath(fill_path, gradient)
            original = QColor(foreground)
            original.setAlpha(160)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(accent, 1.5))
            painter.drawPath(paths[1])
            painter.setPen(QPen(original, 1.25))
            painter.drawPath(paths[0])
            painter.restore()
        painter.setFont(self._labelFont())

        axis = QColor(foreground)
        axis.setAlpha(70)
        painter.setPen(QPen(axis, 1))
        painter.drawLine(
            QPointF(rect.left(), rect.top()), QPointF(rect.left(), rect.bottom())
        )
        painter.drawLine(
            QPointF(rect.left(), rect.bottom()), QPointF(rect.right(), rect.bottom())
        )

        tick = QColor(foreground)
        tick.setAlpha(70)
        label = QColor(foreground)
        label.setAlpha(170)
        for gain in self.GAIN_TICKS:
            y = rect.bottom() - self.gainToNormalized(gain) * rect.height()
            painter.setPen(QPen(tick, 1))
            painter.drawLine(QPointF(rect.left() - 4, y), QPointF(rect.left(), y))

        painter.setPen(QPen(label, 1))
        for gain in self.GAIN_TICKS:
            y = rect.bottom() - self.gainToNormalized(gain) * rect.height()
            text = '0' if gain == 0 else f'{gain:+.0f}'
            painter.drawText(
                QRectF(rect.left() - 38, y - 7, 32, 14),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                text,
            )

        metrics = self._labelMetrics()
        row_height = metrics.height() + self.LABEL_ROW_GAP
        base_y = rect.bottom() + self.LABEL_GAP
        for text, label_left, label_width, row in self._labelLayout():
            box = QRectF(
                label_left,
                base_y + row * row_height,
                label_width,
                metrics.height(),
            )
            center = box.center().x()
            painter.setPen(QPen(tick, 1))
            painter.drawLine(QPointF(center, rect.bottom()), QPointF(center, box.top()))
            painter.setPen(QPen(label, 1))
            painter.drawText(
                box, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, text
            )
        painter.end()

    def _formatFrequency(self, frequency: float) -> str:
        if frequency >= 1000:
            return f'{frequency / 1000:.1f}k'
        return f'{frequency:.1f}'
