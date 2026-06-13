from __future__ import annotations

import logging
import threading

_logger = logging.getLogger(__name__)

from core.app_context import AppContext
from imports import (
    FAVORITES_CHANGED,
    MWINDOW_REFRESH_FOLDERS,
    PLAYLIST_CHANGED,
    PLAY_STORABLE,
    PushButton,
    QLabel,
    QPixmap,
    QPoint,
    QSize,
    Qt,
    QTimer,
    event_bus,
)
from imports import (
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
    QGraphicsOpacityEffect,
    QPropertyAnimation,
    QEasingCurve,
)
from qfluentwidgets import (
    FlowLayout,
    InfoBar,
    TitleLabel,
)
from views.list_widget import SListWidget

from core.models import CloudFolderInfo, LocalFolderInfo, SongStorable
from core.favorites import favorites_manager
from core.backend import getBackend

from views.song_card import CloudFavoriteSongCard, FavoriteSongCard, _SongCardItem


class FavoritesPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        lw = ctx.launch_window
        if lw:
            lw.top('Initializing favorites page...')
        self.setObjectName('favorites_page')

        global_layout = QVBoxLayout(self)

        self.title_label = TitleLabel('None')
        buttons_layout = FlowLayout()
        self.reppl_btn = PushButton('Replace Playlist')
        self.reppl_btn.clicked.connect(self.replacePlaylist)
        buttons_layout.addWidget(self.reppl_btn)
        self.addpl_btn = PushButton('Add to Playlist')
        self.addpl_btn.clicked.connect(self.addFolderToPlaylist)
        buttons_layout.addWidget(self.addpl_btn)

        global_layout.addWidget(self.title_label)
        global_layout.addLayout(buttons_layout)

        self.song_viewer = SListWidget()
        self.song_viewer.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        global_layout.addWidget(self.song_viewer, 1)

        self._song_cards: list[_SongCardItem] = []
        self._lazy_timer = QTimer(self)
        self._lazy_timer.timeout.connect(self._checkVisibleCards)
        self._lazy_timer.start(50)

        self.is_cloud = False
        self.curr_folder: LocalFolderInfo | None = None
        self.curr_cloud_folder: CloudFolderInfo | None = None
        self.curr_cloud_songs: list[SongStorable] = []
        self._cloud_loading = False

        event_bus.subscribe(FAVORITES_CHANGED, self._onFavoritesChanged)

    def _onFavoritesChanged(self, folder_name=None):
        if self.is_cloud:
            return
        if (
            folder_name
            and self.curr_folder
            and folder_name != self.curr_folder.folder_name
        ):
            return
        self.refresh()

    @property
    def _dp(self):
        return self.ctx.playing_page

    @property
    def _mwindow(self):
        return self.ctx.main_window

    @property
    def _plp(self):
        return self.ctx.playlist_page

    @property
    def _pm(self):
        return self.ctx.playing_manager

    def _songs(self) -> list[SongStorable]:
        if self.is_cloud:
            return self.curr_cloud_songs
        elif self.curr_folder:
            return self.curr_folder.songs
        return []

    def displayEmpty(self):
        self.title_label.setText('None')
        self.song_viewer.clear()

    def setDisplayFolder(self, folder: LocalFolderInfo | CloudFolderInfo):
        if isinstance(folder, CloudFolderInfo):
            self.is_cloud = True
            self.curr_cloud_folder = folder
            self.curr_folder = None
            self._loadCloudTracks(folder)
        else:
            self.is_cloud = False
            self.curr_folder = folder
            self.curr_cloud_folder = None
            self.curr_cloud_songs = []
            self.refresh()

    def _loadCloudTracks(self, folder: CloudFolderInfo):
        if self._cloud_loading:
            return
        self._cloud_loading = True
        result: list[SongStorable] = []
        folder_id = folder.id
        mwindow = self._mwindow

        def _fetch():
            nonlocal result
            result = getBackend().getPlaylistTracks(folder_id)
            mwindow.addScheduledTask(_apply)

        def _apply():
            self._cloud_loading = False
            if (
                self.is_cloud
                and self.curr_cloud_folder
                and self.curr_cloud_folder.id == folder_id
            ):
                self.curr_cloud_songs = result
                self.refresh()

        threading.Thread(target=_fetch, daemon=True).start()

    def _get_favs(self):
        return favorites_manager.folders

    def _checkVisibleCards(self):
        for card in self._song_cards:
            if card.load:
                continue
            idx = self._song_cards.index(card)
            item = self.song_viewer.item(idx)
            if item is None:
                continue
            item_rect = self.song_viewer.visualItemRect(item)
            viewport_rect = self.song_viewer.viewport().rect()
            if viewport_rect.intersects(item_rect):
                card.loadDetailAndImage()

    def refresh(self):
        self._song_cards = []
        self.song_viewer.clear()

        if self.is_cloud and self.curr_cloud_folder:
            self.title_label.setText(self.curr_cloud_folder.folder_name)
            songs = self.curr_cloud_songs
        elif self.curr_folder:
            folder_name = self.curr_folder.folder_name
            for f in favorites_manager.folders:
                if f.folder_name == folder_name:
                    self.curr_folder = f
                    break
            self.title_label.setText(self.curr_folder.folder_name)
            songs = self.curr_folder.songs
        else:
            return

        for song in songs:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, song)
            item.setSizeHint(QSize(0, 62))

            if self.is_cloud:
                card = CloudFavoriteSongCard(
                    song,
                    self._dp,
                    self._mwindow,
                    self._plp,
                    remove_callback=lambda s=song: self.deleteCloudSong(s),
                    lazy=True,
                )
            else:
                card = FavoriteSongCard(
                    song,
                    self._dp,
                    self._mwindow,
                    self._plp,
                    remove_callback=lambda s=song: self.deleteSong(s),
                    move_callback=self.moveSong,
                    lazy=True,
                )
            card.clicked.connect(lambda s: self._replaceAndPlay(s))
            card.queued.connect(lambda s: self._queueAfterCurrent(s))
            self.song_viewer.addItem(item)
            self.song_viewer.setItemWidget(item, card)
            self._song_cards.append(card)

    def _replaceAndPlay(self, song: SongStorable):
        self.replacePlaylist(False)
        event_bus.emit(PLAYLIST_CHANGED)
        event_bus.emit(PLAY_STORABLE, song)

    def _queueAfterCurrent(self, song: SongStorable):
        playlist = self._pm.playlist
        insert_index = self._pm.current_index + 2
        playlist.insert(insert_index, song)
        event_bus.emit(PLAYLIST_CHANGED)
        self._animateCoverFly(song)

    def _animateCoverFly(self, song: SongStorable):
        for card in list(self._song_cards):
            try:
                card.objectName()
            except RuntimeError:
                continue
            if card.storable is song:
                break
        else:
            return

        pixmap = card.img_label.pixmap()
        if not pixmap or pixmap.isNull():
            try:
                image_bytes = song.get_image_bytes()
                pixmap = QPixmap()
                pixmap.loadFromData(image_bytes)
                if pixmap.isNull():
                    return
            except Exception:
                return
            scaled = pixmap.scaled(
                50,
                50,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        else:
            scaled = pixmap

        fly_label = QLabel(self._mwindow)
        fly_label.setPixmap(scaled)
        fly_label.setFixedSize(50, 50)

        card_global = card.img_label.mapToGlobal(QPoint(0, 0))
        mwindow_global = self._mwindow.mapToGlobal(QPoint(0, 0))
        start_x = card_global.x() - mwindow_global.x()
        start_y = card_global.y() - mwindow_global.y()
        fly_label.move(start_x, start_y)
        fly_label.show()
        fly_label.raise_()

        end_x = self._mwindow.width() - 55
        end_y = self._mwindow.height() - 55

        mid_x = (start_x + end_x) / 2
        arc_height = 120 + abs(start_y - end_y) * 0.3
        mid_y = min(start_y, end_y) - arc_height

        pos_anim = QPropertyAnimation(fly_label, b'pos', self._mwindow)
        pos_anim.setDuration(500)
        pos_anim.setStartValue(QPoint(start_x, start_y))
        pos_anim.setEndValue(QPoint(end_x, end_y))
        pos_anim.setEasingCurve(QEasingCurve.Type.OutSine)

        keyframe_count = int(self.ctx.app.primaryScreen().refreshRate() * 0.5)
        for i in range(1, keyframe_count):
            t = i / keyframe_count
            x = (1 - t) ** 2 * start_x + 2 * (1 - t) * t * mid_x + t**2 * end_x
            y = (1 - t) ** 2 * start_y + 2 * (1 - t) * t * mid_y + t**2 * end_y
            pos_anim.setKeyValueAt(t, QPoint(int(x), int(y)))

        opacity_effect = QGraphicsOpacityEffect(fly_label)
        fly_label.setGraphicsEffect(opacity_effect)
        opacity_anim = QPropertyAnimation(opacity_effect, b'opacity', self._mwindow)
        opacity_anim.setDuration(500)
        opacity_anim.setStartValue(1.0)
        opacity_anim.setEndValue(0.0)
        opacity_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

        def _cleanup():
            fly_label.deleteLater()

        pos_anim.finished.connect(_cleanup)
        pos_anim.start()
        opacity_anim.start()

    def moveSong(self, song: SongStorable, delta: int):
        if self.is_cloud:
            return
        if not self.curr_folder:
            return
        songs = self.curr_folder.songs
        try:
            old_index = songs.index(song)
        except ValueError:
            return

        new_index = old_index + delta
        if new_index < 0 or new_index >= len(songs):
            return

        current_song = (
            songs[self._pm.current_index]
            if 0 <= self._pm.current_index < len(songs)
            else None
        )

        favorites_manager.moveSong(self.curr_folder.folder_name, song, delta)

        if current_song is not None:
            try:
                self._pm.current_index = songs.index(current_song)
            except ValueError:
                pass

        self.refresh()

    def deleteSong(self, song_storable: SongStorable):
        song_name = song_storable.name
        _logger.info('deleteSong: name=%r, id=%s', song_name, song_storable.id)
        if self.curr_folder:
            _logger.info(
                'curr_folder=%r, songs_count=%d',
                self.curr_folder.folder_name,
                len(self.curr_folder.songs),
            )

        reply = QMessageBox.question(
            self._mwindow,
            'Confirm Delete',
            f'Are you sure you want to delete song {song_name} from favorites?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        _logger.info('deleteSong: reply=%r', reply)

        if reply != QMessageBox.StandardButton.Yes:
            return

        _logger.info('deleteSong: calling removeSong(%r)', song_name)
        favorites_manager.removeSong(song_name)

        if self.curr_folder:
            _logger.info('after remove: songs_count=%d', len(self.curr_folder.songs))
        self.refresh()

        InfoBar.success(
            'Song deleted', f'Song {song_name} deleted', parent=self._mwindow
        )

        event_bus.emit(MWINDOW_REFRESH_FOLDERS)

    def deleteCloudSong(self, song_storable: SongStorable):
        if not self.curr_cloud_folder:
            return
        song_name = song_storable.name
        folder_name = self.curr_cloud_folder.folder_name

        reply = QMessageBox.question(
            self._mwindow,
            'Confirm Delete',
            f'Are you sure you want to delete song {song_name} from cloud folder \'{folder_name}\'?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            if not getBackend().editPlaylist(
                'del', [song_storable.id], self.curr_cloud_folder.id
            ):
                InfoBar.warning(
                    'Session expired',
                    'Please re-login to perform this action',
                    parent=self._mwindow,
                    duration=5000,
                )
                return
            self.curr_cloud_songs = [
                s for s in self.curr_cloud_songs if s.id != song_storable.id
            ]
            self.refresh()
            InfoBar.success(
                'Song deleted',
                f'Song {song_name} removed from cloud folder',
                parent=self._mwindow,
            )

            event_bus.emit(MWINDOW_REFRESH_FOLDERS)

    def replacePlaylist(self, tip=True):
        self._pm.playlist.clear()
        for song in self._songs():
            self._pm.playlist.append(song)
        event_bus.emit(PLAYLIST_CHANGED)
        folder_name = (
            self.curr_cloud_folder.folder_name
            if self.is_cloud and self.curr_cloud_folder
            else self.curr_folder.folder_name
            if self.curr_folder
            else ''
        )
        if tip:
            InfoBar.success(
                'Playlist replaced',
                f'Playlist replaced with {folder_name}',
                parent=self._mwindow,
            )

    def addFolderToPlaylist(self):
        added_count = 0
        for song in self._songs():
            if not any(s.name == song.name for s in self._pm.playlist):
                self._pm.playlist.append(song)
                added_count += 1

        if added_count > 0:
            event_bus.emit(PLAYLIST_CHANGED)
            InfoBar.success(
                'Songs added',
                f'Added {added_count} songs from favorites to playlist',
                parent=self._mwindow,
            )

        event_bus.emit(PLAYLIST_CHANGED)
