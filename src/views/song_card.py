from __future__ import annotations

import logging

import os
import threading
from typing import Callable, TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from views.main_window import MainWindow
    from views.playlist_page import PlaylistPage
    from views.playing_page import PlayingPage

from imports import (
    FAVORITES_CHANGED,
    IMAGE_ASSET_PERSISTED,
    MWINDOW_REFRESH_FOLDERS,
    PLAYLIST_CHANGED,
    PLAY_SONG_AT_INDEX,
    STORABLE_COUNT_CHANGED,
    QSizePolicy,
    QSpacerItem,
    Qt,
    Signal,
    event_bus,
    bindText,
    tr,
)
from imports import QImage, QMouseEvent, QPixmap
from imports import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    CheckBox,
    FluentIcon,
    IndeterminateProgressRing,
    InfoBar,
    MenuAnimationType,
    MessageBoxBase,
    PrimaryToolButton,
    RoundMenu,
    SubtitleLabel,
    TransparentPushButton,
    TransparentToolButton,
)

from core.models import (
    IMAGE_DATA_DIR,
    MUSIC_DATA_DIR,
    CloudFolderInfo,
    SearchSongInfo,
    SongDetail,
    SongInfo,
    SongStorable,
    getCachedHashes
)
from core.icons import bindIcon, getQIcon
from core.downloader import (
    asyncTask,
)
from core.soundfile import getSongFormat, saveSongWithInformation
import requests
from core.favorites import favorites_manager
from core.backend import getBackend
from core.app_context import AppContext
from views.list_widget import SListWidget
from views.folder_card import CloudFolderCard, LocalFolderCard


_image_download_locks: dict[str, threading.Lock] = {}
SONG_CARD_HEIGHT = 70


def _artist_names_text(storable: SongStorable) -> str:
    return '、'.join(artist.name for artist in storable.artists)


def _export_default_path(storable: SongStorable, fmt: str) -> str:
    artists_text = _artist_names_text(storable)
    if artists_text:
        return f'./{storable.name} - {artists_text}{fmt}'
    return f'./{storable.name}{fmt}'


def _get_image_download_lock(song_id: str) -> threading.Lock:
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
            duration=storable.duration,
        )
        self.detail: SongDetail = SongDetail(image_url='')
        self.storable: SongStorable = storable


class FolderSelectDialog(MessageBoxBase):
    def __init__(self, parent, mwindow, song_id: str | list[str]):
        super().__init__(parent)
        self._mwindow = mwindow
        self._cloud_playlists: list[CloudFolderInfo] = []
        song_ids = [song_id] if isinstance(song_id, str) else song_id

        self.title_label = SubtitleLabel()
        bindText(self.title_label, 'song_card.add_to_folder')
        self.viewLayout.addWidget(self.title_label)

        self.list_widget = SListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list_widget.setFixedWidth(int(parent.width() * 0.6))
        self.viewLayout.addWidget(self.list_widget)

        local_folders = [
            f
            for f in favorites_manager.folders
            if any(
                not any(s.id == selected_id for s in f.songs)
                for selected_id in song_ids
            )
        ]

        if local_folders:
            self.list_widget.addItem(QListWidgetItem(tr('song_card.local')))
            for folder in local_folders:
                card = LocalFolderCard(folder, self.list_widget.width())
                card.clicked.connect(
                    lambda f=folder: self._select('local', f.folder_name, None)
                )
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, folder)
                item.setSizeHint(card.sizeHint())
                self.list_widget.addItem(item)
                self.list_widget.setItemWidget(item, card)

        create_item = QListWidgetItem()
        create_btn = TransparentPushButton(FluentIcon.ADD_TO, '')
        bindText(create_btn, 'song_card.create_new_folder')
        create_btn.clicked.connect(self._selectCreateNew)
        create_item.setSizeHint(create_btn.sizeHint())
        self.list_widget.addItem(create_item)
        self.list_widget.setItemWidget(create_item, create_btn)

        logged = getBackend().loggedIn()
        if logged:
            self.list_widget.addItem(QListWidgetItem(tr('song_card.cloud')))
            self._cloud_section_idx = self.list_widget.count()
            loading_item = QListWidgetItem(tr('song_card.loading'))
            self.list_widget.addItem(loading_item)
            threading.Thread(target=self._loadCloudPlaylists, daemon=True).start()

        self.yesButton.hide()
        self._selected: tuple[Literal['local', 'cloud'], str, str | None] | None = None

    def _select(self, sel_type: Literal['local', 'cloud'], name, cloud_id):
        self._selected = (sel_type, name, cloud_id)
        self.accept()

    def _selectCreateNew(self):
        self._selected = ('local', '+ Create New Folder...', None)
        self.accept()

    def _loadCloudPlaylists(self):
        try:
            playlists = getBackend().getUserPlaylists()
            self._mwindow.ctx.addScheduledTask(lambda: self._onCloudLoaded(playlists))
        except Exception:
            self._mwindow.ctx.addScheduledTask(
                lambda: self.list_widget.addItem(
                    QListWidgetItem(tr('song_card.failed_to_load'))
                )
            )

    def _onCloudLoaded(self, playlists: list[CloudFolderInfo]):
        self._cloud_playlists = playlists
        for _ in range(self.list_widget.count() - self._cloud_section_idx):
            self.list_widget.takeItem(self._cloud_section_idx)
        width = self.list_widget.width()
        ctx = self._mwindow.ctx
        for pl in playlists:
            card = CloudFolderCard(pl, width, ctx)
            card.clicked.connect(
                lambda f=pl: self._select('cloud', f.folder_name, str(f.id))
            )
            item = QListWidgetItem()
            item.setSizeHint(card.sizeHint())
            item.setData(Qt.ItemDataRole.UserRole, pl)
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, card)

    def getSelectedFolderInfo(
        self,
    ) -> tuple[Literal['local', 'cloud'], str, str | None] | None:
        return self._selected


class SearchSongCard(QWidget):
    imageLoaded = Signal(bytes)

    def __init__(self, info: SearchSongInfo, play_callback: Callable, ctx: AppContext) -> None:
        super().__init__()
        self.info = info
        self._play_callback = play_callback
        self.ctx = ctx
        self._mwindow = self.ctx.main_window

        self.detail = SongDetail(image_url='')

        global_layout = QVBoxLayout()
        top_layout = QHBoxLayout()

        self.img_label = QLabel()
        self.img_label.setFixedSize(100, 100)
        top_layout.addWidget(self.img_label)
        self.img_label.hide()
        self.ring = IndeterminateProgressRing()
        self.ring.setFixedSize(100, 100)
        top_layout.addWidget(self.ring)
        artists_text = '、'.join(a.name for a in info.artists)
        title_label = SubtitleLabel(info.name)

        topright_layout = QVBoxLayout()
        topright_layout.addSpacerItem(
            QSpacerItem(
                0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
        )
        topright_layout.addWidget(title_label)
        artists_label = QLabel(artists_text)
        artists_label.setWordWrap(True)
        topright_layout.addWidget(artists_label)
        topright_layout.addSpacerItem(
            QSpacerItem(
                0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
        )

        top_layout.addLayout(topright_layout)

        bottom_layout = QHBoxLayout()

        self.playbtn = PrimaryToolButton(FluentIcon.SEND)
        self.playbtn.setEnabled(True)
        bottom_layout.addWidget(self.playbtn)
        self.playbtn.clicked.connect(self.play)

        self.favbtn = TransparentToolButton()
        bindIcon(self.favbtn, 'fav')
        self.favbtn.setEnabled(True)
        bottom_layout.addWidget(self.favbtn)
        self.favbtn.clicked.connect(self.addToFavorites)

        bottom_layout.addSpacerItem(
            QSpacerItem(
                0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
        )

        global_layout.addLayout(top_layout)
        global_layout.addLayout(bottom_layout)

        self.setLayout(global_layout)

        self.load = False
        self.imageLoaded.connect(self.onImageLoaded)

    def play(self):
        self._play_callback(self)

    def addToFavorites(self):
        song_id = str(self.info.id)
        logged = getBackend().loggedIn()
        all_local_have = bool(favorites_manager.folders) and all(
            any(s.id == song_id for s in f.songs) for f in favorites_manager.folders
        )
        if all_local_have and not logged:
            InfoBar.info(
                tr('song_card.already_saved'),
                tr('song_card.this_song_is_already_in_all_folders'),
                parent=self._mwindow,
                duration=3000,
            )
            return

        dialog = FolderSelectDialog(self._mwindow, self._mwindow, song_id)
        reply = dialog.exec()
        if not reply:
            return
        selection = dialog.getSelectedFolderInfo()
        if selection is None:
            return

        folder_type, folder_name, cloud_id = selection

        if folder_type == 'cloud' and cloud_id:
            if not getBackend().editPlaylist('add', [str(self.info.id)], cloud_id):
                InfoBar.warning(
                    tr('song_card.session_expired'),
                    tr('song_card.please_re_login_to_perform_this_action'),
                    parent=self._mwindow,
                    duration=5000,
                )
                return
            event_bus.emit(MWINDOW_REFRESH_FOLDERS)
            InfoBar.success(
                tr('song_card.favorited'),
                tr(
                    'song_card.added_song_name_to_cloud_playlist_folder_name',
                    song_name=self.info.name,
                    folder_name=folder_name,
                ),
                parent=self._mwindow,
                duration=3000,
            )
            return

        if folder_name == '+ Create New Folder...':
            from core.dialogs import getTextLineedit

            folder_name = getTextLineedit(
                tr('song_card.create_new_folder_2'),
                tr('song_card.my_first_folder'),
                tr('song_card.my_first_folder'),
                self._mwindow,
            )
            if not folder_name:
                return
            favorites_manager.addFolder(folder_name)

        storable = SongStorable(
            info=SongInfo(
                name=self.info.name,
                artists=self.info.artists,
                id=str(self.info.id),
                privilege=-1,
                duration=self.info.duration,
            ),
            image=None,
            image_cache_hash=getCachedHashes(song_id).get('image_cache_hash', ''),
            content_cache_hash=getCachedHashes(song_id).get('content_cache_hash', '')
        )
        if not favorites_manager.addSong(folder_name, storable):
            InfoBar.warning(
                tr('song_card.folder_not_found'),
                tr(
                    'song_card.folder_folder_name_may_have_been_removed',
                    folder_name=folder_name,
                ),
                parent=self._mwindow,
                duration=3000,
            )
            return

        event_bus.emit(FAVORITES_CHANGED, folder_name)
        event_bus.emit(MWINDOW_REFRESH_FOLDERS)

        InfoBar.success(
            tr('song_card.favorited'),
            tr(
                'song_card.added_song_name_to_folder_name',
                song_name=self.info.name,
                folder_name=folder_name,
            ),
            parent=self._mwindow,
            duration=3000,
        )

    def loadDetailAndImage(self):
        self.load = True

        def _do():
            detail = getBackend().getTrackDetail(str(self.info.id))
            img_url = detail.cover_url
            self.detail.image_url = img_url

            img_bytes = requests.get(img_url).content

            self.imageLoaded.emit(img_bytes)

        asyncTask(_do, (), self._mwindow)

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
    queued = Signal(object)
    selectionChanged = Signal(object, bool)

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
        self.selection_mode = False

        self.setWindowOpacity(0)
        self.setMinimumHeight(SONG_CARD_HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)

        self.select_box = CheckBox()
        self.select_box.hide()
        self.select_box.setFixedSize(24, 24)
        self.select_box.stateChanged.connect(self._onSelectionChanged)
        layout.addWidget(self.select_box)

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
        self.img_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.img_label)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        text_layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        )
        title_label = SubtitleLabel(storable.name)
        title_label.setWordWrap(True)
        text_layout.addWidget(title_label)
        artists_label = QLabel('、'.join([obj.name for obj in storable.artists]))
        artists_label.setWordWrap(True)
        text_layout.addWidget(artists_label)
        text_layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        )
        layout.addLayout(text_layout, 1)

        layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        )

        self.count_label = SubtitleLabel(str(storable.count))
        suffix_label = QLabel()
        bindText(suffix_label, 'song_card.played_times')

        layout.addWidget(self.count_label)
        layout.addWidget(suffix_label)

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

        event_bus.subscribe(STORABLE_COUNT_CHANGED, self._on_storable_count_changed)

    def _on_storable_count_changed(self, storable: SongStorable):
        if storable != self.storable:
            return

        self.storable.count = storable.count
        self.count_label.setText(str(storable.count))

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

    def setSelectionMode(self, state: bool) -> None:
        self.selection_mode = state
        self.select_box.setVisible(state)
        if not state:
            self.select_box.setChecked(False)

    def isSelected(self) -> bool:
        return self.select_box.isChecked()

    def setSelected(self, state: bool) -> None:
        self.select_box.setChecked(state)

    def _onSelectionChanged(self, state: int) -> None:
        self.selectionChanged.emit(self.storable, self.select_box.isChecked())

    def _auto_download_missing_image(self):
        storable = self.storable
        if storable.imageCached():
            return

        lock = _get_image_download_lock(storable.id)
        if not lock.acquire(blocking=False):
            return
        try:
            if storable.imageCached():
                return
            try:
                detail = getBackend().getTrackDetail(storable.id)
                image_url = detail.cover_url
                image_bytes = requests.get(image_url).content
            except Exception as e:
                self._logger.warning(
                    f'failed to auto-download image for {storable.id}: {e}'
                )
                return

            if not image_bytes:
                return
            storable._writeCache(image_bytes, IMAGE_DATA_DIR, 'image_cache_hash')
            favorites_manager._save()
            if self._mwindow:
                self._mwindow.ctx.addScheduledTask(
                    lambda s=storable: event_bus.emit(IMAGE_ASSET_PERSISTED, s)
                )
            else:
                event_bus.emit(IMAGE_ASSET_PERSISTED, storable)
        finally:
            lock.release()

    def loadImage(self):
        if self._mwindow is None:
            return

        result: dict[str, bytes] = {}

        def _decode():
            try:
                image_bytes = self.storable.getImageBytes()
            except FileNotFoundError:
                return
            result['image_bytes'] = image_bytes

        def _finish():
            image_bytes = result.get('image_bytes')
            if image_bytes is None:
                return

            def _apply_pixmap():
                if not self.load:
                    return
                try:
                    self.img_label.objectName()
                except RuntimeError:
                    return
                image = QImage()
                image.loadFromData(image_bytes)
                if image.isNull():
                    return
                pixmap = QPixmap.fromImage(image)
                if not pixmap.isNull():
                    scaled = pixmap.scaled(
                        self.img_label.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self.img_label.setPixmap(scaled)

            self._mwindow.ctx.addScheduledTask(_apply_pixmap)

        try:
            asyncTask(_decode, (), self._mwindow, _finish)
        except Exception:
            pass

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.selection_mode:
                self.setSelected(not self.isSelected())
                return super().mousePressEvent(event)
            cover_rect = self.img_label.geometry()
            if cover_rect.contains(event.pos()):
                self.queued.emit(self.storable)
            else:
                self.clicked.emit(self.storable)
        return super().mousePressEvent(event)

    def _addTo(self):
        song_id = str(self.storable.id)
        logged = getBackend().loggedIn()
        all_local_have = bool(favorites_manager.folders) and all(
            any(s.id == song_id for s in f.songs) for f in favorites_manager.folders
        )
        if all_local_have and not logged:
            InfoBar.info(
                tr('song_card.already_saved'),
                tr('song_card.this_song_is_already_in_all_folders'),
                parent=self._mwindow,
                duration=3000,
            )
            return

        dialog = FolderSelectDialog(self._mwindow, self._mwindow, song_id)
        reply = dialog.exec()
        if not reply:
            return
        selection = dialog.getSelectedFolderInfo()
        if selection is None:
            return

        folder_type, folder_name, cloud_id = selection

        if folder_type == 'local':
            favorites_manager.addSong(folder_name, self.storable)
        elif folder_type == 'cloud':
            getBackend().editPlaylist('add', [song_id], str(cloud_id))
        InfoBar.info(
            tr('song_card.added'),
            tr(
                'song_card.song_song_name_has_been_added_to_folder_name',
                song_name=self.storable.name,
                folder_name=folder_name,
            ),
            parent=self._mwindow,
            duration=3000,
        )
        event_bus.emit(MWINDOW_REFRESH_FOLDERS)


class PlaylistSongCard(_SongCardItem):
    def moveRequested(self, delta: int):
        self._plp.movePlaylistSong(self.storable, delta)

    def contextMenuEvent(self, event):
        menu = RoundMenu(parent=self)

        export = Action(tr('song_card.export'), menu)
        export.setIcon(getQIcon('export'))
        repeat = Action(tr('song_card.repeat'), menu)
        repeat.setIcon(FluentIcon.SYNC.icon())
        rm = Action(tr('song_card.remove'), menu)
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
                tr('song_card.export_song'),
                _export_default_path(self.storable, getSongFormat(f.read())),
                tr('song_card.song_files_mp3_m4a_flac_wav_ogg_opus'),
            )

        if export_path:

            def _export():
                detail = getBackend().getTrackDetail(self.storable.id)
                image_url = detail.cover_url

                image_bytes = requests.get(image_url).content

                album = detail.album_name
                track_number = f'{detail.cd}/{detail.track_no}'
                publish_time = detail.publish_time
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
                    saveSongWithInformation(
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
                    tr('song_card.export'),
                    tr(
                        'song_card.exported_song_song_name',
                        song_name=self.storable.name,
                    ),
                    parent=self._mwindow,
                    duration=5000,
                )

            asyncTask(_export, (), self._mwindow, _final)

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
        sortable=True,
    ):
        super().__init__(
            storable, dp, mwindow, plp, parent, lazy=lazy, sortable=sortable
        )
        self._remove_callback = remove_callback
        self._move_callback = move_callback

    def moveRequested(self, delta: int):
        if self._move_callback:
            self._move_callback(self.storable, delta)

    def contextMenuEvent(self, event):
        menu = RoundMenu(parent=self)

        export = Action(tr('song_card.export'), menu)
        export.setIcon(getQIcon('export'))
        export.triggered.connect(lambda: self._exportSong())

        remove = Action(tr('song_card.remove'), menu)
        remove.setIcon(getQIcon('remove'))
        remove.triggered.connect(self._removeSong)

        addto = Action(tr('song_card.add_to'), menu)
        addto.setIcon(getQIcon('add'))
        addto.triggered.connect(lambda: self._addTo())

        menu.addActions([export, addto, remove])

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
                tr('song_card.export_song'),
                _export_default_path(self.storable, getSongFormat(f.read())),
                tr('song_card.song_files_mp3_m4a_flac_wav_ogg_opus'),
            )

        if export_path:

            def _export():
                detail = getBackend().getTrackDetail(self.storable.id)
                image_url = detail.cover_url

                image_bytes = requests.get(image_url).content

                album = detail.album_name
                track_number = f'{detail.cd}/{detail.track_no}'
                publish_time = detail.publish_time
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
                    saveSongWithInformation(
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
                    tr('song_card.export'),
                    tr(
                        'song_card.exported_song_song_name',
                        song_name=self.storable.name,
                    ),
                    parent=self._mwindow,
                    duration=5000,
                )

            asyncTask(_export, (), self._mwindow, _final)


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

        export = Action(tr('song_card.export'), menu)
        export.setIcon(getQIcon('export'))
        export.triggered.connect(lambda: self._exportSong())

        remove = Action(tr('song_card.remove'), menu)
        remove.setIcon(getQIcon('remove'))
        remove.triggered.connect(self._removeSong)

        addto = Action(tr('song_card.add_to'), menu)
        addto.setIcon(getQIcon('add'))
        addto.triggered.connect(lambda: self._addTo())

        menu.addActions([export, addto, remove])

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
                tr('song_card.export_song'),
                _export_default_path(self.storable, getSongFormat(f.read())),
                tr('song_card.song_files_mp3_m4a_flac_wav_ogg_opus'),
            )

        if export_path:

            def _export():
                detail = getBackend().getTrackDetail(self.storable.id)
                image_url = detail.cover_url

                image_bytes = requests.get(image_url).content

                album = detail.album_name
                track_number = f'{detail.cd}/{detail.track_no}'
                publish_time = detail.publish_time
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
                    saveSongWithInformation(
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
                    tr('song_card.export'),
                    tr(
                        'song_card.exported_song_song_name',
                        song_name=self.storable.name,
                    ),
                    parent=self._mwindow,
                    duration=5000,
                )

            asyncTask(_export, (), self._mwindow, _final)
