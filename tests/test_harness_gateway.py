from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, override

import anthropic
import httpx2 as httpx
import openai

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.gateway import Gateway


class GatewayTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.gateway = Gateway(self.directory)
        self.base_url = self.gateway.start()
        self.addCleanup(self.gateway.stop)
        self.client = openai.OpenAI(
            api_key='dummy-openai-key',
            base_url=self.base_url + '/v1',
            max_retries=0,
            timeout=3,
            http_client=httpx.Client(trust_env=False),
            _strict_response_validation=True,
        )
        self.addCleanup(self.client.close)
        self.anthropic = anthropic.Anthropic(
            api_key='dummy-anthropic-key',
            base_url=self.base_url,
            max_retries=0,
            timeout=3,
            http_client=httpx.Client(trust_env=False),
            _strict_response_validation=True,
        )
        self.addCleanup(self.anthropic.close)

    def testModelsDoNotConsumeRounds(self) -> None:
        self.gateway.setScenario({
            'models': ['local-chat', 'local-claude'],
            'rounds': [{'text': 'still first'}],
        })
        self.assertEqual(
            [model.id for model in self.client.models.list()],
            ['local-chat', 'local-claude'],
        )
        self.assertEqual(
            [model.id for model in self.anthropic.models.list()],
            ['local-chat', 'local-claude'],
        )
        with httpx.Client(trust_env=False) as client:
            for path in ('/models', '/v1/models'):
                model_response = client.get(self.base_url + path)
                self.assertEqual(model_response.status_code, 200)
                self.assertEqual(model_response.json()['data'][0]['id'], 'local-chat')
        response = self.client.chat.completions.create(
            model='local-chat', messages=[{'role': 'user', 'content': 'hello'}]
        )
        self.assertEqual(response.choices[0].message.content, 'still first')
        self.assertEqual(len(self.gateway.requests), 1)
        self.assertEqual(self.gateway.errors, [])
        self.assertEqual(self.base_url, self.gateway.start())
        self.assertTrue(self.base_url.startswith('http://127.0.0.1:'))
        self.assertFalse(self.base_url.endswith('/v1'))

    def testChatStreamFragmentsToolsAndPreservesHistory(self) -> None:
        self.gateway.setScenario({
            'chunk_size': 2,
            'rounds': [
                {
                    'text': 'checking \u6b4c\u8bcd',
                    'tools': [
                        {
                            'id': 'call_lyrics',
                            'name': 'get_current_lyrics',
                            'arguments': {
                                'song': '\u5357\u65b9',
                                'options': {'full': True},
                            },
                        },
                        {'name': 'get_progress', 'arguments': {}},
                    ],
                    'expect': {
                        'model': 'local-chat',
                        'stream': True,
                        'user_contains': 'translate',
                        'messages': [
                            {'role': 'system', 'content': 'lyrics assistant'},
                            {'role': 'assistant', 'content': 'previous answer'},
                            {'role': 'user', 'content': 'translate this song'},
                        ],
                        'tools': [{'function': {'name': 'get_current_lyrics'}}],
                    },
                },
                {
                    'text': 'translated',
                    'expect': {
                        'messages': [
                            {
                                'role': 'assistant',
                                'tool_calls': [{'id': 'call_lyrics'}],
                            },
                            {
                                'role': 'tool',
                                'tool_call_id': 'call_lyrics',
                                'content': '{"lyric": "hello"}',
                            },
                        ]
                    },
                },
            ],
        })
        messages: list[Any] = [
            {'role': 'system', 'content': 'lyrics assistant'},
            {'role': 'user', 'content': 'previous question'},
            {'role': 'assistant', 'content': 'previous answer'},
            {'role': 'user', 'content': 'translate this song'},
        ]
        with self.client.chat.completions.stream(
            model='local-chat',
            messages=messages,
            tools=[
                {
                    'type': 'function',
                    'function': {
                        'name': 'get_current_lyrics',
                        'parameters': {'type': 'object'},
                    },
                }
            ],
            stream_options={'include_usage': True},
        ) as stream:
            deltas = [event.delta for event in stream if event.type == 'content.delta']
            final = stream.get_final_completion()
        self.assertEqual(''.join(deltas), 'checking \u6b4c\u8bcd')
        self.assertGreater(len(deltas), 1)
        self.assertTrue(all(len(delta) <= 2 for delta in deltas))
        choice = final.choices[0]
        self.assertEqual(choice.finish_reason, 'tool_calls')
        self.assertEqual(choice.message.content, 'checking \u6b4c\u8bcd')
        calls = choice.message.tool_calls
        self.assertIsNotNone(calls)
        assert calls is not None
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].id, 'call_lyrics')
        self.assertEqual(calls[0].function.name, 'get_current_lyrics')
        self.assertEqual(
            json.loads(calls[0].function.arguments),
            {'song': '\u5357\u65b9', 'options': {'full': True}},
        )
        self.assertTrue(calls[1].id)
        self.assertNotEqual(calls[0].id, calls[1].id)
        self.assertEqual(json.loads(calls[1].function.arguments), {})
        self.assertIsNotNone(final.usage)
        messages.append(choice.message.model_dump(exclude_none=True))
        messages.extend([
            {
                'role': 'tool',
                'tool_call_id': 'call_lyrics',
                'content': '{"lyric": "hello"}',
            },
            {'role': 'tool', 'tool_call_id': calls[1].id, 'content': '12'},
        ])
        chunks = list(
            self.client.chat.completions.create(
                model='local-chat', messages=messages, stream=True
            )
        )
        self.assertEqual(
            ''.join(chunk.choices[0].delta.content or '' for chunk in chunks),
            'translated',
        )
        self.assertEqual(chunks[-1].choices[0].finish_reason, 'stop')
        self.assertEqual(self.gateway.errors, [])
        self.assertEqual(self.gateway.requests[1]['body']['messages'], messages)
        self.assertEqual(
            [request['path'] for request in self.gateway.requests],
            ['/v1/chat/completions', '/v1/chat/completions'],
        )
        records = [
            json.loads(line)
            for line in (self.directory / 'requests.jsonl')
            .read_text('utf-8')
            .splitlines()
        ]
        self.assertEqual(records, self.gateway.requests)

    def testResponsesStreamHasFinalEnvelopeAndToolCallOutputs(self) -> None:
        self.gateway.setScenario({
            'chunk_size': 1,
            'rounds': [
                {
                    'text': 'look up',
                    'tools': [
                        {
                            'id': 'call_song',
                            'name': 'find_song',
                            'arguments': {'q': 'a'},
                        },
                        {'name': 'get_progress', 'arguments': {}},
                    ],
                    'expect': {
                        'instructions': 'music assistant',
                        'input': [{'role': 'assistant', 'content': 'earlier answer'}],
                        'user_contains': 'play',
                    },
                },
                {
                    'text': '\u6b63\u5728\u64ad\u653e',
                    'expect': {
                        'input': [
                            {'type': 'function_call', 'call_id': 'call_song'},
                            {
                                'type': 'function_call_output',
                                'call_id': 'call_song',
                                'output': '{"song": 42}',
                            },
                        ]
                    },
                },
            ],
        })
        input_items: list[Any] = [
            {'role': 'user', 'content': 'earlier question'},
            {'role': 'assistant', 'content': 'earlier answer'},
            {
                'role': 'user',
                'content': [{'type': 'input_text', 'text': 'play a song'}],
            },
        ]
        with self.client.responses.stream(
            model='local-chat',
            instructions='music assistant',
            input=input_items,
            tools=[
                {
                    'type': 'function',
                    'name': 'find_song',
                    'parameters': {'type': 'object'},
                    'strict': False,
                }
            ],
        ) as stream:
            events = list(stream)
            final = stream.get_final_response()
        self.assertEqual(final.status, 'completed')
        self.assertEqual(final.object, 'response')
        self.assertEqual(final.output_text, 'look up')
        self.assertEqual(
            ''.join(
                event.delta
                for event in events
                if event.type == 'response.output_text.delta'
            ),
            'look up',
        )
        self.assertEqual(events[0].type, 'response.created')
        self.assertEqual(events[-1].type, 'response.completed')
        self.assertEqual(
            [event.sequence_number for event in events], list(range(len(events)))
        )
        calls = [item for item in final.output if item.type == 'function_call']
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].call_id, 'call_song')
        self.assertNotEqual(calls[0].id, calls[0].call_id)
        self.assertEqual(calls[0].name, 'find_song')
        self.assertEqual(json.loads(calls[0].arguments), {'q': 'a'})
        self.assertTrue(calls[1].call_id)
        argument_deltas = [
            event.delta
            for event in events
            if event.type == 'response.function_call_arguments.delta'
            and event.item_id == calls[0].id
        ]
        self.assertGreater(len(argument_deltas), 1)
        self.assertEqual(json.loads(''.join(argument_deltas)), {'q': 'a'})
        self.assertTrue(
            all(
                item.status == 'completed'
                for item in final.output
                if item.type in ('message', 'function_call')
            )
        )
        input_items.extend(item.model_dump(exclude_none=True) for item in final.output)
        input_items.extend([
            {
                'type': 'function_call_output',
                'call_id': 'call_song',
                'output': '{"song": 42}',
            },
            {
                'type': 'function_call_output',
                'call_id': calls[1].call_id,
                'output': '12',
            },
        ])
        with self.client.responses.stream(
            model='local-chat', input=input_items
        ) as stream:
            text = ''.join(
                event.delta
                for event in stream
                if event.type == 'response.output_text.delta'
            )
            final = stream.get_final_response()
        self.assertEqual(text, '\u6b63\u5728\u64ad\u653e')
        self.assertEqual(final.output_text, text)
        self.assertEqual(self.gateway.requests[1]['body']['input'], input_items)
        self.assertEqual(self.gateway.errors, [])

    def testNonstreamingOpenAIProtocolsReturnToolsAndText(self) -> None:
        for protocol in ('chat', 'responses'):
            with self.subTest(protocol=protocol):
                self.gateway.setScenario({
                    'rounds': [
                        {
                            'text': 'answer',
                            'tools': [
                                {
                                    'id': 'call_one',
                                    'name': 'inspect',
                                    'arguments': {'n': 3},
                                }
                            ],
                            'expect': {'user_contains': 'question'},
                        }
                    ]
                })
                if protocol == 'chat':
                    chat_response = self.client.chat.completions.create(
                        model='local-chat',
                        messages=[{'role': 'user', 'content': 'question'}],
                    )
                    self.assertEqual(chat_response.choices[0].message.content, 'answer')
                    self.assertEqual(
                        chat_response.choices[0].finish_reason, 'tool_calls'
                    )
                    calls = chat_response.choices[0].message.tool_calls
                    assert calls is not None
                    chat_call = calls[0]
                    assert chat_call.type == 'function'
                    self.assertEqual(chat_call.id, 'call_one')
                    self.assertEqual(json.loads(chat_call.function.arguments), {'n': 3})
                else:
                    response = self.client.responses.create(
                        model='local-chat', input='question'
                    )
                    self.assertEqual(response.output_text, 'answer')
                    self.assertEqual(response.status, 'completed')
                    call = next(
                        item for item in response.output if item.type == 'function_call'
                    )
                    self.assertEqual(call.call_id, 'call_one')
                    self.assertEqual(json.loads(call.arguments), {'n': 3})
                self.assertEqual(self.gateway.errors, [])

    def testAnthropicTextStreamAndHistory(self) -> None:
        self.gateway.setScenario({
            'chunk_size': 2,
            'rounds': [
                {
                    'text': 'hello \u5357\u65b9',
                    'expect': {
                        'system': 'music assistant',
                        'max_tokens': 100,
                        'messages': [{'role': 'assistant', 'content': 'previous'}],
                        'user_contains': 'lyrics',
                    },
                },
                {
                    'text': 'done',
                    'expect': {
                        'messages': [
                            {'role': 'assistant', 'content': 'hello \u5357\u65b9'}
                        ]
                    },
                },
            ],
        })
        messages: list[Any] = [
            {'role': 'user', 'content': 'before'},
            {'role': 'assistant', 'content': 'previous'},
            {'role': 'user', 'content': [{'type': 'text', 'text': 'get lyrics'}]},
        ]
        with self.anthropic.messages.stream(
            model='local-claude',
            system='music assistant',
            max_tokens=100,
            messages=messages,
        ) as stream:
            deltas = list(stream.text_stream)
            final = stream.get_final_message()
        self.assertEqual(''.join(deltas), 'hello \u5357\u65b9')
        self.assertGreater(len(deltas), 1)
        assert final.content[0].type == 'text'
        self.assertEqual(final.content[0].text, 'hello \u5357\u65b9')
        self.assertEqual(final.stop_reason, 'end_turn')
        self.assertEqual(final.role, 'assistant')
        self.assertEqual(final.type, 'message')
        messages.append({'role': 'assistant', 'content': final.content[0].text})
        messages.append({'role': 'user', 'content': 'thanks'})
        response = self.anthropic.messages.create(
            model='local-claude', max_tokens=100, messages=messages
        )
        assert response.content[0].type == 'text'
        self.assertEqual(response.content[0].text, 'done')
        self.assertEqual(response.stop_reason, 'end_turn')
        self.assertEqual(self.gateway.requests[1]['body']['messages'], messages)
        self.assertEqual(self.gateway.errors, [])

    def testEmptyStreamsStillFinalize(self) -> None:
        self.gateway.setScenario({'rounds': [{'text': ''}, {'text': ''}, {'text': ''}]})
        with self.client.chat.completions.stream(
            model='local-chat', messages=[{'role': 'user', 'content': 'hello'}]
        ) as stream:
            chat_final = stream.get_final_completion()
        self.assertEqual(chat_final.choices[0].finish_reason, 'stop')
        with self.client.responses.stream(model='local-chat', input='hello') as stream:
            response_final = stream.get_final_response()
        self.assertEqual(response_final.status, 'completed')
        self.assertEqual(response_final.output_text, '')
        with self.anthropic.messages.stream(
            model='local-claude',
            max_tokens=10,
            messages=[{'role': 'user', 'content': 'hello'}],
        ) as stream:
            final = stream.get_final_message()
        assert final.content[0].type == 'text'
        self.assertEqual(final.content[0].text, '')
        self.assertEqual(final.stop_reason, 'end_turn')
        self.assertEqual(self.gateway.errors, [])

    def testStatusFaultsConsumeRoundsWithoutHarnessErrors(self) -> None:
        for protocol in ('chat', 'responses', 'anthropic'):
            for status in (400, 401, 429, 503):
                with self.subTest(protocol=protocol, status=status):
                    self.gateway.setScenario({
                        'rounds': [{'status': status}, {'text': 'recovered'}]
                    })
                    error_type = (
                        anthropic.APIStatusError
                        if protocol == 'anthropic'
                        else openai.APIStatusError
                    )
                    with self.assertRaises(error_type) as raised:
                        self._create(protocol)
                    self.assertIsInstance(
                        raised.exception,
                        (openai.APIStatusError, anthropic.APIStatusError),
                    )
                    self.assertEqual(getattr(raised.exception, 'status_code'), status)
                    self.assertIn('scenario', str(raised.exception).lower())
                    self._create(protocol)
                    self.assertEqual(len(self.gateway.requests), 2)
                    self.assertEqual(self.gateway.errors, [])

    def testExpectationMismatchReportsPathAndConsumesRound(self) -> None:
        for expect in (
            {'messages': [{'role': 'user', 'content': 'different'}]},
            {'tools': [{'function': {'parameters': {'required': ['song']}}}]},
            {'user_contains': 'system only'},
        ):
            with self.subTest(expect=expect):
                self.gateway.setScenario({
                    'rounds': [{'expect': expect}, {'text': 'next'}]
                })
                with self.assertRaises(openai.BadRequestError):
                    self.client.chat.completions.create(
                        model='local-chat',
                        messages=[
                            {'role': 'system', 'content': 'system only'},
                            {'role': 'user', 'content': 'question'},
                        ],
                    )
                self.assertEqual(len(self.gateway.errors), 1)
                self.assertIn('/v1/chat/completions', self.gateway.errors[0])
                self.assertIn('expect', self.gateway.errors[0])
                self.assertEqual(
                    self.gateway.requests[0]['body']['messages'][1]['content'],
                    'question',
                )
                response = self.client.chat.completions.create(
                    model='local-chat', messages=[{'role': 'user', 'content': 'hello'}]
                )
                self.assertEqual(response.choices[0].message.content, 'next')

    def testUnexpectedPostsAndMalformedBodiesAreRecorded(self) -> None:
        self.gateway.setScenario({'rounds': [{'text': 'unused'}]})
        with httpx.Client(trust_env=False) as client:
            response = client.post(self.base_url + '/unknown', json={'input': 'hi'})
            self.assertEqual(response.status_code, 404)
            response = client.post(
                self.base_url + '/v1/responses', json={'input': 'hi'}
            )
            self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.gateway.errors), 2)
        self.assertEqual(
            self.gateway.requests[0], {'path': '/unknown', 'body': {'input': 'hi'}}
        )
        self.gateway.setScenario({'rounds': [{}, {'text': 'next'}]})
        with httpx.Client(trust_env=False) as client:
            for body in ('{bad json', '[]'):
                response = client.post(self.base_url + '/v1/responses', content=body)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.gateway.errors), 2)

    def testCancelledAndDisconnectedStreamsDoNotContaminateNextScenario(self) -> None:
        for protocol in ('chat', 'responses', 'anthropic'):
            for disconnect in (False, True):
                with self.subTest(protocol=protocol, disconnect=disconnect):
                    self.gateway.setScenario({
                        'chunk_size': 1,
                        'rounds': [
                            {
                                'text': 'old stream' * 10,
                                'delay': 0.01,
                                'disconnect': disconnect,
                            }
                        ],
                    })
                    manager: Any
                    if protocol == 'chat':
                        manager = self.client.chat.completions.stream(
                            model='local-chat',
                            messages=[{'role': 'user', 'content': 'hello'}],
                        )
                    elif protocol == 'responses':
                        manager = self.client.responses.stream(
                            model='local-chat', input='hello'
                        )
                    else:
                        manager = self.anthropic.messages.stream(
                            model='local-claude',
                            max_tokens=10,
                            messages=[{'role': 'user', 'content': 'hello'}],
                        )
                    if disconnect:
                        error_type = (
                            httpx.RemoteProtocolError
                            if protocol == 'anthropic'
                            else openai.APIConnectionError
                        )
                        with self.assertRaises(error_type):
                            with manager as stream:
                                list(stream)
                    else:
                        with manager as stream:
                            for event in stream:
                                if event.type in (
                                    'content.delta',
                                    'response.output_text.delta',
                                    'content_block_delta',
                                ):
                                    break
                    self.gateway.setScenario({'rounds': [{'text': 'fresh'}]})
                    self._create(protocol)
                    time.sleep(0.03)
                    self.assertEqual(len(self.gateway.requests), 1)
                    self.assertEqual(self.gateway.errors, [])

    def testScenarioReplacementInterruptsDelayAndKeepsJournal(self) -> None:
        self.gateway.setScenario({'rounds': [{'text': 'old', 'delay': 10}]})
        failures: list[Exception] = []

        def requestOld() -> None:
            try:
                self._create('responses')
            except Exception as error:
                failures.append(error)

        worker = threading.Thread(target=requestOld)
        worker.start()
        deadline = time.monotonic() + 2
        while not self.gateway.requests and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(self.gateway.requests), 1)
        self.gateway.setScenario({'rounds': [{'text': 'fresh'}]})
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(failures), 1)
        response = self.client.responses.create(
            model='local-chat', input='new question'
        )
        self.assertEqual(response.output_text, 'fresh')
        self.assertEqual(len(self.gateway.requests), 1)
        self.assertEqual(self.gateway.errors, [])
        records = [
            json.loads(line)
            for line in (self.directory / 'requests.jsonl')
            .read_text('utf-8')
            .splitlines()
        ]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]['body']['input'], 'hello')
        self.assertEqual(records[1]['body']['input'], 'new question')

    def testStopReleasesListenerAndGatewayCanRestart(self) -> None:
        self.gateway.stop()
        self.gateway.stop()
        with httpx.Client(trust_env=False, timeout=0.5) as client:
            with self.assertRaises((httpx.ConnectError, httpx.ConnectTimeout)):
                client.get(self.base_url + '/models')
        self.gateway.setScenario({'rounds': [{'text': 'restart'}]})
        base_url = self.gateway.start()
        with openai.OpenAI(
            api_key='dummy',
            base_url=base_url + '/v1',
            max_retries=0,
            http_client=httpx.Client(trust_env=False),
        ) as client:
            self.assertEqual(
                client.responses.create(model='local-chat', input='hello').output_text,
                'restart',
            )
        self.assertEqual(self.gateway.errors, [])

    def testStopInterruptsDelayedStream(self) -> None:
        self.gateway.setScenario({'rounds': [{'text': 'waiting', 'delay': 10}]})
        stream = self.client.responses.create(
            model='local-chat', input='hello', stream=True
        )
        self.addCleanup(stream.close)
        started = time.monotonic()
        self.gateway.stop()
        self.assertLess(time.monotonic() - started, 1)
        with self.assertRaises(openai.APIConnectionError):
            list(stream)
        self.assertEqual(self.gateway.errors, [])

    def testToolOnlyStreamsFinalize(self) -> None:
        for protocol in ('chat', 'responses'):
            with self.subTest(protocol=protocol):
                self.gateway.setScenario({
                    'chunk_size': 1,
                    'rounds': [
                        {
                            'tools': [
                                {'name': 'inspect', 'arguments': {'enabled': False}}
                            ]
                        }
                    ],
                })
                if protocol == 'chat':
                    with self.client.chat.completions.stream(
                        model='local-chat',
                        messages=[{'role': 'user', 'content': 'hello'}],
                    ) as stream:
                        chat_final = stream.get_final_completion()
                    calls = chat_final.choices[0].message.tool_calls
                    assert calls is not None
                    assert calls[0].type == 'function'
                    self.assertEqual(
                        json.loads(calls[0].function.arguments), {'enabled': False}
                    )
                    self.assertEqual(chat_final.choices[0].finish_reason, 'tool_calls')
                else:
                    with self.client.responses.stream(
                        model='local-chat', input='hello'
                    ) as stream:
                        response_final = stream.get_final_response()
                    self.assertEqual(len(response_final.output), 1)
                    call = response_final.output[0]
                    assert call.type == 'function_call'
                    self.assertEqual(json.loads(call.arguments), {'enabled': False})
                    self.assertTrue(call.call_id)
                    self.assertEqual(response_final.status, 'completed')
                self.assertEqual(self.gateway.errors, [])

    def testStreamingStatusFaultsAreSDKStatusErrors(self) -> None:
        for protocol in ('chat', 'responses', 'anthropic'):
            with self.subTest(protocol=protocol):
                self.gateway.setScenario({'rounds': [{'status': 503}]})
                error_type = (
                    anthropic.APIStatusError
                    if protocol == 'anthropic'
                    else openai.APIStatusError
                )
                with self.assertRaises(error_type) as raised:
                    self._create(protocol, stream=True)
                self.assertEqual(getattr(raised.exception, 'status_code'), 503)
                self.assertEqual(self.gateway.errors, [])

    def testInvalidScenarioLeavesPreviousScenarioUsable(self) -> None:
        self.gateway.setScenario({'rounds': [{'text': 'unchanged'}]})
        for scenario in (
            {'rounds': 'invalid'},
            {'chunk_size': 0},
            {'rounds': [{'text': 4}]},
            {'rounds': [{'tools': [{'name': 'inspect', 'arguments': 'bad'}]}]},
            {'rounds': [{'delay': -1}]},
            {'rounds': [{'status': 204}]},
        ):
            with self.subTest(scenario=scenario):
                with self.assertRaises(ValueError):
                    self.gateway.setScenario(scenario)
        self.assertEqual(self._create('responses').output_text, 'unchanged')
        self.assertEqual(self.gateway.errors, [])

    def testJournalFailuresAreReportedAndConsumeTheRound(self) -> None:
        self.gateway.setScenario({'rounds': [{'text': 'first'}, {'text': 'second'}]})
        journal = self.directory / 'requests.jsonl'
        journal.mkdir()
        with self.assertRaises(openai.InternalServerError):
            self._create('responses')
        self.assertEqual(len(self.gateway.errors), 1)
        self.assertIn('requests.jsonl', self.gateway.errors[0])
        journal.rmdir()
        self.assertEqual(self._create('responses').output_text, 'second')

    def _create(self, protocol: str, stream: bool = False) -> Any:
        if protocol == 'chat':
            return self.client.chat.completions.create(
                model='local-chat',
                messages=[{'role': 'user', 'content': 'hello'}],
                stream=stream,
            )
        if protocol == 'responses':
            return self.client.responses.create(
                model='local-chat', input='hello', stream=stream
            )
        return self.anthropic.messages.create(
            model='local-claude',
            max_tokens=10,
            messages=[{'role': 'user', 'content': 'hello'}],
            stream=stream,
        )


if __name__ == '__main__':
    unittest.main()
