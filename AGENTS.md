# AGENTS.md - SouthsideMusic

## Project Overview

SouthsideMusic is a cross-platform PySide6 desktop client for NetEase CloudMusic:
streaming playback, word-by-word lyrics, loudness normalization, desktop lyrics,
local favorites, song export, and auto-update support. Windows is the primary
packaged target; macOS and Linux run from source.

The app is packaged with Nuitka and distributed through Inno Setup on Windows.
Runtime caches live under `data/`; persistent user settings live in `config.json`.

Primary docs are `docs/README.md` and `docs/README_zh.md`. No Cursor rules
(`.cursor/rules/` or `.cursorrules`) and no Copilot instructions
(`.github/copilot-instructions.md`) exist at the time this file was written.

## Environment

- Target OS: Windows, macOS, Linux. Windows-only integrations must be guarded by
  `sys.platform`/`os.name` and degrade to a cross-platform fallback.
- Project metadata in `pyproject.toml` says Python `>=3.13`; prefer that over
  older docs that mention Python 3.12+ / 3.12.7.
- `uv.lock` is present; prefer `uv run ...` when available.
- On Windows, initial workspace setup is automated by `python setup_workspace.py`;
  on macOS/Linux use `uv sync`.
- Build output goes to `build.result\raw\`; Windows installers land in
  `build.result\installer\`, and macOS/Linux packages in `build.result\`.
- `torch_venv/` and `freethreaded_venv/` are local virtualenvs. Never search,
  count, or edit files inside them; scope every `find`/`rg` away from them.

## Commands

```bash
python setup_workspace.py                 # bootstrap dependencies/tooling
uv run src/main.py                        # run from source (preferred)
python src/main.py                        # run if environment is already active
uv run python build.py                    # full cross-platform build/package flow
build.bat                                 # Windows wrapper for build.py
python scripts/create_icon.py             # regenerate icons/app.ico

uv run ruff check .                       # lint all files
uv run ruff format --check .              # check formatting
uv run ruff format .                      # format files
uv run mypy src/                          # type check source tree
python -m py_compile src/main.py          # quick syntax/import smoke check
```

`build.py` deletes old outputs and dispatches by host platform. Windows runs
Nuitka on `launcher.py`, copies embedded Python/resources/source, regenerates
the icon, then runs Inno Setup if `ISCC.exe` is installed (`build.bat` wraps
this path). macOS builds a `.app` bundle and `.dmg`; Linux builds a portable
`.tar.gz` and, when `dpkg-deb` exists, a `.deb`. Without Inno Setup, raw
portable files remain in `build.result\raw\`.

## Tests

`tests/` is the real suite. pytest is **not** installed, so do not invoke it.

- `unittest`-based (each has a `__main__` guard, so one file runs directly with
  `uv run python tests/<file>.py`; discover with
  `uv run python -m unittest discover -s tests`): `test_equalizer_curve.py`,
  `test_lyric_sources.py`, `test_music_bridge.py`, `test_rediscovery.py`,
  `test_search_line_edit.py`, `test_streaming_sample_rate.py`.
- Plain assert scripts with a `main()`: `test_lyric_formats.py`,
  `test_llm_lyric_tools.py`, `test_lyrics_viewer_translation.py`.
- Broken at import: `test_crossfade_playback.py`, `test_audio_player_pipeline.py`,
  `test_audio_player_sample_rate.py` still do `from imports import QApplication`,
  but that shim was removed in commit f8cb20c. Fix the import when touching them.

`src/test.py` is a manual API exploration script, not part of the suite.

For small non-test changes, prefer narrow validation first: `python -m py_compile <file>`, then `uv run ruff check <file>`, then broader lint/type checks if useful.

## Project Structure

```text
src/
  main.py          # app entry, QApplication setup, logging, excepthook
  core/            # audio, config, models, lyrics, theme, icons, backends
  services/        # event bus and update checks
  views/           # PySide6 UI pages, cards, windows, widgets
  ncm/             # forked NetEase CloudMusic API client
tests/             # unittest / assert-script suite (no pytest)
scripts/           # icon generation, labs, reverse-engineering helpers
docs/              # English/Chinese user documentation
data/              # runtime caches for music, images, lyrics, temp data
icons/, images/    # packaged UI resources
fonts/             # bundled HarmonyOS Sans SC font assets
config.json        # hand-editable persisted user config
```

Reference style files: `src/views/search_page.py`, `src/views/error_popup.py`.

## Import Style

- Use `from __future__ import annotations` when the file already follows it.
- Import Qt/PySide6 classes directly from their PySide6 module. There is no
  re-export shim: `src/imports.py` was removed in commit f8cb20c.

```python
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import InfoBar, TransparentPushButton
```

- Event constants and `event_bus` come from `services.events`:

```python
from services.events import PLAYLIST_CHANGED, event_bus
```

- Direct third-party imports are fine (`numpy`, `requests`, `qfluentwidgets`).
- Use `if TYPE_CHECKING:` for type-only imports that could create circular imports.
- Keep imports grouped as standard library, third-party, then project imports.

## Formatting

- Ruff config is `.ruff.toml`: line length 88, indent width 4, single quotes.
- Keep edits ASCII unless existing content or UI copy requires non-ASCII.
- Keep QSS color names lowercase (`'white'`, `'black'`).
- Prefer small, local diffs. Do not reformat unrelated files.
- Avoid large abstractions; this codebase favors direct PySide code.
- Write no comments and no docstrings. Names and structure carry the meaning.

## Constants

A constant must earn its name. The single most common failure here is hoisting a
value that is written once and read once.

- If a literal has exactly one use, write the literal at the use site. Do not
  introduce a module-level or class-level name for it.
- Add a constant only when at least two real call sites need the same value, or
  when the value is an identity that outlives any single call site.
- Module-level parameter defaults are still single-use. `def cleanup(\n
  max_age_minutes: int = 24 * 60,\n)` does not need a
  `DEFAULT_MAX_AGE_MINUTES` above it unless a caller passes it explicitly.
- A class attribute read from `self.X` in exactly one method is a single-use
  constant. Inline it as a literal.
- Keep the name when the literal is opaque and the name *is* the documented
  meaning: `STD_INPUT_HANDLE = -10`, `AES.BLOCKSIZE`, `winreg.REG_SZ`. Inlining
  these turns a documented API value into a magic number.
- Keep the name for lookup tables and long format/prompt strings read once
  (`LEVEL_BY_BITRATE`, `ONERAD_SYSTEM_PROMPT`) — the name is the documentation
  and the literal is not self-evident at the call site.
- Inline the name when the literal is self-evident at the use site:
  `APP_DISPLAY_NAME = 'Southside Music'` reads better as the literal.

A registry is not a constant. Before deleting anything that looks unused,
verify how it is read:

- `SouthsideIcon.FAV = 'fav'` and friends are looked up **by string value**
  (`_icon_map = {icon.value: icon for icon in SouthsideIcon}`, then
  `bindIcon(widget, 'fav')`, `getQIcon('drop_up')`). Deleting an enum member
  that greps as "unused" raises `KeyError` at runtime.
- Event names in `services/events/events.py` are the cross-component vocabulary.
- Before inlining or deleting, search the whole project:
  `rg -wn <NAME> src tests scripts` — a hit in `tests/` counts.
- A name may also be reachable through a string literal, `getattr`, or a dict
  key. Grep the string value too, not just the identifier.

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
- Event constants and `event_bus` are imported from `services.events`.
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
- Do not add a constant for a single use. See "Constants" above.
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

<!-- aoci:begin -->
## AOCI 仓库认知

AOCI 为本仓库维护一个稳定、可版本化、可增量更新的仓库级认知层，供模型跨任务复用对系统的理解。

`aoci.txt` 是面向模型的结构化认知索引。它以每个受管理文件、数据库表或其他受管理对象一条独立 Entry 的方式，用符号标签与 F/R/A/S 语义表达对象的核心职责、重要关系、对外契约，以及理解或修改系统时必须知道的非显然约束和设计决策。

Header、目录段和全部 Entry 共同组成完整仓库索引，可以覆盖前端、后端、配置、数据库结构及其他受管理内容。受管理内容发生变化时，通常只需维护受影响的认知条目，不需要重新生成整个索引。

AOCI 提供系统架构、对象职责、重要关系、对外契约和关键约束的高密度视图。

### 工作原理

AOCI 采用“模型生成、模型读取”的认知闭环。

Header、Entry 和 Curation 语义的创作只按当前机器签发的 Plan 与实时 Guide 执行；由 Host 模型基于当前绑定证据独立完成。

Entry 的语义必须来自模型对真实证据的理解。不得仅依据路径、文件名、扩展名、AST、符号列表、依赖扫描、正则、固定模板或规则引擎推导、预填、拼接或改写索引语义。

对 Fresh Bootstrap，只按当前机器签发的 Plan 和实时 Guide 执行。当它们要求创作时，Host 模型创作 Root、Meta、Tag 和 F/R/A/S，提供 authoring-run 声明，并把它绑定到 Plan、Evidence 与完整 Candidate。不得要求 AOCI 填写 `origin=host_model`、制造 Receipt 或把程序生成的 Framework 当作语义。本文件不自行重建 Onboarding 流程。内部批次不是用户决策；只有遇到既有批准边界或真实的安全、漂移、CAS、Recovery 条件才停止。

### 最小使用入口

- `aoci_rules`：取得当前AOCI版本的会话运行合同。
- `aoci_overview`：建立或恢复本仓库的完整认知。
- `aoci_maintain`：受管理对象达到最终稳定状态后检查认知是否需要维护。
- `aoci_update_entry`：提交与当前证据和源码摘要绑定的完整语义更新批次。
- `aoci_report`：仅当当前布局和工具状态支持时，在证据不足、无法可靠生成语义时登记待办，不猜写。

其他MCP工具、CLI命令、参数和专项流程，以当前工具说明、Guide和 `--help` 返回内容为准，不在本文件中重复完整手册。

本区块只规定仓库接入、认知使用和收尾原则。`aoci_rules` 承载当前会话合同，Guide实时输出承载当前Plan的执行顺序与停点，工具Schema、Spec和Validator承载机器结构与判据；Prompt、Description、README和静态文档不能覆盖这些机器事实。

### 建立、生成和恢复认知

1. 每个新的 Agent Run 开始时，应先判断：

   - 本仓库是否已经存在可用的完整AOCI索引；
   - 当前上下文中是否已有与本仓库根、当前索引版本和当前AOCI服务相匹配，并且模型仍可可靠使用的完整仓库认知。

2. 仓库已经存在可用的完整索引，但当前Run没有可靠完整认知时，先调用 `aoci_rules`，再调用 `aoci_overview`。

   完整认知仍可靠时直接复用。局部不确定本身不要求机械重读系统全貌。可靠认知建立后，默认以它作为仓库级系统上下文，只读取当前任务直接需要的源码、测试、配置与契约，只有某个缺失的具体事实可能改变回答或验证结论时才扩大证据范围；AOCI不能替代修改对象与验证行为所需的直接源码证据。

   本Run从已知Host上下文压缩恢复时（包括宿主注入的压缩摘要），必须把此前模型认知视为不可靠。压缩handoff不得保留或摘要正式Whole-Index，也不得保留或摘要任何Overview Header、Entry、Chunk、Challenge或Attestation正文；只能保留安全续接所需的receipt身份、未完成write或Recovery状态，以及立即重载指令。复制进handoff的Whole-Index语义或receipt不能证明恢复后模型的当前认知可靠。若当前上下文已无法可靠保留运行合同，先调用 `aoci_rules`。继续业务任务前，使用 `refresh_reasons=["context_compaction"]` 和新的 `refresh_event_id` 调用普通完整Whole-Index `aoci_overview`（不设置 `check_only` 或设为false）；不得使用 `check_only` 或认知probe。原样跟随每个 `next_cursor` 直到 `completed=true`，确认交付，并且只基于新交付正文提交一次Attestation。完成这次新的完整传输后，即使Attestation为partial或fail也消费该generation，并按既有合同继续source-bound任务，不再自动调用第二次Overview。若该请求返回 `delivery_mode=capacity_stop`，说明Whole-Index装不进本Host的上下文：不得重试或调小 `chunk_tokens`；该停止消费本generation，任务按source-bound继续，且不作无保留的完整系统认知声明。

   AOCI可以针对 `context_compaction`、项目 `cognition_refresh_threshold` 下的机器 `semantic_threshold` 或主要 `phase_transition` 提供checkpoint与认知状态事实。只需要这些紧凑事实时使用 `check_only=true`；这些事实只向Agent提供建议，不替模型决定是否需要系统全貌。

   Agent显式调用普通 `aoci_overview`（未设置 `check_only` 或为false）时，只要能形成一致的CognitionSet，AOCI必须完整交付请求scope。不得因为已有receipt、阈值未达到或没有待处理刷新原因而抑制正文；唯一例外是待处理压缩刷新的上述容量停止。正式认知Dirty或Stale时仍交付正文，但必须标记不可靠。存在未决恢复或无法形成一致snapshot时失败关闭，不返回混合正文。

   普通Overview返回 `continuation_required=true` 时，必须原样提交 `next_cursor` 并自动继续到 `completed=true`。不得询问用户、开始业务任务或给出阶段性系统结论。每块单独一次工具调用；不得在一个脚本里连取多块或合并其输出。缺块、重复、乱序、cursor失败、Index变化或`chunk_tokens`变化时停止本次认知链。Attestation完成前不得用Memory、源码、Spec、`aoci.txt`、历史会话、scope、search或Entry读取修补或补充Whole-Index认知。Challenge ordinal是正式Entry序列中的1-based位置；Header内容、注释、空行、Section/Overview/Chunk Marker、Receipt与Metadata均不计数，Chunk Receipt ordinal使用同一序列。Attestation必须原样回绑本次Challenge发布的当前`index_sha256`、`entry_sequence_sha256`与`entry_count`；旧Index、旧Entry序列、旧数量或旧Attestation均无效。完整链结束后只正式提交一次既有模型认知Attestation；同一响应只允许一次不改变语义答案的JSON Schema或字段格式修正。对象、Tag或F不匹配即失败且认知吸收不确定，不得语义重试或旁路补答。首次认知失败时还不得执行Root/Meta、Migration、全局布局或其他未重新绑定的系统级决策。上下文压缩刷新若传输完整、认知身份不变、治理对齐且没有Recovery或第三方冲突，即使Attestation为partial或fail也消耗该refresh generation，并继续原任务，不再自动重读Overview。`system_mastery_percent`只自评系统框架——架构、职责、强关系、稳定外部契约以及高熵安全和维护约束——不表示完整实现或运行实况知识；机器索引覆盖率必须分开。默认只向用户输出由本次真实覆盖率、Challenge、块数、Token和掌握度生成的规定成功或失败一句话。Host截断某一块时，用同一cursor重取一次并核对 `chunk_sha256`，服务端返回完全相同的字节。仍被截断才停止，提示用户把 `overview_delivery.chunk_tokens` 设置为更小的合法值后重新开始，不得自动修改。

   加法认知等级必须与严格证明字段分开解释。`delivery_verified`表示已加载Index且Host交付已确认，但完整认知验证仍未完成；应表达为“已加载且交付已验证”，不得描述为“没有认知”或“没有理解系统”。`cognition_verified`要求Attestation通过（Challenge至少80%的ordinal完全正确且对象身份至多失手一处），`cognition_governed`还要求治理对齐。通用完整读取失败句只用于真实交付故障。

   当Overview响应包含可选`cognition-state/v2`投影时，必须分别解释各维度。其Level止于`model_cognition_usable`；`strict_attestation_verified`、`governance_aligned`与`current_system_cognition_reliable`都是独立状态，绝不参与该Level。ordinal、对象身份、Tag或核心F不匹配可以导致严格Attestation失败，而模型认知仍然可用；不得仅凭这种不匹配就宣称模型没有理解系统。只有`current_system_cognition_reliable=true`允许无保留地声称当前完整系统认知可靠。投影缺失时继续使用上述Legacy解释。

   普通的只读审计、分析、检查、不修改代码或不提交、不push，不自动等于严格零写入，也不改变上述认知有效性判断。Codex Memory和历史Skill只能辅助恢复经验、用户偏好与调查方向，不能替代与当前仓库根、索引摘要、AOCI服务身份和认知范围匹配的当前认知收据；项目AGENTS和当前AOCI身份在AOCI状态上优先于历史Memory。

   只有用户明确禁止Ledger、元数据、`.aoci`运行资产及任何文件写入时，才按严格零写入处理。若必要的认知建立与该边界冲突，必须报告冲突并请求用户裁决或建议使用隔离副本，不得静默以Memory替代当前仓库认知。

3. 仓库没有可用的完整索引，或当前只有最小骨架、Header不完整、Entries未完成、必要Curation尚未裁决时，如果需要建立正式完整AOCI索引，先取得 `aoci_rules`，然后进入当前AOCI Guide。由Guide依据仓库真实状态决定下一阶段并完成必要安全步骤。

   `aoci_maintain` 不替代索引建立流程。

   不在本文件中自行重建或硬编码完整索引生成状态机。

4. 在长程任务中，模型负责保留当前认知收据并正确使用刷新门禁：

   - Host报告上下文压缩或模型已知系统全貌丢失时，执行上述强制 `context_compaction` 重载规则；AOCI不能自行推断Host事件；
   - 进入真正的主要阶段时声明 `phase_transition`，不得把函数、测试运行或小步骤当作阶段；
   - 在有用的稳定检查点通过 `check_only=true` 取得机器语义计数；
   - 除已知压缩的强制重载外，由Agent判断当前任务是否需要再次显式获取指定scope或完整Overview；
   - 在维护和对齐完成前，保留AOCI报告的Dirty或Stale可靠性状态。

### 任务收尾与认知维护

5. 纯只读问答、分析、版本核验，或没有产生受AOCI管理对象变化的任务，不需要调用维护工具。当前AOCI版本是任意`aoci_overview` check_only或`aoci_maintain`响应里的`cognition_receipt.mcp_service_version`；二进制路径是项目`.mcp.json`里的`command`，CLI不必在PATH上。

6. 发生受AOCI管理对象变化时，待其达到本次任务的最终稳定状态后，只调用一次 `aoci_maintain`。不要在每次中间修改后逐文件维护。

7. 若维护结果返回真实语义候选，Host 模型必须基于每个候选绑定的对象和必要证据，独立创作完整标签与F/R/A/S更新。通过 `aoci_update_entry` 一次提交当前机器签发批次的完整候选集合，同时原样保留每项 `source_sha256`、`candidate_id` 与对应domain批次身份。`max_entries`只限制单次请求和原子事务，不限制logical plan、Whole-Index或Managed Scope。`remaining`非零时，在当前批次成功Apply后重新调用Maintain并从新preimage继续；绝不能为满足transport上限缩减Index覆盖或自行截取返回批次。

   没有足够证据且当前布局支持 `aoci_report` 时，使用它而不猜测、套用模板或为消除待办而生成缺乏证据的认知。

8. 必须遵守工具返回的结构化状态和安全边界：

   - `repair_required`：只修复明确命中的候选，再重新提交当前机器签发的完整批次；
   - `stopped`：结束当前写入尝试并检查 `failed_step`、错误、正式写入证据与Recovery。auto模式下，已证明零写入则记录closure并重新Plan；完整Intent和可证明postimage则Resume；策略要求Rollback且preimage可证明则精确恢复后重新Plan。只有证据不足、第三方正式字节冲突、需要审批或外部动作，或命中其他真实安全边界时，才停止整个用户任务；
   - 冲突、审批、人工裁决、权限和安全信号不得忽略；
   - 已经对齐后不得重复维护或重复写入；`refresh_ready_for_overview` 是checkpoint事实，由Agent决定是否为下一阶段请求普通完整Overview。

   维护完成后如果又修改了任何受管理对象，之前的维护结果失效，应在新的最终稳定状态重新完成收尾。

9. 用户只限制业务文件范围，但没有明确禁止仓库托管资产时，AOCI托管资产可以在收尾阶段为保持认知一致而更新，并应在审计和提交中与业务文件区分。

   用户明确禁止修改 `aoci.txt`、`.aoci`、元数据或任何额外文件时，以用户限制为准，不得写入，并如实报告剩余不一致。

### 专项流程

初始化、完整索引生成、Header生成、Entries生成、数据库结构索引、Curation、人工评审和故障恢复，只按当前AOCI Guide或工具在对应阶段返回的指令、命令和安全停点执行。

不预加载、不猜测，也不自行重建这些专项流程。平台调用方式、请求格式、批次上限、审批规则、索引格式细节和恢复步骤由对应Guide、工具说明、模型Prompt和CLI帮助按需提供。
<!-- aoci:end -->
