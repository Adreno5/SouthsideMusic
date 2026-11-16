from __future__ import annotations

import json
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any, override

from PySide6.QtCore import QThread, QTimer, Signal, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSplitter,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from harness.catalog import CASES, scenarioFor
from harness.gateway import Gateway
from harness.reports import writeReports
from harness.runner import checkGateway
from harness.session import ApplicationSession


class OperationThread(QThread):
    finishedOperation = Signal(object)

    def __init__(self, parent: QWidget, operation: Callable[[], Any]) -> None:
        super().__init__(parent)
        self.operation = operation

    @override
    def run(self) -> None:
        try:
            self.finishedOperation.emit({'ok': True, 'result': self.operation()})
        except BaseException:
            self.finishedOperation.emit({'ok': False, 'error': traceback.format_exc()})


class HarnessPanel(QWidget):
    def __init__(self, root: Path, output: Path) -> None:
        super().__init__()
        self.root = root
        self.output = output
        self.gateway = Gateway(output / 'gateway')
        self.session: ApplicationSession | None = None
        self.worker: OperationThread | None = None
        self.results: list[dict[str, Any]] = []
        self.buttons: list[QPushButton] = []
        self.setWindowTitle('SouthsideMusic · Harness')
        self.resize(1160, 800)
        self.setStyleSheet(
            'QWidget{background:#191b20;color:#ddd;font-size:14px}'
            'QLineEdit,QPlainTextEdit,QListWidget,QComboBox{background:#111318;padding:6px}'
            'QPushButton{background:#303640;padding:8px;border-radius:4px}'
            'QPushButton:disabled{color:#666}'
        )
        layout = QVBoxLayout(self)
        self.status = QLabel('Start creates an isolated, real SouthsideMusic instance.')
        layout.addWidget(self.status)
        controls = QHBoxLayout()
        layout.addLayout(controls)
        self.start_button = self._button(controls, 'Start', self._start)
        self._button(controls, 'Stop application', self._stop)
        self._button(controls, 'Snapshot', lambda: self._request('snapshot'))
        self._button(controls, 'Screenshot', lambda: self._request('capture'))
        splitter = QSplitter()
        layout.addWidget(splitter, 1)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        splitter.addWidget(left)
        self.cases = QListWidget()
        for case in CASES:
            self.cases.addItem(case['group'] + ' / ' + case['id'])
        self.cases.setCurrentRow(0)
        left_layout.addWidget(self.cases)
        case_controls = QHBoxLayout()
        left_layout.addLayout(case_controls)
        self._button(case_controls, 'Run selected', self._runSelected)
        self._button(case_controls, 'Load scenario', self._loadScenario)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        splitter.addWidget(right)
        self.scenario = QPlainTextEdit()
        self.scenario.setPlainText(
            json.dumps(scenarioFor('llm_chat'), ensure_ascii=False, indent=2)
        )
        right_layout.addWidget(QLabel('Gateway scenario JSON'))
        right_layout.addWidget(self.scenario, 1)
        actions = QHBoxLayout()
        right_layout.addLayout(actions)
        self.text = QLineEdit('Harness')
        actions.addWidget(self.text, 1)
        self._button(
            actions,
            'Search',
            lambda: self._request('search', {'text': self._input_text}),
        )
        self._button(actions, 'Play', lambda: self._request('play'))
        assistant = QHBoxLayout()
        right_layout.addLayout(assistant)
        self.provider = QComboBox()
        self.provider.addItems([
            'Harness Chat',
            'Harness Responses',
            'Harness Anthropic',
        ])
        assistant.addWidget(self.provider)
        self._button(assistant, 'Send AI task', self._send)
        self._button(assistant, 'Confirm', lambda: self._request('confirm'))
        self._button(
            assistant, 'Cancel plan', lambda: self._request('confirm', {'cancel': True})
        )
        self._button(assistant, 'Stop stream', lambda: self._request('stop'))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        right_layout.addWidget(self.log, 1)
        splitter.setSizes([330, 830])

    def _button(
        self, layout: QHBoxLayout, text: str, action: Callable[[], Any]
    ) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(lambda: self._dispatch(action))
        layout.addWidget(button)
        self.buttons.append(button)
        return button

    def _dispatch(self, action: Callable[[], Any]) -> None:
        if self.worker and self.worker.isRunning():
            return
        self._selected_case = CASES[self.cases.currentRow()]
        self._input_text = self.text.text()
        self._provider_name = self.provider.currentText()
        self._scenario_text = self.scenario.toPlainText()
        for button in self.buttons:
            button.setEnabled(False)
        self.worker = OperationThread(self, action)
        self.worker.finishedOperation.connect(self._finished)
        self.worker.start()

    def _finished(self, response: dict[str, Any]) -> None:
        for button in self.buttons:
            button.setEnabled(True)
        self.log.appendPlainText(json.dumps(response, ensure_ascii=False, indent=2))
        self.status.setText(
            'Ready' if response['ok'] else 'Operation failed; see details below.'
        )

    def _start(self) -> Any:
        if self.session is None:
            self.session = ApplicationSession(
                self.root, self.gateway.start(), self.output / 'application'
            )
        return self.session.ready

    def _stop(self) -> Any:
        if self.session:
            self.session.close()
            self.session = None
        self.gateway.stop()
        return {'stopped': True}

    def _request(self, operation: str, data: dict[str, Any] | None = None) -> Any:
        if not self.session:
            raise RuntimeError('Start the application first')
        return self.session.request(operation, data)

    def _loadScenario(self) -> dict[str, Any]:
        case = self._selected_case
        value = scenarioFor(case['id'])
        QTimer.singleShot(
            0,
            self,
            lambda: self.scenario.setPlainText(
                json.dumps(value, ensure_ascii=False, indent=2)
            ),
        )
        return value

    def _send(self) -> Any:
        self.gateway.setScenario(json.loads(self._scenario_text))
        return self._request(
            'llm',
            {
                'text': self._input_text,
                'provider': self._provider_name,
                'wait': False,
            },
        )

    def _runSelected(self) -> Any:
        case = self._selected_case
        if case['group'] in ('native', 'whisper'):
            raise RuntimeError('Use the CLI for native and Whisper scenarios')
        self._start()
        self.gateway.setScenario(scenarioFor(case['id']))
        started = time.monotonic()
        result: dict[str, Any] = {**case, 'status': 'passed', 'detail': ''}
        try:
            assert self.session is not None
            value = self.session.request('run_case', case=case['id'])
            if 'skipped' in value:
                result.update(status='skipped', detail=value['skipped'])
            else:
                checkGateway(case['id'], self.gateway)
        except Exception:
            result.update(status='failed', detail=traceback.format_exc())
        result['duration'] = time.monotonic() - started
        self.results.append(result)
        writeReports(self.output, self.results)
        return result

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker and self.worker.isRunning():
            event.ignore()
            self.status.setText('Wait for the current operation before closing.')
            return
        self._stop()
        event.accept()


def runManual(root: Path, output: Path, smoke: bool = False) -> int:
    app = QApplication.instance() or QApplication([])
    panel = HarnessPanel(root, output)
    panel.show()
    exit_code = 0
    if smoke:
        state = {'started': time.monotonic(), 'clicked': False}
        timer = QTimer(panel)

        def check() -> None:
            nonlocal exit_code
            if not state['clicked']:
                state['clicked'] = True
                QTest.mouseClick(panel.start_button, Qt.MouseButton.LeftButton)
            elif panel.worker and not panel.worker.isRunning():
                exit_code = int(panel.session is None)
                panel.grab().save(str(output / 'panel.png'))
                panel.close()
                app.quit()
            elif time.monotonic() - state['started'] > 75:
                exit_code = 1
                app.quit()

        timer.timeout.connect(check)
        timer.start(100)
    app.exec()
    if panel.worker and panel.worker.isRunning():
        panel.worker.wait(10000)
    panel._stop()
    return exit_code
