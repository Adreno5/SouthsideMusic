from __future__ import annotations

import json
import logging
import math
import numpy as np
import time
from typing import TYPE_CHECKING, cast as _cast

from core.app_context import AppContext
from core.models import SongStorable
from core.qt_utils import toQtInt
from core.smooth import EaseOutTimer
from views.setting_page import SettingPage

from core.color import mixColor
from imports import (
    BACKGROUND_RATIO_CHANGED,
    LYRIC_LINE_CHANGED,
    PLAY_STATE_CHANGED,
    PLAY_START_PLAYLIST,
    PLAYLAST,
    PLAYNEXT,
    POST_THEME_CHANGED,
    REFRESH_RATE_CHANGED,
    REPAINT,
    SONG_CHANGED,
    QFont,
    QFontMetricsF,
    QImage,
    QPixmap,
    QPointF,
    QSizePolicy,
    QSpacerItem,
    Qt,
    QSize,
    QTimer,
    event_bus,
)
from imports import (
    QColor,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from imports import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    TransparentToolButton,
)

from core.icons import bindIcon
from core import theme
from core.lyrics import LyricInfo, LRCLyricParser, YRCLyricInfo, YRCLyricParser
from core.audio_player import AudioPlayer
from core.ws_server import QObjectHandler
from core.config import cfg

if TYPE_CHECKING:
    from views.main_window import MainWindow
    from views.playing_page import PlayingPage


class PlayingControllerLyricsViewer(QWidget):
    def __init__(
        self,
        ctx: AppContext,
    ):
        super().__init__()
        self._logger = logging.getLogger(__name__)
        self.ctx = ctx
        self._app = ctx.app
        self._mgr = ctx.mgr
        self._ymgr = ctx.ymgr
        self._player = ctx.player
        self._mwindow = ctx.main_window
        self._cfg = ctx.cfg
        self._dp = ctx.playing_page

        self.ft = QFont(ctx.harmony_font_family, 9)
        self.font_height = QFontMetricsF(self.ft).height()
        self.metri = QFontMetricsF(self.ft)

        self.last_draw: int = time.perf_counter_ns()

        self._lyrics_ready = True
        self._prewarm_version = 0

        self.refresh_rate = max(60, ctx.app.primaryScreen().refreshRate() / 2)
        self._logger.info(f'{self.refresh_rate=}')

        self.delta = 1 / self.refresh_rate

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
        for mgr in (self._mgr, self._ymgr):
            for line in mgr.parsed:
                content = line.content.strip()
                if content:
                    all_texts.add(content)
                if isinstance(line, YRCLyricInfo):
                    for ch in line.chars:
                        c = ch.char.strip()
                        if c:
                            all_texts.add(c)
        for text in all_texts:
            self.metri.horizontalAdvance(text)
        self._lyrics_ready = True
        self.update()

    def _onRepaintTick(self):
        position = self._player.getPosition()
        if self._ymgr.parsed:
            current = self._ymgr.getCurrentLyric(position)
        elif self._mgr.parsed:
            current = self._mgr.getCurrentLyric(position)
        else:
            current = None

        target = 0
        if current:
            text = current.content.strip()
            if text:
                target = int(math.ceil(self.metri.horizontalAdvance(text))) + 20

        self.setFixedWidth(max(1, int(target)))
        self.update()

    def _onRefreshRateChanged(self):
        self.refresh_rate = max(60, self._app.primaryScreen().refreshRate() / 2)
        self._logger.info(f'{self.refresh_rate=}')
        self.delta = 1 / self.refresh_rate

    def _currentLineBaseline(self) -> float:
        return (self.height() - self.font_height) * 0.5 + self.metri.ascent()

    def paintEvent(self, event: QPaintEvent) -> None:
        if not self._mgr.parsed or not self._lyrics_ready:
            return
        if not self.isVisible():
            return

        position = self._player.getPosition()
        use_yrc = bool(self._ymgr.parsed)
        current_line = (
            self._ymgr.getCurrentLyric(position)
            if use_yrc
            else self._mgr.getCurrentLyric(position)
        )

        if not current_line:
            return

        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        painter.setFont(self.ft)

        y = self._currentLineBaseline()

        if current_line.isMetadata:
            tar_color = QColor(255, 255, 255)
        else:
            tar_color = QColor(255, 255, 255) if theme.isDark() else QColor(0, 0, 0)

        color = (
            mixColor(
                self._mwindow.song_theme, tar_color, self._cfg.background_ratio / 2
            )
            if self._mwindow and self._mwindow.song_theme
            else tar_color
        )

        if use_yrc and not current_line.isMetadata:
            y_line = _cast(YRCLyricInfo, current_line)
            content = (y_line.content or current_line.content).strip()
            base_color = QColor(color)
            base_color.setAlpha(120)
            painter.setPen(base_color)
            painter.drawText(0, toQtInt(y), content)

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
                clip_w = text_width * progress
                if clip_w > 0:
                    painter.save()
                    painter.setClipRect(
                        toQtInt(x),
                        clip_y,
                        toQtInt(math.ceil(clip_w)),
                        clip_h,
                    )
                    painter.setPen(color)
                    painter.drawText(0, toQtInt(y), content)
                    painter.restore()
                x += text_width
        else:
            painter.setPen(color)
            painter.drawText(0, toQtInt(y), current_line.content.strip())

        painter.end()


class PlayingController(QWidget):
    def __init__(
        self,
        ctx: AppContext,
    ):
        super().__init__()
        self.ctx = ctx
        self._app = ctx.app
        self._player: AudioPlayer = _cast(AudioPlayer, ctx.player)
        self._mgr: LRCLyricParser = _cast(LRCLyricParser, ctx.mgr)
        self._transmgr: LRCLyricParser = _cast(LRCLyricParser, ctx.transmgr)
        self._ymgr: YRCLyricParser = _cast(YRCLyricParser, ctx.ymgr)
        self._dp: PlayingPage = ctx.playing_page  # type: ignore
        self._mwindow: MainWindow = ctx.main_window  # type: ignore
        self._ws_handler: QObjectHandler = _cast(QObjectHandler, ctx.ws_handler)
        self._stp: SettingPage = ctx.setting_page  # type: ignore

        self.dragging = False

        self.dev_mag: float = 1

        self.draw_ratio_timer = EaseOutTimer(0.25, 4)
        self.prepared_ratio_timer = EaseOutTimer(0.35, 4)

        self.lastfm = time.time()
        self.last_draw: int = time.perf_counter_ns()
        self.refresh_rate = max(60, self._app.primaryScreen().refreshRate() / 2)
        self.delta = 1 / self.refresh_rate

        global_layout = QHBoxLayout()
        global_layout.setContentsMargins(0, 0, 0, 0)

        self.cur_freqs: np.ndarray | None = None
        self.cur_magnitudes: np.ndarray | None = None
        self.final_magnitudes: np.ndarray = np.zeros(513, dtype=np.float32)
        self.smoothed_magnitudes: np.ndarray = np.zeros(513, dtype=np.float32)
        self.draw_magnitudes: np.ndarray = np.zeros(513, dtype=np.float32)
        self.last_lyric: LyricInfo = LyricInfo(time=0, content='')

        self.fm_label = QLabel()
        self.song_title_label = QLabel()
        self.lyrics_viewer = PlayingControllerLyricsViewer(ctx)

        self.middle_widget = QWidget()
        self.middle_layout = QVBoxLayout()
        self.middle_layout.addWidget(self.song_title_label)
        self.middle_layout.addWidget(self.lyrics_viewer)
        self.middle_widget.setLayout(self.middle_layout)

        self.last_btn = TransparentToolButton()
        bindIcon(self.last_btn, 'last')

        self.next_btn = TransparentToolButton()
        bindIcon(self.next_btn, 'next')

        self.play_pausebtn = TransparentToolButton()
        bindIcon(self.play_pausebtn, 'playa')

        self.playlist_btn = TransparentToolButton()
        bindIcon(self.playlist_btn, 'playlist')

        self.last_btn.setIconSize(QSize(30, 30))
        self.play_pausebtn.setIconSize(QSize(30, 30))
        self.next_btn.setIconSize(QSize(30, 30))
        self.playlist_btn.setIconSize(QSize(30, 30))
        self.play_pausebtn.clicked.connect(self.toggle)
        self.playlist_btn.clicked.connect(self.onTogglePlaylist)

        self.next_btn.clicked.connect(lambda: event_bus.emit(PLAYNEXT))
        self.last_btn.clicked.connect(lambda: event_bus.emit(PLAYLAST))

        global_layout.addWidget(self.fm_label)
        global_layout.addWidget(self.middle_widget)
        global_layout.addSpacerItem(
            QSpacerItem(
                0, 0, QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Minimum
            )
        )
        global_layout.addWidget(self.last_btn)
        global_layout.addWidget(self.play_pausebtn)
        global_layout.addWidget(self.next_btn)
        global_layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
        )
        global_layout.addWidget(self.playlist_btn)

        self.bg_color = QColor(0, 0, 0)

        self.setLayout(global_layout)

        event_bus.subscribe(REPAINT, self._updateFFT)

        self._lyric_timer = QTimer(self)
        self._lyric_timer.timeout.connect(self._updateLyric)
        self._lyric_timer.start(100)

        self._fm_timer = QTimer(self)
        self._fm_timer.timeout.connect(self._updateFM)
        self._fm_timer.start(2500)

        self._player.fftDataReady.connect(self.updateFFTData)

        event_bus.subscribe(PLAY_STATE_CHANGED, self._onPlayStateChanged)
        event_bus.subscribe(SONG_CHANGED, self._updateDatas)
        event_bus.subscribe(POST_THEME_CHANGED, self._updateDatas)
        event_bus.subscribe(BACKGROUND_RATIO_CHANGED, self._updateDatas)
        event_bus.subscribe(REFRESH_RATE_CHANGED, self._onRefreshRateChanged)

        if self._mwindow:
            self.bg_color = mixColor(
                QColor(40, 40, 40) if theme.isDark() else QColor(230, 230, 230),
                self._mwindow.song_theme
                if self._mwindow.song_theme
                else QColor(0, 0, 0),
                1 - cfg.background_ratio * 0.5,
            )
        else:
            self.bg_color = (
                QColor(40, 40, 40) if theme.isDark() else QColor(230, 230, 230)
            )

    def _onRefreshRateChanged(self):
        self.refresh_rate = max(60, self._app.primaryScreen().refreshRate() / 2)
        self.delta = 1 / self.refresh_rate

    def onTogglePlaylist(self):
        if self._mwindow and not self._mwindow.pl_animating:
            self._mwindow.togglePlaylistExpand()

    def hideLyrics(self):
        self.lyrics_viewer.hide()
        self.song_title_label.hide()
        self.fm_label.hide()

    def showLyrics(self):
        self.lyrics_viewer.show()
        self.song_title_label.show()
        self.fm_label.show()

    def _updateDatas(self, song: SongStorable | None = None):
        self.bg_color = mixColor(
            QColor(40, 40, 40) if theme.isDark() else QColor(230, 230, 230),
            self._mwindow.song_theme
            if self._mwindow and self._mwindow.song_theme
            else QColor(0, 0, 0),
            1 - cfg.background_ratio * 0.5,
        )

        if song:
            pixmap = QPixmap.fromImage(QImage.fromData(song.get_image_bytes()))
            pixmap = pixmap.scaled(
                self.height(),
                self.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.fm_label.setPixmap(pixmap)

            self.song_title_label.setText(song.name)

        self.update()

    def updateFFTData(self, freqs: np.ndarray, magnitudes: np.ndarray) -> None:
        self.cur_freqs = freqs
        self.cur_magnitudes = magnitudes

    def _updateFFT(self):
        from views.song_card import DummyCard

        if self._stp.enableFFT_box.isChecked():
            if not self._player.isPlaying():
                self.cur_magnitudes = np.zeros(513, dtype=np.float32)
            window_size = int(cfg.fft_filtering_windowsize)

            self.smoothed_magnitudes += (
                self.cur_magnitudes - self.smoothed_magnitudes
            ) * cfg.fft_factor
            self.final_magnitudes = np.convolve(
                self.smoothed_magnitudes,
                np.ones(window_size) / window_size,
                mode='same',
            )
            if isinstance(self._dp.cur, DummyCard):
                self.final_magnitudes *= (
                    2 / self._dp.cur.storable.loudness_gain
                ) * 0.75

            maxmag = max(np.max(self.final_magnitudes), 10)
            self.dev_mag += (maxmag - self.dev_mag) * 0.35
            self.final_magnitudes /= self.dev_mag
            self.final_magnitudes *= self.height() - 10

            self._ws_handler.send(
                json.dumps(
                    {
                        'option': 'update_fft',
                        'magnitudes': [
                            float(item) * cfg.sfft_multiple
                            for item in self.draw_magnitudes.tolist()
                        ],
                    }
                )
            )

        if self._mwindow and self._mwindow.isVisible():
            self.update()

    def _updateLyric(self):
        cl = self._mgr.getCurrentLyric(self._player.getPosition())
        nxt = self._mgr.getOffsetedLyric(self._player.getPosition(), 1)
        trd = self._mgr.getOffsetedLyric(self._player.getPosition(), 2)
        lat = self._mgr.getOffsetedLyric(self._player.getPosition(), -1)
        if cl != self.last_lyric:
            self._ws_handler.send(
                json.dumps(
                    {
                        'option': 'update_lyric',
                        'current': cl.content,
                        'next': nxt.content,
                        'third': trd.content,
                        'last': lat.content,
                    }
                )
            )
            self.last_lyric = cl
            event_bus.emit(
                LYRIC_LINE_CHANGED,
                {
                    'content': cl.content,
                    'next': nxt.content,
                    'third': trd.content,
                    'last': lat.content,
                },
            )

    def _updateFM(self):
        self._dp.sendSongFMAndInfo()

    def _onPlayStateChanged(self, is_playing: bool):
        if is_playing:
            bindIcon(self.play_pausebtn, 'pause')
        else:
            bindIcon(self.play_pausebtn, 'playa')

    def _progressLeft(self) -> int:
        return 52 if self.fm_label.isVisible() else 0

    def _eventPlayingTime(self, event: QMouseEvent) -> float:
        progress_left = self._progressLeft()
        progress_width = max(1, self.width() - progress_left)
        progress = (event.position().x() - progress_left) / progress_width
        progress = max(0.0, min(1.0, progress))
        return progress * self._dp.total_length

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if (
            event.position().y() < 8
            and event.position().x() > self._progressLeft()
            and self._dp.preloaded
        ):
            self.dragging = True
            self._player.setPosition(self._eventPlayingTime(event))
        elif event.position().y() > 8:
            if self._mwindow and not self._mwindow.dp_animating:
                self._mwindow.togglePlayingPageExpand()
        return super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.dragging and self._dp.preloaded:
            self._player.setPosition(self._eventPlayingTime(event))
            self.dragging = False
        return super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.dragging and self._dp.preloaded:
            self._player.setPosition(self._eventPlayingTime(event))
        return super().mouseMoveEvent(event)

    def toggle(self):
        if self._dp.cur is None:
            event_bus.emit(PLAY_START_PLAYLIST)
            return
        if self._player.isPlaying():
            self._player.pause()
            event_bus.emit(PLAY_STATE_CHANGED, False)
        else:
            self._player.resume()
            event_bus.emit(PLAY_STATE_CHANGED, True)

    def paintEvent(self, event: QPaintEvent) -> None:
        now = time.perf_counter_ns()
        _elapsed = min((now - self.last_draw) / 1_000_000_000, 0.1)
        self.last_draw = now
        multiple_factor = _elapsed / self.delta

        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)

        painter.setPen(Qt.PenStyle.NoPen)
        self.bg_color.setAlpha(255)
        painter.setBrush(self.bg_color)
        painter.drawRoundedRect(self.rect(), 10, 10)

        isDark = theme.isDark()

        if (
            self._stp.enableFFT_box.isChecked()
            and self.cur_freqs is not None
            and self.cur_magnitudes is not None
        ):
            self.draw_magnitudes = np.maximum(
                self.final_magnitudes, self.draw_magnitudes
            )
            self.draw_magnitudes += -self.draw_magnitudes * 0.05 * multiple_factor
            self.draw_magnitudes = np.maximum(self.draw_magnitudes, 0)

            path = QPainterPath(QPointF(0, 0))
            total = int(self.cur_magnitudes.size * 0.67)
            for i in range(total):
                x = (52 if self.fm_label.isVisible() else 0) + ((i + 1) / total) * (
                    self.width() - (52 if self.fm_label.isVisible() else 0)
                )
                path.lineTo(
                    QPointF(
                        x,
                        (
                            (self.draw_magnitudes[i] * ((1 + (i * 0.01)) - 0.1))
                            * cfg.cfft_multiple
                        )
                        + 3.5,
                    )
                )
            path.lineTo(QPointF(self.width(), 0))

            painter.setPen(QPen(QColor(120, 120, 120), 1))
            painter.setClipPath(path)
            painter.drawPath(path)
            gradient = QLinearGradient(0, self.height(), 0, 0)
            gradient.setColorAt(
                1,
                QColor(QColor(255, 255, 255, 150) if isDark else QColor(0, 0, 0, 150)),
            )
            gradient.setColorAt(0.5, QColor(0, 0, 0, 0))
            painter.fillRect(0, 0, self.width(), self.height(), gradient)
            painter.setClipPath(path, Qt.ClipOperation.NoClip)

        painter.setPen(QPen(QColor(120, 120, 120), 8))
        progress_left = self._progressLeft()
        progress_width = self.width() - progress_left
        painter.drawLine(progress_left, 0, self.width(), 0)
        if self._dp.total_length > 0:
            prepared_start, prepared_end = self._player.getPreparedTimeSection()
            current_time = max(
                0.0, min(self._player.getPosition(), self._dp.total_length)
            )
            self.draw_ratio_timer.target_value = current_time / self._dp.total_length
            draw_current_x = progress_left + int(
                progress_width * self.draw_ratio_timer.current_value
            )
            prepared_start_x = progress_left + int(
                progress_width
                * (
                    max(0.0, min(prepared_start, self._dp.total_length))
                    / self._dp.total_length
                )
            )
            self.prepared_ratio_timer.target_value = (
                max(0.0, min(prepared_end, self._dp.total_length))
                / self._dp.total_length
            )
            prepared_draw_end_x = progress_left + int(
                progress_width * self.prepared_ratio_timer.current_value
            )

            prepared_visible_start_x = max(prepared_start_x, draw_current_x)
            if prepared_draw_end_x > prepared_visible_start_x:
                painter.setPen(
                    QPen(
                        QColor(255, 255, 255, 70) if isDark else QColor(0, 0, 0, 45),
                        8,
                    )
                )
                painter.drawLine(
                    prepared_visible_start_x,
                    0,
                    prepared_draw_end_x,
                    0,
                )

            painter.setPen(
                QPen(QColor(255, 255, 255) if isDark else QColor(0, 0, 0), 8)
            )
            painter.drawLine(
                progress_left,
                0,
                draw_current_x,
                0,
            )

        painter.end()
