# Tensor Resonance Engine

A 5×5×5 tensor [COLOR × LOUDNESS × FREQUENCY] is split by Tucker/HOSVD into 15 singular values σ.
Each σ plays a triad of 3 unaligned microtonal frequencies (20 Hz–20 kHz, no scale, no ratios): 15 triads, 45 oscillators.
The frequencies drift at a speed ∝ dσ/dt, and the triads re-roll at random moments, so there is no beat grid.

## The five slots on each axis
The three original slots sit at the ends and the middle; between them is an **in-between slot** whose value is a variable:

| axis | slot 1 | **slot 2** | slot 3 | **slot 4** | slot 5 |
|---|---|---|---|---|---|
| COLOR (timbre) | RED · saw | **PURPLE** · saw↔sine | BLUE · sine | **GREEN** · sine↔triangle | YELLOW · triangle |
| LOUDNESS (envelope) | LOUD | **LOUD~SOFT** | SOFT | **SOFT~MIDDLE** | MIDDLE |
| FREQUENCY (drift scale) | F₁ · ×0.5 | **F₁·₂** | F₂ · ×1 | **F₂·₃** | F₃ · ×2 |

The IN-BETWEEN SLOTS card in tab 1 sets each one from 0 (like its left neighbour) to 1 (like its right one).
Tick **follow the tensor** and they move by themselves, each leaning toward whichever neighbouring slice is stronger at the moment.
They're recorded in sigma.json (`blends`) and randomized per track in album mode.

## Start (any Mac: MacBook Pro or Mac mini)
Copy this whole folder to the Mac (AirDrop works), then in Terminal:

    cd ~/Music/tensor_resonance      # wherever you put it; typing the path alone gives "permission denied"
    chmod +x run.sh surge.sh dexed.sh keep_dexed_alive.command   # once
    ./run.sh                         # first run builds its own .venv (~1 min), then opens Chrome

Press START. The address is http://127.0.0.1:5055 (port 5000 is AirPlay's on macOS).
If something doesn't work, run `./run.sh --check` and paste its output.
On a Mac without Python, `python3` offers to install Apple's command line tools first; say yes.

## Tensor Flow v2 (tab 1, on by default)
The tensor now moves by a flow built from three papers. Only their maths is borrowed; nothing is trained.
- **Relaxation** (relaxation dark energy, arXiv 2610.13739): the tensor relaxes toward an equilibrium T∞ on its own clock.
  Slow, broad changes glide in; sharp, local ones ring as damped waves across the 5×5×5 grid.
  Each σ has an equation of state w; when w rises through −1 (a "phantom crossing"), that triad re-rolls.
  New equilibria arrive at random moments (**epochs**).
- **Cross-attention + memory** (X-AVDD, arXiv 2610.13687): COLOR, LOUDNESS and FREQUENCY attend to each other in both directions,
  and that shapes the equilibrium. Recent states are distilled into 4 prototypes; an epoch can recall one, so passages return changed, never copied.
- **Alignment-free readout** (OpenPA, arXiv 2610.13826): each triad's query listens over all 125 cells, and its timbre, envelope and drift
  come from where it listens. Three overall scores: **brightness** (opens the high end; also sent as MIDI CC74),
  **fluency** (scales the drift), **prosody** (scales how often triads re-roll).

Sliders: EXPANSION H (how fast the clock runs), RELAX n, SOUND c_s (how much it rings), CROSS-ATTN κ, MEMORY, EPOCHS/s; NEW EPOCH forces one.
The FLOW READOUT card shows all of it live. FLOW → classic brings back the first engine (`./run.sh --flow classic` from Terminal).
Takes record e-folds, epochs, w and the aspects in sigma.json, and DUDLEY's σ path can follow brightness, fluency, prosody or "disequilibrium".
CC74 is a synth's brightness/timbre controller (MPE "slide" in Surge XT and Vital). To turn it off: the checkbox in tab 9, or `--no-brightness-cc`.

## Its own MIDI lane (keep it off IAC Bus 1)
The engine never takes IAC Bus 1 by itself, because your visuals and SN2 streams may already use it.
- **On one Mac:** add an IAC bus named **Tensor** (Audio MIDI Setup → Window → Show MIDI Studio → double-click IAC Driver → Ports → +, rename it Tensor).
  Then `./run.sh --midi` uses it, and you pick "IAC Driver Tensor" as the input in Dexed or Surge.
  With no Tensor bus, `--midi` makes a private port called "Tensor Resonance" instead.
- **MacBook → Mac mini over Bluetooth:** on the Mac mini open Audio MIDI Setup → MIDI Studio → the Bluetooth button → **Advertise**.
  On the MacBook open the same window and **Connect** to the Mac mini. Each Mac now shows the other as a MIDI device of its own, separate from IAC Bus 1.
  On the MacBook, send to it: `./run.sh --midi "<part of its name>" --midi-mode dexed`, or pick it in tab 9 and in surge.html.
  On the Mac mini, set Surge's / Dexed's MIDI input to that Bluetooth device only.
  Bluetooth carries little data: use single-channel mode (Dexed preset). MPE's pitch-bend stream can lag over Bluetooth.
- **MacBook → Mac mini over the network** (Wi-Fi/Ethernet, more headroom, fine for MPE): Audio MIDI Setup → MIDI Studio → **Network**,
  create a session on both Macs and connect them. It also shows up as its own port.

## MIDI: Dexed, Surge, anything
    ./run.sh --midi --midi-mode dexed               # Dexed: channel 1, nearest semitone, 3 triads
    ./run.sh --midi                                 # MPE, exact microtones (Surge XT / Vital in MPE mode)
    ./run.sh --midi --midi-test --midi-mode dexed   # play a test chord and exit
- In Dexed, a triad's notes hold until it re-rolls, so chords change at random moments. The RE-ROLL sliders in tab 1 set how often.
- Use one MIDI source at a time: either `--midi` here, or tab 9 in the browser.

## Dexed: why it crashed every few hours, and what changed
A MIDI **Program Change** can crash Dexed 1.0.x. That's a bug in Dexed: it repaints its window from the audio thread
while handling one, and once in a few thousand changes that corrupts memory (crash report: `rebuildProgramCombobox`,
`pointer being freed was not allocated`). The notes never cause it; Program Changes do, and the VST randomizer sent one
on every σ surge.
- **Now:** with Dexed picked in the VST randomizer, every patch change goes as a whole **voice in SysEx** (the DX7's own voice dump),
  which Dexed handles safely. Voices come from your Dexed cartridges through the engine, or from .syx files loaded on that page.
  Chrome asks once to let the page "control and reprogram MIDI devices": allow it (that's SysEx).
  "Use Program Change instead" is still there, off.
- **Tab 9:** DEXED VOICE sends one voice now; "new Dexed voice at every epoch" changes it with the v2 flow. The DEXED PRESET also turns CC74 off.
  From Terminal: `./run.sh --midi --midi-mode dexed --dexed-voice epoch` (add `--dexed-mutate 0.3` to alter each voice a little).
- **Other senders:** `./dexed.sh --watch` prints every Program Change, Bank Select and SysEx arriving on each MIDI input, with the port name.
  If one shows up on a port Dexed listens to (for example IAC Bus 1, if program changes for the SN2 travel on it), untick that input in Dexed
  (Options → Audio/MIDI Settings) and keep only **IAC Driver Tensor**.
- **Safety net:** double-click `keep_dexed_alive.command`. If Dexed quits, it is reopened in the background within seconds,
  and each restart is logged to ~/Music/TensorResonance/dexed_restarts.log.
- `./dexed.sh --list` shows the cartridges found; `./dexed.sh --send Tensor` sends one random voice to that port.

## Surge XT patch changing → ./surge.sh
    ./surge.sh                          # Surge on this Mac
    ./surge.sh --host 192.168.1.20      # Surge on another Mac (that Mac's IP), over OSC
`./surge.sh` needs nothing but Python. It opens the patch changer (it also works with `./run.sh` running, at /surge.html).
- **OSC route** (most direct; any patch, by name; never touches MIDI): in Surge XT's menu, open the OSC settings and switch **OSC input** on (port 53280).
  OSC is a separate network channel, so it can't collide with IAC Bus 1, and it can reach Surge on the Mac mini from the MacBook.
- **MIDI route:** a Surge program change only loads patches from `Patches/MIDI Programs/` in Surge's user folder (normally Documents/Surge XT).
  Fill it once with `python3 surge.py --midi-programs` (or the FILL button), then **quit and reopen Surge**: it builds its program table only at start.
  Use the standalone Surge app; the VST3 build ignores program changes, and inside Logic they may not reach it.
- The copy of vst-midi-randomizer.html in this folder knows these banks when it's opened through the engine.

## Record (tab 10, or ● REC in the header)
Every take goes to ~/Music/TensorResonance/takes/take_…/:
mix.wav, tensor.wav, cayley.wav, external.wav (24-bit stereo, sample-aligned), all MIDI as .mid
(microtonal bends included), sigma.json (the σ trajectory, frame by frame), and manifest.json (SHA-1 of every file).
- **External input** records Dexed/Surge audio through BlackHole (a free loopback driver) or your audio interface.
- **MAKE A RECORD** records N tracks automatically. Each track gets a new tensor, new settings, a new CAYLEY XI preset and a new VST patch. The result goes to albums/<name>/ with numbered mixes and tracklist.txt.
- Without the Python engine, a take is downloaded as a .zip when you stop.

## DUDLEY (tab 11)
DUDLEY is included unchanged in dudley/ (CC0; its own window still opens with "Open DUDLEY.command").
Pick carriers (C) and donors (D) from any take or DUDLEY's test sounds, then RENDER.
The new morph path is the **σ path**: the hybrid follows the take's recorded tensor (Σ|dσ/dt|, the dominance of one dimension, or a single σ). The curve is drawn before you render.
ALL HYBRIDS crosses every track of a take with every other. Renders are saved in the take's dudley/ folder.

## Notes
- The `conda-libmamba-solver` error at Terminal login is Anaconda's own installer plugin; it doesn't affect this (run.sh uses its own Python).
- An h11/httpcore pip warning in Anaconda base is harmless here; `pip install -U httpcore` clears it.

## Files
app.py (engine + server) · flow.py (Tensor Flow v2) · dexed.py + dexed.sh + keep_dexed_alive.command (Dexed) · index.html (11 tabs) · surge.sh + surge.html + surge.py (Surge XT) · dudley/ (DUDLEY) ·
cayleyXI.html + vst-midi-randomizer.html (your instruments; these load in tabs 4 and 5) · run.sh · requirements.txt
