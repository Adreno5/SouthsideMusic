import threading
from typing import TYPE_CHECKING

from core.app_context import AppContext
from core.backend import get_backend
from core.dialogs import get_text_lineedit
from core.downloader import doWithMultiThreading
from imports import (
    BACKGROUND_RATIO_CHANGED,
    CLOUD_ADD_TO_LOCAL,
    CLOUD_REMOVE_FOLDER,
    CLOUD_RENAME_FOLDER,
    LOCAL_ADD_TO_CLOUD,
    MWINDOW_REFRESH_FOLDERS,
    PRE_THEME_CHANGED,
    REFRESH_RATE_CHANGED,
    LOCAL_REMOVE_FOLDER,
    LOCAL_RENAME_FOLDER,
    REPAINT,
    SONG_CHANGED,
    InfoBar,
    MessageBox,
    QApplication,
    QDialog,
    QMessageBox,
    QObject,
    QTimer,
    event_bus,
)
from core import theme
from core.favorites import favorites_manager

from views.folder_card import CloudFolderCard, LocalFolderCard


class EventsServices(QObject):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._ctx = ctx
        self._app = ctx.app

        self.refresh_rate = max(60, self._app.primaryScreen().refreshRate() / 2)
        self.repaint_timer = QTimer(self)
        self.repaint_timer.timeout.connect(lambda: event_bus.emit(REPAINT))
        self.repaint_timer.start(int(1000 / self.refresh_rate))
        self._app.primaryScreen().refreshRateChanged.connect(
            lambda: event_bus.emit(REFRESH_RATE_CHANGED)
        )
        event_bus.subscribe(REFRESH_RATE_CHANGED, self._onRefreshRateChanged)

        def _startListen():
            theme.getDarkdetect().listener(
                lambda t: event_bus.emit(PRE_THEME_CHANGED, t)
            )

        threading.Thread(target=_startListen, daemon=True).start()

        event_bus.subscribe(
            SONG_CHANGED, lambda s: event_bus.emit(BACKGROUND_RATIO_CHANGED)
        )
        event_bus.subscribe(LOCAL_REMOVE_FOLDER, self.localRemoveFolder)
        event_bus.subscribe(LOCAL_RENAME_FOLDER, self.localRenameFolder)
        event_bus.subscribe(LOCAL_ADD_TO_CLOUD, self.localAddToCloud)
        event_bus.subscribe(CLOUD_REMOVE_FOLDER, self.cloudRemoveFolder)
        event_bus.subscribe(CLOUD_RENAME_FOLDER, self.cloudRenameFolder)
        event_bus.subscribe(CLOUD_ADD_TO_LOCAL, self.cloudAddtoLocal)

    def cloudAddtoLocal(self, card: CloudFolderCard):
        folder_name = card.folder['folder_name']
        favorites_manager.addFolder(folder_name)
        def _add():
            response = get_backend().get_playlist_tracks(str(card.folder['id']))
            for song in response:
                favorites_manager.addSong(folder_name, song)
        def _finished():
            event_bus.emit(MWINDOW_REFRESH_FOLDERS)
            self._ctx.main_window.addScheduledTask(lambda: InfoBar.success(
                'Imported successfully', f'Folder {folder_name} was added to local',
                duration=5000,
                parent=self._ctx.main_window
            ))
        doWithMultiThreading(_add, (), self, _finished)

    def localAddToCloud(self, card: LocalFolderCard):
        folder_name = card.folder['folder_name']
        def _add():
            id_ = get_backend().create_playlist(folder_name)
            get_backend().edit_playlist('add', [song.id for song in card.folder['songs']], id_)
        def _finished():
            event_bus.emit(MWINDOW_REFRESH_FOLDERS)
            self._ctx.main_window.addScheduledTask(lambda: InfoBar.success(
                'Imported successfully', f'Folder {folder_name} was added to cloud',
                duration=5000,
                parent=self._ctx.main_window
            ))
        doWithMultiThreading(_add, (), self, _finished)

    def cloudRemoveFolder(self, card: CloudFolderCard):
        confirmed: bool = (
            QMessageBox.question(
                None,
                'Remove Folder',
                f"Are you sure to remove folder '{card.folder['folder_name']}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            == QMessageBox.StandardButton.Yes
        )
        if confirmed:
            get_backend().remove_playlist(card.folder['id'])
            event_bus.emit(MWINDOW_REFRESH_FOLDERS)

    def cloudRenameFolder(self, card: CloudFolderCard):
        new_name = get_text_lineedit('Rename Folder', 'enter new name of your folder', 'my folder', self._ctx.main_window)
        if not new_name:
            return
        folder_name = card.folder['folder_name']
        folder_id = card.folder['id']
        def _rename():
            songs = get_backend().get_playlist_tracks(folder_id)
            new_id = get_backend().create_playlist(new_name)
            get_backend().edit_playlist('add', [song.id for song in songs], new_id)
            get_backend().remove_playlist(folder_id)
        def _finished():
            event_bus.emit(MWINDOW_REFRESH_FOLDERS)
            self._ctx.main_window.addScheduledTask(lambda: InfoBar.success(
                'Renamed successfully', f'Folder {folder_name} was renamed to {new_name}',
                duration=5000,
                parent=self._ctx.main_window
            ))
        doWithMultiThreading(_rename, (), self, _finished)

    def localRemoveFolder(self, card: LocalFolderCard):
        confirmed: bool = (
            QMessageBox.question(
                None,
                'Remove Folder',
                f"Are you sure to remove folder '{card.folder['folder_name']}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            == QMessageBox.StandardButton.Yes
        )
        if confirmed:
            favorites_manager.removeFolder(card.folder['folder_name'])
            event_bus.emit(MWINDOW_REFRESH_FOLDERS)

    def localRenameFolder(self, card: LocalFolderCard):
        new = get_text_lineedit(
            'Rename Folder',
            'enter new name of your folder',
            'my folder',
            self._ctx.main_window,
        )
        if new:
            favorites_manager.renameFolder(card.folder['folder_name'], new)
            event_bus.emit(MWINDOW_REFRESH_FOLDERS)

    def _onRefreshRateChanged(self):
        self.refresh_rate = max(60, self._app.primaryScreen().refreshRate() / 2)

        self.repaint_timer.setInterval(int(1000 / self.refresh_rate))
