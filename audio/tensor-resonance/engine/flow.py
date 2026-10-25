"""
TENSOR FLOW v2  ("relax" flow) — three papers turned into one tensor dynamics.
(The browser engine in index.html carries a line-for-line port of this file.)

The tensor is SIZE×SIZE×SIZE (5×5×5): COLOR × LOUDNESS × FREQUENCY, cell = SIZE²·i + SIZE·j + k.
HOSVD gives SIZE singular values per mode, 3·SIZE = 15 σ, and each σ owns one triad.

1. RELAXATION FLOW  (Tasinato, "Relaxation Dark Energy", arXiv:2610.13739)
   The tensor relaxes toward an equilibrium composition T∞ at a constant fractional rate n per
   e-fold of an internal clock N (dN/dt = H, the EXPANSION rate). As in the paper's
   near-equilibrium response, long-wavelength disturbances relax at the background rate while
   short-wavelength ones travel as damped sound waves. Here "wavelength" means modes of the
   graph Laplacian on the 5×5×5 lattice:
        D = T − T∞*,   D̈ = −2nH Ḋ − H²(n² D + c_s² L D)
   (λ = 0: critically damped relaxation at n per e-fold; λ > 0: rings at H·c_s·√λ while decaying).
   Each σ gets a composition variable X = ln(p / p∞) (its share of its mode's energy against the
   equilibrium share) and an equation of state w = −(dX/dN)/3, exactly the paper's X′ = −3w.
   An upward phantom crossing (w rising through −1, the density maximum) re-rolls that σ's triad.
   New equilibria arrive at random moments ("epochs").

2. BIDIRECTIONAL CROSS-ATTENTION  (Mohanty et al., "X-AVDD", arXiv:2610.13687)
   COLOR, LOUDNESS and FREQUENCY attend to each other in both directions (Q = one mode's
   slices, K/V = another's), Add & Norm keeping each slice's size. The equilibrium the tensor
   relaxes toward is the cross-attended version of the target: T∞* = T∞ + κ·XA(T).
   Distillation: recent states are stored as cross-attended embeddings; at each epoch they are
   distilled into K prototypes (initialised from real samples, k-means in the attended space,
   medoid tensors), and an epoch may return to one of them: memory without literal repeats.

3. MULTI-GRANULAR, ALIGNMENT-FREE READOUT  (Chao et al., "OpenPA", arXiv:2610.13826)
   Word level: each triad has a query token (like OpenPA's <score> token) that attends over all
   125 cells, so a triad's timbre (COLOR), envelope (LOUDNESS) and drift scale (FREQUENCY) come
   from where its attention lands, not from a fixed index. Mixed-transcript adaptation: per epoch
   each query is built from its own σ (p = 0.5), a substituted σ (0.3) or a free query (0.2).
   Utterance level: a self-attention context layer over the 15 triads, then aspect-specific
   attention pooling gives three global scores: brightness, fluency, prosody (each read against
   its own recent range). brightness opens the high end of the sound, fluency scales the drift,
   prosody scales how often triads re-roll. A triad's slot only changes when another slot's
   attention beats it by a margin (hysteresis), so timbre and envelope don't flicker.

No training happens: attention weights are random projections redrawn each epoch. The papers'
mathematical structures are borrowed; their scientific claims are not.
"""

import math

import numpy as np

SIZE = 5                      # slots per mode
TOK = SIZE * SIZE             # cells in one slice (a token)
CELLS = SIZE ** 3             # cells in the tensor
TRIADS = 3 * SIZE             # σ = triads

COORD = np.array([[i, j, k] for i in range(SIZE) for j in range(SIZE) for k in range(SIZE)])
DIRS = [(0, 1), (1, 0), (1, 2), (2, 1), (2, 0), (0, 2)]                             # query mode, key mode
DIR_NAMES = ["C→L", "L→C", "L→F", "F→L", "F→C", "C→F"]
TRANSCRIPT_P = (0.5, 0.3, 0.2)                                                      # own, substituted, free
ASPECTS = {"brightness": np.array([1.0, 0.0, 0.5, 0.0]),     # over token features
           "fluency": np.array([0.0, 1.0, 0.0, -0.3]),       # [σ/σmax, speed, share, disequilibrium X]
           "prosody": np.array([0.0, -0.3, 0.0, 1.0])}
EYE = np.eye(SIZE)


def cell(i, j, k):
    return SIZE * SIZE * i + SIZE * j + k


def lattice_laplacian():
    L = np.zeros((CELLS, CELLS))
    for c, (i, j, k) in enumerate(COORD):
        for ax in range(3):
            for d in (-1, 1):
                q = [i, j, k]
                q[ax] += d
                if 0 <= q[ax] < SIZE:
                    L[c, c] += 1
                    L[c, cell(*q)] -= 1
    return L


LAP = lattice_laplacian()


def unfold(T, mode):
    """(CELLS,) -> (SIZE, TOK): mode-`mode` slices as tokens (same ordering as the browser engine)."""
    return np.moveaxis(np.asarray(T).reshape(SIZE, SIZE, SIZE), mode, 0).reshape(SIZE, TOK)


def fold(tokens, mode):
    return np.moveaxis(np.asarray(tokens).reshape(SIZE, SIZE, SIZE), 0, mode).reshape(CELLS)


def shares(s):
    """Each σ's share of its mode's energy: p = σ² / Σ_mode σ² (TRIADS,)."""
    s = np.asarray(s)
    return np.concatenate([(s[SIZE * m:SIZE * m + SIZE] ** 2) / ((s[SIZE * m:SIZE * m + SIZE] ** 2).sum() + 1e-12)
                           for m in range(3)])


def softmax(x, axis=-1):
    x = x - x.max(axis=axis, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=axis, keepdims=True)


def cross_attention(T, W):
    """Bidirectional cross-attention between modes. Returns (force (CELLS,), attention maps
    (6,SIZE,SIZE), embedding (6·TOK,)): force = mean over directions of fold(AddNorm(A + S·V) − A)."""
    F = np.zeros(CELLS)
    maps, emb = [], []
    for d, (a, b) in enumerate(DIRS):
        A, B = unfold(T, a), unfold(T, b)
        S = softmax((A @ W["q"][d]) @ (B @ W["k"][d]).T / 2.0, axis=1)
        R = A + S @ (B @ W["v"][d])
        na = np.linalg.norm(A, axis=1)
        nr = np.linalg.norm(R, axis=1) + 1e-9
        R = R * (na / nr)[:, None]                     # Add & Norm: keep each slice's size
        F += fold(R - A, a)
        maps.append(S)
        emb.append(R.mean(axis=0))                     # global average pool of refined tokens
    return F / len(DIRS), np.array(maps), np.concatenate(emb)


def readout(T, V, sigma, vel, H, Wr, sources):
    """OpenPA-style word level: the triad queries attend over all cells (no fixed alignment).
    Returns attention mass per COLOR slot (TRIADS,SIZE), per LOUDNESS slot (TRIADS,SIZE), the
    expected FREQUENCY slot ek (TRIADS,) in 0..SIZE-1, and the attention (TRIADS,CELLS)."""
    T = np.asarray(T)
    smax = max(float(np.max(sigma)), 1e-6)
    vmax = max(float(np.max(np.abs(vel))), 1e-6)
    cells = np.concatenate([EYE[COORD[:, 0]], EYE[COORD[:, 1]], EYE[COORD[:, 2]],
                            (T / smax)[:, None], (np.asarray(V) / (H * smax + 1e-9))[:, None]], axis=1)
    K = cells @ Wr["k"]                                # (CELLS,4)
    feats = []
    for i in range(TRIADS):
        s = sources[i]
        if s < 0:                                      # free query
            feats.append(Wr["free"][i])
        else:
            m, r = divmod(s, SIZE)
            feats.append(np.concatenate([np.eye(3)[m], EYE[r], [sigma[s] / smax, vel[s] / vmax]]))
    Q = np.array(feats) @ Wr["q"]                      # (TRIADS,4)
    sal = 2.0 * np.abs(T) / (np.max(np.abs(T)) + 1e-9)
    A = softmax(Q @ K.T / 2.0 + sal[None, :], axis=1)  # (TRIADS,CELLS)
    mass = lambda ax: np.stack([A[:, COORD[:, ax] == v].sum(1) for v in range(SIZE)], axis=1)
    return mass(0), mass(1), mass(2) @ np.arange(SIZE, dtype=float), A


def hysteresis(cur, mass, margin=0.06):
    """Keep each triad's slot unless another slot's attention mass beats it by `margin`."""
    out = []
    for i in range(len(mass)):
        b = int(np.argmax(mass[i]))
        c = cur[i] if cur is not None else b
        out.append(b if mass[i][b] > mass[i][c] + margin else c)
    return out


def aspects_raw(sigma, speed, amp, X):
    """OpenPA-style utterance level: context layer over the triads + aspect-specific
    attention pooling. speed = |dσ/dt| smoothed over ~1 s, X = ln(p/p∞) composition.
    Returns the raw (un-normalised) score per aspect."""
    smax = max(float(np.max(sigma)), 1e-6)
    vmax = max(float(np.max(speed)), 1e-6)
    r = np.stack([np.asarray(sigma) / smax, np.asarray(speed) / vmax, TRIADS * np.asarray(amp) - 1,
                  np.clip(np.asarray(X), -2, 2) / 2], axis=1)          # (TRIADS,4)
    C = r + softmax(r @ r.T / 2.0, axis=1) @ r
    out = {}
    for name, a in ASPECTS.items():
        al = softmax(C @ a)
        out[name] = float((al @ C) @ a)
    return out


def distill(buffer, k, rng, iters=6):
    """X-AVDD-style: init from real samples, k-means in cross-attended embedding space,
    prototypes = medoid tensors. Returns list of (T, size)."""
    if len(buffer) < 2 * k:
        return []
    E = np.array([e for e, _ in buffer])
    cent = E[rng.choice(len(E), k, replace=False)]
    for _ in range(iters):
        lab = ((E[:, None, :] - cent[None]) ** 2).sum(-1).argmin(1)
        for c in range(k):
            if np.any(lab == c):
                cent[c] = E[lab == c].mean(0)
    lab = ((E[:, None, :] - cent[None]) ** 2).sum(-1).argmin(1)
    protos = []
    for c in range(k):
        idx = np.where(lab == c)[0]
        if len(idx):
            best = idx[((E[idx] - cent[c]) ** 2).sum(1).argmin()]
            protos.append((buffer[best][1].copy(), int(len(idx))))
    return protos


class RelaxFlow:
    """State + one step of the relax flow. The engine owns T; this owns V, T∞, clock, memory."""

    def __init__(self, rng):
        self.rng = rng
        self.V = np.zeros(CELLS)
        self.Tinf = None
        self.N = 0.0                                   # e-folds of the internal clock
        self.epoch = 0
        self.source = "start"
        self.buffer = []
        self.buf_t = 0.0
        self.protos = []
        self.maps = np.full((6, SIZE, SIZE), 1 / SIZE)
        self.emb = np.zeros(6 * TOK)
        self.xd = np.zeros(TRIADS)
        self.X = None
        self.w = np.zeros(TRIADS)
        self.cool = np.zeros(TRIADS)
        self.p_inf = np.full(TRIADS, 1 / SIZE)
        self.aspects = {k: 0.5 for k in ASPECTS}
        self.asp_mu = {k: 0.0 for k in ASPECTS}
        self.asp_var = {k: 0.0 for k in ASPECTS}
        self.asp_n = 0
        self.speed = np.zeros(TRIADS)
        self.timbre = self.loud = None
        self.ek = np.full(TRIADS, (SIZE - 1) / 2)
        self.attn = np.full((TRIADS, CELLS), 1 / CELLS)
        self.new_projections()

    def new_projections(self):
        g = self.rng.normal
        self.W = dict(q=[g(0, 1 / 3, (TOK, 4)) for _ in DIRS], k=[g(0, 1 / 3, (TOK, 4)) for _ in DIRS],
                      v=[g(0, 1 / SIZE, (TOK, TOK)) for _ in DIRS])
        self.Wr = dict(q=g(0, 0.6, (3 + SIZE + 2, 4)), k=g(0, 0.6, (3 * SIZE + 2, 4)), free=g(0, 1, (TRIADS, 3 + SIZE + 2)))
        u = self.rng.random(TRIADS)
        self.sources = []
        for i in range(TRIADS):       # mixed-transcript adaptation
            if u[i] < TRANSCRIPT_P[0]:
                self.sources.append(i)
            elif u[i] < TRANSCRIPT_P[0] + TRANSCRIPT_P[1]:
                self.sources.append(int(self.rng.choice([j for j in range(TRIADS) if j != i])))
            else:
                self.sources.append(-1)

    def fresh_attractor(self, energy):
        a = 0.3 * math.sqrt(27 / CELLS)                 # same overall spread as the 3×3×3 version
        T = self.rng.uniform(-a, a, CELLS)
        for c in self.rng.choice(CELLS, SIZE, replace=False):
            T[c] = self.rng.choice([-1, 1]) * self.rng.uniform(0.9, 1.3)
        return T * energy / (np.linalg.norm(T) + 1e-9)

    def new_epoch(self, T, energy, memory, sigma_of):
        self.protos = distill(self.buffer, 4, self.rng)
        if self.protos and self.rng.random() < memory:
            sizes = np.array([s for _, s in self.protos], float)
            c = int(self.rng.choice(len(self.protos), p=sizes / sizes.sum()))
            P = self.protos[c][0]
            self.Tinf = P * energy / (np.linalg.norm(P) + 1e-9) + self.rng.normal(0, 0.05 * math.sqrt(27 / CELLS), CELLS)
            self.source = f"memory {c + 1}/{len(self.protos)}"
        else:
            self.Tinf = self.fresh_attractor(energy)
            self.source = "fresh"
        self.epoch += 1
        self.new_projections()
        self.p_inf = shares(sigma_of(self.Tinf))

    def step(self, T, dt, p, sigma_of):
        """Advance T in place. p: params dict. sigma_of(T) -> TRIADS singular values."""
        H, n, cs, kap = p["expansion"], p["relax_n"], p["sound_cs"], p["xattn"]
        if self.Tinf is None:
            self.new_epoch(T, p["energy"], 0.0, sigma_of)
        sub = 3
        h = dt / sub
        for _ in range(sub):
            F, self.maps, self.emb = cross_attention(T, self.W)
            D = T - (self.Tinf + kap * F)
            acc = -2 * n * H * self.V - H * H * (n * n * D + cs * cs * (LAP @ D))
            self.V += acc * h
            T += self.V * h
        self.N += H * dt
        r = self.rng
        sc = math.sqrt(27 / CELLS)                                     # per-cell scale vs 3×3×3
        self.V += r.normal(0, 2 * p["noise"] * H * math.sqrt(dt) * sc, CELLS)  # WANDER
        nrm = float(np.linalg.norm(T)) + 1e-9                         # soft ceiling at 1.6 × ENERGY
        if nrm > 1.6 * p["energy"]:
            T *= (1.6 * p["energy"] / nrm) ** min(1.0, 2.5 * dt)
        if r.random() < 1 - math.exp(-p["kick_rate"] * dt):           # small-scale fluctuations
            if r.random() < 0.5:
                self.V[r.integers(CELLS)] += r.normal() * p["kick_amp"] * H * 4
            else:
                self.V += r.normal(0, p["kick_amp"] * H * 1.5 * sc, CELLS)
        self.buf_t += dt
        if self.buf_t >= 0.5:
            self.buf_t = 0.0
            self.buffer.append((self.emb.copy(), T.copy()))
            del self.buffer[:-240]
        if r.random() < 1 - math.exp(-p["epoch_rate"] * dt):
            self.new_epoch(T, p["energy"], p["memory"], sigma_of)
        return T

    def composition(self, sigma, dt, H):
        """X = ln(p/p∞) per σ, w = −(dX/dN)/3; returns indices with an upward phantom crossing."""
        X = np.log((shares(sigma) + 1e-6) / (self.p_inf + 1e-6))
        if self.X is None:
            self.X = X
        self.xd = 0.8 * self.xd + 0.2 * (X - self.X) / max(dt, 1e-4)
        self.X = X
        w_prev = self.w
        self.w = -self.xd / (3 * max(H, 1e-6))
        self.cool = np.maximum(0, self.cool - dt)
        cross = [i for i in range(TRIADS) if w_prev[i] < -1 <= self.w[i] and self.cool[i] <= 0]
        for i in cross:
            self.cool[i] = 1.5
        return cross

    def read(self, T, V, sigma, vel, H):
        """Word-level readout with hysteresis -> timbre slot, loudness slot (TRIADS each) and the
        expected frequency slot ek (0..SIZE-1, continuous; the engine turns it into a drift scale).
        T, V: the (slice-weighted) tensor and its velocity, flattened."""
        mc, ml, ek, self.attn = readout(T, V, sigma, vel, H, self.Wr, self.sources)
        self.timbre = hysteresis(self.timbre, mc)
        self.loud = hysteresis(self.loud, ml)
        self.ek = ek
        return self.timbre, self.loud, ek

    def score(self, sigma, vel, amp, dt):
        """Utterance-level aspects in 0..1, each normalised against its own recent range
        (running mean/variance over ~20 s) and smoothed over ~1.5 s, so the meters keep moving."""
        self.speed += (1 - math.exp(-dt / 1.0)) * (np.abs(vel) - self.speed)
        raw = aspects_raw(sigma, self.speed, amp, self.X if self.X is not None else np.zeros(TRIADS))
        self.asp_n += 1
        k = max(1.0 / self.asp_n, 1 - math.exp(-dt / 20.0))
        ks = 1 - math.exp(-dt / 1.5)
        for a, x in raw.items():
            self.asp_mu[a] += k * (x - self.asp_mu[a])
            self.asp_var[a] += k * ((x - self.asp_mu[a]) ** 2 - self.asp_var[a])
            z = (x - self.asp_mu[a]) / math.sqrt(self.asp_var[a] + 1e-4)
            target = 1 / (1 + math.exp(-1.6 * max(-20.0, min(20.0, z))))
            self.aspects[a] += ks * (target - self.aspects[a])
        return self.aspects
