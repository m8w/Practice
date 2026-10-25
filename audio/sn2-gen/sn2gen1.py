#!/usr/bin/env python3
"""
sn2_gen.py — Generative MIDI controller for Novation Supernova II
xik6 / built with Claude

Performance Mode: Parts 1–8 → MIDI Channels 1–8
Each Part runs its own mode independently in its own thread.
Live settings menu — adjust anything while running.

Install:  pip install python-rtmidi
Run:      python3 sn2_gen.py

FRACTAL_HOOK: set gen.parts[n].density = audio_energy (0.0–1.0) from flame_pipeline
"""

import rtmidi
import time
import random
import threading
import sys

# ─── CC MAP (Supernova II OS2.0) ──────────────────────────────────────────────

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
    'distortion':     90,
    'reverb_send':    91,
    'delay_send':     92,
    'reverb_decay':   95,
    'delay_time':     101,
    'delay_fb':       102,
    'filter_od':      104,
}

CC_RANGES = {
    'mod_wheel':      [0,   90],
    'pan':            [30, 100],
    'lfo1_speed':     [0,  110],
    'lfo1_delay':     [0,   80],
    'lfo2_speed':     [0,  110],
    'lfo2_delay':     [0,   80],
    'osc1_pw':        [10, 118],
    'osc2_pw':        [10, 118],
    'osc3_pw':        [10, 118],
    'filter_lfo1':    [0,   80],
    'filter_cutoff':  [20, 115],
    'filter_res':     [0,   90],
    'filter_env2':    [0,   80],
    'env1_attack':    [0,   90],
    'env1_decay':     [10, 110],
    'env1_sustain':   [40, 127],
    'env1_release':   [20, 110],
    'chorus_speed':   [0,   90],
    'chorus_depth':   [0,   80],
    'distortion':     [0,   60],
    'reverb_send':    [10,  90],
    'delay_send':     [0,   70],
    'reverb_decay':   [20, 110],
    'delay_time':     [0,  110],
    'delay_fb':       [0,   80],
    'filter_od':      [0,   60],
}

DRIFT_TARGETS = [
    'filter_cutoff', 'filter_res', 'lfo1_speed', 'lfo2_speed',
    'filter_lfo1', 'reverb_send', 'reverb_decay', 'delay_send',
    'env1_attack', 'env1_release', 'mod_wheel', 'distortion',
    'osc1_pw', 'chorus_speed',
]

MODES = ['drift', 'scatter', 'drone', 'pulse', 'silence', 'off']

# ─── PART ─────────────────────────────────────────────────────────────────────

class Part:
    """One SN2 Performance Part = one MIDI channel."""

    def __init__(self, channel):
        self.channel = channel      # 1-indexed (Part number on SN2)
        self.ch = channel - 1       # 0-indexed for MIDI bytes
        self.mode = 'drift'
        self.density = 0.5
        self.active = True
        self.active_notes = set()
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
            'distortion':     0,
            'reverb_send':    40,
            'delay_send':     20,
            'reverb_decay':   60,
            'delay_time':     60,
            'delay_fb':       40,
            'filter_od':      0,
        }

    def label(self):
        flag = '●' if self.active else '○'
        return f"  {flag} Ch{self.channel}  {self.mode:<8} d={self.density:.1f}"


# ─── GENERATOR ────────────────────────────────────────────────────────────────

class SN2Gen:
    def __init__(self, midi_out):
        self.midi = midi_out
        self.running = True
        self.paused = False
        self.lock = threading.Lock()

        # 8 Parts → channels 1–8
        self.parts = [Part(i) for i in range(1, 9)]

        # Default spread across modes
        self.parts[0].mode = 'drift'
        self.parts[1].mode = 'drift'
        self.parts[2].mode = 'scatter'
        self.parts[3].mode = 'scatter'
        self.parts[4].mode = 'drone'
        self.parts[5].mode = 'drone'
        self.parts[6].mode = 'pulse'
        self.parts[7].mode = 'silence'

    # ── MIDI primitives ───────────────────────────────────────────────────────

    def send_cc(self, part, name, value):
        lo, hi = CC_RANGES[name]
        value = max(lo, min(hi, int(value)))
        self.midi.send_message([0xB0 | part.ch, CC_MAP[name], value])
        part.cc_state[name] = value

    def send_note_on(self, part, note, velocity):
        note = max(0, min(127, int(note)))
        velocity = max(1, min(127, int(velocity)))
        self.midi.send_message([0x90 | part.ch, note, velocity])
        part.active_notes.add(note)

    def send_note_off(self, part, note):
        note = max(0, min(127, int(note)))
        self.midi.send_message([0x80 | part.ch, note, 0])
        part.active_notes.discard(note)

    def note_later(self, part, note, duration):
        t = threading.Timer(duration, self.send_note_off, args=[part, note])
        t.daemon = True
        t.start()

    def all_notes_off(self, part=None):
        targets = [part] if part else self.parts
        for p in targets:
            for note in list(p.active_notes):
                self.send_note_off(p, note)
            self.midi.send_message([0xB0 | p.ch, 123, 0])

    # ── MODES ─────────────────────────────────────────────────────────────────

    def drift_step(self, part):
        n = random.randint(1, max(1, int(2 + part.density * 4)))
        targets = random.sample(DRIFT_TARGETS, k=n)
        for name in targets:
            step = random.gauss(0, 4 + part.density * 10)
            self.send_cc(part, name, part.cc_state[name] + step)
        return random.uniform(0.2, 2.5 - part.density * 1.8)

    def scatter_step(self, part):
        if part.density < 0.35:
            scale, root = [0, 2, 4, 7, 9], 48
        elif part.density < 0.65:
            scale, root = [0, 2, 4, 6, 8, 10], 36
        else:
            scale, root = list(range(12)), 36

        if random.random() < (0.25 + part.density * 0.6):
            octave = random.choice([0, 12, 24, 36])
            pitch = max(24, min(96, root + random.choice(scale) + octave))
            vel = int(random.triangular(15, 127, 50 + part.density * 40))
            dur = random.uniform(0.1, 2.0 + part.density * 3.0)
            self.send_note_on(part, pitch, vel)
            self.note_later(part, pitch, dur)

        if random.random() < 0.35:
            name = random.choice(['filter_cutoff', 'filter_res', 'lfo1_speed',
                                  'reverb_send', 'delay_send', 'mod_wheel'])
            self.send_cc(part, name, part.cc_state[name] + random.gauss(0, 12))

        gap = random.expovariate(1.0 / (0.4 + part.density * 1.5))
        return max(0.04, min(4.0, gap))

    def drone_step(self, part):
        if len(part.active_notes) < 2:
            root = random.choice([36, 43, 48, 55])
            voicing = random.choice([
                [0, 7], [0, 7, 14], [0, 5, 7],
                [0, 7, 12], [0, 3, 7, 10],
            ])
            for interval in voicing:
                note = max(24, min(96, root + interval))
                self.send_note_on(part, note, random.randint(50, 85))

        for name in random.sample(['filter_cutoff', 'filter_res', 'lfo1_speed',
                                   'reverb_decay', 'chorus_speed', 'filter_lfo1'], k=2):
            self.send_cc(part, name, part.cc_state[name] + random.gauss(0, 2.5))

        return random.uniform(0.8, 3.5)

    def pulse_step(self, part):
        if random.random() < (0.4 + part.density * 0.45):
            pitch = random.choice([36, 43, 48, 55, 60, 67, 72])
            dur = random.choice([0.04, 0.08, 0.12, 0.2, 0.4, 0.8])
            self.send_note_on(part, pitch, random.randint(55, 115))
            self.note_later(part, pitch, dur)

        if random.random() < 0.3:
            self.send_cc(part, 'filter_cutoff',
                         part.cc_state['filter_cutoff'] + random.randint(-18, 18))

        bpm = random.choice([60, 80, 90, 120, 150, 180])
        return max(0.04, 60.0 / bpm + random.uniform(-0.04, 0.08))

    def silence_step(self, part):
        if random.random() < (0.06 + part.density * 0.05):
            if random.random() < 0.55:
                pitch = random.randint(36, 84)
                dur = random.uniform(2.0, 10.0)
                self.send_note_on(part, pitch, random.randint(15, 65))
                self.note_later(part, pitch, dur)
            else:
                name = random.choice(list(CC_MAP.keys()))
                lo, hi = CC_RANGES[name]
                self.send_cc(part, name, random.randint(lo, hi))
        return random.uniform(3.0, 14.0)

    # ── PER-PART THREAD ───────────────────────────────────────────────────────

    def run_part(self, part):
        step_fns = {
            'drift':   self.drift_step,
            'scatter': self.scatter_step,
            'drone':   self.drone_step,
            'pulse':   self.pulse_step,
            'silence': self.silence_step,
        }
        while self.running:
            if self.paused or not part.active or part.mode == 'off':
                time.sleep(0.05)
                continue
            try:
                sleep_time = step_fns[part.mode](part)
            except Exception as e:
                print(f"\n[Ch{part.channel} error: {e}]")
                sleep_time = 0.5
            chunks = max(1, int(sleep_time / 0.05))
            for _ in range(chunks):
                if not self.running or self.paused or not part.active:
                    break
                time.sleep(0.05)

    def run(self):
        threads = [
            threading.Thread(target=self.run_part, args=[p], daemon=True)
            for p in self.parts
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()


# ─── MIDI PORT ────────────────────────────────────────────────────────────────

def select_port(midi_out):
    ports = midi_out.get_ports()
    if not ports:
        print("No MIDI output ports found.")
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


# ─── CLI ──────────────────────────────────────────────────────────────────────

MAIN_HELP = """
╔══════════════════════════════════════════╗
║     sn2_gen  //  xik6  //  8-channel    ║
╠══════════════════════════════════════════╣
║  s   settings (live, while running)     ║
║  ?   status                             ║
║  r   all notes off                      ║
║  [enter]  pause / resume                ║
║  q   quit                               ║
╚══════════════════════════════════════════╝"""

SETTINGS_HELP = """
── settings ──────────────────────────────
  1-8     select part to edit
  d       drift      s   scatter
  n       drone      p   pulse
  z       silence    o   off
  + / -   density up / down
  t       toggle part on / off
  a       apply this mode → all parts
  A+ A-   density up/down → all parts
  ?       show status
  b       back to main
──────────────────────────────────────────"""

MODE_KEYS = {
    'd': 'drift', 's': 'scatter', 'n': 'drone',
    'p': 'pulse',  'z': 'silence', 'o': 'off',
}

def show_status(gen):
    print("\n── Parts ─────────────────────────────────")
    for p in gen.parts:
        notes = f" ♩{len(p.active_notes)}" if p.active_notes else ""
        print(f"{p.label()}{notes}")
    print(f"  {'⏸  PAUSED' if gen.paused else '▶  running'}")
    print("──────────────────────────────────────────")

def settings_menu(gen):
    print(SETTINGS_HELP)
    selected = gen.parts[0]
    print(f"  editing: Ch{selected.channel}")

    while True:
        try:
            cmd = input("  set> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if cmd in ('b', ''):
            break
        elif cmd in [str(i) for i in range(1, 9)]:
            selected = gen.parts[int(cmd) - 1]
            print(f"  → Ch{selected.channel}: mode={selected.mode}  "
                  f"density={selected.density:.1f}  "
                  f"{'ON' if selected.active else 'off'}")
        elif cmd in MODE_KEYS:
            new_mode = MODE_KEYS[cmd]
            if new_mode in ('off', 'silence', 'drone'):
                gen.all_notes_off(selected)
            selected.mode = new_mode
            print(f"  Ch{selected.channel} → {new_mode}")
        elif cmd == '+':
            selected.density = min(1.0, round(selected.density + 0.1, 1))
            print(f"  Ch{selected.channel} density → {selected.density:.1f}")
        elif cmd == '-':
            selected.density = max(0.0, round(selected.density - 0.1, 1))
            print(f"  Ch{selected.channel} density → {selected.density:.1f}")
        elif cmd == 't':
            selected.active = not selected.active
            if not selected.active:
                gen.all_notes_off(selected)
            print(f"  Ch{selected.channel} → {'ON' if selected.active else 'off'}")
        elif cmd == 'a':
            for p in gen.parts:
                if selected.mode in ('off', 'silence', 'drone'):
                    gen.all_notes_off(p)
                p.mode = selected.mode
            print(f"  all parts → {selected.mode}")
        elif cmd == 'a+':
            for p in gen.parts:
                p.density = min(1.0, round(p.density + 0.1, 1))
            print(f"  all density ↑")
        elif cmd == 'a-':
            for p in gen.parts:
                p.density = max(0.0, round(p.density - 0.1, 1))
            print(f"  all density ↓")
        elif cmd == '?':
            show_status(gen)
        else:
            print(SETTINGS_HELP)

    print("  ← main")

def cli(gen):
    print(MAIN_HELP)
    show_status(gen)

    while gen.running:
        try:
            cmd = input("\n> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            cmd = 'q'

        if cmd == 'q':
            gen.all_notes_off()
            gen.running = False
        elif cmd in ('', ' '):
            gen.paused = not gen.paused
            if gen.paused:
                gen.all_notes_off()
                print("⏸  paused")
            else:
                print("▶  running")
        elif cmd == 's':
            settings_menu(gen)
            show_status(gen)
        elif cmd == 'r':
            gen.all_notes_off()
            print("all notes off")
        elif cmd == '?':
            show_status(gen)
        else:
            print(MAIN_HELP)


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
