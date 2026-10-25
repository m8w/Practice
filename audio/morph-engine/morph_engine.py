#!/usr/bin/env python3
"""
morph_engine.py — Audio-reactive image/video morphing engine
=============================================================

A unified rewrite of the m8w/1 morphing tools. One file, one CLI, no GUI
dependency. Adds the things the original scripts were missing:

  * Correspondence-correct MESH morphing (ORB feature matching + RANSAC),
    so triangles actually track image structure instead of warping between
    unrelated points.
  * An efficient single-accumulator triangle warp (no per-triangle full-frame
    allocations, no double-counting of overlaps).
  * AUDIO-REACTIVE morphing: the morph position and warp intensity are driven
    by the music (RMS envelope, onset strength, beat grid) instead of a flat
    linear ramp. Quiet passages hold on an image; loud passages rush through;
    transients kick extra warp; segment boundaries can snap to the beat.
  * Weighted blending of multiple techniques (mesh / flow / grid / dissolve).
  * Easing curves, ping-pong / seamless-loop, similarity ordering, LAB/HSV
    color-space morphing.
  * A JSON manifest written next to every render (all parameters + input
    file hashes) for reproducibility, plus --seed and --dry-run.
  * Video input (keyframes pulled from a clip) so the still-morpher and the
    video-morpher lineages live in one tool.

Requires: numpy, opencv-python, scipy, librosa, and ffmpeg on PATH.
Author-agnostic, MIT / Unlicense in the spirit of the original repo.
"""

import os
import sys
import glob
import json
import time
import math
import shutil
import hashlib
import argparse
import datetime
import tempfile
import subprocess
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import cv2
from scipy.spatial import Delaunay

# librosa is only needed when audio is supplied; import lazily so the tool
# still runs (image-only, --duration) on a box without it.
try:
    import librosa
    _HAVE_LIBROSA = True
except Exception:
    _HAVE_LIBROSA = False


IMAGE_EXTS = ["jpg", "jpeg", "png", "gif", "bmp", "tiff", "webp"]
VIDEO_EXTS = ["mp4", "mov", "mkv", "avi", "m4v", "webm"]


# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #

def log(msg):
    print(msg, flush=True)


def sha1_of_file(path, chunk=1 << 20):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()[:16]


def list_files_with_extensions(directory, extensions):
    files = []
    for ext in extensions:
        files.extend(glob.glob(os.path.join(directory, f"*.{ext}")))
        files.extend(glob.glob(os.path.join(directory, f"*.{ext.upper()}")))
    # de-dupe (case-insensitive filesystems can double-hit) and sort
    return sorted(set(files))


def which_ffmpeg():
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH. Install it and retry.")
    return exe


# --------------------------------------------------------------------------- #
# Easing curves  (applied to the fractional alpha within a segment)
# --------------------------------------------------------------------------- #

def ease(alpha, kind="smoothstep"):
    a = float(np.clip(alpha, 0.0, 1.0))
    if kind == "linear":
        return a
    if kind == "smoothstep":                 # ease-in-out
        return a * a * (3.0 - 2.0 * a)
    if kind == "smootherstep":
        return a * a * a * (a * (a * 6 - 15) + 10)
    if kind == "ease-in":
        return a * a
    if kind == "ease-out":
        return 1.0 - (1.0 - a) * (1.0 - a)
    if kind == "sine":
        return 0.5 - 0.5 * math.cos(math.pi * a)
    if kind == "exp":
        return 0.0 if a == 0 else math.pow(2, 10 * (a - 1))
    return a


# --------------------------------------------------------------------------- #
# Image ordering
# --------------------------------------------------------------------------- #

def _hist_signature(path, size=64, bins=8):
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        return np.zeros(bins * 3, dtype=np.float32)
    img = cv2.resize(img, (size, size))
    sig = []
    for c in range(3):
        h = cv2.calcHist([img], [c], None, [bins], [0, 256]).flatten()
        s = h.sum()
        sig.append(h / s if s else h)
    return np.concatenate(sig).astype(np.float32)


def order_images(paths, mode="asc", seed=0):
    """Return a reordered list of image paths.

    asc         - filename sort (stable, predictable)
    shuffle     - seeded random permutation (reproducible)
    similarity  - greedy nearest-neighbour by colour histogram (smooth journey)
    contrast    - greedy farthest-neighbour (maximal jumps between frames)
    """
    if mode == "asc":
        return sorted(paths)
    if mode == "shuffle":
        rng = np.random.default_rng(seed)
        idx = np.arange(len(paths))
        rng.shuffle(idx)
        return [paths[i] for i in idx]

    if mode in ("similarity", "contrast"):
        sigs = [_hist_signature(p) for p in paths]
        n = len(paths)
        used = [False] * n
        order = [0]
        used[0] = True
        for _ in range(n - 1):
            last = sigs[order[-1]]
            best_j, best_d = None, None
            for j in range(n):
                if used[j]:
                    continue
                d = float(np.linalg.norm(last - sigs[j]))
                if best_d is None or (d < best_d if mode == "similarity" else d > best_d):
                    best_d, best_j = d, j
            order.append(best_j)
            used[best_j] = True
        return [paths[i] for i in order]

    return sorted(paths)


# --------------------------------------------------------------------------- #
# Feature correspondence  (the real fix for the mesh morph)
# --------------------------------------------------------------------------- #

def _border_points(w, h):
    return np.array([
        [0, 0], [w - 1, 0], [0, h - 1], [w - 1, h - 1],
        [w // 2, 0], [w // 2, h - 1], [0, h // 2], [w - 1, h // 2],
        [w // 4, h // 4], [3 * w // 4, h // 4],
        [w // 4, 3 * h // 4], [3 * w // 4, 3 * h // 4],
    ], dtype=np.float32)


def matched_control_points(img1, img2, max_features=500, min_matches=10):
    """Return (pts1, pts2) that CORRESPOND: pts1[i] in img1 maps to pts2[i]
    in img2.  Uses ORB + ratio-test matching + RANSAC homography filtering,
    then pins the border so the frame edges stay put.  Falls back to a shared
    regular grid when too few reliable matches are found (abstract / noisy
    images), which degrades gracefully to a clean cross-dissolve-like morph."""
    g1 = cv2.cvtColor(img1, cv2.COLOR_RGB2GRAY)
    g2 = cv2.cvtColor(img2, cv2.COLOR_RGB2GRAY)
    h, w = g1.shape

    orb = cv2.ORB_create(nfeatures=max_features)
    k1, d1 = orb.detectAndCompute(g1, None)
    k2, d2 = orb.detectAndCompute(g2, None)

    pts1, pts2 = [], []
    if d1 is not None and d2 is not None and len(k1) >= 2 and len(k2) >= 2:
        bf = cv2.BFMatcher(cv2.NORM_HAMMING)
        knn = bf.knnMatch(d1, d2, k=2)
        good = []
        for pair in knn:
            if len(pair) == 2:
                m, n = pair
                if m.distance < 0.75 * n.distance:
                    good.append(m)
        if len(good) >= min_matches:
            src = np.float32([k1[m.queryIdx].pt for m in good])
            dst = np.float32([k2[m.trainIdx].pt for m in good])
            H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
            if mask is not None:
                inliers = mask.ravel().astype(bool)
                pts1 = list(src[inliers])
                pts2 = list(dst[inliers])

    if len(pts1) < min_matches:
        # Graceful fallback: identical grid in both images (no structural
        # correspondence available) -> mesh reduces to a soft dissolve.
        gx, gy = np.meshgrid(np.linspace(0, w - 1, 6), np.linspace(0, h - 1, 6))
        grid = np.stack([gx.ravel(), gy.ravel()], axis=1).astype(np.float32)
        pts1 = list(grid)
        pts2 = list(grid)

    border = _border_points(w, h)
    pts1 = np.array(pts1 + list(border), dtype=np.float32)
    pts2 = np.array(pts2 + list(border), dtype=np.float32)
    return pts1, pts2


# --------------------------------------------------------------------------- #
# Efficient triangle warp  (single accumulator, mask-composited)
# --------------------------------------------------------------------------- #

def _warp_triangle_into(acc, img1, img2, t1, t2, t, alpha):
    """Warp the triangle t1(img1) and t2(img2) into the destination triangle t,
    alpha-blend, and composite into accumulator `acc` using a local mask.
    Writes only the triangle's bounding box -> no full-frame allocation and no
    double counting where triangles meet."""
    r1 = cv2.boundingRect(np.float32([t1]))
    r2 = cv2.boundingRect(np.float32([t2]))
    r = cv2.boundingRect(np.float32([t]))
    if r[2] <= 0 or r[3] <= 0:
        return

    t1r = [(p[0] - r1[0], p[1] - r1[1]) for p in t1]
    t2r = [(p[0] - r2[0], p[1] - r2[1]) for p in t2]
    tr = [(p[0] - r[0], p[1] - r[1]) for p in t]

    img1_rect = img1[r1[1]:r1[1] + r1[3], r1[0]:r1[0] + r1[2]]
    img2_rect = img2[r2[1]:r2[1] + r2[3], r2[0]:r2[0] + r2[2]]
    if img1_rect.size == 0 or img2_rect.size == 0:
        return

    size = (r[2], r[3])
    m1 = cv2.getAffineTransform(np.float32(t1r), np.float32(tr))
    m2 = cv2.getAffineTransform(np.float32(t2r), np.float32(tr))
    w1 = cv2.warpAffine(img1_rect, m1, size, flags=cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REFLECT_101)
    w2 = cv2.warpAffine(img2_rect, m2, size, flags=cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REFLECT_101)
    patch = (1.0 - alpha) * w1.astype(np.float32) + alpha * w2.astype(np.float32)

    mask = np.zeros((r[3], r[2], 3), dtype=np.float32)
    cv2.fillConvexPoly(mask, np.int32(tr), (1.0, 1.0, 1.0), cv2.LINE_AA, 0)

    y0, y1 = r[1], r[1] + r[3]
    x0, x1 = r[0], r[0] + r[2]
    dst = acc[y0:y1, x0:x1]
    # clip in case a triangle pokes past the frame edge
    hh, ww = dst.shape[:2]
    acc[y0:y1, x0:x1] = dst * (1 - mask[:hh, :ww]) + patch[:hh, :ww] * mask[:hh, :ww]


# --------------------------------------------------------------------------- #
# Individual morph techniques.  Each returns a float32 HxWx3 image in [0,255].
# `gain` (0..1) scales warp intensity for audio reactivity.
# --------------------------------------------------------------------------- #

def morph_mesh(img1, img2, alpha, corr=None, gain=1.0):
    h, w = img1.shape[:2]
    if corr is None:
        corr = matched_control_points(img1, img2)
    p1, p2 = corr
    # audio gain pushes the interpolated points further toward p2's geometry
    a_geo = np.clip(alpha * (0.5 + 0.5 * gain) + (1 - gain) * alpha, 0, 1)
    pts = (1 - a_geo) * p1 + a_geo * p2

    # one triangulation on the average shape, reused for all three point sets
    avg = 0.5 * (p1 + p2)
    tri = Delaunay(avg)
    acc = np.zeros_like(img1, dtype=np.float32)
    f1 = img1.astype(np.float32)
    f2 = img2.astype(np.float32)
    for simplex in tri.simplices:
        _warp_triangle_into(acc, f1, f2,
                            p1[simplex], p2[simplex], pts[simplex], alpha)
    return acc


def morph_flow(img1, img2, alpha, gain=1.0):
    """Dense optical-flow morph. Robust on any imagery, needs no correspondence.
    Warps both images along the estimated flow toward the midpoint and blends."""
    g1 = cv2.cvtColor(img1, cv2.COLOR_RGB2GRAY)
    g2 = cv2.cvtColor(img2, cv2.COLOR_RGB2GRAY)
    flow_f = cv2.calcOpticalFlowFarneback(g1, g2, None,
                                          0.5, 3, 15, 3, 5, 1.2, 0)
    flow_b = cv2.calcOpticalFlowFarneback(g2, g1, None,
                                          0.5, 3, 15, 3, 5, 1.2, 0)
    h, w = g1.shape
    gx, gy = np.meshgrid(np.arange(w), np.arange(h))
    gx = gx.astype(np.float32)
    gy = gy.astype(np.float32)

    a = alpha * gain + alpha * (1 - gain) * 0.0  # gain scales displacement
    map1x = gx + flow_f[..., 0] * a
    map1y = gy + flow_f[..., 1] * a
    map2x = gx + flow_b[..., 0] * (1 - alpha) * gain
    map2y = gy + flow_b[..., 1] * (1 - alpha) * gain

    w1 = cv2.remap(img1, map1x, map1y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)
    w2 = cv2.remap(img2, map2x, map2y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)
    return (1 - alpha) * w1.astype(np.float32) + alpha * w2.astype(np.float32)


def morph_grid(img1, img2, alpha, grid=12, gain=1.0):
    """Sinusoidal grid warp toward a blended target — a looser, more liquid
    distortion that reads well on abstract material."""
    h, w = img1.shape[:2]
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    amp = 0.02 * min(h, w) * math.sin(math.pi * alpha) * gain
    dx = amp * np.sin(2 * math.pi * grid * gy / h)
    dy = amp * np.cos(2 * math.pi * grid * gx / w)
    w1 = cv2.remap(img1, gx + dx * alpha, gy + dy * alpha,
                   cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)
    w2 = cv2.remap(img2, gx - dx * (1 - alpha), gy - dy * (1 - alpha),
                   cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)
    return (1 - alpha) * w1.astype(np.float32) + alpha * w2.astype(np.float32)


def morph_dissolve(img1, img2, alpha, gain=1.0):
    return (1 - alpha) * img1.astype(np.float32) + alpha * img2.astype(np.float32)


# --------------------------------------------------------------------------- #
# Colour-space wrapper
# --------------------------------------------------------------------------- #

_CS_TO = {"lab": cv2.COLOR_RGB2LAB, "hsv": cv2.COLOR_RGB2HSV}
_CS_FROM = {"lab": cv2.COLOR_LAB2RGB, "hsv": cv2.COLOR_HSV2RGB}


def blended_morph(img1, img2, alpha, techniques, corr=None, gain=1.0, colorspace="rgb"):
    """Weighted blend of several techniques, computed in the chosen colour space."""
    if colorspace in _CS_TO:
        a = cv2.cvtColor(img1, _CS_TO[colorspace])
        b = cv2.cvtColor(img2, _CS_TO[colorspace])
    else:
        a, b = img1, img2

    total = sum(w for _, w in techniques) or 1.0
    out = np.zeros_like(a, dtype=np.float32)
    for name, weight in techniques:
        if weight <= 0:
            continue
        if name == "mesh":
            m = morph_mesh(a, b, alpha, corr=corr, gain=gain)
        elif name == "flow":
            m = morph_flow(a, b, alpha, gain=gain)
        elif name == "grid":
            m = morph_grid(a, b, alpha, gain=gain)
        else:
            m = morph_dissolve(a, b, alpha, gain=gain)
        out += (weight / total) * m

    out = np.clip(out, 0, 255).astype(np.uint8)
    if colorspace in _CS_FROM:
        out = cv2.cvtColor(out, _CS_FROM[colorspace])
    return out


# --------------------------------------------------------------------------- #
# Audio analysis  ->  per-frame morph position + warp gain
# --------------------------------------------------------------------------- #

def _normalize(x):
    x = np.asarray(x, dtype=np.float64)
    lo, hi = np.percentile(x, 5), np.percentile(x, 95)
    if hi - lo < 1e-9:
        return np.zeros_like(x)
    return np.clip((x - lo) / (hi - lo), 0, 1)


def build_schedule(audio_path, duration, fps, n_images, mode="linear",
                   pingpong=False, loop=False, easing="smoothstep"):
    """Return (positions, gains) arrays of length total_frames.

    positions[t] is a float in [0, n_segments]; its floor selects the image
    pair and its (eased) fraction is the alpha.  gains[t] in [0,1] modulates
    warp intensity for audio reactivity.  Audio drives BOTH how fast the morph
    advances (louder = faster) and how hard the images warp (transients kick)."""
    n_segments = n_images - 1
    if loop:
        n_segments += 1          # extra last->first segment
    if pingpong:
        n_segments *= 2          # forward then back

    if duration is None and audio_path and _HAVE_LIBROSA:
        duration = librosa.get_duration(path=audio_path)
    if duration is None:
        duration = max(2.0, n_segments * 2.0)   # sensible default

    total_frames = max(1, int(round(duration * fps)))

    # default: constant-speed progression
    speed = np.ones(total_frames)
    gains = np.ones(total_frames)

    if audio_path and _HAVE_LIBROSA and mode != "linear":
        y, sr = librosa.load(audio_path, sr=None, mono=True)
        hop = 512
        rms = librosa.feature.rms(y=y, hop_length=hop)[0]
        onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
        times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
        frame_t = np.linspace(0, duration, total_frames)

        rms_i = np.interp(frame_t, times, _normalize(rms))
        on_i = np.interp(frame_t, times, _normalize(onset[:len(times)]))

        if mode == "rms":
            speed = 0.15 + rms_i
            gains = 0.4 + 0.6 * rms_i
        elif mode == "onset":
            speed = 0.15 + on_i
            gains = 0.4 + 0.6 * on_i
        elif mode in ("beat", "full"):
            tempo, beats = librosa.beat.beat_track(y=y, sr=sr, hop_length=hop)
            beat_t = librosa.frames_to_time(beats, sr=sr, hop_length=hop)
            # snap segment boundaries to beats: build a monotonic position ramp
            # that reaches an integer image index at each beat.
            positions = np.zeros(total_frames)
            if len(beat_t) >= 2:
                seg_edges = np.linspace(0, n_segments, len(beat_t))
                positions = np.interp(frame_t, beat_t, seg_edges,
                                      left=0, right=n_segments)
            else:
                positions = np.linspace(0, n_segments, total_frames)
            gains = 0.4 + 0.6 * (rms_i if mode == "full" else on_i)
            return _finalize_positions(positions, n_segments, easing), np.clip(gains, 0, 1)

    # integrate speed -> cumulative position spanning [0, n_segments]
    cum = np.cumsum(speed)
    cum = cum / cum[-1] * n_segments
    return _finalize_positions(cum, n_segments, easing), np.clip(gains, 0, 1)


def _finalize_positions(raw, n_segments, easing):
    """Apply per-segment easing to the fractional part of the position ramp."""
    raw = np.clip(raw, 0, n_segments - 1e-6)
    seg = np.floor(raw).astype(int)
    frac = raw - seg
    eased = np.array([ease(f, easing) for f in frac])
    return seg + eased


# --------------------------------------------------------------------------- #
# Frame source (images, optionally harvested from a video)
# --------------------------------------------------------------------------- #

def extract_video_keyframes(video_path, n, workdir):
    """Pull n keyframes spread across a clip (scene-aware) as morph inputs."""
    ff = which_ffmpeg()
    out_pat = os.path.join(workdir, "kf_%04d.png")
    # grab scene changes; if too few, fall back to even sampling
    subprocess.run([ff, "-y", "-i", video_path, "-vf",
                    "select='gt(scene,0.3)',scale=iw:ih", "-vsync", "vfr",
                    out_pat], capture_output=True)
    frames = sorted(glob.glob(os.path.join(workdir, "kf_*.png")))
    if len(frames) < n:
        for f in frames:
            os.remove(f)
        probe = subprocess.run([ff, "-i", video_path], capture_output=True, text=True)
        dur = _parse_ffmpeg_duration(probe.stderr) or 10.0
        for i in range(n):
            t = dur * (i + 0.5) / n
            subprocess.run([ff, "-y", "-ss", str(t), "-i", video_path,
                            "-frames:v", "1",
                            os.path.join(workdir, f"kf_{i:04d}.png")],
                           capture_output=True)
        frames = sorted(glob.glob(os.path.join(workdir, "kf_*.png")))
    return frames


def _parse_ffmpeg_duration(text):
    for line in text.splitlines():
        if "Duration:" in line:
            try:
                hms = line.split("Duration:")[1].split(",")[0].strip()
                h, m, s = hms.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
            except Exception:
                return None
    return None


def load_images(paths, max_width=None):
    imgs = []
    target = None
    for p in paths:
        bgr = cv2.imread(p, cv2.IMREAD_COLOR)
        if bgr is None:
            log(f"  ! skipping unreadable image: {p}")
            continue
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        if max_width and rgb.shape[1] > max_width:
            scale = max_width / rgb.shape[1]
            rgb = cv2.resize(rgb, (max_width, int(rgb.shape[0] * scale)))
        if target is None:
            # even dims keep H.264 happy
            h, w = rgb.shape[:2]
            target = (w - (w % 2), h - (h % 2))
        rgb = cv2.resize(rgb, target)
        imgs.append(rgb)
    return imgs, target


# --------------------------------------------------------------------------- #
# Frame rendering
# --------------------------------------------------------------------------- #

def _render_worker(args):
    """Runs in a subprocess. Recomputes correspondence per segment as needed.
    We pass image bytes through shared temp files to avoid pickling big arrays."""
    (idx, pair_a_path, pair_b_path, alpha, gain, techniques,
     colorspace, out_path) = args
    a = cv2.cvtColor(cv2.imread(pair_a_path), cv2.COLOR_BGR2RGB)
    b = cv2.cvtColor(cv2.imread(pair_b_path), cv2.COLOR_BGR2RGB)
    corr = matched_control_points(a, b) if any(t[0] == "mesh" for t in techniques) else None
    frame = blended_morph(a, b, alpha, techniques, corr=corr,
                          gain=gain, colorspace=colorspace)
    cv2.imwrite(out_path, cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_PNG_COMPRESSION, 3])
    return idx


def render_frames(images, positions, gains, techniques, colorspace,
                  workdir, jobs=1, pingpong=False, loop=False):
    """Generate every output frame into workdir/frame_******.png."""
    n = len(images)
    seq = list(range(n))
    if loop:
        seq = seq + [0]
    if pingpong:
        seq = seq + seq[-2::-1]

    # write the (reused) segment endpoint images to disk once
    img_paths = []
    for i, im in enumerate(images):
        p = os.path.join(workdir, f"src_{i:03d}.png")
        cv2.imwrite(p, cv2.cvtColor(im, cv2.COLOR_RGB2BGR))
        img_paths.append(p)

    def path_for_seq_index(k):
        return img_paths[seq[k]]

    total = len(positions)
    tasks = []
    for t, pos in enumerate(positions):
        k = int(math.floor(pos))
        k = min(k, len(seq) - 2)
        alpha = pos - k
        out_path = os.path.join(workdir, f"frame_{t:06d}.png")
        tasks.append((t, path_for_seq_index(k), path_for_seq_index(k + 1),
                      float(alpha), float(gains[t]), techniques,
                      colorspace, out_path))

    done = 0
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            futs = [ex.submit(_render_worker, task) for task in tasks]
            for _ in as_completed(futs):
                done += 1
                if done % 10 == 0 or done == total:
                    log(f"  frames {done}/{total} ({100*done/total:.0f}%)")
    else:
        for task in tasks:
            _render_worker(task)
            done += 1
            if done % 10 == 0 or done == total:
                log(f"  frames {done}/{total} ({100*done/total:.0f}%)")

    return total


# --------------------------------------------------------------------------- #
# Muxing
# --------------------------------------------------------------------------- #

def assemble_video(workdir, out_path, fps, audio_path=None,
                   codec="h264", crf=18):
    ff = which_ffmpeg()
    frame_glob = os.path.join(workdir, "frame_%06d.png")

    if codec == "prores":
        vcodec = ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"]
    elif codec == "gif":
        # two-pass palette for a clean gif
        palette = os.path.join(workdir, "palette.png")
        subprocess.run([ff, "-y", "-framerate", str(fps), "-i", frame_glob,
                        "-vf", "palettegen", palette], capture_output=True)
        subprocess.run([ff, "-y", "-framerate", str(fps), "-i", frame_glob,
                        "-i", palette, "-lavfi", "paletteuse", out_path],
                       capture_output=True)
        return out_path
    else:
        vcodec = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf),
                  "-preset", "medium"]

    cmd = [ff, "-y", "-framerate", str(fps), "-i", frame_glob]
    if audio_path:
        cmd += ["-i", audio_path]
    cmd += vcodec
    if audio_path:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd += [out_path]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError("ffmpeg mux failed:\n" + res.stderr[-1500:])
    return out_path


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #

def write_manifest(out_path, params, image_paths, audio_path, extra=None):
    manifest = {
        "generator": "morph_engine.py",
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "output": os.path.basename(out_path),
        "parameters": params,
        "inputs": {
            "images": [{"file": os.path.basename(p), "sha1": sha1_of_file(p)}
                       for p in image_paths if os.path.exists(p)],
            "audio": ({"file": os.path.basename(audio_path),
                       "sha1": sha1_of_file(audio_path)}
                      if audio_path and os.path.exists(audio_path) else None),
        },
    }
    if extra:
        manifest.update(extra)
    man_path = os.path.splitext(out_path)[0] + ".manifest.json"
    with open(man_path, "w") as f:
        json.dump(manifest, f, indent=2)
    return man_path


# --------------------------------------------------------------------------- #
# Technique parsing
# --------------------------------------------------------------------------- #

def parse_techniques(spec):
    """'mesh:0.6,flow:0.3,dissolve:0.1' -> [('mesh',0.6),('flow',0.3),...]"""
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            name, w = part.split(":")
            out.append((name.strip(), float(w)))
        else:
            out.append((part, 1.0))
    return out


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def run(args):
    t0 = time.time()

    # ---- gather inputs ----
    workdir = args.workdir or tempfile.mkdtemp(prefix="morph_")
    os.makedirs(workdir, exist_ok=True)

    if args.video:
        log(f"Extracting {args.frames} keyframes from video …")
        image_files = extract_video_keyframes(args.video, args.frames, workdir)
    elif args.images:
        image_files = []
        for token in args.images:
            if os.path.isdir(token):
                image_files += list_files_with_extensions(token, IMAGE_EXTS)
            else:
                image_files += sorted(glob.glob(token))
    else:
        src = args.input or str(Path.home() / "Downloads")
        image_files = list_files_with_extensions(src, IMAGE_EXTS)

    if len(image_files) < 2:
        log("Need at least 2 images. Nothing to do.")
        return 1

    image_files = order_images(image_files, args.order, seed=args.seed)
    log(f"Using {len(image_files)} images (order={args.order}):")
    for p in image_files:
        log(f"   • {os.path.basename(p)}")

    audio_path = args.audio
    if audio_path and not os.path.exists(audio_path):
        log(f"  ! audio not found: {audio_path} (continuing silent)")
        audio_path = None

    techniques = parse_techniques(args.techniques)
    log(f"Techniques: {techniques}  |  audio-react={args.audio_react}  "
        f"easing={args.easing}  colorspace={args.colorspace}")

    fps = args.fps
    if args.preview:
        fps = max(12, fps // 2)
        args.max_width = min(args.max_width or 640, 640)

    # ---- schedule (this is where the music drives the motion) ----
    positions, gains = build_schedule(
        audio_path, args.duration, fps, len(image_files),
        mode=args.audio_react, pingpong=args.pingpong,
        loop=args.loop, easing=args.easing)
    log(f"Schedule: {len(positions)} frames @ {fps} fps "
        f"(~{len(positions)/fps:.1f}s)")

    # ---- output path + manifest ----
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = "gif" if args.codec == "gif" else ("mov" if args.codec == "prores" else "mp4")
    out_dir = args.output_dir or str(Path.home() / "Downloads")
    os.makedirs(out_dir, exist_ok=True)
    out_path = args.output or os.path.join(out_dir, f"morph_{ts}.{ext}")

    params = vars(args).copy()
    params["effective_fps"] = fps
    params["total_frames"] = len(positions)
    man = write_manifest(out_path, params, image_files, audio_path)
    log(f"Manifest → {man}")

    if args.dry_run:
        log("Dry run — plan + manifest written, no frames rendered.")
        return 0

    # ---- load, render, mux ----
    images, target = load_images(image_files, max_width=args.max_width)
    log(f"Frame size: {target[0]}x{target[1]}")

    log("Rendering frames …")
    render_frames(images, positions, gains, techniques, args.colorspace,
                  workdir, jobs=args.jobs, pingpong=args.pingpong, loop=args.loop)

    log("Muxing with ffmpeg …")
    assemble_video(workdir, out_path, fps, audio_path=audio_path,
                   codec=args.codec, crf=args.crf)

    if not args.keep_frames and not args.workdir:
        shutil.rmtree(workdir, ignore_errors=True)

    log(f"\n✓ Done in {time.time()-t0:.1f}s → {out_path}")

    # open the folder on desktop OSes
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", os.path.dirname(out_path)])
        elif sys.platform == "win32":
            subprocess.run(["explorer", os.path.dirname(out_path)])
    except Exception:
        pass
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        description="Audio-reactive image/video morphing engine.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    src = p.add_argument_group("inputs")
    src.add_argument("images", nargs="*",
                     help="Image files, globs, or folders. Omit to use --input.")
    src.add_argument("--input", help="Folder to scan for images "
                                     "(default: ~/Downloads).")
    src.add_argument("--video", help="Use keyframes from this video as inputs.")
    src.add_argument("--frames", type=int, default=8,
                     help="Number of keyframes to pull from --video.")
    src.add_argument("--audio", help="Audio track (wav/mp3/…) to drive + mux.")

    out = p.add_argument_group("output")
    out.add_argument("--output", help="Explicit output path.")
    out.add_argument("--output-dir", help="Output folder (default: ~/Downloads).")
    out.add_argument("--fps", type=int, default=30)
    out.add_argument("--duration", type=float,
                     help="Seconds (default: audio length or auto).")
    out.add_argument("--codec", choices=["h264", "prores", "gif"], default="h264")
    out.add_argument("--crf", type=int, default=18, help="H.264 quality (lower=better).")

    look = p.add_argument_group("look")
    look.add_argument("--techniques", default="flow:0.7,dissolve:0.3",
                      help="Weighted blend, e.g. 'mesh:0.6,flow:0.3,dissolve:0.1'. "
                           "Techniques: mesh, flow, grid, dissolve.")
    look.add_argument("--easing", default="smoothstep",
                      choices=["linear", "smoothstep", "smootherstep",
                               "ease-in", "ease-out", "sine", "exp"])
    look.add_argument("--colorspace", default="rgb", choices=["rgb", "lab", "hsv"])
    look.add_argument("--order", default="asc",
                      choices=["asc", "shuffle", "similarity", "contrast"])
    look.add_argument("--pingpong", action="store_true",
                      help="Morph forward then back (seamless boomerang).")
    look.add_argument("--loop", action="store_true",
                      help="Add a final last→first morph for clean looping.")

    react = p.add_argument_group("audio reactivity")
    react.add_argument("--audio-react", default="full",
                       choices=["linear", "rms", "onset", "beat", "full"],
                       help="How the audio drives the morph. linear = ignore audio "
                            "motion; rms/onset = speed+warp follow loudness/transients; "
                            "beat = snap image changes to the beat; full = beat grid + "
                            "loudness-driven warp.")

    perf = p.add_argument_group("performance / repro")
    perf.add_argument("--max-width", type=int, help="Downscale wide images to this.")
    perf.add_argument("--jobs", type=int, default=1, help="Parallel frame workers.")
    perf.add_argument("--preview", action="store_true",
                      help="Fast low-res/low-fps pass for iteration.")
    perf.add_argument("--seed", type=int, default=0, help="RNG seed (shuffle order).")
    perf.add_argument("--workdir", help="Persistent frame cache dir.")
    perf.add_argument("--keep-frames", action="store_true")
    perf.add_argument("--dry-run", action="store_true",
                      help="Write the manifest + plan without rendering.")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        sys.exit(run(args))
    except KeyboardInterrupt:
        log("\nInterrupted.")
        sys.exit(130)


if __name__ == "__main__":
    main()
