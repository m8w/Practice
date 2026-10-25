#!/usr/bin/env python3
"""
Dispatch Replay: makes finished recordings playable and puts them back on the air.

  * Watches your recording folders. A file counts as finished once it has stopped growing
    for a while (OBS has closed it, or the copy or upload into the folder is done).
  * Serves a control page at http://localhost:8765 where you browse, play back, queue and air
    recordings, and drop in new files.
  * Airs the queue one of two ways:
      OBS mode     scripts OBS through its WebSocket server: a "Dispatch Replay" scene with a
                   media source, and a Days on End backdrop behind audio-only recordings.
      Direct mode  its own ffmpeg broadcaster to one or more RTMP URLs (and/or a file), that
                   reconnects every 11 h 45 min so each archive stays under 12 hours.

Python 3.9+ standard library only. Needs ffmpeg and ffprobe.
Run:  python3 dispatch_replay.py            (or double-click "Dispatch Replay.command")
      python3 dispatch_replay.py --check    (show what it found and exit)
"""

import argparse
import base64
import collections
import concurrent.futures
import hashlib
import json
import os
import queue
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = '1.0'
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / 'replay_data'
PAGE_FILE = APP_DIR / 'replay_page.html'
BACKDROP_FILE = APP_DIR / 'Days On End.html'

VIDEO_EXT = {'.mp4', '.m4v', '.mov', '.mkv', '.webm', '.flv', '.ts', '.mts', '.m2ts', '.avi', '.mpg', '.mpeg'}
AUDIO_EXT = {'.wav', '.rf64', '.w64', '.aif', '.aiff', '.aifc', '.flac', '.mp3', '.m4a', '.aac', '.ogg', '.oga', '.opus', '.caf'}
BROWSER_EXT = {'.mp4', '.m4v', '.mov', '.webm', '.mp3', '.m4a', '.aac', '.wav', '.ogg', '.oga', '.opus', '.flac'}
MIME = {
    '.mp4': 'video/mp4', '.m4v': 'video/mp4', '.mov': 'video/mp4', '.webm': 'video/webm',
    '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.aac': 'audio/aac', '.wav': 'audio/wav',
    '.ogg': 'audio/ogg', '.oga': 'audio/ogg', '.opus': 'audio/ogg', '.flac': 'audio/flac',
}
SKIP_DIRS = ('.imovielibrary', '.photoslibrary', '.fcpbundle', '.app', '.theater', '.logicx', '.band')

CYCLE_DEFAULT = '11:45'

DEFAULTS = {
    'folders': [str(Path.home() / 'Movies')],
    'inbox': '',                 # where uploads land; empty = "Replay inbox" inside the first folder
    'port': 8765,
    'allow_lan': False,          # let other computers on your network open the page (needs the code)
    'lan_code': '',
    'settle_seconds': 90,        # a file must stop changing this long before it counts as finished
    'scan_every': 30,
    'min_seconds': 5,            # shorter files are listed but never auto-picked
    'mode': 'obs',               # 'obs' or 'direct'
    'resume_on_launch': True,    # go back on the air after a restart if it was on the air before
    'auto_fill': True,           # when the queue runs dry, pick a finished recording at random
    'fill_from': 'long',         # any | long (1 h+) | video | audio | clips (under 10 min)
    'auto_queue_new': True,      # newly finished recordings join the end of the queue
    'avoid_repeat_hours': 48,
    'ffmpeg': '',                # leave empty to find it automatically
    'ffprobe': '',
    'obs': {
        'host': '127.0.0.1', 'port': 4455, 'password': '',
        'scene': 'Dispatch Replay', 'media': 'Replay Media', 'backdrop': 'Replay Backdrop',
        'backdrop_on': True, 'backdrop_always': False, 'backdrop_url': '',
        'take_program': True, 'restore_scene': True,
    },
    'direct': {
        'outputs': [],           # RTMP URLs including the stream key, one per entry
        'record_folder': '',     # also save what airs, one file per cycle
        'width': 1920, 'height': 1080, 'fps': 30,
        'video_kbps': 6000, 'audio_kbps': 192,
        'encoder': 'auto',       # auto | h264_videotoolbox | libx264
        'audio_visual': 'cqt',   # how audio-only recordings look: cqt | scope | waves | loop | still
        'visual_file': '',       # a video to loop or an image, for 'loop' / 'still'
        'cycle': CYCLE_DEFAULT,  # reconnect every 11 h 45 min (new YouTube archive); 'off' to never
        'cycle_gap': 20,         # seconds off the air between cycles
        'filler': True,          # keep the stream alive with generated visuals when nothing is queued
    },
}

STOP = threading.Event()


# ---------------------------------------------------------------- helpers

def urand():
    """Uniform [0, 1) from the OS entropy pool."""
    return int.from_bytes(os.urandom(7), 'big') / float(1 << 56)


_log_lock = threading.Lock()


def log(*parts):
    line = time.strftime('%Y-%m-%d %H:%M:%S  ') + ' '.join(str(p) for p in parts)
    print(line, flush=True)
    with _log_lock:
        try:
            lf = DATA_DIR / 'replay.log'
            if lf.exists() and lf.stat().st_size > 5 * 1024 * 1024:
                lf.replace(DATA_DIR / 'replay.log.1')
            with open(lf, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except OSError:
            pass


def atomic_write_json(path, obj):
    tmp = Path(str(path) + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def read_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def parse_duration(v):
    """'11:45' -> 42300, '45m' -> 2700, '30s' / '30' -> 30, 'off' -> 0."""
    if v is None:
        return 0
    s = str(v).strip().lower()
    if s in ('', 'off', 'none', '0'):
        return 0
    m = re.match(r'^(\d+):(\d{1,2})$', s)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60
    m = re.match(r'^(\d+(?:\.\d+)?)\s*(h|m|s)?$', s)
    if m:
        return float(m.group(1)) * {'h': 3600, 'm': 60, 's': 1}[m.group(2) or 's']
    return 0


def fmt_dur(s):
    s = int(max(0, s or 0))
    return f'{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}'


def entry_id(path):
    return hashlib.sha1(path.encode('utf-8', 'surrogateescape')).hexdigest()[:12]


def kill_proc(p, wait=3):
    if not p or p.poll() is not None:
        return
    try:
        p.terminate()
        p.wait(wait)
    except Exception:
        try:
            p.kill()
            p.wait(2)
        except Exception:
            pass


def drain(stream, sink):
    """Read a child's stderr into a bounded deque so its pipe never fills up."""
    try:
        for raw in iter(stream.readline, b''):
            line = raw.decode('utf-8', 'replace').rstrip()
            if line:
                sink.append(line)
    except Exception:
        pass


# ---------------------------------------------------------------- config

class Config:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.data = deep_merge(DEFAULTS, read_json(path, {}))
        if not self.data.get('lan_code'):
            self.data['lan_code'] = ''.join(str(b % 10) for b in os.urandom(6))
        self.save()

    def get(self):
        with self.lock:
            return json.loads(json.dumps(self.data))

    def save(self):
        with self.lock:
            atomic_write_json(self.path, self.data)

    def update(self, patch):
        with self.lock:
            self.data = deep_merge(self.data, patch)
            self.save()
            return self.get()


# ---------------------------------------------------------------- tools (ffmpeg)

class Tools:
    CANDIDATE_DIRS = ['/opt/homebrew/bin', '/usr/local/bin', '/opt/local/bin',
                      str(Path.home() / 'miniforge3/bin'), str(Path.home() / 'anaconda3/bin'),
                      str(Path.home() / 'opt/anaconda3/bin'), str(Path.home() / '.local/bin'), '/usr/bin']

    def __init__(self, cfg):
        c = cfg.get()
        self.ffmpeg = self.find(c.get('ffmpeg'), 'ffmpeg')
        self.ffprobe = self.find(c.get('ffprobe'), 'ffprobe')
        self.encoders = set()
        self.filters = set()
        self.h264 = None
        self.burst_opt = False
        self.lsof = shutil.which('lsof')
        self.caffeinate = shutil.which('caffeinate')
        if self.ffmpeg:
            self.encoders = self._list(['-encoders'])
            self.filters = self._list(['-filters'])
            self.h264 = self._pick_h264()
            try:
                helptext = subprocess.run([self.ffmpeg, '-hide_banner', '-h', 'full'], capture_output=True, text=True, timeout=20).stdout
                self.burst_opt = 'readrate_initial_burst' in helptext
            except Exception:
                self.burst_opt = False

    def re(self):
        """Read an input at its native rate, with no catch-up burst at the start."""
        return ['-re'] + (['-readrate_initial_burst', '0'] if self.burst_opt else [])

    def find(self, override, name):
        if override and os.access(override, os.X_OK):
            return override
        p = shutil.which(name)
        if p:
            return p
        for d in self.CANDIDATE_DIRS:
            c = Path(d) / name
            if c.exists() and os.access(c, os.X_OK):
                return str(c)
        return None

    def _list(self, flag):
        try:
            out = subprocess.run([self.ffmpeg, '-hide_banner'] + flag, capture_output=True, text=True, timeout=20).stdout
        except Exception:
            return set()
        names = set()
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and re.match(r'^[A-Z.]{3,6}$', parts[0]):
                names.add(parts[1])
        return names

    def _works(self, enc):
        try:
            r = subprocess.run([self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                                'testsrc=s=320x240:r=30:d=0.5', '-c:v', enc, '-f', 'null', '-'],
                               capture_output=True, timeout=30)
            return r.returncode == 0
        except Exception:
            return False

    def _pick_h264(self):
        for enc in ('h264_videotoolbox', 'libx264'):
            if enc in self.encoders and self._works(enc):
                return enc
        return None

    def video_args(self, enc_pref, kbps, fps):
        enc = enc_pref if enc_pref in ('h264_videotoolbox', 'libx264') and enc_pref in self.encoders else self.h264
        g = str(int(fps) * 2)
        if enc == 'h264_videotoolbox':
            return enc, ['-c:v', enc, '-b:v', f'{kbps}k', '-profile:v', 'high', '-g', g,
                         '-realtime', '1', '-allow_sw', '1', '-pix_fmt', 'yuv420p']
        if enc == 'libx264':
            return enc, ['-c:v', enc, '-preset', 'veryfast', '-b:v', f'{kbps}k', '-maxrate', f'{kbps}k',
                         '-bufsize', f'{kbps * 2}k', '-g', g, '-keyint_min', g, '-sc_threshold', '0',
                         '-profile:v', 'high', '-pix_fmt', 'yuv420p']
        return None, []

    def summary(self):
        return {'ffmpeg': self.ffmpeg, 'ffprobe': self.ffprobe, 'h264': self.h264,
                'ok': bool(self.ffmpeg and self.ffprobe)}


def ffprobe(tools, path, timeout=90):
    r = subprocess.run([tools.ffprobe, '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', path],
                       capture_output=True, timeout=timeout)
    if r.returncode != 0:
        msg = r.stderr.decode('utf-8', 'replace').strip().splitlines()
        raise RuntimeError((msg[-1] if msg else 'ffprobe could not read it')[:200])
    j = json.loads(r.stdout or b'{}')
    fmt = j.get('format') or {}
    streams = j.get('streams') or []
    v = next((s for s in streams if s.get('codec_type') == 'video'
              and not (s.get('disposition') or {}).get('attached_pic')), None)
    a = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    if not v and not a:
        raise RuntimeError('no audio or video in it')
    try:
        dur = float(fmt.get('duration') or 0)
    except ValueError:
        dur = 0.0
    if not dur:
        for s in streams:
            try:
                dur = max(dur, float(s.get('duration') or 0))
            except ValueError:
                pass
    fps = 0.0
    if v and v.get('avg_frame_rate') and '/' in v['avg_frame_rate']:
        n, d = v['avg_frame_rate'].split('/')
        fps = float(n) / float(d) if float(d) else 0.0
    return {
        'duration': round(dur, 3), 'kind': 'video' if v else 'audio',
        'vcodec': v.get('codec_name') if v else None, 'pix_fmt': v.get('pix_fmt') if v else None,
        'width': v.get('width') if v else None, 'height': v.get('height') if v else None,
        'fps': round(fps, 3), 'acodec': a.get('codec_name') if a else None,
        'channels': a.get('channels') if a else None,
        'container': fmt.get('format_name'),
    }


def browser_playable(e):
    ext = Path(e['path']).suffix.lower()
    if ext not in BROWSER_EXT:
        return False
    if e.get('kind') == 'video':
        return (e.get('vcodec') in ('h264', 'vp8', 'vp9', 'av1')
                and e.get('acodec') in (None, 'aac', 'mp3', 'opus', 'vorbis')
                and e.get('pix_fmt') in (None, 'yuv420p', 'yuvj420p'))
    if ext == '.wav' and e.get('size', 0) >= 4 * 1024 ** 3:
        return False
    return e.get('acodec') in ('aac', 'mp3', 'opus', 'vorbis', 'flac', 'pcm_s16le', 'pcm_s24le', 'pcm_f32le', 'pcm_u8')


# ---------------------------------------------------------------- library

class Library:
    """Finds recordings, waits until each one has finished landing on disk, then probes it."""

    def __init__(self, cfg, tools):
        self.cfg = cfg
        self.tools = tools
        self.lock = threading.RLock()
        self.entries = {}
        self.watch = {}
        self.new_ids = collections.deque(maxlen=40)
        self.on_new = []
        self.scanning = False
        self.passes = 0
        self.last_scan = 0
        self.probing = 0
        self.missing = []
        self.dirty = False
        self.wake = threading.Event()
        self.thumb_sem = threading.Semaphore(2)
        for e in read_json(DATA_DIR / 'index.json', []):
            if isinstance(e, dict) and e.get('path'):
                self.entries[e['id']] = e

    def start(self):
        threading.Thread(target=self.run, name='library', daemon=True).start()

    def run(self):
        while not STOP.is_set():
            try:
                self.scan()
            except Exception:
                log('scan error:', traceback.format_exc())
            wait = 4 if self.passes < 2 else max(5, int(self.cfg.get()['scan_every']))
            self.wake.wait(wait)
            self.wake.clear()

    def roots(self):
        out = []
        for f in self.cfg.get()['folders']:
            p = Path(os.path.expanduser(str(f).strip()))
            if str(f).strip():
                out.append(p)
        return out

    def scan(self):
        cfg = self.cfg.get()
        settle = max(5, float(cfg['settle_seconds']))
        now = time.time()
        first = self.passes == 0
        self.scanning = True
        seen = set()
        missing, live_roots = [], []
        for root in self.roots():
            if not root.is_dir():
                missing.append(str(root))
                continue
            live_roots.append(str(root))
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith('.') and not d.endswith(SKIP_DIRS)
                               and Path(dirpath, d) != DATA_DIR]
                for fn in filenames:
                    if fn.startswith('.') or fn.endswith('.part'):
                        continue
                    ext = os.path.splitext(fn)[1].lower()
                    if ext not in VIDEO_EXT and ext not in AUDIO_EXT:
                        continue
                    p = os.path.join(dirpath, fn)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    seen.add(p)
                    self._observe(p, st, now, first)
        self.missing = missing
        with self.lock:
            # Entries under a folder that is currently unreachable (unplugged drive) stay, marked offline.
            for eid, e in list(self.entries.items()):
                under_live = any(e['path'].startswith(r.rstrip('/') + os.sep) for r in live_roots)
                under_any = any(e['path'].startswith(str(r).rstrip('/') + os.sep) for r in self.roots())
                if not under_any:
                    del self.entries[eid]
                    self.dirty = True
                elif under_live and e['path'] not in seen:
                    del self.entries[eid]
                    self.dirty = True
                else:
                    off = not under_live
                    if e.get('offline') != off:
                        e['offline'] = off
                        self.dirty = True
            for p in list(self.watch):
                if p not in seen:
                    del self.watch[p]
        self._probe_ready(now, settle, first)
        self.passes += 1
        self.last_scan = time.time()
        self.scanning = False
        if self.dirty:
            self.save()

    def _observe(self, p, st, now, first):
        sig = (st.st_size, int(st.st_mtime))
        with self.lock:
            eid = entry_id(p)
            e = self.entries.get(eid)
            if e and e.get('ready') and (e.get('size'), int(e.get('mtime', 0))) == sig:
                return
            w = self.watch.get(p)
            if w is None:
                # Files already sitting there at startup count from their modified time; files that
                # appear later count from now, because copies keep the original modified time.
                since = st.st_mtime if first else now
                self.watch[p] = {'sig': sig, 'since': min(since, now), 'checks': 0, 'fail_at': 0, 'initial': first}
                if e and e.get('ready'):
                    e['ready'] = False
                    self.dirty = True
            elif w['sig'] != sig:
                w['sig'] = sig
                w['since'] = now
                w['checks'] = 0
                w['initial'] = False
                if e and e.get('ready'):
                    e['ready'] = False
                    self.dirty = True
            else:
                w['checks'] += 1

    def _probe_ready(self, now, settle, first):
        todo = []
        with self.lock:
            for p, w in self.watch.items():
                if w['checks'] < 1:
                    continue  # must be seen unchanged on two passes
                if now - w['since'] < settle:
                    continue
                if w['fail_at'] and now - w['fail_at'] < 600:
                    continue
                e = self.entries.get(entry_id(p))
                if e and e.get('ready'):
                    continue
                todo.append(p)
        if not todo:
            return
        self.probing = len(todo)

        def work(p):
            try:
                w = self.watch.get(p)
                if not w:
                    return
                recent = now - w['since'] < 3600
                if recent and self.tools.lsof and self._open_elsewhere(p):
                    with self.lock:
                        w['since'] = time.time()  # still open (a paused recording): wait again
                    return
                info = ffprobe(self.tools, p)
                st = os.stat(p)
                with self.lock:
                    eid = entry_id(p)
                    old = self.entries.get(eid) or {}
                    e = dict(info)
                    e.update({
                        'id': eid, 'path': p, 'name': os.path.basename(p), 'folder': os.path.dirname(p),
                        'size': st.st_size, 'mtime': st.st_mtime, 'ready': True, 'offline': False, 'error': None,
                        'first_seen': old.get('first_seen') or w['since'],
                        'ready_at': time.time() if not old.get('ready_at') or old.get('size') != st.st_size else old['ready_at'],
                    })
                    e['playable'] = 'direct' if browser_playable(e) else 'stream'
                    is_new = not w.get('initial') and (not old or old.get('size') != st.st_size)
                    self.entries[eid] = e
                    self.dirty = True
                    if is_new:
                        self.new_ids.appendleft(eid)
                if is_new:
                    log('finished and ready:', p)
                    for cb in self.on_new:
                        try:
                            cb(eid)
                        except Exception:
                            log('on_new error:', traceback.format_exc())
            except Exception as ex:
                with self.lock:
                    w = self.watch.get(p)
                    if w:
                        w['fail_at'] = time.time()
                    eid = entry_id(p)
                    old = self.entries.get(eid)
                    if old:
                        old['error'] = str(ex)
                        old['ready'] = False
                log('could not read', p, '-', ex)
            finally:
                self.probing = max(0, self.probing - 1)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(work, todo))
        self.probing = 0

    def _open_elsewhere(self, p):
        try:
            r = subprocess.run([self.tools.lsof, '-t', '--', p], capture_output=True, text=True, timeout=8)
            pids = [x for x in r.stdout.split() if x.strip() and x.strip() != str(os.getpid())]
            return bool(pids)
        except Exception:
            return False

    def mark_complete(self, p):
        """An upload we received ourselves is known to be complete: skip the waiting period."""
        with self.lock:
            try:
                st = os.stat(p)
            except OSError:
                return
            self.watch[p] = {'sig': (st.st_size, int(st.st_mtime)), 'since': 0, 'checks': 1, 'fail_at': 0, 'initial': False}
        self.wake.set()

    def save(self):
        with self.lock:
            data = list(self.entries.values())
            self.dirty = False
        try:
            atomic_write_json(DATA_DIR / 'index.json', data)
        except OSError as ex:
            log('could not save index:', ex)

    def get(self, eid):
        with self.lock:
            e = self.entries.get(eid)
            return dict(e) if e else None

    def ready_entries(self):
        with self.lock:
            return [dict(e) for e in self.entries.values() if e.get('ready') and not e.get('offline')]

    def pending(self):
        with self.lock:
            return sum(1 for p in self.watch if not (self.entries.get(entry_id(p)) or {}).get('ready'))

    def stats(self):
        with self.lock:
            total = len(self.entries)
            ready = sum(1 for e in self.entries.values() if e.get('ready'))
            offline = sum(1 for e in self.entries.values() if e.get('offline'))
            secs = sum(e.get('duration') or 0 for e in self.entries.values() if e.get('ready'))
        return {'count': total, 'ready': ready, 'offline': offline, 'waiting': self.pending(),
                'probing': self.probing, 'scanning': self.scanning, 'last_scan': self.last_scan,
                'missing_folders': self.missing, 'hours': round(secs / 3600, 1), 'seconds': round(secs)}

    def query(self, q='', filt='all', sort='newest', offset=0, limit=100):
        q = (q or '').lower().strip()
        with self.lock:
            items = [dict(e) for e in self.entries.values()]
        new = set(self.new_ids)

        def keep(e):
            if q and q not in e['name'].lower() and q not in e['folder'].lower():
                return False
            d = e.get('duration') or 0
            if filt == 'video':
                return e.get('kind') == 'video'
            if filt == 'audio':
                return e.get('kind') == 'audio'
            if filt == 'long':
                return d >= 3600
            if filt == 'clips':
                return 0 < d < 600
            if filt == 'new':
                return e['id'] in new
            if filt == 'waiting':
                return not e.get('ready')
            return True

        items = [e for e in items if keep(e)]
        keyf = {
            'newest': (lambda e: -(e.get('mtime') or 0)), 'oldest': (lambda e: e.get('mtime') or 0),
            'longest': (lambda e: -(e.get('duration') or 0)), 'name': (lambda e: e['name'].lower()),
        }.get(sort, lambda e: -(e.get('mtime') or 0))
        items.sort(key=keyf)
        total = len(items)
        page = items[offset:offset + limit]
        for e in page:
            e['is_new'] = e['id'] in new
        return {'total': total, 'items': [summary(e) for e in page]}

    def thumb(self, eid):
        e = self.get(eid)
        if not e or not self.tools.ffmpeg:
            return None
        out = DATA_DIR / 'thumbs' / f'{eid}.jpg'
        if out.exists() and out.stat().st_mtime >= (e.get('mtime') or 0):
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        with self.thumb_sem:
            if out.exists() and out.stat().st_mtime >= (e.get('mtime') or 0):
                return out
            if e.get('kind') == 'video':
                at = min(60.0, (e.get('duration') or 0) * 0.2)
                cmd = [self.tools.ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{at:.2f}', '-i', e['path'],
                       '-frames:v', '1', '-vf', 'scale=320:-2', '-q:v', '5', str(out)]
            else:
                cmd = [self.tools.ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-t', '120', '-i', e['path'],
                       '-filter_complex', 'aformat=channel_layouts=mono,showwavespic=s=320x180:colors=0xf2b45a',
                       '-frames:v', '1', str(out)]
            try:
                subprocess.run(cmd, capture_output=True, timeout=60)
            except Exception:
                return None
        return out if out.exists() else None


def summary(e):
    keys = ('id', 'name', 'folder', 'path', 'duration', 'kind', 'size', 'mtime', 'ready', 'offline', 'error',
            'vcodec', 'acodec', 'width', 'height', 'playable', 'ready_at', 'is_new')
    return {k: e.get(k) for k in keys}


# ---------------------------------------------------------------- obs-websocket (v5) client, standard library only

class WebSocket:
    GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

    def __init__(self, host, port, subprotocol=None, timeout=5):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        lines = [f'GET / HTTP/1.1', f'Host: {host}:{port}', 'Upgrade: websocket', 'Connection: Upgrade',
                 f'Sec-WebSocket-Key: {key}', 'Sec-WebSocket-Version: 13']
        if subprotocol:
            lines.append(f'Sec-WebSocket-Protocol: {subprotocol}')
        self.sock.sendall(('\r\n'.join(lines) + '\r\n\r\n').encode())
        buf = b''
        while b'\r\n\r\n' not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError('closed during handshake')
            buf += chunk
            if len(buf) > 65536:
                raise ConnectionError('bad handshake')
        head, self.buf = buf.split(b'\r\n\r\n', 1)
        status = head.split(b'\r\n', 1)[0]
        if b' 101 ' not in status + b' ':
            raise ConnectionError('not a WebSocket server: ' + status.decode('latin-1'))
        want = base64.b64encode(hashlib.sha1((key + self.GUID).encode()).digest()).decode()
        if want.encode() not in head:
            raise ConnectionError('WebSocket accept key mismatch')
        self.sock.settimeout(None)
        self.wlock = threading.Lock()
        self.closed = False

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self.buf)))
            if not chunk:
                raise ConnectionError('connection closed')
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send(self, opcode, payload):
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack('!H', n)
        else:
            head += bytes([0x80 | 127]) + struct.pack('!Q', n)
        mask = os.urandom(4)
        if n:
            m = (mask * (n // 4 + 1))[:n]
            masked = (int.from_bytes(payload, 'big') ^ int.from_bytes(m, 'big')).to_bytes(n, 'big')
        else:
            masked = b''
        with self.wlock:
            self.sock.sendall(head + mask + masked)

    def send_text(self, s):
        self._send(0x1, s.encode('utf-8'))

    def recv_text(self):
        parts, op0 = [], None
        while True:
            b1, b2 = self._read(2)
            fin, op = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack('!H', self._read(2))[0]
            elif n == 127:
                n = struct.unpack('!Q', self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
            if op == 0x8:
                self.closed = True
                code = struct.unpack('!H', data[:2])[0] if len(data) >= 2 else 1000
                raise ConnectionError(f'closed by OBS ({code} {data[2:].decode("utf-8", "replace")})')
            if op == 0x9:
                self._send(0xA, data)
                continue
            if op == 0xA:
                continue
            if op in (0x1, 0x2):
                op0, parts = op, [data]
            elif op == 0x0:
                parts.append(data)
            if fin and op0 is not None:
                return b''.join(parts).decode('utf-8')

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self._send(0x8, struct.pack('!H', 1000))
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


class ObsError(Exception):
    pass


class ObsClient:
    EVENTS = 4 | 64 | 256  # Scenes, Outputs, MediaInputs

    def __init__(self, host, port, password, on_event):
        self.host, self.port, self.password = host, int(port), password or ''
        self.on_event = on_event
        self.ws = None
        self.alive = False
        self.pending = {}
        self.plock = threading.Lock()
        self.n = 0
        self.version = ''

    def connect(self):
        self.ws = WebSocket(self.host, self.port, 'obswebsocket.json')
        self.ws.sock.settimeout(8)
        hello = json.loads(self.ws.recv_text())
        if hello.get('op') != 0:
            raise ObsError('unexpected greeting from OBS')
        d = hello.get('d') or {}
        self.version = d.get('obsWebSocketVersion', '')
        ident = {'rpcVersion': 1, 'eventSubscriptions': self.EVENTS}
        auth = d.get('authentication')
        if auth:
            if not self.password:
                raise ObsError('OBS asks for a WebSocket password; add it in Settings')
            secret = base64.b64encode(hashlib.sha256((self.password + auth['salt']).encode()).digest()).decode()
            ident['authentication'] = base64.b64encode(hashlib.sha256((secret + auth['challenge']).encode()).digest()).decode()
        self.ws.send_text(json.dumps({'op': 1, 'd': ident}))
        try:
            ok = json.loads(self.ws.recv_text())
        except ConnectionError as ex:
            raise ObsError('OBS refused the connection; check the WebSocket password') from ex
        if ok.get('op') != 2:
            raise ObsError('OBS did not accept the connection')
        self.ws.sock.settimeout(None)
        self.alive = True
        threading.Thread(target=self._reader, name='obs-reader', daemon=True).start()

    def _reader(self):
        try:
            while self.alive:
                msg = json.loads(self.ws.recv_text())
                op, d = msg.get('op'), msg.get('d') or {}
                if op == 7:
                    with self.plock:
                        slot = self.pending.get(d.get('requestId'))
                    if slot:
                        slot[1] = d
                        slot[0].set()
                elif op == 5:
                    try:
                        self.on_event(d.get('eventType'), d.get('eventData') or {})
                    except Exception:
                        log('OBS event handler error:', traceback.format_exc())
        except Exception as ex:
            if self.alive:
                log('OBS connection ended:', ex)
        finally:
            self.alive = False
            with self.plock:
                for slot in self.pending.values():
                    slot[0].set()

    def call(self, rtype, data=None, timeout=8):
        if not self.alive:
            raise ObsError('not connected to OBS')
        with self.plock:
            self.n += 1
            rid = f'r{self.n}'
            slot = [threading.Event(), None]
            self.pending[rid] = slot
        try:
            msg = {'op': 6, 'd': {'requestType': rtype, 'requestId': rid}}
            if data is not None:
                msg['d']['requestData'] = data
            self.ws.send_text(json.dumps(msg))
            if not slot[0].wait(timeout) or slot[1] is None:
                raise ObsError(f'{rtype}: no answer from OBS')
            st = slot[1].get('requestStatus') or {}
            if not st.get('result'):
                raise ObsError(f"{rtype}: {st.get('comment') or 'failed'} (code {st.get('code')})")
            return slot[1].get('responseData') or {}
        finally:
            with self.plock:
                self.pending.pop(rid, None)

    def close(self):
        self.alive = False
        if self.ws:
            self.ws.close()


# ---------------------------------------------------------------- backends

class Backend:
    mode = ''

    def __init__(self, cfg, tools):
        self.cfg, self.tools = cfg, tools
        self.state = 'idle'      # idle | starting | playing | ended | failed | offline | gap | filling
        self.message = ''
        self.entry = None
        self.start_at = 0.0

    def info(self):
        return {}


class ObsBackend(Backend):
    """Scripts OBS: a replay scene holding a media source over an optional browser-source backdrop."""
    mode = 'obs'

    def __init__(self, cfg, tools, backdrop_url):
        super().__init__(cfg, tools)
        self.client = None
        self.next_try = 0
        self.ended_flag = False
        self.cursor = None
        self.duration = None
        self.played_at = 0
        self.last_status = 0
        self.pending_seek = None
        self.saved_scene = None
        self.items = {}
        self.backdrop_url = backdrop_url
        self.message = 'Connecting to OBS'
        self.streaming = None

    def _o(self):
        return self.cfg.get()['obs']

    def _event(self, etype, data):
        o = self._o()
        if etype == 'MediaInputPlaybackEnded' and data.get('inputName') == o['media']:
            self.ended_flag = True
        elif etype == 'StreamStateChanged':
            self.streaming = data.get('outputActive')
            log('OBS stream', 'started' if self.streaming else 'stopped')

    def _connect(self):
        o = self._o()
        self.client = ObsClient(o['host'], o['port'], o['password'], self._event)
        self.client.connect()
        self._setup()
        try:
            self.streaming = self.client.call('GetStreamStatus').get('outputActive')
        except ObsError:
            self.streaming = None
        log(f'connected to OBS (obs-websocket {self.client.version})')

    def _setup(self):
        o, c = self._o(), self.client
        scene, media, bd = o['scene'], o['media'], o['backdrop']
        scenes = [s.get('sceneName') for s in c.call('GetSceneList').get('scenes', [])]
        if scene not in scenes:
            c.call('CreateScene', {'sceneName': scene})
            log('made OBS scene', scene)
        vs = c.call('GetVideoSettings')
        W, H = int(vs.get('baseWidth') or 1920), int(vs.get('baseHeight') or 1080)
        inputs = {i.get('inputName'): i.get('inputKind') for i in c.call('GetInputList').get('inputs', [])}

        def items():
            return {i.get('sourceName'): i.get('sceneItemId')
                    for i in c.call('GetSceneItemList', {'sceneName': scene}).get('sceneItems', [])}

        have = items()
        if o.get('backdrop_on') and self.backdrop_url:
            bset = {'url': self.backdrop_url, 'is_local_file': False, 'width': W, 'height': H,
                    'reroute_audio': True, 'shutdown': False, 'restart_when_active': False, 'fps_custom': False}
            if bd not in inputs:
                c.call('CreateInput', {'sceneName': scene, 'inputName': bd, 'inputKind': 'browser_source',
                                       'inputSettings': bset, 'sceneItemEnabled': False})
            else:
                c.call('SetInputSettings', {'inputName': bd, 'inputSettings': bset, 'overlay': True})
                if bd not in have:
                    c.call('CreateSceneItem', {'sceneName': scene, 'sourceName': bd, 'sceneItemEnabled': False})
            try:
                c.call('SetInputMute', {'inputName': bd, 'inputMuted': True})
            except ObsError:
                pass
        mset = {'is_local_file': True, 'looping': False, 'restart_on_activate': False,
                'close_when_inactive': False, 'clear_on_media_end': True, 'hw_decode': True}
        if media not in inputs:
            c.call('CreateInput', {'sceneName': scene, 'inputName': media, 'inputKind': 'ffmpeg_source',
                                   'inputSettings': mset, 'sceneItemEnabled': True})
        elif media not in have:
            c.call('CreateSceneItem', {'sceneName': scene, 'sourceName': media, 'sceneItemEnabled': True})
        self.items = items()
        fit = {'boundsType': 'OBS_BOUNDS_SCALE_INNER', 'boundsWidth': W, 'boundsHeight': H,
               'boundsAlignment': 0, 'alignment': 5, 'positionX': 0, 'positionY': 0}
        for name in (bd, media):
            if name in self.items:
                try:
                    c.call('SetSceneItemTransform', {'sceneName': scene, 'sceneItemId': self.items[name],
                                                     'sceneItemTransform': fit})
                except ObsError as ex:
                    log('could not fit', name, ex)
        try:
            if bd in self.items:
                c.call('SetSceneItemIndex', {'sceneName': scene, 'sceneItemId': self.items[bd], 'sceneItemIndex': 0})
            c.call('SetSceneItemIndex', {'sceneName': scene, 'sceneItemId': self.items[media],
                                         'sceneItemIndex': max(0, len(self.items) - 1)})
        except ObsError as ex:
            log('could not order sources:', ex)

    def _status(self):
        o = self._o()
        s = self.client.call('GetMediaInputStatus', {'inputName': o['media']})
        return s.get('mediaState'), s.get('mediaCursor'), s.get('mediaDuration')

    def poll(self):
        now = time.time()
        if not self.client or not self.client.alive:
            if now < self.next_try:
                return 'offline'
            o = self._o()
            try:
                self._connect()
                self.message = 'Connected to OBS'
            except Exception as ex:
                self.client = None
                self.next_try = now + 5
                self.message = f"OBS isn't answering at {o['host']}:{o['port']} ({ex}). Open OBS and turn on Tools → WebSocket Server Settings."
                return 'offline'
            if self.entry is not None and self.state in ('starting', 'playing'):
                # OBS may have restarted under us: pick the recording up where we last saw it.
                try:
                    st, cur, _ = self._status()
                except ObsError:
                    st, cur = None, None
                if st not in ('OBS_MEDIA_STATE_PLAYING', 'OBS_MEDIA_STATE_PAUSED'):
                    self.play(self.entry, self.cursor or self.start_at)
        if self.state in ('starting', 'playing'):
            if self.ended_flag:
                self.state = 'idle'
                return 'ended'
            if now - self.last_status >= 2:
                self.last_status = now
                try:
                    st, cur, dur = self._status()
                except ObsError as ex:
                    self.message = str(ex)
                    if 'ResourceNotFound' in str(ex) or 'code 600' in str(ex):
                        self._setup()
                    return self.state
                if cur is not None and cur >= 0:
                    self.cursor = cur / 1000.0
                if dur:
                    self.duration = dur / 1000.0
                if st == 'OBS_MEDIA_STATE_PLAYING':
                    if self.pending_seek is not None:
                        try:
                            self.client.call('SetMediaInputCursor', {'inputName': self._o()['media'],
                                                                     'mediaCursor': int(self.pending_seek * 1000)})
                            self.cursor = self.pending_seek
                        except ObsError as ex:
                            log('could not seek:', ex)
                        self.pending_seek = None
                    self.state = 'playing'
                    self.message = 'Playing in OBS' + ('' if self.streaming is not False else ' (OBS is not streaming right now)')
                elif st == 'OBS_MEDIA_STATE_ENDED':
                    self.state = 'idle'
                    return 'ended'
                elif st in ('OBS_MEDIA_STATE_STOPPED', 'OBS_MEDIA_STATE_NONE', 'OBS_MEDIA_STATE_ERROR') and now - self.played_at > 10:
                    if st == 'OBS_MEDIA_STATE_STOPPED' and self.state == 'playing':
                        self.state = 'idle'
                        return 'ended'  # stopped by hand in OBS: move on
                    self.state = 'idle'
                    self.message = f'OBS could not play {self.entry["name"]}'
                    return 'failed'
            return self.state
        return self.state

    def play(self, entry, start=0.0):
        o, c = self._o(), self.client
        if not c or not c.alive:
            self.entry, self.start_at, self.cursor, self.state = entry, start, start, 'starting'
            return
        self.entry, self.start_at, self.cursor = entry, start, start
        self.duration = entry.get('duration')
        self.ended_flag = False
        settings = {'is_local_file': True, 'local_file': entry['path'], 'looping': False,
                    'restart_on_activate': False, 'close_when_inactive': False, 'clear_on_media_end': True}
        c.call('SetInputSettings', {'inputName': o['media'], 'inputSettings': settings, 'overlay': True})
        c.call('TriggerMediaInputAction', {'inputName': o['media'], 'mediaAction': 'OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART'})
        if o['backdrop'] in self.items:
            show = bool(o.get('backdrop_on')) and (entry.get('kind') == 'audio' or bool(o.get('backdrop_always')))
            try:
                c.call('SetSceneItemEnabled', {'sceneName': o['scene'], 'sceneItemId': self.items[o['backdrop']],
                                               'sceneItemEnabled': show})
            except ObsError as ex:
                log('backdrop:', ex)
        if o.get('take_program'):
            try:
                cur = c.call('GetCurrentProgramScene')
                name = cur.get('currentProgramSceneName') or cur.get('sceneName')
                if name and name != o['scene']:
                    self.saved_scene = name
                    c.call('SetCurrentProgramScene', {'sceneName': o['scene']})
            except ObsError as ex:
                log('could not switch scene:', ex)
        self.pending_seek = start if start > 3 else None
        self.played_at = time.time()
        self.last_status = 0
        self.state = 'starting'
        self.message = 'Starting in OBS'

    def position(self):
        return self.cursor

    def stop_item(self):
        o = self._o()
        if self.client and self.client.alive:
            try:
                self.client.call('TriggerMediaInputAction', {'inputName': o['media'],
                                                             'mediaAction': 'OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP'})
            except ObsError:
                pass
        self.state = 'idle'
        self.entry = None
        self.ended_flag = False

    def release(self):
        """Going off the air: stop, hide the backdrop and give the program scene back."""
        self.stop_item()
        o = self._o()
        if self.client and self.client.alive:
            if o['backdrop'] in self.items:
                try:
                    self.client.call('SetSceneItemEnabled', {'sceneName': o['scene'], 'sceneItemId': self.items[o['backdrop']],
                                                             'sceneItemEnabled': False})
                except ObsError:
                    pass
            if o.get('restore_scene') and self.saved_scene:
                try:
                    self.client.call('SetCurrentProgramScene', {'sceneName': self.saved_scene})
                except ObsError as ex:
                    log('could not restore scene:', ex)
        self.saved_scene = None

    def shutdown(self):
        try:
            self.release()
        finally:
            if self.client:
                self.client.close()

    def info(self):
        return {'connected': bool(self.client and self.client.alive), 'streaming': self.streaming,
                'version': self.client.version if self.client else ''}


class Sink:
    """One output (an RTMP URL or a recording file) with its own ffmpeg, writer thread and reconnect timer.
    Outputs are independent: one dropping never stops the others."""

    def __init__(self, tools, kind, target):
        self.tools, self.kind, self.target = tools, kind, target
        self.proc = None
        self.q = None
        self.alive = False
        self.err = collections.deque(maxlen=40)
        self.t0 = 0.0
        self.retry_at = 0.0
        self.backoff = 5
        self.dropped = 0
        self.path = None
        self.last_error = ''

    def label(self):
        return redact(self.target) if self.kind == 'rtmp' else (self.path or self.target)

    def start(self):
        base = [self.tools.ffmpeg, '-hide_banner', '-loglevel', 'warning', '-nostats', '-nostdin',
                '-fflags', '+genpts', '-analyzeduration', '3000000', '-probesize', '3000000',
                '-f', 'mpegts', '-i', 'pipe:0', '-map', '0', '-c', 'copy']
        if self.kind == 'rtmp':
            cmd = base + ['-f', 'flv', '-flvflags', 'no_duration_filesize', self.target]
        else:
            folder = Path(self.target).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            self.path = str(folder / time.strftime('replay %y %m %d %H.%M.%S.mkv'))
            cmd = base + ['-f', 'matroska', self.path]
        self.err.clear()
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        threading.Thread(target=drain, args=(self.proc.stderr, self.err), daemon=True).start()
        self.q = queue.Queue(maxsize=2048)  # ~24 MB of stream: rides out short network stalls
        self.alive = True
        self.dropped = 0
        self.t0 = time.monotonic()
        threading.Thread(target=self._writer, args=(self.proc, self.q), daemon=True).start()
        log('output up:', self.label())

    def _writer(self, proc, q):
        while True:
            chunk = q.get()
            if chunk is None:
                break
            try:
                proc.stdin.write(chunk)
            except (OSError, ValueError):
                break
        try:
            proc.stdin.close()
        except Exception:
            pass

    def put(self, chunk):
        if not self.alive:
            return
        try:
            self.q.put_nowait(chunk)
        except Exception:
            self.dropped += 1

    def died(self):
        return self.alive and self.proc is not None and self.proc.poll() is not None

    def stop(self, wait=True):
        """Close stdin so ffmpeg finishes the file / stream cleanly, then make sure it is gone."""
        if not self.proc:
            return
        self.alive = False
        q, proc = self.q, self.proc
        try:
            while True:
                try:
                    q.put_nowait(None)
                    break
                except Exception:
                    try:
                        q.get_nowait()
                    except Exception:
                        pass
        except Exception:
            pass

        def finish():
            try:
                proc.wait(12)
            except Exception:
                pass
            kill_proc(proc, 2)

        if wait:
            finish()
        else:
            threading.Thread(target=finish, daemon=True).start()
        self.proc = None


class DirectBackend(Backend):
    """Its own broadcaster. Each recording is decoded and normalised by a feeder ffmpeg that writes
    MPEG-TS; this program relays that stream to every output's own ffmpeg (RTMP or file). Timestamps
    carry on from one recording to the next, so the stream never breaks between recordings, and at
    each cycle boundary only the outputs reconnect while the programme keeps running."""
    mode = 'direct'

    def __init__(self, cfg, tools):
        super().__init__(cfg, tools)
        self.t_ref = time.monotonic()
        self.ts_floor = 0.0
        self.feed_offset = 0.0
        self.pending = None
        self.feeder = None
        self.feed_err = collections.deque(maxlen=40)
        self.feed_t0 = 0.0
        self.filler = False
        self.sinks = []
        self.sink_sig = None
        self.cycle_n = 0
        self.cycle_t0 = 0.0
        self.gap_until = 0.0
        self.message = 'Ready'

    def _d(self):
        return self.cfg.get()['direct']

    def outputs(self):
        d = self._d()
        outs = [u.strip() for u in d.get('outputs') or [] if u and u.strip()]
        return outs, (d.get('record_folder') or '').strip()

    def configured(self):
        outs, rec = self.outputs()
        return bool(outs or rec)

    def cycle_len(self):
        return parse_duration(self._d().get('cycle'))

    # --- outputs
    def _sync_sinks(self):
        outs, rec = self.outputs()
        sig = (tuple(outs), rec)
        if sig == self.sink_sig:
            return
        wanted = [('rtmp', u) for u in outs] + ([('file', rec)] if rec else [])
        keep = []
        for s in self.sinks:
            if (s.kind, s.target) in wanted:
                keep.append(s)
            else:
                s.stop(wait=False)
        have = {(s.kind, s.target) for s in keep}
        for kind, target in wanted:
            if (kind, target) not in have:
                keep.append(Sink(self.tools, kind, target))
        self.sinks = keep
        self.sink_sig = sig

    def _service_sinks(self, now):
        if not self.sinks:
            return
        if not self.cycle_t0:
            self.cycle_t0 = now
            self.cycle_n = 1
        clen = self.cycle_len()
        if clen and now >= self.gap_until and now - self.cycle_t0 >= clen:
            gap = max(2, int(self._d().get('cycle_gap') or 20))
            for s in self.sinks:
                if s.alive:
                    s.stop(wait=False)
                s.retry_at = 0
            self.gap_until = now + gap
            self.cycle_t0 = now + gap
            self.cycle_n += 1
            log(f'cycle boundary: outputs reconnect in {gap} s (cycle {self.cycle_n} begins)')
        if now < self.gap_until:
            return
        for s in self.sinks:
            if s.died():
                s.last_error = ' / '.join(list(s.err)[-2:]) or f'exit {s.proc.returncode}'
                log('output dropped:', s.label(), '-', redact(s.last_error))
                s.alive = False
                s.proc = None
                s.retry_at = now + s.backoff
                s.backoff = min(s.backoff * 2, 120)
            elif s.alive and now - s.t0 > 60:
                s.backoff = 5
            if not s.alive and s.proc is None and now >= s.retry_at:
                try:
                    s.start()
                except Exception as ex:
                    s.last_error = str(ex)
                    s.retry_at = now + s.backoff
                    s.backoff = min(s.backoff * 2, 120)

    # --- feeders
    def _graph(self, entry, filler=False, start=0.0):
        d = self._d()
        W, H, F = int(d['width']), int(d['height']), int(d['fps'])
        left = max(0.0, float((entry or {}).get('duration') or 0) - float(start or 0))
        trim_v = f',trim=duration={left:.3f}' if left else ''
        trim_a = f',atrim=duration={left:.3f}' if left else ''
        R = self.tools.re()
        fit = (f'scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,'
               f'fps={F},format=yuv420p,setsar=1')
        audio = 'aresample=48000:async=1:first_pts=0,aformat=sample_fmts=fltp:channel_layouts=stereo'
        extra, graph = [], []
        if filler:
            # Nothing queued: keep the stream alive with a slowly scrolling cellular automaton and silence.
            rule = int(urand() * 256)
            extra += R + ['-f', 'lavfi', '-i', f'cellauto=s={W // 4}x{H // 4}:rate={F}:rule={rule}:random_fill_ratio=0.5:scroll=1']
            graph = [f'[0:v]scale={W}:{H}:flags=neighbor,format=yuv420p,setsar=1[v]', 'anullsrc=r=48000:cl=stereo[a]']
            return extra, graph, False
        if entry.get('kind') == 'video':
            graph.append(f'[0:v:0]{fit}[v]')
            if entry.get('acodec'):
                graph.append(f'[0:a:0]{audio}[a]')
                return extra, graph, False
            graph.append(f'anullsrc=r=48000:cl=stereo{trim_a}[a]')
            return extra, graph, not left
        style = d.get('audio_visual') or 'cqt'
        vfile = os.path.expanduser(d.get('visual_file') or '')
        if style == 'loop' and vfile and os.path.exists(vfile):
            extra += ['-stream_loop', '-1'] + R + ['-i', vfile]
            graph += [f'[0:a:0]{audio}[a]', f'[1:v:0]{fit}{trim_v}[v]']
            return extra, graph, not left
        if style == 'still' and vfile and os.path.exists(vfile):
            extra += ['-loop', '1', '-framerate', str(F), '-i', vfile]
            graph += [f'[0:a:0]{audio}[a]', f'[1:v:0]{fit}{trim_v}[v]']
            return extra, graph, not left
        if style == 'scope' and 'avectorscope' in self.tools.filters:
            vis = f'avectorscope=s={H}x{H}:r={F}:zoom=1.4:draw=line:mode=lissajous_xy:rc=242:gc=180:bc=90,pad={W}:{H}:(ow-iw)/2:0,format=yuv420p,setsar=1'
        elif style == 'waves' and 'showwaves' in self.tools.filters:
            vis = f'showwaves=s={W}x{H}:mode=cline:rate={F}:colors=0xf2b45a|0x8d8a84,format=yuv420p,setsar=1'
        elif 'showcqt' in self.tools.filters:
            cw = min(W, 1280)
            ch = int(cw * H / W) // 2 * 2
            vis = f'showcqt=s={cw}x{ch}:fps={F}:axis=0:bar_g=2:sono_g=4:sono_h={ch // 2},scale={W}:{H},format=yuv420p,setsar=1'
        else:
            vis = f'showwaves=s={W}x{H}:mode=cline:rate={F},format=yuv420p,setsar=1'
        graph.append(f'[0:a:0]{audio},asplit=2[a][vis]')
        graph.append(f'[vis]{vis}[v]')
        return extra, graph, False

    def _start_feeder(self, entry, start, filler=False):
        d = self._d()
        enc, vargs = self.tools.video_args(d.get('encoder'), int(d['video_kbps']), int(d['fps']))
        if not enc:
            raise RuntimeError('No H.264 encoder in your ffmpeg (needs h264_videotoolbox or libx264)')
        now = time.monotonic()
        wall = now - self.t_ref + 0.25
        offset = max(wall, self.ts_floor + 0.15)  # 0.15 s clears the next feeder's B-frame decode lead
        if os.environ.get('REPLAY_DEBUG'):
            log(f'feeder offset {offset:.3f} (wall {wall:.3f}, floor {self.ts_floor:.3f})')
        extra, graph, shortest = self._graph(entry, filler, start)
        cmd = [self.tools.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin']
        if not filler:
            cmd += self.tools.re()
            if start > 0.5:
                cmd += ['-ss', f'{start:.3f}']
            cmd += ['-i', entry['path']]
        cmd += extra
        cmd += ['-filter_complex', ';'.join(graph), '-map', '[v]', '-map', '[a]']
        cmd += vargs
        cmd += ['-c:a', 'aac', '-b:a', f"{int(d['audio_kbps'])}k", '-ar', '48000', '-ac', '2']
        if shortest:
            cmd += ['-shortest']
        cmd += ['-output_ts_offset', f'{offset:.3f}', '-f', 'mpegts', 'pipe:1']
        self.feed_err.clear()
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        threading.Thread(target=drain, args=(proc.stderr, self.feed_err), daemon=True).start()
        threading.Thread(target=self._pump, args=(proc,), daemon=True).start()
        self.feeder = proc
        self.feed_t0 = time.monotonic()
        self.feed_offset = offset
        self.filler = filler
        if not filler:
            self.entry, self.start_at = entry, start

    def _launch(self, entry, start, filler=False):
        """Start the next feeder when the clock reaches the end of the previous one. ffmpeg's -re reads a
        file a little faster than real time (about half a second early per file); waiting here keeps the
        stream's timestamps level with the wall clock however many short recordings are aired."""
        now = time.monotonic()
        wait = (self.ts_floor + 0.15) - (now - self.t_ref + 0.25)
        if not filler:
            self.entry, self.start_at = entry, start
        if 0.02 < wait <= 3:
            self.pending = (entry, start, filler, now + wait)
            self.state = 'filling' if filler else 'starting'
            return
        self.pending = None
        self._start_feeder(entry, start, filler)

    def _pump(self, proc):
        """Relay the feeder's MPEG-TS to every live output, in whole 188-byte packets."""
        try:
            while True:
                chunk = proc.stdout.read(188 * 64)
                if not chunk:
                    break
                for s in self.sinks:
                    s.put(chunk)
        except Exception:
            pass

    def _note_end(self, natural):
        """Remember where the last feeder's timestamps stopped, so the next one starts after them."""
        ran = time.monotonic() - self.feed_t0
        if natural and self.entry is not None and not self.filler and (self.entry.get('duration') or 0):
            length = max(0.0, self.entry['duration'] - self.start_at)
        else:
            length = ran + (0.0 if natural else 0.6)
        self.ts_floor = max(self.ts_floor, self.feed_offset + length)

    def _stop_feeder(self):
        if self.feeder is not None:
            kill_proc(self.feeder, wait=2)
            self._note_end(natural=False)
        self.feeder = None
        self.filler = False

    def position(self):
        if self.pending and not self.pending[2]:
            return self.start_at
        if self.entry is None or self.filler or not self.feeder:
            return None
        pos = self.start_at + (time.monotonic() - self.feed_t0)
        dur = self.entry.get('duration') or 0
        return min(pos, dur) if dur else pos

    def _outputs_note(self, now):
        if now < self.gap_until:
            return f'cycle {self.cycle_n} starts in {int(self.gap_until - now)} s (outputs reconnecting for a fresh archive)'
        live = [s for s in self.sinks if s.alive]
        down = [s for s in self.sinks if not s.alive]
        bits = []
        if live:
            bits.append(f'{len(live)} output{"s" if len(live) != 1 else ""} live')
        for s in down:
            wait = max(0, int(s.retry_at - now))
            bits.append(f'{s.label()} reconnecting in {wait} s' + (f' ({redact(s.last_error)[:80]})' if s.last_error else ''))
        clen = self.cycle_len()
        if clen and live:
            bits.append(f'new cycle in {fmt_dur(clen - (now - self.cycle_t0))}')
        return ', '.join(bits)

    def poll(self):
        now = time.monotonic()
        if not self.tools.ffmpeg:
            self.message = 'ffmpeg not found. Install it (brew install ffmpeg) or set its path in Settings.'
            return 'offline'
        if not self.configured():
            self.message = 'Add a stream URL (or a record folder) in Settings → Direct before going on the air.'
            return 'offline'
        self._sync_sinks()
        self._service_sinks(now)
        note = self._outputs_note(now)
        if self.pending:
            entry, start, filler, at = self.pending
            if now < at:
                return 'filling' if filler else 'starting'
            self.pending = None
            try:
                self._start_feeder(entry, start, filler)
            except Exception as ex:
                self.message = str(ex)
                self.state = 'idle'
                return 'failed' if not filler else 'idle'
        if self.feeder is not None:
            rc = self.feeder.poll()
            if rc is None:
                if self.filler:
                    self.state = 'filling'
                    self.message = 'Nothing queued: airing filler visuals · ' + note
                    return 'filling'
                self.state = 'playing' if now - self.feed_t0 > 2 else 'starting'
                self.message = f'Cycle {self.cycle_n} · ' + note
                return self.state
            was_filler = self.filler
            self._note_end(natural=True)
            self.feeder = None
            self.filler = False
            if was_filler:
                self.state = 'idle'
                return 'idle'
            ran = now - self.feed_t0
            dur = (self.entry or {}).get('duration') or 0
            if rc == 0 or (dur and self.start_at + ran >= dur - 5):
                self.state = 'idle'
                return 'ended'
            err = ' / '.join(list(self.feed_err)[-2:]) or f'exit {rc}'
            log('feeder failed:', (self.entry or {}).get('path'), err)
            self.message = f'Could not air {(self.entry or {}).get("name")}: {err[:160]}'
            self.state = 'idle'
            return 'failed'
        self.message = note or 'Ready'
        return 'idle' if self.state in ('filling', 'ended', 'failed', 'playing', 'starting') else self.state

    def play(self, entry, start=0.0):
        if self.feeder is not None:
            self._stop_feeder()
        self._sync_sinks()
        self._service_sinks(time.monotonic())
        self._launch(entry, start)
        self.state = 'starting'
        self.message = 'Starting'

    def fill(self):
        if not self._d().get('filler', True) or self.feeder is not None or self.pending:
            return
        self._sync_sinks()
        self._service_sinks(time.monotonic())
        self._launch(None, 0, filler=True)
        self.state = 'filling'

    def stop_item(self):
        self.pending = None
        self._stop_feeder()
        self.entry = None
        self.state = 'idle'

    def release(self):
        self.stop_item()
        for s in self.sinks:
            s.stop(wait=True)
        self.sinks = []
        self.sink_sig = None
        self.cycle_t0 = 0.0
        self.state = 'idle'

    def shutdown(self):
        self.release()

    def info(self):
        now = time.monotonic()
        clen = self.cycle_len()
        return {
            'cycle': self.cycle_n, 'cycle_len': clen,
            'cycle_left': (clen - (now - self.cycle_t0)) if clen and self.cycle_t0 and now >= self.gap_until else None,
            'gap_left': (self.gap_until - now) if now < self.gap_until else None,
            'outputs': [{'label': s.label(), 'kind': s.kind, 'live': s.alive,
                         'retry_in': max(0, int(s.retry_at - now)) if not s.alive else None,
                         'dropped': s.dropped, 'error': redact(s.last_error)[:200] if s.last_error else ''} for s in self.sinks],
            'filler': self.filler, 'encoder': self.tools.h264,
        }


def redact(url):
    """Hide stream keys in logs and on the page."""
    m = re.match(r'^(rtmps?://[^/]+/[^/]+/)(.+)$', url or '')
    if m:
        key = m.group(2)
        return m.group(1) + ('•' * 6 + key[-4:] if len(key) > 4 else '•' * len(key))
    return url


# ---------------------------------------------------------------- station: queue, history, airing

class Station:
    def __init__(self, cfg, lib, tools, backdrop_url_fn):
        self.cfg, self.lib, self.tools = cfg, lib, tools
        self.backdrop_url_fn = backdrop_url_fn
        self.lock = threading.RLock()
        s = read_json(DATA_DIR / 'state.json', {})
        self.queue = s.get('queue', [])
        self.on_air = bool(s.get('on_air')) and bool(self.cfg.get().get('resume_on_launch', True))
        self.history = s.get('history', [])[:300]
        self.played_at = s.get('played_at', {})
        self.qn = int(s.get('qn', 0))
        cur = s.get('current')
        if cur and self.on_air:
            # Resume the recording that was airing when the program stopped.
            self.queue.insert(0, {'qid': self._qid(), 'id': cur['id'], 'start': max(0.0, float(cur.get('pos') or 0) - 5), 'by': cur.get('by', 'resume')})
        self.cur = None
        self.backend = None
        self.bad = {}
        self.retry_at = 0
        self.want_skip = False
        self.message = 'Off the air'
        self.last_save = 0
        self.caff = None
        lib.on_new.append(self._new_ready)

    def _qid(self):
        self.qn += 1
        return f'q{self.qn}'

    def start(self):
        threading.Thread(target=self.run, name='station', daemon=True).start()

    # --- queue editing (called from the page)
    def add(self, eid, where='end', start=0.0, by='you'):
        e = self.lib.get(eid)
        if not e:
            raise KeyError('That recording is no longer in the library')
        item = {'qid': self._qid(), 'id': eid, 'start': float(start or 0), 'by': by}
        with self.lock:
            if where == 'next':
                self.queue.insert(0, item)
            else:
                self.queue.append(item)
        self.save()
        return item

    def remove(self, qid):
        with self.lock:
            self.queue = [q for q in self.queue if q['qid'] != qid]
        self.save()

    def move(self, qid, d):
        with self.lock:
            i = next((k for k, q in enumerate(self.queue) if q['qid'] == qid), None)
            if i is None:
                return
            j = max(0, min(len(self.queue) - 1, i + (-1 if d == 'up' else 1)))
            self.queue[i], self.queue[j] = self.queue[j], self.queue[i]
        self.save()

    def clear(self):
        with self.lock:
            self.queue = []
        self.save()

    def air_now(self, eid):
        self.add(eid, 'next', by='you')
        with self.lock:
            if self.on_air and self.cur:
                self.want_skip = True
            self.on_air = True
        self.save()

    def set_air(self, on):
        with self.lock:
            self.on_air = bool(on)
        self.save()

    def skip(self):
        with self.lock:
            self.want_skip = True

    def _new_ready(self, eid):
        c = self.cfg.get()
        e = self.lib.get(eid)
        if c.get('auto_queue_new') and e and (e.get('duration') or 0) >= float(c.get('min_seconds') or 0):
            with self.lock:
                if not any(q['id'] == eid for q in self.queue):
                    self.queue.append({'qid': self._qid(), 'id': eid, 'start': 0.0, 'by': 'new'})
            self.save()

    # --- choosing
    def _auto_pick(self):
        c = self.cfg.get()
        mins = float(c.get('min_seconds') or 0)
        kind = c.get('fill_from') or 'any'
        pool = []
        for e in self.lib.ready_entries():
            d = e.get('duration') or 0
            if d < mins:
                continue
            if kind == 'long' and d < 3600:
                continue
            if kind == 'clips' and d >= 600:
                continue
            if kind in ('video', 'audio') and e.get('kind') != kind:
                continue
            n, t = self.bad.get(e['id'], (0, 0))
            if n >= 2 and time.time() - t < 6 * 3600:
                continue
            pool.append(e)
        if not pool:
            return None
        horizon = float(c.get('avoid_repeat_hours') or 0) * 3600
        now = time.time()
        fresh = [e for e in pool if now - float(self.played_at.get(e['id'], 0)) > horizon]
        if fresh:
            return fresh[int(urand() * len(fresh))]
        return min(pool, key=lambda e: float(self.played_at.get(e['id'], 0)))

    def _next(self):
        with self.lock:
            while self.queue:
                item = self.queue.pop(0)
                e = self.lib.get(item['id'])
                if e and e.get('ready') and not e.get('offline'):
                    return item, e
        if self.cfg.get().get('auto_fill'):
            e = self._auto_pick()
            if e:
                return {'qid': self._qid(), 'id': e['id'], 'start': 0.0, 'by': 'auto'}, e
        return None, None

    def _finish(self, how):
        if not self.cur:
            return
        pos = self.backend.position() if self.backend else None
        rec = {'id': self.cur['id'], 'name': self.cur['name'], 'aired_at': self.cur['began'],
               'seconds': round(time.time() - self.cur['began']), 'how': how, 'mode': self.cur['mode'], 'by': self.cur['by']}
        with self.lock:
            self.history.insert(0, rec)
            del self.history[300:]
            self.played_at[self.cur['id']] = self.cur['began']
        if how == 'failed':
            n, _ = self.bad.get(self.cur['id'], (0, 0))
            self.bad[self.cur['id']] = (n + 1, time.time())
        if how == 'stopped' and pos is not None:
            e = self.lib.get(self.cur['id'])
            dur = (e or {}).get('duration') or 0
            if not dur or pos < dur - 60:
                with self.lock:
                    self.queue.insert(0, {'qid': self._qid(), 'id': self.cur['id'], 'start': pos, 'by': self.cur['by']})
        self.cur = None
        self.save()

    def _keep_awake(self, on):
        if on and not self.caff and self.tools.caffeinate:
            try:
                self.caff = subprocess.Popen([self.tools.caffeinate, '-dimsu', '-w', str(os.getpid())])
            except OSError:
                self.caff = None
        elif not on and self.caff:
            kill_proc(self.caff, 1)
            self.caff = None

    def run(self):
        while not STOP.is_set():
            try:
                self.tick()
            except Exception:
                log('station error:', traceback.format_exc())
                time.sleep(2)
            if time.time() - self.last_save > 30:
                self.save()
            STOP.wait(0.2 if self.on_air else 0.5)
        if self.backend:
            if self.cur:
                self._finish('stopped')
            try:
                self.backend.shutdown()
            except Exception:
                pass
        self._keep_awake(False)
        self.save()

    def tick(self):
        c = self.cfg.get()
        mode = c.get('mode', 'obs')
        if self.backend and (self.backend.mode != mode or not self.on_air):
            if self.cur:
                self._finish('stopped')
            try:
                self.backend.shutdown()
            except Exception:
                log('while going off the air:', traceback.format_exc())
            self.backend = None
        if not self.on_air:
            self.message = 'Off the air'
            self._keep_awake(False)
            return
        self._keep_awake(True)
        if not self.backend:
            self.backend = ObsBackend(self.cfg, self.tools, self.backdrop_url_fn()) if mode == 'obs' else DirectBackend(self.cfg, self.tools)
        b = self.backend
        state = b.poll()
        self.message = b.message
        if self.want_skip:
            self.want_skip = False
            if self.cur:
                self._finish('skipped')
            b.stop_item()
            state = 'idle'
        if state in ('offline', 'gap', 'starting', 'playing'):
            return
        if state == 'ended':
            self._finish('done')
        elif state == 'failed':
            if self.cur:
                self._finish('failed')
        if time.time() < self.retry_at:
            return
        item, e = self._next()
        if not e:
            if isinstance(b, DirectBackend) and state != 'filling':
                try:
                    b.fill()
                except Exception as ex:
                    self.message = str(ex)
            if state != 'filling':
                self.message = 'Nothing to air. Queue a recording, or turn on auto-fill in Settings.'
            return
        try:
            b.play(e, float(item.get('start') or 0))
        except Exception as ex:
            log('could not start', e['path'], '-', ex)
            self.message = f'Could not start {e["name"]}: {ex}'
            n, _ = self.bad.get(e['id'], (0, 0))
            self.bad[e['id']] = (n + 1, time.time())
            with self.lock:
                if n + 1 < 3:
                    self.queue.insert(0, item)  # try again shortly (OBS may have gone away mid-call)
                else:
                    self.history.insert(0, {'id': e['id'], 'name': e['name'], 'aired_at': time.time(), 'seconds': 0,
                                            'how': 'failed', 'mode': b.mode, 'by': item.get('by')})
            self.retry_at = time.time() + 5
            return
        self.cur = {'qid': item['qid'], 'id': e['id'], 'name': e['name'], 'began': time.time(),
                    'start': float(item.get('start') or 0), 'by': item.get('by', 'you'), 'mode': b.mode}
        log(f'airing ({b.mode}):', e['path'], f'from {fmt_dur(self.cur["start"])}' if self.cur['start'] else '')
        self.save()

    def save(self):
        with self.lock:
            cur = None
            if self.cur:
                pos = self.backend.position() if self.backend else None
                cur = {'id': self.cur['id'], 'pos': pos if pos is not None else self.cur['start'], 'by': self.cur['by']}
            data = {'queue': self.queue, 'on_air': self.on_air, 'history': self.history[:300],
                    'played_at': self.played_at, 'qn': self.qn, 'current': cur}
        try:
            atomic_write_json(DATA_DIR / 'state.json', data)
        except OSError as ex:
            log('could not save state:', ex)
        self.last_save = time.time()

    def snapshot(self):
        c = self.cfg.get()
        with self.lock:
            q = []
            for item in self.queue:
                e = self.lib.get(item['id']) or {}
                q.append({'qid': item['qid'], 'id': item['id'], 'start': item.get('start', 0), 'by': item.get('by'),
                          'name': e.get('name', '(missing)'), 'duration': e.get('duration'), 'kind': e.get('kind'),
                          'offline': e.get('offline', not e)})
            cur = None
            if self.cur:
                e = self.lib.get(self.cur['id']) or {}
                pos = self.backend.position() if self.backend else None
                cur = dict(summary(e)) if e else {'name': self.cur['name']}
                cur.update({'position': pos, 'began': self.cur['began'], 'by': self.cur['by'], 'start': self.cur['start']})
            hist = self.history[:40]
        return {
            'on_air': self.on_air, 'mode': c.get('mode'), 'message': self.message,
            'state': self.backend.state if self.backend else 'off',
            'current': cur, 'queue': q, 'history': hist,
            'backend': self.backend.info() if self.backend else {},
        }


# ---------------------------------------------------------------- web page and API

class App:
    def __init__(self, cfg, tools, lib, station):
        self.cfg, self.tools, self.lib, self.station = cfg, tools, lib, station
        self.port = 0
        self.started = time.time()


class Handler(BaseHTTPRequestHandler):
    server_version = 'DispatchReplay/' + VERSION
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    @property
    def app(self):
        return self.server.app

    # --- access
    def _local(self):
        ip = self.client_address[0]
        return ip in ('127.0.0.1', '::1') or ip.startswith('::ffff:127.')

    def _allowed(self, query):
        if self._local():
            return True
        c = self.app.cfg.get()
        if not c.get('allow_lan'):
            return False
        code = self.headers.get('X-Replay-Code') or (query.get('code') or [''])[0]
        return bool(code) and code == c.get('lan_code')

    # --- responses
    def _send(self, status, body, ctype='application/json; charset=utf-8', extra=None):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, obj, status=200):
        self._send(status, json.dumps(obj))

    def _err(self, status, msg):
        self._json({'error': msg}, status)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > 2_000_000:
            raise ValueError('request too large')
        raw = self.rfile.read(n) if n else b'{}'
        return json.loads(raw or b'{}')

    # --- routing
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        path = u.path
        if not self._allowed(q):
            return self._send(403, 'This computer is not allowed. Turn on network access in Settings and use the code.', 'text/plain; charset=utf-8')
        try:
            if path in ('/', '/index.html'):
                return self._send(200, PAGE_FILE.read_bytes(), 'text/html; charset=utf-8')
            if path.startswith('/backdrop'):
                if not BACKDROP_FILE.exists():
                    return self._err(404, 'Days On End.html is not next to dispatch_replay.py')
                return self._send(200, BACKDROP_FILE.read_bytes(), 'text/html; charset=utf-8')
            if path == '/api/state':
                return self._json(self._state())
            if path == '/api/library':
                g = lambda k, d: (q.get(k) or [d])[0]
                return self._json(self.app.lib.query(g('q', ''), g('filter', 'all'), g('sort', 'newest'),
                                                     int(g('offset', '0')), min(500, int(g('limit', '100')))))
            if path == '/api/settings':
                return self._json(self._settings_out())
            if path == '/api/log':
                try:
                    lines = (DATA_DIR / 'replay.log').read_text(encoding='utf-8', errors='replace').splitlines()[-200:]
                except OSError:
                    lines = []
                return self._json({'lines': lines})
            m = re.match(r'^/(media|stream|thumb)/([0-9a-f]{12})$', path)
            if m:
                kind, eid = m.groups()
                e = self.app.lib.get(eid)
                if not e:
                    return self._err(404, 'not in the library')
                if kind == 'thumb':
                    t = self.app.lib.thumb(eid)
                    if not t:
                        return self._err(404, 'no picture')
                    return self._send(200, t.read_bytes(), 'image/jpeg', {'Cache-Control': 'max-age=3600'})
                if kind == 'media':
                    return self._file(e['path'])
                return self._stream(e, float((q.get('t') or ['0'])[0] or 0))
            return self._err(404, 'not found')
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as ex:
            log('page error:', traceback.format_exc())
            try:
                self._err(500, str(ex))
            except Exception:
                pass

    def do_POST(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        if not self._allowed(q):
            return self._err(403, 'not allowed')
        st = self.app.station
        try:
            if u.path == '/api/upload':
                return self._upload()
            body = self._body()
            if u.path == '/api/queue':
                a = body.get('action')
                if a == 'add':
                    st.add(body['id'], body.get('where', 'end'), float(body.get('start') or 0))
                elif a == 'remove':
                    st.remove(body['qid'])
                elif a == 'move':
                    st.move(body['qid'], body.get('dir', 'up'))
                elif a == 'clear':
                    st.clear()
                return self._json(st.snapshot())
            if u.path == '/api/air':
                a = body.get('action')
                if a == 'start':
                    st.set_air(True)
                elif a == 'stop':
                    st.set_air(False)
                elif a == 'skip':
                    st.skip()
                elif a == 'now':
                    st.air_now(body['id'])
                return self._json(st.snapshot())
            if u.path == '/api/scan':
                self.app.lib.wake.set()
                return self._json({'ok': True})
            if u.path == '/api/settings':
                return self._json(self._settings_in(body))
            return self._err(404, 'not found')
        except KeyError as ex:
            return self._err(400, str(ex).strip("'"))
        except Exception as ex:
            log('page error:', traceback.format_exc())
            return self._err(500, str(ex))

    # --- pieces
    def _state(self):
        a = self.app
        return {
            'version': VERSION, 'now': time.time(), 'port': a.port,
            'tools': a.tools.summary(), 'library': a.lib.stats(),
            'air': a.station.snapshot(),
            'new': [summary(e) for e in (a.lib.get(i) for i in list(a.lib.new_ids)[:12]) if e],
            'lan': {'allowed': a.cfg.get().get('allow_lan'), 'code': a.cfg.get().get('lan_code') if self._local() else None,
                    'addresses': lan_addresses() if self._local() else []},
        }

    def _settings_out(self):
        c = self.app.cfg.get()
        c['obs']['password'] = '••••••' if c['obs'].get('password') else ''
        c['direct']['outputs'] = [redact(u) for u in c['direct'].get('outputs') or []]
        if not self._local():
            c['lan_code'] = None
        c['inbox_resolved'] = str(inbox_dir(self.app.cfg.get()))
        c['backdrop_default'] = default_backdrop(self.app.port)
        return c

    def _settings_in(self, body):
        cur = self.app.cfg.get()
        patch = {}
        for k in ('folders', 'inbox', 'allow_lan', 'settle_seconds', 'scan_every', 'min_seconds', 'mode', 'resume_on_launch',
                  'auto_fill', 'fill_from', 'auto_queue_new', 'avoid_repeat_hours', 'ffmpeg', 'ffprobe'):
            if k in body:
                patch[k] = body[k]
        if 'folders' in patch:
            patch['folders'] = [str(f).strip() for f in patch['folders'] if str(f).strip()]
        if 'obs' in body:
            o = dict(body['obs'])
            if o.get('password') == '••••••':
                o.pop('password')
            patch['obs'] = o
        if 'direct' in body:
            d = dict(body['direct'])
            if 'outputs' in d:
                old = cur['direct'].get('outputs') or []
                keep = []
                for u in d['outputs']:
                    u = str(u).strip()
                    if not u:
                        continue
                    match = next((o for o in old if redact(o) == u), None)
                    keep.append(match or u)
                d['outputs'] = keep
            patch['direct'] = d
        self.app.cfg.update(patch)
        if 'folders' in patch or 'settle_seconds' in patch:
            self.app.lib.wake.set()
        return self._settings_out()

    def _file(self, path):
        try:
            size = os.path.getsize(path)
        except OSError:
            return self._err(404, 'file is gone')
        ctype = MIME.get(Path(path).suffix.lower(), 'application/octet-stream')
        start, end, status = 0, size - 1, 200
        rng = self.headers.get('Range')
        if rng:
            m = re.match(r'bytes=(\d*)-(\d*)', rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(size - 1, int(m.group(2)))
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(length))
        if status == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        if self.command == 'HEAD':
            return
        with open(path, 'rb') as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def _stream(self, e, t):
        """Play anything the browser can't open directly: remux/transcode to fragmented MP4 on the fly."""
        tools = self.app.tools
        if not tools.ffmpeg:
            return self._err(503, 'ffmpeg not found')
        cmd = [tools.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin']
        if t > 0:
            cmd += ['-ss', f'{t:.3f}']
        cmd += ['-i', e['path']]
        if e.get('kind') == 'video':
            if e.get('vcodec') == 'h264' and e.get('pix_fmt') in (None, 'yuv420p', 'yuvj420p'):
                cmd += ['-map', '0:v:0', '-c:v', 'copy']
            else:
                enc, _ = tools.video_args('auto', 3000, 30)
                cmd += ['-map', '0:v:0', '-vf', 'scale=-2:min(720\\,ih),format=yuv420p', '-c:v', enc or 'libx264',
                        '-b:v', '3000k', '-g', '60']
                if enc == 'libx264':
                    cmd += ['-preset', 'veryfast']
            if e.get('acodec'):
                cmd += ['-map', '0:a:0', '-c:a', 'aac', '-b:a', '160k', '-ac', '2']
            cmd += ['-f', 'mp4', '-movflags', 'frag_keyframe+empty_moov+default_base_moof', 'pipe:1']
            ctype = 'video/mp4'
        else:
            cmd += ['-map', '0:a:0', '-c:a', 'aac', '-b:a', '192k', '-ac', '2', '-f', 'mp4',
                    '-movflags', 'empty_moov+default_base_moof', '-frag_duration', '1000000', 'pipe:1']
            ctype = 'audio/mp4'
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        try:
            while True:
                chunk = p.stdout.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            kill_proc(p, 1)

    def _upload(self):
        name = urllib.parse.unquote(self.headers.get('X-Filename') or '')
        name = os.path.basename(name.replace('\\', '/')).strip()
        if not name or name.startswith('.'):
            return self._err(400, 'missing file name')
        if os.path.splitext(name)[1].lower() not in VIDEO_EXT | AUDIO_EXT:
            return self._err(400, 'That is not an audio or video file this can air')
        n = int(self.headers.get('Content-Length') or 0)
        if n <= 0:
            return self._err(400, 'empty upload')
        dest_dir = inbox_dir(self.app.cfg.get())
        dest_dir.mkdir(parents=True, exist_ok=True)
        stem, ext = os.path.splitext(name)
        dest = dest_dir / name
        k = 2
        while dest.exists():
            dest = dest_dir / f'{stem} {k}{ext}'
            k += 1
        part = Path(str(dest) + '.part')
        left = n
        try:
            with open(part, 'wb') as f:
                while left > 0:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        raise ConnectionError('upload was cut off')
                    f.write(chunk)
                    left -= len(chunk)
            os.replace(part, dest)
        except Exception as ex:
            try:
                part.unlink()
            except OSError:
                pass
            return self._err(400, f'Upload failed: {ex}')
        log('received upload:', dest)
        self.app.lib.mark_complete(str(dest))
        return self._json({'ok': True, 'path': str(dest)})


def inbox_dir(c):
    if (c.get('inbox') or '').strip():
        return Path(os.path.expanduser(c['inbox'].strip()))
    folders = [f for f in c.get('folders') or [] if str(f).strip()]
    base = Path(os.path.expanduser(folders[0])) if folders else Path.home() / 'Movies'
    return base / 'Replay inbox'


def default_backdrop(port):
    return f'http://127.0.0.1:{port}/backdrop/?ui=0&sound=0'


def lan_addresses():
    out = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))
        out.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return out


def open_page(url):
    if sys.platform == 'darwin' and Path('/Applications/Google Chrome.app').exists():
        if subprocess.run(['open', '-a', 'Google Chrome', url]).returncode == 0:
            return
    webbrowser.open(url)


# ---------------------------------------------------------------- main

def main():
    global DATA_DIR
    ap = argparse.ArgumentParser(description='Dispatch Replay: play back and re-air finished recordings.')
    ap.add_argument('--port', type=int, help='page port (default 8765)')
    ap.add_argument('--data', help='where settings, index and logs live (default: replay_data next to this file)')
    ap.add_argument('--no-browser', action='store_true', help="don't open the page")
    ap.add_argument('--check', action='store_true', help='show what was found and exit')
    args = ap.parse_args()
    if args.data:
        DATA_DIR = Path(args.data).expanduser().resolve()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg = Config(DATA_DIR / 'config.json')
    tools = Tools(cfg)
    if args.check:
        c = cfg.get()
        print(json.dumps({'version': VERSION, 'python': sys.version.split()[0], 'tools': tools.summary(),
                          'folders': c['folders'], 'mode': c['mode'], 'data': str(DATA_DIR)}, indent=1))
        return 0
    if not tools.ffmpeg or not tools.ffprobe:
        log('ffmpeg/ffprobe not found. Install with "brew install ffmpeg" (or "sudo port install ffmpeg"), '
            'or set their paths in Settings. The page will still open.')

    lib = Library(cfg, tools)
    app = App(cfg, tools, lib, None)
    station = Station(cfg, lib, tools, lambda: cfg.get()['obs'].get('backdrop_url') or default_backdrop(app.port))
    app.station = station

    c = cfg.get()
    host = '0.0.0.0' if c.get('allow_lan') else '127.0.0.1'
    port = args.port or int(c.get('port') or 8765)
    server = None
    for p in range(port, port + 10):
        try:
            server = ThreadingHTTPServer((host, p), Handler)
            app.port = p
            break
        except OSError:
            continue
    if not server:
        log(f'no free port between {port} and {port + 9}')
        return 1
    server.daemon_threads = True
    server.app = app
    lib.start()
    station.start()
    threading.Thread(target=server.serve_forever, name='http', daemon=True).start()
    url = f'http://localhost:{app.port}/'
    log(f'Dispatch Replay {VERSION} at {url}  (mode: {c["mode"]}, ffmpeg: {tools.ffmpeg or "missing"}, h264: {tools.h264})')
    if c.get('allow_lan'):
        log('network access is on; code', c.get('lan_code'), 'at', ', '.join(f'http://{a}:{app.port}/' for a in lan_addresses()))
    if not args.no_browser:
        threading.Timer(0.8, open_page, args=(url,)).start()

    def bye(*_):
        STOP.set()

    signal.signal(signal.SIGINT, bye)
    signal.signal(signal.SIGTERM, bye)
    try:
        while not STOP.is_set():
            STOP.wait(1)
    finally:
        log('shutting down')
        time.sleep(1.2)  # let the station thread release OBS / close the stream
        server.shutdown()
        lib.save()
        station.save()
    return 0


if __name__ == '__main__':
    sys.exit(main())
