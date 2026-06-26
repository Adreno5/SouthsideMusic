from __future__ import annotations

import datetime
import logging

import os
import re
import shutil
import sys
import threading
import time
from typing import TextIO, Optional

from colorama import Fore, Style, init

init(autoreset=True)

_ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def _visible_len(text: str) -> int:
    return len(_ANSI_ESCAPE.sub('', text))


def _wrap_visible_text(text: str, width: int) -> list[str]:
    if width <= 0:
        return [text]
    lines: list[str] = []
    for raw_line in text.splitlines() or ['']:
        line = raw_line
        while _visible_len(line) > width:
            pos = 0
            visible = 0
            while pos < len(line) and visible < width:
                if line[pos] == '\x1b':
                    match = _ANSI_ESCAPE.match(line, pos)
                    if match:
                        pos = match.end()
                        continue
                pos += 1
                visible += 1
            lines.append(line[:pos])
            line = line[pos:]
        lines.append(line)
    return lines


class LogHandler(logging.Handler):
    def __init__(self, level: int | str = 0) -> None:
        super().__init__(level)
        self.buffer: str = ''

    def emit(self, record: logging.LogRecord) -> None:
        if record.name in ('openai._base_client', 'httpcore.http11', 'httpcore.connection', 'httpcore.proxy'):
            return

        message = record.getMessage()

        color = {
            'DEBUG': Fore.LIGHTBLACK_EX,
            'INFO': Fore.LIGHTGREEN_EX,
            'WARNING': Fore.YELLOW,
            'ERROR': Fore.RED,
            'CRITICAL': Fore.RED,
        }.get(record.levelname, Fore.WHITE)

        time_str = datetime.datetime.now().strftime('%H:%M:%S')
        plain_prefix = f'[{time_str}/{record.levelname}] [{record.name}] - '
        plain_suffix = f'[{record.thread}/{record.threadName}]'

        try:
            term_width = shutil.get_terminal_size().columns
        except Exception:
            term_width = 80

        suffix_width = _visible_len(plain_suffix)

        colored_prefix = (
            f'[{Fore.LIGHTBLACK_EX}{time_str}{Style.RESET_ALL}/'
            f'{color}{Style.BRIGHT}{record.levelname}{Style.RESET_ALL}] '
            f'{Fore.LIGHTBLACK_EX}[{record.name}] -{Style.RESET_ALL} '
        )
        colored_suffix = (
            f'{Fore.GREEN}[{Fore.LIGHTBLACK_EX}'
            f'{record.thread}/{record.threadName}'
            f'{Fore.GREEN}]{Style.RESET_ALL}'
        )

        prefix_width = _visible_len(plain_prefix)
        content_width = max(term_width - prefix_width - suffix_width - 1, 1)
        continuation_prefix = ' ' * prefix_width

        match = re.search(r'(?:https?://)?(?:[\w-]+\.)?music\.126\.net', message)
        if match:  # NeteaseCloudMusic private resource domain
            domain_end = match.end()
            remaining = message[domain_end:]
            m = re.search(r'\s', remaining)
            path_end = domain_end + m.start() if m else len(message)
            path = message[domain_end:path_end]
            masked = re.sub(r'[^/?.=&%_\-]', '*', path)
            message = message[:domain_end] + masked + message[path_end:]

        if content_width < 15:
            fallback_width = max(term_width - prefix_width - 1, 20)
            lines = _wrap_visible_text(message, fallback_width)
            rendered_lines = []
            for i, line in enumerate(lines):
                prefix = colored_prefix if i == 0 else continuation_prefix
                rendered_lines.append(f'{prefix}{line}')
        else:
            lines = _wrap_visible_text(message, content_width)
            rendered_lines = []
            for i, line in enumerate(lines):
                prefix = colored_prefix if i == 0 else continuation_prefix
                spaces = max(
                    term_width
                    - _visible_len(prefix)
                    - _visible_len(line)
                    - suffix_width,
                    1,
                )
                final = f'{prefix}{line}{" " * spaces}{colored_suffix}'
                rendered_lines.append(final)
        assert sys.__stdout__ is not None
        sys.__stdout__.write('\n'.join(rendered_lines) + '\n')
        sys.__stdout__.flush()


class LoggingStream:
    def __init__(self, level: int = logging.DEBUG, source: str = 'stderr'):
        self._logger = logging.getLogger(__name__)
        self.level = level
        self.source = source
        self.buffer = ''
        self.original_stream: Optional[TextIO] = None

    def write(self, message: str) -> int:
        if not message:
            return 0

        if getattr(self, '_in_logging', False):
            if self.original_stream:
                self.original_stream.write(message)
            return len(message)

        self.buffer += message
        if self.buffer.endswith('\n'):
            self._flush_buffer()
        return len(message)

    def _flush_buffer(self):
        lines = self.buffer.splitlines()
        self.buffer = ''
        for line in lines:
            if not line:
                continue
            self._in_logging = True
            try:
                if self.source == 'stderr':
                    self._logger.error(line.strip())
                else:
                    self._logger.info(line.strip())
            finally:
                self._in_logging = False

    def flush(self):
        if self.buffer:
            self._flush_buffer()
        if self.original_stream:
            self.original_stream.flush()

    def fileno(self) -> int:
        return self.original_stream.fileno() if self.original_stream else -1

    def isatty(self) -> bool:
        return False


class StderrRedirector:
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger()
        self.pipe_read, self.pipe_write = os.pipe()
        self.original_stderr_fd = os.dup(2)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._buffer = b''

    def start(self):
        os.dup2(self.pipe_write, 2)
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _read_loop(self):
        while not self._stop_event.is_set():
            try:
                data = os.read(self.pipe_read, 4096)
                if not data:
                    break
                self._buffer += data
                while b'\n' in self._buffer:
                    line, self._buffer = self._buffer.split(b'\n', 1)
                    self._log_line(line.decode('utf-8', errors='replace'))
            except (OSError, ValueError):
                break

    def _log_line(self, line: str):
        line = line.strip()
        if 'QPixmap::scaled' in line or 'QFont' in line or 'QBasicTimer::' in line:
            return
        if line:
            self.logger.error(line)

    def stop(self):
        self._stop_event.set()
        os.dup2(self.original_stderr_fd, 2)
        os.close(self.pipe_read)
        os.close(self.pipe_write)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)


def hijackStreams():
    stderr_redirector = StderrRedirector()
    stderr_redirector.start()

    original_stdout = sys.stdout
    original_stderr = sys.stderr

    stdout_stream = LoggingStream(logging.INFO, source='stdout')
    stderr_stream = LoggingStream(logging.ERROR, source='stderr')
    stdout_stream.original_stream = original_stdout
    stderr_stream.original_stream = original_stderr

    sys.stdout = stdout_stream
    sys.stderr = stderr_stream

    return original_stdout, original_stderr, stderr_redirector
