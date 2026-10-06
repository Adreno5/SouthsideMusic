from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    directory = root / 'data' / 'pcm'
    directory.mkdir(parents=True, exist_ok=True)
    experiment = """
import gc
import json
import subprocess
import sys
import tracemalloc
import types
from pathlib import Path

import psutil

sys.path.insert(0, str(Path('src').resolve()))

from core.audio_decode import PatchedAudioSegment
from core.audio_player import AudioPlayer
from core.pcm_timeline import PcmTimeline

before = sys.argv[1] == 'before'
if before:
    module = types.ModuleType('baseline_decode')
    exec(
        subprocess.check_output(
            ['git', 'show', 'HEAD:src/core/audio_decode.py'], text=True
        ),
        module.__dict__,
    )
    decoder = module.PatchedAudioSegment
else:
    decoder = PatchedAudioSegment

baseline = psutil.Process().memory_info().rss
tracemalloc.start()
first = decoder.from_file(sys.argv[2])
second = decoder.from_file(sys.argv[2])
current = AudioPlayer.prepareBuffer(first)
following = AudioPlayer.prepareBuffer(second)
timeline = PcmTimeline(2)
timeline.append(current.samples)
timeline.append(following.samples)
lead = 120 if before else 8
queue = [
    timeline.read(start, min(start + 2048, 96000 * lead))
    for start in range(0, 96000 * lead, 2048)
]
gc.collect()
rss = psutil.Process().memory_info().rss
live, peak = tracemalloc.get_traced_memory()
print(json.dumps({
    'mode': sys.argv[1],
    'rss_mb': rss / 1024**2,
    'audio_rss_delta_mb': (rss - baseline) / 1024**2,
    'live_python_mb': live / 1024**2,
    'peak_python_mb': peak / 1024**2,
}))
"""
    results = []
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        audio = Path(temporary).resolve() / 'input.flac'
        if not audio.is_relative_to(directory.resolve()):
            raise ValueError('Benchmark must remain inside the PCM directory')
        subprocess.run(
            [
                'ffmpeg',
                '-v',
                'error',
                '-f',
                'lavfi',
                '-i',
                'sine=frequency=1000:sample_rate=96000:duration=60',
                '-ac',
                '2',
                '-sample_fmt',
                's32',
                str(audio),
            ],
            check=True,
            cwd=root,
        )
        for mode in ('before', 'after'):
            output = subprocess.check_output(
                [sys.executable, '-c', experiment, mode, str(audio)],
                cwd=root,
                text=True,
            )
            result = json.loads(
                [line for line in output.splitlines() if line.startswith('{')][-1]
            )
            results.append(result)
            print(json.dumps(result))
    path = root / 'data' / 'debug' / 'performance' / 'audio-memory-optimization.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                'scenario': 'Two 60-second 96 kHz stereo tracks with queued PCM',
                'before_lead_seconds': 120,
                'after_lead_seconds': 8,
                'results': results,
            },
            indent=2,
        ),
        encoding='utf-8',
    )


if __name__ == '__main__':
    main()
