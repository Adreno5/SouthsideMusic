from pathlib import Path
import subprocess
import sys


def main() -> int:
    cwd = Path(__file__).resolve().parent
    python = cwd / 'python' / 'python.exe'
    bootstrap = cwd / 'bootstrap.py'

    if not python.exists():
        return 1
    if not bootstrap.exists():
        return 1

    process = subprocess.Popen(
        [str(python), str(bootstrap)],
        cwd=str(cwd),
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
        text=True,
    )
    return process.wait()


if __name__ == '__main__':
    sys.exit(main())
