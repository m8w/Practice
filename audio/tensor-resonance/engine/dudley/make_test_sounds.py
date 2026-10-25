# Synthesizes DUDLEY test sounds from oscillators and noise only; no samples.
# Public domain: CC0 1.0 Universal.
import numpy as np
from scipy.io import wavfile
from scipy.signal import lfilter

sr = 44100
rng = np.random.default_rng(7)


def reson(x, f, bw):
    r = np.exp(-np.pi * bw / sr)
    th = 2 * np.pi * f / sr
    return lfilter([1 - r], [1, -2 * r * np.cos(th), r * r], x)


def save(name, x):
    x = x / np.abs(x).max() * 0.8
    wavfile.write(name, sr, x.astype(np.float32))


dur = 8.0
t = np.arange(int(dur * sr)) / sr

# voice: 110 Hz buzz through formants gliding a -> i -> u
f0 = 110 * (1 + 0.01 * np.sin(2 * np.pi * 5 * t))
buzz = np.sign(np.sin(2 * np.pi * np.cumsum(f0) / sr)) * 0.5
vowels = {"a": (730, 1090, 2440), "i": (270, 2290, 3010), "u": (300, 870, 2240)}
seq = ["a", "i", "u", "a"]
out = np.zeros_like(t)
blk = len(t) // (len(seq) - 1)
for k in range(len(seq) - 1):
    s = slice(k * blk, (k + 1) * blk)
    fa, fb = np.array(vowels[seq[k]]), np.array(vowels[seq[k + 1]])
    seg = buzz[s]
    # piecewise-stationary formant glide in 20 steps
    sub = np.array_split(np.arange(seg.size), 20)
    for j, idx in enumerate(sub):
        m = j / 19
        ff = fa + (fb - fa) * m
        y = sum(reson(seg[idx], f, 80 + 40 * i) for i, f in enumerate(ff))
        out[s][idx] = y
amp = 0.6 + 0.4 * np.sin(2 * np.pi * 0.7 * t) ** 2
save("voice.wav", out * amp)

# bells: inharmonic partials, struck 6 times
bells = np.zeros_like(t)
for k, st in enumerate(np.linspace(0, dur - 1.5, 6)):
    n0 = int(st * sr)
    tt = t[: len(t) - n0]
    base = [220, 330, 262][k % 3]
    for r, a in [(1, 1), (2.76, .6), (5.4, .4), (8.93, .25), (13.3, .15)]:
        bells[n0:] += a * np.sin(2 * np.pi * base * r * tt) * np.exp(-tt * (1.2 + r * 0.3))
save("bells.wav", bells)

# rain: white noise with random droplet clicks and a slow brightness wander
noise = rng.standard_normal(len(t)) * 0.15
clicks = np.zeros_like(t)
idx = rng.integers(0, len(t), 900)
clicks[idx] = rng.uniform(0.3, 1.0, idx.size) * rng.choice([-1, 1], idx.size)
drops = lfilter([1], [1, -0.97], clicks) * 0.3
rain = reson(noise, 2500, 3000) + noise * 0.4 + drops
save("rain.wav", rain)

# controlled pair: pink-ish noise through ONE resonance, 500 Hz vs 3000 Hz
w = rng.standard_normal(len(t))
save("res500.wav", reson(w, 500, 60))
save("res3000.wav", reson(w, 3000, 250))
save("white.wav", w)
print("ok")
