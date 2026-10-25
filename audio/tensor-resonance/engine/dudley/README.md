# DUDLEY: sound hybridization

One sound's spectral envelope, worn by another sound's grain stream.
Named for Homer Dudley (vocoder, 1939). Public domain (CC0), see LICENSE.

## How it works

- **Carrier(s)** (`-c`): the grain stream. What the sound is made *of*.
- **Donor(s)** (`-d`): whose spectral envelope the grains wear. What the sound is *shaped like*.
- **Path** (`--path`): position 0 = grains keep their own envelope, 1 = donor 1's, 2 = donor 2's...
  Fractions interpolate. So the morph never crossfades two sounds: there is only ever one
  grain stream, and its envelope is what moves.

`--morph transport` (default) slides resonant peaks along the frequency axis (1-D optimal
transport). `--morph cepstral` fades peaks in place. `--texture 0..1` also swaps in the donor's
fine structure (harmonics), moving toward full cross-synthesis.

Randomness comes from `os.urandom` unless you pass `--seed`.

## The window

Double-click **Open DUDLEY.command**. (If macOS says it can't be opened, right-click it → Open once.)

- **Carriers / Donors**: Add… to pick files, double-click one (or ▶ Listen) to hear it. ▲▼ sets donor order.
- **Morph path**: click to add a point, drag to move, right-click to delete. Bottom line is the carrier's
  own envelope, each line above is a donor. Or switch to *Entropy walk* / *Follow donor 1 loudness*.
- **Settings**: drag a slider or type a value; double-click a setting's name to reset it. Hover for a hint.
- **RENDER** (⌘R). Each take is numbered (dudley_001, _002…) in ~/Music/DUDLEY/renders unless you
  choose elsewhere. Space plays/stops the last take. Save/Load preset keeps the whole setup.
- **Open last take in Logic Pro**, or tick *send every take to Logic Pro* to have each render go
  straight there. Renders are 24-bit WAV by default (32-bit float is in the same menu); the
  .manifest.json / .schedule.json beside each take are ignored by Logic.

The command line below still works for batch jobs.

## Install

    pip3 install numpy scipy        # ffmpeg on PATH lets it read any format (brew install ffmpeg)

## Recipes

    # rain that speaks: ramps from rain's own envelope to the voice's over the first 30%
    python3 dudley.py -c rain.wav -d voice.wav --path "0:0,0.3:1,1:1" -o rain_speaks.wav

    # voice envelope sliding into bell envelope over a rain grain stream
    python3 dudley.py -c rain.wav -d voice.wav -d bells.wav --path "0:1,0.45:1,0.75:2,1:2"

    # cross-synthesis: bells carrying the voice's harmonics too
    python3 dudley.py -c bells.wav -d voice.wav --texture 0.7 --grain-ms 120 --density 45

    # entropy walk across two donors, grains pulled from two carriers
    python3 dudley.py -c rain.wav -c bells.wav -d voice.wav -d bells.wav --path walk:0.5 --scan random

    # one frozen moment of the carrier, played by the donor's envelope over time
    python3 dudley.py -c voice.wav -d bells.wav --scan freeze:0.2 --grain-ms 150 --pitch -12

    # donor loudness drives how much hybridization happens
    python3 dudley.py -c drone.wav -d speech.wav --path rms

`python3 dudley.py -h` lists everything (grain size, density, onset jitter, spray, pitch,
stereo width, lifter, gain clamp, dynamics, duration, sample rate).

## Outputs

- `name.wav`: stereo, 24-bit PCM by default (`--bits 32` for float)
- `name.manifest.json`: every argument, SHA-1 of every input and the output, entropy source
- `name.schedule.json`: per-video-frame curves (`position`, `alpha` 0..1, `rms`, `centroid_hz`,
  `flux`, plus normalised versions) at `--fps` (default 30). `alpha` is the morph path, ready
  to drive morph_engine's image morph so picture and sound hybridize together.

## Tuning notes

- `--lifter-ms` sets envelope detail. 0.6 gives a broad tilt only; 1.2 (default) gives formants;
  3+ starts letting pitch structure through.
- Grains shorter than ~30 ms smear low-frequency envelope detail; longer than ~150 ms blur
  fast changes in the donor.
- `--dynamics carrier` keeps the carrier's loudness and takes only the donor's *shape*.
- `--onset-jitter 0` is metronomic (can buzz at the grain rate); 1 is even but unpatterned; 3+
  clumps and leaves holes, on purpose.
