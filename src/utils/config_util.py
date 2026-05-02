from dataclasses import dataclass
import json
import logging
import pickle
import os
from typing import Literal

import pyncm

from utils.base.base_util import SongStorable

@dataclass
class Config:
    play_method: Literal['Repeat one', 'Repeat list', 'Shuffle', 'Play in order'] = 'Repeat list'
    skip_nosound: bool = True
    skip_threshold: int = -45
    skip_remain_time: int = 10

    last_playing_song: SongStorable | None = None
    last_playing_time: float = 0

    window_x: int = 0
    window_y: int = 0
    window_width: int = 0
    window_height: int = 0
    window_maximized: bool = False

    enable_fft: bool = True
    fft_filtering_windowsize: int = 4
    fft_factor: float = 0.4
    cfft_multiple: float = 1.0
    sfft_multiple: float = 1.0

    target_lufs: int = -16

    session: str | None = None
    login_status: dict | None = None
    login_method: Literal['anonymous', 'cell phone', 'QR code'] = 'anonymous'

    stereo: bool = True

    show_progress: bool = False
    progress_inter: bool = False
    progress: float = 0

    background_ratio: float = 0.6

cfg = Config()

def restoreOldConfigFormat() -> None:
    if not os.path.exists('./config.json'):
        return
    
    with open('./config.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

        cfg.play_method = data['play_method']
        cfg.skip_nosound = data.get('skip_nosound', True)
        cfg.skip_threshold = data.get('skip_threshold', -45)
        cfg.skip_remain_time = data.get('skip_remain_time', 10)

        cfg.last_playing_song = SongStorable.fromObject(data['last_playing_song']) if data['last_playing_song'] else None
        cfg.last_playing_time = data['last_playing_time']

        cfg.window_x = data.get('window_x', 0)
        cfg.window_y = data.get('window_y', 0)
        cfg.window_width = data.get('window_width', 0)
        cfg.window_height = data.get('window_height', 0)
        cfg.window_maximized = data.get('window_maximized', False)

        cfg.enable_fft = data.get('enable_fft', True)
        cfg.fft_filtering_windowsize = data.get('fft_filtering_windowsize', 4)
        cfg.fft_factor = data.get('fft_factor', 0.4)
        cfg.cfft_multiple = data.get('cfft_multiple', 1.0)
        cfg.sfft_multiple = data.get('sfft_multiple', 1.0)

        cfg.target_lufs = data.get('target_lufs', -16)

        cfg.session = None
        cfg.login_status = None

        cfg.stereo = True

        saveConfig()

        logging.info('restored old config.json to pickle format')

    os.remove('./config.json')

def loadConfig() -> None:
    global cfg
    restoreOldConfigFormat()

    if not os.path.exists('./config.pkl'):
        saveConfig()
    else:
        with open('./config.pkl', 'rb') as f:
            data = pickle.load(f)

            cfg.__dict__.update(data)

def saveConfig() -> None:
    with open('./config.pkl', 'wb') as f:
        pickle.dump(cfg.__dict__, f)
