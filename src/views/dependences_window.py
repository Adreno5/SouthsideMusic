from __future__ import annotations

import logging
import os
import subprocess
import sys as _sys
from threading import Thread
from typing import TYPE_CHECKING


import hPyT
import requests
import shiboken6

from core.audio_player import getAudioDevices
from core.downloader import asyncDownload
from core.dialogs import getValueBylist
from core import theme
from imports import (
    ProgressBar,
    PushButton,
    QSizePolicy,
    QSpacerItem,
    QTimer,
    Qt,
    QVBoxLayout,
    QWidget,
    Signal,
    SubtitleLabel,
    bindText,
    tr,
)

if TYPE_CHECKING:
    from core.app_context import AppContext


class DependencesWindow(QWidget):
    check_done = Signal(str, bool, str)
    all_checked = Signal()
    _configure_pydub = Signal(str, str)

    update_progress = Signal(float)

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._results: dict[str, bool] = {}
        self.logger = logging.getLogger(__name__)

        self.check_done.connect(self._on_check_done, Qt.ConnectionType.QueuedConnection)
        self._configure_pydub.connect(
            self._set_pydub_config, Qt.ConnectionType.QueuedConnection
        )

        self.setWindowTitle(tr('dependences_window.dependences_checking'))
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint
        )
        self.setStyleSheet(
            f'background-color: {"black" if theme.isDark() else "white"};'
        )

        layout = QVBoxLayout()

        self.probar = ProgressBar()
        self.probar.hide()
        layout.addWidget(self.probar)

        self.ffmpeg_label = SubtitleLabel()
        bindText(self.ffmpeg_label, 'dependences_window.ffmpeg_checking')
        layout.addWidget(self.ffmpeg_label)
        self.ffmpeg_btn = PushButton('')
        bindText(self.ffmpeg_btn, 'dependences_window.download_ffmpeg_automatically')
        self.ffmpeg_btn.clicked.connect(self.downloadFFmpeg)
        self.ffmpeg_btn.hide()
        layout.addWidget(self.ffmpeg_btn)

        self.python_runtime_label = SubtitleLabel()
        bindText(
            self.python_runtime_label, 'dependences_window.python_runtime_checking'
        )
        layout.addWidget(self.python_runtime_label)

        self.audio_output_label = SubtitleLabel()
        bindText(self.audio_output_label, 'dependences_window.audio_output_checking')
        layout.addWidget(self.audio_output_label)

        self.network_label = SubtitleLabel()
        bindText(self.network_label, 'dependences_window.network_checking')
        layout.addWidget(self.network_label)

        self.opengl_label = SubtitleLabel()
        bindText(self.opengl_label, 'dependences_window.opengl_checking')
        layout.addWidget(self.opengl_label)

        layout.addSpacerItem(
            QSpacerItem(
                0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
        )

        self.setLayout(layout)

        self.show()

        self.updatePosition()
        QTimer.singleShot(300, self.startCheck)
        self.ctx.app.processEvents()

    def downloadFFmpeg(self):
        self.ffmpeg_btn.setEnabled(False)
        self.resize(self.ctx.app.primaryScreen().size() * 0.5)
        hPyT.window_frame.center(self)
        source = getValueBylist(
            self,
            'select a source to download',
            'choose by your network',
            ['BtBN (Github)', 'gyan.dev'],
        )
        self.ffmpeg_label.setStyleSheet('')
        if not source:
            self.ffmpeg_btn.setEnabled(True)
            return

        url = (
            'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n7.1-latest-win64-lgpl-shared-7.1.zip'
            if source == 'BtBN (Github)'
            else 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
        )

        self.probar.setRange(0, 1000)
        self.probar.setValue(0)
        self.probar.show()
        self.ffmpeg_label.setText(tr('dependences_window.ffmpeg_downloading'))
        self.ctx.app.processEvents()

        def _progress(cur: float):
            self.probar.setValue(int(cur * 1000))
            self.ffmpeg_label.setText(
                tr('dependences_window.ffmpeg_downloading_percent', percent=cur * 100)
            )

        def _finished(data: bytes):
            if not data:
                self.ffmpeg_label.setText(
                    tr('dependences_window.ffmpeg_download_failed')
                )
                self.ffmpeg_btn.setEnabled(True)
                return

            self.ffmpeg_label.setText(tr('dependences_window.ffmpeg_extracting'))
            self.ctx.app.processEvents()

            base_dir = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            zip_path = os.path.join(base_dir, 'ffmpeg_temp.zip')
            ffmpeg_dir = os.path.join(base_dir, 'ffmpeg')

            try:
                with open(zip_path, 'wb') as f:
                    f.write(data)

                import zipfile

                with zipfile.ZipFile(zip_path, 'r') as zf:
                    zf.extractall(base_dir)

                import shutil

                for item in os.listdir(base_dir):
                    item_path = os.path.join(base_dir, item)
                    if (
                        os.path.isdir(item_path)
                        and item.startswith('ffmpeg')
                        and item != 'ffmpeg'
                    ):
                        if os.path.exists(ffmpeg_dir):
                            shutil.rmtree(ffmpeg_dir)
                        os.rename(item_path, ffmpeg_dir)
                        break
            except Exception as e:
                self.logger.exception('Failed to extract FFmpeg')
                self.logger.exception(e)
                self.ffmpeg_label.setText(
                    tr('dependences_window.ffmpeg_extraction_failed')
                )
                self.ffmpeg_btn.setEnabled(True)
                return
            finally:
                if os.path.exists(zip_path):
                    try:
                        os.remove(zip_path)
                    except OSError:
                        pass

            self._add_ffmpeg_to_path(ffmpeg_dir)

            self.ffmpeg_label.setText(tr('dependences_window.ffmpeg_checking_2'))
            self.ctx.app.processEvents()
            self.ffmpeg_btn.hide()
            self.checkFFmpeg()

        manager = asyncDownload(url, parent=self, finished=_finished)
        manager.receiveProgress.connect(_progress)

    def _set_pydub_config(self, ffmpeg_exe: str, ffprobe_exe: str) -> None:
        import pydub

        pydub.AudioSegment.converter = ffmpeg_exe
        if os.path.isfile(ffprobe_exe):
            import pydub.utils as _pu

            _pu.get_prober_name = lambda: ffprobe_exe

    def _add_ffmpeg_to_path(self, ffmpeg_dir: str) -> None:
        bin_path = os.path.abspath(os.path.join(ffmpeg_dir, 'bin'))
        ffmpeg_exe = os.path.join(bin_path, 'ffmpeg.exe')
        ffprobe_exe = os.path.join(bin_path, 'ffprobe.exe')

        self._configure_pydub.emit(ffmpeg_exe, ffprobe_exe)

        current_path = os.environ.get('PATH', '')
        if bin_path.lower() in (p.lower() for p in current_path.split(os.pathsep)):
            return

        try:
            import ctypes
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r'Environment',
                0,
                winreg.KEY_READ | winreg.KEY_SET_VALUE,
            )
            try:
                user_path, _ = winreg.QueryValueEx(key, 'PATH')
            except FileNotFoundError:
                user_path = ''

            if bin_path.lower() in (p.lower() for p in user_path.split(os.pathsep)):
                key.Close()
                return

            new_path = (user_path + os.pathsep + bin_path) if user_path else bin_path
            winreg.SetValueEx(key, 'PATH', 0, winreg.REG_EXPAND_SZ, new_path)
            key.Close()

            os.environ['PATH'] = current_path + os.pathsep + bin_path

            HWND_BROADCAST = 0xFFFF
            WM_SETTINGCHANGE = 0x001A
            SMTO_ABORTIFHUNG = 0x0002
            ctypes.windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST,
                WM_SETTINGCHANGE,
                0,
                'Environment',
                SMTO_ABORTIFHUNG,
                5000,
                None,
            )
        except Exception:
            self.logger.exception('Failed to add FFmpeg to PATH')

    def _on_check_done(self, name: str, ok: bool, detail: str) -> None:
        key = name.lower().replace(' ', '_')
        label = getattr(self, f'{key}_label')
        status = tr('dependences_window.ok') if ok else tr('dependences_window.failed')
        label.setText(
            tr(
                'dependences_window.name_status_detail',
                name=tr(name),
                status=status,
                detail=detail,
            )
        )
        label.setStyleSheet(f'color: {"green" if ok else "red"}')
        self._results[name] = ok
        if not all(self._results.values()):
            self.ctx.dependences_available = False
        if (
            len(self._results) == 5
            and self.ctx is not None
            and all(self._results.values())
        ):
            self.ctx.dependences_available = True
            self.all_checked.emit()
            QTimer.singleShot(500, self.close)

        if name == 'FFmpeg' and not ok:
            self.ffmpeg_btn.show()

        self.updatePosition()
        self.ctx.app.processEvents()

    def updatePosition(self) -> None:
        if (
            len(self._results) == 5
            and self.ctx is not None
            and not all(self._results.values())
        ):
            hPyT.window_frame.center(self)
        else:
            center_x = self.ctx.app.primaryScreen().availableGeometry().center().x()
            center_y = self.ctx.app.primaryScreen().availableGeometry().center().y()
            if self.ctx.launch_window is not None and shiboken6.isValid(
                self.ctx.launch_window
            ):
                self.move(
                    int(center_x + self.ctx.launch_window.width() / 2),
                    int(center_y - self.height() / 2),
                )

    def checkFFmpeg(self) -> None:
        try:
            base_dir = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            ffmpeg_exe = os.path.join(base_dir, 'ffmpeg', 'bin', 'ffmpeg.exe')
            output = subprocess.run(
                [ffmpeg_exe, '-version'], text=True, capture_output=True
            )
            version = (
                output.stdout.splitlines()[0]
                .removeprefix('ffmpeg version ')
                .split(' ')[0]
            )
            self.logger.info(f'FFmpeg found version: {version}')
            self._add_ffmpeg_to_path(os.path.join(base_dir, 'ffmpeg'))
            self.check_done.emit('FFmpeg', True, version)
        except Exception as e:
            self.logger.warning(f'FFmpeg not found: {e}')
            self.check_done.emit('FFmpeg', False, str(e))

    def checkRuntime(self) -> None:
        version = f'{_sys.version_info.major}.{_sys.version_info.minor}.{_sys.version_info.micro}'
        self.logger.info(f'Python Runtime found version: {version}')
        self.check_done.emit('Python Runtime', True, version)

    def checkAudio(self) -> None:
        try:
            devices = getAudioDevices()
            if devices:
                self.logger.info(f'Audio Output found: {len(devices)} device(s)')
                self.check_done.emit(
                    'Audio Output',
                    True,
                    tr('dependences_window.count_device_s', count=len(devices)),
                )
            else:
                self.logger.warning('Audio Output not found: no output device')
                self.check_done.emit(
                    'Audio Output', False, tr('dependences_window.no_output_device')
                )
        except Exception as e:
            self.logger.warning(f'Audio Output not found: {e}')
            self.check_done.emit('Audio Output', False, str(e))

    def checkNetwork(self) -> None:
        try:
            r = requests.head('https://music.163.com', timeout=8)
            ms = r.elapsed.total_seconds() * 1000
            self.logger.info(f'Network found latency: {ms:.0f}ms')
            self.check_done.emit('Network', True, f'{ms:.0f}ms')
        except Exception as e:
            self.logger.warning(f'Network not found: {e}')
            self.check_done.emit('Network', False, str(e))

    def checkOpenGL(self) -> None:
        from PySide6.QtGui import QOpenGLContext, QOffscreenSurface

        try:
            surface = QOffscreenSurface()
            surface.create()
            gl_ctx = QOpenGLContext()
            gl_ctx.create()
            gl_ctx.makeCurrent(surface)
            valid = gl_ctx.isValid()
            gl_ctx.doneCurrent()
            if valid:
                self.logger.info('OpenGL found: available')
                self.check_done.emit('OpenGL', True, tr('dependences_window.available'))
            else:
                self.logger.warning('OpenGL not found: no valid context')
                self.check_done.emit(
                    'OpenGL', False, tr('dependences_window.no_valid_context')
                )
        except Exception as e:
            self.logger.warning(f'OpenGL not found: {e}')
            self.check_done.emit('OpenGL', False, str(e))

    def startCheck(self) -> None:
        threads: list[Thread] = []
        threads.append(Thread(target=self.checkFFmpeg, daemon=True))
        threads.append(Thread(target=self.checkRuntime, daemon=True))
        threads.append(Thread(target=self.checkAudio, daemon=True))
        threads.append(Thread(target=self.checkNetwork, daemon=True))
        threads.append(Thread(target=self.checkOpenGL, daemon=True))

        for thread in threads:
            thread.start()
            self.updatePosition()
            self.ctx.app.processEvents()
        for thread in threads:
            thread.join()
            self.updatePosition()
            self.ctx.app.processEvents()
