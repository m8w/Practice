# practice

The working index of **xik6 / William Victor Newbold** — experimental music, generative video and broadcast tools, built in conversation with Claude.

**[Open the audio rack](https://m8w.github.io/Practice/)** — every audio tool, searchable, with Play and Files links.

Every tool lives in [`audio/`](audio/), one folder each. Browser instruments play straight from GitHub Pages at `https://m8w.github.io/Practice/audio/<folder>/`; scripts and patches have a README in their folder. Tools that only existed inside a chat were rebuilt by replaying every file write and edit from that chat, then checked.

## Instruments

Played live in the browser.

- **SACCADE** — Attention instrument: any click snaps to GRASP (seeded LCG, quantised 16ths, variable-ratio reward, the same eleven places every time); stillness lets DWELL back in over 40 s (true entropy, long windows from anywhere in the archive). Streams WAV, RF64 and BW64 of any size from disk. [play](https://m8w.github.io/Practice/audio/saccade/) · [files](audio/saccade)
- **HELMHOLTZ** — Sound-effects synth: CZ-style phase distortion, 48-partial additive, eight noise sources including true entropy, formant comb, crush and reverb. 18 presets, 24-bit WAV and 8-variation export. [play](https://m8w.github.io/Practice/audio/helmholtz/) · [files](audio/helmholtz)
- **Closed String Instrument** — Multitouch surface whose partials come from the closed-string mass formula. Dual swaps R↔1/R and Measure collapses six vacua. The chat also holds the numpy render of the four-part piece. [play](https://m8w.github.io/Practice/audio/closed-string/) · [files](audio/closed-string)
- **CAYLEY XI** — Octonion ring machine: eight voices multiplied around the Fano plane, harmonic sequencer, granular layer, stereo rotation, XY morph pad, MIDI in and WAV recording. [play](https://m8w.github.io/Practice/audio/cayley-xi/) · [files](audio/cayley-xi)
- **SHIFT** — Bode frequency shifter built from a Hilbert transform. The barberpole that grew into CAYLEY. [play](https://m8w.github.io/Practice/audio/shift/) · [files](audio/shift)
- **Proof Horizon** — Euclid's 465 propositions become chords that walk the E8 lattice into a spinning black hole. Waveforms come from live-solved PDEs: Burgers, Euler, Maxwell, Bondi accretion and an 8-D wave. [play](https://m8w.github.io/Practice/audio/proof-horizon/) · [files](audio/proof-horizon)
- **Kernel Choir** — Graph null-space voices: every vertex sings and each closed neighbourhood sums to exactly zero. Flow paths, 5-cube to 600-cell, tape loops, Geiger counter, DNA reader and a self-growing Creature. [play](https://m8w.github.io/Practice/audio/kernel-choir/) · [studio](https://m8w.github.io/Practice/audio/kernel-choir/studio.html) · [files](audio/kernel-choir)
- **QOIX Synthesizer** — A full software synth: subtractive, 4-operator FM, wavetable, spectral, Scala microtonal and granular engines, stereo oscillators, a mod matrix, a random note generator, MIDI-file render to WAV and a session recorder. It also builds as a Mac app. [play](https://m8w.github.io/Practice/audio/qoix/) · [open](https://h92o.github.io/qoix/) · [files](audio/qoix) · [github.com/h92o/qoix](https://github.com/h92o/qoix) · [github.com/m8w/qoix](https://github.com/m8w/qoix)
- **Glyph Instrument** — Voynich-style procedural glyphs appear one at a time in paginated grids, each with its own 100–2000 Hz tone. Saves PNG frames for video. [play](https://m8w.github.io/Practice/audio/glyph-instrument/) · [files](audio/glyph-instrument)
- **CAYLEY II** — The 8-voice octonion harmonic sequencer that came before CAYLEY XI: each voice is one octonion component, cross-mixed by a spinning unit-octonion rotor, with harmonic-series step patterns, wave-morph voices and haunted memory intrusions. [play](https://m8w.github.io/Practice/audio/cayley-ii/) · [files](audio/cayley-ii) · [github.com/m8w/3dx](https://github.com/m8w/3dx)
- **Quaternion** — SHIFT's Bode shifter and CAYLEY II's sequencer ported to a 4-voice plugin: each voice is one quaternion component mixed by the Hamilton product, morphing sine → triangle → saw → vocoder, with circular FM between the four and a CONJUGATE rotation mode. [github.com/m8w/3dx](https://github.com/m8w/3dx)
- **AS IT IS** — A wall of Bill's own liturgical words. A held gesture pulls the field into a pillar of light while letter-voices converge on 136.1 Hz. [play](https://m8w.github.io/Practice/audio/as-it-is/) · [files](audio/as-it-is)
- **GRASSMANN** — Colour matrix × frequency matrix displaces six 3-D surfaces, Klein bottle and Möbius strip among them. Three noise drones, a databender FX chain and a Blogger-safe embed. [play](https://m8w.github.io/Practice/audio/grassmann/) · [v1](https://m8w.github.io/Practice/audio/grassmann/grassmann-v1.html) · [files](audio/grassmann)
- **LARSEN × CAYLEY patch** — The LARSEN feedback instrument joined to the CAYLEY engine as a patch, in both Pure Data and Max (the larsencayley4 version). [files](audio/larsen-cayley-patch)
- **SERENITY** — Ambient random generator, with a sandboxed Blogger embed. [play](https://m8w.github.io/Practice/audio/serenity/) · [files](audio/serenity)
- **LARSEN** — Feedback instrument: two banks of up to five delay lines, series or parallel, cross-coupled, with XOR bit-plane flips, bit-FIFO removal, ping-pong and mid/side. DAMAGE macro, ignite and panic. [play](https://m8w.github.io/Practice/audio/larsen/) · [files](audio/larsen)
- **Fractal Timing** — Beatless beats: a Cantor-style drum subdivision tree and a Koch melody expander share one depth. A beatlessness slider bends the splits toward the golden ratio. 19-TET and Bohlen–Pierce scales. [play](https://m8w.github.io/Practice/audio/fractal-timing/) · [files](audio/fractal-timing)
- **Technicolor Interposition** — Two Poisson streams fight for one output. A real collision freezes and blooms a video the player chooses, over that video's own sound. Records webm. [play](https://m8w.github.io/Practice/audio/technicolor-interposition/) · [files](audio/technicolor-interposition)
- **Solvable / Solvable / Proven** — Three-movement Fermat piece: the just chord hidden in 3³+4³+5³=6³, beats closing to zero, and one inner pair that never locks. [play](https://m8w.github.io/Practice/audio/solvable/) · [files](audio/solvable)
- **ALL COLORS — opening score** — Scenes 1 and 2 of the chamber musical made from the 2020 inner-child journal, as a performable score for Sound In: BILL as a centred FM bell, BILLY as hard-panned detuned pairs that bloom on “ALL COLORS”, over a living drone. Tap to advance, or Perform for Cagean timing. [play](https://m8w.github.io/Practice/audio/all-colors/) · [files](audio/all-colors)
- **Sigil #4** — Graphic-score engine for page 19 and the purple page: the score's own words (Big Noises, 2 soft squeaks, Sunshine, Silo enters the void, Spirits of time) mark five sections over five minutes, with a black-flash rupture at the void. [play](https://m8w.github.io/Practice/audio/sigil-4/) · [files](audio/sigil-4)
- **Appliance Station** — A dying lawn mower, a refrigerator and an A/C unit as never-repeating procedural noise machines, with fault events, gauges and a phosphor scope. [play](https://m8w.github.io/Practice/audio/appliance-station/) · [mower](https://m8w.github.io/Practice/audio/appliance-station/mower.html) · [files](audio/appliance-station)
- **Oracle Song** — 88 keys, 64 hexagrams, 24 runes. Each throw becomes spoken stanzas over quiet chord drones. The chat also holds the Cage-style word lists as MP3. [play](https://m8w.github.io/Practice/audio/oracle-song/) · [files](audio/oracle-song)

## Endless streams & pieces

Run unattended and compose as they go.

- **Aggregate Field** — Endless modern-classical stream from the documented methods of Babbitt, Feldman, Cage, Stockhausen, Partch, Glass, Riley, Reich, Pärt, Fripp and more, layered out of sync. Microtonal, with a sung solfège chorus; keeps playing in the background. [play](https://m8w.github.io/Practice/audio/endless/aggregate/) · [files](audio/endless/aggregate)
- **Endless Station** — Serves all three endless pages offline on the Mac mini, announces each piece in Dispatch's voice and writes a now-playing file for OBS. [files](audio/endless/station)
- **SAID Symbol Set** — Symbol forge whose motion, light, time and voice channels all sound. The chat also holds “SAID · Six Times”, the six-movement soundtrack and video rendered from Bill's notebook glyphs. [play](https://m8w.github.io/Practice/audio/said-symbol-set/) · [files](audio/said-symbol-set)
- **Endless Sonata** — New piano pieces forever, modelled on ~400 public-domain works with Für Elise and all three Moonlight movements weighted in. Microtonal tunings; save pieces and reopen them in groups. [play](https://m8w.github.io/Practice/audio/endless/sonata/) · [files](audio/endless/sonata)
- **Endless Symphony** — Full orchestral works from the public-domain symphony repertoire, with polytonal and polymicrotonal layers, abstract rhythm and CC0 instruments. [play](https://m8w.github.io/Practice/audio/endless/symphony/) · [files](audio/endless/symphony)
- **Days on End** — Days-long OS-entropy crossfade of 18 visual generators and 14 sound voices: Shepard tone, Risset rhythm, formant choir, Karplus strings, acid bass, drum machine. Starts a fresh cycle when OBS goes live. [play](https://m8w.github.io/Practice/audio/days-on-end/) · [files](audio/days-on-end)
- **Perpendicular Now** — Song/video: ring-modulated voices, a crushed ping-pong delay, a self-erasing asemic pen redrawn at 90°, SAID captions and a live timestamp title. 000 PEACE. [play](https://m8w.github.io/Practice/audio/perpendicular-now/) · [files](audio/perpendicular-now)
- **No Memory Just Conditionally Occurs** — Seven chairs from pages 14–15 fire on their own Poisson clocks while a density curve carries the piece from near-silence to noise over 10–15 minutes, drawing marks as they sound. Runs until stopped. [play](https://m8w.github.io/Practice/audio/no-memory/) · [files](audio/no-memory)

## Generators & MIDI

Drive the Supernova II, microKORG and soft synths.

- **vital_bridge.py** — Makes Vital wavetables (spectral, glyph-to-wave, audio slices) and plays Vital while sweeping wavetable position from a Lorenz attractor, a random walk or drifting LFOs. --follow lets an existing MIDI stream play the notes. [files](audio/vital-bridge)
- **Tensor Resonance Engine** — A 5×5×5 colour × loudness × frequency tensor whose singular values become unaligned microtonal triads. Drives CAYLEY XI, Surge XT, Dexed and DUDLEY, records stems, MIDI and σ, makes whole albums, and changes Surge patches. [play](https://m8w.github.io/Practice/audio/tensor-resonance/) · [files](audio/tensor-resonance)
- **powwow_drums.py** — Percussion engine on Drum Maps a–h: heartbeat pulse, honor beats, call-and-response, sneak-up. Parts 5–7 use Bank H drum Programs because maps e, f and g aren't in ROM. [files](audio/powwow-drums)
- **physics_synth.py** — Masses and springs in 3-D make the sound. String, pendulum, modal, chaotic-orbit and wave-packet patches, with bridges to radiot, Dispatch and gem.bfg. [files](audio/physics-synth)
- **noodle_network.py** — 32 oscillator cells (LFO, logistic-map chaos, audio-rate) wired by one routing matrix, with entropy from os.urandom. [files](audio/noodle)
- **noodle_soup_stereo.pd** — Pd port of the noodle network: cell_sine~, cell_saw~ and cell_chaos~ abstractions in three layers, in stereo, with entropy streamed from a WAV recording. Tested in real Pd. [files](audio/noodle/pd)
- **sn2_perf.py** — Composes eight-part Performances in the Supernova's edit buffer with Bank Select and Program Change, with mutate, freeze, blend between recipes and saved JSON recipes. [files](audio/sn2-perf)
- **sn2_backup.py** — SysEx archive and restore for the Supernova II. The dump starts on the synth; it saves every byte to a .syx file, and it sends archives back at a safe pace. [files](audio/sn2-backup)
- **SN2 Note Generator** — Two-synth engine for the Supernova II and microKORG XL: background chaos on all eight Parts, one 5- or 7-note run at a time, mod-wheel drift, and preset cycling on both synths every 17:17 song unit. Records each session to ~/Music/sn2-recordings. [files](audio/sn2-note-generator)
- **SN2 tools** — Overnight helpers: sn2_chaos8 (eight independent random-note channels), a rolling recorder that writes a new MIDI file every six hours, a performance-dump fixer and a self-healing audio supervisor. [files](audio/sn2-tools)
- **sn2_gen.py** — Generative MIDI for Supernova II Performance Parts 1–8: drift, scatter, drone, pulse and silence, plus 5- and 7-note runs. A live settings menu changes Parts while it plays. [files](audio/sn2-gen)
- **nietzsche.py** — Walks 28 Wahnzettel sections across all eight Parts with DSP-safe program changes: 6 s floor, 90 s cooldowns, a stale-patch check and a gap audit at the end. [files](audio/nietzsche)
- **VST MIDI Randomizer** — Random notes and patch changes with the right bank and program logic for each plugin: Vital, Dexed, Surge XT. It began as the Supernova II randomizer and now also sits in tab 5 of the Tensor engine. [play](https://m8w.github.io/Practice/audio/vst-midi-randomizer/) · [files](audio/vst-midi-randomizer)

## Shaping, rendering & AV

Turn existing sound and picture into new work.

- **Nested Dimensions** — MilkDrop and fractal-stream planes in 3-D, with nonstop crossfaded audio from the xik6 archive.org catalogue and a control panel for several windows. [full version](https://m8w.github.io/nested-dimensions/) · [github.com/m8w/nested-dimensions](https://github.com/m8w/nested-dimensions)
- **relaxfft.py** — Spectral sustain: each FFT bin relaxes toward the input the way the relaxation dark-energy flow does (X′ = −nX). [files](audio/relaxfft)
- **DUDLEY** — Sound hybridization: a carrier's grain stream wears a donor's spectral envelope. Transport or cepstral morph, drawn or entropy-walk paths, numbered takes sent straight to Logic Pro. CC0. [files](audio/dudley)
- **xik6_mosaic.py** — Reworks each long video's picture to react to its own soundtrack. Runs in the background for days on the Seagate without touching the broadcast. [files](audio/xik6-mosaic)
- **random_soundfont.py** — Builds random SoundFont 2 files from a pile of WAVs: random key splits, velocity layers or drum kits, with random loops, envelopes, filters, pan, tuning and LFOs. [files](audio/random-soundfont)
- **Chromaflow** — Audio-reactive VJ page with live-editable shader configs in the spirit of G-Force, an Auto-VJ mode and two MIDI-mappable knobs. [play](https://m8w.github.io/Practice/audio/chromaflow/) · [files](audio/chromaflow)
- **morph_engine.py** — Audio-reactive image morphing: ORB-matched meshes and five audio modes that hold on quiet passages, rush on loud ones and snap to beats. Writes a manifest with every render. [files](audio/morph-engine)

## Broadcast & recording

Newbold Radio's mixer, announcer, recorders and converters.

- **Dispatch Replay** — Watches recording folders, plays back and queues finished recordings, and airs them through OBS WebSocket or straight to RTMP with ffmpeg, reconnecting at 11:45. [files](audio/dispatch-replay)
- **dispatch_aggregate.py** — Speaks Aggregate Field's piece and section changes in Dispatch's voice, logs JSONL and writes YouTube chapter timestamps. Endless Station now does this for all three endless pages. [play](https://m8w.github.io/Practice/audio/dispatch-aggregate/) · [files](audio/dispatch-aggregate)
- **Video audio extractor** — WAV and MP3 from .mov and .mp4 with 3-hour splits, plus frames and 30-second clips. Resumes, skips corrupt files and runs at low CPU priority. [files](audio/video-audio-extractor)
- **wav2mp3.py** — Pick a folder and every WAV gets an MP3 beside it (VBR, about 245 kbps). Skips what's already done. [files](audio/wav2mp3)
- **BlackHole monitor recorder** — Two-hour BlackHole 2ch capture to WAV and MP3 with live headphone monitoring. Refuses loopback outputs that would feed back. [files](audio/blackhole-recorder)
- **radiot.py** — Newbold Radio's mixer: Supernova II, microKORG and Dispatch lanes at EBU R128 loudness, out through BlackHole 16ch to OBS. Duration-lane slots over a 17,000-track index. [github.com/m8w/newbold-radio](https://github.com/m8w/newbold-radio)
- **Dispatch** — The broadcast's announcer and clock: machine-voice announcements, OBS levels, stream cycles every ~11h45m, and resets of MIDI, synth and visual state at each cycle. [files](audio/dispatch)
- **Multi-lane recorder** — USB mixer and BlackHole (radiot) lanes with per-lane gain and meters, EQ voicings, gentle compression, an RF64 working file and two-pass loudnorm to a 2-hour WAV. [files](audio/mixer-record)
