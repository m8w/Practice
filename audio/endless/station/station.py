#!/usr/bin/env python3
"""Endless Station — serves Endless Sonata, Endless Symphony and Aggregate Field on this Mac and speaks an
announcement before every new piece or movement.

Announcements go to Dispatch when the Dispatch announcer is running (a line is added to the ExternalRadio block
file it watches, the same way radiot adds tracks), otherwise they are spoken here in the Dispatch voice
(macOS `say`, system default voice, 190 words a minute). The station also writes now_playing.txt for OBS and
keeps each page's settings in settings/<page>.json so they carry over between sessions and browsers.

    python3 station.py                      # serve on http://127.0.0.1:8787 and open Aggregate Field
    python3 station.py --page symphony      # open another page (sonata, symphony, aggregate, or "home")
    python3 station.py --mode say           # always speak here; --mode dispatch | say | off | auto (default)
    python3 station.py --voice Samantha --rate 175
    python3 station.py --say-test "Testing the station voice."
Standard library only."""
import argparse, datetime, glob, http.server, json, mimetypes, os, queue, re, shutil, socket, subprocess, sys, threading, time, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
WWW = os.path.join(HERE, 'www')
SETTINGS = os.path.join(HERE, 'settings')
VERSION = '1.0'

def find_radio(arg):
    if arg: return os.path.expanduser(arg)
    parent = os.path.dirname(HERE)
    if os.path.isdir(os.path.join(parent, 'logs')): return parent
    return os.path.expanduser('~/ExternalRadio')

ap = argparse.ArgumentParser(description='Endless Station')
ap.add_argument('--port', type=int, default=8787)
ap.add_argument('--mode', choices=['auto', 'dispatch', 'say', 'off'], default=os.environ.get('ENDLESS_MODE', 'auto'))
ap.add_argument('--voice', default=os.environ.get('ENDLESS_VOICE', ''), help='macOS voice for `say` (default: the system voice, like Dispatch)')
ap.add_argument('--rate', type=int, default=int(os.environ.get('ENDLESS_RATE', '190')))
ap.add_argument('--radio', default=os.environ.get('ENDLESS_RADIO', ''), help='ExternalRadio folder (holds logs/)')
ap.add_argument('--lag', type=float, default=2.5, help='seconds Dispatch takes to notice a new line')
ap.add_argument('--tag', default='ENDLESS', help='tag written on Dispatch lines')
ap.add_argument('--page', default='aggregate', help='page to open: aggregate, symphony, sonata or home')
ap.add_argument('--open', action='store_true', help='open the page in a browser')
ap.add_argument('--chrome', action='store_true', help='open in a separate Google Chrome window that may autoplay and keeps time in the background')
ap.add_argument('--say-test', metavar='TEXT')
A = ap.parse_args()
RADIO = find_radio(A.radio)
LOGS = os.path.join(RADIO, 'logs')
NOW_TXT = os.path.join(HERE, 'now_playing.txt')
NOW_JSON = os.path.join(HERE, 'now_playing.json')
LOGFILE = os.path.join(HERE, 'station.log')
os.makedirs(SETTINGS, exist_ok=True)

def log(msg):
    line = f'{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}'
    print(line, flush=True)
    try:
        with open(LOGFILE, 'a', encoding='utf-8') as f: f.write(line + '\n')
    except OSError: pass

# ---------------- Dispatch ----------------
def dispatch_running():
    """The announcer logs at least every 5 minutes while it watches, so a fresh log means it is running."""
    p = os.path.join(LOGS, 'dispatch_announcer.log')
    try: return time.time() - os.path.getmtime(p) < 6.5 * 60
    except OSError: return False

def newest_block():
    files = glob.glob(os.path.join(LOGS, 'block_*.txt'))
    return max(files, key=os.path.getmtime) if files else None

def mode_now():
    if A.mode != 'auto': return A.mode
    if dispatch_running(): return 'dispatch'
    return 'say' if shutil.which('say') else 'off'

def dispatch_line(text, url):
    """Add a track line the way radiot does: '[HH:MM:SS] [TAG] title' and an indented link."""
    os.makedirs(LOGS, exist_ok=True)
    block = newest_block()
    if block:   # radiot closes a block with '— end of block (N tracks) —'; start a fresh one rather than write past it
        try:
            with open(block, 'rb') as f: f.seek(max(0, os.path.getsize(block) - 400)); ended = b'end of block' in f.read()
        except OSError: ended = False
        if ended: block = None
    if not block:
        block = os.path.join(LOGS, f'block_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt')
        with open(block, 'w', encoding='utf-8') as f: f.write(f'ExternalRadio session — {datetime.datetime.now():%Y-%m-%d %H:%M}\n' + '=' * 60 + '\n\n')
    title = re.sub(r'\s+', ' ', text).strip()
    with open(block, 'a', encoding='utf-8') as f: f.write(f'[{datetime.datetime.now():%H:%M:%S}] [{A.tag}] {title}\n  {url}\n')
    return os.path.basename(block)

# ---------------- the Mac voice ----------------
speech = queue.Queue()
def speaker():
    while True:
        text = speech.get()
        cmd = ['say', '-r', str(A.rate)] + (['-v', A.voice] if A.voice else []) + [text]
        try: subprocess.run(cmd, timeout=120)
        except Exception as e: log(f'say failed: {e}')
threading.Thread(target=speaker, daemon=True).start()

def estimate(text, mode):
    words = len(text.split()) + (1 if mode == 'dispatch' else 0)   # Dispatch says the tag ("Endless.") first
    return round(words / (A.rate / 60) + 0.6 + (A.lag if mode == 'dispatch' else 0.4), 2)

def announce(text, page):
    mode = mode_now(); secs = estimate(text, mode)
    if mode == 'dispatch':
        b = dispatch_line(text, f'http://127.0.0.1:{PORT}/{page}/'); log(f'DISPATCH {b}: {text}')
    elif mode == 'say':
        speech.put(text); log(f'SAY {text}')
    else:
        secs = 0; log(f'(silent) {text}')
    return mode, secs

def write_now(line, meta):
    try:
        with open(NOW_TXT, 'w', encoding='utf-8') as f: f.write(line + '\n')
        with open(NOW_JSON, 'w', encoding='utf-8') as f: json.dump(dict(meta, line=line, at=datetime.datetime.now().isoformat(timespec='seconds')), f, ensure_ascii=False, indent=1)
    except OSError as e: log(f'now playing not written: {e}')

# ---------------- HTTP ----------------
mimetypes.add_type('text/javascript', '.js'); mimetypes.add_type('application/json', '.json')
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'EndlessStation/' + VERSION
    def log_message(self, *a): pass
    def send(self, code, body, ctype='application/json; charset=utf-8', extra=None):
        data = body if isinstance(body, bytes) else (json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body).encode('utf-8')
        self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', (extra or {}).get('cache', 'no-store')); self.end_headers()
        if self.command != 'HEAD': self.wfile.write(data)
    def body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > 2_000_000: raise ValueError('too large')
        return json.loads(self.rfile.read(n) or b'{}')
    def do_HEAD(self): self.do_GET()
    def do_GET(self):
        u = urllib.parse.urlparse(self.path); path = urllib.parse.unquote(u.path)
        if path == '/api/status':
            m = mode_now(); b = newest_block()
            return self.send(200, {'mode': m, 'voice': A.voice, 'rate': A.rate, 'lag': A.lag, 'nowFile': NOW_TXT, 'version': VERSION,
                                   'dispatch': {'running': dispatch_running(), 'block': os.path.basename(b) if b else None, 'logs': LOGS}})
        m = re.fullmatch(r'/api/settings/([a-z]+)', path)
        if m:
            p = os.path.join(SETTINGS, m.group(1) + '.json')
            if not os.path.exists(p): return self.send(404, {'error': 'none'})
            with open(p, encoding='utf-8') as f: return self.send(200, f.read())
        # static files from www/
        if path.endswith('/'): path += 'index.html'
        full = os.path.realpath(os.path.join(WWW, path.lstrip('/')))
        if not full.startswith(os.path.realpath(WWW) + os.sep) or not os.path.isfile(full):
            if os.path.isdir(full): self.send_response(301); self.send_header('Location', u.path + '/'); self.end_headers(); return
            return self.send(404, 'Not found', 'text/plain; charset=utf-8')
        ctype = mimetypes.guess_type(full)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype in ('application/json',): ctype += '; charset=utf-8'
        with open(full, 'rb') as f: data = f.read()
        self.send(200, data, ctype, {'cache': 'no-cache' if full.endswith('.html') else 'max-age=86400'})
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try: j = self.body()
        except Exception: return self.send(400, {'error': 'bad json'})
        if path == '/api/announce':
            text = re.sub(r'\s+', ' ', str(j.get('text') or '')).strip()[:600]
            page = re.sub(r'[^a-z]', '', str(j.get('page') or 'home'))
            if not text: return self.send(400, {'error': 'no text'})
            mode, secs = announce(text, page)
            return self.send(200, {'mode': mode, 'seconds': secs})
        if path == '/api/now':
            line = re.sub(r'\s+', ' ', str(j.get('line') or '')).strip()[:400]
            write_now(line, {k: v for k, v in j.items() if isinstance(v, (str, int, float)) and k != 'line'})
            return self.send(200, {'ok': True})
        m = re.fullmatch(r'/api/settings/([a-z]+)', path)
        if m:
            if not isinstance(j.get('settings'), dict): return self.send(400, {'error': 'no settings'})
            p = os.path.join(SETTINGS, m.group(1) + '.json'); tmp = p + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f: json.dump(j, f, ensure_ascii=False)
            os.replace(tmp, p)
            return self.send(200, {'ok': True})
        self.send(404, {'error': 'unknown'})

def free_port(p):
    for q in range(p, p + 20):
        with socket.socket() as s:
            try: s.bind(('127.0.0.1', q)); return q
            except OSError: continue
    sys.exit('No free port near %d' % p)

def open_page(url):
    chrome = '/Applications/Google Chrome.app'
    if A.chrome and os.path.isdir(chrome):
        prof = os.path.join(HERE, 'chrome-profile')
        subprocess.Popen(['open', '-na', 'Google Chrome', '--args', f'--user-data-dir={prof}', '--autoplay-policy=no-user-gesture-required',
                          '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows', '--no-first-run', '--new-window', url])
    elif shutil.which('open'): subprocess.Popen(['open', url])
    else: print('Open', url)

if __name__ == '__main__':
    if A.say_test:
        subprocess.run(['say', '-r', str(A.rate)] + (['-v', A.voice] if A.voice else []) + [A.say_test]); sys.exit(0)
    if not os.path.isdir(WWW): sys.exit('The www folder is missing next to station.py.')
    PORT = free_port(A.port)
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    m = mode_now()
    log(f'Endless Station {VERSION} on http://127.0.0.1:{PORT}/  announcements: {m}'
        + (f' (Dispatch watching {os.path.basename(newest_block() or "")})' if m == 'dispatch' else f' (voice: {A.voice or "system default"}, {A.rate} wpm)' if m == 'say' else '')
        + f'  ExternalRadio: {RADIO}')
    page = '' if A.page == 'home' else A.page.strip('/') + '/'
    if A.open or A.chrome: threading.Timer(0.6, open_page, [f'http://127.0.0.1:{PORT}/{page}' + ('?autoplay' if page else '')]).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: log('stopped')
