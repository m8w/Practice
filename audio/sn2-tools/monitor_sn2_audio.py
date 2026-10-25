#!/usr/bin/env python3
"""
monitor_sn2_audio.py — self-healing overnight supervisor for sn2_chaos8_runs.py.

Every CHECK_INTERVAL seconds: records SAMPLE_SECONDS of audio from the
"USB Audio CODEC" input (the Supernova II's audio return path into the Mac),
reads back mean/max volume (dB), and logs OK/WARNING.

A check is considered a PROBLEM if either:
  - mean volume is below SILENCE_THRESHOLD_DB (likely silence), or
  - mean AND max volume are both within FROZEN_EPSILON_DB of the previous
    check's readings (signal is stuck/frozen, not really live/changing).

On a PROBLEM:
  1st occurrence -> kill and relaunch sn2_chaos8_runs.py, log recovery attempt.
  2nd occurrence in a row (recovery didn't help) -> launch radiot.py as a
      fallback (only if not already running) and stop trying to restart sn2.

Requires ffmpeg and mic/audio-input permission already granted to the
terminal running this.

Run:  python3 monitor_sn2_audio.py
Stop: Ctrl+C
"""
import subprocess
import re
import time
import datetime
import sys
import os

DEVICE_INDEX = 6            # avfoundation index for "USB Audio CODEC"
SAMPLE_SECONDS = 5
CHECK_INTERVAL = 15 * 60    # 15 minutes
SILENCE_THRESHOLD_DB = -50.0
FROZEN_EPSILON_DB = 0.05

MUSIC_DIR = "/Users/wvn/Music"
SN2_SCRIPT = os.path.join(MUSIC_DIR, "sn2_chaos8_runs.py")
RADIOT_SCRIPT = os.path.join(MUSIC_DIR, "radiot.py")


def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")
    sys.stdout.flush()


def check_once():
    cmd = [
        "ffmpeg", "-y",
        "-f", "avfoundation", "-i", f":{DEVICE_INDEX}",
        "-t", str(SAMPLE_SECONDS),
        "-af", "volumedetect",
        "-f", "null", "/dev/null",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=SAMPLE_SECONDS + 30)
    out = result.stderr
    mean_match = re.search(r"mean_volume:\s*(-?[\d.]+) dB", out)
    max_match = re.search(r"max_volume:\s*(-?[\d.]+) dB", out)
    if not mean_match or not max_match:
        return None, None
    return float(mean_match.group(1)), float(max_match.group(1))


def is_running(script_name):
    result = subprocess.run(["pgrep", "-f", script_name], capture_output=True, text=True)
    return bool(result.stdout.strip())


def restart_sn2():
    subprocess.run(["pkill", "-f", "sn2_chaos8_runs.py"])
    time.sleep(1)
    subprocess.Popen(
        ["python3", "-u", SN2_SCRIPT],
        cwd=MUSIC_DIR,
        stdin=subprocess.DEVNULL,
        stdout=open(os.path.join(MUSIC_DIR, "sn2_chaos8_runs.autolog"), "a"),
        stderr=subprocess.STDOUT,
    )


def launch_radiot():
    subprocess.Popen(
        ["python3", "-u", RADIOT_SCRIPT],
        cwd=MUSIC_DIR,
        stdin=subprocess.DEVNULL,
        stdout=open(os.path.join(MUSIC_DIR, "radiot.autolog"), "a"),
        stderr=subprocess.STDOUT,
    )


def main():
    log(f"Monitoring USB Audio CODEC every {CHECK_INTERVAL // 60} minutes. Ctrl+C to stop.")
    prev_mean = None
    prev_max = None
    consecutive_problems = 0
    fallback_active = False

    while True:
        try:
            mean_db, max_db = check_once()
        except Exception as e:
            log(f"ERROR running ffmpeg: {e}")
            mean_db = None

        if mean_db is None:
            log("WARNING: could not read audio levels (device unavailable or permission issue).")
            problem = True
        else:
            silent = mean_db < SILENCE_THRESHOLD_DB
            frozen = (
                prev_mean is not None
                and abs(mean_db - prev_mean) < FROZEN_EPSILON_DB
                and abs(max_db - prev_max) < FROZEN_EPSILON_DB
            )
            if silent:
                log(f"WARNING: likely silence — mean {mean_db:.2f} dB, max {max_db:.2f} dB")
            elif frozen:
                log(f"WARNING: signal looks frozen (identical to last check) — "
                    f"mean {mean_db:.2f} dB, max {max_db:.2f} dB")
            else:
                log(f"OK — signal present, mean {mean_db:.2f} dB, max {max_db:.2f} dB")
            problem = silent or frozen
            prev_mean, prev_max = mean_db, max_db

        if problem:
            consecutive_problems += 1
            if not fallback_active:
                if consecutive_problems == 1:
                    log("Attempting recovery: restarting sn2_chaos8_runs.py")
                    restart_sn2()
                elif consecutive_problems >= 2:
                    if not is_running("radiot.py"):
                        log("Recovery didn't help — falling back to radiot.py")
                        launch_radiot()
                        fallback_active = True
                    else:
                        log("Still a problem, radiot.py already running.")
        else:
            consecutive_problems = 0

        time.sleep(max(0, CHECK_INTERVAL - SAMPLE_SECONDS))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
