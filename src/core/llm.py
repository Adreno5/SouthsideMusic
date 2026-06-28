from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
import json
import logging
import threading
import urllib.error
import urllib.request

from typing import Any, cast

from anthropic import Anthropic
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
    'get_current_song': """
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
""".strip(),
    'get_song_details': """
Purpose:
Read detailed metadata for one song, including style or tag-like fields when the
NetEase detail API returns them.

Arguments:
- song: a song handle from get_current_song, search_cloud, continue_search_cloud,
  or get_folder_songs. A raw song id is also accepted.

Returns:
- song identity, artists, album, duration, publish time, disc/track number.
- style_hints plus raw tag groups such as display_tags, entertainment_tags,
  award_tags, mark_tags, and song_feature.

Use when:
- the user asks about a song's style, genre, tags, vibe, or metadata.
- you need extra context for recommendations or playlist organization.

Rules:
- call get_current_song, search_cloud, or get_folder_songs first when you only
  know the song by user-facing name.
- style_hints may be empty because NetEase often omits genre/style fields.
""".strip(),
    'search_cloud': """
Purpose:
Search NetEase Cloud Music and return the first JSON result batch.

Arguments:
- query: search text. Include artist names when the user gave them.

Behavior:
- query the cloud backend directly.
- do not switch pages, scroll views, or create SearchPage result cards.
- return structured song results with stable result handles.
- create an active cloud-search session for continue_search_cloud.

Returns:
- query, offset, next_offset, result count, and ordered results.
- each result should include handle, title, artists, album, duration, and source id.

Use when:
- the user asks to find cloud songs.
- favorite_song needs a song that is not already represented by a handle.
- building a playlist from song names.

Rules:
- call this before continue_search_cloud.
- pick the best match only when title and artist confidence are high.
- if results are ambiguous, ask the user or search with a more specific query.
""".strip(),
    'continue_search_cloud': """
Purpose:
Continue the active cloud search and return the next JSON result batch.

Arguments:
- none.

Precondition:
- search_cloud must have been called successfully in the current search session.

Behavior:
- use the query and next offset from the last successful search_cloud call.
- query the cloud backend directly.
- do not switch pages, scroll views, or create SearchPage result cards.
- return newly loaded results with stable result handles.

Use when:
- search_cloud did not return a good enough match.
- the user asks for more results.
- a playlist-building workflow needs to keep looking for a specific song.

Rules:
- do not call repeatedly without checking the returned results.
- stop when the target is found, the user has enough choices, or no more results load.
""".strip(),
    'get_folders': """
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
- use get_folder_songs when you need the songs inside a folder.
""".strip(),
    'get_folder_songs': """
Purpose:
Read songs from one favorite folder with pagination.

Arguments:
- folder: a folder handle from get_folders or create_favorite_folder.
- offset: optional zero-based song offset.
- limit: optional maximum song count.

Returns:
- folder summary, offset, limit, total, next_offset, and songs.
- each song includes a handle, id, title, artists, duration, and cached metadata
  when available.

Use when:
- the user asks what songs are in a folder.
- remove_song needs a concrete song handle from a folder.
- you need to inspect a folder without opening it in the UI.

Rules:
- call get_folders first if you only know the folder name.
- use offset and limit for large folders instead of reading everything at once.
- for cloud folders, this may fetch the playlist tracks from the backend.
""".strip(),
    'favorite_song': """
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
""".strip(),
    'create_favorite_folder': """
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
""".strip(),
    'read': """
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
""".strip(),
    'grep': """
Purpose:
Search SouthsideMusic source code with a regular expression.

Arguments:
- path: folder path such as "src/views" or file path such as
  "src/views/main_window.py".
- pattern: Python regular expression.

Behavior:
- if path is a file, search every line in that file.
- if path is a folder, recursively search all startup-scanned files under it.
- return every match with path, line, column, matched text, and full line text.
- access is sandboxed to src only.

Use when:
- the user asks where a feature is implemented.
- the user asks how to use a feature and the relevant file is unknown.
- Onerad needs to locate symbols, labels, event names, settings, or UI actions
  before reading exact code.

Rules:
- call get_tool_usage for grep before using it.
- prefer grep before read when you only know a keyword, UI label, function name,
  class name, event name, or setting name.
- after grep finds candidate files, use read on the best file and line range
  before giving a code-based answer.
- keep regex patterns focused so results stay useful.
- this is read-only; it cannot edit files.
""".strip(),
    'get_language': """
Purpose:
Read the current SouthsideMusic UI language.

Arguments:
- none.

Returns:
- language code, such as "en_US" or "zh_CN".
- translated display name for the current language.

Use when:
- you need to match the app's current UI language.
- source or tool results contain i18n keys and you need translated UI text.

Rules:
- this is read-only.
- use get_translation for actual i18n key translation.
""".strip(),
    'get_translation': """
Purpose:
Translate one SouthsideMusic i18n key in the current UI language.

Arguments:
- key: exact i18n key, such as "setting_page.loudness".

Returns:
- key, current language, translated text, and whether the key was found.

Use when:
- a tool or source-code result contains an i18n key and you need user-facing text.
- explaining settings, sections, options, pages, buttons, or labels.

Rules:
- do not show raw i18n keys like "setting_page.target_lufs" in user-facing
  answers unless the user explicitly asks for the key.
- prefer translated fields such as title_text, name_text, and description_text
  when tools already returned them.
""".strip(),
    'refresh_folders': """
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
""".strip(),
    'switch_page': """
Purpose:
Switch the main UI page.

Arguments:
- page: one of "settings" or "search".

Use when:
- the user asks to go to a page.
- a workflow requires the page to be visible before a UI action.

Rules:
- do not use this to open a favorite folder; use open_folder for that.
- search_cloud does not switch pages; use switch_page only when the user wants to see
  the Search page.
""".strip(),
    'open_folder': """
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
""".strip(),
    'get_sections': """
Purpose:
Read Settings page section names and expansion state.

Arguments:
- none.

Returns:
- ordered sections with translated title, title_text, description_text,
  title_key, description_key, and expanded state.

Use when:
- the user asks about settings.
- get_options or set_option_value needs the correct section name.

Rules:
- the LLM section is hidden and must not be returned or targeted.
- use title or title_text in user-facing answers.
- use title or title_key as the section argument for follow-up setting tools.
- when the user asks where a setting option is, call jump_to_option after
  identifying the option.
""".strip(),
    'jump_to_option': """
Purpose:
Jump to one Settings option, expand its section, and smoothly scroll to it.

Arguments:
- option: option name, translated option name, or name_key from get_options.

Behavior:
- switch to Settings page if needed.
- expand the section containing the option.
- scroll until the option card is near the middle of the visible area.

Use when:
- the user asks to show a concrete settings option.
- the user asks where a setting lives, for example "在哪" or "where is".
- a settings workflow should visibly focus the option before changing values.

Rules:
- call get_sections and get_options first when the exact option is unknown.
- the LLM section is hidden and must not be targeted by this navigation tool.
""".strip(),
    'get_options': """
Purpose:
Read setting options inside one Settings section.

Arguments:
- section: section title or title_key from get_sections.

Returns:
- translated option name, name_text, description_text, name_key, current value,
  value type, and choices if available.

Use when:
- answering what settings exist in a section.
- set_option_value needs the exact option name or current value.

Rules:
- the LLM section is hidden and must not be returned.
- use name or name_text in user-facing answers.
- use name or name_key as the option argument for set_option_value.
""".strip(),
    'set_option_value': """
Purpose:
Set one Settings option value.

Arguments:
- section: section title or title_key from get_sections.
- option: option name or name_key from get_options.
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
""".strip(),
    'get_llm_providers': """
Purpose:
Read configured LLM providers and model mappings.

Arguments:
- none.

Returns:
- current provider and model.
- provider list with api_format, base_url, model ids, display names, and current flag.
- API keys are never returned.

Use when:
- the user asks what model/provider is configured.
- add_llm_provider or set_llm_provider_model may duplicate an existing provider.
""".strip(),
    'fetch_llm_models': """
Purpose:
Fetch model ids from one LLM provider API endpoint.

Arguments:
- api_format: one of "openai_chat", "openai_responses", or "anthropic".
- api_key: provider API key. It is used for this request only and is not returned.
- base_url: provider base URL. Required for OpenAI-compatible providers.

Returns:
- count and models: a list of provider model ids.
- model_mappings: ready-to-pass JSON objects for add_llm_provider.

Use when:
- the user is adding a provider but does not know the supported model ids.
- add_llm_provider needs the models argument and credentials/base URL are known.

Rules:
- call this before asking the user to manually provide model ids when credentials
  and endpoint are available.
- do not expose the API key in the final answer or tool result summary.
""".strip(),
    'add_llm_provider': """
Purpose:
Add one LLM provider to Settings.

Arguments:
- name: provider display name.
- api_format: one of "openai_chat", "openai_responses", or "anthropic".
- api_key: provider API key. It is encrypted before saving.
- base_url: provider base URL. Required for non-Anthropic providers.
- models: JSON array of model mappings. Each item may be a string model id or
  an object with id, display_name, and optional enable_1m_context.

Behavior:
- save the provider through the app config.
- update the Settings provider list and main-window model selector.
- keep the current provider/model unchanged when one already exists.
- select the new provider only when no current provider is configured.

Use when:
- the user asks to add or configure an LLM supplier/provider.
- the user gives enough provider credentials and model mapping details.

Rules:
- call get_llm_providers first to avoid duplicate names.
- never invent API keys, base URLs, or model ids.
- call set_llm_provider_model afterward only if the user explicitly wants to
  switch to the newly added provider.
""".strip(),
    'set_llm_provider_model': """
Purpose:
Select the current LLM provider and model.

Arguments:
- provider: provider name from get_llm_providers or add_llm_provider.
- model: model id under that provider.

Behavior:
- update current provider/model in config.
- refresh the main-window model selector.

Use when:
- the user asks to switch Onerad to a configured model.
""".strip(),
    'get_southside_legacy_connection': """
Purpose:
Read Southside Legacy websocket connection state.

Arguments:
- none.

Returns:
- connected flag, sent/received counters, and latency.

Use when:
- the user asks whether Southside Legacy is connected.
- deciding whether connect_southside_legacy or disconnect_southside_legacy is needed.
""".strip(),
    'connect_southside_legacy': """
Purpose:
Try to connect/start the Southside Legacy websocket bridge.

Arguments:
- none.

Behavior:
- performs the same action as Settings -> connection -> try connect.
- returns the connection state immediately after starting the server.

Use when:
- the user asks to connect or retry Southside Legacy.
""".strip(),
    'disconnect_southside_legacy': """
Purpose:
Disconnect Southside Legacy websocket bridge.

Arguments:
- none.

Behavior:
- performs the same action as Settings -> connection -> disconnect.
- returns the connection state afterward.

Use when:
- the user asks to disconnect Southside Legacy.
""".strip(),
    'reset_desktop_lyrics_position': """
Purpose:
Reset the floating desktop lyrics window position.

Arguments:
- none.

Behavior:
- performs the same action as the reset position button.
- saves the desktop lyrics position as x=0, y=0, anchor="normal".

Use when:
- the user asks to reset desktop lyrics position.
""".strip(),
    'get_nickname': """
Purpose:
Read the current account login status and nickname.

Arguments:
- none.

Returns:
- logged_in flag and nickname when available.

Use when:
- the user asks which account is logged in.
- a cloud action may require login.
""".strip(),
    'login': """
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
""".strip(),
    'remove_song': """
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
""".strip(),
    'get_tool_usage': """
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
""".strip(),
    'get_confirm': """
Purpose:
Ask the user to confirm a plan before a broad, batch, or high-risk workflow.

Arguments:
- plan: short user-facing plan.
- tools: JSON array of tool calls to execute only after approval.

Returns:
- confirmed: false until the user explicitly confirms.
- the plan and pending tools.

Use when:
- the user asks for broad work such as organizing folders.
- you plan to execute multiple app-changing tools as one batch.
- you plan to add, remove, move, or rename multiple songs/folders/playlists.
- the action is destructive or high-risk, especially remove_song.
- the user asks to review a plan before execution.

Do not use when:
- a tool is read-only.
- search, navigation, or refresh is needed to answer the user.
- the user explicitly requested a simple safe action and the needed tool usage
  has already been loaded.

Rules:
- first call get_tool_usage for every pending tool in the plan.
- do not execute pending tools before the user approves the confirmation card.
- do not ask for confirmation only in natural language; call get_confirm with
  the exact pending tools JSON so the app can show an executable confirmation card.
- if the user rejects or is unsure, revise the plan and ask again.
""".strip(),
}


def getToolUsage(tool_name: str) -> str:
    usage = TOOL_USAGE.get(tool_name)
    if usage is not None:
        return usage
    names = ', '.join(sorted(TOOL_USAGE))
    return f'Unknown tool: {tool_name}. Available tools: {names}'


ONERAD_SYSTEM_PROMPT = """You are Onerad, the AI assistant inside SouthsideMusic(南方音乐).
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
2. Before calling any app tool except get_tool_usage or get_confirm, call
   get_tool_usage with that exact tool name.
3. When the user asks how an app feature works, where a control lives, what a
   function/setting/page does, or how to use something in SouthsideMusic, act as
   the app wiki: use grep/read to inspect the source before answering unless the
   answer is already established by tool results in this conversation.
4. When the user asks where a setting, page, folder, or control is, do not only
   describe it. Use a navigation or focus tool when available, such as
   switch_page, open_folder, or jump_to_option, then say what was
   shown.
5. When tool or source results contain i18n keys such as
   "setting_page.target_lufs", use translated fields such as title_text,
   name_text, and description_text, or call get_language/get_translation. In
   user-facing answers, show translated text instead of raw keys unless the user
   explicitly asks for the key.
6. Use read-only, search, navigation, and refresh tools directly when they are
   needed to understand the request or show useful context.
7. For broad planning, batch changes, or destructive/high-risk actions, draft a
   short plan and call get_confirm with the tools to run after approval. This is
   mandatory before adding, removing, moving, or renaming multiple songs,
   folders, or playlists.
8. If the user confirms, execute the pending tools, then summarize results.
9. If the user is unsure or rejects the plan, revise the plan and ask again.

Style:
- Match the user's language.
- Do not start with a markdown heading, table, code fence, list marker, or math block.
- Do not use markdown horizontal rules such as `---`, `***`, or `___`; the UI
  already separates tool cards and answer text with layout spacing.
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
- get_confirm is for plan approval, batch work, and high-risk actions. It is not
  required for every tool call.
- If the next step would run multiple app-changing tool calls, especially many
  favorite_song calls, you must call get_confirm. Do not end with only natural
  language like "要开始添加吗?".
- remove_song is destructive and always requires get_confirm before execution.
- Fields ending in "_key" are internal i18n keys or handles for tool arguments;
  fields ending in "_text" are translated user-facing text.
- For "where is", "在哪", "帮我找到", "跳转到", or "show me" requests, prefer
  a navigation or focus tool over a text-only answer when the app can show it.
- In one assistant turn, you may request multiple tool calls. The app will execute
  them together and return all results.
- Available tool names: get_tool_usage, get_confirm, get_current_song, search_cloud,
  continue_search_cloud, get_song_details, get_folders, get_folder_songs,
  favorite_song, create_favorite_folder, read, grep, get_language,
  get_translation, refresh_folders, switch_page, open_folder, get_sections,
  jump_to_option, get_options, set_option_value, get_llm_providers,
  fetch_llm_models, add_llm_provider, set_llm_provider_model,
  get_southside_legacy_connection, connect_southside_legacy, disconnect_southside_legacy,
  reset_desktop_lyrics_position, get_nickname, login, remove_song."""

LLMMessage = dict[str, str]
ToolRunner = Callable[[str, dict[str, Any]], str]
AfterToolRound = Callable[[], None]


class LLM:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        system_prompt: str = ONERAD_SYSTEM_PROMPT,
        timeout: float = 60,
        api_format: str | None = None,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt
        self.timeout = timeout
        self.api_format = api_format

    def listModels(self) -> list[str]:
        if self._apiFormat() == 'anthropic':
            return self._requestAnthropicModels()
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
        after_tool_round: AfterToolRound | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[str]:
        _logger.info('start stream request')
        if self._apiFormat() == 'openai_responses':
            yield from self._streamResponses(
                message,
                history,
                tools,
                tool_runner,
                after_tool_round,
                cancel_event,
            )
            return
        if self._apiFormat() == 'anthropic':
            yield from self._streamAnthropic(message, history, cancel_event)
            return
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

            if (
                finish_reason != 'tool_calls'
                or not tool_call_parts
                or tool_runner is None
            ):
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
            if after_tool_round is not None:
                after_tool_round()

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
        base_url = self._openAIBaseUrl()
        api_key = self._apiKey() or 'unused'
        return OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self.timeout,
        )

    def _provider(self) -> dict[str, Any] | None:
        name = cfg.llm_current_provider.strip()
        providers = cfg.llm_providers
        if name:
            for provider in providers:
                if str(provider.get('name', '')).strip() == name:
                    return provider
        return providers[0] if providers else None

    def _apiFormat(self) -> str:
        if self.api_format:
            return self.api_format
        provider = self._provider()
        if provider is None:
            return 'openai_chat'
        api_format = str(provider.get('api_format', 'openai_chat'))
        if api_format in ('openai_chat', 'openai_responses', 'anthropic'):
            return api_format
        return 'openai_chat'

    def _apiKey(self) -> str:
        if self.api_key is not None:
            return self.api_key
        provider = self._provider()
        if provider is not None:
            return decryptSecret(str(provider.get('api_key_encrypted', '')))
        return decryptSecret(cfg.llm_api_key_encrypted)

    def _baseUrl(self) -> str:
        provider = self._provider()
        default_base_url = cfg.llm_base_url
        if provider is not None:
            default_base_url = str(provider.get('base_url', ''))
        base_url = (self.base_url or default_base_url).strip().rstrip('/')
        if not base_url:
            raise ValueError('LLM Base URL is required')
        return base_url

    def _openAIBaseUrl(self) -> str:
        base_url = self._baseUrl()
        if base_url.endswith('/v1'):
            return base_url
        return f'{base_url}/v1'

    def _model(self) -> str:
        provider = self._provider()
        default_model = cfg.llm_model
        if provider is not None:
            default_model = cfg.llm_current_model
        model = (self.model or default_model).strip()
        if not model:
            raise ValueError('LLM model is required')
        return model

    def _anthropicModelEnable1mContext(self) -> bool:
        provider = self._provider()
        if provider is None:
            return False
        models = provider.get('models', [])
        if not isinstance(models, list):
            return False
        current_model = self._model()
        for item in models:
            if not isinstance(item, dict):
                continue
            if str(item.get('id', '')).strip() == current_model:
                return bool(item.get('enable_1m_context', False))
        return False

    def _requestModels(self) -> Any:
        base_url = self._baseUrl()
        urls = [f'{base_url}/models']
        if not base_url.endswith('/v1'):
            urls.append(f'{base_url}/v1/models')
        errors: list[Exception] = []
        for url in urls:
            try:
                return self._requestModelsUrl(url)
            except (
                json.JSONDecodeError,
                TimeoutError,
                urllib.error.URLError,
                ValueError,
            ) as e:
                errors.append(e)
                _logger.debug('failed to fetch models from %s: %s', url, e)
        error = self._bestModelsError(errors)
        raise RuntimeError(f'Failed to fetch models: {error}')

    def _bestModelsError(self, errors: list[Exception]) -> Exception | str:
        for error in reversed(errors):
            if isinstance(error, urllib.error.HTTPError):
                return error
        return errors[-1] if errors else 'unknown error'

    def _requestModelsUrl(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> Any:
        api_key = self._apiKey()
        headers = dict(headers or {'Accept': 'application/json'})
        if api_key:
            headers.setdefault('Authorization', f'Bearer {api_key}')

        request = urllib.request.Request(url, headers=headers, method='GET')
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = response.read().decode('utf-8')
            if not body.strip():
                raise ValueError('Empty response')
            return json.loads(body)

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

    def _streamResponses(
        self,
        message: str,
        history: Iterable[LLMMessage] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_runner: ToolRunner | None = None,
        after_tool_round: AfterToolRound | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[str]:
        input_items = [
            {'role': item.get('role', ''), 'content': item.get('content', '')}
            for item in (history or [])
            if item.get('role') in ('user', 'assistant') and item.get('content')
        ]
        input_items.append({'role': 'user', 'content': message})
        response_tools = self._responsesTools(tools or [])
        while True:
            response = None
            stream_kwargs: dict[str, Any] = {
                'model': self._model(),
                'instructions': self.system_prompt,
                'input': input_items,
            }
            if response_tools:
                stream_kwargs['tools'] = response_tools
            with self._client().responses.stream(**stream_kwargs) as stream:
                for event in stream:
                    if cancel_event is not None and cancel_event.is_set():
                        return
                    if event.type == 'response.output_text.delta':
                        yield event.delta
                response = stream.get_final_response()
            tool_outputs = self._responsesToolOutputs(response, tool_runner)
            if not tool_outputs:
                return
            input_items.extend(self._responsesOutputItems(response))
            input_items.extend(tool_outputs)
            if after_tool_round is not None:
                after_tool_round()

    def _responsesTools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        response_tools: list[dict[str, Any]] = []
        for tool in tools:
            if tool.get('type') != 'function':
                continue
            function = tool.get('function', {})
            if not isinstance(function, dict):
                continue
            name = str(function.get('name', '')).strip()
            if not name:
                continue
            response_tools.append(
                {
                    'type': 'function',
                    'name': name,
                    'description': str(function.get('description', '')),
                    'parameters': function.get('parameters', {'type': 'object'}),
                }
            )
        return response_tools

    def _responsesOutputItems(self, response: Any) -> list[dict[str, Any]]:
        output_items: list[dict[str, Any]] = []
        for item in getattr(response, 'output', []) or []:
            item_type = getattr(item, 'type', '')
            if item_type == 'function_call':
                output_items.append(
                    {
                        'type': 'function_call',
                        'call_id': getattr(item, 'call_id', ''),
                        'name': getattr(item, 'name', ''),
                        'arguments': getattr(item, 'arguments', '{}'),
                    }
                )
            elif item_type == 'message':
                content: list[dict[str, str]] = []
                for part in getattr(item, 'content', []) or []:
                    text = getattr(part, 'text', '')
                    if text:
                        content.append({'type': 'output_text', 'text': text})
                if content:
                    output_items.append(
                        {
                            'type': 'message',
                            'role': 'assistant',
                            'content': content,
                        }
                    )
        return output_items

    def _responsesToolOutputs(
        self,
        response: Any,
        tool_runner: ToolRunner | None,
    ) -> list[dict[str, str]]:
        if tool_runner is None:
            return []
        outputs: list[dict[str, str]] = []
        for item in getattr(response, 'output', []) or []:
            if getattr(item, 'type', '') != 'function_call':
                continue
            try:
                arguments = json.loads(getattr(item, 'arguments', '') or '{}')
            except json.JSONDecodeError as e:
                result = json.dumps({'error': str(e)}, ensure_ascii=False)
            else:
                result = tool_runner(getattr(item, 'name', ''), arguments)
            outputs.append(
                {
                    'type': 'function_call_output',
                    'call_id': getattr(item, 'call_id', ''),
                    'output': result,
                }
            )
        return outputs

    def _anthropicClient(self) -> Anthropic:
        api_key = self._apiKey()
        if not api_key:
            raise ValueError('LLM Api Key is required')
        kwargs: dict[str, Any] = {
            'api_key': api_key,
            'timeout': self.timeout,
        }
        if self.base_url:
            kwargs['base_url'] = self._baseUrl()
        elif self._provider() is not None:
            base_url = str(self._provider().get('base_url', '')).strip()  # type: ignore
            if base_url:
                kwargs['base_url'] = base_url.rstrip('/')
        return Anthropic(**kwargs)

    def _streamAnthropic(
        self,
        message: str,
        history: Iterable[LLMMessage] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[str]:
        messages = [
            {'role': item.get('role', ''), 'content': item.get('content', '')}
            for item in (history or [])
            if item.get('role') in ('user', 'assistant') and item.get('content')
        ]
        messages.append({'role': 'user', 'content': message})
        stream_kwargs: dict[str, Any] = {
            'model': self._model(),
            'system': self.system_prompt,
            'messages': messages,
            'max_tokens': 4096,
        }
        if self._anthropicModelEnable1mContext():
            stream_kwargs['extra_headers'] = {
                'anthropic-beta': 'context-1m-2025-08-07',
            }
        with self._anthropicClient().messages.stream(**stream_kwargs) as stream:
            for text in stream.text_stream:
                if cancel_event is not None and cancel_event.is_set():
                    return
                yield text

    def _requestAnthropicModels(self) -> list[str]:
        api_key = self._apiKey()
        if not api_key:
            raise ValueError('LLM Api Key is required')
        base_url = (self.base_url or '').strip().rstrip('/')
        if not base_url and self._provider() is not None:
            base_url = str(self._provider().get('base_url', '')).strip().rstrip('/')  # type: ignore
        if not base_url:
            base_url = 'https://api.anthropic.com/v1'
        urls = [f'{base_url}/models']
        if not base_url.endswith('/v1'):
            urls.append(f'{base_url}/v1/models')
        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {api_key}',
            'anthropic-version': '2023-06-01',
            'x-api-key': api_key,
        }
        errors: list[Exception] = []
        for url in urls:
            try:
                body = self._requestModelsUrl(url, headers)
                return self._parseModelsResponse(body)
            except (
                json.JSONDecodeError,
                TimeoutError,
                urllib.error.URLError,
                ValueError,
            ) as e:
                errors.append(e)
                _logger.debug('failed to fetch Anthropic models from %s: %s', url, e)
        error = self._bestModelsError(errors)
        raise RuntimeError(f'Failed to fetch models: {error}')
