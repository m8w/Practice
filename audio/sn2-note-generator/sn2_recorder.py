#!/usr/bin/env python3
"""
sn2_recorder.py — shared MIDI recording helper for sn2_chaos8_runs.py and
sn2_perpatch_runs.py. Import this and create one Recorder per session; call
.record(source, msg) at the emit layer for every MIDI message either script
sends, and .finalize() to render the take into a Standard MIDI File.

CAPTURE: record(source, msg) is meant to be called from _emit()/_emit_korg()
in the main scripts -- i.e. it sees every message either script actually
sends (notes, CCs, pitch bend, program/bank changes), tagged with which
logical source it came from:
    sn2_ch1..sn2_ch8   -- a Supernova Part, channels 1-8
    sn2_global16       -- the Supernova's Global channel (Performance/Bank
                           changes)
    korg9              -- the Korg's own channel, on its own port

CRASH-SAFE PERSISTENCE: every recorded message is put on an in-memory queue
(record() itself does no file I/O and is effectively instant, so it can't
add latency to the calling thread's note/CC timing) and written to an
on-disk JSONL log by a dedicated background thread, flushed to disk every
~20 events or ~2 seconds of idle time, whichever comes first. That JSONL
log is the source of truth -- even if the process dies before finalize()
runs, the log up to the last flush survives on disk and can be rendered
into a .mid separately (see the __main__ block below).

OUTPUT: finalize() closes the JSONL log and renders it into a type-1
(multi-track) Standard MIDI File in ~/Music/sn2-recordings/, one track per
source (8 Supernova Parts + Korg + the Supernova's Global/Performance
channel), each named, plus a leading conductor track carrying tempo/time
signature. PPQ 480, a fixed 120 BPM default tempo used only to convert the
recorded wall-clock timestamps into ticks -- the music itself is free-time,
so the notated result will look rhythmically complex; that's expected, the
goal is correctly-separated, faithfully-timed data, not sheet music.

Regenerate a .mid from a saved JSONL log directly (e.g. after a crash that
happened before finalize() got to run):
    python3 sn2_recorder.py <logfile.jsonl>
"""
import json
import os
import queue
import threading
import time
from datetime import datetime

try:
    import mido
except ImportError:
    mido = None

RECORDINGS_DIR = os.path.expanduser("~/Music/sn2-recordings")
PPQ = 480
TEMPO_BPM = 120

FLUSH_EVERY_N_EVENTS = 20
FLUSH_IDLE_SECONDS = 2.0

# source tag -> (track name, MIDI channel, 0-indexed)
SOURCE_TRACKS = {}
for _i in range(8):
    SOURCE_TRACKS[f"sn2_ch{_i + 1}"] = (f"Supernova Part {_i + 1}", _i)
SOURCE_TRACKS["korg9"] = ("Korg microKORG XL", 8)
SOURCE_TRACKS["sn2_global16"] = ("Supernova Global (Performances)", 15)

TRACK_ORDER = [f"sn2_ch{i + 1}" for i in range(8)] + ["korg9", "sn2_global16"]


class Recorder:
    """One instance per session. Thread-safe: record() is called from
    whichever background thread is sending a MIDI message (note_loop,
    run_loop, korg_note_loop, performance_loop, etc.) and must never block
    or raise -- recording is strictly best-effort and must never affect the
    actual music."""

    def __init__(self, name_prefix="sn2"):
        os.makedirs(RECORDINGS_DIR, exist_ok=True)
        self._start_monotonic = time.monotonic()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.jsonl_path = os.path.join(RECORDINGS_DIR, f"{name_prefix}_{ts}.jsonl")
        self.mid_path = os.path.join(RECORDINGS_DIR, f"{name_prefix}_{ts}.mid")

        self._queue = queue.Queue()
        self._fh = open(self.jsonl_path, "a")
        self._finalized = False
        self._finalize_lock = threading.Lock()

        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True, name="sn2-recorder-writer")
        self._writer_thread.start()
        print(f"Recording to: {self.jsonl_path}")

    def record(self, source, msg):
        """Cheap and non-blocking: just enqueues. The actual file write
        happens on the background writer thread. Never raises."""
        if self._finalized:
            return
        try:
            t = time.monotonic() - self._start_monotonic
            self._queue.put_nowait((t, source, list(msg)))
        except Exception:
            pass

    def _writer_loop(self):
        pending = 0
        while True:
            try:
                item = self._queue.get(timeout=FLUSH_IDLE_SECONDS)
            except queue.Empty:
                self._flush_fh()
                pending = 0
                continue
            if item is None:  # stop sentinel from finalize()
                self._drain_remaining()
                self._flush_fh()
                return
            self._write_event(*item)
            pending += 1
            if pending >= FLUSH_EVERY_N_EVENTS:
                self._flush_fh()
                pending = 0

    def _drain_remaining(self):
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            if item is not None:
                self._write_event(*item)

    def _write_event(self, t, source, msg):
        try:
            self._fh.write(json.dumps({"t": t, "source": source, "msg": msg}) + "\n")
        except (OSError, ValueError):
            pass

    def _flush_fh(self):
        try:
            self._fh.flush()
            os.fsync(self._fh.fileno())
        except (OSError, ValueError):
            pass

    def start_auto_finalize_timer(self, duration_seconds):
        """Finalizes automatically after duration_seconds -- the music keeps
        playing after that, recording just stops."""
        def _timer():
            time.sleep(duration_seconds)
            self.finalize()
        t = threading.Thread(target=_timer, daemon=True, name="sn2-recorder-auto-finalize")
        t.start()

    def finalize(self):
        """Stops recording, closes the JSONL log, and renders the .mid.
        Idempotent -- safe to call more than once (e.g. once from the
        2-hour timer and again from shutdown cleanup) and from multiple
        threads; only the first call does anything."""
        with self._finalize_lock:
            if self._finalized:
                return self.mid_path
            self._finalized = True

        self._queue.put(None)
        self._writer_thread.join(timeout=5.0)
        try:
            self._fh.close()
        except Exception:
            pass

        try:
            n = render_mid(self.jsonl_path, self.mid_path)
            print(f"\nRecording saved: {self.mid_path} ({n} events)")
        except Exception as e:
            print(f"\nCould not render .mid ({e}); raw log kept at {self.jsonl_path}")
        return self.mid_path


def _mido_message_from_raw(raw, channel):
    if len(raw) < 2:
        return None
    status = raw[0] & 0xF0
    if status == 0x90:
        return mido.Message('note_on', channel=channel, note=raw[1], velocity=raw[2])
    if status == 0x80:
        return mido.Message('note_off', channel=channel, note=raw[1], velocity=raw[2])
    if status == 0xB0:
        return mido.Message('control_change', channel=channel, control=raw[1], value=raw[2])
    if status == 0xC0:
        return mido.Message('program_change', channel=channel, program=raw[1])
    if status == 0xE0:
        raw14 = raw[1] | (raw[2] << 7)
        return mido.Message('pitchwheel', channel=channel, pitch=raw14 - 8192)
    return None  # not a message type we record/notate (shouldn't occur)


def render_mid(jsonl_path, mid_path):
    """Reads a JSONL event log and writes a type-1 Standard MIDI File to
    mid_path: one leading conductor track (tempo/time signature only), then
    one track per source in TRACK_ORDER, each named, carrying that source's
    notes/CCs/pitch-bend/program-changes as absolute-time-derived deltas.
    Tolerant of a truncated last line (e.g. the process died mid-write).
    Returns the number of note/CC/etc. events actually written (excluding
    meta events). Requires mido."""
    if mido is None:
        raise RuntimeError("mido is not installed (pip install mido)")

    events_by_source = {tag: [] for tag in SOURCE_TRACKS}
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue  # tolerate a truncated last line from a crash
            source = ev.get("source")
            if source not in events_by_source:
                continue
            events_by_source[source].append((ev["t"], ev["msg"]))

    tempo = mido.bpm2tempo(TEMPO_BPM)
    mid = mido.MidiFile(type=1, ticks_per_beat=PPQ)

    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage('track_name', name='SN2 Session', time=0))
    conductor.append(mido.MetaMessage('set_tempo', tempo=tempo, time=0))
    conductor.append(mido.MetaMessage('time_signature', numerator=4, denominator=4, time=0))
    conductor.append(mido.MetaMessage('end_of_track', time=0))
    mid.tracks.append(conductor)

    total_events = 0
    for tag in TRACK_ORDER:
        name, channel = SOURCE_TRACKS[tag]
        track = mido.MidiTrack()
        track.append(mido.MetaMessage('track_name', name=name, time=0))

        evs = sorted(events_by_source.get(tag, []), key=lambda e: e[0])
        last_tick = 0
        for t, raw in evs:
            msg = _mido_message_from_raw(raw, channel)
            if msg is None:
                continue
            tick = round(mido.second2tick(t, PPQ, tempo))
            msg.time = max(0, tick - last_tick)
            track.append(msg)
            last_tick = tick
            total_events += 1

        track.append(mido.MetaMessage('end_of_track', time=0))
        mid.tracks.append(track)

    mid.save(mid_path)
    return total_events


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 2:
        print("Usage: python3 sn2_recorder.py <logfile.jsonl>")
        sys.exit(1)
    _jsonl_path = sys.argv[1]
    _mid_path = os.path.splitext(_jsonl_path)[0] + ".mid"
    _n = render_mid(_jsonl_path, _mid_path)
    print(f"Wrote {_mid_path} ({_n} events)")
