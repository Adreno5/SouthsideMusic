from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
import wave
from contextlib import ExitStack, redirect_stdout
from typing import Any


def main() -> int:
    temporary_path: str | None = None
    try:
        arguments = sys.argv[1:]
        probe = arguments[:1] == ['--probe']
        if probe:
            arguments = arguments[1:]
        if (probe and len(arguments) not in (2, 3)) or (
            not probe and len(arguments) != 5
        ):
            raise ValueError(
                'Usage: --probe RUNTIME_PATH DEVICE [MODEL_PATH] or '
                'RUNTIME_PATH MODEL_PATH DEVICE AUDIO_PATH RESULT_PATH'
            )
        runtime_path = os.path.abspath(arguments[0])
        device = arguments[1] if probe else arguments[2]
        if device not in ('cpu', 'cuda'):
            raise ValueError('DEVICE must be cpu or cuda')
        if not os.path.isdir(runtime_path):
            raise FileNotFoundError(runtime_path)
        if probe:
            model_path = arguments[2] if len(arguments) == 3 else None
        else:
            model_path = arguments[1]
        if model_path is not None:
            model_path = os.path.abspath(model_path)
            tokenizer_path = os.path.join(model_path, 'tokenizer.json')
            if not os.path.isfile(tokenizer_path):
                raise FileNotFoundError(tokenizer_path)
        cpu_threads = max(1, min(8, (os.cpu_count() or 2) // 2))
        sys.path.insert(0, runtime_path)
        for name in (
            'HF_HUB_OFFLINE',
            'TRANSFORMERS_OFFLINE',
            'HF_HUB_DISABLE_TELEMETRY',
        ):
            os.environ[name] = '1'
        for name in (
            'OMP_NUM_THREADS',
            'OPENBLAS_NUM_THREADS',
            'MKL_NUM_THREADS',
            'NUMEXPR_NUM_THREADS',
        ):
            os.environ[name] = str(cpu_threads)
        os.environ['TOKENIZERS_PARALLELISM'] = 'false'
        output = sys.stdout
        with redirect_stdout(sys.stderr), ExitStack() as dll_directories:
            if device == 'cuda' and hasattr(os, 'add_dll_directory'):
                for directory, _, _ in os.walk(os.path.join(runtime_path, 'nvidia')):
                    if os.path.basename(directory) in ('bin', 'lib'):
                        dll_directories.enter_context(os.add_dll_directory(directory))

            import onnxruntime

            if device == 'cuda':
                preload_dlls = getattr(onnxruntime, 'preload_dlls', None)
                if preload_dlls is not None:
                    preload_dlls(directory='')

            import ctranslate2
            from faster_whisper import WhisperModel
            from faster_whisper.audio import decode_audio
            from faster_whisper.vad import get_vad_model

            get_vad_model()
            if device == 'cuda' and ctranslate2.get_cuda_device_count() < 1:
                raise RuntimeError('No CUDA device available')
            compute_type = 'int8' if device == 'cpu' else 'float16'
            if compute_type not in ctranslate2.get_supported_compute_types(device):
                raise RuntimeError(f'{device} does not support {compute_type}')
            with tempfile.TemporaryDirectory() as audio_directory:
                audio_path = os.path.join(audio_directory, 'probe.wav')
                with wave.open(audio_path, 'wb') as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(16000)
                    wav_file.writeframes(b'\0\0' * 1600)
                if len(decode_audio(audio_path)) == 0:
                    raise RuntimeError('Audio decoder returned no samples')
            if model_path is not None:
                model = WhisperModel(
                    model_path,
                    device=device,
                    compute_type=compute_type,
                    cpu_threads=cpu_threads,
                    num_workers=1,
                    local_files_only=True,
                )
            if probe:
                print(json.dumps({'ready': True}), file=output, flush=True)
                return 0
            audio = decode_audio(arguments[3])
            language: str | None = None
            window_start = 0
            window_end = min(len(audio), 12 * 16000)
            committed: list[dict[str, Any]] = []
            result_segments: list[dict[str, Any]] = []
            while window_start < len(audio):
                segments, info = model.transcribe(
                    audio[window_start:window_end],
                    language=None,
                    word_timestamps=True,
                    vad_filter=False,
                    condition_on_previous_text=True,
                    initial_prompt=(
                        ''.join(segment['text'] for segment in committed[-2:])[-200:]
                        or None
                    ),
                    beam_size=5,
                )
                pending: list[dict[str, Any]] = []
                for segment in segments:
                    words: list[dict[str, Any]] = []
                    for word in segment.words or []:
                        word_start = float(word.start) + window_start / 16000
                        word_end = float(word.end) + window_start / 16000
                        words.append({
                            'word': str(word.word),
                            'start': word_start,
                            'end': word_end,
                            'probability': float(word.probability),
                        })
                    if words:
                        start = min(word['start'] for word in words)
                        end = max(word['end'] for word in words)
                        text = ''.join(word['word'] for word in words)
                    else:
                        start = float(segment.start) + window_start / 16000
                        end = float(segment.end) + window_start / 16000
                        text = str(segment.text)
                    pending.append({
                        'start': start,
                        'end': end,
                        'text': text,
                        'words': words,
                    })
                if pending:
                    language = str(info.language)
                updated = committed + pending
                if updated != result_segments:
                    result_segments = updated
                    print(
                        json.dumps({
                            'segments': result_segments,
                            'progress': window_end / len(audio),
                        }),
                        file=output,
                        flush=True,
                    )
                if window_end == len(audio):
                    break
                if window_end - window_start >= 30 * 16000:
                    for index, segment in enumerate(pending):
                        if segment['end'] > window_end / 16000 - 8:
                            break
                        if (
                            index + 1 < len(pending)
                            and pending[index + 1]['start'] < segment['end']
                        ):
                            break
                        committed.append(segment)
                        window_start = max(window_start, round(segment['end'] * 16000))
                    if not pending:
                        window_start = window_end - 12 * 16000
                window_end = min(
                    len(audio), max(window_end + 6 * 16000, window_start + 12 * 16000)
                )
            result_path = os.path.abspath(arguments[4])
            with tempfile.NamedTemporaryFile(
                mode='w',
                encoding='utf-8',
                dir=os.path.dirname(result_path),
                prefix=os.path.basename(result_path) + '.',
                suffix='.tmp',
                delete=False,
            ) as result_file:
                temporary_path = result_file.name
                json.dump(
                    {'segments': result_segments, 'language': language or 'unknown'},
                    result_file,
                    ensure_ascii=False,
                )
            os.replace(temporary_path, result_path)
            temporary_path = None
        return 0
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 1
    finally:
        if temporary_path is not None:
            try:
                os.remove(temporary_path)
            except (FileNotFoundError, PermissionError):
                pass


if __name__ == '__main__':
    sys.exit(main())
