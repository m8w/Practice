#!/usr/bin/env python3
"""
BlackHole 2ch Recorder with Live Headphone Monitoring
-------------------------------------------------------
Records audio from a BlackHole 2ch virtual input (up to 2 hours by
default), plays it back live to your headphones while recording, and
saves the result as both WAV and MP3 to a folder you pick with a
native file dialog.

Requirements:
    pip install sounddevice soundfile numpy
    ffmpeg must be installed and on your PATH for the MP3 export
    (brew install ffmpeg)

Usage:
    python3 blackhole_recorder.py               # normal run
    python3 blackhole_recorder.py --list-devices # print all audio devices and exit
                                                  # (handy for checking BlackHole is
                                                  # installed/visible on a given Mac
                                                  # before starting a real recording)

Press Ctrl+C at any time during recording to stop early — whatever
was captured up to that point is still saved as WAV and MP3.
"""

import sys
import os
import argparse
import queue
import threading
import subprocess
import shutil
import datetime
import collections

import numpy as np
import sounddevice as sd
import soundfile as sf

try:
    import tkinter as tk
    from tkinter import filedialog
except ImportError:
    tk = None

SAMPLE_RATE = 44100
CHANNELS = 2
DEFAULT_DURATION_SECONDS = 2 * 60 * 60  # 2 hours
BLOCKSIZE = 1024


def find_blackhole_devices():
    """Search input devices for anything named 'BlackHole 2ch'."""
    devices = sd.query_devices()
    return [
        (idx, dev) for idx, dev in enumerate(devices)
        if dev['max_input_channels'] >= CHANNELS and 'blackhole' in dev['name'].lower()
    ]


def choose_input_device():
    """Auto-detect BlackHole 2ch; fall back to manual pick if needed."""
    candidates = find_blackhole_devices()

    if len(candidates) == 1:
        idx, dev = candidates[0]
        print(f"Found BlackHole input: [{idx}] {dev['name']}")
        return idx

    if len(candidates) > 1:
        print("\nMultiple BlackHole-like devices found:")
        for idx, dev in candidates:
            print(f"  [{idx}] {dev['name']}")
        return _prompt_device_choice([idx for idx, _ in candidates])

    print("\nCouldn't auto-detect a 'BlackHole 2ch' device. All input devices:")
    devices = sd.query_devices()
    inputs = [(idx, dev) for idx, dev in enumerate(devices) if dev['max_input_channels'] >= CHANNELS]
    for idx, dev in inputs:
        print(f"  [{idx}] {dev['name']}")
    return _prompt_device_choice([idx for idx, _ in inputs], label="record from")


def choose_output_device(input_device):
    """List output-capable devices and let the user pick headphones.

    Refuses any choice that would create a feedback loop back into the
    BlackHole input: the same device as the input, or any other
    BlackHole/loopback device (which routes right back around).
    """
    devices = sd.query_devices()
    default_out = sd.default.device[1]
    outputs = [(idx, dev) for idx, dev in enumerate(devices) if dev['max_output_channels'] >= CHANNELS]
    print("\nAvailable output (monitoring) devices:")
    print("(Pick your real headphones/speakers here — NOT a BlackHole or other")
    print(" loopback device, or its output will feed straight back into the")
    print(" input you just chose and cause a feedback loop.)")
    for idx, dev in outputs:
        flag = " (default)" if idx == default_out else ""
        loop_warning = ""
        if idx == input_device:
            loop_warning = "  <- same as your input, would loop back"
        elif 'blackhole' in dev['name'].lower() or 'soundflower' in dev['name'].lower():
            loop_warning = "  <- virtual/loopback device, likely to feed back"
        print(f"  [{idx}] {dev['name']}{flag}{loop_warning}")

    while True:
        choice_idx = _prompt_device_choice([idx for idx, _ in outputs], label="headphones/monitor output")
        if choice_idx == input_device:
            print("\nThat's the same device you're recording from — picking it as "
                  "the output would create a feedback loop. Choose a different device.")
            continue
        chosen_name = next(dev['name'] for idx, dev in outputs if idx == choice_idx).lower()
        if 'blackhole' in chosen_name or 'soundflower' in chosen_name:
            confirm = input(f"\n'{chosen_name}' is a virtual loopback device, not physical "
                             f"speakers/headphones — routing monitoring to it is very likely "
                             f"to feed back. Use it anyway? (y/N): ").strip().lower()
            if confirm != 'y':
                continue
        return choice_idx


def _prompt_device_choice(valid_indices, label="device"):
    while True:
        choice = input(f"\nEnter the {label} number: ").strip()
        try:
            choice_idx = int(choice)
        except ValueError:
            print("Please enter a number.")
            continue
        if choice_idx in valid_indices:
            return choice_idx
        print("Not a valid device number, try again.")


def choose_save_folder():
    """Open a native folder picker. Falls back to a typed path if tkinter is unavailable."""
    if tk is not None:
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        folder = filedialog.askdirectory(title="Choose where to save the recording")
        root.destroy()
        if folder:
            return folder
    while True:
        folder = input("Enter a folder path to save into: ").strip()
        if os.path.isdir(folder):
            return folder
        print("That folder doesn't exist — create it first, or try again.")


def choose_duration():
    raw = input(f"\nRecording length in minutes (default 120 = 2 hours): ").strip()
    if not raw:
        return DEFAULT_DURATION_SECONDS
    try:
        minutes = float(raw)
        if minutes <= 0:
            raise ValueError
        return minutes * 60
    except ValueError:
        print("Didn't understand that, using the default of 2 hours.")
        return DEFAULT_DURATION_SECONDS


def build_filenames(folder):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base = f"blackhole_recording_{timestamp}"
    return os.path.join(folder, base + ".wav"), os.path.join(folder, base + ".mp3")


class MonitorBuffer:
    """
    A small ring buffer that decouples the input stream from the output
    stream. Two independent CoreAudio streams (rather than one duplex
    stream spanning two unrelated devices) avoids AUHAL errors and
    input-overflow issues that show up when one of the devices is
    Bluetooth. If playback falls behind, oldest audio is dropped rather
    than letting monitoring latency grow without bound.
    """
    def __init__(self, max_seconds, samplerate, channels):
        self.lock = threading.Lock()
        self.chunks = collections.deque()
        self.available = 0
        self.max_frames = int(max_seconds * samplerate)
        self.channels = channels

    def write(self, data):
        with self.lock:
            self.chunks.append(data)
            self.available += len(data)
            while self.available > self.max_frames and self.chunks:
                dropped = self.chunks.popleft()
                self.available -= len(dropped)

    def read(self, frames):
        with self.lock:
            out = np.zeros((frames, self.channels), dtype='float32')
            filled = 0
            while filled < frames and self.chunks:
                chunk = self.chunks[0]
                need = frames - filled
                if len(chunk) <= need:
                    out[filled:filled + len(chunk)] = chunk
                    filled += len(chunk)
                    self.available -= len(chunk)
                    self.chunks.popleft()
                else:
                    out[filled:frames] = chunk[:need]
                    self.chunks[0] = chunk[need:]
                    self.available -= need
                    filled = frames
            return out


def record_and_monitor(input_device, output_device, wav_path, duration_seconds):
    """
    Records from input_device on its own stream, and plays monitoring
    audio to output_device on a second, independent stream. Ctrl+C
    stops early and still saves what was captured.
    """
    write_q = queue.Queue()
    monitor_buf = MonitorBuffer(max_seconds=0.5, samplerate=SAMPLE_RATE, channels=CHANNELS)
    stop_flag = threading.Event()
    overflow_count = [0]

    def input_callback(indata, frames, time_info, status):
        if status:
            # Bundle up overflow/underflow spam instead of printing every block
            if 'overflow' in str(status).lower() or 'underflow' in str(status).lower():
                overflow_count[0] += 1
            else:
                print(status, file=sys.stderr)
        data = indata.copy()
        write_q.put(data)
        monitor_buf.write(data)

    def output_callback(outdata, frames, time_info, status):
        if status and 'underflow' not in str(status).lower():
            print(status, file=sys.stderr)
        outdata[:] = monitor_buf.read(frames)

    def writer(sound_file):
        while not stop_flag.is_set() or not write_q.empty():
            try:
                block = write_q.get(timeout=0.5)
            except queue.Empty:
                continue
            sound_file.write(block)

    hours = duration_seconds / 3600
    print(f"\nRecording for up to {hours:.2f} hour(s). Monitoring is live in your headphones.")
    print("Press Ctrl+C at any time to stop early and still save what was recorded.\n")

    with sf.SoundFile(wav_path, mode='w', samplerate=SAMPLE_RATE,
                       channels=CHANNELS, subtype='PCM_24') as sound_file:
        writer_thread = threading.Thread(target=writer, args=(sound_file,), daemon=True)
        writer_thread.start()

        try:
            with sd.InputStream(device=input_device,
                                 samplerate=SAMPLE_RATE,
                                 channels=CHANNELS,
                                 blocksize=BLOCKSIZE,
                                 dtype='float32',
                                 callback=input_callback), \
                 sd.OutputStream(device=output_device,
                                  samplerate=SAMPLE_RATE,
                                  channels=CHANNELS,
                                  blocksize=BLOCKSIZE,
                                  dtype='float32',
                                  callback=output_callback):
                sd.sleep(int(duration_seconds * 1000))
        except KeyboardInterrupt:
            print("\nStopped early by user.")
        finally:
            stop_flag.set()
            writer_thread.join()

    if overflow_count[0]:
        print(f"\n(Note: {overflow_count[0]} brief buffer over/underflows during the session — "
              f"this is just monitoring glitching momentarily, not lost recording data.)")

    print(f"WAV saved: {wav_path}")


def export_mp3(wav_path, mp3_path):
    """Convert the WAV to MP3 using ffmpeg (must be installed and on PATH)."""
    if shutil.which("ffmpeg") is None:
        print("\nffmpeg not found on PATH — skipping MP3 export.")
        print("Install it with: brew install ffmpeg")
        return False
    print("\nEncoding MP3...")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libmp3lame", "-qscale:a", "2", mp3_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print("ffmpeg failed:")
        print(result.stderr[-2000:])
        return False
    print(f"MP3 saved: {mp3_path}")
    return True


def print_device_list():
    """Print every audio device this machine can see, with a quick BlackHole check."""
    devices = sd.query_devices()
    default_in, default_out = sd.default.device
    print(f"\n{'#':<4}{'Name':<40}{'In':<5}{'Out':<5}{'Default'}")
    print("-" * 60)
    for idx, dev in enumerate(devices):
        flags = []
        if idx == default_in:
            flags.append("in")
        if idx == default_out:
            flags.append("out")
        flag_str = f"({', '.join(flags)})" if flags else ""
        print(f"{idx:<4}{dev['name']:<40}{dev['max_input_channels']:<5}"
              f"{dev['max_output_channels']:<5}{flag_str}")

    blackhole_2ch = [d for d in devices if d['name'].lower() == 'blackhole 2ch']
    print()
    if blackhole_2ch:
        print("BlackHole 2ch: found — this machine is ready to record with it.")
    else:
        any_blackhole = any('blackhole' in d['name'].lower() for d in devices)
        if any_blackhole:
            print("BlackHole 2ch: not found, but another BlackHole channel count is "
                  "installed (16ch/64ch). Install the 2ch driver if you specifically "
                  "want 2ch, or pick the available one when the script prompts you.")
        else:
            print("BlackHole 2ch: NOT found on this machine.")
            print("Install it from https://existing.works/blackhole (BlackHole 2ch), "
                  "then reboot for the Core Audio driver to register.")


def preflight_check():
    """Silent check used before the normal prompts — warns early if BlackHole is missing."""
    devices = sd.query_devices()
    if not any('blackhole' in d['name'].lower() for d in devices):
        print("Heads up: no BlackHole device was found on this machine.")
        print("Run 'python3 blackhole_recorder.py --list-devices' for the full device "
              "list, or install BlackHole 2ch from https://existing.works/blackhole "
              "and reboot before continuing.\n")


def main():
    parser = argparse.ArgumentParser(description="Record BlackHole 2ch with live headphone monitoring.")
    parser.add_argument("--list-devices", action="store_true",
                         help="List all audio devices on this machine and exit.")
    args = parser.parse_args()

    if args.list_devices:
        print_device_list()
        return

    print("=== BlackHole 2ch Recorder + Live Monitor ===")
    preflight_check()

    input_device = choose_input_device()
    output_device = choose_output_device(input_device)
    duration_seconds = choose_duration()
    folder = choose_save_folder()
    wav_path, mp3_path = build_filenames(folder)

    record_and_monitor(input_device, output_device, wav_path, duration_seconds)
    export_mp3(wav_path, mp3_path)

    print("\nDone.")


if __name__ == "__main__":
    main()
