from __future__ import annotations

import base64
import logging

import os
import threading
import time
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from views.main_window import MainWindow
    from views.playlist_page import PlaylistPage
    from views.playing_page import PlayingPage

from imports import (
    FAVORITES_CHANGED,
    IMAGE_ASSET_PERSISTED,
    PLAYLIST_CHANGED,
    PLAY_SONG_AT_INDEX,
    QAbstractAnimation,
    QPropertyAnimation,
    QSize,
    Qt,
    Signal,
    event_bus,
)
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

from core.models import (
    IMAGE_DATA_DIR,
    MUSIC_DATA_DIR,
    SearchSongInfo,
    SongDetail,
    SongInfo,
    SongStorable,
)
from core.icons import bindIcon, getQIcon
from core import theme as darkdetect
from core.downloader import (
    doWithMultiThreading,
    downloadWithMultiThreading,
    DownloadingManager,
)
from core.soundfile import getSongFormat, saveSongWithInformations
from core import http_utils as requests
from core.favorites import favorites_manager
from core.models import LocalFolderInfo
from core.backend import get_backend


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

    def __init__(self, info: SearchSongInfo, play_callback: Callable, mwindow) -> None:
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
        artists_text = '、'.join(a['name'] for a in info['artists'])
        title_label = SubtitleLabel(info['name'])
        top_layout.addWidget(title_label)
        artists_label = QLabel(artists_text)
        artists_label.setWordWrap(True)
        top_layout.addWidget(artists_label)

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
        from core.dialogs import get_value_bylist, get_text_lineedit

        folder_names = [f['folder_name'] for f in favorites_manager.folders]
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
                favorites_manager.addFolder(chosen)

            target_folder = LocalFolderInfo(folder_name=chosen, songs=[])
        else:
            target_folder = next(
                f for f in favorites_manager.folders if f['folder_name'] == chosen
            )

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
            backend = get_backend()
            detail = backend.get_track_detail(str(self.info['id']))
            image_url = detail['cover_url']
            image_bytes = requests.get(image_url).content

            audio = backend.get_track_audio(
                str(self.info['id']), bitrate=self.info['privilege']['max_br']
            )
            music_url = audio['url']

            _manager = downloadWithMultiThreading(music_url, {}, {}, None, _downloaded)

        def _finish():
            while not _finished:
                time.sleep(0.2)

            image_bytes, music_bytes = result_container[0]

            storable = SongStorable(
                info={
                    'name': self.info['name'],
                    'artists': '、'.join(a['name'] for a in self.info['artists']),
                    'id': str(self.info['id']),
                    'privilege': -1,
                },
                image=image_bytes,
                music_bin=music_bytes,
                lyric='',
            )
            if not favorites_manager.addSong(chosen, storable):
                InfoBar.warning(
                    'Folder not found',
                    f"Folder '{chosen}' may have been removed",
                    parent=self._mwindow,
                    duration=3000,
                )
                return

            event_bus.emit(FAVORITES_CHANGED, chosen)

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
            detail = get_backend().get_track_detail(str(self.info['id']))
            img_url = detail['cover_url']
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
        plp: PlaylistPage | None = None,
        parent=None,
        lazy: bool = False,
        sortable: bool = True,
    ):
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self.storable = storable
        self._dp: PlayingPage = dp  # type: ignore
        self._mwindow: MainWindow = mwindow  # type: ignore
        self._plp: PlaylistPage = plp  # type: ignore
        self.load = False

        self.setWindowOpacity(0)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        if sortable:
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
        if storable.image_cached():
            return

        lock = _get_image_download_lock(storable.id)
        if not lock.acquire(blocking=False):
            return
        try:
            if storable.image_cached():
                return
            try:
                detail = get_backend().get_track_detail(storable.id)
                image_url = detail['cover_url']
                image_bytes = requests.get(image_url).content
            except Exception as e:
                self._logger.warning(
                    f'failed to auto-download image for {storable.id}: {e}'
                )
                return

            if not image_bytes:
                return
            storable._write_cache(image_bytes, IMAGE_DATA_DIR, 'image_cache_hash')
            favorites_manager._save()
            if self._mwindow:
                self._mwindow.addScheduledTask(
                    lambda s=storable: event_bus.emit(IMAGE_ASSET_PERSISTED, s)
                )
            else:
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
        self._plp.movePlaylistSong(self.storable, delta)

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
        if not self._dp.playing_manager.ensureAssets(self.storable):
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
                detail = get_backend().get_track_detail(self.storable.id)
                image_url = detail['cover_url']

                image_bytes = requests.get(image_url).content

                album = detail['album_name']
                track_number = f'{detail["cd"]}/{detail["track_no"]}'
                publish_time = detail.get('publish_time', 0)
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
        playlist = self._dp.playing_manager.playlist
        try:
            index = playlist.index(self.storable)
        except ValueError:
            return

        insert_index = index + 1
        playlist.insert(insert_index, self.storable)
        if self._dp.playing_manager.current_index >= insert_index:
            self._dp.playing_manager.current_index += 1
        self._dp.playing_manager.refreshRandom()
        event_bus.emit(PLAYLIST_CHANGED)
        self._plp.lst.setCurrentRow(insert_index)

    def _removeSong(self):
        playlist = self._dp.playing_manager.playlist
        for i, storable in enumerate(playlist):
            if storable.id == self.storable.id:
                playlist.remove(playlist[i])
                break

        event_bus.emit(PLAYLIST_CHANGED)

        if self._dp.cur:
            if self.storable.id == self._dp.cur.storable.id:
                event_bus.emit(PLAY_SONG_AT_INDEX, self._dp.current_index)


class FavoriteSongCard(_SongCardItem):
    def __init__(
        self,
        storable,
        dp,
        mwindow,
        plp,
        remove_callback=None,
        move_callback=None,
        parent=None,
        lazy=False,
    ):
        super().__init__(storable, dp, mwindow, plp, parent, lazy=lazy)
        self._remove_callback = remove_callback
        self._move_callback = move_callback

    def moveRequested(self, delta: int):
        if self._move_callback:
            self._move_callback(self.storable, delta)

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
        if not self._dp.playing_manager.ensureAssets(self.storable):
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
                detail = get_backend().get_track_detail(self.storable.id)
                image_url = detail['cover_url']

                image_bytes = requests.get(image_url).content

                album = detail['album_name']
                track_number = f'{detail["cd"]}/{detail["track_no"]}'
                publish_time = detail.get('publish_time', 0)
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


class CloudFavoriteSongCard(_SongCardItem):
    def __init__(
        self,
        storable,
        dp,
        mwindow,
        plp,
        remove_callback=None,
        parent=None,
        lazy=False,
    ):
        super().__init__(
            storable, dp, mwindow, plp, parent=parent, lazy=lazy, sortable=False
        )
        self._remove_callback = remove_callback

    def contextMenuEvent(self, event):
        menu = RoundMenu(parent=self)

        export = Action('Export', menu)
        export.setIcon(getQIcon('export'))
        export.triggered.connect(lambda: self._exportSong())

        add = Action('Add to Local Folder', menu)
        add.setIcon(getQIcon('add'))
        add.triggered.connect(self._addToLocalFolder)

        remove = Action('Remove', menu)
        remove.setIcon(getQIcon('remove'))
        remove.triggered.connect(self._removeSong)

        menu.addActions([export, add, remove])

        menu.exec(event.globalPos(), aniType=MenuAnimationType.DROP_DOWN)

    def _removeSong(self):
        if self._remove_callback:
            self._remove_callback()

    def _addToLocalFolder(self):
        from core.dialogs import get_value_bylist, get_text_lineedit

        folder_names = [f['folder_name'] for f in favorites_manager.folders]
        if not folder_names:
            folder_names.append('Create New Folder...')
            chosen = get_text_lineedit(
                self._mwindow, 'Create New Folder', 'My first folder', self._mwindow
            )
        else:
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
            if not chosen:
                return
            favorites_manager.addFolder(chosen)

        storable = self.storable

        def _add(folder_name: str) -> None:
            if favorites_manager.addSong(folder_name, storable):
                event_bus.emit(FAVORITES_CHANGED, folder_name)
                InfoBar.success(
                    'Added',
                    f"Added {storable.name} to '{folder_name}'",
                    parent=self._mwindow,
                    duration=3000,
                )

        if storable.image_cached() and storable.audio_cached():
            _add(chosen)
            return

        def _do_download() -> None:
            backend = get_backend()
            detail = backend.get_track_detail(str(storable.id))
            image_url = detail['cover_url']
            image_bytes = requests.get(image_url).content
            if not storable.image_cached():
                storable.cache_image(image_bytes)

            audio = backend.get_track_audio(str(storable.id), bitrate=3200 * 1000)
            music_url = audio['url']

            def _on_music_done(music_bytes: bytes) -> None:
                if not storable.audio_cached():
                    storable.cache_audio(music_bytes)
                if self._mwindow is not None:
                    self._mwindow.addScheduledTask(lambda c=chosen: _add(c))

            downloadWithMultiThreading(music_url, {}, {}, None, _on_music_done)

        threading.Thread(target=_do_download, daemon=True).start()

    def _exportSong(self):
        if not self._dp.playing_manager.ensureAssets(self.storable):
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
        if not export_path:
            return
        content = f.read()
        try:
            saveSongWithInformations(
                export_path, content, self.storable, self.storable.get_image_bytes()
            )
        except Exception as e:
            InfoBar.error('Error', f'{e}', duration=-1, parent=self._mwindow)
            return

        InfoBar.success(
            'Done',
            f'Song exported to {os.path.basename(os.path.dirname(export_path))}',
            parent=self._mwindow,
        )
        self._remove_callback = remove_callback

    def contextMenuEvent(self, event):
        menu = RoundMenu(parent=self)

        export = Action('Export', menu)
        export.setIcon(getQIcon('export'))
        export.triggered.connect(lambda: self._exportSong())

        add = Action('Add to Local Folder', menu)
        add.setIcon(getQIcon('add'))
        add.triggered.connect(self._addToLocalFolder)

        remove = Action('Remove', menu)
        remove.setIcon(getQIcon('remove'))
        remove.triggered.connect(self._removeSong)

        menu.addActions([export, add, remove])

        menu.exec(event.globalPos(), aniType=MenuAnimationType.DROP_DOWN)

    def _removeSong(self):
        if self._remove_callback:
            self._remove_callback()

    def _exportSong(self):
        if not self._dp.playing_manager.ensureAssets(self.storable):
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
                detail = get_backend().get_track_detail(self.storable.id)
                image_url = detail['cover_url']

                image_bytes = requests.get(image_url).content

                album = detail['album_name']
                track_number = f'{detail["cd"]}/{detail["track_no"]}'
                publish_time = detail.get('publish_time', 0)
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
