[中文版本](README_zh.md)

# Southside Music

> A refined Windows music workstation for NetEase CloudMusic: precision audio control, live lyrics, visual performance tools, and deep desktop integration in one uncompromising player.

> In an age drowning in things that merely work, what's worth using is what earns the name of a real work. True craft lives in the hours no one sees.

## Friendly Links

- [LINUX DO](https://linux.do) - A new ideal community

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/Adreno5/SouthsideMusic)

Southside Music is a Windows-only third-party NetEase CloudMusic desktop client built around a custom audio engine. It combines loudness normalization, speed and pitch control, word-by-word and desktop lyrics, a manual lyric timing editor, spectrum and beat visualization, local favorites, discovery and rediscovery tools, comments, song and lyric-video export, the Onerad assistant, Windows media integration, and the SouthsideClient WebSocket bridge.

> For the story behind the project, read [SouthsideMusic Story](SouthsideMusic_Story.md).

---

## Star History

<a href="https://www.star-history.com/?repos=Adreno5%2FSouthsideMusic&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=Adreno5/SouthsideMusic&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=Adreno5/SouthsideMusic&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=Adreno5/SouthsideMusic&type=date&legend=top-left" />
 </picture>
</a>

---

## Contents

- [Overview](#overview)
- [Highlights](#highlights)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Feature Guide](#feature-guide)
- [Shortcuts and Tips](#shortcuts-and-tips)
- [Documentation](#documentation)
- [Development](#development)
- [License](#license)

---

## Overview

Southside Music is built for people who want NetEase CloudMusic on the desktop without giving up control of playback. After logging in, you can search the catalog, browse cloud playlists, play daily recommendations, and organize songs into local favorite folders.

The player uses its own audio pipeline instead of delegating the listening experience to a web view. Loudness, equalizer, speed, pitch, stereo width, reverb, transitions, silence detection, preloading, lyrics, and visualization are all handled directly by the application.

Heavy work is moved off the interface: decoding, crossfade rendering, loudness analysis, and image decoding run in a free-threaded worker process, and PCM audio is buffered on disk instead of in memory. The installer ships its own runtimes, so no system Python is required at runtime.

Southside Music is an independent, non-commercial project and is not an official NetEase CloudMusic client.

---

## Highlights

### Audio

- Loudness normalization with an adjustable target LUFS
- Playback speed from `0.1x` to `3.0x` with pitch-preserving time stretching
- Independent pitch shift from `-12` to `+12` semitones
- Graphical EQ curve editor with live spectrum feedback
- Optional stereo Haas widening and reverb
- Smart crossfade with adaptive duration, transition curves, tempo and key matching, and automatic gain control
- Optional lyric handoff, which carries the outgoing lyrics into the incoming track instead of blinking to black
- Silent-ending detection with configurable threshold and detection window
- On-the-fly quality switching from standard streams up to lossless, Hi-Res, and Master
- Current-track and next-track preloading for smoother seeking and switching
- Parallel chunked streaming downloads with a configurable thread count
- Selectable audio output device

### Lyrics and Visuals

- LRC line timing and YRC word-level timing
- Word-by-word highlighting when YRC data is available
- Translated lyrics with an immediate display toggle
- Manual lyric editor: paste your own lyrics and tap the beat to produce timing for tracks NetEase cannot time
- Always-on-top desktop lyrics with saved position, top-edge anchoring, and position reset
- Real-time FFT spectrum visualization and beat detection driving visual pulses
- Theme-aware background color mixed from the current cover, with adjustable strength
- Lyric-video export with configurable format, bitrate, frame rate, alignment, line count, background, animation, translation, and audio
- Dark and light themes with English and Simplified Chinese UI

### Library and Export

- NetEase song and playlist search with incremental loading
- Home entries for HeartBeat mode, Private Roam, Private Radar, and Similar Songs
- Daily recommended songs and user cloud playlists in the same sidebar as local folders
- Batch selection, playlist replacement, queue append, removal, and local reordering
- Song export with cover art, lyrics, album, artist, and track metadata
- Export with playback effects baked in, or keep the original file untouched
- Queue-after-current actions across search, favorites, and the playing view
- Song comments with threaded replies, avatars, pagination, and posting
- A rediscovery page that digs up favorites you have not played in a while

### Assistant and Integration

- Onerad assistant panel with streaming output, Markdown and math rendering, and explicit tool-call confirmation
- OpenAI-compatible Chat Completions, OpenAI Responses, and Anthropic providers, with multiple configured providers and model mappings
- API keys encrypted with the Windows Data Protection API
- Windows SMTC integration: playback appears in the system media overlay and responds to media keys
- SouthsideClient WebSocket bridge on port `15489`
- Lyrics, cover, song information, position, play state, and FFT streaming to SouthsideClient
- Play/pause, seek, next, and previous commands from SouthsideClient

### Reliability

- In-app GitHub release checking and in-place update handling
- Startup checks for FFmpeg, Python runtime, audio output, network, and OpenGL
- Guided FFmpeg download when the dependency is missing
- First-launch bootstrap that repairs missing packages in the bundled runtime
- Automatic cleanup of old or oversized downloadable caches
- Unhandled-exception dialog with traceback details, and config loading that falls back to defaults
- Optional debug mode with `F3` debug and `F4` performance overlays plus startup timing reports
- Animation throttling after a configurable idle timeout
- Single-instance launcher that refuses to start twice

---

## Installation

1. Open the [Releases page](https://github.com/Adreno5/SouthsideMusic/releases).
2. Download the latest `SouthsideMusic_<version>_win64_setup.exe`.
3. Run the installer and launch Southside Music.

The installer writes to its own application folder and requires administrator rights to do so. The bundled Python runtime, fonts, and interface resources are installed together, and existing user data under `data\` is left untouched during an upgrade.

The first launch performs a dependency check. If FFmpeg is missing, the dependency window can download and install it for you, and missing Python packages are repaired automatically by the bundled bootstrap.

> Southside Music currently supports Windows only.

---

## Quick Start

### 1. Log In

Use the account area on the Home page or in the sidebar:

- **Cell Phone** - Enter your phone number and verification code.
- **QR Code** - Scan the code with the NetEase CloudMusic app and confirm the login.
- **Cookie** - Paste your `MUSIC_U` cookie value from the browser DevTools (F12 → Application → Cookies → music.163.com).

The app can start with an anonymous session, but recommendations, cloud playlists, and most account features require a NetEase CloudMusic login.

### 2. Find Music

Select the title-bar search box, enter a keyword, and press Enter. Switch between **Songs** and **Playlists** on the Search page. Results continue loading as you scroll.

### 3. Build a Queue

Select a song to play it immediately. On supported song cards, select the cover to queue that song after the current track. A folder or playlist can replace the active queue or be appended to it.

### 4. Organize Favorites

Use **Add folder** in the Local section to create an app-managed favorite folder. Song actions let you add, remove, export, or reorder tracks. Cloud playlists remain available separately under Cloud, and **Daily Recommend** appears above the local folders.

---

## Feature Guide

### Now Playing

Select the bottom playback bar to open the full playing view. It contains the cover, song information, synchronized lyrics, translation controls, the quality switch, comment entry, lyric editor, and lyric-video export. The compact controller remains available for progress, current lyrics, FFT, previous/play/next, and queue access.

Drag the progress line to seek after the track is ready. Open the queue panel to play, reorder, export, repeat, remove, or clear queued tracks.

### Handoff and Crossfade

Crossfade analyzes adjacent tracks and prepares a transition near the current song's ending. Advanced settings expose transition strength, curve, maximum duration, BPM window, tempo matching, key matching, and automatic gain control. Lyric handoff can keep the lyrics running across the transition. Disable crossfade when exact track boundaries are preferred.

### Lyrics and Lyric Video

YRC lyrics use word-level highlighting; tracks without YRC timing fall back to line-level LRC display. Translation can be shown when NetEase provides it.

The lyric editor opens for the current song and has two steps: paste or rewrite the lyrics, then tap Space along with the music to time them. The result is saved for that song and used by the lyrics viewer and desktop lyrics afterwards.

The export button in Now Playing renders synchronized lyrics as `.mp4`, `.av1`, `.mkv`, or `.webm`. Export options include bitrate, frame rate, visible line count, word-level timing, translation, alignment, solid-color background, scroll animation, and whether the original audio is included.

### Desktop Lyrics

Enable Desktop Lyrics in Settings to open a floating always-on-top lyrics window. Its position is persisted between launches. Drag it to the top edge to anchor it, or use **Reset Position** in Settings.

### Favorites and Song Export

Favorites can display either a local folder or a NetEase cloud playlist. Local folders support reordering and removal; both views support queue replacement, queue append, and batch operations where available.

Song export supports `.mp3`, `.m4a`, `.flac`, `.wav`, `.ogg`, and `.opus`. Southside Music writes available cover art, lyrics, album, artist, and track metadata into the exported file, and can either keep the original audio or render the current playback effects into the export.

### Rediscover Your Favorites

Home has a compact horizontal **Rediscover your favorites** entry at the top, separate from the four playback mode cards. It opens a dedicated page containing all local favorites. Favorite folders and cloud playlists have a compact **Rediscover** button that opens the page for that list only. **A fresh mix** favors lower playback counts and older listens; **Rarely heard** starts with the least-played songs; **Long time no listen** brings back previously played favorites. Songs heard within 24 hours and the current song are placed later when alternatives are available.

The page displays 40 songs at a time and loads more as you scroll, until every song is shown. Loading keeps the existing order and adds no duplicates. Rows show covers, artists, playback counts, and listening dates. **Refresh order** generates a new order; **Play all** uses the full result as the playback queue, including rows not yet displayed. Click a song to start there or its cover to queue it after the current track. The back button returns to the entry page, and favorite folder order is preserved.

Existing playback counts are used immediately. Listening dates start with this update and are saved to `data/listening_history.json` after 30 seconds of active playback, or half the duration for short songs. Paused playback and quick skips do not record a listening date. Older songs without a recorded date are labeled accordingly.

### Comments

The comments button in the playing view opens the comment page for the current song. Threaded replies, avatars, and pagination are supported, and you can post a comment directly from the page after logging in.

### Onerad

Configure a provider in Settings, then select the chat button in the title bar. Onerad supports streaming responses and can request app actions such as searching, building folders and playlists, controlling playback, changing settings, or managing LLM providers. Tool calls that affect the app require confirmation, and answers are rendered as Markdown with math support.

### SouthsideClient

Southside Music exposes a local WebSocket server on port `15489`. The connection sends playback state, song data, lyrics, cover, position, and FFT packets to SouthsideClient, and accepts basic playback controls in return. Connection status, traffic, latency, and connect/disconnect controls are available in Settings.

The exact packet shapes are documented in [Music bridge protocol](music_bridge_protocol.md).

### Settings

Settings use a compact default view. Enable **Advanced Settings** to expose detailed audio, FFT, LLM, storage, and transition controls; sections marked below are only visible in that mode.

| Section | Main options | Advanced only |
| --- | --- | --- |
| App | Language, SMTC service, AFK timeout, debug mode | Partly |
| Window | Cover-color background strength | No |
| Playback | Play order, output device, speed, pitch, smart skip | Partly |
| Handoff | Crossfade strength, curve, duration, BPM window, tempo, key, gain matching, lyric handoff | No |
| Playback Effects | Stereo width, reverb, EQ curve | Partly |
| Desktop Lyrics | Visibility, anchor, position reset | No |
| Cache Storage | Automatic cleanup, cache age, space limit, download concurrency | Partly |
| Loudness | Target LUFS with reference values | Yes |
| Animations | Lyrics animation type and smoothing | Yes |
| Lyrics | Line spacing | No |
| FFT | Spectrum, smoothing, buffer, size, and client scaling | Yes |
| Beat Detection | Beat detection and visual flash | Yes |
| LLM | Providers, API format, key, base URL, and models | Yes |
| Connection | SouthsideClient status, traffic, latency, and controls | Yes |

---

## Shortcuts and Tips

- Press **Space** to toggle play/pause.
- Enable advanced settings, then **App > Debug mode** to use **F3** for the debug overlay and **F4** for the performance and memory overlay. Debug mode is off by default. While enabled, each launch saves stage timings as PNG and JSON in `data/debug/launch`.
- Select the lower part of the playback bar to expand or collapse Now Playing.
- Drag the progress line to seek after the song has loaded.
- Select a song cover in supported lists to queue it after the current track.
- Use the quality button in Now Playing to switch the stream quality of the current song.
- Drag the collapsed title-bar search box to move the window; selecting it expands it for typing.
- Change **Language** in Settings to switch English and Simplified Chinese immediately.
- Adjust **Target LUFS** if tracks feel inconsistent in perceived volume.
- Raise the **AFK Timeout** if animations weaken too eagerly while listening.

---

## Documentation

- [Music bridge protocol](music_bridge_protocol.md) - packet contract between Southside Music and SouthsideClient
- [NetEase PC API notes](netease-pc-api.md) - Chinese protocol record of the official desktop client, used to align the bundled `ncm` client
- [Privacy policy](privacy_policy.md) and [Terms of service](terms_of_service.md)
- [SouthsideMusic Story](SouthsideMusic_Story.md) - how the project came together
- [Licenses](licenses) - third-party license texts bundled with the application

---

## Development

### Requirements

- Windows
- Python `>=3.13` (the workspace is validated against `3.14`, and the packaged app runs on Python `3.14.7` plus a free-threaded `3.14t` worker)
- [`uv`](https://docs.astral.sh/uv/)
- Internet access for initial environment and dependency downloads

### Prepare the Workspace

```bash
git clone https://github.com/Adreno5/SouthsideMusic.git
cd SouthsideMusic
python setup_workspace.py
```

The setup script synchronizes the `uv` environment, prepares the Python 3.14.7 embedded runtime, installs a free-threaded `3.14t` worker environment, creates the isolated Nuitka build environment, validates the runtime, and can install Inno Setup.

### Run from Source

```bash
uv run src/main.py
```

If the project environment is already active:

```bash
python src/main.py
```

### Validate

```bash
python -m py_compile src/main.py
uv run python -m compileall -q src tests scripts beat_test.py
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
uv run python -m unittest discover -s tests
```

`tests/` is the real suite and runs on `unittest`; pytest is not used. Run everything with the discovery command above, or run a single file directly, for example `uv run python tests/test_lyric_formats.py`.

CI (`.github/workflows/ci.yml`) runs the compile, lint, format check, and test steps on Windows with FFmpeg installed. `src/test.py` is a manual API exploration script, not part of the suite.

### Build

```bat
build.bat
```

The build compiles `launcher.py` with Nuitka, assembles the embedded runtimes and application resources, regenerates the icon, and invokes Inno Setup when `ISCC.exe` is available.

```text
build.result\
├── raw\          Runnable portable directory
└── installer\    Versioned installer, when Inno Setup is available
```

If Inno Setup is unavailable, the portable build remains in `build.result\raw\`. Releases are produced by the GitHub Actions workflows in `.github/workflows/`, which read their release notes from `release-notes/<tag>.md`.

### Project Layout

```text
bootstrap.py        First-launch repair for the bundled Python runtime
launcher.py         Nuitka entry point: single instance, then start the app
build.bat           Windows build and packaging flow
setup_workspace.py  Workspace bootstrap
src/
  main.py           Application entry and startup lifecycle
  core/             Audio engine, configuration, models, lyrics, theme, LLM, bridge server
  services/         Event bus and update checks
  views/            Pages, cards, panels, windows, and widgets
  ncm/              Bundled NetEase CloudMusic API client
tests/              unittest suite
scripts/            Icon generation, audio labs, reverse-engineering helpers
docs/               English and Chinese documentation
data/               Runtime caches, local favorites, listening history
fonts/              Bundled HarmonyOS Sans SC fonts
icons/, images/     Packaged interface resources
release-notes/      Release notes consumed by the release workflow
config.json         Persistent user configuration
```

### Tech Stack

| Layer | Technology |
| --- | --- |
| GUI | PySide6 + PySide6-Fluent-Widgets |
| Windowing | qframelesswindow + hPyT |
| Audio and DSP | sounddevice + pydub + NumPy + SciPy |
| Metadata | mutagen |
| NetEase API | Bundled `ncm` client |
| Networking | requests + Tornado WebSocket server |
| Assistant | OpenAI SDK + Anthropic SDK + markdown-it-py + ziamath |
| Windows integration | winrt (SMTC) + pywin32 (DPAPI) + psutil |
| Packaging | Nuitka + Inno Setup |

### Configuration and Data

- Persistent settings are stored in `config.json`.
- Local favorites, playback counts, listening history, and runtime caches are stored under `data/`.
- Legacy `config.pkl` data is migrated and removed.
- LLM API keys are encrypted for the current Windows user with `CryptProtectData`.
- Downloadable caches can be cleaned automatically by age and total size.
- Debug builds write startup timing and performance reports under `data/debug/`.

---

## License

Southside Music is licensed under the [PolyForm Noncommercial License 1.0.0](../LICENSE).

This software is intended for personal learning, research, and private entertainment. Commercial use is not permitted. Users are responsible for music exported through the application; exported audio must not be redistributed or resold.
