# vital_bridge.py

Python ↔ Vital wavetable bridge. Two halves that work independently:

1. **gen** — build wavetables Vital can import
2. **play / learn** — drive Vital's notes and sweep its wavetable positions over MIDI

---

## Install

```bash
pip3 install numpy mido python-rtmidi
pip3 install matplotlib        # optional, only for --preview
```

`numpy` alone is enough for `gen`. `mido` + `python-rtmidi` are needed for
`play`, `learn` and `ports`.

---

## 1. Generating wavetables

```bash
python3 vital_bridge.py gen spectral --mode formant --frames 64 --preview
python3 vital_bridge.py gen glyph    --keys 5 --spin 90 --sharp 0.3 --preview
python3 vital_bridge.py gen audio    --input ~/down1/take.wav --frames 128
```

Files land in `wavetables/`. Each is a mono WAV of 2048-sample frames with a
`clm ` chunk declaring the frame size, which is the format Vital and Serum read.

**To load one:** in Vital, click the oscillator's wavetable name → **Open** →
pick the `.wav`. Then the oscillator's **position** knob scans through the frames.

### The three generators

| | what it does | sounds like |
|---|---|---|
| `spectral` | morphs between additive spectra | the brightest and most "wavetable" of the three — 42–74 audible harmonics |
| `glyph` | reads an asemic stroke as one cycle | soft, organic, 17–32 harmonics |
| `audio` | slices single cycles out of a recording | whatever the source does over time |

**spectral** `--mode`:
- `drift` — sparse random partials, different tilt each keyframe
- `formant` — 2–4 resonant peaks that move across the table (most vocal)
- `primes` — only prime-numbered harmonics
- `oddeven` — morphs from odd-only (hollow, clarinet) to even (full)

**glyph** draws closed looping strokes and reads them as waveforms.
- `--sharp` (0–1) is the fraction of points the pen pauses on. Each pause makes a
  hard corner, which is where the harmonics come from. 0.3 is the sweet spot;
  0 gives a dull triangle, 0.6 starts losing motion again.
- `--read even` (default) traces at constant speed, which sharpens cusps.
  `--read pen` gives every stroke equal time, which smooths them — darker.
- `--spin` rotates the reading angle across the table, so the x and y of the same
  stroke crossfade. Try `--spin 90`.
- `--points FILE` takes your own glyphs: JSON, a list of glyphs, each a list of
  `[x, y]` points. This is the hook for the asemic pages and the glyph instruments.

**audio** finds the pitch of each slice by autocorrelation, cuts exactly one cycle
at a zero crossing, and removes the end-to-start jump. `--min-hz` / `--max-hz`
bound the pitch search; widen them for very low or very high sources.

Common flags: `--frames` (2–256), `--seed` (printed every run so you can recreate
a table), `--name`, `--outdir`, `--preview`, `--pcm16`, `--table-norm`
(keep relative loudness between frames instead of normalising each one).

---

## 2. Playing Vital

### Setup on the Mac mini

1. Open Vital — as a plugin in Reaper or Logic, or standalone.
2. Start the bridge once so its MIDI port exists:
   ```bash
   python3 vital_bridge.py play --no-notes --duration 60
   ```
   This creates a virtual port called **Vital Bridge**. It exists only while the
   script runs, and it is separate from IAC Bus 1 — the script refuses to write
   to Bus 1 at all, so your visual and SN2 streams are never touched.
3. In your DAW, set the Vital track's MIDI input to **Vital Bridge**.
   Standalone Vital: pick it under the MIDI input menu.

### Mapping the knobs

```bash
python3 vital_bridge.py learn
```

It walks the five lanes one at a time and waits for you between each. For each
one: right-click the Vital knob → **Learn MIDI Assignment**, then press Enter and
it sweeps that CC alone, so Vital can't grab the wrong one.

Default lanes:

| CC | suggested target |
|----|------------------|
| 21 | Osc 1 wavetable position |
| 22 | Osc 2 wavetable position |
| 23 | Osc 3 wavetable position |
| 24 | Osc 1 spectral morph amount |
| 25 | Filter 1 cutoff |

Nothing is fixed — map them anywhere. `--cc 30,31,32` uses different numbers, and
the number of lanes follows the list length.

To map just one: `python3 vital_bridge.py learn --single 21`

### Running it

```bash
# Python plays the notes and sweeps the tables
python3 vital_bridge.py play --mode auto --motion lorenz

# your existing stream plays the notes, Python only sweeps the tables
python3 vital_bridge.py play --follow "IAC Driver Bus 1" --no-notes
```

The second form is the one that fits your setup: your MIDI randomization engine
keeps driving everything on Bus 1, this reads those notes, forwards them to Vital
on its own port, and moves the wavetable positions underneath. Note-ons can jolt
the motion (`--kick`), so the tables respond to the stream without being locked to it.

**`--motion`** picks what drives the positions:
- `lorenz` — Lorenz attractor; the three lanes are correlated but never repeat
- `walk` — independent mean-reverting random walks, slowest and least predictable
- `lfo` — several sines at golden-ratio-related periods, so they drift in and out
  of phase over many minutes

**`--mode`** picks what the composer does: `auto` (sections of 1–3 min that choose
themselves), or pin one of `drift`, `drone`, `pulse`, `scatter`, `silence`.

Useful flags: `--speed` (motion rate), `--range 0.1,0.9` (keep positions off the
extremes), `--cc-rate` (default 40/s; repeats are suppressed so it stays quiet),
`--scale` / `--root` / `--octaves`, `--bpm`, `--duration`, `--seed`,
`--port NAME` to use an existing port instead of the virtual one,
`--dry-run` to print MIDI instead of sending it.

`python3 vital_bridge.py ports` lists what's available.

Ctrl-C always sends note-offs for everything sounding plus an all-notes-off, so
nothing hangs.

---

## Notes

- Vital's position knob interpolates between frames, so more frames means a
  smoother sweep. 64 is a good default; 256 is the maximum.
- CC is 7-bit, so a very slow sweep across a table with few frames can step
  audibly. More frames, or a touch of reverb, hides it.
- The script never writes to IAC Bus 1. If `--port` matches Bus 1 among others it
  skips it and says so; if Bus 1 is the only match it refuses and exits.
