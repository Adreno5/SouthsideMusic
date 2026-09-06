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

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
CONFIG_PATH = os.path.join(_PROJECT_ROOT, 'config.json')
LEGACY_PICKLE_CONFIG_PATH = os.path.join(_PROJECT_ROOT, 'config.pkl')
SECRET_PREFIX = 'win32crypt:'


def _configToJsonObject() -> dict[str, Any]:
    data = _instance.__dict__.copy()
    data['last_playlist'] = [
        song.toObject()
        for song in (_instance.last_playlist or [])
        if isinstance(song, SongStorable)
    ]
    data.pop('last_playing_song', None)
    return data


def saveConfig() -> None:
    if _instance is None:
        return
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(_configToJsonObject(), f, ensure_ascii=False, indent=2)


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
    fft_filtering_windowsize: int = 2
    fft_factor: float = 0.4
    cfft_multiple: float = 1.0
    sfft_multiple: float = 1.0
    fft_min_frequency_hz: int = 40
    fft_max_frequency_hz: int = 12000
    fft_buffer_seconds: float = 5
    fft_size: int = 4096

    beat_detection_enabled: bool = True
    beat_detection_sensitivity: float = 1.0
    beat_detection_smoothing: float = 0.35
    beat_detection_hop_seconds: float = 0.01
    beat_detection_min_interval: float = 0.18
    beat_detection_low_hz: int = 40
    beat_detection_high_hz: int = 180
    beat_detection_point_threshold: float = 0.25
    beat_detection_visual_flash: bool = True
    beat_detection_visual_lyrics: bool = True

    target_lufs: int = -16

    session: str | None = None
    login_status: dict | None = None
    login_method: Literal['anonymous', 'cell phone', 'QR code', 'cookie'] = 'anonymous'

    stereo: bool = True
    stereo_haas_index: int = 1
    enable_reverb: bool = False
    reverb_intensity: int = 3

    enable_crossfade: bool = True
    crossfade_strength: float = 1
    crossfade_curve: str = 'smart'
    crossfade_max_duration: float = 24.0
    crossfade_tempo_match: bool = True
    crossfade_key_match: bool = True
    crossfade_bpm_window: int = 15
    crossfade_agc: bool = True

    background_ratio: float = 0.4
    volume: float = 1

    lyrics_smooth_factor: float = 0.028
    acceleration_smooth_factor: float = 0.068

    play_speed: float = 1
    play_pitch: float = 0

    show_translation: bool = True
    show_advanced_settings: bool = False
    setting_section_expanded: dict[str, bool] = field(default_factory=dict)

    lyric_video_export_ext: str = '.mp4'
    lyric_video_export_bitrate_kbps: int = 8000
    lyric_video_export_fps: int = 30
    lyric_video_export_display_line_count: int = 5
    lyric_video_export_word_by_word: bool = True
    lyric_video_export_pure_color: bool = False
    lyric_video_export_with_translation: bool = True
    lyric_video_export_alignment: Literal['left', 'center', 'right'] = 'center'
    lyric_video_export_background_color: str = '#00B140'
    lyric_video_export_with_audio: bool = True
    lyric_video_export_scroll_animation: bool = True

    download_concurrent_threads: int = 16
    data_cleanup_enabled: bool = True
    data_cache_max_mb: int = 4096
    data_cache_max_age_minutes: int = 5

    llm_base_url: str = 'https://api.openai.com/v1'
    llm_api_key_encrypted: str = ''
    llm_model: str = ''
    llm_providers: list[dict[str, Any]] = field(default_factory=list)
    llm_current_provider: str = ''
    llm_current_model: str = ''
    llm_viewer_expanded: bool = False
    
    ws_lyrics_interval = 0.032
    ws_fft_interval = 0.032

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


_instance: Config = cast('Config', None)
Config()
cfg = Config.instance()


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


def _songFromObject(data: Any) -> SongStorable | None:
    if not isinstance(data, dict):
        return None
    try:
        return SongStorable.fromObject(data)  # type: ignore[arg-type]
    except Exception as e:
        _logger.exception(e)
        return None


def _normalizeInt(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, result))


def _normalizeOddInt(value: Any, default: int, minimum: int, maximum: int) -> int:
    result = _normalizeInt(value, default, minimum, maximum)
    if result % 2 == 0:
        result += 1 if result < maximum else -1
    return result


def _normalizeHexColor(value: Any, default: str) -> str:
    text = str(value).strip()
    if text.startswith('#'):
        text = text[1:]
    if len(text) != 6:
        return default
    if any(char not in '0123456789abcdefABCDEF' for char in text):
        return default
    return f'#{text.upper()}'


def _applyConfigJsonObject(data: dict[str, Any]) -> None:
    data.pop('animation_speed', None)
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
                _songFromObject(item) for item in data.get('last_playlist', [])
            )
            if song is not None
        ]
    elif 'last_playing_song' in data:
        song = _songFromObject(data.get('last_playing_song'))
        data['last_playlist'] = [song] if song else []
        data['last_playing_index'] = 0 if song else -1
    data.pop('last_playing_song', None)

    if data.get('lyric_video_export_ext') not in ('.mp4', '.av1', '.mkv', '.webm'):
        data['lyric_video_export_ext'] = Config.lyric_video_export_ext
    data['lyric_video_export_bitrate_kbps'] = _normalizeInt(
        data.get('lyric_video_export_bitrate_kbps'),
        Config.lyric_video_export_bitrate_kbps,
        100,
        100000,
    )
    data['lyric_video_export_fps'] = _normalizeInt(
        data.get('lyric_video_export_fps'),
        Config.lyric_video_export_fps,
        10,
        360,
    )
    data['lyric_video_export_fps'] = round(data['lyric_video_export_fps'] / 10) * 10
    data['lyric_video_export_display_line_count'] = _normalizeOddInt(
        data.get('lyric_video_export_display_line_count'),
        Config.lyric_video_export_display_line_count,
        1,
        21,
    )
    if data.get('lyric_video_export_alignment') not in ('left', 'center', 'right'):
        data['lyric_video_export_alignment'] = Config.lyric_video_export_alignment
    data['lyric_video_export_background_color'] = _normalizeHexColor(
        data.get('lyric_video_export_background_color'),
        Config.lyric_video_export_background_color,
    )
    data.pop('lyric_video_export_x_axis_animation', None)
    data['data_cleanup_enabled'] = bool(
        data.get('data_cleanup_enabled', Config.data_cleanup_enabled)
    )
    data['data_cache_max_mb'] = _normalizeInt(
        data.get('data_cache_max_mb'),
        Config.data_cache_max_mb,
        512,
        102400,
    )
    data.pop('data_cache_max_age_days', None)
    data['data_cache_max_age_minutes'] = _normalizeInt(
        data.get('data_cache_max_age_minutes'),
        Config.data_cache_max_age_minutes,
        1,
        525600,
    )

    providers = data.get('llm_providers')
    if isinstance(providers, list):
        data['llm_providers'] = [
            provider
            for provider in (_normalizeLLMProvider(item) for item in providers)
            if provider is not None
        ]
    else:
        data['llm_providers'] = []

    _instance.__dict__.update(data)
    _migrateLegacyLLMConfig()


def _normalizeLLMProvider(data: Any) -> dict[str, Any] | None:
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


def _migrateLegacyLLMConfig() -> None:
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


def _deleteLegacyPickleConfig() -> None:
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
        # Accept config files written by Windows editors with a UTF-8 BOM.
        with open(CONFIG_PATH, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)

        if isinstance(data, dict):
            _applyConfigJsonObject(data)
            _logger.info(f'loaded config {len(_instance.__dict__)=}')
        else:
            _logger.warning('invalid config.json, using defaults')
            saveConfig()

    _deleteLegacyPickleConfig()
