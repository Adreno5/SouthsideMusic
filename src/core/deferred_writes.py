from __future__ import annotations

import logging
import threading
from collections.abc import Callable

_logger = logging.getLogger(__name__)


class DeferredWriter:
    def __init__(self, name: str) -> None:
        self.name = name
        self._lock = threading.Lock()
        self._pending: Callable[[], None] | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._idle = threading.Event()
        self._idle.set()

    def submit(self, task: Callable[[], None]) -> None:
        thread: threading.Thread | None = None
        with self._lock:
            self._pending = task
            if not self._running:
                self._running = True
                self._idle.clear()
                thread = threading.Thread(
                    target=self._drain,
                    daemon=True,
                    name=f'southside-{self.name}-writer',
                )
                self._thread = thread
        if thread is not None:
            thread.start()

    def flush(self, timeout: float = 2.0) -> bool:
        if threading.current_thread() is self._thread:
            return True
        return self._idle.wait(timeout)

    def _drain(self) -> None:
        while True:
            with self._lock:
                task = self._pending
                self._pending = None
                if task is None:
                    self._running = False
                    self._thread = None
                    self._idle.set()
                    return
            try:
                task()
            except Exception as error:
                _logger.exception('%s deferred write failed', self.name)
                _logger.debug(error)


_writers: dict[str, DeferredWriter] = {}
_writers_lock = threading.Lock()


def getDeferredWriter(name: str) -> DeferredWriter:
    with _writers_lock:
        writer = _writers.get(name)
        if writer is None:
            writer = DeferredWriter(name)
            _writers[name] = writer
        return writer


def flushDeferredWrites(timeout: float = 2.0) -> bool:
    with _writers_lock:
        writers = tuple(_writers.values())
    return all(writer.flush(timeout) for writer in writers)
