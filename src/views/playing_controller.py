from __future__ import annotations

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
from core.free_threaded_worker import json_float_array
from core.ws_server import QObjectHandler
from core.config import cfg

if TYPE_CHECKING:
    from views.main_window import MainWindow
    from views.playing_page import PlayingPage


_WS_LYRIC_INTERVAL = 1 / 30
_WS_FFT_INTERVAL = 1 / 30


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
        self._draw_payload: dict[str, object] = {'ready': False}

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

    def _onRepaintTick(self, _multiple_factor: float = 1.0) -> None:
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
        self._draw_payload = self._buildDrawPayload(position, current)
        self.update()

    def _buildDrawPayload(
        self,
        position: float,
        current_line: LyricInfo | YRCLyricInfo | None,
    ) -> dict[str, object]:
        if current_line is None:
            return {'ready': False}

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
        color_payload = {
            'r': color.red(),
            'g': color.green(),
            'b': color.blue(),
            'a': color.alpha(),
        }
        content = current_line.content.strip()
        payload: dict[str, object] = {
            'ready': True,
            'is_yrc': False,
            'text': content,
            'baseline_y': y,
            'color': color_payload,
            'clips': [],
        }
        if self._ymgr.parsed and isinstance(current_line, YRCLyricInfo):
            y_line = current_line
            content = (y_line.content or current_line.content).strip()
            clips: list[dict[str, float]] = []
            x = 0.0
            for ch in y_line.chars:
                text_width = self.metri.horizontalAdvance(ch.char)
                duration = ch.duration
                if duration <= 0:
                    progress = 1.0 if position >= ch.start else 0.0
                else:
                    progress = (position - ch.start) / duration
                progress = max(0.0, min(1.0, progress))
                clips.append({'x': x, 'width': text_width * progress})
                x += text_width
            payload.update(
                {
                    'is_yrc': not current_line.isMetadata,
                    'text': content,
                    'clips': clips,
                }
            )
        return payload

    def _colorFromPayload(self, value: object) -> QColor:
        if not isinstance(value, dict):
            return QColor(0, 0, 0, 0)
        return QColor(
            int(value.get('r', 0)),
            int(value.get('g', 0)),
            int(value.get('b', 0)),
            int(value.get('a', 0)),
        )

    def _onRefreshRateChanged(self):
        self.refresh_rate = max(60, self._app.primaryScreen().refreshRate() / 2)
        self._logger.info(f'{self.refresh_rate=}')
        self.delta = 1 / self.refresh_rate

    def _currentLineBaseline(self) -> float:
        return (self.height() - self.font_height) * 0.5 + self.metri.ascent()

    def paintEvent(self, event: QPaintEvent) -> None:
        payload = self._draw_payload
        if not payload.get('ready') or not self._lyrics_ready:
            return
        if not self.isVisible():
            return

        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        painter.setFont(self.ft)

        y = float(payload.get('baseline_y', 0.0)) # type: ignore
        content = str(payload.get('text', ''))
        color = self._colorFromPayload(payload.get('color', {}))

        if bool(payload.get('is_yrc', False)):
            base_color = QColor(color)
            base_color.setAlpha(120)
            painter.setPen(base_color)
            painter.drawText(0, toQtInt(y), content)

            clip_y = toQtInt(y - self.metri.ascent())
            clip_h = toQtInt(self.font_height)
            for clip in payload.get('clips', []): # type: ignore
                if not isinstance(clip, dict):
                    continue
                x = float(clip.get('x', 0.0))
                clip_w = float(clip.get('width', 0.0))
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
        else:
            painter.setPen(color)
            painter.drawText(0, toQtInt(y), content)

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
        self.overlay_alpha_timer = EaseOutTimer(0.4, 2)

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
        self.last_lyric: LyricInfo | YRCLyricInfo | None = None
        self._last_ws_lyric_send = 0.0
        self._last_ws_fft_send = 0.0
        self._draw_current_x = 0
        self._prepared_draw_end_x = 0
        self._overlay_alpha = 0
        self._draw_fft = False

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
        event_bus.subscribe(REPAINT, self._updateLyric)

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

    def _updateFFT(self, multiple_factor: float = 1.0) -> None:
        from views.song_card import DummyCard

        self._draw_fft = (
            self._stp.enableFFT_box.isChecked()
            and self.cur_freqs is not None
            and self.cur_magnitudes is not None
        )
        if self._stp.enableFFT_box.isChecked() and self.cur_magnitudes is not None:
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

            if self._ws_handler.is_open:
                now = time.perf_counter()
                if now - self._last_ws_fft_send >= _WS_FFT_INTERVAL:
                    self._last_ws_fft_send = now
                    magnitudes = np.ascontiguousarray(
                        self.draw_magnitudes, dtype=np.float32
                    )
                    multiple = float(cfg.sfft_multiple)
                    self._ws_handler.sendJsonFactory(
                        lambda magnitudes=magnitudes, multiple=multiple: {
                            'option': 'update_fft',
                            'magnitudes': json_float_array(
                                magnitudes.tobytes(),
                                str(magnitudes.dtype),
                                int(magnitudes.size),
                                multiple,
                            ),
                        },
                        coalesce_key='update_fft',
                    )

            self.draw_magnitudes = np.maximum(
                self.final_magnitudes, self.draw_magnitudes
            )
            self.draw_magnitudes += -self.draw_magnitudes * 0.05 * multiple_factor
            self.draw_magnitudes = np.maximum(self.draw_magnitudes, 0)

        progress_left = self._progressLeft()
        progress_width = self.width() - progress_left
        self._draw_current_x = progress_left
        self._prepared_draw_end_x = progress_left
        self._overlay_alpha = 0
        if self._dp.total_length > 0:
            loaded_time = self._player.getLoadedTime()
            current_time = max(
                0.0, min(self._player.getPosition(), self._dp.total_length)
            )
            self.draw_ratio_timer.target_value = current_time / self._dp.total_length
            draw_ratio = max(0.0, min(self.draw_ratio_timer.current_value, 1.0))
            self._draw_current_x = progress_left + int(
                progress_width * draw_ratio
            )
            self.prepared_ratio_timer.target_value = (
                max(0.0, min(loaded_time, self._dp.total_length))
                / self._dp.total_length
            )
            self._prepared_draw_end_x = progress_left + int(
                progress_width * self.prepared_ratio_timer.current_value
            )

            if self.prepared_ratio_timer.current_value >= 0.99:
                self.overlay_alpha_timer.target_value = 0
            else:
                self.overlay_alpha_timer.target_value = 60
            self._overlay_alpha = int(self.overlay_alpha_timer.current_value)
            self._sendDrawPosition(draw_ratio)

        if self._mwindow and self._mwindow.isVisible():
            self.update()

    def _sendDrawPosition(self, draw_ratio: float) -> None:
        if not self._ws_handler.is_open:
            return

        duration = float(self._dp.total_length)
        position = draw_ratio * duration
        self._ws_handler.sendJsonFactory(
            lambda position=position, duration=duration, ratio=draw_ratio: {
                'option': 'play_position',
                'position': position,
                'duration': duration,
                'ratio': ratio,
            },
            coalesce_key='play_position',
        )

    def _lyricLinePayload(
        self,
        line: LyricInfo | YRCLyricInfo | None,
        offset: int,
        index: int,
        use_yrc: bool,
    ) -> dict[str, object]:
        role_map = {
            -2: 'past2',
            -1: 'past1',
            0: 'current',
            1: 'next1',
            2: 'next2',
        }
        if line is None:
            return {
                'offset': offset,
                'role': role_map[offset],
                'index': index,
                'time': 0.0,
                'text': '',
                'translation': '',
                'is_metadata': False,
            }

        return {
            'offset': offset,
            'role': role_map[offset],
            'index': index,
            'time': line.time,
            'text': line.content.strip(),
            'translation': self._translationTextForLine(line, use_yrc),
            'is_metadata': line.isMetadata,
        }

    def _translationTextForLine(
        self, line: LyricInfo | YRCLyricInfo, use_yrc: bool
    ) -> str:
        try:
            return self._dp.viewer._translationTextForLine(line, use_yrc)
        except Exception:
            return ''

    def _lyricWindowPayload(
        self, position: float
    ) -> tuple[list[dict[str, object]], LyricInfo | YRCLyricInfo | None, int, bool]:
        use_yrc = bool(self._ymgr.parsed)
        lines: list[LyricInfo | YRCLyricInfo]
        if use_yrc:
            lines = self._ymgr.parsed # type: ignore
            current_index = self._ymgr.getCurrentIndex(position)
        else:
            lines = self._mgr.parsed # type: ignore
            current_index = self._mgr.getCurrentIndex(position)

        payload_lines: list[dict[str, object]] = []
        current_line: LyricInfo | YRCLyricInfo | None = None
        for offset in (-2, -1, 0, 1, 2):
            index = current_index + offset
            line = lines[index] if 0 <= index < len(lines) else None
            if offset == 0:
                current_line = line
            payload_lines.append(
                self._lyricLinePayload(line, offset, index, use_yrc)
            )

        return payload_lines, current_line, current_index, use_yrc

    def _updateLyric(self, _multiple_factor: float = 1.0) -> None:
        position = self._player.getPosition()
        lines, current_line, current_index, use_yrc = self._lyricWindowPayload(position)
        if self._ws_handler.is_open:
            now = time.perf_counter()
            if now - self._last_ws_lyric_send >= _WS_LYRIC_INTERVAL:
                self._last_ws_lyric_send = now
                layout = dict(self._dp.viewer._layout_payload)
                translation_enabled = bool(cfg.show_translation)
                self._ws_handler.sendJsonFactory(
                    lambda position=position,
                    current_index=current_index,
                    use_yrc=use_yrc,
                    lines=lines,
                    layout=layout,
                    translation_enabled=translation_enabled: {
                        'option': 'update_lyric',
                        'position': position,
                        'current_index': current_index,
                        'use_yrc': use_yrc,
                        'yrc_clip_ratio': layout.get('current_yrc_clip_ratio', 0.0),
                        'yrc_clip_width': layout.get('current_yrc_clip_width', 0.0),
                        'translation_enabled': translation_enabled,
                        'lines': lines,
                        'layout': layout,
                        'render_lines': layout.get('lines', []),
                    },
                    coalesce_key='update_lyric',
                )
        if current_line != self.last_lyric:
            self.last_lyric = current_line
            current = lines[2]
            next_ = lines[3]
            third = lines[4]
            last = lines[1]
            event_bus.emit(
                LYRIC_LINE_CHANGED,
                {
                    'content': current['text'],
                    'next': next_['text'],
                    'third': third['text'],
                    'last': last['text'],
                },
            )

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
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)

        painter.setPen(Qt.PenStyle.NoPen)
        self.bg_color.setAlpha(255)
        painter.setBrush(self.bg_color)
        painter.drawRoundedRect(self.rect(), 10, 10)

        isDark = theme.isDark()

        if self._draw_fft and self.cur_magnitudes is not None:
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
        painter.drawLine(progress_left, 0, self.width(), 0)
        if self._dp.total_length > 0:
            painter.setPen(
                QPen(
                    QColor(255, 255, 255, self._overlay_alpha)
                    if isDark
                    else QColor(0, 0, 0, self._overlay_alpha),
                    8,
                )
            )
            painter.drawLine(
                0,
                0,
                self._prepared_draw_end_x,
                0,
            )

            painter.setPen(
                QPen(QColor(255, 255, 255) if isDark else QColor(0, 0, 0), 8)
            )
            painter.drawLine(
                progress_left,
                0,
                self._draw_current_x,
                0,
            )

        painter.end()
