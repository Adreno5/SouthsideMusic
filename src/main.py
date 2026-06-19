from __future__ import annotations
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), 'utils'))
sys.path.append(os.path.join(os.path.dirname(__file__), 'views'))
sys.path.append(os.path.join(os.path.dirname(__file__), 'services'))
sys.path.append(os.path.dirname(__file__))

from views.dependences_window import DependencesWindow
import logging

from views.playlist_page import PlaylistPage
from views.setting_page import SettingPage

import threading
import time
import uuid
from types import TracebackType
import glob

from services.events.events_services import EventsServices

import imports as _ims
from qfluentwidgets import setTheme, Theme
import shiboken6

from core.config import loadConfig, saveConfig, cfg
from core.favorites import favorites_manager
from core.icons import refreshBoundIcons
from core.audio_player import AudioPlayer
from core.backend import initBackend
from core.netease_backend import NeteaseCloudMusicBackend
from core.playing_manager import PlayingManager
from core import theme as themeModule
import pyncm as ncm
from pyncm import apis
from core.ws_server import ws_server, ws_handler
from views.log_handler import LogHandler, hijackStreams
from views.launch_window import LaunchWindow
from views.search_page import SearchPage
from views.playing_page import PlayingPage
from views.desktop_lyrics import DesktopLyricsPage
from views.favorites_page import FavoritesPage
from views.session_page import SessionPage
from views.main_window import MainWindow
from views.error_popup import ErrorPopupWindow
from core.debugging import Debugging
from services.update import startUpdateCheck

logging_handler = LogHandler()
logging.basicConfig(level=logging.DEBUG, handlers=[logging_handler])
hijackStreams()

_logger = logging.getLogger('main')


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
        _logger.info('quit by user')
        if mwindow:
            mwindow.close()
        app.quit()
        sys.exit()

    txt = '\n'.join(inf)
    if launchwindow is not None and shiboken6.isValid(launchwindow):
        launchwindow.deleteLater()

    popup = ErrorPopupWindow(txt)
    popup.exec()

    saveConfig()


sys.excepthook = patchedExceptHook

import subprocess  # noqa: E402

original_popen = subprocess.Popen


def patched_popen(*args, **kwargs):
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    kwargs['startupinfo'] = startupinfo
    kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    return original_popen(*args, **kwargs)


subprocess.Popen = patched_popen  # type: ignore
subprocess.call = patched_popen  # type: ignore

import traceback
from pathlib import Path

_ims.QApplication.setHighDpiScaleFactorRoundingPolicy(
    _ims.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)
_ims.QApplication.setAttribute(
    _ims.Qt.ApplicationAttribute.AA_CompressHighFrequencyEvents
)

app = _ims.QApplication(sys.argv)

mwindow: MainWindow | None = None
launchwindow: LaunchWindow | None = LaunchWindow(app)
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


if __name__ == '__main__':
    assert launchwindow is not None
    launchwindow.subtitle('Phase 1 (start core...)')

    launchwindow.push('Writting login information...')
    if cfg.login_status and not ncm.getCurrentSession().is_anonymous:
        apis.login.writeLoginInfo(cfg.login_status)
    else:
        cfg.login_status = apis.login.getCurrentLoginStatus()  # type: ignore

    initBackend(NeteaseCloudMusicBackend())

    def _themeChanged(theme: str):
        def _updateTheme():
            global mwindow
            themeModule._is_dark = themeModule.getDarkdetect().isDark()
            setTheme(Theme.LIGHT if theme == 'Light' else Theme.DARK)
            app.setStyleSheet(f'color: {"white" if themeModule.isDark() else "black"};')
            refreshBoundIcons()
            _ims.event_bus.emit(_ims.POST_THEME_CHANGED)

        if mwindow:
            mwindow.addScheduledTask(_updateTheme)

    _ims.event_bus.subscribe(_ims.PRE_THEME_CHANGED, _themeChanged)

    def _cleanCaches():
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

            time.sleep(10)

    threading.Thread(target=_cleanCaches, daemon=True).start()

    app.setStyleSheet(f'color: {"white" if themeModule.isDark() else "black"};')
    setTheme(Theme.LIGHT if themeModule.isLight() else Theme.DARK)

    app.processEvents()

    loadConfig()
    launchwindow.push('Loading config...')

    launchwindow.push('Loading fonts...')
    harmony_font_family = _ims.QFontDatabase.applicationFontFamilies(
        _ims.QFontDatabase.addApplicationFont('fonts/HARMONYOS_SANS_SC_REGULAR.ttf')
    )[0]

    from core.lyrics import LRCLyricParser, YRCLyricParser

    launchwindow.push('Initializing services...')

    mgr = LRCLyricParser()
    transmgr = LRCLyricParser()
    ymgr = YRCLyricParser()

    player = AudioPlayer()

    launchwindow.push('Loading favorites...')
    favorites_manager.load()

    launchwindow.push('Logging in...')
    if cfg.session is None:
        apis.login.loginViaAnonymousAccount()
        sstr = ncm.dumpSessionAsString(apis.getCurrentSession())
        cfg.session = sstr
        cfg.login_status = apis.login.getCurrentLoginStatus()  # type: ignore
        _logger.info('logged into generated anonymous account')
    else:
        ncm.setCurrentSession(ncm.loadSessionFromString(cfg.session))
        _logger.info('loaded session from config')

        if (
            cfg.login_method == 'cell phone' or cfg.login_method == 'QR code'
        ) and cfg.login_status:
            apis.login.writeLoginInfo(cfg.login_status)
            _logger.info('wrote login info')

    csession = ncm.getCurrentSession()
    csession.deviceId = uuid.uuid4().hex
    ncm.setCurrentSession(csession)

    launchwindow.clear()
    launchwindow.subtitle('Phase 2 (initialize components...)')

    from core.app_context import AppContext

    ctx = AppContext(
        app=app,
        player=player,
        cfg=cfg,
        mgr=mgr,
        transmgr=transmgr,
        ymgr=ymgr,
        ws_server=ws_server,
        ws_handler=ws_handler,
        harmony_font_family=harmony_font_family,
        favs=favorites_manager.folders,
        lock=lock,
    )
    ctx.launch_window = launchwindow
    ctx.playing_manager = PlayingManager(ctx)

    launchwindow.subtitle('Preparing (checking dependences...)')
    depwindow = DependencesWindow(ctx)

    def _postStageInit():
        if not launchwindow:
            return
        launchwindow.push('Initializing events services...')
        events_service = EventsServices(ctx)

        launchwindow.push('Initializing debug window...')
        dw = Debugging(ctx)
        ctx.debugging_obj = dw
        launchwindow.push('Initializing playing page...')
        dp = PlayingPage(ctx)
        ctx.playing_page = dp
        launchwindow.push('Initializing search page...')
        sp = SearchPage(ctx)
        ctx.search_page = sp
        launchwindow.push('Initializing desktop lyrics page...')
        dsp = DesktopLyricsPage(ctx)
        ctx.desktop_lyrics_page = dsp
        launchwindow.push('Initializing favorites page...')
        fp = FavoritesPage(ctx)
        ctx.favorites_page = fp
        launchwindow.push('Initializing session page...')
        sep = SessionPage(ctx)
        ctx.session_page = sep
        launchwindow.push('Initializing setting page...')
        stp = SettingPage(ctx)
        ctx.setting_page = stp
        launchwindow.push('Initializing playlist page...')
        plp = PlaylistPage(ctx)
        ctx.playlist_page = plp

        ctx.playing_page = dp
        ctx.search_page = sp
        ctx.desktop_lyrics_page = dsp
        ctx.favorites_page = fp
        ctx.session_page = sep
        ctx.setting_page = stp
        ctx.playlist_page = plp

        launchwindow.push('Initializing main window...')
        mwindow = MainWindow(ctx)
        ctx.main_window = mwindow

        mwindow.init()

        fp.refresh()

        print(ncm.getCurrentSession().bindings)

        _ims.QTimer.singleShot(2000, lambda: startUpdateCheck(mwindow))

        _logger.debug(f'{sys.path=}')

    depwindow.all_checked.connect(_postStageInit)

    app.exec()
