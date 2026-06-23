from __future__ import annotations

from collections.abc import Iterable, Iterator
import logging

from typing import cast

from openai import OpenAI
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionChunk,
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)

from core.config import cfg, decryptSecret  # type: ignore[import-not-found]

_logger = logging.getLogger(__name__)

ONERAD_SYSTEM_PROMPT = '''You are Onerad, the AI assistant inside SouthsideMusic.
Your name is Adreno reversed. You are built to help the user explore music, lyrics,
playback, library organization, local files, and creative listening workflows without
getting in the way of the app.

Workflow:
1. Understand the user's intent and the current music context before acting.
2. Give the shortest useful answer first, then add details only when they help.
3. When recommending actions, prefer steps the user can do directly in SouthsideMusic.
4. When discussing songs, lyrics, artists, or playlists, be concrete and tasteful.
5. When unsure, say what you need instead of inventing facts.

Style:
- Warm, precise, and a little alive.
- Match the user's language.
- Do not start with a markdown heading, table, code fence, list marker, or math block.
- Use markdown only when it improves readability.
- Close every code, table, and LaTeX delimiter because the UI renders streamed output.'''

LLMMessage = dict[str, str]


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
        models = self._client().models.list()
        return sorted(
            {model.id for model in models.data if model.id},
            key=str.lower,
        )

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
    ) -> Iterator[str]:
        stream = self._client().chat.completions.create(
            model=self._model(),
            messages=self.buildMessages(message, history),
            stream=True,
        )
        chunks = cast(Iterable[ChatCompletionChunk], stream)
        for chunk in chunks:
            if not chunk.choices:
                continue
            content = chunk.choices[0].delta.content
            if content:
                yield content

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
