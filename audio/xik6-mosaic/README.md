# xik6_mosaic

A mix-and-match engine for your own sound and your own video. All the material comes from your files, and the engine was written from scratch for this.

- No AI models or sample packs.
- No presets or anyone else's recordings.
- Nothing owed to anyone. You don't need to credit Claude either. The code and everything it produces are yours.

The engine runs on general-purpose tools: Python, numpy, OpenCV and ffmpeg. Using them is like using Reaper; you don't credit them in a release. Their licenses only matter if you redistribute those libraries themselves.

## Modes

| mode | what steers | what gets rearranged |
|---|---|---|
| `sound` | a video | grains of your recordings. Brightness sets loudness, fine detail sets how bright the tone is, motion sets grain density, colour chaos sets noisiness, the image's left/right centre sets pan, hue sets transposition, symmetry sets grain length |
| `video` | a sound | clips from your videos, cut on the sound's onsets. Each clip is the moment whose *look* best matches the sound at that instant |
| `remix` | a video | both: the video builds a new soundtrack from your audio, then that soundtrack re-cuts your videos |

## Run it
```bash
cd ~/Music/xik6_mosaic
pip3 install numpy opencv-python            # ffmpeg must also be installed (brew install ffmpeg)

# the video plays your recordings
python3 xik6_mosaic.py sound --driver ~/Movies/flame.mp4 --corpus ~/Movies ~/Music/sets --out flame_sound.mp4

# a track re-edits your video archive
python3 xik6_mosaic.py video --driver ~/Music/sets/set.wav --corpus ~/Movies "/Volumes/SSD1/down 1" --out cutup.mp4

# everything into everything
python3 xik6_mosaic.py remix --driver "/Volumes/SSD1/down 1/long_A.mp4" --start random --duration 300 \
        --corpus "/Volumes/SSD1/down 1" ~/Movies --out remix.mp4
```
**Where remixes go:** by default, straight into the same folder as the driver video, named `xik6_remix_<video>_<mode>_s<seed>.mp4` (plus a `.wav` and `.json`). Your originals are only read, never changed. Anything named `xik6_remix…` is skipped as source material automatically. Add `--include-remixes` if you want remixes of remixes.

Each run also writes a `.json` edit list that records exactly which moment of which file landed where. `--seed` makes a run repeatable. Change the seed and you get a new variation from the same material.

## Glue: all your 30-second videos into one long video
```bash
python3 xik6_mosaic.py glue --corpus "/Volumes/Seagate/your folder"
```
This takes every video of 2 minutes or less in the folder, and in its subfolders, and joins them into one continuous video with smooth crossfades, sound included. The result is saved in the same folder as `xik6_remix_glued_<N>clips_name.mp4`, next to a `.json` that lists where each clip starts. The originals are untouched. Long videos, like the 11-hour ones, are left out automatically.
- `--order name` (default) goes by filename, which for your recordings is the recording time. `--order date` uses file dates; `--order random` shuffles.
- `--max-clip 60` includes only clips of 60 seconds or less. `--dissolve 0` gives hard cuts, and `--dissolve 15` gives slower crossfades.
- Mixed sizes and frame rates (720p30, 1080p60) are all conformed to `--size`/`--fps`. Clips with no sound become silence.
- The glued video can then drive a remix: `remix --driver xik6_remix_glued_…mp4 --include-remixes …`

## Long files (11-hour videos) and short clips, together
- **Memory stays flat.** Audio is decoded once to a disk cache and read in slices. Video is streamed at 160×90 for analysis. A 1-hour test file needed under 700 MB of RAM, and 11 hours needs about the same.
- **Analyse once, overnight.** After that, remixes start in seconds:
  ```bash
  python3 xik6_mosaic.py analyse --corpus "/Volumes/SSD1/down 1" ~/Movies --cache-dir "/Volumes/SSD1/xik6_cache"
  ```
  The cache holds about 330 MB of decoded audio per hour of material (roughly 3.6 GB per 11-hour file), so keep it on the SSD with `--cache-dir` and pass the same `--cache-dir` on every run. Add `--fast` to analyse keyframes only; it's much quicker but coarser.
- **Balance.** `--balance 0.75` is the default. At `0` a file is picked in proportion to its length, so one 11-hour file would drown out a 30-second clip. At `1` every file gets an equal share. In a test, a 30-second clip got 21 cuts against 48 for a 1-hour file.
- **Long drivers.** A piece defaults to at most 10 minutes. `--start random` picks a fresh stretch of an 11-hour driver each run; `--start 14400` begins 4 hours in. `--duration 3600` makes an hour-long piece.

## Running next to the 24/7 broadcast
You don't need to stop anything. By default the engine is **gentle**:
- lowest CPU priority, so the broadcast always wins
- 2 threads
- no GPU use at all, so your fractal renderer keeps the GPU to itself

Rendering takes roughly 1–1.5 minutes per minute of output. Use `--full-speed` only when the broadcast is off. If Activity Monitor ever shows the stream stuttering, run the `analyse` pass overnight or use `--fast`.

## Knobs
| flag | default | effect |
|---|---|---|
| `--density a b` | 6 60 | grains per second, from still images to busy ones |
| `--grain a b` | 40 700 | grain length in ms |
| `--transpose N` | 7 | max semitones of hue-driven transposition |
| `--scale "0 3 5 7 10"` | off | quantise transposition to a scale |
| `--reverse P` | 0.3 | how often flickering images play grains backwards |
| `--choices N` | 8 | randomness: pick among the N best matches (1 = strict) |
| `--self-weight W` | 0 | blend the driver video's own soundtrack back in (0–1) |
| `--cut a b` | 0.4 3.0 | clip length range in seconds (video/remix) |
| `--dissolve N` | 6 | frames of cross-dissolve at each cut (0 = hard cuts) |
| `--global-norm` | off | judge all files on one shared scale. By default each file is judged on its own scale, so dark and bright sets mix evenly |
| `--balance B` | 0.75 | 0 = pick files by length, 1 = every file gets an equal share |
| `--start S` | 0 | where to start in the driver: seconds, or `random` |
| `--duration D` | driver length, max 600 | length of the piece in seconds |
| `--cache-dir DIR` | ~/.xik6_mosaic_cache | where the analysis and decoded audio are kept |
| `--fast` | off | keyframe-only video analysis, for huge archives |
| `--full-speed` | off | all cores at normal priority (not while broadcasting) |
| `--exclude word …` | none | skip files whose path contains these words, e.g. anything that isn't yours |
| `--size --fps --crf` | 1280x720 30 19 | output format (`--size 1920x1080 --fps 60` for full HD) |
