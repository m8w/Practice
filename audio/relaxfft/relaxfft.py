#!/usr/bin/env python3
"""
relaxfft.py -- FFT "compositional relaxation" audio effect.

Idea (from Tasinato, "Relaxation Dark Energy"): a composition variable
X = ln(r / r_inf) measures displacement from an equilibrium and relaxes under
the flow X' = -n X, per e-fold of expansion N = ln a.  So X(N) = X0 * exp(-n N),
and the equation of state is the power law w = w0 * a^-n.

Audio mapping, per FFT bin k and per frame:
    L_in   = ln |S_k|                    the "equilibrium" (what the input says now)
    L_hold = held log-magnitude           the "state"
    X      = L_hold - L_in                displacement above equilibrium
    X relaxes:  X <- X * exp(-n_k * dN)   dN = (hop/sr) / tau  (e-folds per frame)
When the input rises above the held state, the state snaps to it (excitation).
Energy therefore decays the way the paper's composition does: constant
fractional relaxation of the log displacement, not a fixed dB/sec ramp.

    n_k = n * (f_k / 1 kHz) ** tilt       tilt > 0: highs relax faster
                                           tilt < 0: lows relax faster
    tau                                    the "Hubble time" in seconds
    n = 1/2                                the paper's square-root benchmark

Held bins keep ringing as sinusoids (instantaneous-frequency phase advance),
so the tail is a spectral sustain/smear rather than noise.

Usage:
    python3 relaxfft.py input.wav -o out.wav --n 0.5 --tau 1.5 --tilt 0.5 --mix 1
    python3 relaxfft.py --demo                     # renders demo_in.wav / demo_out.wav
"""
import argparse
import sys
import numpy as np


def read_audio(path):
    try:
        import soundfile as sf
        x, sr = sf.read(path, always_2d=True, dtype="float64")
        return x, sr
    except ImportError:
        import wave
        with wave.open(path, "rb") as w:
            sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if sw != 2:
            sys.exit("Only 16-bit WAV without soundfile. pip install soundfile")
        x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
        return x.reshape(-1, ch), sr


def write_audio(path, y, sr):
    try:
        import soundfile as sf
        sf.write(path, y, sr, subtype="PCM_24")
    except ImportError:
        import wave
        data = (np.clip(y, -1, 1) * 32767).astype("<i2")
        with wave.open(path, "wb") as w:
            w.setnchannels(y.shape[1]); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes(data.tobytes())


def relax_channel(x, sr, n_fft=2048, hop=512, n=0.5, tau=1.5, tilt=0.5,
                  mix=1.0, floor_db=-100.0, tail=2.0):
    win = np.hanning(n_fft + 1)[:-1]
    pad = n_fft
    tail_n = int(tail * sr)
    xp = np.concatenate([np.zeros(pad), x, np.zeros(pad + tail_n + n_fft)])
    nfr = 1 + (len(xp) - n_fft) // hop

    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    n_k = n * (np.maximum(freqs, 20.0) / 1000.0) ** tilt
    dN = (hop / sr) / tau
    decay = np.exp(-n_k * dN)
    omega = 2 * np.pi * freqs * hop / sr          # expected phase advance per hop

    eps = (np.max(np.abs(x)) + 1e-12) * 10 ** (floor_db / 20)
    out = np.zeros(len(xp))
    wsum = np.zeros(len(xp))

    L_hold = prev_ph = ph_out = adv = None
    for i in range(nfr):
        s = i * hop
        S = np.fft.rfft(xp[s:s + n_fft] * win)
        mag, ph = np.abs(S), np.angle(S)
        L = np.log(mag + eps)

        if L_hold is None:
            L_hold, ph_out, adv = L.copy(), ph.copy(), omega.copy()
        else:
            excess = L_hold - L
            held = excess > 0
            L_hold = np.where(held, L + excess * decay, L)   # X relaxes: X' = -nX
            dev = (ph - prev_ph - omega + np.pi) % (2 * np.pi) - np.pi
            fresh_adv = omega + dev
            adv = np.where(held, adv, fresh_adv)             # freeze freq while held
            ph_out = np.where(held, ph_out + adv, ph)
        prev_ph = ph

        mag_out = np.maximum(np.exp(L_hold) - eps, 0.0)
        frame = np.fft.irfft(mag_out * np.exp(1j * ph_out), n_fft) * win
        out[s:s + n_fft] += frame
        wsum[s:s + n_fft] += win ** 2

    wet = out / np.maximum(wsum, 1e-3)
    dry = np.concatenate([x, np.zeros(tail_n)])
    wet = wet[pad:pad + len(dry)]
    return (1 - mix) * dry + mix * wet


def process(x, sr, **kw):
    tail = kw.get("tail", 2.0)
    chans = [relax_channel(x[:, c], sr, **kw) for c in range(x.shape[1])]
    y = np.stack(chans, axis=1)
    peak = np.max(np.abs(y))
    if peak > 0.99:
        y *= 0.99 / peak
    return y


def demo_signal(sr=44100, dur=7.0):
    t = np.arange(int(sr * dur)) / sr
    x = np.zeros_like(t)
    rng = np.random.default_rng(1)
    notes = [220, 277.18, 329.63, 440, 554.37, 659.25, 880, 1108.7]
    for i, f in enumerate(notes * 2):
        t0 = 0.35 * i
        m = t >= t0
        env = np.exp(-(t - t0) * 9.0) * m
        x += env * (np.sin(2 * np.pi * f * t) + 0.4 * np.sin(2 * np.pi * 2 * f * t)
                    + 0.2 * np.sin(2 * np.pi * 3 * f * t))
    for t0 in (0.0, 1.4, 2.8, 4.2):                    # noise bursts
        m = (t >= t0) & (t < t0 + 0.05)
        x += 0.5 * rng.standard_normal(len(t)) * m
    return (x / np.max(np.abs(x)) * 0.8)[:, None], sr


def main():
    p = argparse.ArgumentParser(description="FFT compositional-relaxation effect")
    p.add_argument("input", nargs="?")
    p.add_argument("-o", "--output", default="relaxed.wav")
    p.add_argument("--n", type=float, default=0.5, help="relaxation rate per e-fold (paper benchmark 0.5)")
    p.add_argument("--tau", type=float, default=1.5, help="seconds per e-fold (Hubble time)")
    p.add_argument("--tilt", type=float, default=0.5, help="frequency exponent of n_k")
    p.add_argument("--mix", type=float, default=1.0)
    p.add_argument("--fft", type=int, default=2048)
    p.add_argument("--hop", type=int, default=512)
    p.add_argument("--tail", type=float, default=2.0, help="extra seconds appended for decay")
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()

    if a.demo:
        x, sr = demo_signal()
        write_audio("demo_in.wav", x, sr)
        a.output = "demo_out.wav"
    elif a.input:
        x, sr = read_audio(a.input)
    else:
        p.error("give an input file or --demo")

    y = process(x, sr, n_fft=a.fft, hop=a.hop, n=a.n, tau=a.tau,
                tilt=a.tilt, mix=a.mix, tail=a.tail)
    write_audio(a.output, y, sr)
    print(f"wrote {a.output}  ({len(y)/sr:.2f}s, {sr} Hz)")


if __name__ == "__main__":
    main()
