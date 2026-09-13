[中文版本](README_zh.md)

# Southside Music

> A refined Windows music workstation for NetEase CloudMusic: precision audio control, live lyrics, visual performance tools, and deep desktop integration in one uncompromising player.

> In an age drowning in things that merely work, what's worth using is what earns the name of a real work. True craft lives in the hours no one sees.

## Friendly Links

- [LINUX DO](https://linux.do) - A new ideal community

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/Adreno5/SouthsideMusic)

Southside Music is a Windows-only third-party NetEase CloudMusic desktop client built around a custom audio engine. It combines loudness normalization, speed and pitch control, word-by-word and desktop lyrics, spectrum visualization, local favorites, song and lyric-video export, Onerad assistant support, and SouthsideClient WebSocket integration.

> For the story behind the project, read [SouthsideMusic Story](SouthsideMusic_Story.md).

---

## Contents

- [Overview](#overview)
- [Highlights](#highlights)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Feature Guide](#feature-guide)
- [Shortcuts and Tips](#shortcuts-and-tips)
- [Development](#development)
- [License](#license)

---

## Overview

Southside Music is built for people who want NetEase CloudMusic on the desktop without giving up control of playback. After logging in, you can search the catalog, browse cloud playlists, play daily recommendations, and organize songs into local favorite folders.

The player uses its own audio pipeline instead of delegating the listening experience to a web view. Loudness, speed, pitch, stereo width, reverb, transitions, silence detection, preloading, lyrics, and visualization are all handled directly by the application.

Southside Music is an independent, non-commercial project and is not an official NetEase CloudMusic client.

---

## Highlights

### Audio

- Loudness normalization with an adjustable target LUFS
- Playback speed from `0.1x` to `3.0x` with pitch-preserving time stretching
- Independent pitch shift from `-12` to `+12` semitones
- Optional stereo Haas widening and reverb
- Smart crossfade with adaptive duration, transition curves, tempo and key matching, and automatic gain control
- Silent-ending detection with configurable threshold and detection window
- Current-track and next-track preloading for smoother seeking and switching
- Selectable audio output device

### Lyrics and Visuals

- LRC line timing and YRC word-level timing
- Word-by-word highlighting when YRC data is available
- Translated lyrics with an immediate display toggle
- Always-on-top desktop lyrics with saved position and top-edge anchoring
- Real-time FFT spectrum visualization
- Theme-aware background color mixed from the current cover
- Lyric-video export with configurable format, bitrate, alignment, line count, background, animation, translation, and audio
- Dark and light themes with English and Simplified Chinese UI

### Library and Export

- NetEase song and playlist search with incremental loading
- Daily recommended songs and playlists
- Local favorite folders and NetEase cloud playlists in one sidebar
- Batch selection, playlist replacement, queue append, removal, and local reordering
- A Library view that gathers every locally favorited song
- Song export with cover art, lyrics, album, artist, and track metadata
- Queue-after-current actions across Home, Search, Library, and Favorites
- Song comments with threaded replies, avatars, pagination, and posting support

### Assistant and Integration

- Onerad assistant panel with streaming output and explicit tool-call confirmation
- OpenAI-compatible Chat Completions, OpenAI Responses, and Anthropic providers
- API keys encrypted with Windows Data Protection API
- SouthsideClient WebSocket bridge on port `15489`
- Lyrics, cover, song information, position, play state, and FFT streaming to SouthsideClient
- Play/pause, seek, next, and previous commands from SouthsideClient

### Reliability

- In-app GitHub release checking and update handling
- Startup checks for FFmpeg, Python runtime, audio output, network, and OpenGL
- Guided FFmpeg download when the dependency is missing
- Automatic cleanup of old or oversized downloadable caches
- Unhandled-exception dialog with traceback details
- Runtime debug overlay available with `F3`
- Persistent session refresh, cache-size cleanup, and graceful recovery from missing dependencies

---

## Installation

1. Open the [Releases page](https://github.com/Adreno5/SouthsideMusic/releases).
2. Download the latest `SouthsideMusic_<version>_win64_setup.exe`.
3. Run the installer and launch Southside Music.

The first launch performs a dependency check. If FFmpeg is missing, the dependency window can download and install it for you.

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

Use **Add folder** in the Local section to create an app-managed favorite folder. Song actions let you add, remove, export, or reorder tracks. Cloud playlists remain available separately under Cloud.

---

## Feature Guide

### Now Playing

Select the bottom playback bar to open the full playing view. It contains the cover, song information, synchronized lyrics, translation controls, and lyric-video export. The compact controller remains available for progress, current lyrics, FFT, previous/play/next, and queue access.

Drag the progress line to seek after the track is ready. Open the queue panel to play, reorder, export, repeat, remove, or clear queued tracks.

### Smart Crossfade

Crossfade analyzes adjacent tracks and prepares a transition near the current song's ending. Advanced settings expose transition strength, curve, maximum duration, BPM window, tempo matching, key matching, and automatic gain control. Disable crossfade when exact track boundaries are preferred.

### Lyrics and Lyric Video

YRC lyrics use word-level highlighting; tracks without YRC timing fall back to line-level LRC display. Translation can be shown when NetEase provides it.

The export button in Now Playing renders synchronized lyrics as `.mp4`, `.av1`, `.mkv`, or `.webm`. Export options include bitrate, visible line count, word-level timing, translation, alignment, solid-color background, scroll animation, and whether the original audio is included.

### Desktop Lyrics

Enable Desktop Lyrics in Settings to open a floating always-on-top lyrics window. Its position is persisted between launches. Drag it to the top edge to anchor it, or use **Reset Position** in Settings.

### Favorites and Song Export

Favorites can display either a local folder or a NetEase cloud playlist. Local folders support reordering and removal; both views support queue replacement, queue append, and batch operations where available.

Song export supports `.mp3`, `.m4a`, `.flac`, `.wav`, `.ogg`, and `.opus`. Southside Music writes available cover art, lyrics, album, artist, and track metadata into the exported file.

### Onerad

Configure a provider in Settings, then select the chat button in the title bar. Onerad supports streaming responses and can request app actions such as searching, opening folders, controlling playback, or changing settings. Tool calls that affect the app require confirmation.

### SouthsideClient

Southside Music exposes a local WebSocket server on port `15489`. The connection sends playback state, song data, lyrics, cover, position, and FFT packets to SouthsideClient, and accepts basic playback controls in return. Connection status, traffic, latency, and connect/disconnect controls are available in Settings.

### Settings

Settings use a compact default view. Enable **Advanced Settings** to expose detailed audio, FFT, LLM, storage, and transition controls.

| Section | Main options |
| --- | --- |
| App | Language and download concurrency |
| Cache Storage | Automatic cleanup, cache age, and space limit |
| Playback | Play order, smart skip, stereo, reverb, output device |
| Crossfade | Strength, curve, duration, BPM, tempo, key, and gain matching |
| Playback Effects | Speed, pitch, silence threshold, and detection window |
| LLM | Providers, API format, key, base URL, and models |
| Window | Cover-color background mixing |
| Lyrics | Animation smoothing |
| Desktop Lyrics | Visibility and position reset |
| FFT | Spectrum, smoothing, buffer, size, and client scaling |
| Loudness | Target LUFS |
| Connection | SouthsideClient status, traffic, latency, and controls |

---

## Shortcuts and Tips

- Press **Space** to toggle play/pause.
- Press **F3** to toggle the debug overlay.
- Select the lower part of the playback bar to expand or collapse Now Playing.
- Drag the progress line to seek after the song has loaded.
- Select a song cover in supported lists to queue it after the current track.
- Open **Library** to view songs from all local favorite folders together.
- Change **Language** in Settings to switch English and Simplified Chinese immediately.
- Adjust **Target LUFS** if tracks feel inconsistent in perceived volume.

---

## Development

### Requirements

- Windows
- Python `>=3.13`
- [`uv`](https://docs.astral.sh/uv/)
- Internet access for initial environment and dependency downloads

### Prepare the Workspace

```bash
git clone https://github.com/Adreno5/SouthsideMusic.git
cd SouthsideMusic
python setup_workspace.py
```

The setup script synchronizes the `uv` environment, prepares the Python 3.14.2 embedded runtime, installs a free-threaded `3.14t` worker environment, creates the isolated Nuitka build environment, validates the runtime, and can install Inno Setup.

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
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
```

There is no formal automated test suite yet. `src/test.py` is a manual API exploration script, not a pytest suite.

### Build

```bat
build.bat
```

The build compiles `launcher.py` with Nuitka, assembles the embedded runtimes and application resources, then invokes Inno Setup when `ISCC.exe` is available.

```text
build.result\
├── raw\          Runnable portable directory
└── installer\    Versioned installer, when Inno Setup is available
```

If Inno Setup is unavailable, the portable build remains in `build.result\raw\`.

### Project Layout

```text
src/
  main.py          Application entry and startup lifecycle
  imports.py       Shared Qt, typing, and event imports
  core/            Audio, configuration, models, lyrics, themes, backends
  services/        Event bus, updates, and application services
  views/           Pages, cards, panels, windows, and widgets
  pyncm/           Bundled NetEase CloudMusic API client
docs/              English and Chinese documentation
data/              Runtime caches and local favorite data
fonts/             Bundled HarmonyOS Sans SC fonts
icons/, images/    Packaged interface resources
config.json        Persistent user configuration
```

### Tech Stack

| Layer | Technology |
| --- | --- |
| GUI | PySide6 + PySide6-Fluent-Widgets |
| Windowing | qframelesswindow + hPyT |
| Audio and DSP | sounddevice + pydub + NumPy + SciPy |
| Metadata | mutagen |
| NetEase API | Bundled `pyncm` client |
| Networking | requests + Tornado WebSocket server |
| Assistant | OpenAI SDK + Anthropic SDK |
| Packaging | Nuitka + Inno Setup |

### Configuration and Data

- Persistent settings are stored in `config.json`.
- Local favorites and runtime caches are stored under `data/`.
- Legacy `config.pkl` data is migrated and removed.
- LLM API keys are encrypted for the current Windows user with `CryptProtectData`.
- Downloadable caches can be cleaned automatically by age and total size.

---

## License

Southside Music is licensed under the [PolyForm Noncommercial License 1.0.0](../LICENSE).

This software is intended for personal learning, research, and private entertainment. Commercial use is not permitted. Users are responsible for music exported through the application; exported audio must not be redistributed or resold.
