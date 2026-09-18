# AGENTS.md - SouthsideMusic

## Project Overview

SouthsideMusic is a Windows-only PySide6 desktop client for NetEase CloudMusic:
streaming playback, word-by-word lyrics, loudness normalization, desktop lyrics,
local favorites, song export, and auto-update support.

The app is packaged with Nuitka and distributed through Inno Setup. Runtime caches
live under `data/`; persistent user settings live in `config.json`.

Primary docs are `docs/README.md` and `docs/README_zh.md`. No Cursor rules
(`.cursor/rules/` or `.cursorrules`) and no Copilot instructions
(`.github/copilot-instructions.md`) exist at the time this file was written.

## Environment

- Target OS: Windows.
- Project metadata in `pyproject.toml` says Python `>=3.13`; prefer that over
  older docs that mention Python 3.12+ / 3.12.7.
- `uv.lock` is present; prefer `uv run ...` when available.
- Initial workspace setup is automated by `python setup_workspace.py`.
- Build output goes to `build.result\raw\` and optionally `build.result\installer\`.

## Commands

```bash
python setup_workspace.py                 # bootstrap dependencies/tooling
uv run src/main.py                        # run from source (preferred)
python src/main.py                        # run if environment is already active
build.bat                                 # full Windows build/package flow
python scripts/create_icon.py             # regenerate icons/app.ico

uv run ruff check .                       # lint all files
uv run ruff format --check .              # check formatting
uv run ruff format .                      # format files
uv run mypy src/                          # type check source tree
python -m py_compile src/main.py          # quick syntax/import smoke check
```

`build.bat` deletes old outputs, runs Nuitka on `launcher.py`, copies embedded
Python/resources/source, regenerates the icon, then runs Inno Setup if `ISCC.exe`
is installed. Without Inno Setup, raw portable files remain in `build.result\raw\`.

## Tests

There is no formal test suite yet. `src/test.py` is a manual API exploration
script, not pytest. Do not invent a test framework unless explicitly needed.

If tests are added, use these commands:

```bash
uv run python -m pytest tests/                     # all tests
uv run python -m pytest tests/test_foo.py          # one test file
uv run python -m pytest tests/test_foo.py -k name  # one test by expression
uv run python -m pytest tests/test_foo.py::test_x  # one exact test
```

For small non-test changes, prefer narrow validation first: `python -m py_compile <file>`, then `uv run ruff check <file>`, then broader lint/type checks if useful.

## Project Structure

```text
src/
  main.py          # app entry, QApplication setup, logging, excepthook
  imports.py       # centralized imports/re-exports for Qt, typing, events
  core/            # audio, config, models, lyrics, theme, icons, backends
  services/        # event bus and update checks
  views/           # PySide6 UI pages, cards, windows, widgets
  ncm/             # forked NetEase CloudMusic API client
docs/              # English/Chinese user documentation
data/              # runtime caches for music, images, lyrics, temp data
icons/, images/    # packaged UI resources
fonts/             # bundled HarmonyOS Sans SC font assets
config.json        # hand-editable persisted user config
```

Reference style files: `src/views/search_page.py`, `src/views/error_popup.py`.

## Import Style

- Use `from __future__ import annotations` when the file already follows it.
- Import Qt/PySide6 classes from `imports`, not directly from PySide6:

```python
from imports import QTimer, QVBoxLayout, QWidget, Qt, Signal, event_bus
```

- `src/imports.py` re-exports PySide6 classes, typing helpers, qfluentwidgets,
  and event bus members.
- Direct third-party imports are fine for non-Qt libraries (`numpy`, `requests`).
- Use `if TYPE_CHECKING:` for type-only imports that could create circular imports.
- Keep imports grouped as standard library, third-party, then project imports.
- qfluentwidgets may be imported directly when existing code does so.

## Formatting

- Ruff config is `.ruff.toml`: line length 88, indent width 4, single quotes.
- Keep edits ASCII unless existing content or UI copy requires non-ASCII.
- Keep QSS color names lowercase (`'white'`, `'black'`).
- Prefer small, local diffs. Do not reformat unrelated files.
- Avoid large abstractions; this codebase favors direct PySide code.
- Write no comments and no docstrings. Names and structure carry the meaning.

## Types

- Annotate all parameters and return types in new or changed functions.
- Use PEP 604 unions (`str | None`) except when preserving existing `ncm/` style.
- Use `@dataclass` for config/data containers.
- Use `ABC` / `@abstractmethod` for explicit backend interfaces only.
- Use `cast()` for fields populated after construction when needed.
- Use `@override` where parent methods are intentionally overridden.
- Write no docstrings, including one-liners.

## Naming

- Files/modules: `snake_case.py`.
- Classes and Qt widgets: `PascalCase` (`AudioPlayer`, `SearchPage`).
- Public methods: `camelCase` (`getValue`, `saveConfig`, `loadFavorites`).
- Private methods: `_camelCase` (`_loadCache`, `_validateInput`).
- Variables and attributes: `snake_case`.
- Private variables: `_snake_case`.
- Qt Signals: `camelCase` (`fetchedSongs`, `onEndingNoSound`).
- Constants: `UPPER_CASE`.
- Event constants: `UPPER_CASE` strings (`SONG_CHANGED`, `PRE_THEME_CHANGED`).

## Error Handling And Logging

- Logging modules should define `_logger = logging.getLogger(__name__)`.
- Do not call `logging.basicConfig()` outside `src/main.py`.
- Log exceptions with `_logger.exception(e)` when a traceback matters.
- Global unhandled exceptions route through `sys.excepthook` to `ErrorPopupWindow`.
- Config I/O should fall back gracefully; corrupt config should not crash launch.
- Cache/file operations should handle `FileNotFoundError` and `PermissionError`
  when user files or generated cache paths are involved.
- `hijackStreams()` in `main.py` redirects stdout/stderr into logging/UI output.

## Qt And UI Conventions

- UI classes should subclass `QWidget` or a concrete widget/window, not `QObject`.
- Build layouts in `__init__`, then connect signals after widget/layout setup.
- Declare Qt signals as class attributes, e.g. `fetchedSongs = Signal(list)`.
- Check `shiboken6.isValid()` before accessing widgets Qt may have deleted.
- Use `@Property(type)` for Qt properties used by animations or style bindings.
- Preserve existing visual language; do not redesign UI unless asked.

## Architecture

- `AppContext` is a simple dependency bag passed as `__init__(self, ctx)`.
- Backend abstraction is `MusicServiceBackend` -> `NeteaseCloudMusicBackend`.
- Use the event bus in `services/events/` for cross-component communication:
  `event_bus.subscribe(EVENT, listener)` and `event_bus.emit(EVENT, *args)`.
- Event constants are re-exported through `imports`.
- Views build their own layouts; cards like `song_card` are composable widgets.
- Keep ownership and signal wiring obvious; prefer direct `if/else` over factories.

## Background Work

- Use `asyncTask(fn, args, mwindow)` for simple fire-and-forget work.
- Use `asyncDownload(mwindow).download(url, path)` for downloads with progress.
- Use `QThread` + `moveToThread()` for long-lived structured workers.
- Schedule UI updates on the main thread with `self._mwindow.addScheduledTask(...)`.
- Lazy cards usually set `self.load = False`, poll visibility, then load once.

## Repository Hygiene

- Keep changes minimal and behavior-preserving unless the user asks otherwise.
- Never revert unrelated user changes in a dirty worktree.
- Do not commit, branch, amend, reset, or push unless explicitly requested.
- Do not edit generated/cache/build output unless the task is about those files.
- Prefer `os.path.join()` in existing code; use `pathlib.Path` only where it fits.
- Respect license/user docs: this is personal, research, non-commercial software.

## KISS Rules

- No comments, no docstrings. If a block needs explaining, rewrite the block.
- No over-engineering. Ship the smallest thing that works and stop there.
- Do not add a helper, class, module, config key, or event for a single caller.
- Do not add options, flags, fallbacks, or abstractions "for later".
- Extract shared code only when two real callers already need it.
- Keep functions linear: no state machines, no plugin registries, no indirection.
- The author favors simple Qt code over enterprise patterns.
- Before adding abstraction, ask whether a bool, direct signal, or helper is enough.
- Avoid factories, DI containers, state machines, caching layers, observer wrappers.
- Prefer `self.lst.clear()` and rebuild over complex diff/reconciliation logic.
- Fix root causes, but keep the edit surface local.

## Four-Workspace Collaboration (Only in Codex)

These four repositories form one product family, but each repository has a
separate owner boundary and build flow:

| Workspace | Role | Main integration points |
| --- | --- | --- |
| `D:\PythonProjects\SouthsideMusic` | PySide6 music desktop app and local music bridge server | Serves JSON packets at `ws://localhost:15489/`; checks and applies its own GitHub releases |
| `D:\downloads\Southside-Legacy` | Java/Minecraft client | Consumes the local music bridge, uses the remote IRC/auth API, and is the game artifact launched by `southside-launcher` |
| `D:\downloads\southside-launcher` | React/Tauri launcher | Reads release/JDK metadata from `SouthsideLegacyIRC`, downloads the Legacy JAR/version JSON and runtime files, then starts the Java client |
| `D:\WebProjects\SouthsideLegacyIRC` | Flask IRC, auth, subscription, release, and resource service | Owns `southside.top` REST/WebSocket contracts used by Legacy and the launcher; also exposes user-facing download links |

### Integration Map

1. `SouthsideMusic <-> Southside-Legacy` is a local, bidirectional music
   bridge. Python is the WebSocket server and Java is the client. Python sends
   song, lyric, cover, play-state, progress, playlist, and FFT packets; Java
   renders them and sends music-control commands back.
2. `Southside-Legacy <-> SouthsideLegacyIRC` is the remote authentication and
   chat connection. Legacy uses `https://southside.top/api`, obtains a short-lived
   `clientToken`, exchanges it for a one-use WebSocket `ticket`, and connects to
   `/ws`. The server owns account, HWID, passkey/subscription, IRC, and session
   rules.
3. `southside-launcher -> SouthsideLegacyIRC -> Southside-Legacy` is the release
   path. The launcher reads release and JDK resource metadata from the IRC
   service, resolves the JAR/version JSON URLs, downloads dependencies, and
   launches Legacy. Release API shape changes therefore require checking both
   the Flask producer and the TypeScript/Rust consumers.
4. `SouthsideMusic` distribution is currently independent of the Legacy
   launcher release pipeline. The IRC website links users to the Music GitHub
   releases, while Music performs its own update check. Do not merge these two
   update mechanisms by assumption.

### Protocol Boundaries

- Do not confuse the local music bridge with the remote IRC WebSocket. The
  music bridge uses `localhost:15489`, no ticket, and JSON text frames selected
  by `option`. IRC uses the remote `/api` and `/ws` endpoints with
  `clientToken`/one-use `ticket` authentication.
- For local music protocol work, read this file and
  `D:\downloads\Southside-Legacy\AGENTS.md`, then compare the exact Python JSON
  fields with the Java parser. Start with `src/core/ws_server.py`, `src/main.py`,
  `src/views/playing_controller.py`, and `src/views/playing_page.py` on Python;
  start with `SouthsideMusicModule.java` and
  `SouthsideMusicWebsocketClient.java` on Java.
- For IRC/auth work, treat `SouthsideLegacyIRC` as the contract producer and
  Legacy as a consumer. Check `D:\downloads\Southside-Legacy\Api.md`, the Java
  `Verify`/`WebSocketChatClient` code, and the matching Flask blueprints before
  changing fields, status codes, token lifetime, ticket behavior, or message
  types.
- For release work, check both launcher implementations: `server.ts` supports
  the web development path, while `src-tauri/src/lib.rs` owns the packaged local
  desktop path. Keep their release parsing, resource resolution, download, and
  launch behavior aligned with the IRC release endpoints.

### Cross-Workspace Workflow

1. Read the `AGENTS.md` and primary entry documentation in every workspace
   touched by the change. If a workspace has no `AGENTS.md`, use its `README.md`,
   `package.json`, build metadata, and actual entry code.
2. Identify the producer, transport, and consumer before editing. Search exact
   endpoint names, `option` values, and JSON fields instead of inferring a
   contract from UI labels.
3. Keep edits in the owning repository unless the contract changes. When a
   contract changes, update all affected producers and consumers in the same
   task and preserve compatible fallbacks where practical.
4. Validate each touched workspace with its native toolchain:
   `uv run python -m py_compile <file>` / Ruff for Music,
   `.\gradlew.bat compileJava --rerun-tasks` for Legacy,
   `npm run lint` and `npm run build` for the launcher, and
   `uv run python -m unittest discover -s tests` for IRC.
5. For an end-to-end local music check, start SouthsideMusic first, confirm the
   server is listening on port `15489`, then start/connect Legacy and test both
   outbound state packets and inbound controls.
6. Do not run production deployment, publish releases, upload artifacts, or use
   real credentials as part of normal cross-workspace validation unless the
   user explicitly requests that operation.
