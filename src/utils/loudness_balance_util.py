import logging
import numpy as np
import pyloudnorm as pyln
from pydub import AudioSegment

def getAdjustedGainFactor(target_lufs: float, audio: AudioSegment) -> float:
    samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
    dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
    dtype = dtype_map[audio.sample_width]
    max_val = np.iinfo(dtype).max
    samples = samples / max_val

    meter = pyln.Meter(audio.frame_rate)
    loudness = meter.integrated_loudness(samples)

    gain = 10 ** ((target_lufs - loudness) / 20.0)
    logging.info(f'loudness adjusted, {gain=}, {target_lufs=}')
    return gain