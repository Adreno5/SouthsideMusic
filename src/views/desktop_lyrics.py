from __future__ import annotations

from core.app_context import AppContext

from core.smooth import EaseOutBackTimer
from imports import (
    QSize,
    Qt,
    QTimer,
    QPoint,
    QRect,
    event_bus,
)
from imports import (
    bindText,
    QColor,
    QMouseEvent,
    QMoveEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QWheelEvent,
    tr,
)
from imports import QVBoxLayout, QWidget, QCursor
from qfluentwidgets import CheckBox, FlowLayout, PushButton, FluentIcon, TitleLabel
from core.color import mixColor
from core.config import cfg
from core import theme
from core.lyrics import LyricInfo, YRCLyricInfo
from services.events.events import DESKTOP_LYRICS_ANCHOR_CHANGED, EMIT_DEBUG_INFO, COLLECT_DEBUG_INFO
from views.lyrics_viewer import LyricsViewer
from views.playing_page import _artists_text
import ctypes
from ctypes import wintypes

class DesktopLyricsViewer(LyricsViewer):
    def __init__(
        self,
        ctx: AppContext,
    ):
        self.indentation_y: float = 0
        self.indentation: bool = False

        self.width_timer = EaseOutBackTimer(0.5, 3)
        self.height_timer = EaseOutBackTimer(0.5, 3)

        self.dragging: bool = False
        self.dragging_point: QPoint = QPoint(0, 0)
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
        
        hwnd = int(self.winId())
        
        user32 = ctypes.windll.user32
        user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(-1) if ctypes.sizeof(wintypes.HWND) == 8 else -1, 0, 0, 0, 0, 0x0002 | 0x0001)

        self.check_mouse_timer = QTimer(self)
        self.check_mouse_timer.timeout.connect(self._checkMouse)
        self.check_mouse_timer.start(200)
        
        event_bus.subscribe(COLLECT_DEBUG_INFO, self.emitDebugInfo)
        
    def _checkMouse(self):
        self.indentation = QRect(-5, -int(self.indentation_y), self.width() + 5, self.height()).contains(self.mapFromGlobal(QCursor.pos()))

    def showMinimized(self) -> None:
        self.showNormal()

    def prewarmFontMetrics(self):
        pass

    def emitDebugInfo(self):
        event_bus.emit(
            EMIT_DEBUG_INFO,
            'Desktop Lyrics Viewer',
            [f'{len(self._shown_lines)=}', f'{self.last_lyric=}', f'{self.indentation_y=}'],
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

    def _shouldDrawTranslationForLine(
        self,
        line: LyricInfo | YRCLyricInfo,
        use_yrc: bool,
        is_current_line: bool,
    ) -> bool:
        if is_current_line:
            return True
        return use_yrc and line is self.last_lyric

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

    def updateDatas(self, multiple_factor: float = 1.0) -> None:
        playing = self.ctx.player.isPlaying()
        self.indentation_y += (
            (((-self.height() + 8 if self.indentation else 0) if playing else -self.height()) - self.indentation_y)
            * (0.2 if playing else 0.05)
            * multiple_factor
        )

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
        self.setFixedHeight(max(1, int(self.height_timer.current_value)))
        self.x_pad = self.height() / 2

        tar_width = 10
        if cur_line:
            tar_width = max(
                10,
                int(self.metri.horizontalAdvance(cur_line.content)),
            )
        if self.ctx.config.show_translation and cur_line:
            translation = self._translationTextForLine(
                cur_line, self._ymgr.hasYrcTiming()
            )
            tar_width = max(tar_width, int(self.tmetri.horizontalAdvance(translation)))
        tar_width += int(self.x_pad * 2)

        self.width_timer.target_value = tar_width
        self.setFixedWidth(max(1, int(self.width_timer.current_value), tar_width))

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

        target_point = QPoint(0, 0)
        if cfg.desktop_lyrics_anchor == 'top-center':
            target_point = QPoint(
                int(self.scr_size.width() * 0.5 - self.width() * 0.5),
                0,
            )
        if cfg.desktop_lyrics_anchor == 'normal' and not self.dragging:
            target_point = QPoint(
                int(cfg.desktop_lyrics_x - self.width() * 0.5), self.y()
            )
            if self.x() < 0:
                cfg.desktop_lyrics_x = 0
            if self.x() > self.scr_size.width() - self.width():
                cfg.desktop_lyrics_x = self.scr_size.width() - self.width() // 2
        if not self.dragging and cfg.desktop_lyrics_anchor == 'top-center':
            target_point += QPoint(0, int(self.indentation_y))
        if not self.dragging:
            self.move(target_point)

        self._updateViewLayout(multiple_factor)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.dragging = True
        self.dragging_point = event.pos()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.dragging:
            tp: QPoint = event.globalPos() - self.dragging_point
            center_x = tp.x() + self.width() * 0.5
            screen_center_x = self.scr_size.width() * 0.5
            if abs(center_x - screen_center_x) < 30 and tp.y() < 15:
                cfg.desktop_lyrics_anchor = 'top-center'
            else:
                cfg.desktop_lyrics_anchor = 'normal'
                self.move(tp)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.dragging = False
        event_bus.emit(DESKTOP_LYRICS_ANCHOR_CHANGED)

    def moveEvent(self, event: QMoveEvent) -> None:
        if self.dragging:
            center_x = event.pos().x() + self.width() * 0.5
            if cfg.desktop_lyrics_anchor == 'normal':
                cfg.desktop_lyrics_x, cfg.desktop_lyrics_y = (
                    int(center_x),
                    event.pos().y(),
                )
        return super().moveEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        mwindow = self.ctx.main_window
        if not mwindow:
            return
        song_theme = getattr(mwindow, 'song_theme', None) or QColor(0, 0, 0)

        painter = QPainter(self)
        try:
            painter.setPen(Qt.PenStyle.NoPen)

            draw_rect = QRect(12, 0, self.width() - 24, self.height())

            painter.setBrush(
                mixColor(
                    song_theme,
                    QColor(255, 255, 255) if theme.isLight() else QColor(0, 0, 0),
                    cfg.background_ratio * 0.2,
                )
            )

            if cfg.desktop_lyrics_anchor == 'normal':
                radius = int(self.height() * 0.5)
                painter.drawRoundedRect(draw_rect, radius, radius)
                if self._dp.total_length > 0:
                    painter.save()
                    painter.setBrush(
                        mixColor(
                            song_theme,
                            QColor(125, 125, 125)
                            if theme.isDark()
                            else QColor(80, 80, 80),
                            cfg.background_ratio * 0.5,
                        )
                    )
                    painter.drawRect(
                        self.height() // 2,
                        0,
                        int((self.width() - self.height()) * self._draw_progress_ratio),
                        1,
                    )
                    painter.restore()
            elif cfg.desktop_lyrics_anchor == 'top-center':
                painter.drawRoundedRect(draw_rect, 20, 20)

                draw_path = QPainterPath()
                draw_path.moveTo(4, 0)
                draw_path.lineTo(36, 0)
                draw_path.lineTo(12, 16)
                draw_path.closeSubpath()

                exclude_path = QPainterPath()
                exclude_path.addRect(0, 0, 12, 25)

                clip_path = draw_path - exclude_path
                painter.save()
                painter.setClipPath(clip_path)
                painter.drawPath(draw_path)
                painter.restore()

                draw_path_r = QPainterPath()
                draw_path_r.moveTo(self.width() - 4, 0)
                draw_path_r.lineTo(self.width() - 36, 0)
                draw_path_r.lineTo(self.width() - 12, 16)
                draw_path_r.closeSubpath()

                exclude_path_r = QPainterPath()
                exclude_path_r.addRect(self.width() - 12, 0, 12, 25)

                clip_path_r = draw_path_r - exclude_path_r
                painter.save()
                painter.setClipPath(clip_path_r)
                painter.drawPath(draw_path_r)
                painter.restore()

                if self._dp.total_length > 0:
                    painter.save()
                    painter.setBrush(
                        mixColor(
                            song_theme,
                            QColor(125, 125, 125)
                            if theme.isDark()
                            else QColor(80, 80, 80),
                            cfg.background_ratio * 0.5,
                        )
                    )
                    painter.drawRect(
                        12,
                        0,
                        int((self.width() - 24) * self._draw_progress_ratio),
                        1,
                    )
                    painter.restore()
        finally:
            painter.end()
        return super().paintEvent(event)

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

        self.viewer.move(cfg.desktop_lyrics_x, cfg.desktop_lyrics_y)
        self.viewer.resize(ctx.app.primaryScreen().size().width(), 65)

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
        self.viewer.move(0, 0)
        cfg.desktop_lyrics_anchor = 'normal'
        event_bus.emit(DESKTOP_LYRICS_ANCHOR_CHANGED)

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
