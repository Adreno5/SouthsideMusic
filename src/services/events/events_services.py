import threading
from typing import TYPE_CHECKING

from core.app_context import AppContext
from core.backend import get_backend
from core.dialogs import get_text_lineedit
from imports import (
    BACKGROUND_RATIO_CHANGED,
    CLOUD_REMOVE_FOLDER,
    MWINDOW_REFRESH_FOLDERS,
    PRE_THEME_CHANGED,
    REFRESH_RATE_CHANGED,
    LOCAL_REMOVE_FOLDER,
    LOCAL_RENAME_FOLDER,
    REPAINT,
    SONG_CHANGED,
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
        event_bus.subscribe(LOCAL_REMOVE_FOLDER, self.removeFolder)
        event_bus.subscribe(LOCAL_RENAME_FOLDER, self.renameFolder)
        event_bus.subscribe(CLOUD_REMOVE_FOLDER, self.cloudRemoveFolder)

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

    def removeFolder(self, card: LocalFolderCard):
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

    def renameFolder(self, card: LocalFolderCard):
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
