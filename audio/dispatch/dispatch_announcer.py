#!/usr/bin/env python3
"""
dispatch_announcer.py  -  Newbold Radio / Dispatch
Autonomous scanner-traffic announcer.

Tails the newest ExternalRadio log block and speaks one line, once, the
moment a new track starts. Machine voice (macOS `say`). Runs forever,
logs its own activity to a rotating file, and self-heals - never touch it.

This is module 1 of the Dispatch supervisor. OBS auto-mix, the stream
cycler, and continuous recording clip onto this core separately.

Run it live:      python3 dispatch_announcer.py
Preview cleaning: python3 dispatch_announcer.py --selftest log1.txt log2.txt
"""

import os, re, glob, time, html, argparse, subprocess, logging
from logging.handlers import RotatingFileHandler
from urllib.parse import unquote

# --- CONFIG ------------------------------------------------------------
LOG_DIR      = "/Users/wvn/ExternalRadio/logs"   # where block_*.txt land
BLOCK_GLOB   = "block_*.txt"

VOICE        = ""          # say voice, e.g. "Fred" (flat/robotic) or "Daniel". "" = default
RATE         = 190         # words per minute; dispatch cadence ~ 180-210
AUDIO_DEVICE = ""          # "" = system default output. Leave "" if your default already
                           # feeds OBS (e.g. default set to BlackHole). Or name one: "BlackHole 2ch".

CLEAN_LEVEL  = "clean"     # "clean" = strip machine noise (timestamps/warnings/glitch)
                           # "raw"   = speak titles as-found; only drop repeated warnings
SPEAK_SOURCE = True        # prefix each call with the lane (Bandcamp/Youtube/Archive)
SPEAK_CURRENT_ON_START = False  # speak the most-recent logged track once at boot (proof of life)
PHRASE       = "{source}. {title}."
FALLBACK_TITLE = "stand by"   # spoken when a title cleans down to nothing

POLL_SEC     = 2.0
RETRY_SEC    = 5.0

RUN_LOG      = "/Users/wvn/ExternalRadio/logs/dispatch_announcer.log"
RUN_LOG_MAX  = 2_000_000   # bytes per file (rotating, bounded - can't fill disk)
RUN_LOG_KEEP = 3
# -----------------------------------------------------------------------

TRACK_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s+\[([A-Z]+)\]\s+(.*)$")
URL_RE   = re.compile(r"^\s+(https?://\S+)")

WARN_PATTERNS = [
    r"\[EPILEPSY WARNING\]",
    r"\bFLASH WARNING\b",
    r"\bflash strobe warning\b",
    r"\bflashing lights warning\*?",
    r"\*?\s*Photosensitivity Warning\s*\*?",
    r"\bEpilepsy warning\b",
]
WARN_RE = re.compile("|".join(WARN_PATTERNS), re.IGNORECASE)

NOISE_PATTERNS = [
    r"Movie[\s\.\-]*\d{2}[\s\.\-]*\d{2}[\s\.\-]*\d{2}[\s\.\-]*\d{2}h\d{2}m\d{2}s",
    r"\d{2}h\d{2}m\d{2}s",
    r"\b\d{4}[\s\-\.]\d{2}[\s\-\.]\d{2}[\s\-\.]\d{2}[\s\-\.]\d{2}[\s\-\.]\d{2}\b",
    r"\bnewbold\s+wvn\b",
    r"\bwvn\b",
    r"\bhighlight\s*\d+\b",
    r"\bframe\s*\d+\b",
    r"_part\s*\d+",
    r"\bparts?\s*\d+(?:\s*-\s*\d+)?\b",
    r"[-_]\d+\b",
]
NOISE_RE = re.compile("|".join(NOISE_PATTERNS), re.IGNORECASE)

KEEP_RE  = re.compile(r"[^A-Za-z0-9 ,.'\-:]+")
WS_RE    = re.compile(r"\s+")

log = logging.getLogger("dispatch")


def setup_logging():
    log.setLevel(logging.INFO)
    h = RotatingFileHandler(RUN_LOG, maxBytes=RUN_LOG_MAX, backupCount=RUN_LOG_KEEP)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    s = logging.StreamHandler()
    s.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(s)


def bandcamp_title_from_url(url):
    m = re.search(r"/album/([^/?#]+)", url or "") or re.search(r"/track/([^/?#]+)", url or "")
    return m.group(1).replace("-", " ").strip() if m else ""


def clean_title(source, raw, url, level=None):
    level = level or CLEAN_LEVEL
    t = html.unescape(raw or "")
    t = WARN_RE.sub(" ", t)

    if source == "BANDCAMP" and (not t.strip() or t.strip().upper() == "NA"):
        t = bandcamp_title_from_url(url)

    if level == "clean":
        if " / " in t:
            t = t.split(" / ", 1)[0]
        t = re.sub(r"\.(mp3|wav|aiff)\b", " ", t, flags=re.IGNORECASE)
        t = NOISE_RE.sub(" ", t)
        t = KEEP_RE.sub(" ", t)
        t = WS_RE.sub(" ", t).strip(" -,.:")
    else:
        t = WS_RE.sub(" ", t).strip()

    return t or FALLBACK_TITLE


def build_announcement(source, raw, url, level=None):
    title = clean_title(source, raw, url, level).rstrip(" .,")
    if SPEAK_SOURCE:
        return PHRASE.format(source=source.title(), title=title)
    return title.rstrip(".") + "."


# --- speaking ----------------------------------------------------------
_device_ok = True   # degrades to default output if the -a flag isn't supported

def speak(text):
    global _device_ok
    base = ["say"]
    if VOICE: base += ["-v", VOICE]
    if RATE:  base += ["-r", str(RATE)]
    cmd = list(base)
    if AUDIO_DEVICE and _device_ok:
        cmd += ["-a", AUDIO_DEVICE]
    cmd += [text]
    try:
        r = subprocess.run(cmd, stderr=subprocess.PIPE)
        if r.returncode != 0 and AUDIO_DEVICE and _device_ok:
            log.warning("say -a '%s' unsupported; using default output this run", AUDIO_DEVICE)
            _device_ok = False
            subprocess.run(base + [text])
    except Exception as e:
        log.error("speak error: %r", e)


# --- log tailing -------------------------------------------------------
def latest_block():
    files = [p for p in glob.glob(os.path.join(LOG_DIR, BLOCK_GLOB))
             if re.search(r"block_\d", os.path.basename(p))]   # skip block_startup.txt
    return max(files) if files else None   # names are timestamped -> lexical == chronological


def run_live():
    current = latest_block()
    offset  = os.path.getsize(current) if current else 0
    pending  = None    # (source, rawtitle) awaiting its URL line
    last_key = None    # dedup guard so a re-read never speaks twice

    if current:
        log.info("attached to %s at end (%d bytes)", os.path.basename(current), offset)
        if SPEAK_CURRENT_ON_START:
            recent = list(iter_tracks(current))
            if recent:
                src, raw, url = recent[-1]
                phrase = build_announcement(src, raw, url)
                log.info("SPEAK  [%s] %s   (current track, on start)", src, phrase)
                speak(phrase); last_key = (src, raw)
    else:
        log.info("no block yet in %s — will attach when radiot starts one", LOG_DIR)
    log.info("watching — silent until the next song starts (Ctrl-C to stop)")
    idle_since = time.time()

    while True:
        newest = latest_block()
        if newest and newest != current:
            current, offset, pending = newest, 0, None   # new block -> read from top
            log.info("new block: %s", os.path.basename(current))

        if not current:
            time.sleep(POLL_SEC); continue

        try:
            size = os.path.getsize(current)
            if size < offset:                # rotated/truncated under us
                offset = 0
            if size > offset:
                with open(current, "rb") as f:
                    f.seek(offset)
                    data = f.read(size - offset)
                nl = data.rfind(b"\n")
                if nl != -1:
                    complete = data[:nl + 1]
                    offset  += len(complete)
                    for line in complete.decode("utf-8", errors="replace").splitlines():
                        m = TRACK_RE.match(line)
                        if m:
                            pending = (m.group(2), m.group(3)); continue
                        um = URL_RE.match(line)
                        if um and pending:
                            source, raw = pending; url = um.group(1); pending = None
                            key = (source, raw)
                            if key == last_key:      # same track logged twice back-to-back
                                continue
                            last_key = key
                            phrase = build_announcement(source, raw, url)
                            log.info("SPEAK  [%s] %s", source, phrase)
                            speak(phrase)
                            idle_since = time.time()
        except FileNotFoundError:
            current = None
        except Exception as e:
            log.error("tail error: %r", e)
            time.sleep(RETRY_SEC)

        if time.time() - idle_since > 300:      # heartbeat every 5 min of quiet
            log.info("still watching %s — %d min since last track",
                     os.path.basename(current) if current else "(none)",
                     int((time.time() - idle_since) // 60))
            idle_since = time.time()
        time.sleep(POLL_SEC)


def iter_tracks(path):
    """Yield (source, rawtitle, url) for every track in a block file."""
    with open(path, "rb") as f:
        text = f.read().decode("utf-8", errors="replace")
    pending = None
    for line in text.splitlines():
        m = TRACK_RE.match(line)
        if m:
            pending = (m.group(2), m.group(3)); continue
        um = URL_RE.match(line)
        if um and pending:
            src, raw = pending; pending = None
            yield src, raw, um.group(1)


def selftest(paths):
    for p in paths:
        print("\n==== %s ====" % os.path.basename(p))
        n = 0; fb = 0
        for src, raw, url in iter_tracks(p):
            n += 1
            c = build_announcement(src, raw, url, "clean")
            r = build_announcement(src, raw, url, "raw")
            if c.endswith(FALLBACK_TITLE + "."): fb += 1
            print("  clean: %s" % c)
            print("  raw  : %s" % r)
            print("")
        print("  -- %d tracks, %d hit '%s' in clean mode --" % (n, fb, FALLBACK_TITLE))


def demo(n):
    """Speak the last N tracks from the current block, then exit. Hear it now."""
    blk = latest_block()
    if not blk:
        print("no block_*.txt found in %s" % LOG_DIR); return
    tracks = list(iter_tracks(blk))
    if not tracks:
        print("no tracks yet in %s — waiting won't help, start radiot first" % os.path.basename(blk)); return
    setup_logging()
    log.info("DEMO %s — last %d of %d tracks (device=%r clean=%s)",
             os.path.basename(blk), min(n, len(tracks)), len(tracks),
             AUDIO_DEVICE or "default", CLEAN_LEVEL)
    for src, raw, url in tracks[-n:]:
        phrase = build_announcement(src, raw, url)
        log.info("SPEAK  [%s] %s", src, phrase)
        speak(phrase)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", nargs="+", metavar="LOGFILE",
                    help="print clean+raw announcements for given log files and exit")
    ap.add_argument("--demo", nargs="?", type=int, const=3, metavar="N",
                    help="speak the last N tracks (default 3) from the current block, then exit")
    ap.add_argument("--say", metavar="TEXT",
                    help="speak TEXT once to test the voice/routing, then exit")
    args = ap.parse_args()

    if args.selftest:
        selftest(args.selftest); return
    if args.say is not None:
        setup_logging()
        log.info("SAY test (voice=%r device=%r): %s", VOICE or "default",
                 AUDIO_DEVICE or "default", args.say)
        speak(args.say); return
    if args.demo is not None:
        demo(args.demo); return

    setup_logging()
    log.info("Dispatch announcer online - voice=%r rate=%s device=%r clean=%s",
             VOICE or "default", RATE, AUDIO_DEVICE or "default", CLEAN_LEVEL)
    while True:                              # top-level supervisor: never die
        try:
            run_live()
        except KeyboardInterrupt:
            log.info("stopped by user"); break
        except Exception as e:
            log.error("loop crashed, restarting in %ss: %r", RETRY_SEC, e)
            time.sleep(RETRY_SEC)


if __name__ == "__main__":
    main()
