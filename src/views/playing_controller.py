from __future__ import annotations

from collections import deque
import logging
import math
import numpy as np
import time
from typing import TYPE_CHECKING, cast as _cast

from core.app_context import AppContext
from core.i18n import tr
from core.models import SongStorable
from core.qt_utils import toQtInt
from core.smooth import EaseOutTimer
from views.setting_page import SettingPage

from core.color import mixColor
from imports import (
    BACKGROUND_RATIO_CHANGED,
    BEAT_POINT,
    COLLECT_DEBUG_INFO,
    EMIT_DEBUG_INFO,
    LYRIC_LINE_CHANGED,
    PLAY_STATE_CHANGED,
    PLAY_START_PLAYLIST,
    PLAYLAST,
    PLAYNEXT,
    POST_THEME_CHANGED,
    REFRESH_RATE_CHANGED,
    REPAINT,
    SONG_CHANGED,
    START_CROSSFADE,
    FINISH_CROSSFADE,
    QFont,
    QFontMetricsF,
    QImage,
    QPixmap,
    QPointF,
    QRectF,
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
from core.free_threaded_worker import jsonFloatArray
from core.ws_server import QObjectHandler
from core.config import cfg
from views.translation_handler import TranslationHandler

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
        self._cfg = ctx.config
        self._dp = ctx.playing_page

        self.ft = QFont(ctx.harmony_font_family, 9)
        self.font_height = QFontMetricsF(self.ft).height()
        self.metri = QFontMetricsF(self.ft)

        self.last_draw: int = time.perf_counter_ns()

        self._lyrics_ready = True
        self._prewarm_version = 0
        self._current_line: LyricInfo | YRCLyricInfo | None = None
        self._draw_position = 0.0

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
        position = self.ctx.playing_manager.getDisplaySmoothPosition()
        if self._ymgr.hasYrcTiming():
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
        self._current_line = current
        self._draw_position = position
        self.update()

    def _lineColor(self, line: LyricInfo | YRCLyricInfo) -> QColor:
        if line.isMetadata:
            tar_color = QColor(255, 255, 255)
        else:
            tar_color = QColor(255, 255, 255) if theme.isDark() else QColor(0, 0, 0)

        return (
            mixColor(
                self._mwindow.song_theme, tar_color, self._cfg.background_ratio / 2
            )
            if self._mwindow and self._mwindow.song_theme
            else tar_color
        )

    def _onRefreshRateChanged(self):
        self.refresh_rate = max(60, self._app.primaryScreen().refreshRate() / 2)
        self._logger.info(f'{self.refresh_rate=}')
        self.delta = 1 / self.refresh_rate

    def _currentLineBaseline(self) -> float:
        return (self.height() - self.font_height) * 0.5 + self.metri.ascent()

    def paintEvent(self, event: QPaintEvent) -> None:
        current_line = self._current_line
        if current_line is None or not self._lyrics_ready:
            return
        if not self.isVisible():
            return

        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        painter.setFont(self.ft)

        y = self._currentLineBaseline()
        content = current_line.content.strip()
        color = self._lineColor(current_line)

        if isinstance(current_line, YRCLyricInfo) and not current_line.isMetadata:
            base_color = QColor(color)
            base_color.setAlpha(120)
            painter.setPen(base_color)
            painter.drawText(0, toQtInt(y), content)

            clip_y = toQtInt(y - self.metri.ascent())
            clip_h = toQtInt(self.font_height)
            x = 0.0
            for ch in current_line.chars:
                text_width = self.metri.horizontalAdvance(ch.char)
                if ch.duration <= 0:
                    progress = 1.0 if self._draw_position >= ch.start else 0.0
                else:
                    progress = (self._draw_position - ch.start) / ch.duration
                progress = max(0.0, min(1.0, progress))
                clip_w = text_width * progress
                if clip_w > 0:
                    painter.save()
                    painter.setClipRect(QRectF(x, clip_y, clip_w, clip_h))
                    painter.setPen(color)
                    painter.drawText(0, toQtInt(y), content)
                    painter.restore()
                x += text_width
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

        self.norm_timer: EaseOutTimer = EaseOutTimer(0.5, 2)
        self.norm_timer.current_value = 100000
        self.norm_timer.target_value = 100000
        self.norm_buffer: deque[float] = deque()

        self.draw_ratio_timer = EaseOutTimer(0.25, 4)
        self.prepared_ratio_timer = EaseOutTimer(0.35, 4)
        self.overlay_alpha_timer = EaseOutTimer(0.4, 2)

        self.last_cover = time.time()
        self.last_draw: int = time.perf_counter_ns()
        self.refresh_rate = max(60, self._app.primaryScreen().refreshRate() / 2)
        self.delta = 1 / self.refresh_rate
        self.setFFTBufferSeconds(self.ctx.config.fft_buffer_seconds)

        global_layout = QHBoxLayout()
        global_layout.setContentsMargins(0, 0, 0, 0)

        self.cur_freqs: np.ndarray | None = None
        self.cur_magnitudes: np.ndarray | None = None
        self.final_magnitudes: np.ndarray = np.zeros(2049, dtype=np.float32)
        self.smoothed_magnitudes: np.ndarray = np.zeros(2049, dtype=np.float32)
        self.draw_magnitudes: np.ndarray = np.zeros(2049, dtype=np.float32)
        self.fft_display_magnitudes: np.ndarray = np.zeros(768, dtype=np.float32)
        self.last_lyric: LyricInfo | YRCLyricInfo | None = None
        self._last_ws_lyric_send = 0.0
        self._last_ws_fft_send = 0.0
        self._draw_current_x = 0
        self._prepared_lead_width = 0
        self._prepared_draw_end_x = 0
        self._overlay_alpha = 0
        self._draw_fft = False

        self.cover_label = QLabel()
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

        global_layout.addWidget(self.cover_label)
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

        self.bar_alpha_timer = EaseOutTimer(0.3, 2)
        self.beat_flash_timer = EaseOutTimer(0.6, 2)
        self.beat_flash_timer.target_value = 0
        self.bar_alpha_timer.target_value = 1
        self.tip_handler = TranslationHandler()

        event_bus.subscribe(PLAY_STATE_CHANGED, self._onPlayStateChanged)
        event_bus.subscribe(SONG_CHANGED, self._updateDatas)
        event_bus.subscribe(POST_THEME_CHANGED, self._updateDatas)
        event_bus.subscribe(BACKGROUND_RATIO_CHANGED, self._updateDatas)
        event_bus.subscribe(BEAT_POINT, self._onBeatPoint)
        self._finishInitSubscriptions()

    def _finishInitSubscriptions(self) -> None:
        event_bus.subscribe(REFRESH_RATE_CHANGED, self._onRefreshRateChanged)
        event_bus.subscribe(
            START_CROSSFADE, lambda: setattr(self.bar_alpha_timer, 'target_value', 0.2)
        )
        event_bus.subscribe(
            FINISH_CROSSFADE, lambda: setattr(self.bar_alpha_timer, 'target_value', 1)
        )
        event_bus.subscribe(COLLECT_DEBUG_INFO, self.emitDebugInfo)

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

    def emitDebugInfo(self):
        info = []
        info.extend(self.norm_timer.getDebugInfo())
        event_bus.emit(EMIT_DEBUG_INFO, 'PlayingController', info)

    def _onRefreshRateChanged(self):
        self.refresh_rate = max(60, self._app.primaryScreen().refreshRate() / 2)
        self.delta = 1 / self.refresh_rate
        self.setFFTBufferSeconds(self.ctx.config.fft_buffer_seconds)

    def setFFTBufferSeconds(self, seconds: float) -> None:
        maxlen = max(1, int(self.refresh_rate * seconds))
        self.norm_buffer = deque(self.norm_buffer, maxlen=maxlen)

    def onTogglePlaylist(self):
        if self._mwindow and not self._mwindow.pl_animating:
            self._mwindow.togglePlaylistExpand()

    def hideLyrics(self):
        self.lyrics_viewer.hide()
        self.song_title_label.hide()
        self.cover_label.hide()

    def showLyrics(self):
        self.lyrics_viewer.show()
        self.song_title_label.show()
        self.cover_label.show()

    def _updateDatas(self, song: SongStorable | None = None):
        self.bg_color = mixColor(
            QColor(40, 40, 40) if theme.isDark() else QColor(230, 230, 230),
            self._mwindow.song_theme
            if self._mwindow and self._mwindow.song_theme
            else QColor(0, 0, 0),
            1 - cfg.background_ratio * 0.5,
        )

        self.norm_timer.current_value = 100000
        self.norm_timer.target_value = 100000

        if song:
            try:
                qimg = QImage.fromData(song.getImageBytes())
            except FileNotFoundError:
                qimg = QImage()

            if qimg.isNull():
                self.cover_label.clear()
            else:
                pixmap = QPixmap.fromImage(qimg)
                pixmap = pixmap.scaled(
                    self.height(),
                    self.height(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.cover_label.setPixmap(pixmap)
            self.song_title_label.setText(song.name)

        self.update()

    def updateFFTData(self, freqs: np.ndarray, magnitudes: np.ndarray) -> None:
        if len(magnitudes) != len(self.smoothed_magnitudes):
            self.final_magnitudes = np.zeros_like(magnitudes, dtype=np.float32)
            self.smoothed_magnitudes = np.zeros_like(magnitudes, dtype=np.float32)
            self.draw_magnitudes = np.zeros_like(magnitudes, dtype=np.float32)
        self.cur_freqs = freqs
        self.cur_magnitudes = magnitudes

    def _displayFFTMagnitudes(self) -> np.ndarray:
        if self.cur_freqs is None or len(self.cur_freqs) < 2:
            return np.zeros(0, dtype=np.float32)
        size = min(len(self.cur_freqs), len(self.draw_magnitudes))
        if size < 2:
            return np.zeros(0, dtype=np.float32)

        freqs = self.cur_freqs[:size]
        magnitudes = self.draw_magnitudes[:size]
        min_frequency = max(float(cfg.fft_min_frequency_hz), float(freqs[1]))
        max_frequency = min(float(cfg.fft_max_frequency_hz), float(freqs[-1]))
        if max_frequency <= min_frequency:
            return np.zeros(0, dtype=np.float32)

        mel_min = np.log1p(min_frequency / 700)
        mel_max = np.log1p(max_frequency / 700)
        centers = 700 * np.expm1(np.linspace(mel_min, mel_max, 768))
        display = np.interp(centers, freqs, magnitudes).astype(
            np.float32,
            copy=False,
        )
        peak = float(np.max(display))
        if peak > 0:
            display = peak * np.power(display / peak, 1.6)
        return display.astype(np.float32, copy=False)

    def _updateFFT(self, multiple_factor: float = 1.0) -> None:
        from views.song_card import DummyCard

        self._draw_fft = (
            self._stp.enableFFT_box.isChecked()
            and self.cur_freqs is not None
            and self.cur_magnitudes is not None
        )
        if self._stp.enableFFT_box.isChecked() and self.cur_magnitudes is not None:
            if not self._player.isPlaying():
                self.cur_magnitudes = np.zeros_like(
                    self.cur_magnitudes,
                    dtype=np.float32,
                )
            window_size = int(cfg.fft_filtering_windowsize)

            self.smoothed_magnitudes += (
                self.cur_magnitudes - self.smoothed_magnitudes
            ) * cfg.fft_factor
            self.final_magnitudes = np.convolve(
                self.smoothed_magnitudes,
                np.ones(window_size) / window_size,
                mode='same',
            )
            n = len(self.final_magnitudes)
            offset = int(n * 0.015)
            factor = np.arange(n, dtype=self.final_magnitudes.dtype)
            factor -= offset
            np.abs(factor, out=factor)
            factor += 1.0
            factor *= 1.05
            self.final_magnitudes *= factor
            if isinstance(self._dp.cur, DummyCard):
                self.final_magnitudes *= (
                    2 / self._dp.cur.storable.loudness_gain
                ) * 0.75

            if self.ctx.player.isPlaying():
                self.norm_buffer.append(max(np.max(self.final_magnitudes), 1))
                self.norm_timer.target_value = max(self.norm_buffer)
                if len(self.norm_buffer) >= min(5, int(self.norm_buffer.maxlen or 1)):
                    self.final_magnitudes /= self.norm_timer.current_value
                    self.final_magnitudes *= self.height() - 10

            self.draw_magnitudes = np.maximum(
                self.final_magnitudes, self.draw_magnitudes
            )
            self.draw_magnitudes += -self.draw_magnitudes * 0.07 * multiple_factor
            self.draw_magnitudes = np.maximum(self.draw_magnitudes, 0)
            self.fft_display_magnitudes = self._displayFFTMagnitudes()

            if self._ws_handler.is_open:
                now = time.perf_counter()
                if now - self._last_ws_fft_send >= self.ctx.config.ws_fft_interval:
                    self._last_ws_fft_send = now
                    magnitudes = np.ascontiguousarray(
                        self.fft_display_magnitudes,
                        dtype=np.float32,
                    )
                    multiple = float(cfg.sfft_multiple)
                    self._ws_handler.sendJsonFactory(
                        lambda magnitudes=magnitudes, multiple=multiple: {
                            'option': 'update_fft',
                            'magnitudes': jsonFloatArray(
                                magnitudes.tobytes(),
                                str(magnitudes.dtype),
                                int(magnitudes.size),
                                multiple,
                            ),
                        },
                        coalesce_key='update_fft',
                    )

        progress_left = self._progressLeft()
        progress_width = self.width() - progress_left
        self._draw_current_x = progress_left
        self._prepared_draw_end_x = progress_left
        self._overlay_alpha = 0
        if self._dp.total_length > 0:
            loaded_time = self.ctx.playing_manager.getDisplayLoadedTime()
            current_time = max(
                0.0,
                min(
                    self.ctx.playing_manager.getDisplayPosition(),
                    self._dp.total_length,
                ),
            )
            self.draw_ratio_timer.target_value = current_time / self._dp.total_length
            draw_ratio = max(0.0, min(self.draw_ratio_timer.current_value, 1.0))
            self._draw_current_x = progress_left + int(progress_width * draw_ratio)
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
            length = self.ctx.playing_manager.getDisplayLength()
            if length > 0:
                self._prepared_lead_width = int(
                    progress_width
                    * self.ctx.playing_manager.getDisplayPreparedLead()
                    / length
                )
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
        use_yrc = self._ymgr.hasYrcTiming()
        lines: list[LyricInfo | YRCLyricInfo]
        if use_yrc:
            lines = self._ymgr.parsed  # type: ignore
            current_index = self._ymgr.getCurrentIndex(position)
        else:
            lines = self._mgr.parsed  # type: ignore
            current_index = self._mgr.getCurrentIndex(position)

        payload_lines: list[dict[str, object]] = []
        current_line: LyricInfo | YRCLyricInfo | None = None
        for offset in (-2, -1, 0, 1, 2):
            index = current_index + offset
            line = lines[index] if 0 <= index < len(lines) else None
            if offset == 0:
                current_line = line
            payload_lines.append(self._lyricLinePayload(line, offset, index, use_yrc))

        return payload_lines, current_line, current_index, use_yrc

    def _updateLyric(self, _multiple_factor: float = 1.0) -> None:
        position = self.ctx.playing_manager.getDisplayPosition()
        lines, current_line, current_index, use_yrc = self._lyricWindowPayload(position)
        if self._ws_handler.is_open:
            now = time.perf_counter()
            if now - self._last_ws_lyric_send >= self.ctx.config.ws_lyrics_interval:
                self._last_ws_lyric_send = now
                layout = self._dp.viewer.lyricLayoutPayload()
                translation_enabled = bool(cfg.show_translation)
                self._ws_handler.sendJsonFactory(
                    lambda position=position, current_index=current_index, use_yrc=use_yrc, lines=lines, layout=layout, translation_enabled=translation_enabled: {
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
        return 52 if self.cover_label.isVisible() else 0

    def _crossfadeTipText(self) -> str:
        if cfg.show_advanced_settings:
            return tr('playing_controller.crossfading_tip')
        return tr('playing_controller.crossfading_tip_easy')

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
            and not self.ctx.playing_manager.crossfading
        ):
            self.dragging = True
            self._player.setPosition(self._eventPlayingTime(event))
        elif event.position().y() > 8:
            if self._mwindow and not self._mwindow.dp_animating:
                self._mwindow.togglePlayingPageExpand()
        return super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if (
            self.dragging
            and self._dp.preloaded
            and not self.ctx.playing_manager.crossfading
        ):
            self._player.setPosition(self._eventPlayingTime(event))
            self.dragging = False
        return super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self.dragging
            and self._dp.preloaded
            and not self.ctx.playing_manager.crossfading
        ):
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
            fft_left = 52 if self.cover_label.isVisible() else 0
            path = QPainterPath(QPointF(fft_left, 0))
            total = len(self.fft_display_magnitudes)
            for i, magnitude in enumerate(self.fft_display_magnitudes):
                x = fft_left + ((i + 1) / total) * (self.width() - fft_left)
                path.lineTo(
                    QPointF(
                        x,
                        (magnitude * cfg.cfft_multiple) + 3.5,
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

        bar_alpha = int(self.bar_alpha_timer.current_value * 255)
        painter.setPen(QPen(QColor(120, 120, 120, bar_alpha), 8))
        progress_left = self._progressLeft()
        painter.drawLine(progress_left, 0, self.width(), 0)
        if self.ctx.playing_manager.crossfading or bar_alpha < 255:
            painter.setPen(
                QPen(
                    QColor(255, 255, 255, 255 - bar_alpha)
                    if isDark
                    else QColor(0, 0, 0, 255 - bar_alpha),
                    8,
                )
            )
            metrics = QFontMetricsF(painter.font())
            tip_text = self._crossfadeTipText()
            painter.drawText(
                int((self.width() - metrics.horizontalAdvance(tip_text)) * 0.5),
                int(10 + metrics.ascent()),
                tip_text,
            )

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
                QPen(
                    QColor(255, 255, 255, 80) if isDark else QColor(0, 0, 0, 80),
                    8,
                )
            )
            painter.drawLine(
                0,
                0,
                self._draw_current_x + self._prepared_lead_width,
                0,
            )

            painter.setPen(
                QPen(
                    QColor(255, 255, 255, bar_alpha)
                    if isDark
                    else QColor(0, 0, 0, bar_alpha),
                    8,
                )
            )
            painter.drawLine(
                progress_left,
                0,
                self._draw_current_x,
                0,
            )

        flash = 1 - self.beat_flash_timer.current_value
        if flash > 0:
            theme_color = self.ctx.main_window.song_theme
            if theme_color:
                width = self.width() * 0.1
                gradient = QLinearGradient(self._draw_current_x - width, 0, self._draw_current_x + 5, 0)
                gradient.setColorAt(0, QColor(255, 255, 255, 0))
                gradient.setColorAt(1, mixColor(QColor(255, 255, 255), theme_color, 1 - (flash * 0.5 + 0.1)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(gradient)
                painter.drawRect(QRectF(self._draw_current_x - width, -4, width + 5, 8))

        painter.end()

    def _onBeatPoint(self) -> None:
        if not cfg.beat_detection_visual_flash:
            return
        self.beat_flash_timer.current_value = 1
