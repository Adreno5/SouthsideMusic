from dataclasses import dataclass
import json
import logging
import os
import threading
import time
from typing import Any, Literal

from utils.base.base_util import SongStorable

cfg_changed: bool = False


@dataclass
class Config:
    play_method: Literal["Repeat one", "Repeat list", "Shuffle", "Play in order"] = (
        "Repeat list"
    )
    skip_nosound: bool = True
    skip_threshold: int = -45
    skip_remain_time: int = 10

    last_playlist: list[SongStorable] | None = None
    last_playing_index: int = -1
    last_playing_time: float = 0

    window_x: int = 0
    window_y: int = 0
    window_width: int = 0
    window_height: int = 0
    window_maximized: bool = False

    enable_desktop_lyrics: bool = False
    desktop_lyrics_anchor: Literal["top-center", "bottom-center", "normal"] = "normal"
    desktop_lyrics_x: int = 0
    desktop_lyrics_y: int = 0

    enable_fft: bool = True
    fft_filtering_windowsize: int = 4
    fft_factor: float = 0.4
    cfft_multiple: float = 1.0
    sfft_multiple: float = 1.0

    target_lufs: int = -16

    session: str | None = None
    login_status: dict | None = None
    login_method: Literal["anonymous", "cell phone", "QR code"] = "anonymous"

    stereo: bool = True

    show_progress: bool = False
    progress_inter: bool = False
    progress: float = 0

    background_ratio: float = 0.4
    volume: float = 1

    lyrics_smooth_factor: float = 8.5
    acceleration_smooth_factor: float = 9.5

    play_speed: float = 1

    def __setattr__(self, name: str, value: Any) -> None:
        global cfg_changed
        cfg_changed = True
        super().__setattr__(name, value)


cfg = Config()


CONFIG_PATH = "./config.json"
LEGACY_PICKLE_CONFIG_PATH = "./config.pkl"


def _song_to_object(song: SongStorable | None):
    return song.toObject() if isinstance(song, SongStorable) else None


def _song_from_object(data: Any) -> SongStorable | None:
    if not isinstance(data, dict):
        return None
    try:
        return SongStorable.fromObject(data)  # type: ignore[arg-type]
    except Exception as e:
        logging.warning(f"failed to restore song from config: {e}")
        return None


def _config_to_json_object() -> dict[str, Any]:
    data = cfg.__dict__.copy()
    data["last_playlist"] = [
        song.toObject()
        for song in (cfg.last_playlist or [])
        if isinstance(song, SongStorable)
    ]
    data.pop("last_playing_song", None)
    return data


def _apply_config_json_object(data: dict[str, Any]) -> None:
    if "last_playlist" in data:
        data["last_playlist"] = [
            song
            for song in (
                _song_from_object(item) for item in data.get("last_playlist", [])
            )
            if song is not None
        ]
    elif "last_playing_song" in data:
        song = _song_from_object(data.get("last_playing_song"))
        data["last_playlist"] = [song] if song else []
        data["last_playing_index"] = 0 if song else -1
    data.pop("last_playing_song", None)
    cfg.__dict__.update(data)


def _delete_legacy_pickle_config() -> None:
    if not os.path.exists(LEGACY_PICKLE_CONFIG_PATH):
        return
    try:
        os.remove(LEGACY_PICKLE_CONFIG_PATH)
        logging.info("deleted legacy config.pkl")
    except OSError as e:
        logging.warning(f"failed to delete legacy config.pkl: {e}")


def loadConfig() -> None:
    global cfg, cfg_changed

    if not os.path.exists(CONFIG_PATH):
        saveConfig()
    else:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            _apply_config_json_object(data)
            logging.info(f"loaded config {len(cfg.__dict__)=}")
        else:
            logging.warning("invalid config.json, using defaults")
            saveConfig()

    _delete_legacy_pickle_config()
    cfg_changed = False


def saveConfig() -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(_config_to_json_object(), f, ensure_ascii=False, indent=2)

        logging.info("saved config")


def autoSave():
    global cfg_changed
    while True:
        time.sleep(1)
        if cfg_changed:
            saveConfig()
            cfg_changed = False


autosave_thread = threading.Thread(target=autoSave)
autosave_thread.daemon = True
