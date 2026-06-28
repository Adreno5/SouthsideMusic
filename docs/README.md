[中文版本](README_zh.md)

# Southside Music

> In an age drowning in things that merely work, what's worth using is what earns the name of a real work. True craft lives in the hours no one sees.

# Friendly Links

- [LINUX DO](https://linux.do) A new ideal community

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/Adreno5/SouthsideMusic)

A Windows-only third-party NetEase CloudMusic desktop client with streaming playback, word-by-word lyrics, loudness normalization, desktop lyrics, local favorites, song export, Onerad assistant support, and SouthsideClient integration.

> For the project's history, see [SouthsideMusic Story](SouthsideMusic_Story.md).

---

## Table of Contents

- [What It Is](#what-it-is)
- [Features](#features)
- [Installation](#installation)
- [Getting Started](#getting-started)
- [Interface Guide](#interface-guide)
- [Advanced Features](#advanced-features)
- [Tips](#tips)
- [Development](#development)

---

## What It Is

Southside Music is a Windows desktop music player for NetEase CloudMusic. Log in with your account to search songs and playlists, browse your cloud playlists, play daily recommendations, and keep your own local favorite folders.

It has its own audio engine with loudness normalization, playback speed and pitch controls, stereo Haas widening, reverb, crossfade, silent-ending skip, preloading, FFT visualization, word-by-word lyrics, translated lyrics, and a desktop lyrics overlay.

In short: it plays your NetEase music in a cleaner, more focused way, while keeping power-user controls close at hand.

---

## Features

**Playback**

- Full-catalog song search and playlist search through NetEase CloudMusic
- Daily recommended songs and recommended playlists on the Home page
- Loudness normalization so songs stay close to the same perceived volume
- Playback speed from 0.1x to 3.0x
- Pitch shift from -12 to +12 semitones
- Optional stereo Haas widening and reverb
- Crossfade between adjacent songs
- Auto-skip silent endings, with threshold and remaining-time controls
- Preloading of the current and next track for smoother seeking and switching
- Output device selection

**Lyrics and Visuals**

- LRC and YRC lyric support
- Word-by-word lyric highlighting when YRC timing is available
- Translated lyrics toggle when translation data exists
- Desktop lyrics in an always-on-top floating window, with reset-position support
- Real-time FFT spectrum visualization in SouthsideMusic
- Theme-aware background color mixed from the current song cover
- Dark and light theme support, plus English and Simplified Chinese UI

**Organization**

- Local favorite folders stored by the app
- NetEase cloud playlists in the sidebar
- Create local folders and cloud playlists from the app
- Add songs to local folders or cloud playlists
- Batch-select songs in a folder for add-to-playlist, add-to-folder, and removal
- Library page that gathers all local favorite songs
- Queue songs after the current track from Home, Library, Favorites, and search results
- Export songs as audio files with cover art, lyrics, album, artists, and metadata

**Assistant and Client Integration**

- Onerad assistant side panel with streaming responses and tool-call confirmation
- OpenAI-compatible Chat Completions, OpenAI Responses, and Anthropic provider configuration
- Encrypted API-key storage through Windows user data protection
- SouthsideClient WebSocket bridge on port `15489`
- Sends lyrics, cover, playback position, play state, and FFT data to SouthsideClient
- Receives basic music-control commands from SouthsideClient: toggle, seek, next, previous

**Auto-Update and Diagnostics**

- Checks GitHub releases and starts update handling from inside the app
- Startup dependency check for FFmpeg, Python runtime, audio output, network, and OpenGL
- Automatic FFmpeg download helper when FFmpeg is missing
- Error popup with traceback details for unhandled exceptions
- Debug overlay toggle with `F3`

---

## Installation

Download `SouthsideMusic_win64_setup.exe` from the latest version on the [Releases page](https://github.com/Adreno5/SouthsideMusic/releases) and run it.

On startup the app checks FFmpeg, Python runtime, audio output, network access, and OpenGL. If FFmpeg is missing, the dependency window can download and install it automatically.

> SouthsideMusic is Windows-only.

---

## Getting Started

### 1. Log In

Open the app and use the account area in the sidebar or Home page to log in:

- **Cell Phone** - Enter your phone number, then the verification code
- **QR Code** - Scan with the NetEase CloudMusic app, then confirm that you scanned it

Anonymous sessions can launch the app, but most useful cloud features require a real NetEase CloudMusic login.

### 2. Search

Click the search box in the title bar, type a keyword, and press Enter. On the Search page, choose **Songs** or **Playlists** from the search-type selector.

Song results can be played directly or added to a local/cloud folder. Playlist results open as cloud playlist cards.

### 3. Use Home

The Home page shows daily recommended songs and recommended playlists after login. Click a song to play it, or click its cover to queue it after the current track.

### 4. Browse Folders and Playlists

The left sidebar contains:

- **Daily Recommend**
- **Local** favorite folders
- **Cloud** NetEase playlists
- **Refresh**, **Library**, **Settings**, and **Add folder** controls

Click a folder or playlist to open it in the Favorites page. Use **Replace Playlist** to make it the active queue, or **Add to Playlist** to append it.

### 5. Manage Local Favorites

Use **Add folder** under the Local section to create local folders. Right-click or use song-card actions to add songs, export songs, remove songs, and move songs inside local folders.

---

## Interface Guide

### Home

Home welcomes the logged-in user and loads daily recommended songs and recommended playlists.

### Search

Search supports **Songs** and **Playlists**. Results load incrementally as you scroll.

### Library

Library gathers songs from all local favorite folders into one view, with lazy-loaded covers and details.

### Favorites

Favorites displays a selected local folder or cloud playlist. It supports replacing the current playlist, appending a folder to the playlist, batch selection, add-to-folder, add-to-playlist, removal, and local song reordering.

### Now Playing

Click the bottom playback bar to expand the playing panel. The panel shows:

- Album cover, song title, and artist
- Scrolling lyrics with word-level highlighting when available
- Translated lyrics toggle when translation data exists

The bottom controller stays available for cover, current lyric, progress, FFT visualization, previous/play/next, and playlist expansion. Drag the progress line to seek.

### Playlist Panel

Click the playlist button in the bottom controller to open the right-side playlist panel. You can play a queued song, reorder tracks, export a track, repeat one item, remove one item, or clear the queue.

### Onerad

Click the chat button in the title bar to open the Onerad side panel. Configure providers in Settings first. Onerad can answer questions about the app and, after explicit confirmation, run supported app actions such as searching, opening folders, or changing settings.

### Settings

Settings are grouped into collapsible sections:

| Section | Main options |
| --- | --- |
| App | Language, download concurrent threads |
| Playing | Play order, stereo, Haas delay, reverb, smart skip, crossfade, speed, pitch, skip threshold, skip remaining time, output device |
| LLM | Providers, API format, API key, Base URL, model mappings |
| Window | Background mix ratio |
| Lyrics | Lyrics smooth factor, acceleration smooth factor |
| Desktop Lyrics | Enable desktop lyrics, reset position |
| FFT | Enable spectrum, FFT smoothing, SouthsideMusic FFT factor, SouthsideClient FFT factor |
| Loudness | Target LUFS, reference values |
| Connection | SouthsideClient connection status, sent/received size, latency, connect/disconnect |

---

## Advanced Features

### Word-by-Word Lyrics

When NetEase returns YRC word timing, lyrics switch to word-level highlighting. Otherwise the app falls back to line-level LRC lyrics.

### Translated Lyrics

If translation data exists for the current song, the expanded playing panel shows a translation toggle.

### Desktop Lyrics

Enable Desktop Lyrics in Settings to show an always-on-top floating lyrics window. The window position is saved; drag it to the top edge to anchor it, or use **Reset Position** in Settings.

### Loudness Normalization

Target LUFS controls the perceived playback loudness. Lower values are quieter; the default target is `-16`.

### Crossfade and Smart Skip

Crossfade blends adjacent songs. Smart Skip can skip silent endings near the end of a song using the configured threshold and remaining-time window.

### Song Export

Right-click a song and select **Export**. Supported output extensions include `.mp3`, `.m4a`, `.flac`, `.wav`, `.ogg`, and `.opus`. Export writes cover art, lyrics, album, artist, and track metadata when available.

### SouthsideClient Bridge

SouthsideMusic starts a WebSocket server on port `15489` for SouthsideClient. The bridge streams playback state, lyrics, position, cover/info, and FFT data, and accepts simple playback controls.

---

## Tips

- **Spacebar** toggles play/pause
- **F3** toggles the debug overlay
- Click the bottom bar below the progress line to expand/collapse the playing panel
- Drag the bottom progress line to seek once the song is ready
- Click a song card cover in several views to queue that song after the current track
- Use **Library** to see all local favorite songs in one place
- Use Settings -> **Language** to switch between English and Simplified Chinese immediately
- If volume feels inconsistent, adjust **Target LUFS** in Settings and restart when prompted

---

## Development

### Requirements

- Windows
- Python `>=3.13` for the project environment
- `uv`
- Internet connection for the initial setup and dependency downloads

`setup_workspace.py` currently prepares a Python 3.14.2 embedded runtime, a free-threaded worker Python (`3.14t`), and a clean build venv with Nuitka.

### Setup

```bash
git clone https://github.com/Adreno5/SouthsideMusic.git
cd SouthsideMusic
python setup_workspace.py
```

`setup_workspace.py` checks the environment, installs or verifies `uv`, runs `uv sync`, prepares embedded Python, installs embedded requirements, prepares the build venv, validates the runtime, and installs Inno Setup when accepted.

### Run from Source

```bash
uv run src/main.py
```

If the environment is already active, this also works:

```bash
python src/main.py
```

### Validation

```bash
python -m py_compile src/main.py
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
```

There is no formal test suite yet. `src/test.py` is a manual API exploration script, not a pytest suite.

### Build

```bash
build.bat
```

`build.bat` deletes old outputs, builds `launcher.py` with Nuitka, copies embedded Python, free-threaded Python, `src`, fonts, icons, images, and runtime metadata, regenerates the icon, then runs Inno Setup if `ISCC.exe` is installed.

Build output:

```text
build.result\
├── raw\          Runnable portable directory
└── installer\    Installer when Inno Setup is available
```

If Inno Setup is not found, the raw portable files remain in `build.result\raw\`.

### Tech Stack

| Layer | Technology |
| --- | --- |
| GUI | PySide6 + PySide6-Fluent-Widgets |
| Windowing | qframelesswindow + hPyT |
| Audio | pydub + sounddevice |
| Math/DSP | NumPy + SciPy |
| Metadata | mutagen |
| API | bundled `pyncm` NetEase CloudMusic client |
| Networking | requests + Tornado WebSocket server |
| Assistant | OpenAI SDK + Anthropic SDK |
| Packaging | Nuitka + Inno Setup |
| Font | HarmonyOS Sans SC |

### Configuration and Data

- Persistent app settings are saved in `config.json`
- Local favorite data and runtime caches live under `data/`
- Legacy `config.pkl` is migrated away and deleted
- LLM API keys are encrypted with Windows `CryptProtectData`
- Runtime caches such as `ffcache*` are cleaned while the app is running

### License

PolyForm Noncommercial License 1.0.0 - see [LICENSE](../LICENSE).

This software is for personal learning, research, and private entertainment only. No commercial use. Users are solely responsible for all music exported through this software. Do not redistribute or resell exported audio files. The developer assumes no liability for misuse.
