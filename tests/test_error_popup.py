from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from views.error_popup import ErrorPopupWindow


class ErrorPopupTest(unittest.TestCase):
    _app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = cast(QApplication, QApplication.instance()) or QApplication([])

    def testCopiesCompletePlainTextWithTitle(self) -> None:
        clipboard = QApplication.clipboard()
        previous = clipboard.text()
        self.addCleanup(clipboard.setText, previous)
        parent = QWidget()
        detail = (
            'Permission denied: C:\\Temp\\segment_000.mkv\n<details> & 错误\n' * 100
        )
        popup = ErrorPopupWindow(detail, parent=parent, title='导出失败')
        expected = f'导出失败\n\n{detail}'
        try:
            self.assertEqual(popup.parentWidget(), parent)
            self.assertEqual(popup.windowTitle(), '导出失败')
            self.assertTrue(popup.detail_content.isReadOnly())
            QTest.keyClicks(popup.detail_content, 'changed')
            self.assertEqual(popup.detail_content.toPlainText(), expected)
            popup.copy_btn.click()
            self.assertEqual(clipboard.text(), expected)
        finally:
            popup.close()
            parent.close()

    def testExistingExceptionPopupStillExecutes(self) -> None:
        popup = ErrorPopupWindow('Traceback\nRuntimeError: boom')
        self.assertEqual(
            popup.detail_content.toPlainText(), 'Traceback\nRuntimeError: boom'
        )
        QTimer.singleShot(0, popup.reject)
        self.assertEqual(popup.exec(), QDialog.DialogCode.Rejected)


if __name__ == '__main__':
    unittest.main()
