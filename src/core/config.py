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

    enable_crossfade: bool = True
    crossfade_strength: float = 1

    background_ratio: float = 0.4
    volume: float = 1

    lyrics_smooth_factor: float = 0.028
    acceleration_smooth_factor: float = 0.068

    play_speed: float = 1
    play_pitch: float = 0

    show_translation: bool = True
    show_advanced_settings: bool = False
    setting_section_expanded: dict[str, bool] = field(default_factory=dict)

    download_concurrent_threads: int = 16

    llm_base_url: str = 'https://api.openai.com/v1'
    llm_api_key_encrypted: str = ''
    llm_model: str = ''
    llm_providers: list[dict[str, Any]] = field(default_factory=list)
    llm_current_provider: str = ''
    llm_current_model: str = ''
    llm_viewer_expanded: bool = False

    def __init__(self) -> None:
        super().__init__()
        self.setting_section_expanded = {}
        self.llm_providers = []
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

    providers = data.get('llm_providers')
    if isinstance(providers, list):
        data['llm_providers'] = [
            provider
            for provider in (_normalize_llm_provider(item) for item in providers)
            if provider is not None
        ]
    else:
        data['llm_providers'] = []

    _instance.__dict__.update(data)
    _migrate_legacy_llm_config()


def _normalize_llm_provider(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    name = str(data.get('name', '')).strip()
    if not name:
        return None
    api_format = str(data.get('api_format', 'openai_chat'))
    if api_format not in ('openai_chat', 'openai_responses', 'anthropic'):
        api_format = 'openai_chat'
    models_data = data.get('models')
    models: list[dict[str, Any]] = []
    if isinstance(models_data, list):
        for item in models_data:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get('id', '')).strip()
            display_name = str(item.get('display_name', '')).strip()
            if not model_id or not display_name:
                continue
            models.append(
                {
                    'id': model_id,
                    'display_name': display_name,
                    'enable_1m_context': bool(item.get('enable_1m_context', False)),
                }
            )
    return {
        'name': name,
        'api_format': api_format,
        'api_key_encrypted': str(data.get('api_key_encrypted', '')),
        'base_url': str(data.get('base_url', '')).strip().rstrip('/'),
        'models': models,
    }


def _migrate_legacy_llm_config() -> None:
    if _instance.llm_providers:
        return
    if not (
        _instance.llm_base_url or _instance.llm_api_key_encrypted or _instance.llm_model
    ):
        return
    models: list[dict[str, str]] = []
    if _instance.llm_model:
        models.append(
            {
                'id': _instance.llm_model,
                'display_name': _instance.llm_model,
            }
        )
    _instance.llm_providers = [
        {
            'name': 'Default',
            'api_format': 'openai_chat',
            'api_key_encrypted': _instance.llm_api_key_encrypted,
            'base_url': _instance.llm_base_url,
            'models': models,
        }
    ]
    _instance.llm_current_provider = 'Default'
    _instance.llm_current_model = _instance.llm_model


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
