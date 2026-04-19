from typing import Any, Callable, Optional, Dict, List
from qfluentwidgets import * # type: ignore
from PySide6.QtCore import (
    QThread, Qt, QTimer, Signal, QMutex, QWaitCondition, QObject, Slot
)
from PySide6.QtWidgets import QLabel, QMessageBox, QApplication
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import math


class LoadingBox(MessageBoxBase):
    def __init__(self, parent=None, txt: str = ''):
        super().__init__(parent)

        self.ring = IndeterminateProgressRing()
        self.text = SubtitleLabel(txt)

        self.viewLayout.addWidget(
            self.ring,
            alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter
        )
        self.viewLayout.addWidget(
            self.text,
            alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter
        )

        self.cancelButton.hide()
        self.yesButton.hide()
        self.buttonGroup.hide()


class DownloadBox(MessageBoxBase):
    downloadStarted = Signal()
    receiveProgress = Signal(float)
    downloadFinished = Signal(bytes)

    MAX_CHUNK_THREADS = 12
    MIN_CHUNK_SIZE = 1

    def __init__(
        self,
        parent=None,
        txt: str = '',
        url: str = '',
        headers: Optional[Dict] = None,
        data: Optional[Dict] = None
    ):
        super().__init__(parent)
        self.par = parent
        self.url = url
        self.headers = headers.copy() if headers else {}
        self.data = data

        self._total_length = 0
        self._downloaded_bytes = 0
        self._chunk_results: Dict[int, bytes] = {}
        self._mutex = QMutex()
        self._finished_chunks = 0
        self._total_chunks = 0
        self._error_occurred = False
        self._final_data = bytes()

        self.iring = IndeterminateProgressRing()
        self.ring = ProgressRing()
        self.ring.setMaximum(1)
        self.ring.hide()
        self.percent = QLabel('Connecting...')
        self.text = SubtitleLabel(txt)

        self.viewLayout.addWidget(
            self.iring,
            alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter
        )
        self.viewLayout.addWidget(
            self.ring,
            alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter
        )
        self.viewLayout.addWidget(
            self.percent,
            alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter
        )
        self.viewLayout.addWidget(
            self.text,
            alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter
        )

        self.cancelButton.hide()
        self.yesButton.hide()
        self.buttonGroup.hide()

        self._main_thread = QThread(self)
        self._main_thread.run = self._start_download
        self._main_thread.start()

    def _start_download(self):
        self.downloadStarted.emit()

        try:
            with requests.head(
                self.url,
                headers=self.headers,
                timeout=10,
                allow_redirects=True
            ) as head_resp:
                head_resp.raise_for_status()
                self._total_length = int(head_resp.headers.get('content-length', 0))
                accept_ranges = head_resp.headers.get('accept-ranges', '').lower() == 'bytes'
        except Exception as e:
            self.percent.setText('Download failed! (HEAD request)')
            self.downloadFinished.emit(bytes())
            return

        if self._total_length <= 0:
            self._fallback_single_download()
            return

        if not accept_ranges:
            self._fallback_single_download()
            return

        chunk_size = max(
            self.MIN_CHUNK_SIZE,
            math.ceil(self._total_length / self.MAX_CHUNK_THREADS)
        )
        ranges = []
        start = 0
        while start < self._total_length:
            end = min(start + chunk_size - 1, self._total_length - 1)
            ranges.append((start, end))
            start = end + 1

        self._total_chunks = len(ranges)
        self._chunk_results = {i: b'' for i in range(self._total_chunks)}

        threads = []
        for idx, (start, end) in enumerate(ranges):
            thread = QThread(self)
            worker = self._ChunkDownloader(idx, start, end, self)
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.progress.connect(self._on_chunk_progress)
            worker.finished.connect(self._on_chunk_finished)
            worker.error.connect(self._on_chunk_error)
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            threads.append(thread)

            if not hasattr(self, '_workers'):
                self._workers = []
            self._workers.append(worker)

        for thread in threads:
            thread.start()

        self.iring.hide()
        self.ring.show()
        self.ring.setVal(0)
        self.percent.setText('Downloading... (0.00%)')

    def _fallback_single_download(self):
        try:
            session = requests.Session()
            response = session.get(
                self.url,
                headers=self.headers,
                data=self.data,
                stream=True,
                timeout=30
            )
            self._total_length = int(response.headers.get('content-length', 0))
        except Exception as e:
            self.percent.setText('Download failed!')
            self.downloadFinished.emit(bytes())
            return

        downloaded = 0
        final_data = bytearray()
        chunk_size = 1024 * 1024
        for chunk in response.iter_content(chunk_size=chunk_size):
            if chunk:
                if not self.ring.isVisible():
                    self.ring.show()
                    self.iring.hide()

                final_data.extend(chunk)
                downloaded += len(chunk)
                if self._total_length > 0:
                    progress = downloaded / self._total_length
                    self.receiveProgress.emit(progress)
                    self.ring.setVal(progress)
                    self.percent.setText(f'Downloading... ({round(progress * 100, 2)}%)')
                else:
                    self.percent.setText(f'Downloading... ({downloaded} bytes)')

        self._final_data = bytes(final_data)
        self.downloadFinished.emit(self._final_data)

    @Slot(int, int)
    def _on_chunk_progress(self, chunk_idx: int, bytes_downloaded: int):
        self._mutex.lock()
        self._downloaded_bytes = sum(
            len(data) for data in self._chunk_results.values()
        )
        self._mutex.unlock()

        if self._total_length > 0:
            progress = self._downloaded_bytes / self._total_length
            self.receiveProgress.emit(progress)
            self.ring.setVal(progress)
            self.percent.setText(f'Downloading... ({round(progress * 100, 2)}%)')

    @Slot(int, bytes)
    def _on_chunk_finished(self, chunk_idx: int, data: bytes):
        self._mutex.lock()
        self._chunk_results[chunk_idx] = data
        self._finished_chunks += 1
        all_finished = (self._finished_chunks == self._total_chunks)
        self._mutex.unlock()

        if all_finished and not self._error_occurred:
            final = bytearray()
            for i in range(self._total_chunks):
                final.extend(self._chunk_results[i])
            self._final_data = bytes(final)
            self.downloadFinished.emit(self._final_data)

    @Slot(int, str)
    def _on_chunk_error(self, chunk_idx: int, error_msg: str):
        self._mutex.lock()
        if not self._error_occurred:
            self._error_occurred = True
            self.percent.setText(f'Download failed! Chunk {chunk_idx} error')
            self.downloadFinished.emit(bytes())
        self._mutex.unlock()

    class _ChunkDownloader(QObject):
        progress = Signal(int, int)
        finished = Signal(int, bytes)
        error = Signal(int, str)

        def __init__(self, idx: int, start: int, end: int, parent: 'DownloadBox'):
            super().__init__()
            self.idx = idx
            self.start = start
            self.end = end
            self.parent_ = parent
            self.url = parent.url
            self.headers = parent.headers.copy()
            self.data = parent.data

        @Slot()
        def run(self):
            try:
                headers = self.headers.copy()
                headers['Range'] = f'bytes={self.start}-{self.end}'
                response = requests.get(
                    self.url,
                    headers=headers,
                    data=self.data,
                    stream=True,
                    timeout=30
                )
                response.raise_for_status()

                chunk_data = bytearray()
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        chunk_data.extend(chunk)
                        self.progress.emit(self.idx, len(chunk_data))

                expected = self.end - self.start + 1
                if len(chunk_data) != expected:
                    raise ValueError(
                        f"Chunk size mismatch: expected {expected}, got {len(chunk_data)}"
                    )

                self.finished.emit(self.idx, bytes(chunk_data))

            except Exception as e:
                self.error.emit(self.idx, str(e))


def downloadWithMultiThreading(
    url: str,
    headers: Optional[Dict] = None,
    data: Optional[Dict] = None,
    parent=None,
    txt: str = '',
    finished: Optional[Callable[[bytes], None]] = None
) -> DownloadBox:
    box = DownloadBox(parent, txt, url, headers, data)

    def __finish(data: bytes):
        box.accept()
        if finished:
            finished(data)

    box.downloadFinished.connect(__finish)
    box.show()
    return box


def doWithMultiThreading(
    task: Callable,
    args: tuple,
    parent,
    txt: str,
    finished: Optional[Callable[[], None]] = None,
    dialog: bool = True
):
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