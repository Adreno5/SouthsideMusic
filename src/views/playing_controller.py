from __future__ import annotations

import json
import math
import numpy as np
import time
from typing import TYPE_CHECKING, cast as _cast

if TYPE_CHECKING:
    from views.main_window import MainWindow
    from views.sidebar import Sidebar
    from views.playing_page import PlayingPage

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    Qt,
    QRect,
    QSize,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget
from qfluentwidgets import (
    PushButton,
    Slider,
    TransparentToolButton,
)

from utils.icon_util import bindIcon
from utils import darkdetect_util as darkdetect
from utils.time_util import float2time
from utils.lyric_util import LyricInfo, LRCLyricParser, YRCLyricParser
from utils.play_util import AudioPlayer
from utils.websocket_util import QObjectHandler
from views.song_card import DummyCard
from utils.config_util import cfg


class PlayingController(QWidget):
    onSongFinish = Signal()
    playLastSignal = Signal()
    playNextSignal = Signal()

    def __init__(
        self,
        player: AudioPlayer | None = None,
        mgr: LRCLyricParser | None = None,
        transmgr: LRCLyricParser | None = None,
        ymgr: YRCLyricParser | None = None,
        dp: PlayingPage | None = None,
        sidebar: Sidebar | None = None,
        mwindow: MainWindow | None = None,
        ws_handler=None,
    ):
        super().__init__()
        self._player: AudioPlayer = _cast(AudioPlayer, player)
        self._mgr: LRCLyricParser = _cast(LRCLyricParser, mgr)
        self._transmgr: LRCLyricParser = _cast(LRCLyricParser, transmgr)
        self._ymgr: YRCLyricParser = _cast(YRCLyricParser, ymgr)
        self._dp: PlayingPage = dp  # type: ignore
        self._sidebar: Sidebar = sidebar  # type: ignore
        self._mwindow: MainWindow = mwindow  # type: ignore
        self._ws_handler: QObjectHandler = _cast(QObjectHandler, ws_handler)

        self.expanded = False
        self.dragging = False

        self.dev_mag: float = 1

        self.lastfm = time.time()

        global_layout = QHBoxLayout()

        self.cur_freqs: np.ndarray | None = None
        self.cur_magnitudes: np.ndarray | None = None
        self.final_magnitudes: np.ndarray = np.zeros(513, dtype=np.float32)
        self.smoothed_magnitudes: np.ndarray = np.zeros(513, dtype=np.float32)
        self.draw_magnitudes: np.ndarray = np.zeros(513, dtype=np.float32)
        self.last_lyric: LyricInfo = LyricInfo(time=0, content="")

        self.time_label = QLabel()
        global_layout.addWidget(
            self.time_label,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        )

        self.last_btn = TransparentToolButton()
        bindIcon(self.last_btn, "last")
        self.next_btn = TransparentToolButton()
        bindIcon(self.next_btn, "next")
        self.last_btn.clicked.connect(self.playLastSignal.emit)
        self.next_btn.clicked.connect(self.playNextSignal.emit)

        self.play_pausebtn = TransparentToolButton()
        bindIcon(self.play_pausebtn, "playa")
        self.play_pausebtn.setIconSize(QSize(30, 30))
        self.last_btn.setIconSize(QSize(30, 30))
        self.next_btn.setIconSize(QSize(30, 30))
        self.play_pausebtn.clicked.connect(self.toggle)
        global_layout.addWidget(
            self.last_btn,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
        )
        global_layout.addWidget(
            self.play_pausebtn,
            alignment=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
        )
        global_layout.addWidget(
            self.next_btn,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        )

        right_layout = QVBoxLayout()

        self.vol_slider = Slider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(100)
        self.vol_slider.valueChanged.connect(self.updateVol)
        right_layout.addWidget(
            self.vol_slider,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
        )
        self.expand_btn = PushButton("Menu")
        bindIcon(self.expand_btn, "pl_expand")
        right_layout.addWidget(
            self.expand_btn,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
        )
        self.expand_btn.clicked.connect(self.toggleExpand)

        global_layout.addLayout(right_layout)

        self.setLayout(global_layout)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.updateWidgets)
        self.timer.start(20)
        self.playingtime_lastupdate = time.perf_counter()

        self._player.fftDataReady.connect(self.updateFFTData)

    def updateFFTData(self, freqs: np.ndarray, magnitudes: np.ndarray) -> None:
        self.cur_freqs = freqs
        self.cur_magnitudes = magnitudes

    def toggleExpand(self):
        self.expanded = not self.expanded
        self.expand_btn.setEnabled(False)

        if self.expanded:
            if not self._mwindow.isMaximized():
                mwindow_anim = QPropertyAnimation(self._mwindow, b"geometry", self)
                mwindow_anim.setDuration(200)
                mwindow_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                mwindow_anim.setStartValue(self._mwindow.geometry())
                mwindow_anim.finished.connect(lambda: self.expand_btn.setEnabled(True))
                mwindow_anim.setEndValue(
                    QRect(
                        self._mwindow.x() - 250,
                        self._mwindow.y(),
                        self._mwindow.width() + 505,
                        self._mwindow.height(),
                    )
                )
                mwindow_anim.start()
            else:
                self.expand_btn.setEnabled(True)

            self._sidebar.show()

            self.expand_btn.setText("Collapse")
            bindIcon(self.expand_btn, "pl_collapse")
        else:
            self._sidebar.hide()
            if not self._mwindow.isMaximized():
                mwindow_anim = QPropertyAnimation(self._mwindow, b"geometry", self)
                mwindow_anim.setDuration(200)
                mwindow_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                mwindow_anim.setStartValue(self._mwindow.geometry())
                mwindow_anim.finished.connect(lambda: self.expand_btn.setEnabled(True))
                mwindow_anim.setEndValue(
                    QRect(
                        self._mwindow.x() + 250,
                        self._mwindow.y(),
                        self._mwindow.width() - 505,
                        self._mwindow.height(),
                    )
                )
                mwindow_anim.start()
            else:
                self.expand_btn.setEnabled(True)

            self.expand_btn.setText("Menu")
            bindIcon(self.expand_btn, "pl_expand")

    def updateWidgets(self):
        from views.title_bar import SouthsideMusicTitleBar

        title_bar = None
        try:
            title_bar = self._mwindow.titleBar
        except:
            pass
        if isinstance(title_bar, SouthsideMusicTitleBar):
            if self._mwindow.stackedWidget.currentWidget() == self._dp:
                title_bar.song_title.clear()
                title_bar.lyric_label.clear()
                title_bar.fm_label.setPixmap(QPixmap())
            else:
                title_bar.song_title.setText(self._dp.title_label.text())
                l = self._mgr.getCurrentLyric(self._player.getPosition())
                title_bar.lyric_label.setText(
                    l["content"] if l["content"] else self._dp.artists_label.text()
                )
                title_bar.fm_label.setPixmap(
                    self._dp.img_label.pixmap().scaled(
                        40,
                        40,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )

        try:
            self._sidebar.southsideclient_status_label.setText(
                "Connection Status: <span style='color: green;'>Connected</span>"
                if self._mwindow.connected
                else "Connection Status: <span style='color: red;'>Disconnected</span>"
            )
            self._sidebar.now_volume.setText(
                f"Current volume(db): {(round(self._player.db * 10) / 10) if self._player.db != float('-inf') else '-inf'}"
            )
        except:
            pass

        if self._dp.cur and self._sidebar.lst_shoud_set:
            for i, song in enumerate(self._dp.playlist):
                if (
                    self._dp.cur
                    and hasattr(self._dp.cur, "storable")
                    and song.name == self._dp.cur.storable.name
                ):
                    self._sidebar.lst.setCurrentRow(i)
                    break

        if self._player.isPlaying():
            if (
                not self._dp._preload_triggered
                and self._dp.current_index < len(self._dp.playlist) - 1
            ):
                self._dp._preload_triggered = True
                self._dp.preloadNextSong()

            if self._dp.current_index >= len(self._dp.playlist) - 1:
                self._dp.preloaded = True

        cl = self._mgr.getCurrentLyric(self._player.getPosition())
        nxt = self._mgr.getOffsetedLyric(self._player.getPosition(), 1)
        trd = self._mgr.getOffsetedLyric(self._player.getPosition(), 2)
        lat = self._mgr.getOffsetedLyric(self._player.getPosition(), -1)
        if cl != self.last_lyric:
            self._ws_handler.send(
                json.dumps(
                    {
                        "option": "update_lyric",
                        "current": cl["content"],
                        "next": nxt["content"],
                        "third": trd["content"],
                        "last": lat["content"],
                    }
                )
            )
            self.last_lyric = cl

        if self._sidebar.enableFFT_box.isChecked():
            if not self._player.isPlaying():
                self.cur_magnitudes = np.zeros(513, dtype=np.float32)
            window_size = int(cfg.fft_filtering_windowsize)

            self.smoothed_magnitudes += (
                self.cur_magnitudes - self.smoothed_magnitudes
            ) * cfg.fft_factor
            self.final_magnitudes = np.convolve(
                self.smoothed_magnitudes,
                np.ones(window_size) / window_size,
                mode="same",
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
                        "option": "update_fft",
                        "magnitudes": [
                            float(item) * cfg.sfft_multiple
                            for item in self.draw_magnitudes.tolist()
                        ],
                    }
                )
            )

        if time.time() - self.lastfm > 2.5:
            self.lastfm = time.time()
            self._dp.sendSongFMAndInfo()

        if not self._player.isPlaying():
            bindIcon(self.play_pausebtn, "playa")
        else:
            bindIcon(self.play_pausebtn, "pause")

        if self._mwindow and self._mwindow.isVisible():
            self.repaint()

    def updateVol(self):
        value = self.vol_slider.value()
        if value == 0:
            volume = 0
        else:
            volume = math.log(value / 100 * (math.e - 1) + 1)
        cfg.volume = volume
        self._player.setVolume(volume)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.position().y() < 8 and self._dp.preloaded:
            self.dragging = True
            playing_time = min(
                self._dp.total_length,
                max(0, (event.position().x() / self.width()) * self._dp.total_length),
            )
            self._player.setPosition(playing_time)
        return super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.dragging and self._dp.preloaded:
            playing_time = min(
                self._dp.total_length,
                max(0, (event.position().x() / self.width()) * self._dp.total_length),
            )
            self._player.setPosition(playing_time)
            self.dragging = False
        return super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.dragging and self._dp.preloaded:
            playing_time = min(
                self._dp.total_length,
                max(0, (event.position().x() / self.width()) * self._dp.total_length),
            )
            self._player.setPosition(playing_time)
        return super().mouseMoveEvent(event)

    def toggle(self):
        if self._player.isPlaying():
            self._player.pause()
        else:
            self._player.resume()

    def setPlaytime(self, time_value: float) -> None:
        self._player.setPosition(time_value)

    def paintEvent(self, event: QPaintEvent) -> None:
        from PySide6.QtGui import QPixmap

        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)

        isDark = darkdetect.isDark()

        if (
            self._sidebar.enableFFT_box.isChecked()
            and self.cur_freqs is not None
            and self.cur_magnitudes is not None
        ):
            self.draw_magnitudes = np.maximum(
                self.final_magnitudes, self.draw_magnitudes
            )
            self.draw_magnitudes = np.maximum(self.draw_magnitudes * 0.8, 0)

            path = QPainterPath(QPointF(0, 0))
            total = int(self.cur_magnitudes.size * 0.67)
            for i in range(total):
                x = ((i + 1) / total) * self.width()
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
        painter.drawLine(0, 0, self.width(), 0)
        if self._dp.total_length > 0:
            painter.setPen(
                QPen(QColor(255, 255, 255) if isDark else QColor(0, 0, 0), 8)
            )
            painter.drawLine(
                0,
                0,
                int(
                    self.width()
                    * (self._player.getPosition() / self._dp.total_length)
                ),
                0,
            )

            cur_time = float2time(self._player.getPosition())
            self.time_label.setText(
                f"{str(cur_time['minutes']).zfill(2)}:{str(cur_time['seconds']).zfill(2)}"
            )

        painter.end()
