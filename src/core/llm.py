from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
import json
import logging
import threading
import urllib.request

from typing import Any, cast

from openai import OpenAI
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionChunk,
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionUserMessageParam,
)

from core.config import cfg, decryptSecret  # type: ignore[import-not-found]

_logger = logging.getLogger(__name__)

TOOL_USAGE: dict[str, str] = {
    'get_current_song': '''
Purpose:
Read the song that is currently loaded or playing.

Arguments:
- none.

Returns:
- current song identity: id, title, artists, album, source, and duration.
- playback state: playing or paused, current position, volume, and play mode.

Use when:
- the user asks what is playing, asks about this song, or refers to "current song".
- a later action needs a concrete song target and the user did not name one.

Do not use when:
- the user has already provided a specific song name that should be searched.
'''.strip(),
    'search_cloud': '''
Purpose:
Search NetEase Cloud Music from the SearchPage and return the first batch.

Arguments:
- query: search text. Include artist names when the user gave them.

Behavior:
- switch to SearchPage.
- submit the query through the app's normal search flow.
- after results load, return structured song results with stable result handles.
- create an active cloud-search session for continue_search_cloud.

Returns:
- query, result count, and ordered results.
- each result should include handle, title, artists, album, duration, and source id.

Use when:
- the user asks to find cloud songs.
- favorite_song needs a song that is not already represented by a handle.
- building a playlist from song names.

Rules:
- call this before continue_search_cloud.
- pick the best match only when title and artist confidence are high.
- if results are ambiguous, ask the user or search with a more specific query.
'''.strip(),
    'continue_search_cloud': '''
Purpose:
Continue the active cloud search and return more results.

Arguments:
- none.

Precondition:
- search_cloud must have been called successfully in the current search session.

Behavior:
- use the current SearchPage result scroll area.
- call scroll_area.delegate.scrollValue(bar.maximum() - bar.value()).
- wait for the next batch to finish loading.
- return newly loaded results and the total visible result count.

Use when:
- search_cloud did not return a good enough match.
- the user asks for more results.
- a playlist-building workflow needs to keep looking for a specific song.

Rules:
- do not call repeatedly without checking the returned results.
- stop when the target is found, the user has enough choices, or no more results load.
'''.strip(),
    'get_folders': '''
Purpose:
Read favorite folder lists.

Arguments:
- none.

Returns:
- {"local": [...], "cloud": [...]}.
- each folder should include handle, name, type, song count, and source id if any.

Use when:
- the user asks what folders exist.
- favorite_song or remove_song needs a destination/source folder.
- create_favorite_folder might duplicate an existing folder.

Rules:
- use folder handles from this tool for later folder actions.
- if a folder name is ambiguous, ask the user to choose.
'''.strip(),
    'favorite_song': '''
Purpose:
Add one song to a local or cloud favorite folder.

Arguments:
- song: a song handle from search_cloud, continue_search_cloud, or get_current_song.
- folder: a folder handle from get_folders or create_favorite_folder.

Behavior:
- add the song through the app's normal favorite flow.
- switch to the Favorites page.
- open and display the target folder after the song is added.

Use when:
- the user asks to favorite a song.
- the user asks Onerad to build a folder or playlist from song names.

Rules:
- for song names, search_cloud first and favorite only high-confidence matches.
- get_folders first unless the target folder was just created.
- do not silently choose between ambiguous folders or songs.
- summarize successes and unresolved songs after batch operations.
'''.strip(),
    'create_favorite_folder': '''
Purpose:
Create a local or cloud favorite folder.

Arguments:
- name: folder name.
- kind: "local" or "cloud".

Returns:
- created folder handle, name, kind, and source id if any.

Use when:
- the user asks to create a folder or playlist.
- a playlist-building workflow needs a new destination folder.

Rules:
- call get_folders first when duplicate names matter.
- cloud folders may require login; use get_nickname or login if needed.
- after creating, use the returned folder handle for favorite_song.
'''.strip(),
    'read': '''
Purpose:
Read SouthsideMusic source context from the startup-scanned src tree.

Arguments:
- path: folder path such as "src/core" or file path such as
  "src/views/main_window.py".
- offset: optional zero-based line or item offset.
- limit: optional maximum line or item count.

Behavior:
- folders return child files and folders from the in-memory source tree.
- files return content slices with line numbers.
- access is sandboxed to src only.

Use when:
- the user asks about implementation details.
- Onerad needs exact app internals before suggesting or explaining behavior.

Rules:
- never request paths outside src.
- start with a directory read when the relevant file is unknown.
- use offset and limit instead of reading huge files at once.
- this is read-only; it cannot edit files.
'''.strip(),
    'refresh_folders': '''
Purpose:
Refresh the app's folder list.

Arguments:
- none.

Behavior:
- equivalent to clicking the top-left refresh button.
- returns refreshed local and cloud folder summary when available.

Use when:
- folder data may be stale after login, creation, deletion, or sync.
- the user asks to refresh folders.

Rules:
- prefer get_folders for a simple read.
- use refresh_folders when the app UI should actively reload folder data.
'''.strip(),
    'switch_page': '''
Purpose:
Switch the main UI page.

Arguments:
- page: one of "settings", "account", or "search".

Use when:
- the user asks to go to a page.
- a workflow requires the page to be visible before a UI action.

Rules:
- do not use this to open a favorite folder; use open_folder for that.
- search_cloud already switches to the SearchPage by itself.
'''.strip(),
    'open_folder': '''
Purpose:
Open a favorite folder in the Favorites page.

Arguments:
- folder: folder handle from get_folders or create_favorite_folder.

Behavior:
- switch to the Favorites page.
- display the specified local or cloud folder.

Use when:
- the user asks to open a folder or show a playlist.
- favorite_song or remove_song should leave the user looking at the folder.

Rules:
- call get_folders first if you only know a folder name.
- do not use switch_page for folder-specific navigation.
'''.strip(),
    'get_sections': '''
Purpose:
Read Settings page section names and expansion state.

Arguments:
- none.

Returns:
- ordered sections with title, description, and expanded state.

Use when:
- the user asks about settings.
- get_options or set_option_value needs the correct section name.

Rules:
- the LLM section is hidden and must not be returned or targeted.
'''.strip(),
    'scroll_to_section_and_expend': '''
Purpose:
Scroll to a Settings section, center its title, and expand it.

Arguments:
- section: exact section title from get_sections.

Behavior:
- switch to Settings page if needed.
- scroll until the section title is near the middle of the visible area.
- expand the section.

Use when:
- the user asks to show a settings section.
- a settings workflow should visibly focus the section before changing values.

Rules:
- call get_sections first when the exact section title is unknown.
- the LLM section is hidden and must not be targeted.
'''.strip(),
    'get_options': '''
Purpose:
Read setting options inside one Settings section.

Arguments:
- section: exact section title from get_sections.

Returns:
- option name, description, current value, value type, and choices if available.

Use when:
- answering what settings exist in a section.
- set_option_value needs the exact option name or current value.

Rules:
- the LLM section is hidden and must not be returned.
- use exact option names from this tool for set_option_value.
'''.strip(),
    'set_option_value': '''
Purpose:
Set one Settings option value.

Arguments:
- section: exact section title from get_sections.
- option: exact option name from get_options.
- value: value text supplied by the model.
- converter: one of "str", "int", "float", or "bool".

Behavior:
- safely convert value with the named converter.
- set the option through the app's normal setting widget or handler.
- return the old value, new value, and whether the UI accepted it.

Use when:
- the user explicitly asks to change a setting.

Rules:
- call get_sections and get_options first unless exact names and choices are known.
- for choice options, use an exact available choice as value and converter "str".
- for numeric options, respect the range returned by get_options.
- the LLM section is hidden and must not be changed through this tool.
- never treat converter as arbitrary code; it is a safe built-in conversion name.
'''.strip(),
    'get_nickname': '''
Purpose:
Read the current account login status and nickname.

Arguments:
- none.

Returns:
- logged_in flag and nickname when available.

Use when:
- the user asks which account is logged in.
- a cloud action may require login.
'''.strip(),
    'login': '''
Purpose:
Open or trigger the app's account login flow.

Arguments:
- none.

Behavior:
- switch to the Account page.
- perform the same action as clicking the login button.

Use when:
- the user asks to log in.
- a requested cloud operation is blocked by missing login and the user agrees.

Rules:
- do not call login just to inspect account state; use get_nickname.
'''.strip(),
    'remove_song': '''
Purpose:
Remove a song from a favorite folder.

Arguments:
- folder: folder handle from get_folders or open_folder context.
- song: song handle from the folder view or a prior search/current-song result.

Behavior:
- show an in-panel confirmation dialog, not a full-screen popup.
- remove only after the user confirms.
- leave the relevant folder visible.

Use when:
- the user explicitly asks to remove a song from a folder.

Rules:
- this is destructive and always requires confirmation.
- never remove songs as an inferred cleanup step.
- if song or folder is ambiguous, ask before showing confirmation.
'''.strip(),
    'get_tool_usage': '''
Purpose:
Return exact usage instructions for one tool.

Arguments:
- tool_name: exact tool name.

Returns:
- purpose, arguments, behavior, result shape, and safety rules for the tool.

Use when:
- you are unsure how to call a tool.
- the user asks what tools are available or how a specific tool works.

Rules:
- pass an exact tool name when possible.
- if the tool is unknown, report the available tool names.
'''.strip(),
    'get_confirm': '''
Purpose:
Ask the user to confirm a plan before any app-changing tools run.

Arguments:
- plan: short user-facing plan.
- tools: JSON array of tool calls to execute only after approval.

Returns:
- confirmed: false until the user explicitly confirms.
- the plan and pending tools.

Use when:
- the user asks for an action that changes app state.
- the user asks for broad work such as organizing folders or changing settings.
- you have a batch of multiple tools that should be executed together.

Rules:
- first call get_tool_usage for every non-trivial tool you plan to use.
- do not execute action tools before get_confirm returns user approval.
- if the user rejects or is unsure, revise the plan and ask again.
'''.strip(),
}


def getToolUsage(tool_name: str) -> str:
    usage = TOOL_USAGE.get(tool_name)
    if usage is not None:
        return usage
    names = ', '.join(sorted(TOOL_USAGE))
    return f'Unknown tool: {tool_name}. Available tools: {names}'


ONERAD_SYSTEM_PROMPT = '''You are Onerad, the AI assistant inside SouthsideMusic.
You are built to help the user explore music, lyrics, playback, library organization,
local files, and creative listening workflows without getting in the way of the app.
Onerad is your in-app assistant identity. If the user asks whether you are DeepSeek,
OpenAI, or another model/provider, answer honestly: you are Onerad inside
SouthsideMusic, powered by the currently configured model. If you know the provider or
model from context, mention it; if you do not, say the app is using the configured
OpenAI-compatible model. Do not deny the underlying model/provider. Do not explain the
Adreno/Onerad name origin unless the user asks.

Workflow:
1. Understand the user's request.
2. If the request requires app state, use read-only tools first.
3. If the request requires action, call get_tool_usage for each tool you may use,
   then draft a short plan.
4. Ask for confirmation with get_confirm before executing app-changing tools.
5. If the user confirms, execute all needed tools in one batch when possible, then
   summarize results.
6. If the user is unsure or rejects the plan, revise the plan and ask again.

Style:
- Warm, precise, and a little alive.
- Match the user's language.
- Do not start with a markdown heading, table, code fence, list marker, or math block.
- Use markdown only when it improves readability.
- For math, use inline `$...$` or display `$$...$$` formulas.
- Use ```latex fenced code blocks for full LaTeX source code, including requests
  like "write LaTeX", "show LaTeX code", or "give me a LaTeX example".
- Do not leave full LaTeX source code as plain text.
- Do not wrap standalone formulas in ```latex code blocks.
- Close every code, table, and LaTeX delimiter because the UI renders streamed output.

Tool use rules:
- Never invent tool results.
- Use get_tool_usage to learn exact purpose, arguments, behavior, and safety rules.
- Do not assume a tool's exact usage from its name.
- Before any app-changing tool, call get_tool_usage, propose a plan, then call
  get_confirm. Do not execute the action until the user confirms.
- In one assistant turn, you may request multiple tool calls. The app will execute
  them together and return all results.
- Available tool names: get_tool_usage, get_confirm, get_current_song, search_cloud,
  continue_search_cloud, get_folders, favorite_song, create_favorite_folder, read,
  refresh_folders, switch_page, open_folder, get_sections,
  scroll_to_section_and_expend, get_options, set_option_value, get_nickname, login,
  remove_song.'''

LLMMessage = dict[str, str]
ToolRunner = Callable[[str, dict[str, Any]], str]


class LLM:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        system_prompt: str = ONERAD_SYSTEM_PROMPT,
        timeout: float = 60,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt
        self.timeout = timeout

    def listModels(self) -> list[str]:
        return self._parseModelsResponse(self._requestModels())

    def chat(
        self,
        message: str,
        history: Iterable[LLMMessage] | None = None,
    ) -> str:
        response = self._client().chat.completions.create(
            model=self._model(),
            messages=self.buildMessages(message, history),
        )
        content = response.choices[0].message.content
        return content or ''

    def streamChat(
        self,
        message: str,
        history: Iterable[LLMMessage] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_runner: ToolRunner | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[str]:
        _logger.info('start stream request')
        messages = self.buildMessages(message, history)
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return
            stream_kwargs: dict[str, Any] = {
                'model': self._model(),
                'messages': messages,
                'stream': True,
            }
            if tools:
                stream_kwargs['tools'] = tools
            stream = self._client().chat.completions.create(**stream_kwargs)
            chunks = cast(Iterable[ChatCompletionChunk], stream)
            content_parts: list[str] = []
            tool_call_parts: dict[int, dict[str, str]] = {}
            finish_reason = None

            for chunk in chunks:
                if cancel_event is not None and cancel_event.is_set():
                    return
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                finish_reason = choice.finish_reason or finish_reason
                delta = choice.delta
                content = delta.content
                if content:
                    content_parts.append(content)
                    yield content
                for tool_call in delta.tool_calls or []:
                    index = tool_call.index
                    part = tool_call_parts.setdefault(
                        index,
                        {'id': '', 'name': '', 'arguments': ''},
                    )
                    if tool_call.id:
                        part['id'] += tool_call.id
                    if tool_call.function:
                        if tool_call.function.name:
                            part['name'] += tool_call.function.name
                        if tool_call.function.arguments:
                            part['arguments'] += tool_call.function.arguments

            if finish_reason != 'tool_calls' or not tool_call_parts or tool_runner is None:
                return

            tool_call_payloads: list[dict[str, Any]] = []
            for _, part in sorted(tool_call_parts.items()):
                tool_call_payloads.append(
                    {
                        'id': part['id'],
                        'type': 'function',
                        'function': {
                            'name': part['name'],
                            'arguments': part['arguments'],
                        },
                    }
                )
            messages.append(
                cast(
                    ChatCompletionMessageParam,
                    {
                        'role': 'assistant',
                        'content': ''.join(content_parts) or None,
                        'tool_calls': tool_call_payloads,
                    },
                )
            )
            for call_payload in tool_call_payloads:
                function = call_payload['function']
                try:
                    arguments = json.loads(function['arguments'] or '{}')
                except json.JSONDecodeError as e:
                    result = json.dumps({'error': str(e)}, ensure_ascii=False)
                else:
                    result = tool_runner(function['name'], arguments)
                tool_message: ChatCompletionToolMessageParam = {
                    'role': 'tool',
                    'tool_call_id': call_payload['id'],
                    'content': result,
                }
                messages.append(tool_message)

    def buildMessages(
        self,
        message: str,
        history: Iterable[LLMMessage] | None = None,
    ) -> list[ChatCompletionMessageParam]:
        system_message: ChatCompletionSystemMessageParam = {
            'role': 'system',
            'content': self.system_prompt,
        }
        messages: list[ChatCompletionMessageParam] = [system_message]
        if history:
            for item in history:
                role = item.get('role', '')
                content = item.get('content', '')
                if role == 'user' and content:
                    user_message: ChatCompletionUserMessageParam = {
                        'role': 'user',
                        'content': content,
                    }
                    messages.append(user_message)
                elif role == 'assistant' and content:
                    assistant_message: ChatCompletionAssistantMessageParam = {
                        'role': 'assistant',
                        'content': content,
                    }
                    messages.append(assistant_message)

        final_user_message: ChatCompletionUserMessageParam = {
            'role': 'user',
            'content': message,
        }
        messages.append(final_user_message)
        return messages

    def _client(self) -> OpenAI:
        base_url = self._baseUrl()
        api_key = self.api_key or decryptSecret(cfg.llm_api_key_encrypted) or 'unused'
        return OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self.timeout,
        )

    def _baseUrl(self) -> str:
        base_url = (self.base_url or cfg.llm_base_url).strip().rstrip('/')
        if not base_url:
            raise ValueError('LLM Base URL is required')
        return base_url

    def _model(self) -> str:
        model = (self.model or cfg.llm_model).strip()
        if not model:
            raise ValueError('LLM model is required')
        return model

    def _requestModels(self) -> Any:
        url = f'{self._baseUrl()}/models'
        api_key = self.api_key or decryptSecret(cfg.llm_api_key_encrypted)
        headers = {'Accept': 'application/json'}
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'

        request = urllib.request.Request(url, headers=headers, method='GET')
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode('utf-8'))

    def _parseModelsResponse(self, body: Any) -> list[str]:
        if isinstance(body, dict):
            data = body.get('data', body.get('models', []))
        else:
            data = body

        if not isinstance(data, list):
            return []

        models: list[str] = []
        for item in data:
            if isinstance(item, str):
                models.append(item)
            elif isinstance(item, dict):
                model_id = item.get('id') or item.get('name') or item.get('model')
                if isinstance(model_id, str):
                    models.append(model_id)

        return sorted(set(models), key=str.lower)
