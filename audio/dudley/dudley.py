#!/usr/bin/env python3
"""
DUDLEY -- sound hybridization engine
xik6 / m8w

Public domain: CC0 1.0 Universal. No rights reserved.
https://creativecommons.org/publicdomain/zero/1.0/

Named for Homer Dudley, whose 1939 vocoder was the first machine to lift the
spectral envelope off one sound and lay it onto another.

What it does
------------
A granular engine plays one or more CARRIER sounds (the grain stream). Each
grain is analysed, its own spectral envelope is removed, and a TARGET envelope
is imposed in its place. The target is read from one or more DONOR sounds,
following the donor's own time course.

The morph is not a crossfade. Every point on the morph path is a single grain
stream with a single envelope; what moves is the envelope itself:

  position 0        -> each grain keeps its own envelope (carrier untouched)
  position 1        -> each grain wears donor 1's envelope
  position 2        -> each grain wears donor 2's envelope ... etc.
  fractional values -> envelopes interpolated between neighbours

Two ways to interpolate between neighbouring envelopes (--morph):

  cepstral   linear interpolation of log-spectral envelopes (== cepstra).
             Peaks fade in place: formant A dims while formant B brightens.
  transport  1-D optimal transport (displacement interpolation) on a
             log-frequency axis. Peaks TRAVEL: a resonance at 700 Hz slides
             to 2.3 kHz instead of fading out while another fades in.

--texture (0..1) additionally swaps the carrier's fine spectral structure
(harmonics, partial detail) for the donor's, moving from envelope transfer
toward full cross-synthesis.

Randomness is read from os.urandom by default (true entropy, unrepeatable).
Pass --seed only when you need a repeatable render; the manifest records which.

Outputs
-------
  <out>.wav                 32-bit float stereo
  <out>.manifest.json       args, SHA-1 of every input and the output, entropy source
  <out>.schedule.json       per-video-frame control curves (morph position,
                            RMS, spectral centroid, flux) for driving
                            morph_engine or anything else frame-based

Requires numpy and scipy. ffmpeg (if on PATH) is used to read any format;
without it, WAV only.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np
from scipy.io import wavfile
from scipy.ndimage import gaussian_filter1d
from scipy.signal import resample_poly

__version__ = "1.0"
EPS = 1e-12


# ----------------------------------------------------------------------------
# entropy
# ----------------------------------------------------------------------------
class Entropy:
    """os.urandom by default; a seeded PRNG only when explicitly asked for."""

    def __init__(self, seed=None):
        self.seed = seed
        self.rng = np.random.default_rng(seed) if seed is not None else None
        self.source = f"numpy PCG64 seed={seed}" if seed is not None else "os.urandom"

    def uniform(self, n):
        n = int(n)
        if n <= 0:
            return np.zeros(0)
        if self.rng is not None:
            return self.rng.random(n)
        b = np.frombuffer(os.urandom(8 * n), dtype=np.uint64)
        return (b >> np.uint64(11)).astype(np.float64) / float(1 << 53)

    def normal(self, n):
        u1 = np.maximum(self.uniform(n), 1e-300)
        u2 = self.uniform(n)
        return np.sqrt(-2.0 * np.log(u1)) * np.cos(2.0 * np.pi * u2)


# ----------------------------------------------------------------------------
# io
# ----------------------------------------------------------------------------
def sha1(path, chunk=1 << 20):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def load_audio(path, sr):
    """Mono float64 at sr."""
    if shutil.which("ffmpeg"):
        cmd = ["ffmpeg", "-v", "error", "-i", path, "-f", "f32le",
               "-ac", "1", "-ar", str(sr), "-"]
        res = subprocess.run(cmd, capture_output=True)
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg could not read {path}: {res.stderr.decode()[:300]}")
        x = np.frombuffer(res.stdout, dtype=np.float32).astype(np.float64)
    else:
        fsr, x = wavfile.read(path)
        if x.dtype.kind == "i":
            x = x.astype(np.float64) / float(np.iinfo(x.dtype).max)
        elif x.dtype.kind == "u":
            x = (x.astype(np.float64) - 128.0) / 128.0
        else:
            x = x.astype(np.float64)
        if x.ndim > 1:
            x = x.mean(axis=1)
        if fsr != sr:
            g = np.gcd(int(sr), int(fsr))
            x = resample_poly(x, sr // g, fsr // g)
    if x.size == 0:
        raise RuntimeError(f"{path} decoded to zero samples")
    return x


def write_wav(path, y, sr, bits=24):
    """bits=24: 24-bit PCM (Logic's native import format). bits=32: 32-bit float."""
    if bits == 32:
        wavfile.write(path, sr, y.astype(np.float32))
        return
    y = np.atleast_2d(y.T).T if y.ndim == 1 else y
    q = np.ascontiguousarray(np.clip(np.round(y * 8388607.0), -8388608, 8388607).astype("<i4"))
    raw = q.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()  # little-endian low 3 bytes
    ch = y.shape[1]
    import struct
    hdr = b"RIFF" + struct.pack("<I", 36 + len(raw)) + b"WAVE"
    hdr += b"fmt " + struct.pack("<IHHIIHH", 16, 1, ch, sr, sr * ch * 3, ch * 3, 24)
    hdr += b"data" + struct.pack("<I", len(raw))
    with open(path, "wb") as fh:
        fh.write(hdr); fh.write(raw)
        if len(raw) % 2:
            fh.write(b"\0")


# ----------------------------------------------------------------------------
# spectral envelope machinery
# ----------------------------------------------------------------------------
def next_pow2(n):
    return 1 << int(np.ceil(np.log2(max(2, n))))


def cepstral_smooth(logmag, n_fft, q):
    """Real-cepstrum smoothing: keep quefrencies below q samples."""
    c = np.fft.irfft(logmag, n_fft)
    q = int(min(max(q, 2), n_fft // 2 - 1))
    c[q:n_fft - q + 1] = 0.0
    return np.fft.rfft(c, n_fft).real


def level(l):
    """Energy level (natural log amplitude) of a log-magnitude envelope."""
    m = l.max()
    return m + 0.5 * np.log(np.mean(np.exp(2.0 * (l - m))) + EPS)


class Grid:
    """Log-frequency grid on which envelopes are compared and morphed."""

    def __init__(self, sr, k=384, fmin=30.0):
        self.sr = sr
        self.f = np.geomspace(fmin, 0.499 * sr, k)
        self.k = k

    def from_bins(self, env_bins, n_fft):
        bf = np.arange(n_fft // 2 + 1) * self.sr / n_fft
        return np.interp(self.f, bf, env_bins)

    def to_bins(self, env_k, n_fft):
        bf = np.arange(n_fft // 2 + 1) * self.sr / n_fft
        return np.interp(bf, self.f, env_k)


def transport(la, lb, t, gamma=0.6, m=2048):
    """Displacement interpolation between two log envelopes on the K grid.

    Envelopes are treated as mass distributions (compressed by gamma), their
    quantile functions are interpolated, and the result is differentiated
    back into a density. Peaks move rather than crossfade.
    """
    if t <= 0.0:
        return la
    if t >= 1.0:
        return lb
    k = la.size
    x = np.arange(k, dtype=np.float64)
    pa = np.exp(gamma * (la - la.max())) + 1e-9
    pb = np.exp(gamma * (lb - lb.max())) + 1e-9
    ca = np.cumsum(pa); ca /= ca[-1]
    cb = np.cumsum(pb); cb /= cb[-1]
    u = np.linspace(0.0, 1.0, m)
    qa = np.interp(u, ca, x)
    qb = np.interp(u, cb, x)
    qt = (1.0 - t) * qa + t * qb
    qt = np.maximum.accumulate(qt) + np.arange(m) * 1e-9
    ct = np.interp(x, qt, u)
    dens = gaussian_filter1d(np.gradient(ct), 1.0)
    dens = np.maximum(dens, dens.max() * 1e-5)
    lt = np.log(dens) / gamma
    target = (1.0 - t) * level(la) + t * level(lb)
    return lt + (target - level(lt))


def cepstral_interp(la, lb, t):
    return (1.0 - t) * la + t * lb


# ----------------------------------------------------------------------------
# donor analysis
# ----------------------------------------------------------------------------
class Donor:
    N_FFT = 2048
    HOP = 512

    def __init__(self, path, x, sr, grid, lifter_q, keep_detail):
        self.path = path
        self.sr = sr
        self.dur = x.size / sr
        n, h = self.N_FFT, self.HOP
        w = np.hanning(n)
        pad = np.concatenate([np.zeros(n // 2), x, np.zeros(n)])
        frames = max(1, 1 + (pad.size - n) // h)
        idx = np.arange(n)[None, :] + h * np.arange(frames)[:, None]
        S = np.fft.rfft(pad[idx] * w, n, axis=1)
        lm = np.log(np.abs(S) + EPS) - np.log(w.sum())
        env = np.empty_like(lm)
        for i in range(frames):
            env[i] = cepstral_smooth(lm[i], n, lifter_q)
        self.env_k = np.stack([grid.from_bins(e, n) for e in env]).astype(np.float64)
        self.detail = (lm - env).astype(np.float32) if keep_detail else None
        self.rms = np.sqrt(np.mean((pad[idx] * w) ** 2, axis=1))
        self.frames = frames

    def _frame_pos(self, tfrac):
        return np.clip(tfrac, 0.0, 1.0) * (self.frames - 1)

    def env_at(self, tfrac):
        p = self._frame_pos(tfrac)
        i = int(np.floor(p)); f = p - i
        j = min(i + 1, self.frames - 1)
        return (1.0 - f) * self.env_k[i] + f * self.env_k[j]

    def detail_at(self, tfrac, n_fft):
        i = int(round(self._frame_pos(tfrac)))
        d = self.detail[i].astype(np.float64)
        src = np.arange(d.size) * self.sr / self.N_FFT
        dst = np.arange(n_fft // 2 + 1) * self.sr / n_fft
        return np.interp(dst, src, d)


# ----------------------------------------------------------------------------
# morph path
# ----------------------------------------------------------------------------
def build_path(spec, n_donors, ent, donors):
    """Return f(tfrac) -> position in [0, n_donors]."""
    top = float(n_donors)
    if spec is None:
        spec = f"0:0,1:{top:g}"
    spec = spec.strip()

    if spec.startswith("walk"):
        rate = float(spec.split(":")[1]) if ":" in spec else 0.35
        knots = 400
        steps = ent.normal(knots) * rate * np.sqrt(1.0 / knots) * 6.0
        p = np.empty(knots)
        p[0] = ent.uniform(1)[0] * top
        for i in range(1, knots):
            v = p[i - 1] + steps[i]
            while v < 0 or v > top:  # reflect at the walls
                v = -v if v < 0 else 2 * top - v
            p[i] = v
        p = gaussian_filter1d(p, 3.0)
        tk = np.linspace(0, 1, knots)
        return lambda t: float(np.interp(t, tk, p)), f"walk rate={rate}"

    if spec == "rms":
        r = gaussian_filter1d(donors[0].rms, 4.0)
        r = (r - r.min()) / (r.max() - r.min() + EPS)
        tk = np.linspace(0, 1, r.size)
        return lambda t: float(np.interp(t, tk, r) * top), "rms of donor 1"

    pts = []
    for tok in spec.split(","):
        a, b = tok.split(":")
        pts.append((float(a), float(np.clip(float(b), 0.0, top))))
    pts.sort()
    tk = np.array([p[0] for p in pts]); pk = np.array([p[1] for p in pts])
    return lambda t: float(np.interp(t, tk, pk)), f"breakpoints {spec}"


# ----------------------------------------------------------------------------
# the engine
# ----------------------------------------------------------------------------
class Cancelled(Exception):
    pass


def render(a, progress=None, cancel=None):
    """progress(stage:str, fraction:float) and cancel() -> bool are optional
    hooks for a GUI; the command line uses neither."""
    t_start = time.time()
    say = progress or (lambda *_: None)
    say("reading files", 0.0)
    sr = a.sr
    ent = Entropy(a.seed)
    grid = Grid(sr, k=a.grid)
    lifter_q = int(round(a.lifter_ms * 1e-3 * sr))

    carriers = [load_audio(p, sr) for p in a.carrier]
    say("analysing donors", 0.02)
    donors = [Donor(p, load_audio(p, sr), sr, grid, lifter_q, a.texture > 0) for p in a.donor]
    nd = len(donors)

    dur = a.duration if a.duration else max(d.dur for d in donors)
    n_out = int(dur * sr)
    path_fn, path_desc = build_path(a.path, nd, ent, donors)
    interp = transport if a.morph == "transport" else cepstral_interp

    # --- grain schedule, all entropy drawn up front ------------------------
    n_grains = max(1, int(a.density * dur))
    # stratified: one grain per equal time slot, placed by entropy within it.
    # onset_jitter 1 = anywhere in its slot, 0 = metronomic, >1 = clumpier
    slots = (np.arange(n_grains) + 0.5 + a.onset_jitter * (ent.uniform(n_grains) - 0.5))
    onsets = np.clip(np.sort(slots) / n_grains, 0.0, 1.0) * n_out
    g_len_ms = a.grain_ms * np.exp(a.grain_jitter * ent.normal(n_grains) * 0.5)
    g_len = np.clip((g_len_ms * 1e-3 * sr).astype(int), 64, int(0.5 * sr))
    pitch = a.pitch + a.pitch_spread * ent.normal(n_grains)
    ratio = 2.0 ** (pitch / 12.0)
    spray = ent.normal(n_grains) * a.spray_ms * 1e-3 * sr
    pan = 0.5 + a.width * (ent.uniform(n_grains) - 0.5)
    which = np.minimum((ent.uniform(n_grains) * len(carriers)).astype(int), len(carriers) - 1)
    rand_pos = ent.uniform(n_grains)

    scan = a.scan
    freeze_at = None
    if scan.startswith("freeze"):
        freeze_at = float(scan.split(":")[1]) if ":" in scan else 0.5
        scan = "freeze"

    max_gain = a.max_gain_db / 20.0 * np.log(10.0)
    out = np.zeros((2, n_out + int(0.6 * sr)))
    used = 0

    for g in range(n_grains):
        if g % 32 == 0:
            if cancel and cancel():
                raise Cancelled()
            say("grains", 0.05 + 0.85 * g / n_grains)
        L = int(g_len[g]); t0 = int(onsets[g])
        tfrac = (t0 + 0.5 * L) / max(1, n_out)
        c = carriers[which[g]]
        need = int(np.ceil(L * ratio[g])) + 2

        if scan == "stretch":
            pos = tfrac * max(0, c.size - need)
        elif scan == "loop":
            pos = (t0 % max(1, c.size - need))
        elif scan == "random":
            pos = rand_pos[g] * max(0, c.size - need)
        else:
            pos = freeze_at * max(0, c.size - need)
        pos = int(np.clip(pos + spray[g], 0, max(0, c.size - need)))

        seg = c[pos:pos + need]
        if seg.size < need:
            seg = np.pad(seg, (0, need - seg.size))
        grain = np.interp(np.arange(L) * ratio[g], np.arange(seg.size), seg)
        w = np.hanning(L)
        grain *= w
        if np.sqrt(np.mean(grain ** 2)) < 1e-6:
            continue

        n = max(256, next_pow2(2 * L))
        S = np.fft.rfft(grain, n)
        lm = np.log(np.abs(S) + EPS) - np.log(w.sum())
        env_c = cepstral_smooth(lm, n, lifter_q)
        env_c_k = grid.from_bins(env_c, n)

        # position on the morph path; 0 is the grain's own envelope
        p = path_fn(tfrac)
        i = int(np.floor(p)); f = p - i
        if i >= nd:
            i, f = nd - 1, 1.0
        lo = env_c_k if i == 0 else donors[i - 1].env_at(tfrac)
        hi = donors[i].env_at(tfrac)
        tgt_k = interp(lo, hi, f)
        if a.dynamics == "carrier":
            tgt_k = tgt_k + (level(env_c_k) - level(tgt_k))

        gain = grid.to_bins(tgt_k, n) - env_c
        if a.texture > 0:
            # swap fine structure: carrier detail out, donor detail in,
            # weighted by how much each donor is present at this position
            wts = {}
            if i >= 1:
                wts[i - 1] = 1.0 - f
            wts[i] = wts.get(i, 0.0) + f
            dd = sum(wv * donors[k].detail_at(tfrac, n) for k, wv in wts.items() if wv > 0)
            present = sum(wts.values())
            gain = gain + a.texture * (dd - present * (lm - env_c))
        gain = np.clip(gain, -max_gain, max_gain)

        y = np.fft.irfft(S * np.exp(gain), n)[:L] * w
        out[0, t0:t0 + L] += y * np.cos(0.5 * np.pi * pan[g])
        out[1, t0:t0 + L] += y * np.sin(0.5 * np.pi * pan[g])
        used += 1

    out = out[:, :n_out]
    pk = np.abs(out).max()
    if pk > 0:
        out *= 10 ** (a.peak_db / 20.0) / pk

    # --- write -----------------------------------------------------------
    say("writing", 0.92)
    base = os.path.splitext(a.output)[0]
    write_wav(a.output, out.T, sr, getattr(a, "bits", 24))
    sched = schedule(out.mean(axis=0), sr, dur, path_fn, nd, a.fps)
    with open(base + ".schedule.json", "w") as fh:
        json.dump(sched, fh)

    manifest = {
        "instrument": "DUDLEY",
        "version": __version__,
        "rendered": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "seconds_to_render": round(time.time() - t_start, 2),
        "entropy": ent.source,
        "path": path_desc,
        "grains_scheduled": n_grains,
        "grains_sounded": used,
        "duration_s": round(dur, 3),
        "sr": sr,
        "args": {k: v for k, v in vars(a).items()},
        "carriers": [{"path": p, "sha1": sha1(p)} for p in a.carrier],
        "donors": [{"path": p, "sha1": sha1(p)} for p in a.donor],
        "output": {"path": a.output, "sha1": sha1(a.output)},
        "schedule": base + ".schedule.json",
    }
    with open(base + ".manifest.json", "w") as fh:
        json.dump(manifest, fh, indent=2)
    say("done", 1.0)
    return manifest


def schedule(y, sr, dur, path_fn, nd, fps):
    """Per-video-frame control curves. 'alpha' is the morph path scaled to
    0..1, ready to use as an external morph schedule."""
    n_frames = max(1, int(round(dur * fps)))
    hop = sr / fps
    n = 2048
    w = np.hanning(n)
    freqs = np.arange(n // 2 + 1) * sr / n
    rows, prev = [], None
    for k in range(n_frames):
        c = int(k * hop)
        seg = y[max(0, c - n // 2): c + n // 2]
        seg = np.pad(seg, (0, n - seg.size))
        mag = np.abs(np.fft.rfft(seg * w))
        rms = float(np.sqrt(np.mean(seg ** 2)))
        cen = float((freqs * mag).sum() / (mag.sum() + EPS))
        flux = 0.0 if prev is None else float(np.sum(np.maximum(mag - prev, 0)) / (mag.sum() + EPS))
        prev = mag
        pos = path_fn(k / max(1, n_frames - 1))
        rows.append({"frame": k, "t": round(k / fps, 4), "position": round(pos, 4),
                     "alpha": round(pos / nd, 4), "rms": round(rms, 6),
                     "centroid_hz": round(cen, 1), "flux": round(flux, 5)})
    # normalised copies for convenience
    for key in ("rms", "flux"):
        vals = np.array([r[key] for r in rows])
        span = vals.max() - vals.min()
        for r, v in zip(rows, vals):
            r[key + "_norm"] = round(float((v - vals.min()) / span) if span > 0 else 0.0, 5)
    return {"instrument": "DUDLEY", "fps": fps, "duration_s": round(dur, 4),
            "donors": nd, "frames": rows}


# ----------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="dudley",
        description="DUDLEY: sound hybridization. One sound's spectral envelope "
                    "worn by another sound's grain stream.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("-c", "--carrier", action="append", required=True,
                    help="grain source (repeatable; grains are drawn across all)")
    ap.add_argument("-d", "--donor", action="append", required=True,
                    help="envelope donor (repeatable; the path visits them in order)")
    ap.add_argument("-o", "--output", default="dudley_out.wav")
    ap.add_argument("--morph", choices=["transport", "cepstral"], default="transport",
                    help="how neighbouring envelopes are interpolated")
    ap.add_argument("--path", default=None,
                    help="morph path: 't:pos,t:pos,...' (t in 0..1, pos in 0..N donors), "
                         "'walk[:rate]' for an entropy random walk, or 'rms' to follow donor 1's loudness. "
                         "Default ramps 0 -> N.")
    ap.add_argument("--texture", type=float, default=0.0,
                    help="0 = envelope only; 1 = also replace fine structure (cross-synthesis)")
    ap.add_argument("--dynamics", choices=["donor", "carrier"], default="donor",
                    help="whose loudness contour the result follows")
    ap.add_argument("--grain-ms", type=float, default=80.0)
    ap.add_argument("--grain-jitter", type=float, default=0.3, help="log-normal spread of grain length")
    ap.add_argument("--density", type=float, default=60.0, help="grains per second")
    ap.add_argument("--onset-jitter", type=float, default=1.0,
                    help="0 = metronomic grain onsets, 1 = entropy-placed within even slots, >1 = clumps and gaps")
    ap.add_argument("--scan", default="stretch",
                    help="carrier read position: stretch | loop | random | freeze[:0..1]")
    ap.add_argument("--spray-ms", type=float, default=30.0, help="read-position jitter")
    ap.add_argument("--pitch", type=float, default=0.0, help="grain transposition, semitones")
    ap.add_argument("--pitch-spread", type=float, default=0.0, help="per-grain pitch deviation, semitones")
    ap.add_argument("--width", type=float, default=0.8, help="stereo spread 0..1")
    ap.add_argument("--lifter-ms", type=float, default=1.2,
                    help="cepstral cutoff; larger keeps finer envelope detail")
    ap.add_argument("--max-gain-db", type=float, default=36.0, help="per-bin gain clamp")
    ap.add_argument("--grid", type=int, default=384, help="log-frequency bins for morphing")
    ap.add_argument("--duration", type=float, default=None, help="seconds; default = longest donor")
    ap.add_argument("--sr", type=int, default=44100)
    ap.add_argument("--peak-db", type=float, default=-1.0)
    ap.add_argument("--bits", type=int, choices=[24, 32], default=24,
                    help="24 = 24-bit PCM (Logic, Reaper, anything); 32 = 32-bit float")
    ap.add_argument("--fps", type=float, default=30.0, help="schedule.json frame rate")
    ap.add_argument("--seed", type=int, default=None,
                    help="use a repeatable PRNG instead of os.urandom")
    a = ap.parse_args(argv)
    m = render(a)
    print(f"DUDLEY  {m['output']['path']}  {m['duration_s']}s  "
          f"{m['grains_sounded']}/{m['grains_scheduled']} grains  "
          f"{m['seconds_to_render']}s  entropy={m['entropy']}")


if __name__ == "__main__":
    main()
