from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from typing import override

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

_logger = logging.getLogger(__name__)


class LaunchWindow(QWidget):
    def __init__(self, app: QApplication, debug_mode: bool = False) -> None:
        super().__init__()
        self._app: QApplication = app
        self._stage_started_ns = time.perf_counter_ns() if debug_mode else 0
        self._launch_started_ns = self._stage_started_ns
        self._stage_text = 'Launching...'
        self._stages: list[tuple[str, int, int]] = []
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setFixedSize(app.primaryScreen().size() * 0.25)
        screen_geometry = app.primaryScreen().availableGeometry()
        self.move(screen_geometry.center() - self.rect().center())

        self._stack: list[str] = []

        launchlayout = QVBoxLayout()
        title_label = QLabel('Southside Music')
        title_label.setStyleSheet('font-size: 28px; font-weight: 600;')
        launchlayout.addWidget(
            title_label,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
        )
        self.sublabel = QLabel('Launching...')
        self.sublabel.setStyleSheet('font-size: 16px;')
        launchlayout.addWidget(
            self.sublabel,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        )
        launchlayout.addSpacerItem(
            QSpacerItem(
                0,
                0,
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Expanding,
            )
        )
        self.subtitlel = QLabel()
        launchlayout.addWidget(self.subtitlel)
        progress_bar = QProgressBar()
        progress_bar.setRange(0, 0)
        progress_bar.setTextVisible(False)
        progress_bar.setFixedHeight(4)
        launchlayout.addWidget(progress_bar)
        self.setLayout(launchlayout)

        is_light = app.palette().color(QPalette.ColorRole.Window).lightness() > 127
        self.setStyleSheet(
            f'QWidget {{ background-color: {"#dddddd" if is_light else "#111111"}; }} '
            f'QLabel {{ color: {"black" if is_light else "white"}; }}'
        )

        self.show()
        self.raise_()
        self.activateWindow()
        self._app.processEvents()

    def subtitle(self, text: str) -> None:
        if self._stage_started_ns:
            now = time.perf_counter_ns()
            self._stages.append((self._stage_text, self._stage_started_ns, now))
            self._stage_started_ns = now
            self._stage_text = text
        self.subtitlel.setText(text)
        self._app.processEvents()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        super().closeEvent(event)
        if not event.isAccepted() or not self._stage_started_ns:
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
