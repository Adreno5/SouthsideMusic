import os
import sys
import shutil
import atexit
import signal
import tempfile
import subprocess
import importlib
import time
import hashlib
import threading
from typing import Any


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------
class SetupError(Exception):
    """Non-recoverable error raised by helper functions.

    Caught by main() and translated to sys.exit(1) so that helpers remain
    composable — callers other than main() can handle the error instead of
    having the process killed unconditionally.
    """


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_ZIP = os.path.join(SCRIPT_DIR, 'python.zip')
EMBED_DIR = os.path.join(SCRIPT_DIR, 'embed_python')
BUILD_VENV = os.path.join(SCRIPT_DIR, 'build_venv')
GET_PIP = os.path.join(SCRIPT_DIR, 'get-pip.py')
REQUIREMENTS = os.path.join(SCRIPT_DIR, 'requirements.txt')
REQUIREMENTS_HASH = os.path.join(SCRIPT_DIR, '.requirements_sha256')

PYTHON_VERSION = '3.12.7'
DOWNLOAD_BASE = 'https://www.python.org/ftp/python/3.12.7'

DEFAULT_TIMEOUT = 600  # seconds for subprocess calls
DOWNLOAD_CHUNK_SIZE = 8192
MIN_FREE_DISK_MB = 500

EMBED_ZIP_SHA256: dict[str, str] = {
    'amd64': '0d57bb6cb078b74d23dbfe91f77d6780d45bed328911609f1f7ee2ba1606bf44',
    'win32': 'c2f7e103a1cb73e90b3cd4fa72f9b12891116ec2d8feaefc4b8e76be1fdc0d98',
}

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/125.0.0.0 Safari/537.36'
)

INNO_SETUP_RELEASES_API = (
    'https://api.github.com/repos/jrsoftware/issrc/releases/latest'
)
INNO_SETUP_INSTALL_ARGS = ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-']

# Files / dirs to clean up on abnormal exit
_temp_files: list[str] = []
_cleanup_lock = threading.Lock()


def _register_cleanup(path: str) -> None:
    _temp_files.append(path)


def _unregister_cleanup(path: str) -> None:
    """Safely remove *path* from the cleanup list if present."""
    try:
        _temp_files.remove(path)
    except ValueError:
        pass


def _cleanup() -> None:
    if not _cleanup_lock.acquire(blocking=False):
        return
    try:
        for f in _temp_files:
            try:
                if os.path.isdir(f):
                    shutil.rmtree(f, ignore_errors=True)
                elif os.path.isfile(f):
                    os.remove(f)
            except OSError:
                pass
    finally:
        _cleanup_lock.release()


atexit.register(_cleanup)


def _signal_handler(signum, frame):
    os.write(sys.stderr.fileno(), b'\nInterrupted. Cleaning up...\n')
    _cleanup()
    os._exit(1)


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)
if hasattr(signal, 'SIGBREAK'):
    signal.signal(signal.SIGBREAK, _signal_handler)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def run(
    cmd: list[str],
    *,
    check: bool = True,
    timeout: int = DEFAULT_TIMEOUT,
    **kwargs,
) -> subprocess.CompletedProcess[str]:
    """Wrapper around subprocess.run with consistent error reporting and timeout.

    stderr is always captured (piped) so it can be surfaced on failure.
    """
    print(f'  > {" ".join(cmd)}')
    if 'capture_output' not in kwargs and 'stderr' not in kwargs:
        kwargs['stderr'] = subprocess.PIPE
    try:
        return subprocess.run(cmd, check=check, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired:
        print(f'  [ERROR] Command timed out after {timeout}s: {" ".join(cmd)}')
        raise
    except subprocess.CalledProcessError as e:
        if hasattr(e, 'stderr') and e.stderr:
            stderr_text = e.stderr.decode('utf-8', errors='replace').strip()
            if stderr_text:
                print(f'  [ERROR] stderr: {stderr_text}')
        if hasattr(e, 'stdout') and e.stdout:
            stdout_text = e.stdout.decode('utf-8', errors='replace').strip()
            if stdout_text:
                print(f'  [ERROR] stdout: {stdout_text}')
        raise


def pip_install(package: str, *, retries: int = 3) -> bool:
    """Attempt to pip-install *package* with retry. Returns True on success."""
    for attempt in range(1, retries + 1):
        try:
            run(
                [
                    sys.executable,
                    '-m',
                    'pip',
                    'install',
                    '--no-cache-dir',
                    package,
                ]
            )
            return True
        except subprocess.CalledProcessError:
            if attempt < retries:
                print(
                    f'  Retrying pip install {package} (attempt {attempt}/{retries})...'
                )
                time.sleep(2**attempt)
            else:
                print(
                    f'  [ERROR] Failed to install {package} after {retries} attempts.'
                )
                return False
    return False


def ensure_module(name: str, *, retries: int = 3) -> None:
    """Make sure *name* is importable; pip-install it if not."""
    try:
        importlib.import_module(name)
    except ImportError:
        print(f'{name} not installed, installing...')
        if not pip_install(name, retries=retries):
            raise SetupError(f'Cannot continue without {name}.')
        try:
            importlib.import_module(name)
        except ImportError:
            raise SetupError(f'{name} installed but still not importable.')


def is_in_virtualenv() -> bool:
    """Detect virtualenv / venv / conda environments safely."""
    try:
        if hasattr(sys, 'real_prefix'):
            return True
        if hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix:
            return True
    except AttributeError:
        pass
    if hasattr(sys, 'frozen'):
        return True
    if 'CONDA_PREFIX' in os.environ:
        return True
    return False


def _check_disk_space(path: str, required_mb: int = MIN_FREE_DISK_MB) -> None:
    """Ensure at least *required_mb* MB free on the drive containing *path*."""
    try:
        usage = shutil.disk_usage(os.path.dirname(os.path.abspath(path)) or '.')
        free_mb = usage.free // (1024 * 1024)
        if free_mb < required_mb:
            raise SetupError(
                f'Insufficient disk space: {free_mb} MB free, '
                f'need at least {required_mb} MB.'
            )
    except SetupError:
        raise
    except Exception as e:
        print(f'  [WARNING] Could not check disk space: {e}')


def _check_network(url: str = 'https://www.python.org', timeout: int = 10) -> bool:
    """Quick connectivity check. Returns True if reachable."""
    import socket

    try:
        host = url.split('/')[2] if '://' in url else url.split('/')[0]
        socket.create_connection((host, 443), timeout=timeout)
        return True
    except OSError:
        return False


def _detect_architecture() -> tuple[str, str]:
    """Detect CPU arch. Returns (label, tag). Supports x86, x64, ARM64."""
    machine = os.environ.get('PROCESSOR_ARCHITECTURE', '').upper()
    # On 32-bit Py on 64-bit OS, PROCESSOR_ARCHITEW6432 holds the real arch
    wow64 = os.environ.get('PROCESSOR_ARCHITEW6432', '').upper()

    effective = wow64 or machine

    if effective in ('AMD64', 'EM64T'):
        return '64-bit (x64)', 'amd64'
    elif effective in ('ARM64',):
        raise SetupError(
            'ARM64 Windows detected. Python embeddable packages '
            'for ARM64 are not available from python.org. '
            'Please use an x64/x86 environment.'
        )
    elif effective in ('IA64',):
        raise SetupError('Itanium (IA64) is not supported.')
    else:
        return '32-bit (x86)', 'win32'


def _safe_remove(path: str) -> None:
    """Remove a file or directory quietly, logging failures."""
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=False)
        elif os.path.isfile(path):
            os.remove(path)
    except OSError as e:
        print(f'  [WARNING] Could not remove {path}: {e}')


def _atomic_write(path: str, data: str, encoding: str = 'utf-8') -> None:
    """Write *data* to *path* atomically via a temp file."""
    dirname = os.path.dirname(path) or '.'
    fd, tmp = tempfile.mkstemp(dir=dirname, suffix='.tmp')
    _register_cleanup(tmp)
    try:
        with open(fd, 'w', encoding=encoding) as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        _safe_remove(tmp)
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    finally:
        _unregister_cleanup(tmp)


def _precheck_required_files() -> None:
    """Verify that local files the script depends on exist before starting."""
    missing: list[str] = []
    for p in [GET_PIP, REQUIREMENTS]:
        if not os.path.isfile(p):
            missing.append(os.path.basename(p))
    if missing:
        raise SetupError(
            'Required files not found in script directory: ' + ', '.join(missing)
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    innosetup_only = '--innosetup' in sys.argv

    print('SouthsideMusic Workspace Setup')
    print(f'  Python: {sys.version}')
    print(f'  Script dir: {SCRIPT_DIR}')

    if innosetup_only:
        ensure_module('tqdm')
        ensure_module('requests')
        import tqdm as _tqdm
        import requests as _requests

        _ensure_innosetup(_tqdm, _requests)
        return

    # 0. Pre-check required files
    _precheck_required_files()

    # 1. Environment check
    if is_in_virtualenv():
        print(
            'Please run this script in the global Python environment, '
            'not inside a venv/virtualenv/conda env.'
        )
        sys.exit(1)

    if os.name != 'nt':
        print('Sorry, this setup script only supports Windows.')
        sys.exit(1)

    # 2. Ensure bootstrap dependencies (avoid rerun dance)
    ensure_module('tqdm')
    ensure_module('requests')

    import tqdm
    import requests
    import zipfile

    # Defer inquirer – only needed for the uv prompt
    _maybe_inquirer = None
    try:
        import inquirer

        _maybe_inquirer = inquirer
    except ImportError:
        pass

    # 3. Network check before any downloads
    print('\nChecking network connectivity...')
    if not _check_network():
        print(
            '  [ERROR] Cannot reach python.org. '
            'Check your internet connection and try again.'
        )
        sys.exit(1)
    print('  Network OK.')

    # 4. Disk space check
    _check_disk_space(SCRIPT_DIR)

    # 5. Ensure uv is available
    _ensure_uv(_maybe_inquirer)

    # 6. Sync uv project environment
    print('\n[1/5] Syncing uv environment...')
    _uv_sync_with_retry()

    # 7. Set up embedded Python
    _setup_embed_python(tqdm, requests, zipfile)

    # 8. Set up build venv (clean venv with Nuitka only)
    _setup_build_venv()

    # 9. Install requirements into embedded Python
    _install_embed_requirements()

    # 10. Quick validation
    _validate_embed_python()

    # 11. Inno Setup
    _ensure_innosetup(tqdm, requests)

    print('\nSetup complete!')


# ---------------------------------------------------------------------------
# Step: uv
# ---------------------------------------------------------------------------
def _ensure_uv(inquirer_mod: Any | None = None) -> None:
    try:
        run(['uv', '--version'], capture_output=True)
        return
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    print('\nuv is not installed.')
    install = True
    if inquirer_mod is not None:
        try:
            install = inquirer_mod.confirm('Install uv automatically?', default=True)
        except Exception as e:
            print(f'  [WARNING] inquirer prompt failed ({e}), defaulting to install.')

    if not install:
        print(
            'uv install docs:\n'
            '  (zh-cn) https://uv.doczh.com/getting-started/installation\n'
            '  (en-us) https://docs.astral.sh/uv/getting-started/installation'
        )
        sys.exit(1)

    print('Installing uv...')
    if not pip_install('uv'):
        raise SetupError('Failed to install uv.')

    try:
        result = run(['uv', '--version'], capture_output=True, text=True)
        print(f'  uv installed: {result.stdout.strip()}')
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise SetupError(
            'uv was installed but "--version" still fails. Check the docs above.'
        )


def _uv_sync_with_retry(retries: int = 3) -> None:
    for attempt in range(1, retries + 1):
        try:
            run(['uv', 'sync'], cwd=SCRIPT_DIR)
            return
        except subprocess.CalledProcessError:
            if attempt < retries:
                wait = 2**attempt
                print(
                    f'  uv sync failed (attempt {attempt}/{retries}). '
                    f'Retrying in {wait}s...'
                )
                time.sleep(wait)
            else:
                raise SetupError(f'uv sync failed after {retries} attempts.')


# ---------------------------------------------------------------------------
# Step: embedded Python
# ---------------------------------------------------------------------------
def _setup_embed_python(tqdm, requests, zipfile) -> None:
    print('\n[2/5] Setting up embedded Python...')

    embed_exe = os.path.join(EMBED_DIR, 'python.exe')
    if os.path.isfile(embed_exe):
        # Verify existing installation works
        try:
            run([embed_exe, '--version'], capture_output=True)
            print(
                f'  Embedded Python already exists at {EMBED_DIR}, skipping download.'
            )
            return
        except (subprocess.CalledProcessError, FileNotFoundError):
            print('  Existing embedded Python is broken, re-downloading...')
            _safe_remove(EMBED_DIR)

    os.makedirs(EMBED_DIR, exist_ok=True)
    _register_cleanup(EMBED_DIR)

    try:
        arch_label, arch_tag = _detect_architecture()
        print(f'  Detected {arch_label} system')

        download_url = f'{DOWNLOAD_BASE}/python-{PYTHON_VERSION}-embed-{arch_tag}.zip'

        _download_file(
            download_url,
            PYTHON_ZIP,
            'embeddable Python',
            tqdm,
            requests,
        )

        print('  Extracting...')
        try:
            with zipfile.ZipFile(PYTHON_ZIP, 'r') as zf:
                bad = zf.testzip()
                if bad is not None:
                    raise zipfile.BadZipFile(f'Corrupted member: {bad}')
                zf.extractall(EMBED_DIR)
        except zipfile.BadZipFile as e:
            raise SetupError(
                f'Downloaded zip is corrupted: {e}. Please re-run the script.'
            )
        finally:
            _safe_remove(PYTHON_ZIP)

        _patch_embed_pth()
    except Exception:
        _safe_remove(EMBED_DIR)
        raise
    finally:
        _unregister_cleanup(EMBED_DIR)


def _patch_embed_pth() -> None:
    """Enable site-packages in the embedded Python ._pth file."""
    print('  Enabling site-packages...')
    pth_file = os.path.join(EMBED_DIR, f'python{PYTHON_VERSION[:3]}._pth')
    if not os.path.isfile(pth_file):
        print(f'  [WARNING] {pth_file} not found, trying alternate names...')
        candidates = [
            os.path.join(EMBED_DIR, f)
            for f in os.listdir(EMBED_DIR)
            if f.endswith('._pth')
        ]
        if not candidates:
            print('  [WARNING] No ._pth file found, skipping site-packages patch.')
            return
        pth_file = candidates[0]
        print(f'  Found: {os.path.basename(pth_file)}')

    encodings_to_try = ['utf-8', 'utf-8-sig', 'cp1252', 'latin-1']
    content = None
    used_enc = None
    for enc in encodings_to_try:
        try:
            with open(pth_file, 'r', encoding=enc) as f:
                content = f.read()
            used_enc = enc
            break
        except (UnicodeDecodeError, FileNotFoundError):
            continue

    if content is None:
        print(f'  [WARNING] Could not read {pth_file} with any encoding.')
        return

    if '#import site' in content:
        content = content.replace('#import site', 'import site')
        _atomic_write(pth_file, content, encoding=used_enc or 'utf-8')
        print('  site-packages enabled.')
    else:
        print('  site-packages already enabled (or pattern not found).')


# ---------------------------------------------------------------------------
# Step: pip + requirements for embedded Python
# ---------------------------------------------------------------------------
# Step: build venv (clean venv with Nuitka only)
# ---------------------------------------------------------------------------
def _setup_build_venv() -> None:
    """Create a clean venv with only Nuitka, used for building launcher.py.

    Uses venv (not embedded Python) so Nuitka's ReExecute mechanism works.
    Has no extra packages, so standalone builds stay lean.
    """
    print('\n[3/5] Setting up build venv (Nuitka only)...')

    build_python = os.path.join(BUILD_VENV, 'Scripts', 'python.exe')
    if os.path.isfile(build_python):
        try:
            run([build_python, '-m', 'nuitka', '--version'], capture_output=True)
            print(f'  Build venv with Nuitka already exists at {BUILD_VENV}, skipping.')
            return
        except (subprocess.CalledProcessError, FileNotFoundError):
            print('  Existing build venv is broken, re-creating...')
            _safe_remove(BUILD_VENV)

    _safe_remove(BUILD_VENV)

    print('  Creating venv...')
    try:
        run([sys.executable, '-m', 'venv', BUILD_VENV, '--clear'])
    except subprocess.CalledProcessError:
        raise SetupError('Failed to create build venv.')

    _register_cleanup(BUILD_VENV)

    try:
        print('  Installing Nuitka...')
        for attempt in range(1, 4):
            try:
                run(
                    [
                        build_python,
                        '-m',
                        'pip',
                        'install',
                        '--no-input',
                        '--no-cache-dir',
                        'nuitka',
                    ]
                )
                break
            except subprocess.CalledProcessError:
                if attempt < 3:
                    wait = 2**attempt
                    print(
                        f'  Nuitka install failed (attempt {attempt}/3). '
                        f'Retrying in {wait}s...'
                    )
                    time.sleep(wait)
                else:
                    raise SetupError('Failed to install Nuitka after 3 attempts.')

        try:
            result = run(
                [build_python, '-m', 'nuitka', '--version'],
                capture_output=True,
                text=True,
            )
            print(f'  Nuitka installed: {result.stdout.strip().split(chr(10))[0]}')
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise SetupError('Nuitka installed but not importable.')
    except Exception:
        _safe_remove(BUILD_VENV)
        raise
    finally:
        _unregister_cleanup(BUILD_VENV)


# ---------------------------------------------------------------------------
# Step: pip + requirements for embedded Python
# ---------------------------------------------------------------------------
def _install_embed_requirements() -> None:
    embed_exe = os.path.join(EMBED_DIR, 'python.exe')

    if not os.path.isfile(embed_exe):
        raise SetupError(f'Embedded Python not found at {embed_exe}.')

    # Install pip if not present
    print('[4/5] Ensuring pip in embedded Python...')
    try:
        run([embed_exe, '-m', 'pip', '--version'], capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print('  Installing pip via get-pip.py...')
        try:
            run([embed_exe, GET_PIP])
        except subprocess.CalledProcessError:
            raise SetupError('Failed to install pip in embedded Python.')

    # Install requirements
    print('\n[4/5] Installing requirements into embedded Python...')
    _validate_requirements_file(REQUIREMENTS)

    if not _requirements_changed():
        print('  requirements.txt unchanged, skipping install.')
        return

    for attempt in range(1, 4):
        try:
            run(
                [
                    embed_exe,
                    '-m',
                    'pip',
                    'install',
                    '--no-input',
                    '--no-cache-dir',
                    '-r',
                    REQUIREMENTS,
                ]
            )
            _save_requirements_hash()
            return
        except subprocess.CalledProcessError:
            if attempt < 3:
                wait = 2**attempt
                print(
                    f'  pip install failed (attempt {attempt}/3). '
                    f'Retrying in {wait}s...'
                )
                time.sleep(wait)
            else:
                raise SetupError('Failed to install requirements after 3 attempts.')


def _detect_file_encoding(path: str) -> str:
    """Detect the encoding of a text file via BOM / trial reads."""
    with open(path, 'rb') as f:
        head = f.read(4)
    if head.startswith(b'\xff\xfe'):
        return 'utf-16-le'
    if head.startswith(b'\xfe\xff'):
        return 'utf-16-be'
    if head.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'
    encodings_to_try = ['utf-8', 'utf-8-sig', 'cp1252', 'latin-1', 'utf-16']
    for enc in encodings_to_try:
        try:
            with open(path, 'r', encoding=enc) as f:
                f.read(1)
            return enc
        except (UnicodeDecodeError, UnicodeError, FileNotFoundError):
            continue
    return 'utf-8'


def _validate_requirements_file(path: str) -> None:
    """Check that requirements.txt has actual content."""
    enc = _detect_file_encoding(path)
    try:
        with open(path, 'r', encoding=enc) as f:
            lines = [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith('#')
            ]
        if not lines:
            print(f'  [WARNING] {path} is empty or contains only comments.')
        else:
            print(f'  {len(lines)} package(s) to install from {path}')
    except OSError as e:
        raise SetupError(f'Cannot read {path}: {e}')


def _compute_file_hash(path: str) -> str:
    """Compute SHA256 hex digest of a file."""
    sha256 = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            sha256.update(chunk)
    return sha256.hexdigest()


def _requirements_changed() -> bool:
    """Check if requirements.txt has changed since last successful install."""
    try:
        current = _compute_file_hash(REQUIREMENTS)
    except OSError:
        return True
    try:
        with open(REQUIREMENTS_HASH, 'r') as f:
            stored = f.read().strip()
        return stored != current
    except (OSError, FileNotFoundError):
        return True


def _save_requirements_hash() -> None:
    """Persist current requirements.txt hash."""
    try:
        h = _compute_file_hash(REQUIREMENTS)
        _atomic_write(REQUIREMENTS_HASH, h)
    except OSError as e:
        print(f'  [WARNING] Could not save requirements hash: {e}')


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _validate_embed_python() -> None:
    """Quick smoke-test for embedded Python."""
    embed_exe = os.path.join(EMBED_DIR, 'python.exe')
    if not os.path.isfile(embed_exe):
        raise SetupError(
            f'Embedded Python not found at {EMBED_DIR}. Setup did not complete.'
        )
    try:
        run(
            [embed_exe, '-c', 'import sys; print(sys.version)'],
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise SetupError(f'Embedded Python failed validation smoke test: {e}')


# ---------------------------------------------------------------------------
# Inno Setup
# ---------------------------------------------------------------------------
def _is_innosetup_installed() -> bool:
    """Check whether Inno Setup is installed (PATH or registry)."""
    import shutil as _shutil

    if _shutil.which('iscc') is not None:
        return True

    try:
        import winreg
    except ImportError:
        return False

    keys = [
        # win64 native
        (
            winreg.HKEY_LOCAL_MACHINE,
            r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 7_is1',
        ),
        # win32 on win64
        (
            winreg.HKEY_LOCAL_MACHINE,
            r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 7_is1',
        ),
        # Inno Setup 6 fallbacks
        (
            winreg.HKEY_LOCAL_MACHINE,
            r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1',
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1',
        ),
    ]
    for hive, subkey in keys:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                path, _ = winreg.QueryValueEx(key, 'InstallLocation')
                iscc = os.path.join(path, 'ISCC.exe')
                if os.path.isfile(iscc):
                    return True
        except OSError:
            continue
    return False


def _ensure_innosetup(tqdm, requests) -> None:
    if _is_innosetup_installed():
        print('  Inno Setup already installed.')
        return

    print('\nInno Setup is not installed.')

    inquirer_mod = None
    try:
        import inquirer

        inquirer_mod = inquirer
    except ImportError:
        pass

    if inquirer_mod is not None:
        try:
            ok = inquirer_mod.confirm(
                'Download and install Inno Setup automatically?', default=True
            )
        except Exception:
            ok = True
    else:
        ok = True

    if not ok:
        print('Download Inno Setup manually:\n  https://jrsoftware.org/isdl.php')
        return

    print('\n[5/5] Installing Inno Setup...')
    _check_network('https://api.github.com')

    api_url = INNO_SETUP_RELEASES_API
    resp = requests.get(api_url, headers={'User-Agent': USER_AGENT}, timeout=30)
    resp.raise_for_status()
    release = resp.json()

    assets = release.get('assets', [])
    exe_assets = [a for a in assets if a.get('name', '').endswith('.exe')]
    if not exe_assets:
        print(
            '  No .exe assets found in the latest GitHub release. '
            'Please download Inno Setup manually:\n'
            '  https://jrsoftware.org/isdl.php'
        )
        return

    asset = _pick_innosetup_asset(exe_assets, inquirer_mod)
    if asset is None:
        return

    installer_path = os.path.join(tempfile.gettempdir(), asset['name'])
    _download_file(
        asset['browser_download_url'],
        installer_path,
        'Inno Setup',
        tqdm,
        requests,
        retries=3,
    )

    print('  Running Inno Setup installer (silent)...')
    try:
        run([installer_path] + INNO_SETUP_INSTALL_ARGS, timeout=120)
    except subprocess.CalledProcessError as e:
        print(f'  [WARNING] Inno Setup installer exited with error: {e}')
    finally:
        _safe_remove(installer_path)

    if _is_innosetup_installed():
        print('  Inno Setup installed successfully.')
    else:
        print('  [WARNING] ISCC still not found on PATH after installation.')
        print('  Try running build.bat — it will auto-detect via registry.')


def _pick_innosetup_asset(assets: list[dict], inquirer_mod: Any | None) -> dict | None:
    """Pick the right Inno Setup .exe from the list of release assets.

    Returns the selected asset dict, or None if the user cancels.
    When there is exactly one asset it is returned immediately;
    otherwise inquirer is used to let the user choose.
    """
    if len(assets) == 1:
        print(f'  Found installer: {assets[0]["name"]}')
        return assets[0]

    # Multiple .exe found — need user input
    if inquirer_mod is None:
        print('  Multiple Inno Setup .exe files found:')
        for a in assets:
            print(f'    - {a["name"]}')
        print(
            '  Install "inquirer" (pip install inquirer) to enable interactive selection.'
        )
        print('  Defaulting to the first asset.')
        return assets[0]

    choices = [(a['name'], a) for a in sorted(assets, key=lambda x: x['name'])]

    try:
        result = inquirer_mod.list_input(
            'Multiple Inno Setup installers found. Which one to download?',
            choices=choices,
        )
        return result
    except Exception as e:
        print(f'  [WARNING] inquirer selection failed ({e}), using first asset.')
        return assets[0]


# ---------------------------------------------------------------------------
# Download helper
# ---------------------------------------------------------------------------
def _verify_hash(filepath: str, url: str) -> None:
    """Verify the SHA256 of *filepath* against EMBED_ZIP_SHA256 if the URL matches."""

    expected = None
    for arch_tag, sha in EMBED_ZIP_SHA256.items():
        if f'-embed-{arch_tag}.zip' in url:
            expected = sha
            break

    if expected is None:
        print('  [WARNING] No SHA256 on file for this download, skipping hash check.')
        return

    print('  Verifying SHA256...')
    sha256 = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            sha256.update(chunk)
    actual = sha256.hexdigest()
    if actual != expected:
        raise OSError(f'SHA256 mismatch:\n  expected {expected}\n  got      {actual}')
    print('  SHA256 OK.')


def _download_file(
    url: str,
    dest: str,
    desc: str,
    tqdm,
    requests,
    retries: int = 3,
) -> None:
    session = requests.Session()
    for env_var in ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy'):
        proxy = os.environ.get(env_var)
        if proxy:
            session.proxies['https'] = proxy
            session.proxies['http'] = proxy
            break
    verify = os.environ.get(
        'REQUESTS_CA_BUNDLE', os.environ.get('CURL_CA_BUNDLE', True)
    )

    for attempt in range(1, retries + 1):
        tmp_dest = dest + '.part'
        try:
            resp = session.get(
                url,
                stream=True,
                timeout=(30, 300),
                headers={'User-Agent': USER_AGENT},
                verify=verify,
            )
            resp.raise_for_status()

            total = int(resp.headers.get('content-length', 0))
            _register_cleanup(tmp_dest)

            disable_bar = not sys.stdout.isatty()
            bar = tqdm.tqdm(
                desc=f'Downloading {desc}',
                total=total or None,
                unit='B',
                unit_scale=True,
                unit_divisor=1024,
                disable=disable_bar,
            )
            try:
                with open(tmp_dest, 'wb') as f:
                    for chunk in resp.iter_content(
                        chunk_size=DOWNLOAD_CHUNK_SIZE,
                    ):
                        if chunk:
                            f.write(chunk)
                            bar.update(len(chunk))
            finally:
                bar.close()

            downloaded_size = os.path.getsize(tmp_dest)
            if total > 0 and downloaded_size != total:
                raise OSError(
                    f'Download incomplete: '
                    f'expected {total} bytes, got {downloaded_size}'
                )
            elif total == 0 and downloaded_size == 0:
                raise OSError(
                    'Download produced zero bytes. '
                    'The server may not support range requests.'
                )

            _verify_hash(tmp_dest, url)

            os.replace(tmp_dest, dest)
            _unregister_cleanup(tmp_dest)
            return

        except (requests.RequestException, OSError) as e:
            _safe_remove(tmp_dest)
            _unregister_cleanup(tmp_dest)
            if attempt < retries:
                wait = 2**attempt
                print(
                    f'  Download failed (attempt {attempt}/{retries}): {e}. '
                    f'Retrying in {wait}s...'
                )
                time.sleep(wait)
            else:
                raise SetupError(f'Download failed after {retries} attempts: {e}')


# ---------------------------------------------------------------------------
if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nInterrupted by user. Cleaning up...')
        _cleanup()
        sys.exit(1)
    except SetupError as e:
        print(f'\n[FATAL] {e}')
        _cleanup()
        sys.exit(1)
