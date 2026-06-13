# AGENTS.md — SouthsideMusic

## Project Overview

PySide6 desktop music player (Netease Cloud Music client). Windows-only, compiled
with Nuitka and distributed via Inno Setup. Python 3.13+ required.

## Build, Run, Lint & Test

```bash
python src/main.py                        # run directly
build.bat                                 # full build (Nuitka + Inno Setup)
python scripts/create_icon.py             # generate .ico from icon.png

ruff check .                              # lint
ruff format --check .                     # format check
ruff format .                             # auto-format
mypy src/                                 # type check (no config file; runs bare)
```

Config: `.ruff.toml` (`line-length=88`, `indent-width=4`, `quote-style=single`).

No test framework exists yet. `src/test.py` is a manual API exploration script.
If adding tests:

```bash
python -m pytest tests/                   # all tests
python -m pytest tests/test_foo.py        # single file
python -m pytest tests/test_foo.py -k "test_name"  # single test
```

## Project Structure

```
src/
  main.py          # entry point, app init, exception hook, logging setup
  imports.py       # centralized re-exports (PySide6, typing, qfluentwidgets, events)
  core/            # domain logic (audio, config, lyrics, models, theme, icons)
  views/           # PySide6 UI (pages, cards, windows, custom widgets)
  services/        # event bus, update checking
  pyncm/           # forked Netease Cloud Music API client
data/              # runtime caches (music, images, lyrics)
config.json        # persisted user config (hand-editable JSON)
```

## Code Style

### Imports

- `from __future__ import annotations` in every file (PEP 604 union syntax).
- Import PySide6/Qt classes **from `imports`**, not directly from PySide6:
  ```python
  from imports import QWidget, QVBoxLayout, QTimer, Qt, Signal, event_bus
  ```
- `imports.py` re-exports PySide6 widgets, QtCore, QtGui, typing, qfluentwidgets,
  and all event bus members. qfluentwidgets may also be imported directly.
- Third-party libraries imported directly (`import numpy as np`).
- `if TYPE_CHECKING:` block for type-only imports to avoid circular imports.
- Standard library first, then third-party, then project modules.

### Naming

- **Files**: `snake_case.py`.
- **Classes / Qt classes**: `PascalCase` (`AudioPlayer`, `SearchPage`).
- **Public methods**: `camelCase` (`loadConfig`, `addToFolder`).
- **Private methods**: `_snake_case` (`_write_cache`, `_do`).
- **Variables**: `snake_case` (`current_index`, `play_speed`).
- **Constants**: `UPPER_CASE` (`CONFIG_PATH`, `MAX_CHUNK_THREADS`).
- **Event names**: `UPPER_CASE` strings (`SONG_CHANGED`, `PRE_THEME_CHANGED`).

### Types & Comments

- All function signatures must have type annotations (params and return).
- Use `| None` (not `Optional`). Exception: pre-existing calls in pyncm/.
- `@dataclass` for config/data objects, `ABC` + `@abstractmethod` for interfaces.
- `cast()` for fields populated after construction, `@override` for method overrides.
- English comments only, lowercase except proper nouns. Sparse and brief.
- Docstrings use `"""single line."""` for public APIs.

### Error Handling & Logging

- Log via `_logger.exception(e)` for full tracebacks.
- Unhandled exceptions caught by global `sys.excepthook` in `main.py` → `ErrorPopupWindow`.
- Config I/O falls back gracefully — corrupt config → defaults.
- Cache ops handle `FileNotFoundError` and `PermissionError` explicitly.
- Every module: `_logger = logging.getLogger(__name__)`.
- Configured once in `main.py` via `logging.basicConfig(level=DEBUG)` with a custom
  `LogHandler` that routes messages to the UI.
- `hijackStreams()` in `main.py` captures `sys.stdout`/`sys.stderr` through logging.
- Other files must NOT call `logging.basicConfig`.

## Architecture & Qt Conventions

- Subclass `QWidget` (or concrete widgets) — NOT `QObject` directly for UI.
- Connect signals in `__init__` after layout setup.
- Signals declared as class attributes: `fetchedSongs = Signal(list)`.
- `shiboken6.isValid()` before accessing potentially deleted Qt objects.
- `@Property(type)` decorator for Qt property system (animations, style bindings).
- QSS colors must be lowercase (`'white'`, `'black'`).

### Core Architecture

- **Event Bus**: `services/events/` — pub/sub. `event_bus.subscribe(EVENT, listener)`,
  `event_bus.emit(EVENT, *args)`. Events are string constants re-exported through
  `imports`. Thread-safe. All inter-component communication uses this.
- **AppContext**: bag of app-wide dependencies (player, cfg, pages) passed to
  view/page constructors. `def __init__(self, ctx: AppContext) -> None:`.
- **Backend**: `MusicServiceBackend` (ABC) → `NeteaseCloudMusicBackend`.
- **Views**: `QWidget` subclasses that build their own layout in `__init__`.
  Cards (`song_card`, `folder_card`) are composable QWidgets used within pages.

### Background Work & Lazy Loading

Three async patterns, depending on complexity:
| Pattern | When | Example |
|---|---|---|
| `asyncTask(fn, args, mwindow)` | Simple fire-and-forget network calls | Search, lyrics fetch |
| `asyncDownload(mwindow).download(url, path)` | Downloading files with progress | Song/avatar download |
| `QThread` + `moveToThread()` | Long-lived workers with structured lifecycle | `DownloadingManager`, `TaskManager` |

After background work, update UI via: `self._mwindow.addScheduledTask(lambda: ...)` to queue a
callable on the main thread's event loop.

Cards defer heavy work until visible. Set `self.load = False`, then `QTimer.singleShot()`
to poll `parent.viewport().visualItemRect(self)`. On enter, set `self.load = True` and load.

## KISS: Keep It Stupid Simple

The most important rule. The author (Adreno) writes simple Qt, not enterprise Java.
After writing code, ask: *Would Adreno have written this?* If you used a pattern
that needs a Wikipedia article to explain, delete it and try again. Load the
`adreno-perspective` skill before writing complex logic. Reference files:
`src/views/search_page.py`, `src/views/error_popup.py`.

### What NOT to Write

- No abstract factories — `if/else` on a config string is fine.
- No singleton metaclasses — module-level globals are fine.
- No middleware chains — Qt signals exist. Connect them directly.
- No DI containers — `AppContext` is a bag of references.
- No state machines — a `bool` or string enum is fine.
- No batch/diff/reconciliation — `self.lst.clear()` and rebuild.
- No caching layers — `data/` already handles audio/image/lyrics caches.
- No observer/mediator wrappers — the event bus IS the pub/sub system.

### Other Conventions

- Paths: `os.path.join()` for filesystem, `pathlib.Path` for manipulation.
- Config: plain JSON at project root, loaded/saved by `core/config.py`.
- Cache: audio/images/lyrics under `data/`. Songs identified by track ID (int or str).
- Icons: `SouthsideIcon` enum in `core/icons.py` extends `FluentIconBase`.
