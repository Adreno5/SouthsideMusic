from __future__ import annotations

import json
import logging
from pathlib import Path
import subprocess
import threading
import time
from typing import Callable, Literal
from PySide6.QtWidgets import *  # type: ignore
from PySide6.QtCore import *  # type: ignore
from PySide6.QtGui import QColor

SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_EXE = SCRIPT_DIR / 'python' / 'python.exe'
FULL_REQUIREMENTS = SCRIPT_DIR / 'full_requirements.txt'
MAIN_SCRIPT = SCRIPT_DIR / 'src' / 'main.py'

from urllib.request import Request, urlopen

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler()],
)
_logger = logging.getLogger('bootstrap')

MIRRORS: dict[str, str] = {
    'PyPI': 'https://pypi.org/simple/',
    'Tsinghua': 'https://pypi.tuna.tsinghua.edu.cn/simple/',
    'Aliyun': 'https://mirrors.aliyun.com/pypi/simple/',
    'Tencent': 'https://mirrors.cloud.tencent.com/pypi/simple/',
    'USTC': 'https://pypi.mirrors.ustc.edu.cn/simple/',
    'Huawei': 'https://repo.huaweicloud.com/repository/pypi/simple/',
}


def runMain() -> None:
    bwindow.hide()

    _logger.debug('spawning main: %s %s', PYTHON_EXE, MAIN_SCRIPT)
    proc = subprocess.Popen(
        [str(PYTHON_EXE), str(MAIN_SCRIPT)],
        cwd=str(SCRIPT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )
    if not proc.stdout:
        app.quit()
        return
    for line in proc.stdout:
        print(line.strip())
    proc.wait()
    if proc.returncode != 0:
        _logger.error('main.py exited with code %d', proc.returncode)
    app.quit()


class RequirementInfo:
    def __init__(self, name: str, version: str) -> None:
        self.name = name
        self.version = version

    name: str
    version: str


def getRequirements() -> list[RequirementInfo]:
    result = []
    for line in FULL_REQUIREMENTS.read_text().splitlines():
        try:
            result.append(
                RequirementInfo(name=line.split('==')[0], version=line.split('==')[1])
            )
        except:
            pass
    return result


def getInstalledPackages() -> list[RequirementInfo]:
    result = []
    output = subprocess.run(
        [str(PYTHON_EXE), '-m', 'pip', 'list', '--format', 'json'],
        cwd=str(SCRIPT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    ).stdout
    parsed = json.loads(output)
    for data in parsed:
        if data['name'] == 'pip':
            continue
        result.append(RequirementInfo(name=data['name'], version=data['version']))
    return result


class BootstrapWindow(QWidget):
    latencyFinished = Signal(str, str, float)
    allDone = Signal()

    task = Signal(object)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.NoTitleBarBackgroundHint
        )
        self.setWindowTitle('Setting up Enviroment')
        self.setFixedSize(app.primaryScreen().size() * 0.35)

        self._layout = QVBoxLayout()
        self.status_label = QLabel('Testing latency of mirrors')
        self._layout.addWidget(self.status_label)
        self.setLayout(self._layout)

        self.latency_test_threads = []
        self.latency_testing = False
        self.latencyFinished.connect(self.latencyTestFinished)
        self.startTestLatency()

        self.task.connect(self.doTask)

        self.show()

    def doTask(self, content: object):
        if isinstance(content, Callable):
            content()

    def latencyTestFinished(self, mirror_name: str, mirror_url: str, latency: float):
        _logger.info(f'latency test finished: {mirror_name} {mirror_url} {latency}s')
        for thread in self.latency_test_threads:
            thread.join()

        self.status_table = QTableWidget()
        self.status_table.setColumnCount(2)
        self.status_table.setHorizontalHeaderLabels(['Package', 'Status'])
        self.status_table.setRowCount(len(getRequirements()))
        for i, requirement in enumerate(getRequirements()):
            self.status_table.setItem(i, 0, QTableWidgetItem(requirement.name))
            item = QTableWidgetItem('Waiting')
            self.status_table.setItem(i, 1, item)
        self._layout.addWidget(self.status_table)
        threading.Thread(
            target=self.installRequirements, args=(mirror_name, mirror_url, latency)
        ).start()

    def installRequirements(self, mirror_name: str, mirror_url: str, latency: float):
        self.status_label.setText(
            f'Installing requirements with mirror {mirror_name} | {int(latency * 1000)}ms'
        )
        installed = {r.name: r.version for r in getInstalledPackages()}
        required = {r.name: r.version for r in getRequirements()}

        for name in installed:
            self.updateStatus(name, 'Uninstalling')
            popen = subprocess.Popen(
                [str(PYTHON_EXE), '-m', 'pip', 'uninstall', '-y', name],
                cwd=str(SCRIPT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            if popen.stdout:
                for line in popen.stdout:
                    _logger.debug(line.strip())
            popen.wait()
            self.updateStatus(name, 'Uninstalled')

        for name in required:
            self.updateStatus(name, 'Waiting')
        popen = subprocess.Popen(
            [
                str(PYTHON_EXE),
                '-m',
                'pip',
                'install',
                '--index-url',
                mirror_url,
                '-r',
                str(FULL_REQUIREMENTS),
            ],
            cwd=str(SCRIPT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if not popen.stdout:
            self.allDone.emit()
            return
        for line in popen.stdout:
            c = line.strip()
            _logger.debug(line.strip())
            if 'Collecting' in c:
                package = c.split('Collecting ')[1].split(' ')[0]
                self.updateStatus(package, 'Collecting')
            elif 'Downloading' in c:
                package = c.split('Downloading ')[1].split(' ')[0]
                self.updateStatus(package, 'Downloading')
            elif 'Using cached' in c:
                package = c.split('Using cached ')[1].split(' ')[0]
                self.updateStatus(package, 'Installed')
        popen.wait()

        self.allDone.emit()

    def updateStatus(
        self,
        package_name: str,
        status: Literal[
            'Collecting',
            'Downloading',
            'Installed',
            'Waiting',
            'Uninstalling',
            'Uninstalled',
        ],
    ):
        def text(item, text):
            def _set():
                item.setText(text)

            self.task.emit(_set)

        def foreground(item, color_string):
            def _set():
                item.setForeground(QColor(color_string))

            self.task.emit(_set)

        def resetColor(item):
            def _set():
                item.setForeground(Qt.GlobalColor.gray)

            self.task.emit(_set)

        for line in range(self.status_table.rowCount()):
            _1item = self.status_table.item(line, 0)
            _2item = self.status_table.item(line, 1)
            if _1item is None or _2item is None:
                continue

            if status in [
                'Collecting',
                'Downloading',
                'Installed',
            ] and _2item.text() in ['Downloading', 'Collecting']:
                text(_2item, 'Installed')
                foreground(_2item, '#00FF00')

            if _1item.text().upper().replace('-', '_') in package_name.upper().replace(
                '-', '_'
            ):
                text(_2item, status)
                if status == 'Installed':
                    foreground(_2item, '#00FF00')
                elif status == 'Collecting':
                    foreground(_2item, '#FFFF00')
                elif status == 'Downloading':
                    foreground(_2item, '#00FFFF')
                elif status == 'Uninstalling':
                    foreground(_2item, '#FF0000')
                elif status == 'Uninstalled':
                    foreground(_2item, '#D400FF')
                elif status == 'Waiting':
                    resetColor(_2item)

    def testLatency(self, mirror_name: str, mirror_url: str):
        _logger.debug('testing latency of %s: %s', mirror_name, mirror_url)
        request = Request(mirror_url)
        start_time = time.perf_counter()
        try:
            with urlopen(request) as response:
                if response.status != 200 or not self.latency_testing:
                    return
                end_time = time.perf_counter()
                latency = end_time - start_time
                self.latency_testing = False
                self.latencyFinished.emit(mirror_name, mirror_url, latency)
        except Exception as e:
            return

    def startTestLatency(self):
        installed = {r.name: r.version for r in getInstalledPackages()}
        required = {r.name: r.version for r in getRequirements()}
        _logger.info(f'{len(installed)} installed, {len(required)} required')
        if installed == required:
            _logger.info('all requirements satisfied')
            self.allDone.emit()
            return

        for mirror_name, mirror_url in MIRRORS.items():
            thread = threading.Thread(
                target=self.testLatency, args=(mirror_name, mirror_url)
            )
            self.latency_test_threads.append(thread)

        self.latency_testing = True
        for thread in self.latency_test_threads:
            thread.start()


if __name__ == '__main__':
    app = QApplication([])
    bwindow = BootstrapWindow()
    bwindow.allDone.connect(runMain)
    app.exec()
