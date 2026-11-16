from __future__ import annotations

import ctypes
import faulthandler
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import traceback
import zipfile
from ctypes import wintypes
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from logging.handlers import RotatingFileHandler
from types import TracebackType
from typing import Any, TextIO

_logger = logging.getLogger(__name__)
_session_dir: str | None = None
_native_file: TextIO | None = None
_monitor: subprocess.Popen[bytes] | None = None
_exception_lock = threading.Lock()

STD_ERROR_HANDLE = -12
SEM_NOGPFAULTERRORBOX = 0x0002

_EXIT_REASONS = {
    3: 'C/C++ runtime abort',
    0x40000015: 'Fatal application exit',
    0xC0000005: 'Access violation (invalid memory access)',
    0xC0000006: 'In-page error (memory / disk access failure)',
    0xC000001D: 'Illegal CPU instruction',
    0xC0000094: 'Integer division by zero',
    0xC00000FD: 'Stack overflow',
    0xC0000374: 'Heap corruption',
    0xC0000409: 'Fail-fast / stack buffer overrun',
    0xE06D7363: 'Unhandled C++ exception',
}


def nativeLogActive() -> bool:
    return _native_file is not None


def installCrashHandler(report_root: str | None = None) -> str:
    global _session_dir, _native_file, _monitor
    if _session_dir is not None:
        return _session_dir

    app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    session_name = f'{datetime.now():%Y%m%d-%H%M%S-%f}-{os.getpid()}'
    try:
        session_dir = os.path.join(
            report_root or app_root, 'data', 'crashes', session_name
        )
        os.makedirs(session_dir)
        native_file = open(
            os.path.join(session_dir, 'native.log'), 'a', encoding='utf-8'
        )
    except OSError:
        session_dir = tempfile.mkdtemp(prefix='SouthsideMusic-crash-')
        native_file = open(
            os.path.join(session_dir, 'native.log'), 'a', encoding='utf-8'
        )
    _session_dir = session_dir
    _native_file = native_file
    faulthandler.enable(file=native_file, all_threads=True)
    os.dup2(native_file.fileno(), 2)

    handler = RotatingFileHandler(
        os.path.join(session_dir, 'application.log'),
        maxBytes=2 * 1024 * 1024,
        backupCount=2,
        encoding='utf-8',
    )
    handler.setFormatter(
        logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s [%(threadName)s/%(thread)d] %(message)s'
        )
    )
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    sys.excepthook = _exceptionHook
    threading.excepthook = _threadExceptionHook
    sys.unraisablehook = _unraisableHook

    import msvcrt

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.SetStdHandle.argtypes = (wintypes.DWORD, wintypes.HANDLE)
    kernel32.SetStdHandle.restype = wintypes.BOOL
    if not kernel32.SetStdHandle(
        STD_ERROR_HANDLE, msvcrt.get_osfhandle(native_file.fileno())
    ):
        _logger.warning('Unable to redirect Windows stderr: %s', ctypes.WinError())
    kernel32.SetErrorMode(kernel32.GetErrorMode() | SEM_NOGPFAULTERRORBOX)

    app_version = 'unknown'
    try:
        with open(os.path.join(app_root, 'pyproject.toml'), 'rb') as file:
            app_version = tomllib.load(file)['project']['version']
    except (OSError, ValueError, KeyError):
        pass
    packages = {}
    for name in ('PySide6', 'shiboken6', 'numpy', 'scipy', 'sounddevice'):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = 'metadata unavailable'
    metadata = {
        'started_at': datetime.now().astimezone().isoformat(),
        'pid': os.getpid(),
        'app_version': app_version,
        'python': sys.version,
        'executable': sys.executable,
        'entry_point': os.path.abspath(sys.argv[0]),
        'working_directory': os.getcwd(),
        'windows': platform.platform(),
        'architecture': platform.machine(),
        'packages': packages,
    }
    with open(os.path.join(session_dir, 'session.json'), 'w', encoding='utf-8') as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)

    try:
        _monitor = subprocess.Popen(
            [
                sys.executable,
                os.path.abspath(__file__),
                '--watch',
                str(os.getpid()),
                session_dir,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=app_root,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        deadline = time.monotonic() + 10
        while not os.path.exists(os.path.join(session_dir, 'monitor.ready')):
            if _monitor.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError(f'Crash monitor failed to start: {session_dir}')
            time.sleep(0.01)
    except (OSError, RuntimeError):
        _monitor = None
        _logger.exception('Crash monitor unavailable')
        _nativeMessage(writeReport(session_dir))
        return session_dir
    _logger.info('Crash monitor ready; diagnostics: %s', session_dir)
    return session_dir


def installQtMessageHandler() -> None:
    from PySide6.QtCore import QMessageLogContext, QtMsgType, qInstallMessageHandler

    def handleMessage(
        message_type: QtMsgType, context: QMessageLogContext, message: str
    ) -> None:
        location = f'{context.file}:{context.line} {context.function}'
        detail = f'Qt {message_type.name}: {message}\n{location}\n'
        try:
            os.write(2, detail.encode('utf-8', errors='backslashreplace'))
            if message_type == QtMsgType.QtFatalMsg:
                faulthandler.dump_traceback(file=2, all_threads=True)
            level = {
                QtMsgType.QtDebugMsg: logging.DEBUG,
                QtMsgType.QtInfoMsg: logging.INFO,
                QtMsgType.QtWarningMsg: logging.WARNING,
                QtMsgType.QtCriticalMsg: logging.ERROR,
                QtMsgType.QtFatalMsg: logging.CRITICAL,
            }[message_type]
            logging.getLogger('Qt').log(level, '%s [%s]', message, location)
        except Exception:
            sys.__excepthook__(*sys.exc_info())

    qInstallMessageHandler(handleMessage)


def _exceptionHook(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_traceback: TracebackType | None,
) -> None:
    if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    try:
        detail = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        detail = f'{datetime.now().astimezone().isoformat()} [{threading.current_thread().name}]\n{detail}'
        with _exception_lock:
            if _session_dir is None:
                sys.__excepthook__(exc_type, exc_value, exc_traceback)
                return
            with open(
                os.path.join(_session_dir, 'exceptions.log'), 'a', encoding='utf-8'
            ) as file:
                file.write(detail + '\n')
        _logger.error(
            'Unhandled exception', exc_info=(exc_type, exc_value, exc_traceback)
        )
        if _monitor is None or _monitor.poll() is not None:
            report_path = writeReport(_session_dir)
            try:
                _startViewer(report_path)
            except OSError:
                _nativeMessage(report_path)
    except BaseException:
        sys.__excepthook__(exc_type, exc_value, exc_traceback)


def _threadExceptionHook(args: threading.ExceptHookArgs) -> None:
    if args.exc_type is SystemExit:
        return
    _exceptionHook(args.exc_type, args.exc_value or args.exc_type(), args.exc_traceback)


def _unraisableHook(args: Any) -> None:
    try:
        if _native_file is not None:
            _native_file.write(
                f'Unraisable exception: {args.err_msg}; object={args.object!r}\n'
            )
            _native_file.flush()
        _exceptionHook(args.exc_type, args.exc_value, args.exc_traceback)
    except Exception:
        sys.__unraisablehook__(args)


def writeReport(session_dir: str, exit_code: int | None = None) -> str:
    if exit_code is None:
        summary = '捕获到未处理异常 / Unhandled exception'
    else:
        code = exit_code & 0xFFFFFFFF
        reason = _EXIT_REASONS.get(code, 'Abnormal process termination')
        summary = f'程序异常退出 / Process crashed\n{reason}\nExit code: {exit_code} (0x{code:08X})'
    parts = [
        summary,
        f'Reported at: {datetime.now().astimezone().isoformat()}',
        f'Diagnostics: {session_dir}',
    ]
    for name in (
        'session.json',
        'exceptions.log',
        'native.log',
        'application.log',
        'monitor.log',
        'viewer.log',
    ):
        path = os.path.join(session_dir, name)
        try:
            with open(path, 'rb') as file:
                size = os.fstat(file.fileno()).st_size
                file.seek(max(0, size - 512 * 1024))
                content = (
                    file.read().decode('utf-8', errors='replace').replace('\r\n', '\n')
                )
            if size > 512 * 1024:
                content = (
                    '[Showing the last 512 KiB; full logs are in the ZIP.]\n' + content
                )
        except FileNotFoundError:
            content = '(not recorded)'
        except OSError as error:
            content = f'(unable to read: {error})'
        content = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', content)
        parts.append(f'===== {name} =====\n{content or "(empty)"}')
    report_path = os.path.join(session_dir, 'report.txt')
    with open(report_path + '.tmp', 'w', encoding='utf-8', newline='\n') as file:
        file.write('\n\n'.join(parts))
    os.replace(report_path + '.tmp', report_path)
    archive_path = os.path.join(session_dir, 'diagnostics.zip')
    with zipfile.ZipFile(archive_path + '.tmp', 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in (
            'report.txt',
            'session.json',
            'exceptions.log',
            'native.log',
            'application.log',
            'application.log.1',
            'application.log.2',
            'monitor.log',
            'viewer.log',
        ):
            try:
                archive.write(os.path.join(session_dir, name), name)
            except FileNotFoundError:
                pass
    os.replace(archive_path + '.tmp', archive_path)
    return report_path


def _watchProcess(pid: int, session_dir: str) -> None:
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    viewer: subprocess.Popen[bytes] | None = None
    exception_path = os.path.join(session_dir, 'exceptions.log')
    last_exception_size = 0
    report_path = os.path.join(session_dir, 'report.txt')
    exited = False
    try:
        with open(
            os.path.join(session_dir, 'monitor.ready.tmp'), 'w', encoding='utf-8'
        ) as file:
            file.write(str(os.getpid()))
        os.replace(
            os.path.join(session_dir, 'monitor.ready.tmp'),
            os.path.join(session_dir, 'monitor.ready'),
        )
        while True:
            wait_result = kernel32.WaitForSingleObject(handle, 250)
            if wait_result == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            exited = wait_result == 0
            exit_code = wintypes.DWORD()
            if exited and not kernel32.GetExitCodeProcess(
                handle, ctypes.byref(exit_code)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                exception_size = os.path.getsize(exception_path)
            except FileNotFoundError:
                exception_size = 0
            pending = exception_size != last_exception_size
            last_exception_size = exception_size
            crashed = exited and exit_code.value not in (0, 0xC000013A)
            if pending or crashed:
                writeReport(session_dir, exit_code.value if crashed else None)
                if viewer is None or viewer.poll() is not None:
                    viewer = _startViewer(report_path)
            if viewer is not None and viewer.poll() not in (None, 0):
                _nativeMessage(report_path)
                viewer = None
            if exited:
                if viewer is not None and viewer.wait() != 0:
                    _nativeMessage(report_path)
                return
    finally:
        kernel32.CloseHandle(handle)
        if exited and not os.path.exists(report_path):
            for name in (
                'native.log',
                'application.log',
                'application.log.1',
                'application.log.2',
                'session.json',
                'monitor.ready',
            ):
                try:
                    os.remove(os.path.join(session_dir, name))
                except OSError:
                    pass
            try:
                os.rmdir(session_dir)
            except OSError:
                pass


def _startViewer(report_path: str) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env['QT_OPENGL'] = 'software'
    env['QT_QUICK_BACKEND'] = 'software'
    with open(
        os.path.join(os.path.dirname(report_path), 'viewer.log'), 'ab', buffering=0
    ) as output:
        return subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), '--show', report_path],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=output,
            env=env,
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )


def _nativeMessage(report_path: str) -> None:
    try:
        with open(report_path, encoding='utf-8') as file:
            summary = file.read(1600).split('=====')[0]
    except OSError as error:
        summary = str(error)
    message = f'{summary}\n\n完整报告 / Full report:\n{report_path}\n\n打开完整报告？ / Open full report?'
    if (
        ctypes.windll.user32.MessageBoxW(None, message, 'Southside Music — Crash', 0x14)
        == 6
    ):
        os.startfile(report_path)


def _showReport(report_path: str) -> None:
    from PySide6.QtCore import Qt, QTimer, QUrl
    from PySide6.QtGui import QDesktopServices, QFontDatabase, QIcon
    from PySide6.QtWidgets import (
        QApplication,
        QDialog,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QVBoxLayout,
    )

    app = QApplication([])
    dialog = QDialog()
    dialog.setWindowTitle('Southside Music — 错误报告 / Error report')
    dialog.setWindowIcon(
        QIcon(os.path.join(os.path.dirname(__file__), '..', '..', 'icons', 'app.ico'))
    )
    dialog.resize(940, 680)
    layout = QVBoxLayout(dialog)
    title = QLabel('Southside Music 遇到了错误 / An error occurred')
    layout.addWidget(title)
    description = QLabel(
        '诊断信息已保存。可以复制详情，或导出包含日志的 ZIP 文件。\nDiagnostics saved. Copy details or export the logs as a ZIP file.'
    )
    description.setWordWrap(True)
    layout.addWidget(description)
    details = QPlainTextEdit()
    details.setReadOnly(True)
    details.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
    layout.addWidget(details)
    location = QLabel(os.path.dirname(report_path))
    location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    location.setWordWrap(True)
    layout.addWidget(location)
    buttons = QHBoxLayout()
    layout.addLayout(buttons)
    copy_button = QPushButton('复制详情 / Copy details')
    export_button = QPushButton('导出日志包 / Export ZIP')
    open_button = QPushButton('打开文件夹 / Open folder')
    close_button = QPushButton('关闭 / Close')
    for button in (copy_button, export_button, open_button, close_button):
        buttons.addWidget(button)

    last_modified = 0

    def refresh() -> None:
        nonlocal last_modified
        try:
            modified = os.stat(report_path).st_mtime_ns
            if modified == last_modified:
                return
            with open(report_path, encoding='utf-8') as file:
                text = file.read()
            details.setPlainText(text)
            title.setText(text.splitlines()[0])
            last_modified = modified
        except OSError as error:
            title.setText(str(error))

    def export() -> None:
        destination, _ = QFileDialog.getSaveFileName(
            dialog,
            '导出日志包 / Export diagnostics',
            f'SouthsideMusic-{os.path.basename(os.path.dirname(report_path))}.zip',
            'ZIP (*.zip)',
        )
        if destination:
            try:
                shutil.copyfile(
                    os.path.join(os.path.dirname(report_path), 'diagnostics.zip'),
                    destination,
                )
            except OSError as error:
                QMessageBox.warning(dialog, '导出失败 / Export failed', str(error))

    copy_button.clicked.connect(lambda: app.clipboard().setText(details.toPlainText()))
    export_button.clicked.connect(export)
    open_button.clicked.connect(
        lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(os.path.dirname(report_path))
        )
    )
    close_button.clicked.connect(dialog.accept)
    timer = QTimer(dialog)
    timer.timeout.connect(refresh)
    timer.start(500)
    refresh()
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    app.exec()


if __name__ == '__main__':
    if sys.argv[1] == '--watch':
        try:
            _watchProcess(int(sys.argv[2]), sys.argv[3])
        except Exception:
            with open(
                os.path.join(sys.argv[3], 'monitor.log'), 'a', encoding='utf-8'
            ) as file:
                traceback.print_exc(file=file)
            _nativeMessage(writeReport(sys.argv[3]))
    elif sys.argv[1] == '--show':
        try:
            _showReport(sys.argv[2])
        except Exception:
            traceback.print_exc()
            _nativeMessage(sys.argv[2])
