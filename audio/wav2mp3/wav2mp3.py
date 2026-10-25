#!/usr/bin/env python3
"""Convert every .wav in a chosen folder to .mp3, saved next to the original.

Needs ffmpeg:  brew install ffmpeg
Usage:  python3 wav2mp3.py            (opens a folder picker)
        python3 wav2mp3.py /some/dir  (skips the picker)
"""
import shutil
import subprocess
import sys
from pathlib import Path

RECURSIVE = False      # True = also convert wavs inside subfolders
OVERWRITE = False      # True = redo mp3s that already exist
QUALITY = "0"          # LAME VBR quality: 0 (best, ~245k) to 9 (smallest)


def pick_folder():
    # Native macOS picker; falls back to tkinter elsewhere.
    if sys.platform == "darwin":
        r = subprocess.run(
            ["osascript", "-e",
             'POSIX path of (choose folder with prompt "Folder of WAVs to convert to MP3")'],
            capture_output=True, text=True)
        return r.stdout.strip() or None
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    return filedialog.askdirectory(title="Folder of WAVs to convert to MP3") or None


def main():
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found. Install it with: brew install ffmpeg")

    folder = sys.argv[1] if len(sys.argv) > 1 else pick_folder()
    if not folder:
        sys.exit("No folder selected.")
    folder = Path(folder)

    pattern = "**/*" if RECURSIVE else "*"
    wavs = sorted(p for p in folder.glob(pattern)
                  if p.is_file() and p.suffix.lower() == ".wav")
    if not wavs:
        sys.exit(f"No .wav files found in {folder}")

    done = skipped = failed = 0
    for i, wav in enumerate(wavs, 1):
        mp3 = wav.with_suffix(".mp3")
        if mp3.exists() and not OVERWRITE:
            print(f"[{i}/{len(wavs)}] skip (exists): {mp3.name}")
            skipped += 1
            continue
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
             "-codec:a", "libmp3lame", "-q:a", QUALITY, str(mp3)],
            capture_output=True, text=True)
        if r.returncode == 0:
            print(f"[{i}/{len(wavs)}] {wav.name} -> {mp3.name}")
            done += 1
        else:
            print(f"[{i}/{len(wavs)}] FAILED: {wav.name}\n{r.stderr.strip()}")
            failed += 1

    print(f"\nDone. {done} converted, {skipped} skipped, {failed} failed.")


if __name__ == "__main__":
    main()
