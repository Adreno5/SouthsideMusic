from __future__ import annotations

import base64
import hashlib
import logging
import os
import threading
from typing import Callable, TYPE_CHECKING, cast as _cast

if TYPE_CHECKING:
    from views.main_window import MainWindow
    from views.sidebar import Sidebar
    from views.playing_page import PlayingPage

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    FluentIcon,
    FlowLayout,
    IndeterminateProgressRing,
    InfoBar,
    MenuAnimationType,
    PrimaryToolButton,
    RoundMenu,
    SubtitleLabel,
    TransparentToolButton,
)

from utils.base.base_util import (
    IMAGE_DATA_DIR,
    MUSIC_DATA_DIR,
    SongDetail,
    SongInfo,
    SongStorable,
)
from utils.icon_util import bindIcon, getQIcon
from utils import darkdetect_util as darkdetect
from utils.loading_util import doWithMultiThreading
from utils.soundfile_util import getSongFormat, saveSongWithInformations
from utils import requests_util as requests

from pyncm import apis
import pyncm as ncm


_image_download_locks: dict[str, threading.Lock] = {}


def _get_image_download_lock(song_id: str) -> threading.Lock:
    global _image_download_locks
    if song_id not in _image_download_locks:
        _image_download_locks[song_id] = threading.Lock()
    return _image_download_locks[song_id]


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

    def __init__(self, info: SongInfo, play_callback: Callable, mwindow) -> None:
        super().__init__()
        self.info = info
        self._play_callback = play_callback
        self._mwindow = mwindow

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

        self.favbtn = TransparentToolButton()
        bindIcon(self.favbtn, "fav")
        self.favbtn.setEnabled(True)
        bottom_layout.addWidget(self.favbtn)
        self.favbtn.clicked.connect(self.addToFavorites)

        global_layout.addLayout(top_layout)
        global_layout.addLayout(bottom_layout)

        self.setLayout(global_layout)

        self.load = False
        self.imageLoaded.connect(self.onImageLoaded)

    def play(self):
        self._play_callback(self)

    def addToFavorites(self):
        if self.info["privilege"] > ncm.GetCurrentSession().vipType:
            InfoBar.warning(
                "Cannot add to favorites",
                "Need more privilege",
                parent=self._mwindow,
            )
            return

        result_container = []

        def _download():
            with ncm.GetCurrentSession():
                response = apis.track.GetTrackDetail(song_ids=[self.info["id"]])
                assert isinstance(response, dict), "Invalid response"
                image_url = response["songs"][0]["al"]["picUrl"]  # type: ignore

                image_bytes = requests.get(image_url).content

                music_url = apis.track.GetTrackAudio(
                    str(self.info["id"]),  # type: ignore
                    bitrate=3200 * 1000,
                )
                music_bytes = requests.get(music_url["data"][0]["url"]).content  # type: ignore

                result_container.append((image_bytes, music_bytes))

        def _finish():
            from utils.favorite_util import loadFavorites, saveFavorites
            from utils.base.base_util import FolderInfo

            image_bytes, music_bytes = result_container[0]

            image_base64 = base64.b64encode(image_bytes).decode()
            content_base64 = base64.b64encode(music_bytes).decode()
            image_cache_hash = hashlib.sha256(image_bytes).hexdigest()
            content_cache_hash = hashlib.sha256(music_bytes).hexdigest()

            os.makedirs(IMAGE_DATA_DIR, exist_ok=True)
            os.makedirs(MUSIC_DATA_DIR, exist_ok=True)
            with open(os.path.join(IMAGE_DATA_DIR, image_cache_hash), "wb") as f:
                f.write(image_bytes)
            with open(os.path.join(MUSIC_DATA_DIR, content_cache_hash), "wb") as f:
                f.write(music_bytes)

            storable = SongStorable(
                image_base64=image_base64,
                content_base64=content_base64,
                image_cache_hash=image_cache_hash,
                content_cache_hash=content_cache_hash,
                gain=1.0,
                target_lufs=-16,
                lyric="",
                translated_lyric="",
                info=self.info,
            )

            favs = loadFavorites()
            favs[0]["songs"].append(storable)
            saveFavorites(favs)

            InfoBar.success(
                "Favorited",
                f"Added {self.info['name']} to favorites",
                parent=self._mwindow,
                duration=3000,
            )

        doWithMultiThreading(_download, (), self._mwindow, _finish)

    def loadDetailAndImage(self):
        def _do():
            with ncm.GetCurrentSession():
                response = apis.track.GetTrackDetail(song_ids=[self.info["id"]])
                assert isinstance(response, dict), "Invalid response"
                img_url = response["songs"][0]["al"]["picUrl"]  # type: ignore
                self.detail["image_url"] = img_url

                img_bytes = requests.get(img_url).content

                self.imageLoaded.emit(img_bytes)

        doWithMultiThreading(_do, (), self._mwindow)

    def onImageLoaded(self, bytes):
        self.ring.hide()
        self.img_label.show()
        pixmap = QPixmap()
        pixmap.loadFromData(bytes)
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                self.img_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.img_label.setPixmap(scaled)
        self.playbtn.setEnabled(True)


class _SongCardItem(QWidget):
    clicked = Signal(object)

    def __init__(
        self,
        storable: SongStorable,
        dp: PlayingPage | None = None,
        favs_ref=None,
        save_favorites_fn=None,
        mwindow: MainWindow | None = None,
        sidebar: Sidebar | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.storable = storable
        self._dp: PlayingPage = dp  # type: ignore
        self._favs_ref = favs_ref
        self._save_favorites_fn = save_favorites_fn
        self._mwindow: MainWindow = mwindow  # type: ignore
        self._sidebar: Sidebar = sidebar  # type: ignore

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
        if self._dp:
            self._dp.imageAssetPersisted.connect(self._on_image_asset_persisted)
        if self.img_label.pixmap() is None or self.img_label.pixmap().isNull():
            threading.Thread(
                target=self._auto_download_missing_image, daemon=True
            ).start()

    def _on_image_asset_persisted(self, storable: SongStorable):
        if storable is self.storable:
            self.loadImage()

    def _auto_download_missing_image(self):
        storable = self.storable
        storable._ensure_cache_fields()
        if storable.image_cache_hash and os.path.exists(
            os.path.join(IMAGE_DATA_DIR, storable.image_cache_hash)
        ):
            return

        lock = _get_image_download_lock(storable.id)
        if not lock.acquire(blocking=False):
            return
        try:
            storable._ensure_cache_fields()
            if storable.image_cache_hash and os.path.exists(
                os.path.join(IMAGE_DATA_DIR, storable.image_cache_hash)
            ):
                return
            try:
                with ncm.GetCurrentSession():
                    response = apis.track.GetTrackDetail(song_ids=[storable.id])
                    assert isinstance(response, dict)
                    image_url = response["songs"][0]["al"]["picUrl"]  # type: ignore
                    image_bytes = requests.get(image_url).content
            except Exception as e:
                logging.warning(f"failed to auto-download image for {storable.id}: {e}")
                return

            if not image_bytes:
                return
            os.makedirs(IMAGE_DATA_DIR, exist_ok=True)
            cache_hash = hashlib.sha256(image_bytes).hexdigest()
            cache_path = os.path.join(IMAGE_DATA_DIR, cache_hash)
            if not os.path.exists(cache_path):
                with open(cache_path, "wb") as f:
                    f.write(image_bytes)
            storable.image_base64 = base64.b64encode(image_bytes).decode()
            storable.image_cache_hash = cache_hash
            self._save_favorites_fn(self._favs_ref)  # type: ignore
            self._dp.imageAssetPersisted.emit(storable)
        finally:
            lock.release()

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
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.storable)
        return super().mousePressEvent(event)


class PlaylistSongCard(_SongCardItem):
    def contextMenuEvent(self, event):
        menu = RoundMenu(parent=self)

        export = Action("Export", menu)
        export.setIcon(getQIcon("export"))
        rm = Action("Remove", menu)
        rm.setIcon(getQIcon("remove"))

        export.triggered.connect(lambda: self._exportSong())
        rm.triggered.connect(lambda: self._removeSong())

        menu.addActions([export, rm])

        menu.exec(event.globalPos(), aniType=MenuAnimationType.DROP_DOWN)

    def _exportSong(self):
        if not self._dp.ensureAssets(self.storable):
            return
        with open(
            os.path.join(MUSIC_DATA_DIR, self.storable.content_cache_hash), "rb"
        ) as f:
            export_path, fmt = QFileDialog.getSaveFileName(
                self._mwindow,
                "Export song",
                f"./{self.storable.name} - {self.storable.artists}{getSongFormat(f.read())}",
                "Song Files (*.mp3, *.m4a, *.flac, *.wav, *.ogg, *.opus)",
            )

        if export_path:

            def _export():
                with ncm.GetCurrentSession():
                    response = apis.track.GetTrackDetail(song_ids=[self.storable.id])
                    assert isinstance(response, dict)
                    detail = response["songs"][0]  # type: ignore
                    image_url = detail["al"]["picUrl"]

                    image_bytes = requests.get(image_url).content

                    album = detail["al"]["name"]
                    track_number = f"{detail['cd']}/{detail['no']}"
                    publish_time = detail.get("publishTime", 0)
                    year = ""
                    if publish_time:
                        import datetime

                        year = str(
                            datetime.datetime.fromtimestamp(publish_time / 1000).year
                        )

                    with open(
                        os.path.join(MUSIC_DATA_DIR, self.storable.content_cache_hash),
                        "rb",
                    ) as song:
                        saveSongWithInformations(
                            song.read(),
                            image_bytes,
                            self.storable.name,
                            self.storable.artists,
                            export_path,
                            self.storable.lyric,
                            album,
                            "",
                            year,
                            track_number,
                            "",
                            "",
                        )

            def _final():
                InfoBar.success(
                    "Export",
                    f"Exported song {self.storable.name}",
                    parent=self._mwindow,
                    duration=5000,
                )

            doWithMultiThreading(_export, (), self._mwindow, _final)

    def _removeSong(self):
        for i, storable in enumerate(self._dp.playlist):
            if storable.id == self.storable.id:
                self._dp.playlist.remove(self._dp.playlist[i])
                break

        self._sidebar.refreshPlaylistWidget()

        if self._dp.cur:
            if self.storable.id == self._dp.cur.storable.id:
                self._dp.playSongAtIndex(self._dp.current_index)


class FavoriteSongCard(_SongCardItem):
    def contextMenuEvent(self, event):
        menu = RoundMenu(parent=self)

        export = Action("Export", menu)
        export.setIcon(getQIcon("export"))

        export.triggered.connect(lambda: self._exportSong())

        menu.addActions([export])

        menu.exec(event.globalPos(), aniType=MenuAnimationType.DROP_DOWN)

    def _exportSong(self):
        if not self._dp.ensureAssets(self.storable):
            return
        with open(
            os.path.join(MUSIC_DATA_DIR, self.storable.content_cache_hash), "rb"
        ) as f:
            export_path, fmt = QFileDialog.getSaveFileName(
                self._mwindow,
                "Export song",
                f"./{self.storable.name} - {self.storable.artists}{getSongFormat(f.read())}",
                "Song Files (*.mp3, *.m4a, *.flac, *.wav, *.ogg, *.opus)",
            )

        if export_path:

            def _export():
                with ncm.GetCurrentSession():
                    response = apis.track.GetTrackDetail(song_ids=[self.storable.id])
                    assert isinstance(response, dict)
                    detail = response["songs"][0]  # type: ignore
                    image_url = detail["al"]["picUrl"]

                    image_bytes = requests.get(image_url).content

                    album = detail["al"]["name"]
                    track_number = f"{detail['cd']}/{detail['no']}"
                    publish_time = detail.get("publishTime", 0)
                    year = ""
                    if publish_time:
                        import datetime

                        year = str(
                            datetime.datetime.fromtimestamp(publish_time / 1000).year
                        )

                    with open(
                        os.path.join(MUSIC_DATA_DIR, self.storable.content_cache_hash),
                        "rb",
                    ) as song:
                        saveSongWithInformations(
                            song.read(),
                            image_bytes,
                            self.storable.name,
                            self.storable.artists,
                            export_path,
                            self.storable.lyric,
                            album,
                            "",
                            year,
                            track_number,
                            "",
                            "",
                        )

            def _final():
                InfoBar.success(
                    "Export",
                    f"Exported song {self.storable.name}",
                    parent=self._mwindow,
                    duration=5000,
                )

            doWithMultiThreading(_export, (), self._mwindow, _final)
