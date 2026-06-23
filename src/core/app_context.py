from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Callable, cast

from imports import QObject, Signal

if TYPE_CHECKING:
    from core.audio_player import AudioPlayer
    from core.config import Config
    from core.debugging import Debugging
    from core.llm import LLM
    from core.lyrics import LRCLyricParser, YRCLyricParser
    from core.models import (
        CloudFolderInfo,
        LocalFolderInfo,
        SearchSongInfo,
        SongStorable,
    )
    from core.playing_manager import PlayingManager
    from core.ws_server import WebSocketServer, QObjectHandler
    from PySide6.QtWidgets import QApplication
    from services.events.events_services import EventsServices
    from views.desktop_lyrics import DesktopLyricsPage
    from views.dependences_window import DependencesWindow
    from views.favorites_page import FavoritesPage
    from views.launch_window import LaunchWindow
    from views.main_window import MainWindow
    from views.playing_page import PlayingPage
    from views.playlist_page import PlaylistPage
    from views.search_page import SearchPage
    from views.session_page import SessionPage
    from views.setting_page import SettingPage


class _ScheduledTaskRunner(QObject):
    scheduledTaskRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._logger = logging.getLogger(__name__)
        self._scheduled_tasks: list[
            tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]
        ] = []
        self._scheduled_tasks_lock = threading.Lock()
        self.scheduledTaskRequested.connect(self._runScheduledTasks)

    def addTask(self, task: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        with self._scheduled_tasks_lock:
            self._scheduled_tasks.append((task, args, kwargs))
        self.scheduledTaskRequested.emit()

    def _runScheduledTasks(self) -> None:
        while True:
            with self._scheduled_tasks_lock:
                if not self._scheduled_tasks:
                    return
                task, args, kwargs = self._scheduled_tasks.pop(0)
            try:
                task(*args, **kwargs)
            except Exception as e:
                self._logger.exception('scheduled task failed')
                raise e


class AppContext:
    def __init__(self) -> None:
        self.app: QApplication = cast('QApplication', None)
        self.player: AudioPlayer = cast('AudioPlayer', None)
        self.cfg: Config = cast('Config', None)
        self.mgr: LRCLyricParser = cast('LRCLyricParser', None)
        self.transmgr: LRCLyricParser = cast('LRCLyricParser', None)
        self.ymgr: YRCLyricParser = cast('YRCLyricParser', None)
        self.ws_server: WebSocketServer = cast('WebSocketServer', None)
        self.ws_handler: QObjectHandler = cast('QObjectHandler', None)
        self.harmony_font_family: str = ''
        self.favs: list = []
        self.lock: threading.Lock = threading.Lock()
        self._scheduled_task_runner = _ScheduledTaskRunner()
        self.playing_manager: PlayingManager = cast('PlayingManager', None)
        self.llm: LLM = cast('LLM', None)
        self.llm_song_handles: dict[str, SearchSongInfo | SongStorable] = {}
        self.llm_folder_handles: dict[str, LocalFolderInfo | CloudFolderInfo] = {}
        self.llm_cloud_search_query: str = ''
        self.llm_cloud_search_offset: int = 0
        self.dependences_available: bool = True
        self.debugging: bool = False

        self.launch_window: LaunchWindow = cast('LaunchWindow', None)
        self.main_window: MainWindow = cast('MainWindow', None)
        self.playing_page: PlayingPage = cast('PlayingPage', None)
        self.search_page: SearchPage = cast('SearchPage', None)
        self.desktop_lyrics_page: DesktopLyricsPage = cast('DesktopLyricsPage', None)
        self.favorites_page: FavoritesPage = cast('FavoritesPage', None)
        self.session_page: SessionPage = cast('SessionPage', None)
        self.setting_page: SettingPage = cast('SettingPage', None)
        self.playlist_page: PlaylistPage = cast('PlaylistPage', None)
        self.dependences_window: DependencesWindow = cast('DependencesWindow', None)
        self.debugging_obj: Debugging = cast('Debugging', None)
        self.events_service: EventsServices = cast('EventsServices', None)

    def addScheduledTask(
        self,
        task: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self._scheduled_task_runner.addTask(task, *args, **kwargs)
