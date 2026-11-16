# Testing SouthsideMusic

Run these commands in the repository root on Windows. Use the existing lockfile:

```powershell
uv sync --frozen
uv run --frozen python scripts/test_harness.py --help
uv run --frozen python scripts/test_harness.py prepare-worker
```

The harness runs the application against generated music, covers, lyrics, library
data, and a local HTTP gateway. The `core` provider scenarios exercise the real
LLM SDKs through that gateway with fixture responses. The `online` group exercises
real public NetEase searches and records a dependency skip when the external
service is unavailable. NetEase credentials and paid LLM keys are unnecessary.

## Commands

```powershell
uv run --frozen python scripts/test_harness.py prepare-worker
uv run --frozen python scripts/test_harness.py unit --report-dir .harness-results/unit
uv run --frozen python scripts/test_harness.py list
uv run --frozen python scripts/test_harness.py run --group core --group online --report-dir .harness-results/run
uv run --frozen python scripts/test_harness.py run --case boot --report-dir .harness-results/boot
uv run --frozen python scripts/test_harness.py manual
uv run --frozen python scripts/test_harness.py gateway
```

Use `list` to get the current scenario IDs and groups. Repeat `--group` to select
several groups; repeat `--case` to select individual scenarios. The standalone
gateway prints its local base URL. Stop it with `Ctrl+C` when finished.

The default report directory is `.harness-results/unit` for `unit` and
`.harness-results/run` for `run`.

## Legacy unit suite

`unit` collects every `tests/test_*.py` file and runs each file in its own Python
subprocess. It executes guarded `unittest.main()` scripts, assert-based `main()`
scripts, and test functions without an entry point, including
`test_lyrics_viewer_translation.py`. For a file without an entry point, reflection
collects its unittest classes and module-level `test_*` functions. Importing a
test file alone does not count as a successful run.

Source, tests, scripts, fonts, icons, images, and project metadata are copied into
an isolated temporary root. Test working directories, source-relative paths, and
temporary-file locations point into that root. The suite does not copy the user's
configuration, favorites, caches, `torch_venv`, or `freethreaded_venv`. A supplied
worker interpreter remains outside the sandbox. Available FFmpeg and FFprobe
binaries are staged for tests that require bundled executable paths.

A file has a 180-second limit. Timeouts kill its process tree and the runner
continues to the next file. An empty collection, a file that executes zero tests,
an assertion failure, a unittest failure, a nonzero exit, a crash, or a timeout
makes the command return a nonzero exit code. Abrupt exits are failures even when
the operating-system exit code is zero. Existing unittest dependency skips remain
visible in the report.

The report directory contains:

| Output | Contents |
| --- | --- |
| `unit-results.json` | Per-file status, exit code, duration, test/check count, skip count, errors, and log paths |
| `unit-results.xml` | JUnit-compatible per-file results with captured stdout and stderr |
| `unit-summary.md` | Human-readable results and failures |
| `unit-logs/<test>.stdout.log` | Complete subprocess stdout |
| `unit-logs/<test>.stderr.log` | Complete subprocess stderr and tracebacks |
| `unit-logs/<test>.json` | Child execution result, when the child completed its reporting |

The XML counts test files as cases. JSON reports also record the tests or checks
executed inside each file. A main-only assert script is one check; unittest and
reflected functions report their individual test counts.

To validate the collector itself:

```powershell
uv run --frozen python tests/test_harness_unit_runner.py
uv run --frozen ruff check tests/harness/unit_runner.py tests/test_harness_unit_runner.py
uv run --frozen ruff format --check tests/harness/unit_runner.py tests/test_harness_unit_runner.py
```

## Scenario groups and prerequisites

| Group | Coverage | Execution |
| --- | --- | --- |
| `core` | Application startup, library, playback, lyrics, exports, desktop integration, local provider SDK requests, tool flows, and Markdown | Required in CI |
| `online` | Real public NetEase song and playlist searches | Selected in CI; dependency skip when the external service is unavailable |
| `native` | Local audio devices and native Windows media integration | Optional local group |
| `whisper` | Locally installed Whisper runtime and model | Optional local group |

The catalog exposes these scenario IDs:

| Scenarios | Checks |
| --- | --- |
| `boot`, `playback`, `queue` | Real entry point, decode/PCM playback, pause/resume, track changes, and queue operations |
| `search_ui`, `favorites_ui` | Search controls, pagination, empty results, playlist search, filtering, and batch selection |
| `lyrics`, `lyric_editor` | LRC, translations, YRC, desktop lyrics, timing edits, and lyric-cache persistence |
| `settings`, `effects` | Configuration persistence, volume, speed, pitch, equalizer, and processing worker |
| `export_original`, `export_effects`, `export_video` | Audio exports and tags, effects, and real lyric video verified with FFprobe |
| `smtc`, `bridge` | Fixture media state/control callbacks and real bidirectional WebSocket packets |
| `llm_chat`, `llm_responses`, `llm_responses_tools`, `llm_anthropic` | Provider SSE streams and Responses tool rounds rendered in the real chat controls |
| `llm_explain_code`, `llm_search`, `llm_no_results` | Source reads/searches and song search tools, including pagination and empty results |
| `llm_organize`, `llm_confirm`, `llm_cancel` | Playlist edits, explicit tool confirmation, and cancellation without writes |
| `llm_lyrics`, `llm_usage_guard`, `llm_unknown_tool`, `llm_read_boundary` | Lyric tools, usage prerequisites, invalid tool names, and source-read boundaries |
| `llm_http_error`, `llm_disconnect`, `llm_timeout`, `llm_stop`, `llm_history` | Provider errors, interrupted/late streams, cancellation, and conversation history |
| `md_<fixture-id>` | Each Markdown fixture's rendering and text expectations; `list` prints every generated ID |
| `online_search` | Real NetEase searches |
| `native_audio`, `native_smtc` | Physical audio output and actual Windows SMTC |
| `whisper` | Local-model transcription of real audio |

Automatic skips are only for unavailable dependencies: the external service for
`online`, physical devices for `native`, and a local runtime/model for `whisper`.
Fake-provider SDK and Markdown scenarios belong to `core`. An assertion,
exception, crash, timeout, or malformed response fails a selected scenario.
Missing core prerequisites fail startup; they must not turn the core group into a
successful skipped run.

Install FFmpeg and FFprobe and put their executable directory on `PATH`, or use
the application's existing `ffmpeg/bin` directory. The scenario sandbox stages
those binaries. Prepare a separate Python 3.14 free-threaded worker using the same
installed versions as the frozen normal environment:

```powershell
uv python install 3.14t
if ($LASTEXITCODE -ne 0) { throw 'Free-threaded Python installation failed' }
$workerBase = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
$workerRoot = Join-Path $workerBase 'southside-harness-worker'
uv venv --python 3.14t $workerRoot
if ($LASTEXITCODE -ne 0) { throw 'Worker environment creation failed' }
$workerPython = Join-Path $workerRoot 'Scripts/python.exe'
$workerRequirements = @(uv run --frozen python -c "from importlib.metadata import version; print('\n'.join(f'{name}=={version(name)}' for name in ('numpy', 'scipy', 'pillow', 'pydub', 'audioop-lts')))")
if ($LASTEXITCODE -ne 0) { throw 'Reading installed worker package versions failed' }
uv pip install --python $workerPython @workerRequirements
if ($LASTEXITCODE -ne 0) { throw 'Worker dependency installation failed' }
& $workerPython -c "import sys; assert sys._is_gil_enabled() is False"
if ($LASTEXITCODE -ne 0) { throw 'Worker Python is not running free-threaded' }
$env:SOUTHSIDE_FREE_THREADED_PYTHON = $workerPython
```

If the worker is already prepared, set `SOUTHSIDE_FREE_THREADED_PYTHON` to its
`Scripts/python.exe` before invoking the harness. The pinned worker packages are
`numpy`, `scipy`, `pillow`, `pydub`, and `audioop-lts`; this setup does not upgrade
the project's dependency versions.

```powershell
uv run --frozen python scripts/test_harness.py run --group native
$env:SOUTHSIDE_HARNESS_WHISPER_AUDIO = 'D:\fixtures\speech.wav'
$env:SOUTHSIDE_HARNESS_WHISPER_MODEL_DIR = 'D:\models\faster-whisper-tiny'
uv run --frozen python scripts/test_harness.py run --group whisper
```

Whisper uses existing local audio, model files, and runtime packages. It does not
download them during a test. Missing inputs are reported as dependency skips.

## Manual application checks

`manual` opens a separate desktop control panel. Select a case and use **Load
scenario**, **Start**, and **Run selected** to reproduce an automatic test. Edit
the gateway JSON and input text, choose a provider, then use **Send AI task**,
**Confirm**, **Cancel plan**, or **Stop stream**. **Snapshot** and **Screenshot**
capture the current application state. The CLI's `gateway --scenario file.json`
also serves a saved scenario independently.

The panel opens the real application in an isolated fixture environment. Exercise
the visible controls directly: browse the fixture library, select tracks, pause
and resume, seek, switch tracks, change playback settings, toggle translations,
show desktop lyrics, and export a song or lyric video. Open Onerad and test
streaming and tool confirmation with the fixture providers. Check the rendered
Markdown, copyable text, scrolling, and cancellation in the conversation view.

Close the harness application when finished so its subprocesses and temporary
root can be cleaned up. Inspect the manual report directory for application logs
and any captured failure evidence. The optional native group is the place to
check physical audio output and actual Windows media controls.

`latex`, `math`, and `tex` fences parse their contents as Markdown. Bare `x^2`
remains text; formulas require `$...$`, `\(...\)`, `$$...$$`, or `\[...\]`.
Empty fences generate no formulas. Each shared Markdown case runs both in the
renderer/widget tests and inside the launched application.

`run` writes `results.json`, `junit.xml`, `index.html`, and `summary.md` after
each case. The `sessions/` directory contains application logs, thread stacks,
gateway request transcripts, screenshots, exported files, and isolated persisted
state. Assertion failures fail the command; dependency skips retain their reason.

## Gateway scenario format

The local gateway consumes scenario objects with this structure:

```json
{
  "models": ["harness-model"],
  "chunk_size": 2,
  "rounds": [
    {
      "text": "Checking the current song.",
      "tools": [
        {
          "id": "call_lyrics",
          "name": "get_current_lyrics",
          "arguments": {}
        }
      ],
      "expect": {
        "model": "harness-model",
        "stream": true,
        "user_contains": "lyrics"
      }
    },
    {
      "text": "The lyric tool returned its result.",
      "expect": {
        "messages": [
          {
            "role": "tool",
            "tool_call_id": "call_lyrics"
          }
        ]
      }
    }
  ]
}
```

`models` controls model-list responses. `chunk_size` is a positive integer and
controls streamed text and tool-argument fragments. Each POST consumes one round;
model-list requests do not consume rounds.

| Round field | Meaning |
| --- | --- |
| `text` | Assistant response text |
| `tools` | Tool calls with a required `name` and `arguments` object and an optional `id` |
| `expect` | Required request-body subset; arrays match expected items in order |
| `expect.user_contains` | A string or list of strings that must appear in user content |
| `status` | HTTP status, default `200`; error statuses exercise provider failure handling |
| `delay` | Nonnegative delay in seconds before a response or each streamed event |
| `disconnect` | Close the connection during the response to exercise interrupted streams |

Use `messages` in request expectations for Chat Completions and Anthropic; use
`input` for Responses, including `function_call_output` items for tool results.
Unexpected endpoints, extra POSTs, and unmet expectations are recorded as gateway
errors. Request bodies are journaled in `requests.jsonl` under the gateway report
directory.

The gateway supports `/models`, `/v1/models`, `/v1/chat/completions`,
`/v1/responses`, and `/v1/messages`. Chat Completions and Responses support tool
rounds. Anthropic scenarios support text responses; an Anthropic tool response
fails explicitly. Use the printed base URL and the provider's normal path rules
when configuring a client.

## CI and build gate

`.github/workflows/ci.yml` retains push-to-master, pull-request, and manual
triggers, and is also reusable through `workflow_call`. Its check job keeps the
compile, Ruff lint, and formatting checks, then runs the complete `unit` command.
A separate Windows harness job installs the frozen dependencies and FFmpeg,
creates the free-threaded worker in `RUNNER_TEMP`, and runs `core` plus `online`.

Both jobs attempt report uploads and GitHub step summaries even after a failed
step. Unit artifacts include only the JSON/XML reports, summary, and per-file
logs from `.harness-results/unit`. Harness artifacts come from
`.harness-results/run`; copied source/assets and worker environments are
excluded. Both artifacts retain files for 14 days and have separate names.
Summaries read `.harness-results/unit/unit-summary.md` and
`.harness-results/run/summary.md`. Local reports are ignored by Git through
`/.harness-results/`.

`build.yml` calls the reusable CI workflow in its `quality` job. Its build job
requires `quality` to succeed. The existing release workflow already requires
that build, so a failing check blocks packaging and the dependent release. Local
test commands do not dispatch builds or publish releases.
