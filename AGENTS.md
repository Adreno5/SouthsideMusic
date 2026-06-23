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
  pyncm/           # forked NetEase CloudMusic API client
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
- Add comments only for non-obvious behavior; keep them English and sparse.

## Types

- Annotate all parameters and return types in new or changed functions.
- Use PEP 604 unions (`str | None`) except when preserving existing `pyncm/` style.
- Use `@dataclass` for config/data containers.
- Use `ABC` / `@abstractmethod` for explicit backend interfaces only.
- Use `cast()` for fields populated after construction when needed.
- Use `@override` where parent methods are intentionally overridden.
- Keep public docstrings short: `"""single line."""`.

## Naming

- Files/modules: `snake_case.py`.
- Classes and Qt widgets: `PascalCase` (`AudioPlayer`, `SearchPage`).
- Public methods: project-style `camelCase` (`loadConfig`, `addToFolder`).
- Private helpers: `_snake_case`.
- Variables and attributes: `snake_case`.
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

- The author favors simple Qt code over enterprise patterns.
- Before adding abstraction, ask whether a bool, direct signal, or helper is enough.
- Avoid factories, DI containers, state machines, caching layers, observer wrappers.
- Prefer `self.lst.clear()` and rebuild over complex diff/reconciliation logic.
- Fix root causes, but keep the edit surface local.

# Southside Dual Workspace(Only in Codex)

Python project:
D:\PythonProjects\SouthsideMusic

Java project:
D:\downloads\Southside-Legacy

跨端协议任务必须先读两个项目的入口说明，再确认 Python 发出的 JSON 字段和 Java 消费字段是否一致。
