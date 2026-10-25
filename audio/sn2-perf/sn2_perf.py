#!/usr/bin/env python3
"""
sn2_perf.py - generative Performance builder for the Novation Supernova II
xik6 / built with Claude

Composes 8-part Performances by sending Bank Select + Program Change on
channels 1-8, then mutates them with per-part FREEZE so good parts survive
while the rest keep moving.

Companion to sn2_gen.py (notes/CCs) and sn2_backup.py (SysEx).
Chain: Mac mini <-USB-> microKORG XL <-DIN-> Supernova II

    Global MIDI channel : 16
    Parts 1-8           : channels 1-8
    Program pool        : banks A-H, 128 each = 1024

Recipes are saved as JSON, not to the synth. The synth only holds one
Performance at a time; the JSON library has no limit. Write to the
front panel only for the keepers.

Install:  pip install python-rtmidi
Run:      python3 sn2_perf.py
"""

import os
import sys
import json
import time
import random
import glob
from datetime import datetime

try:
    import rtmidi
except ImportError:
    sys.exit("Missing dependency. Run:  pip install python-rtmidi")

# --- CONFIG -----------------------------------------------------------------

GLOBAL_MIDI_CHANNEL = 16          # must stay >8 or Part assignment breaks
PART_CHANNELS = [1, 2, 3, 4, 5, 6, 7, 8]

RECIPE_DIR = os.path.expanduser("~/sn2_performances")

BANKS = ["A", "B", "C", "D", "E", "F", "G", "H"]

# Bank Select LSB (CC32) value for each program bank.
#
# UNVERIFIED - the manual's bank message page didn't come through legibly,
# so this is the obvious mapping, not a confirmed one. CC32 value 0 is
# RESERVED: the SN2 treats bank 0 as the Program Change table and starts
# remapping every Program Change it receives afterwards. So this map
# starts at 1 and the code refuses to ever send 0.
#
# Run option 5 (probe) to confirm against the synth's display, then fix
# these numbers if they're off by one.
BANK_LSB = {"A": 1, "B": 2, "C": 3, "D": 4,
            "E": 5, "F": 6, "G": 7, "H": 8}

CC_BANK_MSB = 0
CC_BANK_LSB = 32
CC_VOLUME = 7
CC_PAN = 10

PC_SETTLE = 0.05      # pause after each part's program change

# --- PART / PERFORMANCE STATE ----------------------------------------------


class Part:
    def __init__(self, channel, bank="A", program=0, level=100, pan=64):
        self.channel = channel
        self.bank = bank
        self.program = program
        self.level = level
        self.pan = pan
        self.frozen = False

    def label(self):
        lock = "[FROZEN]" if self.frozen else "        "
        return (f"  Part {self.channel}  {lock}  "
                f"{self.bank}{self.program:03d}  "
                f"lvl {self.level:>3}  pan {self.pan:>3}")

    def to_dict(self):
        return {"channel": self.channel, "bank": self.bank,
                "program": self.program, "level": self.level,
                "pan": self.pan, "frozen": self.frozen}

    @staticmethod
    def from_dict(d):
        p = Part(d["channel"], d["bank"], d["program"],
                 d.get("level", 100), d.get("pan", 64))
        p.frozen = d.get("frozen", False)
        return p


class Performance:
    def __init__(self):
        self.parts = [Part(ch) for ch in PART_CHANNELS]
        self.name = "untitled"
        self.lineage = []          # names of recipes this descends from

    def show(self):
        print(f"\n  === {self.name} ===")
        for p in self.parts:
            print(p.label())
        frozen = sum(1 for p in self.parts if p.frozen)
        print(f"  {frozen}/8 frozen")

    def to_dict(self):
        return {"name": self.name,
                "lineage": self.lineage,
                "parts": [p.to_dict() for p in self.parts]}

    def load_dict(self, d):
        self.name = d.get("name", "untitled")
        self.lineage = d.get("lineage", [])
        self.parts = [Part.from_dict(x) for x in d["parts"]]


# --- MIDI -------------------------------------------------------------------


def open_output():
    midi_out = rtmidi.MidiOut()
    ports = midi_out.get_ports()
    if not ports:
        sys.exit("No MIDI output ports. Is the Korg plugged in?")
    print("\nAvailable MIDI OUTPUT ports:")
    for i, name in enumerate(ports):
        print(f"  [{i}] {name}")
    while True:
        raw = input("Select port number: ").strip()
        if raw.isdigit() and 0 <= int(raw) < len(ports):
            midi_out.open_port(int(raw))
            print(f"  -> {ports[int(raw)]}")
            return midi_out
        print("  Not a valid port number.")


def send_part(midi_out, part):
    """Bank Select + Program Change + level + pan on this part's channel."""
    ch = part.channel - 1                      # MIDI channels are 0-indexed
    lsb = BANK_LSB[part.bank]

    if lsb == 0:
        print(f"  ! refusing bank LSB 0 on part {part.channel} "
              f"(that's the Program Change table)")
        return
    if part.channel == GLOBAL_MIDI_CHANNEL:
        print(f"  ! part {part.channel} collides with the Global channel - "
              f"the synth would drop to Program Mode. Skipped.")
        return

    midi_out.send_message([0xB0 | ch, CC_BANK_MSB, 0])
    midi_out.send_message([0xB0 | ch, CC_BANK_LSB, lsb])
    midi_out.send_message([0xC0 | ch, part.program])
    midi_out.send_message([0xB0 | ch, CC_VOLUME, part.level])
    midi_out.send_message([0xB0 | ch, CC_PAN, part.pan])
    time.sleep(PC_SETTLE)


def send_performance(midi_out, perf):
    print()
    for p in perf.parts:
        send_part(midi_out, p)
        print(f"  sent part {p.channel}: {p.bank}{p.program:03d}")


# --- GENERATION -------------------------------------------------------------
#
# Pure uniform random across 8 parts gives mud almost every time: eight
# leads all fighting for the same register at the same volume. These
# constraints don't pick "good" sounds - they just keep the eight parts
# from occupying identical space.


def random_part(part, spread_levels=True):
    part.bank = random.choice(BANKS)
    part.program = random.randint(0, 127)
    if spread_levels:
        # a few parts sit forward, most sit back - not all at 100
        part.level = random.choice([127, 110, 100, 90, 80, 70, 55, 40])
        part.pan = random.choice([0, 16, 32, 48, 64, 64, 80, 96, 112, 127])


def generate(perf, respect_freeze=True):
    """Fresh performance. Frozen parts are left completely alone."""
    for p in perf.parts:
        if respect_freeze and p.frozen:
            continue
        random_part(p)
    perf.name = f"gen-{datetime.now().strftime('%H%M%S')}"


def mutate(perf, amount):
    """
    Nudge unfrozen parts. amount 0.0-1.0.

    Low amount  -> neighbouring programs in the same bank. The library is
                   roughly grouped by type, so a +/-3 step usually lands on
                   something related. This is the 'blend' behaviour.
    High amount -> jumps bank entirely. Discontinuity.
    """
    moved = 0
    for p in perf.parts:
        if p.frozen:
            continue
        moved += 1
        if random.random() < amount:
            # big move: new bank, new program
            p.bank = random.choice(BANKS)
            p.program = random.randint(0, 127)
        else:
            # small move: drift within the bank
            step = max(1, int(round(amount * 12)))
            p.program = max(0, min(127, p.program +
                                   random.randint(-step, step)))
        # levels drift more gently than programs
        p.level = max(0, min(127, p.level + random.randint(-12, 12)))
        p.pan = max(0, min(127, p.pan + random.randint(-16, 16)))

    if moved == 0:
        print("  Everything is frozen - nothing to mutate.")
    perf.name = f"mut-{datetime.now().strftime('%H%M%S')}"
    return moved


def blend(perf_a, perf_b, position):
    """
    Interpolate between two saved recipes. position 0.0 = A, 1.0 = B.

    Programs can't meaningfully average (program 40 isn't 'between' 10 and
    70 sonically), so parts CHOOSE which parent to take, weighted by
    position. Levels and pans DO interpolate, so the crossfade is audible
    even on the parts that haven't flipped yet.
    """
    out = Performance()
    for i, (a, b) in enumerate(zip(perf_a.parts, perf_b.parts)):
        p = out.parts[i]
        src = b if random.random() < position else a
        p.bank, p.program = src.bank, src.program
        p.level = int(a.level + (b.level - a.level) * position)
        p.pan = int(a.pan + (b.pan - a.pan) * position)
    out.name = f"blend-{position:.2f}"
    out.lineage = [perf_a.name, perf_b.name]
    return out


# --- RECIPE I/O -------------------------------------------------------------


def save_recipe(perf, name=None):
    os.makedirs(RECIPE_DIR, exist_ok=True)
    if name:
        perf.name = name
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in perf.name)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(RECIPE_DIR, f"{stamp}_{safe}.json")
    with open(path, "w") as f:
        json.dump(perf.to_dict(), f, indent=2)
    print(f"\n  Saved: {path}")
    return path


def list_recipes():
    return sorted(glob.glob(os.path.join(RECIPE_DIR, "*.json")))


def choose_recipe(prompt="Select recipe number: "):
    files = list_recipes()
    if not files:
        print(f"\n  No recipes in {RECIPE_DIR} yet.")
        return None
    print()
    for i, p in enumerate(files):
        print(f"  [{i}] {os.path.basename(p)}")
    while True:
        raw = input(prompt).strip()
        if raw == "":
            return None
        if raw.isdigit() and 0 <= int(raw) < len(files):
            return files[int(raw)]
        print("  Not a valid number (Enter to cancel).")


def load_recipe(path):
    with open(path) as f:
        d = json.load(f)
    p = Performance()
    p.load_dict(d)
    return p


# --- PROBE ------------------------------------------------------------------


def probe(midi_out):
    """
    Verify the BANK_LSB map against what the synth actually displays.
    Steps a single part through each bank at program 000.
    """
    print("""
  BANK PROBE
  Watch the SN2 display. I'll set Part 1 to program 000 in each bank
  in turn. Note which bank letter it actually shows for each LSB value.
  If they don't line up, correct BANK_LSB at the top of this file.
""")
    for bank in BANKS:
        lsb = BANK_LSB[bank]
        midi_out.send_message([0xB0, CC_BANK_MSB, 0])
        midi_out.send_message([0xB0, CC_BANK_LSB, lsb])
        midi_out.send_message([0xC0, 0])
        input(f"  sent LSB {lsb} (expecting bank {bank}) "
              f"- what does the display say? Enter to continue... ")


# --- INTERACTIVE ------------------------------------------------------------


def toggle_freeze(perf):
    perf.show()
    print("\n  Enter part numbers to toggle (e.g. '1 4 7'),")
    print("  'all' to freeze everything, 'none' to release everything.")
    raw = input("  > ").strip().lower()
    if raw == "all":
        for p in perf.parts:
            p.frozen = True
    elif raw == "none":
        for p in perf.parts:
            p.frozen = False
    else:
        for tok in raw.split():
            if tok.isdigit() and 1 <= int(tok) <= 8:
                perf.parts[int(tok) - 1].frozen ^= True
    perf.show()


def do_blend(midi_out):
    print("\n  Pick the FIRST recipe (A):")
    pa = choose_recipe()
    if not pa:
        return None
    print("\n  Pick the SECOND recipe (B):")
    pb = choose_recipe()
    if not pb:
        return None
    a, b = load_recipe(pa), load_recipe(pb)

    print("\n  Walk the blend. Enter a position 0.0-1.0 to hear it,")
    print("  'k' to keep the current one, Enter to stop.")
    current = None
    while True:
        raw = input("  position > ").strip().lower()
        if raw == "":
            return current
        if raw == "k":
            if current:
                save_recipe(current)
            continue
        try:
            pos = max(0.0, min(1.0, float(raw)))
        except ValueError:
            print("  Give me a number 0.0-1.0, or 'k', or Enter.")
            continue
        current = blend(a, b, pos)
        current.show()
        send_performance(midi_out, current)


def menu(midi_out):
    perf = Performance()
    print("\n  Starting from a blank performance. Generate one first.")

    while True:
        print("""
================================================================
  sn2_perf.py     Global ch 16 | parts 1-8 | 1024 programs
================================================================
  1  Generate    (new random, frozen parts survive)
  2  Mutate      (nudge unfrozen parts)
  3  Freeze      (lock/unlock parts)
  4  Send        (push current performance to the synth)
  5  Probe banks (verify the bank map - do this once)
  6  Save recipe
  7  Load recipe
  8  Blend two recipes
  9  Show current
  q  Quit
""")
        choice = input("> ").strip().lower()

        if choice == "1":
            generate(perf)
            perf.show()
            send_performance(midi_out, perf)

        elif choice == "2":
            raw = input("  Mutation amount 0.0 (tiny) - 1.0 (total) [0.3]: ").strip()
            try:
                amt = max(0.0, min(1.0, float(raw))) if raw else 0.3
            except ValueError:
                amt = 0.3
            mutate(perf, amt)
            perf.show()
            send_performance(midi_out, perf)

        elif choice == "3":
            toggle_freeze(perf)

        elif choice == "4":
            send_performance(midi_out, perf)

        elif choice == "5":
            probe(midi_out)

        elif choice == "6":
            name = input("  Name (Enter to keep auto name): ").strip()
            save_recipe(perf, name or None)
            print("""
  Recipe saved to disk. To make it permanent ON THE SYNTH:
    press Write, choose a slot in Performance bank C or D,
    name it, press Write again.
  When it asks whether to update all Programs used in the
  Performance, answer NO - otherwise it rewrites the source
  programs in your banks.
""")

        elif choice == "7":
            path = choose_recipe()
            if path:
                perf = load_recipe(path)
                perf.show()
                send_performance(midi_out, perf)

        elif choice == "8":
            result = do_blend(midi_out)
            if result:
                perf = result

        elif choice == "9":
            perf.show()

        elif choice in ("q", "quit", "exit"):
            return

        else:
            print("  ?")


def main():
    print(f"""
  sn2_perf.py

  Before you start:
    - Supernova II in PERFORMANCE mode
    - Global MIDI channel = {GLOBAL_MIDI_CHANNEL} (must be >8)
    - Parts 1-8 on MIDI channels 1-8 (Performance A126 does this)
    - sn2_gen.py can run alongside; nietzsche.py CANNOT
      (it sends its own Program Changes and will fight this)
""")
    midi_out = open_output()
    try:
        menu(midi_out)
    finally:
        midi_out.close_port()
        del midi_out
        print("\n  Port closed.")


if __name__ == "__main__":
    main()
