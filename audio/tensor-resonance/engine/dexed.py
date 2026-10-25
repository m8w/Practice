"""
DEXED — change Dexed's sound without crashing it, and see who sends it Program Changes.

Why this exists
---------------
Dexed 1.0.x handles a MIDI Program Change on its audio thread, and while doing it repaints part of
its window from that same thread (DexedAudioProcessor::setCurrentProgram ->
editor->global.setParamMessage -> repaint). The window is also being repainted normally, so now and
then the two collide and corrupt memory: the crash report then shows rebuildProgramCombobox ->
NSViewComponentPeer::repaint -> "pointer being freed was not allocated". The more Program Changes,
the sooner it happens (every few hours with patch changes every few seconds).

A single-voice SysEx dump (the DX7's own "here is a voice" message) goes another way in Dexed
(updateProgramFromSysex): it copies the voice and only asks the window to refresh later, from the
right thread. So this sends voices as SysEx, taken from your Dexed cartridges (.syx), never as
Program Change.

    python3 dexed.py --list                         # cartridges found, voices in them
    python3 dexed.py --send Tensor                  # send one random voice to the port named *Tensor*
    python3 dexed.py --send Tensor --name BRASS     # ... a voice whose name contains BRASS
    python3 dexed.py --send Tensor --mutate 0.3     # ... slightly altered (rates, fine tuning, LFO)
    python3 dexed.py --watch                        # print every Program Change / Bank Select / SysEx
                                                    # arriving on any MIDI input, with the port's name
--list needs only Python; --send and --watch need mido + python-rtmidi (the engine's .venv has them):
    ./dexed.sh --watch
"""

import argparse
import os
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()

# Where Dexed keeps cartridges (from Dexed's own resolvAppDir) + a few usual places.
CART_DIRS = [
    HOME / "Library" / "Application Support" / "DigitalSuburban" / "Dexed" / "Cartridges",
    Path("/Applications/Dexed/Cartridges"),             # "Dexed" folder next to Dexed.app
    HOME / "Documents" / "Dexed",
    HOME / "Music" / "Dexed",
    HOME / ".local" / "share" / "DigitalSuburban" / "Dexed" / "Cartridges",
    HERE / "dexed_carts",                               # drop .syx files here too
]

# Highest legal value of each of the 155 VCED bytes (DX7 single-voice format).
_OP_MAX = [99, 99, 99, 99, 99, 99, 99, 99,   # EG rates 1-4, levels 1-4
           99, 99, 99, 3, 3, 7, 3, 7,       # break point, L/R depth, L/R curve, rate scaling, AMS, KVS
           99, 1, 31, 99, 14]               # output level, osc mode, coarse, fine, detune
VCED_MAX = _OP_MAX * 6 + [99] * 8 + [31, 7, 1, 99, 99, 99, 99, 1, 5, 7, 48] + [126] * 10


# ----------------------------------------------------------------------------- cartridges
def parse_cart(data):
    """32-voice cartridge (.syx) -> the 4096 packed voice bytes, or None.
    Follows Dexed's Cartridge::load: a 4104-byte F0 43 0n 09 20 00 ... F7 block, or raw 4096 bytes."""
    data = bytes(data)
    if len(data) < 4096:
        return None
    if data[0] != 0xF0:
        return data[:4096] if len(data) == 4096 else None
    i = 0
    while i + 4104 <= len(data):
        if data[i] == 0xF0 and data[i + 1] == 0x43 and data[i + 3] == 0x09 and data[i + 4103] == 0xF7:
            return data[i + 6:i + 4102]
        nxt = data.find(b"\xF0", i + 1)
        if nxt < 0:
            break
        i = nxt
    return None


def voice_name(vmem, idx):
    raw = vmem[idx * 128 + 118: idx * 128 + 128]
    return "".join(chr(b & 0x7F) if 32 <= (b & 0x7F) < 127 else " " for b in raw).rstrip()


def _norm(v, mx):
    return v if v <= mx else int(v / 255 * mx)


def unpack_voice(vmem, idx):
    """Packed 128-byte voice -> 155-byte VCED, as Dexed's Cartridge::unpackProgram does it,
    then every value clamped to its legal range (so a damaged cartridge can't upset Dexed)."""
    b = vmem[idx * 128: idx * 128 + 128]
    out = [0] * 155
    for op in range(6):
        s, d = op * 17, op * 21
        for i in range(11):
            out[d + i] = _norm(b[s + i] & 0x7F, 99)
        lr = b[s + 11] & 0x0F
        out[d + 11], out[d + 12] = lr & 3, (lr >> 2) & 3
        det_rs = b[s + 12] & 0x7F
        out[d + 13] = det_rs & 7
        kvs_ams = b[s + 13] & 0x1F
        out[d + 14], out[d + 15] = kvs_ams & 3, (kvs_ams >> 2) & 7
        out[d + 16] = b[s + 14] & 0x7F
        fc_mode = b[s + 15] & 0x3F
        out[d + 17], out[d + 18] = fc_mode & 1, (fc_mode >> 1) & 0x1F
        out[d + 19] = b[s + 16] & 0x7F
        out[d + 20] = (det_rs >> 3) & 0x7F
    for i in range(8):
        out[126 + i] = _norm(b[102 + i] & 0x7F, 99)
    out[134] = _norm(b[110] & 0x1F, 31)
    oks_fb = b[111] & 0x0F
    out[135], out[136] = oks_fb & 7, oks_fb >> 3
    for i in range(4):
        out[137 + i] = b[112 + i] & 0x7F
    lpms = b[116] & 0x7F
    out[141], out[142], out[143] = lpms & 1, (lpms >> 1) & 7, lpms >> 4
    out[144] = b[117] & 0x7F
    for i in range(10):
        out[145 + i] = b[118 + i] & 0x7F
    return clamp_vced(out)


def clamp_vced(v):
    out = [max(0, min(VCED_MAX[i], int(x))) for i, x in enumerate(v)]
    for i in range(145, 155):
        if out[i] < 32:
            out[i] = 32
    return out


def mutate_vced(v, amount, rng):
    """Small, musical changes: envelope rates, fine tuning, detune, LFO. Levels and the
    algorithm stay, so the voice keeps its character and never goes silent."""
    if amount <= 0:
        return list(v)
    v = list(v)
    def nudge(i, span):
        if rng.random() < 0.6:
            v[i] = v[i] + int(round(rng.gauss(0, amount * span)))
    for op in range(6):
        d = op * 21
        for i in range(4):                       # EG rates
            nudge(d + i, 25)
        nudge(d + 19, 40)                         # fine frequency
        nudge(d + 20, 4)                          # detune
    for i in (137, 139, 140):                     # LFO speed, pitch / amp depth
        nudge(i, 20)
    nudge(135, 2)                                 # feedback
    return clamp_vced(v)


def single_voice_sysex(vced, channel=1):
    """DX7 single-voice dump: F0 43 0n 00 01 1B <155 bytes> checksum F7 (163 bytes)."""
    vced = clamp_vced(vced)
    return bytes([0xF0, 0x43, (int(channel) - 1) & 0x0F, 0x00, 0x01, 0x1B, *vced, (-sum(vced)) & 0x7F, 0xF7])


class VoicePool:
    """Every voice in every cartridge found, loaded once (and again if the folders change)."""

    def __init__(self, dirs=None):
        extra = [Path(p) for p in os.environ.get("TRE_DEXED_CARTS", "").split(os.pathsep) if p]
        self.dirs = extra + list(dirs or CART_DIRS)
        self.carts, self._sig = [], None

    def _scan_sig(self):
        sig = []
        for d in self.dirs:
            try:
                if d.is_dir():
                    sig.append((str(d), d.stat().st_mtime))
            except OSError:
                pass
        return tuple(sig)

    def load(self, force=False):
        sig = self._scan_sig()
        if self.carts and sig == self._sig and not force:
            return self.carts
        carts, seen = [], set()
        for d in self.dirs:
            if not d.is_dir():
                continue
            for p in sorted(d.rglob("*")):
                if p.suffix.lower() != ".syx" or not p.is_file() or len(carts) >= 3000:
                    continue
                try:
                    if p.stat().st_size > 70000:
                        continue
                    vm = parse_cart(p.read_bytes())
                except OSError:
                    continue
                if vm is None or vm in seen:
                    continue
                seen.add(vm)
                carts.append(dict(path=str(p), name=p.stem, dir=str(d), vmem=vm))
        self.carts, self._sig = carts, sig
        return carts

    def summary(self):
        carts = self.load()
        return dict(dirs=[str(d) for d in self.dirs if d.is_dir()], carts=len(carts), voices=32 * len(carts))

    def random(self, channel=1, mutate=0.0, name=None, rng=None):
        rng = rng or random.Random()
        carts = self.load()
        if not carts:
            return None
        picks = [(c, i) for c in carts for i in range(32)]
        if name:
            picks = [(c, i) for c, i in picks if name.lower() in voice_name(c["vmem"], i).lower()] or picks
        c, i = rng.choice(picks)
        vced = mutate_vced(unpack_voice(c["vmem"], i), float(mutate), rng)
        nm = voice_name(c["vmem"], i) + (" ~" if mutate > 0 else "")
        return dict(name=nm, cart=c["name"], index=i, sysex=list(single_voice_sysex(vced, channel)))


POOL = VoicePool()


# ----------------------------------------------------------------------------- MIDI helpers
def _mido():
    try:
        import mido
        return mido
    except ImportError:
        raise SystemExit("this needs mido + python-rtmidi: run it as ./dexed.sh (the engine's .venv has them)")


def pick_port(names, want):
    exact = [n for n in names if n == want]
    part = [n for n in names if want.lower() in n.lower()]
    return (exact or part or [None])[0]


def send(port, channel=1, name=None, mutate=0.0):
    mido = _mido()
    target = pick_port(mido.get_output_names(), port)
    if not target:
        raise SystemExit(f"no MIDI output matches '{port}'. Outputs: {', '.join(mido.get_output_names()) or '(none)'}")
    v = POOL.random(channel, mutate, name)
    if not v:
        raise SystemExit("no Dexed cartridges found in:\n  " + "\n  ".join(str(d) for d in POOL.dirs))
    with mido.open_output(target) as out:
        out.send(mido.Message("sysex", data=v["sysex"][1:-1]))
    print(f"  sent '{v['name']}' ({v['cart']} #{v['index'] + 1}) -> {target}, channel {channel}, as SysEx")


def watch(log=None):
    """Listen on every MIDI input; print Program Changes, Bank Selects and SysEx, plus a note count
    per port every 30 s. Whatever shows Program Changes on a port Dexed listens to is a crash risk."""
    mido = _mido()
    names = [n for n in mido.get_input_names()]
    if not names:
        raise SystemExit("no MIDI inputs")
    fh = open(log, "a") if log else None
    counts = {n: 0 for n in names}

    def out(line):
        line = time.strftime("%H:%M:%S  ") + line
        print(line, flush=True)
        if fh:
            fh.write(line + "\n")
            fh.flush()

    def handler(name):
        def cb(msg):
            if msg.type == "program_change":
                out(f"PROGRAM CHANGE  {msg.program:3d}  ch {msg.channel + 1:2d}  <- {name}")
            elif msg.type == "control_change" and msg.control in (0, 32):
                out(f"bank select     CC{msg.control}={msg.value:3d}  ch {msg.channel + 1:2d}  <- {name}")
            elif msg.type == "sysex":
                d = msg.data
                what = ("DX7 single voice (safe)" if len(d) >= 5 and d[0] == 0x43 and d[2] == 0 and d[3] == 1
                        else "DX7 32-voice cartridge (switches program: like a Program Change)" if len(d) >= 4 and d[0] == 0x43 and d[2] == 9
                        else f"manufacturer {d[0]:#04x}" if d else "empty")
                out(f"sysex {len(d) + 2:5d} B  {what}  <- {name}")
            elif msg.type in ("note_on", "note_off"):
                counts[name] += 1
        return cb

    ports = [mido.open_input(n, callback=handler(n)) for n in names]
    out("listening on: " + " | ".join(names))
    out("Program Changes on a port Dexed listens to can crash Dexed 1.0.x. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(30)
            busy = ", ".join(f"{n}: {c}" for n, c in counts.items() if c)
            if busy:
                out("notes in the last 30 s  " + busy)
            for n in counts:
                counts[n] = 0
    except KeyboardInterrupt:
        pass
    finally:
        for p in ports:
            p.close()


def main():
    ap = argparse.ArgumentParser(description="crash-safe Dexed voice changes (SysEx) + MIDI Program Change sniffer")
    ap.add_argument("--list", action="store_true", help="show the cartridges found")
    ap.add_argument("--send", metavar="PORT", help="send one random voice to this MIDI output (part of its name)")
    ap.add_argument("--channel", type=int, default=1)
    ap.add_argument("--name", help="only voices whose name contains this")
    ap.add_argument("--mutate", type=float, default=0.0, help="0..1: alter rates, fine tuning, detune, LFO")
    ap.add_argument("--watch", action="store_true", help="print Program Changes / Bank Selects / SysEx on every input")
    ap.add_argument("--log", help="with --watch: also append to this file")
    a = ap.parse_args()
    if a.watch:
        return watch(a.log)
    if a.send:
        return send(a.send, a.channel, a.name, a.mutate)
    s = POOL.summary()
    print(f"  Dexed cartridges: {s['carts']} ({s['voices']} voices)")
    for d in s["dirs"]:
        print("   in", d)
    if a.list:
        for c in POOL.load():
            names = [voice_name(c["vmem"], i) for i in range(32)]
            print(f"  {c['name']}: " + ", ".join(names[:8]) + ", ...")
    if not s["carts"]:
        print("  none found. Looked in:\n    " + "\n    ".join(str(d) for d in POOL.dirs))


if __name__ == "__main__":
    main()
