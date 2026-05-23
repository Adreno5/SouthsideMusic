from __future__ import annotations

import logging

import math
import time
from typing import cast

from core.app_context import AppContext

from imports import (
    REFRESH_RATE_CHANGED,
    REPAINT,
    QEnterEvent,
    QEvent,
    QPointF,
    Qt,
    QTimer,
    event_bus,
)

from imports import (
    QColor,
    QFont,
    QFontMetricsF,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QWheelEvent,
)
from imports import QWidget

from core.qt_utils import toQtInt
from core.time_format import float2time
from core.color import mixColor
from core import theme as darkdetect
from core.lyrics import LyricInfo, YRCLyricInfo


class LyricsViewer(QWidget):
    _TRANSLATION_TIME_TOLERANCE = 0.02

    def __init__(
        self,
        ctx: AppContext,
        ft_size: int | None = None,
        transft_size: int | None = None,
    ):
        super().__init__()
        self._logger = logging.getLogger(__name__)
        self.ctx = ctx
        self._app = ctx.app
        self._mgr = ctx.mgr
        self._transmgr = ctx.transmgr
        self._ymgr = ctx.ymgr
        self._player = ctx.player
        self._mwindow = ctx.main_window
        self._cfg = ctx.cfg
        self._dp = ctx.playing_page

        self.draw_offset: float = 0
        self.target_draw_offset: float = 0

        self.acc: float = 0
        self.target_acc: float = 0

        self.ft = QFont(ctx.harmony_font_family, ft_size or 14)
        self.font_height = QFontMetricsF(self.ft).height()
        self.metri = QFontMetricsF(self.ft)

        self.tft = QFont(ctx.harmony_font_family, transft_size or 10)
        self.theight = QFontMetricsF(self.tft).height()
        self.tmetri = QFontMetricsF(self.tft)

        self.selecting: bool = False
        self.hovering_lyric: LyricInfo | None = None
        self.mouse_pos: QPointF | None = None
        self.last_wheel: float = time.time()

        self.draw_x_offset: float = 0

        self.last_draw: int = time.perf_counter_ns()

        self.setMouseTracking(True)

        self._lyrics_ready = True
        self._prewarm_version = 0
        self._translation_lookup_key: tuple[int, int] | None = None
        self._translation_by_time: dict[int, str] = {}
        self._shifted_translation_by_time: dict[int, str] = {}
        self._translation_timing_shifted = False

        self.refresh_rate = max(60, ctx.app.primaryScreen().refreshRate() / 2)
        self._logger.info(f'{self.refresh_rate=}')

        self.delta = 1 / self.refresh_rate

        self.hovering = False

        event_bus.subscribe(REFRESH_RATE_CHANGED, self._onRefreshRateChanged)
        event_bus.subscribe(REPAINT, self._onRepaintTick)

    def prewarmFontMetrics(self):
        self._lyrics_ready = False
        self._prewarm_version += 1
        version = self._prewarm_version
        QTimer.singleShot(0, lambda: self._doPrewarm(version))

    def _doPrewarm(self, version: int):
        if version != self._prewarm_version:
            return
        all_texts: set[str] = set()
        for mgr in (self._mgr, self._transmgr, self._ymgr):
            for line in mgr.parsed:
                content = line.get('content', '').strip()
                if content:
                    all_texts.add(content)
                if 'chars' in line:
                    for ch in line['chars']:
                        c = ch['char'].strip()
                        if c:
                            all_texts.add(c)
        for text in all_texts:
            self.metri.horizontalAdvance(text)
        self._lyrics_ready = True
        self.update()

    def _onRepaintTick(self):
        self.update()

    def _onRefreshRateChanged(self):
        self.refresh_rate = max(60, self._app.primaryScreen().refreshRate() / 2)
        self._logger.info(f'{self.refresh_rate=}')

        self.delta = 1 / self.refresh_rate

    def _hasTranslation(self) -> bool:
        return bool(self._transmgr.parsed)

    def _timeKey(self, value: float) -> int:
        return round(value * 1000)

    def _timesClose(self, left: float, right: float) -> bool:
        return abs(left - right) <= self._TRANSLATION_TIME_TOLERANCE

    def _translationLookupKey(self) -> tuple[int, int]:
        return (
            getattr(self._mgr, 'version', 0),
            getattr(self._transmgr, 'version', 0),
        )

    def _ensureTranslationLookup(self) -> None:
        key = self._translationLookupKey()
        if key == self._translation_lookup_key:
            return

        self._translation_lookup_key = key
        self._translation_by_time = {}
        self._shifted_translation_by_time = {}
        self._translation_timing_shifted = False

        original_lines = [
            line
            for line in self._mgr.parsed
            if line.get('content', '').strip() and not line.get('isMetadata')
        ]
        translated_lines = [
            line
            for line in self._transmgr.parsed
            if line.get('content', '').strip() and not line.get('isMetadata')
        ]

        for line in translated_lines:
            self._translation_by_time[self._timeKey(line['time'])] = line[
                'content'
            ].strip()

        if len(original_lines) < 2 or len(translated_lines) + 1 != len(original_lines):
            return

        empty_times = getattr(self._transmgr, 'empty_times', [])
        if not any(
            self._timesClose(empty_time, original_lines[0]['time'])
            for empty_time in empty_times
        ):
            return

        shifted_timestamps_match = all(
            self._timesClose(translated_line['time'], original_line['time'])
            for translated_line, original_line in zip(
                translated_lines, original_lines[1:]
            )
        )
        if not shifted_timestamps_match:
            return

        self._translation_timing_shifted = True
        self._shifted_translation_by_time = {
            self._timeKey(original_line['time']): translated_line['content'].strip()
            for original_line, translated_line in zip(original_lines, translated_lines)
        }

    def _translationTimeForLine(
        self,
        line: LyricInfo | YRCLyricInfo,
        use_yrc: bool | None = None,
    ) -> float:
        trans_time = line['time']
        if use_yrc is None:
            use_yrc = 'chars' in line
        if use_yrc and self._mgr.parsed:
            lrc_line = self._mgr.getCurrentLyric(line['time'])
            if lrc_line.get('content', '').strip():
                trans_time = lrc_line['time']
        return trans_time

    def _translationTextForLine(
        self,
        line: LyricInfo | YRCLyricInfo,
        use_yrc: bool | None = None,
    ) -> str:
        if (
            not line.get('content', '').strip()
            or line.get('isMetadata')
            or not self._transmgr.parsed
        ):
            return ''
        self._ensureTranslationLookup()
        trans_time = self._translationTimeForLine(line, use_yrc)

        if self._translation_timing_shifted:
            return self._shifted_translation_by_time.get(self._timeKey(trans_time), '')

        direct_match = self._translation_by_time.get(self._timeKey(trans_time))
        if direct_match is not None:
            return direct_match

        for trans_line in self._transmgr.parsed:
            if abs(trans_line['time'] - trans_time) <= self._TRANSLATION_TIME_TOLERANCE:
                return trans_line['content'].strip()
        return ''

    def _shouldDrawTranslationForLine(
        self,
        line: LyricInfo | YRCLyricInfo,
        use_yrc: bool,
        is_current_line: bool,
    ) -> bool:
        return bool(self._transmgr.parsed)

    def _lineStep(self) -> float:
        if self._hasTranslation():
            return self.font_height + self.theight + self.font_height * 0.75
        return self.font_height * 1.85

    def _currentLineBaseline(self) -> float:
        block_height = self.font_height
        if self._hasTranslation():
            block_height += 2 + self.theight
        return (self.height() - block_height) * 0.5 + self.metri.ascent()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.mouse_pos = event.position()
        return super().mouseMoveEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        now = time.perf_counter_ns()
        _elapsed: float = min((now - self.last_draw) / 1_000_000_000, 0.1)
        self.last_draw = now
        multiple_factor = _elapsed * self.refresh_rate

        self.hovering_lyric = None
        if not self._mgr.parsed or not self._lyrics_ready:
            return

        self.target_acc = (
            (self.target_draw_offset - self.draw_offset)
            * self.delta
            * (self._cfg.lyrics_smooth_factor * self.refresh_rate)
            * multiple_factor
        )
        self.acc += (
            (self.target_acc - self.acc)
            * self.delta
            * (self._cfg.acceleration_smooth_factor * self.refresh_rate)
            # / max(0.5, min(1, abs(self.target_acc - self.acc)))
            * multiple_factor
        )

        if self.draw_offset != self.target_draw_offset:
            self.draw_offset += self.acc

        if not self.isVisible():
            return

        position = self._player.getPosition()
        use_yrc = bool(self._ymgr.parsed)
        lines = self._ymgr.parsed if use_yrc else self._mgr.parsed
        idx = (
            self._ymgr.getCurrentIndex(position)
            if use_yrc
            else self._mgr.getCurrentIndex(position)
        )

        line_step = self._lineStep()
        current_baseline = self._currentLineBaseline()

        if not all(
            math.isfinite(value)
            for value in (self.draw_offset, self.target_draw_offset, self.acc)
        ):
            self.draw_offset = 0
            self.target_draw_offset = 0
            self.acc = 0

        if not self.selecting:
            self.target_draw_offset = -idx * line_step
        else:
            if time.time() - self.last_wheel > 3:
                self.selecting = False

        if self.draw_offset > 0:
            self.target_draw_offset = 0
        if self.draw_offset < -len(lines) * line_step:
            self.target_draw_offset = -len(lines) * line_step
        self.draw_offset = max(-len(lines) * line_step, min(0, self.draw_offset))

        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        painter.setFont(self.ft)

        y = self.draw_offset + current_baseline
        for i, line in enumerate(lines):
            is_current_line = i == idx
            if is_current_line:
                if line.get('isMetadata'):
                    tar_color = QColor(255, 255, 255)
                    y += 5
                else:
                    tar_color = (
                        QColor(255, 255, 255)
                        if darkdetect.isDark()
                        else QColor(0, 0, 0)
                    )
            else:
                tar_color = (
                    QColor(240, 240, 240, 120)
                    if darkdetect.isDark()
                    else QColor(55, 55, 55, 120)
                )
            color = (
                mixColor(
                    self._mwindow.song_theme, tar_color, self._cfg.background_ratio / 2
                )
                if self._mwindow and self._mwindow.song_theme
                else tar_color
            )
            if is_current_line and use_yrc and not line.get('isMetadata'):
                y_line = cast(YRCLyricInfo, line)
                content = (y_line['content'] or line['content']).strip()
                base_color = QColor(color)
                base_color.setAlpha(120)
                painter.setPen(base_color)
                painter.drawText(toQtInt(self.draw_x_offset), toQtInt(y), content)

                x = 0.0
                clip_y = toQtInt(y - self.metri.ascent())
                clip_h = toQtInt(self.font_height)
                for ch in y_line['chars']:
                    text_width = self.metri.horizontalAdvance(ch['char'])
                    duration = ch['duration']
                    if duration <= 0:
                        progress = 1.0 if position >= ch['start'] else 0.0
                    else:
                        progress = (position - ch['start']) / duration
                    progress = max(0.0, min(1.0, progress))
                    clip_w = text_width * progress
                    if clip_w > 0:
                        painter.save()
                        painter.setClipRect(
                            toQtInt(x + self.draw_x_offset),
                            clip_y,
                            toQtInt(math.ceil(clip_w)),
                            clip_h,
                        )
                        painter.setPen(color)
                        painter.drawText(
                            toQtInt(self.draw_x_offset), toQtInt(y), content
                        )
                        painter.restore()
                    x += text_width
            else:
                painter.setPen(color)
                painter.drawText(
                    toQtInt(self.draw_x_offset),
                    toQtInt(y),
                    line['content'].strip(),
                )

            translation_text = (
                self._translationTextForLine(line, use_yrc)
                if self._shouldDrawTranslationForLine(line, use_yrc, is_current_line)
                else ''
            )
            if translation_text:
                painter.setFont(self.tft)
                painter.setPen(
                    QColor(255, 255, 255, 120)
                    if darkdetect.isDark()
                    else QColor(0, 0, 0, 120)
                )
                painter.drawText(
                    toQtInt(self.draw_x_offset),
                    toQtInt(y + self.metri.descent() + 2 + self.tmetri.ascent()),
                    translation_text,
                )
                painter.setFont(self.ft)

            if (
                self.mouse_pos
                and self.mouse_pos.y() > y - self.metri.ascent()
                and self.mouse_pos.y() < y + self.metri.descent() + self.theight + 5
            ):
                self.hovering_lyric = line
                if self.selecting:
                    painter.setBrush(
                        QColor(255, 255, 255, 100)
                        if darkdetect.isDark()
                        else QColor(0, 0, 0, 100)
                    )
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawRoundedRect(
                        toQtInt(self.draw_x_offset),
                        toQtInt(y - self.metri.ascent()),
                        toQtInt(self.width() - self.draw_x_offset),
                        toQtInt(self.font_height),
                        5,
                        5,
                    )
                    painter.setPen(color)
                    if self.hovering_lyric:
                        info = float2time(self.hovering_lyric['time'])
                    timetxt = f'{f"{info['minutes']}".zfill(2)}:{f"{info['seconds']}".zfill(2)}'
                    painter.drawText(
                        toQtInt(
                            self.width() - self.metri.horizontalAdvance(timetxt) - 5
                        ),
                        toQtInt(y),
                        timetxt,
                    )

            y += line_step

        painter.end()

    def enterEvent(self, event: QEnterEvent) -> None:
        self.hovering = True
        return super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self.mouse_pos = None
        self.selecting = False
        self.hovering_lyric = None
        self.hovering = False
        return super().leaveEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        self.selecting = True
        self.target_draw_offset += event.angleDelta().y()
        self.last_wheel = time.time()
        return super().wheelEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.hovering_lyric and event.button() == Qt.MouseButton.LeftButton:
            self._player.setPosition(self.hovering_lyric['time'])
            self.selecting = False
            self.hovering_lyric = None
            self.mouse_pos = None
        return super().mousePressEvent(event)
