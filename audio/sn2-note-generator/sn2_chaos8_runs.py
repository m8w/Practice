#!/usr/bin/env python3
"""
sn2_chaos8_runs.py — 8-Part Novation Supernova II random note generator +
its own microKORG XL voice, on a TWO-PORT hardware model. This is the single
authoritative version -- it consolidates the hardware-correct Korg routing
with the ThumbJam-motif, Supernova filter/envelope, and crash-safety work
from a parallel copy of this script; see the README changelog for how.

  TWO PORTS, TWO SYNTHS:
  - midi_out: the Supernova II's own MIDI OUT/DIN port ("MIDI OUT" or
    "Supernova" in the port list). Carries the Supernova's notes/mod
    wheel/runs on channels 1-8 (one per Part), its Performance changes on
    its Global MIDI channel (16), and its own filter/envelope sound-design
    drift (see SUPERNOVA SOUND DESIGN below).
  - korg_out: the microKORG XL's OWN sound-engine USB-MIDI port, found by
    select_korg_port() as whichever port name contains both "korg" and
    "sound" (e.g. "microKORG XL SOUND") -- a physically separate cable from
    the Supernova's DIN thru. Carries the Korg's notes/mod wheel/runs on
    KORG_CHANNEL, its own Program cycling, and its own front-panel-knob
    sound-design drift (see KORG SOUND DESIGN below). If this port can't be
    found, Korg output is silently disabled (korg_out stays None) rather
    than crashing or falling back to sending Korg data down the Supernova's
    port on some channel -- that channel never reaches the Korg's sound
    engine at all, so there is no "channel 9 of the main port" fallback
    anywhere in this script.
  Both also fan out to a third, virtual CoreMIDI port ("sn2 chaos →
  visuals") so the butterchurn visualizer can read the identical stream
  from either synth in perfect sync -- see MIDI FAN-OUT below.

  RUNS: one continuous background thread plays melodic runs of 5 or 7
  notes, back-to-back with no gap between them (each note 0.1s - 1.0s
  long), on a randomly chosen target -- any of the Supernova's 8 Parts, OR
  the Korg (RUN_TARGETS). Only one run happens anywhere in the whole
  system at a time. Whichever target is playing a run has its normal
  single-note chaos paused (locked) so the run isn't muddied by unrelated
  notes on the same channel/voice. After a run finishes, there's a pause
  before the next run starts on a (possibly different) target -- how long
  that pause is gets slowly modulated, see MODULATION SYSTEMS below. Each
  new run's melodic shape (a sequence of offsets from its first note,
  transposed to a new root) comes from one of three sources:
  reusing/mutating the previous run's shape, a fresh fully-random shape, or
  a real motif extracted from Bill's ThumbJam playing (RUN_THUMBJAM_PROB
  chance, per fresh run), run through one random transformation (identity,
  invert, retrograde, mutate, or stretch/compress) so his actual phrasing
  feeds the generative runs on EITHER synth, not just the Supernova. See
  THUMBJAM_MOTIFS_5/7 and transform_motif().

  PERFORMANCE CYCLING (Supernova II): continuously and automatically picks
  a uniformly random Performance from PERFORMANCE_BANK_LETTER (default
  'C'), number 000-099, and plays it for one SONG_UNIT_SECONDS (17:17 =
  1037s) before switching to the next random pick (one is also sent right
  away at startup). Sent as a Performance Bank+Program Change on the
  Global MIDI channel (16). That ch-16 Program Change is the message the
  butterchurn visualizer locks its clock to (t≡0, then every 1037s).

  KORG PRESET CYCLING (microKORG XL): independently, on the Korg's own
  sound engine. Picks a uniformly random preset 000-127 (the FULL range --
  no exclusions: Bill's rig feeds live audio into the Korg's vocoder
  input, so even the mic-only vocoder programs produce sound on his setup)
  and plays it for KORG_PRESET_DWELL_SECONDS (5:55), independent of the
  Supernova's SONG_UNIT_SECONDS cadence. Bank Select is fixed at MSB=0,
  LSB=0 -- a single Program Change 0-127 addresses the whole A11-B88 range
  directly, unlike the Supernova's bank-letter scheme.

  MODULATION SYSTEMS: several independent, slow-moving modulators run on
  the computer side. Each one is a smooth sine curve so it rises and falls
  continuously with no jumps, and each one's cycle length is a multiple of
  SONG_UNIT_SECONDS (17:17), so the whole piece breathes in arcs related to
  that one "song unit" instead of drifting on an arbitrary clock.

  Steering modulators — reshape the ranges the note/run loops pick from,
  rather than sending their own MIDI messages. Every Supernova Part gets
  its own copy, phase-offset from the other 7 (CHANNEL_PHASE); the Korg
  gets its own copy too, on its own phase (KORG_PHASE, chosen not to
  coincide with any Supernova phase):
    - density   (1 song unit)     — how close together notes trigger
    - velocity  (1.5 song units)  — overall loudness/dynamics swell
    - activity  (2 song units)    — how present each voice is; voices thin
                                     out and swell back on their own offset
                                     schedule instead of moving in lockstep
    - mod wheel (0.5 song units)  — the center point the mod wheel's
                                     random values drift around, per channel
    - runs      (0.75 song units) — how often wandering runs happen

  Sound-design modulators — TWO SEPARATE systems, one per synth, each
  using that synth's own real CC numbers, sent via that synth's own port:

  KORG SOUND DESIGN (korg_sound_design_loop, korg_out, KORG_CHANNEL): the
  microKORG XL's own front-panel-knob CCs, from its Owner's Manual MIDI
  appendix. One instance, offset parameter-to-parameter since there's only
  the one channel:
    - pitch      (1.25 song units) — Pitch Bend, a gentle detune wobble
    - hardness   (1.75 song units) — Drive/Wave-Shape Depth (CC#83), edge/grit
    - cutoff     (2.25 song units) — Filter 1 Cutoff (CC#74)
    - resonance  (2.75 song units) — Filter 1 Resonance (CC#71)
    - osc sync   (3.25 song units) — OSC2 Osc Mod (CC#19); this CC is zoned
                                      by the synth itself (OFF/RING/SYNC/
                                      RING+SYNC), so drifting it wanders
                                      through those modes rather than a
                                      continuously variable "sync amount"

  SUPERNOVA SOUND DESIGN (supernova_sound_design_loop, midi_out, ch 1-8):
  the Supernova II's own real MIDI Controller Allocation CCs -- a
  completely different CC set from the Korg's, so the two systems never
  collide even though both ultimately reach the fan-out. One instance per
  Part, each on its own channel phase plus its own parameter phase (8
  parameters spread evenly around the circle). Bill specifically loves how
  envelope changes here morph the Korg's vocoder output (fed by the
  Supernova's audio), so the envelope drifts are the actual point of this
  system, not incidental:
    - cutoff       (1.1 song units) — Filter Cutoff Freq (CC#105), never
                                       fully closes (floor of 30)
    - resonance    (1.6 song units) — Filter Resonance (CC#106)
    - overdrive    (2.1 song units) — Filter Overdrive (CC#104)
    - env amount   (2.6 song units) — Filter Freq Env 2 amount (CC#107),
                                       modest range so the filter env stays
                                       expressive rather than swinging wild
    - env1 attack  (3.1 song units) — AMP env Attack (CC#108)
    - env1 release (3.6 song units) — AMP env Release (CC#111)
    - env2 decay   (4.1 song units) — FILTER env Decay (CC#115)
    - env2 sustain (4.6 song units) — FILTER env Sustain (CC#116)

  MIDI FAN-OUT: every message this script sends, to EITHER port, also goes
  out a third, virtual CoreMIDI port named "sn2 chaos → visuals" (launch
  the visualizer with MIDI=1 -- it matches "visuals"/"sn2"), so it can read
  the identical, perfectly in-sync stream from both synths at once,
  including the channel-16 Program Change used as the clock pulse. If the
  virtual port can't be opened, the script logs that and keeps running
  against the two hardware ports only.

  CRASH-SAFE SHUTDOWN: All Notes Off + centered Pitch Bend on the
  Supernova's channels (1-8) AND the Korg (via korg_out) is guaranteed on
  any exit path, not just a clean 'q'/Ctrl+C:
    - an atexit handler covers normal interpreter shutdown and any
      uncaught exception in the main thread
    - SIGINT and SIGTERM handlers cover Ctrl+C, `kill`, a closed terminal
    - threading.excepthook covers an uncaught exception in ANY background
      thread (note/run/modulator/preset loops, on either synth) -- those
      don't crash the process by themselves, which is what previously let
      the rest of the script keep running for hours with one channel
      silently dead and droning
  The underlying cleanup() is idempotent and thread-safe (holds a lock for
  its entire body, not just a done-flag check) so however many of these
  fire, the actual silencing work runs exactly once, and nothing that
  triggers it can let the process exit before that work finishes -- then
  exits via os._exit() rather than sys.exit(), to sidestep a known
  interpreter-finalization deadlock against the input-reading thread.
  Separately, every note-on has its matching note-off wrapped in
  try/finally, on both synths, so an exception between the two can't leave
  a note stuck even before the broader shutdown cleanup kicks in.

  RECORDING: every message either port sends (via sn2_recorder.py, shared
  with sn2_perpatch_runs.py) is captured at the _emit()/_emit_korg() layer,
  tagged with its logical source (sn2_ch1-8, sn2_global16, korg9), and
  appended in real time to a JSONL event log in ~/Music/sn2-recordings/ --
  recording itself is just a non-blocking queue.put() in the calling
  thread, with the actual file I/O on a separate background thread, so it
  can't add latency to note/CC timing. The log is flushed to disk every
  ~20 events or ~2s of idle time, so a crash loses at most a couple
  seconds of the tail. It's finalized into a type-1 Standard MIDI File
  (one track per source, each named, PPQ 480 @ 120 BPM used only to
  convert timestamps to ticks) automatically after RECORD_DURATION_SECONDS
  (2h; the music keeps playing after that, recording just stops), and also
  on any clean or crash-safe shutdown via the same cleanup() as above. If
  a session ends before finalize() runs, the JSONL log survives on disk
  and can be rendered separately: `python3 sn2_recorder.py <log>.jsonl`.

Ctrl+C (or 'q') to stop — sends All Notes Off, centers Pitch Bend, and finalizes the recording on both synths first.

Install:  pip install python-rtmidi mido
Run:      python3 sn2_chaos8_runs.py
"""
import rtmidi
import time
import math
import random
import threading
import sys
import os
import atexit
import signal
import sn2_recorder

NUM_CHANNELS = 8

# ── korg's own voice, on its own port ───────────────────────────────────────
KORG_CHANNEL = 8   # 0-indexed -> MIDI channel 9, but on korg_out (its own
                    # port), NEVER on midi_out -- there is no "Korg on
                    # channel 9 of the Supernova's port" anywhere here.
KORG_PHASE = math.pi * 0.55   # distinct phase offset, not shared with SN2 channels
KORG_PRESET_DWELL_SECONDS = 5 * 60 + 55   # 5:55, independent of SONG_UNIT_SECONDS

# ── single-note chaos ─────────────────────────────────────────────────────────
NOTE_GAP_MIN, NOTE_GAP_MAX = 0.01, 2.0
NOTE_DUR_MIN, NOTE_DUR_MAX = 0.1, 3.0
PITCH_MIN, PITCH_MAX = 12, 108
VEL_MIN, VEL_MAX = 1, 127

MODWHEEL_CC = 1
MODWHEEL_GAP_MIN, MODWHEEL_GAP_MAX = 3.0, 15.0

# ── runs ──────────────────────────────────────────────────────────────────────
RUN_LENGTHS = [5, 7]
RUN_NOTE_DUR_MIN, RUN_NOTE_DUR_MAX = 0.1, 1.0
RUN_PAUSE_MIN, RUN_PAUSE_MAX = 10.0, 45.0
RUN_PAUSE_BUSY_MIN, RUN_PAUSE_BUSY_MAX = 6.0, 20.0
RUN_ROOT_MIN, RUN_ROOT_MAX = PITCH_MIN + 12, PITCH_MAX - 12
RUN_STEP_CHOICES = [-3, -2, -1, 1, 2, 3]
RUN_REUSE_PROB = 0.6
RUN_MUTATE_PROB = 0.5

# runs can land on any Supernova Part (0-7) or the Korg (sentinel 'korg')
RUN_TARGETS = list(range(NUM_CHANNELS)) + ['korg']

# ── ThumbJam motifs ──────────────────────────────────────────────────────────
# Real melodic phrases extracted from Bill's ThumbJam playing (~9.8 min,
# chromatic, expressive session), represented the same way make_pattern()
# builds a fresh pattern: offsets from the first note. When a run draws from
# these, one random transformation is applied (see transform_motif()) before
# the usual root transpose -- his actual phrasing feeds the generative runs
# on either synth instead of pure randomness.
RUN_THUMBJAM_PROB = 0.5   # chance a fresh (non-reused) run sources from a motif

THUMBJAM_MOTIFS_5 = [
    [0, 4, 2, 0, -1], [0, -17, 1, -14, 2], [0, 0, 1, 20, 1], [0, 20, 13, -17, -16],
    [0, 13, -19, 14, -18], [0, -4, -6, -8, -10], [0, -5, -5, 0, 10], [0, 18, 14, -2, -22],
    [0, 10, 17, 9, 0], [0, -24, 8, -1, -21], [0, -2, 5, 0, 5], [0, 4, -1, 10, -1],
    [0, 2, 5, 12, 5], [0, -5, 3, -2, -3],
]
THUMBJAM_MOTIFS_7 = [
    [0, 4, 2, 0, -1, 3, 4], [0, -15, 1, -16, -1, -18, -2], [0, -22, 4, 5, 24, -19, 6],
    [0, 20, 13, -17, -16, -12, 1], [0, -9, -11, -10, -11, -12, -12], [0, 15, -3, 4, -2, -15, -20],
    [0, 2, -4, -7, -12, 0, 13], [0, -22, 0, -21, -1, -1, -18], [0, 21, 14, 23, -7, -24, 22],
    [0, -8, 0, 0, -8, 0, -1], [0, 7, 2, 0, 0, 1, 12], [0, 1, 7, 0, -2, 8, -2],
    [0, -10, 3, -8, -4, 2, -7], [0, 6, -2, -2, 3, 0, 8],
]

# ── performance cycling ───────────────────────────────────────────────────────
GLOBAL_MIDI_CHANNEL = 16
PERF_BANK_LSB = {'A': 1, 'B': 2, 'C': 3, 'D': 4}
PERFORMANCE_BANK_LETTER = 'C'
PERFORMANCE_NUMBER_MIN, PERFORMANCE_NUMBER_MAX = 0, 99

# ── song unit + modulation systems ────────────────────────────────────────────
SONG_UNIT_SECONDS = 17 * 60 + 17               # 1037s
PERFORMANCE_DWELL_SECONDS = SONG_UNIT_SECONDS

DENSITY_LFO_PERIOD  = SONG_UNIT_SECONDS * 1.0
VELOCITY_LFO_PERIOD = SONG_UNIT_SECONDS * 1.5
ACTIVITY_LFO_PERIOD = SONG_UNIT_SECONDS * 2.0
MODWHEEL_LFO_PERIOD = SONG_UNIT_SECONDS * 0.5
RUN_LFO_PERIOD      = SONG_UNIT_SECONDS * 0.75

DENSITY_GAP_MAX_BUSY, DENSITY_GAP_MAX_SPARSE = NOTE_GAP_MAX, NOTE_GAP_MAX * 3
VELOCITY_SPREAD = 35
ACTIVITY_MIN, ACTIVITY_MAX = 0.15, 1.0
MODWHEEL_DRIFT_RANGE = 25

# ── Korg sound-design modulation (its own front-panel CCs, via korg_out) ────
FILTER1_CUTOFF_CC = 74
FILTER1_RESONANCE_CC = 71
WS_DEPTH_CC = 83
OSC2_MOD_CC = 19

SOUND_DESIGN_GAP_MIN, SOUND_DESIGN_GAP_MAX = 2.0, 6.0

PITCH_BEND_LFO_PERIOD    = SONG_UNIT_SECONDS * 1.25
HARDNESS_LFO_PERIOD      = SONG_UNIT_SECONDS * 1.75
CUTOFF_LFO_PERIOD        = SONG_UNIT_SECONDS * 2.25
RESONANCE_LFO_PERIOD     = SONG_UNIT_SECONDS * 2.75
SYNC_LFO_PERIOD          = SONG_UNIT_SECONDS * 3.25

PITCH_PARAM_PHASE     = 0.0
HARDNESS_PARAM_PHASE  = math.pi * 0.4
CUTOFF_PARAM_PHASE    = math.pi * 0.8
RESONANCE_PARAM_PHASE = math.pi * 1.2
SYNC_PARAM_PHASE      = math.pi * 1.6

PITCH_BEND_RANGE = 1200
HARDNESS_MIN, HARDNESS_MAX = 0, 110
CUTOFF_MIN, CUTOFF_MAX = 24, 127
RESONANCE_MIN, RESONANCE_MAX = 0, 110
SYNC_CC_MIN, SYNC_CC_MAX = 0, 127

# ── Supernova II sound-design modulation (its own real CCs, via midi_out) ───
# Real CC numbers from the Supernova II's own MIDI Controller Allocation
# chart (Bill's manual), NOT the microKORG XL's -- separate, additional
# system from korg_sound_design_loop() above, which stays Korg-only. Runs
# on all 8 Supernova Parts (ch 1-8), each with its own copy on its own
# channel phase, same as every other per-channel modulator.
SN2_CUTOFF_CC       = 105  # Filter Cutoff Freq
SN2_RESONANCE_CC    = 106  # Filter Resonance
SN2_OVERDRIVE_CC    = 104  # Filter Overdrive
SN2_ENV_AMOUNT_CC   = 107  # Filter Freq Env 2 (amount)
SN2_ENV1_ATTACK_CC  = 108  # Env 1 (AMP env) Attack
SN2_ENV1_RELEASE_CC = 111  # Env 1 (AMP env) Release
SN2_ENV2_DECAY_CC   = 115  # Env 2 (FILTER env) Decay
SN2_ENV2_SUSTAIN_CC = 116  # Env 2 (FILTER env) Sustain

# Bill specifically loves how envelope changes here morph the Korg's vocoder
# output (fed by the Supernova's audio), so the three envelope drifts --
# Env1 Attack/Release and Env2 Decay/Sustain, plus the Env2 amount -- are
# the point of this system, not an afterthought.
SN2_CUTOFF_LFO_PERIOD       = SONG_UNIT_SECONDS * 1.1
SN2_RESONANCE_LFO_PERIOD    = SONG_UNIT_SECONDS * 1.6
SN2_OVERDRIVE_LFO_PERIOD    = SONG_UNIT_SECONDS * 2.1
SN2_ENV_AMOUNT_LFO_PERIOD   = SONG_UNIT_SECONDS * 2.6
SN2_ENV1_ATTACK_LFO_PERIOD  = SONG_UNIT_SECONDS * 3.1
SN2_ENV1_RELEASE_LFO_PERIOD = SONG_UNIT_SECONDS * 3.6
SN2_ENV2_DECAY_LFO_PERIOD   = SONG_UNIT_SECONDS * 4.1
SN2_ENV2_SUSTAIN_LFO_PERIOD = SONG_UNIT_SECONDS * 4.6

# fixed offsets (radians), evenly spread over the circle (2*pi/8 apart) so
# none of the 8 parameters peak together, on top of each channel's own phase
SN2_CUTOFF_PARAM_PHASE       = math.pi * 0.25 * 0
SN2_RESONANCE_PARAM_PHASE    = math.pi * 0.25 * 1
SN2_OVERDRIVE_PARAM_PHASE    = math.pi * 0.25 * 2
SN2_ENV_AMOUNT_PARAM_PHASE   = math.pi * 0.25 * 3
SN2_ENV1_ATTACK_PARAM_PHASE  = math.pi * 0.25 * 4
SN2_ENV1_RELEASE_PARAM_PHASE = math.pi * 0.25 * 5
SN2_ENV2_DECAY_PARAM_PHASE   = math.pi * 0.25 * 6
SN2_ENV2_SUSTAIN_PARAM_PHASE = math.pi * 0.25 * 7

SN2_CUTOFF_MIN, SN2_CUTOFF_MAX = 30, 127             # never fully closes
SN2_RESONANCE_MIN, SN2_RESONANCE_MAX = 0, 110
SN2_OVERDRIVE_MIN, SN2_OVERDRIVE_MAX = 0, 90
SN2_ENV_AMOUNT_MIN, SN2_ENV_AMOUNT_MAX = 40, 90      # modest range -- keeps the filter env expressive without extremes
SN2_ENV1_ATTACK_MIN, SN2_ENV1_ATTACK_MAX = 10, 90    # moderate -- stays responsive, never glacial or instant
SN2_ENV1_RELEASE_MIN, SN2_ENV1_RELEASE_MAX = 10, 90
SN2_ENV2_DECAY_MIN, SN2_ENV2_DECAY_MAX = 20, 100
SN2_ENV2_SUSTAIN_MIN, SN2_ENV2_SUSTAIN_MAX = 20, 100

# ── recording ────────────────────────────────────────────────────────────────
RECORD_DURATION_SECONDS = 2 * 60 * 60   # 2h -- auto-finalize the .mid at this mark; music keeps playing after

running = True
midi_out = None
midi_fanout = None                       # virtual port the visualizer reads
korg_out = None                          # korg's own sound engine, separate from the DIN thru to the SN2
recorder = None                          # sn2_recorder.Recorder instance, set in main()
channel_locked = [False] * NUM_CHANNELS
korg_run_locked = False                  # same idea as channel_locked, for the Korg's own voice
lock = threading.Lock()

song_start = None
CHANNEL_PHASE = [i * (2 * math.pi / NUM_CHANNELS) for i in range(NUM_CHANNELS)]


def select_port():
    global midi_out
    midi_out = rtmidi.MidiOut()
    ports = midi_out.get_ports()
    if not ports:
        print("No MIDI output ports found. Check Audio MIDI Setup.")
        sys.exit(1)
    for i, name in enumerate(ports):
        if any(k in name.lower() for k in ['korg', 'microkey', 'microkorg', 'supernova']):
            print(f"Auto-selected: [{i}] {name}")
            midi_out.open_port(i)
            return
    print("\nMIDI output ports:")
    for i, name in enumerate(ports):
        print(f"  [{i}] {name}")
    midi_out.open_port(int(input("Select port number: ").strip()))


def select_korg_port():
    """The korg's OWN sound engine is a separate USB-MIDI port from the DIN
    thru used for the SN2 -- 'microKORG XL SOUND' rather than 'MIDI OUT'."""
    global korg_out
    candidate = rtmidi.MidiOut()
    ports = candidate.get_ports()
    for i, name in enumerate(ports):
        if 'sound' in name.lower() and 'korg' in name.lower():
            print(f"Korg sound engine on: [{i}] {name} (channel {KORG_CHANNEL + 1})")
            candidate.open_port(i)
            korg_out = candidate
            return
    print("Could not find the korg's own 'SOUND' port; channel 9 voice disabled.")
    korg_out = None


def open_fanout():
    """Open a virtual MIDI port the ButterchurnVisualizer can read (host-side)."""
    global midi_fanout
    try:
        midi_fanout = rtmidi.MidiOut()
        midi_fanout.open_virtual_port("sn2 chaos → visuals")
        print("Fan-out virtual port open: 'sn2 chaos → visuals' (launch visualizer with MIDI=1)")
    except Exception as e:
        print(f"Fan-out virtual port unavailable ({e}); continuing without it.")
        midi_fanout = None


def _recorder_source_for_sn2(msg):
    ch = msg[0] & 0x0F
    if ch == GLOBAL_MIDI_CHANNEL - 1:
        return "sn2_global16"
    return f"sn2_ch{ch + 1}"


def _emit(msg):
    """Send to the Supernova AND the visuals fan-out so both read the identical stream."""
    midi_out.send_message(msg)
    if midi_fanout is not None:
        midi_fanout.send_message(msg)
    if recorder is not None:
        try:
            recorder.record(_recorder_source_for_sn2(msg), msg)
        except Exception:
            pass


def _emit_korg(msg):
    """Send to the korg's own sound engine AND the visuals fan-out."""
    if korg_out is not None:
        korg_out.send_message(msg)
        if recorder is not None:
            try:
                recorder.record("korg9", msg)
            except Exception:
                pass
    if midi_fanout is not None:
        midi_fanout.send_message(msg)


def send_note_on(ch, note, vel):
    _emit([0x90 | ch, note, vel])


def send_note_off(ch, note):
    _emit([0x80 | ch, note, 0])


def send_cc(ch, cc, val):
    _emit([0xB0 | ch, cc, val])


def _pitch_bend_bytes(value):
    value = max(-8192, min(8191, value))
    raw = value + 8192
    return raw & 0x7F, (raw >> 7) & 0x7F


def send_pitch_bend(ch, value):
    """value: -8192..+8191, 0 = center."""
    lsb, msb = _pitch_bend_bytes(value)
    _emit([0xE0 | ch, lsb, msb])


def send_korg_pitch_bend(value):
    lsb, msb = _pitch_bend_bytes(value)
    _emit_korg([0xE0 | KORG_CHANNEL, lsb, msb])


def all_notes_off():
    for ch in range(NUM_CHANNELS):
        _emit([0xB0 | ch, 123, 0])
    _emit_korg([0xB0 | KORG_CHANNEL, 123, 0])


def center_pitch_bend_all():
    for ch in range(NUM_CHANNELS):
        send_pitch_bend(ch, 0)
    send_korg_pitch_bend(0)


# ── crash-safe shutdown ─────────────────────────────────────────────────────
# cleanup() is the single source of truth for silencing everything, on BOTH
# ports. It's idempotent (safe to call more than once) and gets wired up
# from three independent triggers so an abnormal exit can't skip it:
#   - atexit: any interpreter shutdown, including an uncaught exception in
#     the main thread
#   - SIGINT/SIGTERM handlers: Ctrl+C, `kill`, a terminal closing, etc.
#   - threading.excepthook: an uncaught exception in ANY background thread,
#     on either synth's loops. Those don't crash the process or trigger
#     atexit by themselves -- the main thread would otherwise keep running
#     forever with that one thread silently dead, which is what left notes
#     droning for 1.5 hours previously.
_cleanup_lock = threading.Lock()
_cleanup_done = False


def cleanup():
    # Holds the lock for the ENTIRE body, not just the done-flag check. If
    # cleanup() is triggered from a background (daemon) thread -- e.g. via
    # _thread_excepthook -- and the main thread reaches its own cleanup()
    # call around the same time, the main thread must BLOCK here until the
    # first call's work is actually finished, not just started. Otherwise
    # the main thread could see _cleanup_done, return immediately, let
    # main() (and the whole process) exit, and the interpreter would kill
    # the still-working daemon thread mid-cleanup -- silently dropping the
    # rest of the silencing messages.
    global running, _cleanup_done
    with _cleanup_lock:
        if _cleanup_done:
            return
        _cleanup_done = True
        running = False
        if midi_out is None:
            return
        try:
            time.sleep(0.05)
            all_notes_off()
            center_pitch_bend_all()
            time.sleep(0.15)
        except Exception as e:
            print(f"\nCleanup warning: could not fully silence outputs ({e})")
        try:
            midi_out.close_port()
        except Exception:
            pass
        if midi_fanout is not None:
            try:
                midi_fanout.close_port()
            except Exception:
                pass
        if korg_out is not None:
            try:
                korg_out.close_port()
            except Exception:
                pass
        if recorder is not None:
            try:
                recorder.finalize()
            except Exception as e:
                print(f"\nCould not finalize recording ({e})")
        print("\nStopped. All notes off.")


def _handle_signal(signum, frame):
    cleanup()
    # os._exit(), not sys.exit(): sys.exit() raises SystemExit and drives
    # Python's normal interpreter finalization, which tries to deal with
    # every daemon thread -- including input_loop's, which is typically
    # blocked inside a native input() call on stdin. Finalizing while that
    # thread holds stdin's buffer lock is a known deadlock/crash ("could not
    # acquire lock for <_io.BufferedReader...> at interpreter shutdown").
    # cleanup() has already fully run at this point, so there's nothing left
    # to lose by exiting immediately without the normal teardown.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


def _thread_excepthook(args):
    print(f"\nUnhandled exception in thread {args.thread.name}: {args.exc_value}")
    cleanup()
    # Same reasoning as _handle_signal: terminate immediately rather than
    # let the main thread notice `running` went False and return from
    # main() normally, which would hit the same input_loop-blocked-on-stdin
    # finalization hazard. cleanup() already fully ran above.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


def send_performance_change(bank_letter, number):
    ch = GLOBAL_MIDI_CHANNEL - 1
    lsb = PERF_BANK_LSB[bank_letter]
    _emit([0xB0 | ch, 0, 0])       # Bank Select MSB = 0
    _emit([0xB0 | ch, 32, lsb])    # Bank Select LSB -> Perf bank
    _emit([0xC0 | ch, number])     # Program Change (the ch-16 sync anchor)
    print(f"\n→ Performance {bank_letter}{number:03d} (ch {GLOBAL_MIDI_CHANNEL})")


def send_korg_program_change(program):
    """Bank Select is fixed at MSB=0, LSB=0 on the microKORG XL -- a single
    Program Change 0-127 addresses the whole A11-B88 range directly."""
    _emit_korg([0xB0 | KORG_CHANNEL, 0, 0])     # Bank Select MSB = 0
    _emit_korg([0xB0 | KORG_CHANNEL, 32, 0])    # Bank Select LSB = 0
    _emit_korg([0xC0 | KORG_CHANNEL, program])  # Program Change
    print(f"\n→ Korg preset {program:03d} (ch {KORG_CHANNEL + 1})")


def korg_preset_loop():
    while running:
        if korg_out is not None:
            # Full 0-127 range, no exclusions: Bill feeds live audio into
            # the Korg's vocoder input, so the mic-only vocoder programs
            # DO produce sound on his rig -- nothing to skip here.
            program = random.randint(0, 127)
            send_korg_program_change(program)
        elapsed = 0.0
        while running and elapsed < KORG_PRESET_DWELL_SECONDS:
            time.sleep(1.0)
            elapsed += 1.0


def performance_loop():
    while running:
        number = random.randint(PERFORMANCE_NUMBER_MIN, PERFORMANCE_NUMBER_MAX)
        send_performance_change(PERFORMANCE_BANK_LETTER, number)
        elapsed = 0.0
        while running and elapsed < PERFORMANCE_DWELL_SECONDS:
            time.sleep(1.0)
            elapsed += 1.0


def elapsed_song_time():
    return time.time() - song_start


def lfo01(period, phase=0.0):
    t = elapsed_song_time()
    return (math.sin(2 * math.pi * t / period + phase) + 1) / 2


def lerp(a, b, x):
    return a + (b - a) * x


def note_loop(ch):
    phase = CHANNEL_PHASE[ch]
    while running:
        with lock:
            locked = channel_locked[ch]
        if locked:
            time.sleep(0.05)
            continue

        activity = lerp(ACTIVITY_MIN, ACTIVITY_MAX, lfo01(ACTIVITY_LFO_PERIOD, phase))
        if random.random() > activity:
            time.sleep(0.1)
            continue

        density = lfo01(DENSITY_LFO_PERIOD, phase)
        gap_max = lerp(DENSITY_GAP_MAX_SPARSE, DENSITY_GAP_MAX_BUSY, density)

        vel_center = lerp(VEL_MIN, VEL_MAX, lfo01(VELOCITY_LFO_PERIOD, phase))
        vel_lo = max(VEL_MIN, int(vel_center - VELOCITY_SPREAD))
        vel_hi = min(VEL_MAX, int(vel_center + VELOCITY_SPREAD))

        note = random.randint(PITCH_MIN, PITCH_MAX)
        vel = random.randint(vel_lo, vel_hi)
        dur = random.uniform(NOTE_DUR_MIN, NOTE_DUR_MAX)
        send_note_on(ch, note, vel)

        try:
            off_timer = threading.Timer(dur, send_note_off, args=[ch, note])
            off_timer.daemon = True
            off_timer.start()
        except Exception:
            # couldn't schedule the note-off -- send it immediately rather
            # than leave the note stuck on
            send_note_off(ch, note)
            raise

        gap = random.uniform(NOTE_GAP_MIN, gap_max)
        time.sleep(gap)


def korg_note_loop():
    """Same activity/density/velocity LFO treatment as the SN2 channels, but
    on the korg's own sound engine instead of the SN2 -- and pausable by a
    run hosted on the Korg (korg_run_locked), just like a Supernova Part is
    pausable by channel_locked."""
    phase = KORG_PHASE
    while running:
        if korg_out is None:
            time.sleep(1.0)
            continue

        with lock:
            locked = korg_run_locked
        if locked:
            time.sleep(0.05)
            continue

        activity = lerp(ACTIVITY_MIN, ACTIVITY_MAX, lfo01(ACTIVITY_LFO_PERIOD, phase))
        if random.random() > activity:
            time.sleep(0.1)
            continue

        density = lfo01(DENSITY_LFO_PERIOD, phase)
        gap_max = lerp(DENSITY_GAP_MAX_SPARSE, DENSITY_GAP_MAX_BUSY, density)

        vel_center = lerp(VEL_MIN, VEL_MAX, lfo01(VELOCITY_LFO_PERIOD, phase))
        vel_lo = max(VEL_MIN, int(vel_center - VELOCITY_SPREAD))
        vel_hi = min(VEL_MAX, int(vel_center + VELOCITY_SPREAD))

        note = random.randint(PITCH_MIN, PITCH_MAX)
        vel = random.randint(vel_lo, vel_hi)
        dur = random.uniform(NOTE_DUR_MIN, NOTE_DUR_MAX)
        _emit_korg([0x90 | KORG_CHANNEL, note, vel])

        try:
            off_timer = threading.Timer(dur, lambda n=note: _emit_korg([0x80 | KORG_CHANNEL, n, 0]))
            off_timer.daemon = True
            off_timer.start()
        except Exception:
            _emit_korg([0x80 | KORG_CHANNEL, note, 0])
            raise

        gap = random.uniform(NOTE_GAP_MIN, gap_max)
        time.sleep(gap)


def modwheel_loop(ch):
    phase = CHANNEL_PHASE[ch]
    while running:
        gap = random.uniform(MODWHEEL_GAP_MIN, MODWHEEL_GAP_MAX)
        time.sleep(gap)
        if not running:
            break
        center = lerp(0, 127, lfo01(MODWHEEL_LFO_PERIOD, phase))
        val = int(center + random.uniform(-MODWHEEL_DRIFT_RANGE, MODWHEEL_DRIFT_RANGE))
        val = max(0, min(127, val))
        send_cc(ch, MODWHEEL_CC, val)


def korg_sound_design_loop():
    """Drifts pitch, hardness, filter cutoff/resonance, and osc sync on the
    Korg's own sound engine (korg_out/KORG_CHANNEL) -- these are the
    microKORG XL's own front-panel-knob CCs, meaningless on the Supernova.
    Separate from supernova_sound_design_loop() below."""
    phase = KORG_PHASE
    while running:
        gap = random.uniform(SOUND_DESIGN_GAP_MIN, SOUND_DESIGN_GAP_MAX)
        time.sleep(gap)
        if not running:
            break
        if korg_out is None:
            continue

        bend_x = lfo01(PITCH_BEND_LFO_PERIOD, phase + PITCH_PARAM_PHASE) * 2 - 1
        send_korg_pitch_bend(int(bend_x * PITCH_BEND_RANGE))

        hardness = int(lerp(HARDNESS_MIN, HARDNESS_MAX, lfo01(HARDNESS_LFO_PERIOD, phase + HARDNESS_PARAM_PHASE)))
        _emit_korg([0xB0 | KORG_CHANNEL, WS_DEPTH_CC, hardness])

        cutoff = int(lerp(CUTOFF_MIN, CUTOFF_MAX, lfo01(CUTOFF_LFO_PERIOD, phase + CUTOFF_PARAM_PHASE)))
        _emit_korg([0xB0 | KORG_CHANNEL, FILTER1_CUTOFF_CC, cutoff])

        resonance = int(lerp(RESONANCE_MIN, RESONANCE_MAX, lfo01(RESONANCE_LFO_PERIOD, phase + RESONANCE_PARAM_PHASE)))
        _emit_korg([0xB0 | KORG_CHANNEL, FILTER1_RESONANCE_CC, resonance])

        sync = int(lerp(SYNC_CC_MIN, SYNC_CC_MAX, lfo01(SYNC_LFO_PERIOD, phase + SYNC_PARAM_PHASE)))
        _emit_korg([0xB0 | KORG_CHANNEL, OSC2_MOD_CC, sync])


def supernova_sound_design_loop(ch):
    """Drifts Supernova II filter/envelope character, per Part (ch 1-8).
    Separate from korg_sound_design_loop() above -- these are the
    Supernova's own Controller Allocation CC numbers, not the Korg's, and
    only ever go out midi_out, never korg_out."""
    phase = CHANNEL_PHASE[ch]
    while running:
        gap = random.uniform(SOUND_DESIGN_GAP_MIN, SOUND_DESIGN_GAP_MAX)
        time.sleep(gap)
        if not running:
            break

        cutoff = int(lerp(SN2_CUTOFF_MIN, SN2_CUTOFF_MAX, lfo01(SN2_CUTOFF_LFO_PERIOD, phase + SN2_CUTOFF_PARAM_PHASE)))
        send_cc(ch, SN2_CUTOFF_CC, cutoff)

        resonance = int(lerp(SN2_RESONANCE_MIN, SN2_RESONANCE_MAX, lfo01(SN2_RESONANCE_LFO_PERIOD, phase + SN2_RESONANCE_PARAM_PHASE)))
        send_cc(ch, SN2_RESONANCE_CC, resonance)

        overdrive = int(lerp(SN2_OVERDRIVE_MIN, SN2_OVERDRIVE_MAX, lfo01(SN2_OVERDRIVE_LFO_PERIOD, phase + SN2_OVERDRIVE_PARAM_PHASE)))
        send_cc(ch, SN2_OVERDRIVE_CC, overdrive)

        env_amount = int(lerp(SN2_ENV_AMOUNT_MIN, SN2_ENV_AMOUNT_MAX, lfo01(SN2_ENV_AMOUNT_LFO_PERIOD, phase + SN2_ENV_AMOUNT_PARAM_PHASE)))
        send_cc(ch, SN2_ENV_AMOUNT_CC, env_amount)

        env1_attack = int(lerp(SN2_ENV1_ATTACK_MIN, SN2_ENV1_ATTACK_MAX, lfo01(SN2_ENV1_ATTACK_LFO_PERIOD, phase + SN2_ENV1_ATTACK_PARAM_PHASE)))
        send_cc(ch, SN2_ENV1_ATTACK_CC, env1_attack)

        env1_release = int(lerp(SN2_ENV1_RELEASE_MIN, SN2_ENV1_RELEASE_MAX, lfo01(SN2_ENV1_RELEASE_LFO_PERIOD, phase + SN2_ENV1_RELEASE_PARAM_PHASE)))
        send_cc(ch, SN2_ENV1_RELEASE_CC, env1_release)

        env2_decay = int(lerp(SN2_ENV2_DECAY_MIN, SN2_ENV2_DECAY_MAX, lfo01(SN2_ENV2_DECAY_LFO_PERIOD, phase + SN2_ENV2_DECAY_PARAM_PHASE)))
        send_cc(ch, SN2_ENV2_DECAY_CC, env2_decay)

        env2_sustain = int(lerp(SN2_ENV2_SUSTAIN_MIN, SN2_ENV2_SUSTAIN_MAX, lfo01(SN2_ENV2_SUSTAIN_LFO_PERIOD, phase + SN2_ENV2_SUSTAIN_PARAM_PHASE)))
        send_cc(ch, SN2_ENV2_SUSTAIN_CC, env2_sustain)


def make_pattern(length):
    steps = []
    cur = 0
    for _ in range(length):
        steps.append(cur)
        cur += random.choice(RUN_STEP_CHOICES)
    return steps


def mutate_pattern(pattern):
    p = pattern[:]
    idx = random.randrange(len(p))
    p[idx] += random.choice([-2, -1, 1, 2])
    return p


def invert_pattern(pattern):
    return [-offset for offset in pattern]


def retrograde_pattern(pattern):
    reversed_p = list(reversed(pattern))
    base = reversed_p[0]
    return [v - base for v in reversed_p]


def stretch_pattern(pattern, factor):
    return [int(round(offset * factor)) for offset in pattern]


def transform_motif(motif):
    """Applies one random transformation to a ThumbJam motif before it's used
    as a run pattern -- his phrase, put through a different lens each time."""
    transform = random.choice(['identity', 'invert', 'retrograde', 'mutate', 'stretch'])
    if transform == 'identity':
        return motif[:]
    if transform == 'invert':
        return invert_pattern(motif)
    if transform == 'retrograde':
        return retrograde_pattern(motif)
    if transform == 'mutate':
        return mutate_pattern(motif)
    factor = random.choice([2, 0.5])
    return stretch_pattern(motif, factor)


def run_loop():
    global korg_run_locked
    last_pattern = None
    while running:
        target = random.choice(RUN_TARGETS)
        is_korg = (target == 'korg')
        length = random.choice(RUN_LENGTHS)

        if last_pattern is not None and len(last_pattern) == length and random.random() < RUN_REUSE_PROB:
            # reusing/mutating whatever the last pattern was -- if that
            # happened to be a transformed ThumbJam motif, this naturally
            # reuses a transformed motif too
            pattern = mutate_pattern(last_pattern) if random.random() < RUN_MUTATE_PROB else last_pattern[:]
        elif random.random() < RUN_THUMBJAM_PROB:
            motifs = THUMBJAM_MOTIFS_5 if length == 5 else THUMBJAM_MOTIFS_7
            pattern = transform_motif(random.choice(motifs))
        else:
            pattern = make_pattern(length)
        last_pattern = pattern

        root = random.randint(RUN_ROOT_MIN, RUN_ROOT_MAX)
        pitches = [max(0, min(127, root + offset)) for offset in pattern]

        if is_korg:
            with lock:
                korg_run_locked = True
        else:
            ch = target
            with lock:
                channel_locked[ch] = True
        try:
            for pitch in pitches:
                if not running:
                    break
                vel = random.randint(VEL_MIN, VEL_MAX)
                dur = random.uniform(RUN_NOTE_DUR_MIN, RUN_NOTE_DUR_MAX)
                if is_korg:
                    _emit_korg([0x90 | KORG_CHANNEL, pitch, vel])
                else:
                    send_note_on(ch, pitch, vel)
                try:
                    time.sleep(dur)
                finally:
                    # guarantees a matching note-off even if something above
                    # raises mid-sleep, instead of leaving the note stuck on
                    if is_korg:
                        _emit_korg([0x80 | KORG_CHANNEL, pitch, 0])
                    else:
                        send_note_off(ch, pitch)
        finally:
            if is_korg:
                with lock:
                    korg_run_locked = False
            else:
                with lock:
                    channel_locked[ch] = False

        busy = lfo01(RUN_LFO_PERIOD)
        pause_min = lerp(RUN_PAUSE_MIN, RUN_PAUSE_BUSY_MIN, busy)
        pause_max = lerp(RUN_PAUSE_MAX, RUN_PAUSE_BUSY_MAX, busy)
        pause = random.uniform(pause_min, pause_max)
        chunks = max(1, int(pause / 0.1))
        for _ in range(chunks):
            if not running:
                break
            time.sleep(0.1)


def input_loop():
    global running
    while running:
        try:
            cmd = input().strip()
        except EOFError:
            return
        except KeyboardInterrupt:
            running = False
            return
        if cmd == 'q':
            running = False
            return


def main():
    global running, song_start, recorder
    select_port()
    select_korg_port()
    open_fanout()
    recorder = sn2_recorder.Recorder(name_prefix="sn2chaos8")
    recorder.start_auto_finalize_timer(RECORD_DURATION_SECONDS)
    atexit.register(cleanup)
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    threading.excepthook = _thread_excepthook
    song_start = time.time()

    dwell_min = PERFORMANCE_DWELL_SECONDS // 60
    dwell_sec = PERFORMANCE_DWELL_SECONDS % 60
    korg_dwell_min = KORG_PRESET_DWELL_SECONDS // 60
    korg_dwell_sec = KORG_PRESET_DWELL_SECONDS % 60
    print(f"\nRunning {NUM_CHANNELS} Supernova channels of random notes + mod wheel, "
          f"plus the Korg's own voice, plus one wandering run that can land on either.")
    print(f"Cycling random Performances {PERFORMANCE_BANK_LETTER}{PERFORMANCE_NUMBER_MIN:03d}-"
          f"{PERFORMANCE_BANK_LETTER}{PERFORMANCE_NUMBER_MAX:03d}, {dwell_min}:{dwell_sec:02d} each (ch {GLOBAL_MIDI_CHANNEL}).")
    print(f"Cycling random Korg presets 000-127 (full range), {korg_dwell_min}:{korg_dwell_sec:02d} each, "
          f"on its own port.")
    print("Density, dynamics, presence, mod wheel drift, and run frequency all slowly")
    print("breathe in cycles based on that same 17:17 song unit, on both synths.")
    print("Korg pitch/hardness/filter/sync drift on its own port; Supernova filter/")
    print("overdrive/envelope drift on each Part (ch 1-8) via midi_out.")
    record_h = RECORD_DURATION_SECONDS // 3600
    print(f"Recording everything to {sn2_recorder.RECORDINGS_DIR}/ -- auto-saves as a .mid after {record_h}h")
    print("(music keeps playing after that); also saved on 'q'/Ctrl+C.")
    print("'q' + enter, or Ctrl+C, to stop.\n")

    threads = []
    for ch in range(NUM_CHANNELS):
        t1 = threading.Thread(target=note_loop, args=[ch], daemon=True)
        t2 = threading.Thread(target=modwheel_loop, args=[ch], daemon=True)
        t3 = threading.Thread(target=supernova_sound_design_loop, args=[ch], daemon=True)
        threads += [t1, t2, t3]
        t1.start()
        t2.start()
        t3.start()

    threading.Thread(target=run_loop, daemon=True).start()
    threading.Thread(target=performance_loop, daemon=True).start()
    threading.Thread(target=korg_note_loop, daemon=True).start()
    threading.Thread(target=korg_preset_loop, daemon=True).start()
    threading.Thread(target=korg_sound_design_loop, daemon=True).start()
    threading.Thread(target=input_loop, daemon=True).start()

    try:
        while running:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        cleanup()


if __name__ == '__main__':
    main()
