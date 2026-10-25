#!/usr/bin/env python3
"""
xik6_mosaic.py -- mix and match your own sound and your own fractal video.

Everything you hear and see comes from YOUR files. No pretrained models, no
sample packs, no third-party presets. The only dependencies are general-purpose
tools (numpy, OpenCV, ffmpeg) -- like using Reaper, nothing to credit.

Works the same on 30-second clips and 12-hour recordings: audio is decoded once
to a disk cache and read in slices, video is analysed by streaming, and each
file is analysed only once (results cached).

Modes
-----
  sound    A video DRIVES a grain mosaic built from your recordings.
  video    A sound DRIVES a cut-up of your videos, cut on its onsets.
  remix    Both: the driver video writes a new soundtrack from your audio,
           then that soundtrack re-edits your videos.
  react    Rework each video so its picture reacts to its OWN sound, as it plays:
           loudness pumps and zooms, hits flash mirror-symmetry and split colour,
           the tone of the sound turns the colours, noisy sound leaves trails.
           Same video, same length, original sound untouched, one continuous pass.
           python3 xik6_mosaic.py react --corpus "/Volumes/Seagate/folder"
  full     For every long original in a folder (e.g. 11 h), make a remix of EXACTLY the
           same length, steered by that original from start to end. Resumable.
           --kind video  new picture, original sound (default)
           --kind sound  original picture, new sound
           --kind remix  both new
  glue     Join every short clip in a folder (30 s videos etc.) into ONE long
           video, in order, with crossfades. Saved beside the clips.
  analyse  Pre-analyse folders/drives (do this overnight for big archives).
  list     Show what is in the cache.

Examples
--------
  python3 xik6_mosaic.py analyse --corpus "/Volumes/SSD1/down 1" ~/Movies
  python3 xik6_mosaic.py remix --driver long_11h.mp4 --start random --duration 300 \\
          --corpus "/Volumes/SSD1/down 1" ~/Movies --out remix.mp4
  python3 xik6_mosaic.py sound --driver clip30s.mp4 --corpus "/Volumes/SSD1/down 1" --out s.mp4
  python3 xik6_mosaic.py video --driver set.wav --corpus ~/Movies --out cutup.mp4

Long vs short files
-------------------
  --balance B   0 = files are picked in proportion to their length (an 11 h file
                wins ~1400x more often than a 30 s clip); 1 = every file gets an
                equal share no matter its length. Default 0.75.
  --start S     where to begin in the driver: seconds, or "random".
  --duration D  length of the piece. Default: the driver's length, capped at 10 min.
"""

import os as _os
import sys as _sys

GENTLE = "--full-speed" not in _sys.argv
if GENTLE:  # must be set before numpy loads
    for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        _os.environ.setdefault(_v, "2")

import argparse
import hashlib
import shutil
import json
import os
import subprocess
import sys
import time

import numpy as np

try:
    import cv2
except ImportError:
    sys.exit("needs OpenCV:  pip3 install opencv-python")

SR = 44100
HOP = 1024          # audio analysis hop (~23 ms)
WIN = 2048
AUDIO_EXT = {".wav", ".aif", ".aiff", ".flac", ".mp3", ".m4a", ".ogg", ".opus"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}
CACHE_DIR = os.path.expanduser("~/.xik6_mosaic_cache")
MAX_ROWS = 150_000  # per-source candidate cap (keeps matching fast for 12 h files)
DEFAULT_MAX_DUR = 600.0

VIDEO_FEATS = ["brightness", "motion", "detail", "fractal_dim", "hue_sin", "hue_cos",
               "centroid_x", "centroid_y", "contrast", "saturation", "spread",
               "symmetry", "color_entropy", "hf_energy", "flicker"]
PER_SOURCE = True
QUIET = False


FF_THREADS = ["-threads", "2"] if GENTLE else []


def log(*a):
    print(*a, flush=True)


def hms(s):
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


# ============================================================================
# files, probing, decoding
# ============================================================================

INCLUDE_REMIXES = False


def collect(paths, exts, exclude=()):
    """Find media files. Files named xik6_remix* (this engine's own output) are skipped
    unless --include-remixes is given, so remixes saved beside the originals never
    get fed back in by accident."""
    out = []
    for p in paths:
        p = os.path.expanduser(p)
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not d.endswith(".bundle")
                                 and not d.endswith(".imovielibrary") and not d.endswith(".photoslibrary"))
                for f in sorted(files):
                    if os.path.splitext(f)[1].lower() in exts and not f.startswith("._"):
                        out.append(os.path.join(root, f))
        elif os.path.isfile(p) and os.path.splitext(p)[1].lower() in exts:
            out.append(p)
    out = [f for f in out if not any(x.lower() in f.lower() for x in exclude)]
    if not INCLUDE_REMIXES:
        out = [f for f in out if not os.path.basename(f).lower().startswith("xik6_remix")]
    seen, uniq = set(), []
    for f in out:
        k = os.path.realpath(f)
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return uniq


def is_video(path):
    return os.path.splitext(path)[1].lower() in VIDEO_EXT


_probe_cache = {}


def probe(path):
    """(duration_s, has_audio, has_video, fps) or None if unreadable."""
    if path in _probe_cache:
        return _probe_cache[path]
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration:stream=codec_type,avg_frame_rate",
                        "-of", "json", path], capture_output=True, text=True)
    try:
        j = json.loads(r.stdout)
        dur = float(j["format"]["duration"])
        types = [s.get("codec_type") for s in j.get("streams", [])]
        fps = 30.0
        for s in j.get("streams", []):
            if s.get("codec_type") == "video":
                n, d = s.get("avg_frame_rate", "30/1").split("/")
                fps = float(n) / float(d) if float(d) else 30.0
        res = (dur, "audio" in types, "video" in types, fps)
    except Exception:
        res = None
    _probe_cache[path] = res
    return res


def load_audio(path, start=0.0, dur=None):
    """Decode a section of any audio/video file to float32 stereo at SR (for drivers)."""
    cmd = ["ffmpeg", "-v", "error"] + FF_THREADS
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", path, "-vn", "-ac", "2", "-ar", str(SR)]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    a = np.frombuffer(raw, dtype=np.float32)
    if a.size < 2:
        return np.zeros((0, 2), np.float32)
    return a[: a.size // 2 * 2].reshape(-1, 2).copy()


def cache_key(path, kind, ext="npz"):
    st = os.stat(path)
    h = hashlib.sha1(f"{os.path.realpath(path)}|{st.st_size}|{int(st.st_mtime)}|{kind}|v3".encode()).hexdigest()[:16]
    return os.path.join(CACHE_DIR, f"{kind}_{h}.{ext}")


def pcm_cache(path):
    """Mono int16 PCM on disk, memory-mapped. ~320 MB per hour of audio."""
    ck = cache_key(path, "pcm", "s16")
    if not os.path.exists(ck):
        os.makedirs(CACHE_DIR, exist_ok=True)
        info = probe(path)
        need = int((info[0] if info else 3600) * SR * 2) + 20 * 1024 ** 3   # this file + 20 GB spare
        free = shutil.disk_usage(CACHE_DIR).free
        if free < need:
            sys.exit(f"\nSTOPPED before filling the disk: {CACHE_DIR} has {free / 1e9:.0f} GB free, "
                     f"needs about {need / 1e9:.0f} GB for {os.path.basename(path)}.\n"
                     f"Point --cache-dir at a drive with more room (e.g. the Seagate).")
        tmp = ck + ".part"
        t0 = time.time()
        subprocess.run(["ffmpeg", "-v", "error", "-y"] + FF_THREADS + ["-i", path, "-vn", "-ac", "1", "-ar", str(SR),
                        "-f", "s16le", tmp], check=False)
        if not os.path.exists(tmp) or os.path.getsize(tmp) < 4 * WIN:
            if os.path.exists(tmp):
                os.remove(tmp)
            return None
        os.replace(tmp, ck)
        log(f"      decoded audio {hms(os.path.getsize(ck) / 2 / SR)} in {time.time() - t0:.0f}s")
    n = os.path.getsize(ck) // 2
    return np.memmap(ck, dtype=np.int16, mode="r", shape=(n,))


# ============================================================================
# audio analysis (streams over the memmap in chunks)
# ============================================================================

def audio_features(mono):
    """Per-hop: loudness dB, log2 spectral centroid, flatness, flux. mono: float or int16 array."""
    scale = 1.0 / 32768 if mono.dtype == np.int16 else 1.0
    n = max(0, (len(mono) - WIN) // HOP + 1)
    feats = np.zeros((n, 4), np.float32)
    if n == 0:
        return feats
    win = np.hanning(WIN).astype(np.float32)
    freqs = np.fft.rfftfreq(WIN, 1 / SR).astype(np.float32)
    base_idx = np.arange(WIN)[None, :] + HOP * np.arange(4096)[:, None]
    prev = None
    for s in range(0, n, 4096):
        m = min(4096, n - s)
        lo = s * HOP
        seg = np.asarray(mono[lo: lo + (m - 1) * HOP + WIN], dtype=np.float32) * scale
        fr = seg[base_idx[:m]] * win
        mag = np.abs(np.fft.rfft(fr, axis=1)).astype(np.float32) + 1e-9
        rms = np.sqrt((fr ** 2).mean(axis=1)) + 1e-9
        feats[s:s + m, 0] = 20 * np.log10(rms)
        cen = (mag * freqs).sum(1) / mag.sum(1)
        feats[s:s + m, 1] = np.log2(np.maximum(cen, 20.0))
        feats[s:s + m, 2] = np.exp(np.log(mag).mean(1)) / mag.mean(1)
        lm = np.log1p(mag)
        both = lm if prev is None else np.vstack([prev[None], lm])
        fl = np.maximum(both[1:] - both[:-1], 0).sum(1)
        feats[s:s + m, 3] = np.r_[0.0, fl] if prev is None else fl
        prev = lm[-1]
    return feats


def analyse_audio(path):
    ck = cache_key(path, "audio")
    if os.path.exists(ck):
        return np.load(ck)["feats"]
    pcm = pcm_cache(path)
    if pcm is None:
        return np.zeros((0, 4), np.float32)
    t0 = time.time()
    feats = audio_features(pcm)
    np.savez_compressed(ck, feats=feats)
    log(f"      analysed audio in {time.time() - t0:.0f}s")
    return feats


# ============================================================================
# video analysis (ffmpeg streams tiny frames; long files sampled less often)
# ============================================================================

def box_count_dimension(edges):
    sizes = [2, 4, 8, 16, 32]
    n = edges.shape[0]
    counts = [max(int(edges.reshape(n // s, s, n // s, s).any(axis=(1, 3)).sum()), 1) for s in sizes]
    return float(np.polyfit(np.log(1.0 / np.array(sizes)), np.log(counts), 1)[0])


class FrameFeatures:
    def __init__(self):
        self.prev_gray = None
        self.prev_bright = None
        yy, xx = np.mgrid[0:128, 0:128]
        self.hf_mask = np.hypot(yy - 64, xx - 64) > 24
        self.ys, self.xs = np.mgrid[0:90, 0:160]

    def __call__(self, frame_bgr):
        small = frame_bgr if frame_bgr.shape[:2] == (90, 160) else \
            cv2.resize(frame_bgr, (160, 90), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV).astype(np.float32)
        h = hsv[..., 0] * (2 * np.pi / 180.0)
        s = hsv[..., 1] / 255.0
        v = hsv[..., 2] / 255.0
        f = {}
        f["brightness"] = v.mean()
        f["contrast"] = v.std()
        f["saturation"] = s.mean()
        w = s * v + 1e-6
        f["hue_sin"] = (np.sin(h) * w).sum() / w.sum()
        f["hue_cos"] = (np.cos(h) * w).sum() / w.sum()
        f["motion"] = 0.0 if self.prev_gray is None else np.abs(v - self.prev_gray).mean()
        self.prev_gray = v
        gx = cv2.Sobel(v, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(v, cv2.CV_32F, 0, 1, ksize=3)
        f["detail"] = np.hypot(gx, gy).mean()
        g128 = cv2.resize(v, (128, 128), interpolation=cv2.INTER_AREA)
        edges = cv2.Canny((g128 * 255).astype(np.uint8), 40, 120) > 0
        f["fractal_dim"] = box_count_dimension(edges) if edges.any() else 0.0
        tot = v.sum() + 1e-6
        cx = (v * self.xs).sum() / tot
        cy = (v * self.ys).sum() / tot
        f["centroid_x"] = cx / 160.0
        f["centroid_y"] = cy / 90.0
        f["spread"] = (v * np.hypot(self.xs - cx, self.ys - cy)).sum() / tot / 160.0
        f["symmetry"] = 1.0 - 0.5 * (np.abs(v - v[:, ::-1]).mean() + np.abs(v - v[::-1, :]).mean())
        hist, _ = np.histogram(h, bins=24, range=(0, 2 * np.pi), weights=w)
        p = hist / (hist.sum() + 1e-9)
        p = p[p > 0]
        f["color_entropy"] = -(p * np.log2(p)).sum()
        spec = np.fft.fftshift(np.abs(np.fft.fft2(g128)) ** 2)
        f["hf_energy"] = spec[self.hf_mask].sum() / (spec.sum() + 1e-9)
        b = f["brightness"]
        f["flicker"] = 0.0 if self.prev_bright is None else abs(b - self.prev_bright)
        self.prev_bright = b
        return np.array([f[k] for k in VIDEO_FEATS], np.float32)


def analysis_rate(dur):
    if dur <= 600:
        return 10.0
    if dur <= 3 * 3600:
        return 4.0
    return 2.0


def analyse_video(path, fast=False):
    """Features sampled over the whole file. Returns (times, feats)."""
    info = probe(path)
    if not info or not info[2]:
        return np.zeros(0, np.float32), np.zeros((0, len(VIDEO_FEATS)), np.float32)
    dur = info[0]
    rate = 1.0 if fast else analysis_rate(dur)
    ck = cache_key(path, f"video{rate:g}{'k' if fast else ''}")
    if os.path.exists(ck):
        d = np.load(ck)
        return d["times"], d["feats"]
    # also accept a finer analysis that already exists
    for r in (10.0, 4.0, 2.0, 1.0):
        alt = cache_key(path, f"video{r:g}")
        if r >= rate and os.path.exists(alt):
            d = np.load(alt)
            return d["times"], d["feats"]
    cmd = ["ffmpeg", "-v", "error"] + FF_THREADS
    if fast:
        cmd += ["-skip_frame", "nokey"]
    cmd += ["-i", path, "-an", "-sn", "-dn", "-vf", f"fps={rate:g},scale=160:90:flags=area",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10 ** 7)
    fx = FrameFeatures()
    fsize = 160 * 90 * 3
    feats = []
    t0 = time.time()
    last_log = t0
    while True:
        buf = p.stdout.read(fsize)
        if len(buf) < fsize:
            break
        feats.append(fx(np.frombuffer(buf, np.uint8).reshape(90, 160, 3)))
        if time.time() - last_log > 30:
            done = len(feats) / rate
            eta = (dur - done) / max(done / (time.time() - t0), 1e-6)
            log(f"      video {hms(done)} / {hms(dur)}   (~{hms(eta)} left)")
            last_log = time.time()
    p.wait()
    feats = np.array(feats, np.float32).reshape(-1, len(VIDEO_FEATS))
    times = (np.arange(len(feats)) / rate).astype(np.float32)
    os.makedirs(CACHE_DIR, exist_ok=True)
    np.savez_compressed(ck, times=times, feats=feats)
    log(f"      analysed video {hms(dur)} ({len(times)} samples) in {time.time() - t0:.0f}s")
    return times, feats


# ============================================================================
# helpers
# ============================================================================

def rank01(x):
    """Empirical CDF (0..1) of each column -> makes any two sources comparable."""
    x = np.asarray(x, np.float64)
    if x.ndim == 1:
        x = x[:, None]
    out = np.empty_like(x)
    n = len(x)
    for j in range(x.shape[1]):
        order = np.argsort(x[:, j], kind="mergesort")
        r = np.empty(n)
        r[order] = np.arange(n)
        out[:, j] = r / max(n - 1, 1)
    return out


def smooth(x, k):
    if k <= 1 or len(x) < 3:
        return x
    ker = np.hanning(k + 2)[1:-1]
    ker /= ker.sum()
    pad = np.pad(x, [(k, k)] + [(0, 0)] * (x.ndim - 1), mode="edge")
    if x.ndim == 1:
        return np.convolve(pad, ker, "same")[k:-k]
    return np.stack([np.convolve(pad[:, j], ker, "same")[k:-k] for j in range(x.shape[1])], 1)


def quantise(semi, scale):
    if not scale:
        return semi
    octv = np.floor(semi / 12.0)
    rem = semi - 12 * octv
    cand = np.array(scale + [12])
    return 12 * octv + cand[np.abs(cand - rem).argmin()]


def source_weights(durs, balance):
    w = np.asarray(durs, np.float64) ** (1.0 - balance)
    return w / w.sum()


# ============================================================================
# corpus: one entry per file; matching happens inside a chosen file
# ============================================================================

class Source:
    pass


class AudioCorpus:
    def __init__(self, files, rng, min_db=-50.0, global_rank=False):
        self.src = []
        for f in files:
            info = probe(f)
            if not info or not info[1]:
                if info is None:
                    log(f"    skip (unreadable): {os.path.basename(f)}")
                continue
            log(f"    audio: {os.path.basename(f)}  {hms(info[0])}")
            feats = analyse_audio(f)
            pcm = pcm_cache(f)
            if pcm is None or len(feats) < 8:
                continue
            keep = np.where(feats[:, 0] > min_db)[0]
            if len(keep) < 8:
                continue
            if len(keep) > MAX_ROWS:                     # even coverage of long files
                keep = np.sort(rng.choice(keep, MAX_ROWS, replace=False))
            s = Source()
            s.path, s.pcm, s.dur = f, pcm, len(pcm) / SR
            s.frames = keep
            s.raw = feats[keep]
            s.desc = rank01(s.raw).astype(np.float32)
            s.recent = []
            self.src.append(s)
        if not self.src:
            sys.exit("no usable audio found in corpus")
        if global_rank:
            allr = rank01(np.vstack([s.raw for s in self.src])).astype(np.float32)
            i = 0
            for s in self.src:
                s.desc = allr[i:i + len(s.raw)]
                i += len(s.raw)

    def grain(self, s, frame, length, reverse=False):
        start = int(min(frame * HOP, max(0, len(s.pcm) - length - 1)))
        g = np.asarray(s.pcm[start:start + length], dtype=np.float32) / 32768.0
        if len(g) < length:
            g = np.pad(g, (0, length - len(g)))
        return g[::-1] if reverse else g


class VideoCorpus:
    def __init__(self, files, fast=False, global_rank=False):
        self.src = []
        for f in files:
            info = probe(f)
            if not info or not info[2]:
                if info is None:
                    log(f"    skip (unreadable): {os.path.basename(f)}")
                continue
            log(f"    video: {os.path.basename(f)}  {hms(info[0])}")
            times, feats = analyse_video(f, fast)
            if len(times) < 4:
                continue
            s = Source()
            s.path, s.dur, s.fps = f, info[0], info[3]
            s.times, s.feats = times, feats
            s.rank = rank01(feats).astype(np.float32)
            s.last_t = -99.0
            self.src.append(s)
        if not self.src:
            sys.exit("no usable video found in corpus")
        if global_rank:
            allr = rank01(np.vstack([s.feats for s in self.src])).astype(np.float32)
            i = 0
            for s in self.src:
                s.rank = allr[i:i + len(s.feats)]
                i += len(s.feats)


# ============================================================================
# SOUND MODE: video -> grain mosaic of your audio
# ============================================================================

def driver_controls(path, start, dur, fast=False):
    times, feats = analyse_video(path, fast)
    if len(times) < 2:
        sys.exit("could not read driver video")
    r = rank01(feats)
    col = lambda n: r[:, VIDEO_FEATS.index(n)]
    raw = lambda n: feats[:, VIDEO_FEATS.index(n)]
    c = {
        "loud": smooth(col("brightness"), 3),
        "centroid": smooth(0.6 * col("hf_energy") + 0.4 * col("detail"), 3),
        "flatness": smooth(col("color_entropy"), 3),
        "flux": smooth(col("motion"), 2),
        "density": smooth(col("motion"), 4),
        "length": smooth(0.6 * col("symmetry") + 0.4 * (1 - col("motion")), 4),
        "pan": smooth(col("centroid_x"), 4),
        "pitch": smooth(np.arctan2(raw("hue_sin"), raw("hue_cos")) / np.pi, 6),
        "reverse": smooth(col("flicker"), 3),
        "fdim": smooth(col("fractal_dim"), 3),
    }
    total = float(times[-1])

    def at(t):
        tt = (start + t) % max(total, 1e-3)
        return {k: float(np.interp(tt, times, v)) for k, v in c.items()}
    return at, total


def render_sound(driver, corpus, start, dur, args, rng):
    at, _ = driver_controls(driver, start, dur, args.fast)
    n_out = int(dur * SR) + SR
    out = np.zeros((n_out, 2), np.float32)
    weights = np.array([1.2, 1.0, 0.7, 0.6], np.float32)
    pw = source_weights([s.dur for s in corpus.src], args.balance)
    t, count = 0.0, 0
    dmin, dmax = args.density
    gmin, gmax = args.grain
    used = np.zeros(len(corpus.src), int)
    t0 = time.time()
    while t < dur:
        p = at(t)
        target = np.array([p["loud"], p["centroid"], p["flatness"], p["flux"]], np.float32)
        si = int(rng.choice(len(corpus.src), p=pw))
        s = corpus.src[si]
        d = (((s.desc - target) * weights) ** 2).sum(1)
        for r in s.recent:                                    # avoid immediate repeats
            d[np.abs(s.frames - r) < 8] += 0.05
        k = min(args.choices, len(d))
        j = int(rng.choice(np.argpartition(d, k - 1)[:k]))
        frame = int(s.frames[j])
        s.recent = (s.recent + [frame])[-24:]
        used[si] += 1

        L = int(gmin * (gmax / gmin) ** p["length"] * SR / 1000)
        semi = quantise(p["pitch"] * args.transpose + rng.normal(0, 0.3 * p["fdim"]), args.scale)
        ratio = 2 ** (semi / 12.0)
        g = corpus.grain(s, frame, int(L * ratio) + 2, reverse=rng.random() < args.reverse * p["reverse"])
        if abs(ratio - 1) > 1e-3:
            g = np.interp(np.arange(L) * ratio, np.arange(len(g)), g)
        else:
            g = g[:L]
        g = g * (np.hanning(len(g)) ** 0.8)
        pan = np.clip(p["pan"] + rng.normal(0, 0.12), 0, 1)
        amp = 0.35 + 0.65 * p["loud"]
        a = int(t * SR)
        e = min(a + len(g), n_out)
        out[a:e, 0] += g[: e - a] * np.cos(pan * np.pi / 2) * amp
        out[a:e, 1] += g[: e - a] * np.sin(pan * np.pi / 2) * amp
        dens = dmin * (dmax / dmin) ** p["density"]
        t += rng.exponential(1.0 / dens) if args.jitter else 1.0 / dens
        count += 1
    out = out[: int(dur * SR)]

    if args.self_weight > 0:
        own = load_audio(driver, start, dur)
        if len(own):
            own = np.resize(own, out.shape) if len(own) < len(out) else own[: len(out)]
            out = (1 - args.self_weight) * out / (np.abs(out).max() + 1e-9) + \
                args.self_weight * own / (np.abs(own).max() + 1e-9)

    if QUIET:
        return master(out)
    log(f"    {count} grains in {time.time() - t0:.1f}s")
    share = sorted(zip(used, [os.path.basename(s.path) for s in corpus.src]), reverse=True)[:8]
    log("    grains per file: " + ", ".join(f"{n} {name[:28]}" for n, name in share))
    return master(out)


def master(x):
    x = x.astype(np.float64) - x.mean(0)
    k = 2205  # remove DC / sub-sonic drift with a ~50 ms moving average
    cs = np.cumsum(np.pad(x, ((k, k), (0, 0)), mode="edge"), axis=0)
    y = x - ((cs[2 * k:] - cs[:-2 * k]) / (2 * k))[: len(x)]
    y = y / (np.abs(y).max() + 1e-9) * 1.4
    return (np.tanh(y) / np.tanh(1.4) * 0.93).astype(np.float32)


# ============================================================================
# VIDEO MODE: sound -> cut-up of your videos
# ============================================================================

def onsets_from_flux(feats, min_gap, max_gap):
    fl = smooth(feats[:, 3].astype(np.float64), 3)
    thr = smooth(fl, 43) * 1.4 + 1e-6
    peaks = np.where((fl[1:-1] > fl[:-2]) & (fl[1:-1] >= fl[2:]) & (fl[1:-1] > thr[1:-1]))[0] + 1
    cuts = [0.0]
    for tp in peaks * HOP / SR:
        while tp - cuts[-1] > max_gap:
            cuts.append(cuts[-1] + max_gap)
        if tp - cuts[-1] >= min_gap:
            cuts.append(float(tp))
    return np.array(cuts)


def plan_video(audio, vcorp, args, rng):
    feats = audio_features(audio.mean(1))
    dur = len(audio) / SR
    cuts = onsets_from_flux(feats, args.cut[0], args.cut[1])
    cuts = np.append(cuts[cuts < dur], dur)
    ar = rank01(feats)
    w = np.array([1.2, 1.0, 0.8, 0.8])
    pw = source_weights([s.dur for s in vcorp.src], args.balance)
    for s in vcorp.src:
        V = lambda n, s=s: s.rank[:, VIDEO_FEATS.index(n)]
        s.vdesc = np.stack([V("brightness"), 0.5 * V("detail") + 0.5 * V("hf_energy"),
                            V("color_entropy"), V("motion")], 1)
    plan = []
    last = -1
    for a, b in zip(cuts[:-1], cuts[1:]):
        i0 = int(a * SR / HOP)
        target = ar[i0:max(int(b * SR / HOP), i0 + 1)].mean(0)
        seg = b - a
        p = pw.copy()
        if last >= 0 and len(p) > 1:
            p[last] *= 0.5                       # gently favour switching files
            p /= p.sum()
        si = int(rng.choice(len(vcorp.src), p=p))
        s = vcorp.src[si]
        d = (((s.vdesc - target) * w) ** 2).sum(1)
        d[s.times + seg > s.dur - 0.2] += 10     # clip must fit
        d[np.abs(s.times - s.last_t) < 4.0] += 0.3
        k = min(args.choices, len(d))
        j = int(rng.choice(np.argpartition(d, k - 1)[:k]))
        st = float(s.times[j])
        if seg > s.dur:                          # clip longer than the whole source
            st = 0.0
        s.last_t = st
        plan.append((si, st, float(a), float(seg)))
        last = si
    return plan


def fit_frame(frame, W, H):
    h, w = frame.shape[:2]
    s = max(W / w, H / h)
    fr = cv2.resize(frame, (int(w * s + 0.5), int(h * s + 0.5)), interpolation=cv2.INTER_AREA)
    y0 = (fr.shape[0] - H) // 2
    x0 = (fr.shape[1] - W) // 2
    return fr[y0:y0 + H, x0:x0 + W]


def read_clip(path, start, n, fps, W, H):
    """n frames at `fps` from `start` seconds, via ffmpeg fast seek (fine on 12 h files)."""
    cmd = ["ffmpeg", "-v", "error"] + FF_THREADS + ["-ss", f"{max(start, 0):.3f}", "-i", path, "-an", "-sn",
           "-frames:v", str(n), "-vf",
           f"fps={fps:g},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    fs = W * H * 3
    frames = [np.frombuffer(raw[i:i + fs], np.uint8).reshape(H, W, 3) for i in range(0, len(raw) - fs + 1, fs)]
    if not frames:
        frames = [np.zeros((H, W, 3), np.uint8)]
    while len(frames) < n:                       # source ran out: hold last frame
        frames.append(frames[-1])
    return frames


def render_video(plan, vcorp, audio, out_path, args):
    W, H = map(int, args.size.lower().split("x"))
    fps = args.fps
    wav = out_path + ".tmp.wav"
    write_wav(wav, audio)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
           "-r", str(fps), "-i", "-", "-i", wav, "-map", "0:v", "-map", "1:a",
           "-c:v", "libx264"] + FF_THREADS + ["-preset", "medium", "-crf", str(args.crf), "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "256k", "-shortest", out_path]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    total = int(round(len(audio) / SR * fps))
    written = 0
    prev_tail = None
    t0 = time.time()
    last_log = t0
    for ci, (si, st, at, seg) in enumerate(plan):
        n = total - written if ci == len(plan) - 1 else int(round((at + seg) * fps)) - written
        if n <= 0:
            continue
        frames = read_clip(vcorp.src[si].path, st, n, fps, W, H)
        dissolve = min(args.dissolve, n // 2)
        for k, img in enumerate(frames[:n]):
            if prev_tail is not None and k < dissolve:
                a = (k + 1) / (dissolve + 1)
                img = cv2.addWeighted(img, a, prev_tail, 1 - a, 0)
            ff.stdin.write(img.tobytes())
            written += 1
        prev_tail = frames[n - 1]
        if time.time() - last_log > 20 and not QUIET:
            el = time.time() - t0
            log(f"    cut {ci + 1}/{len(plan)}  {hms(written / fps)} of {hms(total / fps)}"
                f"  (~{hms(el / max(written, 1) * (total - written))} left)")
            last_log = time.time()
    ff.stdin.close()
    ff.wait()
    try:
        os.remove(wav)
    except OSError:
        pass  # folder may not allow deletes; the temp wav is harmless


# ============================================================================
# output
# ============================================================================


# ============================================================================
# GLUE MODE: every short clip in a folder -> one long video
# ============================================================================

def stream_frames(path, fps, W, H):
    """Yield frames of a whole file at `fps`, fitted to WxH, without loading it all."""
    cmd = ["ffmpeg", "-v", "error"] + FF_THREADS + ["-i", path, "-an", "-sn", "-vf",
           f"fps={fps:g},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10 ** 7)
    fs = W * H * 3
    while True:
        buf = p.stdout.read(fs)
        if len(buf) < fs:
            break
        yield np.frombuffer(buf, np.uint8).reshape(H, W, 3)
    p.stdout.close()
    p.wait()


def gather_clips(args):
    clips = []
    for f in collect(args.corpus, VIDEO_EXT, args.exclude):
        info = probe(f)
        if not info or not info[2]:
            if info is None:
                log(f"    skip (unreadable): {os.path.basename(f)}")
            continue
        if 0.5 <= info[0] <= args.max_clip:
            clips.append((f, info))
    if not clips:
        sys.exit(f"no clips of {args.max_clip:g}s or shorter found")
    return clips


def order_clips(clips, order, rng):
    if order == "date":
        return sorted(clips, key=lambda c: os.stat(c[0]).st_mtime)
    if order == "random":
        return [clips[i] for i in rng.permutation(len(clips))]
    return sorted(clips, key=lambda c: os.path.basename(c[0]).lower())


def plan_glue(clips, args, rng):
    """Split clips into --count videos of about --hours each.
    Every clip is used once before any clip repeats; each pass reshuffles in random order."""
    xf_s = max(0, int(args.dissolve)) / args.fps
    if not args.hours:                                 # classic: everything, once, one video
        return [order_clips(clips, args.order, rng)]
    target = args.hours * 3600.0
    pool, parts, reuse = [], [], 0
    for _ in range(args.count):
        part, length = [], 0.0
        while length < target:
            if not pool:
                pool = list(order_clips(clips, args.order, rng))
                reuse += 1
            c = pool.pop(0)
            part.append(c)
            length += c[1][0] - (xf_s if len(part) > 1 else 0)
        parts.append(part)
    material = sum(c[1][0] for c in clips)
    need = target * args.count
    if need > material:
        log(f"  note: {len(clips)} clips = {hms(material)} of material, but {args.count} x {args.hours:g} h needs "
            f"{hms(need)} -> each clip appears about {need / material:.1f} times across the set")
    return parts


def glue(args, rng):
    rng = np.random.default_rng(args.seed if args.seed is not None else 1)   # same plan on every run
    clips = gather_clips(args)
    parts = plan_glue(clips, args, rng)
    folder = os.path.dirname(os.path.abspath(clips[0][0]))
    outs = []
    for k, part in enumerate(parts):
        if args.out and len(parts) == 1:
            out = args.out
            if not os.path.basename(out).lower().startswith("xik6_remix"):
                d, b = os.path.split(out)
                out = os.path.join(d, "xik6_remix_" + b)
        elif args.hours:
            out = os.path.join(args.out_dir or folder,
                               f"xik6_remix_glued_{args.hours:g}h_part{k + 1:02d}of{len(parts):02d}.mp4")
        else:
            out = os.path.join(args.out_dir or folder,
                               f"xik6_remix_glued_{len(part)}clips_{args.order}.mp4")
        if os.path.exists(out) and os.path.exists(os.path.splitext(out)[0] + ".json"):
            log(f"  [{k + 1}/{len(parts)}] already done, skipping: {os.path.basename(out)}")
            continue
        log(f"  [{k + 1}/{len(parts)}] {len(part)} clips -> {os.path.basename(out)}")
        render_glue(part, out, args)
        outs.append(out)
    log(f"  finished: {len(outs)} rendered, {len(parts) - len(outs)} already existed")


def render_glue(clips, out, args):
    W, H = map(int, args.size.lower().split("x"))
    fps = args.fps
    xf = max(0, int(args.dissolve))                 # overlap in frames at each join
    total = sum(c[1][0] for c in clips) - (len(clips) - 1) * xf / fps
    log(f"      length {hms(total)}")

    # ---- pass 1: audio as raw 16-bit PCM (no 4 GB WAV limit), frame-aligned, crossfaded
    pcm = out + ".tmp.s16"
    xs = int(round(xf / fps * SR))
    ramp = np.linspace(0, 1, xs, dtype=np.float32)[:, None] if xs else None
    counts = []
    t0 = time.time()
    with open(pcm, "wb") as fh:
        tail = None
        for i, (f, info) in enumerate(clips):
            nfr = max(1, int(round(info[0] * fps)))
            counts.append(nfr)
            need = int(round(nfr / fps * SR))
            a = load_audio(f) if info[1] else np.zeros((0, 2), np.float32)
            a = a[:need] if len(a) >= need else np.pad(a, ((0, need - len(a)), (0, 0)))
            if tail is not None and xs:
                a[:xs] = a[:xs] * ramp + tail * (1 - ramp)
            last = i == len(clips) - 1
            body = a if (last or not xs) else a[:-xs]
            tail = None if (last or not xs) else a[-xs:].copy()
            fh.write((np.clip(body, -1, 1) * 32767).astype(np.int16).tobytes())
    log(f"      sound assembled in {time.time() - t0:.0f}s")

    # ---- pass 2: video, streamed clip by clip, dissolved at joins
    part = out + ".part.mp4"
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
           "-r", str(fps), "-i", "-", "-f", "s16le", "-ar", str(SR), "-ac", "2", "-i", pcm,
           "-map", "0:v", "-map", "1:a", "-c:v", "libx264"] + FF_THREADS + [
           "-preset", args.preset, "-crf", str(args.crf), "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", "-shortest", part]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = time.time()
    last_log = t0
    written = 0
    held = []                                         # previous clip's last xf frames
    black = np.zeros((H, W, 3), np.uint8)
    edit = []
    for i, (f, info) in enumerate(clips):
        nfr = counts[i]
        last = i == len(clips) - 1
        edit.append({"src": f, "at": round(written / fps, 3), "len": round(info[0], 3)})
        k = 0
        buf = []
        prev = black
        for fr in stream_frames(f, fps, W, H):
            if k >= nfr:
                break
            prev = fr
            if held and k < len(held):
                a = (k + 1) / (len(held) + 1)
                fr = cv2.addWeighted(fr, a, held[k], 1 - a, 0)
            buf.append(fr)
            k += 1
            if len(buf) > xf:
                ff.stdin.write(buf.pop(0).tobytes())
                written += 1
        while k < nfr:                                 # clip shorter than it claimed: hold
            buf.append(prev)
            k += 1
            if len(buf) > xf:
                ff.stdin.write(buf.pop(0).tobytes())
                written += 1
        if last or not xf:
            for fr in buf:
                ff.stdin.write(fr.tobytes())
                written += 1
            held = []
        else:
            held = [b.copy() for b in buf[-xf:]]
            for fr in buf[:-xf]:
                ff.stdin.write(fr.tobytes())
                written += 1
        if time.time() - last_log > 60:
            el = time.time() - t0
            log(f"      clip {i + 1}/{len(clips)}  {hms(written / fps)} of {hms(total)}"
                f"  (~{hms(el / max(written, 1) * (total * fps - written))} left)")
            last_log = time.time()
    ff.stdin.close()
    ff.wait()
    for tmp in (pcm,):
        try:
            os.remove(tmp)
        except OSError:
            pass
    if ff.returncode != 0 or not os.path.exists(part):
        sys.exit(f"ffmpeg failed while writing {out}")
    os.replace(part, out)                              # only a finished file gets the real name
    save_plan(os.path.splitext(out)[0] + ".json",
              {"mode": "glue", "order": args.order, "clips": edit, "args": vars(args)})
    log(f"      wrote {os.path.basename(out)}  ({hms(written / fps)}, {os.path.getsize(out) / 1e9:.1f} GB)")



# ============================================================================
# FULL MODE: a remix exactly as long as each original (made in chunks, joined losslessly)
# ============================================================================

def concat_wavs_to_pcm(wavs, pcm_path):
    import wave
    with open(pcm_path, "wb") as out:
        for w in wavs:
            with wave.open(w, "rb") as r:
                while True:
                    b = r.readframes(SR * 60)
                    if not b:
                        break
                    out.write(b)


def full(args, rng):
    kind = args.kind
    files = collect(args.corpus, VIDEO_EXT, args.exclude)
    if args.driver:
        drivers = [args.driver]
    else:
        drivers = [f for f in files if (probe(f) or (0,))[0] >= args.min_hours * 3600]
    if not drivers:
        sys.exit(f"no videos of {args.min_hours:g} h or longer found to remix")
    log(f"  {len(drivers)} original(s) to remix ({kind}):")
    for d in drivers:
        log(f"    {hms(probe(d)[0])}  {os.path.basename(d)}")

    def paths_for(drv):
        stem = os.path.splitext(os.path.basename(drv))[0]
        ext = ".mov" if (kind == "sound" and drv.lower().endswith(".mov")) else ".mp4"
        folder = args.out_dir or os.path.dirname(os.path.abspath(drv))
        return (os.path.join(folder, f"xik6_remix_{stem}_{kind}{ext}"),
                os.path.join(folder, f"xik6_remix_work_{stem}_{kind}"))

    if args.status:
        done = part = todo = 0
        log("")
        for drv in drivers:
            out, work = paths_for(drv)
            n = max(1, int(np.ceil((probe(drv)[0] - 0.5) / args.chunk)))
            if os.path.exists(out):
                log(f"  DONE         {os.path.basename(out)}")
                done += 1
            elif os.path.isdir(work):
                k = len([f for f in os.listdir(work) if f.endswith(".done")])
                log(f"  PART-WAY     {os.path.basename(drv)}   {k}/{n} sections ({100 * k // max(n, 1)}%)")
                part += 1
            else:
                log(f"  not started  {os.path.basename(drv)}")
                todo += 1
        log(f"\n  {done} done, {part} part-way, {todo} not started")
        return

    acorp = vcorp = None
    if kind in ("sound", "remix"):
        afiles = collect(args.audio_corpus or args.corpus, AUDIO_EXT | VIDEO_EXT, args.exclude)
        log(f"  audio corpus: {len(afiles)} files")
        acorp = AudioCorpus(afiles, rng, global_rank=args.global_norm)
    if kind in ("video", "remix"):
        vfiles = collect(args.video_corpus or args.corpus, VIDEO_EXT, args.exclude)
        log(f"  video corpus: {len(vfiles)} files")
        vcorp = VideoCorpus(vfiles, args.fast, args.global_norm)

    seed = args.seed if args.seed is not None else 1
    grand_t0 = time.time()
    for di, drv in enumerate(drivers):
        info = probe(drv)
        dur = info[0]
        ext = ".mov" if (kind == "sound" and drv.lower().endswith(".mov")) else ".mp4"
        out, work = paths_for(drv)
        if os.path.exists(out):
            log(f"\n[{di + 1}/{len(drivers)}] already done, skipping: {os.path.basename(out)}")
            continue
        n = max(1, int(np.ceil((dur - 0.5) / args.chunk)))
        if os.path.isdir(work):
            k = len([f for f in os.listdir(work) if f.endswith(".done")])
            log(f"\n[{di + 1}/{len(drivers)}] {os.path.basename(drv)}  {hms(dur)}  -- resuming, {k}/{n} sections already made")
        else:
            log(f"\n[{di + 1}/{len(drivers)}] {os.path.basename(drv)}  {hms(dur)}")
        os.makedirs(work, exist_ok=True)
        mp4s, wavs = [], []
        t0 = time.time()
        made = 0
        for k in range(n):
            cs = k * args.chunk
            cd = min(args.chunk, dur - cs)
            if cd < 0.5:
                break
            cbase = os.path.join(work, f"xik6_remix_chunk_{k:04d}")
            mp4s.append(cbase + ".mp4")
            wavs.append(cbase + ".wav")
            if os.path.exists(cbase + ".done"):
                continue
            rk = np.random.default_rng(seed * 100003 + di * 1009 + k)
            if kind in ("sound", "remix"):
                audio = render_sound(drv, acorp, cs, cd, args, rk)
            else:
                audio = load_audio(drv, cs, cd)
                if len(audio) < int(cd * SR):
                    audio = np.pad(audio, ((0, int(cd * SR) - len(audio)), (0, 0)))
            write_wav(cbase + ".wav", audio)
            if kind in ("video", "remix"):
                plan = plan_video(audio, vcorp, args, rk)
                render_video(plan, vcorp, audio, cbase + ".mp4", args)
            open(cbase + ".done", "w").close()
            made += 1
            el = time.time() - t0
            left = el / made * (n - k - 1)
            log(f"    {hms(min((k + 1) * args.chunk, dur))} of {hms(dur)} made  (~{hms(left)} left for this video)")

        # ---- join losslessly
        log("    joining sections ...")
        part = out + ".part" + ext
        if kind in ("sound", "remix"):
            pcm = os.path.join(work, "xik6_remix_all.s16")
            concat_wavs_to_pcm(wavs, pcm)
        if kind == "sound":           # original picture untouched, new sound
            cmd = ["ffmpeg", "-v", "error", "-y", "-i", drv, "-f", "s16le", "-ar", str(SR), "-ac", "2", "-i", pcm,
                   "-map", "0:v:0", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest", part]
        else:
            lst = os.path.join(work, "xik6_remix_list.txt")
            with open(lst, "w") as fh:
                for m in mp4s:
                    fh.write("file '" + m.replace("'", "'\\''") + "'\n")
            if kind == "video":       # new picture, original sound
                cmd = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-i", drv,
                       "-map", "0:v", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
                       "-movflags", "+faststart", "-shortest", part]
            else:                     # both new
                cmd = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                       "-f", "s16le", "-ar", str(SR), "-ac", "2", "-i", pcm,
                       "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
                       "-movflags", "+faststart", "-shortest", part]
        r = subprocess.run(cmd)
        if r.returncode != 0 or not os.path.exists(part):
            log(f"    join FAILED for {os.path.basename(drv)}; sections are kept in {work}, re-run to retry")
            continue
        os.replace(part, out)
        log(f"    wrote {os.path.basename(out)}  ({hms(probe(out)[0])}, {os.path.getsize(out) / 1e9:.1f} GB)")
        if not args.keep_work:        # tidy up the sections (if the folder allows deleting)
            import shutil
            shutil.rmtree(work, ignore_errors=True)
            if os.path.exists(work):
                log(f"    (could not delete the work folder {os.path.basename(work)}; safe to delete by hand)")
    log(f"\nall done in {hms(time.time() - grand_t0)}")



# ============================================================================
# REACT MODE: the same video, reworked so the picture reacts to its own sound.
# One continuous pass, same length, original soundtrack untouched.
# ============================================================================

def react_controls(path, fps, dur):
    """Per-video-frame envelopes (0..1) from the file's own audio."""
    feats = analyse_audio(path)                      # loud, centroid, flatness, flux per 23 ms
    n = int(round(dur * fps))
    if len(feats) < 4:
        z = np.zeros(n, np.float32)
        return {"loud": z, "hit": z, "tone": z + 0.5, "noise": z}
    r = rank01(feats)
    t_a = (np.arange(len(feats)) * HOP + WIN / 2) / SR
    t_v = np.arange(n) / fps
    get = lambda j: np.interp(t_v, t_a, r[:, j]).astype(np.float32)
    loud, tone, noise, flux = get(0), get(1), get(2), get(3)
    # onset envelope: jump on strong flux, decay ~0.25 s
    hit = np.zeros(n, np.float32)
    dec = np.exp(-1.0 / (0.25 * fps))
    v = 0.0
    thr = 0.82
    for i in range(n):
        x = flux[i]
        v = max(v * dec, (x - thr) / (1 - thr) if x > thr else 0.0)
        hit[i] = v
    # loudness: fast attack, slower release
    env = np.zeros(n, np.float32)
    rel = np.exp(-1.0 / (0.35 * fps))
    v = 0.0
    for i in range(n):
        v = loud[i] if loud[i] > v else v * rel + loud[i] * (1 - rel)
        env[i] = v
    return {"loud": env, "hit": hit, "tone": smooth(tone, int(fps * 0.5)),
            "noise": smooth(noise, int(fps * 0.5))}


class Reactor:
    def __init__(self, W, H, amount):
        self.W, self.H, self.a = W, H, amount
        self.prev = None
        self.phase = 0.0
        self.hue_lut = [np.array([(h + k) % 180 for h in range(256)], np.uint8) for k in range(180)]

    def __call__(self, img, c):
        a = self.a
        L, hit, tone, noise = c
        W, H = self.W, self.H
        # 1. breathe + turn with the sound
        self.phase += 0.01 + 0.05 * L * a
        scale = 1.0 + a * (0.06 * L + 0.07 * hit)
        angle = a * (4.0 * np.sin(self.phase) + 6.0 * (tone - 0.5))
        M = cv2.getRotationMatrix2D((W / 2, H / 2), angle, scale)
        out = cv2.warpAffine(img, M, (W, H), borderMode=cv2.BORDER_REFLECT)
        # 2. colour follows the tone of the sound
        shift = int((tone - 0.5) * 50 * a) % 180
        if shift:
            hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV)
            hsv[..., 0] = cv2.LUT(hsv[..., 0], self.hue_lut[shift])
            out = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        # 3. pump with loudness
        # contrast pump around mid-grey; bright frames get pulled down instead of blown out
        mean = float(out.mean()) / 255.0
        alpha = 1.0 + 0.35 * a * (L - 0.4)
        beta = 128 * (1 - alpha) + 18 * a * hit * (1 - mean) - 60 * a * max(0.0, mean - 0.7)
        out = cv2.convertScaleAbs(out, alpha=alpha, beta=beta)
        # 4. hits: mirror-symmetry flash + colour split
        if hit > 0.05:
            m = min(1.0, hit * a)
            out = cv2.addWeighted(out, 1 - 0.5 * m, cv2.max(out, cv2.flip(out, 1)), 0.5 * m, 0)
            d = int(14 * m * a)
            if d:
                b, g, r = cv2.split(out)
                out = cv2.merge([np.roll(b, -d, axis=1), g, np.roll(r, d, axis=1)])
        # 5. noisy sound leaves trails
        if self.prev is not None:
            fb = min(0.85, a * (0.1 + 0.6 * noise) * (1 - 0.7 * hit))
            if fb > 0.02:
                out = cv2.addWeighted(out, 1 - fb, self.prev, fb, 0)
        self.prev = out
        return out


def react_one(path, out, args):
    info = probe(path)
    dur, has_a, has_v, sfps = info
    W, H = map(int, args.size.lower().split("x"))
    fps = args.fps or sfps
    log(f"      reading the sound ...")
    c = react_controls(path, fps, dur)
    n = len(c["loud"])
    part = out + ".part.mp4"
    enc = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
           "-r", f"{fps:g}", "-i", "-", "-i", path, "-map", "0:v", "-map", "1:a:0?",
           "-c:v", "libx264"] + FF_THREADS + ["-preset", args.preset, "-crf", str(args.crf),
           "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", "-shortest", part]
    ff = subprocess.Popen(enc, stdin=subprocess.PIPE)
    fx = Reactor(W, H, args.amount)
    t0 = time.time()
    last_log = t0
    i = 0
    last = np.zeros((H, W, 3), np.uint8)
    for fr in stream_frames(path, fps, W, H):
        if i >= n:
            break
        last = fx(fr, (c["loud"][i], c["hit"][i], c["tone"][i], c["noise"][i]))
        ff.stdin.write(last.tobytes())
        i += 1
        if time.time() - last_log > 60:
            el = time.time() - t0
            log(f"      {hms(i / fps)} of {hms(dur)}   (~{hms(el / i * (n - i))} left)")
            last_log = time.time()
    while i < n:                                      # keep picture as long as the sound
        ff.stdin.write(last.tobytes())
        i += 1
    ff.stdin.close()
    ff.wait()
    if ff.returncode != 0 or not os.path.exists(part):
        log(f"      FAILED: {os.path.basename(path)}")
        return False
    os.replace(part, out)
    log(f"      wrote {os.path.basename(out)}  ({hms(probe(out)[0])}, {os.path.getsize(out) / 1e9:.1f} GB,"
        f" {hms(time.time() - t0)})")
    return True


def react(args):
    if args.driver:
        todo = [args.driver]
    else:
        todo = [f for f in collect(args.corpus, VIDEO_EXT, args.exclude)
                if (probe(f) or (0,))[0] >= args.min_hours * 3600]
    todo = [f for f in todo if probe(f) and probe(f)[2]]
    if not todo:
        sys.exit(f"no videos of {args.min_hours:g} h or longer found")
    log(f"  {len(todo)} video(s):")
    for f in todo:
        log(f"    {hms(probe(f)[0])}  {os.path.basename(f)}")
    t0 = time.time()
    for k, f in enumerate(todo):
        stem = os.path.splitext(os.path.basename(f))[0]
        folder = args.out_dir or os.path.dirname(os.path.abspath(f))
        out = os.path.join(folder, f"xik6_remix_{stem}_react.mp4")
        if os.path.exists(out):
            log(f"  [{k + 1}/{len(todo)}] already done: {os.path.basename(out)}")
            continue
        log(f"  [{k + 1}/{len(todo)}] {os.path.basename(f)}")
        react_one(f, out, args)
    log(f"  all done in {hms(time.time() - t0)}")


def write_wav(path, x):
    import wave
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def mux(video_path, wav_path, out_path, crf, size, fps, start, dur):
    W, H = map(int, size.lower().split("x"))
    vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={fps}"
    cmd = ["ffmpeg", "-v", "error", "-y", "-stream_loop", "-1", "-ss", f"{start:.3f}", "-i", video_path,
           "-i", wav_path, "-map", "0:v", "-map", "1:a", "-vf", vf, "-c:v", "libx264"] + FF_THREADS + ["-crf", str(crf),
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-t", f"{dur:.3f}", out_path]
    subprocess.run(cmd, check=True)


def save_plan(path, meta):
    with open(path, "w") as fh:
        json.dump(meta, fh, indent=1)


def list_cache():
    if not os.path.isdir(CACHE_DIR):
        log("cache is empty")
        return
    tot = 0
    for f in sorted(os.listdir(CACHE_DIR)):
        sz = os.path.getsize(os.path.join(CACHE_DIR, f))
        tot += sz
    log(f"{CACHE_DIR}: {len(os.listdir(CACHE_DIR))} files, {tot / 1e9:.2f} GB")


# ============================================================================
# main
# ============================================================================

def main():
    global CACHE_DIR
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["react", "sound", "video", "remix", "full", "glue", "analyse", "list"])
    ap.add_argument("--driver", help="video (sound/remix) or audio/video (video mode) that steers the piece")
    ap.add_argument("--corpus", nargs="+", default=[], help="files/folders to draw material from")
    ap.add_argument("--audio-corpus", nargs="+", help="override: where sounds come from")
    ap.add_argument("--video-corpus", nargs="+", help="override: where images come from")
    ap.add_argument("--exclude", nargs="*", default=[], help="skip files whose path contains any of these")
    ap.add_argument("--out", help="output file (default: xik6_remix_<driver>_<seed>.mp4 next to the driver)")
    ap.add_argument("--include-remixes", action="store_true",
                    help="also use earlier xik6_remix* files as material (remixes of remixes)")
    ap.add_argument("--start", default="0", help='seconds into the driver, or "random"')
    ap.add_argument("--duration", type=float, help=f"seconds (default: driver length, max {DEFAULT_MAX_DUR:g})")
    ap.add_argument("--balance", type=float, default=0.75, help="0 = by length, 1 = every file equal")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--density", type=float, nargs=2, default=[6, 60])
    ap.add_argument("--grain", type=float, nargs=2, default=[40, 700])
    ap.add_argument("--transpose", type=float, default=7)
    ap.add_argument("--scale", type=str, default="", help='e.g. "0 3 5 7 10"')
    ap.add_argument("--reverse", type=float, default=0.3)
    ap.add_argument("--choices", type=int, default=8, help="randomness: pick among N best matches")
    ap.add_argument("--jitter", type=int, default=1, help="1 = organic grain timing, 0 = mechanical")
    ap.add_argument("--self-weight", type=float, default=0.0)
    ap.add_argument("--cut", type=float, nargs=2, default=[0.4, 3.0])
    ap.add_argument("--dissolve", type=int, default=6)
    ap.add_argument("--size", default="1280x720")
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--crf", type=int, default=19)
    ap.add_argument("--global-norm", action="store_true",
                    help="compare files on one shared scale (default: each file on its own scale)")
    ap.add_argument("--fast", action="store_true", help="keyframes-only video analysis (very quick, coarser)")
    ap.add_argument("--cache-dir", help=f"where analysis + decoded audio live (default {CACHE_DIR})")
    ap.add_argument("--max-clip", type=float, default=120,
                    help="glue: only clips this many seconds or shorter (default 120)")
    ap.add_argument("--order", choices=["name", "date", "random"], default="name",
                    help="glue: clip order (name = filename, usually the recording time)")
    ap.add_argument("--hours", type=float, default=0,
                    help="glue: make videos of at least this many hours (default: all clips once)")
    ap.add_argument("--count", type=int, default=1, help="glue: how many videos to make (with --hours)")
    ap.add_argument("--out-dir", help="glue: where to save (default: the clips' folder)")
    ap.add_argument("--preset", default="medium",
                    help="x264 speed: veryfast/faster/medium/slow (faster = quicker render, bigger file)")
    ap.add_argument("--kind", choices=["video", "sound", "remix"], default="remix",
                    help="full: remix = new sound + new picture (default); "
                         "video = new picture + original sound; sound = original picture + new sound")
    ap.add_argument("--status", action="store_true",
                    help="full: just report which videos are done / part-way / not started")
    ap.add_argument("--min-hours", type=float, default=1.0,
                    help="full: remix every video at least this long (default 1 h)")
    ap.add_argument("--chunk", type=float, default=600, help="full: section length in seconds (default 600)")
    ap.add_argument("--keep-work", action="store_true", help="full: keep the section files afterwards")
    ap.add_argument("--amount", type=float, default=1.0,
                    help="react: how strongly the picture reacts (0.5 subtle, 1 default, 2 wild)")
    ap.add_argument("--full-speed", action="store_true",
                    help="use all CPU cores at normal priority (default is gentle: low priority, 2 threads, "
                         "so a live broadcast on the same Mac keeps running smoothly)")
    ap.add_argument("--wav-only", action="store_true", help="sound mode: write only the WAV")
    args = ap.parse_args()
    if GENTLE:
        try:
            os.nice(15)
        except OSError:
            pass
    if args.cache_dir:
        CACHE_DIR = os.path.expanduser(args.cache_dir)
    args.scale = [float(v) for v in args.scale.split()] if args.scale else []
    args.balance = float(np.clip(args.balance, 0, 1))
    global INCLUDE_REMIXES
    INCLUDE_REMIXES = args.include_remixes

    if args.mode == "list":
        list_cache()
        return

    seed = args.seed if args.seed is not None else int(time.time()) % 100000
    rng = np.random.default_rng(seed)
    log(f"xik6_mosaic {args.mode}  seed {seed}")

    if args.mode == "analyse":
        files = collect(args.corpus, AUDIO_EXT | VIDEO_EXT, args.exclude)
        total = sum((probe(f) or (0,))[0] for f in files)
        log(f"  {len(files)} files, {hms(total)} of material")
        for i, f in enumerate(files):
            info = probe(f)
            log(f"  [{i + 1}/{len(files)}] {f}  {hms(info[0]) if info else 'UNREADABLE'}")
            if not info:
                continue
            if info[1]:
                analyse_audio(f)
            if info[2] and is_video(f):
                analyse_video(f, args.fast)
        list_cache()
        return

    if args.mode == "react":
        if not args.corpus and not args.driver:
            sys.exit("react needs --corpus <folder> (or --driver <one video>)")
        react(args)
        return

    if args.mode == "full":
        if not args.corpus:
            sys.exit("full needs --corpus <folder>")
        global QUIET
        QUIET = True
        full(args, rng)
        return

    if args.mode == "glue":
        if not args.corpus:
            sys.exit("glue needs --corpus <folder>")
        glue(args, rng)
        return

    if not args.driver:
        sys.exit("--driver is required")
    dinfo = probe(args.driver)
    if not dinfo:
        sys.exit(f"cannot read driver: {args.driver}")
    ddur = dinfo[0]
    dur = args.duration or min(ddur, DEFAULT_MAX_DUR)
    if args.start == "random":
        start = float(rng.uniform(0, max(ddur - dur, 0)))
    else:
        start = float(args.start)
    if not args.duration and ddur > DEFAULT_MAX_DUR:
        log(f"  driver is {hms(ddur)}; making a {hms(dur)} piece from {hms(start)} (use --duration / --start)")

    if not args.out:
        stem = os.path.splitext(os.path.basename(args.driver))[0][:40]
        args.out = os.path.join(os.path.dirname(os.path.abspath(args.driver)),
                                f"xik6_remix_{stem}_{args.mode}_s{seed}.mp4")
    elif not os.path.basename(args.out).lower().startswith("xik6_remix"):
        d, b = os.path.split(args.out)
        args.out = os.path.join(d, "xik6_remix_" + b)   # tag every output so it is recognised later
    audio_src = args.audio_corpus or args.corpus or [args.driver]
    video_src = args.video_corpus or args.corpus or [args.driver]
    base, _ = os.path.splitext(args.out)
    meta = {"mode": args.mode, "seed": seed, "driver": args.driver, "driver_start": start,
            "duration": dur, "args": vars(args)}

    if args.mode in ("sound", "remix"):
        if not dinfo[2]:
            sys.exit("sound/remix mode needs a VIDEO as --driver")
        files = collect(audio_src, AUDIO_EXT | VIDEO_EXT, args.exclude)
        log(f"  audio corpus: {len(files)} files")
        acorp = AudioCorpus(files, rng, global_rank=args.global_norm)
        log("  building sound from video ...")
        audio = render_sound(args.driver, acorp, start, dur, args, rng)
        meta["audio_corpus"] = [s.path for s in acorp.src]
        wav = base + ".wav"
        write_wav(wav, audio)
        log(f"  wrote {wav}")
        if args.mode == "sound":
            if not args.wav_only:
                mux(args.driver, wav, args.out, args.crf, args.size, args.fps, start, len(audio) / SR)
                log(f"  wrote {args.out}")
            save_plan(base + ".json", meta)
            return
    else:
        audio = load_audio(args.driver, start, dur)
        if not len(audio):
            sys.exit("driver has no audio")

    vfiles = collect(video_src, VIDEO_EXT, args.exclude)
    log(f"  video corpus: {len(vfiles)} files")
    vcorp = VideoCorpus(vfiles, args.fast, args.global_norm)
    log("  editing video from sound ...")
    plan = plan_video(audio, vcorp, args, rng)
    log(f"    {len(plan)} cuts")
    render_video(plan, vcorp, audio, args.out, args)
    meta["video_corpus"] = [s.path for s in vcorp.src]
    meta["edit"] = [{"src": vcorp.src[si].path, "src_time": round(st, 3), "at": round(at, 3),
                     "len": round(sg, 3)} for si, st, at, sg in plan]
    save_plan(base + ".json", meta)
    log(f"  wrote {args.out}  (+ {base}.json edit list)")


if __name__ == "__main__":
    main()
