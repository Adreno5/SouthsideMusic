from pathlib import Path
import subprocess
import sys


def main() -> int:
    cwd = Path(__file__).resolve().parent
    python = cwd / "python" / "python.exe"
    launch = cwd / "src" / "launch.py"

    if not python.exists():
        return 1
    if not launch.exists():
        return 1

    process = subprocess.Popen(
        [str(python), str(launch)],
        cwd=str(cwd),
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
        text=True,
    )
    return process.wait()


if __name__ == "__main__":
    sys.exit(main())
