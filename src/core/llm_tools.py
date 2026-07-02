from __future__ import annotations

from dataclasses import dataclass
import json
import os
import queue
import re
from typing import Any, Callable

from core.app_context import AppContext
from core.backend import getBackend
from core.config import cfg, encryptSecret, saveConfig
from core.favorites import favorites_manager
from core.i18n import language
from core.llm import LLM, TOOL_USAGE, getToolUsage
from core.models import (
    CloudFolderInfo,
    LocalFolderInfo,
    SearchSongInfo,
    SongInfo,
    SongStorable,
)
from imports import QApplication, QCheckBox, QComboBox, QThread, QWidget, event_bus, tr
import pyncm
from services.events.events import FAVORITES_CHANGED, MWINDOW_REFRESH_FOLDERS
from views.number_viewer import SettableNumberViewer


ToolCallback = Callable[[str, str, str], None]
PendingCallback = Callable[[str, list[dict[str, Any]]], None]

USAGE_FREE_TOOLS = {'get_tool_usage', 'get_confirm'}
CONFIRM_REQUIRED_TOOLS = {'remove_song'}
KNOWN_TOOLS = set(TOOL_USAGE)
TOOL_SCHEMA_DESCRIPTION = (
    'Available app tool. Call get_tool_usage with this exact name before use.'
)
TOOL_ARG_DESCRIPTION = 'See get_tool_usage for this tool.'


def _schema(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        'type': 'function',
        'function': {
            'name': name,
            'description': description,
            'parameters': {
                'type': 'object',
                'properties': properties,
                'required': [
                    key for key, value in properties.items() if value.get('_required')
                ],
                'additionalProperties': False,
            },
        },
    }


def _prop(
    prop_type: str,
    description: str,
    *,
    enum: list[str] | None = None,
    required: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        'type': prop_type,
        'description': description,
        '_required': required,
    }
    if enum is not None:
        result['enum'] = enum
    return result


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    parameters = schema['function']['parameters']
    for prop in parameters['properties'].values():
        prop.pop('_required', None)
    return schema


def llmToolSchemas() -> list[dict[str, Any]]:
    schemas = [
        _schema(
            'get_tool_usage',
            'Get exact usage for one tool.',
            {
                'tool_name': _prop('string', 'Exact tool name.', required=True),
            },
        ),
        _schema(
            'get_confirm',
            'Ask the user to confirm a plan before actions.',
            {
                'plan': _prop(
                    'string', 'Short action plan shown to the user.', required=True
                ),
                'tools': _prop(
                    'string',
                    'JSON array of tool calls to execute after approval.',
                    required=True,
                ),
            },
        ),
        _schema('get_current_song', TOOL_SCHEMA_DESCRIPTION, {}),
        _schema(
            'get_song_details',
            TOOL_SCHEMA_DESCRIPTION,
            {
                'song': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
            },
        ),
        _schema('get_folders', TOOL_SCHEMA_DESCRIPTION, {}),
        _schema(
            'get_folder_songs',
            TOOL_SCHEMA_DESCRIPTION,
            {
                'folder': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
                'offset': _prop('integer', TOOL_ARG_DESCRIPTION),
                'limit': _prop('integer', TOOL_ARG_DESCRIPTION),
            },
        ),
        _schema(
            'read',
            TOOL_SCHEMA_DESCRIPTION,
            {
                'path': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
                'offset': _prop('integer', TOOL_ARG_DESCRIPTION),
                'limit': _prop('integer', TOOL_ARG_DESCRIPTION),
            },
        ),
        _schema(
            'grep',
            TOOL_SCHEMA_DESCRIPTION,
            {
                'path': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
                'pattern': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
            },
        ),
        _schema('get_language', TOOL_SCHEMA_DESCRIPTION, {}),
        _schema(
            'get_translation',
            TOOL_SCHEMA_DESCRIPTION,
            {
                'key': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
            },
        ),
        _schema('refresh_folders', TOOL_SCHEMA_DESCRIPTION, {}),
        _schema(
            'switch_page',
            TOOL_SCHEMA_DESCRIPTION,
            {
                'page': _prop(
                    'string',
                    TOOL_ARG_DESCRIPTION,
                    enum=['settings', 'account', 'search'],
                    required=True,
                ),
            },
        ),
        _schema(
            'open_folder',
            TOOL_SCHEMA_DESCRIPTION,
            {
                'folder': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
            },
        ),
        _schema(
            'search_cloud',
            TOOL_SCHEMA_DESCRIPTION,
            {
                'query': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
            },
        ),
        _schema('continue_search_cloud', TOOL_SCHEMA_DESCRIPTION, {}),
        _schema(
            'favorite_song',
            TOOL_SCHEMA_DESCRIPTION,
            {
                'song': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
                'folder': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
            },
        ),
        _schema(
            'create_favorite_folder',
            TOOL_SCHEMA_DESCRIPTION,
            {
                'name': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
                'kind': _prop(
                    'string',
                    TOOL_ARG_DESCRIPTION,
                    enum=['local', 'cloud'],
                    required=True,
                ),
            },
        ),
        _schema('get_sections', TOOL_SCHEMA_DESCRIPTION, {}),
        _schema(
            'jump_to_option',
            TOOL_SCHEMA_DESCRIPTION,
            {
                'option': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
            },
        ),
        _schema(
            'get_options',
            TOOL_SCHEMA_DESCRIPTION,
            {
                'section': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
            },
        ),
        _schema(
            'set_option_value',
            TOOL_SCHEMA_DESCRIPTION,
            {
                'section': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
                'option': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
                'value': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
                'converter': _prop(
                    'string',
                    TOOL_ARG_DESCRIPTION,
                    enum=['str', 'int', 'float', 'bool'],
                    required=True,
                ),
            },
        ),
        _schema('get_llm_providers', TOOL_SCHEMA_DESCRIPTION, {}),
        _schema(
            'fetch_llm_models',
            TOOL_SCHEMA_DESCRIPTION,
            {
                'api_format': _prop(
                    'string',
                    TOOL_ARG_DESCRIPTION,
                    enum=['openai_chat', 'openai_responses', 'anthropic'],
                    required=True,
                ),
                'api_key': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
                'base_url': _prop('string', TOOL_ARG_DESCRIPTION),
            },
        ),
        _schema(
            'add_llm_provider',
            TOOL_SCHEMA_DESCRIPTION,
            {
                'name': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
                'api_format': _prop(
                    'string',
                    TOOL_ARG_DESCRIPTION,
                    enum=['openai_chat', 'openai_responses', 'anthropic'],
                    required=True,
                ),
                'api_key': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
                'base_url': _prop('string', TOOL_ARG_DESCRIPTION),
                'models': _prop(
                    'string',
                    TOOL_ARG_DESCRIPTION,
                    required=True,
                ),
            },
        ),
        _schema(
            'set_llm_provider_model',
            TOOL_SCHEMA_DESCRIPTION,
            {
                'provider': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
                'model': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
            },
        ),
        _schema('get_southside_legacy_connection', TOOL_SCHEMA_DESCRIPTION, {}),
        _schema('connect_southside_legacy', TOOL_SCHEMA_DESCRIPTION, {}),
        _schema('disconnect_southside_legacy', TOOL_SCHEMA_DESCRIPTION, {}),
        _schema('reset_desktop_lyrics_position', TOOL_SCHEMA_DESCRIPTION, {}),
        _schema('get_nickname', TOOL_SCHEMA_DESCRIPTION, {}),
        _schema('login', TOOL_SCHEMA_DESCRIPTION, {}),
        _schema(
            'remove_song',
            TOOL_SCHEMA_DESCRIPTION,
            {
                'folder': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
                'song': _prop('string', TOOL_ARG_DESCRIPTION, required=True),
            },
        ),
    ]
    return [_clean_schema(schema) for schema in schemas]


@dataclass
class SourceFile:
    path: str
    lines: list[str]


class SourceTree:
    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)
        self.files: dict[str, SourceFile] = {}
        self.dirs: dict[str, list[str]] = {}
        self.refresh()

    def refresh(self) -> None:
        self.files.clear()
        self.dirs.clear()
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [name for name in dirnames if name != '__pycache__']
            rel_dir = self._to_rel(dirpath)
            children = [f'{name}/' for name in sorted(dirnames)]
            children.extend(sorted(filenames))
            self.dirs[rel_dir] = children
            for filename in filenames:
                full = os.path.join(dirpath, filename)
                rel = self._to_rel(full)
                try:
                    with open(full, 'r', encoding='utf-8') as f:
                        lines = f.read().splitlines()
                except UnicodeDecodeError:
                    lines = ['<binary file>']
                except OSError as e:
                    lines = [f'<failed to read: {e}>']
                self.files[rel] = SourceFile(rel, lines)

    def read(self, path: str, offset: int = 0, limit: int = 80) -> dict[str, Any]:
        rel = self._normalize(path)
        offset = max(0, offset)
        limit = max(1, min(400, limit))
        if rel in self.dirs:
            items = self.dirs[rel]
            return {
                'path': rel,
                'type': 'directory',
                'offset': offset,
                'limit': limit,
                'total': len(items),
                'items': items[offset : offset + limit],
            }
        if rel in self.files:
            source = self.files[rel]
            lines = source.lines[offset : offset + limit]
            return {
                'path': rel,
                'type': 'file',
                'offset': offset,
                'limit': limit,
                'total': len(source.lines),
                'lines': [
                    {'line': offset + index + 1, 'text': text}
                    for index, text in enumerate(lines)
                ],
            }
        return {'path': rel, 'error': 'Path not found under src'}

    def grep(self, path: str, pattern: str) -> dict[str, Any]:
        rel = self._normalize(path)
        if not pattern:
            return {'path': rel, 'pattern': pattern, 'error': 'pattern is required'}
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return {'path': rel, 'pattern': pattern, 'error': f'invalid regex: {e}'}

        files = self._grep_files(rel)
        if files is None:
            return {
                'path': rel,
                'pattern': pattern,
                'error': 'Path not found under src',
            }

        matches = []
        for source in files:
            if source.lines == ['<binary file>']:
                continue
            for line_index, line in enumerate(source.lines):
                for match in regex.finditer(line):
                    matches.append(
                        {
                            'path': source.path,
                            'line': line_index + 1,
                            'column': match.start() + 1,
                            'match': match.group(0),
                            'text': line,
                        }
                    )
        return {
            'path': rel,
            'pattern': pattern,
            'files_scanned': len(files),
            'count': len(matches),
            'matches': matches,
        }

    def _normalize(self, path: str) -> str:
        cleaned = path.replace('\\', '/').strip().strip('/')
        if not cleaned:
            return 'src'
        if cleaned == 'src' or cleaned.startswith('src/'):
            candidate = cleaned
        else:
            candidate = f'src/{cleaned}'
        parts = []
        for part in candidate.split('/'):
            if part in ('', '.'):
                continue
            if part == '..':
                return 'src'
            parts.append(part)
        if not parts or parts[0] != 'src':
            return 'src'
        return '/'.join(parts)

    def _to_rel(self, path: str) -> str:
        rel = os.path.relpath(path, os.path.dirname(self.root))
        return rel.replace('\\', '/')

    def _grep_files(self, rel: str) -> list[SourceFile] | None:
        if rel in self.files:
            return [self.files[rel]]
        if rel not in self.dirs:
            return None
        prefix = rel.rstrip('/') + '/'
        return [
            source
            for path, source in sorted(self.files.items())
            if path.startswith(prefix)
        ]


class LLMToolRunner:
    def __init__(
        self,
        ctx: AppContext,
        callback: ToolCallback | None = None,
        pending_callback: PendingCallback | None = None,
        allow_actions: bool = False,
        require_usage: bool = True,
    ) -> None:
        self.ctx = ctx
        self.callback = callback
        self.pending_callback = pending_callback
        self.allow_actions = allow_actions
        self.require_usage = require_usage
        self.source_tree = SourceTree(os.path.join(os.getcwd(), 'src'))
        self._song_handles = ctx.llm_song_handles
        self._folder_handles = ctx.llm_folder_handles
        self._loaded_usage: set[str] = set()
        self._pending_favorite_folders: set[str] = set()
        self._pending_refresh_folders = False
        self._pending_open_folder: LocalFolderInfo | CloudFolderInfo | None = None
        self._tool_run_index = 0

    def runTool(self, name: str, arguments: dict[str, Any]) -> str:
        self._tool_run_index += 1
        run_id = f'{name}:{self._tool_run_index}'
        self._emit(run_id, name, 'running')
        try:
            result = self._run_tool(name, arguments)
        except Exception as e:
            result = {'error': str(e)}
        text = json.dumps(result, ensure_ascii=False)
        self._emit(run_id, name, text)
        return text

    def _run_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in KNOWN_TOOLS:
            return {'error': f'Unknown tool: {name}'}
        if (
            self.require_usage
            and name not in USAGE_FREE_TOOLS
            and name not in self._loaded_usage
        ):
            return {
                'requires_tool_usage': True,
                'tool_name': name,
                'error': f'Call get_tool_usage with tool_name="{name}" before using this tool.',
            }
        if name in CONFIRM_REQUIRED_TOOLS and not self.allow_actions:
            return {
                'requires_user_confirmation': True,
                'tool_name': name,
                'error': 'Draft a plan and call get_confirm before running this high-risk tool.',
            }
        if name == 'get_tool_usage':
            tool_name = str(arguments.get('tool_name', ''))
            if tool_name in KNOWN_TOOLS:
                self._loaded_usage.add(tool_name)
            return {'tool_name': tool_name, 'usage': getToolUsage(tool_name)}
        if name == 'get_confirm':
            return self.getConfirm(
                str(arguments.get('plan', '')),
                str(arguments.get('tools', '[]')),
            )
        if name == 'get_current_song':
            return self.getCurrentSong()
        if name == 'get_song_details':
            return self.getSongDetails(str(arguments.get('song', '')))
        if name == 'get_folders':
            return self.getFolders()
        if name == 'get_folder_songs':
            return self.getFolderSongs(
                str(arguments.get('folder', '')),
                int(arguments.get('offset', 0) or 0),
                int(arguments.get('limit', 30) or 30),
            )
        if name == 'read':
            return self.read(
                str(arguments.get('path', 'src')),
                int(arguments.get('offset', 0) or 0),
                int(arguments.get('limit', 80) or 80),
            )
        if name == 'grep':
            return self.grep(
                str(arguments.get('path', 'src')),
                str(arguments.get('pattern', '')),
            )
        if name == 'get_language':
            return self.getLanguage()
        if name == 'get_translation':
            return self.getTranslation(str(arguments.get('key', '')))
        if name == 'refresh_folders':
            return self.refreshFolders()
        if name == 'switch_page':
            return self.switchPage(str(arguments.get('page', '')))
        if name == 'open_folder':
            return self.openFolder(str(arguments.get('folder', '')))
        if name == 'search_cloud':
            return self.searchCloud(str(arguments.get('query', '')))
        if name == 'continue_search_cloud':
            return self.continueSearchCloud()
        if name == 'favorite_song':
            return self.favoriteSong(
                str(arguments.get('song', '')),
                str(arguments.get('folder', '')),
            )
        if name == 'create_favorite_folder':
            return self.createFavoriteFolder(
                str(arguments.get('name', '')),
                str(arguments.get('kind', 'local')),
            )
        if name == 'get_sections':
            return self.getSections()
        if name == 'jump_to_option':
            return self.jumpToOption(str(arguments.get('option', '')))
        if name == 'get_options':
            return self.getOptions(str(arguments.get('section', '')))
        if name == 'set_option_value':
            return self.setOptionValue(
                str(arguments.get('section', '')),
                str(arguments.get('option', '')),
                str(arguments.get('value', '')),
                str(arguments.get('converter', 'str')),
            )
        if name == 'get_llm_providers':
            return self.getLlmProviders()
        if name == 'fetch_llm_models':
            return self.fetchLlmModels(
                str(arguments.get('api_format', 'openai_chat')),
                str(arguments.get('api_key', '')),
                str(arguments.get('base_url', '')),
            )
        if name == 'add_llm_provider':
            return self.addLlmProvider(
                str(arguments.get('name', '')),
                str(arguments.get('api_format', 'openai_chat')),
                str(arguments.get('api_key', '')),
                str(arguments.get('base_url', '')),
                str(arguments.get('models', '[]')),
            )
        if name == 'set_llm_provider_model':
            return self.setLlmProviderModel(
                str(arguments.get('provider', '')),
                str(arguments.get('model', '')),
            )
        if name == 'get_southside_legacy_connection':
            return self.getSouthsideLegacyConnection()
        if name == 'connect_southside_legacy':
            return self.connectSouthsideLegacy()
        if name == 'disconnect_southside_legacy':
            return self.disconnectSouthsideLegacy()
        if name == 'reset_desktop_lyrics_position':
            return self.resetDesktopLyricsPosition()
        if name == 'get_nickname':
            return self.getNickname()
        if name == 'login':
            return self.login()
        if name == 'remove_song':
            return {
                'requires_confirmation': True,
                'message': 'remove_song is not enabled until the in-panel confirmation UI is implemented.',
            }
        return {'error': f'Unknown tool: {name}'}

    def getConfirm(self, plan: str, tools: str) -> dict[str, Any]:
        pending_tools = self._parse_pending_tools(tools)
        missing_usage = [
            str(item.get('name', ''))
            for item in pending_tools
            if (
                self.require_usage
                and str(item.get('name', '')) not in USAGE_FREE_TOOLS
                and str(item.get('name', '')) not in self._loaded_usage
            )
        ]
        if missing_usage:
            return {
                'requires_tool_usage': True,
                'missing_tools': sorted(set(missing_usage)),
                'error': 'Call get_tool_usage for every pending tool before get_confirm.',
            }
        if self.pending_callback is not None:
            self.pending_callback(plan, pending_tools)
        return {
            'requires_user_confirmation': True,
            'confirmed': False,
            'plan': plan,
            'tools': pending_tools,
            'message': 'Show this plan to the user. Execute the listed tools only after the user confirms.',
        }

    def getCurrentSong(self) -> dict[str, Any]:
        pm = self.ctx.playing_manager
        player = self.ctx.player
        song = pm.current_song if pm else None
        if song is None and pm and 0 <= pm.current_index < len(pm.playlist):
            song = pm.playlist[pm.current_index]
        if song is None:
            return {'has_song': False}
        handle = self._songHandle(song)
        return {
            'has_song': True,
            'song': self._songToDict(song, handle),
            'playback': {
                'playing': player.isPlaying(),
                'position': player.getPosition(),
                'duration': player.getLength(),
                'volume': player.volume_gain,
                'play_mode': pm.play_mode if pm else cfg.play_method,
            },
        }

    def getSongDetails(self, song: str) -> dict[str, Any]:
        song_id = self._songIdFromHandleOrId(song)
        if not song_id:
            return {'error': 'song handle or id is required'}
        detail = getBackend().getTrackDetail(song_id)
        style_hints = self._styleHints(detail)
        return {
            'id': str(song_id),
            'title': detail.name,
            'artists': [artist.name for artist in detail.artists],
            'album': detail.album_name,
            'duration': detail.duration,
            'publish_time': detail.publish_time,
            'track_no': detail.track_no,
            'cd': detail.cd,
            'aliases': detail.aliases,
            'style_hints': style_hints,
            'display_tags': detail.display_tags,
            'entertainment_tags': detail.entertainment_tags,
            'award_tags': detail.award_tags,
            'mark_tags': detail.mark_tags,
            'song_feature': detail.song_feature,
        }

    def getFolders(self) -> dict[str, Any]:
        local = []
        for index, local_folder in enumerate(favorites_manager.folders):
            handle = f'local:{index}:{local_folder.folder_name}'
            self._folder_handles[handle] = local_folder
            local.append(
                {
                    'handle': handle,
                    'name': local_folder.folder_name,
                    'kind': 'local',
                    'song_count': len(local_folder.songs),
                }
            )
        cloud = []
        if getBackend().loggedIn():
            for index, cloud_folder in enumerate(getBackend().getUserPlaylists()):
                handle = f'cloud:{index}:{cloud_folder.id}'
                self._folder_handles[handle] = cloud_folder
                cloud.append(
                    {
                        'handle': handle,
                        'name': cloud_folder.folder_name,
                        'kind': 'cloud',
                        'song_count': self._cloudSongCount(cloud_folder),
                        'id': cloud_folder.id,
                    }
                )
        return {'local': local, 'cloud': cloud}

    def getFolderSongs(
        self,
        folder: str,
        offset: int = 0,
        limit: int = 30,
    ) -> dict[str, Any]:
        folder_obj = self._folder_handles.get(folder)
        if folder_obj is None:
            self.getFolders()
            folder_obj = self._folder_handles.get(folder)
        if folder_obj is None:
            return {'error': f'folder handle not found: {folder}'}

        offset = max(0, offset)
        limit = max(1, min(100, limit))
        if isinstance(folder_obj, CloudFolderInfo):
            songs = getBackend().getPlaylistTracks(folder_obj.id)
            if self._cloudSongCount(folder_obj) is None:
                self._setCloudSongCount(folder_obj, len(songs))
        else:
            songs = folder_obj.songs

        sliced = songs[offset : offset + limit]
        return {
            'folder': self._folderToDict(folder, folder_obj),
            'offset': offset,
            'limit': limit,
            'total': len(songs),
            'next_offset': offset + len(sliced),
            'songs': [
                self._songToDict(song, self._songHandle(song)) for song in sliced
            ],
        }

    def read(self, path: str, offset: int = 0, limit: int = 80) -> dict[str, Any]:
        return self.source_tree.read(path, offset, limit)

    def grep(self, path: str, pattern: str) -> dict[str, Any]:
        return self.source_tree.grep(path, pattern)

    def getLanguage(self) -> dict[str, Any]:
        current_language = language()
        return {
            'language': current_language,
            'text': tr(f'language.{current_language}'),
        }

    def getTranslation(self, key: str) -> dict[str, Any]:
        key = key.strip()
        if not key:
            return {'error': 'key is required'}
        text = tr(key)
        return {
            'key': key,
            'language': language(),
            'text': text,
            'found': text != key,
        }

    def refreshFolders(self) -> dict[str, Any]:
        self._run_main_thread(lambda: self.ctx.main_window.refreshFolders())
        return self.getFolders()

    def switchPage(self, page: str) -> dict[str, Any]:
        def _switch() -> str:
            mw = self.ctx.main_window
            if page == 'settings':
                mw.contents_widget.setCurrentWidget(self.ctx.setting_page)
            elif page == 'search':
                mw.contents_widget.setCurrentWidget(self.ctx.search_page)
            else:
                return 'unknown page'
            return 'ok'

        return {'page': page, 'status': self._run_main_thread(_switch)}

    def openFolder(self, folder: str) -> dict[str, Any]:
        folder_obj = self._folder_handles.get(folder)
        if folder_obj is None:
            self.getFolders()
            folder_obj = self._folder_handles.get(folder)
        if folder_obj is None:
            return {'error': f'folder handle not found: {folder}'}
        self._run_main_thread(lambda: self.ctx.main_window._openFolder(folder_obj))
        return {'opened': self._folder_to_dict(folder, folder_obj)}

    def searchCloud(self, query: str) -> dict[str, Any]:
        query = query.strip()
        if not query:
            return {'error': 'query is required'}
        results = getBackend().searchSong(query, offset=0)
        self.ctx.llm_cloud_search_query = query
        self.ctx.llm_cloud_search_offset = len(results)
        return {
            'query': query,
            'offset': 0,
            'next_offset': self.ctx.llm_cloud_search_offset,
            'count': len(results),
            'results': [self._searchSongToDict(song) for song in results],
        }

    def continueSearchCloud(self) -> dict[str, Any]:
        query = self.ctx.llm_cloud_search_query
        offset = self.ctx.llm_cloud_search_offset
        if not query:
            return {'error': 'search_cloud must be called first'}
        results = getBackend().searchSong(query, offset=offset)
        self.ctx.llm_cloud_search_offset += len(results)
        return {
            'query': query,
            'offset': offset,
            'next_offset': self.ctx.llm_cloud_search_offset,
            'count': len(results),
            'results': [self._searchSongToDict(song) for song in results],
        }

    def favoriteSong(self, song: str, folder: str) -> dict[str, Any]:
        song_obj = self._song_handles.get(song)
        folder_obj = self._folder_handles.get(folder)
        if song_obj is None:
            return {'error': f'song handle not found: {song}'}
        if folder_obj is None:
            self.getFolders()
            folder_obj = self._folder_handles.get(folder)
        if folder_obj is None:
            return {'error': f'folder handle not found: {folder}'}

        storable = self._toStorable(song_obj)
        if isinstance(folder_obj, CloudFolderInfo):
            ok = getBackend().editPlaylist('add', [str(storable.id)], folder_obj.id)
            if not ok:
                return {'error': 'cloud favorite failed'}
        else:
            if not favorites_manager.addSong(folder_obj.folder_name, storable):
                return {'error': 'local favorite failed'}
            self._pending_favorite_folders.add(folder_obj.folder_name)

        self._pending_refresh_folders = True
        self._pending_open_folder = folder_obj
        return {
            'favorited': self._songToDict(storable, song),
            'folder': self._folderToDict(folder, folder_obj),
        }

    def createFavoriteFolder(self, name: str, kind: str) -> dict[str, Any]:
        if not name.strip():
            return {'error': 'folder name is required'}
        if kind == 'cloud':
            folder_id = getBackend().createPlaylist(name)
            folder_obj: LocalFolderInfo | CloudFolderInfo = CloudFolderInfo(
                folder_name=name,
                image_url='',
                id=folder_id,
            )
            handle = f'cloud:new:{folder_id}'
        else:
            folder_obj = favorites_manager.addFolder(name)
            handle = f'local:new:{folder_obj.folder_name}'
        self._folder_handles[handle] = folder_obj
        self._pending_refresh_folders = True
        self._pending_open_folder = folder_obj
        return {'created': self._folderToDict(handle, folder_obj)}

    def flushPostActions(self) -> None:
        favorite_folders = sorted(self._pending_favorite_folders)
        refresh_folders = self._pending_refresh_folders
        open_folder = self._pending_open_folder
        if not favorite_folders and not refresh_folders and open_folder is None:
            return
        self._pending_favorite_folders.clear()
        self._pending_refresh_folders = False
        self._pending_open_folder = None

        def _apply() -> None:
            for folder_name in favorite_folders:
                event_bus.emit(FAVORITES_CHANGED, folder_name)
            if refresh_folders:
                event_bus.emit(MWINDOW_REFRESH_FOLDERS)
            if open_folder is not None:
                self.ctx.main_window._openFolder(open_folder)

        self._run_main_thread(_apply)

    def getSections(self) -> dict[str, Any]:
        return self._run_main_thread(self._get_sections)

    def jumpToOption(self, option: str) -> dict[str, Any]:
        def _show() -> None:
            found = self._find_option_anywhere(option)
            if found is None:
                return
            target, card = found
            self.ctx.main_window.contents_widget.setCurrentWidget(self.ctx.setting_page)
            target.setExpanded(True)
            target.refreshContentHeight()
            scroller = self.ctx.setting_page.scroller
            card_y = card.mapTo(
                self.ctx.setting_page.options_widget, card.rect().topLeft()
            ).y()
            center = card_y - scroller.viewport().height() // 2 + card.height() // 2
            bar = scroller.verticalScrollBar()
            value = max(0, min(bar.maximum(), center))
            delegate = getattr(scroller, 'delegate', None)
            smooth_bar = getattr(delegate, 'vScrollBar', None)
            if smooth_bar is not None:
                smooth_bar.scrollValue(value - smooth_bar.value())
            else:
                bar.setValue(value)

        found = self._run_main_thread(lambda: self._find_option_anywhere(option))
        if found is None:
            return {'error': f'option not found: {option}'}
        section, card = found
        if section.title == 'setting_page.llm':
            return {'error': 'LLM section is hidden from navigation tools'}
        self._run_main_thread(_show)
        return {
            'option': option,
            'option_key': getattr(card, '_llm_setting_name', ''),
            'option_text': tr(getattr(card, '_llm_setting_name', '')),
            'section_key': section.title,
            'section_text': tr(section.title),
            'expanded': True,
        }

    def getOptions(self, section: str) -> dict[str, Any]:
        return self._run_main_thread(lambda: self._get_options(section))

    def setOptionValue(
        self,
        section: str,
        option: str,
        value: str,
        converter: str,
    ) -> dict[str, Any]:
        converted = self._convert(value, converter)
        return self._run_main_thread(
            lambda: self._set_option_value(section, option, converted)
        )

    def getLlmProviders(self) -> dict[str, Any]:
        return self._run_main_thread(self._get_llm_providers)

    def fetchLlmModels(
        self,
        api_format: str,
        api_key: str,
        base_url: str,
    ) -> dict[str, Any]:
        return self._fetch_llm_models(api_format, api_key, base_url)

    def addLlmProvider(
        self,
        name: str,
        api_format: str,
        api_key: str,
        base_url: str,
        models: str,
    ) -> dict[str, Any]:
        return self._run_main_thread(
            lambda: self._add_llm_provider(name, api_format, api_key, base_url, models)
        )

    def setLlmProviderModel(self, provider: str, model: str) -> dict[str, Any]:
        return self._run_main_thread(
            lambda: self._set_llm_provider_model(provider, model)
        )

    def getSouthsideLegacyConnection(self) -> dict[str, Any]:
        return self._run_main_thread(self._get_southside_legacy_connection)

    def connectSouthsideLegacy(self) -> dict[str, Any]:
        self._run_main_thread(self.ctx.setting_page.connectToSouthsideClient)
        return self.getSouthsideLegacyConnection()

    def disconnectSouthsideLegacy(self) -> dict[str, Any]:
        self._run_main_thread(self.ctx.setting_page.disconnectFromSouthsideClient)
        return self.getSouthsideLegacyConnection()

    def resetDesktopLyricsPosition(self) -> dict[str, Any]:
        def _reset() -> None:
            self.ctx.desktop_lyrics_page.onResetPos()
            cfg.desktop_lyrics_x = 0
            cfg.desktop_lyrics_y = 0
            cfg.desktop_lyrics_anchor = 'normal'
            saveConfig()

        self._run_main_thread(_reset)
        return {
            'reset': True,
            'x': cfg.desktop_lyrics_x,
            'y': cfg.desktop_lyrics_y,
            'anchor': cfg.desktop_lyrics_anchor,
        }

    def getNickname(self) -> dict[str, Any]:
        return self._run_main_thread(self._get_nickname)

    def _get_nickname(self) -> dict[str, Any]:
        backend = getBackend()
        return {
            'logged_in': not backend.loggedIn(),
            'nickname': pyncm.getCurrentSession().nickname,
            'vip_level': pyncm.getCurrentSession().vipType,
        }

    def _get_llm_providers(self) -> dict[str, Any]:
        providers = []
        for provider in cfg.llm_providers:
            models = provider.get('models', [])
            model_items = models if isinstance(models, list) else []
            providers.append(
                {
                    'name': str(provider.get('name', '')),
                    'api_format': str(provider.get('api_format', 'openai_chat')),
                    'base_url': str(provider.get('base_url', '')),
                    'models': [
                        {
                            'id': str(item.get('id', '')),
                            'display_name': str(item.get('display_name', '')),
                            'enable_1m_context': bool(
                                item.get('enable_1m_context', False)
                            ),
                        }
                        for item in model_items
                        if isinstance(item, dict)
                    ],
                    'current': str(provider.get('name', ''))
                    == cfg.llm_current_provider,
                }
            )
        return {
            'current_provider': cfg.llm_current_provider,
            'current_model': cfg.llm_current_model,
            'providers': providers,
        }

    def _fetch_llm_models(
        self,
        api_format: str,
        api_key: str,
        base_url: str,
    ) -> dict[str, Any]:
        api_format = api_format.strip()
        base_url = base_url.strip().rstrip('/')
        if api_format not in ('openai_chat', 'openai_responses', 'anthropic'):
            return {'error': f'unsupported api_format: {api_format}'}
        if api_format != 'anthropic' and not base_url:
            return {'error': 'base_url is required for this api_format'}
        models = LLM(
            base_url=base_url,
            api_key=api_key.strip(),
            api_format=api_format,
            timeout=20,
        ).listModels()
        return {
            'api_format': api_format,
            'base_url': base_url,
            'count': len(models),
            'models': models,
            'model_mappings': [
                {'id': model, 'display_name': model} for model in models
            ],
        }

    def _add_llm_provider(
        self,
        name: str,
        api_format: str,
        api_key: str,
        base_url: str,
        models_text: str,
    ) -> dict[str, Any]:
        name = name.strip()
        if not name:
            return {'error': 'provider name is required'}
        if api_format not in ('openai_chat', 'openai_responses', 'anthropic'):
            return {'error': f'unsupported api_format: {api_format}'}
        if api_format != 'anthropic' and not base_url.strip():
            return {'error': 'base_url is required for this api_format'}
        models = self._parse_llm_models(models_text)
        if not models:
            return {'error': 'models must include at least one model mapping'}
        existing_names = {
            str(provider.get('name', '')).strip() for provider in cfg.llm_providers
        }
        if name in existing_names:
            return {'error': f'provider already exists: {name}'}

        current_provider_exists = any(
            str(provider.get('name', '')).strip() == cfg.llm_current_provider
            for provider in cfg.llm_providers
        )
        should_select = not cfg.llm_current_provider or not current_provider_exists

        provider = {
            'name': name,
            'api_format': api_format,
            'api_key_encrypted': encryptSecret(api_key.strip()),
            'base_url': base_url.strip().rstrip('/'),
            'models': models,
        }
        cfg.llm_providers.append(provider)
        if should_select:
            cfg.llm_current_provider = name
            cfg.llm_current_model = str(models[0]['id'])
        self.ctx.setting_page._syncLegacyLlmConfig()
        saveConfig()
        self.ctx.setting_page._refreshLlmProvidersView()
        self.ctx.setting_page._refreshMainWindowLlmModels()
        return {
            'added': True,
            'provider': name,
            'selected_as_current': should_select,
            'current_provider': cfg.llm_current_provider,
            'current_model': cfg.llm_current_model,
            'model_count': len(models),
        }

    def _set_llm_provider_model(self, provider: str, model: str) -> dict[str, Any]:
        provider = provider.strip()
        model = model.strip()
        for item in cfg.llm_providers:
            if str(item.get('name', '')).strip() != provider:
                continue
            models = item.get('models', [])
            if not isinstance(models, list):
                return {'error': f'provider has no models: {provider}'}
            model_ids = [
                str(model_item.get('id', '')).strip()
                for model_item in models
                if isinstance(model_item, dict)
            ]
            if model not in model_ids:
                return {'error': f'model not found for provider {provider}: {model}'}
            cfg.llm_current_provider = provider
            cfg.llm_current_model = model
            self.ctx.setting_page._syncLegacyLlmConfig()
            saveConfig()
            self.ctx.setting_page._refreshMainWindowLlmModels()
            return {'provider': provider, 'model': model, 'selected': True}
        return {'error': f'provider not found: {provider}'}

    def _parse_llm_models(self, models_text: str) -> list[dict[str, object]]:
        try:
            raw = json.loads(models_text)
        except json.JSONDecodeError:
            raw = [
                {'id': item.strip(), 'display_name': item.strip()}
                for item in models_text.split(',')
                if item.strip()
            ]
        if isinstance(raw, dict):
            raw = raw.get('models', [])
        if not isinstance(raw, list):
            return []

        models: list[dict[str, object]] = []
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        for item in raw:
            if isinstance(item, str):
                model_id = item.strip()
                display_name = model_id
                enable_1m_context = False
            elif isinstance(item, dict):
                model_id = str(item.get('id', '')).strip()
                display_name = str(item.get('display_name', model_id)).strip()
                enable_1m_context = bool(item.get('enable_1m_context', False))
            else:
                continue
            if not model_id or not display_name:
                continue
            if model_id in seen_ids or display_name in seen_names:
                continue
            seen_ids.add(model_id)
            seen_names.add(display_name)
            models.append(
                {
                    'id': model_id,
                    'display_name': display_name,
                    'enable_1m_context': enable_1m_context,
                }
            )
        return models

    def _get_southside_legacy_connection(self) -> dict[str, Any]:
        handler = self.ctx.ws_handler
        return {
            'connected': handler.is_open,
            'sent_mb': handler.sent,
            'received_kb': handler.received,
            'latency_ms': handler.ping,
        }

    def login(self) -> dict[str, Any]:
        self._run_main_thread(lambda: self.ctx.main_window.login())
        return self.getNickname()

    def _get_sections(self) -> dict[str, Any]:
        sections = []
        for section in self.ctx.setting_page._sections:
            if section.title == 'setting_page.llm':
                continue
            title_text = tr(section.title)
            description_text = tr(section._title)
            sections.append(
                {
                    'title': title_text,
                    'title_text': title_text,
                    'title_key': section.title,
                    'description': description_text,
                    'description_text': description_text,
                    'description_key': section._title,
                    'expanded': section.isExpanded(),
                }
            )
        return {'sections': sections}

    def _has_section(self, section: str) -> bool:
        target = self._find_section(section)
        return target is not None and target.title != 'setting_page.llm'

    def _get_options(self, section: str) -> dict[str, Any]:
        target = self._find_section(section)
        if target is None:
            return {'error': f'section not found: {section}'}
        if target.title == 'setting_page.llm':
            return {'error': 'LLM section is hidden from tools'}
        options = []
        for widget in self._section_option_cards(target):
            name = getattr(widget, '_llm_setting_name', '')
            desc = getattr(widget, '_llm_setting_description', '')
            value_widget = getattr(widget, '_llm_setting_widget', None)
            value = self._widget_value(value_widget)
            name_text = tr(name)
            description_text = tr(desc)
            options.append(
                {
                    'name': name_text,
                    'name_text': name_text,
                    'name_key': name,
                    'description': description_text,
                    'description_text': description_text,
                    'description_key': desc,
                    'value': value,
                    'value_type': type(value).__name__,
                    'choices': self._widget_choices(value_widget),
                }
            )
        return {'section': section, 'options': options}

    def _set_option_value(
        self,
        section: str,
        option: str,
        converted: Any,
    ) -> dict[str, Any]:
        target = self._find_section(section)
        if target is None or target.title == 'setting_page.llm':
            return {'error': 'section not found or hidden'}
        card = self._find_option_card(target, option)
        if card is None:
            return {'error': f'option not found: {option}'}
        widget = getattr(card, '_llm_setting_widget', None)
        old = self._widget_value(widget)
        self._set_widget_value(widget, converted)
        return {
            'section': section,
            'option': option,
            'old_value': old,
            'new_value': self._widget_value(widget),
        }

    def _searchSongToDict(self, song: SearchSongInfo) -> dict[str, Any]:
        handle = self._songHandle(song)
        return {
            'handle': handle,
            'id': str(song.id),
            'title': song.name,
            'artists': [artist.name for artist in song.artists],
            'album': song.album.name,
            'duration': song.duration,
        }

    def _songToDict(
        self,
        song: SongStorable | SearchSongInfo,
        handle: str,
    ) -> dict[str, Any]:
        if isinstance(song, SearchSongInfo):
            return self._searchSongToDict(song)
        return {
            'handle': handle,
            'id': str(song.id),
            'title': song.name,
            'artists': [artist.name for artist in song.artists],
            'duration': song.duration,
        }

    def _folderToDict(
        self,
        handle: str,
        folder: LocalFolderInfo | CloudFolderInfo,
    ) -> dict[str, Any]:
        if isinstance(folder, CloudFolderInfo):
            return {
                'handle': handle,
                'kind': 'cloud',
                'name': folder.folder_name,
                'id': folder.id,
                'song_count': self._cloudSongCount(folder),
            }
        return {
            'handle': handle,
            'kind': 'local',
            'name': folder.folder_name,
            'song_count': len(folder.songs),
        }

    def _cloudSongCount(self, folder: CloudFolderInfo) -> int | None:
        count = getattr(folder, 'song_count', None)
        return count if isinstance(count, int) else None

    def _setCloudSongCount(self, folder: CloudFolderInfo, count: int) -> None:
        setattr(folder, 'song_count', count)

    def _songIdFromHandleOrId(self, song: str) -> str:
        song = song.strip()
        song_obj = self._song_handles.get(song)
        if song_obj is not None:
            return str(song_obj.id)
        if song.startswith('song:'):
            return song.removeprefix('song:').strip()
        return song

    def _styleHints(self, detail: Any) -> list[str]:
        hints: list[str] = []
        for item in (
            *detail.display_tags,
            *detail.entertainment_tags,
            *detail.award_tags,
            *detail.mark_tags,
        ):
            if item and item not in hints:
                hints.append(item)
        feature = detail.song_feature
        if isinstance(feature, str) and feature and feature not in hints:
            hints.append(feature)
        return hints

    def _songHandle(self, song: SearchSongInfo | SongStorable) -> str:
        song_id = str(song.id)
        handle = f'song:{song_id}'
        self._song_handles[handle] = song
        return handle

    def _toStorable(self, song: SearchSongInfo | SongStorable) -> SongStorable:
        if isinstance(song, SongStorable):
            return song
        return SongStorable(
            SongInfo(
                name=song.name,
                artists=song.artists,
                id=str(song.id),
                privilege=song.privilege.fee,
                duration=song.duration,
            )
        )

    def _find_section(self, section: str) -> Any | None:
        for item in self.ctx.setting_page._sections:
            if item.title == section or tr(item.title) == section:
                return item
        return None

    def _section_option_cards(self, section: Any) -> list[QWidget]:
        result = []
        layout = section.content_layout
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget()
            if widget is not None and hasattr(widget, '_llm_setting_name'):
                result.append(widget)
        return result

    def _find_option_card(self, section: Any, option: str) -> QWidget | None:
        for card in self._section_option_cards(section):
            name = getattr(card, '_llm_setting_name', '')
            if name == option or tr(name) == option:
                return card
        return None

    def _find_option_anywhere(self, option: str) -> tuple[Any, QWidget] | None:
        option = option.strip()
        for section in self.ctx.setting_page._sections:
            card = self._find_option_card(section, option)
            if card is not None:
                return section, card
        return None

    def _widget_value(self, widget: Any) -> Any:
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            data = widget.currentData()
            return data if data is not None else widget.currentText()
        if isinstance(widget, SettableNumberViewer):
            return widget.value
        if hasattr(widget, 'text'):
            return widget.text()
        if hasattr(widget, 'cur_text'):
            return widget.cur_text
        return None

    def _widget_choices(self, widget: Any) -> list[str]:
        if isinstance(widget, QComboBox):
            return [
                str(widget.itemData(index) or widget.itemText(index))
                for index in range(widget.count())
            ]
        return []

    def _set_widget_value(self, widget: Any, value: Any) -> None:
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, QComboBox):
            index = widget.findData(value)
            if index < 0:
                index = widget.findText(str(value))
            if index >= 0:
                widget.setCurrentIndex(index)
        elif isinstance(widget, SettableNumberViewer):
            widget.setValue(float(value))
            widget.valueChanged.emit(widget.value)
        elif hasattr(widget, 'setText'):
            widget.setText(str(value))

    def _convert(self, value: str, converter: str) -> Any:
        if converter == 'int':
            return int(value)
        if converter == 'float':
            return float(value)
        if converter == 'bool':
            return value.strip().lower() in ('1', 'true', 'yes', 'on', 'checked')
        return value

    def _run_main_thread(self, fn: Callable[[], Any]) -> Any:
        app = QApplication.instance()
        if app is not None and QThread.currentThread() == app.thread():
            return fn()

        result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def _wrapped() -> None:
            try:
                result_queue.put((True, fn()))
            except Exception as e:
                result_queue.put((False, e))

        self.ctx.addScheduledTask(_wrapped)
        ok, result = result_queue.get(timeout=30)
        if ok:
            return result
        raise result

    def _emit(self, run_id: str, name: str, content: str) -> None:
        if self.callback is not None:
            self.callback(run_id, name, content)

    def _parse_pending_tools(self, tools: str) -> list[dict[str, Any]]:
        text = tools.strip()
        fenced = re.search(r'```(?:json)?\s*(.*?)```', text, re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return []
        if isinstance(raw, dict):
            for key in ('tools', 'tool_calls', 'calls', 'actions'):
                value = raw.get(key)
                if isinstance(value, list):
                    raw = value
                    break
        if not isinstance(raw, list):
            return []

        result: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get('name') or item.get('tool')
            arguments = item.get('arguments') or item.get('args') or {}
            function = item.get('function')
            if isinstance(function, dict):
                name = name or function.get('name')
                arguments = function.get('arguments', arguments)
            if 'parameters' in item and not arguments:
                arguments = item.get('parameters')
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            if isinstance(name, str) and isinstance(arguments, dict):
                result.append({'name': name, 'arguments': arguments})
        return result
