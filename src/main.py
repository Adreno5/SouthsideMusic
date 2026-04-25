from pprint import pprint
import re
import shutil
import logging
import sys
import datetime
import os
import threading
from typing import TextIO, Optional, override
from PySide6.QtGui import QKeyEvent, QMouseEvent, QResizeEvent
from colorama import Fore, Style, init

sys.path.append(os.path.join(os.path.dirname(__file__), "utils"))
sys.path.append(os.path.dirname(__file__))

init(autoreset=True)

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _visible_len(text: str) -> int:
    return len(_ANSI_ESCAPE.sub("", text))


class LogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()

        color = {
            "DEBUG": Fore.LIGHTBLACK_EX,
            "INFO": Fore.LIGHTGREEN_EX,
            "WARNING": Fore.YELLOW,
            "ERROR": Fore.RED,
            "CRITICAL": Fore.RED,
        }.get(record.levelname, Fore.WHITE)

        time_str = datetime.datetime.now().strftime("%H:%M:%S")
        plain_prefix = f"[{time_str}/{record.levelname}] - "
        plain_msg = plain_prefix + message
        plain_suffix = f"[{record.thread}/{record.threadName}]"

        try:
            term_width = shutil.get_terminal_size().columns
        except Exception:
            term_width = 80

        visible_len = _visible_len(plain_msg) + _visible_len(plain_suffix)
        spaces = max(term_width - visible_len, 1)

        colored_prefix = (
            f"[{Fore.LIGHTBLACK_EX}{time_str}{Style.RESET_ALL}/"
            f"{color}{Style.BRIGHT}{record.levelname}{Style.RESET_ALL}] "
            f"{Fore.LIGHTBLACK_EX}-{Style.RESET_ALL} "
        )
        colored_suffix = (
            f"{Fore.LIGHTGREEN_EX}[{Style.RESET_ALL}"
            f"{record.thread}/{record.threadName}"
            f"{Fore.LIGHTGREEN_EX}]{Style.RESET_ALL}"
        )

        final = f"{colored_prefix}{message}{' ' * spaces}{colored_suffix}"
        assert sys.__stdout__ is not None
        sys.__stdout__.write(final + "\n")
        sys.__stdout__.flush()


class LoggingStream:
    def __init__(self, level: int = logging.DEBUG, source: str = "stderr"):
        self.level = level
        self.source = source
        self.buffer = ""
        self.original_stream: Optional[TextIO] = None

    def write(self, message: str) -> int:
        if not message:
            return 0

        if getattr(self, "_in_logging", False):
            if self.original_stream:
                self.original_stream.write(message)
            return len(message)

        self.buffer += message
        if self.buffer.endswith("\n"):
            self._flush_buffer()
        return len(message)

    def _flush_buffer(self):
        lines = self.buffer.splitlines()
        self.buffer = ""
        for line in lines:
            if not line:
                continue
            self._in_logging = True

            if "QFluentWidgets" in line.strip():
                continue

            try:
                if self.source == "stderr":
                    logging.error(line.strip())
                else:
                    logging.info(line.strip())
            finally:
                self._in_logging = False

    def flush(self):
        if self.buffer:
            self._flush_buffer()
        if self.original_stream:
            self.original_stream.flush()

    def fileno(self) -> int:
        return self.original_stream.fileno() if self.original_stream else -1

    def isatty(self) -> bool:
        return False


class StderrRedirector:
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger()
        self.pipe_read, self.pipe_write = os.pipe()
        self.original_stderr_fd = os.dup(2)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._buffer = b""

    def start(self):
        os.dup2(self.pipe_write, 2)
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _read_loop(self):
        while not self._stop_event.is_set():
            try:
                data = os.read(self.pipe_read, 4096)
                if not data:
                    break
                self._buffer += data
                while b"\n" in self._buffer:
                    line, self._buffer = self._buffer.split(b"\n", 1)
                    self._log_line(line.decode("utf-8", errors="replace"))
            except (OSError, ValueError):
                break

    def _log_line(self, line: str):
        line = line.strip()
        if "QPixmap::scaled" in line or "QFont" in line:
            return
        if line:
            self.logger.error(line)

    def stop(self):
        self._stop_event.set()
        os.dup2(self.original_stderr_fd, 2)
        os.close(self.pipe_read)
        os.close(self.pipe_write)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)


def hijackStreams():
    stderr_redirector = StderrRedirector()
    stderr_redirector.start()

    original_stdout = sys.stdout
    original_stderr = sys.stderr

    stdout_stream = LoggingStream(logging.INFO, source="stdout")
    stderr_stream = LoggingStream(logging.ERROR, source="stderr")
    stdout_stream.original_stream = original_stdout
    stderr_stream.original_stream = original_stderr

    sys.stdout = stdout_stream
    sys.stderr = stderr_stream

    return original_stdout, original_stderr, stderr_redirector


if __name__ == "__main__":
    logging_handler = LogHandler()
    logging.basicConfig(level=logging.DEBUG, handlers=[logging_handler])

    hijackStreams()

from PySide6.QtWidgets import *  # type: ignore
from PySide6.QtCore import *  # type: ignore
from PySide6.QtGui import *  # type: ignore
import hPyT
import threading

app = QApplication([])

import base64
import io
import os
import subprocess
import time
import traceback
from types import FrameType, TracebackType
from typing import TextIO, Union
import numpy as np
from qfluentwidgets import *  # type: ignore
from qfluentwidgets.window.fluent_window import FluentWindowBase
from qframelesswindow import TitleBar
import requests

import darkdetect
import math

from utils.random_util import AdvancedRandom

from functools import lru_cache
from utils.base.base_util import FolderInfo, SongInfo, SongStorable
from utils.base.base_util import SongDetail
from utils.lyric_util import LRCLyricManager, LyricInfo
from utils.time_util import float2time
from utils.favorite_util import (
    loadFavorites,
    loadFavoritesWithLaunching,
    saveFavorites,
    getFavoriteSong,
)
from utils.config_util import loadConfig, saveConfig, cfg
from utils.loudness_balance_util import getAdjustedGainFactor
from utils.play_util import AudioPlayer
from utils.play_util import PatchedAudioSegment as AudioSegment
from utils.icon_util import getQIcon
from utils.dialog_util import QRCodeLoginDialog, get_value_bylist, get_text_lineedit
from utils.websocket_util import WebSocketServer, ws_server, ws_handler
from utils.pyside_util import remove_widgets

from pyncm import apis
import pyncm as ncm

ws_handler.onConnected.connect(lambda: mwindow.onWebsocketConnected())
ws_handler.onDisconnected.connect(lambda: mwindow.onWebsocketDisconnected())

original_popen = subprocess.Popen


def patched_popen(*args, **kwargs):
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    kwargs["startupinfo"] = startupinfo
    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    return original_popen(*args, **kwargs)


subprocess.Popen = patched_popen
subprocess.call = patched_popen


def patchedExceptHook(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_traceback: TracebackType,
):
    inf: list[str] = []

    logging.error("| Unhandled Exception occurred |")
    logging.error(f"Caused by {exc_type.__name__}")
    logging.error("Traceback:")
    inf.append("| Unhandled Exception occurred |")
    inf.append(f"Caused by {exc_type.__name__}")
    inf.append("Traceback:")
    stack_frames = traceback.extract_tb(exc_traceback)
    for frame in stack_frames:
        logging.error(
            f"    at {Path(frame.filename).resolve().as_posix()}:{frame.lineno}|{frame.name}"
        )
        inf.append(
            f"    at {Path(frame.filename).resolve().as_posix()}:{frame.lineno}|{frame.name}"
        )
    logging.error("Exception chain:")
    inf.append("Exception chain:")
    current_exc = exc_value
    logging.error(f"    caused by {type(current_exc).__name__}({current_exc}) #0")
    inf.append(f"    caused by {type(current_exc).__name__}({current_exc}) #0")
    if current_exc.__traceback__:
        root_frames = traceback.extract_tb(current_exc.__traceback__)
        for frame in root_frames:
            logging.error(
                f"      at {Path(frame.filename).resolve().as_posix()}:{frame.lineno}|{frame.name}"
            )
            inf.append(
                f"      at {Path(frame.filename).resolve().as_posix()}:{frame.lineno}|{frame.name}"
            )
    chain_level = 1
    while True:
        next_exc = current_exc.__cause__ or current_exc.__context__
        if not next_exc or next_exc is current_exc:
            break
        exc = next_exc
        logging.error(f"    caused by {type(exc).__name__}({exc}) #{chain_level}")
        inf.append(f"    caused by {type(exc).__name__}({exc}) #{chain_level}")
        if next_exc.__traceback__:
            root_frames = traceback.extract_tb(next_exc.__traceback__)
            for frame in root_frames:
                logging.error(
                    f"      at {Path(frame.filename).resolve().as_posix()}:{frame.lineno}|{frame.name}"
                )
                inf.append(
                    f"      at {Path(frame.filename).resolve().as_posix()}:{frame.lineno}|{frame.name}"
                )
        current_exc = next_exc
        chain_level += 1
    logging.error(f"Raised {exc_type.__name__}({exc_value})")
    inf.append(f"Raised {exc_type.__name__}({exc_value})")

    if exc_type is KeyboardInterrupt:
        logging.info("quit by user")
        mwindow.close()
        app.quit()
        sys.exit()

    txt = "\n".join(inf)
    launchwindow.deleteLater()
    QMessageBox.critical(
        None,
        "Error Occured",
        txt,
        buttons=QMessageBox.StandardButton.Close,
        defaultButton=QMessageBox.StandardButton.Close,
    )

    saveConfig()


sys.excepthook = patchedExceptHook


class DummyCard:
    def __init__(self, storable: SongStorable):
        self.info: SongInfo = SongInfo(
            name=storable.name,
            artists=storable.artists,
            id=storable.id,
            privilege=-1,
        )
        self.detail: SongDetail = SongDetail(image_url="")
        self.storable: SongStorable = storable


class SongCard(QWidget):
    imageLoaded = Signal(bytes)

    def __init__(self, info: SongInfo) -> None:
        super().__init__()
        self.info = info

        self.detail = SongDetail(image_url="")

        global_layout = QVBoxLayout()
        top_layout = FlowLayout()

        ali = Qt.AlignmentFlag
        self.img_label = QLabel()
        self.img_label.setFixedSize(100, 100)
        top_layout.addWidget(self.img_label)
        self.img_label.hide()
        self.ring = IndeterminateProgressRing()
        self.ring.setFixedSize(100, 100)
        top_layout.addWidget(self.ring)
        title_label = SubtitleLabel(info["name"])
        top_layout.addWidget(title_label)
        artists_label = QLabel(info["artists"])
        artists_label.setWordWrap(True)
        top_layout.addWidget(artists_label)
        self.vip_label = SubtitleLabel(
            f"Need more privilege ({info['privilege']}(song)>{ncm.GetCurrentSession().vipType}(yours))"
        )
        self.vip_label.setStyleSheet("color: red;")
        if info["privilege"] <= ncm.GetCurrentSession().vipType:
            self.vip_label.hide()
        top_layout.addWidget(self.vip_label)

        pri_label = QLabel(
            f"privilege: (song: {info['privilege']}, yours: {ncm.GetCurrentSession().vipType})"
        )
        pri_label.setStyleSheet(
            f"color: {'#666666' if darkdetect.isDark() else '#CCCCCC'};"
        )
        top_layout.addWidget(pri_label)

        bottom_layout = FlowLayout()

        self.playbtn = PrimaryToolButton(FluentIcon.SEND)
        self.playbtn.setEnabled(False)
        bottom_layout.addWidget(self.playbtn)
        self.playbtn.clicked.connect(self.play)

        self.favbtn = TransparentToolButton(getQIcon("fav"))
        self.favbtn.setEnabled(True)
        bottom_layout.addWidget(self.favbtn)
        self.favbtn.clicked.connect(self.addToFavorites)

        global_layout.addLayout(top_layout)
        global_layout.addLayout(bottom_layout)

        self.setLayout(global_layout)

        self.load = False
        self.imageLoaded.connect(self.onImageLoaded)

    def play(self):
        mwindow.play(self)

    def addToFavorites(self):
        if self.info["privilege"] > ncm.GetCurrentSession().vipType:
            InfoBar.warning(
                "Cannot add to favorites",
                "Need more privilege",
                parent=mwindow,
            )
            return

        result_container = []

        def _download():
            with ncm.GetCurrentSession():
                response = apis.track.GetTrackDetail(song_ids=[self.info["id"]])
                assert isinstance(response, dict), "Invalid response"
                image_url = response["songs"][0]["al"]["picUrl"]  # type: ignore

                # Download image
                image_bytes = requests.get(
                    image_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    },
                ).content

                # Download music
                music_url = apis.track.GetTrackAudio(
                    str(self.info["id"]), # type: ignore
                    bitrate=3200 * 1000,  # type: ignore
                )  # type: ignore
                logging.debug(f"{music_url['data'][0]['url']=}")  # type: ignore
                music_bytes = requests.get(
                    music_url["data"][0]["url"],  # type: ignore
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    },
                ).content

                # Download lyrics
                lyric_data = apis.track.GetTrackLyrics(song_id=self.info["id"])
                assert isinstance(lyric_data, dict), "Invalid response"

                lyric = lyric_data["lrc"]["lyric"]  # type: ignore
                translated_lyric = lyric_data.get("tlyric", {}).get(
                    "lyric", "[00:00.000]"
                )

                result_container.append(
                    (image_bytes, music_bytes, lyric, translated_lyric)
                )

        def _on_finished():
            if not result_container:
                InfoBar.error(
                    "Failed to add to favorites", "Download failed", parent=mwindow
                )
                return

            image_bytes, music_bytes, lyric, translated_lyric = result_container[0]

            # Prepare folder list for selection
            folder_names = [folder["folder_name"] for folder in favs]
            folder_names.append("Create new folder...")

            # Let user select folder
            selected = get_value_bylist(
                mwindow,
                "Select folder",
                f"which folder do you want to add {self.info['name']} to?",
                folder_names,
            )

            if not selected:
                # User cancelled
                return

            selected_folder = selected

            # Handle new folder creation
            if selected_folder == "Create new folder...":
                new_folder_name = get_text_lineedit(
                    "Create New Folder", "Enter folder name", "My folder", mwindow
                )

                if not new_folder_name:
                    # User cancelled
                    return

                # Create new folder
                new_folder = FolderInfo(folder_name=new_folder_name, songs=[])
                favs.append(new_folder)
                selected_folder = new_folder_name

            # Find the target folder
            target_folder = None
            for folder in favs:
                if folder["folder_name"] == selected_folder:
                    target_folder = folder
                    break

            # Add song to target folder

            with lock:
                song_storable = SongStorable(
                    self.info,
                    image_bytes,
                    music_bytes,
                    lyric,
                    translated_lyric,
                    getAdjustedGainFactor(
                        -16, AudioSegment.from_file(io.BytesIO(music_bytes))
                    ),
                    cfg.target_lufs,
                )
                target_folder["songs"].append(song_storable)  # type: ignore

            # Save favorites
            from utils.favorite_util import saveFavorites

            saveFavorites(favs)

            InfoBar.success(
                "Added to favorites",
                f"Added {self.info['name']} To {selected_folder}",
                parent=mwindow,
                duration=2000,
            )

            fp.refresh()

        doWithMultiThreading(_download, (), mwindow, "Downloading...", _on_finished)

    def loadDetailAndImage(self):
        self.load = True

        @lru_cache(maxsize=128)
        def _load():
            with ncm.GetCurrentSession():
                response = apis.track.GetTrackDetail(song_ids=[self.info["id"]])
                assert isinstance(response, dict), "Invalid response"

                self.detail["image_url"] = response["songs"][0]["al"]["picUrl"]  # type: ignore

                image: bytes = requests.get(
                    self.detail["image_url"],
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    },
                ).content

                self.imageLoaded.emit(image)

        doWithMultiThreading(_load, (), mwindow, f"Loading...", dialog=False)

    @Slot(bytes)
    def onImageLoaded(self, byte_data: bytes):
        pixmap = QPixmap()
        pixmap.loadFromData(byte_data)
        if not pixmap.isNull():
            scaled_pixmap = pixmap.scaled(
                self.img_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.img_label.setPixmap(scaled_pixmap)

            self.img_label.show()
            self.ring.hide()

            if self.info["privilege"] < ncm.GetCurrentSession().vipType:
                self.playbtn.setEnabled(True)


class _SongCardItem(QWidget):
    clicked = Signal(object)

    def __init__(self, storable: SongStorable, parent=None):
        super().__init__(parent)
        self.storable = storable

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        self.img_label = QLabel()
        self.img_label.setFixedSize(50, 50)
        layout.addWidget(self.img_label)

        text_layout = QVBoxLayout()
        title_label = SubtitleLabel(storable.name)
        title_label.setWordWrap(True)
        text_layout.addWidget(title_label)
        artists_label = QLabel(storable.artists)
        artists_label.setWordWrap(True)
        text_layout.addWidget(artists_label)
        layout.addLayout(text_layout, 1)

        self.loadImage()

    def loadImage(self):
        try:
            image_bytes = self.storable.get_image_bytes()
            pixmap = QPixmap()
            pixmap.loadFromData(image_bytes)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self.img_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.img_label.setPixmap(scaled)
        except Exception:
            pass

    def mousePressEvent(self, event: QMouseEvent):
        self.clicked.emit(self.storable)
        return super().mousePressEvent(event)


class PlaylistSongCard(_SongCardItem):
    pass


class FavoriteSongCard(_SongCardItem):
    pass


class SearchPage(QWidget):
    resultGot = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("search_page")
        self.img_card_map: dict[str, SongCard] = {}

        global_layout = QVBoxLayout()

        top_layout = QHBoxLayout()
        self.inputer = LineEdit()
        self.search_btn = PrimaryPushButton(FluentIcon.SEARCH, "Search")
        self.search_btn.clicked.connect(self.search)
        self.inputer.returnPressed.connect(self.search)
        top_layout.addWidget(self.inputer)
        top_layout.addWidget(self.search_btn)
        global_layout.addLayout(top_layout)

        self.lst = ListWidget()
        self.lst.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.lst.verticalScrollBar().setSingleStep(14)
        self.lst.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.lst.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        global_layout.addWidget(self.lst)

        self.setLayout(global_layout)

        self.resultGot.connect(self.addSongs)

        self.check_timer = QTimer(self)
        self.check_timer.timeout.connect(self.checkRect)
        self.check_timer.start(50)

        self.cards: list[SongCard] = []

    def checkRect(self) -> None:
        for i, card in enumerate(self.cards):
            item = self.lst.item(i)
            if item is None:
                continue

            item_rect = self.lst.visualItemRect(item)
            viewport_rect = self.lst.viewport().rect()

            if viewport_rect.intersects(item_rect) and not card.load:
                logging.debug(f"loading {card.info['name']}")
                card.loadDetailAndImage()

    def setImage_(self, byte: bytes, ca: SongCard):
        ca.img_label.setPixmap(QPixmap(byte))

    def search(self) -> None:
        if not self.inputer.text().strip():
            InfoBar.warning("Search failed", "the keyword is empty!", parent=mwindow)
            return

        if self.search_btn.isEnabled() is False:
            return

        self.search_btn.setEnabled(False)
        self.lst.clear()
        self.cards.clear()
        self.img_card_map.clear()

        result: list[SongInfo] = []

        def _do():
            nonlocal result
            result = wy.search(self.inputer.text())

        def _finish():
            nonlocal result

            self.search_btn.setEnabled(True)

            self.resultGot.emit(result)

        doWithMultiThreading(_do, (), mwindow, "Searching...", _finish)

    def addSongs(self, result: list[SongInfo]) -> None:
        for i, song in enumerate(result):
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 150))
            self.lst.addItem(item)
            content_widget = SongCard(song)
            self.lst.setItemWidget(item, content_widget)
            self.cards.append(content_widget)
            content_widget.load = False


class PlayingController(QWidget):
    onSongFinish = Signal()
    playLastSignal = Signal()
    playNextSignal = Signal()

    def __init__(self):
        super().__init__()
        self.expanded = False
        self.dragging = False

        self.dev_mag: float = 1

        self.lastfm = time.time()

        global_layout = QHBoxLayout()

        self.cur_freqs: np.ndarray | None = None
        self.cur_magnitudes: np.ndarray | None = None
        self.final_magnitudes: np.ndarray = np.zeros(513, dtype=np.float32)
        self.smoothed_magnitudes: np.ndarray = np.zeros(513, dtype=np.float32)
        self.last_lyric: LyricInfo = LyricInfo(time=0, content="")

        self.time_label = QLabel()
        global_layout.addWidget(
            self.time_label,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        )

        self.last_btn = TransparentToolButton(getQIcon("last"))
        self.next_btn = TransparentToolButton(getQIcon("next"))
        self.last_btn.clicked.connect(self.playLastSignal.emit)
        self.next_btn.clicked.connect(self.playNextSignal.emit)

        self.play_pausebtn = TransparentToolButton(getQIcon("playa"))
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
        self.expand_btn = PushButton(getQIcon("pl_expand"), "Menu")
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

        player.fftDataReady.connect(self.updateFFTData)

    def updateFFTData(self, freqs: np.ndarray, magnitudes: np.ndarray) -> None:
        self.cur_freqs = freqs
        self.cur_magnitudes = magnitudes

    def toggleExpand(self):
        self.expanded = not self.expanded

        if self.expanded:
            if not mwindow.isMaximized():
                mwindow_anim = QPropertyAnimation(mwindow, b"geometry", self)
                mwindow_anim.setDuration(200)
                mwindow_anim.setEasingCurve(QEasingCurve.Type.OutBack)
                mwindow_anim.setStartValue(mwindow.geometry())
                mwindow_anim.setEndValue(
                    QRect(
                        mwindow.x() - 250,
                        mwindow.y(),
                        mwindow.width() + 505,
                        mwindow.height(),
                    )
                )
                mwindow_anim.start()

            dp.expanded_widget.show()

            self.expand_btn.setText("Collapse")
            self.expand_btn.setIcon(getQIcon("pl_collapse"))
        else:
            dp.expanded_widget.hide()
            if not mwindow.isMaximized():
                mwindow_anim = QPropertyAnimation(mwindow, b"geometry", self)
                mwindow_anim.setDuration(200)
                mwindow_anim.setEasingCurve(QEasingCurve.Type.OutBack)
                mwindow_anim.setStartValue(mwindow.geometry())
                mwindow_anim.setEndValue(
                    QRect(
                        mwindow.x() + 250,
                        mwindow.y(),
                        mwindow.width() - 505,
                        mwindow.height(),
                    )
                )
                mwindow_anim.start()

            self.expand_btn.setText("Menu")
            self.expand_btn.setIcon(getQIcon("pl_expand"))

    def updateWidgets(self):
        title_bar = None
        try:
            title_bar = mwindow.titleBar
        except:
            pass
        if isinstance(title_bar, SouthsideMusicTitleBar):
            if mwindow.stackedWidget.currentWidget() == dp:
                title_bar.song_title.clear()
                title_bar.lyric_label.clear()
                title_bar.fm_label.setPixmap(QPixmap())
            else:
                title_bar.song_title.setText(dp.title_label.text())
                title_bar.lyric_label.setText(
                    dp.lyric_label.text()
                    if dp.lyric_label.text()
                    else dp.artists_label.text()
                )
                title_bar.fm_label.setPixmap(
                    dp.img_label.pixmap().scaled(
                        40,
                        40,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )

        try:
            dp.southsideclient_status_label.setText(
                "Connection Status: <span style='color: green;'>Connected</span>"
                if mwindow.connected
                else "Connection Status: <span style='color: red;'>Disconnected</span>"
            )
            dp.now_volume.setText(
                f"Current volume(db): {(round(player.db * 10) / 10) if player.db != float('-inf') else '-inf'}"
            )
        except:
            pass

        if dp.cur and dp.lst_shoud_set:
            # Highlight the currently playing song in the playlist
            for i, song in enumerate(dp.playlist):
                if (
                    dp.cur
                    and hasattr(dp.cur, "storable")
                    and song.name == dp.cur.storable.name
                ):
                    dp.lst.setCurrentRow(i)
                    break

        if player.isPlaying():
            if not dp._preload_triggered and dp.current_index < len(dp.playlist) - 1:
                dp._preload_triggered = True
                dp.preloadNextSong()

        cl = mgr.getCurrentLyric(player.getPosition())
        nxt = mgr.getOffsetedLyric(player.getPosition(), 1)
        trd = mgr.getOffsetedLyric(player.getPosition(), 2)
        lat = mgr.getOffsetedLyric(player.getPosition(), -1)
        if cl != self.last_lyric:
            ws_handler.send(
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

        if dp.enableFFT_box.isChecked():
            if not player.isPlaying():
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
            if isinstance(dp.cur, DummyCard):
                self.final_magnitudes *= (2 / dp.cur.storable.loudness_gain) * 0.75
            
            maxmag = max(np.max(self.final_magnitudes), 10)
            self.dev_mag += (maxmag - self.dev_mag) * 0.35
            self.final_magnitudes /= self.dev_mag
            self.final_magnitudes *= self.height() - 10

            ws_handler.send(
                json.dumps(
                    {
                        "option": "update_fft",
                        "magnitudes": [
                            float(item) * cfg.sfft_multiple
                            for item in self.final_magnitudes.tolist()
                        ],
                    }
                )
            )

        if time.time() - self.lastfm > 2.5:
            self.lastfm = time.time()
            dp.sendSongFMAndInfo()

        if player.isPlaying():
            self.play_pausebtn.setIcon(getQIcon("playa"))
        else:
            self.play_pausebtn.setIcon(getQIcon("pause"))

        self.repaint()

    def updateVol(self):
        value = self.vol_slider.value()
        if value == 0:
            volume = 0
        else:
            volume = math.log(value / 100 * (math.e - 1) + 1)
        player.setVolume(volume)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.position().y() < 8:
            self.dragging = True
            playing_time = min(
                dp.total_length,
                max(0, (event.position().x() / self.width()) * dp.total_length),
            )
            player.setPosition(playing_time)
        return super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.dragging:
            playing_time = min(
                dp.total_length,
                max(0, (event.position().x() / self.width()) * dp.total_length),
            )
            player.setPosition(playing_time)
            self.dragging = False
        return super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.dragging:
            playing_time = min(
                dp.total_length,
                max(0, (event.position().x() / self.width()) * dp.total_length),
            )
            player.setPosition(playing_time)
        return super().mouseMoveEvent(event)

    def toggle(self):
        logging.debug("toggle")

        if player.isPlaying():
            player.pause()
        else:
            player.resume()

    def setPlaytime(self, time_value: float) -> None:
        playing_time = time_value
        player.setPosition(playing_time)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)

        isDark = darkdetect.isDark()

        if (
            dp.enableFFT_box.isChecked()
            and self.cur_freqs is not None
            and self.cur_magnitudes is not None
        ):
            path = QPainterPath(QPointF(0, 0))
            total = int(self.cur_magnitudes.size / 1.5)
            for i in range(total):
                x = ((i + 1) / total) * self.width()
                path.lineTo(
                    QPointF(
                        x,
                        (
                            (self.final_magnitudes[i] * ((1 + (i * 0.01)) - 0.1))
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
        if dp.total_length > 0:
            painter.setPen(
                QPen(QColor(255, 255, 255) if isDark else QColor(0, 0, 0), 8)
            )
            painter.drawLine(
                0, 0, int(self.width() * (player.getPosition() / dp.total_length)), 0
            )

        cur_time = float2time(player.getPosition())
        # self.time_label.setText(
        #     f'{str(cur_time['minutes']).zfill(2)}:{str(cur_time['seconds']).zfill(2)}.{str(cur_time['millionsecs']).zfill(3)}'
        # )
        self.time_label.setText(
            f"{str(cur_time['minutes']).zfill(2)}:{str(cur_time['seconds']).zfill(2)}"
        )

        yw = mgr.getCurrentLyric(player.getPosition())["content"]
        dp.lyric_label.setText(yw)
        if not yw.strip():
            dp.transl_label.setText("")
        else:
            dp.transl_label.setText(
                transmgr.getCurrentLyric(player.getPosition())["content"]
            )

        painter.end()


class PlayingPage(QWidget):
    imageLoaded = Signal(bytes)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("studio_page")
        self.cur: DummyCard | None = None

        self.total_length = 0

        self._preload_triggered = False

        # Playlist management
        self.playlist: list[SongStorable] = []
        self.current_index = -1
        self.next_song_audio: AudioSegment | None = None
        self.next_song_gain: float | None = None

        # Caches
        self._gain_cache: dict[str, float] = {}

        self.controller = PlayingController()
        player.onFullFinished.connect(self.controller.onSongFinish.emit)
        player.onEndingNoSound.connect(self.onEndingNoSound)
        self.controller.onSongFinish.connect(lambda: self.playNext(False))
        # Connect play button to start playlist if no song is loaded
        self.controller.play_pausebtn.clicked.connect(self.onPlayButtonClicked)

        self.lst_shoud_set: bool = True

        global_layout = QHBoxLayout()

        contents_layout = QVBoxLayout()

        ali = Qt.AlignmentFlag

        top_layout = FlowLayout(needAni=False)
        # top_layout.setAnimation(500, QEasingCurve.Type.OutCubic)
        topright_layout = QVBoxLayout()
        topright_widget = QWidget()
        topright_widget.setLayout(topright_layout)
        self.img_label = QLabel()
        self.img_label.hide()
        self.img_label.setFixedSize(200, 200)
        self.ring = IndeterminateProgressRing()
        self.ring.setFixedSize(195, 195)
        self.ring.hide()
        top_layout.addWidget(self.ring)
        top_layout.addWidget(self.img_label)
        self.title_label = SubtitleLabel()
        self.artists_label = QLabel()
        topright_layout.addWidget(
            self.title_label, alignment=ali.AlignLeft | ali.AlignTop
        )
        topright_layout.addWidget(
            self.artists_label, alignment=ali.AlignLeft | ali.AlignTop
        )
        top_layout.addWidget(topright_widget)

        contents_layout.addLayout(top_layout)

        middle_layout = QVBoxLayout()
        self.lyric_label = SubtitleLabel("null")
        self.transl_label = QLabel("null")
        self.lyric_label.setWordWrap(True)
        self.transl_label.setWordWrap(True)
        
        middle_layout.addWidget(
            self.lyric_label, alignment=ali.AlignHCenter | ali.AlignBottom
        )
        middle_layout.addWidget(
            self.transl_label, alignment=ali.AlignHCenter | ali.AlignTop
        )
        contents_layout.addLayout(middle_layout)

        self.controller.setFixedWidth(self.width())

        global_layout.addLayout(contents_layout)

        self.expanded_widget = QWidget()
        expanded_layout = QVBoxLayout()
        self.pivot = Pivot(self)
        self.stacked_widget = QStackedWidget(self)

        self.expanded_widget.setFixedWidth(500)

        expanded_layout.addWidget(self.pivot)
        expanded_layout.addWidget(self.stacked_widget)
        expanded_layout.setContentsMargins(30, 0, 30, 30)

        self.lst_interface = QWidget()
        self.lst_layout = QVBoxLayout()
        self.lst = ListWidget()
        self.lst.setFixedWidth(500)
        self.lst.entered.connect(lambda: self.__setattr__("lst_shoud_set", False))
        self.lst.leaveEvent = lambda e: self.__setattr__("lst_shoud_set", True)
        self.lst.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.lst_layout.addWidget(self.lst)

        btn_layout = QHBoxLayout()
        self.delete_btn = TransparentPushButton(FluentIcon.DELETE, "Remove")
        self.delete_btn.clicked.connect(self.removeSong)
        self.add_btn = TransparentPushButton(getQIcon("pl"), "Add")
        self.add_btn.clicked.connect(self.addSong)
        self.insert_btn = TransparentPushButton(getQIcon("insert"), "Insert")
        self.insert_btn.clicked.connect(self.insertSong)
        self.removeall_btn = TransparentPushButton(getQIcon("clearall"), "Remove All")
        self.removeall_btn.clicked.connect(self.removeAllSongs)
        btn_layout.addWidget(self.delete_btn)
        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.insert_btn)
        btn_layout.addWidget(self.removeall_btn)
        self.lst_layout.addLayout(btn_layout)

        self.lst_interface.setLayout(self.lst_layout)

        self.playing_scrollarea = SmoothScrollArea()

        self.options_interface = QWidget()
        self.options_interface.setStyleSheet(
            f"background: #{'000000' if darkdetect.isDark() else 'FFFFFF'}"
        )
        self.playing_layout = QGridLayout()

        self.addSeparateWidget(TitleLabel("Playing"))

        self.play_method_box = ComboBox()
        self.play_method_box.addItems(
            ["Repeat one", "Repeat list", "Shuffle", "Play in order"]
        )
        self.play_method_box.setCurrentText("Repeat list")
        self.addSetting("Play order", "the order of play", self.play_method_box)

        self.addCheckSetting("Enable Stereo", "enable stereo effect", "stereo")

        self.addCheckSetting(
            "Smart Skip", "Skip the no sound section when song ends", "skip_nosound"
        )

        self.addNumberSetting(
            "Skip Threshold", "the threshold of the skip", -100, 0, 1, "skip_threshold"
        )
        self.now_volume = QLabel(f"Current volume(db): {0}")
        self.addSeparateWidget(self.now_volume)

        self.addNumberSetting(
            "Remain time to Skip",
            "start detecting volume during the remaining specified seconds",
            1,
            60,
            1,
            "skip_remain_time",
        )

        self.addSeparateWidget(TitleLabel("FFT"))

        self.enableFFT_box = CheckBox("Enable Frequency Graphics")
        self.addSeparateWidget(self.enableFFT_box)
        self.enableFFT_box.setChecked(cfg.enable_fft)

        self.addNumberSetting(
            "FFT Filtering Window size",
            "larger value means more smoothing",
            1,
            200,
            1,
            "fft_filtering_windowsize",
        )

        self.addNumberSetting(
            "FFT Smoothing Factor",
            "larger value means a more sudden change",
            0.01,
            1.0,
            0.05,
            "fft_factor",
        )

        self.addNumberSetting(
            "SouthsideMusic side FFT Multiple Factor",
            "larger value means more intense changing(only on SouthsideMusic side)",
            0,
            15.0,
            0.05,
            "cfft_multiple",
        )

        self.addNumberSetting(
            "SouthsideClient side FFT Multiple Factor",
            "larger value means more intense changing(only on SouthsideClient side)",
            00,
            15.0,
            0.05,
            "sfft_multiple",
        )

        self.addSeparateWidget(TitleLabel("Loudness Balance"))

        self.target_lufs = Slider(Qt.Orientation.Horizontal)
        self.target_lufs.setRange(-60, 0)
        self.target_lufs.setSingleStep(1)
        self.target_lufs.valueChanged.connect(self.onTargetLUFSChanged)
        self.target_lufs.setValue(cfg.target_lufs)
        self.addSeparateWidget(self.target_lufs)
        self.target_lufs_label = SubtitleLabel(f"Target LUFS: {cfg.target_lufs}")
        self.addSeparateWidget(self.target_lufs_label)
        self.addSeparateWidget(
            QLabel(
                "Target LUFS Help:\nRange: -60(quietest)~0(loudest)\nRecommend: -16~-18"
                "\nReference:\nYoutube > -14LUFS\nNetflix > -27LUFS\nTikTok / Instagram Reels > -13LUFS\nApple Music (Video) > -16LUFS"
                "\nSpotify (Video): -14LUFS / -16LUFS"
            )
        )

        self.addSeparateWidget(QLabel())
        self.addSeparateWidget(TitleLabel("SouthsideClient Connection"))
        self.southsideclient_status_label = SubtitleLabel(
            "Connection Status: <span style='color: red;'>Disconnected</span>"
        )
        self.addSeparateWidget(self.southsideclient_status_label)
        self.disconnect_btn = TransparentPushButton(getQIcon("disc"), "Disconnect")
        self.disconnect_btn.clicked.connect(self.disconnectFromSouthsideClient)
        self.disconnect_btn.setEnabled(False)
        self.addSeparateWidget(self.disconnect_btn)
        self.connect_btn = TransparentPushButton(getQIcon("cnnt"), "Try connect")
        self.connect_btn.clicked.connect(self.connectToSouthsideClient)
        self.connect_btn.setEnabled(False)
        self.addSeparateWidget(self.connect_btn)

        self.song_randomer = AdvancedRandom()
        self.song_randomer.init(self.playlist)

        self.options_interface.setLayout(self.playing_layout)
        self.playing_scrollarea.setWidget(self.options_interface)
        self.playing_scrollarea.setWidgetResizable(True)

        self.addSubInterface(self.lst_interface, "playlist_listwidget", "Playlist")
        self.addSubInterface(self.playing_scrollarea, "options_interface", "Options")

        self.stacked_widget.setCurrentWidget(self.lst)
        self.pivot.setCurrentItem("playlist_listwidget")
        self.pivot.currentItemChanged.connect(
            lambda k: self.stacked_widget.setCurrentWidget(
                mwindow.findChild(QWidget, k)  # type: ignore
            )
        )  # type: ignore

        self.expanded_widget.setLayout(expanded_layout)

        self.expanded_widget.hide()

        self.setLayout(global_layout)

        self.imageLoaded.connect(self.onImageLoaded)

        self.controller.playLastSignal.connect(self.playLast)
        self.controller.playNextSignal.connect(lambda: self.playNext(True))

        self.lufs_changed_timer = QTimer(self)
        self.lufs_changed_timer.timeout.connect(self.applyNewLUFS)

        for slider in self.options_interface.findChildren(QSlider):
            slider.wheelEvent = lambda e: e.ignore()  # 防止滚动时触发

    def addNumberSetting(
        self,
        title: str,
        description: str,
        min: float | int,
        max: float | int,
        step: float | int,
        configurationName: str,
    ) -> None:
        box = DoubleSpinBox()
        box.setRange(min, max)  # pyright: ignore[reportArgumentType]
        box.setValue(getattr(cfg, configurationName))
        box.setSingleStep(step)  # pyright: ignore[reportArgumentType]

        def _valueChanged(value: float | int):
            setattr(cfg, configurationName, value)

        box.valueChanged.connect(_valueChanged)
        
        self.addSetting(title, description, box)

    def addCheckSetting(
        self, title: str, description: str, configurationName: str
    ) -> None:
        box = CheckBox(title)

        def __valueChanged():
            setattr(cfg, configurationName, box.checkState() == Qt.CheckState.Checked)

        box.stateChanged.connect(__valueChanged)
        box.setChecked(getattr(cfg, configurationName))

        self.addSetting(title, description, box)

    def onNosoundSkipChanged(self, state: Qt.CheckState):
        checked = state == Qt.CheckState.Checked
        cfg.skip_nosound = checked

    def onEndingNoSound(self):
        if not cfg.skip_nosound:
            return
        self.controller.onSongFinish.emit()

    def disconnectFromSouthsideClient(self):
        ws_server.tryGetHandler()
        ws_server.stop()
        ws_server.join()
        ws_handler.onDisconnected.emit()

        self.disconnect_btn.setEnabled(False)
        self.connect_btn.setEnabled(True)

    def connectToSouthsideClient(self):
        ws_server = WebSocketServer(port=15489)
        ws_server.start()

        self.connect_btn.setEnabled(False)

    def removeSong(self) -> None:
        selected = get_value_bylist(
            mwindow,
            "Remove Song",
            "select a song to remove",
            [song.name for song in self.playlist],
        )

        if not selected:
            return

        for i, storable in enumerate(self.playlist):
            if storable.name == selected:
                self.playlist.remove(self.playlist[i])

                InfoBar.success(
                    "Removed",
                    f"{selected} has been removed",
                    duration=1500,
                    parent=mwindow,
                )
                break

        self.refreshPlaylistWidget()

        if self.cur:
            if selected == self.cur.storable.name:
                self.playSongAtIndex(self.current_index)

    def addSong(self) -> None:
        selected = getFavoriteSong(mwindow, favs)

        if not selected:
            return

        self.playlist.append(selected)
        InfoBar.success(
            "Added", f"Added {selected.name} to playlist", duration=1500, parent=mwindow
        )

        self.refreshPlaylistWidget()

        self.preloadNextSong()

    def insertSong(self) -> None:
        selected = getFavoriteSong(mwindow, favs)

        if not selected:
            return

        self.playlist.insert(self.current_index + 1, selected)
        InfoBar.success(
            "Inserted",
            f"Inserted {selected.name} to next song",
            duration=1500,
            parent=mwindow,
        )

        self.refreshPlaylistWidget()

        self.preloadNextSong()

    def removeAllSongs(self) -> None:
        self.playlist.clear()
        if isinstance(self.cur, DummyCard) and isinstance(
            self.cur.storable, SongStorable
        ):
            self.playlist.append(self.cur.storable)

        self.refreshPlaylistWidget()

        InfoBar.success("Inserted", "Removed all songs", duration=1500, parent=mwindow)

    def addSongCardToList(self, song: SongStorable) -> QListWidgetItem:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, song)
        item.setSizeHint(QSize(0, 62))
        card = PlaylistSongCard(song)
        card.clicked.connect(lambda s, it=item: self.onPlaylistCardClicked(s, it))
        self.lst.addItem(item)
        self.lst.setItemWidget(item, card)
        return item

    def refreshPlaylistWidget(self):
        self.lst.clear()

        for song in self.playlist:
            self.addSongCardToList(song)

    def applyNewLUFS(self):
        self.lufs_changed_timer.stop()
        self.target_lufs.hide()

        self.target_lufs_label.setText("Reapplying")

        def _apply():
            if not isinstance(self.cur, DummyCard):
                return
            if not hasattr(self.cur, "storable"):
                return

            audio: AudioSegment = AudioSegment.from_file(
                io.BytesIO(self.cur.storable.get_music_bytes())
            )
            logging.debug("new lufs -> applying gain")
            gain = getAdjustedGainFactor(cfg.target_lufs, audio)
            self.cur.storable.loudness_gain = gain
            self.cur.storable.target_lufs = cfg.target_lufs

            p = player.getPosition()
            playingnow = player.isPlaying()
            self.playStorable(self.cur.storable)
            if playingnow:
                player.play()

            self.target_lufs_label.setText(f"Target LUFS: {cfg.target_lufs}")
            player.setPosition(p)

            QTimer.singleShot(250, self.preloadNextSong)
            self.target_lufs.show()

        threading.Thread(target=_apply).start()

    def onTargetLUFSChanged(self, value: int):
        cfg.target_lufs = value
        if hasattr(self, "target_lufs_label"):
            self.target_lufs_label.setText(f"Target LUFS: {value}")
            self.lufs_changed_timer.start(1000)

    @staticmethod
    def patchedPaintEvent(card: CardWidget, e):
        painter = QPainter(card)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing)

        w, h = card.width(), card.height()
        r = card.getBorderRadius()
        d = 2 * r

        isDark = isDarkTheme()

        # draw top border
        path = QPainterPath()
        # path.moveTo(1, h - r)
        path.arcMoveTo(1, h - d - 1, d, d, 240)
        path.arcTo(1, h - d - 1, d, d, 225, -60)
        path.lineTo(1, r)
        path.arcTo(1, 1, d, d, -180, -90)
        path.lineTo(w - r, 1)
        path.arcTo(w - d - 1, 1, d, d, 90, -90)
        path.lineTo(w - 1, h - r)
        path.arcTo(w - d - 1, h - d - 1, d, d, 0, -60)

        topBorderColor = QColor(0, 0, 0, 0)
        if isDark:
            topBorderColor = QColor(255, 255, 255, 11)
            if card.isPressed:
                topBorderColor = QColor(255, 255, 255, 34)
            elif card.isHover:
                topBorderColor = QColor(255, 255, 255, 30)
        else:
            topBorderColor = QColor(0, 0, 0, 28)

        painter.strokePath(path, topBorderColor)

        # draw bottom border
        path = QPainterPath()
        path.arcMoveTo(1, h - d - 1, d, d, 240)
        path.arcTo(1, h - d - 1, d, d, 240, 30)
        path.lineTo(w - r - 1, h - 1)
        path.arcTo(w - d - 1, h - d - 1, d, d, 270, 30)

        bottomBorderColor = topBorderColor
        if not isDark and card.isHover and not card.isPressed:
            bottomBorderColor = QColor(0, 0, 0, 27)

        painter.strokePath(path, bottomBorderColor)

        # draw background
        painter.setPen(Qt.PenStyle.NoPen)
        rect = card.rect().adjusted(1, 1, -1, -1)
        painter.setBrush(card.backgroundColor)
        painter.drawRoundedRect(rect, r, r)

    def addSetting(self, name: str, description: str, widget: QWidget) -> None:
        card = CardWidget()
        card.paintEvent = lambda e: self.patchedPaintEvent(card, e)
        card.setBackgroundColor(QColor(255, 255, 255, 0))
        card._normalBackgroundColor = lambda: QColor(255, 255, 255, 0)
        card._hoverBackgroundColor = lambda: QColor(255, 255, 255, 0)
        card._pressedBackgroundColor = lambda: QColor(255, 255, 255, 0)
        card._focusInBackgroundColor = lambda: QColor(255, 255, 255, 0)
        global_layout = QVBoxLayout()
        top_layout = QHBoxLayout()
        name_l = QLabel(name)
        name_l.setStyleSheet("font-weight: bold;")
        name_l.setWordWrap(True)
        top_layout.addWidget(name_l)
        top_layout.addWidget(widget)
        global_layout.addLayout(top_layout)
        desc_l = QLabel(description)
        desc_l.setWordWrap(True)
        global_layout.addWidget(desc_l)
        card.setLayout(global_layout)
        self.playing_layout.addWidget(card, self.playing_layout.rowCount(), 0, 2, 2)

    def addSeparateWidget(self, widget: QWidget) -> None:
        self.playing_layout.addWidget(widget, self.playing_layout.rowCount(), 0, 1, 2)

    def addSubInterface(self, widget: QWidget, objectName, text):
        widget.setObjectName(objectName)
        self.stacked_widget.addWidget(widget)
        self.pivot.addItem(routeKey=objectName, text=text)

    def onPlaylistCardClicked(self, storable: SongStorable, item: QListWidgetItem):
        self.current_index = self.playlist.index(storable)
        self.playSongAtIndex(self.current_index)

    def init(self):
        if self.cur is None:
            return

        for label in self.findChildren(QLabel):
            label.setWordWrap(True)

        self.title_label.setText(self.cur.info["name"])
        self.artists_label.setText(self.cur.info["artists"])

        # Check if cur has storable attribute (DummyCard from playlist)
        if hasattr(self.cur, "storable"):
            # Use local data from storable
            image_bytes = self.cur.storable.get_image_bytes()
            self.onImageLoaded(image_bytes)
            if self.cur.storable.target_lufs == cfg.target_lufs:
                self.loadMusicFromBytes(
                    self.cur.storable.get_music_bytes(), self.cur.storable.loudness_gain
                )
            else:
                self.applyNewLUFS()

            # Use local lyrics if available
            if hasattr(self.cur.storable, "lyric") and self.cur.storable.lyric:
                mgr.cur = self.cur.storable.lyric

                if (
                    hasattr(self.cur.storable, "translated_lyric")
                    and self.cur.storable.translated_lyric
                ):
                    transmgr.cur = self.cur.storable.translated_lyric
                else:
                    transmgr.cur = "[00:00.000]"

                def _parse_local():
                    mgr.parse()
                    transmgr.parse()

                doWithMultiThreading(_parse_local, (), mwindow, "Parsing...")
            else:
                # Fallback to network download
                self.downloadLyric()

            mwindow.switchTo(dp)
        else:
            # Original network download
            if player.isPlaying():
                player.stop()

            def _do():
                img_bytes = requests.get(
                    self.cur.detail["image_url"],  # type: ignore
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    },
                ).content
                self.imageLoaded.emit(img_bytes)

            self.img_label.hide()
            self.ring.show()
            doWithMultiThreading(_do, (), mwindow, "Loading...")

    def preloadNextSong(self):
        if len(dp.playlist) <= 1:
            return
        if dp.current_index >= len(dp.playlist) - 1:
            return

        try:
            logging.info("preloading")

            next_song = self.playlist[self.current_index + 1]

            logging.debug(next_song)

            if self.play_method_box.currentText() == "Play in order":
                if self.current_index + 1 >= len(self.playlist):
                    return
            elif self.play_method_box.currentText() == "Repeat list":
                if self.current_index + 1 >= len(self.playlist):
                    next_song = self.playlist[0]
                else:
                    next_song = self.playlist[self.current_index + 1]
            else:
                next_song = self.playlist[self.current_index + 1]
            if not (
                self.play_method_box.currentText() in ["Play in order", "Repeat list"]
            ):
                return

            def _preload():
                with lock:
                    song_bytes = next_song.get_music_bytes()
                    self.next_song_audio = AudioSegment.from_file(
                        io.BytesIO(song_bytes)
                    )

                if (
                    next_song.loudness_gain == 1.0
                    or next_song.target_lufs != cfg.target_lufs
                ) and isinstance(self.next_song_audio, AudioSegment):
                    next_song.loudness_gain = getAdjustedGainFactor(
                        cfg.target_lufs, self.next_song_audio
                    )
                    next_song.target_lufs = cfg.target_lufs
                self.next_song_gain = next_song.loudness_gain

                if isinstance(self.next_song_audio, AudioSegment):
                    logging.debug(
                        f"preload -> applying gain {self.next_song_gain} {cfg.target_lufs=}"
                    )
                    self.next_song_audio = self.next_song_audio.apply_gain(
                        20 * np.log10(self.next_song_gain)
                    )

                logging.info("preloaded")
                logging.debug(
                    f"(Types) {type(self.next_song_audio)=} {type(self.next_song_gain)=}"
                )

            threading.Thread(target=_preload, daemon=True).start()
        finally:
            logging.debug("started preload thread")

    def downloadLyric(self):
        assert self.cur is not None

        def _parse():
            with ncm.GetCurrentSession():
                data: dict = apis.track.GetTrackLyricsNew(str(self.cur.info["id"]))  # type: ignore
            mgr.cur = data["lrc"]["lyric"]
            if data.get("tlyric"):
                transmgr.cur = "\n".join(data["tlyric"]["lyric"].splitlines()[1:])
            else:
                transmgr.cur = "[00:00.000]"

            def _real():
                mgr.parse()
                transmgr.parse()

            def _fini():
                player.play()

                self.sendSongFMAndInfo()

            doWithMultiThreading(
                _real, (), mwindow, "Parsing...", finished=_fini, dialog=False
            )

        doWithMultiThreading(_parse, (), mwindow, "Loading...", dialog=False)

    def downloadMusic(self):
        assert self.cur is not None

        def _downloaded(bytes: bytes):
            if player.isPlaying():
                player.stop()

            with lock:
                audio = AudioSegment.from_file(io.BytesIO(bytes))

            player.load(audio)
            self.total_length = player.getLength()
            player.play()

            def computeGain():
                try:
                    gain = getAdjustedGainFactor(cfg.target_lufs, audio)
                    if self.cur:
                        self._gain_cache[self.cur.info["id"]] = gain
                    player.setGain(gain)
                except Exception as e:
                    pass

            threading.Thread(target=computeGain, daemon=True).start()

            self.downloadLyric()

        music_url = apis.track.GetTrackAudio(
            str(self.cur.info["id"]), # type: ignore
            bitrate=3200 * 1000,  # type: ignore
        )  # type: ignore
        logging.debug(f"{music_url['data'][0]['url']=}")  # type: ignore
        downloadWithMultiThreading(
            music_url["data"][0]["url"],  # type: ignore
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            },
            None,
            mwindow,
            "Loading...",
            _downloaded,
        )

    def onImageLoaded(self, bytes):
        pixmap = QPixmap()
        pixmap.loadFromData(bytes)
        if not pixmap.isNull():
            scaled_pixmap = pixmap.scaled(
                self.img_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.img_label.setPixmap(scaled_pixmap)

            self.img_label.show()
            self.ring.hide()

        if not hasattr(self.cur, "storable"):
            self.downloadMusic()

        self.sendSongFMAndInfo()

    def onPlayButtonClicked(self):
        # If no song is currently loaded, start playlist
        if self.cur is None:
            self.startPlaylist()

    def playNext(self, byuser: bool):
        self.sendSongFMAndInfo()
        logging.debug(
            f"(Types) {type(self.next_song_audio)=} {type(self.next_song_gain)=}"
        )
        if (
            isinstance(self.next_song_audio, AudioSegment)
            and isinstance(self.next_song_gain, float)
            and (dp.play_method_box.currentText() in ["Play in order", "Repeat list"])
        ):
            self.playPreloadedSong()
            self.current_index += 1
            return

        if self.current_index < 0 or self.current_index >= len(self.playlist) - 1:
            if dp.play_method_box.currentText() == "Play in order":
                # No next song, reset and pause
                InfoBar.warning(
                    "Warning",
                    "This song is the last song in the playlist.",
                    parent=mwindow,
                )
                self.controller.setPlaytime(0)
                return
            elif dp.play_method_box.currentText() == "Repeat list":
                self.current_index = 0
                self.playSongAtIndex(self.current_index)
                return

        if dp.play_method_box.currentText() == "Repeat one" and not byuser:
            self.playSongAtIndex(self.current_index)
            return
        elif dp.play_method_box.currentText() == "Shuffle":
            start_storable: SongStorable = self.playlist[self.current_index]
            cur_storable: SongStorable = self.playlist[self.current_index]
            while self.current_index == self.playlist.index(start_storable):
                cur_storable = self.song_randomer.random()
                self.current_index = self.playlist.index(cur_storable)
            self.playSongAtIndex(self.current_index)
            return

        self.current_index += 1
        self.playSongAtIndex(self.current_index)

    def playPreloadedSong(self) -> None:
        if (not isinstance(self.next_song_audio, AudioSegment)) or (
            not isinstance(self.next_song_gain, float)
        ):
            logging.error(
                f"cant play preloaded song: (Types) {type(self.next_song_audio)=} {type(self.next_song_gain)=}"
            )
            return

        logging.info("using preloaded song")

        song_storable = self.playlist[self.current_index + 1]
        self.cur = DummyCard(song_storable)

        # Update UI
        self.title_label.setText(song_storable.name)
        self.artists_label.setText(song_storable.artists)
        self.lyric_label.setText("Loading...")
        self.transl_label.setText("")
        QApplication.processEvents()

        image_bytes = song_storable.get_image_bytes()
        self.onImageLoaded(image_bytes)

        audio = self.next_song_audio

        player.load(audio)
        self.total_length = player.getLength()

        mgr.cur = song_storable.lyric
        if song_storable.translated_lyric:
            transmgr.cur = song_storable.translated_lyric
        else:
            transmgr.cur = "[00:00.000]"
        try:
            mgr.parse()
            transmgr.parse()
        finally:
            if not player.isPlaying():
                player.play()

            self._preload_triggered = False
            self.next_song_audio = None
            self.next_song_gain = None

    def playLast(self):
        if self.current_index < 1 or self.current_index >= len(self.playlist):
            # No last song, reset and pause
            InfoBar.warning(
                "Warning",
                "This song is the first song in the playlist.",
                parent=mwindow,
            )
            self.controller.setPlaytime(0)
            return

        self._preload_triggered = False
        self.next_song_audio = None
        self.next_song_gain = None

        self.current_index -= 1
        self.playSongAtIndex(self.current_index)

    def playSongAtIndex(self, index: int):
        if index < 0 or index >= len(self.playlist):
            return

        self.current_index = index
        song = self.playlist[index]
        self.playStorable(song)

    def playStorable(self, song_storable: SongStorable):
        player.stop()
        self.cur = DummyCard(song_storable)

        # Update UI
        self.title_label.setText(song_storable.name)
        self.artists_label.setText(song_storable.artists)
        self.lyric_label.setText("Loading...")
        self.transl_label.setText("")
        QApplication.processEvents()

        image_bytes = song_storable.get_image_bytes()
        self.onImageLoaded(image_bytes)

        # Load from cache/base64-backed bytes
        if song_storable.target_lufs == cfg.target_lufs:
            self.loadMusicFromBytes(
                song_storable.get_music_bytes(), song_storable.loudness_gain
            )
        else:
            music_bytes = song_storable.get_music_bytes()
            logging.debug(f"loading data {len(music_bytes)}")
            with lock:
                audio = AudioSegment.from_file(io.BytesIO(music_bytes))

            song_storable.target_lufs = cfg.target_lufs
            song_storable.loudness_gain = getAdjustedGainFactor(cfg.target_lufs, audio)
            gain = song_storable.loudness_gain

            logging.debug(f"applying gain {gain} {cfg.target_lufs=}")
            audio = audio.apply_gain(20 * np.log10(gain))

            player.load(audio)
            self.total_length = player.getLength()

        mgr.cur = song_storable.lyric
        if song_storable.translated_lyric:
            transmgr.cur = song_storable.translated_lyric
        else:
            transmgr.cur = "[00:00.000]"
        try:
            mgr.parse()
            transmgr.parse()
        finally:
            if not player.isPlaying():
                player.play()

            self._preload_triggered = False
            self.next_song_audio = None
            self.next_song_gain = None

        self.sendSongFMAndInfo()

    def loadMusicFromBase64(self, content_base64: str, gain: float):
        music_bytes = base64.b64decode(content_base64)
        self.loadMusicFromBytes(music_bytes, gain)

    def loadMusicFromBytes(self, music_bytes: bytes, gain: float):
        logging.debug(f"loading data {len(music_bytes)}")
        with lock:
            audio = AudioSegment.from_file(io.BytesIO(music_bytes))

        logging.debug(f"applying gain {gain} {cfg.target_lufs=}")
        audio = audio.apply_gain(20 * np.log10(gain))

        player.load(audio)
        self.total_length = player.getLength()

    def startPlaylist(self):
        fp.folder_selector.setCurrentRow(0)
        fp.addFolderToPlaylist()

        # Start playing first song
        self.current_index = 0
        self.playSongAtIndex(0)

        # Ensure playing state
        if not player.isPlaying():
            player.play()

    def sendSongFMAndInfo(self):
        if self.cur is None:
            return
        if not isinstance(self.cur, DummyCard):
            return

        pixmap = self.img_label.pixmap().scaled(
            self.img_label.pixmap().size(), Qt.AspectRatioMode.KeepAspectRatio
        )

        from PySide6.QtCore import QBuffer, QIODevice

        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buffer, "PNG")
        img_bytes = buffer.data().data()
        buffer.close()

        img_base64 = base64.b64encode(img_bytes).decode()

        ws_handler.send(
            json.dumps(
                {
                    "option": "fm",
                    "image": img_base64,
                    "song_name": self.cur.storable.name,
                    "artists": self.cur.storable.artists,
                }
            )
        )


class FavoritesPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("favorites_page")

        global_layout = QVBoxLayout(self)

        top_layout = FlowLayout()
        top_layout.addWidget(TitleLabel("Favorites"))
        self.refresh_btn = PrimaryPushButton(FluentIcon.SYNC, "Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        top_layout.addWidget(self.refresh_btn)
        self.newfolder_btn = PushButton(FluentIcon.ADD, "New Folder")
        self.newfolder_btn.clicked.connect(self.newFolder)
        top_layout.addWidget(self.newfolder_btn)
        self.deletefolder_btn = PushButton(FluentIcon.DELETE, "Delete Folder")
        self.deletefolder_btn.clicked.connect(self.deleteFolder)
        top_layout.addWidget(self.deletefolder_btn)
        self.renamefolder_btn = PushButton(FluentIcon.EDIT, "Rename Folder")
        self.renamefolder_btn.clicked.connect(self.renameFolder)
        top_layout.addWidget(self.renamefolder_btn)
        global_layout.addLayout(top_layout)
        bottom_layout = QHBoxLayout()

        # Left side: folder selector and add to playlist button
        left_layout = QVBoxLayout()
        self.folder_selector = ListWidget()
        self.folder_selector.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.folder_selector.itemClicked.connect(self.viewSongs)
        left_layout.addWidget(self.folder_selector, 1)
        self.addplaylist_btn = PushButton(
            getQIcon("pl"), "Add selected folder to playlist"
        )
        self.addplaylist_btn.clicked.connect(self.addFolderToPlaylist)
        left_layout.addWidget(self.addplaylist_btn)
        self.addall_btn = PrimaryPushButton(
            getQIcon("pl", "light"), "Add all folder to playlist"
        )
        self.addall_btn.clicked.connect(self.addAllToPlaylist)
        left_layout.addWidget(self.addall_btn)
        bottom_layout.addLayout(left_layout, 3)

        # Separator
        bottom_layout.addWidget(QLabel(">"), alignment=Qt.AlignmentFlag.AlignVCenter)

        # Right side: song viewer and delete song button
        right_layout = QVBoxLayout()
        self.song_viewer = ListWidget()
        self.song_viewer.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        right_layout.addWidget(self.song_viewer, 1)
        self.deletesong_btn = PushButton(FluentIcon.DELETE, "Delete Song")
        self.deletesong_btn.clicked.connect(self.deleteSong)
        right_layout.addWidget(self.deletesong_btn)
        bottom_layout.addLayout(right_layout, 7)

        global_layout.addLayout(bottom_layout)

        self.setLayout(global_layout)

    def renameFolder(self):
        got = get_text_lineedit(
            "Rename Folder",
            "Enter new folder name:",
            self.folder_selector.selectedItems()[0].text(),
            mwindow,
        )

        if got:
            global favs

            for i, folder in enumerate(favs):
                if (
                    folder["folder_name"]
                    == self.folder_selector.selectedItems()[0].text()
                ):
                    favs[i]["folder_name"] = got
                    break
            saveFavorites(favs)

            self.refresh()

    def viewSongs(self, i: QListWidgetItem):
        global favs
        self.song_viewer.clear()
        for f in favs:
            if i.text() == f["folder_name"]:
                for song in f["songs"]:
                    item = QListWidgetItem()
                    item.setData(Qt.ItemDataRole.UserRole, song)
                    item.setSizeHint(QSize(0, 62))
                    card = FavoriteSongCard(song)
                    card.clicked.connect(
                        lambda s, it=item: self.song_viewer.setCurrentItem(it)
                    )
                    self.song_viewer.addItem(item)
                    self.song_viewer.setItemWidget(item, card)

    def newFolder(self):
        from utils.base.base_util import FolderInfo

        global favs

        name, ok = QInputDialog.getText(mwindow, "New Folder", "Enter folder name:")
        if ok and name:
            if not name.strip():
                InfoBar.warning(
                    "Invalid name", "Folder name cannot be empty", parent=mwindow
                )
                return
            # Check duplicate
            for folder in favs:
                if folder["folder_name"] == name:
                    InfoBar.warning(
                        "Duplicate", "Folder already exists", parent=mwindow
                    )
                    return
            favs.append(FolderInfo(folder_name=name, songs=[]))
            saveFavorites(favs)
            self.refresh()
            InfoBar.success("Folder created", f"Folder {name} created", parent=mwindow)

    def deleteFolder(self):
        global favs
        selected = self.folder_selector.currentItem()
        if not selected:
            InfoBar.warning(
                "No selection", "Please select a folder to delete", parent=mwindow
            )
            return

        folder_name = selected.text()
        reply = QMessageBox.question(
            mwindow,
            "Confirm Delete",
            f"Are you sure you want to delete folder {folder_name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            # Remove folder
            favs = [f for f in favs if f["folder_name"] != folder_name]
            saveFavorites(favs)
            self.refresh()
            InfoBar.success(
                "Folder deleted", f"Folder {folder_name} deleted", parent=mwindow
            )

    def deleteSong(self):
        global favs
        selected_folder = self.folder_selector.currentItem()
        if not selected_folder:
            InfoBar.warning(
                "No folder selected", "Please select a folder first", parent=mwindow
            )
            return
        selected_song = self.song_viewer.currentItem()
        if not selected_song:
            InfoBar.warning(
                "No song selected", "Please select a song to delete", parent=mwindow
            )
            return

        folder_name = selected_folder.text()
        song_storable: SongStorable = selected_song.data(Qt.ItemDataRole.UserRole)
        song_name = song_storable.name

        reply = QMessageBox.question(
            mwindow,
            "Confirm Delete",
            f"Are you sure you want to delete song {song_name} from folder {folder_name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            # Find folder and remove song
            for folder in favs:
                if folder["folder_name"] == folder_name:
                    folder["songs"] = [
                        s for s in folder["songs"] if s.name != song_name
                    ]
                    break
            saveFavorites(favs)
            self.viewSongs(selected_folder)  # refresh song view
            InfoBar.success("Song deleted", f"Song {song_name} deleted", parent=mwindow)
        if reply == QMessageBox.StandardButton.Yes:
            # Find folder and remove song
            for folder in favs:
                if folder["folder_name"] == folder_name:
                    folder["songs"] = [
                        s for s in folder["songs"] if s.name != song_name
                    ]
                    break
            saveFavorites(favs)
            self.viewSongs(selected_folder)  # refresh song view
            InfoBar.success("Song deleted", f"Song {song_name} deleted", parent=mwindow)

    def addFolderToPlaylist(self):
        global favs
        selected_folder = self.folder_selector.currentItem()
        if not selected_folder:
            InfoBar.warning(
                "No folder selected", "Please select a folder first", parent=mwindow
            )
            return

        folder_name = selected_folder.text()

        # Find folder
        target_folder = None
        for folder in favs:
            if folder["folder_name"] == folder_name:
                target_folder = folder
                break

        if not target_folder or not target_folder["songs"]:
            InfoBar.warning(
                "Empty folder", f"Folder {folder_name} is empty", parent=mwindow
            )
            return

        if not dp:
            InfoBar.error(
                "Playlist not available",
                "Playlist page not initialized",
                parent=mwindow,
            )
            return

        added_count = 0
        for song in target_folder["songs"]:
            # Check for duplicates
            if not any(s.name == song.name for s in dp.playlist):
                dp.playlist.append(song)
                dp.addSongCardToList(song)
                added_count += 1

        if added_count > 0:
            InfoBar.success(
                "Songs added",
                f"Added {added_count} songs from folder {folder_name} to playlist",
                parent=mwindow,
            )
        else:
            InfoBar.info(
                "No new songs",
                f"All songs from folder {folder_name} already in playlist",
                parent=mwindow,
            )

        dp.song_randomer.init(dp.playlist)

    def addAllToPlaylist(self):
        global favs

        for folder in favs:
            for song in folder["songs"]:
                if not any(s.name == song.name for s in dp.playlist):
                    dp.playlist.append(song)
                    dp.addSongCardToList(song)
        InfoBar.success(
            "Songs added",
            "Added all songs from favorites to playlist",
            parent=mwindow,
        )

        dp.song_randomer.init(dp.playlist)

    def refresh(self):
        global favs
        favs = loadFavorites()

        self.folder_selector.clear()
        self.song_viewer.clear()

        for folder in favs:
            self.folder_selector.addItem(folder["folder_name"])


class DynamicIslandPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("dynamic_island_page")

        self.island_x = cfg.island_x
        self.island_y = cfg.island_y

        self.show_island = cfg.island_checked

        island.setVisible(self.show_island)

        global_layout = QVBoxLayout()
        global_layout.addWidget(TitleLabel("Lyric Island"))

        self.island_check = CheckBox("Enable Lyric Island")
        self.island_check.checkStateChanged.connect(
            lambda state: self.__setattr__(
                "show_island", state == Qt.CheckState.Checked
            )
        )
        global_layout.addWidget(self.island_check)

        self.edit_btn = PrimaryPushButton(FluentIcon.EDIT, "Edit island position")
        self.edit_btn.clicked.connect(self.editIslandPosition)
        global_layout.addWidget(self.edit_btn)

        self.setLayout(global_layout)

    def editIslandPosition(self):
        if not self.show_island:
            InfoBar.warning(
                "Lyric Island",
                "enable the lyric island first!",
                parent=mwindow,
                duration=2000,
            )
            return

        mwindow.hide()
        island_editing_overlay.show()


class SessionPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("session_page")

        self.nickname = TitleLabel()
        self.avatar = QLabel()
        global_layout = QVBoxLayout()

        user_layout = QHBoxLayout()

        user_layout.addWidget(
            self.avatar,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        user_layout.addWidget(
            self.nickname,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )

        self.avatar.setFixedSize(self.nickname.height() - 3, self.nickname.height() - 3)

        global_layout.addLayout(user_layout)

        bottom_layout = QHBoxLayout()

        self.vip = SubtitleLabel("VIP Level: Loading...")
        bottom_layout.addWidget(
            self.vip, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )

        self.login_btn = PrimaryPushButton(getQIcon("login", "light"), "Login")
        self.login_btn.clicked.connect(self.login)

        bottom_layout.addWidget(
            self.login_btn,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
        )

        global_layout.addLayout(bottom_layout)

        self.setLayout(global_layout)

        self.refreshInformations()

    def refreshInformations(self):
        if os.path.exists("images/avatar.png"):
            os.remove("images/avatar.png")

        try:
            session = ncm.GetCurrentSession()
        except Exception as e:
            logging.warning(f"Failed to get session: {e}")
            session = None

        try:
            login_status = apis.login.GetCurrentLoginStatus()
            if (
                login_status
                and "account" in login_status
                and "id" in login_status["account"]  # type: ignore
            ):  # type: ignore
                detail = apis.user.GetUserDetail(login_status["account"]["id"])  # type: ignore
                logging.debug(f"{detail['profile']['avatarUrl']=}")  # type: ignore
                avatar_url = detail["profile"]["avatarUrl"]  # type: ignore
                avatar_data = requests.get(avatar_url).content
                with open("images/avatar.png", "wb") as f:
                    f.write(avatar_data)
        except Exception as e:
            logging.warning(f"Failed to fetch user detail or avatar: {e}")

        nickname = "Anonymous User"
        if session is not None:
            try:
                nick = getattr(session, "nickname", None)
                if nick and isinstance(nick, str) and nick.strip():
                    nickname = nick.strip()
                if cfg.login_status:
                    nick = getattr(cfg.login_status.get("account"), "userName", None)
                    if nick and isinstance(nick, str) and nick.strip():
                        nickname = nick.strip()
            except Exception as e:
                logging.warning(f"Failed to get nickname: {e}")
        self.nickname.setText(nickname)

        vip_level = 0
        if session is not None:
            try:
                vip = getattr(session, "vipType", 0)
                if isinstance(vip, (int, float)):
                    vip_level = int(vip)
            except Exception as e:
                logging.warning(f"Failed to get vipType: {e}")
        self.vip.setText(f"VIP Level: {vip_level}")

        if not os.path.exists("images/avatar.png"):
            pixmap = QPixmap("images/def_avatar.png")
        else:
            pixmap = QPixmap("images/avatar.png")
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                self.height() * 0.4,  # type: ignore
                self.height() * 0.4,  # type: ignore
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.avatar.setPixmap(scaled)

    def login(self):
        method = get_value_bylist(
            mwindow,
            "Login",
            "choose method to log into an account",
            ["QR Code", "Cell Phone", "Anonymous"],
        )
        if method is None:
            return

        if method == "Anonymous":
            apis.login.LoginViaAnonymousAccount()

            cfg.session = ncm.DumpSessionAsString(ncm.GetCurrentSession())
        elif method == "QR Code":
            logging.info("start logging in(via QRCode)")

            key: str = apis.login.LoginQrcodeUnikey()["unikey"]  # type: ignore
            logging.debug(f"{key=}")

            url = apis.login.GetLoginQRCodeUrl(key)
            logging.debug(f"{url=}")

            msgbox = QRCodeLoginDialog(mwindow, url, key, logging)
            if msgbox.exec():
                cfg.session = ncm.DumpSessionAsString(ncm.GetCurrentSession())
                cfg.login_status = apis.login.GetCurrentLoginStatus()  # type: ignore
                cfg.login_method = "QR code"
        elif method == "Cell Phone":
            logging.info("start logging in(via cell phone)")
            phone = get_text_lineedit(
                "Login", "enter your cell phone number", "1xxxxxxxxxx", mwindow
            )
            if not phone:
                return

            result = apis.login.SetSendRegisterVerifcationCodeViaCellphone(phone, 86)
            assert result.get("code", 0) == 200, "Invaild response"  # type: ignore
            while True:
                captcha = get_text_lineedit(
                    "Verification Code Sent",
                    "enter the verification code",
                    "xxxx",
                    mwindow,
                )
                if len(captcha) != 4:
                    continue
                verified = apis.login.GetRegisterVerifcationStatusViaCellphone(
                    phone, captcha, 86
                )
                if verified.get("code", 0) == 200:  # type: ignore
                    break

            apis.login.LoginViaCellphone(phone, captcha=captcha, ctcode=86)

            cfg.session = ncm.DumpSessionAsString(ncm.GetCurrentSession())
            cfg.login_status = apis.login.GetCurrentLoginStatus()  # type: ignore
            cfg.login_method = "cell phone"

        InfoBar.success(
            "Login successful",
            f"logged in via method {method}",
            parent=mwindow,
            duration=5000,
        )

        self.refreshInformations()

    def showSession(self):
        s = ncm.DumpSessionAsString(ncm.GetCurrentSession())

        msgbox = MessageBox("Session", s, mwindow)
        msgbox.exec()


class EditingIslandOverlay(QWidget):
    def __init__(self) -> None:
        super().__init__()

        self.setFixedSize(QApplication.primaryScreen().size())
        self.move(0, 0)

        self.setMouseTracking(True)

        self.dragging = False
        self.dragging_pos = QPointF(0, 0)

        self.mouse_pos = QPointF(0, 0)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.X11BypassWindowManagerHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.repaint)
        self.timer.start(int((1 / 60) * 1000))

        InfoBar.info(
            "Help", "press ESC to quit", isClosable=False, duration=-1, parent=self
        )

        self.hide()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.mouse_pos = event.position()
        if self.dragging:
            ip.island_x = int(
                self.mouse_pos.x() - self.dragging_pos.x() + (island.island_width / 2)
            )
            if abs((self.width() / 2) - ip.island_x) < 30:
                ip.island_x = int(self.width() / 2)
            ip.island_y = int(self.mouse_pos.y() - self.dragging_pos.y())
            if abs((self.height() / 2) - ip.island_y) < 30:
                ip.island_y = int(self.height() / 2)
        return super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.dragging:
            self.dragging = False
        return super().mouseReleaseEvent(event)

    def showEvent(self, event: QShowEvent) -> None:
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        return super().showEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            mwindow.show()
            return
        return super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if QRectF(
                ip.island_x - (island.island_width / 2),
                ip.island_y,
                island.island_width,
                island.island_height,
            ).contains(event.position()):
                self.dragging = True
                self.dragging_pos = event.position() - QPointF(
                    ip.island_x - (island.island_width / 2), ip.island_y
                )
        return super().mousePressEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        if not self.isVisible():
            return

        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 120))
        painter.setPen(QPen(QColor(255, 255, 255, 100), 1))
        painter.drawLines(
            [
                QLineF(self.mouse_pos.x(), 0, self.mouse_pos.x(), self.height()),
                QLineF(0, self.mouse_pos.y(), self.width(), self.mouse_pos.y()),
            ]
        )
        painter.drawRoundedRect(
            ip.island_x - (island.island_width / 2),  # type: ignore
            ip.island_y,
            island.island_width,  # type: ignore
            island.island_height,  # type: ignore
            (island.island_height / 2),
            (island.island_height / 2),
        )

        if ip.island_x == int(self.width() / 2) and self.dragging:
            painter.setPen(QPen(QColor(255, 255, 255, 50), 1))
            painter.drawLine(
                int(self.width() / 2), 0, int(self.width() / 2), self.height()
            )
        if ip.island_y == int(self.height() / 2) and self.dragging:
            painter.setPen(QPen(QColor(255, 255, 255, 50), 1))
            painter.drawLine(
                0, int(self.height() / 2), self.width(), int(self.height() / 2)
            )
        painter.end()


class LyricIslandOverlay(QWidget):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.X11BypassWindowManagerHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.last_draw = time.perf_counter()

        self.ft = QFont(harmony_font_family, 14)
        self.me = QFontMetricsF(self.ft)
        self.island_height = self.me.height() + 5

        self.island_width = 0
        self.target_width = 0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.repaint)
        self.timer.start(int((1 / 60) * 1000))

        self.resize(QApplication.primaryScreen().size())
        self.move(0, 0)

    def paintEvent(self, event: QPaintEvent) -> None:
        lyric = mgr.getCurrentLyric(player.getPosition())["content"]
        txt = (
            (
                lyric
                if not island_editing_overlay.isVisible() or lyric
                else "Example Lyric"
            )
            if ip.island_check.isChecked()
            else ""
        )

        self.target_width = (
            (self.me.horizontalAdvance(txt) + (10 if txt else 0))
            if ip.island_check.isChecked()
            else 0
        )
        self.island_width += (self.target_width - self.island_width) * min(
            1, (time.perf_counter() - self.last_draw) * 5
        )

        if not ip.island_check.isChecked() and self.island_width < 1:
            return

        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        self.last_draw = time.perf_counter()

        path = QPainterPath()
        path.addRoundedRect(
            ip.island_x - (self.island_width / 2),
            ip.island_y,
            self.island_width,
            self.island_height,
            self.island_height / 2,
            self.island_height / 2,
        )
        painter.setClipPath(path)
        painter.fillPath(path, QColor(0, 0, 0, 175))
        painter.setFont(self.ft)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(
            (ip.island_x - (self.island_width / 2)) + 5,  # type: ignore
            ip.island_y + self.me.height() - 2.5,  # type: ignore
            txt,
        )  # type: ignore # 实际上这个函数可以提供浮点数数据，虽然类型标注为int...

        self.resize(
            ip.island_x + (self.island_width / 2), # type: ignore
            ip.island_y + self.island_height,  # type: ignore
        )  # type: ignore

        painter.end()


class SouthsideMusicTitleBar(TitleBar):
    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedHeight(48)
        self.hBoxLayout.removeWidget(self.minBtn)
        self.hBoxLayout.removeWidget(self.maxBtn)
        self.hBoxLayout.removeWidget(self.closeBtn)

        # add title label
        self.titleLabel = CaptionLabel(self)
        self.hBoxLayout.insertWidget(
            0,
            self.titleLabel,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        self.titleLabel.setObjectName("titleLabel")
        self.window().windowTitleChanged.connect(self.setTitle)

        middle_layout = QHBoxLayout()
        middle_widget = QWidget()

        self.fm_label = QLabel(self)
        self.fm_label.setFixedSize(40, 40)
        self.fm_label.setObjectName("fm_label")
        self.hBoxLayout.insertWidget(
            1,
            self.fm_label,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        texts_layout = QVBoxLayout()

        self.song_title = QLabel(self)
        f = self.song_title.font()
        f.setPointSize(f.pointSize() + 1)
        self.song_title.setFont(f)
        self.song_title.setStyleSheet("font-weight: bold;")
        self.song_title.setObjectName("song_title")

        texts_layout.addWidget(self.song_title)

        self.lyric_label = QLabel(self)
        self.lyric_label.setObjectName("lyric_label")
        texts_layout.addWidget(self.lyric_label)

        middle_layout.addWidget(self.fm_label)
        middle_layout.addLayout(texts_layout)
        middle_widget.setLayout(middle_layout)

        self.hBoxLayout.addWidget(
            middle_widget, 2, alignment=Qt.AlignmentFlag.AlignVCenter
        )

        self.vBoxLayout = QVBoxLayout()
        self.buttonLayout = QHBoxLayout()
        self.buttonLayout.setSpacing(0)
        self.buttonLayout.setContentsMargins(0, 0, 0, 0)
        self.buttonLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.buttonLayout.addWidget(self.minBtn)
        self.buttonLayout.addWidget(self.maxBtn)
        self.buttonLayout.addWidget(self.closeBtn)
        self.vBoxLayout.addLayout(self.buttonLayout)
        self.vBoxLayout.addStretch(1)
        self.hBoxLayout.addLayout(self.vBoxLayout, 0)

        FluentStyleSheet.FLUENT_WINDOW.apply(self)

    def setTitle(self, title):
        self.titleLabel.setText(title)
        self.titleLabel.adjustSize()


class MainWindow(FluentWindowBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitleBar(SouthsideMusicTitleBar(self))

        self.navigationInterface = NavigationInterface(self, showReturnButton=True)
        self.widgetLayout = QVBoxLayout()

        contents_layout = QHBoxLayout()

        left_layout = QVBoxLayout()

        # initialize layout
        self.hBoxLayout.addWidget(self.navigationInterface)
        self.hBoxLayout.addLayout(self.widgetLayout)
        self.hBoxLayout.setStretchFactor(self.widgetLayout, 1)

        left_layout.addWidget(self.stackedWidget)
        contents_layout.setContentsMargins(0, 48, 0, 0)

        left_layout.addWidget(dp.controller, alignment=Qt.AlignmentFlag.AlignHCenter)
        contents_layout.addLayout(left_layout)
        contents_layout.addWidget(dp.expanded_widget)
        self.widgetLayout.addLayout(contents_layout)

        self.navigationInterface.displayModeChanged.connect(self.titleBar.raise_)
        self.titleBar.raise_()

        self.closing = False
        self.connected = False

        self.setWindowTitle("Southside Music")

        self.addSubInterface(
            sp,
            getQIcon("music"),
            "Search",
        )
        self.addSubInterface(
            dp,
            getQIcon("studio"),
            "Playing",
        )
        self.addSubInterface(
            fp,
            getQIcon("fav"),
            "Favorites",
            NavigationItemPosition.BOTTOM,
        )
        self.addSubInterface(
            ip,
            getQIcon("island"),
            "Lyric Island",
            NavigationItemPosition.SCROLL,
        )
        self.addSubInterface(
            sep,
            getQIcon("session"),
            "Session",
            NavigationItemPosition.BOTTOM,
        )

        self.show()

        if cfg.window_width == 0 and cfg.window_height == 0:
            self.resize(QApplication.primaryScreen().size() * 0.65)

            cfg.window_x = self.x()
            cfg.window_y = self.y()
            cfg.window_width = self.width()
            cfg.window_height = self.height()
        else:
            self.move(cfg.window_x, cfg.window_y)
            self.resize(cfg.window_width, cfg.window_height)

            if cfg.window_maximized:
                QTimer.singleShot(500, self.showMaximized)

        self.init()

        QTimer.singleShot(1750, ws_server.start)

    def addSubInterface(
        self,
        interface: QWidget,
        icon: Union[FluentIconBase, QIcon, str],
        text: str,
        position=NavigationItemPosition.TOP,
        parent=None,
        isTransparent=False,
    ) -> NavigationTreeWidget:
        if not interface.objectName():
            raise ValueError("The object name of `interface` can't be empty string.")

        parentRouteKey = parent
        if parent and isinstance(parent, QWidget):
            parentRouteKey = parent.objectName()
            if not parentRouteKey:
                raise ValueError("The object name of `parent` can't be empty string.")

        interface.setProperty("isStackedTransparent", isTransparent)
        self.stackedWidget.addWidget(interface)

        # add navigation item
        routeKey = interface.objectName()
        item = self.navigationInterface.addItem(
            routeKey=routeKey,
            icon=icon,
            text=text,
            onClick=lambda: self.switchTo(interface),
            position=position,
            tooltip=text,
            parentRouteKey=parentRouteKey,  # type: ignore
        )

        # initialize selected item
        if self.stackedWidget.count() == 1:
            self.stackedWidget.currentChanged.connect(self._onCurrentInterfaceChanged)
            self.navigationInterface.setCurrentItem(routeKey)
            qrouter.setDefaultRouteKey(self.stackedWidget, routeKey)  # type: ignore

        self._updateStackedBackground()

        return item

    def play(self, card: SongCard):
        logging.debug(card.info["id"])

        dp.lyric_label.setText("")
        dp.transl_label.setText("")

        dp.cur = None

        dp.cur = card  # type: ignore
        self.switchTo(dp)
        dp.init()

    def init(self) -> None:
        _last_storable: SongStorable | None = None

        def _init():
            global launchwindow
            launchwindow.setStatusText(
                "Initializing...\n  Initializing Mainwindow...\n    Initializing..."
            )

            wy.init()

            dp.play_method_box.setCurrentText(cfg.play_method)

            ############################## 世界上最神秘的虫子修复 #####################################
            ip.island_check.setChecked(True)
            ip.island_check.setChecked(False)
            ############################## 世界上最神秘的虫子修复 #####################################

            ip.island_check.setChecked(cfg.island_checked)

            nonlocal _last_storable

            if cfg.last_playing_song:
                _last_storable = cfg.last_playing_song
                dp.playlist.append(_last_storable)

                dp.playSongAtIndex(0)
                dp.controller.setPlaytime(cfg.last_playing_time)
                player.stop()

            launchwindow.setStatusText("Initializing...\n  Initializing Mainwindow...")

        def _finish_init():
            if isinstance(_last_storable, SongStorable):
                dp.addSongCardToList(_last_storable)

            launchwindow.deleteLater()
            sep.refreshInformations()

        doWithMultiThreading(_init, (), self, "Loading...", finished=_finish_init)

        InfoBar.info(
            "Initialization", f"Loaded {len(favs)} folders", parent=self, duration=2000
        )

    def closeEvent(self, e: QCloseEvent):
        e.ignore()
        self.closing = True

        self.hide()
        island.hide()
        player.stop()

        ws_server.stop()
        ws_server.join()

        cfg.last_playing_song = (
            dp.cur.storable if isinstance(dp.cur, DummyCard) else None
        )
        cfg.last_playing_time = player.getPosition()
        cfg.island_checked = ip.island_check.isChecked()
        cfg.play_method = dp.play_method_box.currentText()  # type: ignore
        cfg.island_x = ip.island_x
        cfg.island_y = ip.island_y
        cfg.window_x = self.x() + (
            253 if dp.controller.expand_btn.text() == "Collapse" else 0
        )
        cfg.window_y = self.y()
        cfg.window_width = self.width() - (
            505 if dp.controller.expand_btn.text() == "Collapse" else 0
        )
        cfg.window_height = self.height()
        cfg.window_maximized = self.isMaximized()

        saveConfig()
        saveFavorites(favs)

        island_editing_overlay.timer.stop()
        island_editing_overlay.timer.deleteLater()
        island_editing_overlay.deleteLater()
        island.timer.stop()
        island.timer.deleteLater()
        island.deleteLater()

        ws_server.stop()
        ws_server.join()
        player.stop()

        sys.exit(0)

    def resizeEvent(self, e):
        self.titleBar.move(46, 0)
        self.titleBar.resize(self.width() - 46, self.titleBar.height())

    def onWebsocketConnected(self):
        InfoBar.success(
            "SouthsideClient connection",
            "SouthsideMusic was connected to SouthsidClient",
            duration=5000,
            parent=self,
        )
        QTimer.singleShot(
            500,
            lambda: ws_handler.send(
                json.dumps(
                    {
                        "option": f"{'disable' if not dp.enableFFT_box.isChecked() else 'enable'}_fft"
                    }
                )
            ),
        )

        dp.sendSongFMAndInfo()

        self.connected = True

        dp.disconnect_btn.setEnabled(True)
        dp.connect_btn.setEnabled(False)

    def onWebsocketDisconnected(self):
        InfoBar.warning(
            "SouthsideClient connection",
            "SouthsideMusic was been disconnected from SouthsidClient",
            duration=5000,
            parent=mwindow,
        )

        self.connected = False

        dp.connect_btn.setEnabled(True)
        dp.disconnect_btn.setEnabled(False)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_F3:
            debug_window.setVisible(not debug_window.isVisible())
            event.accept()
        elif event.key() == Qt.Key.Key_Space:
            dp.controller.toggle()
            event.accept()
        else:
            return super().keyPressEvent(event)

class LaunchWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setFixedSize(app.primaryScreen().size() * 0.25)
        hPyT.window_frame.center(self)

        launchlayout = QVBoxLayout()
        launchlayout.addWidget(
            TitleLabel("Southside Music"),
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
        )
        self.sublabel = QLabel("Launching...")
        launchlayout.addWidget(
            self.sublabel,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        )
        self.setLayout(launchlayout)

        self.setStyleSheet(
            f"QWidget {{ background-color: {'#FFFFFF' if darkdetect.isLight() else '#000000'} }} QLabel {{ color: {'white' if darkdetect.isDark() else 'black'}; }}"
        )

        self.show()

    def setStatusText(self, text: str, sleep=True):
        self.sublabel.setText(f"Launching...\n{text}")
        app.processEvents()
        if sleep:
            time.sleep(0.05)

class DebugWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()

        self.setFixedSize(QApplication.primaryScreen().size() * 0.7)

        global_layout = QVBoxLayout()
        self.objname_inputer = LineEdit()
        global_layout.addWidget(self.objname_inputer)

        self.obj_label = QLabel()
        global_layout.addWidget(self.obj_label)

        scroll_widget = SmoothScrollArea()
        content_widget = QWidget()
        content_layout = QVBoxLayout()

        self.eval_inputer = LineEdit()
        self.eval_label = QLabel()

        content_layout.addWidget(self.eval_inputer)
        content_layout.addWidget(self.eval_label)

        self.tree = TreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(['Name', 'Value'])
        self.tree.header().setStretchLastSection(False)

        content_layout.addWidget(self.tree)

        content_widget.setLayout(content_layout)
        scroll_widget.setWidget(content_widget)
        scroll_widget.setWidgetResizable(True)
        global_layout.addWidget(scroll_widget)

        self.selected_object: Optional[object] = None

        self.setLayout(global_layout)

        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.updateDatas)
        self.update_timer.start(500)

        self.hide()

    def updateDatas(self) -> None:
        if not self.isVisible():
            return
        if self.selected_object:
            self.tree.clear()
            
            def _recursive(obj: object, layer: int) -> list:
                res = []
                if layer > 5:
                    return [('To deep')]
                if hasattr(obj, '__dict__'):
                    for k, v in obj.__dict__.items():
                        if isinstance(v, int) or isinstance(v, float) or isinstance(v, str) or isinstance(v, bool) or isinstance(v, list):
                            res.append((k, v))
                        else:
                            res.append((k, _recursive(v, layer+1)))
                    return res
                else:
                    return []
            
            def _build_tree(data: list, parent: Union[QTreeWidget, QTreeWidgetItem]):
                for item in data:
                    if isinstance(item, tuple):
                        key, value = item
                        if isinstance(value, list):
                            tree_item = QTreeWidgetItem([key, ''])
                            if isinstance(parent, QTreeWidget):
                                parent.addTopLevelItem(tree_item)
                            else:
                                parent.addChild(tree_item)
                            _build_tree(value, tree_item)
                        else:
                            tree_item = QTreeWidgetItem([key, str(value)])
                            if isinstance(parent, QTreeWidget):
                                parent.addTopLevelItem(tree_item)
                            else:
                                parent.addChild(tree_item)
                    elif isinstance(item, list):
                        for sub_item in item:
                            _build_tree([sub_item], parent)
            
            result = _recursive(self.selected_object, 1)
            _build_tree(result, self.tree)
            
            self.tree.expandAll()

            self.obj_label.setText(str(self.selected_object))
        self.tree.setColumnWidth(0, self.width() // 2 - 15)
        self.tree.setColumnWidth(1, self.width() // 2 - 15)

        self.selected_object = globals().get(self.objname_inputer.text())

        completer = QCompleter(list(globals().keys()), self.objname_inputer)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setMaxVisibleItems(20)
        self.objname_inputer.setCompleter(completer)

        try: self.eval_label.setText(str(eval(self.eval_inputer.text())))
        except: pass

        self.setStyleSheet(f'background: {'white' if darkdetect.isLight() else 'black'}')

    def closeEvent(self, event: QCloseEvent):
        event.ignore()
        self.hide()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            return super().keyPressEvent(event)

if __name__ == "__main__":
    if cfg.login_status and not ncm.GetCurrentSession().is_anonymous:
        apis.login.WriteLoginInfo(cfg.login_status)
    else:
        cfg.login_status = apis.login.GetCurrentLoginStatus()  # type: ignore

    app.setStyleSheet(
        f"QLabel {{ color: {'white' if darkdetect.isDark() else 'black'}; }}"
    )
    setTheme(Theme.AUTO)

    launchwindow = LaunchWindow()
    launchwindow.setStatusText("Initializing...")

    app.processEvents()

    harmony_font_family = QFontDatabase.applicationFontFamilies(
        QFontDatabase.addApplicationFont("fonts/HARMONYOS_SANS_SC_REGULAR.ttf")
    )[0]

    from utils.loading_util import doWithMultiThreading, downloadWithMultiThreading

    from utils.base.w163_util import CloudMusicUtil

    launchwindow.setStatusText("Initializing...\n  Intializing services...")
    wy = CloudMusicUtil()  # type: ignore

    mgr = LRCLyricManager()
    transmgr = LRCLyricManager()

    launchwindow.setStatusText("Initializing...\n  Loading favorites...")
    favs: list[FolderInfo] = loadFavoritesWithLaunching(launchwindow)

    loadConfig()

    player = AudioPlayer()

    lock = threading.Lock()

    if cfg.session is None:
        apis.login.LoginViaAnonymousAccount()

        sstr = ncm.DumpSessionAsString(apis.GetCurrentSession())
        cfg.session = sstr
        cfg.login_status = apis.login.GetCurrentLoginStatus()  # type: ignore

        logging.info("logged into generated anonymous account")
    else:
        ncm.SetCurrentSession(ncm.LoadSessionFromString(cfg.session))

        logging.info("loaded session from pickle")

        if (
            cfg.login_method == "cell phone" or cfg.login_method == "QR code"
        ) and cfg.login_status:
            apis.login.WriteLoginInfo(cfg.login_status)  # type: ignore
            logging.info("wrote login info")

    launchwindow.setStatusText("Initializing...\n  Initializing pages...")

    debug_window = DebugWindow()

    dp = PlayingPage()
    sp = SearchPage()
    fp = FavoritesPage()
    island_editing_overlay = EditingIslandOverlay()
    island = LyricIslandOverlay()
    ip = DynamicIslandPage()
    sep = SessionPage()
    launchwindow.setStatusText("Initializing...\n  Initializing Mainwindow...")
    mwindow = MainWindow()

    fp.refresh()

    app.exec()
