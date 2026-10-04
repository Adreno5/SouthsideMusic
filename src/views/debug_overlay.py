import json
import logging
import os
import time
from collections import deque
from dataclasses import asdict
from typing import override

import numpy as np
import psutil
from PySide6.QtCore import QPoint, QPointF, QRect, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QHideEvent,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QShowEvent,
    Qt,
    QWheelEvent,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QPushButton, QWidget

from core import theme
from core.app_context import AppContext
from core.frame_profiler import FrameProfile, frame_profiler
from core.lyric_video_export import (
    lyricVideoExportDebugInfo,
    lyricVideoExportDebugProcessPids,
)
from core.models import DATA_DIR
from core.smooth import EaseOutTimer
from services.events import REPAINT_ALWAYS, SECOND_TICK, event_bus
from services.events.events import _50MS_TICK

_logger = logging.getLogger(__name__)


class DebugOverlay(QOpenGLWidget):
    _exclude_from_profile = True

    def __init__(self, ctx: AppContext, parent: QWidget) -> None:
        super().__init__(parent)
        surface_format = self.format()
        surface_format.setSwapInterval(0)
        surface_format.setAlphaBufferSize(8)
        surface_format.setSamples(4)
        self.setFormat(surface_format)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)
        self.ctx = ctx
        self.title_ft = QFont(ctx.harmony_font_family, 15, QFont.Weight.Bold)
        self.content_ft = QFont(ctx.harmony_font_family, 10, QFont.Weight.Normal)
        self.title_height = int(QFontMetricsF(self.title_ft).height())
        self.content_height = int(QFontMetricsF(self.content_ft).height())
        self.content_metri = QFontMetricsF(self.content_ft)
        self.profile_colors: dict[str, QColor] = {}
        self._profile_history: deque[FrameProfile] = deque(maxlen=30)
        self._capturing = False
        self._capture_deadline_ns = 0
        self._resume_debug_collection = False
        self.export_button = QPushButton('Export 10s PNG', self)
        self.export_button.setGeometry(430, 10, 180, 30)
        self.export_timer = QTimer(self)
        self.export_timer.setSingleShot(True)
        self.export_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.export_button.clicked.connect(self._startExport)
        self.export_timer.timeout.connect(self._finishExport)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.dragging = False
        self.drag_pos: QPoint = QPoint(0, 0)
        self.resizing = False
        self.resize_edges: tuple[bool, bool] = (False, False)
        self.resize_origin = QPoint(0, 0)
        self.geometry_origin = self.geometry()
        self.resize_margin = 10
        self.setMinimumSize(420, 360)

        self.mem_datas: dict[str, deque[int]] = {}
        self._ticks_active = False
        self.total_mem = psutil.virtual_memory().total
        self.mmax_value: EaseOutTimer = EaseOutTimer(1, 2)

        self.cpu_datas: dict[str, deque[float]] = {}
        self.cpu_cores = os.cpu_count() or 1
        self.last_cpu_time: dict[int, float] = {}
        self.last_wall: dict[int, float] = {}
        self.cmax_value: EaseOutTimer = EaseOutTimer(1, 2)
        self.tracked_pids: dict[str, set[int]] = {}

        self.process_cache: dict[int, psutil.Process] = {}

        self.offset_timer = EaseOutTimer(0.3, 2)

        self.last_update_cpu = -1.0
        self.beat_datas: deque[float] = deque(maxlen=800)
        self.beat_points: deque[int] = deque(maxlen=800)
        player = getattr(getattr(ctx, 'playing_manager', None), '_player', None)
        if player is not None and hasattr(player, 'beatDataReady'):
            player.beatDataReady.connect(self.onBeatData)
            player.beatDataReset.connect(self._resetBeatData)
        self.hide()

    def onBeatData(self, intensity: float, is_point: bool) -> None:
        if not self.ctx.debugging:
            return
        self.beat_datas.append(float(intensity))
        self.beat_points.append(int(is_point))

    def _resetBeatData(self) -> None:
        self.beat_datas.clear()
        self.beat_points.clear()

    def showEvent(self, event: QShowEvent) -> None:
        if self._capturing:
            return super().showEvent(event)
        self._startTicks()
        event_bus.subscribe(REPAINT_ALWAYS, self.refresh)
        return super().showEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:
        self._stopTicks()
        self._profile_history.clear()
        if not self._capturing:
            event_bus.unsubscribe(REPAINT_ALWAYS, self.refresh)
        self.ctx.debugging = False
        return super().hideEvent(event)

    def _startTicks(self) -> None:
        if self._ticks_active:
            return
        self._ticks_active = True
        event_bus.subscribe(_50MS_TICK, self.updateDatas)
        event_bus.subscribe(SECOND_TICK, self.tryRaise)

    def _stopTicks(self) -> None:
        if not self._ticks_active:
            return
        self._ticks_active = False
        event_bus.unsubscribe(_50MS_TICK, self.updateDatas)
        event_bus.unsubscribe(SECOND_TICK, self.tryRaise)

    def tryRaise(self):
        if self.isHidden():
            return
        self.raise_()

    def updateDatas(self) -> None:
        if not self.ctx.debugging:
            return
        pids = self._activeProcessPids()
        self._clearStaleProcessData(pids)
        self.updateMemories(pids)
        if time.perf_counter() - self.last_update_cpu >= 0.2:
            self.updateCpus(pids)
            self.last_update_cpu = time.perf_counter()

    def _processPids(self) -> dict[str, int]:
        pids = dict(self.ctx.process_pids)
        pids.update(lyricVideoExportDebugProcessPids())
        return pids

    def _activeProcessPids(self) -> dict[str, int]:
        active_pids: dict[str, int] = {}
        for name, pid in self._processPids().items():
            try:
                if not self.process_cache.get(pid):
                    self.process_cache[pid] = psutil.Process(pid)
                if not self.process_cache[pid].is_running():
                    continue
            except psutil.Error:
                continue
            active_pids[name] = pid
        return active_pids

    def _clearStaleProcessData(self, pids: dict[str, int]) -> None:
        stale_names = (
            set(self.mem_datas) | set(self.cpu_datas) | set(self.tracked_pids)
        ) - set(pids)
        for name in stale_names:
            self.mem_datas.pop(name, None)
            self.cpu_datas.pop(name, None)
            for pid in self.tracked_pids.pop(name, set()):
                self.last_cpu_time.pop(pid, None)
                self.process_cache.pop(pid, None)

        active_parent_pids = set(pids.values())
        for pid in list(self.last_wall):
            if pid not in active_parent_pids:
                self.last_wall.pop(pid, None)

    def _shouldTrackChildren(self, name: str) -> bool:
        return name.startswith('lyric-video-')

    def _trackedProcesses(self, name: str, pid: int) -> list[psutil.Process]:
        if not self.process_cache.get(pid):
            self.process_cache[pid] = psutil.Process(pid)
        process = self.process_cache[pid]
        if not self._shouldTrackChildren(name):
            return [process]
        return [process, *process.children(recursive=True)]

    def _updateTrackedPids(self, name: str, processes: list[psutil.Process]) -> None:
        pids = {process.pid for process in processes}
        for pid in self.tracked_pids.get(name, set()) - pids:
            self.last_cpu_time.pop(pid, None)
            self.process_cache.pop(pid, None)
        self.tracked_pids[name] = pids

    def updateMemories(self, pids: dict[str, int]) -> None:
        if not self.ctx.debugging:
            return

        for name, pid in pids.items():
            if not self.mem_datas.get(name):
                self.mem_datas[name] = deque(maxlen=200)
            try:
                processes = self._trackedProcesses(name, pid)
                rss = sum(process.memory_info().rss for process in processes)
                self.mem_datas[name].append(rss)
            except psutil.Error:
                continue

        max_v = 0
        for lst in self.mem_datas.values():
            for v in lst:
                max_v = max(max_v, v)
        self.mmax_value.target_value = max_v

    def updateCpus(self, pids: dict[str, int]) -> None:
        if not self.ctx.debugging:
            return

        for name, pid in pids.items():
            if not self.cpu_datas.get(name):
                self.cpu_datas[name] = deque(maxlen=200)
            try:
                processes = self._trackedProcesses(name, pid)
                self._updateTrackedPids(name, processes)
                cpu_delta = 0.0
                for process in processes:
                    times = process.cpu_times()
                    cpu_time = times.user + times.system
                    last_cpu_time = self.last_cpu_time.get(process.pid)
                    if last_cpu_time is not None:
                        cpu_delta += max(0.0, cpu_time - last_cpu_time)
                    self.last_cpu_time[process.pid] = cpu_time
            except psutil.Error:
                continue
            now = time.perf_counter()
            elapsed = max(now - self.last_wall.get(pid, 0.0), 0.001)
            cpu_percent = cpu_delta / elapsed / self.cpu_cores * 100
            self.cpu_datas[name].append(cpu_percent)
            self.last_wall[pid] = now

        max_v = 0.0
        for lst in self.cpu_datas.values():
            for v in lst:
                max_v = max(max_v, v)
        self.cmax_value.target_value = max_v

    def wheelEvent(self, event: QWheelEvent) -> None:
        self.offset_timer.target_value += event.angleDelta().y()
        return super().wheelEvent(event)

    def refresh(
        self, _multiple_factor: float = 1.0, raise_overlay: bool = False
    ) -> None:
        if self._capturing:
            return
        self.setVisible(self.ctx.debugging)
        if self.ctx.debugging:
            profile = frame_profiler.snapshot
            if (
                profile is not None
                and profile.duration_ns > 0
                and (
                    not self._profile_history
                    or self._profile_history[-1] is not profile
                )
            ):
                self._profile_history.append(profile)
            if raise_overlay:
                self.raise_()
            self.update()

    def adjustToParent(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        bounds = parent.rect()
        width = min(self.width(), bounds.width())
        height = min(self.height(), bounds.height())
        x = max(0, min(self.x(), bounds.width() - width))
        y = max(0, min(self.y(), bounds.height() - height))
        self.setGeometry(
            x, y, max(self.minimumWidth(), width), max(self.minimumHeight(), height)
        )

    def _resizeEdges(self, position: QPoint) -> tuple[bool, bool]:
        return (
            position.x() >= self.width() - self.resize_margin,
            position.y() >= self.height() - self.resize_margin,
        )

    def _updateResizeCursor(self, position: QPoint) -> None:
        right, bottom = self._resizeEdges(position)
        if right and bottom:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif right:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif bottom:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        event.accept()
        edges = self._resizeEdges(event.pos())
        if any(edges):
            self.resizing = True
            self.resize_edges = edges
            self.resize_origin = event.globalPosition().toPoint()
            self.geometry_origin = self.geometry()
            return
        self.dragging = True
        self.drag_pos = QPoint(event.x(), event.y())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.resizing:
            delta = event.globalPosition().toPoint() - self.resize_origin
            right, bottom = self.resize_edges
            width = self.geometry_origin.width() + (delta.x() if right else 0)
            height = self.geometry_origin.height() + (delta.y() if bottom else 0)
            self.resize(
                max(self.minimumWidth(), width), max(self.minimumHeight(), height)
            )
            self.adjustToParent()
            event.accept()
            return
        if not self.dragging:
            self._updateResizeCursor(event.pos())
            return super().mouseMoveEvent(event)
        event.accept()
        self.move(self.pos() + event.pos() - self.drag_pos)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        event.accept()
        self.dragging = False
        self.resizing = False
        self._updateResizeCursor(event.pos())

    def _profileColor(self, name: str) -> QColor:
        if name not in self.profile_colors:
            self.profile_colors[name] = QColor.fromHsvF(
                len(self.profile_colors) * 0.61803398875 % 1, 0.65, 0.95
            )
        return self.profile_colors[name]

    def _startExport(self) -> None:
        if (
            self._capturing
            or not self.ctx.debugging
            or not frame_profiler.isRecording()
        ):
            return
        self._capturing = True
        self.export_button.setEnabled(False)
        self._resume_debug_collection = self.ctx.debugging_obj.isCollecting()
        self.ctx.debugging_obj.setCollecting(False)
        self.hide()
        frame_profiler.startCapture()
        self._capture_deadline_ns = time.perf_counter_ns() + 10_000_000_000
        self.export_timer.start(10_000)

    def _finishExport(self) -> None:
        if self._capturing and self.ctx.debugging:
            remaining_ns = self._capture_deadline_ns - time.perf_counter_ns()
            if remaining_ns > 0:
                self.export_timer.start(max(1, (remaining_ns + 999_999) // 1_000_000))
                return
        try:
            frames = frame_profiler.finishCapture()
            if frames:
                path = self._savePerformanceReport(frames)
                self.export_button.setText('PNG + JSON saved - Export 10s')
                self.export_button.setToolTip(path)
                _logger.info('performance report saved to %s', path)
                self.show()
            else:
                self.export_button.setText('Export 10s PNG')
                self.export_button.setToolTip('Capture cancelled: debugging disabled')
        except Exception as error:
            _logger.exception('performance report export failed')
            self.export_button.setText('Export failed - Retry')
            self.export_button.setToolTip(str(error))
        finally:
            self._capturing = False
            self.ctx.debugging = True
            self.export_button.setEnabled(True)
            if self.ctx.debugging:
                if self._resume_debug_collection:
                    self.ctx.debugging_obj.setCollecting(True)
                self.show()
                self.update()

    def _savePerformanceReport(self, frames: tuple[FrameProfile, ...]) -> str:
        all_frames = frames
        frames = tuple(frame for frame in frames if frame.complete)
        totals: dict[str, int] = {}
        counts: dict[str, int] = {}
        for frame in frames:
            for name, duration in frame.sections:
                totals[name] = totals.get(name, 0) + duration
            for name, count in frame.section_counts:
                counts[name] = counts.get(name, 0) + count
        duration_ns = sum(frame.duration_ns for frame in frames)
        wall_ns = sum(frame.wall_duration_ns or frame.duration_ns for frame in frames)
        frame_count = sum(frame.frame_count for frame in frames)
        if duration_ns <= 0 or frame_count <= 0:
            raise ValueError('No performance samples were recorded')
        refresh_rate = self.ctx.events_service.refresh_rate
        budget_ns = 1_000_000_000 / refresh_rate
        interval_ns = np.array([frame.wall_duration_ns for frame in frames])
        work_ns = np.array([frame.work_duration_ns for frame in frames])
        statistics = {
            'interval_percentiles_ms': (
                np.percentile(interval_ns, [50, 95, 99]) / 1_000_000
            ).tolist(),
            'work_percentiles_ms': (
                np.percentile(work_ns, [50, 95, 99]) / 1_000_000
            ).tolist(),
            'interval_over_budget_ratio': float(np.mean(interval_ns > budget_ns)),
            'work_over_budget_ratio': float(np.mean(work_ns > budget_ns)),
            'interval_over_two_budgets_ratio': float(
                np.mean(interval_ns > 2 * budget_ns)
            ),
        }
        idle_name = 'Idle / uninstrumented'
        top = sorted(
            (
                (name, duration)
                for name, duration in totals.items()
                if name != idle_name
            ),
            key=lambda item: item[1],
            reverse=True,
        )[:10]
        other_ns = duration_ns - totals.get(idle_name, 0) - sum(v for _, v in top)
        slices = [*top, ('Other modules', max(0, other_ns))]
        slices.append((idle_name, totals.get(idle_name, 0)))
        series = ['Refresh interval (wall time)', *(name for name, _ in top)]
        width = 1680
        height = 1260 + ((len(series) + 1) // 2) * 30
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor('#171a20'))
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QColor('#edf0f5'))
            painter.setFont(QFont(self.ctx.harmony_font_family, 22, QFont.Weight.Bold))
            painter.drawText(40, 48, 'SouthsideMusic - Performance capture')
            painter.setFont(QFont(self.ctx.harmony_font_family, 12))
            painter.drawText(
                40,
                82,
                f'{wall_ns / 1_000_000_000:.3f}s | {frame_count} refresh cycles | '
                f'{frame_count * 1_000_000_000 / max(1, wall_ns):.1f} Hz | '
                f'Screen: {refresh_rate:.1f} Hz | '
                f'Average interval: {wall_ns / frame_count / 1_000_000:.3f} ms | '
                'Debug overlay excluded',
            )
            painter.setFont(QFont(self.ctx.harmony_font_family, 16, QFont.Weight.Bold))
            painter.drawText(40, 126, 'Average time distribution')
            painter.drawText(410, 126, 'Top 10 modules - average over complete cycles')
            pie_rect = QRect(40, 154, 310, 310)
            angle = 0
            elapsed_ns = 0
            painter.setPen(Qt.PenStyle.NoPen)
            for name, duration in slices:
                elapsed_ns += duration
                end_angle = round(elapsed_ns / duration_ns * 5760)
                painter.setBrush(self._profileColor(name))
                painter.drawPie(pie_rect, angle, end_angle - angle)
                angle = end_angle
            painter.setFont(QFont(self.ctx.harmony_font_family, 11))
            for i, (name, duration) in enumerate(top):
                row_y = 166 + i * 50
                painter.fillRect(
                    QRect(410, row_y - 14, 12, 12), self._profileColor(name)
                )
                painter.setPen(QColor('#edf0f5'))
                text = painter.fontMetrics().elidedText(
                    f'{i + 1}. {name}', Qt.TextElideMode.ElideRight, 1200
                )
                painter.drawText(432, row_y, text)
                painter.setPen(QColor('#aeb8c8'))
                painter.drawText(
                    432,
                    row_y + 22,
                    f'{duration / frame_count / 1_000_000:.4f} ms/cycle | '
                    f'{duration / duration_ns * 100:.2f}% | '
                    f'{counts.get(name, 0)} calls | '
                    f'{duration / max(1, counts.get(name, 0)) / 1_000_000:.4f} ms/call',
                )
            for i, (name, duration) in enumerate(slices[-2:]):
                row_y = 500 + i * 35
                painter.fillRect(
                    QRect(40, row_y - 14, 12, 12), self._profileColor(name)
                )
                painter.setPen(QColor('#edf0f5'))
                painter.drawText(
                    62, row_y, f'{name}: {duration / duration_ns * 100:.2f}%'
                )
            painter.setPen(QColor('#edf0f5'))
            painter.setFont(QFont(self.ctx.harmony_font_family, 16, QFont.Weight.Bold))
            painter.drawText(
                40, 708, 'Per-cycle time (ms) - refresh interval and top 10 modules'
            )
            plot = QRect(85, 746, width - 130, 340)
            painter.setFont(QFont(self.ctx.harmony_font_family, 11))
            max_ms = max(frame.wall_duration_ns / 1_000_000 for frame in frames) * 1.1
            max_ms = max(1.0, max_ms)
            for tick in range(6):
                grid_y = plot.bottom() - round(plot.height() * tick / 5)
                painter.setPen(QPen(QColor('#343c48'), 1))
                painter.drawLine(plot.left(), grid_y, plot.right(), grid_y)
                painter.setPen(QColor('#aeb8c8'))
                painter.drawText(15, grid_y + 5, f'{max_ms * tick / 5:.1f}')
                tick_x = plot.left() + round(plot.width() * tick / 5)
                painter.drawText(
                    tick_x - 16, plot.bottom() + 26, f'{wall_ns / 1e9 * tick / 5:.1f}s'
                )
            samples = [dict(frame.sections) for frame in frames]
            painter.save()
            painter.setClipRect(plot.adjusted(-1, -1, 1, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for series_index, name in enumerate(series):
                path = QPainterPath()
                elapsed = 0
                for index, frame in enumerate(frames):
                    elapsed += frame.wall_duration_ns or frame.duration_ns
                    value_ns = (
                        frame.wall_duration_ns
                        if series_index == 0
                        else samples[index].get(name, 0)
                    )
                    x = plot.left() + (plot.width() - 1) * elapsed / max(1, wall_ns)
                    y = (
                        plot.bottom()
                        - (plot.height() - 1) * value_ns / 1_000_000 / max_ms
                    )
                    (path.moveTo if index == 0 else path.lineTo)(x, y)
                color = (
                    QColor('#edf0f5') if series_index == 0 else self._profileColor(name)
                )
                painter.setPen(QPen(color, 2 if series_index == 0 else 1.2))
                painter.drawPath(path)
                if len(frames) == 1:
                    painter.drawEllipse(QPointF(x, y), 3, 3)
            painter.restore()
            for index, name in enumerate(series):
                x = 40 + (index % 2) * 820
                y = 1150 + (index // 2) * 30
                color = QColor('#edf0f5') if index == 0 else self._profileColor(name)
                painter.fillRect(QRect(x, y - 12, 12, 12), color)
                painter.setPen(QColor('#edf0f5'))
                painter.drawText(
                    x + 22,
                    y,
                    painter.fontMetrics().elidedText(
                        name, Qt.TextElideMode.ElideRight, 760
                    ),
                )
            interval_text = ' / '.join(
                f'{v:.3f}' for v in statistics['interval_percentiles_ms']
            )
            work_text = ' / '.join(
                f'{v:.3f}' for v in statistics['work_percentiles_ms']
            )
            painter.drawText(
                40, height - 70, f'Interval P50 / P95 / P99: {interval_text} ms'
            )
            painter.drawText(
                40, height - 45, f'Instrumented work P50 / P95 / P99: {work_text} ms'
            )
            painter.drawText(
                40,
                height - 20,
                f'Budget: {budget_ns / 1_000_000:.3f} ms | '
                f'Interval over budget: {statistics["interval_over_budget_ratio"]:.1%} | '
                f'Work over budget: {statistics["work_over_budget_ratio"]:.1%} | '
                f'Interval over 2x budget: {statistics["interval_over_two_budgets_ratio"]:.1%}',
            )
        finally:
            painter.end()
        directory = os.path.join(DATA_DIR, 'debug', 'performance')
        os.makedirs(directory, exist_ok=True)
        filename = f'performance-{time.strftime("%Y%m%d-%H%M%S")}-{time.time_ns() % 1_000_000_000:09d}.png'
        file_path = os.path.join(directory, filename)
        if not image.save(file_path):
            raise OSError(f'Could not save performance report: {file_path}')
        with open(
            os.path.splitext(file_path)[0] + '.json', 'w', encoding='utf-8'
        ) as file:
            json.dump(
                {
                    'refresh_rate_hz': refresh_rate,
                    'window_size': [
                        self.ctx.main_window.width(),
                        self.ctx.main_window.height(),
                    ],
                    'device_pixel_ratio': self.ctx.main_window.devicePixelRatioF(),
                    'window_visible': self.ctx.main_window.isVisible(),
                    'window_minimized': self.ctx.main_window.isMinimized(),
                    'window_exposed': bool(
                        (handle := self.ctx.main_window.windowHandle()) is not None
                        and handle.isExposed()
                    ),
                    'budget_ns': budget_ns,
                    'statistics': statistics,
                    'frames': [asdict(frame) for frame in all_frames],
                },
                file,
                ensure_ascii=False,
            )
        return file_path

    def _drawText(self, painter: QPainter, x: int, y: int, text: str) -> bool:
        bounds = painter.fontMetrics().boundingRect(text).translated(x, y)
        if not self.rect().contains(painter.worldTransform().mapRect(bounds)):
            return False
        if painter.hasClipping() and not painter.clipBoundingRect().contains(bounds):
            return False
        painter.drawText(x, y, text)
        return True

    @override
    def paintGL(self) -> None:
        painter = QPainter(self)
        try:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver
            )
            if not self.ctx.debugging or self._capturing:
                return
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.translate(0, 50 + self.offset_timer.current_value)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(
                QColor(255, 255, 255, 100) if theme.isLight() else QColor(0, 0, 0, 100)
            )

            y = 0
            column_width = 420
            chart_width = column_width - 10
            mem_rect = QRect(5, y - 215, chart_width, 200)
            cpu_rect = QRect(5, y - 430, chart_width, 200)
            beat_rect = QRect(5, y - 645, chart_width, 200)

            painter.drawRect(
                0,
                -int(self.offset_timer.current_value) - 50,
                self.width(),
                self.height(),
            )
            painter.drawRect(mem_rect)  # make the
            painter.drawRect(mem_rect)  # color darker
            painter.drawRect(cpu_rect)  # make the
            painter.drawRect(cpu_rect)  # color darker
            painter.drawRect(beat_rect)  # make the
            painter.drawRect(beat_rect)  # color darker

            painter.setPen(
                QPen(
                    QColor(255, 255, 255) if theme.isDark() else QColor(0, 0, 0),
                    1,
                )
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setFont(self.content_ft)

            mem_max = self.mmax_value.current_value * 1.1
            if mem_max > 0:
                painter.save()
                painter.setClipRect(mem_rect)
                for name, values in self.mem_datas.items():
                    path = QPainterPath()
                    if not values:
                        continue
                    path.moveTo(5, values[0] / mem_max * -200 + y - 15)
                    for i, v in enumerate(values):
                        x = 5 + chart_width * (i / 200)
                        y_ = v / mem_max * -200 - 15
                        path.lineTo(x, y_)
                    txt = f'{name} - {(values[-1] / 1024 / 1024):.2f} MB'
                    self._drawText(
                        painter,
                        int(
                            max(
                                0,
                                x - self.content_metri.horizontalAdvance(txt) - 5,
                            )
                        ),
                        int(y_ - 5),
                        txt,
                    )
                    painter.drawPath(path)
                painter.restore()

            cpu_max = self.cmax_value.current_value
            if cpu_max > 0:
                painter.save()
                painter.setClipRect(cpu_rect)
                for name, cpu_values in self.cpu_datas.items():
                    path = QPainterPath()
                    if not cpu_values:
                        continue
                    path.moveTo(5, cpu_values[0] / cpu_max * -200 + y - 230)
                    for i, cpu_value in enumerate(cpu_values):
                        x = 5 + chart_width * (i / 200)
                        y_ = cpu_value / cpu_max * -200 - 230
                        path.lineTo(x, y_)
                    txt = f'{name} - {(cpu_values[-1]):.2f}%'
                    self._drawText(
                        painter,
                        int(
                            max(
                                0,
                                x - self.content_metri.horizontalAdvance(txt) - 5,
                            )
                        ),
                        int(y_ - 5),
                        txt,
                    )
                    painter.drawPath(path)
                painter.restore()

            painter.save()
            painter.setClipRect(beat_rect)
            if self.beat_datas:
                path = QPainterPath()
                for i, value in enumerate(self.beat_datas):
                    x = 5 + chart_width * i / 800
                    y_ = y - 445 - value * 180
                    (path.moveTo if i == 0 else path.lineTo)(x, y_)
                painter.drawPath(path)
                self._drawText(
                    painter, 10, y - 455, f'Beat intensity - {self.beat_datas[-1]:.2f}'
                )
            painter.setPen(QPen(QColor(210, 105, 105, 180), 1))
            for i, point in enumerate(self.beat_points):
                if not point:
                    continue
                x = int(5 + chart_width * i / 800)
                painter.drawLine(x, y - 645, x, y - 445)
            painter.restore()

            if self._profile_history:
                sample_count = len(self._profile_history)
                frame_duration_ns = (
                    sum(profile.duration_ns for profile in self._profile_history)
                    / sample_count
                )
                section_totals: dict[str, tuple[float, int]] = {}
                for profile in self._profile_history:
                    for name, duration_ns in profile.sections:
                        percentage_sum, duration_sum = section_totals.get(
                            name, (0.0, 0)
                        )
                        section_totals[name] = (
                            percentage_sum + duration_ns / profile.duration_ns * 100,
                            duration_sum + duration_ns,
                        )
                sections = sorted(
                    (
                        (
                            name,
                            percentage_sum / sample_count,
                            duration_sum / sample_count,
                        )
                        for name, (
                            percentage_sum,
                            duration_sum,
                        ) in section_totals.items()
                    ),
                    key=lambda item: item[1],
                    reverse=True,
                )
                profile_x = column_width + 10
                profile_width = column_width - 20
                pie_size = 180
                pie_rect = QRect(
                    profile_x + (profile_width - pie_size) // 2,
                    y + 10,
                    pie_size,
                    pie_size,
                )
                row_height = self.content_height + 4
                legend_y = pie_rect.bottom() + 15 + row_height
                self._drawText(
                    painter,
                    profile_x,
                    legend_y,
                    f'Frame: {frame_duration_ns / 1_000_000:.3f} ms',
                )
                legend_y += row_height
                angle = 0
                elapsed_percentage = 0.0
                painter.save()
                painter.setPen(Qt.PenStyle.NoPen)
                for name, percentage, mean_duration_ns in sections:
                    color = self._profileColor(name)
                    elapsed_percentage += percentage
                    end_angle = round(elapsed_percentage / 100 * 5760)
                    painter.setBrush(color)
                    painter.drawPie(pie_rect, angle, end_angle - angle)
                    angle = end_angle
                painter.restore()
                for name, percentage, mean_duration_ns in sections:
                    value_text = (
                        f'{percentage:5.1f}%  {mean_duration_ns / 1_000_000:.3f} ms'
                    )
                    value_width = self.content_metri.horizontalAdvance(value_text)
                    name_width = max(1, profile_width - value_width - 25)
                    name_text = self.content_metri.elidedText(
                        name, Qt.TextElideMode.ElideRight, name_width
                    )
                    self._drawText(painter, profile_x + 15, legend_y, name_text)
                    self._drawText(
                        painter,
                        int(profile_x + profile_width - value_width),
                        legend_y,
                        value_text,
                    )
                    color_rect = QRect(
                        profile_x, legend_y - self.content_height + 3, 10, 10
                    )
                    if self.rect().contains(
                        painter.worldTransform().mapRect(color_rect)
                    ):
                        painter.fillRect(color_rect, self.profile_colors[name])
                    legend_y += row_height

            y += 10
            export_info = lyricVideoExportDebugInfo()
            blocks: list[tuple[str, list[str]]] = []
            if export_info:
                blocks.append(('Lyric Video Export', export_info))
            for info in self.ctx.debugging_obj.infos:
                name, lines = next(iter(info.items()))
                blocks.append((name, lines))

            if blocks:
                available_width = max(1, self.width() - 20)
                column_count = max(1, available_width // column_width)
                column_heights = [y for _ in range(column_count)]
                columns = [column for column in range(column_count) if column != 1]

                for name, lines in blocks:
                    column = min(columns, key=column_heights.__getitem__)
                    x = int(10 + column * column_width)
                    block_y = column_heights[column]
                    text_width = max(1, int(column_width) - 20)
                    painter.setFont(self.title_ft)
                    self._drawText(painter, x, block_y, name)
                    block_y += self.title_height + 10
                    painter.setFont(self.content_ft)
                    for line in lines:
                        self._drawText(
                            painter,
                            x + 10,
                            block_y,
                            self.content_metri.elidedText(
                                line, Qt.TextElideMode.ElideRight, text_width
                            ),
                        )
                        block_y += self.content_height + 1
                    column_heights[column] = block_y + 12
        finally:
            painter.end()
