from dataclasses import dataclass
import json
import os
from typing import Literal

from utils.lyrics.base_util import SongStorable

@dataclass
class Config:
    play_method: Literal['Repeat one', 'Repeat list', 'Shuffle', 'Play in order'] = 'Repeat list'
    island_checked: bool = False
    island_x: int = 0
    island_y: int = 0
    island_background_alpha: int = 120

    last_playing_song: SongStorable | None = None
    last_playing_time: float = 0

    window_x: int = 0
    window_y: int = 0
    window_width: int = 0
    window_height: int = 0
    wiondow_maximized: bool = False

    enable_fft: bool = True
    fft_filtering_windowsize: int = 4
    fft_factor: float = 0.4

cfg = Config()

def loadConfig() -> None:
    if not os.path.exists('./config.json'):
        saveConfig()
    else:
        with open('./config.json', 'r', encoding='utf-8') as f:
            data = json.load(f)

            cfg.play_method = data['play_method']
            cfg.island_checked = data['island_checked']
            cfg.island_x = data['island_x']
            cfg.island_y = data['island_y']

            cfg.last_playing_song = SongStorable.fromObject(data['last_playing_song']) if data['last_playing_song'] else None
            cfg.last_playing_time = data['last_playing_time']

            cfg.window_x = data.get('window_x', 0)
            cfg.window_y = data.get('window_y', 0)
            cfg.window_width = data.get('window_width', 0)
            cfg.window_height = data.get('window_height', 0)
            cfg.wiondow_maximized = data.get('window_maximized', False)

            cfg.enable_fft = data.get('enable_fft', True)
            cfg.fft_filtering_windowsize = data.get('fft_filtering_windowsize', 4)
            cfg.fft_factor = data.get('fft_factor', 0.4)

def saveConfig() -> None:
    with open('./config.json', 'w', encoding='utf-8') as f:
        json.dump({
            'play_method': cfg.play_method,
            'island_checked': cfg.island_checked,
            'island_x': cfg.island_x,
            'island_y': cfg.island_y,
            'last_playing_song': cfg.last_playing_song.toObject() if cfg.last_playing_song else None,
            'last_playing_time': cfg.last_playing_time,
            'window_x': cfg.window_x,
            'window_y': cfg.window_y,
            'window_width': cfg.window_width,
            'window_height': cfg.window_height,
            'window_maximized': cfg.wiondow_maximized,
            'enable_fft': cfg.enable_fft,
            'fft_filtering_windowsize': cfg.fft_filtering_windowsize,
            'fft_factor': cfg.fft_factor
        }, f, indent=4)