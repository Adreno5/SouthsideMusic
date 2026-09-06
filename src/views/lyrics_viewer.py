from __future__ import annotations

import logging

import math
import time

from core.app_context import AppContext

from core.downloader import asyncTask
from imports import (
    REFRESH_RATE_CHANGED,
    REPAINT,
    QEnterEvent,
    QEvent,
    QPen,
    QPointF,
    QRectF,
    Qt,
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
    BEAT_POINT
)
from imports import QWidget

from core.qt_utils import toQtInt
from core.time_format import float2time
from core.color import mixColor
from core import theme
from core.smooth import EaseOutTimer
from core.lyrics import LyricInfo, YRCLyricInfo
from services.events.events import (
    PLAY_STORABLE,
)

_HORIZONTAL_SCROLL_MARGIN = 12.0
_X_SCROLL_SMOOTH_DURATION = 0.3
_X_SCROLL_SMOOTH_POWER = 2
_X_SCROLL_FLIP_RATIO = 0.95


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
        self._cfg = ctx.config
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

        self._translation_lookup_key: tuple[int, int] | None = None
        self._translation_by_time: dict[int, str] = {}
        self._shifted_translation_by_time: dict[int, str] = {}
        self._translation_timing_shifted = False
        self._text_width_map: dict[str, float] = {}

        self.refresh_rate = max(60, ctx.app.primaryScreen().refreshRate() / 2)
        self._logger.info(f'{self.refresh_rate=}')

        self.delta = 1 / self.refresh_rate

        self.hovering = False

        self.translation_timer = EaseOutTimer(0.4, 4)

        self.x_scroll_timer = EaseOutTimer(
            _X_SCROLL_SMOOTH_DURATION, _X_SCROLL_SMOOTH_POWER
        )
        self._x_scroll_current_index = -1

        self._view_position = 0.0
        self._view_lines: list[LyricInfo | YRCLyricInfo] = []
        self._view_current_index = -1
        self._view_use_yrc = False
        self._view_y_offsets: list[float] = []
        self._view_top_offset = 0.0
        self._view_total_height = 0.0
        self._shown_lines: list[int] = []
        self._line_alphas: dict[int, EaseOutTimer] = {}
        
        self.beat_flash_timer = EaseOutTimer(0.6, 2)
        self.beat_flash_timer.target_value = 0

        self.last_lyric: YRCLyricInfo | LyricInfo | None = None

        event_bus.subscribe(REFRESH_RATE_CHANGED, self._onRefreshRateChanged)
        event_bus.subscribe(REPAINT, self._onRepaintTick)
        event_bus.subscribe(PLAY_STORABLE, lambda _: self.prewarmFontMetrics())
        event_bus.subscribe(BEAT_POINT, self._onBeatPoint)
        
    def _onBeatPoint(self):
        if not self.ctx.config.beat_detection_visual_lyrics:
            return
        self.beat_flash_timer.current_value = 1

    def prewarmFontMetrics(self):
        self._text_width_map.clear()
        asyncTask(self._doPrewarm, (), self)

    def _doPrewarm(self):
        time.sleep(2)
        all_texts: set[str] = set()
        for line in self._mgr.parsed:
            all_texts.add(line.content)
        for line in self._transmgr.parsed:
            all_texts.add(line.content)
        for line in self._ymgr.parsed:
            all_texts.add(line.content)
            for char in line.chars:
                all_texts.add(char.char)
        for text in all_texts:
            self._text_width_map[text] = self.metri.horizontalAdvance(text)

    def _onRepaintTick(self, multiple_factor: float = 1.0) -> None:
        self.updateDatas(multiple_factor)

    def updateDatas(self, multiple_factor: float = 1.0) -> None:
        self._updateViewLayout(multiple_factor)
        self.update()

    def _viewPosition(self) -> float:
        return self.ctx.playing_manager.getDisplaySmoothPosition()

    def _updateViewLayout(self, multiple_factor: float = 1.0) -> None:
        position = self._viewPosition()
        lines, current_index, use_yrc = self._lyricsForPosition(position)
        if not lines:
            self._view_position = position
            self._view_lines = []
            self._view_current_index = -1
            self._view_use_yrc = False
            self._view_y_offsets = []
            self._view_top_offset = 0.0
            self._view_total_height = 0.0
            self._shown_lines = []
            return

        self.translation_timer.target_value = (
            1.0 if self.ctx.config.show_translation else 0.0
        )
        y_offsets, total_height = self._lineOffsets(lines, use_yrc)
        if not self.selecting:
            self.target_draw_offset = (
                -y_offsets[current_index] if 0 <= current_index < len(y_offsets) else 0
            )
        elif time.time() - self.last_wheel > 3:
            self.selecting = False

        self.target_draw_offset = max(-total_height, min(0.0, self.target_draw_offset))
        self._updateDrawOffset(multiple_factor)
        if not all(
            math.isfinite(value)
            for value in (self.draw_offset, self.target_draw_offset, self.acc)
        ):
            self.draw_offset = 0
            self.target_draw_offset = 0
            self.acc = 0

        current_baseline = self._currentBaseline(lines, current_index, use_yrc)
        top_offset = self.draw_offset + current_baseline
        self._view_position = position
        self._view_lines = lines
        self._view_current_index = current_index
        self._view_use_yrc = use_yrc
        self._view_y_offsets = y_offsets
        self._view_top_offset = top_offset
        self._view_total_height = total_height
        self._shown_lines = self._visibleIndexes(lines, y_offsets, top_offset)
        for i in self._shown_lines:
            if i not in self._line_alphas:
                self._line_alphas[i] = EaseOutTimer(0.2, 2)
            self._line_alphas[i].target_value = 255 if i == current_index else 120
        for i in list(self._line_alphas):
            if i not in self._shown_lines:
                self._line_alphas.pop(i)
        self._updateXScroll(lines, current_index, position)

    def _onRefreshRateChanged(self):
        self.refresh_rate = max(60, self._app.primaryScreen().refreshRate() / 2)
        self._logger.info(f'{self.refresh_rate=}')

        self.delta = 1 / self.refresh_rate

    def _hasTranslation(self) -> bool:
        return (
            bool(self._transmgr.parsed) if self.ctx.config.show_translation else False
        )

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

    def _lyricsForPosition(
        self, position: float
    ) -> tuple[list[LyricInfo | YRCLyricInfo], int, bool]:
        use_yrc = self._ymgr.hasYrcTiming()
        if use_yrc:
            return self._ymgr.parsed, self._ymgr.getCurrentIndex(position), use_yrc  # type: ignore
        return self._mgr.parsed, self._mgr.getCurrentIndex(position), use_yrc  # type: ignore

    def _lineOffsets(
        self,
        lines: list[LyricInfo | YRCLyricInfo],
        use_yrc: bool,
    ) -> tuple[list[float], float]:
        y_offsets: list[float] = []
        y = 0.0
        for line in lines:
            y_offsets.append(y)
            has_trans = bool(self._translationTextForLine(line, use_yrc))
            y += self._lineStep(has_trans)
        return y_offsets, y

    def _currentBaseline(
        self,
        lines: list[LyricInfo | YRCLyricInfo],
        current_index: int,
        use_yrc: bool,
    ) -> float:
        current_has_trans = (
            bool(self._translationTextForLine(lines[current_index], use_yrc))
            if 0 <= current_index < len(lines)
            else False
        )
        return self._currentLineBaseline(current_has_trans)

    def _updateDrawOffset(self, multiple_factor: float = 1.0) -> None:
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

    def _xScrollClipWidth(self) -> float:
        return max(1.0, float(self.width()) - _HORIZONTAL_SCROLL_MARGIN)

    def _updateXScroll(
        self,
        lines: list[LyricInfo | YRCLyricInfo],
        current_index: int,
        position: float,
    ) -> None:
        if current_index < 0 or current_index >= len(lines):
            self._x_scroll_current_index = -1
            self.x_scroll_timer.reset()
            self.draw_x_offset = 0.0
            return
        if current_index != self._x_scroll_current_index:
            self._x_scroll_current_index = current_index
            self.x_scroll_timer.reset()

        line = lines[current_index]
        content = line.content.strip()
        text_width = self._textWidth(content)
        clip_width = self._xScrollClipWidth()
        overflow = max(0.0, text_width - clip_width)
        if overflow <= 0:
            self.x_scroll_timer.reset()
            self.draw_x_offset = 0.0
            return

        has_word_timing = bool(
            self._view_use_yrc
            and not line.isMetadata
            and getattr(line, 'chars', None)
        )
        if has_word_timing:
            _ratio, clip_w = self._yrcClipPayload(line, position)
            page_width = clip_width * _X_SCROLL_FLIP_RATIO
            page = int(clip_w // page_width)
            target = max(-overflow, min(0.0, -page * page_width))
            self.x_scroll_timer.target_value = target
            self.draw_x_offset = self.x_scroll_timer.current_value
        else:
            progress = self._linePlaybackProgress(lines, current_index, position)
            eased = 1.0 - pow(1.0 - progress, 3)
            self.draw_x_offset = -overflow * eased

    def _linePlaybackProgress(
        self,
        lines: list[LyricInfo | YRCLyricInfo],
        current_index: int,
        position: float,
    ) -> float:
        if current_index < 0 or current_index >= len(lines):
            return 0.0
        line = lines[current_index]
        if current_index + 1 < len(lines):
            next_time = lines[current_index + 1].time
        else:
            next_time = line.time + max(
                float(getattr(line, 'duration', 0.0)),
                3.0,
            )
        line_duration = getattr(line, 'duration', 0.0)
        if line_duration > 0:
            next_time = max(next_time, line.time + line_duration)
        duration = max(0.001, next_time - line.time)
        return max(0.0, min(1.0, (position - line.time) / duration))

    def _visibleIndexes(
        self,
        lines: list[LyricInfo | YRCLyricInfo],
        y_offsets: list[float],
        top_offset: float,
    ) -> list[int]:
        shown: list[int] = []
        for i in range(len(lines)):
            y_pos = top_offset + y_offsets[i]
            line_bottom = y_pos + self.font_height
            if line_bottom >= 0 and y_pos - self.font_height <= self.height():
                shown.append(i)
        return shown

    def _colorPayload(self, color: QColor) -> dict[str, int]:
        return {
            'r': color.red(),
            'g': color.green(),
            'b': color.blue(),
            'a': color.alpha(),
        }

    def _primaryColorForLine(
        self,
        line: LyricInfo | YRCLyricInfo,
        is_current_line: bool,
        alpha: float,
    ) -> QColor:
        if is_current_line:
            if line.isMetadata:
                tar_color = QColor(255, 255, 255)
            else:
                tar_color = QColor(255, 255, 255) if theme.isDark() else QColor(0, 0, 0)
        else:
            tar_color = (
                QColor(240, 240, 240, 120)
                if theme.isDark()
                else QColor(55, 55, 55, 120)
            )
        tar_color.setAlpha(int(alpha))

        return (
            mixColor(
                self._mwindow.song_theme, tar_color, self._cfg.background_ratio / 2
            )
            if self._mwindow and self._mwindow.song_theme
            else tar_color
        )

    def _translationColor(self, alpha: float) -> QColor:
        cur = self.translation_timer.current_value * 0.6
        return (
            QColor(255, 255, 255, int(alpha * cur))
            if theme.isDark()
            else QColor(0, 0, 0, int(alpha * cur))
        )

    def _textWidth(self, text: str) -> float:
        width = self._text_width_map.get(text)
        if width is None:
            width = self.metri.horizontalAdvance(text)
            self._text_width_map[text] = width
        return width

    def _yrcClipPayload(
        self,
        line: LyricInfo | YRCLyricInfo,
        position: float,
    ) -> tuple[float, float]:
        if not isinstance(line, YRCLyricInfo) or line.isMetadata:
            return 0.0, 0.0

        content = line.content.strip()
        if not content:
            return 0.0, 0.0

        total_width = self._textWidth(content)
        if total_width <= 0:
            return 0.0, 0.0
        if not line.chars:
            if line.duration <= 0:
                return 0.0, 0.0
            ratio = max(0.0, min(1.0, (position - line.time) / line.duration))
            return ratio, total_width * ratio

        filled_width = 0.0
        for ch in line.chars:
            text_width = self._textWidth(ch.char)
            if ch.duration <= 0:
                progress = 1.0 if position >= ch.start else 0.0
            else:
                progress = (position - ch.start) / ch.duration
            progress = max(0.0, min(1.0, progress))
            filled_width += text_width * progress

        ratio = max(0.0, min(1.0, filled_width / total_width))
        return ratio, filled_width

    def lyricLayoutPayload(
        self,
    ) -> dict[str, object]:
        position = self._view_position
        lines = self._view_lines
        current_index = self._view_current_index
        use_yrc = self._view_use_yrc
        if not lines:
            return {
                'schema': 'southside_lyric_layout_v1',
                'ready': False,
                'position': position,
                'lines': [],
            }

        y_offsets = self._view_y_offsets
        total_height = self._view_total_height
        top_offset = self._view_top_offset
        center_y = self.height() * 0.5
        shown = self._shown_lines

        payload_lines: list[dict[str, object]] = []
        current_yrc_clip_ratio = 0.0
        current_yrc_clip_width = 0.0
        for i in shown:
            line = lines[i]
            is_current_line = i == current_index
            timer = self._line_alphas.get(i)
            alpha = (
                timer.current_value
                if timer is not None
                else (255.0 if is_current_line else 120.0)
            )

            baseline_y = top_offset + y_offsets[i]
            translation_text = (
                self._translationTextForLine(line, use_yrc)
                if self._shouldDrawTranslationForLine(line, use_yrc, is_current_line)
                else ''
            )
            translation_baseline_y = (
                baseline_y + self.metri.descent() + 2 + self.tmetri.ascent()
            )
            primary_color = self._primaryColorForLine(line, is_current_line, alpha)
            yrc_clip_ratio, yrc_clip_width = self._yrcClipPayload(line, position)
            if is_current_line:
                current_yrc_clip_ratio = yrc_clip_ratio
                current_yrc_clip_width = yrc_clip_width

            hit_bottom = baseline_y + self.metri.descent() + self.theight + 5
            is_hovered = bool(
                self.mouse_pos
                and self.mouse_pos.y() > baseline_y - self.metri.ascent()
                and self.mouse_pos.y() < hit_bottom
            )
            hover_time_text = ''
            hover_time_x = 0.0
            if is_hovered:
                info = float2time(line.time)
                hover_time_text = f'{info.minutes:02d}:{info.seconds:02d}'
                hover_time_x = (
                    self.width() - self.metri.horizontalAdvance(hover_time_text) - 5
                )

            debug_center = self.height() // 2
            debug_offset_target_y = (
                -int(self.target_draw_offset - self.draw_offset) + debug_center
            )
            debug_acc_target_y = -int(self.target_acc) + debug_center
            debug_acc_y = -int(self.target_acc - self.acc) + debug_center

            payload_lines.append(
                {
                    'index': i,
                    'offset': i - current_index,
                    'time': line.time,
                    'text': line.content.strip(),
                    'translation': translation_text,
                    'is_current': is_current_line,
                    'is_metadata': line.isMetadata,
                    'is_hovered': is_hovered,
                    'draw_text': line.content.strip(),
                    'hover_time_text': hover_time_text,
                    'hover_time_x': hover_time_x,
                    'debug_center_y': debug_center,
                    'debug_offset_target_y': debug_offset_target_y,
                    'debug_acc_target_y': debug_acc_target_y,
                    'debug_acc_y': debug_acc_y,
                    'alpha': int(alpha),
                    'alpha_ratio': alpha / 255,
                    'baseline_y': baseline_y,
                    'baseline_y_from_center': baseline_y - center_y,
                    'top_y': baseline_y - self.metri.ascent(),
                    'top_y_from_center': baseline_y - self.metri.ascent() - center_y,
                    'bottom_y': baseline_y + self.metri.descent(),
                    'bottom_y_from_center': baseline_y
                    + self.metri.descent()
                    - center_y,
                    'x': self.draw_x_offset if is_current_line else 0.0,
                    'primary_color': self._colorPayload(primary_color),
                    'yrc_base_color': self._colorPayload(
                        QColor(
                            primary_color.red(),
                            primary_color.green(),
                            primary_color.blue(),
                            120,
                        )
                    ),
                    'yrc_clip_ratio': yrc_clip_ratio,
                    'yrc_clip_width': yrc_clip_width,
                    'translation_baseline_y': translation_baseline_y,
                    'translation_baseline_y_from_center': translation_baseline_y
                    - center_y,
                    'translation_alpha': self._translationColor(alpha).alpha(),
                    'translation_color': self._colorPayload(
                        self._translationColor(alpha)
                    ),
                }
            )

        return {
            'schema': 'southside_lyric_layout_v1',
            'ready': True,
            'position': position,
            'use_yrc': use_yrc,
            'current_index': current_index,
            'canvas_width': self.width(),
            'canvas_height': self.height(),
            'center_y': center_y,
            'x': self.draw_x_offset,
            'draw_offset': self.draw_offset,
            'target_draw_offset': self.target_draw_offset,
            'acceleration': self.acc,
            'total_height': total_height,
            'primary_font_family': self.ft.family(),
            'primary_font_point_size': self.ft.pointSizeF(),
            'primary_font_size_px': self.font_height,
            'primary_font_height': self.font_height,
            'primary_font_ascent': self.metri.ascent(),
            'primary_font_descent': self.metri.descent(),
            'translation_font_family': self.tft.family(),
            'translation_font_point_size': self.tft.pointSizeF(),
            'translation_font_size_px': self.theight,
            'translation_font_height': self.theight,
            'translation_font_ascent': self.tmetri.ascent(),
            'translation_font_descent': self.tmetri.descent(),
            'translation_progress': self.translation_timer.current_value,
            'current_yrc_clip_ratio': current_yrc_clip_ratio,
            'current_yrc_clip_width': current_yrc_clip_width,
            'lines': payload_lines,
        }

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.mouse_pos = event.position()
        return super().mouseMoveEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        if not self.isVisible():
            return

        position = self._view_position
        lines = self._view_lines
        current_index = self._view_current_index
        use_yrc = self._view_use_yrc
        if not lines:
            return
        y_offsets = self._view_y_offsets
        top_offset = self._view_top_offset

        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        painter.setFont(self.ft)
        self.hovering_lyric = None

        for i in self._shown_lines:
            line = lines[i]
            is_current_line = i == current_index
            timer = self._line_alphas.get(i)
            alpha = int(
                timer.current_value
                if timer is not None
                else (255 if is_current_line else 120)
            )
            y = top_offset + y_offsets[i]
            x = 0.0
            text_x = self.draw_x_offset if is_current_line else 0.0
            color = self._primaryColorForLine(line, is_current_line, alpha)
            if is_current_line and self.ctx.debugging:
                color = QColor(0, 255, 0)

            if self.ctx.debugging:
                painter.setPen(QPen(QColor(255, 0, 0), 1))
                painter.drawLine(0, toQtInt(y), self.width(), toQtInt(y))
                painter.setFont(self.db_ft)
                painter.drawText(10, toQtInt(y + 15), f'Baseline {toQtInt(y)}')
                painter.setFont(self.ft)

            content = line.content.strip()
            if is_current_line and use_yrc and not line.isMetadata and content:
                base_color = QColor(color)
                base_color.setAlpha(90 + int(30 * self.beat_flash_timer.current_value))
                painter.setPen(base_color)
                painter.drawText(toQtInt(text_x), toQtInt(y), content)

                yrc_current_ratio, clip_w = self._yrcClipPayload(line, position)
                self.current_index = i
                self.yrc_current_ratio = yrc_current_ratio
                if clip_w > 0:
                    clip_y = toQtInt(y - self.metri.ascent())
                    clip_h = toQtInt(self.font_height)
                    painter.save()
                    if self.ctx.debugging and 0.0 < yrc_current_ratio < 1.0:
                        painter.setPen(QPen(QColor(120, 0, 255), 1))
                        _x = toQtInt(text_x + clip_w)
                        painter.drawLine(_x, clip_y, _x, clip_y + clip_h)
                        painter.setFont(self.db_ft)
                        painter.drawText(
                            _x + 5,
                            clip_y,
                            f'YRC Clip Progress: {yrc_current_ratio:.3f}',
                        )
                        painter.setFont(self.ft)
                    painter.setClipRect(
                        QRectF(text_x, clip_y, clip_w, clip_h),
                    )
                    c = QColor(color)
                    c.setAlpha(200 + int(55 * self.beat_flash_timer.current_value))
                    painter.setPen(c)
                    painter.drawText(toQtInt(text_x), toQtInt(y), content)
                    painter.restore()
            else:
                color.setAlpha(
                    (color.alpha() - 10 + int(10 * self.beat_flash_timer.current_value)) if not is_current_line else 
                    (color.alpha() - 55 + int(55 * self.beat_flash_timer.current_value))
                )
                painter.setPen(color)
                painter.drawText(
                    toQtInt(text_x),
                    toQtInt(y),
                    content,
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
            translation_color = self._translationColor(alpha)
            if translation_text and translation_color.alpha() > 0:
                translation_y = y + self.metri.descent() + 2 + self.tmetri.ascent()
                painter.setFont(self.tft)
                painter.setPen(translation_color)
                painter.drawText(
                    toQtInt(x),
                    toQtInt(translation_y),
                    translation_text,
                )
                painter.setFont(self.ft)

            hit_bottom = y + self.metri.descent() + self.theight + 5
            is_hovered = bool(
                self.mouse_pos
                and self.mouse_pos.y() > y - self.metri.ascent()
                and self.mouse_pos.y() < hit_bottom
            )
            if is_hovered:
                self.hovering_lyric = line
                if self.selecting:
                    painter.setBrush(
                        QColor(255, 255, 255, 100)
                        if theme.isDark()
                        else QColor(0, 0, 0, 100)
                    )
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawRoundedRect(
                        toQtInt(x),
                        toQtInt(y - self.metri.ascent()),
                        toQtInt(self.width() - x),
                        toQtInt(self.font_height),
                        5,
                        5,
                    )
                    painter.setPen(color)
                    info = float2time(line.time)
                    timetxt = f'{info.minutes:02d}:{info.seconds:02d}'
                    hover_time_x = (
                        self.width() - self.metri.horizontalAdvance(timetxt) - 5
                    )
                    painter.drawText(
                        toQtInt(hover_time_x),
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
