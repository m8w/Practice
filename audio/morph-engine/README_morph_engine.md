# morph_engine.py

An audio-reactive rewrite of the morphing tools in this repo. One file, one CLI,
no GUI dependency. It keeps the Delaunay-mesh identity of the original project but
fixes its two structural weaknesses and adds the feature the tool was really
missing: **the morph now listens to the audio.**

---

## What changed from the original scripts

**Fixed — feature correspondence.** `morph_video.py` detected feature points
independently in each image and then truncated both lists to the same length, so
point *i* in image A had no relationship to point *i* in image B. The triangle
warp was effectively warping between unrelated points and the cross-dissolve was
carrying the look. `morph_engine.py` uses ORB detection → ratio-test matching →
RANSAC homography filtering to get points that actually correspond, pins the frame
border, and triangulates once per pair on the average shape. When too few reliable
matches exist (very abstract / noisy source images) it falls back gracefully to a
shared grid so you get a clean soft morph instead of garbage.

**Fixed — triangle accumulation.** The old warp allocated a full-frame array per
triangle and summed them (slow at 1080p, and overlaps double-counted). The new warp
composites each triangle into a single accumulator inside its own bounding box with
a local mask — one allocation, no double counting.

**New — audio-reactive motion.** Instead of ramping alpha linearly across the audio
duration, the morph position and warp intensity are driven by the music. Quiet
passages hold on an image; loud passages rush through; transients kick extra warp;
segment boundaries can snap to the beat.

**Also new:** weighted blending of several techniques, easing curves, ping-pong /
seamless loop, similarity/contrast ordering, LAB/HSV colour-space morphing, video
keyframe input, ProRes and GIF output, parallel frame rendering, a preview mode, a
`--seed`, and a JSON **manifest** written next to every render (all parameters +
SHA-1 of every input) so any piece can be reproduced exactly.

---

## Install

```bash
pip install numpy opencv-python scipy librosa
# ffmpeg must be on your PATH (brew install ffmpeg)
```

`librosa` is only needed for audio-reactive modes; the tool still runs image-only
(`--duration`) without it.

---

## Quick start

Drop images + a WAV in `~/Downloads`, then:

```bash
python3 morph_engine.py --audio ~/Downloads/track.wav
```

Or be explicit about everything:

```bash
python3 morph_engine.py img/*.png \
  --audio track.wav \
  --techniques "mesh:0.5,flow:0.3,dissolve:0.2" \
  --audio-react full \
  --easing smoothstep \
  --order similarity \
  --output out/piece.mp4
```

Preview a look fast (half res, half fps) before committing to a full render:

```bash
python3 morph_engine.py img/*.png --audio track.wav --preview
```

See the full plan and write the manifest without rendering a frame:

```bash
python3 morph_engine.py img/*.png --audio track.wav --dry-run
```

---

## The audio-reactive modes (`--audio-react`)

| Mode     | What the audio does                                                        |
|----------|----------------------------------------------------------------------------|
| `linear` | Ignore audio motion — constant-speed morph (the original behaviour).       |
| `rms`    | Morph *speed* and *warp intensity* follow loudness. Quiet holds, loud rushes.|
| `onset`  | Same, driven by onset strength — reacts to transients / attacks.           |
| `beat`   | Image changes snap to the detected beat grid. Rhythmic, on-the-beat cuts.   |
| `full`   | Beat-snapped segment boundaries **plus** loudness-driven warp. (default)    |

In every reactive mode a separate **warp-gain** signal (0–1) scales the mesh
displacement, flow displacement, and grid amplitude, so even while an image is
"held" it breathes with the music.

---

## Techniques (`--techniques`)

Weighted blend, e.g. `mesh:0.6,flow:0.3,dissolve:0.1`.

- `mesh` — correspondence-correct Delaunay triangle morph (feature-tracked).
- `flow` — dense optical-flow morph. Robust on anything, needs no correspondence.
  Good default.
- `grid` — sinusoidal grid warp. Looser, more liquid; reads well on abstract material.
- `dissolve` — straight cross-dissolve. Cheap, stabilises the blend.

Default is `flow:0.7,dissolve:0.3` because it's robust on any imagery. Add `mesh`
when your sources have structure worth tracking.

---

## Full option list

```
inputs
  images                 image files / globs / folders (omit to use --input)
  --input DIR            folder to scan (default: ~/Downloads)
  --video FILE           use keyframes pulled from a video as inputs
  --frames N             how many keyframes to pull from --video (default 8)
  --audio FILE           audio track to drive + mux

output
  --output PATH          explicit output path
  --output-dir DIR       output folder (default: ~/Downloads)
  --fps N                frames per second (default 30)
  --duration SEC         length (default: audio length, or auto)
  --codec {h264,prores,gif}
  --crf N                H.264 quality, lower = better (default 18)

look
  --techniques SPEC      weighted blend (see above)
  --easing {linear,smoothstep,smootherstep,ease-in,ease-out,sine,exp}
  --colorspace {rgb,lab,hsv}
  --order {asc,shuffle,similarity,contrast}
  --pingpong             morph forward then back (seamless boomerang)
  --loop                 add a final last->first morph for clean looping

audio reactivity
  --audio-react {linear,rms,onset,beat,full}

performance / repro
  --max-width PX         downscale wide images
  --jobs N               parallel frame workers
  --preview              fast low-res / low-fps pass
  --seed N               RNG seed for shuffle order
  --workdir DIR          persistent frame cache
  --keep-frames          keep rendered PNG frames
  --dry-run              write manifest + plan only
```

---

## Reproducibility

Every render writes `<output>.manifest.json` beside the video: the full parameter
set, the effective fps / frame count, and a SHA-1 for every input image and the
audio. Re-running with the same manifest parameters on the same inputs reproduces
the piece. `--seed` fixes the shuffle order.

---

## Notes for a Mac / Apple-silicon workflow

- `--codec prores` writes a `.mov` (ProRes 422 HQ, 10-bit) for editing rather than
  a delivery H.264.
- Use `--workdir /Volumes/ssd1/morph_cache --keep-frames` to keep the frame cache
  on a fast external disk for long renders, mirroring the SSD pattern in the old
  `video_morpher.py`.
- `--jobs` scales across performance cores; start with the number of P-cores.
