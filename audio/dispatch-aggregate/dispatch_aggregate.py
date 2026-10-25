#!/usr/bin/env python3
"""
dispatch_aggregate.py — bridge from the Aggregate Field broadcast page to Dispatch.

The page (standalone file, opened with #onair-dispatch or with Announcements set
to "Dispatch bridge") POSTs one JSON event per piece start, section change and
stop. This bridge:

  * speaks each event's `speak` text, one at a time, through macOS `say`
    (or through your own Dispatch announcer function with --announcer)
  * can send that speech to a specific output device (e.g. a BlackHole channel
    that radiot.py mixes) with --device
  * appends every event to a JSONL log
  * writes YouTube-style chapter lines (H:MM:SS Title) timed from the start of
    your recording, so the archive upload gets chapters for free

Standard library only. Python 3.8+.

Examples
  python3 dispatch_aggregate.py
  python3 dispatch_aggregate.py --voice Samantha --rate 165 --device "BlackHole 16ch"
  python3 dispatch_aggregate.py --announcer dispatch_announcer:announce
  python3 dispatch_aggregate.py --recording-start 2026-10-16T18:00:00
  python3 dispatch_aggregate.py --list-devices
  python3 dispatch_aggregate.py --test
"""
import argparse
import datetime as dt
import importlib
import json
import queue
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def parse_args():
    ap = argparse.ArgumentParser(description="Aggregate Field → Dispatch announcement bridge")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--voice", default=None, help="macOS voice name (see `say -v '?'`)")
    ap.add_argument("--rate", type=int, default=170, help="speech rate in words per minute")
    ap.add_argument("--device", default=None, help="audio output device for `say -a` (name or ID)")
    ap.add_argument("--announcer", default=None,
                    help="module:function to call with the text instead of `say`, e.g. dispatch_announcer:announce")
    ap.add_argument("--no-sections", action="store_true", help="speak piece intros only, log sections silently")
    ap.add_argument("--log", default="aggregate_field_log.jsonl", help="JSONL event log path")
    ap.add_argument("--chapters", default="aggregate_field_chapters.txt", help="chapter list path ('' to disable)")
    ap.add_argument("--recording-start", default=None,
                    help="ISO local time your recording started; chapter times count from here (default: bridge start)")
    ap.add_argument("--list-devices", action="store_true", help="list `say` output devices and exit")
    ap.add_argument("--test", action="store_true", help="speak a test line and exit")
    return ap.parse_args()


class Speaker:
    """Speaks queued lines one at a time so announcements never overlap."""

    def __init__(self, args):
        self.args = args
        self.q = queue.Queue()
        self.fn = None
        if args.announcer:
            mod_name, _, fn_name = args.announcer.partition(":")
            if not fn_name:
                sys.exit("--announcer needs module:function")
            self.fn = getattr(importlib.import_module(mod_name), fn_name)
        threading.Thread(target=self._run, daemon=True).start()

    def say(self, text):
        if text:
            self.q.put(text)

    def _run(self):
        while True:
            text = self.q.get()
            try:
                if self.fn:
                    self.fn(text)
                else:
                    cmd = ["say", "-r", str(self.args.rate)]
                    if self.args.voice:
                        cmd += ["-v", self.args.voice]
                    if self.args.device:
                        cmd += ["-a", self.args.device]
                    subprocess.run(cmd + [text], check=False)
            except Exception as e:  # keep the bridge alive whatever the voice does
                print(f"[speak error] {e}", file=sys.stderr)
            finally:
                self.q.task_done()


def fmt_clock(seconds):
    s = max(0, int(seconds))
    return f"{s // 3600}:{(s // 60) % 60:02d}:{s % 60:02d}"


def make_handler(args, speaker, t0):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"aggregate-field bridge ok\n")

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode("utf-8", "replace")
            self.send_response(204)
            self._cors()
            self.end_headers()
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                print(f"[bad event] {raw[:120]}", file=sys.stderr)
                return
            now = dt.datetime.now()
            ev["received"] = now.isoformat(timespec="seconds")
            kind = ev.get("type", "?")
            with lock:
                with open(args.log, "a", encoding="utf-8") as f:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
                if kind == "piece" and args.chapters:
                    who = " × ".join(ev.get("who", []))
                    line = f"{fmt_clock((now - t0).total_seconds())} No. {ev.get('no')} · {ev.get('title')} ({who})"
                    with open(args.chapters, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
            if kind == "piece":
                print(f"{now:%H:%M:%S}  PIECE   No. {ev.get('no')}  {ev.get('title')}  seed {ev.get('seed')}")
                speaker.say(ev.get("speak"))
            elif kind == "section":
                print(f"{now:%H:%M:%S}  SECTION {ev.get('section')}/{ev.get('of')}  {'; '.join(ev.get('changes', []))}")
                if not args.no_sections:
                    speaker.say(ev.get("speak"))
            elif kind == "stop":
                print(f"{now:%H:%M:%S}  STOP")
            else:
                print(f"{now:%H:%M:%S}  {kind}")

        def log_message(self, *a):  # silence default access log
            pass

    return Handler


def main():
    args = parse_args()
    sys.stdout.reconfigure(line_buffering=True)
    if args.list_devices:
        subprocess.run(["say", "-a", "?"])
        return
    speaker = Speaker(args)
    if args.test:
        speaker.say("Newbold Radio. Aggregate Field bridge test.")
        speaker.q.join()
        return
    t0 = dt.datetime.fromisoformat(args.recording_start) if args.recording_start else dt.datetime.now()
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(args, speaker, t0))
    print(f"Aggregate Field bridge listening on http://{args.host}:{args.port}/event")
    print(f"  log: {args.log}" + (f"   chapters: {args.chapters} (from {t0:%H:%M:%S})" if args.chapters else ""))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbridge stopped")


if __name__ == "__main__":
    main()
