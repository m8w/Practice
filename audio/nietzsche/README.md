# nietzsche.py

Wahnzettel sequencer for the Novation Supernova II. The 28 Turin notes (31 Dec 1888 – 6 Jan 1889) drive 8 generative parts. Program changes are rationed with a minimum gap so the SN2's DSP is never thrashed.

## Run

```
pip3 install mido python-rtmidi
python3 nietzsche.py --list-ports
python3 nietzsche.py --dry-run --speed 60
python3 nietzsche.py --port "Supernova II"
python3 nietzsche.py --port "Supernova II" --panic
```

## Source

Rebuilt from the chat it was made in (replaying every file write and edit in order), then syntax-checked; a dry run confirms the program-change gap floor is respected.

Part of the [xik6 Audio Rack](https://m8w.github.io/Practice/).
