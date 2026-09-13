from pathlib import Path
import ctypes
import subprocess
import sys

# Held for the lifetime of the process. It stops a second copy from
# bootstrapping while this one runs, and it is what makes the installer ask the
# user to close the app before overwriting files it is running from.
MUTEX_NAME = 'Local\\SouthsideMusicLauncher'
ERROR_ALREADY_EXISTS = 183
MB_ICONERROR = 0x10
# Nuitka standalone needs Launch.exe and its whole runtime together, and the
# app root must stay free of a GIL-build python314.dll: the app's free-threaded
# Python loads the wrong one through scipy -> ctypes and crashes. So the
# launcher lives in this folder and the app payload is its parent.
LAUNCHER_DIR_NAME = 'launcher'


def appRoot() -> Path:
    """The folder holding python/, bootstrap.py and src/."""
    here = Path(__file__).resolve().parent
    return here.parent if here.name == LAUNCHER_DIR_NAME else here


def showError(message: str) -> None:
    # The packaged launcher runs without a console, so a print would be lost.
    try:
        ctypes.windll.user32.MessageBoxW(None, message, 'Southside Music', MB_ICONERROR)
    except OSError:
        pass


def acquireSingleInstance() -> int | None:
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return None
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return handle


def main() -> int:
    mutex = acquireSingleInstance()
    if mutex is None:
        showError('SouthsideMusic is already running.')
        return 0

    cwd = appRoot()
    python = cwd / 'python' / 'python.exe'
    bootstrap = cwd / 'bootstrap.py'

    if not python.exists():
        showError(f'Missing interpreter:\n{python}')
        return 1
    if not bootstrap.exists():
        showError(f'Missing bootstrap:\n{bootstrap}')
        return 1

    process = subprocess.Popen(
        [str(python), str(bootstrap)],
        # Always start from the app root: the launcher folder holds a GIL-build
        # python DLL that must not be on a free-threaded subprocess search path.
        cwd=str(cwd),
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
        text=True,
    )
    return process.wait()


if __name__ == '__main__':
    sys.exit(main())
