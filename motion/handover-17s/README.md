# HANDOVER — 19.6 s · SouthsideMusic smart crossfade

One take, no cuts. A hand holds a fader against a wall where two songs meet; the machine
removes four obstacles, then the hand rides the fade.

Never a formula. The mechanism is four physical objects:

| the product does | the film shows |
|---|---|
| fade length snapped to whole bars | an odometer bolted to the wall counts bars; the detent drags the splice patch out to exactly 4 and stamps it |
| tempo matched (0.85–1.15, WSOLA) | the blue field **rotates** into the red one's rhythm in five hard snaps, its beat ticks sliding until they continue across the seam |
| key compatibility → harmonic blend | a 12-spoke wheel rises out of the seam; two markers travel and stop as neighbours, a lime arc lights, the splice seals |
| equal-power fade curves | the splice patch is a physical strip whose **thickness is the level** — it pinches to a waist at 50/50 mix, and the cables bell out to fill it |
| the mix itself | inside the patch, vermilion and cobalt are printed in a scattered halftone whose density is the mix ratio |

The patch is the hero: born at the seam, stretched by the detent, sealed by the key match,
pinched and filled during the ride, drained to pure cobalt, then dissolved into the song.
The lime line that appears when the level holds straight is the last thing left, and it
becomes the lime bar under `CROSSFADE`.

## Beat sheet

`u` is film time; the film holds six times (dead-still rests), so real time = u + elapsed holds.

| u | beat | what moves | still | carries into the next |
|---|---|---|---|---|
| 0.00 | hook | nothing — a fist already on the fader, red field against blue, one lime arrow | **yes, 1.05 s** | the hand tightens at 0.30 |
| 1.05 | reveal | camera pulls back 2.2× → 1.0, the whole wall and the rail come in | — | the seam |
| 2.30 | **tempo** | 5 snaps at 2.42/2.62/2.84/3.06/3.30: the blue field steps 18° each time, ticks compress 96→132 px and phase-align | between snaps | the now-continuous tick row |
| 3.42 | rest | **0.58 s freeze** | yes | the seam |
| 3.72 | **length** | odometer counts bars; the splice patch unrolls from the seam, detent at 5.02 slams it to 4 bars, `4 BARS` stamp | — | the patch |
| 5.20 | rest | **0.55 s freeze** on the stamp | yes | the patch |
| 5.55 | **key** | disc rises from the patch, markers travel 6.00–6.62, lock, lime arc, patch seals | — | the sealed patch |
| 6.72 | rest | **0.45 s freeze** on the sealed patch | yes | the patch |
| 7.85 | ride | the hand drags: mix 0 → 1, the patch pinches as the mix crosses 50/50 | — | the patch's waist |
| 7.95 | rest | **0.40 s freeze** before the deepest point | yes | the waist |
| 10.10 | **level** | the two fade curves bell out, the waist fills, the deck snaps straight, a lime line draws along the patch | — | lime line |
| 10.55 | rest | **0.95 s freeze** — the held breath after the twang | yes | lime line |
| 13.20 | release | knob hits the end stop, hand lifts, machinery retracts in four uneven hits, bands slide out, wall swells to full bleed | — | the lime line |
| 14.30 | rest | **0.45 s freeze** — flat cobalt, one lime line | yes | the lime line |
| 14.90 | mark | the line becomes the bar, `CROSSFADE` stamps with a 36 px shadow that shrinks to 8 | — | — |
| 15.30 – 19.63 | end | dead still | **1.6 s** | — |

Real-time shot lengths: 1.05, 1.25, 2.0, 1.38, 1.0, 1.07, 1.68, 2.65, 0.45, 2.35, 0.67, 0.58,
0.85, 2.6 → **CV 0.51**.

## Numbers

- 3840×2160 at 60 fps, 19.63 s, 1177 frames, 4163 captures in 312 s on 8 workers; 11.4 Mbit/s, AAC 48 kHz stereo.
- 1920×1080 CSS with the canvas at `devicePixelRatio` — the final is the same composition at `--scale 2`, not an upscale.
- `verify_promo.py` on the final: cadence **CV 0.51**, rest **still 0.277 / longest quiet 2.5 s**, burst 10, not-flat 0.35, audio **−7.5 dBFS peak / 0 clipped / 25.5 % quiet**, continuity 1.00 over 0 boundaries (one continuous world — nothing cuts, so probe has no boundary to score), curves **90 px/frame peak at 60 fps with the 180° shutter**, framing: knob in frame all 697 samples, mark whole.
- `probe.py`: 4 tracked elements, 26 moves, 26 shutter-sized moves, median t80 0.70.
- Look `stencil` (made for this film, `look.json`): bone `#EDE7DA`, vermilion `#FF3B12`, cobalt `#2226DE`, acid lime `#D8FF00`, ink `#0E0E0C`. Type: Archivo Condensed 900 (display), Archivo 700, Geist Mono 600; Chinese in the product's own `HARMONYOS_SANS_SC_REGULAR.ttf`, subset to the 132 glyphs the film sets.
- Score: one convolution room (synthetic IR, 1.6 s), five materials (wood/metal/thump/air/sweep), a 110 BPM pulse whose hi-hat pattern enters at B's own period and **steps to the grid on each of the five snaps** — the sound does the tempo lock the picture does. The bed gates to silence in all six holds, which is where the 23 % quiet and the 2.25 s longest-quiet come from. Mastered −16 LUFS, ceiling −7.5 dBFS so the AAC stays under −3.

## What was rejected

- **v1** — full-frame striped fields, no ground: a plaid wallpaper. The frame had no air; the splice was invisible against its own colours. Rebuilt around a 460 px wall strip with bone above and below.
- **v2** — a "tape filled with blue cells" dither built on a Bayer matrix: read as polka dots, and a hash bug made it 100 % blue in the middle of the mix (the exact moment the mix is the point).
- **v3** — a card-carrying fist (fingers inside the palm silhouette) read as a mitten; tips drawn *under* the fingers with a 0.72 rad bend fixed it.
- The first master gain was `db(gain + 16)` instead of `db(gain)` — a +20 dB error, 2 clipped samples, verdict FAIL. Which is the whole reason the oracle runs before the eye.

## Files

`film.mp4` final · `draft.mp4` 1080p30 review copy · `comp.html` the composition (every value a pure
function of time; `?t=8.4&hud` pins a frame, `?play` loops) · `motion.js` + `lib` moves · `look.json`
the look, `look.js` the compiled subset with the type · `sfx.py` the score, `score.json` the event list
dumped from the comp · `shot.py` stills · `do_verify.py` the oracle with the shot list.
