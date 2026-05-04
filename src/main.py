from __future__ import annotations

import logging

import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), 'utils'))
sys.path.append(os.path.join(os.path.dirname(__file__), 'views'))
sys.path.append(os.path.join(os.path.dirname(__file__), 'services'))
sys.path.append(os.path.dirname(__file__))

import threading
import time
import uuid
from types import FrameType, TracebackType
from typing import Any, TextIO
import glob

from services.events.events_services import EventsServices

import pydub
import imports as _ims
from qfluentwidgets import setTheme, Theme
import shiboken6

from utils.config_util import loadConfig, saveConfig, cfg, autosave_thread
from utils.favorite_util import loadFavorites, saveFavorites, favs
from utils.icon_util import refreshBoundIcons
from utils.play_util import AudioPlayer
import pyncm as ncm
from pyncm import apis
from utils import darkdetect_util as darkdetect
from utils.websocket_util import ws_server, ws_handler
from views.log_handler import LogHandler, hijackStreams
from views.launch_window import LaunchWindow
from views.search_page import SearchPage
from views.sidebar import Sidebar
from views.playing_page import PlayingPage
from views.desktop_lyrics import DesktopLyricsPage
from views.favorites_page import FavoritesPage
from views.session_page import SessionPage
from views.main_window import MainWindow
from views.error_popup import ErrorPopupWindow
from services.update import startUpdateCheck

logging_handler = LogHandler()
logging.basicConfig(level=logging.DEBUG, handlers=[logging_handler])
hijackStreams()

_logger = logging.getLogger(__name__)


def patchedExceptHook(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_traceback: TracebackType,
):
    global mwindow, launchwindow, app

    inf: list[str] = []

    _logger.error('| Unhandled Exception occurred |')
    _logger.error(f'Caused by {exc_type.__name__}')
    _logger.error('Traceback:')
    inf.append('| Unhandled Exception occurred |')
    inf.append(f'Caused by {exc_type.__name__}')
    inf.append('Traceback:')
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

pydub.AudioSegment.converter = r'ffmpeg\bin\ffmpeg.exe'
pydub.AudioSegment.ffmpeg = r'ffmpeg\bin\ffmpeg.exe'

if getattr(sys, 'frozen', False):
    base_dir = os.path.dirname(sys.executable)
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

ffmpeg_dir = os.path.join(base_dir, 'ffmpeg', 'bin')
os.environ['PATH'] = ffmpeg_dir + os.pathsep + os.environ['PATH']

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

app = _ims.QApplication(sys.argv)

mwindow: MainWindow | None = None
launchwindow: LaunchWindow | None = LaunchWindow(app)
player: AudioPlayer | None = None
sidebar: Sidebar | None = None
dp: PlayingPage | None = None
sp: SearchPage | None = None
dsp: DesktopLyricsPage | None = None
fp: FavoritesPage | None = None
sep: SessionPage | None = None
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
    if cfg.login_status and not ncm.GetCurrentSession().is_anonymous:
        apis.login.WriteLoginInfo(cfg.login_status)
    else:
        cfg.login_status = apis.login.GetCurrentLoginStatus()  # type: ignore

    def _themeChanged(theme: str):

        def _updateTheme():
            global mwindow, sidebar
            darkdetect._is_dark = darkdetect.getDarkdetect().isDark()
            setTheme(Theme.LIGHT if theme == 'Light' else Theme.DARK)
            app.setStyleSheet(
                f'QLabel {{ color: {'white' if darkdetect.isDark() else 'black'}; }}'
            )
            if sidebar:
                sidebar.updateTheme()
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
                for cache in caches:
                    os.remove(cache)
                if len(caches) > 0:
                    _logger.info(f'cleared {len(caches)} caches')

            time.sleep(10)

    threading.Thread(target=_cleanCaches, daemon=True).start()

    app.setStyleSheet(
        f'QLabel {{ color: {'white' if darkdetect.isDark() else 'black'}; }}'
    )
    setTheme(Theme.LIGHT if darkdetect.isLight() else Theme.DARK)

    app.processEvents()

    loadConfig()
    launchwindow.push('Loading config...')

    launchwindow.push('Loading fonts...')
    harmony_font_family = _ims.QFontDatabase.applicationFontFamilies(
        _ims.QFontDatabase.addApplicationFont('fonts/HARMONYOS_SANS_SC_REGULAR.ttf')
    )[0]

    from utils.lyric_util import LRCLyricParser, YRCLyricParser

    launchwindow.push('Initializing services...')

    mgr = LRCLyricParser()
    transmgr = LRCLyricParser()
    ymgr = YRCLyricParser()

    player = AudioPlayer()

    launchwindow.push('Loading favorites...')
    loadFavorites()

    autosave_thread.start()

    launchwindow.push('Logging in...')
    if cfg.session is None:
        apis.login.LoginViaAnonymousAccount()
        sstr = ncm.DumpSessionAsString(apis.GetCurrentSession())
        cfg.session = sstr
        cfg.login_status = apis.login.GetCurrentLoginStatus()  # type: ignore
        _logger.info('logged into generated anonymous account')
    else:
        ncm.SetCurrentSession(ncm.LoadSessionFromString(cfg.session))
        _logger.info('loaded session from config')

        if (
            cfg.login_method == 'cell phone' or cfg.login_method == 'QR code'
        ) and cfg.login_status:
            apis.login.WriteLoginInfo(cfg.login_status)
            _logger.info('wrote login info')

    csession = ncm.GetCurrentSession()
    csession.deviceId = uuid.uuid4().hex
    ncm.SetCurrentSession(csession)

    launchwindow.push('Initializing events services...')
    events_service = EventsServices(app)

    launchwindow.clear()
    launchwindow.subtitle('Phase 2 (initialize components...)')
    launchwindow.push('Initializing sidebar...')
    sidebar = Sidebar(
        None,
        None,
        player,
        ws_server,
        ws_handler,
        app,
        launchwindow=launchwindow,
    )
    launchwindow.push('Initializing playing page...')
    dp = PlayingPage(
        app,
        player,
        mgr,
        transmgr,
        ymgr,
        None,
        sidebar,
        favs,
        lock,
        ws_handler,
        harmony_font_family,
        launchwindow=launchwindow,
    )
    launchwindow.push('Initializing search page...')
    sp = SearchPage(None, launchwindow)
    launchwindow.push('Initializing desktop lyrics page...')
    dsp = DesktopLyricsPage(
        app,
        mgr,
        transmgr,
        ymgr,
        player,
        None,
        harmony_font_family,
        cfg,
        dp,
        launchwindow,
    )
    launchwindow.push('Initializing favorites page...')
    fp = FavoritesPage(dp, sidebar, None, launchwindow)
    launchwindow.push('Initializing session page...')
    sep = SessionPage(None, launchwindow)

    launchwindow.push('Initializing main window...')
    mwindow = MainWindow(
        app,
        dp,
        sp,
        dsp,
        fp,
        sep,
        sidebar,
        player,
        ws_server,
        ws_handler,
        launchwindow,
    )

    launchwindow.clear()
    launchwindow.subtitle('Phase 3 (inject dependencies...)')
    launchwindow.push('injecting Playing Page to sidebar')
    sidebar._dp = dp
    launchwindow.top('injecting Main Window to sidebar')
    sidebar._mwindow = mwindow
    launchwindow.top('injecting Main Window to Playing Page')
    dp._mwindow_obj = mwindow
    launchwindow.top('injecting Main Window to Playing Controller')
    dp.controller._mwindow = mwindow
    launchwindow.top('injecting Main Window to Playing Lyrics Viewer')
    dp.viewer._mwindow = mwindow
    launchwindow.top('injecting Main Window to Desktop Lyrics Viewer')
    dsp.viewer._mwindow = mwindow
    launchwindow.top('injecting Favorites page to Playing Page')
    dp._fp = fp
    launchwindow.top('injecting Main Window to Search Page')
    sp._mwindow = mwindow
    launchwindow.top('injecting Main Window to Favorites Page')
    fp._mwindow = mwindow
    launchwindow.top('injecting Main Window to Session Page')
    sep._mwindow = mwindow

    mwindow.init()

    fp.refresh()

    _ims.QTimer.singleShot(2000, lambda: startUpdateCheck(mwindow))

    _logger.debug(f'{sys.path=}')

    app.exec()
