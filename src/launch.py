import os
from pathlib import Path
import subprocess
import sys

if __name__ == '__main__':
    print('[LAUNCH] launching')

    cwd = Path(os.getcwd()).resolve()
    python = cwd / ".venv" / "Scripts" / "python.exe"
    mainpy = cwd / "src" / "main.py"

    if not python.exists():
        print('[ERROR] python.exe not found')
        sys.exit(1)
    if not mainpy.exists():
        print('[ERROR] main.py not found')
        sys.exit(1)

    print(f'[LAUNCH] cwd={cwd.as_posix()}')
    print(f'[LAUNCH] python={python.as_posix()}')
    print(f'[LAUNCH] mainpy={mainpy.as_posix()}')

    powershell_cmd = [
        'powershell.exe',
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-Command',
        f"cd '{cwd.as_posix()}'; & '{python.as_posix()}' '{mainpy.as_posix()}'"
    ]

    print(f'[LAUNCH] excution command: {' '.join(powershell_cmd)}')

    subprocess.run([python.as_posix(), '--version'], text=True, shell=True)

    print('[LAUNCH] run launch script')
    process = subprocess.Popen(
        powershell_cmd,
        stdout=sys.stdout,
        stderr=sys.stderr,
        stdin=sys.stdin,
        text=True,
        cwd=cwd.as_posix()
    )

    process.wait()

    print(f'[EXIT] exited: {process.returncode}')
    sys.exit(process.returncode)