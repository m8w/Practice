# Dispatch Replay

Re-airs finished recordings. It watches your recording folders, waits until a file has stopped growing, and lists it on a local control page where you can play, queue and air it, either by scripting OBS or through its own ffmpeg broadcaster. `Days On End.html` is the generative backdrop it shows behind audio-only recordings. See `READ ME.txt` for the full guide.

## Run

```
Double-click "Dispatch Replay.command" (right-click → Open the first time)
python3 dispatch_replay.py --check   # show what it found and exit
```

## Source

Rebuilt from the chat it was made in (replaying every file write and edit in order), then syntax-checked, including the 13 patch scripts applied during the build. Its rebuilt Days On End engine matches the published version byte for byte.

Part of the [xik6 Audio Rack](https://m8w.github.io/Practice/).
