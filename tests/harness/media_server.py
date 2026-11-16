from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO, override


class MediaHandler(SimpleHTTPRequestHandler):
    @override
    def log_message(self, format: str, *args: Any) -> None:
        pass

    @override
    def send_head(self) -> BinaryIO | None:
        path = Path(self.translate_path(self.path))
        if not path.is_file():
            self.send_error(404)
            return None
        file = path.open('rb')
        length = path.stat().st_size
        start, end = 0, length - 1
        header = self.headers.get('Range', '')
        if header:
            try:
                if not header.startswith('bytes=') or ',' in header:
                    raise ValueError(header)
                parts = header.removeprefix('bytes=').split('-', 1)
                if parts[0]:
                    start = int(parts[0])
                    end = min(int(parts[1]) if parts[1] else end, end)
                else:
                    suffix = int(parts[1])
                    if suffix <= 0:
                        raise ValueError(header)
                    start = max(0, length - suffix)
                if start < 0 or start > end:
                    raise ValueError(header)
            except (ValueError, IndexError):
                file.close()
                self.send_error(416)
                return None
        self._remaining = end - start + 1
        self.send_response(206 if header else 200)
        self.send_header('Content-Type', self.guess_type(str(path)))
        self.send_header('Content-Length', str(self._remaining))
        self.send_header('Accept-Ranges', 'bytes')
        if header:
            self.send_header('Content-Range', f'bytes {start}-{end}/{length}')
        self.end_headers()
        file.seek(start)
        return file

    @override
    def copyfile(self, source: Any, outputfile: Any) -> None:
        remaining = self._remaining
        while remaining > 0:
            chunk = source.read(min(65536, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)


class MediaServer:
    def __init__(self, root: Path) -> None:
        handler = functools.partial(MediaHandler, directory=str(root))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> str:
        self.thread.start()
        return self.url

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
