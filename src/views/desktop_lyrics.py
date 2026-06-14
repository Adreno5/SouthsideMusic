from __future__ import annotations

from core.app_context import AppContext

from imports import (
    QSize,
    Qt,
    QTimer,
    QPoint,
    QRect,
    REPAINT,
    event_bus,
)
from imports import (
    QColor,
    QMouseEvent,
    QMoveEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QWheelEvent,
)
from imports import QVBoxLayout, QWidget
from qfluentwidgets import CheckBox, FlowLayout, PushButton, FluentIcon, TitleLabel
from core.color import mixColor
from core.config import cfg
from core import theme
from core.lyrics import LyricInfo, YRCLyricInfo
from services.events.events import EMIT_DEBUG_INFO
from views.lyrics_viewer import LyricsViewer


class DesktopLyricsViewer(LyricsViewer):
    def __init__(
        self,
        ctx: AppContext,
    ):
        self.indentation_y: float = 0
        self.indentation: bool = False

        self.cwidth: float = 10
        self.cheight: float = 65

        self.dragging: bool = False
        self.dragging_point: QPoint = QPoint(0, 0)

        self.scr_size: QSize = ctx.app.primaryScreen().size()
        super().__init__(ctx)
        self.indentation_timer = QTimer(self)
        self.indentation_timer.timeout.connect(self.unindentation)
        
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        event_bus.subscribe(REPAINT, self._onRepaintTick)

    def emitDebugInfo(self):
        event_bus.emit(
            EMIT_DEBUG_INFO,
            'Desktop Lyrics Viewer',
            [f'{len(self._shown_lines)=}', f'{self.last_lyric=}'],
        )

    def _onRepaintTick(self):
        self.updateDatas()
        self.update()

    def unindentation(self):
        if not cfg.desktop_lyrics_anchor == 'top-center':
            return
        self.indentation = False

    def _currentLyricLine(
        self,
        position: float | None = None,
    ) -> YRCLyricInfo | LyricInfo | None:
        if position is None:
            position = self._player.getPosition()
        if self._ymgr.parsed:
            return self._ymgr.getCurrentLyric(position)
        if self._mgr.parsed:
            return self._mgr.getCurrentLyric(position)
        return None

    def _shouldDrawTranslationForLine(
        self,
        line: LyricInfo | YRCLyricInfo,
        use_yrc: bool,
        is_current_line: bool,
    ) -> bool:
        return is_current_line

    def _hasCurrentLineTranslation(
        self,
        line: YRCLyricInfo | LyricInfo | None = None,
    ) -> bool:
        if line is None:
            line = self._currentLyricLine()
        return bool(line and self._translationTextForLine(line))

    def _hasTranslation(self) -> bool:
        return self._hasCurrentLineTranslation()

    def _lineStep(self, has_translation: bool = False) -> float:
        if self._transmgr.parsed:
            return self.font_height + self.theight + self.font_height * 0.75
        return self.font_height * 1.85

    def updateDatas(self):
        self.indentation_y += (
            (-self.height() + 8 if self.indentation else 0) - self.indentation_y
        ) * 0.2

        position = self._player.getPosition()
        cur_line = self._currentLyricLine(position)
        meta = cur_line.isMetadata if cur_line else False

        has_translation = self._hasCurrentLineTranslation(cur_line)
        tar_height = 65 if has_translation and self.ctx.cfg.show_translation else 46
        if meta:
            tar_height = self.font_height + 10
        self.cheight += (tar_height - self.cheight) * 0.12
        self.setFixedHeight(int(self.cheight))

        tar_width = 0
        if self._ymgr.parsed:
            yidx = self._ymgr.getCurrentIndex(position)
            y_line = (
                self._ymgr.parsed[0]
                if yidx < 0
                else self._ymgr.getCurrentLyric(position)
            )
            tar_width = max(
                10,
                int(self.metri.horizontalAdvance(y_line.content)),
            )
        elif self._mgr.parsed:
            lidx = self._mgr.getCurrentIndex(position)
            l_line = (
                self._mgr.parsed[0] if lidx < 0 else self._mgr.getCurrentLyric(position)
            )
            tar_width = max(
                10,
                int(self.metri.horizontalAdvance(l_line.content)),
            )
        tar_width += self.draw_x_offset + self.height() * 0.5 + 10

        self.cwidth += (tar_width - self.cwidth) * 0.07
        self.setFixedWidth(int(self.cwidth))

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

        self.draw_x_offset = self.height() / 2

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.dragging = True
        self.dragging_point = event.pos()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self.dragging:
            self.indentation = True
            if self.indentation_timer.isActive():
                self.indentation_timer.stop()
            self.indentation_timer.start(1000)

        if self.dragging:
            tp: QPoint = self.pos() + event.pos() - self.dragging_point
            center_x = tp.x() + self.width() * 0.5
            screen_center_x = self.scr_size.width() * 0.5
            if abs(center_x - screen_center_x) < 30 and tp.y() < 15:
                cfg.desktop_lyrics_anchor = 'top-center'
            else:
                cfg.desktop_lyrics_anchor = 'normal'
                self.move(tp)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.dragging = False

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
                        int(
                            (self.width() - self.height())
                            * (self._player.getPosition() / self._dp.total_length)
                        ),
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
                        int(
                            (self.width() - 24)
                            * (self._player.getPosition() / self._dp.total_length)
                        ),
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
            ctx.launch_window.top('Initializing desktop lyrics page...')
        self.ctx = ctx
        self._app = ctx.app
        self.setObjectName('desktop_lyrics_page')

        if ctx.launch_window:
            ctx.launch_window.top('  Creating desktop lyrics viewer...')
        self.viewer = DesktopLyricsViewer(ctx)
        self.viewer.setVisible(cfg.enable_desktop_lyrics)

        self.viewer.move(cfg.desktop_lyrics_x, cfg.desktop_lyrics_y)
        self.viewer.resize(ctx.app.primaryScreen().size().width(), 65)

        if ctx.launch_window:
            ctx.launch_window.top('  Building settings panel...')
        global_layout = QVBoxLayout()
        global_layout.addWidget(TitleLabel('Desktop Lyrics'))
        self.inputer = CheckBox('Enable Desktop Lyrics')
        self.inputer.checkStateChanged.connect(self.onEnableChanged)
        self.inputer.setChecked(cfg.enable_desktop_lyrics)
        global_layout.addWidget(self.inputer)
        buttons_layout = FlowLayout()
        self.reset_pos = PushButton(FluentIcon.SYNC, 'Reset Position')
        self.reset_pos.clicked.connect(self.onResetPos)
        buttons_layout.addWidget(self.reset_pos)
        global_layout.addLayout(buttons_layout)
        self.setLayout(global_layout)

    def onResetPos(self):
        self.viewer.move(0, 0)
        cfg.desktop_lyrics_anchor = 'normal'

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
