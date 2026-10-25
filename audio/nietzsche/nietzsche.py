#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nietzsche.py  --  Wahnzettel sequencer for the Novation Supernova II

Turin, 31 Dec 1888 - 6 Jan 1889.  Twenty-eight notes, eighteen recipients,
four days.  Ten signed Dionysos, ten signed Der Gekreuzigte, three unsigned,
one 'Astu', two lost, and at the end the given name comes back.

That signature track is the score.  It is neither random nor pseudo-random:
it is a found sequence with real internal structure.  This script walks it
in order, one section per note, driving 8 Performance Parts as independent
generative threads -- same event, different register, different pulse.

  D  Dionysos          bright mode, mid density
  G  Der Gekreuzigte   dark mode, slow, long durations
  N  Nietzsche/Caesar  chromatic, restless, high density
  ND transitional      the Mendes drafts, name being deleted in real time
  AN Astu -> Nietzsche one-shot cluster, then chromatic  (final event only)
  O  unsigned          silence (all three are Cosima, 3 Jan)
  ?  lost              notes generated, nothing transmitted

Every phrase decays rather than cadences.  Where the source note ends in an
ellipsis the section boundary is NOT cut off -- notes hang across it.

--------------------------------------------------------------------------
SUPERNOVA II SAFETY -- READ THIS BEFORE RUNNING
--------------------------------------------------------------------------
The SN2 reallocates DSP when a part loads a new patch.  Eight simultaneous
program changes, or rapid repeated ones, will glitch voices and can hang the
machine.  This script therefore:

  * never lets two program changes overlap        (global gate + lock)
  * enforces PC_MIN_GLOBAL_GAP between any two    (default 6.0 s)
  * enforces PC_PART_COOLDOWN per part            (default 90.0 s)
  * changes at most MAX_PC_PER_SECTION parts      (default 3)
  * silences a part, waits, changes, waits again  before it plays anything
  * skips the change entirely if the patch is already loaded

Result: on a ~35 minute run each part changes patch a handful of times, never
in lockstep, never while sounding.

*** MIDI CHANNEL WARNING ***
On the SN2, a Program Change sent on a PART's channel changes that part's
patch.  A Program Change sent on the GLOBAL / Performance control channel
changes the whole Performance.  Make sure PART_CHANNELS below does not
include your global channel, or this will flip Performances instead of
patches -- which is exactly the failure you asked me to avoid.

Check on the synth:  Globals > MIDI > Program Change Receive = On,
and note your Performance control channel.
--------------------------------------------------------------------------

Usage
    python3 nietzsche.py --list-ports
    python3 nietzsche.py --dry-run --speed 60
    python3 nietzsche.py --port "Supernova II"
    python3 nietzsche.py --port "Supernova II" --speed 2 --from 13
    python3 nietzsche.py --port "Supernova II" --panic

Requires:  pip3 install mido python-rtmidi
Ctrl-C at any time sends a full panic (all notes off, all sound off, 8 parts).
"""

import argparse
import math
import random
import signal
import sys
import threading
import time

try:
    import mido
except ImportError:
    sys.exit("need mido:  pip3 install mido python-rtmidi")


# =========================================================================
# EDIT THIS BLOCK FOR YOUR RIG
# =========================================================================

# MIDI channels for the 8 Performance Parts, 1-16 as shown on the synth.
# MUST NOT contain your Performance/global control channel.
PART_CHANNELS = [1, 2, 3, 4, 5, 6, 7, 8]

# Patch pools, per signature family.  Program numbers as displayed on the
# SN2 (1-128); the script converts to 0-127 for you.  Pick sounds that sit
# together -- these are placeholders, replace with your own.
#
# Dionysos  -> bright, moving, anything with motion in it
# Gekreuzigt-> dark, slow, drones and pads
# Nietzsche -> unstable, chromatic-friendly, bells/plucks/noise
# Astu      -> ONE patch, heard once in the whole piece, at the very end
PATCH_POOLS = {
    "D": [1, 5, 9, 14, 18, 23, 27, 31],
    "G": [33, 37, 41, 46, 50, 55, 59, 63],
    "N": [65, 70, 74, 79, 83, 88, 92, 97],
    "A": [100],
}

# Bank select.  None = don't send bank select at all (safest, stays in the
# currently selected bank).  Set to ints 0-127 if you need to reach a bank.
BANK_MSB = None      # CC 0
BANK_LSB = None      # CC 32

# --- program change safety ------------------------------------------------
PC_MIN_GLOBAL_GAP = 6.0     # seconds between ANY two program changes
PC_PART_COOLDOWN  = 90.0    # seconds before the same part may change again
PC_PRE_SETTLE     = 0.15    # after silencing a part, before the PC
PC_POST_SETTLE    = 0.35    # after the PC, before the part plays again
MAX_PC_PER_SECTION = 3      # never re-patch more than this many parts at once

# --- musical ------------------------------------------------------------
ROOT_PC        = 2          # 0=C, 2=D ...  fixed root; the mode moves, not the key
BASE_SECONDS   = 16.0       # section length = BASE + LOG_GAIN * ln(1 + words)
LOG_GAIN       = 7.0
GLOBAL_VEL     = 1.0        # 0.0-1.5 velocity trim
MAX_SOUNDING_PER_PART = 4   # polyphony cap per part, protects SN2 voice pool

# Per-part register (lo, hi) and pulse multiplier.  The pulses are sqrt2,
# phi, 1+sqrt2, e, pi -- mutually irrational, so the parts never lock.
PART_REGISTERS = [
    (28, 52), (33, 57), (40, 64), (45, 69),
    (52, 76), (57, 81), (64, 88), (69, 93),
]
PART_PULSE = [1.0, 1.19, 1.414, 1.618, 2.0, 2.414, 2.718, 3.142]

# =========================================================================
# THE SCORE -- 28 events, in order
# idx, date, recipient, sig_code, words, ends_ellipsis
# =========================================================================

WAHNZETTEL = [
    ( 1, "1888-12-31", "Strindberg",            "N",  40, False),
    ( 2, "1889-01-01", "Mendes draft I",        "ND", 32, False),
    ( 3, "1889-01-01", "Mendes draft II",       "ND", 34, False),
    ( 4, "1889-01-01", "Mendes final",          "D",  31, True ),
    ( 5, "1889-01-01", "Bourdeau",              "N",  60, True ),
    ( 6, "1889-01-01", "Bourdeau proclamation", "?",   0, False),
    ( 7, "1889-01-01", "Bourdeau italian note", "?",   9, False),
    ( 8, "1889-01-02", "Strindberg",            "G",   6, True ),
    ( 9, "1889-01-03", "Meta von Salis",        "G",  42, False),
    (10, "1889-01-03", "Cosima I",              "O",  17, True ),
    (11, "1889-01-03", "Cosima II",             "O", 104, True ),
    (12, "1889-01-03", "Cosima III",            "O",  17, False),
    (13, "1889-01-03", "Cosima IV",             "D",   4, False),
    (14, "1889-01-04", "Georg Brandes",         "G",  25, True ),
    (15, "1889-01-04", "Hans von Buelow",       "D",  48, True ),
    (16, "1889-01-04", "Burckhardt I",          "D",  54, True ),
    (17, "1889-01-04", "Paul Deussen",          "D",  41, False),
    (18, "1889-01-04", "Peter Gast",            "G",  15, False),
    (19, "1889-01-04", "Umberto I",             "G",  22, False),
    (20, "1889-01-04", "Cardinal Mariani",      "G",  21, True ),
    (21, "1889-01-04", "Haus Baden",            "G",  38, True ),
    (22, "1889-01-04", "Malwida von Meysenbug", "G",  64, False),
    (23, "1889-01-04", "Franz und Ida Overbeck","D",  44, True ),
    (24, "1889-01-04", "Den erlauchten Polen",  "G",  32, True ),
    (25, "1889-01-04", "Erwin Rohde",           "D",  43, True ),
    (26, "1889-01-04", "Carl Spitteler",        "D",  17, True ),
    (27, "1889-01-04", "Heinrich Wiener",       "D",  44, True ),
    (28, "1889-01-06", "Burckhardt II",         "AN",600, True ),
]

# pitch-class sets, relative to ROOT_PC
PROFILES = {
    # bright: lydian with a raised 5th leaning whole-tone
    "D":  dict(scale=[0, 2, 4, 6, 7, 9, 11], density=0.55,
               dur=(0.35, 1.9), vel=(58, 100), patch="D"),
    # dark: phrygian, minor-second heavy, slow
    "G":  dict(scale=[0, 1, 3, 5, 7, 8, 10], density=0.34,
               dur=(1.1, 4.6), vel=(42, 84),  patch="G"),
    # restless: full chromatic, short, high density
    "N":  dict(scale=list(range(12)),         density=0.66,
               dur=(0.18, 0.95), vel=(70, 110), patch="N"),
    # transitional: the name being deleted across three drafts
    "ND": dict(scale=[0, 2, 3, 4, 6, 7, 9, 11], density=0.48,
               dur=(0.3, 1.6), vel=(60, 98),  patch="N"),
    # terminal: Astu one-shot, then chromatic
    "AN": dict(scale=list(range(12)),         density=0.60,
               dur=(0.25, 2.4), vel=(64, 106), patch="N"),
    # unsigned -> silence
    "O":  dict(scale=[], density=0.0, dur=(1, 1), vel=(0, 0), patch=None),
    # lost -> generated, never transmitted
    "?":  dict(scale=[0, 5, 7], density=0.4, dur=(0.5, 2.0),
               vel=(50, 80), patch=None),
}

SIG_NAME = {
    "D": "Dionysos", "G": "Der Gekreuzigte", "N": "Nietzsche / Caesar",
    "ND": "Nietzsche -> Dionysos", "AN": "Astu -> Nietzsche",
    "O": "unsigned", "?": "lost",
}


# =========================================================================
# TRANSPORT
# =========================================================================

class Out:
    """Thread-safe MIDI sender with a hard global gate on program changes."""

    def __init__(self, port, dry_run=False, verbose=False):
        self.port = port
        self.dry_run = dry_run
        self.verbose = verbose
        self._lock = threading.Lock()      # serialises every byte we send
        self._pc_gate = threading.Lock()   # only one program change in flight
        self._last_pc = 0.0
        self.pc_count = 0
        self.pc_times = []                 # for the gap audit
        self.aborting = False              # set on shutdown; kills pending PCs

    def _send(self, msg):
        with self._lock:
            if self.dry_run:
                if self.verbose or msg.type in ("program_change",):
                    print("    tx %s" % msg)
            else:
                self.port.send(msg)

    def note_on(self, ch, note, vel):
        self._send(mido.Message("note_on", channel=ch - 1,
                                note=note, velocity=vel))

    def note_off(self, ch, note):
        self._send(mido.Message("note_off", channel=ch - 1,
                                note=note, velocity=0))

    def cc(self, ch, control, value):
        self._send(mido.Message("control_change", channel=ch - 1,
                                control=control, value=value))

    def silence_channel(self, ch):
        self.cc(ch, 64, 0)     # sustain off
        self.cc(ch, 120, 0)    # all sound off
        self.cc(ch, 123, 0)    # all notes off

    def panic(self):
        for ch in PART_CHANNELS:
            self.silence_channel(ch)
        print("  [panic] all sound off on channels %s" %
              ",".join(str(c) for c in PART_CHANNELS))

    def pc_slot(self):
        """
        Context manager. Acquires the one global program-change slot and
        enforces PC_MIN_GLOBAL_GAP before handing control back. Only one
        part is ever inside this at a time, machine-wide.
        """
        return _PCSlot(self)

    def raw_program_change(self, ch, program_1based, part_label=""):
        """Only ever called from inside a pc_slot."""
        if BANK_MSB is not None:
            self.cc(ch, 0, BANK_MSB)
        if BANK_LSB is not None:
            self.cc(ch, 32, BANK_LSB)
        self._send(mido.Message("program_change", channel=ch - 1,
                                program=(program_1based - 1) & 0x7F))
        self.pc_times.append(time.time())
        self.pc_count += 1
        print("  [patch] %-8s ch%-2d -> program %d" %
              (part_label, ch, program_1based))


class _PCSlot(object):
    def __init__(self, out):
        self.out = out

    def __enter__(self):
        self.out._pc_gate.acquire()
        wait = PC_MIN_GLOBAL_GAP - (time.time() - self.out._last_pc)
        if wait > 0:
            time.sleep(wait)
        return self

    def __exit__(self, *exc):
        self.out._last_pc = time.time()
        self.out._pc_gate.release()
        return False


# =========================================================================
# PART
# =========================================================================

class Part(threading.Thread):
    """One Performance Part. Independent generative thread."""

    def __init__(self, index, out, stop_evt, speed=1.0):
        threading.Thread.__init__(self, daemon=True)
        self.index = index
        self.channel = PART_CHANNELS[index]
        self.lo, self.hi = PART_REGISTERS[index]
        self.pulse = PART_PULSE[index]
        self.out = out
        self.stop_evt = stop_evt
        self.speed = speed
        self.label = "part%d" % (index + 1)

        self.rng = random.Random(0x4E1E + index * 977)
        self.profile = PROFILES["O"]
        self.transmit = False
        self.muted = True
        self._patching = threading.Event()   # held while a PC is actually landing
        self._pending  = threading.Event()   # held while a PC is queued or landing
        self.current_program = None
        self.last_pc_time = 0.0

        self.sounding = {}                 # note -> release deadline
        self._snd_lock = threading.Lock()

    # -- patch handling ---------------------------------------------------

    def wants_program(self, family):
        if family is None:
            return None
        pool = PATCH_POOLS.get(family)
        if not pool:
            return None
        return pool[self.index % len(pool)]

    def may_change_patch(self, family, now):
        prog = self.wants_program(family)
        if prog is None:
            return False
        if prog == self.current_program:
            return False
        if now - self.last_pc_time < PC_PART_COOLDOWN:
            return False
        return True

    def apply_patch(self, family, still_current):
        """
        Runs on its own thread. Waits for the global slot, then RE-CHECKS
        that this patch family is still the one the piece wants. If the
        music has moved on while it was queued, the change is abandoned --
        this is what stops stale program changes piling up on the SN2.
        """
        if self._pending.is_set():
            return                      # already queued, don't double up
        self._pending.set()
        try:
            prog = self.wants_program(family)
            if prog is None or prog == self.current_program:
                return
            if self.out.aborting or self.stop_evt.is_set():
                return

            with self.out.pc_slot():
                if self.out.aborting or self.stop_evt.is_set():
                    return
                if not still_current(family):
                    print("  [patch] %-8s skipped, section moved on" % self.label)
                    return

                self._patching.set()
                try:
                    self.kill_sounding()
                    self.out.silence_channel(self.channel)
                    time.sleep(PC_PRE_SETTLE)
                    self.out.raw_program_change(self.channel, prog, self.label)
                    time.sleep(PC_POST_SETTLE)
                    self.current_program = prog
                    self.last_pc_time = time.time()
                finally:
                    self._patching.clear()
        finally:
            self._pending.clear()

    # -- note handling ----------------------------------------------------

    def kill_sounding(self):
        with self._snd_lock:
            notes = list(self.sounding.keys())
            self.sounding.clear()
        for n in notes:
            self.out.note_off(self.channel, n)

    def release_due(self, now, force=False):
        with self._snd_lock:
            due = [n for n, t in self.sounding.items() if force or t <= now]
            for n in due:
                self.sounding.pop(n, None)
        for n in due:
            self.out.note_off(self.channel, n)

    def set_section(self, sig_code, transmit):
        self.profile = PROFILES[sig_code]
        self.transmit = transmit
        self.muted = False

    def fire_astu(self):
        """The one-shot. Once in the whole piece, never again."""
        prog = PATCH_POOLS["A"][0]
        with self.out.pc_slot():
            self._patching.set()
            try:
                self.kill_sounding()
                self.out.silence_channel(self.channel)
                time.sleep(PC_PRE_SETTLE)
                self.out.raw_program_change(self.channel, prog, self.label + "*")
                time.sleep(PC_POST_SETTLE)
                self.current_program = prog
                self.last_pc_time = time.time()
                root = ROOT_PC + 12 * (self.lo // 12)
                for off in (0, 1, 6):
                    n = self._clamp(root + off + 12)
                    self.out.note_on(self.channel, n, int(96 * GLOBAL_VEL))
                    with self._snd_lock:
                        self.sounding[n] = time.time() + 9.0
            finally:
                self._patching.clear()

    def _clamp(self, n):
        while n < self.lo:
            n += 12
        while n > self.hi:
            n -= 12
        return max(0, min(127, n))

    def _pick_note(self):
        scale = self.profile["scale"]
        if not scale:
            return None
        deg = self.rng.choice(scale)
        octv = self.rng.randrange(self.lo // 12, (self.hi // 12) + 1)
        return self._clamp(ROOT_PC + deg + 12 * octv)

    # -- loop -------------------------------------------------------------

    def run(self):
        while not self.stop_evt.is_set():
            now = time.time()
            self.release_due(now)

            if (self.muted or self._patching.is_set()
                    or self.profile["density"] <= 0.0):
                time.sleep(0.05)
                continue

            step = (0.25 * self.pulse) / self.speed
            if self.rng.random() < self.profile["density"]:
                with self._snd_lock:
                    room = len(self.sounding) < MAX_SOUNDING_PER_PART
                if room:
                    note = self._pick_note()
                    if note is not None:
                        lo_v, hi_v = self.profile["vel"]
                        vel = int(self.rng.randint(lo_v, hi_v) * GLOBAL_VEL)
                        vel = max(1, min(127, vel))
                        d_lo, d_hi = self.profile["dur"]
                        dur = self.rng.uniform(d_lo, d_hi) / self.speed
                        if self.transmit:
                            self.out.note_on(self.channel, note, vel)
                            with self._snd_lock:
                                self.sounding[note] = time.time() + dur
            self.stop_evt.wait(step)

        self.release_due(0, force=True)


# =========================================================================
# CONDUCTOR
# =========================================================================

def section_seconds(words, speed):
    return (BASE_SECONDS + LOG_GAIN * math.log(1.0 + words)) / speed


def run_piece(out, speed=1.0, start_idx=1, stop_idx=28):
    stop_evt = threading.Event()
    parts = [Part(i, out, stop_evt, speed) for i in range(8)]

    # the conductor's view of "what patch family does the piece want NOW".
    # queued program changes revalidate against this before they land.
    state = {"family": None}

    def still_current(f):
        return state["family"] == f

    def handler(sig, frame):
        print("\n  [stop] shutting down")
        out.aborting = True
        stop_evt.set()
        time.sleep(0.4)
        for p in parts:
            p.kill_sounding()
        out.panic()
        sys.exit(0)

    signal.signal(signal.SIGINT, handler)

    for p in parts:
        p.start()

    print("=" * 66)
    print("  NIETZSCHE.PY  --  Wahnzettel, Turin, 1888-89")
    print("  8 parts on MIDI channels %s" %
          ",".join(str(c) for c in PART_CHANNELS))
    print("  speed x%.1f   events %d-%d" % (speed, start_idx, stop_idx))
    total = sum(section_seconds(w, speed)
                for (i, _, _, _, w, _) in WAHNZETTEL
                if start_idx <= i <= stop_idx)
    print("  projected length %.1f minutes" % (total / 60.0))
    if speed > 4.0:
        print("  note: patch-change safety timing is REAL time and does not")
        print("        compress with --speed. At high speed the repatching")
        print("        will lag behind the sections. Fine for testing.")
    print("=" * 66)

    t_start = time.time()
    prev_family = None

    for (idx, date, who, sig, words, ellipsis) in WAHNZETTEL:
        if idx < start_idx or idx > stop_idx:
            continue
        if stop_evt.is_set():
            break

        prof = PROFILES[sig]
        family = prof["patch"]
        dur = section_seconds(words, speed)
        transmit = (sig != "?")

        print("\n[%2d] %s  %s" % (idx, date, who))
        print("     signature: %-20s  %3d words  %s" %
              (SIG_NAME[sig], words, "..." if ellipsis else "|"))
        print("     section: %.1fs   %s" %
              (dur, "TRANSMIT" if transmit else "generated, not transmitted"))

        for p in parts:
            p.set_section(sig, transmit)

        # ---- patch changes: staggered, capped, revalidated, never overlapping
        if family:
            state["family"] = family
        if family and family != prev_family:
            now = time.time()
            due = [p for p in parts if p.may_change_patch(family, now)]
            due.sort(key=lambda p: p.last_pc_time)
            due = due[:MAX_PC_PER_SECTION]
            if due:
                print("     repatching %d part(s), >=%.1fs apart, revalidated"
                      % (len(due), PC_MIN_GLOBAL_GAP))
                for p in due:
                    if stop_evt.is_set():
                        break
                    threading.Thread(target=p.apply_patch,
                                     args=(family, still_current),
                                     daemon=True).start()
                    time.sleep(0.05)
        prev_family = family or prev_family

        # ---- the one-shot -----------------------------------------------
        if sig == "AN":
            print("     ASTU -- one-shot, fires once, never again")
            parts[3].fire_astu()

        stop_evt.wait(dur)

        # ---- ellipsis rule ----------------------------------------------
        # a note that ends in "..." does not get cut off at the boundary
        if not ellipsis:
            for p in parts:
                p.release_due(0, force=True)
            print("     | boundary: hard release")
        else:
            print("     ... boundary: notes hang across")

    out.aborting = True
    elapsed = (time.time() - t_start) / 60.0
    print("\n" + "=" * 66)
    print("  end.  %.1f minutes, %d program changes total" % (elapsed, out.pc_count))
    if len(out.pc_times) > 1:
        gaps = [out.pc_times[i] - out.pc_times[i - 1]
                for i in range(1, len(out.pc_times))]
        print("  program change gaps: min %.2fs  mean %.2fs  (floor %.1fs)"
              % (min(gaps), sum(gaps) / len(gaps), PC_MIN_GLOBAL_GAP))
        if min(gaps) < PC_MIN_GLOBAL_GAP - 0.05:
            print("  !! GAP FLOOR VIOLATED -- do not run this on the synth")
        else:
            print("  gap floor respected. safe for the SN2.")
        per_part = out.pc_count / 8.0
        print("  average %.1f patch changes per part over the run" % per_part)
    print("=" * 66)

    stop_evt.set()
    time.sleep(0.5)
    for p in parts:
        p.kill_sounding()
    out.panic()


# =========================================================================

def main():
    ap = argparse.ArgumentParser(description="Wahnzettel sequencer for the Supernova II")
    ap.add_argument("--port", help="MIDI output port name (substring match)")
    ap.add_argument("--list-ports", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="no MIDI out, print the structure instead")
    ap.add_argument("--verbose", action="store_true",
                    help="in dry-run, also print every note_on")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="time multiplier, >1 is faster")
    ap.add_argument("--from", dest="start", type=int, default=1)
    ap.add_argument("--to", dest="stop", type=int, default=28)
    ap.add_argument("--panic", action="store_true",
                    help="send all-notes-off on all 8 part channels and exit")
    args = ap.parse_args()

    def list_ports():
        try:
            names = mido.get_output_names()
        except Exception as e:
            print("could not open the MIDI system: %s" % e)
            print("on macOS this normally just works; check the SN2 is")
            print("connected and visible in Audio MIDI Setup.")
            return []
        print("MIDI outputs:")
        for n in names:
            print("  " + n)
        if not names:
            print("  (none found)")
        return names

    if args.list_ports:
        list_ports()
        return

    # sanity check on config
    if len(set(PART_CHANNELS)) != 8:
        sys.exit("PART_CHANNELS must be 8 distinct channels")
    if len(PART_REGISTERS) != 8 or len(PART_PULSE) != 8:
        sys.exit("PART_REGISTERS and PART_PULSE must both have 8 entries")

    if args.dry_run:
        out = Out(None, dry_run=True, verbose=args.verbose)
    else:
        names = list_ports()
        if not args.port:
            sys.exit("\nchoose one with --port, or use --dry-run")
        match = [n for n in names if args.port.lower() in n.lower()]
        if not match:
            sys.exit("no output port matching %r" % args.port)
        print("opening %r" % match[0])
        out = Out(mido.open_output(match[0]))

    if args.panic:
        out.panic()
        return

    run_piece(out, speed=max(0.05, args.speed),
              start_idx=args.start, stop_idx=args.stop)


if __name__ == "__main__":
    main()
