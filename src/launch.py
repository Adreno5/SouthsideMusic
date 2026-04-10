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

    cmd_command = [
        'cmd.exe', '/c',
        str(python),
        str(mainpy)
    ]

    print(f'[LAUNCH] executing command: {" ".join(cmd_command)}')

    subprocess.run([str(python), '--version'], text=True)

    print('[LAUNCH] run launch script')
    process = subprocess.Popen(
        cmd_command,
        stdout=sys.stdout,
        stderr=sys.stderr,
        stdin=sys.stdin,
        text=True,
        cwd=cwd
    )

    process.wait()

    print(f'[EXIT] exited: {process.returncode}')
    sys.exit(process.returncode)