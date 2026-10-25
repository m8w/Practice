# BlackHole Recorder

Records BlackHole 2ch (up to 2 hours) to WAV and MP3 in a folder you pick, while monitoring live in your headphones. Input and output run as two separate streams, and it refuses a monitor output that would feed back into BlackHole.

## Run

```
pip3 install sounddevice soundfile numpy
brew install portaudio ffmpeg
python3 blackhole_recorder.py --list-devices
python3 blackhole_recorder.py
```

## Source

Rebuilt from the chat it was made in (replaying every file write and edit in order), then syntax-checked.

Part of the [xik6 Audio Rack](https://m8w.github.io/Practice/).
