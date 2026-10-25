#!/usr/bin/env python3
"""
sn2_backup.py - SysEx backup & restore for the Novation Supernova II
xik6 / built with Claude

Companion to sn2_gen.py. Uses the same MIDI chain:
    Mac mini <-USB-> microKORG XL <-DIN-> Supernova II
Requires the RETURN path: SN2 MIDI Out -> Korg MIDI In.

WHY THIS DESIGN
---------------
The SN2 initiates its own bulk dumps from the front panel (Global menu ->
MIDI dump page). This script does NOT try to send a "please dump" request
message, because that request format is device-specific and getting it
wrong sends garbage into a synth you're about to reset. Instead:

    capture mode  -> script listens, you press Write on the synth,
                     script saves every SysEx byte it hears to a .syx file
    send mode     -> script streams a .syx file back, message by message,
                     with a safe inter-message delay

That is format-agnostic. It works whether the dump is one giant message
or 128 small ones, and it will still work if the OS version differs.

Install:  pip install python-rtmidi
Run:      python3 sn2_backup.py
"""

import os
import sys
import time
import glob
import argparse
from datetime import datetime

try:
    import rtmidi
except ImportError:
    sys.exit("Missing dependency. Run:  pip install python-rtmidi")

# --- CONFIG -----------------------------------------------------------------

BACKUP_DIR = os.path.expanduser("~/sn2_backups")

# Seconds of silence after the last SysEx byte before we call a dump finished.
IDLE_TIMEOUT = 4.0

# Delay between messages when sending back. The SN2 writes to flash as it
# receives; too fast and it drops banks silently. 0.12s is conservative.
SEND_DELAY = 0.12

# Absolute cap on a capture session, so a stuck stream can't run forever.
MAX_CAPTURE_SECONDS = 600

SYSEX_START = 0xF0
SYSEX_END = 0xF7

# --- PORT SELECTION ---------------------------------------------------------


def pick_port(midi, direction):
    """Show available ports and let the user choose one."""
    ports = midi.get_ports()
    if not ports:
        sys.exit(f"No MIDI {direction} ports found. Is the Korg plugged in?")

    print(f"\nAvailable MIDI {direction} ports:")
    for i, name in enumerate(ports):
        print(f"  [{i}] {name}")

    while True:
        raw = input(f"Select {direction} port number: ").strip()
        if raw.isdigit() and 0 <= int(raw) < len(ports):
            idx = int(raw)
            print(f"  -> using: {ports[idx]}")
            return idx
        print("  Not a valid port number.")


def open_input():
    midi_in = rtmidi.MidiIn()
    idx = pick_port(midi_in, "INPUT")
    midi_in.open_port(idx)
    # rtmidi drops SysEx by default. This is the line everything depends on.
    midi_in.ignore_types(sysex=False, timing=True, active_sense=True)
    return midi_in


def open_output():
    midi_out = rtmidi.MidiOut()
    idx = pick_port(midi_out, "OUTPUT")
    midi_out.open_port(idx)
    return midi_out


# --- SYSEX HELPERS ----------------------------------------------------------


def split_sysex(data):
    """Split a raw byte blob into individual F0...F7 messages."""
    messages = []
    current = None
    for byte in data:
        if byte == SYSEX_START:
            current = [SYSEX_START]
        elif current is not None:
            current.append(byte)
            if byte == SYSEX_END:
                messages.append(current)
                current = None
    if current is not None:
        print(f"  ! Warning: trailing {len(current)} bytes with no F7 terminator "
              f"- dump may be truncated.")
    return messages


def describe(messages):
    """Print a short summary so the user can sanity-check what was caught."""
    if not messages:
        return
    total = sum(len(m) for m in messages)
    sizes = {}
    for m in messages:
        sizes[len(m)] = sizes.get(len(m), 0) + 1
    print(f"  {len(messages)} SysEx message(s), {total} bytes total")
    for size, count in sorted(sizes.items()):
        print(f"    {count:>4} x {size} bytes")
    head = " ".join(f"{b:02X}" for b in messages[0][:8])
    print(f"    first message begins: {head} ...")


# --- CAPTURE ----------------------------------------------------------------


def capture(label):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    midi_in = open_input()

    print("\n" + "=" * 62)
    print("  CAPTURE MODE - listening for a SysEx dump")
    print("=" * 62)
    print("""
On the Supernova II:
  1. Global menu -> page through to the MIDI dump page
     ("Dump to MIDI" / "Send data:" depending on OS version)
  2. Select what you want dumped (a Program bank, a Performance bank,
     Global data, Arp patterns - one at a time is safest)
  3. Press Write to start the dump

I'll save everything I hear. Capture ends automatically after
{:.0f}s of silence. Ctrl-C to abort.
""".format(IDLE_TIMEOUT))
    input("Press Enter when you're ready, then trigger the dump on the synth... ")

    buf = bytearray()
    started = False
    last_byte_at = None
    session_start = time.time()
    last_tick = session_start

    print("\nListening", end="", flush=True)
    try:
        while True:
            msg = midi_in.get_message()
            if msg:
                data, _delta = msg
                if data and data[0] == SYSEX_START:
                    if not started:
                        print("\n  Dump detected - receiving", end="", flush=True)
                        started = True
                    buf.extend(data)
                    last_byte_at = time.time()
                    print(".", end="", flush=True)
                continue

            time.sleep(0.01)

            if started and last_byte_at and (time.time() - last_byte_at) > IDLE_TIMEOUT:
                break
            if not started and (time.time() - last_tick) > 2.0:
                # idle heartbeat so it doesn't look frozen
                last_tick = time.time()
                print(".", end="", flush=True)
            if (time.time() - session_start) > MAX_CAPTURE_SECONDS:
                print("\n  Hit the capture time limit.")
                break
    except KeyboardInterrupt:
        print("\n  Aborted by user.")
    finally:
        midi_in.close_port()
        del midi_in

    print()
    if not buf:
        print("\nNothing received.")
        print("Check: SN2 MIDI Out -> Korg MIDI In, and that the Korg is")
        print("actually forwarding DIN input to USB (not just USB to DIN).")
        return

    messages = split_sysex(bytes(buf))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
    path = os.path.join(BACKUP_DIR, f"{stamp}_{safe_label}.syx")

    with open(path, "wb") as f:
        f.write(buf)

    print(f"\nSaved: {path}")
    describe(messages)
    print("\nDump one bank per file. Label them as you go - the .syx itself")
    print("carries no human-readable name.")


# --- SEND -------------------------------------------------------------------


def choose_file():
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "*.syx")))
    if not files:
        sys.exit(f"No .syx files found in {BACKUP_DIR}")
    print("\nAvailable backups:")
    for i, p in enumerate(files):
        size = os.path.getsize(p)
        print(f"  [{i}] {os.path.basename(p)}  ({size} bytes)")
    while True:
        raw = input("Select file number: ").strip()
        if raw.isdigit() and 0 <= int(raw) < len(files):
            return files[int(raw)]
        print("  Not a valid file number.")


def send(path, delay):
    if path is None:
        path = choose_file()

    with open(path, "rb") as f:
        data = f.read()

    messages = split_sysex(data)
    if not messages:
        sys.exit("No complete SysEx messages in that file.")

    print(f"\nFile: {path}")
    describe(messages)

    est = len(messages) * delay
    print("\n" + "!" * 62)
    print("  THIS OVERWRITES MEMORY ON THE SYNTH.")
    print("!" * 62)
    print("""
On the Supernova II, before you continue:
  - Memory Protect must be OFF (Global menu, local control page)
  - Nothing else should be sending MIDI. Kill sn2_gen.py and
    nietzsche.py first - a stray Program Change mid-restore will
    land in the wrong place.
  - Don't touch the front panel while it's writing.
""")
    print(f"Estimated transfer time: {est:.1f}s")

    if input('Type "send" to proceed: ').strip().lower() != "send":
        print("Cancelled.")
        return

    midi_out = open_output()
    print()
    try:
        for i, m in enumerate(messages, 1):
            midi_out.send_message(m)
            print(f"\r  sent {i}/{len(messages)}", end="", flush=True)
            time.sleep(delay)
    except KeyboardInterrupt:
        print("\n\n  Aborted mid-transfer. The synth's memory is now in a")
        print("  PARTIAL state. Re-run the full send before using it.")
        return
    finally:
        midi_out.close_port()
        del midi_out

    print("\n\nTransfer complete.")
    print("Power cycle the SN2, or step to a new Program, to clear the buffers.")
    print("Then turn Memory Protect back ON.")


# --- VERIFY -----------------------------------------------------------------


def verify():
    """Inspect a saved file without touching the synth."""
    path = choose_file()
    with open(path, "rb") as f:
        data = f.read()
    messages = split_sysex(data)
    print(f"\n{path}")
    describe(messages)
    if not messages:
        print("  No valid SysEx found - this file is not usable.")


# --- MENU -------------------------------------------------------------------


def menu():
    while True:
        print("""
================================================================
  sn2_backup.py - Supernova II SysEx backup / restore
================================================================
  1  Capture a dump from the synth  (safe, read-only)
  2  Send a backup back to the synth  (OVERWRITES MEMORY)
  3  Inspect a saved .syx file  (safe, offline)
  q  Quit
""")
        choice = input("> ").strip().lower()
        if choice == "1":
            label = input("Label for this dump (e.g. prog-bank-A): ").strip() or "dump"
            capture(label)
        elif choice == "2":
            send(None, SEND_DELAY)
        elif choice == "3":
            verify()
        elif choice in ("q", "quit", "exit"):
            return
        else:
            print("  ?")


def main():
    ap = argparse.ArgumentParser(description="Supernova II SysEx backup/restore")
    ap.add_argument("--capture", metavar="LABEL",
                    help="capture a dump straight away, with this label")
    ap.add_argument("--send", metavar="FILE",
                    help="send a specific .syx file")
    ap.add_argument("--delay", type=float, default=SEND_DELAY,
                    help=f"seconds between messages when sending "
                         f"(default {SEND_DELAY})")
    args = ap.parse_args()

    if args.capture:
        capture(args.capture)
    elif args.send:
        send(args.send, args.delay)
    else:
        menu()


if __name__ == "__main__":
    main()
