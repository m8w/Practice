#!/usr/bin/env python3
"""
Surge XT patch changing, done the way Surge itself does it (checked against the Surge XT source).

Two routes:

1. OSC  (most direct: any patch, by name)
   Surge XT listens for OSC once "OSC in" is switched on in its menu (default port 53280,
   DEFAULT_OSC_PORT_IN in src/common/globals.h; it starts switched off).
     /patch/load <absolute path, no .fxp>    e.g. /Library/Application Support/Surge XT/patches_factory/Plucks/Clean
     /patch/random   /patch/incr   /patch/decr   /patch/incr_category   /patch/decr_category

2. MIDI Bank Select + Program Change
   SurgeSynthesizer::programChange() loads storage.patchIdToMidiBankAndProgram[CC0][program],
   and that table is filled ONLY from  <Surge XT user folder>/Patches/MIDI Programs/
   (user folder: Documents/Surge XT by default; can be elsewhere, e.g. ~/Music/Surge XT):
     bank 0      = the .fxp files loose in that folder (natural name order, max 128)
     bank 1..127 = its subfolders, in natural name order, 128 patches each at most
   An empty slot is ignored silently, which is why random program changes "did nothing":
   out of the box that folder is empty. `python surge.py --midi-programs` fills it from the
   factory library (copies only; it never deletes your own files). Restart Surge afterwards.
   The VST3 build does not receive program changes; use the standalone Surge XT app.

Run:
    ./surge.sh   (= python3 surge.py --serve)   # the patch-changer page; no installs needed
    python surge.py --list                 # show the patch library it found
    python surge.py --midi-programs        # fill Patches/MIDI Programs (bank map printed)
    python surge.py --osc-test             # load a random patch over OSC
"""

import argparse
import json
import os
import random
import re
import shutil
import socket
import struct
import sys
from pathlib import Path

HOME = Path.home()
FACTORY_CANDIDATES = [
    Path("/Library/Application Support/Surge XT"),
    HOME / "Library/Application Support/Surge XT",
    Path("/usr/share/surge-xt"), Path("/usr/local/share/surge-xt"),
    HOME / ".local/share/surge-xt",
    Path("C:/ProgramData/Surge XT"),
]
# Surge XT keeps a user folder in Documents by default, but it can live elsewhere (yours is in
# ~/Music/Surge XT), hold nested data folders, and SurgeXTUserDefaults.xml can redirect it.
USER_BASES = [HOME / "Documents" / "Surge XT", HOME / "Music" / "Surge XT"]
MIDI_PROGRAMS = "MIDI Programs"
MARKER = ".tensor_resonance_midi_programs.json"
OSC_PORT = 53280
_NOTED = set()


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]


def find_factory(factory=None):
    factory = factory or os.environ.get("SURGE_FACTORY_DIR")
    if factory:
        return Path(factory).expanduser()
    return next((p for p in FACTORY_CANDIDATES if (p / "patches_factory").is_dir()), None)


def user_dirs(user=None):
    """Every folder that could be Surge XT's active user folder, with the reason it was found.
    Surge only reads MIDI Programs from the ACTIVE one, so the filler writes to all of them."""
    user = user or os.environ.get("SURGE_USER_DIR")
    if user:
        return [(Path(user).expanduser(), "given")]
    found = []

    def add(p, why):
        p = Path(p).expanduser()
        if p.is_dir() and all(p.resolve() != q.resolve() for q, _ in found):
            found.append((p, why))

    for base in USER_BASES:
        if not base.is_dir():
            continue
        xml = base / "SurgeXTUserDefaults.xml"
        if xml.exists():
            m = re.search(r'key="userDataPath"\s+value="([^"]+)"', xml.read_text(errors="ignore"))
            if m:
                target = Path(m.group(1)).expanduser()
                if target.is_dir():
                    add(target, f"userDataPath in {xml}")
                elif str(target) not in _NOTED:
                    _NOTED.add(str(target))
                    print(f"  note: {xml} points Surge at {target}, which doesn't exist on this Mac")
        add(base, "standard location")
        for sub in sorted(base.iterdir(), key=lambda q: natural_key(q.name)):
            if sub.is_dir() and (sub / "Patches").is_dir() and (sub / "SurgePatches.db").exists():
                add(sub, f"data folder inside {base.name}")
    return found


def find_dirs(factory=None, user=None):
    """(factory folder, first user folder) — kept for callers that want one of each."""
    u = user_dirs(user)
    return find_factory(factory), (u[0][0] if u else USER_BASES[0])


def scan_library(factory=None, user=None, include_user=True):
    """[{id, label, source, patches:[{id, name, path}]}], path = absolute .fxp path."""
    fdir = find_factory(factory)
    udirs = user_dirs(user)
    roots = []
    if fdir:
        roots += [("Factory", fdir / "patches_factory"), ("3rd party", fdir / "patches_3rdparty")]
    if include_user:
        for u, _ in udirs:
            roots.append(("User" if len(udirs) == 1 else f"User ({u.name})", u / "Patches"))
    cats = []
    for source, root in roots:
        if not root.is_dir():
            continue
        groups = {}
        for fxp in root.rglob("*.fxp"):
            rel = fxp.parent.relative_to(root)
            if source.startswith("User") and rel.parts[:1] == (MIDI_PROGRAMS,):
                continue
            groups.setdefault(rel, []).append(fxp)
        for rel in sorted(groups, key=natural_key):
            label = f"{source} / " + (" / ".join(rel.parts) if rel.parts else "(top)")
            pats = sorted(groups[rel], key=lambda p: natural_key(p.stem))
            cats.append(dict(label=label, source=source,
                             patches=[dict(name=p.stem, path=str(p)) for p in pats]))
    for ci, c in enumerate(cats):
        c["id"] = ci
        for pi, p in enumerate(c["patches"]):
            p["id"] = f"{ci}:{pi}"
    return dict(factory=str(fdir) if fdir else None, user=str(udirs[0][0]) if udirs else None,
                user_dirs=[dict(path=str(u), why=w) for u, w in udirs], categories=cats,
                count=sum(len(c["patches"]) for c in cats))


# ----------------------------------------------------------------------------- OSC
def _pad(b):
    return b + b"\0" * (4 - len(b) % 4)


def osc_message(address, *args):
    tags, data = ",", b""
    for a in args:
        if isinstance(a, str):
            tags += "s"
            data += _pad(a.encode("utf-8"))
        elif isinstance(a, float):
            tags += "f"
            data += struct.pack(">f", a)
        else:
            tags += "i"
            data += struct.pack(">i", int(a))
    return _pad(address.encode()) + _pad(tags.encode()) + data


def osc_send(address, *args, host="127.0.0.1", port=OSC_PORT):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(osc_message(address, *args), (host, port))


def osc_load(fxp_path, host="127.0.0.1", port=OSC_PORT):
    """Surge wants the absolute path WITHOUT the .fxp extension."""
    p = str(fxp_path)
    if p.lower().endswith(".fxp"):
        p = p[:-4]
    osc_send("/patch/load", p, host=host, port=port)
    return p


# ----------------------------------------------------------------------------- MIDI Programs
def build_midi_programs(factory=None, user=None, with_3rdparty=False, mix=128, dry_run=False, log=print):
    """Copy factory patches into <user folder>/Patches/MIDI Programs so Bank Select + Program Change
    work. Written to EVERY candidate user folder (same banks in each), because Surge reads only the
    active one and that can be hard to tell from outside. Never deletes your own files."""
    lib = scan_library(factory, user, include_user=False)
    if not lib["factory"]:
        raise SystemExit("Surge XT factory patches not found. Pass --factory '<folder that contains patches_factory>'")
    cats = [c for c in lib["categories"] if c["source"] == "Factory" or (with_3rdparty and c["source"] == "3rd party")]
    targets = [u for u, _ in user_dirs(user)] or [USER_BASES[0]]
    banks = []
    pool = [p for c in cats for p in c["patches"]]
    rng = random.Random(int.from_bytes(os.urandom(8), "big"))
    banks.append(dict(bank=0, label="Mix (random factory picks)", folder="", patches=rng.sample(pool, min(mix, len(pool)))))
    for c in cats:
        name = c["label"].split(" / ", 1)[1].replace(" / ", " - ")
        for k in range(0, len(c["patches"]), 128):
            part = c["patches"][k:k + 128]
            label = name + (f" {k // 128 + 1}" if len(c["patches"]) > 128 else "")
            banks.append(dict(bank=len(banks), label=label, patches=part))
    banks = banks[:128]
    for b in banks[1:]:
        b["folder"] = f"{b['bank']:03d} {re.sub(r'[^A-Za-z0-9 ._-]+', '_', b['label'])}"
    dests = [t / "Patches" / MIDI_PROGRAMS for t in targets]
    if dry_run:
        return dests, banks
    bank_map = [dict(bank=b["bank"], label=b["label"], programs=[p["name"] for p in b["patches"]]) for b in banks]
    for dest in dests:
        marker = dest / MARKER
        if marker.exists():
            for f in json.loads(marker.read_text()).get("files", []):   # only what this script made
                try:
                    (dest / f).unlink()
                except FileNotFoundError:
                    pass
        elif dest.exists() and any(dest.rglob("*.fxp")):
            log(f"  note: {dest} already has your own .fxp files; they are kept and mix with the new banks.")
        created = []
        for b in banks:
            d = dest / b["folder"] if b["folder"] else dest
            d.mkdir(parents=True, exist_ok=True)
            for i, p in enumerate(b["patches"]):
                fname = f"{i:03d} {re.sub(r'[^A-Za-z0-9 ._-]+', '_', p['name'])}.fxp"
                shutil.copy2(p["path"], d / fname)
                created.append(str((d / fname).relative_to(dest)))
        marker.write_text(json.dumps(dict(files=created, banks=bank_map), indent=1))
    return dests, banks


def load_bank_map(user=None):
    for u, _ in user_dirs(user):
        m = u / "Patches" / MIDI_PROGRAMS / MARKER
        if m.exists():
            return json.loads(m.read_text()).get("banks")
    return None


# ----------------------------------------------------------------------------- tiny helper server
# `python3 surge.py --serve` (or ./surge.sh) needs nothing but Python itself: it serves surge.html
# and turns the page's clicks into OSC. The Tensor Resonance engine answers the same /api/surge/*
# requests, so the page works with either one running.
_STATE = dict(lib=None, host="127.0.0.1", port=OSC_PORT)


def api(action, q=None):
    """Shared request handler: library | osc | fill | map. Returns a JSON-able dict."""
    q = q or {}
    if action == "library":
        if _STATE["lib"] is None or q.get("refresh"):
            _STATE["lib"] = scan_library()
        return dict(_STATE["lib"], bank_map=load_bank_map(), host=_STATE["host"], port=_STATE["port"])
    if action == "map":
        return dict(banks=load_bank_map())
    if action == "osc":
        if q.get("port"):
            _STATE["port"] = int(q["port"])
        if q.get("host"):
            _STATE["host"] = str(q["host"]).strip()
        host, port, act = _STATE["host"], _STATE["port"], q.get("action", "random")
        try:
            if act == "load":
                if _STATE["lib"] is None:
                    _STATE["lib"] = scan_library()
                ci, pi = (int(x) for x in str(q["id"]).split(":"))
                p = _STATE["lib"]["categories"][ci]["patches"][pi]
                sent = osc_load(p["path"], host=host, port=port)
                return dict(ok=True, address="/patch/load", arg=sent, name=p["name"], to=f"{host}:{port}")
            if act not in ("random", "incr", "decr", "incr_category", "decr_category"):
                return dict(error=f"unknown action {act}")
            osc_send("/patch/" + act, host=host, port=port)
            return dict(ok=True, address="/patch/" + act, to=f"{host}:{port}")
        except Exception as e:
            return dict(error=f"{type(e).__name__}: {e}")
    if action == "fill":
        try:
            dests, banks = build_midi_programs(with_3rdparty=bool(q.get("with3rd")), log=lambda *x: None)
            return dict(ok=True, dests=[str(d) for d in dests],
                        banks=[dict(bank=b["bank"], label=b["label"], count=len(b["patches"])) for b in banks])
        except SystemExit as e:
            return dict(error=str(e))
        except Exception as e:
            return dict(error=f"{type(e).__name__}: {e}")
    return dict(error=f"unknown request {action}")


def serve(http_port=5057, open_browser=True, bind="127.0.0.1"):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlparse, parse_qs
    import webbrowser
    import threading
    here = Path(__file__).resolve().parent

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):
            self._send(204, b"", "text/plain")

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path in ("/", "/surge.html"):
                return self._send(200, (here / "surge.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/health":
                return self._send(200, dict(ok=True, surge_helper=True))
            if u.path == "/api/surge_map":
                return self._send(200, api("map"))
            if u.path.startswith("/api/surge/"):
                return self._send(200, api(u.path.rsplit("/", 1)[1], q))
            self._send(404, dict(error="not found"))

        def do_POST(self):
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            try:
                q = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                q = {}
            if u.path.startswith("/api/surge/"):
                return self._send(200, api(u.path.rsplit("/", 1)[1], q))
            self._send(404, dict(error="not found"))

    port = http_port
    for _ in range(20):
        try:
            srv = ThreadingHTTPServer((bind, port), H)
            break
        except OSError:
            port += 1
    else:
        raise SystemExit("no free port")
    url = f"http://127.0.0.1:{port}/"
    lib = api("library")
    print("\n  SURGE XT PATCH CHANGER")
    print(f"  factory patches: {lib['factory'] or 'NOT FOUND (pass --factory)'}  ({lib['count']} patches)")
    print(f"  OSC -> {_STATE['host']}:{_STATE['port']}  (switch on OSC input in Surge XT; port 53280)")
    print(f"  open  -> {url}")
    print("  stop  -> Ctrl+C (in Spyder: the red square / Interrupt kernel)\n")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("  stopped")
    finally:
        srv.server_close()


def main():
    ap = argparse.ArgumentParser(description="Surge XT patch changing (OSC + MIDI Programs folder)")
    ap.add_argument("--factory", help="folder containing patches_factory (auto-detected)")
    ap.add_argument("--user", help="Surge XT user folder (default ~/Documents/Surge XT)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--midi-programs", action="store_true", help="fill Patches/MIDI Programs from the factory library")
    ap.add_argument("--with-3rdparty", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--osc-test", action="store_true", help="load a random factory patch over OSC")
    ap.add_argument("--port", type=int, default=OSC_PORT, help="Surge's OSC input port (53280)")
    ap.add_argument("--host", default="127.0.0.1", help="computer running Surge (an IP for another Mac)")
    ap.add_argument("--serve", action="store_true", help="run the patch-changer page (no installs needed)")
    ap.add_argument("--http-port", type=int, default=5057)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    _STATE.update(host=a.host, port=a.port)
    if a.serve or not any((a.list, a.midi_programs, a.osc_test)):
        return serve(a.http_port, not a.no_browser)   # plain `python3 surge.py` or Spyder's Run
    if a.list:
        lib = scan_library(a.factory, a.user)
        print(f"factory: {lib['factory']}\n{lib['count']} patches")
        for u in lib["user_dirs"]:
            print(f"user folder: {u['path']}   ({u['why']})")
        for c in lib["categories"]:
            print(f"  {len(c['patches']):4d}  {c['label']}")
    elif a.midi_programs:
        dests, banks = build_midi_programs(a.factory, a.user, a.with_3rdparty, dry_run=a.dry_run)
        for dest in dests:
            print(f"{'would fill' if a.dry_run else 'filled'}: {dest}")
        print("  bank (CC0)  programs  contents")
        for b in banks:
            print(f"  {b['bank']:>4}        {len(b['patches']):>4}     {b['label']}")
        print("Restart Surge XT (standalone) so it rescans. Then CC0 = bank, Program Change = patch.")
        print("Check: Surge's patch browser should now show a 'MIDI Programs' category full of patches.")
    elif a.osc_test:
        lib = scan_library(a.factory, a.user, include_user=False)
        pats = [p for c in lib["categories"] for p in c["patches"]]
        if not pats:
            raise SystemExit("no factory patches found")
        p = random.choice(pats)
        print("OSC /patch/load", osc_load(p["path"], host=a.host, port=a.port), f"-> {a.host}:{a.port}")
        print("Nothing changed? In Surge XT, switch OSC input on (port", a.port, ").")


if __name__ == "__main__":
    main()
