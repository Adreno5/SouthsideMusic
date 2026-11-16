# SouthsideMusic promo (video-shotcraft)

24.0 s · 1920×1080 composition @60fps → 4K60 (--scale=2) · real UI captures.

## Quick start

    npm install
    node node_modules/@remotion/cli/remotion-cli.js studio src/index.ts

Render:

    # 4K with BGM
    node node_modules/@remotion/cli/remotion-cli.js render src/index.ts SouthsidePromo out/promo.mp4 --scale=2 --log=error

    # 4K without BGM (SFX kept)
    node node_modules/@remotion/cli/remotion-cli.js render src/index.ts SouthsidePromo out/promo-nobgm.mp4 --scale=2 --props=props-nobgm.json --log=error

## Layout

- src/SouthsidePromo.tsx — 10 shots + SFX table + composition
- src/lib/ui.tsx — design tokens, captions, wordmark, grain/vignette/dust
- src/generated/ — layout.json (real element boxes), seq.json (captured lyric frames), gl.json (EQ rect)
- public/pages, public/elements, public/seq — real captures (see DESIGN.md)
- public/audio — BGM cut + Freesound SFX, manifest and credits
- capture/harness.py — Qt capture harness (real app, 3× DPR)
- research/ — shot-card study + BGM beat analysis

## Docs

- DESIGN.md — product brief, visual spec, shot table, capture limits
- CREDITS.md — BGM and SFX licensing
- public/audio/audio-manifest.json — measured SFX peaks and suggested frames
- research/shots.md — function→shot mapping from the 124-card library
- research/bgm/analysis.md — BPM grid, sections, strong hits
