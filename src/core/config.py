import base64
from dataclasses import dataclass, field
import json
import logging
import os

from typing import Any, Literal, cast

import win32crypt

from core.models import SongStorable

_logger = logging.getLogger(__name__)

cfg_cache: dict[str, Any] = {}

@dataclass
class Config:
    language: Literal['en_US', 'zh_CN'] = 'en_US'

    search_type: Literal['Songs', 'Playlists'] = 'Songs'

    play_method: Literal['Repeat one', 'Repeat list', 'Shuffle', 'Play in order'] = (
        'Repeat list'
    )
    skip_nosound: bool = True
    skip_threshold: int = -45
    skip_remain_time: int = 10

    last_playlist: list[SongStorable] | None = None
    last_playing_index: int = -1
    last_playing_time: float = 0

    output_device_index: int = 0

    window_x: int = 0
    window_y: int = 0
    window_width: int = 0
    window_height: int = 0
    window_maximized: bool = False

    enable_desktop_lyrics: bool = False
    desktop_lyrics_anchor: Literal['top-center', 'normal'] = 'normal'
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
    login_method: Literal['anonymous', 'cell phone', 'QR code'] = 'anonymous'

    stereo: bool = True
    stereo_haas_index: int = 1
    enable_reverb: bool = False
    reverb_intensity: int = 3

    background_ratio: float = 0.4
    volume: float = 1

    lyrics_smooth_factor: float = 0.028
    acceleration_smooth_factor: float = 0.068

    play_speed: float = 1
    play_pitch: float = 0

    show_translation: bool = True
    setting_section_expanded: dict[str, bool] = field(default_factory=dict)

    download_concurrent_threads: int = 16

    llm_base_url: str = 'https://api.openai.com/v1'
    llm_api_key_encrypted: str = ''
    llm_model: str = ''
    llm_viewer_expanded: bool = False

    def __init__(self) -> None:
        super().__init__()
        global _instance
        _instance = self

    @staticmethod
    def instance() -> 'Config':
        global _instance
        return _instance

_instance: Config = cast(Config, None)
Config()
cfg = Config.instance()

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
CONFIG_PATH = os.path.join(_PROJECT_ROOT, 'config.json')
LEGACY_PICKLE_CONFIG_PATH = os.path.join(_PROJECT_ROOT, 'config.pkl')
SECRET_PREFIX = 'win32crypt:'


def encryptSecret(value: str) -> str:
    if not value:
        return ''
    encrypted = win32crypt.CryptProtectData(
        value.encode('utf-8'),
        'SouthsideMusic',
        None,
        None,
        None,
        0,
    )
    return f'{SECRET_PREFIX}{base64.b64encode(encrypted).decode("ascii")}'


def decryptSecret(value: str) -> str:
    if not value or not value.startswith(SECRET_PREFIX):
        return ''
    try:
        encrypted = base64.b64decode(value[len(SECRET_PREFIX) :].encode('ascii'))
        _desc, data = win32crypt.CryptUnprotectData(
            encrypted,
            None,
            None,
            None,
            0,
        )
        return data.decode('utf-8')
    except Exception as e:
        _logger.exception(e)
        return ''


def _song_from_object(data: Any) -> SongStorable | None:
    if not isinstance(data, dict):
        return None
    try:
        return SongStorable.fromObject(data)  # type: ignore[arg-type]
    except Exception as e:
        _logger.exception(e)
        return None


def _config_to_json_object() -> dict[str, Any]:
    data = _instance.__dict__.copy()
    data['last_playlist'] = [
        song.toObject()
        for song in (_instance.last_playlist or [])
        if isinstance(song, SongStorable)
    ]
    data.pop('last_playing_song', None)
    return data


def _apply_config_json_object(data: dict[str, Any]) -> None:
    if data.get('language') not in ('en_US', 'zh_CN'):
        data.pop('language', None)

    if 'setting_section_expanded' in data:
        section_expanded = data.get('setting_section_expanded')
        if isinstance(section_expanded, dict):
            data['setting_section_expanded'] = {
                str(key): bool(value) for key, value in section_expanded.items()
            }
        else:
            data['setting_section_expanded'] = {}

    if 'last_playlist' in data:
        data['last_playlist'] = [
            song
            for song in (
                _song_from_object(item) for item in data.get('last_playlist', [])
            )
            if song is not None
        ]
    elif 'last_playing_song' in data:
        song = _song_from_object(data.get('last_playing_song'))
        data['last_playlist'] = [song] if song else []
        data['last_playing_index'] = 0 if song else -1
    data.pop('last_playing_song', None)
    _instance.__dict__.update(data)


def _delete_legacy_pickle_config() -> None:
    if not os.path.exists(LEGACY_PICKLE_CONFIG_PATH):
        return
    try:
        os.remove(LEGACY_PICKLE_CONFIG_PATH)
        _logger.info('deleted legacy config.pkl')
    except Exception as e:
        _logger.exception(e)


def loadConfig() -> None:
    global cfg
    if not os.path.exists(CONFIG_PATH):
        saveConfig()
    else:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if isinstance(data, dict):
            _apply_config_json_object(data)
            _logger.info(f'loaded config {len(_instance.__dict__)=}')
        else:
            _logger.warning('invalid config.json, using defaults')
            saveConfig()

    _delete_legacy_pickle_config()


def saveConfig() -> None:
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(_config_to_json_object(), f, ensure_ascii=False, indent=2)

        _logger.info('saved config')
