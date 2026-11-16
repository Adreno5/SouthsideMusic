import pathlib
import wave

import numpy as np
from PIL import Image, ImageDraw

here = pathlib.Path(__file__).parent
w = wave.open(str(here / 'sfx.wav'))
x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2) / 32767.0
sr = w.getframerate()
mono = x.mean(axis=1)

W, H = 1800, 420
img = Image.new('RGB', (W, H), '#EDE7DA')
d = ImageDraw.Draw(img)
step = max(1, len(mono) // W)
for i in range(W):
    seg = mono[i * step : (i + 1) * step]
    if not len(seg):
        break
    hi, lo = seg.max(), seg.min()
    d.line([(i, H / 2 - hi * H * 0.46), (i, H / 2 - lo * H * 0.46)], fill='#0E0E0C')
d.line([(0, H / 2), (W, H / 2)], fill='#2226DE')
# 1 s grid
for s in range(int(len(mono) / sr) + 1):
    px = s / (len(mono) / sr) * W
    d.line([(px, 0), (px, 12)], fill='#FF3B12')
img.save(here / 'wave.png')
print('dur', round(len(mono) / sr, 2), 'peak', round(float(np.abs(x).max()), 3))
