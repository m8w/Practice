"""
noodle_network.py

A generalized "Cell" abstraction for building networks of oscillators / LFOs
that can be wired in serial, parallel, matrix, or recursive topology -- with
feedback (including cycles), true OS-sourced entropy injection, and simple
chaotic oscillator cores.

Core idea: one routing matrix W (n_cells x n_cells) IS the topology.
  - W[dst, src] = how much src's previous-sample output modulates dst's
    instantaneous frequency.
  - Diagonal entries are self-feedback (a cell modulating its own frequency
    from its own last output).
  - A serial chain is just a path graph in W.
  - A parallel bus is a set of cells with no cross-modulation, summed at
    the output stage via bus_weights.
  - A dense random W is the "matrix" topology -- the tangled soup.
  - Slow cells (low base_freq) modulating fast cells gives a recursive /
    hierarchical LFO-of-LFOs feel without any special-casing in the code.

All cells update in lock-step, one sample at a time, using the PREVIOUS
sample's outputs as modulation sources. This is the standard trick for
letting feedback cycles exist in a network without needing to solve an
implicit system each sample -- it's exactly how a real analog patch bay
with audio-rate feedback behaves (there is always at least one sample /
one signal-propagation-time of delay around any loop).

Entropy: instead of numpy's PRNG, the modulation "noise" field is drawn
from os.urandom() -- the OS entropy pool (on most systems ultimately fed by
hardware interrupt jitter / hardware RNG). Each cell reads a different
offset+stride into the SAME entropy stream, the way several independent
Geiger counters would each sample one shared background-radiation field
differently. This keeps the true-entropy ethic (real physical randomness,
not a seeded algorithm) while still being cheap to generate in bulk.
"""

import os
import wave
import numpy as np

SR = 44100


# ---------------------------------------------------------------------------
# Entropy field
# ---------------------------------------------------------------------------

def make_entropy_field(n_samples: int, n_cells: int) -> np.ndarray:
    """Return an (n_samples, n_cells) array in [-1, 1] sourced from os.urandom.

    Each cell gets its own offset + stride into one shared entropy buffer,
    so cells are correlated-but-distinct samplings of the same physical
    randomness, rather than independent PRNG streams.
    """
    raw = os.urandom(n_samples * 4)
    pool = np.frombuffer(raw, dtype=np.uint32).astype(np.float64)
    pool = (pool / np.iinfo(np.uint32).max) * 2.0 - 1.0

    # Deterministic (non-entropic) offsets/strides -- these just decide HOW
    # each cell taps the shared field, they aren't the randomness itself.
    layout = np.random.default_rng(12345)
    offsets = layout.integers(0, len(pool), size=n_cells)
    strides = layout.integers(1, 7, size=n_cells)

    field = np.empty((n_samples, n_cells))
    idx_base = np.arange(n_samples)
    for i in range(n_cells):
        idx = (offsets[i] + strides[i] * idx_base) % len(pool)
        field[:, i] = pool[idx]
    return field


# ---------------------------------------------------------------------------
# Cell
# ---------------------------------------------------------------------------

class Cell:
    """One oscillator/LFO node. wf_type in:
        'sine', 'tri', 'saw', 'pulse', 'chaotic'
    'chaotic' uses a logistic-map core (x_{n+1} = r * x_n * (1 - x_n)) whose
    r is itself pulled toward/away from the chaotic regime by incoming
    modulation + entropy, so it can range from periodic to fully chaotic
    depending on what feeds it.
    """

    def __init__(self, name, wf_type, base_freq, mod_depth=1.0, entropy_amount=0.0):
        self.name = name
        self.wf_type = wf_type
        self.base_freq = float(base_freq)
        self.mod_depth = float(mod_depth)
        self.entropy_amount = float(entropy_amount)


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

class NoodleNetwork:
    def __init__(self, cells, routing_matrix, bus_weights, sr=SR):
        self.cells = cells
        self.n = len(cells)
        self.W = np.asarray(routing_matrix, dtype=np.float64)
        assert self.W.shape == (self.n, self.n)
        self.bus = np.asarray(bus_weights, dtype=np.float64)
        self.sr = sr

        self.base_freq = np.array([c.base_freq for c in cells])
        self.mod_depth = np.array([c.mod_depth for c in cells])
        self.entropy_amount = np.array([c.entropy_amount for c in cells])
        self.types = [c.wf_type for c in cells]

    def render(self, seconds: float, seed: int = 7) -> np.ndarray:
        n_samples = int(seconds * self.sr)
        entropy_field = make_entropy_field(n_samples, self.n)

        phase = np.zeros(self.n)
        last_output = np.zeros(self.n)
        logistic_x = np.random.default_rng(seed).uniform(0.1, 0.9, self.n)
        out = np.zeros(n_samples)

        type_mask = {t: np.array([tt == t for tt in self.types]) for t in set(self.types)}
        nyquist = self.sr * 0.45

        for t in range(n_samples):
            mod_input = self.W @ last_output  # incoming modulation per cell, from t-1

            freq_eff = (
                self.base_freq
                + mod_input * self.mod_depth * self.base_freq
                + entropy_field[t] * self.entropy_amount * self.base_freq
            )
            freq_eff = np.clip(freq_eff, 0.01, nyquist)
            phase = (phase + freq_eff / self.sr) % 1.0

            raw = np.zeros(self.n)

            if 'sine' in type_mask:
                m = type_mask['sine']
                raw[m] = np.sin(2 * np.pi * phase[m])
            if 'tri' in type_mask:
                m = type_mask['tri']
                raw[m] = 2 * np.abs(2 * (phase[m] - np.floor(phase[m] + 0.5))) - 1
            if 'saw' in type_mask:
                m = type_mask['saw']
                raw[m] = 2 * phase[m] - 1
            if 'pulse' in type_mask:
                m = type_mask['pulse']
                raw[m] = np.where(phase[m] < 0.5, 1.0, -1.0)
            if 'chaotic' in type_mask:
                m = type_mask['chaotic']
                r = 3.6 + 0.39 * np.tanh(mod_input[m] + entropy_field[t][m] * 0.5)
                logistic_x[m] = np.clip(r * logistic_x[m] * (1 - logistic_x[m]), 1e-6, 1 - 1e-6)
                raw[m] = logistic_x[m] * 2 - 1

            output = np.tanh(raw)  # soft-limit each cell before it feeds forward
            last_output = output
            out[t] = np.dot(output, self.bus)

        peak = np.max(np.abs(out)) + 1e-9
        return (out / peak) * 0.9


# ---------------------------------------------------------------------------
# Demo patch: "noodle soup" -- big, dense, deliberately tangled
# ---------------------------------------------------------------------------

def build_noodle_soup(rng_seed: int = 42) -> NoodleNetwork:
    rng = np.random.default_rng(rng_seed)
    cells = []

    # 8 slow control-rate cells: the "LFO layer"
    for i in range(8):
        cells.append(Cell(
            f"lfo{i}",
            rng.choice(['sine', 'tri', 'saw']),
            base_freq=rng.uniform(0.05, 4.0),
            mod_depth=1.0,
            entropy_amount=rng.uniform(0.05, 0.4),
        ))

    # 8 chaotic cells: mid-range, feed the tangle
    for i in range(8):
        cells.append(Cell(
            f"chaos{i}",
            'chaotic',
            base_freq=rng.uniform(0.5, 50.0),
            mod_depth=1.0,
            entropy_amount=rng.uniform(0.1, 0.6),
        ))

    # 16 audio-rate carriers: what you actually hear
    for i in range(16):
        cells.append(Cell(
            f"osc{i}",
            rng.choice(['sine', 'saw', 'pulse', 'tri']),
            base_freq=rng.uniform(60.0, 900.0),
            mod_depth=rng.uniform(0.2, 1.2),
            entropy_amount=rng.uniform(0.0, 0.15),
        ))

    n = len(cells)  # 32
    W = np.zeros((n, n))

    # self-feedback on every cell
    for i in range(n):
        W[i, i] = rng.uniform(0.05, 0.5)

    # LFO layer (0-7) modulates chaos + carrier layers (recursive/hierarchical)
    for i in range(8):
        targets = rng.choice(np.arange(8, n), size=rng.integers(3, 7), replace=False)
        for j in targets:
            W[j, i] += rng.uniform(0.05, 0.4)

    # chaos layer (8-15) cross-wires densely into everything -- the "soup"
    for i in range(8, 16):
        targets = rng.choice(np.arange(0, n), size=rng.integers(4, 10), replace=False)
        for j in targets:
            if j != i:
                W[j, i] += rng.uniform(-0.3, 0.3)

    # a handful of deliberate serial chains among the carriers
    carrier_ids = list(range(16, n))
    rng.shuffle(carrier_ids)
    for k in range(0, len(carrier_ids) - 1, 2):
        a, b = carrier_ids[k], carrier_ids[k + 1]
        W[b, a] += rng.uniform(0.2, 0.5)

    # dense-but-light random matrix sprinkled among carriers themselves
    for i in range(16, n):
        targets = rng.choice(np.arange(16, n), size=4, replace=False)
        for j in targets:
            if j != i:
                W[j, i] += rng.uniform(-0.15, 0.15)

    bus_weights = np.zeros(n)
    bus_weights[16:] = rng.uniform(0.5, 1.0, size=16)  # only carriers reach the output

    return NoodleNetwork(cells, W, bus_weights)


def write_wav(path: str, samples: np.ndarray, sr: int = SR):
    samples_i16 = np.int16(np.clip(samples, -1, 1) * 32767)
    with wave.open(path, 'w') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(samples_i16.tobytes())


if __name__ == "__main__":
    net = build_noodle_soup(rng_seed=42)
    audio = net.render(seconds=20.0)
    write_wav("/mnt/user-data/outputs/noodle_soup_demo.wav", audio)
    print("rendered", len(audio) / SR, "seconds")
