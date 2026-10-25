#!/usr/bin/env python3
"""
sn2_chaos8.py — 8-channel independent random note + mod wheel generator
for Novation Supernova II (or anything else listening on MIDI channels 1-8).

Each of the 8 channels independently and continuously:
  - fires a note at a random interval (NOTE_GAP_MIN - NOTE_GAP_MAX seconds apart)
  - each note has a random length (NOTE_DUR_MIN - NOTE_DUR_MAX seconds)
  - random pitch and velocity
  - separately, sends random Mod Wheel (CC1) changes on a slower,
    independent random interval (MODWHEEL_GAP_MIN - MODWHEEL_GAP_MAX seconds),
    each with a random value 0-127

Ctrl+C to stop (sends All Notes Off on all 8 channels first).

Install:  pip install python-rtmidi
Run:      python3 sn2_chaos8.py
"""
import rtmidi
import time
import random
import threading
import sys

NUM_CHANNELS = 8

NOTE_GAP_MIN, NOTE_GAP_MAX = 0.01, 2.0     # seconds between note triggers
NOTE_DUR_MIN, NOTE_DUR_MAX = 0.1, 3.0      # note length in seconds
PITCH_MIN, PITCH_MAX = 12, 108             # C0 - C8
VEL_MIN, VEL_MAX = 1, 127

MODWHEEL_CC = 1
MODWHEEL_GAP_MIN, MODWHEEL_GAP_MAX = 3.0, 15.0   # slower, independent timescale

running = True
midi_out = None


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


def send_note_on(ch, note, vel):
    midi_out.send_message([0x90 | ch, note, vel])


def send_note_off(ch, note):
    midi_out.send_message([0x80 | ch, note, 0])


def send_cc(ch, cc, val):
    midi_out.send_message([0xB0 | ch, cc, val])


def all_notes_off():
    for ch in range(NUM_CHANNELS):
        midi_out.send_message([0xB0 | ch, 123, 0])


def note_loop(ch):
    while running:
        note = random.randint(PITCH_MIN, PITCH_MAX)
        vel = random.randint(VEL_MIN, VEL_MAX)
        dur = random.uniform(NOTE_DUR_MIN, NOTE_DUR_MAX)
        send_note_on(ch, note, vel)

        off_timer = threading.Timer(dur, send_note_off, args=[ch, note])
        off_timer.daemon = True
        off_timer.start()

        gap = random.uniform(NOTE_GAP_MIN, NOTE_GAP_MAX)
        time.sleep(gap)


def modwheel_loop(ch):
    while running:
        gap = random.uniform(MODWHEEL_GAP_MIN, MODWHEEL_GAP_MAX)
        time.sleep(gap)
        if not running:
            break
        val = random.randint(0, 127)
        send_cc(ch, MODWHEEL_CC, val)


def main():
    global running
    select_port()
    print(f"\nRunning {NUM_CHANNELS} channels of random notes + mod wheel. Ctrl+C to stop.\n")

    threads = []
    for ch in range(NUM_CHANNELS):
        t1 = threading.Thread(target=note_loop, args=[ch], daemon=True)
        t2 = threading.Thread(target=modwheel_loop, args=[ch], daemon=True)
        threads += [t1, t2]
        t1.start()
        t2.start()

    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        running = False
        time.sleep(0.05)
        all_notes_off()
        time.sleep(0.15)
        midi_out.close_port()
        print("\nStopped. All notes off.")


if __name__ == '__main__':
    main()
