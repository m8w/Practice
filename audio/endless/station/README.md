# Endless Station

Three endless, generative pages built only from public-domain music, with every instrument included:

| Page | What it plays |
|---|---|
| **Aggregate Field** (`www/aggregate/`) | Modern methods after Cage, Feldman, Babbitt, Eno, Roach, Glass, Riley, Adams, Pärt, Reich, Fripp (Frippertronics) and the public-domain modernists, often several at once on their own clocks, on the full orchestra plus keyboards, mallets, plucked strings, percussion, electronics, a chorus and a soprano |
| **Endless Symphony** (`www/symphony/`) | New symphonies planned after the public-domain catalog, on the orchestra |
| **Endless Sonata** (`www/sonata/`) | New piano pieces grown from 400 works and the Beethoven favorites, on 17 keyboard, mallet and plucked instruments |

## Two ways to use it

**Just listen, anywhere.** Double-click `www/index.html` (or any page's `index.html`). Everything loads from this folder, so it works with no internet and no server. Settings are saved in that browser.

**In the background.** Aggregate Field's **Background play** setting (Orchestra tab, on by default) keeps the music going while other apps are in front. It plays through the browser's media player, so the keyboard's play/pause key and Control Center's Now Playing control it. While the page is hidden it renders the music about a minute and a half ahead and queues it as finished audio, so a browser that slows background tabs can't interrupt it, and it restarts the audio by itself if the system suspends it. It mixes with other apps' audio on a computer. On an iPhone or iPad, open the page in Safari and start it there before switching apps; phones may still pause web audio in the background.

**As a station on the Mac mini.** Double-click **`Endless Station.command`**. It starts a small server on `http://127.0.0.1:8787` (this Mac only, nothing is exposed to the network) and opens Aggregate Field in its own Chrome window, which is allowed to start playing by itself and keeps time when it's in the background. Then, before every new piece or movement:

1. The page lowers the music and sends the announcement to the station.
2. The station speaks it: through **Dispatch** when the Dispatch announcer is running, or in the same voice Dispatch uses (macOS `say`, system voice, 190 words a minute) when it isn't.
3. The station writes `now_playing.txt` (one line, for an OBS text source) and `now_playing.json`.
4. The page waits for the announcement to finish, then the music starts.

Closing the Terminal window stops the station. The first time, macOS may ask before running a downloaded script: right-click the file, choose **Open**, then **Open** again.

### How Dispatch is used

The station adds a line to the newest `ExternalRadio/logs/block_*.txt`, the same way radiot adds tracks:

```
[08:19:27] [ENDLESS] Aggregate Field. Field Study No. 3. First movement, Slow field. Durations after Feldman…
  http://127.0.0.1:8787/aggregate/
```

Dispatch then speaks it as it does every track ("Endless. Aggregate Field…"). If the newest block has already been closed (`— end of block —`), the station starts a fresh block file rather than writing past the end. It decides Dispatch is running when `logs/dispatch_announcer.log` has been written in the last 6½ minutes (the announcer writes at least every 5).

The station looks for `logs/` in the folder above it, so keep this folder inside `ExternalRadio`. Otherwise it uses `~/ExternalRadio`, or pass `--radio /path/to/ExternalRadio`.

### Options

Add these after `station.py` in `Endless Station.command`, or run `python3 station.py …` in Terminal:

| Option | Effect |
|---|---|
| `--mode auto` | Dispatch when it's running, otherwise the Mac voice (default) |
| `--mode dispatch` | Always write to Dispatch's block file |
| `--mode say` | Always speak here, even when Dispatch is running |
| `--mode off` | No announcements; now_playing.txt is still written |
| `--page sonata` / `symphony` / `aggregate` / `home` | Which page opens |
| `--voice Samantha --rate 175` | Voice and speed when speaking here (`say -v '?'` lists the voices) |
| `--port 8787` | Server port (the next free one is used if it's taken) |
| `--lag 2.5` | Seconds Dispatch takes to notice a new line (added to the pause before the music) |
| `--tag ENDLESS` | Tag written on Dispatch lines |
| `--say-test "Testing."` | Speak one line and exit, to hear the voice |

When a page is opened through the station, its **Broadcast station** field (next to Your instruments) shows where announcements go and has a switch to turn them off for that page.

### Getting it into a stream

Announcements and music come out of the Mac's audio output, so OBS picks them up with **Desktop Audio**. To capture only the station, route Chrome and the voice through a loopback device such as BlackHole and add it as an audio input in OBS. Add `now_playing.txt` as a **Text (read from file)** source for an on-screen title.

## Settings carry over

Every control on every page (instruments, tuning, form choices, reverb, the station switch, your own instruments' assignments) is saved as you change it and restored the next time the page opens:

- in the browser that opened it,
- in `settings/<page>.json` here when the page is opened through the station, so the station's Chrome window, any other browser and a fresh profile all start where you left off,
- in your claude.ai account when you use the published pages there.

Whichever copy was saved most recently wins.

## Your own instruments

The **Your instruments** field on each page (Orchestra tab in Aggregate Field and Endless Symphony, Stream tab in Endless Sonata) takes your own samples: WAV, AIFF, CAF, MP3, M4A/AAC, FLAC, OGG or Opus files, a folder of them, a `.zip`, an SFZ instrument, or a ThumbJam instrument export (the zip with a plist). Root notes come from the plist or SFZ, then the file names (`Pluck_C4.wav`, `Pluck_60.wav`), and are detected from the audio as a last resort. They're stored in that browser only and are never uploaded.

## What's inside

- `station.py`: the server (Python 3.7+, standard library only)
- `www/`: the three pages, the landing page, `inst/` (one file per instrument), and the analysis models
- Created while running: `settings/`, `now_playing.txt`, `now_playing.json`, `station.log`, `chrome-profile/` (the station's own Chrome profile)

## Sound sources

All sampled instruments are CC0 or CC BY:

- Salamander Grand Piano by Alexander Holm (CC BY 3.0)
- Versilian Studios VSCO-2 Community Edition and the Versilian Community Sample Library (CC0)
- tonejs-instruments by Nicholaus Brosowsky (CC BY 3.0, built from the Philharmonia Orchestra and other free samples)
- The chorus and soprano in Aggregate Field: syllables (one to eight, do re mi fa sol la ti, ah) spoken by the Kokoro-82M speech model (hexgrad, Apache 2.0) and turned into sung notes with the WORLD vocoder (Masanori Morise, modified BSD)

Every score the generators learned from is in the public domain.
