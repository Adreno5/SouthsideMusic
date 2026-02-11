from typing import Any, Callable
from qfluentwidgets import * # type: ignore
from PySide6.QtCore import QThread, Qt, QTimer
from PySide6.QtWidgets import QLabel, QMessageBox, QApplication
import requests

class LoadingBox(MessageBoxBase):
    def __init__(self, parent=None, txt:str=''):
        super().__init__(parent)

        self.ring = IndeterminateProgressRing()
        self.text = SubtitleLabel(txt)

        self.viewLayout.addWidget(self.ring, alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)
        self.viewLayout.addWidget(self.text, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        self.cancelButton.hide()
        self.yesButton.hide()

        self.buttonGroup.hide()

class DownloadBox(MessageBoxBase):
    downloadStarted = Signal()
    receiveProgress = Signal(float) # percent
    downloadFinished = Signal(bytes)

    def __init__(self, parent=None, txt:str='', url: str='', headers: dict | None=None, data: dict | None=None):
        super().__init__(parent)
        self.par = parent
        self.downloaded = 0
        self.data_length = 0
        self.finish_data = bytes()

        self.headers = headers
        self.url = url
        self.data = data

        self.iring = IndeterminateProgressRing()
        self.ring = ProgressRing()
        self.ring.setMaximum(1)
        self.ring.hide()
        self.percent = QLabel('Connecting...')
        self.text = SubtitleLabel(txt)

        self.viewLayout.addWidget(self.iring, alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)
        self.viewLayout.addWidget(self.ring, alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)
        self.viewLayout.addWidget(self.percent, alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)
        self.viewLayout.addWidget(self.text, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        self.cancelButton.hide()
        self.yesButton.hide()

        self.buttonGroup.hide()

        self.thread_ = QThread(self)
        self.thread_.run = self._download
        self.thread_.start()

    def _download(self):
        self.downloadStarted.emit()

        try:
            response = requests.get(self.url, headers=self.headers, data=self.data, stream=True)
            self.data_length = int(response.headers['content-length'])
        except Exception as e:
            self.percent.setText('Download failed!')
            self.downloadFinished.emit(self.finish_data)
            return

        for chunk in response.iter_content(chunk_size=512):
            if chunk:
                if not self.ring.isVisible():
                    self.ring.show()
                    self.iring.hide()

                self.finish_data += chunk
                self.downloaded += len(chunk)
                progress = (self.downloaded / self.data_length)
                self.receiveProgress.emit(progress)
                self.ring.setVal(progress)
                self.percent.setText(f'Downloading... ({round(progress * 100, 2)}%)')

        self.downloadFinished.emit(self.finish_data)

def downloadWithMultiThreading(url: str, headers: dict | None=None, data: dict | None=None, parent=None, txt: str='', finished: Callable[[bytes], None] | None=None) -> DownloadBox:
    box = DownloadBox(parent, txt, url, headers, data)
    
    def __finish(bytes: bytes):
        box.accept()
        if finished:
            finished(bytes)
    
    box.downloadFinished.connect(__finish)

    box.show()

    return box

def doWithMultiThreading(task: Callable, args: tuple, parent, txt: str, finished: Callable[[], None] | None=None, dialog: bool=True):
    box = LoadingBox(parent, txt)

    def __finish():
        if dialog:
            box.accept()
        if finished:
            finished()

        thread.quit()

    thread = QThread(parent)
    thread.run = lambda: task(*args)
    thread.finished.connect(__finish)
    thread.start()

    if dialog:
        box.show()