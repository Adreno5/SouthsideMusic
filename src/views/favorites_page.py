from __future__ import annotations

import logging
import threading

from core.app_context import AppContext
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QLabel
from qfluentwidgets import FluentIcon, PrimaryPushButton, PushButton
from core.i18n import bindText, tr
from services.events import Events, event_bus
from PySide6.QtCore import QEasingCurve, QPropertyAnimation
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    FlowLayout,
    InfoBar,
    MessageBox,
    PillToolButton,
    SearchLineEdit,
    TitleLabel,
)

from core.models import CloudFolderInfo, LocalFolderInfo, SongStorable
from core.favorites import favorites_manager
from core.backend import getBackend
from core.downloader import asyncTask
from core.icons import bindIcon

from views.song_card import (
    CloudFavoriteSongCard,
    FavoriteSongCard,
    FolderSelectDialog,
    _SongCardItem,
)
from views.song_list import SongListWidget

_logger = logging.getLogger(__name__)


class FavoritesPage(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        lw = ctx.launch_window
        if lw:
            lw.subtitle(tr('favorites_page.initializing_favorites_page'))
        self.setObjectName('favorites_page')
        self.is_cloud = False
        self.curr_folder: LocalFolderInfo | None = None
        self.curr_cloud_folder: CloudFolderInfo | None = None
        self.curr_cloud_songs: list[SongStorable] = []

        global_layout = QVBoxLayout(self)

        self.title_label = TitleLabel(tr('favorites_page.none'))
        buttons_layout = FlowLayout()
        self.playall_btn = PrimaryPushButton('')
        self.playall_btn.setIcon(FluentIcon.PLAY_SOLID)
        bindText(self.playall_btn, 'favorites_page.play_all')
        self.playall_btn.clicked.connect(self.playAll)
        buttons_layout.addWidget(self.playall_btn)
        self.addpl_btn = PushButton('')
        bindText(self.addpl_btn, 'favorites_page.add_to_playlist')
        self.addpl_btn.clicked.connect(self.addFolderToPlaylist)
        buttons_layout.addWidget(self.addpl_btn)
        self.rediscovery_button = PushButton(FluentIcon.HISTORY, '', self)
        bindText(self.rediscovery_button, 'rediscovery.entry_button')
        self.rediscovery_button.setEnabled(False)
        self.rediscovery_button.clicked.connect(self.openRediscovery)
        buttons_layout.addWidget(self.rediscovery_button)
        self.batch_btn = PillToolButton(self)
        self.batch_btn.setFixedSize(32, 32)
        self.batch_btn.setToolTip(tr('favorites_page.multiple_selection'))
        bindIcon(self.batch_btn, 'playlist_multiple_selection')
        self.batch_btn.toggled.connect(self.setBatchMode)
        buttons_layout.addWidget(self.batch_btn)

        global_layout.addWidget(self.title_label)
        global_layout.addLayout(buttons_layout)

        self.batch_widget = QWidget()
        batch_layout = QHBoxLayout(self.batch_widget)
        batch_layout.setContentsMargins(0, 0, 0, 0)
        self.selectall_btn = PushButton('')
        bindText(self.selectall_btn, 'favorites_page.select_all')
        bindIcon(self.selectall_btn, 'playlist_multiple_selection')
        self.selectall_btn.clicked.connect(self.selectAllSongs)
        batch_layout.addWidget(self.selectall_btn)
        self.clear_selection_btn = PushButton('')
        bindText(self.clear_selection_btn, 'favorites_page.clear')
        bindIcon(self.clear_selection_btn, 'clearall')
        self.clear_selection_btn.clicked.connect(self.clearSelection)
        batch_layout.addWidget(self.clear_selection_btn)
        self.batch_addpl_btn = PushButton('')
        bindText(self.batch_addpl_btn, 'favorites_page.add_to_playlist')
        bindIcon(self.batch_addpl_btn, 'playlist')
        self.batch_addpl_btn.clicked.connect(self.addSelectedToPlaylist)
        batch_layout.addWidget(self.batch_addpl_btn)
        self.batch_addto_btn = PushButton('')
        bindText(self.batch_addto_btn, 'favorites_page.add_to_folder')
        bindIcon(self.batch_addto_btn, 'add')
        self.batch_addto_btn.clicked.connect(self.addSelectedToFolder)
        batch_layout.addWidget(self.batch_addto_btn)
        self.batch_remove_btn = PushButton('')
        bindText(self.batch_remove_btn, 'favorites_page.remove')
        bindIcon(self.batch_remove_btn, 'remove')
        self.batch_remove_btn.clicked.connect(self.removeSelectedSongs)
        batch_layout.addWidget(self.batch_remove_btn)
        batch_layout.addStretch(1)
        self.batch_widget.hide()
        global_layout.addWidget(self.batch_widget)

        self.search_input = SearchLineEdit(self)
        self.search_input.setPlaceholderText(tr('favorites_page.search_placeholder'))
        self.search_input.setFixedHeight(33)
        self.search_input.textChanged.connect(self._onSearchTextChanged)
        global_layout.addWidget(self.search_input)

        self.song_viewer = SongListWidget(self._createSongCard)
        global_layout.addWidget(self.song_viewer, 1)

        self._batch_songs: list[SongStorable] = []

        self._cloud_loading = False
        self._batch_mode = False
        self._selected_song_ids: set[str] = set()
        self._favorites_refresh_seq = 0

        event_bus.subscribe(Events.FAVORITES_CHANGED, self._onFavoritesChanged)
        event_bus.subscribe(Events.LANGUAGE_CHANGED, self._refreshSearchPlaceholder)

        # self.playall_btn.setFixedSize(self.playall_btn.size() * 2)

    def _refreshSearchPlaceholder(self) -> None:
        self.search_input.setPlaceholderText(tr('favorites_page.search_placeholder'))

    def _onSearchTextChanged(self, _text: str) -> None:
        self.refresh()

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

        event_bus.emit(Events.MWINDOW_REFRESH_FOLDERS)

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

    def displayEmpty(self) -> None:
        self.rediscovery_button.setEnabled(False)
        self._favorites_refresh_seq += 1
        self._batch_songs = []
        self.title_label.setText(tr('favorites_page.none'))
        self.setBatchMode(False)
        self.batch_btn.setChecked(False)
        self.song_viewer.setSongs([])

    def setDisplayFolder(self, folder: LocalFolderInfo | CloudFolderInfo) -> None:
        if self._cloud_loading:
            self._cloud_loading = False
            event_bus.emit(Events.STOP_INTER_LOADING)
        self.setBatchMode(False)
        self.batch_btn.setChecked(False)
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        if isinstance(folder, CloudFolderInfo):
            self.displayEmpty()
            self.is_cloud = True
            self.curr_cloud_folder = folder
            self.curr_folder = None
            self.curr_cloud_songs = []
            self._loadCloudTracks(folder)
        else:
            self.is_cloud = False
            self.curr_folder = folder
            self.curr_cloud_folder = None
            self.curr_cloud_songs = []
            self.refresh()

    def _loadCloudTracks(self, folder: CloudFolderInfo) -> None:
        self._cloud_loading = True
        event_bus.emit(Events.START_INTER_LOADING)
        result: list[SongStorable] = []
        folder_id = folder.id
        refresh_seq = self._favorites_refresh_seq

        def _fetch() -> None:
            nonlocal result
            result = getBackend().getPlaylistTracks(folder_id)
            self.ctx.addScheduledTask(_apply)

        def _apply() -> None:
            if refresh_seq != self._favorites_refresh_seq:
                return
            self._cloud_loading = False
            if (
                self.is_cloud
                and self.curr_cloud_folder
                and self.curr_cloud_folder.id == folder_id
            ):
                self.curr_cloud_songs = result
                self.refresh()
                event_bus.emit(Events.STOP_INTER_LOADING)

        threading.Thread(target=_fetch, daemon=True).start()

    def _get_favs(self):
        return favorites_manager.folders

    def refresh(self) -> None:
        self._favorites_refresh_seq += 1

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
            songs = []

        songs = list(songs)
        self.rediscovery_button.setEnabled(bool(songs))
        self._selected_song_ids.intersection_update(str(song.id) for song in songs)
        self._syncBatchButtons()
        keyword = self.search_input.text().strip().lower()
        if keyword:
            songs = [
                song
                for song in songs
                if keyword in song.name.lower()
                or any(keyword in artist.name.lower() for artist in song.artists)
            ]
        self._batch_songs = songs
        self.song_viewer.setSongs(songs)

    def _createSongCard(self, song: SongStorable) -> _SongCardItem:
        if self.is_cloud:
            card = CloudFavoriteSongCard(
                song,
                self._dp,
                self._mwindow,
                self._plp,
                remove_callback=lambda s=song: self.deleteCloudSong(s),
                parent=self.song_viewer.viewport(),
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
                parent=self.song_viewer.viewport(),
                lazy=True,
            )
        card.clicked.connect(self._replaceAndPlay)
        card.queued.connect(self._queueAfterCurrent)
        card.setSelectionMode(self._batch_mode)
        card.setSelected(str(song.id) in self._selected_song_ids)
        card.selectionChanged.connect(self._onSongSelectionChanged)
        return card

    def setBatchMode(self, state: bool) -> None:
        self._batch_mode = state
        self.batch_widget.setVisible(state)
        if not state:
            self._selected_song_ids.clear()
        for card in self.song_viewer.cards():
            card.setSelectionMode(state)
        self._syncBatchButtons()

    def _onSongSelectionChanged(self, song: SongStorable, selected: bool) -> None:
        song_id = str(song.id)
        if selected:
            self._selected_song_ids.add(song_id)
        else:
            self._selected_song_ids.discard(song_id)
        self._syncBatchButtons()

    def _selectedSongs(self) -> list[SongStorable]:
        return [
            song
            for song in self._batch_songs
            if str(song.id) in self._selected_song_ids
        ]

    def _syncBatchButtons(self) -> None:
        selected_count = len(self._selected_song_ids)
        has_selection = selected_count > 0
        self.batch_addpl_btn.setEnabled(has_selection)
        self.batch_addto_btn.setEnabled(has_selection)
        self.batch_remove_btn.setEnabled(has_selection)
        self.clear_selection_btn.setEnabled(has_selection)
        self.selectall_btn.setEnabled(bool(self._songs()))

    def selectAllSongs(self) -> None:
        self._selected_song_ids = {str(song.id) for song in self._batch_songs}
        for card in self.song_viewer.cards():
            card.setSelected(True)
        self._syncBatchButtons()

    def clearSelection(self) -> None:
        self._selected_song_ids.clear()
        for card in self.song_viewer.cards():
            card.setSelected(False)
        self._syncBatchButtons()

    def _replaceAndPlay(self, song: SongStorable):
        self.playAll(False)
        event_bus.emit(Events.PLAY_PLAYLIST_STORABLE, song)

    def _queueAfterCurrent(self, song: SongStorable):
        playlist = self._pm.playlist
        insert_index = self._pm.current_index + 2
        playlist.insert(insert_index, song)
        event_bus.emit(Events.PLAYLIST_CHANGED)
        self._animateCoverFly(song)

    def _animateCoverFly(self, song: SongStorable):
        for card in self.song_viewer.cards():
            if card.storable is song:
                break
        else:
            return

        pixmap = card.img_label.pixmap()
        if not pixmap or pixmap.isNull():
            card.loadImage()
            return
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

        dialog = MessageBox(
            tr('favorites_page.confirm_delete'),
            tr(
                'favorites_page.are_you_sure_you_want_to_delete_song_song_name_from_favorites',
                song_name=song_name,
            ),
            self._mwindow,
        )
        dialog.cancelButton.setText(tr('favorites_page.cancel'))
        dialog.yesButton.setText(tr('favorites_page.delete'))
        dialog.yesButton.setStyleSheet(
            dialog.yesButton.styleSheet()
            + 'PrimaryPushButton { color: white; background: #c42b1c; border: none; }'
            'PrimaryPushButton:hover { background: #d13438; border: none; }'
            'PrimaryPushButton:pressed { background: #a4262c; border: none; }'
        )
        reply = dialog.exec()
        _logger.info('deleteSong: reply=%r', reply)

        if not reply:
            return

        _logger.info('deleteSong: calling removeSong(%r)', song_name)
        favorites_manager.removeSong(song_name)

        if self.curr_folder:
            _logger.info('after remove: songs_count=%d', len(self.curr_folder.songs))
        self.refresh()

        InfoBar.success(
            tr('favorites_page.song_deleted'),
            tr('favorites_page.song_song_name_deleted', song_name=song_name),
            parent=self._mwindow,
        )

        event_bus.emit(Events.MWINDOW_REFRESH_FOLDERS)

    def deleteCloudSong(self, song_storable: SongStorable):
        if not self.curr_cloud_folder:
            return
        song_name = song_storable.name
        folder_name = self.curr_cloud_folder.folder_name

        dialog = MessageBox(
            tr('favorites_page.confirm_delete'),
            tr(
                'favorites_page.are_you_sure_you_want_to_delete_song_song_name_from_cloud_folder_folde',
                song_name=song_name,
                folder_name=folder_name,
            ),
            self._mwindow,
        )
        dialog.yesButton.setStyleSheet(
            dialog.yesButton.styleSheet()
            + 'PrimaryPushButton { color: white; background: #c42b1c; border: none; }'
            'PrimaryPushButton:hover { background: #d13438; border: none; }'
            'PrimaryPushButton:pressed { background: #a4262c; border: none; }'
        )
        dialog.cancelButton.setText(tr('favorites_page.cancel'))
        dialog.yesButton.setText(tr('favorites_page.delete'))
        if dialog.exec():
            folder_id = self.curr_cloud_folder.id
            result: dict[str, bool] = {}

            def _delete() -> None:
                result['removed'] = getBackend().editPlaylist(
                    'del', [song_storable.id], folder_id
                )

            def _finish() -> None:
                if not result.get('removed', False):
                    InfoBar.warning(
                        tr('favorites_page.session_expired'),
                        tr('favorites_page.please_re_login_to_perform_this_action'),
                        parent=self._mwindow,
                        duration=5000,
                    )
                    return
                self.curr_cloud_songs = [
                    s for s in self.curr_cloud_songs if s.id != song_storable.id
                ]
                self.refresh()
                InfoBar.success(
                    tr('favorites_page.song_deleted'),
                    tr(
                        'favorites_page.song_song_name_removed_from_cloud_folder',
                        song_name=song_name,
                    ),
                    parent=self._mwindow,
                )
                event_bus.emit(Events.MWINDOW_REFRESH_FOLDERS)

            asyncTask(_delete, (), self, _finish)

    def addSelectedToPlaylist(self) -> None:
        selected = self._selectedSongs()
        if not selected:
            return

        added_count = 0
        for song in selected:
            if not any(s.name == song.name for s in self._pm.playlist):
                self._pm.playlist.append(song)
                added_count += 1

        if added_count > 0:
            event_bus.emit(Events.PLAYLIST_CHANGED)
            InfoBar.success(
                tr('favorites_page.songs_added'),
                tr(
                    'favorites_page.added_added_count_selected_songs_to_playlist',
                    added_count=added_count,
                ),
                parent=self._mwindow,
            )

    def addSelectedToFolder(self) -> None:
        selected = self._selectedSongs()
        if not selected:
            return

        dialog = FolderSelectDialog(
            self._mwindow, self._mwindow, [str(song.id) for song in selected]
        )
        reply = dialog.exec()
        if not reply:
            return
        selection = dialog.getSelectedFolderInfo()
        if selection is None:
            return

        folder_type, folder_name, cloud_id = selection
        if folder_type == 'local':
            if folder_name == '+ Create New Folder...':
                from core.dialogs import getTextLineedit

                folder_name = getTextLineedit(
                    tr('favorites_page.create_new_folder'),
                    tr('favorites_page.enter_name_of_your_new_folder'),
                    tr('favorites_page.my_folder'),
                    self._mwindow,
                )
                if not folder_name:
                    return
                favorites_manager.addFolder(folder_name)
            added_count = 0
            for song in reversed(selected):
                target_folder = next(
                    (
                        f
                        for f in favorites_manager.folders
                        if f.folder_name == folder_name
                    ),
                    None,
                )
                if target_folder and any(s.id == song.id for s in target_folder.songs):
                    continue
                if favorites_manager.addSong(folder_name, song):
                    added_count += 1
            event_bus.emit(Events.FAVORITES_CHANGED, folder_name)
            event_bus.emit(Events.MWINDOW_REFRESH_FOLDERS)
            InfoBar.success(
                tr('favorites_page.songs_added'),
                tr(
                    'favorites_page.added_added_count_selected_songs_to_folder_name',
                    added_count=added_count,
                    folder_name=folder_name,
                ),
                parent=self._mwindow,
            )
        elif folder_type == 'cloud' and cloud_id:
            song_ids = [str(song.id) for song in reversed(selected)]
            result: dict[str, bool] = {}

            def _addCloud() -> None:
                result['added'] = getBackend().editPlaylist('add', song_ids, cloud_id)

            def _finishCloud() -> None:
                if not result.get('added', False):
                    InfoBar.warning(
                        tr('favorites_page.session_expired'),
                        tr('favorites_page.please_re_login_to_perform_this_action'),
                        parent=self._mwindow,
                        duration=5000,
                    )
                    return
                event_bus.emit(Events.MWINDOW_REFRESH_FOLDERS)
                InfoBar.success(
                    tr('favorites_page.songs_added'),
                    tr(
                        'favorites_page.added_count_selected_songs_to_folder_name',
                        count=len(selected),
                        folder_name=folder_name,
                    ),
                    parent=self._mwindow,
                )

            asyncTask(_addCloud, (), self, _finishCloud)

    def removeSelectedSongs(self) -> None:
        selected = self._selectedSongs()
        if not selected:
            return

        folder_name = (
            self.curr_cloud_folder.folder_name
            if self.is_cloud and self.curr_cloud_folder
            else self.curr_folder.folder_name
            if self.curr_folder
            else ''
        )
        dialog = MessageBox(
            tr('favorites_page.confirm_delete'),
            tr(
                'favorites_page.are_you_sure_you_want_to_delete_count_selected_songs_from_folder_name',
                count=len(selected),
                folder_name=folder_name,
            ),
            self._mwindow,
        )
        dialog.yesButton.setStyleSheet(
            dialog.yesButton.styleSheet()
            + 'PrimaryPushButton { color: white; background: #c42b1c; border: none; }'
            'PrimaryPushButton:hover { background: #d13438; border: none; }'
            'PrimaryPushButton:pressed { background: #a4262c; border: none; }'
        )
        dialog.cancelButton.setText(tr('favorites_page.cancel'))
        dialog.yesButton.setText(tr('favorites_page.delete'))
        if not dialog.exec():
            return

        selected_ids = {str(song.id) for song in selected}
        if self.is_cloud:
            if not self.curr_cloud_folder:
                return
            folder_id = self.curr_cloud_folder.id
            result: dict[str, bool] = {}

            def _deleteCloud() -> None:
                result['removed'] = getBackend().editPlaylist(
                    'del', list(selected_ids), folder_id
                )

            def _finishCloud() -> None:
                if not result.get('removed', False):
                    InfoBar.warning(
                        tr('favorites_page.session_expired'),
                        tr('favorites_page.please_re_login_to_perform_this_action'),
                        parent=self._mwindow,
                        duration=5000,
                    )
                    return
                self.curr_cloud_songs = [
                    song
                    for song in self.curr_cloud_songs
                    if str(song.id) not in selected_ids
                ]
                self._selected_song_ids.clear()
                self.refresh()
                InfoBar.success(
                    tr('favorites_page.songs_deleted'),
                    tr(
                        'favorites_page.deleted_count_selected_songs',
                        count=len(selected),
                    ),
                    parent=self._mwindow,
                )
                event_bus.emit(Events.MWINDOW_REFRESH_FOLDERS)

            asyncTask(_deleteCloud, (), self, _finishCloud)
            return
        if self.curr_folder:
            self.curr_folder.songs = [
                song
                for song in self.curr_folder.songs
                if str(song.id) not in selected_ids
            ]
            favorites_manager._saveAsync()
            event_bus.emit(Events.FAVORITES_CHANGED, self.curr_folder.folder_name)
        else:
            return

        self._selected_song_ids.clear()
        InfoBar.success(
            tr('favorites_page.songs_deleted'),
            tr('favorites_page.deleted_count_selected_songs', count=len(selected)),
            parent=self._mwindow,
        )
        event_bus.emit(Events.MWINDOW_REFRESH_FOLDERS)

    def openRediscovery(self) -> None:
        folder = self.curr_cloud_folder if self.is_cloud else self.curr_folder
        if folder is not None and self._songs():
            self.ctx.main_window.openRediscovery(folder)

    def playAll(self, tip=True):
        self._pm.setPlaylist(list(self._songs()))
        folder_name = (
            self.curr_cloud_folder.folder_name
            if self.is_cloud and self.curr_cloud_folder
            else self.curr_folder.folder_name
            if self.curr_folder
            else ''
        )
        event_bus.emit(Events.PLAY_SONG_AT_INDEX, 0)
        if tip:
            InfoBar.success(
                tr('favorites_page.playlist_replaced'),
                tr(
                    'favorites_page.playlist_replaced_with_folder_name',
                    folder_name=folder_name,
                ),
                parent=self._mwindow,
            )

    def addFolderToPlaylist(self):
        added_count = 0
        for song in self._songs():
            if not any(s.name == song.name for s in self._pm.playlist):
                self._pm.playlist.append(song)
                added_count += 1

        if added_count > 0:
            event_bus.emit(Events.PLAYLIST_CHANGED)
            InfoBar.success(
                tr('favorites_page.songs_added'),
                tr(
                    'favorites_page.added_added_count_songs_from_favorites_to_playlist',
                    added_count=added_count,
                ),
                parent=self._mwindow,
            )
