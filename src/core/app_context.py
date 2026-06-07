from __future__ import annotations

import threading
from typing import TYPE_CHECKING, cast

from views.dependences_window import DependencesWindow

if TYPE_CHECKING:
    from core.audio_player import AudioPlayer
    from core.config import Config
    from core.lyrics import LRCLyricParser, YRCLyricParser
    from core.playing_manager import PlayingManager
    from core.ws_server import WebSocketServer, QObjectHandler
    from PySide6.QtWidgets import QApplication
    from views.desktop_lyrics import DesktopLyricsPage
    from views.favorites_page import FavoritesPage
    from views.launch_window import LaunchWindow
    from views.main_window import MainWindow
    from views.playing_page import PlayingPage
    from views.playlist_page import PlaylistPage
    from views.search_page import SearchPage
    from views.session_page import SessionPage
    from views.setting_page import SettingPage


class AppContext:
    def __init__(
        self,
        app: QApplication,
        player: AudioPlayer,
        cfg: Config,
        mgr: LRCLyricParser,
        transmgr: LRCLyricParser,
        ymgr: YRCLyricParser,
        ws_server: WebSocketServer,
        ws_handler: QObjectHandler,
        harmony_font_family: str,
        favs: list | None = None,
        lock: threading.Lock | None = None,
    ) -> None:
        self.app = app
        self.player = player
        self.cfg = cfg
        self.mgr = mgr
        self.transmgr = transmgr
        self.ymgr = ymgr
        self.ws_server = ws_server
        self.ws_handler = ws_handler
        self.harmony_font_family = harmony_font_family
        self.favs = favs if favs is not None else []
        self.lock = lock if lock is not None else threading.Lock()
        self.playing_manager: PlayingManager = cast('PlayingManager', None)
        self.dependences_available: bool = True

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
