from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from typing import cast, override
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtWidgets import QApplication
import shiboken6

from core.downloader import TaskManager


class BackgroundTaskTests(unittest.TestCase):
    _app: QApplication | None = None

    @classmethod
    @override
    def setUpClass(cls) -> None:
        cls._app = cast(QApplication, QApplication.instance()) or QApplication([])

    @override
    def setUp(self) -> None:
        self.parent = QObject()

    @override
    def tearDown(self) -> None:
        if shiboken6.isValid(self.parent):
            shiboken6.delete(self.parent)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        cast(QApplication, self._app).processEvents()

    def test_parent_deleted_while_task_runs_skips_finished_callback(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        arguments: list[str] = []

        def task(value: str) -> None:
            arguments.append(value)
            entered.set()
            if not release.wait(5):
                raise TimeoutError('Test did not release the background task')

        manager = TaskManager(task, ('payload',), self.parent)
        finished = Mock()
        manager.taskFinished.connect(finished)
        thread_errors: list[threading.ExceptHookArgs] = []
        with patch('threading.excepthook', side_effect=thread_errors.append):
            manager.start()
            worker = manager._thread
            self.assertIsNotNone(worker)
            try:
                self.assertTrue(entered.wait(2), 'Background task did not start')
                self.parent.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.assertFalse(shiboken6.isValid(self.parent))
                self.assertFalse(shiboken6.isValid(manager))
            finally:
                release.set()
                cast(threading.Thread, worker).join(2)
            self.assertFalse(cast(threading.Thread, worker).is_alive())
        cast(QApplication, self._app).processEvents()
        self.assertEqual(arguments, ['payload'])
        self.assertEqual(
            [error.exc_value for error in thread_errors],
            [],
            'Completing a task after Qt deletion raised an unhandled thread exception',
        )
        finished.assert_not_called()
        self.assertEqual(manager.args, ())
        self.assertIsNot(manager.task, task)

    def test_deletion_between_validity_check_and_emit_is_handled(self) -> None:
        task = Mock()
        manager = TaskManager(task, ('payload',), self.parent)
        finished = Mock()
        manager.taskFinished.connect(finished)
        is_valid = shiboken6.isValid

        def deleteAfterCheck(obj: QObject) -> bool:
            valid = is_valid(obj)
            if valid and obj is manager:
                shiboken6.delete(self.parent)
            return valid

        with patch('core.downloader.shiboken6.isValid', side_effect=deleteAfterCheck):
            manager._run()
        self.assertFalse(shiboken6.isValid(self.parent))
        self.assertFalse(shiboken6.isValid(manager))
        task.assert_called_once_with('payload')
        finished.assert_not_called()
        self.assertEqual(manager.args, ())
        self.assertIsNot(manager.task, task)

    def test_live_parent_receives_finished_callback_once(self) -> None:
        task = Mock()
        manager = TaskManager(task, ('payload',), self.parent)
        finished = Mock()
        manager.taskFinished.connect(finished)
        manager._run()
        self.assertTrue(shiboken6.isValid(manager))
        task.assert_called_once_with('payload')
        finished.assert_called_once_with()
        self.assertEqual(manager.args, ())
        self.assertIsNot(manager.task, task)


if __name__ == '__main__':
    unittest.main()
