#!/usr/bin/env python3
"""
DUDLEY -- window interface
xik6 / m8w

Public domain: CC0 1.0 Universal. No rights reserved.

Everything the command line does, in a window you click:
  - add carrier and donor sounds with file pickers (double-click one to hear it)
  - draw the morph path with the mouse
  - sliders for every setting, presets you can save and load
  - Render / Stop, progress bar, Play the result, spectrogram of each take
Each render gets its own numbered file, so no take is overwritten.

Run:   python3 dudley_gui.py        (dudley.py must sit in the same folder)
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dudley  # noqa: E402

# ----------------------------------------------------------------------------
# look
# ----------------------------------------------------------------------------
BG = "#15161a"
PANEL = "#1d1f25"
FIELD = "#262932"
LINE = "#343845"
TEXT = "#e4e4e8"
DIM = "#8b8f9c"
ACCENT = "#e8a33d"      # amber: the path, the render button
ACCENT2 = "#5fb3b3"     # teal: donors
MONO = ("Menlo", 11) if sys.platform == "darwin" else ("DejaVu Sans Mono", 10)
UI = ("Helvetica", 12) if sys.platform == "darwin" else ("DejaVu Sans", 10)
UI_B = (UI[0], UI[1], "bold")
TITLE = (UI[0], UI[1] + 6, "bold")

AUDIO_TYPES = [("Audio", "*.wav *.aif *.aiff *.flac *.mp3 *.m4a *.ogg *.caf *.w64 *.rf64"),
               ("All files", "*.*")]

# (key, label, min, max, default, resolution, help)
SLIDERS = [
    ("texture", "Texture", 0.0, 1.0, 0.0, 0.01,
     "0 = envelope only.  1 = also take the donor's harmonics (cross-synthesis)"),
    ("grain_ms", "Grain size  ms", 5, 400, 80, 1, "length of each grain"),
    ("grain_jitter", "Grain size spread", 0.0, 1.5, 0.3, 0.01, "variation in grain length"),
    ("density", "Density  grains/s", 2, 300, 60, 1, "how many grains per second"),
    ("onset_jitter", "Onset jitter", 0.0, 4.0, 1.0, 0.05,
     "0 metronomic · 1 even but unpatterned · 3+ clumps and gaps"),
    ("spray_ms", "Spray  ms", 0, 1000, 30, 1, "randomness of where each grain is read from"),
    ("pitch", "Pitch  semitones", -36, 36, 0, 0.1, "transpose all grains"),
    ("pitch_spread", "Pitch spread", 0.0, 12.0, 0.0, 0.05, "random detune per grain, semitones"),
    ("width", "Stereo width", 0.0, 1.0, 0.8, 0.01, "grain pan spread"),
    ("lifter_ms", "Envelope detail  ms", 0.3, 6.0, 1.2, 0.05,
     "0.6 broad tilt · 1.2 formants · 3+ lets pitch through"),
    ("max_gain_db", "Max boost  dB", 6, 72, 36, 1, "limit on how far any frequency is pushed"),
    ("duration", "Length  s  (0 = donor)", 0, 600, 0, 0.5, "0 uses the longest donor's length"),
    ("peak_db", "Output peak  dBFS", -24, 0, -1, 0.5, "normalise level"),
]


class Slider:
    def __init__(self, parent, row, key, label, lo, hi, default, res, tip, on_hover):
        self.key, self.res, self.default = key, res, default
        self.var = tk.DoubleVar(value=default)
        lab = tk.Label(parent, text=label, bg=PANEL, fg=TEXT, font=UI, anchor="w")
        lab.grid(row=row, column=0, sticky="w", padx=(12, 6), pady=2)
        s = ttk.Scale(parent, from_=lo, to=hi, variable=self.var, orient="horizontal",
                      command=lambda _=None: self._show())
        s.grid(row=row, column=1, sticky="ew", pady=2)
        self.entry = tk.Entry(parent, width=7, bg=FIELD, fg=TEXT, insertbackground=TEXT,
                              relief="flat", font=MONO, justify="right")
        self.entry.grid(row=row, column=2, padx=(6, 12), pady=2)
        self.entry.bind("<Return>", self._typed)
        self.entry.bind("<FocusOut>", self._typed)
        for w in (lab, s, self.entry):
            w.bind("<Enter>", lambda _e: on_hover(tip))
        lab.bind("<Double-Button-1>", lambda _e: self.set(default))  # double-click label = reset
        self.lo, self.hi = lo, hi
        self._show()

    def _fmt(self, v):
        return f"{v:.0f}" if self.res >= 1 else (f"{v:.1f}" if self.res >= 0.1 else f"{v:.2f}")

    def _show(self):
        v = round(self.var.get() / self.res) * self.res
        self.entry.delete(0, "end")
        self.entry.insert(0, self._fmt(v))

    def _typed(self, _e=None):
        try:
            self.set(float(self.entry.get()))
        except ValueError:
            self._show()

    def set(self, v):
        self.var.set(min(max(float(v), self.lo), self.hi))
        self._show()

    def get(self):
        return round(self.var.get() / self.res) * self.res


# ----------------------------------------------------------------------------
# morph path editor
# ----------------------------------------------------------------------------
class PathEditor(tk.Canvas):
    """x = time through the render, y = 0 (carrier's own envelope) .. N donors.
    Click to add a point, drag to move it, right-click or double-click to delete."""

    PAD_L, PAD_R, PAD_T, PAD_B = 96, 14, 12, 22

    def __init__(self, parent, on_change):
        super().__init__(parent, bg=FIELD, highlightthickness=0, height=210)
        self.on_change = on_change
        self.names = []
        self.points = [(0.0, 0.0), (1.0, 1.0)]
        self.drag = None
        self.mode = "draw"
        self.preview = None  # array for walk / rms previews
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", lambda _e: setattr(self, "drag", None))
        self.bind("<Double-Button-1>", self._delete)
        self.bind("<Button-3>", self._delete)
        self.bind("<Button-2>", self._delete)          # mac two-finger / ctrl-click
        self.bind("<Control-Button-1>", self._delete)

    # geometry
    @property
    def n(self):
        return max(1, len(self.names))

    def _xy(self, t, p):
        w, h = self.winfo_width(), self.winfo_height()
        x = self.PAD_L + t * (w - self.PAD_L - self.PAD_R)
        y = self.PAD_T + (1 - p / self.n) * (h - self.PAD_T - self.PAD_B)
        return x, y

    def _tp(self, x, y):
        w, h = self.winfo_width(), self.winfo_height()
        t = (x - self.PAD_L) / max(1, (w - self.PAD_L - self.PAD_R))
        p = (1 - (y - self.PAD_T) / max(1, (h - self.PAD_T - self.PAD_B))) * self.n
        return min(max(t, 0.0), 1.0), min(max(p, 0.0), float(self.n))

    def _hit(self, x, y):
        for i, (t, p) in enumerate(self.points):
            px, py = self._xy(t, p)
            if abs(px - x) < 9 and abs(py - y) < 9:
                return i
        return None

    # interaction
    def _press(self, e):
        if self.mode != "draw":
            return
        i = self._hit(e.x, e.y)
        if i is None:
            t, p = self._tp(e.x, e.y)
            self.points.append((t, p))
            self.points.sort()
            i = self.points.index((t, p))
        self.drag = i
        self.redraw(); self.on_change()

    def _move(self, e):
        if self.drag is None or self.mode != "draw":
            return
        t, p = self._tp(e.x, e.y)
        i = self.drag
        # keep order; first and last points stay pinned to the ends in time
        if i == 0:
            t = 0.0
        elif i == len(self.points) - 1:
            t = 1.0
        else:
            t = min(max(t, self.points[i - 1][0] + 1e-3), self.points[i + 1][0] - 1e-3)
        self.points[i] = (t, p)
        self.redraw(); self.on_change()

    def _delete(self, e):
        if self.mode != "draw":
            return
        i = self._hit(e.x, e.y)
        if i is not None and 0 < i < len(self.points) - 1:
            self.points.pop(i)
            self.redraw(); self.on_change()

    # api
    def set_names(self, names):
        old_n = self.n
        self.names = names
        if self.points == [(0.0, 0.0), (1.0, float(old_n))]:
            self.points = [(0.0, 0.0), (1.0, float(self.n))]   # default ramp follows donor count
        self.points = [(t, min(p, float(self.n))) for t, p in self.points]
        self.redraw()

    def spec(self):
        return ",".join(f"{t:.4f}:{p:.4f}" for t, p in self.points)

    def load_spec(self, s):
        try:
            pts = sorted((float(a), float(b)) for a, b in (tok.split(":") for tok in s.split(",")))
            if pts:
                self.points = pts
        except ValueError:
            pass
        self.redraw()

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 50:
            return
        labels = ["carrier's own"] + [os.path.basename(n) for n in self.names] if self.names \
            else ["carrier's own", "donor 1"]
        for k, lab in enumerate(labels):
            _, y = self._xy(0, k)
            self.create_line(self.PAD_L, y, w - self.PAD_R, y, fill=LINE, dash=(2, 4))
            col = DIM if k == 0 else ACCENT2
            self.create_text(self.PAD_L - 8, y, text=(lab[:13] + "…") if len(lab) > 14 else lab,
                             fill=col, font=(UI[0], UI[1] - 2), anchor="e")
        for q in (0.25, 0.5, 0.75):
            x, _ = self._xy(q, 0)
            self.create_line(x, self.PAD_T, x, h - self.PAD_B, fill=LINE, dash=(1, 5))
        self.create_text(self.PAD_L, h - 8, text="start", fill=DIM, font=(UI[0], UI[1] - 3), anchor="w")
        self.create_text(w - self.PAD_R, h - 8, text="end", fill=DIM, font=(UI[0], UI[1] - 3), anchor="e")

        if self.mode == "draw":
            xy = [c for t, p in self.points for c in self._xy(t, p)]
            if len(xy) >= 4:
                self.create_line(*xy, fill=ACCENT, width=2.5)
            for t, p in self.points:
                x, y = self._xy(t, p)
                self.create_oval(x - 5, y - 5, x + 5, y + 5, fill=BG, outline=ACCENT, width=2)
        else:
            if self.preview is not None:
                ts = np.linspace(0, 1, self.preview.size)
                xy = [c for t, p in zip(ts, self.preview) for c in self._xy(t, min(p, self.n))]
                self.create_line(*xy, fill=ACCENT, width=2, smooth=True)
            msg = ("entropy walk: a fresh path is drawn from os.urandom at every render (preview shown)"
                   if self.mode == "walk" else "follows donor 1's loudness")
            self.create_text(w - self.PAD_R - 4, self.PAD_T + 6, text=msg, fill=DIM,
                             font=(UI[0], UI[1] - 3), anchor="ne")


# ----------------------------------------------------------------------------
# file list
# ----------------------------------------------------------------------------
class FileList(tk.Frame):
    def __init__(self, parent, title, subtitle, color, on_change, on_audition, reorder=False):
        super().__init__(parent, bg=PANEL)
        self.paths, self.on_change = [], on_change
        head = tk.Frame(self, bg=PANEL)
        head.pack(fill="x", padx=12, pady=(10, 2))
        tk.Label(head, text=title, bg=PANEL, fg=color, font=UI_B).pack(side="left")
        tk.Label(head, text="  " + subtitle, bg=PANEL, fg=DIM, font=(UI[0], UI[1] - 2)).pack(side="left")
        self.lb = tk.Listbox(self, height=4, bg=FIELD, fg=TEXT, selectbackground=LINE,
                             selectforeground=TEXT, relief="flat", highlightthickness=0,
                             font=MONO, activestyle="none")
        self.lb.pack(fill="x", padx=12)
        self.lb.bind("<Double-Button-1>", lambda _e: self._sel() and on_audition(self._sel()))
        bar = tk.Frame(self, bg=PANEL)
        bar.pack(fill="x", padx=12, pady=(4, 4))
        ttk.Button(bar, text="Add…", command=self.add).pack(side="left")
        ttk.Button(bar, text="Remove", command=self.remove).pack(side="left", padx=4)
        if reorder:
            ttk.Button(bar, text="▲", width=2, command=lambda: self.shift(-1)).pack(side="left")
            ttk.Button(bar, text="▼", width=2, command=lambda: self.shift(1)).pack(side="left", padx=2)
        ttk.Button(bar, text="▶ Listen", command=lambda: self._sel() and on_audition(self._sel())).pack(side="right")

    def _sel(self):
        s = self.lb.curselection()
        return self.paths[s[0]] if s else None

    def add(self):
        for p in filedialog.askopenfilenames(title="Choose sound files", filetypes=AUDIO_TYPES):
            self.paths.append(p)
        self.refresh()

    def remove(self):
        s = self.lb.curselection()
        if s:
            self.paths.pop(s[0]); self.refresh()

    def shift(self, d):
        s = self.lb.curselection()
        if not s:
            return
        i, j = s[0], s[0] + d
        if 0 <= j < len(self.paths):
            self.paths[i], self.paths[j] = self.paths[j], self.paths[i]
            self.refresh(); self.lb.selection_set(j)

    def set(self, paths):
        self.paths = [p for p in paths if os.path.exists(p)]
        self.refresh()

    def refresh(self):
        self.lb.delete(0, "end")
        for k, p in enumerate(self.paths, 1):
            self.lb.insert("end", f"{k}  {os.path.basename(p)}")
        self.on_change()


# ----------------------------------------------------------------------------
# spectrogram as a Tk image (no PIL / matplotlib needed)
# ----------------------------------------------------------------------------
def _lut():
    stops = np.array([[0, 0.05, 0.05, 0.08], [0.25, 0.25, 0.07, 0.35], [0.5, 0.65, 0.15, 0.35],
                      [0.75, 0.95, 0.45, 0.2], [1.0, 1.0, 0.93, 0.7]])
    x = np.linspace(0, 1, 256)
    return (np.stack([np.interp(x, stops[:, 0], stops[:, c]) for c in (1, 2, 3)], 1) * 255).astype(np.uint8)


LUT = _lut()


def spectrogram_ppm(path, width, height):
    from scipy.io import wavfile
    sr, y = wavfile.read(path)
    y = y.mean(axis=1) if y.ndim > 1 else y
    y = y.astype(np.float64)
    n = 2048
    hop = max(1, (y.size - n) // width)
    cols = min(width, max(1, (y.size - n) // hop))
    win = np.hanning(n)
    idx = np.arange(n)[None, :] + hop * np.arange(cols)[:, None]
    S = np.abs(np.fft.rfft(y[idx] * win, axis=1)) ** 2
    f = np.fft.rfftfreq(n, 1 / sr)
    fy = np.geomspace(50, sr / 2 * 0.98, height)[::-1]
    img = np.stack([np.interp(fy, f, col) for col in S], 1)
    db = 10 * np.log10(img + 1e-14)
    db = np.clip((db - db.max() + 70) / 70, 0, 1)
    rgb = LUT[(db * 255).astype(np.uint8)]
    if rgb.shape[1] < width:
        rgb = np.repeat(rgb, int(np.ceil(width / rgb.shape[1])), axis=1)[:, :width]
    h, w = rgb.shape[:2]
    return f"P6 {w} {h} 255 ".encode() + rgb.tobytes()


# ----------------------------------------------------------------------------
# the window
# ----------------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        root.title("DUDLEY — sound hybridization")
        root.configure(bg=BG)
        root.minsize(1060, 720)
        self._style()
        self.q = queue.Queue()
        self.worker = None
        self.cancel_flag = threading.Event()
        self.player = None
        self.last_output = None
        self.spec_img = None

        # header
        top = tk.Frame(root, bg=BG)
        top.pack(fill="x", padx=16, pady=(12, 6))
        tk.Label(top, text="DUDLEY", bg=BG, fg=ACCENT, font=TITLE).pack(side="left")
        tk.Label(top, text="   one sound's spectral envelope, worn by another sound's grains",
                 bg=BG, fg=DIM, font=UI).pack(side="left")
        ttk.Button(top, text="Save preset…", command=self.save_preset).pack(side="right")
        ttk.Button(top, text="Load preset…", command=self.load_preset).pack(side="right", padx=6)

        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=16)
        body.columnconfigure(0, weight=5, uniform="c")
        body.columnconfigure(1, weight=4, uniform="c")
        body.rowconfigure(0, weight=1)

        # ---------------- left column: sources, path, modes ----------------
        left = tk.Frame(body, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        src = tk.Frame(left, bg=PANEL)
        src.pack(fill="x")
        src.columnconfigure(0, weight=1); src.columnconfigure(1, weight=1)
        self.carriers = FileList(src, "CARRIERS", "what it's made of (grains)", ACCENT,
                                 self._sources_changed, self.audition)
        self.carriers.grid(row=0, column=0, sticky="nsew")
        self.donors = FileList(src, "DONORS", "what it's shaped like (envelope)", ACCENT2,
                               self._sources_changed, self.audition, reorder=True)
        self.donors.grid(row=0, column=1, sticky="nsew")

        pathbox = tk.Frame(left, bg=PANEL)
        pathbox.pack(fill="both", expand=True, pady=(8, 0))
        ph = tk.Frame(pathbox, bg=PANEL)
        ph.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(ph, text="MORPH PATH", bg=PANEL, fg=TEXT, font=UI_B).pack(side="left")
        self.path_mode = tk.StringVar(value="draw")
        for val, lab in (("draw", "Draw"), ("walk", "Entropy walk"), ("rms", "Follow donor 1 loudness")):
            ttk.Radiobutton(ph, text=lab, value=val, variable=self.path_mode,
                            command=self._path_mode_changed).pack(side="left", padx=(12, 0))
        self.path = PathEditor(pathbox, self._path_changed)
        self.path.pack(fill="both", expand=True, padx=12)
        pf = tk.Frame(pathbox, bg=PANEL)
        pf.pack(fill="x", padx=12, pady=(4, 10))
        self.path_hint = tk.Label(pf, text="click to add a point · drag to move · right-click to delete",
                                  bg=PANEL, fg=DIM, font=(UI[0], UI[1] - 2))
        self.path_hint.pack(side="left")
        ttk.Button(pf, text="Reset", command=self._reset_path).pack(side="right")
        self.walk_rate = tk.DoubleVar(value=0.35)
        self.walk_box = tk.Frame(pf, bg=PANEL)
        tk.Label(self.walk_box, text="walk rate", bg=PANEL, fg=DIM, font=(UI[0], UI[1] - 2)).pack(side="left")
        ttk.Scale(self.walk_box, from_=0.05, to=2.0, variable=self.walk_rate, length=140,
                  command=lambda _=None: self._path_mode_changed()).pack(side="left", padx=6)

        modes = tk.Frame(left, bg=PANEL)
        modes.pack(fill="x", pady=(8, 0))
        self.morph = tk.StringVar(value="transport")
        self.dynamics = tk.StringVar(value="donor")
        self.scan = tk.StringVar(value="stretch")
        self.freeze = tk.DoubleVar(value=0.5)
        r = 0
        for label, var, opts in (
            ("Between envelopes", self.morph, (("transport", "Transport · peaks travel"),
                                               ("cepstral", "Cepstral · peaks fade in place"))),
            ("Loudness follows", self.dynamics, (("donor", "Donor"), ("carrier", "Carrier"))),
        ):
            tk.Label(modes, text=label, bg=PANEL, fg=TEXT, font=UI).grid(row=r, column=0, sticky="w",
                                                                         padx=12, pady=(8 if r == 0 else 2, 2))
            f = tk.Frame(modes, bg=PANEL)
            f.grid(row=r, column=1, sticky="w", pady=(8 if r == 0 else 2, 2))
            for v, t in opts:
                ttk.Radiobutton(f, text=t, value=v, variable=var).pack(side="left", padx=(0, 14))
            r += 1
        tk.Label(modes, text="Read carrier", bg=PANEL, fg=TEXT, font=UI).grid(row=r, column=0, sticky="w",
                                                                             padx=12, pady=(2, 10))
        f = tk.Frame(modes, bg=PANEL)
        f.grid(row=r, column=1, sticky="w", pady=(2, 10))
        for v, t in (("stretch", "Stretch"), ("loop", "Loop"), ("random", "Random"), ("freeze", "Freeze at")):
            ttk.Radiobutton(f, text=t, value=v, variable=self.scan).pack(side="left", padx=(0, 10))
        ttk.Scale(f, from_=0, to=1, variable=self.freeze, length=110).pack(side="left")

        # ---------------- right column: sliders + render ----------------
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        sl = tk.Frame(right, bg=PANEL)
        sl.pack(fill="x")
        sl.columnconfigure(1, weight=1)
        tk.Label(sl, text="SETTINGS", bg=PANEL, fg=TEXT, font=UI_B).grid(row=0, column=0, sticky="w",
                                                                        padx=12, pady=(10, 4))
        tk.Label(sl, text="double-click a name to reset", bg=PANEL, fg=DIM,
                 font=(UI[0], UI[1] - 3)).grid(row=0, column=1, columnspan=2, sticky="e", padx=12)
        self.sliders = {}
        for i, (k, lab, lo, hi, d, res, tip) in enumerate(SLIDERS, start=1):
            self.sliders[k] = Slider(sl, i, k, lab, lo, hi, d, res, tip, self._tip)
        i = len(SLIDERS) + 1
        tk.Label(sl, text="Sample rate", bg=PANEL, fg=TEXT, font=UI).grid(row=i, column=0, sticky="w", padx=12)
        self.sr = tk.StringVar(value="44100")
        ttk.Combobox(sl, textvariable=self.sr, values=["44100", "48000", "88200", "96000"],
                     width=8, state="readonly").grid(row=i, column=1, sticky="w", pady=2)
        i += 1
        tk.Label(sl, text="Entropy", bg=PANEL, fg=TEXT, font=UI).grid(row=i, column=0, sticky="w",
                                                                     padx=12, pady=(2, 8))
        ef = tk.Frame(sl, bg=PANEL)
        ef.grid(row=i, column=1, columnspan=2, sticky="w", pady=(2, 8))
        self.use_seed = tk.BooleanVar(value=False)
        self.seed = tk.StringVar(value="1")
        ttk.Checkbutton(ef, text="repeatable, seed", variable=self.use_seed).pack(side="left")
        tk.Entry(ef, textvariable=self.seed, width=7, bg=FIELD, fg=TEXT, insertbackground=TEXT,
                 relief="flat", font=MONO).pack(side="left", padx=6)
        tk.Label(ef, text="(off = os.urandom)", bg=PANEL, fg=DIM, font=(UI[0], UI[1] - 3)).pack(side="left")

        self.tip = tk.Label(right, text="", bg=BG, fg=DIM, font=(UI[0], UI[1] - 2),
                            anchor="w", justify="left", wraplength=420)
        self.tip.pack(fill="x", pady=(4, 0))

        out = tk.Frame(right, bg=PANEL)
        out.pack(fill="x", pady=(6, 0))
        of = tk.Frame(out, bg=PANEL)
        of.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(of, text="SAVE TO", bg=PANEL, fg=TEXT, font=UI_B).pack(side="left")
        ttk.Button(of, text="Choose…", command=self.choose_output).pack(side="right")
        self.output = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Music", "DUDLEY", "renders",
                                                      "dudley.wav"))
        tk.Label(out, textvariable=self.output, bg=PANEL, fg=DIM, font=MONO, anchor="w").pack(
            fill="x", padx=12)
        self.number_takes = tk.BooleanVar(value=True)
        ttk.Checkbutton(out, text="keep every take (number the files, never overwrite)",
                        variable=self.number_takes).pack(anchor="w", padx=12, pady=(2, 2))
        lf = tk.Frame(out, bg=PANEL)
        lf.pack(fill="x", padx=12, pady=(0, 8))
        self.auto_logic = tk.BooleanVar(value=False)
        ttk.Checkbutton(lf, text="send every take to Logic Pro when it finishes",
                        variable=self.auto_logic).pack(side="left")
        self.bits = tk.StringVar(value="24-bit")
        ttk.Combobox(lf, textvariable=self.bits, values=["24-bit", "32-bit float"], width=11,
                     state="readonly").pack(side="right")

        go = tk.Frame(right, bg=BG)
        go.pack(fill="x", pady=(8, 0))
        self.render_btn = tk.Button(go, text="RENDER", command=self.render, bg=ACCENT, fg="#111",
                                    activebackground="#f2b95a", font=(UI[0], UI[1] + 2, "bold"),
                                    relief="flat", padx=20, pady=6, highlightthickness=0, bd=0)
        self.render_btn.pack(side="left")
        ttk.Button(go, text="Stop", command=self.stop_all).pack(side="left", padx=6)
        ttk.Button(go, text="▶ Play result", command=lambda: self.last_output and self.audition(self.last_output)
                   ).pack(side="left")
        ttk.Button(go, text="Show file", command=self.reveal).pack(side="left", padx=6)
        self.logic_btn = tk.Button(right, text="Open last take in Logic Pro", command=self.open_in_logic,
                                   bg=ACCENT2, fg="#111", activebackground="#7cc7c7",
                                   font=UI_B, relief="flat", padx=10, pady=4,
                                   highlightthickness=0, bd=0)
        self.logic_btn.pack(fill="x", pady=(6, 0))
        self.prog = ttk.Progressbar(right, mode="determinate", maximum=1.0)
        self.prog.pack(fill="x", pady=(8, 2))
        self.status = tk.Label(right, text="add at least one carrier and one donor", bg=BG, fg=DIM,
                               font=MONO, anchor="w")
        self.status.pack(fill="x")

        # spectrogram strip across the bottom
        sp = tk.Frame(root, bg=PANEL)
        sp.pack(fill="x", padx=16, pady=(8, 14))
        tk.Label(sp, text="LAST TAKE", bg=PANEL, fg=TEXT, font=UI_B).pack(anchor="w", padx=12, pady=(8, 2))
        self.spec = tk.Canvas(sp, bg=FIELD, height=130, highlightthickness=0)
        self.spec.pack(fill="x", padx=12, pady=(0, 10))
        self.spec.create_text(12, 65, text="spectrogram of each render appears here", fill=DIM,
                              font=(UI[0], UI[1] - 2), anchor="w", tags="ph")

        root.bind("<Command-r>" if sys.platform == "darwin" else "<Control-r>", lambda _e: self.render())
        root.bind("<space>", lambda _e: self._space())
        root.protocol("WM_DELETE_WINDOW", self._quit)
        self.root.after(80, self._poll)

    # ---------------- styling ----------------
    def _style(self):
        st = ttk.Style()
        st.theme_use("clam")
        st.configure(".", background=PANEL, foreground=TEXT, font=UI)
        st.configure("TButton", background=FIELD, foreground=TEXT, bordercolor=LINE,
                     lightcolor=FIELD, darkcolor=FIELD, padding=(8, 3))
        st.map("TButton", background=[("active", LINE)])
        st.configure("TRadiobutton", background=PANEL, foreground=TEXT, indicatorcolor=FIELD)
        st.map("TRadiobutton", indicatorcolor=[("selected", ACCENT)], background=[("active", PANEL)])
        st.configure("TCheckbutton", background=PANEL, foreground=TEXT, indicatorcolor=FIELD)
        st.map("TCheckbutton", indicatorcolor=[("selected", ACCENT)], background=[("active", PANEL)])
        st.configure("Horizontal.TScale", background=PANEL, troughcolor=FIELD, bordercolor=PANEL,
                     lightcolor=ACCENT, darkcolor=ACCENT)
        st.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=FIELD,
                     bordercolor=BG, lightcolor=ACCENT, darkcolor=ACCENT)
        st.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=TEXT,
                     arrowcolor=TEXT, bordercolor=LINE)
        st.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)],
               selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", TEXT)])

    def _tip(self, text):
        self.tip.config(text=text)

    # ---------------- sources / path ----------------
    def _sources_changed(self):
        if hasattr(self, "path"):
            self.path.set_names(self.donors.paths)
            self._path_mode_changed()

    def _path_changed(self):
        pass

    def _reset_path(self):
        self.path.points = [(0.0, 0.0), (1.0, float(self.path.n))]
        self.path_mode.set("draw")
        self._path_mode_changed()

    def _path_mode_changed(self):
        m = self.path_mode.get()
        self.path.mode = m
        if m == "walk":
            self.walk_box.pack(side="right", padx=8)
            self.path_hint.config(text="a new random walk every render")
            fn, _ = dudley.build_path(f"walk:{self.walk_rate.get():.3f}", self.path.n,
                                      dudley.Entropy(), [])
            self.path.preview = np.array([fn(t) for t in np.linspace(0, 1, 200)])
        elif m == "rms":
            self.walk_box.pack_forget()
            self.path_hint.config(text="louder donor = further along the path")
            self.path.preview = None
        else:
            self.walk_box.pack_forget()
            self.path_hint.config(text="click to add a point · drag to move · right-click to delete")
        self.path.redraw()

    # ---------------- presets ----------------
    def _state(self):
        return {
            "carriers": self.carriers.paths, "donors": self.donors.paths,
            "path_mode": self.path_mode.get(), "path_points": self.path.spec(),
            "walk_rate": self.walk_rate.get(), "morph": self.morph.get(),
            "dynamics": self.dynamics.get(), "scan": self.scan.get(), "freeze": self.freeze.get(),
            "sliders": {k: s.get() for k, s in self.sliders.items()},
            "sr": self.sr.get(), "use_seed": self.use_seed.get(), "seed": self.seed.get(),
            "output": self.output.get(), "number_takes": self.number_takes.get(),
            "auto_logic": self.auto_logic.get(), "bits": self.bits.get(),
        }

    def save_preset(self):
        p = filedialog.asksaveasfilename(title="Save preset", defaultextension=".dudley.json",
                                         filetypes=[("DUDLEY preset", "*.json")])
        if p:
            with open(p, "w") as fh:
                json.dump(self._state(), fh, indent=2)
            self.status.config(text=f"preset saved  {os.path.basename(p)}")

    def load_preset(self):
        p = filedialog.askopenfilename(title="Load preset", filetypes=[("DUDLEY preset", "*.json")])
        if not p:
            return
        try:
            with open(p) as fh:
                s = json.load(fh)
        except (OSError, ValueError) as e:
            messagebox.showerror("DUDLEY", f"Couldn't read preset:\n{e}")
            return
        self.apply_state(s)
        self.status.config(text=f"preset loaded  {os.path.basename(p)}")

    def apply_state(self, s):
        self.carriers.set(s.get("carriers", []))
        self.donors.set(s.get("donors", []))
        self.path.load_spec(s.get("path_points", "0:0,1:1"))
        self.walk_rate.set(s.get("walk_rate", 0.35))
        self.path_mode.set(s.get("path_mode", "draw"))
        for var, key in ((self.morph, "morph"), (self.dynamics, "dynamics"), (self.scan, "scan"),
                         (self.sr, "sr"), (self.seed, "seed"), (self.output, "output")):
            if key in s:
                var.set(s[key])
        self.freeze.set(s.get("freeze", 0.5))
        self.use_seed.set(s.get("use_seed", False))
        self.number_takes.set(s.get("number_takes", True))
        self.auto_logic.set(s.get("auto_logic", False))
        self.bits.set(s.get("bits", "24-bit"))
        for k, v in s.get("sliders", {}).items():
            if k in self.sliders:
                self.sliders[k].set(v)
        self._path_mode_changed()

    # ---------------- output ----------------
    def choose_output(self):
        cur = self.output.get()
        p = filedialog.asksaveasfilename(title="Save renders as", defaultextension=".wav",
                                         initialdir=os.path.dirname(cur), initialfile=os.path.basename(cur),
                                         filetypes=[("WAV", "*.wav")])
        if p:
            self.output.set(p)

    def _take_path(self):
        p = self.output.get()
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        if not self.number_takes.get():
            return p
        base, ext = os.path.splitext(p)
        k = 1
        while os.path.exists(f"{base}_{k:03d}{ext or '.wav'}"):
            k += 1
        return f"{base}_{k:03d}{ext or '.wav'}"

    def reveal(self):
        p = self.last_output
        if not p or not os.path.exists(p):
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", p])
        elif sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", p])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(p)])

    # ---------------- Logic Pro ----------------
    LOGIC_IDS = ("com.apple.logic10", "com.apple.logic.pro")

    def open_in_logic(self):
        p = self.last_output
        if not p or not os.path.exists(p):
            messagebox.showinfo("DUDLEY", "Render a take first.")
            return
        if sys.platform != "darwin":
            messagebox.showinfo("DUDLEY", "Logic Pro is Mac-only; use Show file instead.")
            return
        for bid in self.LOGIC_IDS:  # bundle id works whether it's named "Logic Pro" or "Logic Pro X"
            if subprocess.run(["open", "-b", bid, p], capture_output=True).returncode == 0:
                self.status.config(text=f"sent to Logic Pro  {os.path.basename(p)}")
                return
        messagebox.showerror("DUDLEY", "Couldn't find Logic Pro. Use Show file and drag the take in.")

    # ---------------- audio playback ----------------
    def audition(self, path):
        self.stop_audio()
        if sys.platform == "darwin":
            cmd = ["afplay", path]
        elif shutil.which("ffplay"):
            cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]
        elif shutil.which("aplay"):
            cmd = ["aplay", "-q", path]
        else:
            messagebox.showinfo("DUDLEY", "No audio player found (afplay / ffplay / aplay).")
            return
        try:
            self.player = subprocess.Popen(cmd)
            self.status.config(text=f"playing  {os.path.basename(path)}   (space or Stop to stop)")
        except OSError as e:
            messagebox.showerror("DUDLEY", str(e))

    def stop_audio(self):
        if self.player and self.player.poll() is None:
            self.player.terminate()
        self.player = None

    def _space(self):
        if isinstance(self.root.focus_get(), tk.Entry):
            return
        if self.player and self.player.poll() is None:
            self.stop_audio()
        elif self.last_output:
            self.audition(self.last_output)

    def stop_all(self):
        self.stop_audio()
        if self.worker and self.worker.is_alive():
            self.cancel_flag.set()
            self.status.config(text="stopping…")

    # ---------------- render ----------------
    def _args(self, out_path):
        s = {k: v.get() for k, v in self.sliders.items()}
        m = self.path_mode.get()
        path = (self.path.spec() if m == "draw" else
                f"walk:{self.walk_rate.get():.3f}" if m == "walk" else "rms")
        scan = self.scan.get()
        if scan == "freeze":
            scan = f"freeze:{self.freeze.get():.3f}"
        seed = None
        if self.use_seed.get():
            try:
                seed = int(self.seed.get())
            except ValueError:
                seed = 1
        return SimpleNamespace(
            carrier=list(self.carriers.paths), donor=list(self.donors.paths), output=out_path,
            morph=self.morph.get(), path=path, texture=s["texture"], dynamics=self.dynamics.get(),
            grain_ms=s["grain_ms"], grain_jitter=s["grain_jitter"], density=s["density"],
            onset_jitter=s["onset_jitter"], scan=scan, spray_ms=s["spray_ms"], pitch=s["pitch"],
            pitch_spread=s["pitch_spread"], width=s["width"], lifter_ms=s["lifter_ms"],
            max_gain_db=s["max_gain_db"], grid=384,
            duration=(s["duration"] or None), sr=int(self.sr.get()), peak_db=s["peak_db"],
            fps=30.0, seed=seed, bits=32 if self.bits.get().startswith("32") else 24)

    def render(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.carriers.paths or not self.donors.paths:
            messagebox.showinfo("DUDLEY", "Add at least one carrier and one donor first.")
            return
        try:
            out = self._take_path()
        except OSError as e:
            messagebox.showerror("DUDLEY", f"Can't write there:\n{e}")
            return
        args = self._args(out)
        self.cancel_flag.clear()
        self.render_btn.config(state="disabled", text="RENDERING")
        self.prog["value"] = 0

        def work():
            try:
                m = dudley.render(args, progress=lambda st, f: self.q.put(("prog", st, f)),
                                  cancel=self.cancel_flag.is_set)
                self.q.put(("done", m))
            except dudley.Cancelled:
                self.q.put(("cancelled",))
            except Exception as e:  # surface anything to the window, don't die silently
                self.q.put(("error", f"{type(e).__name__}: {e}"))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "prog":
                    self.prog["value"] = msg[2]
                    self.status.config(text=f"{msg[1]}  {int(msg[2] * 100)}%")
                elif kind == "done":
                    m = msg[1]
                    self.last_output = m["output"]["path"]
                    self.status.config(text=f"{os.path.basename(self.last_output)}  ·  {m['duration_s']}s  ·  "
                                            f"{m['grains_sounded']} grains  ·  {m['seconds_to_render']}s  ·  "
                                            f"{m['entropy']}")
                    self._reset_btn()
                    self._show_spec(self.last_output)
                    if self.auto_logic.get():
                        self.open_in_logic()
                elif kind == "cancelled":
                    self.status.config(text="render stopped")
                    self.prog["value"] = 0
                    self._reset_btn()
                elif kind == "error":
                    self.status.config(text="render failed")
                    self._reset_btn()
                    messagebox.showerror("DUDLEY", msg[1])
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _reset_btn(self):
        self.render_btn.config(state="normal", text="RENDER")

    def _show_spec(self, path):
        try:
            w = max(200, self.spec.winfo_width())
            h = max(60, self.spec.winfo_height())
            self.spec_img = tk.PhotoImage(data=spectrogram_ppm(path, w, h))
            self.spec.delete("all")
            self.spec.create_image(0, 0, image=self.spec_img, anchor="nw")
            self.spec.create_text(8, 8, text=os.path.basename(path), fill=TEXT, anchor="nw",
                                  font=(UI[0], UI[1] - 2))
        except Exception as e:
            self.spec.delete("all")
            self.spec.create_text(12, 20, text=f"(no preview: {e})", fill=DIM, anchor="w")

    def _quit(self):
        self.stop_all()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = App(root)
    # optional: a preset path on the command line opens straight into it
    if len(sys.argv) > 1 and sys.argv[1].endswith(".json") and os.path.exists(sys.argv[1]):
        with open(sys.argv[1]) as fh:
            app.apply_state(json.load(fh))
    root.mainloop()


if __name__ == "__main__":
    main()
