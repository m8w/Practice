#!/usr/bin/env python3
"""
vital_bridge.py — Python <-> Vital wavetable bridge
===================================================

Two halves:

GENERATE wavetables Vital can import (.wav, 2048-sample frames, Serum-style 'clm ' chunk)
    python3 vital_bridge.py gen spectral --mode formant --frames 64 --seed 7
    python3 vital_bridge.py gen glyph    --keys 5 --spin 90 --seed 3
    python3 vital_bridge.py gen audio    --input recording.wav --frames 128

PLAY Vital from Python over its own MIDI port (never writes to IAC Bus 1)
    python3 vital_bridge.py ports
    python3 vital_bridge.py learn                     # map CCs to Vital knobs one at a time
    python3 vital_bridge.py play --mode auto --motion lorenz
    python3 vital_bridge.py play --follow "IAC Driver Bus 1" --no-notes
        -> your existing generative stream plays Vital, Python only sweeps the tables

Requires: numpy (gen), mido + python-rtmidi (ports/learn/play)
    pip3 install numpy mido python-rtmidi
"""

import argparse
import heapq
import itertools
import json
import math
import os
import random
import struct
import sys
import time

import numpy as np

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

FRAME_SIZE = 2048          # Vital's native frame length
MAX_FRAMES = 256           # Vital's frame limit
VIRTUAL_PORT_NAME = "Vital Bridge"

# Default CC lanes. Any free CC works because Vital assigns them by MIDI learn.
DEFAULT_LANES = [
    ("Osc 1 wavetable position", 21),
    ("Osc 2 wavetable position", 22),
    ("Osc 3 wavetable position", 23),
    ("Osc 1 spectral morph amount", 24),
    ("Filter 1 cutoff", 25),
]

SCALES = {
    "major":      [0, 2, 4, 5, 7, 9, 11],
    "minor":      [0, 2, 3, 5, 7, 8, 10],
    "dorian":     [0, 2, 3, 5, 7, 9, 10],
    "phrygian":   [0, 1, 3, 5, 7, 8, 10],
    "lydian":     [0, 2, 4, 6, 7, 9, 11],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "pentatonic": [0, 2, 4, 7, 9],
    "minpent":    [0, 3, 5, 7, 10],
    "wholetone":  [0, 2, 4, 6, 8, 10],
    "hirajoshi":  [0, 2, 3, 7, 8],
    "chromatic":  list(range(12)),
}


# ===========================================================================
# PART 1 — WAVETABLE GENERATION
# ===========================================================================

def finish_frames(frames, per_frame_norm=True):
    """Remove DC and normalise each frame (or the whole table)."""
    out = []
    for f in frames:
        f = np.asarray(f, dtype=np.float64)
        f = f - f.mean()
        out.append(f)
    if per_frame_norm:
        res = []
        for f in out:
            peak = np.max(np.abs(f))
            res.append(f / peak * 0.95 if peak > 1e-9 else f)
        return res
    peak = max(np.max(np.abs(f)) for f in out)
    return [f / peak * 0.95 if peak > 1e-9 else f for f in out]


def write_wavetable(path, frames, frame_size=FRAME_SIZE, sr=44100, pcm16=False):
    """Write frames as one mono WAV with a 'clm ' chunk declaring the frame size.

    Vital (and Serum) read the clm chunk; without it Vital still assumes 2048.
    """
    data = np.concatenate(frames).astype(np.float64)
    if pcm16:
        audio_fmt, bits = 1, 16
        payload = (np.clip(data, -1, 1) * 32767).round().astype("<i2").tobytes()
        fmt_body = struct.pack("<HHIIHH", audio_fmt, 1, sr, sr * 2, 2, bits)
    else:
        audio_fmt, bits = 3, 32
        payload = data.astype("<f4").tobytes()
        fmt_body = struct.pack("<HHIIHHH", audio_fmt, 1, sr, sr * 4, 4, bits, 0)

    def chunk(cid, body):
        pad = b"\x00" if len(body) % 2 else b""
        return cid + struct.pack("<I", len(body)) + body + pad

    clm = f"<!>{frame_size} 00000000 wavetable (vital_bridge)".encode("ascii")
    chunks = chunk(b"fmt ", fmt_body)
    if not pcm16:
        chunks += chunk(b"fact", struct.pack("<I", len(data)))
    chunks += chunk(b"clm ", clm) + chunk(b"data", payload)
    riff = b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks
    with open(path, "wb") as fh:
        fh.write(riff)


def read_wav_mono(path):
    """Read PCM 8/16/24/32-bit or float 32/64 WAV, mixed to mono float64."""
    with open(path, "rb") as fh:
        b = fh.read()
    if b[:4] != b"RIFF" or b[8:12] != b"WAVE":
        raise ValueError(f"{path} is not a RIFF/WAVE file")
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(b):
        cid = b[pos:pos + 4]
        size = struct.unpack("<I", b[pos + 4:pos + 8])[0]
        body = b[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = body
        elif cid == b"data":
            data = body
        pos += 8 + size + (size & 1)
    if fmt is None or data is None:
        raise ValueError(f"{path}: missing fmt or data chunk")
    tag, ch, sr, _, block, bits = struct.unpack("<HHIIHH", fmt[:16])
    if tag == 0xFFFE and len(fmt) >= 26:            # WAVE_FORMAT_EXTENSIBLE
        tag = struct.unpack("<H", fmt[24:26])[0]
    n = len(data) // block
    data = data[:n * block]
    if tag == 3:
        x = np.frombuffer(data, dtype="<f4" if bits == 32 else "<f8").astype(np.float64)
    elif tag == 1:
        if bits == 8:
            x = (np.frombuffer(data, dtype=np.uint8).astype(np.float64) - 128) / 128
        elif bits == 16:
            x = np.frombuffer(data, dtype="<i2") / 32768.0
        elif bits == 24:
            raw = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3)
            v = (raw[:, 0].astype(np.int32) | (raw[:, 1].astype(np.int32) << 8)
                 | (raw[:, 2].astype(np.int32) << 16))
            v = np.where(v & 0x800000, v - 0x1000000, v)
            x = v / 8388608.0
        elif bits == 32:
            x = np.frombuffer(data, dtype="<i4") / 2147483648.0
        else:
            raise ValueError(f"unsupported bit depth {bits}")
    else:
        raise ValueError(f"unsupported WAV format tag {tag}")
    return x.reshape(-1, ch).mean(axis=1), sr


def ease(t):
    return 0.5 - 0.5 * math.cos(math.pi * t)


def key_interp(keys, n_frames):
    """Yield (a, b, t) so frames morph smoothly through a list of keyframes."""
    segs = len(keys) - 1
    for i in range(n_frames):
        u = i / max(1, n_frames - 1) * segs
        k = min(int(u), segs - 1)
        yield keys[k], keys[k + 1], ease(u - k)


# ---- spectral --------------------------------------------------------------

def _primes_upto(n):
    sieve = np.ones(n + 1, dtype=bool)
    sieve[:2] = False
    for p in range(2, int(n ** 0.5) + 1):
        if sieve[p]:
            sieve[p * p::p] = False
    return np.nonzero(sieve)[0]


def spectral_key(mode, rng, h, k, n_keys):
    H = len(h)
    if mode == "drift":
        tilt = rng.uniform(0.6, 1.8)
        amps = h ** -tilt
        amps *= rng.random(H) < rng.uniform(0.15, 0.9)      # sparseness
        amps[0] = max(amps[0], 0.3)
        amps *= rng.uniform(0.3, 1.0, H)
    elif mode == "oddeven":
        t = k / max(1, n_keys - 1)                          # odd -> even across keys
        odd = (h % 2 == 1).astype(float)
        amps = (odd * (1 - t) + (1 - odd) * t) * h ** -1.0
        amps[0] = 1.0
    elif mode == "primes":
        mask = np.isin(h, _primes_upto(H))
        mask[0] = True
        amps = mask * h ** -rng.uniform(0.5, 1.3)
        amps *= rng.uniform(0.4, 1.0, H)
    elif mode == "formant":
        amps = np.zeros(H)
        for _ in range(rng.integers(2, 5)):
            centre = rng.uniform(2, 60)
            width = rng.uniform(1.0, 6.0)
            amps += rng.uniform(0.4, 1.0) * np.exp(-0.5 * ((h - centre) / width) ** 2)
        amps += 0.15 * h ** -1.2
    else:
        raise ValueError(f"unknown spectral mode {mode}")
    return amps


def gen_spectral(args, rng):
    H = min(args.max_harmonic, FRAME_SIZE // 2 - 1)
    h = np.arange(1, H + 1, dtype=np.float64)
    keys = [spectral_key(args.mode, rng, h, k, args.keys) for k in range(args.keys)]
    phases = rng.uniform(0, 2 * np.pi, H) if args.random_phase else np.full(H, -np.pi / 2)
    frames = []
    for a, b, t in key_interp(keys, args.frames):
        amps = a * (1 - t) + b * t
        spec = np.zeros(FRAME_SIZE // 2 + 1, dtype=complex)
        spec[1:H + 1] = amps * np.exp(1j * phases)
        frames.append(np.fft.irfft(spec, n=FRAME_SIZE))
    return frames


# ---- glyph -----------------------------------------------------------------

def closed_catmull_rom(pts, samples_per_seg=64):
    pts = np.asarray(pts, dtype=np.float64)
    n = len(pts)
    out = []
    for i in range(n):
        p0, p1, p2, p3 = pts[(i - 1) % n], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        t = np.linspace(0, 1, samples_per_seg, endpoint=False)[:, None]
        out.append(0.5 * ((2 * p1) + (-p0 + p2) * t
                          + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t ** 2
                          + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    return np.vstack(out)


def resample_closed_curve(curve, n, by_arclength=False):
    """Resample a closed curve to n points.

    by_arclength=False ('pen'): even steps in the spline's own parameter, so a
    long stroke is crossed fast. That speed variation is what puts harmonics in
    the waveform - it reads like a pen moving, not a shape traced at constant rate.
    by_arclength=True ('even'): constant speed, much softer and more sine-like.
    """
    loop = np.vstack([curve, curve[:1]])
    if by_arclength:
        seg = np.linalg.norm(np.diff(loop, axis=0), axis=1)
        s = np.concatenate([[0], np.cumsum(seg)])
    else:
        s = np.arange(len(loop), dtype=np.float64)
    target = np.linspace(0, s[-1], n, endpoint=False)
    return np.column_stack([np.interp(target, s, loop[:, 0]),
                            np.interp(target, s, loop[:, 1])])


def random_glyph(rng, n_points, sharp=0.3):
    """An asemic stroke: a looping path that wanders, crosses itself, returns.

    `sharp` is the fraction of points the pen pauses on. A repeated control point
    makes Catmull-Rom stall and then turn a hard corner, which is both what a
    written mark looks like and where the waveform gets its harmonics.
    """
    ang = np.sort(rng.uniform(0, 2 * np.pi, n_points))
    rad = rng.uniform(0.2, 1.0, n_points)
    pts = np.column_stack([rad * np.cos(ang), rad * np.sin(ang)])
    # a few self-crossing jumps make it read as a written mark, not a blob
    for _ in range(rng.integers(1, max(2, n_points // 3))):
        i, j = rng.integers(0, n_points, 2)
        pts[[i, j]] = pts[[j, i]]
    n_sharp = int(round(sharp * n_points))
    if n_sharp:
        for i in sorted(rng.choice(n_points, n_sharp, replace=False), reverse=True):
            pts = np.insert(pts, i, pts[i], axis=0)      # pause, then corner
    return pts


def gen_glyph(args, rng):
    if args.points:
        with open(args.points) as fh:
            glyph_pts = [np.asarray(g, dtype=np.float64) for g in json.load(fh)]
        if len(glyph_pts) < 2:
            glyph_pts.append(glyph_pts[0])
    else:
        glyph_pts = [random_glyph(rng, args.points_per_glyph, args.sharp)
                     for _ in range(args.keys)]
    curves = []
    for g in glyph_pts:
        g = g - g.mean(axis=0)
        g = g / (np.max(np.abs(g)) or 1)
        curves.append(resample_closed_curve(closed_catmull_rom(g), FRAME_SIZE,
                                            by_arclength=(args.read == "even")))
    frames = []
    for i, (a, b, t) in enumerate(key_interp(curves, args.frames)):
        c = a * (1 - t) + b * t
        theta = math.radians(args.spin) * i / max(1, args.frames - 1)
        frames.append(c[:, 0] * math.sin(theta) + c[:, 1] * math.cos(theta))
    return frames


# ---- audio slicing ---------------------------------------------------------

def detect_period(seg, sr, min_hz, max_hz):
    n = len(seg)
    spec = np.fft.rfft(seg - seg.mean(), 2 * n)
    r = np.fft.irfft(np.abs(spec) ** 2)[:n]
    if r[0] <= 1e-12:
        return None, 0.0
    r = r / r[0]
    lo, hi = int(sr / max_hz), min(int(sr / min_hz), n - 1)
    if hi <= lo:
        return None, 0.0
    lag = lo + int(np.argmax(r[lo:hi]))
    return lag, float(r[lag])


def gen_audio(args, rng):
    x, sr = read_wav_mono(args.input)
    win = int(sr / args.min_hz) * 3
    if len(x) < win * 2:
        raise ValueError("input file is too short for the requested min pitch")
    fallback = int(sr / 110)
    starts = np.linspace(0, len(x) - win - 1, args.frames).astype(int)
    frames, prev = [], None
    for p in starts:
        seg = x[p:p + win]
        if np.sqrt(np.mean(seg ** 2)) < 1e-4:          # silence -> hold previous
            if prev is not None:
                frames.append(prev)
            continue
        lag, conf = detect_period(seg, sr, args.min_hz, args.max_hz)
        if lag is None or conf < 0.3:
            lag = fallback
        zc = np.nonzero((seg[:-1] <= 0) & (seg[1:] > 0))[0]
        s = int(zc[0]) if len(zc) and zc[0] + lag + 1 < len(seg) else 0
        cyc = seg[s:s + lag].copy()
        # remove the jump between the end of the cycle and the next sample
        cyc -= np.linspace(0, 1, len(cyc), endpoint=False) * (seg[s + lag] - seg[s])
        grid = np.linspace(0, len(cyc), FRAME_SIZE, endpoint=False)
        frame = np.interp(grid, np.arange(len(cyc) + 1), np.append(cyc, cyc[0]))
        frames.append(frame)
        prev = frame
    if not frames:
        raise ValueError("no audible material found in input")
    while len(frames) < args.frames:
        frames.append(frames[-1])
    return frames


def save_preview(path, frames):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  (matplotlib not installed - skipping preview)")
        return
    n = len(frames)
    step = max(1, n // 24)
    fig, ax = plt.subplots(figsize=(8, 6), dpi=110)
    fig.patch.set_facecolor("#111")
    ax.set_facecolor("#111")
    xs = np.linspace(0, 1, FRAME_SIZE)
    for k, i in enumerate(range(0, n, step)):
        off = k * 0.35
        ax.plot(xs + k * 0.012, frames[i] * 0.6 + off, lw=0.8,
                color=plt.cm.viridis(i / max(1, n - 1)))
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)


def cmd_gen(args):
    if not 2 <= args.frames <= MAX_FRAMES:
        sys.exit(f"--frames must be between 2 and {MAX_FRAMES} (Vital's limit)")
    seed = args.seed if args.seed is not None else random.randrange(1_000_000)
    rng = np.random.default_rng(seed)
    if args.kind == "spectral":
        frames = gen_spectral(args, rng)
        tag = f"spectral-{args.mode}"
    elif args.kind == "glyph":
        frames = gen_glyph(args, rng)
        tag = "glyph"
    else:
        frames = gen_audio(args, rng)
        tag = "audio-" + os.path.splitext(os.path.basename(args.input))[0]
    frames = finish_frames(frames, per_frame_norm=not args.table_norm)
    os.makedirs(args.outdir, exist_ok=True)
    name = args.name or f"{tag}_{seed}"
    path = os.path.join(args.outdir, name + ".wav")
    write_wavetable(path, frames, pcm16=args.pcm16)
    print(f"wrote {path}  ({len(frames)} frames x {FRAME_SIZE}, seed {seed})")
    if args.preview:
        png = os.path.join(args.outdir, name + ".png")
        save_preview(png, frames)
        if os.path.exists(png):
            print(f"preview {png}")


# ===========================================================================
# PART 2 — PLAYING VITAL OVER MIDI
# ===========================================================================

def need_mido():
    try:
        import mido
        return mido
    except ImportError:
        sys.exit("mido is not installed: pip3 install mido python-rtmidi")


class MidoOut:
    def __init__(self, port, channel):
        import mido
        self.mido, self.port, self.ch = mido, port, channel

    def on(self, note, vel):
        self.port.send(self.mido.Message("note_on", channel=self.ch, note=note, velocity=vel))

    def off(self, note):
        self.port.send(self.mido.Message("note_off", channel=self.ch, note=note, velocity=0))

    def cc(self, num, val):
        self.port.send(self.mido.Message("control_change", channel=self.ch, control=num, value=val))

    def close(self):
        self.port.close()


class PrintOut:
    """--dry-run: print what would be sent."""
    def __init__(self, t0, quiet_cc=False):
        self.t0, self.quiet_cc = t0, quiet_cc

    def _p(self, s):
        print(f"{time.monotonic() - self.t0:8.3f}  {s}")

    def on(self, note, vel):
        self._p(f"note_on  {note:3d} vel {vel}")

    def off(self, note):
        self._p(f"note_off {note:3d}")

    def cc(self, num, val):
        if not self.quiet_cc:
            self._p(f"cc {num:3d} = {val}")

    def close(self):
        pass


def open_output(args, t0):
    if args.dry_run:
        return PrintOut(t0, quiet_cc=getattr(args, "quiet_cc", False))
    mido = need_mido()
    ch = args.channel - 1
    if args.port:
        names = mido.get_output_names()
        match = [n for n in names if args.port.lower() in n.lower()]
        if not match:
            sys.exit(f"no output port matching '{args.port}'. Available: {names}")
        # Bus 1 carries the visual + SN2 streams; never send there, even on a loose match.
        safe = [n for n in match if "iac driver bus 1" not in n.lower()]
        if not safe:
            sys.exit("refusing to write to IAC Bus 1 (it carries your visual and SN2 "
                     "streams). Use the virtual port, or a separate IAC bus.")
        if len(safe) < len(match):
            print("note: skipped IAC Bus 1")
        if len(safe) > 1:
            print(f"note: '{args.port}' matched {safe}; using the first")
        print(f"output: {safe[0]}")
        return MidoOut(mido.open_output(safe[0]), ch)
    print(f"output: virtual port '{VIRTUAL_PORT_NAME}' (exists while this script runs)")
    return MidoOut(mido.open_output(VIRTUAL_PORT_NAME, virtual=True), ch)


class NoteBook:
    """Reference-counted notes so overlapping chords never cut each other off."""
    def __init__(self, out):
        self.out, self.count = out, {}

    def on(self, note, vel):
        c = self.count.get(note, 0)
        if c == 0:
            self.out.on(note, vel)
        self.count[note] = c + 1

    def off(self, note):
        c = self.count.get(note, 0)
        if c <= 1:
            self.count.pop(note, None)
            if c == 1:
                self.out.off(note)
        else:
            self.count[note] = c - 1

    def panic(self):
        for note in list(self.count):
            self.out.off(note)
        self.count.clear()
        try:
            self.out.cc(123, 0)   # all notes off
        except Exception:
            pass


# ---- motion sources (wavetable position curves) ----------------------------

class Motion:
    def __init__(self, n_lanes, speed, rng):
        self.n, self.speed, self.rng = n_lanes, speed, rng

    def kick(self, amount):
        pass


class Lorenz(Motion):
    def __init__(self, *a):
        super().__init__(*a)
        self.s = np.array([self.rng.uniform(-10, 10), self.rng.uniform(-10, 10),
                           self.rng.uniform(10, 30)])

    def step(self, dt):
        h = 0.01 * self.speed
        for _ in range(max(1, int(dt / 0.025))):
            x, y, z = self.s
            self.s = self.s + h * np.array([10 * (y - x), x * (28 - z) - y, x * y - 8 / 3 * z])
        nx = np.clip((self.s[0] + 20) / 40, 0, 1)
        ny = np.clip((self.s[1] + 27) / 54, 0, 1)
        nz = np.clip((self.s[2] - 5) / 45, 0, 1)
        feats = [nx, ny, nz, (nx + nz) / 2, 1 - ny]
        return [feats[i % len(feats)] for i in range(self.n)]

    def kick(self, amount):
        self.s = self.s + self.rng.normal(0, 4 * amount, 3)


class Walk(Motion):
    def __init__(self, *a):
        super().__init__(*a)
        self.v = self.rng.uniform(0.2, 0.8, self.n)

    def step(self, dt):
        theta, sigma = 0.05, 0.12 * self.speed
        self.v += theta * (0.5 - self.v) * dt + sigma * math.sqrt(dt) * self.rng.normal(0, 1, self.n)
        self.v = np.where(self.v < 0, -self.v, self.v)
        self.v = np.where(self.v > 1, 2 - self.v, self.v)
        return list(self.v)

    def kick(self, amount):
        self.v = np.clip(self.v + self.rng.normal(0, 0.3 * amount, self.n), 0, 1)


class LFO(Motion):
    def __init__(self, *a):
        super().__init__(*a)
        phi = (1 + 5 ** 0.5) / 2
        self.periods = [(23 + 7 * i) * phi ** (i % 3) for i in range(self.n)]
        self.ph = self.rng.uniform(0, 2 * np.pi, self.n)
        self.t = 0.0

    def step(self, dt):
        self.t += dt * self.speed
        out = []
        for i, p in enumerate(self.periods):
            a = math.sin(2 * math.pi * self.t / p + self.ph[i])
            b = math.sin(2 * math.pi * self.t / (p * 2.718) + 2 * self.ph[i])
            out.append(0.5 + 0.35 * a + 0.15 * b)
        return out

    def kick(self, amount):
        self.ph += self.rng.normal(0, amount, self.n)


MOTIONS = {"lorenz": Lorenz, "walk": Walk, "lfo": LFO}


# ---- composer ---------------------------------------------------------------

class Composer:
    MODES = ["drift", "drone", "pulse", "scatter", "silence"]
    WEIGHTS = [0.3, 0.25, 0.2, 0.15, 0.1]

    def __init__(self, args, rng, schedule, on_note):
        self.args, self.rng = args, rng
        self.schedule, self.on_note = schedule, on_note
        self.scale = SCALES[args.scale]
        self.deg = len(self.scale)           # start an octave above root
        self.mode = args.mode
        self.section_end = 0.0

    def pitch(self, deg):
        deg = max(0, min(deg, len(self.scale) * self.args.octaves))
        o, i = divmod(deg, len(self.scale))
        return self.args.root + 12 * o + self.scale[i]

    def walk(self, spread=2):
        self.deg += int(self.rng.integers(-spread, spread + 1))
        top = len(self.scale) * self.args.octaves
        if self.deg < 0 or self.deg > top:
            self.deg = top // 2
        return self.deg

    def note(self, t, deg, dur, vel):
        n = self.pitch(deg)
        self.schedule(t, ("on", n, int(vel)))
        self.schedule(t + dur, ("off", n))
        self.on_note()

    def step(self, now):
        a, r = self.args, self.rng
        if a.mode == "auto" and now >= self.section_end:
            self.mode = str(r.choice(self.MODES, p=self.WEIGHTS))
            self.section_end = now + r.uniform(a.section_min, a.section_max)
            print(f"[section] {self.mode} for {self.section_end - now:.0f}s")
        m = self.mode
        if m == "drone":
            hold = r.uniform(20, 50)
            root = self.walk(3)
            shapes = [[0, 2, 4], [0, 4, 7], [0, 3, 7], [0, 2, 4, 7]]
            for off in shapes[int(r.integers(len(shapes)))]:
                self.note(now, root + off, hold, r.integers(45, 75))
            return now + hold - 3                  # 3 s overlap into next chord
        if m == "drift":
            for _ in range(int(r.integers(1, 3))):
                self.note(now + r.uniform(0, 0.4), self.walk(2), r.uniform(3, 12), r.integers(40, 90))
            return now + r.uniform(2, 9)
        if m == "pulse":
            step = 60 / a.bpm / 2
            if r.random() < 0.8:
                self.note(now, self.walk(2), step * 0.6, r.integers(60, 100))
            return now + step
        if m == "scatter":
            t = now
            for _ in range(int(r.integers(3, 10))):
                self.note(t, self.walk(4), r.uniform(0.1, 0.6), r.integers(30, 110))
                t += r.uniform(0.06, 0.25)
            return t + r.uniform(1, 5)
        # silence
        return max(now + 1, self.section_end) if a.mode == "auto" else now + r.uniform(5, 15)


def parse_lanes(spec):
    if not spec:
        return DEFAULT_LANES
    nums = [int(x) for x in spec.split(",") if x.strip()]
    names = [n for n, _ in DEFAULT_LANES]
    return [(names[i] if i < len(names) else f"lane {i + 1}", c) for i, c in enumerate(nums)]


def cmd_play(args):
    t0 = time.monotonic()
    rng = np.random.default_rng(args.seed)
    lanes = parse_lanes(args.cc)
    lo, hi = (float(v) for v in args.range.split(","))
    out = open_output(args, t0)
    book = NoteBook(out)
    motion = MOTIONS[args.motion](len(lanes), args.speed, rng)

    inport = None
    if args.follow:
        mido = need_mido()
        names = mido.get_input_names()
        match = [n for n in names if args.follow.lower() in n.lower()]
        if not match:
            sys.exit(f"no input port matching '{args.follow}'. Available: {names}")
        inport = mido.open_input(match[0])
        print(f"following: {match[0]}")

    heap, seq = [], itertools.count()

    def schedule(t, ev):
        heapq.heappush(heap, (t, next(seq), ev))

    def on_note():
        if rng.random() < args.kick:
            motion.kick(0.5)

    composer = None if args.no_notes else Composer(args, rng, schedule, on_note)
    next_comp = t0
    mod_dt = 1.0 / args.cc_rate
    next_mod = t0
    last_cc = {}
    end = t0 + args.duration if args.duration > 0 else float("inf")

    print("lanes: " + ", ".join(f"CC{c} -> {n}" for n, c in lanes))
    print("Ctrl-C to stop.")
    try:
        while True:
            now = time.monotonic()
            if now >= end:
                break
            while heap and heap[0][0] <= now:
                _, _, ev = heapq.heappop(heap)
                if ev[0] == "on":
                    book.on(ev[1], ev[2])
                else:
                    book.off(ev[1])
            if composer and now >= next_comp:
                next_comp = composer.step(now)
            if now >= next_mod:
                vals = motion.step(mod_dt)
                for (name, cc), v in zip(lanes, vals):
                    val = int(round(127 * (lo + (hi - lo) * float(v))))
                    val = max(0, min(127, val))
                    if last_cc.get(cc) != val:
                        out.cc(cc, val)
                        last_cc[cc] = val
                next_mod += mod_dt
                if next_mod < now:                 # fell behind; don't burst
                    next_mod = now + mod_dt
            if inport:
                for msg in inport.iter_pending():
                    if msg.type == "note_on" and msg.velocity > 0:
                        book.on(msg.note, msg.velocity)
                        if rng.random() < args.kick:
                            motion.kick(msg.velocity / 127)
                    elif msg.type in ("note_off", "note_on"):
                        book.off(msg.note)
            nxt = min([next_mod, end] + ([heap[0][0]] if heap else [])
                      + ([next_comp] if composer else []))
            wait = nxt - time.monotonic()
            time.sleep(min(max(wait, 0), 0.005 if inport else 0.05))
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        book.panic()
        if inport:
            inport.close()
        out.close()


def cmd_learn(args):
    t0 = time.monotonic()
    lanes = parse_lanes(args.cc)
    if args.single is not None:
        lanes = [(f"CC{args.single}", args.single)]
    out = open_output(args, t0)
    print("In Vital: right-click a knob -> 'Learn MIDI Assignment'. This sends one CC at a "
          "time, sweeping slowly, so Vital grabs the right one.")
    try:
        for name, cc in lanes:
            if len(lanes) > 1 and not args.dry_run:
                input(f"\nArm '{name}' in Vital, then press Enter to send CC{cc}...")
            print(f"sending CC{cc} for {args.seconds:.0f}s")
            start = time.monotonic()
            while time.monotonic() - start < args.seconds:
                ph = (time.monotonic() - start) / 4.0
                tri = 1 - abs((ph % 1) * 2 - 1)
                out.cc(cc, int(16 + 96 * tri))
                time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        out.close()
    print("done")


def cmd_ports(args):
    mido = need_mido()
    print("outputs:")
    for n in mido.get_output_names():
        print("   ", n)
    print("inputs:")
    for n in mido.get_input_names():
        print("   ", n)


# ===========================================================================
# CLI
# ===========================================================================

def build_parser():
    p = argparse.ArgumentParser(description="Python <-> Vital wavetable bridge",
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    # gen ----------------------------------------------------------------
    g = sub.add_parser("gen", help="generate a wavetable .wav for Vital")
    gsub = g.add_subparsers(dest="kind", required=True)

    def common(sp):
        sp.add_argument("--frames", type=int, default=64, help="frames in the table (2-256)")
        sp.add_argument("--seed", type=int, help="random seed (printed so you can recreate)")
        sp.add_argument("--outdir", default="wavetables")
        sp.add_argument("--name", help="output file name (no extension)")
        sp.add_argument("--pcm16", action="store_true", help="16-bit PCM instead of 32-bit float")
        sp.add_argument("--table-norm", action="store_true",
                        help="normalise the whole table instead of each frame")
        sp.add_argument("--preview", action="store_true", help="also write a PNG preview")

    s = gsub.add_parser("spectral", help="morph between additive spectra")
    common(s)
    s.add_argument("--mode", choices=["drift", "formant", "primes", "oddeven"], default="drift")
    s.add_argument("--keys", type=int, default=4, help="spectral keyframes to morph through")
    s.add_argument("--max-harmonic", type=int, default=256)
    s.add_argument("--random-phase", action="store_true")

    gl = gsub.add_parser("glyph", help="asemic strokes read as waveforms")
    common(gl)
    gl.add_argument("--keys", type=int, default=4, help="glyphs to morph through")
    gl.add_argument("--points-per-glyph", type=int, default=9)
    gl.add_argument("--points", help="JSON file: list of glyphs, each a list of [x, y]")
    gl.add_argument("--spin", type=float, default=0.0,
                    help="degrees the reading angle rotates across the table")
    gl.add_argument("--read", choices=["pen", "even"], default="even",
                    help="how the stroke is traced into a cycle. even (default) follows it at "
                         "constant speed, which sharpens cusps and sounds brighter; pen gives "
                         "every stroke equal time, which smooths cusps and sounds softer")
    gl.add_argument("--sharp", type=float, default=0.3,
                    help="fraction of points the pen pauses on, making hard corners (0-1)")

    a = gsub.add_parser("audio", help="slice single cycles out of a recording")
    common(a)
    a.add_argument("--input", required=True, help="WAV file")
    a.add_argument("--min-hz", type=float, default=40.0)
    a.add_argument("--max-hz", type=float, default=1000.0)

    # play ---------------------------------------------------------------
    def out_args(sp):
        sp.add_argument("--port", help="existing output port (substring); default: virtual port")
        sp.add_argument("--channel", type=int, default=1)
        sp.add_argument("--cc", help="comma list of CC numbers for the lanes (default 21-25)")
        sp.add_argument("--dry-run", action="store_true", help="print MIDI instead of sending")

    pl = sub.add_parser("play", help="play Vital and sweep its wavetables")
    out_args(pl)
    pl.add_argument("--mode", choices=["auto"] + Composer.MODES, default="auto")
    pl.add_argument("--motion", choices=list(MOTIONS), default="lorenz")
    pl.add_argument("--speed", type=float, default=1.0, help="motion speed multiplier")
    pl.add_argument("--range", default="0,1", help="CC range as fractions, e.g. 0.1,0.9")
    pl.add_argument("--cc-rate", type=float, default=40.0, help="CC updates per second")
    pl.add_argument("--kick", type=float, default=0.15,
                    help="chance a note-on jolts the motion (0-1)")
    pl.add_argument("--scale", choices=list(SCALES), default="dorian")
    pl.add_argument("--root", type=int, default=45, help="MIDI note of scale root")
    pl.add_argument("--octaves", type=int, default=3)
    pl.add_argument("--bpm", type=float, default=72.0)
    pl.add_argument("--section-min", type=float, default=60.0)
    pl.add_argument("--section-max", type=float, default=180.0)
    pl.add_argument("--follow", help="input port to forward notes from (e.g. 'IAC Driver Bus 1')")
    pl.add_argument("--no-notes", action="store_true", help="don't generate notes, only motion")
    pl.add_argument("--duration", type=float, default=0, help="seconds; 0 = run forever")
    pl.add_argument("--seed", type=int)
    pl.add_argument("--quiet-cc", action="store_true", help="dry-run: hide CC lines")

    le = sub.add_parser("learn", help="send one CC at a time for Vital's MIDI learn")
    out_args(le)
    le.add_argument("--single", type=int, help="send just this CC number")
    le.add_argument("--seconds", type=float, default=8.0)

    sub.add_parser("ports", help="list MIDI ports")
    return p


def main():
    args = build_parser().parse_args()
    try:
        if args.cmd == "gen":
            cmd_gen(args)
        elif args.cmd == "play":
            cmd_play(args)
        elif args.cmd == "learn":
            cmd_learn(args)
        elif args.cmd == "ports":
            cmd_ports(args)
    except BrokenPipeError:                 # piped into head/less
        try:
            sys.stdout.close()
        except Exception:
            pass
        os._exit(0)
    except (FileNotFoundError, IsADirectoryError, PermissionError) as e:
        sys.exit(f"could not open {getattr(e, 'filename', '')or ''}: {e.strerror}")
    except ValueError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
