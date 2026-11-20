from pathlib import Path
import ctypes
import os
import subprocess
import sys

# Held for the lifetime of the process. It stops a second copy from
# bootstrapping while this one runs, and on Windows it is what makes the
# installer ask the user to close the app before overwriting files it is
# running from.
MUTEX_NAME = 'Local\\SouthsideMusicLauncher'
ERROR_ALREADY_EXISTS = 183
MB_ICONERROR = 0x10
# Nuitka standalone needs Launch.exe and its whole runtime together, and the
# app root must stay free of a GIL-build python314.dll: the app's free-threaded
# Python loads the wrong one through scipy -> ctypes and crashes. So the
# launcher lives in this folder and the app payload is its parent.
LAUNCHER_DIR_NAME = 'launcher'

_instance_handle: object | None = None


def appRoot() -> Path:
    """The folder holding python/, bootstrap.py and src/."""
    here = Path(__file__).resolve().parent
    return here.parent if here.name == LAUNCHER_DIR_NAME else here


def showError(message: str) -> None:
    # The packaged launcher runs without a console on Windows, so a print would
    # be lost there. POSIX packages still have stderr attached to the caller.
    if os.name == 'nt':
        try:
            ctypes.windll.user32.MessageBoxW(
                None, message, 'Southside Music', MB_ICONERROR
            )
        except OSError:
            pass
        return
    try:
        print(message, file=sys.stderr)
    except (OSError, ValueError):
        pass


def acquireSingleInstance() -> bool:
    global _instance_handle
    if os.name == 'nt':
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_wchar_p,
        ]
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return False
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _instance_handle = handle
        return True

    import fcntl
    import tempfile

    lock_path = Path(tempfile.gettempdir()) / f'southside-music-{os.getuid()}.lock'
    handle = lock_path.open('a+')
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _instance_handle = handle
    return True


def pythonPath(root: Path) -> Path | None:
    if os.name == 'nt':
        candidate = root / 'python' / 'python.exe'
        return candidate if candidate.is_file() else None
    bindir = root / 'python' / 'bin'
    for name in ('python3', 'python3.14'):
        candidate = bindir / name
        if candidate.is_file():
            return candidate
    matches = sorted(
        path for path in bindir.glob('python3*') if not path.name.endswith('-config')
    )
    return matches[0] if matches else None


def main() -> int:
    if not acquireSingleInstance():
        showError('SouthsideMusic is already running.')
        return 0

    cwd = appRoot()
    python = pythonPath(cwd)
    if os.name == 'nt':
        script = cwd / 'bootstrap.py'
    else:
        script = cwd / 'src' / 'main.py'

    if python is None:
        showError(f'Missing interpreter under:\n{cwd / "python"}')
        return 1
    if not script.exists():
        showError(f'Missing entry point:\n{script}')
        return 1

    creationflags = 0
    if os.name == 'nt':
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    process = subprocess.Popen(
        [str(python), str(script)],
        # Always start from the app root: the launcher folder holds a GIL-build
        # python DLL that must not be on a free-threaded subprocess search path.
        cwd=str(cwd),
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
        text=True,
        creationflags=creationflags,
    )
    return process.wait()


if __name__ == '__main__':
    sys.exit(main())
