from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import zipfile
from pathlib import Path
from typing import cast
from unittest.mock import patch

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core.crash_handler import _showReport, writeReport


@unittest.skipUnless(sys.platform == 'win32', 'Windows process supervision')
class CrashHandlerTest(unittest.TestCase):
    def _runChild(self, body: str, *, expected_code: int | None = None) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = Path(__file__).resolve().parents[1] / 'src'
        script = root / 'crash_probe.py'
        script.write_text(
            'import sys, os, time, logging, ctypes\n'
            f'sys.path.insert(0, {str(source)!r})\n'
            'ctypes.windll.kernel32.SetErrorMode(3)\n'
            'print(os.getpid(), flush=True)\n'
            'from core.crash_handler import installCrashHandler\n'
            f'session = installCrashHandler({str(root)!r})\n'
            f'while not os.path.exists({str(root / "proceed")!r}): time.sleep(0.01)\n'
            'logging.getLogger("probe").warning("application log before failure")\n'
            'os.write(2, b"native stderr before failure\\n")\n' + textwrap.dedent(body),
            encoding='utf-8',
        )
        env = os.environ.copy()
        env['QT_QPA_PLATFORM'] = 'offscreen'
        env['PYTHONIOENCODING'] = 'utf-8'
        process = subprocess.Popen(
            [sys.executable, str(script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.addCleanup(self._stopProcess, process.pid)
        deadline = time.monotonic() + 15
        session: Path | None = None
        monitor: psutil.Process | None = None
        while time.monotonic() < deadline:
            sessions = list((root / 'data' / 'crashes').glob('*'))
            if sessions and (sessions[0] / 'monitor.ready').exists():
                session = sessions[0]
                try:
                    monitor = psutil.Process(
                        int((session / 'monitor.ready').read_text())
                    )
                except psutil.NoSuchProcess:
                    break
                self.addCleanup(self._stopProcess, monitor.pid)
                break
            time.sleep(0.02)
        self.assertIsNotNone(session)
        assert session is not None
        metadata = json.loads((session / 'session.json').read_text())
        (root / 'proceed').touch()
        stdout, stderr = process.communicate(timeout=15)
        actual_pid = int(stdout.splitlines()[0])
        self.assertEqual(metadata['pid'], actual_pid)
        if expected_code is not None:
            self.assertEqual(process.returncode, expected_code, (stdout, stderr))
        if expected_code == 0 and 'normal exit' in body:
            if monitor is not None:
                monitor.wait(timeout=10)
            self.assertFalse((session / 'report.txt').exists())
            self.assertFalse(session.exists())
            return session
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if (session / 'diagnostics.zip').exists() and (
                session / 'viewer.log'
            ).exists():
                break
            time.sleep(0.02)
        self.assertTrue(
            (session / 'report.txt').exists(),
            (session / 'monitor.log').read_text()
            if (session / 'monitor.log').exists()
            else 'No report generated',
        )
        self.assertTrue((session / 'diagnostics.zip').exists())
        self.assertTrue((session / 'viewer.log').exists())
        report = (session / 'report.txt').read_text(encoding='utf-8')
        self.assertIn('application log before failure', report)
        self.assertIn('native stderr before failure', report)
        with zipfile.ZipFile(session / 'diagnostics.zip') as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn('session.json', archive.namelist())
            self.assertEqual(
                json.loads(archive.read('session.json'))['pid'], actual_pid
            )
        return session

    def _stopProcess(self, pid: int) -> None:
        try:
            process = psutil.Process(pid)
            process.suspend()
            for child in process.children():
                self._stopProcess(child.pid)
            process.terminate()
            process.wait(timeout=5)
        except psutil.NoSuchProcess:
            pass

    def testAccessViolationAfterLoggingRedirection(self) -> None:
        session = self._runChild(
            """
            from views.log_handler import hijackStreams
            hijackStreams()
            print('redirected stdout before failure')
            import faulthandler
            faulthandler._read_null()
        """,
            expected_code=0xC0000005,
        )
        report = (session / 'report.txt').read_text(encoding='utf-8')
        self.assertIn('0xC0000005', report)
        self.assertIn('access violation', report.lower())
        self.assertIn('crash_probe.py', report)
        self.assertIn('redirected stdout before failure', report)

    def testAbort(self) -> None:
        session = self._runChild('os.abort()')
        self.assertIn(
            'abort', (session / 'report.txt').read_text(encoding='utf-8').lower()
        )

    def testQtFatal(self) -> None:
        session = self._runChild(
            'from core.crash_handler import installQtMessageHandler\n'
            'installQtMessageHandler()\n'
            'from PySide6.QtCore import qFatal\nqFatal("fatal Qt probe")'
        )
        self.assertIn(
            'fatal Qt probe', (session / 'report.txt').read_text(encoding='utf-8')
        )
        self.assertIn(
            'crash_probe.py', (session / 'report.txt').read_text(encoding='utf-8')
        )

    def testWindowsNativeStderr(self) -> None:
        session = self._runChild(
            """
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32
            kernel32.GetStdHandle.restype = wintypes.HANDLE
            kernel32.WriteFile.argtypes = (
                wintypes.HANDLE, ctypes.c_char_p, wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
            )
            data = b'Win32 stderr before failure\\n'
            written = wintypes.DWORD()
            assert kernel32.WriteFile(
                kernel32.GetStdHandle(-12), data, len(data),
                ctypes.byref(written), None,
            )
            os._exit(9)
            """,
            expected_code=9,
        )
        self.assertIn(
            'Win32 stderr before failure',
            (session / 'report.txt').read_text(encoding='utf-8'),
        )

    def testQtCallbackExceptionShowsWhileApplicationRuns(self) -> None:
        session = self._runChild(
            """
            from core.crash_handler import installQtMessageHandler
            installQtMessageHandler()
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QApplication
            app = QApplication([])
            def fail() -> None:
                raise RuntimeError('Qt callback failure probe')
            def finish() -> None:
                if os.path.exists(os.path.join(session, 'viewer.log')):
                    os.write(2, b'viewer started while application was running\\n')
                app.quit()
            QTimer.singleShot(0, fail)
            QTimer.singleShot(1200, finish)
            app.exec()
            """,
            expected_code=0,
        )
        report = (session / 'report.txt').read_text(encoding='utf-8')
        self.assertIn('Qt callback failure probe', report)
        self.assertIn(
            'viewer started while application was running',
            (session / 'native.log').read_text(encoding='utf-8'),
        )

    def testMonitorFailureStillReportsAndAllowsStartup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(__file__).resolve().parents[1] / 'src'
            code = textwrap.dedent(
                f"""
                import os, sys
                from unittest.mock import patch
                sys.path.insert(0, {str(source)!r})
                import core.crash_handler as handler
                with (
                    patch.object(handler.subprocess, 'Popen', side_effect=OSError('monitor launch failed')),
                    patch.object(handler, '_nativeMessage') as popup,
                ):
                    session = handler.installCrashHandler({directory!r})
                    assert popup.call_count == 1
                    assert handler.installCrashHandler({directory!r}) == session
                    try:
                        raise RuntimeError('exception without monitor')
                    except RuntimeError:
                        sys.excepthook(*sys.exc_info())
                    assert popup.call_count == 2
                    with open(os.path.join(session, 'report.txt'), encoding='utf-8') as file:
                        report = file.read()
                    assert 'monitor launch failed' in report
                    assert 'exception without monitor' in report
                print('startup continues', flush=True)
                """
            )
            process = subprocess.run(
                [sys.executable, '-c', code],
                capture_output=True,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertIn(b'startup continues', process.stdout)

    def testEarlyUnhandledException(self) -> None:
        session = self._runChild(
            'raise RuntimeError("startup failure probe")', expected_code=1
        )
        self.assertIn(
            'RuntimeError: startup failure probe',
            (session / 'report.txt').read_text(encoding='utf-8'),
        )

    def testThreadAndUnraisableExceptions(self) -> None:
        session = self._runChild(
            """
            import threading
            def worker() -> None:
                raise RuntimeError('thread failure probe')
            thread = threading.Thread(target=worker, name='probe-worker')
            thread.start()
            thread.join()
            class Broken:
                def __del__(self) -> None:
                    raise ValueError('destructor failure probe')
            del_object = Broken()
            del del_object
            time.sleep(0.6)
        """,
            expected_code=0,
        )
        report = (session / 'report.txt').read_text(encoding='utf-8')
        self.assertIn('thread failure probe', report)
        self.assertIn('probe-worker', report)
        self.assertIn('destructor failure probe', report)

    def testHardExitWithoutTraceback(self) -> None:
        session = self._runChild('os._exit(7)', expected_code=7)
        self.assertIn(
            '0x00000007', (session / 'report.txt').read_text(encoding='utf-8')
        )

    def testNormalExitDoesNotShowError(self) -> None:
        self._runChild('print("normal exit")', expected_code=0)

    def testUpdateExitDoesNotShowError(self) -> None:
        self._runChild('print("normal exit", flush=True)\nos._exit(0)', expected_code=0)


class CrashReportTest(unittest.TestCase):
    def testReportIncludesRotatedLogsAndPreservesCompleteArchive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'application.log').write_text(
                'old text\n' * 80000 + 'latest text', encoding='utf-8'
            )
            (root / 'application.log.1').write_text('previous log', encoding='utf-8')
            report = Path(writeReport(directory, -1073741819)).read_text(
                encoding='utf-8'
            )
            self.assertIn('0xC0000005', report)
            self.assertIn('latest text', report)
            self.assertIn('last 512 KiB', report)
            self.assertNotIn(b'\r\r\n', (root / 'report.txt').read_bytes())
            with zipfile.ZipFile(root / 'diagnostics.zip') as archive:
                self.assertEqual(archive.read('application.log.1'), b'previous log')
                self.assertEqual(
                    archive.read('application.log'),
                    (root / 'application.log').read_bytes(),
                )

    def testWindowCopiesAndExportsReport(self) -> None:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import (
            QApplication,
            QDialog,
            QFileDialog,
            QPlainTextEdit,
            QPushButton,
        )

        if QApplication.instance() is not None:
            result = subprocess.run(
                [
                    sys.executable,
                    __file__,
                    'CrashReportTest.testWindowCopiesAndExportsReport',
                ],
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return
        self.assertIsNone(QApplication.instance())
        with tempfile.TemporaryDirectory() as directory:
            report_path = writeReport(directory, 0xC0000409)
            output = os.path.join(directory, 'export.zip')
            errors = []

            def checkWindow() -> None:
                app = cast(QApplication, QApplication.instance())
                try:
                    dialog = next(
                        widget
                        for widget in app.topLevelWidgets()
                        if isinstance(widget, QDialog)
                    )
                    self.assertTrue(dialog.isVisible())
                    details = dialog.findChild(QPlainTextEdit)
                    self.assertIsNotNone(details)
                    assert details is not None
                    self.assertIn('0xC0000409', details.toPlainText())
                    buttons = {
                        button.text(): button
                        for button in dialog.findChildren(QPushButton)
                    }
                    buttons['复制详情 / Copy details'].click()
                    self.assertEqual(app.clipboard().text(), details.toPlainText())
                    buttons['导出日志包 / Export ZIP'].click()
                    self.assertEqual(
                        Path(output).read_bytes(),
                        Path(directory, 'diagnostics.zip').read_bytes(),
                    )
                    dialog.close()
                except BaseException as error:
                    errors.append(error)
                finally:
                    app.quit()

            real_exec = QApplication.exec

            def execWithCheck() -> int:
                QTimer.singleShot(0, checkWindow)
                return real_exec()

            with (
                patch.object(QApplication, 'exec', side_effect=execWithCheck),
                patch.object(
                    QFileDialog, 'getSaveFileName', return_value=(output, 'ZIP (*.zip)')
                ),
            ):
                _showReport(report_path)
            if errors:
                raise errors[0]


if __name__ == '__main__':
    unittest.main()
