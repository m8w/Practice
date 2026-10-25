#!/usr/bin/env python3
"""
sn2_gen.py — Generative MIDI controller for Novation Supernova II
xik6 / built with Claude

Modes:
  drift   — slow CC random walks (filter, LFO, reverb, env)
  scatter — probabilistic note events, Cage-style
  drone   — sustained harmonic clusters with CC mutation
  pulse   — indeterminate rhythmic gestures
  silence — Oliveros mode, long gaps + rare single gestures

Future hook: fractal stream audio-reactive input
→ search for "# FRACTAL_HOOK" to see integration points

Install:  pip install python-rtmidi
Run:      python3 sn2_gen.py
"""

import rtmidi
import time
import random
import threading
import sys

# ─── CONFIG ───────────────────────────────────────────────────────────────────

MIDI_CHANNEL = 0        # 0-indexed internally; = channel 1 on the SN2

# Supernova II CC map (from manual OS2.0)
CC_MAP = {
    'mod_wheel':      1,
    'pan':            10,
    'lfo1_speed':     16,
    'lfo1_delay':     17,
    'lfo2_speed':     18,
    'lfo2_delay':     19,
    'osc1_pw':        22,
    'osc2_pw':        27,
    'osc3_pw':        42,
    'filter_lfo1':    60,
    'filter_cutoff':  105,
    'filter_res':     106,
    'filter_env2':    107,
    'env1_attack':    108,
    'env1_decay':     109,
    'env1_sustain':   110,
    'env1_release':   111,
    'chorus_speed':   87,
    'chorus_depth':   88,
    'chorus_fb':      89,
    'distortion':     90,
    'reverb_send':    91,
    'delay_send':     92,
    'chorus_send':    93,
    'reverb_decay':   95,
    'delay_time':     101,
    'delay_fb':       102,
    'filter_od':      104,
}

# Per-CC safe operating ranges [lo, hi]
CC_RANGES = {
    'mod_wheel':      [0,   90],
    'pan':            [30, 100],
    'lfo1_speed':     [0,  110],
    'lfo1_delay':     [0,   80],
    'lfo2_speed':     [0,  110],
    'lfo2_delay':     [0,   80],
    'osc1_pw':        [10,  118],
    'osc2_pw':        [10,  118],
    'osc3_pw':        [10,  118],
    'filter_lfo1':    [0,   80],
    'filter_cutoff':  [20,  115],
    'filter_res':     [0,   90],
    'filter_env2':    [0,   80],
    'env1_attack':    [0,   90],
    'env1_decay':     [10,  110],
    'env1_sustain':   [40,  127],
    'env1_release':   [20,  110],
    'chorus_speed':   [0,   90],
    'chorus_depth':   [0,   80],
    'chorus_fb':      [0,   70],
    'distortion':     [0,   60],
    'reverb_send':    [10,  90],
    'delay_send':     [0,   70],
    'chorus_send':    [0,   70],
    'reverb_decay':   [20,  110],
    'delay_time':     [0,   110],
    'delay_fb':       [0,   80],
    'filter_od':      [0,   60],
}

# ─── GENERATOR ────────────────────────────────────────────────────────────────

class SN2Gen:
    def __init__(self, midi_out):
        self.midi = midi_out
        self.mode = 'drift'
        self.running = True
        self.paused = False
        self.density = 0.5      # 0.0 – 1.0
        self.lock = threading.Lock()
        self.active_notes = set()

        # CC state — start at neutral-ish values
        self.cc_state = {
            'mod_wheel':      0,
            'pan':            64,
            'lfo1_speed':     40,
            'lfo1_delay':     0,
            'lfo2_speed':     40,
            'lfo2_delay':     0,
            'osc1_pw':        64,
            'osc2_pw':        64,
            'osc3_pw':        64,
            'filter_lfo1':    0,
            'filter_cutoff':  80,
            'filter_res':     30,
            'filter_env2':    40,
            'env1_attack':    20,
            'env1_decay':     60,
            'env1_sustain':   80,
            'env1_release':   60,
            'chorus_speed':   40,
            'chorus_depth':   30,
            'chorus_fb':      20,
            'distortion':     0,
            'reverb_send':    40,
            'delay_send':     20,
            'chorus_send':    20,
            'reverb_decay':   60,
            'delay_time':     60,
            'delay_fb':       40,
            'filter_od':      0,
        }

    # ── MIDI primitives ───────────────────────────────────────────────────────

    def send_cc(self, name, value):
        lo, hi = CC_RANGES[name]
        value = max(lo, min(hi, int(value)))
        self.midi.send_message([0xB0 | MIDI_CHANNEL, CC_MAP[name], value])
        self.cc_state[name] = value

    def send_note_on(self, note, velocity):
        note = max(0, min(127, int(note)))
        velocity = max(1, min(127, int(velocity)))
        self.midi.send_message([0x90 | MIDI_CHANNEL, note, velocity])
        self.active_notes.add(note)

    def send_note_off(self, note):
        note = max(0, min(127, int(note)))
        self.midi.send_message([0x80 | MIDI_CHANNEL, note, 0])
        self.active_notes.discard(note)

    def all_notes_off(self):
        for note in list(self.active_notes):
            self.send_note_off(note)
        self.midi.send_message([0xB0 | MIDI_CHANNEL, 123, 0])

    def note_later(self, note, duration):
        """Schedule a note-off in a daemon timer thread."""
        t = threading.Timer(duration, self.send_note_off, args=[note])
        t.daemon = True
        t.start()

    # ── FRACTAL_HOOK ──────────────────────────────────────────────────────────
    # Future: flame_pipeline feeds amplitude/spectral energy here.
    # Replace self.density with a live value from the audio analysis thread.
    # Example: gen.density = audio_energy_normalized  (0.0 – 1.0)
    # Each mode already reads self.density per step — no other changes needed.

    # ── MODES ─────────────────────────────────────────────────────────────────

    def drift_step(self):
        """Slow Gaussian random walk across CC parameters."""
        # Drift targets — core timbral CCs
        drift_targets = [
            'filter_cutoff', 'filter_res', 'lfo1_speed', 'lfo2_speed',
            'reverb_send', 'reverb_decay', 'delay_send', 'filter_lfo1',
            'env1_attack', 'env1_release', 'mod_wheel', 'distortion',
            'osc1_pw', 'chorus_speed',
        ]
        n_targets = random.randint(1, max(1, int(3 + self.density * 3)))
        targets = random.sample(drift_targets, k=n_targets)

        for name in targets:
            step_sigma = 4 + self.density * 10
            step = random.gauss(0, step_sigma)
            self.send_cc(name, self.cc_state[name] + step)

        # Slower = more meditative; faster density = shorter gaps
        return random.uniform(0.2, 2.5 - self.density * 1.8)

    def scatter_step(self):
        """Probabilistic note events — Cage / indeterminacy style."""
        # Scale selection by density
        if self.density < 0.35:
            scale, root = [0, 2, 4, 7, 9], 48        # pentatonic C3
        elif self.density < 0.65:
            scale, root = [0, 2, 4, 6, 8, 10], 36    # whole tone C2
        else:
            scale, root = list(range(12)), 36          # chromatic

        note_prob = 0.25 + self.density * 0.6
        if random.random() < note_prob:
            octave = random.choice([0, 12, 24]) + random.choice([0, 12])
            pitch = root + random.choice(scale) + octave
            pitch = max(24, min(96, pitch))
            vel = int(random.triangular(15, 127, 50 + self.density * 40))
            dur = random.uniform(0.1, 2.0 + self.density * 3.0)
            self.send_note_on(pitch, vel)
            self.note_later(pitch, dur)

        # Occasional CC scatter
        if random.random() < 0.35:
            name = random.choice(['filter_cutoff', 'filter_res', 'lfo1_speed',
                                  'reverb_send', 'delay_send', 'mod_wheel'])
            self.send_cc(name, self.cc_state[name] + random.gauss(0, 12))

        gap = random.expovariate(1.0 / (0.4 + self.density * 1.5))
        return max(0.04, min(4.0, gap))

    def drone_step(self):
        """Sustained harmonic cluster with slow CC undercurrent."""
        # Build cluster if thin
        if len(self.active_notes) < 2:
            root = random.choice([36, 43, 48, 55])
            voicing = random.choice([
                [0, 7],
                [0, 7, 14],
                [0, 5, 7],
                [0, 7, 12],
                [0, 3, 7, 10],    # minor 7th
            ])
            for interval in voicing:
                note = max(24, min(96, root + interval))
                self.send_note_on(note, random.randint(50, 85))

        # Slow CC undercurrent — always active in drone
        slow_targets = ['filter_cutoff', 'filter_res', 'lfo1_speed',
                        'reverb_decay', 'chorus_speed', 'filter_lfo1']
        for name in random.sample(slow_targets, k=2):
            self.send_cc(name, self.cc_state[name] + random.gauss(0, 2.5))

        return random.uniform(0.8, 3.5)

    def pulse_step(self):
        """Rhythmic impulses with indeterminate timing — not quite a grid."""
        pitches = [36, 43, 48, 55, 60, 67, 72]

        note_prob = 0.4 + self.density * 0.45
        if random.random() < note_prob:
            pitch = random.choice(pitches)
            vel = random.randint(55, 115)
            dur = random.choice([0.04, 0.08, 0.12, 0.2, 0.4, 0.8])
            self.send_note_on(pitch, vel)
            self.note_later(pitch, dur)

        # Rhythmic CC flutter
        if random.random() < 0.3:
            self.send_cc('filter_cutoff',
                         self.cc_state['filter_cutoff'] + random.randint(-18, 18))

        # Indeterminate tempo — somewhere between 60 and 180 BPM, with jitter
        bpm = random.choice([60, 80, 90, 120, 150, 180])
        base = 60.0 / bpm
        jitter = random.uniform(-0.04, 0.08)
        return max(0.04, base + jitter)

    def silence_step(self):
        """Oliveros / Deep Listening mode — profound gaps, rare single gestures."""
        if random.random() < 0.07 + self.density * 0.05:
            if random.random() < 0.55:
                pitch = random.randint(36, 84)
                vel = random.randint(15, 65)
                dur = random.uniform(2.0, 10.0)
                self.send_note_on(pitch, vel)
                self.note_later(pitch, dur)
            else:
                name = random.choice(list(CC_MAP.keys()))
                lo, hi = CC_RANGES[name]
                self.send_cc(name, random.randint(lo, hi))

        return random.uniform(3.0, 14.0)

    # ── MAIN LOOP ─────────────────────────────────────────────────────────────

    def run(self):
        step_fns = {
            'drift':   self.drift_step,
            'scatter': self.scatter_step,
            'drone':   self.drone_step,
            'pulse':   self.pulse_step,
            'silence': self.silence_step,
        }

        while self.running:
            if self.paused:
                time.sleep(0.05)
                continue

            with self.lock:
                mode = self.mode

            try:
                sleep_time = step_fns[mode]()
            except Exception as e:
                print(f"\n[error in {mode}: {e}]")
                sleep_time = 0.5

            # Interruptible sleep in 50ms chunks
            chunks = max(1, int(sleep_time / 0.05))
            for _ in range(chunks):
                if not self.running or self.paused:
                    break
                time.sleep(0.05)


# ─── MIDI PORT SELECTION ──────────────────────────────────────────────────────

def select_port(midi_out):
    ports = midi_out.get_ports()
    if not ports:
        print("No MIDI output ports found. Check Audio MIDI Setup.")
        sys.exit(1)

    # Auto-detect Korg / microKEY
    for i, name in enumerate(ports):
        if any(k in name.lower() for k in ['korg', 'microkey', 'supernova']):
            print(f"Auto-selected: [{i}] {name}")
            midi_out.open_port(i)
            return

    print("\nMIDI output ports:")
    for i, name in enumerate(ports):
        print(f"  [{i}] {name}")
    choice = input("Select port number: ").strip()
    try:
        midi_out.open_port(int(choice))
    except Exception:
        print("Invalid selection.")
        sys.exit(1)


# ─── CLI ──────────────────────────────────────────────────────────────────────

HELP = """
╔══════════════════════════════════════╗
║   sn2_gen — Supernova II generator   ║
╠══════════════════════════════════════╣
║  d  drift      s  scatter           ║
║  n  drone      p  pulse             ║
║  z  silence    [enter]  pause       ║
║  +  density↑   -  density↓          ║
║  r  reset/AOF  ?  status   q  quit  ║
╚══════════════════════════════════════╝
"""

def status(gen):
    state = "PAUSED" if gen.paused else "running"
    print(f"  [{gen.mode}]  density={gen.density:.1f}  "
          f"notes={len(gen.active_notes)}  {state}")

def cli(gen):
    print(HELP)
    status(gen)

    while gen.running:
        try:
            cmd = input("\n> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            cmd = 'q'

        if cmd == 'q':
            gen.all_notes_off()
            gen.running = False
        elif cmd == 'd':
            with gen.lock:
                gen.mode = 'drift'
            print("→ drift")
        elif cmd == 's':
            with gen.lock:
                gen.mode = 'scatter'
            print("→ scatter")
        elif cmd == 'n':
            gen.all_notes_off()
            with gen.lock:
                gen.mode = 'drone'
            print("→ drone")
        elif cmd == 'p':
            gen.all_notes_off()
            with gen.lock:
                gen.mode = 'pulse'
            print("→ pulse")
        elif cmd == 'z':
            gen.all_notes_off()
            with gen.lock:
                gen.mode = 'silence'
            print("→ silence (Oliveros mode)")
        elif cmd == '' or cmd == ' ':
            gen.paused = not gen.paused
            if gen.paused:
                gen.all_notes_off()
                print("⏸  paused")
            else:
                print("▶  running")
        elif cmd == '+':
            gen.density = min(1.0, round(gen.density + 0.1, 1))
            print(f"density → {gen.density:.1f}")
        elif cmd == '-':
            gen.density = max(0.0, round(gen.density - 0.1, 1))
            print(f"density → {gen.density:.1f}")
        elif cmd == 'r':
            gen.all_notes_off()
            print("all notes off")
        elif cmd == '?':
            status(gen)
        else:
            print(HELP)


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    midi_out = rtmidi.MidiOut()
    select_port(midi_out)

    gen = SN2Gen(midi_out)

    gen_thread = threading.Thread(target=gen.run, daemon=True)
    gen_thread.start()

    try:
        cli(gen)
    finally:
        gen.running = False
        gen.all_notes_off()
        time.sleep(0.15)
        midi_out.close_port()
        print("Done.")


if __name__ == '__main__':
    main()
