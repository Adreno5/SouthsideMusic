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
from services.events.events import (
    PLAY_STORABLE,
    START_PROGRESS_LOADING,
    STOP_PROGRESS_LOADING,
    UPDATE_LOADING_PROGRESS,
)


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
        self._layout_payload: dict[str, object] = {
            'schema': 'southside_lyric_layout_v1',
            'ready': False,
            'lines': [],
        }

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
        self.ctx.addScheduledTask(self._prewarmFontMetricsOnMainThread, all_texts)

    def _prewarmFontMetricsOnMainThread(self, all_texts: set[str]) -> None:
        if not all_texts:
            self._lyrics_ready = True
            return
        event_bus.emit(START_PROGRESS_LOADING)
        for i, text in enumerate(all_texts):
            self.metri.horizontalAdvance(text)
            event_bus.emit(UPDATE_LOADING_PROGRESS, i / len(all_texts))
        event_bus.emit(STOP_PROGRESS_LOADING)
        self._lyrics_ready = True

    def _onRepaintTick(self, multiple_factor: float = 1.0) -> None:
        self.updateDatas(multiple_factor)

    def updateDatas(self, multiple_factor: float = 1.0) -> None:
        self._layout_payload = self.lyricLayoutPayload(
            update_animation=True,
            multiple_factor=multiple_factor,
        )
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

    def _lyricsForPosition(
        self, position: float
    ) -> tuple[list[LyricInfo | YRCLyricInfo], int, bool]:
        use_yrc = bool(self._ymgr.parsed)
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

    def _colorFromPayload(self, value: object) -> QColor:
        if not isinstance(value, dict):
            return QColor(0, 0, 0, 0)
        return QColor(
            int(value.get('r', 0)),
            int(value.get('g', 0)),
            int(value.get('b', 0)),
            int(value.get('a', 0)),
        )

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

        total_width = max(1.0, self.metri.horizontalAdvance(content))
        if not line.chars:
            if line.duration <= 0:
                return 0.0, 0.0
            ratio = max(0.0, min(1.0, (position - line.time) / line.duration))
            return ratio, total_width * ratio

        filled_width = 0.0
        for ch in line.chars:
            text_width = self.metri.horizontalAdvance(ch.char)
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
        position: float | None = None,
        update_animation: bool = True,
        multiple_factor: float = 1.0,
    ) -> dict[str, object]:
        if position is None:
            position = self.ctx.playing_manager.getDisplayPosition()

        if not self._lyrics_ready:
            return {'schema': 'southside_lyric_layout_v1', 'ready': False, 'lines': []}

        lines, current_index, use_yrc = self._lyricsForPosition(position)
        if not lines:
            return {
                'schema': 'southside_lyric_layout_v1',
                'ready': False,
                'position': position,
                'lines': [],
            }

        self.translation_timer.target_value = (
            1.0 if self.ctx.cfg.show_translation else 0.0
        )

        y_offsets, total_height = self._lineOffsets(lines, use_yrc)

        if update_animation:
            if not self.selecting:
                self.target_draw_offset = (
                    -y_offsets[current_index]
                    if 0 <= current_index < len(y_offsets)
                    else 0
                )
            else:
                if time.time() - self.last_wheel > 3:
                    self.selecting = False

            if self.target_draw_offset > 0:
                self.target_draw_offset = 0
            if self.target_draw_offset < -total_height:
                self.target_draw_offset = -total_height

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
        center_y = self.height() * 0.5
        shown = self._visibleIndexes(lines, y_offsets, top_offset)
        self._shown_lines = shown
        self.hovering_lyric = None

        payload_lines: list[dict[str, object]] = []
        current_yrc_clip_ratio = 0.0
        current_yrc_clip_width = 0.0
        for i in shown:
            line = lines[i]
            if not self._line_alphas.get(i):
                self._line_alphas[i] = EaseOutTimer(0.2, 2)
            timer = self._line_alphas[i]
            is_current_line = i == current_index
            timer.target_value = 255 if is_current_line else 120
            alpha = timer.current_value

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
                self.current_index = i
                self.yrc_current_ratio = yrc_clip_ratio

            hit_bottom = baseline_y + self.metri.descent() + self.theight + 5
            is_hovered = bool(
                self.mouse_pos
                and self.mouse_pos.y() > baseline_y - self.metri.ascent()
                and self.mouse_pos.y() < hit_bottom
            )
            hover_time_text = ''
            hover_time_x = 0.0
            if is_hovered:
                self.hovering_lyric = line
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
                    'x': self.draw_x_offset,
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

        to_remove = []
        for key in self._line_alphas:
            if key not in shown:
                to_remove.append(key)
        for item in to_remove:
            self._line_alphas.pop(item)

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

    def _updateShownLines(self):
        self._layout_payload = self.lyricLayoutPayload(update_animation=False)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        payload = self._layout_payload
        if not payload.get('ready'):
            return
        if not self.isVisible():
            return

        use_yrc = bool(payload.get('use_yrc', False))

        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        painter.setFont(self.ft)

        for item in payload.get('lines', []):  # type: ignore
            if not isinstance(item, dict):
                continue
            is_current_line = bool(item.get('is_current', False))
            is_metadata = bool(item.get('is_metadata', False))
            y = float(item.get('baseline_y', 0.0))
            x = float(item.get('x', self.draw_x_offset))
            alpha = int(item.get('alpha', 0))
            color = self._colorFromPayload(item.get('primary_color', {}))
            if is_current_line and self.ctx.debugging:
                color = QColor(0, 255, 0)

            if self.ctx.debugging:
                painter.setPen(QPen(QColor(255, 0, 0), 1))
                painter.drawLine(0, toQtInt(y), self.width(), toQtInt(y))
                painter.setFont(self.db_ft)
                painter.drawText(10, toQtInt(y + 15), f'Baseline {toQtInt(y)}')
                painter.setFont(self.ft)

            if is_current_line and use_yrc and not is_metadata:
                content = str(item.get('draw_text', ''))
                base_color = self._colorFromPayload(item.get('yrc_base_color', {}))
                painter.setPen(base_color)
                painter.drawText(toQtInt(x), toQtInt(y), content)

                clip_w = float(item.get('yrc_clip_width', 0.0))
                yrc_current_ratio = float(item.get('yrc_clip_ratio', 0.0))
                if clip_w > 0:
                    clip_y = toQtInt(y - self.metri.ascent())
                    clip_h = toQtInt(self.font_height)
                    painter.save()
                    if self.ctx.debugging and 0.0 < yrc_current_ratio < 1.0:
                        painter.setPen(QPen(QColor(120, 0, 255), 1))
                        _x = toQtInt(x + clip_w)
                        painter.drawLine(_x, clip_y, _x, clip_y + clip_h)
                        painter.setFont(self.db_ft)
                        painter.drawText(
                            _x + 5,
                            clip_y,
                            f'YRC Clip Progress: {yrc_current_ratio:.2f}',
                        )
                        painter.setFont(self.ft)
                    painter.setClipRect(
                        toQtInt(x),
                        clip_y,
                        toQtInt(math.ceil(clip_w)),
                        clip_h,
                    )
                    c = QColor(color)
                    c.setAlpha(alpha)
                    painter.setPen(c)
                    painter.drawText(toQtInt(x), toQtInt(y), content)
                    painter.restore()
            else:
                painter.setPen(color)
                painter.drawText(
                    toQtInt(x),
                    toQtInt(y),
                    str(item.get('draw_text', '')),
                )

            if self.ctx.debugging:
                center = int(item.get('debug_center_y', 0))

                painter.setPen(QPen(QColor(255, 120, 120), 1))
                delta_ = int(item.get('debug_offset_target_y', center))
                painter.drawLine(self.width() - 200, delta_, self.width(), delta_)
                painter.setFont(self.db_ft)
                painter.drawText(self.width() - 200, delta_ + 15, 'Offset Target')

                painter.setPen(QPen(QColor(120, 255, 255), 1))
                painter.drawLine(self.width() - 200, center, self.width(), center)
                painter.drawText(self.width() - 200, center + 15, 'Offset')

                painter.setPen(QPen(QColor(255, 75, 255), 1))
                delta_ = int(item.get('debug_acc_target_y', center))
                painter.drawLine(self.width() - 400, delta_, self.width() - 200, delta_)
                painter.drawText(self.width() - 400, delta_ + 15, 'Acceleration Target')

                painter.setPen(QPen(QColor(75, 75, 255), 1))
                delta_ = int(item.get('debug_acc_y', center))
                painter.drawLine(self.width() - 400, delta_, self.width() - 200, delta_)
                painter.drawText(self.width() - 400, delta_ + 15, 'Acceleration')

            translation_text = str(item.get('translation', ''))
            if translation_text and int(item.get('translation_alpha', 0)) > 0:
                painter.setFont(self.tft)
                painter.setPen(
                    self._colorFromPayload(item.get('translation_color', {}))
                )
                painter.drawText(
                    toQtInt(x),
                    toQtInt(item.get('translation_baseline_y', y)),
                    translation_text,
                )
                painter.setFont(self.ft)

            if bool(item.get('is_hovered', False)):
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
                    timetxt = str(item.get('hover_time_text', ''))
                    painter.drawText(
                        toQtInt(float(item.get('hover_time_x', 0.0))),
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
