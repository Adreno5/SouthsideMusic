from __future__ import annotations

import base64
import hashlib
import logging

import os
import threading
import time
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from views.main_window import MainWindow
    from views.sidebar import Sidebar
    from views.playing_page import PlayingPage

from imports import IMAGE_ASSET_PERSISTED, QSize, Qt, Signal, event_bus
from imports import QImage, QMouseEvent, QPixmap
from imports import (
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
    ToolButton,
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
from utils.loading_util import (
    doWithMultiThreading,
    downloadWithMultiThreading,
    DownloadingManager,
)
from utils.soundfile_util import getSongFormat, saveSongWithInformations
from utils import requests_util as requests
from utils.favorite_util import FolderInfo, favs, saveFavorites

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
        self.detail: SongDetail = SongDetail(image_url='')
        self.storable: SongStorable = storable


class SongCard(QWidget):
    imageLoaded = Signal(bytes)

    def __init__(self, info: SongInfo, play_callback: Callable, mwindow) -> None:
        super().__init__()
        self.info = info
        self._play_callback = play_callback
        self._mwindow = mwindow

        self.detail = SongDetail(image_url='')

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
        title_label = SubtitleLabel(info['name'])
        top_layout.addWidget(title_label)
        artists_label = QLabel(info['artists'])
        artists_label.setWordWrap(True)
        top_layout.addWidget(artists_label)
        self.vip_label = SubtitleLabel(
            f'Need more privilege ({info["privilege"]}(song)>{ncm.GetCurrentSession().vipType}(yours))'
        )
        self.vip_label.setStyleSheet('color: red;')
        if info['privilege'] <= ncm.GetCurrentSession().vipType:
            self.vip_label.hide()
        top_layout.addWidget(self.vip_label)

        pri_label = QLabel(
            f'privilege: (song: {info["privilege"]}, yours: {ncm.GetCurrentSession().vipType})'
        )
        pri_label.setStyleSheet(
            f'color: {"#666666" if darkdetect.isDark() else "#CCCCCC"};'
        )
        top_layout.addWidget(pri_label)

        bottom_layout = FlowLayout()

        self.playbtn = PrimaryToolButton(FluentIcon.SEND)
        self.playbtn.setEnabled(False)
        bottom_layout.addWidget(self.playbtn)
        self.playbtn.clicked.connect(self.play)

        self.favbtn = TransparentToolButton()
        bindIcon(self.favbtn, 'fav')
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
        from utils.dialog_util import get_value_bylist, get_text_lineedit

        if self.info['privilege'] > ncm.GetCurrentSession().vipType:
            InfoBar.warning(
                'Cannot add to favorites',
                'Need more privilege',
                parent=self._mwindow,
            )
            return

        folder_names = [f['folder_name'] for f in favs]
        folder_names.append('Create New Folder...')
        chosen = get_value_bylist(
            self._mwindow,
            'Choose Folder',
            'Which folder to save this song?',
            folder_names,
        )
        if chosen is None:
            return

        if chosen == 'Create New Folder...':
            chosen = get_text_lineedit(
                self._mwindow, 'Create New Folder', 'My first folder', self._mwindow
            )
            if chosen:
                favs.append({'folder_name': chosen, 'songs': []})

            target_folder = FolderInfo(folder_name=chosen, songs=[])
        else:
            target_folder = next(f for f in favs if f['folder_name'] == chosen)

        result_container = []
        _finished = False
        _manager: DownloadingManager | None = None

        image_bytes = None

        def _downloaded(music_bytes: bytes):
            nonlocal _finished, image_bytes
            result_container.append((image_bytes, music_bytes))
            _finished = True

        def _download():
            nonlocal _manager, image_bytes
            with ncm.GetCurrentSession():
                response = apis.track.GetTrackDetail(song_ids=[self.info['id']])
                assert isinstance(response, dict), 'Invalid response'
                image_url = response['songs'][0]['al']['picUrl']  # type: ignore

                image_bytes = requests.get(image_url).content

                audio_resp = apis.track.GetTrackAudio(
                    str(self.info['id']),  # type: ignore
                    bitrate=3200 * 1000,
                )
                music_url = audio_resp['data'][0]['url']  # type: ignore

                _manager = downloadWithMultiThreading(
                    music_url, {}, {}, None, _downloaded
                )

        def _finish():
            while not _finished:
                time.sleep(0.2)

            image_bytes, music_bytes = result_container[0]

            image_cache_hash = hashlib.sha256(image_bytes).hexdigest()
            content_cache_hash = hashlib.sha256(music_bytes).hexdigest()

            os.makedirs(IMAGE_DATA_DIR, exist_ok=True)
            os.makedirs(MUSIC_DATA_DIR, exist_ok=True)
            with open(os.path.join(IMAGE_DATA_DIR, image_cache_hash), 'wb') as f:
                f.write(image_bytes)
            with open(os.path.join(MUSIC_DATA_DIR, content_cache_hash), 'wb') as f:
                f.write(music_bytes)

            storable = SongStorable(
                info={
                    'name': self.info['name'],
                    'artists': self.info['artists'],
                    'id': self.info['id'],
                    'privilege': -1,
                },
                image_cache_hash=image_cache_hash,
                content_cache_hash=content_cache_hash,
                lyric='',
            )
            target_folder['songs'].append(storable)
            saveFavorites()

            InfoBar.success(
                'Favorited',
                f"Added {self.info['name']} to '{chosen}'",
                parent=self._mwindow,
                duration=3000,
            )

        doWithMultiThreading(_download, (), self._mwindow, _finish)

    def loadDetailAndImage(self):
        self.load = True

        def _do():
            with ncm.GetCurrentSession():
                response = apis.track.GetTrackDetail(song_ids=[self.info['id']])
                assert isinstance(response, dict), 'Invalid response'
                img_url = response['songs'][0]['al']['picUrl']  # type: ignore
                self.detail['image_url'] = img_url

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
        mwindow: MainWindow | None = None,
        sidebar: Sidebar | None = None,
        parent=None,
        lazy: bool = False,
    ):
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self.storable = storable
        self._dp: PlayingPage = dp  # type: ignore
        self._mwindow: MainWindow = mwindow  # type: ignore
        self._sidebar: Sidebar = sidebar  # type: ignore
        self.load = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        order_layout = QVBoxLayout()
        order_layout.setContentsMargins(0, 0, 0, 0)
        order_layout.setSpacing(0)
        self.move_up_btn = TransparentToolButton()
        self.move_down_btn = TransparentToolButton()
        bindIcon(self.move_up_btn, 'drop_up')
        bindIcon(self.move_down_btn, 'drop_down')
        self.move_up_btn.setFixedSize(24, 24)
        self.move_down_btn.setFixedSize(24, 24)
        self.move_up_btn.clicked.connect(lambda: self.moveRequested(-1))
        self.move_down_btn.clicked.connect(lambda: self.moveRequested(1))
        order_layout.addWidget(self.move_up_btn)
        order_layout.addWidget(self.move_down_btn)
        layout.addLayout(order_layout)

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

        if not lazy:
            self.load = True
            self.loadImage()
            if self._dp:
                event_bus.subscribe(
                    IMAGE_ASSET_PERSISTED, self._on_image_asset_persisted
                )
            if self.img_label.pixmap() is None or self.img_label.pixmap().isNull():
                threading.Thread(
                    target=self._auto_download_missing_image, daemon=True
                ).start()

    def loadDetailAndImage(self):
        if self.load:
            return
        self.load = True
        self.loadImage()
        if self._dp:
            event_bus.subscribe(IMAGE_ASSET_PERSISTED, self._on_image_asset_persisted)
        try:
            needs_download = (
                self.img_label.pixmap() is None or self.img_label.pixmap().isNull()
            )
        except RuntimeError:
            return
        if needs_download:
            threading.Thread(
                target=self._auto_download_missing_image, daemon=True
            ).start()

    def _on_image_asset_persisted(self, storable: SongStorable):
        if storable is self.storable:
            self.loadImage()

    def moveRequested(self, delta: int):
        pass

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
                    image_url = response['songs'][0]['al']['picUrl']  # type: ignore
                    image_bytes = requests.get(image_url).content
            except Exception as e:
                self._logger.warning(
                    f'failed to auto-download image for {storable.id}: {e}'
                )
                return

            if not image_bytes:
                return
            os.makedirs(IMAGE_DATA_DIR, exist_ok=True)
            cache_hash = hashlib.sha256(image_bytes).hexdigest()
            cache_path = os.path.join(IMAGE_DATA_DIR, cache_hash)
            if not os.path.exists(cache_path):
                with open(cache_path, 'wb') as f:
                    f.write(image_bytes)
            storable.image_cache_hash = cache_hash
            saveFavorites()
            event_bus.emit(IMAGE_ASSET_PERSISTED, storable)
        finally:
            lock.release()

    def loadImage(self):
        if self._mwindow is None:
            return

        result: dict[str, QImage] = {}

        def _decode():
            try:
                image_bytes = self.storable.get_image_bytes()
            except FileNotFoundError:
                return
            image = QImage()
            image.loadFromData(image_bytes)
            if not image.isNull():
                result['image'] = image

        def _finish():
            image = result.get('image')
            if image is None:
                return

            def _apply_pixmap():
                if not self.load:
                    return
                try:
                    self.img_label.objectName()
                except RuntimeError:
                    return
                pixmap = QPixmap.fromImage(image)
                if not pixmap.isNull():
                    scaled = pixmap.scaled(
                        self.img_label.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self.img_label.setPixmap(scaled)

            self._mwindow.addScheduledTask(_apply_pixmap)

        try:
            doWithMultiThreading(_decode, (), self._mwindow, _finish)
        except Exception:
            pass

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.storable)
        return super().mousePressEvent(event)


class PlaylistSongCard(_SongCardItem):
    def moveRequested(self, delta: int):
        self._sidebar.movePlaylistSong(self.storable, delta)

    def contextMenuEvent(self, event):
        menu = RoundMenu(parent=self)

        export = Action('Export', menu)
        export.setIcon(getQIcon('export'))
        repeat = Action('Repeat', menu)
        repeat.setIcon(FluentIcon.SYNC.icon())
        rm = Action('Remove', menu)
        rm.setIcon(getQIcon('remove'))

        export.triggered.connect(lambda: self._exportSong())
        repeat.triggered.connect(lambda: self._repeatSong())
        rm.triggered.connect(lambda: self._removeSong())

        menu.addActions([export, repeat, rm])

        menu.exec(event.globalPos(), aniType=MenuAnimationType.DROP_DOWN)

    def _exportSong(self):
        if not self._dp.ensureAssets(self.storable):
            return
        with open(
            os.path.join(MUSIC_DATA_DIR, self.storable.content_cache_hash), 'rb'
        ) as f:
            export_path, fmt = QFileDialog.getSaveFileName(
                self._mwindow,
                'Export song',
                f'./{self.storable.name} - {self.storable.artists}{getSongFormat(f.read())}',
                'Song Files (*.mp3, *.m4a, *.flac, *.wav, *.ogg, *.opus)',
            )

        if export_path:

            def _export():
                with ncm.GetCurrentSession():
                    response = apis.track.GetTrackDetail(song_ids=[self.storable.id])
                    assert isinstance(response, dict)
                    detail = response['songs'][0]  # type: ignore
                    image_url = detail['al']['picUrl']

                    image_bytes = requests.get(image_url).content

                    album = detail['al']['name']
                    track_number = f'{detail["cd"]}/{detail["no"]}'
                    publish_time = detail.get('publishTime', 0)
                    year = ''
                    if publish_time:
                        import datetime

                        year = str(
                            datetime.datetime.fromtimestamp(publish_time / 1000).year
                        )

                    with open(
                        os.path.join(MUSIC_DATA_DIR, self.storable.content_cache_hash),
                        'rb',
                    ) as song:
                        saveSongWithInformations(
                            song.read(),
                            image_bytes,
                            self.storable.name,
                            self.storable.artists,
                            export_path,
                            self.storable.lyric,
                            album,
                            '',
                            year,
                            track_number,
                            '',
                            '',
                        )

            def _final():
                InfoBar.success(
                    'Export',
                    f'Exported song {self.storable.name}',
                    parent=self._mwindow,
                    duration=5000,
                )

            doWithMultiThreading(_export, (), self._mwindow, _final)

    def _repeatSong(self):
        try:
            index = self._dp.playlist.index(self.storable)
        except ValueError:
            return

        insert_index = index + 1
        self._dp.playlist.insert(insert_index, self.storable)
        if self._dp.current_index >= insert_index:
            self._dp.current_index += 1
        self._dp.song_randomer.init(self._dp.playlist)
        self._sidebar.refreshPlaylistWidget()
        self._sidebar.lst.setCurrentRow(insert_index)

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
    def __init__(
        self,
        storable,
        dp,
        mwindow,
        sidebar,
        remove_callback=None,
        move_callback=None,
        parent=None,
        lazy=False,
    ):
        super().__init__(storable, dp, mwindow, sidebar, parent, lazy=lazy)
        self._remove_callback = remove_callback
        self._move_callback = move_callback

    def moveRequested(self, delta: int):
        if self._move_callback:
            self._move_callback(delta)

    def contextMenuEvent(self, event):
        menu = RoundMenu(parent=self)

        export = Action('Export', menu)
        export.setIcon(getQIcon('export'))
        export.triggered.connect(lambda: self._exportSong())

        remove = Action('Remove', menu)
        remove.setIcon(getQIcon('remove'))
        remove.triggered.connect(self._removeSong)

        menu.addActions([export, remove])

        menu.exec(event.globalPos(), aniType=MenuAnimationType.DROP_DOWN)

    def _removeSong(self):
        if self._remove_callback:
            self._remove_callback()

    def _exportSong(self):
        if not self._dp.ensureAssets(self.storable):
            return
        with open(
            os.path.join(MUSIC_DATA_DIR, self.storable.content_cache_hash), 'rb'
        ) as f:
            export_path, fmt = QFileDialog.getSaveFileName(
                self._mwindow,
                'Export song',
                f'./{self.storable.name} - {self.storable.artists}{getSongFormat(f.read())}',
                'Song Files (*.mp3, *.m4a, *.flac, *.wav, *.ogg, *.opus)',
            )

        if export_path:

            def _export():
                with ncm.GetCurrentSession():
                    response = apis.track.GetTrackDetail(song_ids=[self.storable.id])
                    assert isinstance(response, dict)
                    detail = response['songs'][0]  # type: ignore
                    image_url = detail['al']['picUrl']

                    image_bytes = requests.get(image_url).content

                    album = detail['al']['name']
                    track_number = f'{detail["cd"]}/{detail["no"]}'
                    publish_time = detail.get('publishTime', 0)
                    year = ''
                    if publish_time:
                        import datetime

                        year = str(
                            datetime.datetime.fromtimestamp(publish_time / 1000).year
                        )

                    with open(
                        os.path.join(MUSIC_DATA_DIR, self.storable.content_cache_hash),
                        'rb',
                    ) as song:
                        saveSongWithInformations(
                            song.read(),
                            image_bytes,
                            self.storable.name,
                            self.storable.artists,
                            export_path,
                            self.storable.lyric,
                            album,
                            '',
                            year,
                            track_number,
                            '',
                            '',
                        )

            def _final():
                InfoBar.success(
                    'Export',
                    f'Exported song {self.storable.name}',
                    parent=self._mwindow,
                    duration=5000,
                )

            doWithMultiThreading(_export, (), self._mwindow, _final)
