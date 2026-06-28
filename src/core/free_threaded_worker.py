from __future__ import annotations

import base64
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
import io
import logging
import os
from pathlib import Path
import pickle
import struct
import subprocess
import sys
import threading
from typing import Any


_B64_BYTES_KEY = '__southside_b64_bytes__'
_FLOAT_ARRAY_KEY = '__southside_float_array__'
_MAIN_THREAD_OPS = {'loudness_gain'}
_ORIGINAL_POPEN = subprocess.Popen


def json_base64_bytes(data: bytes) -> dict[str, bytes]:
    """Mark bytes for base64 encoding inside the free-threaded worker."""
    return {_B64_BYTES_KEY: data}


def json_float_array(
    data: bytes,
    dtype: str,
    count: int,
    multiple: float = 1.0,
) -> dict[str, dict[str, object]]:
    """Mark a raw float array for JSON list conversion inside the worker."""
    return {
        _FLOAT_ARRAY_KEY: {
            'data': data,
            'dtype': dtype,
            'count': count,
            'multiple': multiple,
        }
    }


def _read_frame(stream) -> Any | None:
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise EOFError('incomplete frame header')
    length = struct.unpack('>I', header)[0]
    payload = stream.read(length)
    if len(payload) != length:
        raise EOFError('incomplete frame body')
    return pickle.loads(payload)


def _write_frame(stream, payload: Any) -> None:
    data = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    stream.write(struct.pack('>I', len(data)))
    stream.write(data)
    stream.flush()


def _float_unpack_format(dtype: str) -> str | None:
    dtype = dtype.lower()
    if dtype in {'float32', 'single', 'f4', '<f4', '|f4'}:
        return '<f'
    if dtype in {'float64', 'double', 'f8', '<f8'}:
        return '<d'
    return None


def _normalize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        if _B64_BYTES_KEY in value:
            data = value[_B64_BYTES_KEY]
            if not isinstance(data, bytes):
                return ''
            return base64.b64encode(data).decode('ascii')

        if _FLOAT_ARRAY_KEY in value:
            spec = value[_FLOAT_ARRAY_KEY]
            if not isinstance(spec, dict):
                return []
            data = spec.get('data', b'')
            dtype = str(spec.get('dtype', ''))
            count = int(spec.get('count', 0))
            multiple = float(spec.get('multiple', 1.0))
            if not isinstance(data, bytes):
                return []
            fmt = _float_unpack_format(dtype)
            if fmt is None or count <= 0:
                return []
            item_size = struct.calcsize(fmt)
            count = min(count, len(data) // item_size)
            view = memoryview(data)[: count * item_size]
            return [item[0] * multiple for item in struct.iter_unpack(fmt, view)]

        return {str(key): _normalize_payload(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [_normalize_payload(item) for item in value]

    if isinstance(value, bytes):
        return base64.b64encode(value).decode('ascii')

    return value


def _average_color(image_bytes: bytes) -> list[float]:
    if not image_bytes:
        return [128, 128, 128]
    from PIL import Image, ImageStat

    with Image.open(io.BytesIO(image_bytes)) as img:
        rgb = img.convert('RGB')
        stat = ImageStat.Stat(rgb)
        return [float(value) for value in stat.mean[:3]]


def _loudness_gain(payload: dict[str, Any]) -> float:
    src_dir = Path(__file__).resolve().parents[1]
    src_dir_text = str(src_dir)
    if src_dir_text not in sys.path:
        sys.path.insert(0, src_dir_text)

    from core.loudness import getAdjustedGainFactorFromSamples

    samples_bytes = payload.get('samples', b'')
    if not isinstance(samples_bytes, bytes):
        raise TypeError('samples must be bytes')

    return float(
        getAdjustedGainFactorFromSamples(
            float(payload.get('target_lufs', -16.0)),
            samples_bytes,
            int(payload.get('sample_width', 2)),
            int(payload.get('frame_rate', 44100)),
        )
    )


def _fix_wav_headers(data: bytearray) -> None:
    pos = 12
    data_position = -1
    data_size = 0
    while pos + 8 <= len(data):
        subchunk_id = data[pos : pos + 4]
        subchunk_size = struct.unpack_from('<I', data, pos + 4)[0]
        if subchunk_id == b'data':
            data_position = pos
            data_size = subchunk_size
            break
        pos += subchunk_size + 8

    if data_position < 0 or data_size < 0:
        return
    if len(data) > 2**32:
        raise ValueError('Unable to process >4GB files')

    data[4:8] = struct.pack('<I', len(data) - 8)
    data[data_position + 4 : data_position + 8] = struct.pack(
        '<I',
        len(data) - data_position - 8,
    )


def _decode_audio(payload: dict[str, Any]) -> bytes:
    import json

    from pydub import AudioSegment
    from pydub.exceptions import CouldntDecodeError
    from pydub.utils import get_prober_name, mediainfo_json

    filename_value = payload.get('path')
    filename = str(filename_value) if filename_value else None
    stdin_data = payload.get('data')
    if filename is None:
        if not isinstance(stdin_data, bytes):
            raise TypeError('decode_audio requires path or bytes data')
    else:
        stdin_data = None

    conversion_command = [AudioSegment.converter, '-y']
    stdin_parameter = None
    if filename:
        conversion_command += ['-i', filename]
    else:
        stdin_parameter = subprocess.PIPE
        conversion_command += ['-i', 'pipe:0']

    info = None
    if filename:
        info = mediainfo_json(filename, read_ahead_limit=-1)
    elif isinstance(stdin_data, bytes):
        probe = subprocess.Popen(
            [
                get_prober_name(),
                '-of',
                'json',
                '-v',
                'info',
                '-show_format',
                '-show_streams',
                'pipe:0',
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        probe_out, _probe_err = probe.communicate(input=stdin_data)
        if probe.returncode == 0 and probe_out:
            info = json.loads(probe_out.decode('utf-8', 'ignore'))

    if info:
        audio_streams = [
            stream for stream in info['streams'] if stream['codec_type'] == 'audio'
        ]
        audio_codec = audio_streams[0].get('codec_name')
        if audio_streams[0].get('sample_fmt') == 'fltp' and audio_codec in [
            'mp3',
            'mp4',
            'aac',
            'webm',
            'ogg',
        ]:
            bits_per_sample = 16
        else:
            bits_per_sample = int(
                audio_streams[0].get('bits_per_sample')
                or audio_streams[0].get('bits_per_raw_sample')
                or 0
            )
        if bits_per_sample == 8:
            conversion_command += ['-acodec', 'pcm_u8']
        elif bits_per_sample > 0:
            conversion_command += ['-acodec', f'pcm_s{bits_per_sample}le']

    conversion_command += ['-vn', '-f', 'wav', '-']
    process = subprocess.Popen(
        conversion_command,
        stdin=stdin_parameter,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = process.communicate(input=stdin_data)
    if process.returncode != 0 or len(stdout) == 0:
        raise CouldntDecodeError(
            'Decoding failed. ffmpeg returned error code: {0}\n\nOutput from ffmpeg/avlib:\n\n{1}'.format(
                process.returncode,
                stderr.decode(errors='ignore'),
            )
        )

    wav_bytes = bytearray(stdout)
    _fix_wav_headers(wav_bytes)
    return bytes(wav_bytes)


def _handle_worker_request(request: dict[str, Any]) -> Any:
    op = request.get('op')
    payload = request.get('payload', {})
    if op == 'json_dumps':
        return dump_json_payload(payload)

    if not isinstance(payload, dict):
        raise TypeError('worker payload must be a dict')

    if op == 'base64_decode':
        return base64.b64decode(str(payload.get('data', '')))
    if op == 'average_color':
        image_bytes = payload.get('image', b'')
        if not isinstance(image_bytes, bytes):
            return [128, 128, 128]
        return _average_color(image_bytes)
    if op == 'loudness_gain':
        return _loudness_gain(payload)
    if op == 'decode_audio':
        return _decode_audio(payload)

    raise ValueError(f'unsupported worker op: {op}')


def dump_json_payload(payload: Any) -> str:
    """Dump a worker-compatible payload to compact JSON."""
    import json

    return json.dumps(_normalize_payload(payload), separators=(',', ':'))


def _worker_main() -> int:
    max_workers = max(2, os.cpu_count() or 2)
    if '--workers' in sys.argv:
        try:
            max_workers = max(1, int(sys.argv[sys.argv.index('--workers') + 1]))
        except (ValueError, IndexError):
            pass

    stdout_lock = threading.Lock()

    def _send_response(response: dict[str, Any]) -> None:
        with stdout_lock:
            _write_frame(sys.stdout.buffer, response)

    def _done(request_id: int, future) -> None:
        try:
            msg = future.result()
            _send_response({'id': request_id, 'ok': True, 'msg': msg})
        except Exception as e:
            _send_response({'id': request_id, 'ok': False, 'error': repr(e)})

    with ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix='southside-ft-json',
    ) as executor:
        while True:
            request = _read_frame(sys.stdin.buffer)
            if request is None:
                return 0
            if not isinstance(request, dict):
                continue
            if request.get('op') == 'shutdown':
                return 0

            request_id = int(request.get('id', 0))
            if request.get('op') in _MAIN_THREAD_OPS:
                try:
                    msg = _handle_worker_request(request)
                    _send_response({'id': request_id, 'ok': True, 'msg': msg})
                except Exception as e:
                    _send_response({'id': request_id, 'ok': False, 'error': repr(e)})
                continue

            future = executor.submit(_handle_worker_request, request)
            future.add_done_callback(lambda fut, rid=request_id: _done(rid, fut))


class FreeThreadedJsonSender:
    """Send JSON packing work to a Python free-threaded sidecar process."""

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        max_workers: int | None = None,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._max_workers = max_workers or max(2, os.cpu_count() or 2)
        self._lock = threading.Lock()
        self._callbacks: dict[int, Callable[[Any | None], None]] = {}
        self._next_id = 0
        self._process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._interpreter: Path | None = None
        self._shutdown = False

    def submit(
        self,
        payload: dict[str, object],
        callback: Callable[[str], None],
    ) -> bool:
        def _callback(msg: Any | None) -> None:
            if isinstance(msg, str):
                callback(msg)

        return self._submit_request('json_dumps', payload, _callback) is not None

    def dump(
        self,
        payload: dict[str, object],
        timeout: float = 5.0,
    ) -> str | None:
        result = self.call('json_dumps', payload, timeout)
        return result if isinstance(result, str) else None

    def call(
        self,
        op: str,
        payload: dict[str, object],
        timeout: float = 5.0,
    ) -> Any | None:
        done = threading.Event()
        result: list[Any | None] = []

        def _capture(msg: Any | None) -> None:
            result.append(msg)
            done.set()

        request_id = self._submit_request(op, payload, _capture)
        if request_id is None:
            return None
        if not done.wait(timeout):
            with self._lock:
                self._callbacks.pop(request_id, None)
            self._logger.warning('free-threaded worker request timed out')
            return None
        return result[0] if result else None

    def _submit_request(
        self,
        op: str,
        payload: dict[str, object],
        callback: Callable[[Any | None], None],
    ) -> int | None:
        with self._lock:
            if self._shutdown:
                return None
            process = self._ensure_process_locked()
            if process is None or process.stdin is None:
                return None

            self._next_id += 1
            request_id = self._next_id
            self._callbacks[request_id] = callback
            try:
                _write_frame(
                    process.stdin,
                    {
                        'id': request_id,
                        'op': op,
                        'payload': payload,
                    },
                )
            except Exception as e:
                self._callbacks.pop(request_id, None)
                self._logger.warning('free-threaded worker submit failed: %s', e)
                self._stop_process_locked()
                return None

        return request_id

    def shutdown(self) -> None:
        reader_thread: threading.Thread | None
        with self._lock:
            self._shutdown = True
            process = self._process
            if process is not None and process.stdin is not None:
                try:
                    _write_frame(process.stdin, {'op': 'shutdown'})
                except Exception:
                    pass
            reader_thread = self._stop_process_locked(terminate_first=False)
        self._join_reader_thread(reader_thread)

    def is_running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def _ensure_process_locked(self) -> subprocess.Popen | None:
        if self._shutdown:
            return None
        if self._process is not None and self._process.poll() is None:
            return self._process

        interpreter = self._find_interpreter()
        if interpreter is None:
            return None

        creationflags = 0
        if os.name == 'nt' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
            creationflags = subprocess.CREATE_NO_WINDOW

        env = os.environ.copy()
        env['PYTHON_GIL'] = '0'
        try:
            process = _ORIGINAL_POPEN(
                [
                    str(interpreter),
                    str(Path(__file__).resolve()),
                    '--worker',
                    '--workers',
                    str(self._max_workers),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=str(Path(__file__).resolve().parents[2]),
                creationflags=creationflags,
                env=env,
            )
        except Exception as e:
            self._logger.warning('failed to start free-threaded worker: %s', e)
            return None

        self._process = process
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            args=(process,),
            daemon=True,
            name='southside-ft-json-reader',
        )
        self._reader_thread.start()
        self._logger.info('free-threaded JSON worker started: %s', interpreter)
        return process

    def _find_interpreter(self) -> Path | None:
        if self._interpreter is not None:
            return self._interpreter

        root = Path(__file__).resolve().parents[2]
        candidates: list[Path] = []

        for env_name in (
            'SOUTHSIDE_FREE_THREADED_PYTHON',
            'PYTHON_FREETHREADED',
        ):
            value = os.environ.get(env_name)
            if value:
                candidates.append(Path(value))

        candidates.append(root / '.python-ft-pyside-blocked' / 'python.exe')

        for name in (
            '.venv-ft',
            '.venv-ft-pyside-blocked',
            '.venv-free-threaded',
        ):
            candidates.append(root / name / 'Scripts' / 'python.exe')

        appdata = os.environ.get('APPDATA')
        if appdata:
            uv_python_dir = Path(appdata) / 'uv' / 'python'
            if uv_python_dir.is_dir():
                candidates.extend(
                    path / 'python.exe' for path in uv_python_dir.glob('*freethreaded*')
                )

        for candidate in candidates:
            if candidate.is_file() and self._is_free_threaded(candidate):
                self._interpreter = candidate
                return candidate

        self._logger.warning('no free-threaded Python interpreter found')
        return None

    def _is_free_threaded(self, interpreter: Path) -> bool:
        code = (
            'import sys, sysconfig; '
            'print(int(not sys._is_gil_enabled())); '
            'print(sysconfig.get_config_var("Py_GIL_DISABLED"))'
        )
        creationflags = 0
        if os.name == 'nt' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
            creationflags = subprocess.CREATE_NO_WINDOW
        env = os.environ.copy()
        env['PYTHON_GIL'] = '0'
        try:
            result = subprocess.run(
                [str(interpreter), '-c', code],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=creationflags,
                env=env,
            )
        except Exception:
            return False
        if result.returncode != 0:
            return False
        values = [line.strip() for line in result.stdout.splitlines()]
        return len(values) >= 2 and values[0] == '1' and values[1] == '1'

    def _read_loop(self, process: subprocess.Popen) -> None:
        stdout = process.stdout
        if stdout is None:
            return
        while True:
            try:
                response = _read_frame(stdout)
            except Exception as e:
                self._logger.debug('free-threaded worker read ended: %s', e)
                break
            if response is None:
                break
            if not isinstance(response, dict):
                continue

            request_id = int(response.get('id', 0))
            with self._lock:
                callback = self._callbacks.pop(request_id, None)
            if callback is None:
                continue
            if response.get('ok'):
                callback(response.get('msg'))
            else:
                self._logger.warning(
                    'free-threaded worker error: %s',
                    response.get('error'),
                )
                callback(None)

        with self._lock:
            callbacks = list(self._callbacks.values())
            self._callbacks.clear()
        for callback in callbacks:
            callback(None)

    def _stop_process_locked(
        self,
        *,
        terminate_first: bool = True,
    ) -> threading.Thread | None:
        process = self._process
        reader_thread = self._reader_thread
        self._process = None
        self._reader_thread = None
        self._callbacks.clear()
        if process is None:
            return reader_thread

        if not terminate_first:
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except Exception:
                pass
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
            except Exception:
                pass

        if process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except Exception:
                    pass
                try:
                    process.wait(timeout=1.0)
                except Exception:
                    pass
            except Exception:
                pass

        for pipe in (process.stdin, process.stdout, process.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except Exception:
                pass
        return reader_thread

    def _join_reader_thread(self, reader_thread: threading.Thread | None) -> None:
        if (
            reader_thread is not None
            and reader_thread.is_alive()
            and threading.current_thread() is not reader_thread
        ):
            reader_thread.join(timeout=0.5)


if __name__ == '__main__' and '--worker' in sys.argv:
    raise SystemExit(_worker_main())
