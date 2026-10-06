from __future__ import annotations

import ctypes
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from threading import Thread
from typing import override

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QIcon,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QRegion,
)
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import IndeterminateProgressRing, ProgressBar, ProgressRing

_logger = logging.getLogger(__name__)


class LaunchWindow(QWidget):
    closeRequested = Signal()
    downloadProgressChanged = Signal(object)

    def __init__(
        self,
        app: QApplication,
        debug_mode: bool = False,
        separate_process: bool = False,
    ) -> None:
        from core.smooth import EaseInOutTimer

        super().__init__()
        self._app: QApplication = app
        self._process: subprocess.Popen[bytes] | None = None
        self._stage_started_ns = time.perf_counter_ns() if debug_mode else 0
        self._launch_started_ns = self._stage_started_ns
        self._stage_text = 'Launching...'
        self._stages: list[tuple[str, int, int]] = []
        self.width_timer = EaseInOutTimer(0.5, 3)
        self.height_timer = EaseInOutTimer(0.5, 3)
        self._download_progress: float | None = None
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        screen_geometry = app.primaryScreen().geometry()
        self.setFixedSize(screen_geometry.width(), 40)
        self.move(
            screen_geometry.x(),
            screen_geometry.y() + (screen_geometry.height() - self.height()) // 2,
        )

        icon_path = os.path.join(
            os.path.dirname(__file__), '..', '..', 'icons', 'app.ico'
        )
        self._icon_pixmap = QIcon(icon_path).pixmap(
            QSize(28, 28), self.devicePixelRatioF()
        )

        self.ring = IndeterminateProgressRing(self, start=not separate_process)
        self.ring.setFixedSize(36, 36)
        self.ring.setStrokeWidth(2)
        self.ring.hide()

        self.download_ring = ProgressRing(self, useAni=False)
        self.download_ring.setFixedSize(36, 36)
        self.download_ring.setStrokeWidth(2)
        self.download_ring.setRange(0, 1000)
        self.download_ring.hide()

        self.progress_bar = ProgressBar(self, useAni=False)
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.hide()

        is_light = app.palette().color(QPalette.ColorRole.Window).lightness() > 127
        self._background_color = QColor('#dddddd' if is_light else '#111111')
        self._text_color = QColor('black' if is_light else 'white')
        self._title_text = 'SouthsideMusic'
        QFontDatabase.addApplicationFont(
            os.path.join(
                os.path.dirname(__file__),
                '..',
                '..',
                'fonts',
                'HARMONYOS_SANS_SC_REGULAR.ttf',
            )
        )
        self._title_font = QFont('HarmonyOS Sans SC')
        self._title_font.setPixelSize(18)
        self._title_font.setWeight(QFont.Weight.DemiBold)
        self._title_width = QFontMetricsF(self._title_font).horizontalAdvance(
            self._title_text
        )
        self._paint_timer = QTimer(self)
        self._paint_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._paint_timer.setInterval(16)
        self._paint_timer.timeout.connect(self._updateAnimation)
        self.downloadProgressChanged.connect(
            self.setDownloadProgress, Qt.ConnectionType.QueuedConnection
        )

        if separate_process:
            process = subprocess.Popen(
                [
                    sys.executable,
                    os.path.abspath(__file__),
                    str(self.x()),
                    str(self.y()),
                    self._background_color.name(),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self._process = process
            assert process.stdin is not None
            process_stdin = process.stdin
            self.destroyed.connect(lambda _obj=None: process_stdin.close())
            Thread(target=process.wait, daemon=True).start()
        else:
            self.show()
            self.raise_()
            self.activateWindow()
            self._app.processEvents()
            self.width_timer.target_value = 1
            self._paint_timer.start()

    def subtitle(self, text: str) -> None:
        if self._stage_started_ns:
            now = time.perf_counter_ns()
            self._stages.append((self._stage_text, self._stage_started_ns, now))
            self._stage_started_ns = now
            self._stage_text = text
        self._app.processEvents()

    def setDownloadProgress(self, progress: float | None) -> None:
        if self._process is not None:
            if self._process.stdin is not None and not self._process.stdin.closed:
                try:
                    self._process.stdin.write(f'{json.dumps(progress)}\n'.encode())
                    self._process.stdin.flush()
                except OSError:
                    _logger.exception('failed to update launch download progress')
            return

        if progress is None:
            if self._download_progress is not None:
                self.ring.start()
            self.height_timer.target_value = 0
        else:
            if self._download_progress is None:
                self.ring.stop()
            progress = max(0.0, min(1.0, progress))
            self.download_ring.setValue(round(progress * 1000))
            self.progress_bar.setValue(round(progress * 1000))
            self.height_timer.target_value = 1
        self._download_progress = progress
        self.update()

    def _updateAnimation(self) -> None:
        height = 40 + round(24 * self.height_timer.current_value)
        if self.height() != height:
            self.setFixedHeight(height)
        self.update()

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._background_color)
        radius = 20
        pill_width = (
            40 + (8 + self._title_width + radius) * self.width_timer.current_value
        )
        painter.translate((self.width() - pill_width) / 2, 0)
        pill_path = QPainterPath()
        pill_path.addRoundedRect(
            QRectF(0, 0, pill_width, self.height()), radius, radius
        )
        painter.drawPath(pill_path)
        painter.setClipPath(pill_path)
        painter.drawPixmap(
            QRectF(6, 6, 28, 28), self._icon_pixmap, QRectF(self._icon_pixmap.rect())
        )
        painter.save()
        ring = self.ring if self._download_progress is None else self.download_ring
        ring.render(painter, QPoint(2, 2), QRegion(), QWidget.RenderFlag.DrawChildren)
        painter.restore()
        painter.setFont(self._title_font)
        painter.setPen(self._text_color)
        painter.drawText(
            QRectF(48, 0, self._title_width, 40),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._title_text,
        )
        if self.height() > 40:
            self.progress_bar.setFixedWidth(max(1, round(pill_width - 40)))
            painter.save()
            self.progress_bar.render(
                painter, QPoint(20, 48), QRegion(), QWidget.RenderFlag.DrawChildren
            )
            painter.restore()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        super().closeEvent(event)
        if not event.isAccepted():
            return
        self._paint_timer.stop()
        if self._process is not None and self._process.stdin is not None:
            self._process.stdin.close()
        if not self._stage_started_ns:
            return
        now = time.perf_counter_ns()
        self._stages.append((self._stage_text, self._stage_started_ns, now))
        self._stage_started_ns = 0
        try:
            self._saveLaunchReport(now)
        except Exception:
            _logger.exception('failed to save launch timing report')

    def _saveLaunchReport(self, finished_ns: int) -> None:
        from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter

        elapsed_ns = finished_ns - self._launch_started_ns
        stages = [
            {
                'text': text,
                'start_ms': (start - self._launch_started_ns) / 1_000_000,
                'end_ms': (end - self._launch_started_ns) / 1_000_000,
                'duration_ms': (end - start) / 1_000_000,
                'percentage': (end - start) / max(1, elapsed_ns) * 100,
            }
            for text, start, end in self._stages
        ]
        totals: dict[str, float] = {}
        for text, start, end in self._stages:
            totals[text] = totals.get(text, 0.0) + (end - start) / 1_000_000
        ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
        created_at = datetime.now().astimezone()
        directory = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__), '..', '..', 'data', 'debug', 'launch'
            )
        )
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(
            directory, f'launch_{created_at:%Y%m%d_%H%M%S_%f}_{os.getpid()}'
        )
        with open(f'{path}.json', 'w', encoding='utf-8') as output:
            json.dump(
                {
                    'created_at': created_at.isoformat(),
                    'total_ms': elapsed_ns / 1_000_000,
                    'stages': stages,
                    'totals_by_text': [
                        {'text': text, 'duration_ms': duration}
                        for text, duration in ranked
                    ],
                },
                output,
                ensure_ascii=False,
                indent=2,
            )

        font = QFont('HarmonyOS Sans SC', 10)
        metrics = QFontMetrics(font)
        label_width = max(metrics.horizontalAdvance(text) for text in totals) + 70
        width = max(1280, label_width + 720)
        image = QImage(width, 160 + len(stages) * 36, QImage.Format.Format_ARGB32)
        image.fill(QColor('#111111'))
        painter = QPainter(image)
        try:
            painter.setFont(QFont(font.family(), 18, QFont.Weight.Bold))
            painter.setPen(QColor('#eeeeee'))
            painter.drawText(24, 38, 'SouthsideMusic launch timings')
            painter.setFont(font)
            painter.drawText(
                24,
                68,
                f'{created_at.isoformat()}   Total: {elapsed_ns / 1_000_000:.2f} ms',
            )
            painter.drawText(
                24,
                96,
                f'Slowest (total by text): {ranked[0][0]} ({ranked[0][1]:.2f} ms)',
            )
            painter.drawText(
                24, 120, 'Chronological stages - label changes to window close'
            )
            maximum_ns = max(end - start for _, start, end in self._stages)
            for index, (text, start, end) in enumerate(self._stages):
                y = 150 + index * 36
                duration_ns = end - start
                painter.setPen(QColor('#eeeeee'))
                painter.drawText(24, y + 16, f'{index + 1:02d}  {text}')
                painter.fillRect(
                    label_width,
                    y,
                    max(1, round(duration_ns / max(1, maximum_ns) * 500)),
                    22,
                    QColor('#ffb454' if duration_ns == maximum_ns else '#58a6ff'),
                )
                painter.drawText(
                    label_width + 516,
                    y + 16,
                    f'{duration_ns / 1_000_000:.2f} ms '
                    f'({duration_ns / max(1, elapsed_ns) * 100:.1f}%)',
                )
        finally:
            painter.end()
        if not image.save(f'{path}.png'):
            raise OSError(f'failed to save {path}.png')
        _logger.info('launch timing report saved to %s', path)

    def clear(self) -> None:
        pass


if __name__ == '__main__':
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from core.theme import syncSystemThemeColor

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    syncSystemThemeColor()
    window = LaunchWindow(app)
    window.move(int(sys.argv[1]), int(sys.argv[2]))
    window._background_color = QColor(sys.argv[3])
    window.update()
    ctypes.windll.user32.AllowSetForegroundWindow(os.getppid())
    window.closeRequested.connect(app.quit)
    theme_timer = QTimer(window)
    theme_timer.timeout.connect(syncSystemThemeColor)
    theme_timer.start(1000)

    def _waitForClose() -> None:
        for line in sys.stdin.buffer:
            window.downloadProgressChanged.emit(json.loads(line))
        window.closeRequested.emit()

    Thread(target=_waitForClose, daemon=True).start()
    sys.exit(app.exec())
