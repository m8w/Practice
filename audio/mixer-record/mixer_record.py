#!/usr/bin/env python3
"""
mixer_record.py — Multi-lane mixer recorder: USB mixer + BlackHole (radiot)
                  -> one processed, normalized 48 kHz / 16-bit WAV.

ONE FILE OUT. No raw copy, no MP3, no leftovers.

  * Mixes 2+ input lanes (e.g. USB mixer ch1-2 + BlackHole 2ch ch3-4) to stereo,
    with independent gain per lane and live per-lane metering.
  * Stops itself at a 2-hour cap (and a 2 GB safety cap, whichever lands first).
  * EQ -> loudness normalize (EBU R128) with a true-peak ceiling, so nothing
    ends up too loud or too soft.
  * Native folder picker (with a New Folder button) so you choose where it lands.
  * Remembers your device, lanes, gains, and folder in ~/.mixer_record.json.

IMPORTANT — two devices, two clocks:
  BlackHole and your USB mixer each run their own clock. To record both in one
  stream you must combine them into a macOS Aggregate Device first, or they will
  drift apart over a long take. Run with --howto for step-by-step setup.

Dependencies:
    pip install sounddevice soundfile numpy
    ffmpeg on PATH  (brew install ffmpeg)

Usage:
    python3 mixer_record.py --howto      # aggregate device setup instructions
    python3 mixer_record.py --setup      # interactive: device, lanes, gains, folder
    python3 mixer_record.py              # record using saved settings
    python3 mixer_record.py --folder     # pick a new output folder this run
    python3 mixer_record.py --gains 1.0,0.7      # override lane gains
    python3 mixer_record.py --hours 1            # shorter cap
    python3 mixer_record.py --process-only x.wav # process an existing file
"""

import argparse
import datetime as dt
import json
import os
import platform
import queue
import shutil
import subprocess
import sys

import numpy as np
import soundfile as sf

# sounddevice / PortAudio is imported lazily inside capture functions so that
# --process-only and --howto work anywhere.

CONFIG_PATH = os.path.expanduser("~/.mixer_record.json")

# ---- Defaults --------------------------------------------------------------

DEFAULT_RATE = 48000        # matches radiot / BlackHole — no resampling
DEFAULT_BITS = 16           # final file bit depth
DEFAULT_HOURS = 2.0         # hard duration cap
SIZE_LIMIT = 2_000_000_000  # 2 GB safety cap, below WAV's 2^31 break point
HEADER_RESERVE = 4096
BLOCKSIZE = 2048

DEFAULT_LUFS = -16.0        # loudness target
DEFAULT_TP = -1.0           # true-peak ceiling, dBTP
DEFAULT_LRA = 11.0
DEFAULT_HIGHPASS = 24.0     # Hz subsonic filter; 0 disables

HOWTO = """
================= AGGREGATE DEVICE SETUP (do this once) =================

Your USB mixer and BlackHole are separate devices with separate clocks.
Recording both at once without combining them causes drift over a long take.
macOS fixes this natively:

  1. Open  Audio MIDI Setup  (Applications > Utilities).
  2. Click  +  (bottom-left)  ->  "Create Aggregate Device".
  3. Tick the checkbox for BOTH:
         - your USB mixer
         - BlackHole 2ch
  4. Set  Clock Source  to your USB MIXER  (real hardware clock).
  5. Tick  Drift Correction  on the BlackHole row.
         (If you ever hear artifacts, try the reverse: BlackHole as clock
          source, drift correction on the mixer.)
  6. Rename it something findable, e.g. "MixBus".
  7. Note the CHANNEL ORDER shown in the device list — usually the first
     device listed occupies channels 1-2, the second 3-4.

Then point radiot / your player at BlackHole 2ch as its output, and run:

     python3 mixer_record.py --setup

...which will find the aggregate device and let you assign the lanes.

TIP — want Logic (or any app) in the mix too? Set that app's output to
BlackHole as well. It sums into the same lane automatically; no script change.
=========================================================================
"""


# ---- Small helpers ---------------------------------------------------------

def human_bytes(n):
    # Decimal units (1 GB = 1,000,000,000 B) to match what macOS Finder shows,
    # so free-space checks here line up with what you see in the OS.
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.2f} {unit}"
        n /= 1000


def human_time(seconds):
    return str(dt.timedelta(seconds=int(seconds)))


def bytes_per_frame(channels, bits):
    return channels * (2 if bits == 16 else 3)


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
        print(f"Settings saved to {CONFIG_PATH}")
    except OSError as e:
        print(f"Could not save settings: {e}")


# ---- Folder picker ---------------------------------------------------------

def pick_folder(start=None, prompt="Choose where to save the recording"):
    """Native folder chooser WITH a New Folder button. Returns path or None."""
    start = start or os.path.expanduser("~")

    if platform.system() == "Darwin":
        # macOS 'choose folder' dialog includes a New Folder button.
        script = (f'POSIX path of (choose folder with prompt "{prompt}" '
                  f'default location POSIX file "{start}")')
        try:
            r = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True, timeout=300)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().rstrip("/")
            return None      # user cancelled
        except (subprocess.SubprocessError, OSError):
            pass             # fall through to tkinter

    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.update()
        path = filedialog.askdirectory(title=prompt, initialdir=start,
                                       mustexist=False)
        root.destroy()
        return path or None
    except Exception:
        print("\nNo graphical picker available.")
        typed = input(f"Type an output folder path [{start}]: ").strip()
        return typed or start


def ensure_folder(path):
    """Create the folder if needed. Returns the path, or None on failure."""
    try:
        os.makedirs(path, exist_ok=True)
        return path
    except OSError as e:
        print(f"Could not create folder '{path}': {e}")
        return None


def free_space(path):
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


# ---- Device / lane setup ---------------------------------------------------

def list_input_devices():
    import sounddevice as sd
    print("\nInput devices (index: name  [input channels] @ default rate):")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            print(f"  {i}: {d['name']}  "
                  f"[{d['max_input_channels']} ch] @ "
                  f"{int(d['default_samplerate'])} Hz")
    print()


def interactive_setup(cfg):
    """Walk through device, lane mapping, gains, and output folder."""
    import sounddevice as sd

    print("\n" + "=" * 64)
    print("SETUP  —  pick your device, assign lanes, choose a folder")
    print("=" * 64)
    list_input_devices()
    print("If you don't see an aggregate device combining your mixer and")
    print("BlackHole, quit and run:  python3 mixer_record.py --howto\n")

    devices = sd.query_devices()
    while True:
        raw = input("Device index to record from: ").strip()
        if raw.isdigit() and 0 <= int(raw) < len(devices) \
                and devices[int(raw)]["max_input_channels"] > 0:
            dev = int(raw)
            break
        print("  Not a valid input device index — try again.")

    info = devices[dev]
    nch = info["max_input_channels"]
    print(f"\n'{info['name']}' offers {nch} input channel(s).")
    print("Assign lanes as 1-based channel pairs, e.g.  1,2  then  3,4")
    print("(a single number = mono lane, duplicated to stereo)\n")

    lanes = []
    default_names = ["Mixer / synths", "radiot / BlackHole", "Lane 3", "Lane 4"]
    while True:
        n = len(lanes) + 1
        suggested = default_names[min(n - 1, len(default_names) - 1)]
        chans = input(f"Lane {n} channels (blank to finish): ").strip()
        if not chans:
            if lanes:
                break
            print("  Need at least one lane.")
            continue

        try:
            idx = [int(x.strip()) - 1 for x in chans.split(",") if x.strip()]
        except ValueError:
            print("  Use numbers like: 1,2")
            continue
        if not idx or any(i < 0 or i >= nch for i in idx) or len(idx) > 2:
            print(f"  Channels must be 1..{nch}, one or two of them.")
            continue

        name = input(f"  Name for lane {n} [{suggested}]: ").strip() or suggested
        graw = input("  Gain (1.0 = unity) [1.0]: ").strip()
        try:
            gain = float(graw) if graw else 1.0
        except ValueError:
            gain = 1.0
        lanes.append({"name": name, "ch": idx, "gain": gain})
        print(f"  + {name}: channels {[i + 1 for i in idx]} @ gain {gain}\n")
        if len(lanes) >= 4:
            break

    start = cfg.get("folder") or os.path.expanduser("~")
    print("Opening the folder picker — use its New Folder button if you want")
    print("a fresh folder for these recordings...")
    folder = pick_folder(start)
    if not folder:
        folder = start
        print(f"Cancelled — using {folder}")

    cfg.update({"device_name": info["name"], "device_index": dev,
                "lanes": lanes, "folder": folder})
    save_config(cfg)
    return cfg


def resolve_device(cfg):
    """Find the saved device by NAME first (indices shift), then index."""
    import sounddevice as sd
    devices = sd.query_devices()
    name = cfg.get("device_name")

    if name:
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0 and d["name"] == name:
                return i
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0 and name.lower() in d["name"].lower():
                return i

    idx = cfg.get("device_index")
    if idx is not None and 0 <= idx < len(devices) \
            and devices[idx]["max_input_channels"] > 0:
        print(f"Note: device '{name}' not found by name; using index {idx}.")
        return idx
    return None


# ---- Processing (ffmpeg) ---------------------------------------------------

def ffmpeg_available():
    return shutil.which("ffmpeg") is not None


def eq_for_voice(voice, custom):
    if custom:
        return custom
    return {
        "flat": None,
        "warm": "bass=g=2:f=110:width_type=q:width=0.7,"
                "treble=g=-1.5:f=8000:width_type=q:width=0.7",
        "air":  "treble=g=2.5:f=9000:width_type=q:width=0.7",
    }.get(voice)


def build_pre_chain(args):
    stages = []
    if args.highpass and args.highpass > 0:
        stages.append(f"highpass=f={args.highpass}")
    eq = eq_for_voice(args.voice, args.eq)
    if eq:
        stages.append(eq)
    if args.compress:
        stages.append("acompressor=threshold=-18dB:ratio=3:attack=20:"
                      "release=250:knee=6:makeup=2")
    return stages


def measure_loudnorm(infile, pre, I, TP, LRA):
    af = ",".join(pre + [f"loudnorm=I={I}:TP={TP}:LRA={LRA}:print_format=json"])
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", infile,
                        "-af", af, "-f", "null", "-"],
                       capture_output=True, text=True)
    txt = r.stderr
    s, e = txt.rfind("{"), txt.rfind("}")
    if s == -1 or e == -1 or e < s:
        return None
    try:
        return json.loads(txt[s:e + 1])
    except json.JSONDecodeError:
        return None


def postprocess(infile, outfile, args, out_bits=DEFAULT_BITS):
    """EQ + normalize infile -> outfile. Returns True on success."""
    if not ffmpeg_available():
        print("ffmpeg not found — cannot process. Install:  brew install ffmpeg")
        return False

    sr = int(sf.info(infile).samplerate)
    codec = "pcm_s16le" if out_bits == 16 else "pcm_s24le"
    pre = build_pre_chain(args)
    I, TP, LRA = args.target_lufs, args.peak, args.lra

    print("Processing:")
    print("  pass 1/2 — measuring loudness...")
    m = measure_loudnorm(infile, pre, I, TP, LRA)
    if m is None:
        print("  couldn't read loudness measurement.")
        return False

    print("  pass 2/2 — applying EQ + normalize...")
    # linear=true prefers one clean gain move, preserving dynamics; it only
    # falls back to dynamic handling if the peak ceiling demands it.
    ln = (f"loudnorm=I={I}:TP={TP}:LRA={LRA}:"
          f"measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
          f"offset={m['target_offset']}:linear=true")
    af = ",".join(pre + [ln])
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", infile,
                        "-af", af, "-ar", str(sr), "-c:a", codec, outfile],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("  ffmpeg processing failed:")
        print(r.stderr[-800:])
        return False
    return True


# ---- Capture ---------------------------------------------------------------

def mix_block(block, lanes):
    """Sum lanes to stereo. Returns (stereo_mix, [per-lane peak, ...])."""
    out = np.zeros((len(block), 2), dtype=np.float32)
    peaks = []
    for lane in lanes:
        ch = lane["ch"]
        seg = block[:, ch]
        if seg.shape[1] == 1:                    # mono lane -> both sides
            seg = np.repeat(seg, 2, axis=1)
        seg = seg * lane["gain"]
        peaks.append(float(np.max(np.abs(seg))) if seg.size else 0.0)
        out += seg
    return out, peaks


def record(temp_path, device, lanes, samplerate, temp_bits, max_frames):
    """Capture and live-mix lanes to temp_path. Returns frames written."""
    import sounddevice as sd

    open_ch = max(i for lane in lanes for i in lane["ch"]) + 1
    subtype = "PCM_24" if temp_bits == 24 else "PCM_16"
    q = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status:
            print(f"\n[stream status] {status}", file=sys.stderr)
        q.put(indata.copy())   # PortAudio reuses its buffer

    written = 0
    clipped = False
    try:
        # RF64 for the working file: standard WAV breaks near 2 GiB, and a long
        # 24-bit take can cross that. RF64 has no such ceiling and ffmpeg reads
        # it fine. The final deliverable is written as plain WAV.
        with sf.SoundFile(temp_path, mode="w", samplerate=samplerate,
                          channels=2, subtype=subtype, format="RF64") as f, \
             sd.InputStream(samplerate=samplerate, device=device,
                            channels=open_ch, dtype="float32",
                            blocksize=BLOCKSIZE, callback=callback):
            start = dt.datetime.now()
            while True:
                try:
                    block = q.get(timeout=0.5)
                except queue.Empty:
                    continue

                remaining = max_frames - written
                if remaining <= 0:
                    break
                if len(block) > remaining:
                    block = block[:remaining]

                mixed, peaks = mix_block(block, lanes)
                mx = float(np.max(np.abs(mixed))) if mixed.size else 0.0
                if mx >= 0.999:
                    clipped = True
                    np.clip(mixed, -1.0, 1.0, out=mixed)

                f.write(mixed)
                written += len(mixed)

                meters = "  ".join(
                    f"{lane['name'][:12]} {20 * np.log10(p):5.1f}"
                    if p > 0 else f"{lane['name'][:12]}  -inf"
                    for lane, p in zip(lanes, peaks))
                sum_db = f"{20 * np.log10(mx):5.1f}" if mx > 0 else " -inf"
                elapsed = (dt.datetime.now() - start).total_seconds()
                pct = 100 * written / max_frames
                sys.stdout.write(
                    f"\r  {human_time(elapsed)} ({pct:4.1f}%)  |  {meters}  "
                    f"|  MIX {sum_db} dBFS{'  CLIP!' if clipped else '     '}")
                sys.stdout.flush()

                if written >= max_frames:
                    break
    except KeyboardInterrupt:
        print("\n\nStopped by user.")

    if clipped:
        print("\n!! The lane sum clipped at some point. Lower a lane gain")
        print("   (--gains) or trim the mixer's output next time.")
    return written


# ---- Main ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Record + mix USB mixer and BlackHole to one normalized WAV.")
    ap.add_argument("--howto", action="store_true",
                    help="print aggregate-device setup instructions and exit")
    ap.add_argument("--setup", action="store_true",
                    help="interactively set device, lanes, gains, output folder")
    ap.add_argument("--list", action="store_true",
                    help="list input devices and exit")
    ap.add_argument("--folder", action="store_true",
                    help="open the folder picker for this run")
    ap.add_argument("--subfolder",
                    help="create/use this subfolder inside the output folder")
    ap.add_argument("--gains",
                    help="comma-separated lane gains, e.g. 1.0,0.7")
    ap.add_argument("--rate", type=int, default=DEFAULT_RATE,
                    help=f"sample rate (default: {DEFAULT_RATE})")
    ap.add_argument("--bits", type=int, choices=(16, 24), default=DEFAULT_BITS,
                    help=f"final bit depth (default: {DEFAULT_BITS})")
    ap.add_argument("--temp-bits", type=int, choices=(16, 24), default=24,
                    help="working-file bit depth (default: 24, headroom for mixing)")
    ap.add_argument("--hours", type=float, default=DEFAULT_HOURS,
                    help=f"duration cap in hours (default: {DEFAULT_HOURS})")
    ap.add_argument("--name", help="base filename (default: timestamp)")

    ap.add_argument("--voice", choices=("flat", "warm", "air"), default="flat",
                    help="EQ voicing (default: flat = no tonal coloring)")
    ap.add_argument("--eq", help="custom ffmpeg filter string (overrides --voice)")
    ap.add_argument("--highpass", type=float, default=DEFAULT_HIGHPASS,
                    help="subsonic high-pass Hz, 0 to disable (default: 24)")
    ap.add_argument("--compress", action="store_true",
                    help="gentle leveling so loud/soft passages sit closer")
    ap.add_argument("--target-lufs", type=float, default=DEFAULT_LUFS,
                    help=f"loudness target LUFS (default: {DEFAULT_LUFS})")
    ap.add_argument("--peak", type=float, default=DEFAULT_TP,
                    help=f"true-peak ceiling dBTP (default: {DEFAULT_TP})")
    ap.add_argument("--lra", type=float, default=DEFAULT_LRA,
                    help=f"target loudness range (default: {DEFAULT_LRA})")
    ap.add_argument("--process-only", help="process an existing WAV and exit")
    args = ap.parse_args()

    if args.howto:
        print(HOWTO)
        return
    if args.list:
        list_input_devices()
        return

    if args.process_only:
        base, _ = os.path.splitext(args.process_only)
        out = f"{base}_processed.wav"
        if postprocess(args.process_only, out, args, args.bits):
            print(f"\nDone. Wrote {out} ({human_bytes(os.path.getsize(out))}).")
        return

    cfg = load_config()
    if args.setup or not cfg.get("lanes"):
        if not cfg.get("lanes") and not args.setup:
            print("No saved settings yet — running setup.")
        cfg = interactive_setup(cfg)

    device = resolve_device(cfg)
    if device is None:
        print(f"\nCouldn't find '{cfg.get('device_name')}'. Is the mixer plugged")
        print("in and the aggregate device present? Re-run with --setup.")
        list_input_devices()
        return

    import sounddevice as sd
    info = sd.query_devices(device)
    lanes = [dict(l) for l in cfg["lanes"]]

    if args.gains:
        try:
            gs = [float(g) for g in args.gains.split(",")]
            for lane, g in zip(lanes, gs):
                lane["gain"] = g
        except ValueError:
            print("Couldn't parse --gains; using saved values.")

    need = max(i for lane in lanes for i in lane["ch"]) + 1
    if need > info["max_input_channels"]:
        print(f"\nLanes need {need} channels but '{info['name']}' has "
              f"{info['max_input_channels']}. Re-run with --setup.")
        return

    # Output folder
    folder = cfg.get("folder") or os.path.expanduser("~")
    if args.folder:
        picked = pick_folder(folder)
        if picked:
            folder = picked
            cfg["folder"] = folder
            save_config(cfg)
    if args.subfolder:
        folder = os.path.join(folder, args.subfolder)
    if not ensure_folder(folder):
        return

    # Caps: duration is the primary limit. The 2 GB safety cap is measured
    # against the FINAL wav (the working file is RF64, which has no ceiling),
    # so at 48k/16-bit stereo a full 2 h lands ~1.24 GB and duration binds.
    rate = args.rate
    dur_frames = int(args.hours * 3600 * rate)
    size_frames = (SIZE_LIMIT - HEADER_RESERVE) // bytes_per_frame(2, args.bits)
    max_frames = min(dur_frames, size_frames)
    capped_by = "duration" if max_frames == dur_frames else "2 GB size limit"
    est_temp = max_frames * bytes_per_frame(2, args.temp_bits)
    est_final = max_frames * bytes_per_frame(2, args.bits)

    base = args.name or ("mixer_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
    final_path = os.path.join(folder, base + ".wav")
    temp_path = os.path.join(folder, f".{base}_working.wav")

    # Space check: the working file and final file coexist briefly.
    need_bytes = est_temp + est_final
    avail = free_space(folder)
    print("=" * 68)
    print(f"Device  : {info['name']} (index {device})")
    for lane in lanes:
        print(f"  lane  : {lane['name']:<20} ch {[c + 1 for c in lane['ch']]} "
              f"@ gain {lane['gain']}")
    print(f"Format  : {rate} Hz, stereo mix, {args.bits}-bit final")
    print(f"Cap     : {human_time(max_frames / rate)}  (by {capped_by}) "
          f"-> ~{human_bytes(est_final)} final")
    chain = f"EQ[{args.voice}]" + (" +compress" if args.compress else "")
    print(f"Chain   : {chain} -> normalize {args.target_lufs} LUFS @ "
          f"{args.peak} dBTP")
    print(f"Folder  : {folder}")
    print(f"Output  : {os.path.basename(final_path)}   (single file, no raw kept)")
    if avail is not None:
        print(f"Space   : {human_bytes(avail)} free; "
              f"needs up to {human_bytes(need_bytes)} briefly")
        if avail < need_bytes:
            print("\n!! Not enough free space for a full-length take.")
            print("   Options: --hours 1, or --temp-bits 16, or a roomier folder.")
            if input("   Continue anyway? [y/N] ").strip().lower() != "y":
                return
    print("=" * 68)
    print("Recording... Ctrl+C to stop early and finalize.\n")

    written = record(temp_path, device, lanes, rate, args.temp_bits, max_frames)
    dur = written / rate
    print(f"\nCaptured {human_time(dur)}.")

    if written == 0:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        print("Nothing recorded — no file written.")
        return

    if postprocess(temp_path, final_path, args, args.bits):
        os.remove(temp_path)
        print(f"\nDone -> {final_path}")
        print(f"       {human_bytes(os.path.getsize(final_path))}, "
              f"{human_time(dur)}, normalized to {args.target_lufs} LUFS")
    else:
        # Processing failed — keep the capture rather than lose the take.
        salvage = os.path.join(folder, base + "_unprocessed.wav")
        os.rename(temp_path, salvage)
        print(f"\nProcessing failed. Your recording is safe at:\n  {salvage}")
        print(f"Retry with:  python3 mixer_record.py --process-only '{salvage}'")


if __name__ == "__main__":
    main()
