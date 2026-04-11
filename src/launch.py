import os
import sys
import subprocess
from pathlib import Path

if __name__ == '__main__':
    print('[LAUNCH] launching')

    cwd = Path(os.path.abspath(__file__)).parent
    mainpy = cwd / "src" / "main.py"
    venv_python = cwd / ".venv" / "Scripts" / "python.exe"

    print(f'[LAUNCH] cwd      = {cwd}')
    print(f'[LAUNCH] using    = {venv_python}')
    print(f'[LAUNCH] main.py  = {mainpy}')

    subprocess.call([
        str(venv_python),
        str(mainpy)
    ], cwd=cwd)

    sys.exit(0)