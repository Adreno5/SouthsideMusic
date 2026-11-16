from __future__ import annotations

import builtins
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, override
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core import whisper_worker


class WhisperWorkerTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime_path = self.root / 'runtime'
        self.runtime_path.mkdir()
        self.model_path = self.root / 'model'
        self.model_path.mkdir()
        (self.model_path / 'tokenizer.json').write_text('{}', encoding='utf-8')
        self.result_path = self.root / 'result.json'
        self.model = Mock()
        self.model.transcribe.return_value = (
            iter(()),
            SimpleNamespace(duration=10.0, language='zh'),
        )
        self.factory = Mock(return_value=self.model)
        self.supported_types = Mock(return_value={'int8', 'float16'})
        self.device_count = Mock(return_value=1)
        self.vad_model = Mock()
        self.preload = Mock()
        self.decode_audio = Mock(return_value=[0.0])
        self.runtime_modules: dict[str, Any] = {
            'onnxruntime': SimpleNamespace(preload_dlls=self.preload),
            'ctranslate2': SimpleNamespace(
                get_supported_compute_types=self.supported_types,
                get_cuda_device_count=self.device_count,
            ),
            'faster_whisper': SimpleNamespace(WhisperModel=self.factory),
            'faster_whisper.audio': SimpleNamespace(decode_audio=self.decode_audio),
            'faster_whisper.vad': SimpleNamespace(get_vad_model=self.vad_model),
        }

    def _runWorker(
        self, arguments: list[str], cpu_count: int | None = 32
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(sys, 'argv', ['whisper_worker.py', *arguments]),
            patch.object(sys, 'path', sys.path.copy()),
            patch.dict(os.environ),
            patch.dict(sys.modules, self.runtime_modules),
            patch.object(os, 'cpu_count', return_value=cpu_count),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            status = whisper_worker.main()
        return status, stdout.getvalue(), stderr.getvalue()

    def _inferenceArguments(self, device: str = 'cpu') -> list[str]:
        return [
            str(self.runtime_path),
            str(self.model_path),
            device,
            'audio.wav',
            str(self.result_path),
        ]

    def _segment(self, text: str, start: float, end: float) -> SimpleNamespace:
        return SimpleNamespace(
            start=start,
            end=end,
            text=text,
            words=[SimpleNamespace(word=text, start=start, end=end, probability=1.0)],
        )

    def testImportDoesNotLoadOptionalDependencies(self) -> None:
        original_import = builtins.__import__
        imports = []

        def checkImport(
            name: str,
            globals: dict[str, Any] | None = None,
            locals: dict[str, Any] | None = None,
            fromlist: tuple[str, ...] = (),
            level: int = 0,
        ) -> ModuleType:
            imports.append(name)
            if (
                name.split('.')[0]
                in (
                    'ctranslate2',
                    'faster_whisper',
                    'onnxruntime',
                    'PySide6',
                )
                or name == 'core.config'
            ):
                raise ImportError(name)
            return original_import(name, globals, locals, fromlist, level)

        spec = importlib.util.spec_from_file_location(
            'isolated_whisper_worker', whisper_worker.__file__
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        with patch.object(builtins, '__import__', side_effect=checkImport):
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        self.assertTrue(callable(module.main))
        self.assertNotIn('faster_whisper', imports)

    def testProbeSetsOfflineFlagsBeforeRuntimeImports(self) -> None:
        original_import = builtins.__import__
        imports = []

        def checkImport(
            name: str,
            globals: dict[str, Any] | None = None,
            locals: dict[str, Any] | None = None,
            fromlist: tuple[str, ...] = (),
            level: int = 0,
        ) -> ModuleType:
            if name in self.runtime_modules:
                imports.append(name)
                self.assertEqual(sys.path[0], str(self.runtime_path))
                for flag in (
                    'HF_HUB_OFFLINE',
                    'TRANSFORMERS_OFFLINE',
                    'HF_HUB_DISABLE_TELEMETRY',
                ):
                    self.assertEqual(os.environ[flag], '1')
                for variable in (
                    'OMP_NUM_THREADS',
                    'OPENBLAS_NUM_THREADS',
                    'MKL_NUM_THREADS',
                    'NUMEXPR_NUM_THREADS',
                ):
                    self.assertEqual(os.environ[variable], '8')
                self.assertEqual(os.environ['TOKENIZERS_PARALLELISM'], 'false')
            return original_import(name, globals, locals, fromlist, level)

        with (
            patch.dict(os.environ, HF_HUB_OFFLINE='0', OMP_NUM_THREADS='100'),
            patch.object(builtins, '__import__', side_effect=checkImport),
        ):
            status, stdout, stderr = self._runWorker([
                '--probe',
                str(self.runtime_path),
                'cpu',
            ])
        self.assertEqual(status, 0, stderr)
        self.assertEqual(
            [json.loads(line) for line in stdout.splitlines()], [{'ready': True}]
        )
        self.assertEqual(
            imports,
            [
                'onnxruntime',
                'ctranslate2',
                'faster_whisper',
                'faster_whisper.audio',
                'faster_whisper.vad',
            ],
        )
        self.vad_model.assert_called_once_with()
        self.decode_audio.assert_called_once()
        self.supported_types.assert_called_once_with('cpu')
        self.device_count.assert_not_called()
        self.preload.assert_not_called()
        self.factory.assert_not_called()

    def testProbeLoadsLocalModelWithBoundedThreads(self) -> None:
        status, _, stderr = self._runWorker([
            '--probe',
            str(self.runtime_path),
            'cpu',
            str(self.model_path),
        ])
        self.assertEqual(status, 0, stderr)
        self.factory.assert_called_once_with(
            str(self.model_path),
            device='cpu',
            compute_type='int8',
            cpu_threads=8,
            num_workers=1,
            local_files_only=True,
        )
        self.model.transcribe.assert_not_called()

    def testCpuThreadsHandlesMissingAndSingleCpu(self) -> None:
        for count, expected in ((None, 1), (1, 1), (3, 1), (8, 4)):
            with self.subTest(count=count):
                self.factory.reset_mock()
                status, _, stderr = self._runWorker(
                    ['--probe', str(self.runtime_path), 'cpu', str(self.model_path)],
                    cpu_count=count,
                )
                self.assertEqual(status, 0, stderr)
                self.assertEqual(self.factory.call_args.kwargs['cpu_threads'], expected)

    def testCudaRegistersAndKeepsDllDirectoriesBeforeImport(self) -> None:
        directories = [
            self.runtime_path / 'nvidia' / 'cublas' / 'bin',
            self.runtime_path / 'nvidia' / 'cudnn' / 'lib',
        ]
        for directory in directories:
            directory.mkdir(parents=True)
        handles = [MagicMock(), MagicMock()]
        order = Mock()
        register = Mock(side_effect=handles)
        order.attach_mock(register, 'register')
        order.attach_mock(self.preload, 'preload')
        order.attach_mock(self.supported_types, 'compute')
        original_import = builtins.__import__

        def checkImport(
            name: str,
            globals: dict[str, Any] | None = None,
            locals: dict[str, Any] | None = None,
            fromlist: tuple[str, ...] = (),
            level: int = 0,
        ) -> ModuleType:
            if name == 'ctranslate2':
                self.assertEqual(register.call_count, 2)
                self.preload.assert_called_once_with(directory='')
                order.ctranslate2()
            return original_import(name, globals, locals, fromlist, level)

        def initializeVad() -> None:
            self.assertEqual(register.call_count, 2)
            self.preload.assert_called_once_with(directory='')
            for handle in handles:
                handle.__exit__.assert_not_called()

        self.vad_model.side_effect = initializeVad
        with (
            patch.object(os, 'add_dll_directory', register, create=True),
            patch.object(builtins, '__import__', side_effect=checkImport),
        ):
            status, stdout, stderr = self._runWorker([
                '--probe',
                str(self.runtime_path),
                'cuda',
                str(self.model_path),
            ])
        self.assertEqual(status, 0, stderr)
        self.assertEqual(json.loads(stdout), {'ready': True})
        self.assertEqual(
            {call.args[0] for call in register.call_args_list},
            {str(directory) for directory in directories},
        )
        self.assertEqual(
            [call[0] for call in order.mock_calls],
            ['register', 'register', 'preload', 'ctranslate2', 'compute'],
        )
        for handle in handles:
            handle.__exit__.assert_called_once()
        self.device_count.assert_called_once_with()
        self.assertEqual(self.factory.call_args.kwargs['compute_type'], 'float16')

    def testCudaSupportsOnnxRuntimeWithoutPreloadFunction(self) -> None:
        self.runtime_modules['onnxruntime'] = SimpleNamespace()
        status, _, stderr = self._runWorker(['--probe', str(self.runtime_path), 'cuda'])
        self.assertEqual(status, 0, stderr)
        self.device_count.assert_called_once_with()

    def testProbeRejectsMissingCudaDevice(self) -> None:
        self.device_count.return_value = 0
        status, stdout, stderr = self._runWorker([
            '--probe',
            str(self.runtime_path),
            'cuda',
        ])
        self.assertEqual(status, 1)
        self.assertEqual(stdout, '')
        self.assertIn('No CUDA device available', stderr)

    def testProbeRejectsUnsupportedComputeType(self) -> None:
        self.supported_types.return_value = {'float32'}
        status, stdout, stderr = self._runWorker([
            '--probe',
            str(self.runtime_path),
            'cpu',
        ])
        self.assertEqual(status, 1)
        self.assertEqual(stdout, '')
        self.assertIn('cpu does not support int8', stderr)

    def testProbeRejectsMissingSileroModel(self) -> None:
        self.vad_model.side_effect = FileNotFoundError('silero_vad.onnx')
        status, stdout, stderr = self._runWorker([
            '--probe',
            str(self.runtime_path),
            'cpu',
        ])
        self.assertEqual(status, 1)
        self.assertEqual(stdout, '')
        self.assertIn('Traceback', stderr)
        self.assertIn('silero_vad.onnx', stderr)

    def testProbeRejectsIncompatibleAudioDecoder(self) -> None:
        self.decode_audio.side_effect = TypeError(
            "open() got an unexpected keyword argument 'metadata_errors'"
        )
        status, stdout, stderr = self._runWorker([
            '--probe',
            str(self.runtime_path),
            'cpu',
        ])
        self.assertEqual(status, 1)
        self.assertEqual(stdout, '')
        self.assertIn('metadata_errors', stderr)

    def testProbeRejectsBrokenWhisperModel(self) -> None:
        self.factory.side_effect = RuntimeError('invalid model')
        status, stdout, stderr = self._runWorker([
            '--probe',
            str(self.runtime_path),
            'cpu',
            str(self.model_path),
        ])
        self.assertEqual(status, 1)
        self.assertEqual(stdout, '')
        self.assertIn('invalid model', stderr)

    def testMissingTokenizerFailsBeforeModelCanDownload(self) -> None:
        (self.model_path / 'tokenizer.json').unlink()
        status, stdout, stderr = self._runWorker(self._inferenceArguments())
        self.assertEqual(status, 1)
        self.assertEqual(stdout, '')
        self.assertIn('tokenizer.json', stderr)
        self.factory.assert_not_called()

    def testTranscriptionWritesGenuineWordsAndSegmentProgress(self) -> None:
        self.decode_audio.return_value = [0.0] * (16000 * 5)
        first_word = SimpleNamespace(
            word='\u4f60', start=0.25, end=0.75, probability=0.95
        )
        segments = [
            SimpleNamespace(start=0.25, end=2.0, text='\u4f60', words=[first_word]),
            SimpleNamespace(start=2.0, end=4.0, text='plain text', words=None),
        ]
        self.model.transcribe.return_value = (
            iter(segments),
            SimpleNamespace(duration=10.0, language='zh'),
        )
        self.result_path.write_text('old result', encoding='utf-8')
        original_replace = os.replace

        def replaceResult(source: str, target: str) -> None:
            self.assertEqual(Path(source).parent, self.result_path.parent)
            self.assertNotEqual(Path(source), self.result_path)
            self.assertEqual(self.result_path.read_text(encoding='utf-8'), 'old result')
            self.assertIn('\u4f60', Path(source).read_text(encoding='utf-8'))
            original_replace(source, target)

        with patch.object(os, 'replace', side_effect=replaceResult) as replace:
            status, stdout, stderr = self._runWorker(self._inferenceArguments())
        self.assertEqual(status, 0, stderr)
        self.model.transcribe.assert_called_once_with(
            [0.0] * (16000 * 5),
            language=None,
            word_timestamps=True,
            vad_filter=False,
            condition_on_previous_text=True,
            initial_prompt=None,
            beam_size=5,
        )
        self.assertEqual(
            [json.loads(line) for line in stdout.splitlines()],
            [
                {
                    'segments': [
                        {
                            'start': 0.25,
                            'end': 0.75,
                            'text': '\u4f60',
                            'words': [
                                {
                                    'word': '\u4f60',
                                    'start': 0.25,
                                    'end': 0.75,
                                    'probability': 0.95,
                                }
                            ],
                        },
                        {
                            'start': 2.0,
                            'end': 4.0,
                            'text': 'plain text',
                            'words': [],
                        },
                    ],
                    'progress': 1.0,
                },
            ],
        )
        self.assertEqual(
            json.loads(self.result_path.read_text(encoding='utf-8')),
            {
                'segments': [
                    {
                        'start': 0.25,
                        'end': 0.75,
                        'text': '\u4f60',
                        'words': [
                            {
                                'word': '\u4f60',
                                'start': 0.25,
                                'end': 0.75,
                                'probability': 0.95,
                            }
                        ],
                    },
                    {'start': 2.0, 'end': 4.0, 'text': 'plain text', 'words': []},
                ],
                'language': 'zh',
            },
        )
        replace.assert_called_once()
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def testGrowingContextRevisesLyricsBeforeNextInference(self) -> None:
        self.decode_audio.return_value = [0.0] * (16000 * 20)
        printer = Mock(wraps=print)
        windows = [
            [self._segment(' Wrong', 4.9, 5.2)],
            [self._segment(' Right across the boundary', 4.8, 6.1)],
            [
                self._segment(' Right across the boundary', 4.8, 6.1),
                self._segment(' Next sentence', 17.0, 19.0),
            ],
        ]
        call_count = 0

        def transcribe(
            audio: list[float], **kwargs: Any
        ) -> tuple[Iterator[SimpleNamespace], SimpleNamespace]:
            nonlocal call_count
            self.assertEqual(printer.call_count, call_count)
            self.assertIsNone(kwargs['language'])
            self.assertTrue(kwargs['word_timestamps'])
            segments = windows[call_count]
            if call_count:
                self.assertTrue(printer.call_args.kwargs['flush'])
            call_count += 1
            return iter(segments), SimpleNamespace(language='en')

        self.model.transcribe.side_effect = transcribe
        with patch.object(whisper_worker, 'print', printer, create=True):
            status, stdout, stderr = self._runWorker(self._inferenceArguments())
        self.assertEqual(status, 0, stderr)
        records = [json.loads(line) for line in stdout.splitlines()]
        self.assertEqual(
            [len(call.args[0]) for call in self.model.transcribe.call_args_list],
            [16000 * 12, 16000 * 18, 16000 * 20],
        )
        self.assertEqual(records[0]['segments'][0]['text'], ' Wrong')
        self.assertEqual(
            records[1]['segments'][0]['text'], ' Right across the boundary'
        )
        self.assertEqual(len(records[1]['segments']), 1)
        self.assertEqual(len(records[-1]['segments']), 2)
        self.assertEqual(records[-1]['segments'][0]['start'], 4.8)
        self.assertEqual(records[-1]['progress'], 1.0)
        self.assertEqual(
            json.loads(self.result_path.read_text(encoding='utf-8'))['segments'],
            records[-1]['segments'],
        )

    def testWindowAdvancesAtSentenceEndAndKeepsUnfinishedWords(self) -> None:
        self.decode_audio.return_value = list(range(16000 * 40))
        first = self._segment(' First sentence', 1.0, 9.0)
        second = self._segment(' Second sentence', 10.0, 20.0)
        windows = [
            [first],
            [first, self._segment(' unfinished', 10.0, 17.0)],
            [first, second],
            [first, second, self._segment(' partial', 21.0, 29.0)],
            [self._segment(' completed tail', 1.0, 15.0)],
            [
                self._segment(' completed tail', 1.0, 15.0),
                self._segment(' final', 17.0, 20.0),
            ],
        ]
        self.model.transcribe.side_effect = [
            (iter(segments), SimpleNamespace(language='en')) for segments in windows
        ]
        status, stdout, stderr = self._runWorker(self._inferenceArguments())
        self.assertEqual(status, 0, stderr)
        calls = self.model.transcribe.call_args_list
        self.assertEqual([call.args[0][0] for call in calls], [0] * 4 + [320000] * 2)
        self.assertEqual(
            [len(call.args[0]) / 16000 for call in calls], [12, 18, 24, 30, 16, 20]
        )
        self.assertEqual(
            calls[-1].kwargs['initial_prompt'], ' First sentence Second sentence'
        )
        records = [json.loads(line) for line in stdout.splitlines()]
        final = records[-1]['segments']
        self.assertEqual(
            [segment['text'] for segment in final],
            [' First sentence', ' Second sentence', ' completed tail', ' final'],
        )
        self.assertEqual([segment['start'] for segment in final], [1, 10, 21, 37])
        self.assertEqual(final[2]['words'][0]['end'], 35.0)

    def testLongSentenceIsNotCutAtThirtySeconds(self) -> None:
        self.decode_audio.return_value = [0.0] * (16000 * 36)
        self.model.transcribe.side_effect = [
            (
                iter([self._segment(' long sentence', 1.0, end)]),
                SimpleNamespace(language='en'),
            )
            for end in (12, 18, 24, 30, 35)
        ]
        status, stdout, stderr = self._runWorker(self._inferenceArguments())
        self.assertEqual(status, 0, stderr)
        final = json.loads(stdout.splitlines()[-1])['segments']
        self.assertEqual(len(final), 1)
        self.assertEqual(final[0]['end'], 35.0)

    def testOverlappingSentenceIsReprocessedWithoutCuttingItsFirstWord(self) -> None:
        self.decode_audio.return_value = list(range(16000 * 36))
        first = self._segment(' first', 1.0, 20.0)
        overlap = self._segment(' overlapping', 19.8, 29.0)
        self.model.transcribe.side_effect = [
            (iter([first]), SimpleNamespace(language='en')),
            (iter([first]), SimpleNamespace(language='en')),
            (iter([first]), SimpleNamespace(language='en')),
            (iter([first, overlap]), SimpleNamespace(language='en')),
            (
                iter([first, self._segment(' complete overlapping', 19.8, 35.0)]),
                SimpleNamespace(language='en'),
            ),
        ]
        status, stdout, stderr = self._runWorker(self._inferenceArguments())
        self.assertEqual(status, 0, stderr)
        self.assertEqual(self.model.transcribe.call_args.args[0][0], 0)
        final = json.loads(stdout.splitlines()[-1])['segments']
        self.assertEqual(len(final), 2)
        self.assertEqual(final[1]['words'][0]['start'], 19.8)

    def testIntroLanguageIsReevaluatedAndFalseLyricsCanBeRemoved(self) -> None:
        self.decode_audio.return_value = [0.0] * (16000 * 24)
        self.model.transcribe.side_effect = [
            (
                iter([self._segment(' false intro', 0.0, 5.0)]),
                SimpleNamespace(language='it'),
            ),
            (iter([]), SimpleNamespace(language='it')),
            (
                iter([self._segment(' Actual lyrics', 19.0, 23.0)]),
                SimpleNamespace(language='en'),
            ),
        ]
        status, stdout, stderr = self._runWorker(self._inferenceArguments())
        self.assertEqual(status, 0, stderr)
        for call in self.model.transcribe.call_args_list:
            self.assertIsNone(call.kwargs['language'])
            self.assertIsNone(call.kwargs['initial_prompt'])
        records = [json.loads(line) for line in stdout.splitlines()]
        self.assertEqual(records[1]['segments'], [])
        result = json.loads(self.result_path.read_text(encoding='utf-8'))
        self.assertEqual(result['language'], 'en')
        self.assertEqual(result['segments'][0]['text'], ' Actual lyrics')

    def testLongIntroRetainsContextAndMakesForwardProgress(self) -> None:
        self.decode_audio.return_value = list(range(16000 * 42))
        self.model.transcribe.side_effect = [
            *[(iter([]), SimpleNamespace(language='it')) for _ in range(4)],
            (
                iter([self._segment(' voice', 13.0, 18.0)]),
                SimpleNamespace(language='en'),
            ),
            (
                iter([self._segment(' voice continues', 13.0, 24.0)]),
                SimpleNamespace(language='en'),
            ),
        ]
        status, stdout, stderr = self._runWorker(self._inferenceArguments())
        self.assertEqual(status, 0, stderr)
        calls = self.model.transcribe.call_args_list
        self.assertEqual([call.args[0][0] for call in calls], [0] * 4 + [288000] * 2)
        final = json.loads(stdout.splitlines()[-1])['segments']
        self.assertEqual(final[0]['start'], 31.0)
        self.assertEqual(final[0]['end'], 42.0)

    def testProgressUsesDecodedAudioDuration(self) -> None:
        self.decode_audio.return_value = [0.0] * (16000 * 2)
        for end in (1.0, 2.0):
            with self.subTest(end=end):
                words = [
                    SimpleNamespace(word='test', start=0.0, end=end, probability=1.0)
                ]
                self.model.transcribe.return_value = (
                    iter([
                        SimpleNamespace(
                            start=0.0,
                            end=end,
                            text='test',
                            words=words,
                        )
                    ]),
                    SimpleNamespace(duration=10.0, language='en'),
                )
                status, stdout, stderr = self._runWorker(self._inferenceArguments())
                self.assertEqual(status, 0, stderr)
                self.assertEqual(
                    json.loads(stdout),
                    {
                        'segments': [
                            {
                                'start': 0.0,
                                'end': end,
                                'text': 'test',
                                'words': [
                                    {
                                        'word': 'test',
                                        'start': 0.0,
                                        'end': end,
                                        'probability': 1.0,
                                    }
                                ],
                            }
                        ],
                        'progress': 1.0,
                    },
                )

    def testEmptyAudioWritesEmptySegments(self) -> None:
        status, stdout, stderr = self._runWorker(self._inferenceArguments())
        self.assertEqual(status, 0, stderr)
        self.assertEqual(stdout, '')
        self.assertEqual(
            json.loads(self.result_path.read_text(encoding='utf-8')),
            {'segments': [], 'language': 'unknown'},
        )

    def testRuntimeDiagnosticsGoToStderr(self) -> None:
        def initializeVad() -> None:
            print('runtime diagnostic')

        self.vad_model.side_effect = initializeVad
        status, stdout, stderr = self._runWorker([
            '--probe',
            str(self.runtime_path),
            'cpu',
        ])
        self.assertEqual(status, 0, stderr)
        self.assertEqual(json.loads(stdout), {'ready': True})
        self.assertEqual(stderr, 'runtime diagnostic\n')

    def testGeneratorFailurePreservesExistingResult(self) -> None:
        def segments() -> Iterator[SimpleNamespace]:
            yield SimpleNamespace(start=0.0, end=1.0, text='one', words=[])
            raise RuntimeError('decoder failed')

        self.model.transcribe.return_value = (
            segments(),
            SimpleNamespace(duration=10.0, language='en'),
        )
        self.result_path.write_text('old result', encoding='utf-8')
        status, _, stderr = self._runWorker(self._inferenceArguments())
        self.assertEqual(status, 1)
        self.assertIn('decoder failed', stderr)
        self.assertEqual(self.result_path.read_text(encoding='utf-8'), 'old result')
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def testAtomicReplaceFailureCleansTemporaryFile(self) -> None:
        self.result_path.write_text('old result', encoding='utf-8')
        with patch.object(os, 'replace', side_effect=PermissionError('locked result')):
            status, _, stderr = self._runWorker(self._inferenceArguments())
        self.assertEqual(status, 1)
        self.assertIn('locked result', stderr)
        self.assertEqual(self.result_path.read_text(encoding='utf-8'), 'old result')
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def testInvalidArgumentsReturnTracebackAndNoReadyMessage(self) -> None:
        for arguments in (
            [],
            ['--probe'],
            ['--probe', str(self.runtime_path), 'mps'],
            ['--probe', str(self.root / 'missing'), 'cpu'],
            ['--probe', str(self.runtime_path), 'cpu', str(self.model_path), 'extra'],
        ):
            with self.subTest(arguments=arguments):
                status, stdout, stderr = self._runWorker(arguments)
                self.assertEqual(status, 1)
                self.assertEqual(stdout, '')
                self.assertIn('Traceback', stderr)


if __name__ == '__main__':
    unittest.main()
