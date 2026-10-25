# Mixer Recorder

Records the USB mixer and BlackHole (radiot) together as mixed lanes through a macOS Aggregate Device, at 48 kHz / 16-bit, stopping at 2 hours. It then EQs and loudness-normalizes the take to one WAV (−16 LUFS, −1 dBTP) and keeps no raw copy. A native folder picker with a New Folder button sets where it lands.

## Run

```
pip3 install sounddevice soundfile numpy && brew install ffmpeg
python3 mixer_record.py --howto   # Aggregate Device setup
python3 mixer_record.py --setup
python3 mixer_record.py
python3 mixer_record.py --process-only take.wav
```

## Source

Rebuilt from the chat it was made in (replaying every file write and edit in order), then syntax-checked.

Part of the [xik6 Audio Rack](https://m8w.github.io/Practice/).
