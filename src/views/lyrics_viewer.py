from __future__ import annotations

import logging

import math
import time
from typing import cast

from core.app_context import AppContext

from core.downloader import asyncTask
from imports import (
    REFRESH_RATE_CHANGED,
    REPAINT,
    QEnterEvent,
    QEvent,
    QPen,
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
    QPaintEvent,
    QWheelEvent,
)
from imports import QWidget

from core.qt_utils import toQtInt
from core.time_format import float2time
from core.color import mixColor
from core import theme
from core.smooth import EaseOutTimer
from core.lyrics import LyricInfo, YRCLyricInfo
from services.events.events import PLAY_STORABLE, START_PROGRESS_LOADING, STOP_PROGRESS_LOADING, UPDATE_LOADING_PROGRESS


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

        self.current_index: int = 0
        self.yrc_current_ratio: float = 0

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

        self.db_ft = QFont(ctx.harmony_font_family, 10)

        self.selecting: bool = False
        self.hovering_lyric: LyricInfo | YRCLyricInfo | None = None
        self.mouse_pos: QPointF | None = None
        self.last_wheel: float = time.time()

        self.draw_x_offset: float = 0

        self.last_draw: int = time.perf_counter_ns()

        self.setMouseTracking(True)

        self._lyrics_ready = True
        self._translation_lookup_key: tuple[int, int] | None = None
        self._translation_by_time: dict[int, str] = {}
        self._shifted_translation_by_time: dict[int, str] = {}
        self._translation_timing_shifted = False

        self.refresh_rate = max(60, ctx.app.primaryScreen().refreshRate() / 2)
        self._logger.info(f'{self.refresh_rate=}')

        self.delta = 1 / self.refresh_rate

        self.hovering = False

        self.translation_timer = EaseOutTimer(0.4, 4)

        self._shown_lines: list[int] = []
        self._line_alphas: dict[int, EaseOutTimer] = {}

        self.last_lyric: YRCLyricInfo | LyricInfo | None = None

        self._visibility_timer = QTimer(self)
        self._visibility_timer.timeout.connect(self._updateShownLines)
        self._visibility_timer.start(50)

        event_bus.subscribe(REFRESH_RATE_CHANGED, self._onRefreshRateChanged)
        event_bus.subscribe(REPAINT, self._onRepaintTick)
        event_bus.subscribe(PLAY_STORABLE, lambda _: self.prewarmFontMetrics())
        
    def prewarmFontMetrics(self):
        self._lyrics_ready = False
        asyncTask(self._doPrewarm, (), self)

    def _doPrewarm(self):
        all_texts: set[str] = set()
        for mgr in (self._mgr, self._transmgr, self._ymgr):
            for line in mgr.parsed:
                content = line.content.strip()
                if content:
                    all_texts.add(content)
                if isinstance(line, YRCLyricInfo):
                    for ch in line.chars:
                        c = ch.char.strip()
                        if c:
                            all_texts.add(c)
        event_bus.emit(START_PROGRESS_LOADING)
        for i, text in enumerate(all_texts):
            self.metri.horizontalAdvance(text)
            event_bus.emit(UPDATE_LOADING_PROGRESS, i / len(all_texts))
            time.sleep(0.02)
        event_bus.emit(STOP_PROGRESS_LOADING)
        self._lyrics_ready = True

    def _onRepaintTick(self):
        self.update()

    def _onRefreshRateChanged(self):
        self.refresh_rate = max(60, self._app.primaryScreen().refreshRate() / 2)
        self._logger.info(f'{self.refresh_rate=}')

        self.delta = 1 / self.refresh_rate

    def _hasTranslation(self) -> bool:
        return bool(self._transmgr.parsed) if self.ctx.cfg.show_translation else False

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
            if line.content.strip() and not line.isMetadata
        ]
        translated_lines = [
            line
            for line in self._transmgr.parsed
            if line.content.strip() and not line.isMetadata
        ]

        for line in translated_lines:
            self._translation_by_time[self._timeKey(line.time)] = line.content.strip()

        if len(original_lines) < 2 or len(translated_lines) + 1 != len(original_lines):
            return

        empty_times = getattr(self._transmgr, 'empty_times', [])
        if not any(
            self._timesClose(empty_time, original_lines[0].time)
            for empty_time in empty_times
        ):
            return

        shifted_timestamps_match = all(
            self._timesClose(translated_line.time, original_line.time)
            for translated_line, original_line in zip(
                translated_lines, original_lines[1:]
            )
        )
        if not shifted_timestamps_match:
            return

        self._translation_timing_shifted = True
        self._shifted_translation_by_time = {
            self._timeKey(original_line.time): translated_line.content.strip()
            for original_line, translated_line in zip(original_lines, translated_lines)
        }

    def _translationTimeForLine(
        self,
        line: LyricInfo | YRCLyricInfo,
        use_yrc: bool | None = None,
    ) -> float:
        if not hasattr(line, 'chars'):
            return line.time
        yrc_time = line.time
        for trans_line in self._transmgr.parsed:
            if abs(trans_line.time - yrc_time) <= self._TRANSLATION_TIME_TOLERANCE:
                return yrc_time
        if use_yrc and self._mgr.parsed:
            lrc_line = self._mgr.getCurrentLyric(yrc_time)
            if lrc_line.content.strip():
                return lrc_line.time
        return yrc_time

    def _translationTextForLine(
        self,
        line: LyricInfo | YRCLyricInfo,
        use_yrc: bool | None = None,
    ) -> str:
        if not line.content.strip() or line.isMetadata or not self._transmgr.parsed:
            return ''
        self._ensureTranslationLookup()
        trans_time = self._translationTimeForLine(line, use_yrc)

        if self._translation_timing_shifted:
            return self._shifted_translation_by_time.get(self._timeKey(trans_time), '')

        direct_match = self._translation_by_time.get(self._timeKey(trans_time))
        if direct_match is not None:
            return direct_match

        for trans_line in self._transmgr.parsed:
            if abs(trans_line.time - trans_time) <= self._TRANSLATION_TIME_TOLERANCE:
                return trans_line.content.strip()
        return ''

    def _shouldDrawTranslationForLine(
        self,
        line: LyricInfo | YRCLyricInfo,
        use_yrc: bool,
        is_current_line: bool,
    ) -> bool:
        return bool(self._translationTextForLine(line, use_yrc))

    def _lineStep(self, has_translation: bool = False) -> float:
        cur = self.translation_timer.current_value
        if has_translation:
            return self.font_height * (1.85 - (0.1 * cur)) + (self.theight * cur)
        return self.font_height * 1.85

    def _currentLineBaseline(self, has_translation: bool = False) -> float:
        cur = self.translation_timer.current_value
        block_height = self.font_height + (
            (2 + self.theight) * cur if has_translation else 0
        )
        return (self.height() - block_height) * 0.5 + self.metri.ascent()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.mouse_pos = event.position()
        return super().mouseMoveEvent(event)

    def _updateShownLines(self):
        if not self._mgr.parsed or not self._lyrics_ready:
            return
        use_yrc = bool(self._ymgr.parsed)
        lines = self._ymgr.parsed if use_yrc else self._mgr.parsed

        y_offsets: list[float] = []
        y = 0.0
        for line in lines:
            y_offsets.append(y)
            has_trans = bool(self._translationTextForLine(line, use_yrc))
            y += self._lineStep(has_trans)

        position = self._player.getPosition()
        current_idx = (
            self._ymgr.getCurrentIndex(position)
            if use_yrc
            else self._mgr.getCurrentIndex(position)
        )
        current_has_trans = (
            bool(self._translationTextForLine(lines[current_idx], use_yrc))
            if 0 <= current_idx < len(lines)
            else False
        )
        current_baseline = self._currentLineBaseline(current_has_trans)

        top_offset = self.draw_offset + current_baseline
        shown: list[int] = []
        for i in range(len(lines)):
            y_pos = top_offset + y_offsets[i]
            line_bottom = y_pos + self.font_height
            if (line_bottom >= 0 and y_pos - self.font_height <= self.height()):
                shown.append(i)
        self._shown_lines = shown

        to_remove = []
        for key in self._line_alphas:
            if key not in shown:
                to_remove.append(key)
        for item in to_remove:
            self._line_alphas.pop(item)

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

        y_offsets: list[float] = []
        y = 0.0
        for line in lines:
            y_offsets.append(y)
            has_trans = bool(self._translationTextForLine(line, use_yrc))
            y += self._lineStep(has_trans)
        total_height = y

        current_has_trans = (
            bool(self._translationTextForLine(lines[idx], use_yrc))
            if 0 <= idx < len(lines)
            else False
        )
        current_baseline = self._currentLineBaseline(current_has_trans)

        if not all(
            math.isfinite(value)
            for value in (self.draw_offset, self.target_draw_offset, self.acc)
        ):
            self.draw_offset = 0
            self.target_draw_offset = 0
            self.acc = 0

        if not self.selecting:
            self.target_draw_offset = (
                -y_offsets[idx] if 0 <= idx < len(y_offsets) else 0
            )
        else:
            if time.time() - self.last_wheel > 3:
                self.selecting = False

        if self.target_draw_offset > 0:
            self.target_draw_offset = 0
        if self.target_draw_offset < -total_height:
            self.target_draw_offset = -total_height

        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        painter.setFont(self.ft)

        top_offset = self.draw_offset + current_baseline
        for i in (idx for idx in self._shown_lines if idx < len(lines)):
            line = lines[i]
            if not self._line_alphas.get(i):
                self._line_alphas[i] = EaseOutTimer(0.2, 2)
            timer = self._line_alphas[i]
            is_current_line = i == idx
            y = int(top_offset + y_offsets[i])
            if self.ctx.debugging:
                painter.setPen(QPen(QColor(255, 0, 0), 1))
                painter.drawLine(0, y, self.width(), y)
                painter.setFont(self.db_ft)
                painter.drawText(10, y + 15, f'Baseline {y}')
                painter.setFont(self.ft)
            if is_current_line:
                timer.target_value = 255
                self.current_index = i
            else:
                timer.target_value = 120

            alpha = timer.current_value

            
            if is_current_line:
                if line.isMetadata:
                    tar_color = QColor(255, 255, 255)
                else:
                    tar_color = (
                        QColor(255, 255, 255) if theme.isDark() else QColor(0, 0, 0)
                    )
            else:
                tar_color = (
                    QColor(240, 240, 240, 120)
                    if theme.isDark()
                    else QColor(55, 55, 55, 120)
                )
            tar_color.setAlpha(int(alpha))

            color = (
                mixColor(
                    self._mwindow.song_theme, tar_color, self._cfg.background_ratio / 2
                )
                if self._mwindow and self._mwindow.song_theme
                else tar_color
            )
            if is_current_line and self.ctx.debugging:
                color = QColor(0, 255, 0)

            if is_current_line and use_yrc and not line.isMetadata:
                y_line = cast(YRCLyricInfo, line)
                content = (y_line.content or line.content).strip()
                base_color = QColor(color)
                base_color.setAlpha(120)
                painter.setPen(base_color)
                painter.drawText(toQtInt(self.draw_x_offset), toQtInt(y), content)

                x = 0.0
                clip_y = toQtInt(y - self.metri.ascent())
                clip_h = toQtInt(self.font_height)
                for ch in y_line.chars:
                    text_width = self.metri.horizontalAdvance(ch.char)
                    duration = ch.duration
                    if duration <= 0:
                        progress = 1.0 if position >= ch.start else 0.0
                    else:
                        progress = (position - ch.start) / duration
                    progress = max(0.0, min(1.0, progress))
                    if progress > 0 and progress < 1:
                        self.yrc_current_ratio = progress
                    clip_w = text_width * progress
                    if clip_w > 0:
                        painter.save()
                        if self.ctx.debugging and (progress > 0 and progress < 1):
                            painter.setPen(QPen(QColor(120, 0, 255), 1))
                            _x = toQtInt(x + self.draw_x_offset + clip_w)
                            painter.drawLine(_x, clip_y, _x, clip_y + clip_h)
                            painter.setFont(self.db_ft)
                            painter.drawText(_x + 5, clip_y, f'YRC Clip Progress: {progress:.2f}')
                            painter.setFont(self.ft)
                        painter.setClipRect(
                            toQtInt(x + self.draw_x_offset),
                            clip_y,
                            toQtInt(math.ceil(clip_w)),
                            clip_h,
                        )
                        c = QColor(color)
                        c.setAlpha(int(alpha))
                        painter.setPen(c)
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
                    line.content.strip(),
                )

            if self.ctx.debugging:
                center = self.height() // 2

                painter.setPen(QPen(QColor(255, 120, 120), 1))
                delta_ = -int(self.target_draw_offset - self.draw_offset) + center
                painter.drawLine(self.width() - 200, delta_, self.width(), delta_)
                painter.setFont(self.db_ft)
                painter.drawText(self.width() - 200, delta_ + 15, 'Offset Target')
                
                painter.setPen(QPen(QColor(120, 255, 255), 1))
                painter.drawLine(self.width() - 200, center, self.width(), center)
                painter.drawText(self.width() - 200, center + 15, 'Offset')

                painter.setPen(QPen(QColor(255, 75, 255), 1))
                delta_ = -int(self.target_acc) + center
                painter.drawLine(self.width() - 400, delta_, self.width() - 200, delta_)
                painter.drawText(self.width() - 400, delta_ + 15, 'Acceleration Target')

                painter.setPen(QPen(QColor(75, 75, 255), 1))
                delta_ = -int(self.target_acc - self.acc) + center
                painter.drawLine(self.width() - 400, delta_, self.width() - 200, delta_)
                painter.drawText(self.width() - 400, delta_ + 15, 'Acceleration')

            translation_text = (
                self._translationTextForLine(line, use_yrc)
                if self._shouldDrawTranslationForLine(line, use_yrc, is_current_line)
                else ''
            )
            if translation_text:
                self.translation_timer.target_value = (
                    1.0 if self.ctx.cfg.show_translation else 0.0
                )
            cur = self.translation_timer.current_value * 0.6
            if translation_text and cur > 0.0:
                painter.setFont(self.tft)
                painter.setPen(
                    QColor(255, 255, 255, int(alpha * cur))
                    if theme.isDark()
                    else QColor(0, 0, 0, int(alpha * cur))
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
                        if theme.isDark()
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
                        info = float2time(self.hovering_lyric.time)
                        timetxt = (
                            f'{f'{info.minutes}'.zfill(2)}:{f'{info.seconds}'.zfill(2)}'
                        )
                    painter.drawText(
                        toQtInt(
                            self.width() - self.metri.horizontalAdvance(timetxt) - 5
                        ),
                        toQtInt(y),
                        timetxt,
                    )

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
            self._player.setPosition(self.hovering_lyric.time)
            self.selecting = False
            self.hovering_lyric = None
            self.mouse_pos = None
        return super().mousePressEvent(event) 
