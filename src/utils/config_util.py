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

def saveConfig() -> None:
    with open('./config.json', 'w', encoding='utf-8') as f:
        json.dump({
            'play_method': cfg.play_method,
            'island_checked': cfg.island_checked,
            'island_x': cfg.island_x,
            'island_y': cfg.island_y,
            'last_playing_song': cfg.last_playing_song.toObject() if cfg.last_playing_song else None,
            'last_playing_time': cfg.last_playing_time
        }, f, indent=4)