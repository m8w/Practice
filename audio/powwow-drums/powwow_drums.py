#!/usr/bin/env python3
"""
powwow_drums.py — Generative percussion engine for Novation Supernova II
xik6 / built with Claude

Structurally inspired by Plains powwow drumming (steady pulse, honor beats,
call-and-response, sneak-up crescendo) — NOT reproducing any specific
ceremonial song.

MANUAL SETUP REQUIRED BEFORE RUNNING:
  On the SN2, load a Performance where each Part uses a Drum Map or
  percussion Program. The script does NOT send Bank Select / Program
  Change messages — that was crashing the SN2.

  Suggested Performance layout (edit PART_MAP at the top if different):
    Part 1 → Drum Map 'a'       (909/808 kit)
    Part 2 → Drum Map 'b'       (electro kit)
    Part 3 → Drum Map 'c'       (SFX kit — noise/sweeps)
    Part 4 → Drum Map 'd'       (FM/orchestral kit)
    Part 5 → any drum Program   (will be used as 'low')
    Part 6 → any drum Program   (will be used as 'mid')
    Part 7 → any drum Program   (will be used as 'high')
    Part 8 → Drum Map 'h'       (hybrid kit)

Install:  pip install python-rtmidi
Run:      python3 powwow_drums.py

Live controls:
  s   settings
  ?   status
  [enter]  pause / resume
  r   all notes off
  q   quit
"""

import rtmidi
import random
import re
import time
import threading
import sys

# ─── PART → DRUM MAP ASSIGNMENTS ───────────────────────────────────────────────
# Edit this to match your actual Performance. For parts without a Drum Map,
# use None and set a fixed_note range in SIMPLE_PART_NOTES below.

PART_MAP = {
    1: "a",
    2: "b",
    3: "c",
    4: "d",
    5: None,   # Part 5 → generic percussion range
    6: None,   # Part 6 → generic percussion range
    7: None,   # Part 7 → generic percussion range
    8: None,   # Part 8 → H034 Program (restricted — see PROGRAM_NOTES)
}

# ─── PROGRAM-BASED NOTE RESTRICTIONS ──────────────────────────────────────────
# Some SN2 Programs only respond to a narrow range of notes. These
# restrictions are a property of the PROGRAM itself, so they follow the
# Program wherever it's loaded. If you move H034 to a different Part,
# just update PART_PROGRAMS below and the restriction follows automatically.
#
# To add a new restricted Program:
#   PROGRAM_NOTES["H056"] = [60, 62]   # H056 only plays MIDI 60 and 62

PROGRAM_NOTES = {
    "H034": [79, 80],   # G4 and G#4 — one-shot sound, same tone for both notes
}

# Tells the script which Program is loaded in which Part. Only list Parts
# whose Programs are in PROGRAM_NOTES above — all others follow PART_MAP.
PART_PROGRAMS = {
    8: "H034",
}

# For parts with no Drum Map (set to None above), use these note ranges
# cycling by role. Default: GM-style drum note positions.
SIMPLE_PART_NOTES = {
    "low":  36,   # kick
    "mid":  38,   # snare
    "high": 42,   # closed hi-hat
    "rim":  41,   # low tom
}

BASE_NOTE = 36  # Position 0 in any Drum Map

# ─── DRUM MAP NAME TABLES (from manual OS2.0) ─────────────────────────────────

DRUM_MAPS = {
    "a": [
        "909 Kick 2", "Rim Shot", "808 Snare 2", "808 Clap", "909 Snare",
        "Electro Low Tom 1", "Closed High Hat 1", "Electro Low Tom 2", "Closed High Hat 2", "Electro Mid Tom",
        "Open High Hat 1", "Electro Hi Tom 1", "Electro Hi Tom 2", "Crash Cymbal 1", "Crash Cymbal 3",
        "Crash Cymbal 4", "909 Clap", "909 Low Tom 1", "808 Closed HiHat", "909 Low Tom 2",
        "808 Cowbell", "909 Mid Tom", "808 Open HiHat", "909 Hi Tom 1", "909 Hi Tom 2",
        "808 Kick Click L", "808 Low Conga", "Noisy Kick", "808 Mid Conga", "808 Hi Conga",
        "Snova2 Kick5", "808 Low Tom", "Snova2 Kick4", "808 Mid Tom", "808 Maracas",
        "808 Hi Tom", "808 Kick Click", "Dodger Rodger", "Noisy Kick 1", "808 Clave",
        "FM Block", "Bottle", "Simple Low Conga", "Simple Mid Conga", "Simple Hi Conga",
        "Snova Kick5", "Snova2 Kick4", "Snova2 Kick3", "Snova2 Kick2", "General kit 1 FX",
    ],
    "b": [
        "909 Kick 2", "Rim Shot", "808 Snare 2", "808 Clap", "909 Snare2",
        "Electro Tom 5", "Closed High Hat 3", "Electro Tom 4", "Closed High Hat 4", "Electro Tom 3",
        "Open High Hat 2", "Electro Tom 2", "Electro Tom 1", "Crash Cymbal 1", "Crash Cymbal 2",
        "Ride Cymbal", "Clap 3", "Disco Tom 5", "808 Closed HiHat", "Disco Tom 4",
        "808 Cowbell 2", "Disco Tom 3", "808 Open HiHat", "Disco Tom 2", "Disco Tom 1",
        "808 Kick Click L", "808 Low Conga", "808 Snare", "808 Mid Conga", "808 Hi Conga",
        "Snova2 Kick1", "Garage Snare 4", "Breakdown Kick", "Velo Spitz", "Maracas 2",
        "Electro Guiro", "Synth Scratch", "Sonar", "Synth Backslip", "808 Clave 2",
        "Wah 4", "Wah 3", "MultiHigh Hat", "Wah 2", "Hi Hat (Closed)",
        "Wah 1", "Hi Hat (Open)", "909 Kick + Click", "808 Kick No Clk", "General kit 2 FX",
    ],
    "c": [
        "Res FX 1", "Wip 1", "Res FX 2", "Force Field 2", "Res FX 3",
        "Res FX 4", "Vindaloo Noise", "Spit U Like M-Wh", "RainPicks", "Spit U Like2M-Wh",
        "Infiltrator SFX", "Burbler", "Down Osc", "Rain Piz", "Filter Sweep KHz",
        "Ringglass", "Explosion", "Liquinova M-Wh", "Self Osc", "Radar>Comms",
        "Whiteout", "Low Transmitter", "RaverSiren", "Ring>Vince", "Wabbler",
        "Tubus", "Force Field", "Science Lab", "Downloading", "LFO Madness",
        "Res FX 10", "Res FX 11", "Noise Sweep", "Velo Spitz M-Wh", "Noise Sweep 1",
        "Noise Sweep 3", "Noise Sweep 4", "Noise Sweep 5", "REZ1", "REZ2",
        "REZ3", "Noise Sweep 6", "CHIRP 1", "CHIRP 2", "CHIRP 3",
        "CHIRP 4", "CHIRP 5", "CHIRP 6", "CHIRP 7", "SFX Kit FX",
    ],
    "d": [
        "FM 808 Clap", "Rim Shot 2", "909 Snare 2", "808 Clap 3", "808 Snare 3",
        "Tom1", "808 CHH", "Tom2", "808 CHH2", "Tom3",
        "808 OHH", "Tom4", "Tom5", "Crash Cymbal 2", "Noise Shot",
        "Ride 2", "China Cymbal", "Nova Crash", "Rev High Hat 1", "Noise Sweep",
        "FM Cowbell", "Lo Timbale/SN", "Rev High Hat 2", "Hi Timbale/SN", "High Conga 4",
        "High Conga 3", "High Conga 2", "High Conga 1", "Low Conga 1", "Low Conga 2",
        "Low Conga 3", "FM GarageSnare 1", "FM GarageSnare 2", "Timpani", "Timpani",
        "Timpani", "Timpani", "Timpani", "Timpani", "Timpani",
        "Timpani", "Timpani", "Timpani", "Timpani", "Triangle 2",
        "Triangle 1", "FM 909 Kick 1", "FM 909 Kick 2", "FM Kit FX", "FM Kit FX",
    ],
    "h": [
        "909 Kick + Click", "808 Kick Click L", "808 Snare 2", "808 Clap", "909 Snare 2",
        "909 Snare 2", "Closed High Hat3", "909 Snare 2", "Closed High Hat4", "909 Kick 2",
        "Closed High Hat4", "Open High Hat 2", "Open High Hat 2", "808 Kick Click L", "Noise Shot",
        "Ride 2", "Explosion", "Nova Crash 2", "Closed High Hat3", "Closed High Hat3",
        "Closed High Hat4", "Closed High Hat4", "Closed High Hat4", "Closed High Hat4", "Hi Conga 4",
        "Hi Conga 3", "Hi Conga 2", "Hi conga 1", "Low Conga 1", "Low Conga 2",
        "Low Conga 3", "FM GarageSnare1", "FM GarageSnare2", "Maracas 2", "Noise Sweep 1",
        "Maracas 2", "808 Cow Bell 2", "Init Program", "808 Low Conga", "808 Clave 2",
        "808 Mid Conga", "808 Hi Conga", "Hi Hat (Closed)", "Init Program", "Hi Hat (Open)",
        "Triangle 2", "Triangle 1", "909 Kick + Click", "Init Program", "Init Program",
    ],
}

ROLE_KEYWORDS = {
    "low":  [r"\bkick\b"],
    "mid":  [r"\bsnare\b", r"\bclap\b"],
    "high": [r"hi\s*hat", r"\bchh\b", r"\bohh\b", r"cymbal", r"\bride\b", r"\bcrash\b"],
    "rim":  [r"\brim\b", r"\btom\b", r"conga", r"clave", r"cowbell", r"maracas", r"triangle", r"block"],
}

# ─── PATTERN TEMPLATES ─────────────────────────────────────────────────────────

def heartbeat_hits(length):
    return [(i, "low", False) for i in range(length)]

def honor_beats_hits(length):
    hits = [(i, "low", False) for i in range(length)]
    honor_positions = random.sample(range(length), k=random.randint(1, 3))
    for pos in honor_positions:
        hits.append((pos, random.choice(["high", "mid"]), True))
    return hits

def call_response_hits(length):
    hits = []
    for i in range(0, length, 2):
        hits.append((i, "low", False))
        if i + 1 < length:
            hits.append((i + 1, random.choice(["mid", "high"]), False))
    return hits

def sneak_up_hits(length):
    """Build velocity from quiet to loud over the bar."""
    hits = []
    for i in range(0, length, 2):
        hits.append((i, random.choice(["rim", "low"]), False))
    hits.append((length - 1, "high", True))
    return hits

def scatter_hits(length):
    """Cage-style random — pick roles and positions freely."""
    n = random.randint(3, max(4, length // 2))
    hits = []
    for _ in range(n):
        beat = random.randint(0, length - 1)
        role = random.choice(["low", "mid", "high", "rim"])
        honor = random.random() < 0.15
        hits.append((beat, role, honor))
    return hits

def gallop_hits(length):
    """Fast double-strikes with gaps."""
    hits = []
    i = 0
    while i < length:
        hits.append((i, "low", False))
        if i + 1 < length and random.random() < 0.7:
            hits.append((i + 1, "mid", False))
        i += random.choice([3, 4])
    # Occasional high accent
    if random.random() < 0.5:
        hits.append((random.randint(0, length - 1), "high", True))
    return hits

def rolling_hits(length):
    """Continuous roll on one role across the bar."""
    role = random.choice(["rim", "mid", "high"])
    hits = [(i, role, False) for i in range(length)]
    # Punctuate with kicks
    for pos in random.sample(range(length), k=max(1, length // 4)):
        hits.append((pos, "low", True))
    return hits

def chaos_hits(length):
    """Full-palette chaos — uses role 'any' which spans ALL 50 drum map positions
    regardless of category. This hits sounds like 'Dodger Rodger', 'FM Block',
    'Bottle', 'General kit 1 FX' that no role keyword matches."""
    n = random.randint(4, max(5, length))
    hits = []
    for _ in range(n):
        beat = random.randint(0, length - 1)
        honor = random.random() < 0.1
        hits.append((beat, "any", honor))
    return hits

def tour_hits(length):
    """Deliberately cycles through many positions — showcase template."""
    hits = []
    for i in range(length):
        role = random.choice(["low", "mid", "high", "rim", "any", "any"])
        honor = (i == 0) or (random.random() < 0.1)
        hits.append((i, role, honor))
    return hits

TEMPLATE_FNS = {
    "heartbeat":     heartbeat_hits,
    "honor_beats":   honor_beats_hits,
    "call_response": call_response_hits,
    "sneak_up":      sneak_up_hits,
    "scatter":       scatter_hits,
    "gallop":        gallop_hits,
    "rolling":       rolling_hits,
    "chaos":         chaos_hits,
    "tour":          tour_hits,
}

# ─── ENGINE ────────────────────────────────────────────────────────────────────

class DrumEngine:
    def __init__(self, midi_out):
        self.midi = midi_out
        self.midi_lock = threading.Lock()  # serialize MIDI writes
        self.running = True
        self.paused = False

        # Live-tunable parameters
        self.density = 0.5
        self.bpm_range = [80, 160]
        self.silence_chance = 0.2
        self.silence_range = [0.5, 2.5]
        self.active_parts_range = [1, 4]
        self.velocity_range = [60, 110]
        self.honor_velocity = [115, 127]
        self.part_offset_jitter = 0.015
        self.bar_length_options = [8, 12, 16]

        self.enabled_parts = {p: True for p in PART_MAP}

        # Built in start()
        self.part_roles = {}

    # ── MIDI (thread-safe, rate-limited) ─────────────────────────────────────

    def send(self, msg_bytes):
        with self.midi_lock:
            self.midi.send_message(msg_bytes)
            time.sleep(0.001)  # 1ms gap prevents flooding the SN2

    def send_note_on(self, channel, note, velocity):
        self.send([0x90 | channel,
                   max(0, min(127, note)),
                   max(1, min(127, velocity))])

    def send_note_off(self, channel, note):
        self.send([0x80 | channel, max(0, min(127, note)), 0])

    def note_later(self, channel, note, duration):
        t = threading.Timer(duration, self.send_note_off, args=[channel, note])
        t.daemon = True
        t.start()

    def all_notes_off(self):
        for ch in range(16):
            with self.midi_lock:
                self.midi.send_message([0xB0 | ch, 123, 0])
                time.sleep(0.002)

    # ── SETUP ────────────────────────────────────────────────────────────────

    def build_role_map(self, map_letter, part_number=None):
        # Program-based restriction takes highest priority
        if part_number is not None and part_number in PART_PROGRAMS:
            program_name = PART_PROGRAMS[part_number]
            if program_name in PROGRAM_NOTES:
                allowed = PROGRAM_NOTES[program_name]
                return {role: list(allowed) for role in ["low", "mid", "high", "rim", "any"]}

        if map_letter is None:
            # Generic percussion for parts without a Drum Map
            roles = dict(SIMPLE_PART_NOTES)
            roles["any"] = list(range(36, 85))
            return roles

        names = DRUM_MAPS.get(map_letter, [])
        role_notes = {}
        for role, patterns in ROLE_KEYWORDS.items():
            matches = []
            for pos, name in enumerate(names):
                low = name.lower()
                if any(re.search(p, low) for p in patterns):
                    matches.append(BASE_NOTE + pos)
            if matches:
                role_notes[role] = matches
        if names:
            role_notes["any"] = [BASE_NOTE + pos for pos in range(len(names))]
        return role_notes

    def initialize_parts(self):
        print("\n  Part role mappings (number = available sounds per role):")
        for part, map_letter in PART_MAP.items():
            roles = self.build_role_map(map_letter, part_number=part)
            if roles:
                self.part_roles[part] = roles
                if part in PART_PROGRAMS:
                    prog = PART_PROGRAMS[part]
                    label = f"Program {prog} (restricted → {PROGRAM_NOTES[prog]})"
                elif map_letter:
                    label = f"Drum Map '{map_letter}'"
                else:
                    label = "generic"
                role_summary = ", ".join(
                    f"{r}={len(v) if isinstance(v, list) else 1}"
                    for r, v in roles.items()
                )
                print(f"    Part {part} → {label}: {role_summary}")
            else:
                print(f"    Part {part} (map '{map_letter}') — no sounds, skipping")

    def resolve_note(self, role_entry):
        """A role entry may be an int (simple) or a list (pick one)."""
        if isinstance(role_entry, list):
            return random.choice(role_entry)
        return role_entry

    def events_from_hits(self, hits, step_seconds, role_notes):
        events = []
        for beat, role, honor in hits:
            if role not in role_notes:
                continue
            vel_range = self.honor_velocity if honor else self.velocity_range
            vel = random.randint(*vel_range)
            note = self.resolve_note(role_notes[role])
            events.append((beat * step_seconds, note, vel))
        return sorted(events)

    # ── MAIN LOOP ────────────────────────────────────────────────────────────

    def run(self):
        template_names = list(TEMPLATE_FNS.keys())
        recent_templates = []  # avoid repeating recent choices

        while self.running:
            if self.paused:
                time.sleep(0.05)
                continue

            active_parts = [p for p in self.part_roles if self.enabled_parts.get(p, True)]
            if not active_parts:
                time.sleep(0.2)
                continue

            # Template rotation — avoid last 2 to force variation
            avoid = recent_templates[-2:] if len(recent_templates) >= 2 else recent_templates
            choices = [t for t in template_names if t not in avoid]
            template_name = random.choice(choices)
            recent_templates.append(template_name)
            if len(recent_templates) > 4:
                recent_templates.pop(0)

            bpm = random.uniform(*self.bpm_range)
            step_seconds = 60.0 / bpm / 2

            length = random.choice(self.bar_length_options)
            hits = TEMPLATE_FNS[template_name](length)
            bar_duration = length * step_seconds
            bar_start = time.time()

            # Choose active parts for this bar, density affects count
            max_parts = min(self.active_parts_range[1] + int(self.density * 2),
                            len(active_parts))
            min_parts = min(self.active_parts_range[0], max_parts)
            n_active = random.randint(min_parts, max_parts)
            chosen_parts = random.sample(active_parts, k=n_active)

            # Build per-part events
            part_events = {}
            for part in chosen_parts:
                roles = self.part_roles[part]
                offset = random.uniform(-self.part_offset_jitter, self.part_offset_jitter)
                events = [(max(0.0, t + offset), note, vel)
                          for t, note, vel in self.events_from_hits(hits, step_seconds, roles)]
                if events:
                    part_events[part] = events

            merged = sorted(
                (t, part, note, vel)
                for part, events in part_events.items()
                for t, note, vel in events
            )

            for t, part, note, vel in merged:
                if not self.running or self.paused:
                    break
                sleep_for = (bar_start + t) - time.time()
                if sleep_for > 0:
                    time.sleep(sleep_for)
                ch = part - 1
                self.send_note_on(ch, note, vel)
                # Short note-off, varies per hit so it doesn't sound robotic
                self.note_later(ch, note, random.uniform(0.05, 0.15))

            # Finish the bar
            remaining = bar_duration - (time.time() - bar_start)
            if remaining > 0:
                time.sleep(remaining)

            # Occasional silence
            if random.random() < self.silence_chance:
                gap = random.uniform(*self.silence_range)
                time.sleep(gap)


# ─── MIDI PORT SELECTION ───────────────────────────────────────────────────────

def select_port(midi_out):
    ports = midi_out.get_ports()
    if not ports:
        print("No MIDI output ports found. Check Audio MIDI Setup.")
        sys.exit(1)

    for keywords in [
        ['iac', 'virtual'],
        ['korg', 'microkorg', 'microkey'],
        ['supernova'],
    ]:
        for i, name in enumerate(ports):
            if any(k in name.lower() for k in keywords):
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


# ─── CLI ───────────────────────────────────────────────────────────────────────

MAIN_HELP = """
╔═══════════════════════════════════════════╗
║   powwow_drums  //  xik6  //  8-channel  ║
╠═══════════════════════════════════════════╣
║  s   settings                            ║
║  ?   status                              ║
║  [enter]  pause / resume                 ║
║  r   all notes off                       ║
║  q   quit                                ║
╚═══════════════════════════════════════════╝"""

SETTINGS_HELP = """
── settings ──────────────────────────────
  1-8     toggle part on/off
  + / -   density up / down
  f / w   faster / slower bpm range
  z+/z-   more / less silence
  m / l   more / fewer active parts
  ?       show status
  b       back
──────────────────────────────────────────"""

def show_status(eng):
    print("\n── Status ────────────────────────────────")
    parts_line = "  Parts: "
    for p in sorted(PART_MAP):
        if p not in eng.part_roles:
            continue
        flag = '●' if eng.enabled_parts.get(p, True) else '○'
        parts_line += f"{flag}{p} "
    print(parts_line)
    print(f"  density={eng.density:.1f}  bpm={eng.bpm_range[0]:.0f}-{eng.bpm_range[1]:.0f}  "
          f"silence={eng.silence_chance:.2f}  "
          f"active/bar={eng.active_parts_range[0]}-{eng.active_parts_range[1]}")
    print(f"  {'⏸  PAUSED' if eng.paused else '▶  running'}")
    print("──────────────────────────────────────────")

def settings_menu(eng):
    print(SETTINGS_HELP)
    while True:
        try:
            cmd = input("  set> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break
        if cmd in ('b', ''):
            break
        elif cmd in [str(i) for i in range(1, 9)]:
            p = int(cmd)
            if p in eng.enabled_parts:
                eng.enabled_parts[p] = not eng.enabled_parts[p]
                print(f"  Part {p} → {'ON' if eng.enabled_parts[p] else 'off'}")
        elif cmd == '+':
            eng.density = min(1.0, round(eng.density + 0.1, 1))
            print(f"  density → {eng.density:.1f}")
        elif cmd == '-':
            eng.density = max(0.0, round(eng.density - 0.1, 1))
            print(f"  density → {eng.density:.1f}")
        elif cmd == 'f':
            eng.bpm_range = [min(240, eng.bpm_range[0] + 10),
                             min(280, eng.bpm_range[1] + 10)]
            print(f"  bpm range → {eng.bpm_range[0]}-{eng.bpm_range[1]}")
        elif cmd == 'w':
            eng.bpm_range = [max(40, eng.bpm_range[0] - 10),
                             max(60, eng.bpm_range[1] - 10)]
            print(f"  bpm range → {eng.bpm_range[0]}-{eng.bpm_range[1]}")
        elif cmd == 'z+':
            eng.silence_chance = min(0.9, round(eng.silence_chance + 0.05, 2))
            print(f"  silence → {eng.silence_chance:.2f}")
        elif cmd == 'z-':
            eng.silence_chance = max(0.0, round(eng.silence_chance - 0.05, 2))
            print(f"  silence → {eng.silence_chance:.2f}")
        elif cmd == 'm':
            eng.active_parts_range[1] = min(8, eng.active_parts_range[1] + 1)
            print(f"  active/bar → {eng.active_parts_range[0]}-{eng.active_parts_range[1]}")
        elif cmd == 'l':
            eng.active_parts_range[1] = max(eng.active_parts_range[0],
                                            eng.active_parts_range[1] - 1)
            print(f"  active/bar → {eng.active_parts_range[0]}-{eng.active_parts_range[1]}")
        elif cmd == '?':
            show_status(eng)
        else:
            print(SETTINGS_HELP)
    print("  ← main")

def cli(eng):
    print(MAIN_HELP)
    show_status(eng)
    while eng.running:
        try:
            cmd = input("\n> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            cmd = 'q'
        if cmd == 'q':
            eng.all_notes_off()
            eng.running = False
        elif cmd in ('', ' '):
            eng.paused = not eng.paused
            if eng.paused:
                eng.all_notes_off()
                print("⏸  paused")
            else:
                print("▶  running")
        elif cmd == 's':
            settings_menu(eng)
            show_status(eng)
        elif cmd == 'r':
            eng.all_notes_off()
            print("all notes off")
        elif cmd == '?':
            show_status(eng)
        else:
            print(MAIN_HELP)


# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    midi_out = rtmidi.MidiOut()
    select_port(midi_out)

    eng = DrumEngine(midi_out)
    eng.initialize_parts()

    if not eng.part_roles:
        print("\nNo parts have usable drum content. Exiting.")
        sys.exit(1)

    print("\n" + "=" * 50)
    print("  MANUAL SN2 SETUP CHECKLIST:")
    print("  1. SN2 Performance Mode active")
    print("  2. Each Part set to the matching Drum Map or")
    print("     drum Program (see PART_MAP in the script)")
    print("  3. Part MIDI channels = Part numbers (1-8)")
    print("  4. Output routing set (Parts to outs 3/4, etc.)")
    print("=" * 50)
    input("\nPress ENTER when the SN2 is ready to receive...")

    print("\nStarting generative engine. Will send one test hit per part.")
    # Gentle test — one note per enabled part, so you can verify setup
    for part in sorted(eng.part_roles):
        roles = eng.part_roles[part]
        first_role = next(iter(roles))
        note = eng.resolve_note(roles[first_role])
        eng.send_note_on(part - 1, note, 90)
        time.sleep(0.1)
        eng.send_note_off(part - 1, note)
        time.sleep(0.3)

    print("Test complete. Starting generative loop.\n")

    gen_thread = threading.Thread(target=eng.run, daemon=True)
    gen_thread.start()

    try:
        cli(eng)
    finally:
        eng.running = False
        eng.all_notes_off()
        time.sleep(0.2)
        midi_out.close_port()
        print("Done.")

if __name__ == '__main__':
    main()
