from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from views.playing_page import PlayingPage

from imports import QEvent, QPointF, Qt, QTimer
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

from utils.time_util import float2time
from utils.color_util import mixColor
from utils import darkdetect_util as darkdetect
from utils.lyric_util import LyricInfo, YRCLyricInfo

if TYPE_CHECKING:
    from utils.play_util import AudioPlayer

class LyricsViewer(QWidget):
    def __init__(
        self,
        app,
        mgr,
        transmgr,
        ymgr,
        player: AudioPlayer,
        mwindow,
        harmony_font_family: str,
        cfg,
        dp: PlayingPage
    ):
        super().__init__()
        self._app = app
        self._mgr = mgr
        self._transmgr = transmgr
        self._ymgr = ymgr
        self._player = player
        self._mwindow = mwindow
        self._cfg = cfg
        self._dp = dp

        self.draw_offset: float = 0
        self.target_draw_offset: float = 0

        self.acc: float = 0
        self.target_acc: float = 0

        self.ft = QFont(harmony_font_family, 14)
        self.font_height = QFontMetricsF(self.ft).height()
        self.metri = QFontMetricsF(self.ft)

        self.tft = QFont(harmony_font_family, 10)
        self.theight = QFontMetricsF(self.tft).height()
        self.tmetri = QFontMetricsF(self.tft)

        self.selecting: bool = False
        self.hovering_lyric: LyricInfo | None = None
        self.mouse_pos: QPointF | None = None
        self.last_wheel: float = time.time()

        self.draw_x_offset: float = 0

        self.last_draw: int = time.perf_counter_ns()

        self.setMouseTracking(True)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.repaint)
        self.timer.start(int(1000 / app.primaryScreen().refreshRate()))

        self.delta = 1 / app.primaryScreen().refreshRate()

    def _hasTranslation(self) -> bool:
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
        self.hovering_lyric = None
        if not self._mgr.parsed:
            return

        now = time.perf_counter_ns()
        _elapsed: float = (now - self.last_draw) / 1_000_000_000
        self.last_draw = now
        multiple_factor = _elapsed / self.delta

        self.target_acc = (
            (self.target_draw_offset - self.draw_offset)
            * self.delta
            * self._cfg.lyrics_smooth_factor
        )
        self.acc += (
            (self.target_acc - self.acc)
            * self.delta
            * self._cfg.acceleration_smooth_factor
            / max(0.5, min(1, abs(self.target_acc - self.acc)))
            * multiple_factor
        )

        if self.draw_offset != self.target_draw_offset:
            self.draw_offset += self.acc
        if abs(self.target_draw_offset - self.draw_offset) < 0.01:
            self.draw_offset = self.target_draw_offset

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

        current_line = (
            self._ymgr.getCurrentLyric(position)
            if use_yrc
            else self._mgr.getCurrentLyric(position)
        )
        y = self.draw_offset + current_baseline
        for i, line in enumerate(lines):
            is_current_line = line == current_line
            if is_current_line:
                if line.get("isMetadata"):
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
            if is_current_line and use_yrc and not line.get("isMetadata"):
                y_line = cast(YRCLyricInfo, line)
                content = (y_line["content"] or line["content"]).strip()
                base_color = QColor(color)
                base_color.setAlpha(120)
                painter.setPen(base_color)
                painter.drawText(_toQtInt(self.draw_x_offset), _toQtInt(y), content)

                x = 0.0
                clip_y = _toQtInt(y - self.metri.ascent())
                clip_h = _toQtInt(self.font_height)
                for ch in y_line["chars"]:
                    text_width = self.metri.horizontalAdvance(ch["char"])
                    duration = ch["duration"]
                    if duration <= 0:
                        progress = 1.0 if position >= ch["start"] else 0.0
                    else:
                        progress = (position - ch["start"]) / duration
                    progress = max(0.0, min(1.0, progress))
                    clip_w = text_width * progress
                    if clip_w > 0:
                        painter.save()
                        painter.setClipRect(
                            _toQtInt(x + self.draw_x_offset),
                            clip_y,
                            _toQtInt(math.ceil(clip_w)),
                            clip_h,
                        )
                        painter.setPen(color)
                        painter.drawText(
                            _toQtInt(self.draw_x_offset), _toQtInt(y), content
                        )
                        painter.restore()
                    x += text_width
            else:
                painter.setPen(color)
                painter.drawText(
                    _toQtInt(self.draw_x_offset),
                    _toQtInt(y),
                    line["content"].strip(),
                )

            if self._transmgr.parsed:
                trans_time = line["time"]
                if use_yrc:
                    lrc_candidates = [
                        lrc_line
                        for lrc_line in self._mgr.parsed
                        if lrc_line["content"].strip() == line["content"].strip()
                    ]
                    lrc_line = min(
                        lrc_candidates,
                        key=lambda lrc_line: abs(lrc_line["time"] - line["time"]),
                        default=None,
                    )
                    if lrc_line:
                        trans_time = lrc_line["time"]
                painter.setFont(self.tft)
                painter.setPen(
                    QColor(255, 255, 255, 120)
                    if darkdetect.isDark()
                    else QColor(0, 0, 0, 120)
                )
                painter.drawText(
                    _toQtInt(self.draw_x_offset),
                    _toQtInt(y + self.metri.descent() + 2 + self.tmetri.ascent()),
                    self._transmgr.getCurrentLyric(trans_time)["content"].strip(),
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
                        _toQtInt(self.draw_x_offset),
                        _toQtInt(y - self.metri.ascent()),
                        _toQtInt(self.width() - self.draw_x_offset),
                        _toQtInt(self.font_height),
                        5,
                        5,
                    )
                    painter.setPen(color)
                    if self.hovering_lyric:
                        info = float2time(self.hovering_lyric["time"])
                    timetxt = f"{f'{info["minutes"]}'.zfill(2)}:{f'{info["seconds"]}'.zfill(2)}"
                    painter.drawText(
                        _toQtInt(
                            self.width() - self.metri.horizontalAdvance(timetxt) - 5
                        ),
                        _toQtInt(y),
                        timetxt,
                    )

            y += line_step

        painter.end()

    def leaveEvent(self, event: QEvent) -> None:
        self.mouse_pos = None
        self.selecting = False
        self.hovering_lyric = None
        return super().leaveEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        self.selecting = True
        self.target_draw_offset += event.angleDelta().y()
        self.last_wheel = time.time()
        return super().wheelEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.hovering_lyric and event.button() == Qt.MouseButton.LeftButton:
            self._player.setPosition(self.hovering_lyric["time"])
            self.selecting = False
            self.hovering_lyric = None
            self.mouse_pos = None
        return super().mousePressEvent(event)


def _toQtInt(value: float | int) -> int:
    import math as _math
    from functools import lru_cache as _lru_cache

    _QT_INT_MIN = -(2**31)
    _QT_INT_MAX = 2**31 - 1

    if not _math.isfinite(value):
        return 0
    return max(_QT_INT_MIN, min(_QT_INT_MAX, int(value)))
