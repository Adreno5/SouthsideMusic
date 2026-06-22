from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Callable, Literal
from urllib.request import Request, urlopen

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_EXE = SCRIPT_DIR / 'python' / 'python.exe'
FULL_REQUIREMENTS = SCRIPT_DIR / 'full_requirements.txt'
MAIN_SCRIPT = SCRIPT_DIR / 'src' / 'main.py'
SITE_PACKAGES = PYTHON_EXE.parent / 'Lib' / 'site-packages'

PYSIDE_REQUIREMENT_NAMES = {
    'pyside6',
    'pyside6-addons',
    'pyside6-essentials',
    'shiboken6',
}
PYSIDE_REQUIRED_FILES = [
    Path('PySide6') / 'Qt6WebEngineCore.dll',
    Path('PySide6') / 'Qt6WebEngineWidgets.dll',
    Path('PySide6') / 'QtWebEngineCore.pyd',
    Path('PySide6') / 'QtWebEngineWidgets.pyd',
    Path('PySide6') / 'QtWebEngineProcess.exe',
    Path('PySide6') / 'resources' / 'qtwebengine_resources.pak',
]

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
        line = line.strip()
        if '==' not in line:
            continue
        name, version = line.split('==', 1)
        result.append(RequirementInfo(name=name, version=version))
    return result


def normalizePackageName(name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower()


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
        if normalizePackageName(data['name']) == 'pip':
            continue
        result.append(RequirementInfo(name=data['name'], version=data['version']))
    return result


def getUnsatisfiedRequirements(
    installed: list[RequirementInfo], required: list[RequirementInfo]
) -> list[RequirementInfo]:
    installed_versions = {
        normalizePackageName(requirement.name): requirement.version
        for requirement in installed
    }
    return [
        requirement
        for requirement in required
        if installed_versions.get(normalizePackageName(requirement.name))
        != requirement.version
    ]


def getPySideRequirements(required: list[RequirementInfo]) -> list[RequirementInfo]:
    return [
        requirement
        for requirement in required
        if normalizePackageName(requirement.name) in PYSIDE_REQUIREMENT_NAMES
    ]


def isFullPySideInstalled() -> bool:
    return all((SITE_PACKAGES / path).exists() for path in PYSIDE_REQUIRED_FILES)


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
        installed = getInstalledPackages()
        required = getRequirements()
        unsatisfied = getUnsatisfiedRequirements(installed, required)
        pyside_incomplete = not isFullPySideInstalled()
        if not unsatisfied and not pyside_incomplete:
            self.allDone.emit()
            return

        installed_versions = {
            normalizePackageName(requirement.name): requirement.version
            for requirement in installed
        }
        for requirement in required:
            installed_version = installed_versions.get(
                normalizePackageName(requirement.name)
            )
            if installed_version == requirement.version:
                self.updateStatus(requirement.name, 'Installed')
            else:
                self.updateStatus(requirement.name, 'Waiting')

        if unsatisfied:
            returncode = self.runPipInstall(mirror_url, ['-r', str(FULL_REQUIREMENTS)])
            if returncode == 0:
                for requirement in required:
                    self.updateStatus(requirement.name, 'Installed')

        if pyside_incomplete:
            pyside_requirements = getPySideRequirements(required)
            for requirement in pyside_requirements:
                self.updateStatus(requirement.name, 'Uninstalling')
            self.runPipUninstall(
                [requirement.name for requirement in pyside_requirements]
            )
            for requirement in pyside_requirements:
                self.updateStatus(requirement.name, 'Uninstalled')
            returncode = self.runPipInstall(
                mirror_url,
                [
                    *[
                        f'{requirement.name}=={requirement.version}'
                        for requirement in pyside_requirements
                    ],
                ],
            )
            if returncode == 0:
                for requirement in pyside_requirements:
                    self.updateStatus(requirement.name, 'Installed')

        self.allDone.emit()

    def runPipInstall(self, mirror_url: str, args: list[str]) -> int:
        popen = subprocess.Popen(
            [str(PYTHON_EXE), '-m', 'pip', 'install', '--index-url', mirror_url, *args],
            cwd=str(SCRIPT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if not popen.stdout:
            popen.wait()
            return popen.returncode
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
        return popen.returncode

    def runPipUninstall(self, package_names: list[str]) -> int:
        popen = subprocess.Popen(
            [str(PYTHON_EXE), '-m', 'pip', 'uninstall', '-y', *package_names],
            cwd=str(SCRIPT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if popen.stdout:
            for line in popen.stdout:
                _logger.debug(line.strip())
        popen.wait()
        return popen.returncode

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
        except Exception:
            return

    def startTestLatency(self):
        installed = getInstalledPackages()
        required = getRequirements()
        unsatisfied = getUnsatisfiedRequirements(installed, required)
        pyside_incomplete = not isFullPySideInstalled()
        _logger.info(f'{len(installed)} installed, {len(required)} required')
        if not unsatisfied and not pyside_incomplete:
            _logger.info('all requirements satisfied')
            self.allDone.emit()
            return
        _logger.info(f'{len(unsatisfied)} requirements need install/update')
        if pyside_incomplete:
            _logger.info('PySide6 needs install overwrite to restore full files')

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
    bwindow.startTestLatency()
    app.exec()
