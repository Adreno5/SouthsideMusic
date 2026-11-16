from __future__ import annotations

import json
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO

import psutil

from harness.environment import Sandbox, stopProcessTree
from harness.media_server import MediaServer


class ApplicationSession:
    def __init__(
        self,
        root: Path,
        gateway_url: str,
        output: Path,
        native: bool = False,
        sandbox: Sandbox | None = None,
    ) -> None:
        self.output = output
        self.sandbox = sandbox
        self._owns_sandbox = sandbox is None
        self.media: MediaServer | None = None
        self.log: BinaryIO | None = None
        self.process: subprocess.Popen[Any] | None = None
        self.connection: socket.socket | None = None
        self.ready: dict[str, Any] = {}
        self._buffer = bytearray()
        self._closed = False
        self._stop_tracking = threading.Event()
        self._children_lock = threading.Lock()
        self._children: set[psutil.Process] = set()
        self._tracker: threading.Thread | None = None
        self._parent: psutil.Process | None = None
        listener: socket.socket | None = None
        try:
            output.mkdir(parents=True, exist_ok=True)
            if self.sandbox is None:
                self.sandbox = Sandbox(root, gateway_url, native)
            self.media = MediaServer(self.sandbox.root / 'fixtures')
            media_url = self.media.start()
            listener = socket.socket()
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            listener.settimeout(0.1)
            address = f'127.0.0.1:{listener.getsockname()[1]}'
            token = secrets.token_hex(16)
            self.log = (output / 'application.log').open('wb')
            command = [
                sys.executable,
                str(root / 'scripts' / 'test_harness.py'),
                '_app',
                '--root',
                str(self.sandbox.root),
                '--address',
                address,
                '--token',
                token,
                '--media-url',
                media_url,
                '--report-dir',
                str(output),
            ]
            if native:
                command.append('--native')
            self.process = subprocess.Popen(
                command,
                cwd=self.sandbox.root,
                env=self.sandbox.environment,
                stdout=self.log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            try:
                self._parent = psutil.Process(self.process.pid)
            except psutil.NoSuchProcess:
                pass
            self._tracker = threading.Thread(target=self._trackProcesses, daemon=True)
            self._tracker.start()
            deadline = time.monotonic() + 60
            while self.connection is None:
                if self.process.poll() is not None:
                    raise RuntimeError(
                        f'Application exited before handshake (exit={self.process.poll()})'
                    )
                if time.monotonic() >= deadline:
                    raise TimeoutError('Application startup timed out')
                try:
                    self.connection, _ = listener.accept()
                except TimeoutError:
                    continue
            hello = self._receive(deadline - time.monotonic())
            if hello.get('token') != token:
                raise RuntimeError('Invalid application handshake')
            ready = self._receive(deadline - time.monotonic())
            if not ready.get('ok'):
                raise RuntimeError(str(ready.get('error')))
            self.ready = ready
        except BaseException as error:
            try:
                self.close()
            except Exception as cleanup_error:
                error.add_note(f'Application cleanup failed: {cleanup_error}')
            raise
        finally:
            if listener is not None:
                listener.close()

    def _trackProcesses(self) -> None:
        while not self._stop_tracking.is_set():
            with self._children_lock:
                parents = list(self._children)
            if self._parent is not None:
                parents.append(self._parent)
            for parent in parents:
                try:
                    children = parent.children(recursive=True)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                with self._children_lock:
                    self._children.update(children)
            self._stop_tracking.wait(0.05)

    def _receive(self, timeout: float = 90) -> dict[str, Any]:
        assert self.connection is not None
        assert self.process is not None
        deadline = time.monotonic() + timeout
        while b'\n' not in self._buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Application response timed out')
            self.connection.settimeout(min(remaining, 0.1))
            try:
                chunk = self.connection.recv(65536)
            except TimeoutError:
                if self.process.poll() is not None:
                    raise RuntimeError(
                        f'Application disconnected (exit={self.process.poll()})'
                    ) from None
                continue
            if not chunk:
                raise RuntimeError(
                    f'Application disconnected (exit={self.process.poll()})'
                )
            self._buffer.extend(chunk)
        line, _, rest = self._buffer.partition(b'\n')
        self._buffer = bytearray(rest)
        response = json.loads(line)
        if not isinstance(response, dict):
            raise RuntimeError('Invalid application response')
        return response

    def request(
        self,
        operation: str,
        data: dict[str, Any] | None = None,
        case: str = '',
        timeout: float = 90,
    ) -> Any:
        assert self.connection is not None
        started = time.monotonic()
        self.connection.settimeout(timeout)
        payload = {'op': operation, 'data': data or {}, 'case': case}
        self.connection.sendall(
            (json.dumps(payload, ensure_ascii=False) + '\n').encode()
        )
        response = self._receive(timeout - (time.monotonic() - started))
        if not response.get('ok'):
            raise AssertionError(response.get('error', 'Unknown application error'))
        return response.get('result')

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[Exception] = []
        if self.process is not None:
            if self.process.poll() is None and self.ready:
                try:
                    self.request('shutdown', timeout=10)
                    self.process.wait(timeout=10)
                except (
                    OSError,
                    ValueError,
                    AssertionError,
                    RuntimeError,
                    subprocess.TimeoutExpired,
                ):
                    pass
            with self._children_lock:
                children = list(self._children)
            try:
                stopProcessTree(self.process, children)
            except Exception as error:
                errors.append(error)
        self._stop_tracking.set()
        if self._tracker is not None and self._tracker.ident is not None:
            self._tracker.join(timeout=3)
        for stream in (self.connection, self.log):
            if stream is not None:
                try:
                    stream.close()
                except OSError as error:
                    errors.append(error)
        if self.media is not None:
            try:
                if self.media.thread.ident is not None:
                    self.media.stop()
                else:
                    self.media.server.server_close()
            except Exception as error:
                errors.append(error)
        if self.sandbox is not None:
            try:
                for name in ('config.json', 'favorites.json', 'fixture-state.json'):
                    source = self.sandbox.root / name
                    if source.is_file():
                        shutil.copy2(source, self.output / name)
                crashes = self.sandbox.root / 'data' / 'crashes'
                if crashes.is_dir():
                    shutil.copytree(
                        crashes, self.output / 'crashes', dirs_exist_ok=True
                    )
                for source in self.sandbox.root.rglob('*.log'):
                    target = (
                        self.output
                        / 'sandbox-logs'
                        / source.relative_to(self.sandbox.root)
                    )
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
            except Exception as error:
                errors.append(error)
            if self._owns_sandbox and not errors:
                try:
                    self.sandbox.close()
                except Exception as error:
                    errors.append(error)
        if errors:
            raise ExceptionGroup('Application cleanup failed', errors)
