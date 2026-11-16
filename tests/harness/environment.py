from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np
import psutil
from PIL import Image


def _isRunning(process: psutil.Process) -> bool:
    try:
        return process.is_running()
    except psutil.Error:
        return False


def _waitSurvivors(
    processes: list[psutil.Process], timeout: float
) -> list[psutil.Process]:
    pending = set(processes)
    deadline = time.monotonic() + max(0.0, timeout)
    while pending:
        for process in list(pending):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return list(pending)
            try:
                process.wait(timeout=max(remaining / len(pending), 0.001))
            except psutil.TimeoutExpired:
                continue
            except (psutil.Error, OSError):
                pass
            pending.discard(process)
    return []


def stopProcessTree(
    process: subprocess.Popen[Any],
    descendants: list[psutil.Process] | None = None,
    timeout: float = 3,
) -> None:
    children = set(descendants or [])
    try:
        parent = psutil.Process(process.pid)
        children.update(parent.children(recursive=True))
    except psutil.Error:
        parent = None
    if process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        except ProcessLookupError:
            process.wait(timeout=5)
    deadline = time.monotonic() + min(timeout, 1)
    while True:
        for child in [*children, *([parent] if parent else [])]:
            try:
                children.update(child.children(recursive=True))
            except psutil.Error:
                pass
        alive = _waitSurvivors(list(children), min(0.05, deadline - time.monotonic()))
        if not alive or time.monotonic() >= deadline:
            break
    for child in alive:
        if not _isRunning(child):
            continue
        try:
            child.terminate()
        except psutil.Error:
            pass
    alive = _waitSurvivors(alive, timeout)
    for child in [*children, *([parent] if parent else [])]:
        try:
            alive.extend(child.children(recursive=True))
        except psutil.Error:
            pass
    for child in set(alive):
        if not _isRunning(child):
            continue
        try:
            child.kill()
        except psutil.Error:
            pass
    survivors = _waitSurvivors([child for child in set(alive) if _isRunning(child)], 5)
    if survivors:
        raise RuntimeError(
            'Application descendants survived cleanup: '
            + ', '.join(str(child.pid) for child in survivors)
        )


def findWorker(root: Path) -> str | None:
    candidates = [
        os.environ.get('SOUTHSIDE_FREE_THREADED_PYTHON', ''),
        os.environ.get('PYTHON_FREETHREADED', ''),
        str(root / '.harness-results' / 'worker' / 'Scripts' / 'python.exe'),
        str(root / '.venv-ft' / 'Scripts' / 'python.exe'),
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            result = subprocess.run(
                [
                    candidate,
                    '-c',
                    'import numpy, scipy, PIL, pydub, sysconfig; '
                    'print(sysconfig.get_config_var("Py_GIL_DISABLED"))',
                ],
                capture_output=True,
                text=True,
                timeout=20,
                env={**os.environ, 'PYTHON_GIL': '0'},
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and result.stdout.strip().endswith('1'):
            return candidate
    return None


def prepareWorker(root: Path) -> str:
    from importlib.metadata import version

    worker = root / '.harness-results' / 'worker'
    subprocess.run(['uv', 'python', 'install', '3.14t'], check=True)
    subprocess.run(['uv', 'venv', '--python', '3.14t', str(worker)], check=True)
    executable = worker / 'Scripts' / 'python.exe'
    packages = [
        f'{name}=={version(name)}'
        for name in ('numpy', 'scipy', 'pillow', 'pydub', 'audioop-lts')
    ]
    subprocess.run(
        ['uv', 'pip', 'install', '--python', str(executable), *packages], check=True
    )
    return str(executable)


def userFingerprint(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in [root / 'config.json', root / 'favorites.json', root / 'config.pkl']:
        result[path.name] = (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ''
        )
    for path in (root / 'data').rglob('*'):
        if path.is_file():
            info = path.stat()
            result[str(path.relative_to(root))] = f'{info.st_size}:{info.st_mtime_ns}'
    return result


class Sandbox:
    def __init__(self, root: Path, gateway_url: str, native: bool = False) -> None:
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            candidate = root / 'ffmpeg' / 'bin' / 'ffmpeg.exe'
            ffmpeg = str(candidate) if candidate.is_file() else None
        if not ffmpeg:
            raise RuntimeError('FFmpeg is required; install it before running core')
        if not (Path(ffmpeg).parent / 'ffprobe.exe').is_file():
            raise RuntimeError('FFprobe is required')
        worker = findWorker(root)
        if not worker:
            raise RuntimeError(
                'Free-threaded worker is required. Run: '
                'uv run python scripts/test_harness.py prepare-worker'
            )
        self._temporary = tempfile.TemporaryDirectory(prefix='southside-harness-')
        self.root = Path(self._temporary.name).resolve()
        self.environment = os.environ.copy()
        try:
            for name in ('src', 'icons', 'images', 'fonts'):
                source = root / name
                if source.is_dir():
                    shutil.copytree(
                        source,
                        self.root / name,
                        ignore=shutil.ignore_patterns(
                            '__pycache__',
                            '*.pyc',
                            'avatar.png',
                            'torch_venv',
                            'freethreaded_venv',
                            '.mypy_cache',
                            *(['data'] if name == 'src' else []),
                        ),
                    )
            for name in ('pyproject.toml', '.python-version'):
                shutil.copy2(root / name, self.root / name)
            binary_dir = self.root / 'ffmpeg' / 'bin'
            shutil.copytree(Path(ffmpeg).parent, binary_dir)
            self.environment['PATH'] = (
                str(binary_dir) + os.pathsep + self.environment['PATH']
            )
            self.environment['SOUTHSIDE_FREE_THREADED_PYTHON'] = worker
            self.environment['NO_PROXY'] = '127.0.0.1,localhost'
            self.environment['QT_SCALE_FACTOR'] = '1'
            self.environment['QT_OPENGL'] = 'software' if not native else 'desktop'
            self.environment['PYTHONUNBUFFERED'] = '1'
            self.environment.pop('PYTHON_GIL', None)
            self.environment['TEMP'] = str(self.root / 'temp')
            self.environment['TMP'] = self.environment['TEMP']
            (self.root / 'temp').mkdir()
            self._createMedia()
            config = {
                'language': 'en_US',
                'window_width': 1250,
                'window_height': 780,
                'window_x': 20,
                'window_y': 20,
                'enable_crossfade': False,
                'skip_nosound': False,
                'whisper_enabled': False,
                'smtc_enabled': True,
                'llm_viewer_expanded': False,
                'llm_current_provider': 'Harness Chat',
                'llm_current_model': 'harness-chat',
                'llm_providers': [
                    {
                        'name': label,
                        'api_format': api_format,
                        'base_url': gateway_url,
                        'api_key_encrypted': '',
                        'models': [{'id': model, 'display_name': model}],
                    }
                    for label, api_format, model in (
                        ('Harness Chat', 'openai_chat', 'harness-chat'),
                        ('Harness Responses', 'openai_responses', 'harness-responses'),
                        ('Harness Anthropic', 'anthropic', 'harness-anthropic'),
                    )
                ],
            }
            (self.root / 'config.json').write_text(json.dumps(config), encoding='utf-8')
        except BaseException as error:
            try:
                self.close()
            except Exception as cleanup_error:
                error.add_note(f'Sandbox cleanup failed: {cleanup_error}')
            raise

    def _createMedia(self) -> None:
        directory = self.root / 'fixtures'
        directory.mkdir()
        Image.new('RGB', (96, 96), '#325a7c').save(directory / 'cover.png')
        for index, frequency in enumerate((220, 440, 660)):
            times = np.arange(44100 * 12, dtype=np.float64) / 44100
            signal = 0.18 * np.sin(times * frequency * 2 * np.pi)
            signal += 0.05 * np.sin(times * 70 * 2 * np.pi) * ((times % 0.5) < 0.08)
            signal[-22050:] *= np.linspace(1, 0, 22050)
            stereo = np.column_stack((signal, signal * 0.85))
            with wave.open(str(directory / f'track-{index}.wav'), 'wb') as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(44100)
                output.writeframes((stereo * 32767).astype('<i2').tobytes())
            subprocess.run(
                [
                    str(self.root / 'ffmpeg' / 'bin' / 'ffmpeg.exe'),
                    '-v',
                    'error',
                    '-y',
                    '-i',
                    str(directory / f'track-{index}.wav'),
                    str(directory / f'track-{index}.flac'),
                ],
                check=True,
                timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

    def close(self) -> None:
        self._temporary.cleanup()
