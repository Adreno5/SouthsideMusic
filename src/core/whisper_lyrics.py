from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psutil
import requests

from core.config import cfg
from core.lyric_formats import LyricLine, LyricWord, parseYrc, toLrc, toYrc

if TYPE_CHECKING:
    from core.models import SongStorable

_logger = logging.getLogger(__name__)
WHISPER_DATA_DIR = Path(__file__).resolve().parents[2] / 'data' / 'whisper'
WHISPER_PIPELINE_VERSION = 5
_install_lock = threading.Lock()


@dataclass(frozen=True)
class WhisperModelSpec:
    name: str
    label: str
    size_mb: int
    ram_gb: int
    repository: str


WHISPER_MODELS = (
    WhisperModelSpec('tiny', 'Tiny', 75, 1, 'Systran/faster-whisper-tiny'),
    WhisperModelSpec('base', 'Base', 145, 1, 'Systran/faster-whisper-base'),
    WhisperModelSpec('small', 'Small', 465, 2, 'Systran/faster-whisper-small'),
    WhisperModelSpec('medium', 'Medium', 1460, 5, 'Systran/faster-whisper-medium'),
    WhisperModelSpec(
        'large-v3', 'Large v3', 2950, 8, 'Systran/faster-whisper-large-v3'
    ),
    WhisperModelSpec(
        'turbo', 'Turbo', 1540, 4, 'mobiuslabsgmbh/faster-whisper-large-v3-turbo'
    ),
)


@dataclass(frozen=True)
class WhisperHardware:
    runtime_package: str
    runtime_label: str
    device: str
    recommended_model: str
    description: str


@lru_cache(maxsize=1)
def getWhisperHardware() -> WhisperHardware:
    ram_gb = psutil.virtual_memory().total / 1024**3
    cores = os.cpu_count() or 1
    description = f'{ram_gb:.1f} GB RAM · {cores} CPU threads'
    executable = shutil.which('nvidia-smi')
    if executable:
        try:
            result = subprocess.run(
                [
                    executable,
                    '--query-gpu=name,memory.total,driver_version',
                    '--format=csv,noheader,nounits',
                ],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                check=True,
            )
            for line in result.stdout.splitlines()[:1]:
                name, memory, driver = [part.strip() for part in line.rsplit(',', 2)]
                vram = int(memory)
                description += f' · {name} ({vram / 1024:.1f} GB VRAM)'
                if int(driver.split('.')[0]) >= 572 and vram >= 2048:
                    recommended = (
                        'large-v3'
                        if vram >= 12288
                        else 'turbo'
                        if vram >= 6144
                        else 'small'
                        if vram >= 3072
                        else 'base'
                    )
                    return WhisperHardware(
                        'onnxruntime-gpu',
                        'ONNX Runtime CUDA 12 · CTranslate2 FP16',
                        'cuda',
                        recommended,
                        description,
                    )
        except (OSError, ValueError, subprocess.SubprocessError):
            _logger.debug('NVIDIA runtime detection failed', exc_info=True)
    return WhisperHardware(
        'onnxruntime',
        'ONNX Runtime CPU · CTranslate2 INT8',
        'cpu',
        'small' if ram_gb >= 15 and cores >= 8 else 'base' if ram_gb >= 7 else 'tiny',
        description,
    )


def getWhisperModel(name: str | None = None) -> WhisperModelSpec:
    selected = name if name is not None else cfg.whisper_model
    for model in WHISPER_MODELS:
        if model.name == selected:
            return model
    recommended = getWhisperHardware().recommended_model
    return next(model for model in WHISPER_MODELS if model.name == recommended)


def _runtimePath() -> Path:
    return (
        WHISPER_DATA_DIR
        / 'runtime'
        / f'{sys.implementation.cache_tag}-{getWhisperHardware().device}'
    )


def _modelReady(model_name: str) -> bool:
    directory = WHISPER_DATA_DIR / 'models' / model_name
    try:
        manifest = json.loads((directory / '.ready.json').read_text(encoding='utf-8'))
        files = manifest['files']
        return (
            manifest['model'] == model_name
            and {'model.bin', 'config.json', 'tokenizer.json'} <= files.keys()
            and all(
                isinstance(size, int)
                and size > 0
                and (directory / name).stat().st_size == size
                for name, size in files.items()
                if Path(name).name == name
            )
            and all(Path(name).name == name for name in files)
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def _runtimeReady() -> bool:
    directory = _runtimePath()
    try:
        manifest = json.loads((directory / '.ready.json').read_text(encoding='utf-8'))
        return (
            manifest['runtime'] == 2
            and manifest['python'] == sys.implementation.cache_tag
            and manifest['device'] == getWhisperHardware().device
            and all(
                (directory / name / '__init__.py').is_file()
                for name in ('faster_whisper', 'ctranslate2', 'onnxruntime', 'av')
            )
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def isWhisperReady(model_name: str | None = None) -> bool:
    return _runtimeReady() and _modelReady(getWhisperModel(model_name).name)


def _downloadFile(
    url: str,
    destination: Path,
    progress: Callable[[float], None] | None = None,
    sha256: str | None = None,
    size: int | None = None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.part')
    digest = hashlib.sha256()
    with requests.get(url, stream=True, timeout=(15, 60)) as response:
        response.raise_for_status()
        expected = int(response.headers.get('content-length', 0))
        received = 0
        with temporary.open('wb') as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                output.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                if progress is not None and (size or expected):
                    progress(min(1.0, received / (size or expected)))
    if (
        received == 0
        or (expected and received != expected)
        or (size and received != size)
    ):
        raise ValueError(f'Incomplete download: {destination.name}')
    if sha256 and digest.hexdigest() != sha256:
        raise ValueError(f'SHA256 mismatch: {destination.name}')
    os.replace(temporary, destination)


def _runWorker(
    arguments: list[str],
    cancelled: Callable[[], bool] | None = None,
    on_result: Callable[[dict[str, Any]], None] | None = None,
) -> str | None:
    environment = os.environ.copy()
    environment.update({
        'HF_HUB_OFFLINE': '1',
        'TRANSFORMERS_OFFLINE': '1',
        'HF_HUB_DISABLE_TELEMETRY': '1',
        'DO_NOT_TRACK': '1',
    })
    worker = Path(__file__).with_name('whisper_worker.py')
    with subprocess.Popen(
        [sys.executable, '-I', str(worker), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding='utf-8',
        errors='replace',
        env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
    ) as process:
        if process.stdout is None:
            raise RuntimeError('Whisper worker output is unavailable')
        process_stdout = process.stdout
        lines: queue.Queue[str | None] = queue.Queue()

        def _readOutput() -> None:
            try:
                for line in process_stdout:
                    lines.put(line)
            finally:
                lines.put(None)

        reader = threading.Thread(target=_readOutput, daemon=True)
        reader.start()
        output: list[str] = []

        def _stopWorker() -> None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

        while True:
            if cancelled is not None and cancelled():
                _stopWorker()
                reader.join(timeout=1)
                return None
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                break
            output.append(line)
            if on_result is not None:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and isinstance(
                    record.get('segments'), list
                ):
                    try:
                        on_result(record)
                    except Exception:
                        _stopWorker()
                        reader.join(timeout=1)
                        raise
        process.wait()
        reader.join()
        worker_output = ''.join(output)
        if process.returncode:
            raise RuntimeError(worker_output[-6000:].strip() or 'Whisper worker failed')
        return worker_output


def installWhisper(model_name: str, progress: Callable[[str, float], None]) -> None:
    model = getWhisperModel(model_name)
    hardware = getWhisperHardware()
    with _install_lock:
        WHISPER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        needed = (
            model.size_mb + (4500 if hardware.device == 'cuda' else 700)
        ) * 1024**2
        if (
            not isWhisperReady(model.name)
            and shutil.disk_usage(WHISPER_DATA_DIR).free < needed
        ):
            raise OSError(
                f'Whisper needs at least {needed / 1024**3:.1f} GB free space'
            )
        if not _runtimeReady():
            progress('Downloading runtime installer', 0.0)
            runtime = _runtimePath()
            runtime.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix='.install-', dir=runtime.parent
            ) as work:
                directory = Path(work)
                with requests.get(
                    'https://pypi.org/pypi/pip/json', timeout=30
                ) as response:
                    response.raise_for_status()
                    pip_info = response.json()
                wheel = next(
                    item
                    for item in pip_info['urls']
                    if item['filename'].endswith('.whl')
                )
                pip_path = directory / wheel['filename']
                _downloadFile(wheel['url'], pip_path, sha256=wheel['digests']['sha256'])
                packages = directory / 'packages'
                runtime_requirement = (
                    'onnxruntime-gpu[cuda,cudnn]>=1.24,<1.27'
                    if hardware.device == 'cuda'
                    else 'onnxruntime>=1.24,<2'
                )
                dependencies = [
                    'ctranslate2>=4.6,<5',
                    'huggingface-hub>=0.21',
                    'tokenizers>=0.13,<1',
                    'av>=11,<19',
                    'tqdm',
                    runtime_requirement,
                ]
                for requirements, fraction, no_deps in (
                    (dependencies, 0.08, False),
                    (['faster-whisper==1.2.1'], 0.3, True),
                ):
                    progress(
                        'Installing ONNX Runtime and Whisper dependencies', fraction
                    )
                    command = [
                        sys.executable,
                        '-I',
                        '-c',
                        "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('pip',run_name='__main__')",
                        str(pip_path),
                        'install',
                        '--target',
                        str(packages),
                        '--index-url',
                        'https://pypi.org/simple/',
                        '--only-binary=:all:',
                        '--disable-pip-version-check',
                        '--no-input',
                        '--no-compile',
                        '--no-warn-script-location',
                    ]
                    if no_deps:
                        command.append('--no-deps')
                    completed = subprocess.run(
                        [*command, *requirements],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        encoding='utf-8',
                        errors='replace',
                        timeout=3600,
                        creationflags=subprocess.CREATE_NO_WINDOW
                        if os.name == 'nt'
                        else 0,
                    )
                    if completed.returncode:
                        raise RuntimeError(completed.stdout[-6000:].strip())
                progress('Checking local runtime', 0.4)
                _runWorker(['--probe', str(packages), hardware.device])
                (packages / '.ready.json').write_text(
                    json.dumps({
                        'runtime': 2,
                        'python': sys.implementation.cache_tag,
                        'device': hardware.device,
                    }),
                    encoding='utf-8',
                )
                if runtime.exists():
                    if not runtime.resolve().is_relative_to(WHISPER_DATA_DIR.resolve()):
                        raise ValueError('Invalid Whisper runtime path')
                    runtime.rename(directory / 'previous')
                packages.rename(runtime)
        directory = WHISPER_DATA_DIR / 'models' / model.name
        directory.mkdir(parents=True, exist_ok=True)
        if not _modelReady(model.name):
            progress(f'Downloading Whisper {model.label}', 0.45)
            with requests.get(
                f'https://huggingface.co/api/models/{model.repository}',
                params={'blobs': 'true'},
                timeout=30,
            ) as response:
                response.raise_for_status()
                model_info = response.json()
            allowed = {
                'model.bin',
                'config.json',
                'tokenizer.json',
                'preprocessor_config.json',
                'vocabulary.json',
                'vocabulary.txt',
            }
            files = [
                item for item in model_info['siblings'] if item['rfilename'] in allowed
            ]
            if not {'model.bin', 'config.json', 'tokenizer.json'} <= {
                item['rfilename'] for item in files
            }:
                raise ValueError(
                    'Whisper repository is missing model or tokenizer files'
                )
            total = (
                sum(int(item.get('size', 0)) for item in files)
                or model.size_mb * 1024**2
            )
            downloaded = 0
            sizes: dict[str, int] = {}
            for item in files:
                filename = item['rfilename']
                size = int(item.get('size', 0))
                lfs = item.get('lfs') or {}
                sha256 = lfs.get('sha256')
                destination = directory / filename

                def _modelProgress(value: float) -> None:
                    progress(
                        f'Downloading Whisper {model.label}: {filename}',
                        0.45 + min(0.5, (downloaded + value * size) / total * 0.5),
                    )

                if (
                    not destination.is_file()
                    or not size
                    or destination.stat().st_size != size
                ):
                    _downloadFile(
                        f'https://huggingface.co/{model.repository}/resolve/{model_info["sha"]}/{filename}',
                        destination,
                        _modelProgress,
                        sha256=sha256,
                        size=size or None,
                    )
                sizes[filename] = destination.stat().st_size
                downloaded += sizes[filename]
            progress('Checking Whisper model', 0.97)
            _runWorker([
                '--probe',
                str(_runtimePath()),
                hardware.device,
                str(directory),
            ])
            (directory / '.ready.json').write_text(
                json.dumps({
                    'model': model.name,
                    'revision': model_info['sha'],
                    'files': sizes,
                }),
                encoding='utf-8',
            )
        progress('Whisper is ready', 1.0)


def _splitWhisperLines(lines: list[LyricLine]) -> list[LyricLine]:
    result: list[LyricLine] = []
    for line in lines:
        words = line.words
        widths = [
            sum(
                0
                if unicodedata.combining(char)
                else 2
                if unicodedata.east_asian_width(char) in ('W', 'F')
                else 1
                for char in word.text
            )
            for word in words
        ]
        start = 0
        while start < len(words):
            end = start + 1
            width = widths[start]
            last_end = words[start].end
            preferred = end
            for index in range(start + 1, len(words)):
                previous = words[index - 1].text.rstrip().rstrip('"\'”’」』)）')
                gap = words[index].start - last_end
                if gap >= 650 or previous.endswith((
                    '.',
                    '!',
                    '?',
                    '。',
                    '！',
                    '？',
                    ';',
                    '；',
                )):
                    break
                if (
                    width + widths[index] > 56
                    or max(last_end, words[index].end) - words[start].start > 6500
                ):
                    if preferred > start + 1:
                        end = preferred
                    break
                if width >= 20 and (
                    gap >= 150 or previous.endswith((',', '，', ':', '：'))
                ):
                    preferred = index
                width += widths[index]
                last_end = max(last_end, words[index].end)
                end = index + 1
            group = [
                LyricWord(
                    word.text.lstrip() if index == start else word.text,
                    word.start,
                    word.end,
                )
                for index, word in enumerate(words[start:end], start)
            ]
            result.append(
                LyricLine(
                    min(word.start for word in group),
                    max(word.end for word in group),
                    ''.join(word.text for word in group),
                    group,
                )
            )
            start = end
    return result


def whisperResultToLyrics(result: dict[str, Any]) -> dict[str, str]:
    lines: list[LyricLine] = []
    for segment in result.get('segments', []):
        words: list[LyricWord] = []
        for word in segment.get('words', []):
            try:
                start, end = float(word['start']), float(word['end'])
                if not math.isfinite(start) or not math.isfinite(end) or end < start:
                    continue
                text = str(word['word']).replace('\n', ' ')
                text = text if words else text.lstrip()
                start_ms, end_ms = (
                    max(0, round(start * 1000)),
                    max(0, round(end * 1000)),
                )
                if not text:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            words.append(LyricWord(text, start_ms, end_ms))
        if words:
            lines.append(
                LyricLine(
                    min(word.start for word in words),
                    max(word.end for word in words),
                    ''.join(word.text for word in words),
                    words,
                )
            )
    lines = _splitWhisperLines(lines)
    return {
        'lyric': toLrc(lines),
        'yrc_lyric': toYrc(lines),
        'translated_lyric': '',
        'ytlrc_lyric': '',
    }


def _lyricCachePath(song: SongStorable, model_name: str) -> Path:
    key = hashlib.sha256(str(song.id).encode('utf-8')).hexdigest()
    return WHISPER_DATA_DIR / 'lyrics' / model_name / f'{key}.json'


def _readLyricCache(
    song: SongStorable, model_name: str, complete_only: bool = False
) -> dict[str, str] | None:
    try:
        data = json.loads(_lyricCachePath(song, model_name).read_text(encoding='utf-8'))
        if (
            data['pipeline'] != WHISPER_PIPELINE_VERSION
            or (complete_only and data.get('complete') is not True)
            or data['model'] != model_name
            or data['song_id'] != str(song.id)
            or (
                song.content_cache_hash
                and data['audio_hash'] != song.content_cache_hash
            )
            or not all(
                isinstance(data['lyrics'][key], str)
                for key in ('lyric', 'yrc_lyric', 'translated_lyric', 'ytlrc_lyric')
            )
        ):
            return None
        lyrics = data['lyrics']
        lines = _splitWhisperLines(parseYrc(lyrics['yrc_lyric']))
        return {**lyrics, 'lyric': toLrc(lines), 'yrc_lyric': toYrc(lines)}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def hasWhisperLyrics(song: SongStorable, model_name: str | None = None) -> bool:
    return (
        _readLyricCache(song, getWhisperModel(model_name).name, complete_only=True)
        is not None
    )


def readWhisperLyrics(
    song: SongStorable, model_name: str | None = None
) -> dict[str, str]:
    return _readLyricCache(song, getWhisperModel(model_name).name) or {
        'lyric': '',
        'yrc_lyric': '',
        'translated_lyric': '',
        'ytlrc_lyric': '',
    }


def writeWhisperLyrics(
    song: SongStorable,
    model_name: str,
    result: dict[str, Any],
    complete: bool = True,
) -> dict[str, str]:
    lyrics = whisperResultToLyrics(result)
    path = _lyricCachePath(song, model_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(
        json.dumps(
            {
                'pipeline': WHISPER_PIPELINE_VERSION,
                'complete': complete,
                'model': model_name,
                'song_id': str(song.id),
                'audio_hash': song.content_cache_hash,
                'lyrics': lyrics,
            },
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    os.replace(temporary, path)
    return lyrics


def transcribeWhisper(
    song: SongStorable,
    model_name: str,
    cancelled: Callable[[], bool],
    on_update: Callable[[dict[str, str]], None] | None = None,
) -> dict[str, str] | None:
    if cancelled():
        return None
    cached = _readLyricCache(song, model_name, complete_only=True)
    if cached is not None:
        return cached
    if not isWhisperReady(model_name):
        raise RuntimeError('Install the Whisper runtime and selected model first')
    source = song.getMusicPath()
    directory = WHISPER_DATA_DIR / 'lyrics' / model_name
    directory.mkdir(parents=True, exist_ok=True)

    def _onResult(result: dict[str, Any]) -> None:
        if cancelled():
            return
        lyrics = writeWhisperLyrics(song, model_name, result, complete=False)
        if on_update is not None:
            on_update(lyrics)

    with tempfile.TemporaryDirectory(prefix='.recognition-', dir=directory) as work:
        result_path = Path(work) / 'result.json'
        output = _runWorker(
            [
                str(_runtimePath()),
                str(WHISPER_DATA_DIR / 'models' / model_name),
                getWhisperHardware().device,
                source,
                str(result_path),
            ],
            cancelled,
            _onResult,
        )
        if output is None or cancelled():
            return None
        result = json.loads(result_path.read_text(encoding='utf-8'))
        return writeWhisperLyrics(song, model_name, result)
