from __future__ import annotations

from imports import QEnterEvent, Qt, QTimer, QPoint, QRect
from imports import (
    QColor,
    QFontMetricsF,
    QMouseEvent,
    QMoveEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QWheelEvent,
)
from imports import QLabel, QVBoxLayout, QWidget
from qfluentwidgets import CheckBox, FlowLayout, PushButton, FluentIcon, TitleLabel

from utils import darkdetect_util as darkdetect
from utils.lyric_util import LyricInfo, YRCLyricInfo
from utils.config_util import cfg
from views.lyrics_viewer import LyricsViewer


class DesktopLyricsPage(QWidget):
    class DesktopLyricsViewer(LyricsViewer):
        def __init__(
            self, app, mgr, transmgr, ymgr, player, mwindow, harmony_font_family, cfg
        ):
            super().__init__(
                app, mgr, transmgr, ymgr, player, mwindow, harmony_font_family, cfg
            )
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

            self.dragging: bool = False
            self.dragging_point: QPoint = QPoint(0, 0)

            self.cwidth: float = 10
            self.cheight: float = 65

            self.indentation_timer = QTimer(self)
            self.indentation_timer.timeout.connect(self.unindentation)
            self.indentation_y: float = 0
            self.indentation: bool = False

            self.timer = QTimer(self)
            self.timer.timeout.connect(self.updateDatas)
            self.timer.start(16)

        def unindentation(self):
            if not cfg.desktop_lyrics_anchor == 'top-center':
                return
            self.indentation = False

        def updateDatas(self):
            self.indentation_y += ((-self.height() + 8 if self.indentation else 0) - self.indentation_y) * 0.2

            cur_line: YRCLyricInfo | LyricInfo | None = (
                self._ymgr.getCurrentLyric(self._player.getPosition())
                if self._ymgr.parsed
                else self._mgr.getCurrentLyric(self._player.getPosition())
                if self._mgr.parsed
                else None
            )
            meta = cur_line.get("isMetadata") if cur_line else False

            has_translation = bool(self._transmgr.parsed)
            tar_height = 65 if has_translation else 46
            if meta:
                tar_height = self.font_height + 2
            self.cheight += (tar_height - self.cheight) * 0.12
            self.setFixedHeight(int(self.cheight))

            tar_width = 0
            position = self._player.getPosition()
            if self._ymgr.parsed:
                yidx = self._ymgr.getCurrentIndex(position)
                y_line = (
                    self._ymgr.parsed[0]
                    if yidx < 0
                    else self._ymgr.getCurrentLyric(position)
                )
                tar_width = max(
                    10,
                    int(self.metri.horizontalAdvance(y_line["content"])),
                )
            elif self._mgr.parsed:
                lidx = self._mgr.getCurrentIndex(position)
                l_line = (
                    self._mgr.parsed[0]
                    if lidx < 0
                    else self._mgr.getCurrentLyric(position)
                )
                tar_width = max(
                    10,
                    int(self.metri.horizontalAdvance(l_line["content"])),
                )
            tar_width += self.draw_x_offset + self.height() * 0.5 + 10

            self.cwidth += (tar_width - self.cwidth) * 0.07
            self.setFixedWidth(int(self.cwidth))

            target_point = QPoint(0, 0)
            if cfg.desktop_lyrics_anchor == "top-center":
                target_point = QPoint(
                    int(
                        self._app.primaryScreen().size().width() * 0.5
                        - self.width() * 0.5
                    ),
                    0,
                )
            if cfg.desktop_lyrics_anchor == "bottom-center":
                target_point = QPoint(
                    int(
                        self._app.primaryScreen().size().width() * 0.5
                        - self.width() * 0.5
                    ),
                    self._app.primaryScreen().size().height() - self.height() - 100,
                )
            if cfg.desktop_lyrics_anchor == "normal" and not self.dragging:
                target_point = QPoint(int(cfg.desktop_lyrics_x - self.width() * 0.5), self.y())
            if not self.dragging and cfg.desktop_lyrics_anchor == 'top-center':
                target_point += QPoint(0, int(self.indentation_y))
            if not self.dragging:
                self.move(target_point)

            self.draw_x_offset = self.height() / 2

        def mousePressEvent(self, event: QMouseEvent) -> None:
            self.dragging = True
            self.dragging_point = event.pos()

        def mouseMoveEvent(self, event: QMouseEvent) -> None:
            if self.dragging:
                tp: QPoint = self.pos() + event.pos() - self.dragging_point
                center_x = tp.x() + self.width() * 0.5
                screen_center_x = self._app.primaryScreen().size().width() * 0.5
                if abs(center_x - screen_center_x) < 30 and tp.y() < 15:
                    cfg.desktop_lyrics_anchor = "top-center"
                elif (
                    abs(center_x - screen_center_x) < 30
                    and tp.y()
                    > self._app.primaryScreen().size().height() - 100 - self.height()
                ):
                    cfg.desktop_lyrics_anchor = "bottom-center"
                else:
                    cfg.desktop_lyrics_anchor = "normal"
                    self.move(tp)

        def mouseReleaseEvent(self, event: QMouseEvent) -> None:
            self.dragging = False

        def moveEvent(self, event: QMoveEvent) -> None:
            if self.dragging:
                center_x = event.pos().x() + self.width() * 0.5
                if cfg.desktop_lyrics_anchor == "normal":
                    cfg.desktop_lyrics_x, cfg.desktop_lyrics_y = (
                        int(center_x),
                        event.pos().y(),
                    )
            return super().moveEvent(event)

        def paintEvent(self, event: QPaintEvent) -> None:
            painter = QPainter(self)
            painter.setPen(Qt.PenStyle.NoPen)

            painter.setBrush(
                QColor(255, 255, 255) if darkdetect.isLight() else QColor(0, 0, 0)
            )

            draw_rect = QRect(12, 0, self.width() - 24, self.height())

            if cfg.desktop_lyrics_anchor == "normal":
                radius = int(self.height() * 0.5)
                painter.drawRoundedRect(draw_rect, radius, radius)
            elif cfg.desktop_lyrics_anchor == "top-center":
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
            elif cfg.desktop_lyrics_anchor == "bottom-center":
                radius = int(self.height() * 0.5)
                painter.drawRoundedRect(draw_rect, radius, radius)

            painter.end()
            return super().paintEvent(event)

        def wheelEvent(self, event: QWheelEvent) -> None:
            event.ignore()

        def enterEvent(self, event: QEnterEvent) -> None:
            self.indentation = True
            if self.indentation_timer.isActive():
                self.indentation_timer.stop()
            self.indentation_timer.start(1000)
            return super().enterEvent(event)

    def __init__(
        self,
        app,
        mgr,
        transmgr,
        ymgr,
        player,
        mwindow,
        harmony_font_family,
        cfg,
        launchwindow=None,
    ) -> None:
        super().__init__()
        if launchwindow:
            launchwindow.top("Initializing desktop lyrics page...")
        self._app = app
        self.setObjectName("desktop_lyrics_page")

        if launchwindow:
            launchwindow.top("  Creating desktop lyrics viewer...")
        self.viewer = self.DesktopLyricsViewer(
            app, mgr, transmgr, ymgr, player, mwindow, harmony_font_family, cfg
        )
        self.viewer.setVisible(cfg.enable_desktop_lyrics)

        self.viewer.move(cfg.desktop_lyrics_x, cfg.desktop_lyrics_y)
        self.viewer.resize(app.primaryScreen().size().width(), 65)

        if launchwindow:
            launchwindow.top("  Building settings panel...")
        global_layout = QVBoxLayout()
        global_layout.addWidget(TitleLabel("Desktop Lyrics"))
        self.inputer = CheckBox("Enable Desktop Lyrics")
        self.inputer.checkStateChanged.connect(self.onEnableChanged)
        self.inputer.setChecked(cfg.enable_desktop_lyrics)
        global_layout.addWidget(self.inputer)
        buttons_layout = FlowLayout()
        self.reset_pos = PushButton(FluentIcon.SYNC, "Reset Position")
        self.reset_pos.clicked.connect(self.onResetPos)
        buttons_layout.addWidget(self.reset_pos)
        global_layout.addLayout(buttons_layout)
        self.setLayout(global_layout)

    def onResetPos(self):
        self.viewer.move(0, 0)
        cfg.desktop_lyrics_anchor = "normal"

    def onEnableChanged(self):
        self.viewer.setVisible(self.inputer.isChecked())
        cfg.enable_desktop_lyrics = self.inputer.isChecked()
