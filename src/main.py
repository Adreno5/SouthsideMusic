from __future__ import annotations
import json
import subprocess
import sys
import os
import traceback
from pathlib import Path
import atexit

_SRC_DIR = os.path.abspath(os.path.dirname(__file__))
if _SRC_DIR in sys.path:
    sys.path.remove(_SRC_DIR)
sys.path.insert(0, _SRC_DIR)
sys.path.append(os.path.join(_SRC_DIR, 'utils'))
sys.path.append(os.path.join(_SRC_DIR, 'views'))
sys.path.append(os.path.join(_SRC_DIR, 'services'))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from views.launch_window import LaunchWindow

QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)
QApplication.setAttribute(Qt.ApplicationAttribute.AA_CompressHighFrequencyEvents)

app = QApplication(sys.argv)

from core.smtc import SmtcController, initAppIdentity

initAppIdentity()

launchwindow: LaunchWindow | None = LaunchWindow(app)
launchwindow.subtitle('Loading libraries...')
app.processEvents()

from views.home_page import HomePage
from views.library_page import LibraryPage
from views.comments_page import CommentsPage

from core.lyrics import LRCLyricParser, YRCLyricParser
from views.dependences_window import DependencesWindow
import logging

from views.playlist_page import PlaylistPage
from views.setting_page import SettingPage

import threading
import time
from types import TracebackType
import glob

from services.services import EventsServices

import imports as _ims
from qfluentwidgets import setTheme, Theme
import shiboken6

from core.config import loadConfig, saveConfig, Config
from core.cache_cleanup import DEFAULT_DATA_CLEANUP_INTERVAL_SECONDS, cleanupDataFolder
from core.favorites import favorites_manager, saveFavorites
from core.icons import refreshBoundIcons
from core.llm import LLM
from core.audio_player import AudioPlayer
from core.backend import initBackend
from core.netease_backend import NeteaseCloudMusicBackend
from core.playing_manager import PlayingManager
from core import theme as themeModule
from core.ws_server import ws_server, ws_handler
from views.log_handler import LogHandler, hijackStreams
from views.search_page import SearchPage
from views.playing_page import PlayingPage
from views.desktop_lyrics import DesktopLyricsPage
from views.favorites_page import FavoritesPage
from views.main_window import LLM_WINDOW_WIDTH_DELTA, MainWindow
from views.error_popup import ErrorPopupWindow
from core.debugging import Debugging
from services.update import startUpdateCheck

logging_handler = LogHandler()
logging.basicConfig(level=logging.DEBUG, handlers=[logging_handler])
hijackStreams()

_logger = logging.getLogger('main')
_exit_cleanup_done = False


def atExitListener():
    global _exit_cleanup_done
    if _exit_cleanup_done:
        return
    _exit_cleanup_done = True
    logging.info('exiting by listener')

    context = globals().get('ctx')
    if context is None:
        ws_server.stop(shutdown_json_sender=True)
        return

    playing_manager = context.playing_manager
    cfg.last_playing_time = context.player.getPosition()
    context.player.shutdown()
    if playing_manager is not None:
        playing_manager.shutdownWorkers()
    context.ws_server.stop(shutdown_json_sender=True)

    cfg.last_playlist = context.playing_manager.playlist.copy()
    cfg.last_playing_index = context.playing_manager.current_index

    cfg.window_x = context.main_window.x()
    cfg.window_y = context.main_window.y()
    cfg.window_width = context.main_window.width() - (
        LLM_WINDOW_WIDTH_DELTA if context.main_window.llm_viewer_panel.expanded else 0
    )
    cfg.window_height = context.main_window.height()
    cfg.window_maximized = context.main_window.isMaximized()
    cfg.llm_viewer_expanded = context.main_window.llm_viewer_panel.expanded

    saveConfig()
    saveFavorites()


atexit.register(atExitListener)


def patchedExceptHook(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_traceback: TracebackType | None,
):
    global mwindow, launchwindow, app

    inf: list[str] = []

    _logger.error('| Unhandled Exception occurred |')
    _logger.error(f'Caused by {exc_type.__name__}')
    _logger.error('Traceback:')
    inf.append('| Unhandled Exception occurred |')
    inf.append(f'Caused by {exc_type.__name__}')
    inf.append('Traceback:')
    if exc_traceback:
        stack_frames = traceback.extract_tb(exc_traceback)
        for frame in stack_frames:
            _logger.error(
                f'    at {Path(frame.filename).resolve().as_posix()}:{frame.lineno}|{frame.name}'
            )
            inf.append(
                f'    at {Path(frame.filename).resolve().as_posix()}:{frame.lineno}|{frame.name}'
            )
    _logger.error('Exception chain:')
    inf.append('Exception chain:')
    current_exc = exc_value
    _logger.error(f'    caused by {type(current_exc).__name__}({current_exc}) #0')
    inf.append(f'    caused by {type(current_exc).__name__}({current_exc}) #0')
    if current_exc.__traceback__:
        root_frames = traceback.extract_tb(current_exc.__traceback__)
        for frame in root_frames:
            _logger.error(
                f'      at {Path(frame.filename).resolve().as_posix()}:{frame.lineno}|{frame.name}'
            )
            inf.append(
                f'      at {Path(frame.filename).resolve().as_posix()}:{frame.lineno}|{frame.name}'
            )
    chain_level = 1
    while True:
        next_exc = current_exc.__cause__ or current_exc.__context__
        if not next_exc or next_exc is current_exc:
            break
        _logger.error(
            f'    caused by {type(next_exc).__name__}({next_exc}) #{chain_level}'
        )
        inf.append(
            f'    caused by {type(next_exc).__name__}({next_exc}) #{chain_level}'
        )
        if next_exc.__traceback__:
            root_frames = traceback.extract_tb(next_exc.__traceback__)
            for frame in root_frames:
                _logger.error(
                    f'      at {Path(frame.filename).resolve().as_posix()}:{frame.lineno}|{frame.name}'
                )
                inf.append(
                    f'      at {Path(frame.filename).resolve().as_posix()}:{frame.lineno}|{frame.name}'
                )
        current_exc = next_exc
        chain_level += 1
    _logger.error(f'Raised {exc_type.__name__}({exc_value})')
    inf.append(f'Raised {exc_type.__name__}({exc_value})')

    if exc_type is KeyboardInterrupt:
        _logger.info('quit by KeyboardInterrupt')
        sys.exit()

    txt = '\n'.join(inf)
    if launchwindow is not None and shiboken6.isValid(launchwindow):
        launchwindow.deleteLater()

    popup = ErrorPopupWindow(txt)
    popup.exec()

    saveConfig()


sys.excepthook = patchedExceptHook

original_popen = subprocess.Popen
original_call = subprocess.call


def _hide_subprocess_window(kwargs):
    if sys.platform != 'win32':
        return
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    kwargs['startupinfo'] = startupinfo
    kwargs['creationflags'] = (
        int(kwargs.get('creationflags', 0)) | subprocess.CREATE_NO_WINDOW
    )


def patched_popen(*args, **kwargs):
    _hide_subprocess_window(kwargs)
    return original_popen(*args, **kwargs)


def patched_call(*args, **kwargs):
    _hide_subprocess_window(kwargs)
    return original_call(*args, **kwargs)


subprocess.Popen = patched_popen  # type: ignore
subprocess.call = patched_call  # type: ignore

mwindow: MainWindow | None = None
lock: threading.Lock = threading.Lock()

_ims.event_bus._lw = launchwindow


def _on_ws_connected():
    if mwindow:
        mwindow.onWebsocketConnected()


def _on_ws_disconnected():
    if mwindow:
        mwindow.onWebsocketDisconnected()


ws_handler.onConnected.connect(_on_ws_connected)
ws_handler.onDisconnected.connect(_on_ws_disconnected)


def _schedule_ws_task(fn) -> None:
    if mwindow and getattr(mwindow, 'ctx', None):
        mwindow.ctx.addScheduledTask(fn)
    else:
        _ims.QTimer.singleShot(0, fn)


def _playlist_artists_text(song) -> str:
    artists = []
    for artist in getattr(song, 'artists', []) or []:
        name = getattr(artist, 'name', '')
        if name:
            artists.append(str(name))
    return ', '.join(artists)


def _playlist_item_payload(index: int, song) -> dict[str, object]:
    duration = int(getattr(song, 'duration', 0) or 0)
    return {
        'index': index,
        'id': str(getattr(song, 'id', '')),
        'name': str(getattr(song, 'name', '')),
        'artists': _playlist_artists_text(song),
        'duration': duration,
        'duration_ms': duration,
    }


def _ws_playlist_payload() -> dict[str, object] | None:
    ctx_obj = globals().get('ctx')
    playing_manager = getattr(ctx_obj, 'playing_manager', None)
    if playing_manager is None:
        return None

    playlist = list(playing_manager.playlist)
    current_index = int(getattr(playing_manager, 'current_index', -1))
    current_song_id = ''
    if 0 <= current_index < len(playlist):
        current_song_id = str(getattr(playlist[current_index], 'id', ''))
    return {
        'option': 'playlist_update',
        'current_index': current_index,
        'current_song_id': current_song_id,
        'play_mode': str(getattr(playing_manager, 'play_mode', '')),
        'count': len(playlist),
        'items': [
            {
                **_playlist_item_payload(index, song),
                'is_current': index == current_index,
            }
            for index, song in enumerate(playlist)
        ],
    }


def _send_ws_playlist_state() -> None:
    if not ws_handler.is_open:
        return
    ws_handler.sendJsonFactory(
        lambda: (
            _ws_playlist_payload()
            or {
                'option': 'playlist_update',
                'current_index': -1,
                'current_song_id': '',
                'play_mode': '',
                'count': 0,
                'items': [],
            }
        ),
        coalesce_key='playlist_update',
    )


def _payload_int(payload: dict, key: str, default: int = -1) -> int:
    try:
        return int(payload.get(key, default))
    except (TypeError, ValueError):
        return default


def _handle_ws_playlist_control(payload: dict) -> None:
    ctx_obj = globals().get('ctx')
    playing_manager = getattr(ctx_obj, 'playing_manager', None)
    if playing_manager is None:
        return

    action = str(payload.get('action', 'get'))
    playlist = playing_manager.playlist

    if action == 'get':
        _send_ws_playlist_state()
        return

    if action == 'play_index':
        index = _payload_int(payload, 'index')
        playing_manager.playSongAtIndex(index)
        _send_ws_playlist_state()
        return

    if action == 'remove_index':
        index = _payload_int(payload, 'index')
        if index < 0 or index >= len(playlist):
            return
        removing_current = index == playing_manager.current_index
        playlist.pop(index)
        if playing_manager.current_index > index:
            playing_manager.current_index -= 1
        elif playing_manager.current_index >= len(playlist):
            playing_manager.current_index = len(playlist) - 1
        _ims.event_bus.emit(_ims.PLAYLIST_CHANGED)
        if removing_current and 0 <= playing_manager.current_index < len(playlist):
            playing_manager.playSongAtIndex(playing_manager.current_index)
        return

    if action == 'move':
        from_index = _payload_int(payload, 'from_index')
        to_index = _payload_int(payload, 'to_index')
        if (
            from_index < 0
            or from_index >= len(playlist)
            or to_index < 0
            or to_index >= len(playlist)
            or from_index == to_index
        ):
            return
        current_song = None
        if 0 <= playing_manager.current_index < len(playlist):
            current_song = playlist[playing_manager.current_index]
        song = playlist.pop(from_index)
        playlist.insert(to_index, song)
        if current_song is not None:
            try:
                playing_manager.current_index = playlist.index(current_song)
            except ValueError:
                playing_manager.current_index = -1
        _ims.event_bus.emit(_ims.PLAYLIST_CHANGED)
        return

    if action == 'clear':
        current_song = None
        if 0 <= playing_manager.current_index < len(playlist):
            current_song = playlist[playing_manager.current_index]
        playlist.clear()
        if current_song is not None:
            playlist.append(current_song)
            playing_manager.current_index = 0
        else:
            playing_manager.current_index = -1
        _ims.event_bus.emit(_ims.PLAYLIST_CHANGED)


def _handle_ws_message(message: str) -> None:
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        _logger.debug('ignored non-json websocket message: %s', message)
        return

    if not isinstance(payload, dict):
        return

    option = payload.get('option')
    if option == 'playlist_control':
        _schedule_ws_task(lambda payload=payload: _handle_ws_playlist_control(payload))
        return
    if option != 'music_control':
        return

    command = payload.get('command')

    def _run() -> None:
        if command == 'toggle':
            if mwindow and getattr(mwindow, 'controller', None):
                mwindow.controller.toggle()
            else:
                player_obj = globals().get('player')
                if player_obj is None:
                    return
                if player_obj.isPlaying():
                    player_obj.pause()
                    _ims.event_bus.emit(_ims.PLAY_STATE_CHANGED, False)
                else:
                    player_obj.resume()
                    _ims.event_bus.emit(_ims.PLAY_STATE_CHANGED, True)
        elif command == 'seek':
            player_obj = globals().get('player')
            if player_obj is None:
                return
            try:
                position = float(payload.get('position', 0.0))
            except (TypeError, ValueError):
                return
            player_obj.setPosition(max(0.0, position))
        elif command == 'next':
            _ims.event_bus.emit(_ims.PLAYNEXT)
        elif command == 'previous':
            _ims.event_bus.emit(_ims.PLAYLAST)

    _schedule_ws_task(_run)


ws_handler.onMessage.connect(_handle_ws_message)
ws_handler.onConnected.connect(_send_ws_playlist_state)
_ims.event_bus.subscribe(_ims.PLAYLIST_CHANGED, lambda: _send_ws_playlist_state())
_ims.event_bus.subscribe(_ims.SONG_CHANGED, lambda _song: _send_ws_playlist_state())
_ims.event_bus.subscribe(
    _ims.PLAY_STATE_CHANGED,
    lambda is_playing: ws_handler.sendJson(
        {
            'option': 'play_state',
            'is_playing': bool(is_playing),
            'position': ctx.player.getPosition(),
            'duration': ctx.player.getLength(),
        },
        coalesce_key='play_state',
    ),
)


if __name__ == '__main__':
    assert launchwindow is not None
    launchwindow.subtitle('Phase 1 (start core...)')

    backend = NeteaseCloudMusicBackend()
    initBackend(backend)

    launchwindow.subtitle('Writting login information...')
    cfg = Config.instance()
    if cfg.login_status and not backend.currentSessionIsAnonymous():
        backend.writeLoginInfo(cfg.login_status)
    else:
        cfg.login_status = backend.getCurrentLoginStatus()

    def _themeChanged(theme: str):
        def _updateTheme():
            global mwindow
            themeModule._is_dark = themeModule.getDarkdetect().isDark()
            setTheme(Theme.LIGHT if theme == 'Light' else Theme.DARK)
            app.setStyleSheet(f'color: {"white" if themeModule.isDark() else "black"};')
            refreshBoundIcons()
            _ims.event_bus.emit(_ims.POST_THEME_CHANGED)

        if mwindow:
            mwindow.ctx.addScheduledTask(_updateTheme)

    _ims.event_bus.subscribe(_ims.PRE_THEME_CHANGED, _themeChanged)

    def _cleanCaches():
        last_data_cleanup = 0.0
        while True:
            if mwindow:
                while mwindow._loading_song:
                    time.sleep(1)
                files = glob.glob('*')
                caches = []
                for file in files:
                    if file.startswith('ffcache'):
                        caches.append(file)
                cleared_count = 0
                for cache in caches:
                    try:
                        os.remove(cache)
                        cleared_count += 1
                    except PermissionError:
                        _logger.debug(f'skip locked cache file: {cache}')
                    except OSError as e:
                        _logger.debug(f'failed to remove cache file {cache}: {e}')
                if cleared_count > 0:
                    _logger.info(f'cleared {cleared_count} caches')

                now = time.time()
                if (
                    cfg.data_cleanup_enabled
                    and now - last_data_cleanup >= DEFAULT_DATA_CLEANUP_INTERVAL_SECONDS
                ):
                    last_data_cleanup = now
                    try:
                        cleanupDataFolder(
                            max_bytes=cfg.data_cache_max_mb * 1024 * 1024,
                            max_age_minutes=cfg.data_cache_max_age_minutes,
                        )
                    except Exception as e:
                        _logger.exception(e)

            time.sleep(10)

    threading.Thread(target=_cleanCaches, daemon=True).start()

    app.setStyleSheet(f'color: {"white" if themeModule.isDark() else "black"};')
    setTheme(Theme.LIGHT if themeModule.isLight() else Theme.DARK)

    app.processEvents()

    loadConfig()
    launchwindow.subtitle('Loading config...')

    launchwindow.subtitle('Loading fonts...')
    harmony_font_family = _ims.QFontDatabase.applicationFontFamilies(
        _ims.QFontDatabase.addApplicationFont('fonts/HARMONYOS_SANS_SC_REGULAR.ttf')
    )[0]

    launchwindow.subtitle('Initializing services...')

    launchwindow.subtitle('Loading favorites...')
    favorites_manager.load()

    launchwindow.subtitle('Logging in...')
    if cfg.session is None:
        snapshot = backend.loginViaAnonymousAccount()
        cfg.session = snapshot.session
        cfg.login_status = snapshot.login_status
        _logger.info('logged into generated anonymous account')
    else:
        backend.loadSession(cfg.session)
        _logger.info('loaded session from config')

        if (
            cfg.login_method == 'cell phone'
            or cfg.login_method == 'QR code'
            or cfg.login_method == 'cookie'
        ) and cfg.login_status:
            backend.writeLoginInfo(cfg.login_status)
            _logger.info('wrote login info')

    backend.setRandomDeviceId()

    launchwindow.clear()
    launchwindow.subtitle('Phase 2 (initialize components...)')

    from core.app_context import AppContext

    ctx = AppContext()
    ctx.app = app
    ctx.player = AudioPlayer()
    ctx.config = Config.instance()
    ctx.mgr = LRCLyricParser()
    ctx.transmgr = LRCLyricParser()
    ctx.ymgr = YRCLyricParser()
    ctx.ws_server = ws_server
    ctx.ws_handler = ws_handler
    ctx.harmony_font_family = harmony_font_family
    ctx.lock = lock
    ctx.launch_window = launchwindow
    ctx.llm = LLM()
    ctx.playing_manager = PlayingManager(ctx)
    ctx.smtc = SmtcController(ctx)

    launchwindow.subtitle('Preparing (checking dependences...)')
    depwindow = DependencesWindow(ctx)
    ctx.dependences_window = depwindow

    def _postStageInit():
        global mwindow

        if not launchwindow:
            return
        launchwindow.subtitle('Initializing events services...')
        ctx.events_service = EventsServices(ctx)

        launchwindow.subtitle('Initializing debug window...')
        dw = Debugging(ctx)
        ctx.debugging_obj = dw
        launchwindow.subtitle('Initializing playing page...')
        dp = PlayingPage(ctx)
        ctx.playing_page = dp
        launchwindow.subtitle('Initializing search page...')
        sp = SearchPage(ctx)
        ctx.search_page = sp
        launchwindow.subtitle('Initializing desktop lyrics page...')
        dsp = DesktopLyricsPage(ctx)
        ctx.desktop_lyrics_page = dsp
        launchwindow.subtitle('Initializing favorites page...')
        fp = FavoritesPage(ctx)
        ctx.favorites_page = fp
        launchwindow.subtitle('Initializing setting page...')
        stp = SettingPage(ctx)
        ctx.setting_page = stp
        launchwindow.subtitle('Initializing playlist page...')
        plp = PlaylistPage(ctx)
        ctx.playlist_page = plp
        launchwindow.subtitle('Initializing home page...')
        hp = HomePage(ctx)
        ctx.home_page = hp
        launchwindow.subtitle('Initializing library page...')
        lrp = LibraryPage(ctx)
        ctx.library_page = lrp
        launchwindow.subtitle('Initializing comments page...')
        ctp = CommentsPage(ctx)
        ctx.comments_page = ctp

        ctx.playing_page = dp
        ctx.search_page = sp
        ctx.desktop_lyrics_page = dsp
        ctx.favorites_page = fp
        ctx.setting_page = stp
        ctx.playlist_page = plp
        ctx.home_page = hp
        ctx.library_page = lrp

        ctx.process_pids['main'] = os.getpid()

        launchwindow.subtitle('Initializing main window...')
        mwindow = MainWindow(ctx)
        ctx.main_window = mwindow
        app.aboutToQuit.connect(atExitListener)

        mwindow.init()
        ctx.smtc.setEnabled(ctx.config.smtc_enabled)

        fp.refresh()

        print(backend.getSessionBindings())

        _ims.QTimer.singleShot(2000, lambda: startUpdateCheck(mwindow))  # type: ignore

        _logger.debug(f'{sys.path=}')

    depwindow.destroyed.connect(
        lambda _obj=None: _ims.QTimer.singleShot(0, _postStageInit)
    )

    app.exec()
