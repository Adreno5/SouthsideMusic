from __future__ import annotations

import copy
import json
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast, override
from urllib.parse import urlsplit


def _subset(expected: Any, actual: Any, path: str = 'expect') -> str | None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return f'{path}: expected an object, got {actual!r}'
        for key, value in expected.items():
            if key not in actual:
                return f'{path}.{key}: missing'
            mismatch = _subset(value, actual[key], f'{path}.{key}')
            if mismatch is not None:
                return mismatch
        return None
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return f'{path}: expected a list, got {actual!r}'
        position = 0
        for index, value in enumerate(expected):
            while position < len(actual):
                mismatch = _subset(value, actual[position], f'{path}[{index}]')
                position += 1
                if mismatch is None:
                    break
            else:
                return f'{path}[{index}]: no matching item for {value!r}'
        return None
    if type(expected) is not type(actual) or expected != actual:
        return f'{path}: expected {expected!r}, got {actual!r}'
    return None


def _fragments(text: str, chunk_size: int) -> Iterator[str]:
    for offset in range(0, len(text), chunk_size):
        yield text[offset : offset + chunk_size]


def _disconnect(connection: socket.socket) -> None:
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


class Gateway:
    def __init__(self, directory: Path | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self._directory = directory
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)
        self._scenario: dict[str, Any] = {'rounds': []}
        self._round = 0
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._connections: set[socket.socket] = set()
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        with self._lock:
            if self._server is None:
                if self._cancel.is_set():
                    self._cancel = threading.Event()
                self._server = _Server(self)
                self._thread = threading.Thread(
                    target=self._server.serve_forever,
                    kwargs={'poll_interval': 0.02},
                    daemon=True,
                )
                self._thread.start()
            return f'http://127.0.0.1:{self._server.server_port}'

    def stop(self) -> None:
        with self._lock:
            self._cancel.set()
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
            connections = tuple(self._connections)
        for connection in connections:
            _disconnect(connection)
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join()

    def setScenario(self, scenario: dict[str, Any]) -> None:
        scenario = copy.deepcopy(scenario)
        rounds = scenario.get('rounds', [])
        if not isinstance(rounds, list) or not all(isinstance(r, dict) for r in rounds):
            raise ValueError('rounds must be a list of objects')
        chunk_size = scenario.get('chunk_size', 4)
        if type(chunk_size) is not int or chunk_size < 1:
            raise ValueError('chunk_size must be a positive integer')
        for item in rounds:
            if not isinstance(item.get('text', ''), str):
                raise ValueError('round text must be a string')
            if not isinstance(item.get('expect', {}), dict):
                raise ValueError('round expect must be an object')
            tools = item.get('tools', [])
            if not isinstance(tools, list) or not all(
                isinstance(tool, dict)
                and isinstance(tool.get('name'), str)
                and isinstance(tool.get('arguments'), dict)
                and isinstance(tool.get('id', ''), str)
                for tool in tools
            ):
                raise ValueError('tools need a name, arguments object, and optional id')
            status = item.get('status', 200)
            if type(status) is not int or not 200 <= status <= 599 or status == 204:
                raise ValueError('round status must permit an HTTP response body')
            delay = item.get('delay', 0)
            if not isinstance(delay, (int, float)) or not 0 <= delay < float('inf'):
                raise ValueError('round delay must be finite and nonnegative')
        models = scenario.get('models', ['harness-model'])
        if not isinstance(models, list) or not all(isinstance(m, str) for m in models):
            raise ValueError('models must be a list of strings')
        with self._lock:
            self._cancel.set()
            self._cancel = threading.Event()
            self._scenario = scenario
            self._round = 0
            self.requests.clear()
            self.errors.clear()
            connections = tuple(self._connections)
        for connection in connections:
            _disconnect(connection)


class _Server(ThreadingHTTPServer):
    daemon_threads = False

    def __init__(self, gateway: Gateway) -> None:
        self.gateway = gateway
        super().__init__(('127.0.0.1', 0), _Handler)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    @property
    def _gateway(self) -> Gateway:
        return cast(_Server, self.server).gateway

    @override
    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(3)
        with self._gateway._lock:
            self._cancel = self._gateway._cancel
            self._gateway._connections.add(self.connection)

    @override
    def handle(self) -> None:
        try:
            super().handle()
        except OSError:
            pass

    @override
    def finish(self) -> None:
        try:
            super().finish()
        except OSError:
            pass
        finally:
            with self._gateway._lock:
                self._gateway._connections.discard(self.connection)

    @override
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        if urlsplit(self.path).path not in ('/models', '/v1/models'):
            self._reject('Unknown model endpoint', 404)
            return
        with self._gateway._lock:
            models = self._gateway._scenario.get('models', ['harness-model'])
        self._reply({
            'object': 'list',
            'data': [
                {
                    'id': model,
                    'object': 'model',
                    'created': 0,
                    'owned_by': 'harness',
                    'type': 'model',
                    'display_name': model,
                    'created_at': '2026-01-01T00:00:00Z',
                }
                for model in models
            ],
            'has_more': False,
            'first_id': models[0] if models else None,
            'last_id': models[-1] if models else None,
        })

    def do_POST(self) -> None:
        cancel = self._cancel
        try:
            parse_error = None
            body: Any = None
            try:
                length = int(self.headers.get('Content-Length', '0'))
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    parse_error = 'Expected a JSON object'
            except (ValueError, UnicodeDecodeError) as error:
                parse_error = f'Invalid JSON body: {error}'
            path = urlsplit(self.path).path
            with self._gateway._lock:
                if cancel.is_set():
                    return
                record = {'path': self.path, 'body': body}
                self._gateway.requests.append(record)
                index = self._gateway._round
                self._gateway._round += 1
                if self._gateway._directory is not None:
                    try:
                        with (self._gateway._directory / 'requests.jsonl').open(
                            'a', encoding='utf-8'
                        ) as journal:
                            journal.write(json.dumps(record, ensure_ascii=False) + '\n')
                    except OSError as error:
                        raise RuntimeError(
                            f'Cannot persist requests.jsonl: {error}'
                        ) from error
                rounds = self._gateway._scenario.get('rounds', [])
                item = rounds[index] if index < len(rounds) else None
                chunk_size = self._gateway._scenario.get('chunk_size', 4)
                mismatch = parse_error
                status = 400
                if path not in (
                    '/v1/chat/completions',
                    '/v1/responses',
                    '/v1/messages',
                ):
                    mismatch = 'Unexpected POST endpoint'
                    status = 404
                elif item is None:
                    mismatch = f'Unexpected POST: no scenario round {index + 1}'
                elif mismatch is None:
                    expected = dict(item.get('expect', {}))
                    user_contains = expected.pop('user_contains', None)
                    mismatch = _subset(expected, body)
                    if mismatch is None and user_contains is not None:
                        user_texts: list[str] = []
                        messages = body.get('messages', body.get('input', []))
                        if isinstance(messages, str):
                            user_texts.append(messages)
                        elif isinstance(messages, list):
                            for message in messages:
                                if (
                                    not isinstance(message, dict)
                                    or message.get('role') != 'user'
                                ):
                                    continue
                                content = message.get('content', '')
                                if isinstance(content, str):
                                    user_texts.append(content)
                                elif isinstance(content, list):
                                    user_texts.extend(
                                        part['text']
                                        for part in content
                                        if isinstance(part, dict)
                                        and isinstance(part.get('text'), str)
                                    )
                        needles = (
                            user_contains
                            if isinstance(user_contains, list)
                            else [user_contains]
                        )
                        if not all(
                            isinstance(needle, str) and needle in '\n'.join(user_texts)
                            for needle in needles
                        ):
                            mismatch = (
                                f'expect.user_contains: missing {user_contains!r}'
                            )
                if mismatch is not None:
                    mismatch = f'{self.path} round {index + 1}: {mismatch}'
                    self._gateway.errors.append(mismatch)
            if mismatch is not None:
                self._reject(mismatch, status)
                return
            assert item is not None
            status = item.get('status', 200)
            delay = item.get('delay', 0)
            if status >= 300:
                if not cancel.wait(delay):
                    self._reject(f'Scenario HTTP status {status}', status)
                return
            if path == '/v1/messages' and item.get('tools'):
                with self._gateway._lock:
                    if not cancel.is_set():
                        self._gateway.errors.append(
                            'Anthropic harness supports text only'
                        )
                self._reject('Anthropic harness supports text only', 400)
                return
            response = self._response(path, item, body)
            if body.get('stream'):
                if path == '/v1/chat/completions':
                    events = self._chatEvents(response, chunk_size, body)
                elif path == '/v1/responses':
                    events = self._responseEvents(response, chunk_size)
                else:
                    events = self._messageEvents(response, chunk_size)
                self._stream(
                    events, cancel, delay, item.get('disconnect', False), status
                )
            elif not cancel.wait(delay):
                if item.get('disconnect'):
                    _disconnect(self.connection)
                else:
                    self._reply(response, status)
        except OSError:
            pass
        except Exception as error:
            with self._gateway._lock:
                if cancel.is_set():
                    return
                message = f'{self.path}: {type(error).__name__}: {error}'
                self._gateway.errors.append(message)
            self._reject(message, 500)
        finally:
            self.close_connection = True

    def _reply(self, body: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(data)
            self.wfile.flush()
        except OSError:
            pass

    def _reject(self, message: str, status: int) -> None:
        self._reply(
            {
                'type': 'error',
                'error': {
                    'type': 'api_error' if status >= 500 else 'invalid_request_error',
                    'message': message,
                    'code': str(status),
                    'param': None,
                },
            },
            status,
        )

    def _response(
        self, path: str, item: dict[str, Any], body: dict[str, Any]
    ) -> dict[str, Any]:
        text = item.get('text', '')
        identity = uuid.uuid4().hex
        calls: list[dict[str, Any]] = [
            {
                'id': tool.get('id') or f'call_{uuid.uuid4().hex}',
                'type': 'function',
                'function': {
                    'name': tool['name'],
                    'arguments': json.dumps(tool['arguments'], ensure_ascii=False),
                },
            }
            for tool in item.get('tools', [])
        ]
        output_tokens = len(text) + sum(
            len(call['function']['arguments']) for call in calls
        )
        if path == '/v1/chat/completions':
            message: dict[str, Any] = {
                'role': 'assistant',
                'content': text or None,
                'refusal': None,
            }
            if calls:
                message['tool_calls'] = calls
            return {
                'id': f'chatcmpl_{identity}',
                'object': 'chat.completion',
                'created': int(time.time()),
                'model': body.get('model', 'harness-model'),
                'system_fingerprint': None,
                'choices': [
                    {
                        'index': 0,
                        'message': message,
                        'finish_reason': 'tool_calls' if calls else 'stop',
                        'logprobs': None,
                    }
                ],
                'usage': {
                    'prompt_tokens': 0,
                    'completion_tokens': output_tokens,
                    'total_tokens': output_tokens,
                },
            }
        if path == '/v1/messages':
            return {
                'id': f'msg_{identity}',
                'type': 'message',
                'role': 'assistant',
                'model': body.get('model', 'harness-model'),
                'content': [{'type': 'text', 'text': text}],
                'stop_reason': 'end_turn',
                'stop_sequence': None,
                'usage': {'input_tokens': 0, 'output_tokens': output_tokens},
            }
        output: list[dict[str, Any]] = []
        if text or not calls:
            output.append({
                'id': f'msg_{identity}',
                'type': 'message',
                'role': 'assistant',
                'status': 'completed',
                'content': [
                    {
                        'type': 'output_text',
                        'text': text,
                        'annotations': [],
                        'logprobs': [],
                    }
                ],
            })
        output.extend(
            {
                'id': f'fc_{uuid.uuid4().hex}',
                'type': 'function_call',
                'call_id': call['id'],
                'name': call['function']['name'],
                'arguments': call['function']['arguments'],
                'status': 'completed',
            }
            for call in calls
        )
        return {
            'id': f'resp_{identity}',
            'object': 'response',
            'created_at': time.time(),
            'completed_at': time.time(),
            'status': 'completed',
            'error': None,
            'incomplete_details': None,
            'instructions': body.get('instructions'),
            'model': body.get('model', 'harness-model'),
            'output': output,
            'parallel_tool_calls': body.get('parallel_tool_calls', True),
            'tool_choice': body.get('tool_choice', 'auto'),
            'tools': body.get('tools', []),
            'text': {'format': {'type': 'text'}},
            'metadata': {},
            'previous_response_id': body.get('previous_response_id'),
            'usage': {
                'input_tokens': 0,
                'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0},
                'output_tokens': output_tokens,
                'output_tokens_details': {'reasoning_tokens': 0},
                'total_tokens': output_tokens,
            },
        }

    def _chatEvents(
        self, response: dict[str, Any], chunk_size: int, body: dict[str, Any]
    ) -> Iterator[dict[str, Any] | str]:
        envelope = {
            'id': response['id'],
            'object': 'chat.completion.chunk',
            'created': response['created'],
            'model': response['model'],
            'system_fingerprint': None,
            'usage': None,
        }
        choice = response['choices'][0]
        message = choice['message']
        yield {
            **envelope,
            'choices': [
                {
                    'index': 0,
                    'delta': {'role': 'assistant', 'content': ''},
                    'finish_reason': None,
                    'logprobs': None,
                }
            ],
        }
        for fragment in _fragments(message['content'] or '', chunk_size):
            yield {
                **envelope,
                'choices': [
                    {
                        'index': 0,
                        'delta': {'content': fragment},
                        'finish_reason': None,
                        'logprobs': None,
                    }
                ],
            }
        for index, tool in enumerate(message.get('tool_calls', [])):
            yield {
                **envelope,
                'choices': [
                    {
                        'index': 0,
                        'delta': {
                            'tool_calls': [
                                {
                                    'index': index,
                                    'id': tool['id'],
                                    'type': 'function',
                                    'function': {
                                        'name': tool['function']['name'],
                                        'arguments': '',
                                    },
                                }
                            ]
                        },
                        'finish_reason': None,
                        'logprobs': None,
                    }
                ],
            }
            for fragment in _fragments(tool['function']['arguments'], chunk_size):
                yield {
                    **envelope,
                    'choices': [
                        {
                            'index': 0,
                            'delta': {
                                'tool_calls': [
                                    {
                                        'index': index,
                                        'function': {'arguments': fragment},
                                    }
                                ]
                            },
                            'finish_reason': None,
                            'logprobs': None,
                        }
                    ],
                }
        yield {
            **envelope,
            'choices': [
                {
                    'index': 0,
                    'delta': {},
                    'finish_reason': choice['finish_reason'],
                    'logprobs': None,
                }
            ],
        }
        if body.get('stream_options', {}).get('include_usage'):
            yield {**envelope, 'choices': [], 'usage': response['usage']}
        yield '[DONE]'

    def _responseEvents(
        self, response: dict[str, Any], chunk_size: int
    ) -> Iterator[dict[str, Any]]:
        initial = {
            **response,
            'status': 'in_progress',
            'output': [],
            'usage': None,
            'completed_at': None,
        }
        yield {'type': 'response.created', 'response': initial}
        yield {'type': 'response.in_progress', 'response': initial}
        for index, item in enumerate(response['output']):
            added = {**item, 'status': 'in_progress'}
            if item['type'] == 'message':
                added['content'] = []
            else:
                added['arguments'] = ''
            yield {
                'type': 'response.output_item.added',
                'output_index': index,
                'item': added,
            }
            location = {'item_id': item['id'], 'output_index': index}
            if item['type'] == 'message':
                location['content_index'] = 0
                part = item['content'][0]
                yield {
                    'type': 'response.content_part.added',
                    **location,
                    'part': {**part, 'text': ''},
                }
                for fragment in _fragments(part['text'], chunk_size):
                    yield {
                        'type': 'response.output_text.delta',
                        **location,
                        'delta': fragment,
                        'logprobs': [],
                    }
                yield {
                    'type': 'response.output_text.done',
                    **location,
                    'text': part['text'],
                    'logprobs': [],
                }
                yield {'type': 'response.content_part.done', **location, 'part': part}
            else:
                for fragment in _fragments(item['arguments'], chunk_size):
                    yield {
                        'type': 'response.function_call_arguments.delta',
                        **location,
                        'delta': fragment,
                    }
                yield {
                    'type': 'response.function_call_arguments.done',
                    **location,
                    'arguments': item['arguments'],
                }
            yield {
                'type': 'response.output_item.done',
                'output_index': index,
                'item': item,
            }
        yield {'type': 'response.completed', 'response': response}

    def _messageEvents(
        self, response: dict[str, Any], chunk_size: int
    ) -> Iterator[dict[str, Any]]:
        yield {
            'type': 'message_start',
            'message': {
                **response,
                'content': [],
                'stop_reason': None,
                'usage': {'input_tokens': 0, 'output_tokens': 0},
            },
        }
        yield {
            'type': 'content_block_start',
            'index': 0,
            'content_block': {'type': 'text', 'text': ''},
        }
        for fragment in _fragments(response['content'][0]['text'], chunk_size):
            yield {
                'type': 'content_block_delta',
                'index': 0,
                'delta': {'type': 'text_delta', 'text': fragment},
            }
        yield {'type': 'content_block_stop', 'index': 0}
        yield {
            'type': 'message_delta',
            'delta': {'stop_reason': 'end_turn', 'stop_sequence': None},
            'usage': {'output_tokens': response['usage']['output_tokens']},
        }
        yield {'type': 'message_stop'}

    def _stream(
        self,
        events: Iterator[dict[str, Any] | str],
        cancel: threading.Event,
        delay: float,
        disconnect: bool,
        status: int,
    ) -> None:
        self.send_response(status)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Transfer-Encoding', 'chunked')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.flush()
        for sequence, event in enumerate(events):
            if cancel.wait(delay):
                return
            prefix = ''
            if isinstance(event, dict):
                event_type = event.get('type', '')
                if event_type.startswith('response.'):
                    event = {**event, 'sequence_number': sequence}
                if event_type:
                    prefix = f'event: {event_type}\n'
                data = json.dumps(event, ensure_ascii=False)
                meaningful = event_type in (
                    'response.output_text.delta',
                    'response.function_call_arguments.delta',
                    'content_block_delta',
                ) or any(
                    choice.get('delta', {}).get('content')
                    or choice.get('delta', {}).get('tool_calls')
                    for choice in event.get('choices', [])
                )
            else:
                data = event
                meaningful = False
            packet = f'{prefix}data: {data}\n\n'.encode('utf-8')
            self.wfile.write(f'{len(packet):x}\r\n'.encode('ascii') + packet + b'\r\n')
            self.wfile.flush()
            if disconnect and meaningful:
                _disconnect(self.connection)
                return
        if disconnect:
            _disconnect(self.connection)
        elif not cancel.is_set():
            self.wfile.write(b'0\r\n\r\n')
            self.wfile.flush()
