#!/usr/bin/env python3
"""
reprogram_watcher.py — continuously watches for incoming "Single performance"
SysEx dumps from the Supernova II, applies the confirmed reprogramming fixes
to all 8 parts, and immediately sends the corrected dump straight back.

Fixes applied to every part (1-8) of whatever performance comes in:
  - MIDI channel  -> set to the part's own number (Part N -> channel N)
  - Arp On        -> off  (clear bit 0x10 of the byte at 3190 + 44*(N-1))
  - Arp Latch     -> off  (clear bit 0x01 of the byte at part_block_start+256)
  - Prog Level    -> 64   (byte at part_block_start+245)

Offsets confirmed via controlled diff testing on Performance C000 (see prior
session history for the derivation).

Usage:
  On the Supernova II: Global -> Sysex transmission -> "Single performance",
  Memory Protect = Off, Sysex reception = "Normal (Rx as sent)".
  Then for each performance: select it, press MIDI to send. This script
  catches it, fixes it, and sends the corrected version straight back to the
  same slot. Save/Write it on the hardware, move to the next performance,
  repeat.

Ctrl+C to stop.

Install:  pip install python-rtmidi
Run:      python3 reprogram_watcher.py
"""
import rtmidi
import time
import sys
import threading

EXPECTED_MSG_LENGTHS = [296, 98] * 8 + [381, 25, 473]   # 19 messages, 4031 bytes total
EXPECTED_TOTAL_LEN = sum(EXPECTED_MSG_LENGTHS)

CHANNEL_BASE = 3182
CHANNEL_STRIDE = 44
ARPON_BASE = 3190
ARPON_STRIDE = 44
ARPON_BIT = 0x10

PART_BLOCK_STRIDE = 394
LATCH_OFFSET = 256
LATCH_BIT = 0x01
LEVEL_OFFSET = 245
LEVEL_VALUE = 64

SEND_DELAY = 0.1  # seconds between outgoing messages

running = True
midi_in = None
midi_out = None

buf = bytearray()
msg_lengths = []
lock = threading.Lock()
processed_count = 0


def select_ports():
    global midi_in, midi_out
    midi_in = rtmidi.MidiIn()
    midi_in.ignore_types(sysex=False, timing=True, active_sense=True)
    midi_out = rtmidi.MidiOut()

    in_ports = midi_in.get_ports()
    out_ports = midi_out.get_ports()

    in_idx = next((i for i, p in enumerate(in_ports) if 'MIDI IN' in p), None)
    out_idx = next((i for i, p in enumerate(out_ports) if 'MIDI OUT' in p), None)

    if in_idx is None or out_idx is None:
        print("Could not auto-detect MIDI IN/OUT ports.")
        print("IN ports:", in_ports)
        print("OUT ports:", out_ports)
        sys.exit(1)

    print(f"Listening on:  {in_ports[in_idx]}")
    print(f"Sending on:    {out_ports[out_idx]}")
    midi_in.open_port(in_idx)
    midi_out.open_port(out_idx)


def fix_performance(data):
    d = bytearray(data)
    for n in range(1, 9):
        ch_off = CHANNEL_BASE + CHANNEL_STRIDE * (n - 1)
        d[ch_off] = n - 1

        arpon_off = ARPON_BASE + ARPON_STRIDE * (n - 1)
        d[arpon_off] &= ~ARPON_BIT

        part_start = (n - 1) * PART_BLOCK_STRIDE
        latch_off = part_start + LATCH_OFFSET
        d[latch_off] &= ~LATCH_BIT

        level_off = part_start + LEVEL_OFFSET
        d[level_off] = LEVEL_VALUE
    return bytes(d)


def send_fixed(data):
    messages = []
    i = 0
    while i < len(data):
        j = data.index(0xF7, i)
        messages.append(data[i:j + 1])
        i = j + 1
    for m in messages:
        midi_out.send_message(list(m))
        time.sleep(SEND_DELAY)


def on_message_complete():
    global processed_count, buf, msg_lengths
    data = bytes(buf)
    buf = bytearray()
    msg_lengths = []

    if len(data) != EXPECTED_TOTAL_LEN:
        print(f"\n[skip] received {len(data)} bytes, expected {EXPECTED_TOTAL_LEN} "
              f"(not a single-performance dump, or a transmission error) — ignoring.")
        return

    try:
        fixed = fix_performance(data)
    except Exception as e:
        print(f"\n[error] failed to fix performance: {e}")
        return

    send_fixed(fixed)
    processed_count += 1
    print(f"\n[{processed_count}] performance received, fixed, and sent back.  "
          f"Save/Write it, then move to the next one whenever ready.")


def midi_callback(event, data=None):
    msg, deltatime = event
    if not msg:
        return
    with lock:
        if msg[0] == 0xF0:
            buf.extend(msg)
            msg_lengths.append(len(msg))
            if msg_lengths == EXPECTED_MSG_LENGTHS[:len(msg_lengths)]:
                if len(msg_lengths) == len(EXPECTED_MSG_LENGTHS):
                    on_message_complete()
            else:
                if msg_lengths[:-1] != EXPECTED_MSG_LENGTHS[:len(msg_lengths) - 1]:
                    buf.clear()
                    buf.extend(msg)
                    msg_lengths.clear()
                    msg_lengths.append(len(msg))


def main():
    select_ports()
    midi_in.set_callback(midi_callback)

    print("\nWatching for incoming 'Single performance' dumps.")
    print("On the synth: select a performance, Sysex transmission = 'Single performance', press MIDI.")
    print("Each one gets fixed and sent straight back automatically. Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        midi_in.close_port()
        midi_out.close_port()
        print(f"\nStopped. {processed_count} performance(s) fixed this session.")


if __name__ == '__main__':
    main()
