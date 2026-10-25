# practice

The working index of **xik6 / William Victor Newbold** — experimental music, generative video and broadcast tools, built in conversation with Claude.

**[Open the audio rack](https://m8w.github.io/Practice/)** — every audio tool, searchable, with where each one lives.

## Instruments

Played live in the browser.

- **SACCADE** — Attention instrument: any click snaps to GRASP (seeded LCG, quantised 16ths, variable-ratio reward, the same eleven places every time); stillness lets DWELL back in over 40 s (true entropy, long windows from anywhere in the archive). Streams WAV, RF64 and BW64 of any size from disk.
- **HELMHOLTZ** — Sound-effects synth: CZ-style phase distortion, 48-partial additive, eight noise sources including true entropy, formant comb, crush and reverb. 18 presets, 24-bit WAV and 8-variation export.
- **Closed String Instrument** — Multitouch surface whose partials come from the closed-string mass formula. Dual swaps R↔1/R and Measure collapses six vacua. The chat also holds the numpy render of the four-part piece.
- **Proof Horizon** — Euclid's 465 propositions become chords that walk the E8 lattice into a spinning black hole. Waveforms come from live-solved PDEs: Burgers, Euler, Maxwell, Bondi accretion and an 8-D wave.
- **SHIFT** — Bode frequency shifter built from a Hilbert transform. The barberpole that grew into CAYLEY.
- **CAYLEY XI** — Octonion ring machine: eight voices multiplied around the Fano plane, harmonic sequencer, granular layer, stereo rotation, XY morph pad, MIDI in and WAV recording.
- **Kernel Choir** — Graph null-space voices: every vertex sings and each closed neighbourhood sums to exactly zero. Flow paths, 5-cube to 600-cell, tape loops, Geiger counter, DNA reader and a self-growing Creature.
- **QOIX Synthesizer** — A full software synth: subtractive, 4-operator FM, wavetable, spectral, Scala microtonal and granular engines, stereo oscillators, a mod matrix, a random note generator, MIDI-file render to WAV and a session recorder. It also builds as a Mac app. [open](https://h92o.github.io/qoix/) · [github.com/h92o/qoix](https://github.com/h92o/qoix) · [github.com/m8w/qoix](https://github.com/m8w/qoix)
- **Glyph Instrument** — Voynich-style procedural glyphs appear one at a time in paginated grids, each with its own 100–2000 Hz tone. Saves PNG frames for video.
- **AS IT IS** — A wall of Bill's own liturgical words. A held gesture pulls the field into a pillar of light while letter-voices converge on 136.1 Hz.
- **GRASSMANN** — Colour matrix × frequency matrix displaces six 3-D surfaces, Klein bottle and Möbius strip among them. Three noise drones, a databender FX chain and a Blogger-safe embed.
- **SERENITY** — Ambient random generator, with a sandboxed Blogger embed.
- **LARSEN** — Feedback instrument: two banks of up to five delay lines, series or parallel, cross-coupled, with XOR bit-plane flips, bit-FIFO removal, ping-pong and mid/side. DAMAGE macro, ignite and panic.
- **Fractal Timing** — Beatless beats: a Cantor-style drum subdivision tree and a Koch melody expander share one depth. A beatlessness slider bends the splits toward the golden ratio. 19-TET and Bohlen–Pierce scales.
- **Technicolor Interposition** — Two Poisson streams fight for one output. A real collision freezes and blooms a video the player chooses, over that video's own sound. Records webm.
- **Solvable / Solvable / Proven** — Three-movement Fermat piece: the just chord hidden in 3³+4³+5³=6³, beats closing to zero, and one inner pair that never locks.
- **Appliance Station** — A dying lawn mower, a refrigerator and an A/C unit as never-repeating procedural noise machines, with fault events, gauges and a phosphor scope.
- **Oracle Song** — 88 keys, 64 hexagrams, 24 runes. Each throw becomes spoken stanzas over quiet chord drones. The chat also holds the Cage-style word lists as MP3.

## Endless streams & pieces

Run unattended and compose as they go.

- **Endless Station** — Serves all three endless pages offline on the Mac mini, announces each piece in Dispatch's voice and writes a now-playing file for OBS.
- **Aggregate Field** — Endless modern-classical stream from the documented methods of Babbitt, Feldman, Cage, Stockhausen, Partch, Glass, Riley, Reich, Pärt, Fripp and more, layered out of sync. Microtonal, with a sung solfège chorus; keeps playing in the background.
- **SAID Symbol Set** — Symbol forge whose motion, light, time and voice channels all sound. The chat also holds “SAID · Six Times”, the six-movement soundtrack and video rendered from Bill's notebook glyphs.
- **Endless Symphony** — Full orchestral works from the public-domain symphony repertoire, with polytonal and polymicrotonal layers, abstract rhythm and CC0 instruments.
- **Endless Sonata** — New piano pieces forever, modelled on ~400 public-domain works with Für Elise and all three Moonlight movements weighted in. Microtonal tunings; save pieces and reopen them in groups.
- **Days on End** — Days-long OS-entropy crossfade of 18 visual generators and 14 sound voices: Shepard tone, Risset rhythm, formant choir, Karplus strings, acid bass, drum machine. Starts a fresh cycle when OBS goes live.
- **Perpendicular Now** — Song/video: ring-modulated voices, a crushed ping-pong delay, a self-erasing asemic pen redrawn at 90°, SAID captions and a live timestamp title. 000 PEACE.

## Generators & MIDI

Drive the Supernova II, microKORG and soft synths.

- **vital_bridge.py** — Makes Vital wavetables (spectral, glyph-to-wave, audio slices) and plays Vital while sweeping wavetable position from a Lorenz attractor, a random walk or drifting LFOs. --follow lets an existing MIDI stream play the notes.
- **Tensor Resonance Engine** — A 5×5×5 colour × loudness × frequency tensor whose singular values become unaligned microtonal triads. Drives CAYLEY XI, Surge XT, Dexed and DUDLEY, records stems, MIDI and σ, makes whole albums, and changes Surge patches.
- **powwow_drums.py** — Percussion engine on Drum Maps a–h: heartbeat pulse, honor beats, call-and-response, sneak-up. Parts 5–7 use Bank H drum Programs because maps e, f and g aren't in ROM.
- **physics_synth.py** — Masses and springs in 3-D make the sound. String, pendulum, modal, chaotic-orbit and wave-packet patches, with bridges to radiot, Dispatch and gem.bfg.
- **noodle_soup_stereo.pd** — Pd port of the noodle network: cell_sine~, cell_saw~ and cell_chaos~ abstractions in three layers, in stereo, with entropy streamed from a WAV recording. Tested in real Pd.
- **noodle_network.py** — 32 oscillator cells (LFO, logistic-map chaos, audio-rate) wired by one routing matrix, with entropy from os.urandom.
- **sn2_backup.py** — SysEx archive and restore for the Supernova II. The dump starts on the synth; it saves every byte to a .syx file, and it sends archives back at a safe pace.
- **sn2_perf.py** — Composes eight-part Performances in the Supernova's edit buffer with Bank Select and Program Change, with mutate, freeze, blend between recipes and saved JSON recipes.
- **SN2 Note Generator** — Two-synth engine for the Supernova II and microKORG XL: background chaos on all eight Parts, one 5- or 7-note run at a time, mod-wheel drift, and preset cycling on both synths every 17:17 song unit. Records each session to ~/Music/sn2-recordings.
- **sn2_gen.py** — Generative MIDI for Supernova II Performance Parts 1–8: drift, scatter, drone, pulse and silence, plus 5- and 7-note runs. A live settings menu changes Parts while it plays.
- **nietzsche.py** — Walks 28 Wahnzettel sections across all eight Parts with DSP-safe program changes: 6 s floor, 90 s cooldowns, a stale-patch check and a gap audit at the end.
- **VST MIDI Randomizer** — Random notes and patch changes with the right bank and program logic for each plugin: Vital, Dexed, Surge XT. It began as the Supernova II randomizer and now also sits in tab 5 of the Tensor engine.

## Shaping, rendering & AV

Turn existing sound and picture into new work.

- **Nested Dimensions** — MilkDrop and fractal-stream planes in 3-D, with nonstop crossfaded audio from the xik6 archive.org catalogue and a control panel for several windows. [full version](https://m8w.github.io/nested-dimensions/) · [github.com/m8w/nested-dimensions](https://github.com/m8w/nested-dimensions)
- **relaxfft.py** — Spectral sustain: each FFT bin relaxes toward the input the way the relaxation dark-energy flow does (X′ = −nX).
- **DUDLEY** — Sound hybridization: a carrier's grain stream wears a donor's spectral envelope. Transport or cepstral morph, drawn or entropy-walk paths, numbered takes sent straight to Logic Pro. CC0.
- **xik6_mosaic.py** — Reworks each long video's picture to react to its own soundtrack. Runs in the background for days on the Seagate without touching the broadcast.
- **Chromaflow** — Audio-reactive VJ page with live-editable shader configs in the spirit of G-Force, an Auto-VJ mode and two MIDI-mappable knobs.
- **morph_engine.py** — Audio-reactive image morphing: ORB-matched meshes and five audio modes that hold on quiet passages, rush on loud ones and snap to beats. Writes a manifest with every render.

## Broadcast & recording

Newbold Radio's mixer, announcer, recorders and converters.

- **Dispatch Replay** — Watches recording folders, plays back and queues finished recordings, and airs them through OBS WebSocket or straight to RTMP with ffmpeg, reconnecting at 11:45.
- **dispatch_aggregate.py** — Speaks Aggregate Field's piece and section changes in Dispatch's voice, logs JSONL and writes YouTube chapter timestamps. Endless Station now does this for all three endless pages.
- **Video audio extractor** — WAV and MP3 from .mov and .mp4 with 3-hour splits, plus frames and 30-second clips. Resumes, skips corrupt files and runs at low CPU priority.
- **wav2mp3.py** — Pick a folder and every WAV gets an MP3 beside it (VBR, about 245 kbps). Skips what's already done.
- **BlackHole monitor recorder** — Two-hour BlackHole 2ch capture to WAV and MP3 with live headphone monitoring. Refuses loopback outputs that would feed back.
- **Dispatch** — The broadcast's announcer and clock: machine-voice announcements, OBS levels, stream cycles every ~11h45m, and resets of MIDI, synth and visual state at each cycle.
- **radiot.py** — Newbold Radio's mixer: Supernova II, microKORG and Dispatch lanes at EBU R128 loudness, out through BlackHole 16ch to OBS. Duration-lane slots over a 17,000-track index. [github.com/m8w/newbold-radio](https://github.com/m8w/newbold-radio)
- **Multi-lane recorder** — USB mixer and BlackHole (radiot) lanes with per-lane gain and meters, EQ voicings, gentle compression, an RF64 working file and two-pass loudnorm to a 2-hour WAV.

## In this repo

- `index.html` — the audio rack (GitHub Pages front page)
- `audio/saccade.html` — SACCADE, the attention instrument, as one standalone file

All open source, free, public domain where the material allows. 000 Peace.
