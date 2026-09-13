from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import gzip
import hashlib
import html
import json
import logging
import os
from pathlib import Path
import platform
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Callable
from urllib.parse import urljoin
from urllib.request import Request, urlopen
import zipfile
import zlib

from PySide6.QtCore import QLocale, QObject, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_EXE = SCRIPT_DIR / 'python' / 'python.exe'
FREE_THREADED_PYTHON_EXE = SCRIPT_DIR / 'freethreaded_python' / 'python.exe'
FULL_REQUIREMENTS = SCRIPT_DIR / 'full_requirements.txt'
MAIN_SCRIPT = SCRIPT_DIR / 'src' / 'main.py'
SITE_PACKAGES = PYTHON_EXE.parent / 'Lib' / 'site-packages'
DATA_DIR = SCRIPT_DIR / 'data'
PIP_CACHE_DIR = DATA_DIR / 'pip-cache'
PIP_WHEELHOUSE = DATA_DIR / 'pip-wheels'

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
MAX_WHEEL_DOWNLOAD_WORKERS = 8
PARALLEL_DOWNLOAD_THRESHOLD = 16
WHEEL_WATCH_INTERVAL = 0.25
DOWNLOAD_EMIT_INTERVAL = 0.25
PIP_RETRIES = '2'
PIP_TIMEOUT = '20'
HEARTBEAT_TICK_MS = 100
MIRROR_LATENCY_TIMEOUT = 3
FREE_THREADED_REQUIREMENT_NAMES = [
    'numpy',
    'scipy',
    'pillow',
    'pydub',
    'audioop-lts',
]
FREE_THREADED_IMPORT_CHECKS = {
    'audioop-lts': 'audioop',
    'numpy': 'numpy',
    'pillow': 'PIL',
    'pydub': 'pydub',
    'scipy': 'scipy',
}
PIP_SIZE_RE = re.compile(r'\((?P<size>\d+(?:\.\d+)?)\s*(?P<unit>bytes?|kB|KB|MB|GB)\)')
PIP_PROGRESS_RE = re.compile(
    r'(?P<downloaded>\d+(?:\.\d+)?)\s*(?P<down_unit>bytes?|kB|KB|MB|GB)'
    r'\s*/\s*'
    r'(?P<total>\d+(?:\.\d+)?)\s*(?P<total_unit>bytes?|kB|KB|MB|GB)'
)

PIP_NAME_RE = re.compile(r'^(?P<name>[A-Za-z0-9_.]+?)(?=-\d)')
PIP_SIZE_UNITS = {
    'byte': 1,
    'bytes': 1,
    'kb': 1000,
    'mb': 1000 * 1000,
    'gb': 1000 * 1000 * 1000,
}
# The native downloader replaces `pip download`; set SOUTHSIDE_NATIVE_DOWNLOAD=0
# to fall back to the pip path without editing code.
NATIVE_DOWNLOAD_ENV = 'SOUTHSIDE_NATIVE_DOWNLOAD'
WHEEL_INDEX_CACHE = DATA_DIR / 'wheel-index.json'
WHEEL_INDEX_TTL = 12 * 3600
NATIVE_DOWNLOAD_WORKERS = 8
INDEX_WORKERS = 8
INDEX_TIMEOUT = 45
MAX_CLOSURE_PACKAGES = 120
MAX_PARALLEL_RESOLVE = 12
# A failed extension import is often a transient DLL load; retry before
# declaring a package broken and blocking startup.
IMPORT_CHECK_ATTEMPTS = 3
IMPORT_CHECK_RETRY_DELAY = 1.0
IMPORT_CHECK_TIMEOUT = 120
# Mirrors that may serve the artifact paths their index advertises. Aliyun and
# Tencent answer those with 404, and Tsinghua/USTC answer many of them with 403,
# so the host actually used is probed once per run.
_ARTIFACT_CANDIDATES = (
    'https://pypi.mirrors.ustc.edu.cn',
    'https://pypi.tuna.tsinghua.edu.cn',
    'https://repo.huaweicloud.com',
    'https://mirrors.aliyun.com',
    'https://mirrors.cloud.tencent.com',
    'https://files.pythonhosted.org',
)
_ARTIFACT_PROBES = (
    'https://pypi.org/packages/44/6f/'
    '7120676b6d73228c96e17f1f794d8ab046fc910d781c8d151120c3f1569e/'
    'toml-0.10.2-py2.py3-none-any.whl',
    'https://pypi.org/packages/d1/d6/'
    '3965ed04c63042e047cb6a3e6ed1a63a35087b6a609aa3a15ed8ac56c221/'
    'colorama-0.4.6-py2.py3-none-any.whl',
    'https://pypi.org/packages/f9/1c/'
    '01bfd571a64e7f270e6bab5e3777debe0edc56759233ce84f27dec92d14/'
    'tqdm-4.67.3-py3-none-any.whl',
)
_ARTIFACT_PROBED = [False]
_WORKING_ARTIFACT_HOSTS: list[str] = []
_DEAD_ARTIFACT_HOSTS: set[str] = set()
_INDEX_HOST: list[str] = ['']


def pickArtifactHost(timeout: float = 6.0) -> str:
    """Find mirrors that actually serve wheels, once per run.

    One probe file is not enough: some mirrors serve pure-python wheels but
    answer others with 403, which used to send every download down the slow
    fallback. Rank the hosts by how many sample wheels they return.
    """
    if _WORKING_ARTIFACT_HOSTS:
        return _WORKING_ARTIFACT_HOSTS[0]
    paths = [probe[probe.index('/packages/') :] for probe in _ARTIFACT_PROBES]
    scores: list[tuple[int, str]] = []
    for host in _ARTIFACT_CANDIDATES:
        served = 0
        for path in paths:
            try:
                request = Request(
                    host + path,
                    headers={'User-Agent': 'SouthsideMusic'},
                )
                with urlopen(request, timeout=timeout) as response:
                    response.read(512)
                served += 1
            except Exception as e:
                _logger.debug('artifact host %s failed on a probe: %s', host, e)
                break
        if served:
            scores.append((served, host))
    scores.sort(reverse=True)
    _WORKING_ARTIFACT_HOSTS.extend(host for _score, host in scores)
    for host in _ARTIFACT_CANDIDATES:
        if host not in _WORKING_ARTIFACT_HOSTS:
            _DEAD_ARTIFACT_HOSTS.add(host)
    if scores:
        _logger.info(
            'wheel downloads will use %s (%d sample wheels served)',
            scores[0][1],
            scores[0][0],
        )
    return _WORKING_ARTIFACT_HOSTS[0] if _WORKING_ARTIFACT_HOSTS else ''


def warmArtifactHost() -> None:
    """Probe the mirrors once, in the background, before the first download."""
    if _ARTIFACT_PROBED[0]:
        return
    _ARTIFACT_PROBED[0] = True
    threading.Thread(
        target=pickArtifactHost,
        daemon=True,
        name='southside-mirror-probe',
    ).start()
# Dependencies that ship with CPython and must never be fetched from an index.
_STDLIB_NAMES = {
    'argparse',
    'asyncio',
    'collections',
    'concurrent',
    'ctypes',
    'curses',
    'dataclasses',
    'decimal',
    'email',
    'enum',
    'functools',
    'hashlib',
    'html',
    'http',
    'importlib',
    'io',
    'json',
    'logging',
    'math',
    'multiprocessing',
    'os',
    'pathlib',
    'pickle',
    'queue',
    're',
    'select',
    'shutil',
    'signal',
    'socket',
    'sqlite3',
    'ssl',
    'statistics',
    'string',
    'subprocess',
    'sys',
    'tempfile',
    'threading',
    'time',
    'tkinter',
    'typing',
    'unittest',
    'urllib',
    'uuid',
    'warnings',
    'weakref',
    'xml',
    'zipfile',
    'zlib',
    'audioop',
}
SIMPLE_INDEX_ACCEPT = (
    'application/vnd.pypi.simple.v1+json, application/vnd.pypi.simple.v1+html'
)
INSTALLED_PACKAGES_SCRIPT = r"""
import importlib.metadata as metadata
import json

packages = []
for distribution in metadata.distributions():
    name = distribution.metadata.get('Name') or getattr(distribution, 'name', '')
    if name:
        packages.append({'name': name, 'version': distribution.version})
print(json.dumps(packages))
"""
MISSING_IMPORTS_SCRIPT = r"""
import importlib
import json
import sys

module_map = json.loads(sys.argv[1])
missing = []
for package_name, module_name in module_map.items():
    try:
        importlib.import_module(module_name)
    except Exception as e:
        print(f'{package_name}: {type(e).__name__}: {e}', file=sys.stderr)
        missing.append(package_name)
print(json.dumps(missing))
"""

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
        # The app root, not src/: main.py reads fonts/, icons/ and data/ through
        # relative paths. MAIN_SCRIPT is absolute and main.py puts its own folder
        # on sys.path, so imports do not depend on this.
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
        bwindow.startupFailed.emit(proc.returncode)
        return
    app.quit()


class RequirementInfo:
    def __init__(self, name: str, version: str = '', specifier: str = '') -> None:
        self.name = name
        self.version = version
        self.specifier = specifier

    name: str
    version: str
    specifier: str


def stagedWheelPath(wheel: 'WheelFile') -> Path:
    """Where a wheel downloaded for its metadata is kept for reuse.

    Reading METADATA needs the wheel, and the download phase needs it too, so
    keep one copy instead of fetching everything twice.
    """
    key = hashlib.sha256(wheel.url.encode('utf-8')).hexdigest()[:20]
    directory = DATA_DIR / 'meta-wheels'
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f'{key}-{wheel.filename}'


@dataclass
class WheelFile:
    filename: str
    url: str
    sha256: str
    version: str
    requires_python: str
    size: int = 0
    path: Path | None = None
    metadata_url: str = ''
    metadata_hash: str = ''


class WheelIndex:
    """Reads a PEP 691 simple index and resolves pinned requirements to wheels."""

    def __init__(
        self,
        mirror_url: str,
        python_exe: Path,
        env: dict[str, str] | None = None,
    ) -> None:
        self.mirror_url = mirror_url
        self.python_exe = python_exe
        self.env = env
        self._entries: dict[str, dict] = {}
        self._tag_cache: list[tuple[str, str, str]] | None = None
        self._interpreter_tag = 'py3'
        self._version_cache: str | None = None
        self._requirement: RequirementInfo | None = None
        self._cache_lock = threading.Lock()
        self._metadata_cache: dict[str, bytes | None] = {}
        self._metadata_lock = threading.Lock()
        self._loadCache()

    def _loadCache(self) -> None:
        self._entries = self._readCacheEntries()

    @staticmethod
    def _readCacheEntries() -> dict[str, dict]:
        try:
            payload = json.loads(WHEEL_INDEX_CACHE.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        entries = payload.get('entries')
        return entries if isinstance(entries, dict) else {}

    @staticmethod
    def _writeCacheEntries(entries: dict[str, dict]) -> None:
        try:
            WHEEL_INDEX_CACHE.parent.mkdir(parents=True, exist_ok=True)
            WHEEL_INDEX_CACHE.write_text(
                json.dumps({'version': 1, 'entries': entries}),
                encoding='utf-8',
            )
        except OSError as e:
            _logger.debug('could not persist wheel index cache: %s', e)

    def _cachedFiles(self, key: str) -> list[dict] | None:
        entry = self._entries.get(key)
        if not isinstance(entry, dict):
            return None
        stamp = entry.get('time')
        if not isinstance(stamp, (int, float)) or time.time() - stamp > WHEEL_INDEX_TTL:
            return None
        files = entry.get('files')
        return files if isinstance(files, list) else None

    def compatibilityTags(self) -> list[tuple[str, str, str]]:
        if self._tag_cache is not None:
            return self._tag_cache
        script = (
            'import json, sysconfig\n'
            'platform_tag = sysconfig.get_platform()\n'
            'platform_tag = platform_tag.replace("-", "_").replace(".", "_")\n'
            'print(json.dumps({\n'
            '  "py": sysconfig.get_config_var("py_version_nodot"),\n'
            '  "gil": int(bool(sysconfig.get_config_var("Py_GIL_DISABLED"))),\n'
            '  "platform": platform_tag,\n'
            '}))\n'
        )
        interpreter_tag = 'py3'
        platform_tag = 'any'
        gil_disabled = False
        try:
            completed = subprocess.run(
                [str(self.python_exe), '-c', script],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=20,
                env=self.env,
            )
            if completed.returncode == 0:
                data = json.loads(completed.stdout.strip().splitlines()[-1])
                interpreter_tag = f'cp{data.get("py") or "3"}'
                gil_disabled = bool(data.get('gil'))
                raw_platform = str(data.get('platform') or '')
                platform_tag = raw_platform or platform_tag
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
            pass
        abi_tag = f'{interpreter_tag}t' if gil_disabled else interpreter_tag
        self._interpreter_tag = interpreter_tag
        tags = [
            (interpreter_tag, abi_tag, platform_tag),
            (interpreter_tag, abi_tag, 'any'),
            ('py3', 'none', platform_tag),
            ('py3', 'none', 'any'),
            ('py2.py3', 'none', 'any'),
        ]
        self._tag_cache = tags
        return tags

    def _fetchFiles(self, package: str) -> list[dict] | None:
        key = f'{_indexCacheKey(self.mirror_url)}:{package}'
        payload = None
        candidates = [
            urljoin(self.mirror_url, f'{package}/'),
            urljoin(canonicalSimpleUrl(self.mirror_url), f'{package}/'),
        ]
        for candidate in dict.fromkeys(candidates):
            try:
                payload = fetchBytes(
                    candidate,
                    SIMPLE_INDEX_ACCEPT,
                    timeout=INDEX_TIMEOUT,
                )
                break
            except Exception as e:
                _logger.debug(
                    'simple index miss for %s at %s: %s',
                    package,
                    candidate,
                    e,
                )
        if payload is None:
            return self._cachedFiles(key)
        try:
            files = _parseSimpleJson(payload)
        except (ValueError, UnicodeDecodeError):
            # A mirror may answer with HTML even when JSON was requested.
            _logger.debug('simple index for %s is not JSON, using HTML', package)
            files = _parseSimpleHtml(payload)
        except Exception as e:
            _logger.debug('simple index parse failed for %s: %s', package, e)
            return self._cachedFiles(key)
        if not files:
            files = _parseSimpleHtml(payload)
        files = [
            dict(entry, url=_absoluteUrl(self.mirror_url, entry['url']))
            for entry in files
        ]
        if not files:
            return None
        # Big projects publish megabytes of index; cache only the wheels this
        # interpreter could install so the file stays small and fast to read.
        with self._cache_lock:
            cached = self._cachedFiles(key) or []
            merged = {entry['filename']: entry for entry in cached}
            for entry in self._filterFiles(files):
                merged[entry['filename']] = entry
            self._entries[key] = {
                'files': list(merged.values()),
                'time': time.time(),
            }
            self._writeCacheEntries(self._entries)
        return files

    def _filterFiles(self, files: list[dict]) -> list[dict]:
        """Keep only wheels this interpreter can install."""
        return [
            entry
            for entry in files
            if entry.get('filename', '').endswith('.whl')
            and self.matchesTags(entry['filename'])
            and self._matchesPython(entry.get('requires_python') or '')
        ]

    def resolveCached(self, requirement: RequirementInfo) -> WheelFile | None:
        """Resolve using only the on-disk cache, without touching the network."""
        key = (
            f'{_indexCacheKey(self.mirror_url)}:'
            f'{normalizePackageName(requirement.name)}'
        )
        files = self._cachedFiles(key)
        if not files:
            return None
        return self._pick(files, requirement)

    def _pick(
        self,
        files: list[dict],
        requirement: RequirementInfo,
    ) -> WheelFile | None:
        package = normalizePackageName(requirement.name)
        candidates = [
            entry
            for entry in files
            if entry['filename'].endswith('.whl')
            and self._matchesDistribution(entry['filename'], package)
            and self._matchesVersion(entry['filename'], requirement.version)
            and self.matchesTags(entry['filename'])
            and self._matchesPython(entry.get('requires_python') or '')
        ]
        if not candidates:
            return None
        # Prefer the most specific platform tag (cp314-win_amd64 over py3-none-any).
        candidates.sort(key=lambda entry: self._tagScore(entry['filename']))
        best = candidates[-1]
        hashes = best.get('hashes') or {}
        # The hash fragment is metadata, not part of the request URL.
        download_url = best['url'].split('#', 1)[0]
        return WheelFile(
            filename=best['filename'],
            url=download_url,
            sha256=str(hashes.get('sha256') or parseHashFragment(best['url'])),
            version=requirement.version,
            requires_python=best.get('requires_python') or '',
            size=int(best.get('size') or 0),
            metadata_url=(
                urljoin(download_url, str(best.get('metadata')))
                if best.get('metadata')
                else ''
            ),
            metadata_hash=parseHashFragment(str(best.get('metadata') or '')),
        )

    def resolve(self, requirement: RequirementInfo) -> WheelFile | None:
        package = normalizePackageName(requirement.name)
        files = self._fetchFiles(package)
        if files is None:
            return None
        return self._pick(files, requirement)

    def wheelVersions(self, package: str) -> dict[str, str]:
        """Map version -> file name for every wheel this interpreter can use."""
        files = self._fetchFiles(package)
        if files is None:
            return {}
        versions: dict[str, str] = {}
        for entry in files:
            filename = entry.get('filename') or ''
            if not filename.endswith('.whl'):
                continue
            if not self._matchesDistribution(filename, package):
                continue
            if not self.matchesTags(filename):
                continue
            if not self._matchesPython(entry.get('requires_python') or ''):
                continue
            versions.setdefault(_wheelVersion(filename), filename)
        return versions

    def resolveBest(self, requirement: RequirementInfo) -> WheelFile | None:
        """Pick the newest wheel matching a version range, like a resolver."""
        package = normalizePackageName(requirement.name)
        versions = self.wheelVersions(package)
        if not versions:
            return None
        usable = [
            version
            for version in versions
            if versionSpecifierAllows(requirement.specifier, version)
        ]
        if not usable:
            return None
        best = max(usable, key=versionKey)
        return self.resolve(RequirementInfo(requirement.name, best))

    def wheelMetadata(self, wheel: WheelFile) -> bytes | None:
        key = wheel.path.as_posix() if wheel.path is not None else wheel.url
        with self._metadata_lock:
            if key in self._metadata_cache:
                return self._metadata_cache[key]
        metadata = self._fetchMetadata(wheel)
        with self._metadata_lock:
            self._metadata_cache[key] = metadata
        return metadata

    def _fetchMetadata(self, wheel: WheelFile) -> bytes | None:
        if wheel.metadata_url:
            # PEP 658: a few kilobytes instead of the whole wheel.
            try:
                payload = fetchBytes(
                    wheel.metadata_url,
                    'text/plain',
                    timeout=INDEX_TIMEOUT,
                )
                if not wheel.metadata_hash or _matchesHash(
                    payload, wheel.metadata_hash
                ):
                    return payload
                _logger.debug('metadata hash mismatch for %s', wheel.filename)
            except Exception as e:
                _logger.debug(
                    'metadata fetch failed for %s: %s',
                    wheel.filename,
                    e,
                )
        return self._metadataFromWheel(wheel)

    def _metadataFromWheel(self, wheel: WheelFile) -> bytes | None:
        archive = None
        metadata: bytes | None = None
        source = wheel.path
        reuse = stagedWheelPath(wheel) if source is None else None
        try:
            if source is None:
                if (
                    not reuse.exists()
                    or not looksLikeWheel(reuse)
                    or not verifyWheelHash(reuse, wheel.sha256)
                ):
                    downloadWheelTo(
                        wheel.url,
                        reuse,
                        wheel.filename,
                        threading.Lock(),
                        {},
                    )
                source = reuse
            archive = zipfile.ZipFile(source)
            for entry in archive.namelist():
                if entry.endswith('.dist-info/METADATA'):
                    metadata = archive.read(entry)
                    break
        except Exception as e:
            _logger.debug('could not read metadata for %s: %s', wheel.filename, e)
        finally:
            if archive is not None:
                archive.close()
        return metadata

    def _matchesDistribution(self, filename: str, package: str) -> bool:
        parts = filename.split('-')
        return bool(parts) and normalizePackageName(parts[0]) == package

    def _matchesVersion(self, filename: str, version: str) -> bool:
        if not version:
            return True
        parts = filename.split('-')
        return len(parts) > 1 and parts[1] == version

    def matchesTags(self, filename: str) -> bool:
        stem = filename[:-4] if filename.endswith('.whl') else filename
        parts = stem.split('-')
        if len(parts) < 5:
            return False
        interpreter_tag, abi_tag, platform_tag = parts[-3], parts[-2], parts[-1]
        if not self._interpreterFits(interpreter_tag):
            return False
        if not self._abiFits(abi_tag):
            return False
        return self._platformFits(platform_tag)

    def _interpreterFits(self, tag: str) -> bool:
        allowed = {want for want, _abi, _platform in self.compatibilityTags()}
        if tag in allowed:
            return True
        # Pip accepts older cp3xx tags as candidates; the ABI check below decides
        # whether the wheel actually fits.
        for part in tag.split('.'):
            if part in allowed:
                return True
            if not part.startswith('cp') or not self._interpreter_tag.startswith('cp'):
                continue
            if part.endswith('t') != self._interpreter_tag.endswith('t'):
                continue
            try:
                older = int(part[2:5])
                mine = int(self._interpreter_tag[2:5])
            except ValueError:
                continue
            if older <= mine:
                return True
        return False

    def _abiFits(self, tag: str) -> bool:
        if tag == 'none':
            return True
        # abi3 wheels work on CPython 3.2 and newer.
        if tag == 'abi3':
            return self._interpreter_tag.startswith('cp')
        # Match pip exactly here: a cp39 (or even cp313) wheel does not install
        # on cp314 unless its ABI tag is abi3.
        return tag in {
            want_abi for _want, want_abi, _platform in self.compatibilityTags()
        }

    def _platformFits(self, tag: str) -> bool:
        allowed = [want for _want, _abi, want in self.compatibilityTags()]
        for part in tag.split('.'):
            if part == 'any' or part in allowed:
                return True
        return False

    def _tagScore(self, filename: str) -> int:
        stem = filename[:-4] if filename.endswith('.whl') else filename
        parts = stem.split('-')
        if len(parts) < 3:
            return 0
        platform_tag = parts[-1]
        if platform_tag != 'any':
            return 2
        return 1

    def _matchesPython(self, requires_python: str) -> bool:
        if not requires_python:
            return True
        return self._versionSatisfies(
            requires_python,
            '.'.join(self._targetVersion().split('.')[:2]),
        )

    @staticmethod
    def _versionSatisfies(spec: str, version: str) -> bool:
        """Minimal check for the >=3.x,<3.y forms mirrors actually publish."""
        target = tuple(int(part) for part in version.split('.') if part.isdigit())
        if not target:
            return True
        for item in spec.split(','):
            item = item.strip()
            match = re.match(r'(==|!=|>=|<=|>|<)\s*(\d+(?:\.\d+)*)', item)
            if match is None:
                continue
            operator, raw = match.group(1), match.group(2)
            bound = tuple(int(part) for part in raw.split('.') if part.isdigit())
            # Compare only the parts the bound specifies.
            left = target[: len(bound)]
            if operator in ('>=', '>=') and not left >= bound:
                return False
            if operator == '>' and not left > bound:
                return False
            if operator == '<=' and not left <= bound:
                return False
            if operator == '<' and not left < bound:
                return False
            if operator == '==' and left != bound:
                return False
            if operator == '!=' and left == bound:
                return False
        return True

    def _targetVersion(self) -> str:
        if self._version_cache is None:
            self._version_cache = '3.14'
            try:
                completed = subprocess.run(
                    [
                        str(self.python_exe),
                        '-c',
                        'import platform; print(platform.python_version())',
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=20,
                    env=self.env,
                )
                if completed.returncode == 0:
                    self._version_cache = completed.stdout.strip().splitlines()[-1]
            except (OSError, subprocess.TimeoutExpired, IndexError):
                pass
        return self._version_cache


def _wheelVersion(filename: str) -> str:
    stem = filename[:-4] if filename.endswith('.whl') else filename
    parts = stem.split('-')
    return parts[1] if len(parts) > 1 else ''


def versionKey(version: str) -> tuple:
    """Sort key that handles the numeric versions mirrors publish.

    The release segments are zero padded three wide so "2.10" sorts after
    "2.9", which plain string comparison gets wrong.
    """
    release: list[int] = []
    suffix = ''
    for part in re.split(r'[.\-_+]', version):
        if not part:
            continue
        if part.isdigit():
            release.append(int(part))
        else:
            suffix = suffix or part
    return (tuple(release), suffix)


def _releaseOf(key: tuple) -> tuple[int, ...]:
    return key[0]


def _sameRelease(left: tuple, right: tuple) -> bool:
    """PEP 440 equality: 1.4 and 1.4.0 are the same version."""
    left_release = list(_releaseOf(left))
    right_release = list(_releaseOf(right))
    width = max(len(left_release), len(right_release))
    left_release += [0] * (width - len(left_release))
    right_release += [0] * (width - len(right_release))
    return left_release == right_release


def versionSpecifierAllows(specifier: str, version: str) -> bool:
    """Check the comma separated specifiers that appear in Requires-Dist."""
    if not specifier:
        return True
    for item in specifier.split(','):
        item = item.strip()
        match = re.match(r'(===|==|!=|>=|<=|~=|>|<)\s*([0-9][0-9A-Za-z.\-_+]*)', item)
        if match is None:
            continue
        operator, bound = match.group(1), match.group(2)
        left, right = versionKey(version), versionKey(bound)
        if operator in ('==', '==='):
            if not _sameRelease(left, right):
                return False
        elif operator == '!=':
            if _sameRelease(left, right):
                return False
        elif operator == '>=' and left < right:
            return False
        elif operator == '<=' and left > right:
            return False
        elif operator == '>' and left <= right:
            return False
        elif operator == '<' and left >= right:
            return False
        elif operator == '~=':
            if left < right:
                return False
            # ~=1.4.2 means >=1.4.2 and ==1.4.*
            prefix = _releaseOf(right)[:-1]
            if _releaseOf(left)[: len(prefix)] != prefix:
                return False
    return True


def versionMatchesPin(pin: str, version: str) -> bool:
    """Match "==1.4" style pins, where 1.4 also means 1.4.0."""
    return _sameRelease(versionKey(version), versionKey(pin))


def requirementNames(requirement: str) -> str:
    """Strip extras, specifiers and markers: "anyio<5,>=3.5 ; extra == 'x'"."""
    return re.split(r'[<>=!~\[;]', requirement.split(';', 1)[0], maxsplit=1)[
        0
    ].strip()


def parseWheelRequirements(metadata: bytes) -> list[str]:
    """Read Requires-Dist, decoding the metadata's own encoding."""
    text = metadata.decode('utf-8', 'replace')
    requires: list[str] = []
    in_requires = False
    for line in text.splitlines():
        if line.startswith('Requires-Dist:'):
            requires.append(line.split(':', 1)[1].strip())
            in_requires = True
        elif in_requires and line.startswith((' ', '\t')):
            continue
        elif line.strip():
            in_requires = False
    return [item for item in requires if item]


def appliesToThisEnvironment(requirement: str) -> bool:
    """Evaluate a Requires-Dist environment marker.

    Markers like "extra == 'brotli'" or "sys_platform != 'win32'" mean the
    dependency is not wanted; fetching them pulled in brotlicffi and cffi.
    """
    if ';' not in requirement:
        return True
    marker = requirement.split(';', 1)[1].strip().lower()
    if not marker:
        return True
    result = _evaluateMarker(marker)
    # Only drop a dependency when a clause is definitely false.
    return result is not False


def _evaluateMarker(marker: str) -> bool | None:
    """Best effort PEP 508 marker check for the current platform."""
    any_unknown = False
    for part in re.split(r'\bor\b', marker):
        and_result: bool | None = True
        for clause in re.split(r'\band\b', part):
            clause = clause.strip()
            if not clause:
                continue
            evaluated = _evaluateClause(clause)
            if evaluated is False:
                and_result = False
                break
            if evaluated is None and and_result is True:
                and_result = None
        if and_result is True:
            return True
        if and_result is None:
            any_unknown = True
    # Unknown wins over false: dropping a needed dependency breaks the install,
    # while fetching an unneeded one only costs a little bandwidth.
    return None if any_unknown else False


def _evaluateClause(clause: str) -> bool | None:
    clause = clause.strip().strip('()').strip()
    match = re.match(
        r"(?P<left>[a-z_]+)\s*(?P<op>==|!=|<=|>=|<|>|~=)\s*"
        r"[\"'](?P<right>[^\"']*)[\"']",
        clause,
    )
    if match is None:
        # "extra == 'x'" style clauses with no marker on the left.
        match = re.match(
            r"[\"'](?P<right>[^\"']*)[\"']\s*(?P<op>==|!=)\s*(?P<left>[a-z_]+)",
            clause,
        )
        if match is None:
            return None
    variable = match.group('left')
    operator = match.group('op')
    value = match.group('right')
    if variable == 'extra':
        # We never request extras, so an extra clause is simply not satisfied.
        return operator == '!=' and value != ''
    actual = _MARKER_ENVIRONMENT.get(variable)
    if actual is None:
        return None
    if variable == 'python_version':
        actual = '.'.join(actual.split('.')[:2])
    if operator in ('==', '~='):
        return (
            actual == value
            or actual.startswith(value + '.')
            or actual.lower() == value.lower()
        )
    if operator == '!=':
        return not (
            actual == value
            or actual.startswith(value + '.')
            or actual.lower() == value.lower()
        )
    if operator in ('<', '<=', '>', '>='):
        try:
            left = tuple(int(part) for part in actual.split('.') if part.isdigit())
            right = tuple(int(part) for part in value.split('.') if part.isdigit())
        except ValueError:
            return None
        if not left or not right:
            return None
        width = max(len(left), len(right))
        left += (0,) * (width - len(left))
        right += (0,) * (width - len(right))
        if operator == '<':
            return left < right
        if operator == '<=':
            return left <= right
        if operator == '>':
            return left > right
        return left >= right
    return None

_MARKER_ENVIRONMENT: dict[str, str] = {
    'os_name': 'nt' if os.name == 'nt' else 'posix',
    'sys_platform': 'win32' if os.name == 'nt' else 'linux',
    'platform_system': 'Windows' if os.name == 'nt' else 'Linux',
    'platform_machine': platform.machine(),
    'platform_python_implementation': 'CPython',
    'python_version': '.'.join(platform.python_version_tuple()[:2]),
    'python_full_version': platform.python_version(),
    'implementation_name': 'cpython',
}


def requirementSpecifier(requirement: str) -> str:
    """Extract "(>=1,<2)" from "name>=1,<2"."""
    body = requirement.split(';', 1)[0]
    match = re.match(r'^[A-Za-z0-9._\-]+\s*(?P<spec>[<>=!~].*)$', body.strip())
    if match is None:
        return ''
    return match.group('spec').strip()


@dataclass
class _DownloadSpeedState:
    last_bytes: int
    last_time: float
    bytes_per_second: float = 0.0
    last_emit: float = 0.0
    samples: list[tuple[float, int]] = field(default_factory=list)


# Each phase owns a slice of the overall 0-100 bar. The slices are contiguous
# so the top bar always advances from start to finish.
PHASE_LAYOUT: dict[str, tuple[float, float, str, str]] = {
    'prepare': (0.0, 3.0, '正在准备依赖环境…', 'Preparing dependency environment...'),
    'audit': (3.0, 12.0, '正在检查已安装的库…', 'Checking installed packages...'),
    'resolve': (12.0, 30.0, '正在解析依赖…', 'Resolving dependencies...'),
    'download': (30.0, 78.0, '正在下载 wheel…', 'Downloading wheels...'),
    'install': (78.0, 96.0, '正在安装依赖…', 'Installing dependencies...'),
    'verify': (96.0, 99.0, '正在做最后检查…', 'Running a final check...'),
    'start': (99.0, 100.0, '正在启动 SouthsideMusic…', 'Starting SouthsideMusic...'),
}
SLOT_POOL_SIZE = 8
# Phases that measure themselves; the fade ticker must not fight their numbers.
PHASES_WITH_REAL_PROGRESS = frozenset({'resolve', 'download', 'install'})


class ProgressManager(QObject):
    """Owns the overall bar, the per-phase bar and the parallel slot bars.

    Workers call these from download and resolve threads, so every call that
    touches a widget is marshalled onto the GUI thread. The plain attributes
    (phase, phase_value) are kept in sync locally so ``overallPercent`` stays
    readable from any thread.
    """

    resize_requested = Signal()

    def __init__(
        self,
        window: QWidget,
        layout: QVBoxLayout,
        invoke: Callable[..., None],
    ) -> None:
        super().__init__(window)
        self.window = window
        self.layout = layout
        self._invoke = invoke
        self.phase = ''
        self.phase_start = 0.0
        self.phase_end = 0.0
        self.phase_value = 0.0
        self.phase_visible = False
        self.slots: list[QLabel | QProgressBar] = []
        self.slot_labels: dict[str, QLabel] = {}
        # Mirrors the GUI thread's copy so acquireSlot can decide without it.
        self.slot_names: list[str] = []

        self.main_bar = QProgressBar()
        self.main_bar.setRange(0, 100)
        self.main_bar.setValue(0)
        self.main_bar.setTextVisible(True)
        self.main_bar.setFixedHeight(18)
        self.main_bar.setStyleSheet(_PROGRESS_STYLE)

        self.stage_label = QLabel('')
        self.stage_label.setStyleSheet('color: #444444; font-size: 9pt;')
        self.stage_label.setWordWrap(True)
        self.stage_label.hide()

        self.stage_bar = QProgressBar()
        self.stage_bar.setRange(0, 100)
        self.stage_bar.setValue(0)
        self.stage_bar.setTextVisible(True)
        self.stage_bar.setFixedHeight(13)
        self.stage_bar.setStyleSheet(_PROGRESS_STYLE)
        self.stage_bar.hide()

        self.slot_box = QWidget()
        self.slot_layout = QVBoxLayout(self.slot_box)
        self.slot_layout.setContentsMargins(0, 0, 0, 0)
        self.slot_layout.setSpacing(1)
        self.slot_box.hide()

        # Resizing straight from a paint or layout pass can recurse, so batch
        # the height updates and let the event loop run them when it is idle.
        self.resize_requested.connect(
            self._resizeNow,
            Qt.ConnectionType.QueuedConnection,
        )

        self.layout.addWidget(self.main_bar)
        self.layout.addWidget(self.stage_label)
        self.layout.addWidget(self.stage_bar)
        self.layout.addWidget(self.slot_box)
        self.hideSlots()

    def mainBar(self) -> QProgressBar:
        return self.main_bar

    def setPhase(self, name: str) -> None:
        layout = PHASE_LAYOUT.get(name)
        if layout is None:
            return
        self.phase = name
        self.phase_start, self.phase_end = layout[0], layout[1]
        self.phase_value = 0.0
        self._invoke(self._applyPhase, name)

    def _applyPhase(self, name: str) -> None:
        layout = PHASE_LAYOUT.get(name)
        if layout is None:
            return
        self.stage_label.setText(layout[2] if _IS_CHINESE[0] else layout[3])
        if not self.phase_visible:
            self.stage_label.show()
            self.stage_bar.show()
            self.phase_visible = True
        self.stage_bar.setValue(0)
        self.main_bar.setValue(self.overallPercent())
        self.applyWindowHeight()

    def endPhase(self) -> None:
        """Phase finished: fill its slice so the overall bar never stalls."""
        if self.phase_visible and self.phase:
            self.setPhaseProgress(self.phase, 100.0)

    def setPhaseProgress(self, name: str, percent: float) -> None:
        if name != self.phase:
            return
        self.phase_value = max(0.0, min(100.0, percent))
        self._invoke(self._applyPhaseProgress, int(self.phase_value))

    def _applyPhaseProgress(self, value: int) -> None:
        self.stage_bar.setValue(value)
        self.main_bar.setValue(self.overallPercent())

    def overallPercent(self) -> int:
        span = self.phase_end - self.phase_start
        return int(self.phase_start + span * self.phase_value / 100.0)

    def reportParallel(self, done: int, total: int) -> None:
        """Report a parallel stage where finished items count as progress."""
        if total <= 0:
            return
        self.setPhaseProgress(self.phase, done * 100.0 / total)

    def acquireSlot(self, name: str) -> int | None:
        if name in self.slot_labels or name in self.slot_names:
            return None
        # Recycle the oldest bar when the pool is full so the window height
        # stays bounded while every active download still gets a bar.
        if len(self.slot_names) >= SLOT_POOL_SIZE:
            self.slot_names.pop(0)
        self.slot_names.append(name)
        self._invoke(self._applyAcquireSlot, name)
        return len(self.slot_names)

    def _applyAcquireSlot(self, name: str) -> None:
        if name in self.slot_labels:
            return
        while len(self.slot_labels) >= SLOT_POOL_SIZE:
            self.releaseSlot(next(iter(self.slot_labels)))
        label = QLabel('')
        label.setStyleSheet(
            'color: #666666; font-size: 8pt; font-family: Consolas, monospace;'
        )
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedHeight(7)
        bar.setStyleSheet(_PROGRESS_STYLE)
        self.slots.extend([label, bar])
        self.slot_layout.addWidget(label)
        self.slot_layout.addWidget(bar)
        self.slot_labels[name] = label
        self.slot_box.show()
        self.applyWindowHeight()

    def updateSlot(self, name: str, percent: float, text: str = '') -> None:
        self._invoke(self._applyUpdateSlot, name, percent, text)

    def _applyUpdateSlot(self, name: str, percent: float, text: str) -> None:
        index = self._slotIndex(name)
        if index is None:
            return
        bar = self.slots[index + 1]
        if not isinstance(bar, QProgressBar):
            return
        bar.setValue(int(max(0.0, min(100.0, percent))))
        if text:
            label = self.slot_labels[name]
            if label.text() != text:
                label.setText(text)

    def releaseSlot(self, name: str) -> None:
        if name in self.slot_names:
            self.slot_names.remove(name)
        self._invoke(self._applyReleaseSlot, name)

    def _applyReleaseSlot(self, name: str) -> None:
        label = self.slot_labels.pop(name, None)
        if label is None:
            return
        index = self.slots.index(label)
        bar = self.slots[index + 1]
        for widget in (label, bar):
            self.slot_layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        del self.slots[index : index + 2]
        if not self.slots:
            self.slot_box.hide()
        self.applyWindowHeight()

    def hideSlots(self) -> None:
        """Clear every bar. Call on the GUI thread only."""
        self.slot_names.clear()
        for name in list(self.slot_labels):
            self._applyReleaseSlot(name)

    def _slotIndex(self, name: str) -> int | None:
        label = self.slot_labels.get(name)
        if label is None:
            return None
        try:
            return self.slots.index(label)
        except ValueError:
            return None

    def applyWindowHeight(self) -> None:
        """Queue a height update; it runs once the event loop is idle."""
        self.resize_requested.emit()

    def _resizeNow(self) -> None:
        """Fit the window to the rows the layout currently shows.

        Summing the rows by hand counted the spacing twice and padded the
        result, and Qt handed the leftover pixels to the word-wrapped labels,
        which pushed the rows apart. Hidden rows are skipped, and a wrapped
        label is measured with ``heightForWidth``: that is the height Qt lays
        it out at, while its size hint asks for one line more and leaves the
        difference as slack.
        """
        layout = self.layout
        margins = layout.contentsMargins()
        spacing = layout.spacing()
        width = max(1, self.window.width() - margins.left() - margins.right())
        rows = 0
        target = margins.top() + margins.bottom()
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            height = item.sizeHint().height()
            if widget is not None and widget.hasHeightForWidth():
                height = widget.heightForWidth(width)
            target += height
            rows += 1
        if rows > 1:
            target += spacing * (rows - 1)
        if abs(target - self.window.height()) > 3:
            self.window.setFixedHeight(target)


_PROGRESS_STYLE = """
QProgressBar {
    border: 1px solid #c8c8c8;
    border-radius: 3px;
    background: #f2f2f2;
    color: #333333;
    font-size: 8pt;
    text-align: center;
}
QProgressBar::chunk {
    border-radius: 2px;
    background: #4a9eff;
}
"""

_IS_CHINESE: list[bool] = [True]


def getRequirements() -> list[RequirementInfo]:
    result = []
    for line in FULL_REQUIREMENTS.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if '==' not in line:
            continue
        name, version = line.split('==', 1)
        result.append(RequirementInfo(name=name, version=version))
    return result


def getFreeThreadedRequirements() -> list[RequirementInfo]:
    pinned_versions = {
        normalizePackageName(requirement.name): requirement.version
        for requirement in getRequirements()
    }
    return [
        RequirementInfo(
            name=name,
            version=pinned_versions.get(normalizePackageName(name), ''),
        )
        for name in FREE_THREADED_REQUIREMENT_NAMES
    ]


def getTableRequirements() -> list[RequirementInfo]:
    result = getRequirements()
    existing = {normalizePackageName(requirement.name) for requirement in result}
    for requirement in getFreeThreadedRequirements():
        normalized = normalizePackageName(requirement.name)
        if normalized not in existing:
            result.append(requirement)
            existing.add(normalized)
    return result


def normalizePackageName(name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower()


def getRequirementSpec(requirement: RequirementInfo) -> str:
    if not requirement.version:
        return requirement.name
    return f'{requirement.name}=={requirement.version}'


def ensurePipCacheDirs() -> None:
    PIP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    PIP_WHEELHOUSE.mkdir(parents=True, exist_ok=True)


def getRuntimeWheelhouse(python_exe: Path) -> Path:
    return PIP_WHEELHOUSE / normalizePackageName(python_exe.parent.name)


def getPipEnv(env: dict[str, str] | None = None) -> dict[str, str]:
    result = os.environ.copy() if env is None else env.copy()
    result.setdefault('PIP_DISABLE_PIP_VERSION_CHECK', '1')
    result.setdefault('PIP_NO_INPUT', '1')
    result.setdefault('PIP_CACHE_DIR', str(PIP_CACHE_DIR))
    result.setdefault('PIP_DEFAULT_TIMEOUT', PIP_TIMEOUT)
    return result


def findCachedWheel(requirement: RequirementInfo, wheelhouse: Path) -> Path | None:
    normalized_name = normalizePackageName(requirement.name)
    for wheel in wheelhouse.glob('*.whl'):
        parts = wheel.name.split('-')
        if len(parts) < 2:
            continue
        if normalizePackageName(parts[0]) != normalized_name:
            continue
        if requirement.version and parts[1] != requirement.version:
            continue
        return wheel
    return None


def parsePipDownloadSize(line: str) -> int | None:
    match = PIP_SIZE_RE.search(line)
    if match is None:
        return None
    unit = match.group('unit').lower()
    multiplier = PIP_SIZE_UNITS.get(unit)
    if multiplier is None:
        return None
    return int(float(match.group('size')) * multiplier)


def _pipSizeToBytes(size_text: str, unit: str) -> int | None:
    multiplier = PIP_SIZE_UNITS.get(unit.lower())
    if multiplier is None:
        return None
    return int(float(size_text) * multiplier)


def nativeDownloadEnabled() -> bool:
    return os.environ.get(NATIVE_DOWNLOAD_ENV, '1').strip() not in {
        '0',
        'false',
        'no',
        'off',
    }


def parseHashFragment(url: str) -> str:
    if '#' not in url:
        return ''
    fragment = url.split('#', 1)[1]
    if not fragment.startswith('sha256='):
        return ''
    return fragment[len('sha256=') :].strip().lower()


def verifyWheelHash(path: Path, sha256: str) -> bool:
    if not sha256:
        return True
    digest = hashlib.sha256()
    try:
        with path.open('rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(block)
    except OSError:
        return False
    return digest.hexdigest().lower() == sha256


def looksLikeWheel(path: Path) -> bool:
    """A wheel is a zip; mirrors sometimes answer with an HTML error page."""
    try:
        with path.open('rb') as handle:
            return handle.read(2) == b'PK'
    except OSError:
        return False


def _matchesHash(payload: bytes, sha256: str) -> bool:
    return hashlib.sha256(payload).hexdigest().lower() == sha256.lower()


def _indexCacheKey(mirror_url: str) -> str:
    return hashlib.sha256(mirror_url.encode('utf-8')).hexdigest()[:16]


def _metadataUrl(value: object) -> str:
    """Normalise the PEP 658 dist-info metadata URL, which may be true or a hash."""
    if isinstance(value, str) and value:
        return value
    if value:
        return 'METADATA'
    return ''


def _parseSimpleJson(payload: bytes) -> list[dict]:
    data = json.loads(payload.decode('utf-8'))
    files = data.get('files')
    if not isinstance(files, list):
        return []
    result: list[dict] = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        filename = entry.get('filename')
        url = entry.get('url')
        if not isinstance(filename, str) or not isinstance(url, str):
            continue
        result.append(
            {
                'filename': filename,
                'url': url,
                'hashes': entry.get('hashes') or {},
                'requires_python': entry.get('requires-python') or '',
                'size': entry.get('size') or 0,
                # PEP 658: the METADATA file can be fetched without the wheel.
                'metadata': _metadataUrl(entry.get('dist-info-metadata')),
            }
        )
    return result


def _parseSimpleHtml(payload: bytes) -> list[dict]:
    text = payload.decode('utf-8', 'replace')
    result: list[dict] = []
    # Fallback for mirrors that ignore the JSON accept header.
    for match in re.finditer(r'<a\s+([^>]*)>(.*?)</a>', text, re.S):
        attributes, label = match.group(1), match.group(2)
        href = re.search(r'href="([^"]+)"', attributes)
        if href is None:
            continue
        raw_url = href.group(1)
        # Take the file name from the href, not the anchor text: mirrors wrap
        # the label over several lines with a leading tag.
        path = raw_url.split('#', 1)[0].split('?', 1)[0]
        filename = path.rstrip('/').rsplit('/', 1)[-1]
        if not filename:
            filename = label.strip().splitlines()[-1].strip()
        if not filename:
            continue
        data_python = re.search(r'data-requires-python="([^"]*)"', attributes)
        data_metadata = re.search(
            r'data-dist-info-metadata="([^"]*)"',
            attributes,
        )
        result.append(
            {
                'filename': filename,
                'url': raw_url,
                'hashes': {},
                # The attribute is HTML escaped in the page ("&gt;=3.9").
                'requires_python': (
                    html.unescape(data_python.group(1)) if data_python else ''
                ),
                'size': 0,
                'metadata': _metadataUrl(
                    html.unescape(data_metadata.group(1)) if data_metadata else ''
                ),
            }
        )
    return result


def _absoluteUrl(base_url: str, url: str) -> str:
    if url.startswith(('http://', 'https://')):
        return url
    return urljoin(base_url, url)


def pipTimeoutSeconds() -> float:
    try:
        return float(PIP_TIMEOUT)
    except ValueError:
        return 20.0


def canonicalSimpleUrl(mirror_url: str) -> str:
    """Fallback simple index for a mirror.

    Mirrors sometimes reject their own artifact URLs or answer the index with
    an error, so the canonical PyPI index is the reliable second try.
    """
    return 'https://pypi.org/simple/'


def alternateWheelUrls(url: str) -> list[str]:
    """Other hosts that may serve the same artifact path.

    Several mirrors answer their own artifact URLs with 404 (Aliyun and Tencent
    both do), so the same path is retried on mirrors that do serve artifacts.
    """
    stripped = url.split('#', 1)[0]
    marker = '/packages/'
    if marker not in stripped:
        return []
    path = stripped[stripped.index(marker) :]
    # A host proven to serve artifacts comes first, then the other mirrors, then
    # the CDN. The URL the index advertised goes last: a mirror that 404s its
    # own artifacts would otherwise waste the first attempt on every wheel.
    dead = _DEAD_ARTIFACT_HOSTS
    working = [host for host in _WORKING_ARTIFACT_HOSTS if host not in dead]
    others = [
        host
        for host in _ARTIFACT_CANDIDATES
        if host not in working and host not in dead
    ]
    alternates: list[str] = []
    for host in working + others:
        candidate = host + path
        if candidate not in alternates:
            alternates.append(candidate)
    known_hosts = {_hostOf(item) for item in alternates}
    if _hostOf(stripped) not in known_hosts:
        alternates.append(stripped)
    return alternates


def fetchBytes(
    url: str,
    accept: str,
    timeout: float | None = None,
) -> bytes:
    """Fetch a URL, transparently undoing any gzip/deflate encoding.

    urllib does not decompress on its own, and mirrors happily gzip a 1 MB
    index page, which then fails to decode as JSON or HTML.
    """
    request = Request(
        url,
        headers={
            'Accept': accept,
            'User-Agent': 'SouthsideMusic',
            'Accept-Encoding': 'gzip, deflate',
        },
    )
    with urlopen(request, timeout=timeout or pipTimeoutSeconds()) as response:
        payload = response.read()
        encoding = (response.headers.get('Content-Encoding') or '').lower()
    if encoding == 'gzip':
        payload = gzip.decompress(payload)
    elif encoding == 'deflate':
        try:
            payload = zlib.decompress(payload)
        except zlib.error:
            payload = zlib.decompress(payload, -zlib.MAX_WBITS)
    return payload


def downloadWheelTo(
    url: str,
    target: Path,
    package_name: str,
    lock: threading.Lock,
    current: dict[str, int],
) -> None:
    """Stream a wheel to disk, publishing the byte count as it arrives.

    Tries the canonical mirror list as well, because several mirrors refuse the
    artifact URLs their own index advertises.
    """
    host = _hostOf(url)
    if host:
        _INDEX_HOST[0] = host
    pickArtifactHost()
    candidates = alternateWheelUrls(url)
    if url not in candidates:
        candidates.append(url)
    last_error: Exception | None = None
    attempted: list[str] = []
    for candidate in candidates:
        attempted.append(candidate)
        temporary = target.with_suffix(target.suffix + '.part')
        received = 0
        try:
            request = Request(
                candidate,
                headers={'User-Agent': 'SouthsideMusic'},
            )
            with urlopen(request, timeout=pipTimeoutSeconds()) as response:
                with temporary.open('wb') as handle:
                    while True:
                        block = response.read(256 * 1024)
                        if not block:
                            break
                        handle.write(block)
                        received += len(block)
                        with lock:
                            current[package_name] = received
            temporary.replace(target)
            candidate_host = _hostOf(candidate)
            if not looksLikeWheel(target):
                # A mirror answered the wheel URL with an HTML page.
                target.unlink(missing_ok=True)
                last_error = ValueError(
                    f'{candidate_host} returned a non-wheel payload'
                )
                if candidate_host and candidate_host != _hostOf(url):
                    _DEAD_ARTIFACT_HOSTS.add(candidate_host)
                _logger.debug('non-wheel payload from %s', candidate)
                continue
            if candidate_host and candidate_host not in _WORKING_ARTIFACT_HOSTS:
                _WORKING_ARTIFACT_HOSTS.insert(0, candidate_host)
                _DEAD_ARTIFACT_HOSTS.discard(candidate_host)
                if candidate_host != _hostOf(url):
                    _logger.info(
                        'downloading wheels through %s',
                        candidate_host,
                    )
            return
        except Exception as e:
            last_error = e
            temporary.unlink(missing_ok=True)
            with lock:
                current[package_name] = 0
            failed_host = _hostOf(candidate)
            if failed_host and failed_host != _hostOf(url):
                _DEAD_ARTIFACT_HOSTS.add(failed_host)
            _logger.debug('wheel fetch failed from %s: %s', candidate, e)
    if last_error is not None:
        _logger.warning(
            'could not fetch %s from %d mirrors: %s',
            package_name,
            len(attempted),
            last_error,
        )
        raise last_error


def _hostOf(url: str) -> str:
    match = re.match(r'(https?://[^/]+)', url)
    return match.group(1) if match else ''


def parsePipProgress(line: str) -> tuple[int, int] | None:
    """Read pip's live "123 kB/456 kB" download progress line."""
    match = PIP_PROGRESS_RE.search(line)
    if match is None:
        return None
    downloaded = _pipSizeToBytes(match.group('downloaded'), match.group('down_unit'))
    total = _pipSizeToBytes(match.group('total'), match.group('total_unit'))
    if downloaded is None or total is None:
        return None
    return downloaded, total


def parsePipPackageName(line: str, prefix: str) -> str | None:
    match = re.search(rf'{prefix}\s+([a-zA-Z0-9_\-\.]+)', line)
    if match is None:
        return None
    token = match.group(1)
    # Downloading lines carry the file name ("Downloading numpy-2.4.2-...whl"),
    # so only the distribution part belongs in a status label. Hyphens inside a
    # distribution name must survive (pysidesix-frameless-window-0.8.0-...).
    name_match = PIP_NAME_RE.match(token)
    if name_match is not None:
        return name_match.group('name')
    return token


def formatBytes(size: float) -> str:
    if size < 1024:
        return f'{max(0.0, size):.0f} B'
    if size < 1024 * 1024:
        return f'{size / 1024:.1f} KiB'
    return f'{size / (1024 * 1024):.2f} MiB'


def formatSpeed(bytes_per_second: float) -> str:
    return f'{formatBytes(bytes_per_second)}/s'


def parseWheelPackageName(path_text: str) -> str:
    wheel_name = Path(path_text.strip()).name
    if wheel_name.endswith('.whl'):
        return wheel_name.split('-', 1)[0].replace('_', '-')
    return wheel_name


def getInstalledPackages(
    python_exe: Path = PYTHON_EXE,
    env: dict[str, str] | None = None,
) -> list[RequirementInfo]:
    result = []
    completed = subprocess.run(
        [str(python_exe), '-c', INSTALLED_PACKAGES_SCRIPT],
        cwd=str(SCRIPT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
        env=getPipEnv(env),
    )
    if completed.returncode != 0:
        _logger.warning(
            'importlib metadata check failed for %s: %s',
            python_exe,
            completed.stdout.strip(),
        )
        completed = subprocess.run(
            [str(python_exe), '-m', 'pip', 'list', '--format', 'json'],
            cwd=str(SCRIPT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            env=getPipEnv(env),
        )
    if completed.returncode != 0:
        _logger.warning(
            'pip list failed for %s: %s',
            python_exe,
            completed.stdout.strip(),
        )
        return result
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError:
        _logger.warning('package list returned invalid JSON for %s', python_exe)
        return result
    for data in parsed:
        if normalizePackageName(data['name']) == 'pip':
            continue
        result.append(RequirementInfo(name=data['name'], version=data['version']))
    return result


def getInstalledVersionSets(
    installed: list[RequirementInfo],
) -> dict[str, set[str]]:
    versions: dict[str, set[str]] = {}
    for requirement in installed:
        versions.setdefault(normalizePackageName(requirement.name), set()).add(
            requirement.version
        )
    return versions


def getUnsatisfiedRequirements(
    installed: list[RequirementInfo], required: list[RequirementInfo]
) -> list[RequirementInfo]:
    installed_versions = getInstalledVersionSets(installed)
    unsatisfied = []
    for requirement in required:
        versions = installed_versions.get(normalizePackageName(requirement.name))
        if not versions:
            unsatisfied.append(requirement)
        elif requirement.version and requirement.version not in versions:
            # A site-packages can hold several versions of one distribution (an
            # installer upgrade that left the old metadata behind does this).
            # Only the pinned version counts as satisfied.
            unsatisfied.append(requirement)
    return unsatisfied


def getConflictingVersions(
    installed: list[RequirementInfo], requirement: RequirementInfo
) -> list[str]:
    versions = getInstalledVersionSets(installed).get(
        normalizePackageName(requirement.name), set()
    )
    if not requirement.version:
        return []
    return sorted(version for version in versions if version != requirement.version)


def mergeRequirements(requirements: list[RequirementInfo]) -> list[RequirementInfo]:
    result: list[RequirementInfo] = []
    seen: set[str] = set()
    for requirement in requirements:
        normalized = normalizePackageName(requirement.name)
        if normalized in seen:
            continue
        result.append(requirement)
        seen.add(normalized)
    return result


def getMissingImports(
    python_exe: Path,
    required: list[RequirementInfo],
    import_checks: dict[str, str],
    env: dict[str, str] | None = None,
) -> list[RequirementInfo]:
    requirement_map = {
        normalizePackageName(requirement.name): requirement for requirement in required
    }
    module_map = {
        normalized: module_name
        for normalized, module_name in import_checks.items()
        if normalized in requirement_map
    }
    if not module_map:
        return []

    # A single DLL load failure is enough to report scipy as broken for good,
    # so give a package several chances before calling it missing.
    pending = dict(module_map)
    for attempt in range(IMPORT_CHECK_ATTEMPTS):
        missing_names, output_lines, output = _runImportCheck(
            python_exe,
            pending,
            env,
        )
        if missing_names is None:
            # The check itself failed (timeout, crash); retry it too.
            if attempt + 1 < IMPORT_CHECK_ATTEMPTS:
                time.sleep(IMPORT_CHECK_RETRY_DELAY)
                continue
            _logger.warning('import check failed in %s: %s', python_exe, output)
            return list(requirement_map.values())
        if not missing_names:
            return []
        for name in missing_names:
            if name in requirement_map:
                _logger.warning(
                    'import check: %s failed to import (%s)',
                    name,
                    _importFailureReason(output_lines, name),
                )
        if attempt + 1 >= IMPORT_CHECK_ATTEMPTS:
            return [
                requirement_map[name]
                for name in missing_names
                if name in requirement_map
            ]
        _logger.info(
            'retrying import check for: %s',
            ', '.join(sorted(missing_names)),
        )
        pending = {
            name: module_map[name] for name in missing_names if name in module_map
        }
        time.sleep(IMPORT_CHECK_RETRY_DELAY)
    return []


def _runImportCheck(
    python_exe: Path,
    module_map: dict[str, str],
    env: dict[str, str] | None,
) -> tuple[set[str] | None, list[str], str]:
    """Import every module in a worker process.

    Returns the names that failed, or None when the check itself did not run.
    """
    try:
        completed = subprocess.run(
            [
                str(python_exe),
                '-c',
                MISSING_IMPORTS_SCRIPT,
                json.dumps(module_map),
            ],
            cwd=str(SCRIPT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            timeout=IMPORT_CHECK_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, [], str(e)
    output = completed.stdout.strip()
    if completed.returncode != 0:
        return None, output.splitlines(), output
    output_lines = output.splitlines()
    parsed_missing: list[str] | None = None
    for index in range(len(output_lines) - 1, -1, -1):
        try:
            candidate = json.loads(output_lines[index])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, list):
            parsed_missing = candidate
            break
    if parsed_missing is None:
        return None, output_lines, output
    return set(parsed_missing), output_lines, output


def _importFailureReason(output_lines: list[str], package_name: str = '') -> str:
    """Pick the exception line that belongs to one package."""
    if not package_name:
        return 'no detail'
    prefix = f'{package_name}:'
    for line in output_lines:
        if line.strip().startswith(prefix):
            return line.strip()[len(prefix) :].strip()[:160]
    # No line for this package: the import died without an exception (a native
    # crash) instead of raising, so do not blame another package's message.
    return 'no output (the import crashed)'


def getPySideRequirements(required: list[RequirementInfo]) -> list[RequirementInfo]:
    return [
        requirement
        for requirement in required
        if normalizePackageName(requirement.name) in PYSIDE_REQUIREMENT_NAMES
    ]


def isFullPySideInstalled(site_packages: Path = SITE_PACKAGES) -> bool:
    return all((site_packages / path).exists() for path in PYSIDE_REQUIRED_FILES)


def getFreeThreadedEnv() -> dict[str, str]:
    env = os.environ.copy()
    env['PYTHON_GIL'] = '0'
    # The portable runtime copied from uv retains its PEP 668 marker.
    env['PIP_BREAK_SYSTEM_PACKAGES'] = '1'
    return env


def isFreeThreadedPython(python_exe: Path) -> bool:
    if not python_exe.is_file():
        return False
    try:
        completed = subprocess.run(
            [
                str(python_exe),
                '-c',
                (
                    'import sys, sysconfig; '
                    'print(int(not getattr(sys, "_is_gil_enabled", lambda: True)())); '
                    'print(sysconfig.get_config_var("Py_GIL_DISABLED"))'
                ),
            ],
            cwd=str(SCRIPT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=5,
            env=getFreeThreadedEnv(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode != 0:
        return False
    values = [line.strip() for line in completed.stdout.splitlines()]
    return values[:2] == ['1', '1']


class BootstrapWindow(QWidget):
    latencyFinished = Signal(str, str, float)
    allDone = Signal()

    task = Signal(object)
    progressChanged = Signal(int, str)
    elapsedChanged = Signal(str)
    invokeRequested = Signal(object)
    startupFailed = Signal(int)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.NoTitleBarBackgroundHint
        )
        locale_name = QLocale.system().name().lower()
        self._language = 'zh' if locale_name in {'zh_cn', 'zh_hans_cn'} else 'en'
        self._text_map = {
            'en': {
                'title': 'Setting up Environment',
                'initial_status': 'Preparing dependency environment...',
                'initial_tip': 'Tip: SouthsideMusic will start automatically when setup finishes.',
                'tips': [
                    'Tips: Lyrics, cover art, and playback state are cached for faster startup next time.',
                    "Tips: FFMpeg only needs to be prepared once; it won't be re-downloaded on later launches.",
                    'Tips: Multiple wheels are downloaded in parallel; this part goes by quickly.',
                    'Tips: SouthsideMusic was originally called LyricsStudio.',
                    'Tips: Follow my Bilibili account! @Adreno9135',
                    'Tips: Loudness normalization helps different songs feel closer in volume.',
                    'Tips: The spectrum view gives the music a little visual shadow.',
                    'Tips: You can edit lyrics, translate them, and export lyric videos.',
                    'Tips: Crossfade tries to make the space between two songs less abrupt.',
                    'Tips: The library remembers your collection, sorting, and play counts.',
                    'Tips: Onerad asks for confirmation before performing actions in the app.',
                    'Tips: Private roaming and similar songs are there for moments when search is empty.',
                    'Tips: Cache cleanup protects core data and prefers removing less-used files first.',
                ],
                'mirror': 'Using {mirror} mirror ({latency} ms). Installing dependencies...',
                'checking_environment': 'Checking installed packages...',
                'checking_runtime': 'Checking {runtime} package state...',
                'checking_imports': 'Checking {runtime} imports...',
                'checking': 'Environment check complete. Preparing dependencies from {mirror}...',
                'starting': 'Dependencies installed. Starting SouthsideMusic...',
                'download_stage': 'Downloading wheels...',
                'reinstalling': 'Reinstalling {packages}...',
                'resolving_wheels': 'Resolving {count} wheels from the mirror...',
                'batch_download': 'Downloading wheels from the mirror...',
                'downloading': (
                    'Downloading {package} {percent}% '
                    '({done}, {speed}), {current}/{total} packages'
                ),
                'downloading_wheels': (
                    'Downloading {count} wheels (dependencies included)...'
                ),
                'download': 'Downloading {package} ({percent}%)...',
                'downloaded': 'Downloaded {package} ({current}/{total})',
                'cached': 'Using cached wheel {package} ({current}/{total})',
                'finalizing': 'Downloads complete. Finalizing dependencies; finishing up...',
                'checking_install': 'Installation complete. Running a final check...',
                'installing': 'Installing dependencies; finishing up...',
                'installing_package': 'Installing {package}...',
                'offline_install': 'Installing from wheel cache ({count} packages)...',
                'online_install': 'Installing missing packages ({count} packages)...',
                'runtime_install': 'Installing {runtime} requirements...',
                'startup_failed': (
                    'SouthsideMusic exited while starting (code {code}). '
                    'The log above has the details.'
                ),
                'install_failed': (
                    'Dependency setup failed ({packages}). Check your network and '
                    'restart SouthsideMusic to retry.'
                ),
            },
            'zh': {
                'title': '设置环境',
                'initial_status': '正在准备依赖环境…',
                'initial_tip': 'Tips: SouthsideMusic 准备完成后会自动启动。',
                'tips': [
                    'Tips: 歌词、封面和播放状态会缓存，下次启动会更快。',
                    'Tips: FFMpeg 只需首次准备，之后启动不用重新下载。',
                    'Tips: 多个 wheel 会并行下载，这一段很快就好。',
                    'Tips: SouthsideMusic 最开始的名字叫 LyricsStudio',
                    'Tips: 关注我的 Bilibili 账号！@Adreno9135',
                    'Tips: 响度均衡可以让不同歌曲听起来更接近同一个音量。',
                    'Tips: 频谱让声音多了一点可以看见的影子。',
                    'Tips: 你可以编辑歌词、翻译歌词，还能导出歌词视频。',
                    'Tips: 交叉淡化会尽量把两首歌之间生硬的缝隙磨平。',
                    'Tips: 库页面会记住你的收藏、排序方式和播放次数。',
                    'Tips: Onerad 在执行应用内操作前，会先停下来等待确认。',
                    'Tips: 不知道听什么时，可以试试私人漫游和相似歌曲。',
                    'Tips: 缓存清理会保护核心数据，并优先回收较少使用的文件。',
                ],
                'mirror': '正在使用 {mirror} 镜像（{latency} 毫秒），开始安装依赖…',
                'checking_environment': '正在检查已经安装的库…',
                'checking_runtime': '正在检查 {runtime} 的库状态…',
                'checking_imports': '正在检查 {runtime} 导入状态…',
                'checking': '环境检查完成，正在从 {mirror} 准备依赖…',
                'starting': '依赖安装完成，正在启动 SouthsideMusic…',
                'download_stage': '正在下载 wheel…',
                'reinstalling': '正在重新安装 {packages}…',
                'resolving_wheels': '正在解析镜像上的 {count} 个 wheel…',
                'batch_download': '正在从镜像下载 wheel…',
                'downloading': (
                    '正在下载 {package} {percent}%'
                    '（{done}，{speed}），共 {current}/{total} 个库'
                ),
                'downloading_wheels': '正在下载 {count} 个 wheel（含子依赖）…',
                'download': '正在下载 {package}（{percent}%）…',
                'downloaded': '已下载 {package}（{current}/{total}）',
                'cached': '使用缓存 wheel {package}（{current}/{total}）',
                'finalizing': '下载完成，正在整理依赖；马上就好…',
                'checking_install': '安装完成，正在做最后检查…',
                'installing': '正在安装依赖，最后整理一下…',
                'installing_package': '正在安装 {package}…',
                'offline_install': '正在从本地 wheel 缓存安装（{count} 个库）…',
                'online_install': '正在安装缺失依赖（{count} 个库）…',
                'runtime_install': '正在安装 {runtime} 依赖…',
                'startup_failed': 'SouthsideMusic 启动时退出了（代码 {code}），具体原因见上方日志。',
                'install_failed': '依赖安装失败（{packages}）。请检查网络后重启 SouthsideMusic 重试。',
            },
        }
        self._tips = self._text_map[self._language]['tips']
        self._tip_index = 0
        self._tip_text = self._tips[0]
        self._tip_phase = 'hold'
        self._tip_timer = QTimer(self)
        self._tip_timer.timeout.connect(self._animateTip)

        self.setWindowTitle(self._text('title'))
        self.setFixedWidth(int(app.primaryScreen().size().width() * 0.3))

        self._layout = QVBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.hide()  # replaced by mwindow.progress manager
        self.status_label = QLabel(self._text('initial_status'))
        self.status_label.setWordWrap(True)
        self.elapsed_label = QLabel()
        self.elapsed_label.setStyleSheet('color: #888888; font-size: 9pt;')
        self.elapsed_label.hide()
        self.tip_label = QLabel(self._text('initial_tip'))
        self.tip_label.setWordWrap(True)
        self.tip_label.setStyleSheet('color: #888888; font-size: 9pt;')
        _IS_CHINESE[0] = self._language == 'zh'
        self.mwindow = ProgressManager(self, self._layout, self._invokeOnGui)
        self._layout.addWidget(self.status_label)
        self._layout.addWidget(self.elapsed_label)
        self._layout.addWidget(self.tip_label)
        self.setLayout(self._layout)
        self._scheduleTipHold()

        self._download_total = 0
        self._download_completed = 0
        self._download_progress: dict[str, int] = {}
        self._download_active: dict[str, tuple[str, int, int, float]] = {}
        self._download_speed: dict[str, _DownloadSpeedState] = {}
        self._download_headline = ''
        self._download_progress_lock = threading.Lock()
        self._progress_value = 0
        self._install_stage_start = 10
        self._install_stage_end = 98
        self._check_started = False
        self._active_mirror_url = ''
        self._heartbeat_started_at = time.perf_counter()
        self._heartbeat_last_second = -1
        self._heartbeat_stop = False
        self._gui_heartbeat: QTimer | None = None

        self._gil_installed: list[RequirementInfo] | None = None
        self._gil_unsatisfied: list[RequirementInfo] | None = None
        self._gil_pyside_incomplete: bool | None = None
        self._ft_runtime_ok: bool | None = None
        self._ft_installed: list[RequirementInfo] | None = None
        self._ft_unsatisfied: list[RequirementInfo] | None = None

        self.latency_test_threads = []
        self.latency_testing = False
        self.latency_lock = threading.Lock()
        self.latencyFinished.connect(self.latencyTestFinished)
        self.progressChanged.connect(self.updateProgressUi)
        self.elapsedChanged.connect(self.elapsed_label.setText)
        self.invokeRequested.connect(self._runInvoke)
        self.startupFailed.connect(self.reportStartupFailure)

        self.task.connect(self.doTask)

        self.show()
        self.mwindow.applyWindowHeight()

    def _text(self, key: str, **kwargs: object) -> str:
        return self._text_map[self._language][key].format(**kwargs)  # type: ignore

    def _scheduleTipHold(self) -> None:
        self._tip_phase = 'hold'
        self._tip_timer.setSingleShot(True)
        self._tip_timer.start(random.randint(2000, 2500))

    def _beginTipErase(self) -> None:
        self._tip_phase = 'erase'
        self._tip_timer.setSingleShot(False)
        self._tip_timer.start(25)

    def _animateTip(self) -> None:
        if self._tip_phase == 'hold':
            self._beginTipErase()
            return
        if self._tip_phase == 'erase':
            self._tip_text = self._tip_text[:-1]
            self.tip_label.setText(self._tip_text)
            if not self._tip_text:
                self._tip_index = (self._tip_index + 1) % len(self._tips)
                self._tip_text = ''
                self._tip_phase = 'write'
        else:
            next_tip = self._tips[self._tip_index]
            self._tip_text += next_tip[len(self._tip_text)]
            self.tip_label.setText(self._tip_text)
            if self._tip_text == next_tip:
                self._scheduleTipHold()
        # A tip that grows past the window width needs another line.
        self.mwindow.applyWindowHeight()

    def doTask(self, content: object):
        if isinstance(content, Callable):
            content()

    def latencyTestFinished(self, mirror_name: str, mirror_url: str, latency: float):
        _logger.info(f'latency test finished: {mirror_name} {mirror_url} {latency}s')

        # Probe the artifact mirrors now, while the package checks run.
        warmArtifactHost()

        self.progressChanged.emit(
            10,
            self._text('checking', mirror=mirror_name),
        )
        threading.Thread(
            target=self.installRequirements, args=(mirror_name, mirror_url, latency)
        ).start()

    def installRequirements(
        self, mirror_name: str, mirror_url: str, latency: float
    ) -> None:
        self._active_mirror_url = mirror_url
        self.updateStatusText(
            self._text('mirror', mirror=mirror_name, latency=int(latency * 1000))
        )
        self.beginPhase('download')
        self.installRuntimeRequirements(
            'GIL Python',
            PYTHON_EXE,
            getRequirements(),
            mirror_url,
            site_packages=SITE_PACKAGES,
            reinstall_pyside=True,
            installed=self._gil_installed,
            unsatisfied=self._gil_unsatisfied,
            pyside_incomplete=self._gil_pyside_incomplete,
        )
        self.installFreeThreadedRequirements(mirror_url)
        self.beginPhase('verify')
        missing = self.getMissingRuntimeRequirements()
        if missing:
            # A package can be installed at the right version yet unable to
            # import (an extension whose DLL never loads). Pip calls that
            # "already satisfied", so reinstall exactly those once.
            self._repairMissing(missing)
            missing = self.getMissingRuntimeRequirements()
        if missing:
            package_names = ', '.join(requirement.name for requirement in missing)
            _logger.error('dependency setup incomplete: %s', package_names)
            self.progressChanged.emit(
                99,
                self._text('install_failed', packages=package_names),
            )
            return
        self.progressChanged.emit(
            100,
            self._text('starting'),
        )
        self.mwindow.mainBar().setValue(100)
        self.mwindow.hideSlots()
        self.allDone.emit()

    def installFreeThreadedRequirements(self, mirror_url: str) -> bool:
        if self._ft_runtime_ok is False:
            return True
        if self._ft_runtime_ok is None and not FREE_THREADED_PYTHON_EXE.exists():
            _logger.warning(
                'free-threaded Python not found: %s', FREE_THREADED_PYTHON_EXE
            )
            return True
        if self._ft_runtime_ok is None and not isFreeThreadedPython(
            FREE_THREADED_PYTHON_EXE
        ):
            _logger.warning(
                'free-threaded Python failed validation: %s',
                FREE_THREADED_PYTHON_EXE,
            )
            return False

        return self.installRuntimeRequirements(
            'no-GIL Python',
            FREE_THREADED_PYTHON_EXE,
            getFreeThreadedRequirements(),
            mirror_url,
            env=getFreeThreadedEnv(),
            import_checks=FREE_THREADED_IMPORT_CHECKS,
            installed=self._ft_installed,
            unsatisfied=self._ft_unsatisfied,
        )

    def installRuntimeRequirements(
        self,
        runtime_name: str,
        python_exe: Path,
        required: list[RequirementInfo],
        mirror_url: str,
        *,
        site_packages: Path | None = None,
        reinstall_pyside: bool = False,
        install_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        import_checks: dict[str, str] | None = None,
        installed: list[RequirementInfo] | None = None,
        unsatisfied: list[RequirementInfo] | None = None,
        pyside_incomplete: bool | None = None,
    ) -> bool:
        self.updateStatusText(self._text('runtime_install', runtime=runtime_name))
        prechecked_unsatisfied = unsatisfied is not None
        if installed is None:
            self.progressChanged.emit(
                5,
                self._text('checking_runtime', runtime=runtime_name),
            )
            installed = getInstalledPackages(python_exe, env=env)
        if unsatisfied is None:
            unsatisfied = getUnsatisfiedRequirements(installed, required)
        if import_checks is not None and not prechecked_unsatisfied:
            unsatisfied = mergeRequirements(
                unsatisfied
                + getMissingImports(python_exe, required, import_checks, env=env)
            )
        if pyside_incomplete is None:
            pyside_incomplete = (
                reinstall_pyside
                and site_packages is not None
                and not isFullPySideInstalled(site_packages)
            )
        if not unsatisfied and not pyside_incomplete:
            return True

        success = True

        installed_versions = getInstalledVersionSets(installed)
        for requirement in required:
            versions = installed_versions.get(normalizePackageName(requirement.name))
            if versions and (
                requirement.version in versions
                if requirement.version
                else bool(versions)
            ):
                self.updateStatus(requirement.name, 'Installed')
            else:
                self.updateStatus(requirement.name, 'Waiting')

        if unsatisfied:
            self.removeConflictingVersions(
                python_exe,
                unsatisfied,
                installed,
                required,
                env=env,
            )
            returncode = self.runFastPipInstall(
                python_exe,
                mirror_url,
                unsatisfied,
                install_args
                if install_args is not None
                else [getRequirementSpec(requirement) for requirement in unsatisfied],
                env=env,
            )
            if returncode == 0:
                for requirement in required:
                    self.updateStatus(requirement.name, 'Installed')
            else:
                success = False

        if pyside_incomplete:
            pyside_requirements = getPySideRequirements(required)
            for requirement in pyside_requirements:
                self.updateStatus(requirement.name, 'Uninstalling')
            self.runPipUninstall(
                python_exe,
                [requirement.name for requirement in pyside_requirements],
                env=env,
            )
            for requirement in pyside_requirements:
                self.updateStatus(requirement.name, 'Uninstalled')
            returncode = self.runFastPipInstall(
                python_exe,
                mirror_url,
                pyside_requirements,
                [
                    getRequirementSpec(requirement)
                    for requirement in pyside_requirements
                ],
                env=env,
            )
            if returncode == 0:
                for requirement in pyside_requirements:
                    self.updateStatus(requirement.name, 'Installed')
            else:
                success = False
        return success

    def removeConflictingVersions(
        self,
        python_exe: Path,
        unsatisfied: list[RequirementInfo],
        installed: list[RequirementInfo],
        required: list[RequirementInfo],
        env: dict[str, str] | None = None,
    ) -> None:
        """Drop other versions of a package so pip can install the pinned one.

        Pip treats an already installed newer version as "Requirement already
        satisfied", so without this the install silently does nothing and every
        launch repeats the same check and fails.
        """
        installed_versions = getInstalledVersionSets(installed)
        for requirement in unsatisfied:
            normalized = normalizePackageName(requirement.name)
            conflicts = getConflictingVersions(installed, requirement)
            if not conflicts:
                continue
            if requirement.version and requirement.version in installed_versions.get(
                normalized, set()
            ):
                continue
            names = [
                entry.name
                for entry in required + installed
                if normalizePackageName(entry.name) == normalized
            ]
            unique_names = list(dict.fromkeys(names))
            _logger.warning(
                'removing %s %s to install pinned %s',
                requirement.name,
                ', '.join(conflicts),
                requirement.version,
            )
            self.updateStatus(requirement.name, 'Removing Old Version')
            self.runPipUninstall(python_exe, unique_names, env=env)

    def _repairMissing(self, missing: list[RequirementInfo]) -> None:
        """Reinstall packages that are present but cannot import."""
        free_threaded = set(FREE_THREADED_REQUIREMENT_NAMES)
        for_ft = [
            requirement
            for requirement in missing
            if normalizePackageName(requirement.name) in free_threaded
        ]
        for_gil = [
            requirement
            for requirement in missing
            if normalizePackageName(requirement.name) not in free_threaded
        ]
        if for_gil:
            self.forceReinstallBroken(
                PYTHON_EXE,
                for_gil,
                getRuntimeWheelhouse(PYTHON_EXE),
                None,
                'installed but not importable',
            )
        if for_ft and FREE_THREADED_PYTHON_EXE.exists():
            self.forceReinstallBroken(
                FREE_THREADED_PYTHON_EXE,
                for_ft,
                getRuntimeWheelhouse(FREE_THREADED_PYTHON_EXE),
                getFreeThreadedEnv(),
                'installed but not importable (no-GIL runtime)',
            )

    def forceReinstallBroken(
        self,
        python_exe: Path,
        requirements: list[RequirementInfo],
        wheelhouse: Path | None,
        env: dict[str, str] | None,
        reason: str,
    ) -> bool:
        """Reinstall packages whose files are present but do not import.

        Pip reports a matching version as "already satisfied", so a wheel whose
        files were never fully written (a broken scipy install) stays broken
        forever unless it is reinstalled explicitly.
        """
        if not requirements:
            return True
        names = ', '.join(requirement.name for requirement in requirements)
        _logger.warning('reinstalling %s (%s)', names, reason)
        self.updateStatusText(self._text('reinstalling', packages=names))
        code = self.runPipUninstall(
            python_exe,
            [requirement.name for requirement in requirements],
            env=env,
        )
        if code != 0:
            _logger.warning('uninstall before reinstall returned %d', code)
        returncode = self.runFastPipInstall(
            python_exe,
            self._active_mirror_url or MIRRORS['PyPI'],
            requirements,
            [getRequirementSpec(requirement) for requirement in requirements],
            env=env,
        )
        if returncode != 0:
            _logger.warning('reinstall failed for %s', names)
            return False
        return True

    def getMissingRuntimeRequirements(self) -> list[RequirementInfo]:
        missing = getUnsatisfiedRequirements(
            getInstalledPackages(PYTHON_EXE), getRequirements()
        )
        if self._ft_runtime_ok:
            ft_required = getFreeThreadedRequirements()
            ft_missing = getUnsatisfiedRequirements(
                getInstalledPackages(
                    FREE_THREADED_PYTHON_EXE, env=getFreeThreadedEnv()
                ),
                ft_required,
            )
            ft_missing = mergeRequirements(
                ft_missing
                + getMissingImports(
                    FREE_THREADED_PYTHON_EXE,
                    ft_required,
                    FREE_THREADED_IMPORT_CHECKS,
                    env=getFreeThreadedEnv(),
                )
            )
            missing = mergeRequirements(missing + ft_missing)
        if not isFullPySideInstalled():
            missing = mergeRequirements(
                missing + getPySideRequirements(getRequirements())
            )
        return missing

    def emitInstallProgress(self, value: int, text: str) -> None:
        """Report a percentage of the *current phase*'s own progress."""
        self.progressChanged.emit(max(0, min(100, int(value))), text)

    def beginPhase(self, name: str) -> None:
        """Start a phase: the overall slice and the stage bar switch to it."""
        self.mwindow.setPhase(name)
        self.mwindow.mainBar().setValue(self.mwindow.overallPercent())

    def endPhase(self) -> None:
        self.mwindow.endPhase()
        self.mwindow.mainBar().setValue(self.mwindow.overallPercent())

    def startElapsedTicker(self, start: int = 0, end: int = 100) -> None:
        """Advance the stage bar and the elapsed label for a phase.

        The phase owns the status label for progress messages only; the ticker
        never writes it, so a package line cannot be overwritten every second.
        """
        self._heartbeat_stop = False
        self._heartbeat_last_second = -1
        self._heartbeat_started_at = time.perf_counter()
        self._invokeOnGui(self._startGuiHeartbeat, start, max(0, end - start), None)

    def stopElapsedTicker(self, final_text: str) -> None:
        self._heartbeat_stop = True
        try:
            self.emitInstallProgress(100, final_text)
            self._invokeOnGui(self._hideElapsedLabel)
        except RuntimeError:
            return

    def startDownloadWatch(
        self,
        callback: Callable[[], None],
        interval: float = WHEEL_WATCH_INTERVAL,
    ) -> tuple[threading.Event, threading.Thread]:
        stop_event = threading.Event()

        def watch() -> None:
            while not stop_event.wait(interval):
                try:
                    callback()
                except Exception as e:
                    _logger.debug('download progress watcher failed: %s', e)

        thread = threading.Thread(
            target=watch,
            daemon=True,
            name='southside-download-watch',
        )
        thread.start()
        return stop_event, thread

    def stopDownloadWatch(
        self,
        stop_event: threading.Event,
        thread: threading.Thread,
    ) -> None:
        stop_event.set()
        thread.join(timeout=1)

    def measureDirectory(self, directory: Path) -> tuple[int, int]:
        total = 0
        wheels = 0
        for path in directory.rglob('*'):
            try:
                if not path.is_file():
                    continue
                total += path.stat().st_size
            except OSError:
                continue
            if path.suffix == '.whl':
                wheels += 1
        return total, wheels

    def heartbeatElapsed(self) -> int:
        return int(time.perf_counter() - self._heartbeat_started_at)

    def _hideElapsedLabel(self) -> None:
        # Called on the GUI thread; the timer may already be gone if the tick
        # path cleaned up first.
        self._stopGuiHeartbeat()
        self.elapsed_label.clear()
        self.elapsed_label.hide()
        self.mwindow.applyWindowHeight()

    def _invokeOnGui(self, callback: object, *args: object) -> None:
        if QThread.currentThread() is self.thread():
            callback(*args)  # type: ignore
            return
        self.invokeRequested.emit((callback, args))

    def _runInvoke(self, payload: object) -> None:
        callback, args = payload  # type: ignore
        callback(*args)

    def _startGuiHeartbeat(
        self,
        start: int,
        span: int,
        text_factory: Callable[[int], str] | None,
    ) -> None:
        self._stopGuiHeartbeat()
        self.elapsed_label.show()
        self.mwindow.applyWindowHeight()
        self._gui_heartbeat = QTimer(self)
        self._gui_heartbeat.setTimerType(Qt.TimerType.PreciseTimer)
        self._gui_heartbeat.setInterval(HEARTBEAT_TICK_MS)
        self._gui_heartbeat.timeout.connect(
            lambda: self._heartbeatTick(start, span, text_factory)
        )
        self._gui_heartbeat.start()

    def _stopGuiHeartbeat(self) -> None:
        heartbeat = self._gui_heartbeat
        self._gui_heartbeat = None
        if heartbeat is None:
            return
        heartbeat.stop()
        heartbeat.deleteLater()

    def _heartbeatTick(
        self,
        start: int,
        span: int,
        text_factory: Callable[[int], str] | None,
    ) -> None:
        if self._heartbeat_stop:
            self._hideElapsedLabel()
            return
        elapsed = self.heartbeatElapsed()
        if span <= 0:
            value = start
        else:
            value = start + int(span * elapsed / (elapsed + 20))
        phase_reports = self.mwindow.phase in PHASES_WITH_REAL_PROGRESS
        if text_factory is None:
            if not phase_reports:
                # Keep the bar moving without touching the status label, which
                # belongs to the per-package download line.
                self.emitInstallProgress(value, self.status_label.text())
        elif elapsed != self._heartbeat_last_second and not phase_reports:
            # Keep the phase text at one update per second; the elapsed label
            # carries the finer ticks.
            self._heartbeat_last_second = elapsed
            self.emitInstallProgress(value, text_factory(elapsed))
        self.elapsedChanged.emit(self._elapsedText(elapsed))

    def _elapsedText(self, elapsed: int) -> str:
        if self._language == 'zh':
            return f'已用 {elapsed} 秒'
        return f'{elapsed}s elapsed'

    def reportStartupFailure(self, returncode: int) -> None:
        _logger.error('main.py exited with code %d', returncode)
        self._stopGuiHeartbeat()
        self.mwindow.mainBar().setValue(0)
        self.mwindow.hideSlots()
        self.status_label.setText(
            self._text('startup_failed', code=returncode)
        )
        self.elapsed_label.hide()
        self.show()
        self.mwindow.applyWindowHeight()

    def runFastPipInstall(
        self,
        python_exe: Path,
        mirror_url: str,
        download_requirements: list[RequirementInfo],
        install_args: list[str],
        env: dict[str, str] | None = None,
    ) -> int:
        ensurePipCacheDirs()
        if not download_requirements:
            return self.runPipInstall(python_exe, mirror_url, install_args, env=env)

        wheelhouse = getRuntimeWheelhouse(python_exe)
        wheelhouse.mkdir(parents=True, exist_ok=True)
        failed = self.downloadRequirementWheels(
            python_exe, mirror_url, download_requirements, wheelhouse, env=env
        )
        has_wheels = any(wheelhouse.glob('*.whl'))
        if failed:
            _logger.warning(
                'wheel predownload failed for: %s',
                ', '.join(getRequirementSpec(requirement) for requirement in failed),
            )
        returncode = self.runPipInstall(
            python_exe,
            mirror_url,
            install_args,
            wheelhouse=wheelhouse if has_wheels else None,
            no_index=has_wheels and not failed,
            env=env,
        )
        if returncode != 0 and has_wheels and not failed:
            _logger.warning('offline wheel install failed, retrying with index')
            return self.runPipInstall(
                python_exe,
                mirror_url,
                install_args,
                wheelhouse=wheelhouse,
                no_index=False,
                env=env,
            )
        return returncode

    def downloadRequirementWheels(
        self,
        python_exe: Path,
        mirror_url: str,
        requirements: list[RequirementInfo],
        wheelhouse: Path,
        env: dict[str, str] | None = None,
    ) -> list[RequirementInfo]:
        missing: list[RequirementInfo] = []
        for requirement in requirements:
            if findCachedWheel(requirement, wheelhouse) is not None:
                self.updateStatus(requirement.name, 'Cached')
                self.markDownloadComplete(requirement.name, False)
            else:
                missing.append(requirement)
        if not missing:
            return []

        if nativeDownloadEnabled():
            failed = self.downloadRequirementWheelsNative(
                python_exe,
                mirror_url,
                missing,
                wheelhouse,
                env=env,
            )
            if not failed:
                return []
            _logger.warning(
                'native download missed %s, falling back to pip',
                ', '.join(requirement.name for requirement in failed),
            )
            return self.downloadRequirementWheelsIndividually(
                python_exe,
                mirror_url,
                failed,
                wheelhouse,
                env=env,
            )

        failed = self.downloadRequirementWheelsBatch(
            python_exe,
            mirror_url,
            missing,
            wheelhouse,
            env=env,
        )
        if not failed:
            return []

        _logger.warning('batch wheel download failed, retrying package-by-package')
        return self.downloadRequirementWheelsIndividually(
            python_exe,
            mirror_url,
            missing,
            wheelhouse,
            env=env,
        )

    def downloadRequirementWheelsBatch(
        self,
        python_exe: Path,
        mirror_url: str,
        requirements: list[RequirementInfo],
        wheelhouse: Path,
        env: dict[str, str] | None = None,
    ) -> list[RequirementInfo]:
        if not requirements:
            return []

        wheelhouse.mkdir(parents=True, exist_ok=True)
        batch_lock = threading.Lock()
        batch_name = ['']
        batch_expected = [0]
        batch_files: dict[str, int] = {}
        self._download_total = max(self._download_total, len(requirements))

        def batch_progress() -> None:
            # Pip streams straight into the wheelhouse, so the bytes on disk are
            # the real download progress, whatever pip prints.
            downloaded, wheels = self.measureDirectory(wheelhouse)
            with batch_lock:
                name = batch_name[0]
                total = batch_expected[0]
            self._download_completed = max(self._download_completed, wheels)
            if not name or total <= 0:
                return
            self.reportDownloadProgress(name, downloaded, total)

        self.startElapsedTicker()
        stop_event, watcher = self.startDownloadWatch(batch_progress)
        command = [
            str(python_exe),
            '-m',
            'pip',
            'download',
            '--disable-pip-version-check',
            '--no-input',
            '--only-binary',
            ':all:',
            '--prefer-binary',
            '--retries',
            PIP_RETRIES,
            '--timeout',
            PIP_TIMEOUT,
            '--cache-dir',
            str(PIP_CACHE_DIR),
            '--index-url',
            mirror_url,
            '--dest',
            str(wheelhouse),
            *[getRequirementSpec(requirement) for requirement in requirements],
        ]
        returncode = 1
        try:
            popen = subprocess.Popen(
                command,
                cwd=str(SCRIPT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                env=getPipEnv(env),
            )
            if popen.stdout:
                for line in popen.stdout:
                    line_text = line.strip()
                    _logger.debug('[download batch] %s', line_text)
                    if line_text.startswith('Collecting '):
                        package = parsePipPackageName(line_text, 'Collecting')
                        if package is not None:
                            self.updateStatus(package, 'Resolving')
                    elif line_text.startswith('Using cached '):
                        package = parsePipPackageName(line_text, 'Using cached')
                        if package is not None:
                            self.updateStatus(package, 'Cached')
                    elif line_text.startswith('Downloading '):
                        package = parsePipPackageName(line_text, 'Downloading')
                        size = parsePipDownloadSize(line_text)
                        if package is not None:
                            with batch_lock:
                                batch_name[0] = package
                                if size is not None:
                                    # Pip repeats this line per progress tick, so
                                    # key by file: a repeat must not inflate the
                                    # total, a new file must add to it.
                                    file_name = line_text.split('Downloading ', 1)[
                                        1
                                    ].split(' (', 1)[0]
                                    batch_files[file_name] = size
                                    batch_expected[0] = sum(batch_files.values())
                            self.updateStatus(package, 'Downloading')
                    elif line_text.startswith('Saved '):
                        package = parseWheelPackageName(
                            line_text.split('Saved ', 1)[1]
                        )
                        with batch_lock:
                            batch_name[0] = ''
                        self.updateStatus(package, 'Cached')
            popen.wait()
            returncode = popen.returncode
        except OSError as e:
            _logger.exception(e)
        finally:
            self.stopDownloadWatch(stop_event, watcher)
            self.stopElapsedTicker(self._text('batch_download'))

        if returncode != 0:
            for requirement in requirements:
                self.updateStatus(requirement.name, 'Download Failed')
            return requirements

        for requirement in requirements:
            self.markDownloadComplete(requirement.name, True)
        return []
    def metadataWheelPath(self, wheel: WheelFile) -> Path:
        """Where a wheel downloaded for its metadata is kept for reuse."""
        return stagedWheelPath(wheel)

    def collectWheelClosure(
        self,
        index: WheelIndex,
        requirements: list[RequirementInfo],
        wheelhouse: Path,
    ) -> tuple[list[tuple[RequirementInfo, WheelFile]], list[RequirementInfo]]:
        """Resolve the requirements plus their transitive dependencies.

        Pip can only install offline when every wheel in the graph is in the
        wheelhouse; missing a single dependency like anyio makes pip fall back
        to the index and re-download packages that are already here.
        """
        resolved: list[tuple[RequirementInfo, WheelFile]] = []
        failed: list[RequirementInfo] = []
        # "seen" means already processed; dependencies are queued before that.
        seen: set[str] = set()
        queued = {normalizePackageName(item.name) for item in requirements}
        # Index pages for large projects run to megabytes, so resolve every
        # level of the dependency graph concurrently instead of one at a time.
        level = list(requirements)
        while level:
            batch: list[RequirementInfo] = []
            for requirement in level:
                package = normalizePackageName(requirement.name)
                if package in seen or len(seen) >= MAX_CLOSURE_PACKAGES:
                    continue
                seen.add(package)
                batch.append(requirement)
            if not batch:
                break
            self.mwindow.reportParallel(0, len(batch))
            self.updateStatusText(
                self._text('resolving_wheels', count=len(batch))
            )
            outcomes = self.resolveBatch(index, batch, wheelhouse)
            done = 0
            next_level: list[RequirementInfo] = []
            for requirement in batch:
                wheel = outcomes.get(requirement.name)
                if wheel is None:
                    _logger.warning('no wheel found for %s', requirement.name)
                    failed.append(requirement)
                    done += 1
                    self.mwindow.reportParallel(done, len(batch))
                    continue
                resolved.append((requirement, wheel))
                queued.add(normalizePackageName(requirement.name))
                done += 1
                self.mwindow.reportParallel(done, len(batch))
                for dependency_wheel in self.dependencyWheels(
                    index,
                    wheel,
                    seen,
                    queued,
                ):
                    resolved.append(dependency_wheel)
                    next_level.append(dependency_wheel[0])
            level = next_level
        # A package can be offered by several parents; keep the first wheel only.
        unique: dict[str, tuple[RequirementInfo, WheelFile]] = {}
        for requirement, wheel in resolved:
            key = normalizePackageName(requirement.name)
            unique.setdefault(key, (requirement, wheel))
        final = list(unique.values())
        _logger.info(
            'resolved %d wheels for %d requested packages',
            len(final),
            len(requirements),
        )
        return final, failed

    def resolveBatch(
        self,
        index: WheelIndex,
        batch: list[RequirementInfo],
        wheelhouse: Path,
    ) -> dict[str, WheelFile | None]:
        """Resolve one level of the dependency graph concurrently."""
        outcomes: dict[str, WheelFile | None] = {}
        pending: list[RequirementInfo] = []
        for requirement in batch:
            cached = findCachedWheel(requirement, wheelhouse)
            if cached is not None and index.matchesTags(cached.name):
                self.showCachedWheel(requirement.name)
                self.markDownloadComplete(requirement.name, False)
                outcomes[requirement.name] = WheelFile(
                    filename=cached.name,
                    url='',
                    sha256='',
                    version=_wheelVersion(cached.name),
                    requires_python='',
                    size=cached.stat().st_size,
                    path=cached,
                )
            else:
                pending.append(requirement)
        if not pending:
            return outcomes
        with ThreadPoolExecutor(
            max_workers=min(MAX_PARALLEL_RESOLVE, len(pending))
        ) as executor:
            future_map = {
                executor.submit(
                    self.resolveForDownload,
                    index,
                    requirement,
                ): requirement
                for requirement in pending
            }
            for future in as_completed(future_map):
                requirement = future_map[future]
                try:
                    outcomes[requirement.name] = future.result()
                except Exception as e:
                    _logger.debug(
                        'native resolve failed for %s: %s',
                        requirement.name,
                        e,
                    )
                    outcomes[requirement.name] = None
        return outcomes

    def resolveForDownload(
        self,
        index: WheelIndex,
        requirement: RequirementInfo,
    ) -> WheelFile | None:
        if requirement.version:
            return index.resolve(requirement)
        return index.resolveBest(requirement)

    def dependencyWheels(
        self,
        index: WheelIndex,
        wheel: WheelFile,
        seen: set[str],
        queued: set[str],
    ) -> list[tuple[RequirementInfo, WheelFile]]:
        if wheel.path is not None:
            # Already in the wheelhouse; its dependencies were handled before.
            return []
        metadata = index.wheelMetadata(wheel)
        if metadata is None:
            return []
        found: list[tuple[RequirementInfo, WheelFile]] = []
        for raw in parseWheelRequirements(metadata):
            if not appliesToThisEnvironment(raw):
                continue
            name = requirementNames(raw)
            package = normalizePackageName(name) if name else ''
            if not package or package in seen or package in queued:
                continue
            if package in _STDLIB_NAMES:
                continue
            requirement = RequirementInfo(name, '', requirementSpecifier(raw))
            dependency = index.resolveBest(requirement)
            if dependency is None:
                _logger.debug(
                    'dependency %s of %s not resolvable',
                    name,
                    wheel.filename,
                )
                continue
            queued.add(package)
            found.append((requirement, dependency))
        return found

    def downloadRequirementWheelsNative(
        self,
        python_exe: Path,
        mirror_url: str,
        requirements: list[RequirementInfo],
        wheelhouse: Path,
        env: dict[str, str] | None = None,
    ) -> list[RequirementInfo]:
        """Fetch wheels straight from the mirror's simple index.

        Returns the requirements that could not be fetched, so the caller can
        fall back to pip for those.
        """
        wheelhouse.mkdir(parents=True, exist_ok=True)
        index = WheelIndex(mirror_url, python_exe, env=env)
        self.updateStatusText(
            self._text('resolving_wheels', count=len(requirements))
        )
        self.beginPhase('resolve')
        self.startElapsedTicker()
        warmArtifactHost()

        resolved, failed = self.collectWheelClosure(index, requirements, wheelhouse)
        self.endPhase()
        self.beginPhase('download')
        to_download = [
            (requirement, wheel)
            for requirement, wheel in resolved
            if wheel.url
        ]
        expected_sizes = {
            requirement.name: wheel.size
            for requirement, wheel in to_download
            if wheel.size > 0
        }
        if to_download:
            # Dependencies count too, so the progress line shows how much is left.
            self.updateStatusText(
                self._text('downloading_wheels', count=len(to_download))
            )

        total_download = len(to_download)
        if total_download:
            self._download_total = max(self._download_total, total_download)
        progress_lock = threading.Lock()
        current: dict[str, int] = {
            requirement.name: 0 for requirement, _wheel in to_download
        }
        expected: dict[str, int] = dict(expected_sizes)
        active: dict[str, bool] = {}
        errors: dict[str, str] = {}
        done_flag = threading.Event()
        self._download_completed = 0

        def report() -> None:
            while not done_flag.wait(WHEEL_WATCH_INTERVAL):
                with progress_lock:
                    downloading = [name for name in current if active.get(name)]
                for name in downloading[:SLOT_POOL_SIZE]:
                    self.reportDownloadProgress(
                        name,
                        current[name],
                        expected.get(name, 0),
                    )

        report_thread = threading.Thread(
            target=report,
            daemon=True,
            name='southside-native-progress',
        )
        report_thread.start()

        def fetch(requirement: RequirementInfo, wheel: WheelFile) -> bool:
            target = wheelhouse / wheel.filename
            try:
                staged = self.metadataWheelPath(wheel)
                with progress_lock:
                    size = staged.stat().st_size if staged.exists() else 0
                    current[requirement.name] = size
                    active[requirement.name] = True
                if staged.exists() and verifyWheelHash(staged, wheel.sha256):
                    # Already fetched while reading its metadata; move it in
                    # instead of keeping two copies.
                    target.unlink(missing_ok=True)
                    shutil.move(str(staged), str(target))
                    with progress_lock:
                        current[requirement.name] = target.stat().st_size
                        active[requirement.name] = False
                    self.markDownloadComplete(requirement.name, True)
                    return True
                self.updateStatus(requirement.name, 'Downloading')
                # Show the line immediately, before the first bytes land.
                self.reportDownloadProgress(
                    requirement.name,
                    0,
                    expected.get(requirement.name, 0),
                )
                downloadWheelTo(
                    wheel.url,
                    target,
                    requirement.name,
                    progress_lock,
                    current,
                )
                if not verifyWheelHash(target, wheel.sha256):
                    errors[requirement.name] = 'checksum mismatch'
                    target.unlink(missing_ok=True)
                    return False
                with progress_lock:
                    current[requirement.name] = target.stat().st_size
                    active[requirement.name] = False
                self.markDownloadComplete(requirement.name, True)
                return True
            except Exception as e:
                _logger.warning('download failed for %s: %s', requirement.name, e)
                _logger.debug('download traceback', exc_info=True)
                errors[requirement.name] = str(e)
                target.unlink(missing_ok=True)
                return False
            finally:
                with progress_lock:
                    active[requirement.name] = False

        workers = min(NATIVE_DOWNLOAD_WORKERS, total_download) or 1
        downloaded_ok = 0
        # One bar per wheel, capped by the slot pool.
        for requirement, _wheel in to_download[:SLOT_POOL_SIZE]:
            self.mwindow.acquireSlot(requirement.name)
        try:
            if to_download:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    future_map = {
                        executor.submit(fetch, requirement, wheel): requirement
                        for requirement, wheel in to_download
                    }
                    for future in as_completed(future_map):
                        requirement = future_map[future]
                        try:
                            if future.result():
                                downloaded_ok += 1
                            else:
                                failed.append(requirement)
                        except Exception as e:
                            _logger.exception(e)
                            failed.append(requirement)
            for name in list(errors):
                self.updateStatus(name, 'Download Failed')
        finally:
            self.mwindow.hideSlots()
            done_flag.set()
            report_thread.join(timeout=1)
            self.stopElapsedTicker(self._text('download_stage'))
        _logger.info(
            'native download: %d ok, %d failed, %d already cached',
            downloaded_ok,
            len(failed),
            len(resolved) - len(to_download),
        )
        return failed

    def downloadRequirementWheelsIndividually(
        self,
        python_exe: Path,
        mirror_url: str,
        requirements: list[RequirementInfo],
        wheelhouse: Path,
        env: dict[str, str] | None = None,
    ) -> list[RequirementInfo]:
        workers = min(MAX_WHEEL_DOWNLOAD_WORKERS, len(requirements))
        self.beginPhase('download')
        self.startElapsedTicker()
        wheelhouse.mkdir(parents=True, exist_ok=True)
        copy_lock = threading.Lock()

        def download(requirement: RequirementInfo) -> bool:
            if findCachedWheel(requirement, wheelhouse) is not None:
                self.showCachedWheel(requirement.name)
                self.markDownloadComplete(requirement.name, False)
                return True

            self.updateStatus(requirement.name, 'Downloading')
            spec = getRequirementSpec(requirement)
            with tempfile.TemporaryDirectory(prefix='southside-wheel-') as temp_dir:
                temp_path = Path(temp_dir)
                progress_stop = threading.Event()
                progress_lock = threading.Lock()
                expected_bytes = 0
                last_percent = -1
                last_downloaded = 0

                def tempDownloadSize() -> int:
                    total = 0
                    for path in temp_path.rglob('*'):
                        try:
                            if path.is_file():
                                total += path.stat().st_size
                        except OSError:
                            continue
                    return total

                def updateProgress(force: bool = False) -> None:
                    nonlocal last_percent, last_downloaded
                    with progress_lock:
                        total = expected_bytes
                    downloaded = tempDownloadSize()
                    if total > 0:
                        percent = min(99, max(0, int(downloaded * 100 / total)))
                    else:
                        percent = 0
                    if not force and percent == last_percent and (
                        downloaded == last_downloaded or downloaded <= 0
                    ):
                        return
                    last_percent = percent
                    last_downloaded = downloaded
                    self.reportDownloadProgress(
                        requirement.name,
                        downloaded,
                        total,
                    )

                def watchProgress() -> None:
                    while not progress_stop.wait(WHEEL_WATCH_INTERVAL):
                        updateProgress()

                progress_thread = threading.Thread(
                    target=watchProgress,
                    daemon=True,
                    name=f'southside-wheel-progress-{requirement.name}',
                )
                progress_thread.start()
                popen = subprocess.Popen(
                    [
                        str(python_exe),
                        '-m',
                        'pip',
                        'download',
                        '--disable-pip-version-check',
                        '--no-input',
                        '--only-binary',
                        ':all:',
                        '--no-deps',
                        '--prefer-binary',
                        '--retries',
                        PIP_RETRIES,
                        '--timeout',
                        PIP_TIMEOUT,
                        '--cache-dir',
                        str(PIP_CACHE_DIR),
                        '--index-url',
                        mirror_url,
                        '--dest',
                        str(temp_path),
                        spec,
                    ],
                    cwd=str(SCRIPT_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                    env=getPipEnv(env),
                )
                try:
                    if popen.stdout:
                        for line in popen.stdout:
                            line_text = line.strip()
                            _logger.debug('[download %s] %s', spec, line_text)
                            if 'Downloading' in line_text:
                                size = parsePipDownloadSize(line_text)
                                if size is not None:
                                    with progress_lock:
                                        # Pip repeats the size line on every
                                        # progress tick; sum only new files.
                                        expected_bytes = max(expected_bytes, size)
                                updateProgress(force=True)
                            live = parsePipProgress(line_text)
                            if live is not None:
                                with progress_lock:
                                    expected_bytes = max(expected_bytes, live[1])
                                updateProgress(force=True)
                    popen.wait()
                    if popen.returncode != 0:
                        self.updateStatus(requirement.name, 'Download Failed')
                        return False
                finally:
                    progress_stop.set()
                    progress_thread.join(timeout=0.5)

                copied = 0
                with copy_lock:
                    for wheel in temp_path.glob('*.whl'):
                        target = wheelhouse / wheel.name
                        if not target.exists():
                            shutil.copy2(wheel, target)
                        copied += 1
                self.markDownloadComplete(requirement.name, copied > 0)
                return True

        failed = []
        try:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                future_map = {
                    executor.submit(download, requirement): requirement
                    for requirement in requirements
                }
                for future in as_completed(future_map):
                    requirement = future_map[future]
                    try:
                        if not future.result():
                            failed.append(requirement)
                    except Exception as e:
                        _logger.exception(e)
                        self.updateStatus(requirement.name, 'Download Failed')
                        failed.append(requirement)
        finally:
            self.stopElapsedTicker(
                self._text('downloading_wheels', count=len(requirements))
            )
        return failed

    def runPipInstall(
        self,
        python_exe: Path,
        mirror_url: str,
        args: list[str],
        wheelhouse: Path | None = None,
        no_index: bool = False,
        env: dict[str, str] | None = None,
    ) -> int:
        ensurePipCacheDirs()
        text_key = 'offline_install' if no_index else 'online_install'
        self.beginPhase('install')
        self.startElapsedTicker()
        speed_stop, speed_thread = self.startDownloadWatch(
            self.updateDownloadSpeed,
            0.5,
        )
        command = [
            str(python_exe),
            '-m',
            'pip',
            'install',
            '--disable-pip-version-check',
            '--no-input',
            '--no-compile',
            '--only-binary',
            ':all:',
            '--prefer-binary',
            '--retries',
            PIP_RETRIES,
            '--timeout',
            PIP_TIMEOUT,
            '--cache-dir',
            str(PIP_CACHE_DIR),
        ]
        if no_index:
            command.append('--no-index')
        else:
            command.extend(['--index-url', mirror_url])
        if wheelhouse is not None:
            command.extend(['--find-links', str(wheelhouse)])
        command.extend(args)
        returncode = 1
        try:
            popen = subprocess.Popen(
                command,
                cwd=str(SCRIPT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=getPipEnv(env),
            )
            if popen.stdout:
                installing: list[str] = []
                shown = 0
                for line in popen.stdout:
                    c = line.strip()
                    _logger.debug(c)
                    if 'Collecting' in c:
                        package = parsePipPackageName(c, 'Collecting')
                        if package is not None:
                            self.updateStatus(package, 'Collecting')
                    elif 'Downloading' in c:
                        package = parsePipPackageName(c, 'Downloading')
                        if package is not None:
                            self.updateStatus(package, 'Downloading')
                    elif 'Using cached' in c:
                        package = parsePipPackageName(c, 'Using cached')
                        if package is not None:
                            self.updateStatus(package, 'Cached')
                    elif 'Installing collected packages:' in c:
                        packages_str = c.split('Installing collected packages:')[1]
                        installing = [
                            item.strip()
                            for item in packages_str.split(',')
                            if item.strip()
                        ]
                        self.updateStatusText(
                            self._text('installing_package', package=installing[0])
                            if installing
                            else self._text('installing')
                        )
                    elif 'Successfully installed' in c:
                        packages_str = c.split('Successfully installed')[1]
                        matches = re.findall(r'([a-zA-Z0-9_\-]+)-[\d\.]+', packages_str)
                        for package in matches:
                            self.updateStatus(package, 'Installed')
                    elif installing:
                        # pip prints one "Installing ..." / "Building ..." line per
                        # package; that is the package being written right now.
                        for keyword, state in (
                            ('Installing', 'Installing'),
                            ('Building', 'Building'),
                            ('Uninstalling', 'Uninstalling'),
                        ):
                            if c.startswith(keyword):
                                shown = min(shown + 1, len(installing))
                                self.updateStatusText(
                                    self._text(
                                        'installing_package',
                                        package=installing[shown - 1],
                                    )
                                )
                                break
            popen.wait()
            returncode = popen.returncode
        except OSError as e:
            _logger.exception(e)
        finally:
            self.stopDownloadWatch(speed_stop, speed_thread)
            self.stopElapsedTicker(self._text(text_key, count=len(args)))

        if returncode == 0:
            self.emitInstallProgress(
                99,
                self._text('checking_install'),
            )
        return returncode

    def runPipUninstall(
        self,
        python_exe: Path,
        package_names: list[str],
        env: dict[str, str] | None = None,
    ) -> int:
        ensurePipCacheDirs()
        popen = subprocess.Popen(
            [
                str(python_exe),
                '-m',
                'pip',
                'uninstall',
                '--disable-pip-version-check',
                '-y',
                *package_names,
            ],
            cwd=str(SCRIPT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=getPipEnv(env),
        )
        if popen.stdout:
            for line in popen.stdout:
                _logger.debug(line.strip())
        popen.wait()
        return popen.returncode

    def updateStatusText(self, text: str) -> None:
        def apply() -> None:
            self.status_label.setText(text)
            # A longer status line wraps and needs one more row of height.
            self.mwindow.applyWindowHeight()

        self.task.emit(apply)

    def updateProgressUi(self, value: int, text: str) -> None:
        """Apply a phase percentage to the overall and stage bars.

        The overall bar is derived from the phase slices, so it can never move
        backwards no matter how the phases report their own progress.
        """
        value = max(0, min(100, value))
        manager = self.mwindow
        manager.setPhaseProgress(manager.phase, float(value))
        manager.mainBar().setValue(manager.overallPercent())
        self.status_label.setText(text)
        manager.applyWindowHeight()

    def reportDownloadProgress(
        self,
        package_name: str,
        downloaded_bytes: int,
        total_bytes: int,
    ) -> None:
        """Update one package's download line, measuring its speed as it goes."""
        normalized = normalizePackageName(package_name)
        with self._download_progress_lock:
            previous = self._download_active.get(normalized)
        if (
            previous is not None
            and downloaded_bytes <= 0
            and previous[1] <= 0
            and previous[2] > 0
        ):
            # Already showing this package waiting for its first byte; do not
            # re-emit the same line.
            return
        if total_bytes > 0:
            percent = min(99, max(0, int(downloaded_bytes * 100 / total_bytes)))
        else:
            percent = 0
        speed = self._measureDownloadSpeed(normalized, downloaded_bytes)
        self.updateDownloadProgress(
            package_name,
            percent,
            downloaded_bytes,
            total_bytes,
            speed,
        )

    def updateDownloadProgress(
        self,
        package_name: str,
        package_percent: int,
        downloaded_bytes: int,
        total_bytes: int = 0,
        bytes_per_second: float = 0.0,
    ) -> None:
        if self._download_total <= 0:
            return
        normalized = normalizePackageName(package_name)
        percent = package_percent
        if total_bytes <= 0:
            # Pip did not announce a size; bytes still count, the ratio does not.
            percent = 0
            total_bytes = max(total_bytes, downloaded_bytes)
        now = time.perf_counter()
        with self._download_progress_lock:
            previous_percent = self._download_progress.get(normalized)
            state = self._download_speed.get(normalized)
            # New bytes are worth showing; an unchanged readout is throttled.
            progressed = state is None or downloaded_bytes != state.last_bytes
            throttled = (
                not progressed
                and previous_percent == percent
                and state is not None
                and now - state.last_emit < DOWNLOAD_EMIT_INTERVAL
            )
            if state is not None:
                state.last_emit = now
                state.last_bytes = downloaded_bytes
            self._download_progress[normalized] = percent
            self._download_active[normalized] = (
                package_name,
                downloaded_bytes,
                total_bytes,
                bytes_per_second,
            )
            headline = self._pickHeadlineLocked(normalized)
            done = self._download_completed
            total = self._download_total
            entry = self._download_active[headline]
            total_percent = sum(self._download_progress.values()) / (100 * total)
        if headline == normalized and throttled:
            return
        self.mwindow.updateSlot(
            package_name,
            percent,
            self._downloadText(headline, entry, done, total),
        )
        self.emitInstallProgress(
            total_percent * 100.0,
            self._downloadText(headline, entry, done, total),
        )
    def _pickHeadlineLocked(self, updated: str) -> str:
        """Keep one package on the label until it finishes."""
        if not self._download_active:
            return updated
        current = self._download_headline
        if current in self._download_active:
            return current
        # Nothing shown yet, or the shown package finished: take the least
        # advanced one that is left.
        headline = min(
            self._download_active,
            key=lambda name: (
                self._download_progress.get(name, 0),
                -self._download_active[name][1],
            ),
        )
        self._download_headline = headline
        return headline

    def showCachedWheel(self, package_name: str) -> None:
        if self._download_total <= 0:
            return
        self.emitInstallProgress(
            self._download_completed * 100.0 / max(1, self._download_total),
            self._text(
                'cached',
                package=package_name,
                current=min(self._download_total, self._download_completed + 1),
                total=self._download_total,
            ),
        )

    def _downloadText(
        self,
        normalized: str,
        entry: tuple[str, int, int, float],
        done: int,
        total: int,
    ) -> str:
        package_name = entry[0]
        _, downloaded_bytes, total_bytes, speed = entry
        with self._download_progress_lock:
            percent = self._download_progress.get(normalized, 0)
        # Packages that already finished, plus this one.
        current = min(total, done + 1)
        if total_bytes > 0:
            ratio = f'{formatBytes(downloaded_bytes)}/{formatBytes(total_bytes)}'
        else:
            ratio = f'{formatBytes(downloaded_bytes)}/?'
        return self._text(
            'downloading',
            package=package_name,
            percent=percent,
            done=ratio,
            speed=formatSpeed(speed) if speed > 0 else '...',
            current=current,
            total=total,
        )

    def updateDownloadSpeed(self) -> None:
        with self._download_progress_lock:
            active = list(self._download_active.values())
        for package_name, downloaded_bytes, total_bytes, _ in active:
            if total_bytes <= 0:
                continue
            percent = min(99, max(0, int(downloaded_bytes * 100 / total_bytes)))
            self.updateDownloadProgress(
                package_name,
                percent,
                downloaded_bytes,
                total_bytes,
                self._measureDownloadSpeed(
                    normalizePackageName(package_name), downloaded_bytes
                ),
            )

    def _measureDownloadSpeed(self, normalized: str, downloaded_bytes: int) -> float:
        now = time.perf_counter()
        with self._download_progress_lock:
            state = self._download_speed.get(normalized)
            if state is None:
                self._download_speed[normalized] = _DownloadSpeedState(
                    downloaded_bytes, now, 0.0
                )
                return 0.0
            state.samples.append((now, downloaded_bytes))
            while state.samples and now - state.samples[0][0] > 1.0:
                state.samples.pop(0)
            baseline_time, baseline_bytes = state.samples[0]
            elapsed = now - baseline_time
            moved = downloaded_bytes - baseline_bytes
            if elapsed >= 0.3 and moved > 0:
                state.bytes_per_second = moved / elapsed
            return state.bytes_per_second

    def markDownloadComplete(self, package_name: str, downloaded: bool) -> None:
        normalized = normalizePackageName(package_name)
        with self._download_progress_lock:
            already_complete = self._download_progress.get(normalized, 0) >= 100
            self._download_progress[normalized] = 100
            self._download_active.pop(normalized, None)
            self._download_speed.pop(normalized, None)
            if self._download_headline == normalized:
                self._download_headline = ''
        if already_complete:
            return
        self._download_completed = min(
            self._download_total, self._download_completed + 1
        )
        text_key = 'downloaded' if downloaded else 'cached'
        self.mwindow.releaseSlot(package_name)
        self.emitInstallProgress(
            self._download_completed * 100.0 / max(1, self._download_total),
            self._text(
                text_key,
                package=package_name,
                current=self._download_completed,
                total=self._download_total,
            ),
        )

    def updateStatus(self, package_name: str, status: str) -> None:
        self.updateStatusText(f'{package_name}: {status}')

    def testLatency(self, mirror_name: str, mirror_url: str):
        _logger.debug('testing latency of %s: %s', mirror_name, mirror_url)
        request = Request(mirror_url)
        start_time = time.perf_counter()
        try:
            with urlopen(request, timeout=MIRROR_LATENCY_TIMEOUT) as response:
                if response.status != 200:
                    return
                end_time = time.perf_counter()
                latency = end_time - start_time
                with self.latency_lock:
                    if not self.latency_testing:
                        return
                    self.latency_testing = False
                self.latencyFinished.emit(mirror_name, mirror_url, latency)
        except Exception:
            return

    def finishLatencyFallback(self) -> None:
        deadline = time.perf_counter() + MIRROR_LATENCY_TIMEOUT + 1
        for thread in self.latency_test_threads:
            remaining = max(0, deadline - time.perf_counter())
            thread.join(timeout=remaining)
        with self.latency_lock:
            if not self.latency_testing:
                return
            self.latency_testing = False
        self.latencyFinished.emit(
            'PyPI', MIRRORS['PyPI'], float(MIRROR_LATENCY_TIMEOUT)
        )

    def startTestLatency(self) -> None:
        if self._check_started:
            return
        self._check_started = True
        self.beginPhase('audit')
        self.startElapsedTicker()
        threading.Thread(
            target=self._startTestLatency,
            daemon=True,
            name='southside-bootstrap-check',
        ).start()

    def _startTestLatency(self) -> None:
        self.progressChanged.emit(
            5,
            self._text('checking_runtime', runtime='GIL Python'),
        )
        self.mwindow.reportParallel(0, 2)
        installed = getInstalledPackages(PYTHON_EXE)
        required = getRequirements()
        unsatisfied = getUnsatisfiedRequirements(installed, required)
        pyside_incomplete = not isFullPySideInstalled()
        self._gil_installed = installed
        self._gil_unsatisfied = unsatisfied
        self._gil_pyside_incomplete = pyside_incomplete
        self.mwindow.reportParallel(1, 2)

        ft_unsatisfied: list[RequirementInfo] = []
        if FREE_THREADED_PYTHON_EXE.exists():
            self.progressChanged.emit(
                50,
                self._text('checking_runtime', runtime='no-GIL Python'),
            )
            if isFreeThreadedPython(FREE_THREADED_PYTHON_EXE):
                self._ft_runtime_ok = True
                ft_installed = getInstalledPackages(
                    FREE_THREADED_PYTHON_EXE,
                    env=getFreeThreadedEnv(),
                )
                ft_required = getFreeThreadedRequirements()
                ft_unsatisfied = getUnsatisfiedRequirements(ft_installed, ft_required)
                self.progressChanged.emit(
                    80,
                    self._text('checking_imports', runtime='no-GIL Python'),
                )
                ft_unsatisfied = mergeRequirements(
                    ft_unsatisfied
                    + getMissingImports(
                        FREE_THREADED_PYTHON_EXE,
                        ft_required,
                        FREE_THREADED_IMPORT_CHECKS,
                        env=getFreeThreadedEnv(),
                    )
                )
                self._ft_installed = ft_installed
                self._ft_unsatisfied = ft_unsatisfied
                _logger.info(
                    '%d no-GIL packages installed, %d required',
                    len(ft_installed),
                    len(ft_required),
                )
            else:
                self._ft_runtime_ok = False
                _logger.warning(
                    'free-threaded Python failed validation: %s',
                    FREE_THREADED_PYTHON_EXE,
                )
        else:
            self._ft_runtime_ok = False
            _logger.warning(
                'free-threaded Python not found: %s', FREE_THREADED_PYTHON_EXE
            )

        _logger.info(f'{len(installed)} installed, {len(required)} required')
        if not unsatisfied and not pyside_incomplete and not ft_unsatisfied:
            _logger.info('all requirements satisfied')
            self.endPhase()
            self.beginPhase('start')
            self.allDone.emit()
            return
        self.endPhase()
        _logger.info(f'{len(unsatisfied)} requirements need install/update')
        if ft_unsatisfied:
            _logger.info(
                '%d no-GIL requirements need install/update', len(ft_unsatisfied)
            )
        if pyside_incomplete:
            _logger.info('PySide6 needs install overwrite to restore full files')

        pyside_download_count = (
            len(getPySideRequirements(required)) if pyside_incomplete else 0
        )
        self._download_total = (
            len(unsatisfied) + len(ft_unsatisfied) + pyside_download_count
        )
        self._download_completed = 0
        self._download_progress.clear()
        self._download_active.clear()
        self._download_speed.clear()

        for mirror_name, mirror_url in MIRRORS.items():
            thread = threading.Thread(
                target=self.testLatency, args=(mirror_name, mirror_url)
            )
            thread.daemon = True
            self.latency_test_threads.append(thread)

        self.latency_testing = True
        for thread in self.latency_test_threads:
            thread.start()
        threading.Thread(target=self.finishLatencyFallback, daemon=True).start()


if __name__ == '__main__':
    app = QApplication([])
    bwindow = BootstrapWindow()
    bwindow.allDone.connect(runMain)
    QTimer.singleShot(0, bwindow.startTestLatency)
    app.exec()
