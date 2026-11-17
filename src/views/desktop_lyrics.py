from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import cast, override

from PySide6.QtCore import QPoint, QRect, QRectF, QSize
from PySide6.QtGui import (
    QColor,
    QCursor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QRegion,
    QShowEvent,
    Qt,
    QWheelEvent,
)
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import CheckBox, FlowLayout, FluentIcon, PushButton, TitleLabel

from core import theme
from core.app_context import AppContext
from core.color import mixColor
from core.config import cfg
from core.i18n import bindText, tr
from core.lyrics import LyricInfo, YRCLyricInfo
from core.qt_utils import toQtInt
from core.smooth import EaseInOutTimer, EaseOutTimer
from services.events import Events, event_bus
from views.lyrics_viewer import LyricsViewer
from views.playing_page import _artists_text


class DesktopLyricsViewer(LyricsViewer):
    def __init__(
        self,
        ctx: AppContext,
    ):
        self.indentation_y: float = 0
        self.indentation: bool = False

        self.width_timer = EaseInOutTimer(0.5, 3)
        self.height_timer = EaseOutTimer(0.5, 3)
        self.slide_timer = EaseInOutTimer(0.5, 3)
        self._slide_distance: float = 0.0
        self._outgoing_line: LyricInfo | YRCLyricInfo | None = None
        self._outgoing_text: str = ''
        self._outgoing_translation: str = ''
        self.last_width_animation: float = -1

        self._pill_x: float = 0
        self._pill_w: float = 1
        self._pill_h: float = 1
        self.dragging: bool = False
        self.dragging_point: QPoint = QPoint(0, 0)
        self._grab_dx: float = 0
        self._draw_progress_ratio = 0.0
        self._title_line: LyricInfo | None = None
        self._title_artist: str = ''

        self.scr_size: QSize = ctx.app.primaryScreen().size()
        super().__init__(ctx)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.BypassWindowManagerHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(self.scr_size.width(), 65)
        self.move(0, 0)

        event_bus.subscribe(Events.SECOND_TICK, self._keepOnTop)
        event_bus.subscribe(Events.TICK_200MS, self._checkMouse)

        event_bus.unsubscribe(Events.REPAINT, self._onRepaintTick)
        event_bus.subscribe(Events.REPAINT_ALWAYS, self._onRepaintTick)
        event_bus.subscribe(
            Events.LYRICS_LINE_DURATION, lambda d: self._lyricsLineDuration(d)
        )

    def _lyricsLineDuration(self, d):
        self.width_timer.anim_cycle = d / 1000
        self.slide_timer.anim_cycle = d / 1000

    def _keepOnTop(self) -> None:
        if not self.isVisible():
            return
        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        user32.SetWindowPos(
            wintypes.HWND(hwnd),
            wintypes.HWND(-1),
            0,
            0,
            0,
            0,
            0x0002 | 0x0001 | 0x0010,
        )

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._keepOnTop()

    def _pillOffsetY(self) -> float:
        return self.indentation_y if cfg.desktop_lyrics_anchor == 'top-center' else 0.0

    def _checkMouse(self):
        self.indentation = QRect(
            int(self._pill_x) - 5,
            0,
            int(self._pill_w) + 5,
            int(self._pill_h),
        ).contains(self.mapFromGlobal(QCursor.pos()))

    def showMinimized(self) -> None:
        self.showNormal()

    def prewarmFontMetrics(self):
        pass

    def emitDebugInfo(self):
        event_bus.emit(
            Events.EMIT_DEBUG_INFO,
            'Desktop Lyrics Viewer',
            [
                f'{len(self._shown_lines)=}',
                f'{self.last_lyric=}',
                f'{self.indentation_y=}',
            ],
        )

    def _firstLyricTime(self) -> float:
        lines = self._ymgr.parsed if self._ymgr.hasYrcTiming() else self._mgr.parsed
        for line in lines:
            if line.content.strip() and not line.isMetadata:
                return line.time
        return float('inf')

    def _isPureMusicPlaceholder(self, position: float) -> bool:
        mgr = self._ymgr if self._ymgr.hasYrcTiming() else self._mgr
        if not mgr.parsed:
            return False
        return mgr.getCurrentLyric(position).content.strip() == '纯音乐，请欣赏'

    def _titleLine(self, position: float) -> LyricInfo | None:
        song = getattr(self._dp, 'cur', None)
        storable = song.storable if song else None
        if storable is None or not storable.name:
            return None
        if self._title_line is None:
            self._title_line = LyricInfo(time=0.0, content=storable.name)
        self._title_line.content = storable.name
        self._title_artist = _artists_text(storable)
        if position >= self._firstLyricTime() and not self._isPureMusicPlaceholder(
            position
        ):
            return None
        return self._title_line

    def _lyricsForPosition(
        self, position: float
    ) -> tuple[list[LyricInfo | YRCLyricInfo], int, bool]:
        title = self._titleLine(position)
        if title is None:
            return super()._lyricsForPosition(position)
        lines: list[LyricInfo | YRCLyricInfo] = [title]
        return lines, 0, False

    def _translationTextForLine(
        self,
        line: LyricInfo | YRCLyricInfo,
        use_yrc: bool | None = None,
    ) -> str:
        if self._title_line is not None and line is self._title_line:
            return self._title_artist
        return super()._translationTextForLine(line, use_yrc)

    def _currentLyricLine(
        self,
        position: float | None = None,
    ) -> YRCLyricInfo | LyricInfo | None:
        if position is None:
            position = self.ctx.playing_manager.getDisplayPosition()
        title = self._titleLine(position)
        if title is not None:
            return title
        if self._ymgr.hasYrcTiming():
            line = self._ymgr.getCurrentLyric(position)
            if line.content.strip():
                self.last_lyric = line
            return line
        if self._mgr.parsed:
            line = self._mgr.getCurrentLyric(position)
            if line.content.strip():
                self.last_lyric = line
            return line
        return None

    def _offsetLyricLine(
        self, position: float | None = None, offset: float | None = None
    ) -> YRCLyricInfo | LyricInfo | None:
        if position is None:
            position = self.ctx.playing_manager.getDisplayPosition()
        title = self._titleLine(position + offset)
        if title is not None:
            return title
        if self._ymgr.hasYrcTiming():
            line = self._ymgr.getCurrentLyric(position + offset)
            if line.content.strip():
                self.last_lyric = line
            return line
        if self._mgr.parsed:
            line = self._mgr.getCurrentLyric(position + offset)
            if line.content.strip():
                self.last_lyric = line
            return line
        return None

    def _hasCurrentLineTranslation(
        self,
        line: YRCLyricInfo | LyricInfo | None = None,
    ) -> bool:
        if line is None:
            line = self._currentLyricLine()
        use_yrc = self._ymgr.hasYrcTiming()
        if line and self._translationTextForLine(line, use_yrc):
            return True
        return bool(
            use_yrc
            and self.last_lyric
            and self._translationTextForLine(self.last_lyric, True)
        )

    def _hasTranslation(self) -> bool:
        return self._hasCurrentLineTranslation()

    def _lineStep(self, has_translation: bool = False) -> float:
        if self._transmgr.parsed:
            return self.font_height + self.theight + self.font_height * 0.75
        return self.font_height * 1.85

    def _currentLineChanged(
        self,
        lines: list[LyricInfo | YRCLyricInfo],
        current_index: int,
    ) -> bool:
        if not (0 <= current_index < len(lines)):
            return False
        if not (0 <= self._view_current_index < len(self._view_lines)):
            return True
        return self._view_lines[self._view_current_index] is not lines[current_index]

    def _startLineSlide(self) -> None:
        self._outgoing_line = None
        self._outgoing_text = ''
        self._outgoing_translation = ''
        if 0 <= self._view_current_index < len(self._view_lines):
            line = self._view_lines[self._view_current_index]
            if line.content.strip():
                self._outgoing_line = line
                self._outgoing_text = line.content.strip()
                self._outgoing_translation = self._translationTextForLine(
                    line, self._view_use_yrc
                )
        self.slide_timer.reset()
        self._slide_distance = max(self._pill_w, self.width_timer.target_value)
        self.slide_timer.current_value = self._slide_distance

    @override
    def _updateDrawOffset(
        self,
        lines: list[LyricInfo | YRCLyricInfo],
        current_index: int,
        multiple_factor: float = 1.0,
    ) -> None:
        if self.last_target_draw_offset != self.target_draw_offset:
            duration = int(self._cfg.lyrics_scrolling_duration)
            if 0 <= current_index < len(lines) - 1:
                line_duration = max(
                    1,
                    int(
                        (lines[current_index + 1].time - lines[current_index].time)
                        * 1000
                    ),
                )
                duration = min(duration, line_duration)
            event_bus.emit(Events.LYRICS_LINE_DURATION, duration)
            if self._currentLineChanged(lines, current_index):
                self._startLineSlide()
            self.last_target_draw_offset = self.target_draw_offset
        self.draw_offset = self.target_draw_offset
        self.target_acc = 0.0
        self.acc = 0.0

    @override
    def _renderLinePayload(self, index: int) -> dict[str, object]:
        payload = super()._renderLinePayload(index)
        content = self._view_lines[index].content.strip()
        text_width = self._textWidth(content)
        if text_width <= self._xScrollClipWidth():
            payload['x'] = float(toQtInt((self._pill_w - text_width) * 0.5))
            translation = cast(str, payload['translation'])
            if translation:
                translation_width = self.tmetri.horizontalAdvance(translation)
                payload['translation_x'] = float(
                    toQtInt((self._pill_w - translation_width) * 0.5)
                )
        return payload

    @override
    def _contentWidth(self) -> float:
        return self._pill_w

    @override
    def _contentHeight(self) -> float:
        return self._pill_h

    def updateDatas(self, multiple_factor: float = 1.0) -> None:
        playing = self.ctx.player.isPlaying()
        self.indentation_y += (
            (
                (-self.height_timer.current_value + 8 if self.indentation else 0)
                if playing
                else -self.height_timer.current_value
            )
            - self.indentation_y
        ) * min(1.0, max(0.0, (0.2 if playing else 0.05) * multiple_factor))

        position = self.ctx.playing_manager.getDisplayPosition()
        cur_line = self._currentLyricLine(position)
        meta = cur_line.isMetadata if cur_line else False

        if cur_line and not cur_line.content.strip():
            self.height_timer.target_value = 0
        else:
            has_translation = self._hasCurrentLineTranslation(cur_line)
            tar_height = (
                65 if has_translation and self.ctx.config.show_translation else 46
            )
            if meta:
                tar_height = self.font_height + 10
            self.height_timer.target_value = tar_height
        self._pill_h = max(1.0, self.height_timer.current_value)
        self.x_pad = self._pill_h / 2

        tar_width = 10
        future_line = self._offsetLyricLine(position, self.width_timer.anim_cycle * 0.5)
        if future_line:
            tar_width = max(
                10,
                int(self._textWidth(future_line.content)),
            )
        if self.ctx.config.show_translation and cur_line:
            translation = self._translationTextForLine(
                cur_line, self._ymgr.hasYrcTiming()
            )
            tar_width = max(tar_width, int(self.tmetri.horizontalAdvance(translation)))
        tar_width += int(self.x_pad * 2)

        self.width_timer.target_value = tar_width
        self._pill_w = max(1.0, self.width_timer.current_value)
        if cfg.desktop_lyrics_anchor == 'top-center':
            pill_x = int(self.scr_size.width() * 0.5 - self._pill_w * 0.5)
        else:
            pill_x = int(cfg.desktop_lyrics_x - self._pill_w * 0.5)
        self._pill_x = float(
            max(0, min(pill_x, int(self.scr_size.width() - self._pill_w)))
        )

        if self._dp.total_length > 0:
            self._draw_progress_ratio = max(
                0.0,
                min(
                    1.0,
                    self.ctx.playing_manager.getDisplayPosition()
                    / self._dp.total_length,
                ),
            )
        else:
            self._draw_progress_ratio = 0.0

        rect = QRect(
            int(self._pill_x),
            int(self._pillOffsetY()),
            max(1, int(self._pill_w)),
            max(1, int(self._pill_h)),
        )
        region = QRegion(rect)
        if region != self.mask():
            self.setMask(region)

        if not self.dragging:
            target_y = (
                0
                if cfg.desktop_lyrics_anchor == 'top-center'
                else int(cfg.desktop_lyrics_y)
            )
            if self.y() != target_y:
                self.move(0, target_y)

        super().updateDatas(multiple_factor)
        if self.slide_timer.is_animating:
            self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.dragging = True
        self.dragging_point = event.pos()
        self._grab_dx = event.position().x() - self._pill_x

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self.dragging:
            return
        target_y = int(event.globalPos().y() - self.dragging_point.y())
        center_x = event.globalPos().x() - self._grab_dx + self._pill_w * 0.5
        if abs(center_x - self.scr_size.width() * 0.5) < 30 and target_y < 15:
            cfg.desktop_lyrics_anchor = 'top-center'
            return
        cfg.desktop_lyrics_anchor = 'normal'
        cfg.desktop_lyrics_x = int(center_x)
        cfg.desktop_lyrics_y = target_y
        self.move(0, target_y)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.dragging = False
        event_bus.emit(Events.DESKTOP_LYRICS_ANCHOR_CHANGED)

    @override
    def paintGL(self) -> None:
        if self.width_timer.is_animating:
            self.slide_timer.enabled = False
            self.draw_x_offset = 0
        else:
            self.slide_timer.enabled = True

        painter = QPainter(self)
        try:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver
            )
            mwindow = self.ctx.main_window
            if not mwindow:
                return
            song_theme = getattr(mwindow, 'song_theme', None) or QColor(0, 0, 0)

            painter.setPen(Qt.PenStyle.NoPen)

            width = int(self._pill_w)
            height = int(self._pill_h)
            painter.translate(self._pill_x, self._pillOffsetY())
            painter.setClipRect(QRectF(0, 0, width, height))

            draw_rect = QRect(12, 0, width - 24, height)

            painter.setBrush(
                mixColor(
                    song_theme,
                    QColor(255, 255, 255) if theme.isLight() else QColor(0, 0, 0),
                    cfg.background_ratio * 0.2,
                )
            )

            path = QPainterPath()
            progress_x = 0
            progress_width = 0
            if cfg.desktop_lyrics_anchor == 'normal':
                radius = int(height * 0.5)
                path.addRoundedRect(
                    QRectF(draw_rect).normalized(),
                    abs(draw_rect.width()) * 0.5 if draw_rect.width() < 0 else radius,
                    radius,
                )
                progress_x = height // 2
                progress_width = width - height
            elif cfg.desktop_lyrics_anchor == 'top-center':
                path.setFillRule(Qt.FillRule.WindingFill)
                path.addRoundedRect(
                    QRectF(draw_rect).normalized(),
                    abs(draw_rect.width()) * 0.5 if draw_rect.width() < 0 else 20,
                    20,
                )
                path.moveTo(12, 0)
                path.lineTo(36, 0)
                path.lineTo(12, 16)
                path.closeSubpath()
                path.moveTo(width - 12, 0)
                path.lineTo(width - 12, 16)
                path.lineTo(width - 36, 0)
                path.closeSubpath()
                progress_x = 12
                progress_width = width - 24

            painter.save()
            if cfg.desktop_lyrics_anchor == 'top-center':
                painter.setClipping(False)
            painter.drawPath(path)
            painter.restore()
            if self._dp.total_length > 0 and not path.isEmpty():
                painter.save()
                painter.setBrush(
                    mixColor(
                        song_theme,
                        QColor(125, 125, 125) if theme.isDark() else QColor(80, 80, 80),
                        cfg.background_ratio * 0.5,
                    )
                )
                painter.drawRect(
                    progress_x,
                    0,
                    int(progress_width * self._draw_progress_ratio),
                    1,
                )
                slide = self.slide_timer.current_value
                if slide != 0.0:
                    painter.save()
                    painter.setClipPath(path)
                    painter.translate(slide, 0)
                    self._paintLyrics(painter)
                    painter.restore()
                    self._paintOutgoingLine(painter, slide)
                else:
                    painter.save()
                    painter.setClipPath(path)
                    self._paintLyrics(painter)
                    painter.restore()
                    self._outgoing_line = None
                painter.restore()
        finally:
            painter.end()

    def _paintOutgoingLine(self, painter: QPainter, slide: float) -> None:
        if self._outgoing_line is None or not self._outgoing_text:
            return
        if self._slide_distance <= 0.0:
            return
        if not (0 <= self._view_current_index < len(self._view_y_offsets)):
            return
        ratio = max(0.0, min(1.0, slide / self._slide_distance))
        baseline = toQtInt(
            self._view_top_offset + self._view_y_offsets[self._view_current_index]
        )
        shift = slide - self._slide_distance
        alpha = int(255 * ratio)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        painter.setFont(self.ft)
        painter.setPen(self._primaryColorForLine(self._outgoing_line, False, alpha))
        text_width = self._textWidth(self._outgoing_text)
        x = (self._pill_w - text_width) * 0.5
        if text_width > self._xScrollClipWidth():
            x = self.x_pad
        painter.drawText(toQtInt(x + shift), baseline, self._outgoing_text)
        translation_color = self._translationColor(alpha)
        if self._outgoing_translation and translation_color.alpha() > 0:
            translation_width = self.tmetri.horizontalAdvance(
                self._outgoing_translation
            )
            translation_x = (self._pill_w - translation_width) * 0.5 + shift
            translation_y = baseline + self.metri.descent() + 2 + self.tmetri.ascent()
            painter.setFont(self.tft)
            painter.setPen(translation_color)
            painter.drawText(
                toQtInt(translation_x),
                toQtInt(translation_y),
                self._outgoing_translation,
            )

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()


class DesktopLyricsPage(QWidget):
    def __init__(
        self,
        ctx: AppContext,
    ) -> None:
        super().__init__()
        if ctx.launch_window:
            ctx.launch_window.subtitle(
                tr('desktop_lyrics.initializing_desktop_lyrics_page')
            )
        self.ctx = ctx
        self._app = ctx.app
        self.setObjectName('desktop_lyrics_page')

        if ctx.launch_window:
            ctx.launch_window.subtitle(
                tr('desktop_lyrics.creating_desktop_lyrics_viewer')
            )
        self.viewer = DesktopLyricsViewer(ctx)
        self.viewer.setVisible(cfg.enable_desktop_lyrics)

        if ctx.launch_window:
            ctx.launch_window.subtitle(tr('desktop_lyrics.building_settings_panel'))
        global_layout = QVBoxLayout()
        title_label = TitleLabel()
        bindText(title_label, 'desktop_lyrics.desktop_lyrics')
        global_layout.addWidget(title_label)
        self.inputer = CheckBox()
        bindText(self.inputer, 'desktop_lyrics.enable_desktop_lyrics')
        self.inputer.checkStateChanged.connect(self.onEnableChanged)
        self.inputer.setChecked(cfg.enable_desktop_lyrics)
        global_layout.addWidget(self.inputer)
        buttons_layout = FlowLayout()
        self.reset_pos = PushButton(FluentIcon.SYNC, '')
        bindText(self.reset_pos, 'desktop_lyrics.reset_position')
        self.reset_pos.clicked.connect(self.onResetPos)
        buttons_layout.addWidget(self.reset_pos)
        global_layout.addLayout(buttons_layout)
        self.setLayout(global_layout)

    def onResetPos(self):
        cfg.desktop_lyrics_anchor = 'normal'
        cfg.desktop_lyrics_x = 0
        cfg.desktop_lyrics_y = 0
        event_bus.emit(Events.DESKTOP_LYRICS_ANCHOR_CHANGED)

    def setLyricsVisible(self, visible: bool) -> None:
        cfg.enable_desktop_lyrics = visible
        if self.inputer.isChecked() != visible:
            self.inputer.setChecked(visible)
            return
        if visible:
            self.viewer.show()
            self.viewer.raise_()
        else:
            self.viewer.hide()

    def onEnableChanged(self, _state=None):
        self.setLyricsVisible(self.inputer.isChecked())
