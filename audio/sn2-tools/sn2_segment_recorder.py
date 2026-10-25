#!/usr/bin/env python3
"""
sn2_segment_recorder.py — generic rolling MIDI recorder: captures every
message a script sends and renders it into a sequence of real Standard MIDI
Files, one per `segment_seconds` of real time (default 6 hours), for as
long as the script keeps running — not just a single capped recording like
sn2_recorder.py's one-shot auto-finalize. Channel/track-name agnostic, so
any script with a single MIDI-out port and up to 16 channels can reuse it.

Usage:
    import sn2_segment_recorder as segrec

    recorder = segrec.SegmentedRecorder(
        name_prefix="powwow",
        tracks=[(f"Part {i + 1}", i) for i in range(8)],   # (track name, 0-indexed channel)
        segment_seconds=6 * 60 * 60,
    )
    ...
    recorder.record(raw_midi_bytes)   # call at the point every message is actually sent
    ...
    recorder.stop()                   # on shutdown -- finalizes the last (partial) segment

Each segment is independent: the moment one is finalized (rendered to
.mid), a new JSONL log + .mid pair starts immediately, so a multi-day run
produces a steady stream of ready-to-open ~6-hour .mid files in
~/Music/sn2-recordings/ instead of one unbounded file or a single cutoff.
Rotation is driven by wall-clock time, not musical time, so it keeps
segmenting even while the source script is paused.

Same crash-safety shape as sn2_recorder.py: record() is non-blocking and
never raises (a background thread owns all file I/O, flushed to disk every
~20 events or ~2 idle seconds), so the JSONL log is the source of truth —
if the process dies mid-segment, that segment's .mid can be rendered from
its JSONL log directly:
    python3 sn2_segment_recorder.py <logfile.jsonl> "Part 1" "Part 2" ...
(track names in channel order, starting at channel 0; defaults to 8 parts
if omitted.)
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


def _mido_message_from_raw(raw, channel):
    if len(raw) < 2:
        return None
    status = raw[0] & 0xF0
    if status == 0x90:
        vel = raw[2] if len(raw) > 2 else 0
        if vel == 0:
            return mido.Message('note_off', channel=channel, note=raw[1], velocity=0)
        return mido.Message('note_on', channel=channel, note=raw[1], velocity=vel)
    if status == 0x80:
        return mido.Message('note_off', channel=channel, note=raw[1], velocity=raw[2] if len(raw) > 2 else 0)
    if status == 0xB0:
        return mido.Message('control_change', channel=channel, control=raw[1], value=raw[2])
    if status == 0xC0:
        return mido.Message('program_change', channel=channel, program=raw[1])
    if status == 0xE0:
        raw14 = raw[1] | (raw[2] << 7)
        return mido.Message('pitchwheel', channel=channel, pitch=raw14 - 8192)
    return None  # not a message type we notate (shouldn't occur)


def render_mid(jsonl_path, mid_path, tracks):
    """tracks: list of (name, channel) -- one output track per entry, in
    that order, plus a leading conductor track. Tolerant of a truncated
    last JSONL line (e.g. the process died mid-write). Returns the number
    of note/CC/etc. events actually written."""
    if mido is None:
        raise RuntimeError("mido is not installed (pip install mido)")

    events_by_channel = {ch: [] for _, ch in tracks}
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw = ev.get("msg")
            if not raw:
                continue
            ch = raw[0] & 0x0F
            if ch not in events_by_channel:
                continue
            events_by_channel[ch].append((ev["t"], raw))

    tempo = mido.bpm2tempo(TEMPO_BPM)
    mid = mido.MidiFile(type=1, ticks_per_beat=PPQ)

    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage('track_name', name='Session', time=0))
    conductor.append(mido.MetaMessage('set_tempo', tempo=tempo, time=0))
    conductor.append(mido.MetaMessage('time_signature', numerator=4, denominator=4, time=0))
    conductor.append(mido.MetaMessage('end_of_track', time=0))
    mid.tracks.append(conductor)

    total_events = 0
    for name, ch in tracks:
        track = mido.MidiTrack()
        track.append(mido.MetaMessage('track_name', name=name, time=0))
        evs = sorted(events_by_channel.get(ch, []), key=lambda e: e[0])
        last_tick = 0
        for t, raw in evs:
            msg = _mido_message_from_raw(raw, ch)
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


class _Segment:
    """One rotating recording take: JSONL log + rendered .mid. record()
    never blocks or raises -- a background thread owns all file I/O."""

    def __init__(self, path_prefix, tracks):
        self.tracks = tracks
        self._start_monotonic = time.monotonic()
        self.jsonl_path = path_prefix + ".jsonl"
        self.mid_path = path_prefix + ".mid"
        self._queue = queue.Queue()
        self._fh = open(self.jsonl_path, "a")
        self._finalized = False
        self._finalize_lock = threading.Lock()
        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True,
                                                 name="segrec-writer")
        self._writer_thread.start()

    def record(self, msg):
        if self._finalized:
            return
        try:
            t = time.monotonic() - self._start_monotonic
            self._queue.put_nowait((t, list(msg)))
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

    def _write_event(self, t, msg):
        try:
            self._fh.write(json.dumps({"t": t, "msg": msg}) + "\n")
        except (OSError, ValueError):
            pass

    def _flush_fh(self):
        try:
            self._fh.flush()
            os.fsync(self._fh.fileno())
        except (OSError, ValueError):
            pass

    def finalize(self):
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
            n = render_mid(self.jsonl_path, self.mid_path, self.tracks)
            print(f"\n[recorder] segment saved: {self.mid_path} ({n} events)")
        except Exception as e:
            print(f"\n[recorder] could not render .mid ({e}); raw log kept at {self.jsonl_path}")
        return self.mid_path


class SegmentedRecorder:
    """Owns a sequence of _Segment takes, rotating to a fresh one every
    `segment_seconds` of real (wall-clock) time for as long as the process
    runs. Call .record(raw_midi_bytes) from wherever messages are actually
    sent; .stop() on shutdown to finalize the last (partial) segment."""

    def __init__(self, name_prefix, tracks, segment_seconds=6 * 60 * 60):
        os.makedirs(RECORDINGS_DIR, exist_ok=True)
        self.name_prefix = name_prefix
        self.tracks = tracks
        self.segment_seconds = segment_seconds
        self._lock = threading.Lock()
        self._running = True
        self._segment = self._new_segment()
        self._rotator = threading.Thread(target=self._rotate_loop, daemon=True,
                                          name="segrec-rotator")
        self._rotator.start()

    def _new_segment(self):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path_prefix = os.path.join(RECORDINGS_DIR, f"{self.name_prefix}_{ts}")
        hours = self.segment_seconds / 3600
        print(f"[recorder] recording -> {path_prefix}.mid (new file every {hours:g}h)")
        return _Segment(path_prefix, self.tracks)

    def record(self, msg):
        with self._lock:
            seg = self._segment
        seg.record(msg)

    def _rotate_loop(self):
        while self._running:
            slept = 0.0
            while self._running and slept < self.segment_seconds:
                step = min(5.0, self.segment_seconds - slept)
                time.sleep(step)
                slept += step
            if not self._running:
                return
            with self._lock:
                old = self._segment
                self._segment = self._new_segment()
            old.finalize()

    def stop(self):
        self._running = False
        with self._lock:
            seg = self._segment
        seg.finalize()


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 sn2_segment_recorder.py <logfile.jsonl> [track names in channel order...]")
        sys.exit(1)
    _jsonl_path = sys.argv[1]
    _names = sys.argv[2:] or [f"Part {i + 1}" for i in range(8)]
    _tracks = [(name, i) for i, name in enumerate(_names)]
    _mid_path = os.path.splitext(_jsonl_path)[0] + ".mid"
    _n = render_mid(_jsonl_path, _mid_path, _tracks)
    print(f"Wrote {_mid_path} ({_n} events)")
