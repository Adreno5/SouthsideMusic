import json
import subprocess
import sys
import pathlib

here = pathlib.Path(__file__).parent
score = json.loads((here / 'score.json').read_text(encoding='utf-8'))
shots = ','.join(f'{t:.2f}' for t in score['shots'])
cmd = [
    sys.executable,
    r'C:\Users\Adreno\.agents\skills\onetake\scripts\verify_promo.py',
    'draft.mp4',
    '--comp',
    'comp.html',
    '--shots',
    shots,
]
print(' '.join(cmd[-2:]))
sys.exit(subprocess.run(cmd, cwd=here).returncode)
