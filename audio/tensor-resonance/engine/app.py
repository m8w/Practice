"""
TENSOR RESONANCE ENGINE — Phase 1 backend
=========================================

5x5x5 tensor  T[color, loudness, frequency]   (the 3x3x3 slots + an in-between slot on each side of the middle)
    mode 0  COLOR     : RED / PURPLE / BLUE / GREEN / YELLOW      -> timbre (saw / saw<->sine / sine / sine<->tri / triangle)
    mode 1  LOUDNESS  : LOUD / LOUD-SOFT / SOFT / SOFT-MIDDLE / MIDDLE -> envelope (level + attack)
    mode 2  FREQUENCY : F1 / F1-2 / F2 / F2-3 / F3                -> drift scale (0.5 .. 2)
    The in-between slots are variable blends of their neighbours (sliders, or following the tensor).

Every step:
  1. evolve T. Two flows:
     "relax" (default, Tensor Flow v2, see flow.py): T relaxes toward a moving equilibrium with
        damped sound waves on the 5x5x5 lattice; the modes cross-attend; new equilibria arrive
        at random epochs, sometimes recalled from distilled memory; phantom crossings re-roll.
     "classic": diagonal-dominant coupling + continuous noise + arrhythmic Poisson kicks.
  2. Tucker / HOSVD  -> 3 modes x 5 singular values = 15 sigma
  3. velocity  v_i = d(sigma_i)/dt
  4. each sigma_i owns ONE triad of 3 unaligned microtonal frequencies (20 Hz - 20 kHz,
     log-uniform, no scale, no ratios). Each frequency drifts in log space at a speed
     proportional to v_i. Triads re-roll at random (Poisson) moments whose rate grows with |v_i|
     -> no beat grid, no pattern.
  5. stream state over Socket.IO to index.html, and (optionally) out as MPE-style MIDI.

Run (Mac / Linux):
    ./run.sh                          # makes its own .venv the first time, opens the browser
    ./run.sh --midi --midi-mode dexed # Dexed: channel 1, nearest semitone (IAC Driver if on)
    ./run.sh --midi                   # MPE microtones for MPE synths (Surge XT, Vital)
    ./run.sh --midi-test --midi-mode dexed   # test chord, then exit
    ./run.sh --check                  # diagnostics - paste this if something doesn't work
Also served: /surge.html (Surge XT patch changer), recording to ~/Music/TensorResonance,
DUDLEY renders (dudley/), album mode. See README.md.
"""

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import wave
import webbrowser
from pathlib import Path
from types import SimpleNamespace

try:
    import numpy as np
    from flask import Flask, send_from_directory, send_file, jsonify, request, abort
    from flask_cors import CORS
    from flask_socketio import SocketIO, emit
except ImportError as _e:  # e.g. started from Spyder, whose Python doesn't have Flask
    raise SystemExit(
        f"\n  The engine needs a package this Python doesn't have ({_e.name}).\n"
        "  Start it from Terminal instead; run.sh installs everything in its own folder:\n\n"
        f"      cd '{os.path.dirname(os.path.abspath(__file__))}'\n"
        "      ./run.sh\n\n"
        "  (For Surge patch changing alone, surge.py runs anywhere, Spyder included.)\n") from None

try:  # tensorly is optional: full-rank Tucker == HOSVD, which numpy does exactly
    import tensorly as tl
    from tensorly.decomposition import tucker as tl_tucker
    HAVE_TENSORLY = True
except Exception:  # pragma: no cover
    HAVE_TENSORLY = False

try:
    import mido
    HAVE_MIDO = True
except Exception:  # pragma: no cover
    HAVE_MIDO = False

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import surge as surgelib  # noqa: E402  (Surge XT patch changing: OSC + MIDI Programs folder)
import flow as flowlib  # noqa: E402  (Tensor Flow v2: relaxation flow + cross-attention + readout)
import dexed as dexedlib  # noqa: E402  (Dexed: voices as SysEx, never Program Change - see dexed.py)
SIZE, CELLS, TRIADS = flowlib.SIZE, flowlib.CELLS, flowlib.TRIADS      # 5, 125, 15
COLOR_NAMES = ["RED", "PURPLE", "BLUE", "GREEN", "YELLOW"]
LOUD_NAMES = ["LOUD", "LOUD-SOFT", "SOFT", "SOFT-MIDDLE", "MIDDLE"]
FREQ_NAMES = ["F1", "F1-2", "F2", "F2-3", "F3"]
MODE_NAMES = ["COLOR", "LOUDNESS", "FREQUENCY"]
SIGMA_NAMES = [f"{m}{r + 1}" for m in "CLF" for r in range(SIZE)]
BLEND_KEYS = [["blend_c1", "blend_c2"], ["blend_l1", "blend_l2"], ["blend_f1", "blend_f2"]]
SC = math.sqrt(27 / CELLS)          # per-cell scale, so overall motion matches the 3x3x3 engine


# ----------------------------------------------------------------------------- engine
class TensorResonanceEngine:
    """5x5x5 tensor. On every axis the original three slots sit at 0, 2, 4 and an in-between
    slot at 1 and 3 whose meaning is a variable blend of its two neighbours:
        COLOR      RED · PURPLE (saw<->sine) · BLUE · GREEN (sine<->triangle) · YELLOW
        LOUDNESS   LOUD · LOUD-SOFT · SOFT · SOFT-MIDDLE · MIDDLE        (level + attack)
        FREQUENCY  F1 · F1-2 · F2 · F2-3 · F3                            (drift scale 0.5 .. 2)
    blend 0 = like the left neighbour, 1 = like the right one. With blend_follow on, each blend
    follows the tensor: it leans toward whichever neighbouring slice is stronger right now."""

    def __init__(self, seed=None):
        self.rng = np.random.default_rng(seed)
        self.lock = threading.Lock()
        self.params = dict(
            f_min=20.0, f_max=20000.0,     # audible range, Hz
            drift_gain=6.0,                # octaves per (unit sigma / s)
            reroll_base=0.15,              # re-rolls per second per triad at rest (15 triads now)
            reroll_vel=4.0,                # extra re-rolls per unit |dsigma/dt|
            kick_rate=0.7,                 # arrhythmic tensor kicks per second
            kick_amp=0.45,
            noise=0.12,                    # continuous wander
            couple_rate=0.8,               # how fast coupling matrices act
            energy=2.2,                    # Frobenius norm the tensor relaxes toward
            flow="relax",                  # "relax" (Tensor Flow v2) or "classic"
            expansion=0.6,                 # relax: e-folds per second of the internal clock (H)
            relax_n=0.5,                   # relax: fractional relaxation per e-fold (n)
            sound_cs=1.5,                  # relax: sound speed of lattice waves (c_s)
            xattn=0.5,                     # relax: cross-attention strength in the equilibrium (κ)
            memory=0.4,                    # relax: chance an epoch recalls a distilled prototype
            epoch_rate=0.1,                # relax: new equilibria per second (Poisson)
            blend_c1=0.5, blend_c2=0.5,    # PURPLE (red->blue), GREEN (blue->yellow)
            blend_l1=0.5, blend_l2=0.5,    # LOUD-SOFT, SOFT-MIDDLE
            blend_f1=0.5, blend_f2=0.5,    # F1-2, F2-3
            blend_follow=0.0,              # 1 = the in-betweens follow the tensor
        )
        self.weights = np.ones((3, SIZE))  # slice weights [mode][slot] from the editor
        self.reset()

    # ------------------------------------------------------------------ setup
    def reset(self):
        r = self.rng
        T = r.uniform(-0.3 * SC, 0.3 * SC, (SIZE, SIZE, SIZE))
        for i in range(SIZE):
            T[i, i, i] = r.uniform(0.9, 1.3)
        self.T = T
        self.coupling = []
        for _ in range(3):
            M = r.uniform(-0.08, 0.08, (SIZE, SIZE)) * math.sqrt(2 / (SIZE - 1))
            np.fill_diagonal(M, r.uniform(0.85, 0.92, SIZE))
            self.coupling.append(M)
        self.t = 0.0
        self.flow = flowlib.RelaxFlow(self.rng)
        self.crossings = []
        self.bl = self._blend_params()
        self.sigma, self.factors = self.decompose(self.effective())
        self.vel = np.zeros(TRIADS)
        self.triads = np.zeros((TRIADS, 3))
        self.dirs = np.zeros((TRIADS, 3))
        self.gen = np.zeros(TRIADS, dtype=int)
        for i in range(TRIADS):
            self._reroll(i)
        self.events = []

    def _rand_freq(self, n):
        lo, hi = math.log2(self.params["f_min"]), math.log2(self.params["f_max"])
        return 2.0 ** self.rng.uniform(lo, hi, n)

    def _reroll(self, i):
        self.triads[i] = self._rand_freq(3)
        self.dirs[i] = self.rng.choice([-1.0, 1.0], 3) * self.rng.uniform(0.4, 1.6, 3)
        self.gen[i] += 1

    # ------------------------------------------------------------------ math
    def wmask(self):
        w0, w1, w2 = self.weights
        return w0[:, None, None] * w1[None, :, None] * w2[None, None, :]

    def effective(self):
        return self.T * self.wmask()

    def _sigma_of(self, Tflat):
        return self.decompose(np.asarray(Tflat).reshape(SIZE, SIZE, SIZE) * self.wmask())[0]

    def relax(self):
        return self.params.get("flow", "relax") == "relax"

    @staticmethod
    def decompose(T):
        """Tucker (full rank) / HOSVD. Returns sigma[3*SIZE] (mode-major) and factor matrices."""
        factors, sig = [], []
        for n in range(3):
            unf = np.moveaxis(T, n, 0).reshape(T.shape[n], -1)
            U, s, _ = np.linalg.svd(unf, full_matrices=False)
            factors.append(U)
            sig.append(s)
        return np.concatenate(sig), factors

    @staticmethod
    def tensorly_crosscheck(T):
        """Run tensorly's Tucker once and confirm it gives the same sigma as the numpy HOSVD."""
        if not HAVE_TENSORLY:
            return None
        core, _ = tl_tucker(tl.tensor(T), rank=list(T.shape), init="svd", n_iter_max=10)
        core = np.asarray(core)
        tl_sig = np.concatenate([np.linalg.svd(np.moveaxis(core, n, 0).reshape(T.shape[n], -1), compute_uv=False)
                                 for n in range(3)])
        return float(np.max(np.abs(tl_sig - TensorResonanceEngine.decompose(T)[0])))

    def core(self):
        U0, U1, U2 = self.factors
        return np.einsum("ai,bj,ck,abc->ijk", U0, U1, U2, self.effective())

    # ------------------------------------------------------------------ in-between slots
    def _blend_params(self):
        return np.array([[float(self.params[k]) for k in row] for row in BLEND_KEYS])

    def _update_blends(self, dt):
        """The in-between slots' variable values: the sliders, or (follow) the balance of the two
        neighbouring slices of the tensor: 0 = all left neighbour, 1 = all right neighbour."""
        if self.params.get("blend_follow", 0) < 0.5:
            self.bl = self._blend_params()
            return
        E, k = self.effective(), 1 - math.exp(-dt / 0.5)
        for m in range(3):
            e = np.linalg.norm(np.moveaxis(E, m, 0).reshape(SIZE, -1), axis=1)
            for n, s in enumerate((1, 3)):
                target = e[s + 1] / (e[s - 1] + e[s + 1] + 1e-9)
                self.bl[m, n] += k * (target - self.bl[m, n])

    def freq_log2(self):
        """log2 drift scale of the 5 FREQUENCY slots: 0.5, between, 1, between, 2."""
        b = self.bl[2]
        return np.array([-1.0, -1.0 + b[0], 0.0, b[1], 1.0])

    # ------------------------------------------------------------------ evolution
    def step(self, dt):
        p, r = self.params, self.rng
        with self.lock:
            relax = self.relax()
            if relax:
                Tf = self.T.reshape(CELLS).copy()
                self.flow.step(Tf, dt, p, self._sigma_of)
                self.T = Tf.reshape(SIZE, SIZE, SIZE)
            else:
                self._classic(dt)
            self._update_blends(dt)

            # decompose + velocity
            sig, self.factors = self.decompose(self.effective())
            raw_v = (sig - self.sigma) / max(dt, 1e-4)
            self.vel = 0.75 * self.vel + 0.25 * raw_v
            self.sigma = sig

            # relax: composition (phantom crossings) + multi-granular readout + aspects
            drift_mul, reroll_mul, cross = 1.0, 1.0, []
            if relax:
                H = p["expansion"]
                cross = self.flow.composition(sig, dt, H)
                m = self.wmask().reshape(CELLS)
                self.flow.read(self.T.reshape(CELLS) * m, self.flow.V * m, sig, self.vel, H)
                asp = self.flow.score(sig, self.vel, sig / (sig.sum() + 1e-9), dt)
                drift_mul, reroll_mul = 0.5 + asp["fluency"], 0.5 + asp["prosody"]
            self.crossings = cross

            # drift + arrhythmic re-roll
            lo, hi = math.log2(p["f_min"]), math.log2(p["f_max"])
            dscale = self.drift_scale()
            ev = []
            for i in range(TRIADS):
                lf = np.log2(self.triads[i])
                lf += p["drift_gain"] * drift_mul * self.vel[i] * self.dirs[i] * dscale[i] * dt
                # reflect at the walls of the audible range
                over, under = lf > hi, lf < lo
                lf[over] = 2 * hi - lf[over]
                lf[under] = 2 * lo - lf[under]
                self.dirs[i][over | under] *= -1
                self.triads[i] = 2.0 ** np.clip(lf, lo, hi)
                rate = (p["reroll_base"] + p["reroll_vel"] * abs(self.vel[i])) * reroll_mul
                if i in cross or r.random() < 1 - math.exp(-rate * dt):
                    self._reroll(i)
                    ev.append(i)
            self.t += dt
            self.events = ev
            return self.snapshot()

    def _classic(self, dt):
        p, r = self.params, self.rng
        T = self.T
        # coupling: T x1 M0 x2 M1 x3 M2, applied fractionally so it is dt-independent
        M0, M1, M2 = self.coupling
        TM = np.einsum("ai,bj,ck,ijk->abc", M0, M1, M2, T)
        a = min(1.0, p["couple_rate"] * dt)
        T = (1 - a) * T + a * TM
        # continuous wander
        T = T + r.normal(0, p["noise"] * math.sqrt(dt) * SC, T.shape)
        # arrhythmic kicks (Poisson process -> no grid)
        if r.random() < 1 - math.exp(-p["kick_rate"] * dt):
            if r.random() < 0.5:
                T[tuple(r.integers(0, SIZE, 3))] += r.normal(0, p["kick_amp"] * 2)
            else:
                T = T + r.normal(0, p["kick_amp"] * 0.5 * SC, T.shape)
            # occasionally re-wire one coupling matrix
            if r.random() < 0.3:
                M = self.coupling[r.integers(0, 3)]
                M += r.normal(0, 0.03, (SIZE, SIZE))
                np.fill_diagonal(M, np.clip(np.diag(M), 0.85, 0.92))
        # soft energy normalisation
        nrm = np.linalg.norm(T) + 1e-9
        T *= (p["energy"] / nrm) ** min(1.0, 2.5 * dt)
        self.T = T

    def new_epoch(self):
        """A new equilibrium now (the NEW EPOCH button)."""
        p = self.params
        self.flow.new_epoch(self.T.reshape(CELLS), p["energy"], p["memory"], self._sigma_of)

    # ------------------------------------------------------------------ semantics
    def _dominant(self, mode):
        """for each rank r of `mode`, which semantic slot dominates factor column r"""
        U = self.factors[mode]
        return [int(np.argmax(np.abs(U[:, r]))) for r in range(SIZE)]

    def _readout_ready(self):
        return self.relax() and self.flow.timbre is not None

    def timbre(self):     # per sigma_i : 0 RED saw, 1 PURPLE, 2 BLUE sine, 3 GREEN, 4 YELLOW triangle
        if self._readout_ready():                     # relax: where the triad's query attends
            return list(self.flow.timbre)
        d = self._dominant(0)
        return [d[i % SIZE] for i in range(TRIADS)]

    def loudness(self):   # per sigma_i : 0 LOUD, 1 LOUD-SOFT, 2 SOFT, 3 SOFT-MIDDLE, 4 MIDDLE
        if self._readout_ready():
            return list(self.flow.loud)
        d = self._dominant(1)
        return [d[i % SIZE] for i in range(TRIADS)]

    def drift_scale(self):
        fl = self.freq_log2()
        if self._readout_ready():                     # relax: expected frequency slot, continuous
            return 2.0 ** np.interp(self.flow.ek, np.arange(SIZE), fl)
        d = self._dominant(2)
        return np.array([2.0 ** fl[d[i % SIZE]] for i in range(TRIADS)])

    def flow_state(self):
        """Tensor Flow v2 readouts for the browser, recordings and DUDLEY."""
        f, m = self.flow, self.wmask().reshape(CELLS)
        Tinf = f.Tinf * m if f.Tinf is not None else np.zeros(CELLS)
        return dict(
            efold=round(f.N, 4), epoch=f.epoch, source=f.source,
            w=np.round(f.w, 4).tolist(),
            X=np.round(f.X, 4).tolist() if f.X is not None else [0.0] * TRIADS,
            crossings=list(self.crossings),
            attn=np.round(f.maps, 3).tolist(), attn_dirs=flowlib.DIR_NAMES,
            qattn=np.round(f.attn, 3).tolist(), sources=list(f.sources),
            Tinf=np.round(Tinf, 4).tolist(),
            aspects={k: round(v, 4) for k, v in f.aspects.items()},
            memory=dict(buffer=len(f.buffer), protos=[int(n) for _, n in f.protos]),
        )

    def snapshot(self):
        s = self.sigma
        st = dict(
            t=round(self.t, 4),
            size=SIZE,
            sigma=s.round(5).tolist(),
            vel=self.vel.round(5).tolist(),
            amp=(s / (s.sum() + 1e-9)).round(5).tolist(),
            triads=self.triads.round(4).tolist(),
            gen=self.gen.tolist(),
            events=self.events,
            timbre=self.timbre(),
            loud=self.loudness(),
            dscale=np.round(self.drift_scale(), 4).tolist(),
            blends=np.round(self.bl, 4).tolist(),
            T=self.effective().round(4).tolist(),
            core=self.core().round(4).tolist(),
            weights=self.weights.tolist(),
            params=self.params,
            flow=self.params.get("flow", "relax"),
        )
        if self.relax():
            st.update(self.flow_state())
        return st


# ----------------------------------------------------------------------------- MIDI
class MicrotonalMidiOut:
    """Two ways to get the triads into a synth.

    mode "mpe"    : every note on its own channel (2..16) with its own pitch bend, so each
                    frequency is exact. Needs an MPE synth (Surge XT, Vital, Pianoteq, Logic
                    instruments in MPE mode) set to the same bend range.
    mode "single" : one channel, notes rounded to the nearest semitone, no per-note bend.
                    Works with ANY synth, including Dexed, which plays a single channel.
                    global_bend=True adds one shared bend that follows the strongest triad's
                    microtonal offset (it bends every note on that channel, so leave it off
                    when another app plays the same synth).
    Every message can also be tapped into a recording (see tap / start_capture).
    """

    def __init__(self, port, mode="mpe", bend_range=48, voices=5, channel=1, global_bend=False, follow_drift=False,
                 brightness_cc=True):
        self.port = port
        self.brightness_cc = brightness_cc   # relax flow: brightness -> CC74 (MPE master channel / the single channel)
        self.last_cc74 = None
        self.name = port.name
        self.mode = mode
        self.bend_range = bend_range
        self.voices = voices
        self.channel = max(1, min(16, channel)) - 1
        self.global_bend = global_bend
        self.capture = None            # list of (seconds, mido.Message) while recording
        self.t0 = 0.0
        self.active = {}               # mpe: (triad, n) -> (channel, note)
        self.free = list(range(1, 16))
        self.sounding = {}             # single: note -> velocity
        self.track = {}                # single: (triad, n) -> held note
        self.lastb = {}                # mpe: channel -> last pitch bend sent
        self.follow_drift = follow_drift
        self.last_bend = None
        if mode == "mpe":
            for ch in range(16):       # pitch-bend range RPN 0,0
                for cc, v in ((101, 0), (100, 0), (6, bend_range), (38, 0), (101, 127), (100, 127)):
                    self._send(mido.Message("control_change", channel=ch, control=cc, value=v))

    # -------------------------------------------------------------- plumbing
    def _send(self, msg):
        self.port.send(msg)
        if self.capture is not None:
            self.capture.append((time.perf_counter() - self.t0, msg))

    def start_capture(self):
        self.t0 = time.perf_counter()
        self.capture = []

    def stop_capture(self, path):
        cap, self.capture = self.capture, None
        if not cap:
            return None
        mf = mido.MidiFile(type=0, ticks_per_beat=960)
        tr = mido.MidiTrack()
        mf.tracks.append(tr)
        tr.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
        last = 0
        for t, msg in cap:
            tick = int(round(mido.second2tick(t, 960, 500000)))
            tr.append(msg.copy(time=max(0, tick - last)))
            last = max(last, tick)
        mf.save(path)
        return path

    @staticmethod
    def _midi_float(f):
        return 69 + 12 * math.log2(f / 440.0)

    def _top(self, amp):
        return np.argsort(amp)[::-1][: self.voices].tolist()

    # -------------------------------------------------------------- update
    def update(self, st, prev_gen):
        if self.brightness_cc and "aspects" in st:
            v = max(0, min(127, int(round(st["aspects"]["brightness"] * 127))))
            if self.last_cc74 is None or abs(v - self.last_cc74) >= 2:
                ch = self.channel if self.mode == "single" else 0
                self._send(mido.Message("control_change", channel=ch, control=74, value=v))
                self.last_cc74 = v
        if self.mode == "single":
            return self._update_single(st, prev_gen)
        amp, triads, gen = st["amp"], st["triads"], st["gen"]
        top = set(self._top(amp))
        for key in list(self.active):
            i, _ = key
            if i not in top or gen[i] != prev_gen.get(i, gen[i]):
                ch, note = self.active.pop(key)
                self._send(mido.Message("note_off", channel=ch, note=note))
                self.free.append(ch)
        for i in top:
            vel = max(1, min(127, int(30 + 300 * amp[i] * len(amp) / 9)))
            for n in range(3):
                m = self._midi_float(triads[i][n])
                key = (i, n)
                if key in self.active:
                    ch, note = self.active[key]
                    if abs(m - note) < self.bend_range - 0.5:
                        b = max(-8192, min(8191, int((m - note) / self.bend_range * 8192)))
                        # only bends that moved ~5 cents or more: keeps Bluetooth / network MIDI light
                        if abs(b - self.lastb.get(ch, 99999)) >= max(2, int(8192 / self.bend_range * 0.05)):
                            self._send(mido.Message("pitchwheel", channel=ch, pitch=b))
                            self.lastb[ch] = b
                        continue
                    self._send(mido.Message("note_off", channel=ch, note=note))
                    self.free.append(ch)
                    del self.active[key]
                if not self.free:
                    continue
                ch = self.free.pop(0)
                note = max(0, min(127, int(round(m))))
                b = max(-8192, min(8191, int((m - note) / self.bend_range * 8192)))
                self._send(mido.Message("pitchwheel", channel=ch, pitch=b))
                self.lastb[ch] = b
                self._send(mido.Message("note_on", channel=ch, note=note, velocity=vel))
                self.active[key] = (ch, note)

    def _update_single(self, st, prev_gen):
        """One channel, nearest semitone. A triad's notes hold until it re-rolls (or, with
        follow_drift, until a note has drifted 1.25 semitones away), so Dexed isn't sprayed."""
        amp, triads, gen = st["amp"], st["triads"], st["gen"]
        want, retrig, keep = {}, set(), set()
        for i in self._top(amp):
            vel = max(1, min(127, int(30 + 300 * amp[i] * len(amp) / 9)))
            rolled = gen[i] != prev_gen.get(i, gen[i])
            for n, f in enumerate(triads[i]):
                m = self._midi_float(f)
                cur = self.track.get((i, n))
                if cur is None or rolled or (self.follow_drift and abs(m - cur) > 1.25):
                    cur = max(0, min(127, int(round(m))))
                    self.track[(i, n)] = cur
                    if rolled:
                        retrig.add(cur)
                keep.add((i, n))
                want[cur] = max(vel, want.get(cur, 0))
        for k in [k for k in self.track if k not in keep]:
            del self.track[k]
        ch = self.channel
        for note in [n for n in self.sounding if n not in want or n in retrig]:
            self._send(mido.Message("note_off", channel=ch, note=note))
            del self.sounding[note]
        for note, vel in want.items():
            if note not in self.sounding:
                self._send(mido.Message("note_on", channel=ch, note=note, velocity=vel))
                self.sounding[note] = vel
        if self.global_bend:
            i = int(np.argmax(amp))
            m = self._midi_float(min(triads[i]))
            b = max(-8192, min(8191, int((m - round(m)) / self.bend_range * 8192)))
            if self.last_bend is None or abs(b - self.last_bend) > 40:
                self._send(mido.Message("pitchwheel", channel=ch, pitch=b))
                self.last_bend = b

    def send_voice(self, sysex):
        """A whole DX7 voice as one SysEx message (Dexed's crash-safe way to change sound).
        Dexed silences its notes when a voice arrives, so the held notes are sent again next frame."""
        self._send(mido.Message("sysex", data=list(sysex)[1:-1]))
        self.sounding.clear()
        self.track.clear()
        for ch, note in self.active.values():
            self._send(mido.Message("note_off", channel=ch, note=note))
        self.active.clear()
        self.free = list(range(1, 16))
        self.lastb.clear()

    def test(self):
        """A short chord so you can hear whether the synth is receiving."""
        chs = [self.channel] if self.mode == "single" else [1, 2, 3]
        for ch, note in zip(chs * 3, (60, 64, 67)):
            self._send(mido.Message("note_on", channel=ch, note=note, velocity=100))
        time.sleep(0.8)
        for ch, note in zip(chs * 3, (60, 64, 67)):
            self._send(mido.Message("note_off", channel=ch, note=note))

    def panic(self):
        for ch in range(16):
            self._send(mido.Message("control_change", channel=ch, control=123, value=0))
            if self.mode == "single" and ch == self.channel:
                self._send(mido.Message("pitchwheel", channel=ch, pitch=0))
        self.active.clear()
        self.sounding.clear()
        self.track.clear()
        self.free = list(range(1, 16))
        self.last_bend = None
        self.last_cc74 = None


# ----------------------------------------------------------------------------- server
app = Flask(__name__, static_folder=HERE, static_url_path="")
CORS(app)
# async_handlers=False keeps recording chunks in the order they were sent
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading",
                    async_handlers=False, max_http_buffer_size=256 * 1024 * 1024)
engine = TensorResonanceEngine()
STATE = dict(running=False, hz=30, midi=None)


@app.route("/")
def index():
    return send_from_directory(HERE, "index.html")


@app.route("/health")
def health():
    return jsonify(ok=True, tensorly=HAVE_TENSORLY, mido=HAVE_MIDO,
                   midi=bool(STATE["midi"]), running=STATE["running"])


def dexed_voice(reason):
    """Send Dexed a random voice from its cartridges over the engine's MIDI port (as SysEx)."""
    m = STATE["midi"]
    if not m:
        return None
    v = dexedlib.POOL.random(channel=(m.channel + 1) if m.mode == "single" else 1, mutate=STATE.get("dexed_mutate", 0.0))
    if not v:
        print("  Dexed voice: no cartridges found (see ./dexed.sh --list)")
        return None
    m.send_voice(v["sysex"])
    info = dict(name=v["name"], cart=v["cart"], index=v["index"], reason=reason)
    print(f"  Dexed voice -> '{v['name']}' ({v['cart']} #{v['index'] + 1}, {reason})")
    socketio.emit("dexed_voice", info)
    return info


def run_loop():
    prev_gen = {}
    last_epoch = None
    last = time.perf_counter()
    while True:
        socketio.sleep(1.0 / STATE["hz"])
        now = time.perf_counter()
        dt = min(0.1, now - last)
        last = now
        if not STATE["running"]:
            continue
        st = engine.step(dt)
        if STATE["midi"]:
            try:
                STATE["midi"].update(st, prev_gen)
            except Exception as e:  # keep the engine alive no matter what the port does
                print("MIDI error:", e)
        prev_gen = {i: g for i, g in enumerate(st["gen"])}
        ep = st.get("epoch")
        if STATE.get("dexed_epoch") and ep is not None and last_epoch is not None and ep != last_epoch:
            try:
                dexed_voice(f"epoch {ep}")
            except Exception as e:
                print("Dexed voice error:", e)
        last_epoch = ep
        socketio.emit("state", st)


@socketio.on("connect")
def on_connect():
    m = STATE["midi"]
    emit("hello", dict(tensorly=HAVE_TENSORLY, mido=HAVE_MIDO, midi=bool(m),
                       midi_name=m.name if m else None, midi_mode=m.mode if m else None,
                       midi_channel=(m.channel + 1) if m else None,
                       running=STATE["running"], library=str(ROOT), flow=engine.params["flow"],
                       dexed_epoch=bool(STATE.get("dexed_epoch"))))
    emit("state", engine.snapshot())


@socketio.on("start_engine")
def on_start(_=None):
    STATE["running"] = True
    emit("status", dict(running=True), broadcast=True)


@socketio.on("stop_engine")
def on_stop(_=None):
    STATE["running"] = False
    if STATE["midi"]:
        STATE["midi"].panic()
    emit("status", dict(running=False), broadcast=True)


@socketio.on("update_tensor")
def on_update(msg):
    """msg may contain: weights (3 x SIZE), params {..}, kick [i,j,k,amount], reset: true, epoch: true"""
    with engine.lock:
        if msg.get("reset"):
            engine.reset()
        if "weights" in msg:
            w = np.array(msg["weights"], dtype=float)
            if w.size == 3 * SIZE:                    # an old 3x3 page is ignored
                engine.weights = np.clip(w.reshape(3, SIZE), 0, 3)
        for k, v in (msg.get("params") or {}).items():
            if k == "flow":
                engine.params["flow"] = "classic" if v == "classic" else "relax"
            elif k in engine.params:
                engine.params[k] = float(v)
        if msg.get("epoch"):
            engine.new_epoch()
        if "kick" in msg:
            i, j, k, amt = msg["kick"]
            if max(int(i), int(j), int(k)) < SIZE:
                engine.T[int(i), int(j), int(k)] += float(amt)
        if msg.get("reroll_all"):
            for i in range(TRIADS):
                engine._reroll(i)
    emit("state", engine.snapshot())


@socketio.on("dexed_voice_now")
def on_dexed_voice_now(msg=None):
    """Browser button / epoch while Python owns the MIDI port: send one Dexed voice as SysEx."""
    if msg and "mutate" in msg:
        STATE["dexed_mutate"] = max(0.0, min(1.0, float(msg["mutate"])))
    if not STATE["midi"]:
        emit("dexed_voice", dict(name="(no MIDI port: start with ./run.sh --midi)", cart="-", index=-1, reason="none"))
        return
    if not dexed_voice((msg or {}).get("reason", "button")):
        emit("dexed_voice", dict(name="(no Dexed cartridges found: ./dexed.sh --list)", cart="-", index=-1, reason="none"))


@socketio.on("dexed_settings")
def on_dexed_settings(msg):
    STATE["dexed_epoch"] = bool(msg.get("epoch"))
    STATE["dexed_mutate"] = max(0.0, min(1.0, float(msg.get("mutate", 0) or 0)))


@socketio.on("request_state")
def on_request(_=None):
    emit("state", engine.snapshot())


# ----------------------------------------------------------------------------- takes (recording)
# Everything lands in ~/Music/TensorResonance (change with --library):
#   takes/take_YYYYmmdd_HHMMSS/   mix.wav tensor.wav cayley.wav external.wav  (24-bit stereo)
#                                 tensor_midi.mid vst_midi.mid python_midi.mid sigma.json take.json
#                                 dudley/dudley_001.wav ...   (hybrids rendered from this take)
#   albums/<name>/track_01/ ...   + "01 title.wav" copies of each mix and tracklist.txt
#   dudley_renders/               renders made only from DUDLEY's test sounds
ROOT = Path(os.environ.get("TRE_LIBRARY", str(Path.home() / "Music" / "TensorResonance")))
DUDLEY_DIR = Path(HERE) / "dudley"
SOUNDS = DUDLEY_DIR / "sounds"
REC = {}
_SAFE = re.compile(r"[^A-Za-z0-9._ -]+")


def safe_name(s):
    return _SAFE.sub("_", str(s)).strip(" .")[:80] or "untitled"


def resolve(rel):
    """Library-relative id -> absolute path, refusing anything outside the library."""
    rel = str(rel).replace("\\", "/").lstrip("/")
    if rel.startswith("sounds/"):
        base, sub = SOUNDS.resolve(), rel[len("sounds/"):]
    else:
        base, sub = ROOT.resolve(), rel
    p = (base / sub).resolve()
    if p != base and base not in p.parents:
        raise ValueError(f"{rel} is outside the library")
    return p


def rel_of(p):
    p = Path(p).resolve()
    try:
        return "sounds/" + p.relative_to(SOUNDS.resolve()).as_posix()
    except ValueError:
        return p.relative_to(ROOT.resolve()).as_posix()


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def wav_seconds(path):
    try:
        with wave.open(str(path)) as w:
            return round(w.getnframes() / float(w.getframerate()), 3)
    except Exception:
        return None


@socketio.on("rec_start")
def on_rec_start(msg=None):
    msg = msg or {}
    if msg.get("album"):
        d = ROOT / "albums" / safe_name(msg["album"]) / f"track_{int(msg.get('track', 1)):02d}"
    else:
        d = ROOT / "takes" / time.strftime("take_%Y%m%d_%H%M%S")
    base, k = d, 2
    while d.exists():
        d = base.with_name(f"{base.name}_{k}")
        k += 1
    d.mkdir(parents=True)
    tid = rel_of(d)
    REC[tid] = dict(dir=d, sr=int(msg.get("sr", 48000)), writers={}, started=time.time(), meta=msg)
    if STATE["midi"]:
        STATE["midi"].start_capture()
    print(f"  REC   -> {d}")
    return dict(take=tid, dir=str(d))


@socketio.on("rec_chunk")
def on_rec_chunk(msg):
    r = REC.get(msg.get("take"))
    if not r:
        return
    stem = safe_name(msg.get("stem", "stem"))
    w = r["writers"].get(stem)
    if w is None:
        w = wave.open(str(r["dir"] / f"{stem}.wav"), "wb")
        w.setnchannels(2)
        w.setsampwidth(3)
        w.setframerate(r["sr"])
        r["writers"][stem] = w
    x = np.frombuffer(msg["data"], dtype="<f4").astype(np.float64)
    q = np.clip(np.round(x * 8388607.0), -8388608, 8388607).astype("<i4")
    w.writeframes(q.view(np.uint8).reshape(-1, 4)[:, :3].tobytes())


@socketio.on("rec_file")
def on_rec_file(msg):
    r = REC.get(msg.get("take"))
    if not r:
        return
    data = msg.get("data", b"")
    path = r["dir"] / safe_name(msg.get("name", "file"))
    with open(path, "wb") as fh:
        fh.write(data.encode() if isinstance(data, str) else data)


@socketio.on("rec_stop")
def on_rec_stop(msg=None):
    r = REC.pop((msg or {}).get("take"), None)
    if not r:
        return dict(error="no such take")
    for w in r["writers"].values():
        w.close()
    if STATE["midi"] and STATE["midi"].capture is not None:
        STATE["midi"].stop_capture(str(r["dir"] / "python_midi.mid"))
    files = sorted(f for f in r["dir"].iterdir() if f.is_file() and f.name != "manifest.json")
    manifest = dict(
        instrument="TENSOR RESONANCE ENGINE", take=rel_of(r["dir"]),
        started=time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(r["started"])),
        seconds=round(time.time() - r["started"], 2), sample_rate=r["sr"], meta=r["meta"],
        files=[dict(name=f.name, sha1=sha1(f), seconds=wav_seconds(f) if f.suffix == ".wav" else None)
               for f in files])
    with open(r["dir"] / "manifest.json", "w") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"  saved -> {r['dir']}  ({len(files)} files)")
    return dict(take=rel_of(r["dir"]), dir=str(r["dir"]), files=[rel_of(f) for f in files])


@socketio.on("album_done")
def on_album_done(msg):
    adir = ROOT / "albums" / safe_name(msg.get("album", "album"))
    lines, out = [f"{msg.get('album')}  ·  Tensor Resonance Engine  ·  {time.strftime('%Y-%m-%d')}", ""], []
    for t in msg.get("tracks", []):
        n, title = int(t.get("n", 0)), safe_name(t.get("title", "untitled"))
        src = resolve(t["take"]) / "mix.wav"
        if src.exists():
            dst = adir / f"{n:02d} {title}.wav"
            shutil.copyfile(src, dst)
            out.append(rel_of(dst))
            secs = wav_seconds(dst) or 0
            lines.append(f"{n:02d}  {title}  {int(secs // 60)}:{int(secs % 60):02d}")
    (adir / "tracklist.txt").write_text("\n".join(lines) + "\n")
    with open(adir / "album.json", "w") as fh:
        json.dump(msg, fh, indent=2)
    print(f"  album -> {adir}")
    return dict(dir=str(adir), files=out)


def library_listing():
    takes = []
    for kind in ("takes", "albums"):
        base = ROOT / kind
        if not base.exists():
            continue
        for d in sorted(base.rglob("*")):
            if d.is_dir() and d.name != "dudley" and any(d.glob("*.wav")):
                takes.append(dict(
                    id=rel_of(d), kind=kind, sigma=(d / "sigma.json").exists(),
                    files=[rel_of(f) for f in sorted(d.iterdir()) if f.is_file()],
                    dudley=[rel_of(f) for f in sorted((d / "dudley").glob("*.wav"))]))
    renders = sorted((ROOT / "dudley_renders").glob("*.wav")) if (ROOT / "dudley_renders").exists() else []
    return dict(root=str(ROOT), takes=takes, sounds=[rel_of(f) for f in sorted(SOUNDS.glob("*.wav"))],
                renders=[rel_of(f) for f in renders])


@app.route("/api/library")
def api_library():
    return jsonify(library_listing())


@app.route("/files/<path:rel>")
def files(rel):
    try:
        p = resolve(rel)
    except ValueError:
        abort(403)
    if not p.is_file():
        abort(404)
    return send_file(str(p), conditional=True)


# ----------------------------------------------------------------------------- DUDLEY
# dudley/ is the DUDLEY package as written (CC0). The engine only calls dudley.render();
# the one new idea is the morph PATH: it can now come from a take's recorded sigma.
_DUDLEY = {}
JOB = dict(thread=None, cancel=threading.Event())
DUDLEY_DEFAULTS = dict(morph="transport", texture=0.0, dynamics="donor", grain_ms=80.0,
                       grain_jitter=0.3, density=60.0, onset_jitter=1.0, scan="stretch",
                       spray_ms=30.0, pitch=0.0, pitch_spread=0.0, width=0.8, lifter_ms=1.2,
                       max_gain_db=36.0, grid=384, duration=None, sr=44100, peak_db=-1.0,
                       fps=30.0, seed=None, bits=24)


def get_dudley():
    if "m" not in _DUDLEY:
        sys.path.insert(0, str(DUDLEY_DIR))
        import dudley  # noqa: E402
        _DUDLEY["m"] = dudley
    return _DUDLEY["m"]


def ensure_sounds():
    if not any(SOUNDS.glob("*.wav")):
        SOUNDS.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, str(DUDLEY_DIR / "make_test_sounds.py")], cwd=str(SOUNDS),
                       capture_output=True)


SIGMA_SOURCES = {"speed": "Σ|dσ/dt| (how fast the tensor is changing)",
                 "dominance:0": "COLOR dominance", "dominance:1": "LOUDNESS dominance",
                 "dominance:2": "FREQUENCY dominance",
                 "aspect:brightness": "brightness (relax flow)", "aspect:fluency": "fluency (relax flow)",
                 "aspect:prosody": "prosody (relax flow)",
                 "disequilibrium": "Σ|X|: distance from the equilibrium composition (relax flow)",
                 **{f"sigma:{i}": f"σ {n}" for i, n in enumerate(SIGMA_NAMES)}}


def sigma_path(take_dir, source, n_donors):
    """Turn a take's recorded sigma into a DUDLEY breakpoint path 't:pos,...' over 0..n_donors."""
    with open(Path(take_dir) / "sigma.json") as fh:
        fr = json.load(fh)["frames"]
    if len(fr) < 2:
        raise ValueError("sigma.json has too few frames")
    t = np.array([f["t"] for f in fr], float)
    S = np.array([f["sigma"] for f in fr], float)
    V = np.array([f["vel"] for f in fr], float)
    if source == "speed":
        y = np.abs(V).sum(1)
    elif source.startswith("dominance:"):
        m, nm = int(source.split(":")[1]), S.shape[1] // 3      # 3 per mode in older takes, 5 now
        s = S[:, nm * m:nm * m + nm]
        y = s[:, 0] / (s.sum(1) + 1e-9)
    elif source.startswith("sigma:"):
        i = int(source.split(":")[1])
        if i >= S.shape[1]:
            raise KeyError(f"this take has only {S.shape[1]} σ")
        y = S[:, i]
    elif source.startswith("aspect:") or source == "disequilibrium":
        key = "aspects" if source.startswith("aspect:") else "X"
        if not all(key in f for f in fr):
            raise KeyError("this take was recorded before Tensor Flow v2 (no aspects in sigma.json)")
        y = (np.array([f["aspects"][source.split(":")[1]] for f in fr], float) if key == "aspects"
             else np.abs(np.array([f["X"] for f in fr], float)).sum(1))
    else:
        raise ValueError(f"unknown sigma source {source}")
    k = max(1, len(y) // 120)
    y = np.convolve(np.pad(y, k, mode="edge"), np.ones(2 * k + 1) / (2 * k + 1), mode="valid")
    y = (y - y.min()) / (np.ptp(y) + 1e-9) * n_donors
    tt = (t - t[0]) / max(1e-9, t[-1] - t[0])
    idx = np.unique(np.linspace(0, len(tt) - 1, min(300, len(tt))).astype(int))
    return ",".join(f"{tt[i]:.4f}:{y[i]:.4f}" for i in idx)


def find_take_with_sigma(paths):
    for p in paths:
        for d in (Path(p).parent, Path(p).parent.parent):
            if (d / "sigma.json").exists():
                return d
    return None


def path_spec(pm, carriers, donors):
    mode, n = pm.get("mode", "sigma"), len(donors)
    if mode == "draw":
        return pm.get("spec") or None, "drawn breakpoints"
    if mode == "walk":
        return f"walk:{float(pm.get('rate', 0.35)):.3f}", "entropy walk"
    if mode == "rms":
        return "rms", "donor 1 loudness"
    src = pm.get("source", "speed")
    take = resolve(pm["take"]) if pm.get("take") else find_take_with_sigma(list(donors) + list(carriers))
    if not take or not (take / "sigma.json").exists():
        return "walk:0.35", "entropy walk (no sigma.json found for the sigma path)"
    try:
        return sigma_path(take, src, n), f"sigma path: {SIGMA_SOURCES.get(src, src)} from {rel_of(take)}"
    except KeyError as e:
        return sigma_path(take, "speed", n), f"sigma path: speed from {rel_of(take)} ({e.args[0]})"


def build_args(carriers, donors, out, spec, settings):
    a = dict(DUDLEY_DEFAULTS)
    for k, v in (settings or {}).items():
        if k not in a or v is None or v == "":
            continue
        if k in ("morph", "dynamics", "scan"):
            a[k] = str(v)
        elif k in ("sr", "grid", "bits", "seed"):
            a[k] = int(v)
        elif k == "duration":
            a[k] = float(v) or None
        else:
            a[k] = float(v)
    a.update(carrier=[str(c) for c in carriers], donor=[str(d) for d in donors], output=str(out), path=spec)
    return SimpleNamespace(**a)


def out_dir_for(carriers):
    c = Path(carriers[0]).resolve()
    if SOUNDS.resolve() in c.parents:
        return ROOT / "dudley_renders"
    return c.parent if c.parent.name == "dudley" else c.parent / "dudley"


def next_out(d):
    d.mkdir(parents=True, exist_ok=True)
    k = 1
    while (d / f"dudley_{k:03d}.wav").exists():
        k += 1
    return d / f"dudley_{k:03d}.wav"


def run_jobs(jobs, sid):
    d = get_dudley()
    results = []
    for j, (cs, ds, pm, st, label) in enumerate(jobs):
        if JOB["cancel"].is_set():
            break
        try:
            spec, desc = path_spec(pm, cs, ds)
            out = next_out(out_dir_for(cs))
            args = build_args(cs, ds, out, spec, st)

            def prog(stage, f, j=j, label=label):
                socketio.emit("dudley_progress", dict(stage=stage, f=(j + f) / len(jobs), job=j + 1,
                                                      jobs=len(jobs), label=label), to=sid)

            m = d.render(args, progress=prog, cancel=JOB["cancel"].is_set)
            mpath = out.with_suffix(".manifest.json")
            try:  # note where the path came from, next to DUDLEY's own provenance
                with open(mpath) as fh:
                    man = json.load(fh)
                man["tensor_resonance"] = dict(path=desc, label=label)
                with open(mpath, "w") as fh:
                    json.dump(man, fh, indent=2)
            except Exception:
                pass
            res = dict(file=rel_of(out), label=label, path=desc, duration=m["duration_s"],
                       grains=m["grains_sounded"], seconds=m["seconds_to_render"])
            results.append(res)
            socketio.emit("dudley_done_one", res, to=sid)
            print(f"  DUDLEY -> {out}  ({desc})")
        except d.Cancelled:
            socketio.emit("dudley_progress", dict(stage="stopped", f=0, job=j + 1, jobs=len(jobs)), to=sid)
            break
        except Exception as e:
            socketio.emit("dudley_error", dict(error=f"{type(e).__name__}: {e}", label=label), to=sid)
    socketio.emit("dudley_finished", dict(results=results), to=sid)


def start_jobs(jobs):
    if JOB["thread"] and JOB["thread"].is_alive():
        return dict(error="a DUDLEY render is already running")
    JOB["cancel"].clear()
    JOB["thread"] = threading.Thread(target=run_jobs, args=(jobs, request.sid), daemon=True)
    JOB["thread"].start()
    return dict(ok=True, jobs=len(jobs))


@socketio.on("dudley_sources")
def on_dudley_sources(_=None):
    try:
        get_dudley()
        ok, err = True, None
    except Exception as e:
        ok, err = False, f"{type(e).__name__}: {e}"
    ensure_sounds()
    return dict(ok=ok, error=err, sigma_sources=SIGMA_SOURCES, **library_listing())


@socketio.on("dudley_render")
def on_dudley_render(msg):
    try:
        cs = [resolve(x) for x in msg.get("carriers", [])]
        ds = [resolve(x) for x in msg.get("donors", [])]
    except ValueError as e:
        return dict(error=str(e))
    if not cs or not ds:
        return dict(error="pick at least one carrier and one donor")
    return start_jobs([(cs, ds, msg.get("path") or {}, msg.get("settings") or {}, msg.get("label", "render"))])


@socketio.on("dudley_batch")
def on_dudley_batch(msg):
    """Every stem of a take wearing every other stem's envelope, plus all through all,
    each on the take's own sigma path."""
    try:
        take = resolve(msg["take"])
    except (KeyError, ValueError) as e:
        return dict(error=str(e))
    stems = [f for f in sorted(take.glob("*.wav")) if f.stem != "mix"]
    if len(stems) < 2:
        stems = sorted(take.glob("*.wav"))
    if len(stems) < 2:
        return dict(error="this take needs at least two audio stems")
    pm = dict(mode="sigma", source=msg.get("source", "speed"), take=msg["take"])
    st = msg.get("settings") or {}
    jobs = [([a], [b], pm, st, f"{a.stem} wearing {b.stem}") for a in stems for b in stems if a != b]
    if len(stems) > 2:
        jobs.append((stems, stems, pm, st, "all stems through all envelopes"))
    return start_jobs(jobs)


@socketio.on("dudley_cancel")
def on_dudley_cancel(_=None):
    JOB["cancel"].set()


# ----------------------------------------------------------------------------- Surge XT
SURGE = dict(lib=None, port=surgelib.OSC_PORT)


@socketio.on("surge_library")
def on_surge_library(msg=None):
    msg = msg or {}
    if SURGE["lib"] is None or msg.get("refresh"):
        SURGE["lib"] = surgelib.scan_library()
    return dict(SURGE["lib"], bank_map=surgelib.load_bank_map(), port=SURGE["port"])


@socketio.on("surge_osc")
def on_surge_osc(msg):
    """{action: load|random|incr|decr|incr_category|decr_category, id: 'cat:patch', port}"""
    if msg.get("port"):
        SURGE["port"] = int(msg["port"])
    action = msg.get("action", "random")
    try:
        if action == "load":
            if SURGE["lib"] is None:
                SURGE["lib"] = surgelib.scan_library()
            ci, pi = (int(x) for x in str(msg["id"]).split(":"))
            p = SURGE["lib"]["categories"][ci]["patches"][pi]
            sent = surgelib.osc_load(p["path"], port=SURGE["port"])
            return dict(ok=True, address="/patch/load", arg=sent, name=p["name"])
        if action not in ("random", "incr", "decr", "incr_category", "decr_category"):
            return dict(error=f"unknown action {action}")
        surgelib.osc_send("/patch/" + action, port=SURGE["port"])
        return dict(ok=True, address="/patch/" + action)
    except Exception as e:
        return dict(error=f"{type(e).__name__}: {e}")


@socketio.on("surge_midi_programs")
def on_surge_midi_programs(msg=None):
    try:
        dests, banks = surgelib.build_midi_programs(with_3rdparty=bool((msg or {}).get("with3rd")), log=lambda *a: None)
        return dict(ok=True, dest=" + ".join(str(d) for d in dests), dests=[str(d) for d in dests], banks=[dict(bank=b["bank"], label=b["label"], count=len(b["patches"]))
                                                  for b in banks])
    except SystemExit as e:
        return dict(error=str(e))
    except Exception as e:
        return dict(error=f"{type(e).__name__}: {e}")


@app.route("/api/dexed/voice")
def api_dexed_voice():
    """A random voice from your Dexed cartridges as a ready-to-send single-voice SysEx
    (?channel=1..16, ?mutate=0..1, ?name=part of a voice name). Never a Program Change."""
    a = request.args
    v = dexedlib.POOL.random(channel=int(a.get("channel", 1)), mutate=float(a.get("mutate", 0) or 0),
                             name=a.get("name") or None)
    if not v:
        return jsonify(ok=False, error="no Dexed cartridges (.syx) found", looked_in=[str(d) for d in dexedlib.POOL.dirs])
    return jsonify(ok=True, **v)


@app.route("/api/dexed/carts")
def api_dexed_carts():
    return jsonify(dexedlib.POOL.summary())


@app.route("/api/surge_map")
def api_surge_map():
    return jsonify(banks=surgelib.load_bank_map())


@app.route("/api/surge/<action>", methods=["GET", "POST"])
def api_surge(action):
    """Same requests the stand-alone helper (./surge.sh) answers, so surge.html works with either."""
    q = request.get_json(silent=True) or {}
    q.update(request.args.to_dict())
    return jsonify(surgelib.api(action, q))


@socketio.on("dudley_album")
def on_dudley_album(msg):
    """After an album: one DUDLEY hybrid per track (all stems through all envelopes, own sigma path)."""
    jobs = []
    for tid in msg.get("takes", []):
        try:
            take = resolve(tid)
        except ValueError:
            continue
        stems = [f for f in sorted(take.glob("*.wav")) if f.stem != "mix"]
        if len(stems) >= 2:
            jobs.append((stems, stems, dict(mode="sigma", source=msg.get("source", "speed"), take=tid),
                         msg.get("settings") or {}, f"{take.name}: all stems through all envelopes"))
    if not jobs:
        return dict(error="no track has two or more stems to cross")
    return start_jobs(jobs)


VIRTUAL_NAME = "Tensor Resonance"


def midi_outputs():
    if not HAVE_MIDO:
        return []
    try:
        return mido.get_output_names()
    except Exception as e:  # backend missing (python-rtmidi not installed)
        print("  could not list MIDI ports:", e)
        return []


def open_midi(want):
    """'auto'    -> a port with "Tensor" in its name (an IAC bus you named Tensor), otherwise a
                   private virtual port 'Tensor Resonance'. Never IAC Bus 1 by itself.
    'virtual' -> always the virtual port (exists only while this program runs).
    anything else -> case-insensitive partial match against existing outputs."""
    if not HAVE_MIDO:
        print("  MIDI off: pip install mido python-rtmidi")
        return None
    names = midi_outputs()
    if want == "auto":
        # Never take IAC Bus 1 automatically: other streams (visuals, SN2) may already live there.
        # Prefer a port with "Tensor" in its name (e.g. an IAC bus you added and named Tensor);
        # otherwise make a private virtual port that nothing else uses.
        own = [n for n in names if "tensor" in n.lower()]
        if own:
            print(f"  using '{own[0]}' (its own lane: nothing else shares it)")
            return mido.open_output(own[0])
        want = "virtual"
        if sys.platform == "darwin":
            print("  tip: for a lane that survives restarts, add an IAC bus named 'Tensor'")
            print("       (Audio MIDI Setup > Window > Show MIDI Studio > IAC Driver > Ports > +),")
            print("       then pick 'IAC Driver Tensor' as the input in Dexed / Surge.")
    if want == "virtual":
        try:
            p = mido.open_output(VIRTUAL_NAME, virtual=True)
            print(f"  created virtual port '{VIRTUAL_NAME}'. It exists only while this runs:")
            print("  a synth that was ALREADY open has to re-select it in its MIDI settings.")
            return p
        except Exception as e:
            print(f"  could not create a virtual MIDI port ({e}).")
            print("  On Windows install loopMIDI, then:  python app.py --midi loopMIDI")
            return None
    hits = [n for n in names if want.lower() in n.lower()]
    if hits:
        return mido.open_output(hits[0])
    print(f"  no MIDI output matching '{want}'. Available outputs:")
    for n in names or ["(none)"]:
        print("     -", n)
    if sys.platform == "darwin":
        print("  Mac: run with plain  --midi  (uses the IAC Driver if it is on, else a virtual port)")
    return None


def port_free(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def check():
    """Diagnostics: paste this output when something doesn't work."""
    import platform
    print("Tensor Resonance Engine - check")
    print(f"  python   {sys.version.split()[0]}  ({platform.machine()}, {sys.platform})  {sys.executable}")
    from importlib import metadata
    for mod, dist in (("numpy", "numpy"), ("scipy", "scipy"), ("flask", "flask"), ("flask_socketio", "flask-socketio"),
                      ("socketio", "python-socketio"), ("engineio", "python-engineio"),
                      ("simple_websocket", "simple-websocket"), ("tensorly", "tensorly"),
                      ("mido", "mido"), ("rtmidi", "python-rtmidi")):
        try:
            __import__(mod)
            try:
                ver = metadata.version(dist)
            except Exception:
                ver = "ok"
            print(f"  {dist:<17}{ver}")
        except Exception as e:
            note = "  (only needed for --midi)" if mod in ("mido", "rtmidi") else ""
            print(f"  {dist:<17}MISSING ({e.__class__.__name__}){note}")
    print("  MIDI outputs:", ", ".join(midi_outputs()) or "(none)")
    if HAVE_MIDO:
        try:
            p = mido.open_output(VIRTUAL_NAME + " test", virtual=True)
            p.close()
            print("  virtual MIDI port: supported")
        except Exception as e:
            print("  virtual MIDI port: NOT supported here -", e)
    print("  ffmpeg:", shutil.which("ffmpeg") or "not found (DUDLEY reads WAV only; fine for takes)")
    try:
        get_dudley()
        print("  DUDLEY: ok  (", DUDLEY_DIR, ")")
    except Exception as e:
        print("  DUDLEY: NOT available -", e, "(needs scipy: pip install scipy)")
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        print("  library:", ROOT, "(writable)")
    except Exception as e:
        print("  library:", ROOT, "NOT writable -", e)
    lib = surgelib.scan_library()
    for u in lib["user_dirs"]:
        print(f"  Surge XT user folder: {u['path']}  ({u['why']})")
    print(f"  Surge XT: factory patches {'at ' + lib['factory'] if lib['factory'] else 'not found'}, "
          f"{lib['count']} patches; MIDI Programs banks: "
          f"{len(surgelib.load_bank_map() or []) or 'not set up (python surge.py --midi-programs)'}")
    s = dexedlib.POOL.summary()
    print(f"  Dexed cartridges: {s['carts']} ({s['voices']} voices)" + (f" in {', '.join(s['dirs'])}" if s["dirs"] else ""))
    for port in (5000, 5055):
        state = "free" if port_free("127.0.0.1", port) else "IN USE"
        hint = "  (on macOS this is usually AirPlay Receiver)" if port == 5000 and state == "IN USE" else ""
        print(f"  port {port}: {state}{hint}")
    e = TensorResonanceEngine(seed=1)
    for _ in range(60):
        e.step(1 / 30)
    print("  engine (relax flow): 60 steps ok, sigma =", np.round(e.sigma, 3).tolist(),
          f"e-folds {e.flow.N:.2f}, epoch {e.flow.epoch}")
    e.params["flow"] = "classic"
    for _ in range(60):
        e.step(1 / 30)
    print("  engine (classic flow): 60 steps ok")
    d = TensorResonanceEngine.tensorly_crosscheck(e.effective())
    print("  tensorly Tucker vs numpy HOSVD: " + ("not installed" if d is None else f"max diff {d:.1e}"))


def main():
    global ROOT
    ap = argparse.ArgumentParser(description="Tensor Resonance Engine")
    ap.add_argument("--port", type=int, default=5055, help="web port (5000 is taken by AirPlay on macOS)")
    ap.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to reach it from other machines")
    ap.add_argument("--midi", nargs="?", const="auto",
                    help="no value: a port named *Tensor* if there is one, else a private virtual port "
                         "(never IAC Bus 1 by itself). Or part of a port name, e.g. --midi 'Bus 2'")
    ap.add_argument("--midi-mode", choices=["mpe", "single", "dexed"], default="mpe",
                    help="mpe = exact microtones for MPE synths; single = one channel, nearest "
                         "semitone (any synth); dexed = single on channel 1, 3 triads")
    ap.add_argument("--channel", type=int, default=1, help="channel for single/dexed mode (1-16)")
    ap.add_argument("--follow-drift", action="store_true",
                    help="single mode: notes also move when they drift (busier); default: only on re-roll")
    ap.add_argument("--global-bend", action="store_true",
                    help="single mode: one shared bend following the strongest triad")
    ap.add_argument("--bend", type=int, default=None,
                    help="pitch-bend range in semitones (default 48 for mpe, 2 for single)")
    ap.add_argument("--voices", type=int, default=None, help="triads sent to MIDI at once (1-5)")
    ap.add_argument("--midi-test", action="store_true", help="play a test chord on the MIDI port and exit")
    ap.add_argument("--no-brightness-cc", action="store_true",
                    help="don't send the relax flow's brightness as CC74")
    ap.add_argument("--dexed-voice", choices=["off", "epoch"], default="off",
                    help="epoch = send Dexed a new voice from your cartridges at every epoch of the v2 flow "
                         "(as SysEx, which is safe; Program Changes can crash Dexed 1.0.x)")
    ap.add_argument("--dexed-mutate", type=float, default=0.0,
                    help="0..1: alter those voices a little (envelope rates, fine tuning, detune, LFO)")
    ap.add_argument("--flow", choices=["relax", "classic"], default="relax",
                    help="relax = Tensor Flow v2 (default); classic = the first coupling flow")
    ap.add_argument("--library", default=None, help="where takes, albums and renders are saved")
    ap.add_argument("--list-midi", action="store_true")
    ap.add_argument("--check", action="store_true", help="print diagnostics and exit")
    ap.add_argument("--autostart", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    if a.library:
        ROOT = Path(a.library).expanduser()
    if a.check:
        check()
        return
    if a.list_midi:
        print("\n".join(midi_outputs()) or "(no MIDI outputs)")
        return
    engine.params["flow"] = a.flow

    mode = "single" if a.midi_mode in ("single", "dexed") else "mpe"
    voices = a.voices or (3 if a.midi_mode == "dexed" else 5)
    bend = a.bend or (48 if mode == "mpe" else 2)
    if a.midi_test:
        p = open_midi(a.midi or "auto")
        if not p:
            raise SystemExit(1)
        out = MicrotonalMidiOut(p, mode, bend, voices, a.channel, a.global_bend, a.follow_drift)
        where = f"channel {a.channel}" if mode == "single" else "channels 2-4"
        print(f"  test chord (C E G) -> '{p.name}' on {where} ...")
        out.test()
        print("  done. Heard nothing? In the synth's MIDI settings, enable this port as an input,")
        print(f"  and make sure it listens on {where} (or Omni).")
        return

    port = a.port
    while not port_free(a.host, port):
        print(f"  port {port} is busy, trying {port + 1}")
        port += 1
        if port > a.port + 20:
            raise SystemExit("no free port found; pass --port")

    print("\n  TENSOR RESONANCE ENGINE")
    if a.midi:
        p = open_midi(a.midi)
        if p:
            # CC74 means nothing to Dexed, so dexed mode leaves it out
            STATE["midi"] = MicrotonalMidiOut(p, mode, bend, min(5, voices), a.channel, a.global_bend, a.follow_drift,
                                              not a.no_brightness_cc and a.midi_mode != "dexed")
            STATE["dexed_epoch"] = a.dexed_voice == "epoch"
            STATE["dexed_mutate"] = max(0.0, min(1.0, a.dexed_mutate))
            desc = (f"MPE: channels 2-16 + pitch bend ±{bend} st" if mode == "mpe" else
                    f"single channel {a.channel}, nearest semitone, {voices} triads"
                    + (f", shared bend ±{bend} st" if a.global_bend else ""))
            print(f"  MIDI  -> '{p.name}'  ({desc})")
            if STATE["dexed_epoch"]:
                s = dexedlib.POOL.summary()
                print(f"  Dexed -> new voice every epoch, as SysEx ({s['voices']} voices in {s['carts']} cartridges)")
        else:
            print("  MIDI  -> off (continuing without it)")
    url = f"http://{'127.0.0.1' if a.host in ('0.0.0.0', '127.0.0.1') else a.host}:{port}"
    print(f"  flow  -> {a.flow}" + ("  (Tensor Flow v2: relaxation + cross-attention + readout)" if a.flow == "relax" else ""))
    print(f"  saves -> {ROOT}")
    print(f"  open  -> {url}   (Chrome or Edge; Safari has no Web MIDI)")
    print("  stop  -> Ctrl+C\n")
    STATE["running"] = a.autostart
    socketio.start_background_task(run_loop)
    if not a.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    import logging
    import flask.cli
    logging.getLogger("werkzeug").setLevel(logging.ERROR)   # hide the scary-looking dev-server banner
    flask.cli.show_server_banner = lambda *args, **kw: None
    try:
        socketio.run(app, host=a.host, port=port, allow_unsafe_werkzeug=True, log_output=False)
    finally:
        if STATE["midi"]:
            STATE["midi"].panic()


if __name__ == "__main__":
    main()
