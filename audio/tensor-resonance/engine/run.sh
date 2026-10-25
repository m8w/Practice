#!/bin/bash
# Tensor Resonance Engine launcher: keeps its own Python environment in .venv
# so it never touches (or is broken by) Anaconda's base packages.
cd "$(dirname "$0")"
PY=python3
command -v $PY >/dev/null || PY=python
if [ ! -f .venv/.ready ]; then
  echo "first run: creating .venv and installing requirements (about a minute)..."
  rm -rf .venv
  $PY -m venv .venv || { echo "could not create a virtual environment with $PY"; exit 1; }
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r requirements.txt || { echo "core install failed (see above)"; rm -rf .venv; exit 1; }
  .venv/bin/python -m pip install -q "mido>=1.3" "python-rtmidi>=1.5" \
    || echo "NOTE: MIDI packages did not install - Python MIDI (--midi) is off; browser Web MIDI still works."
  touch .venv/.ready
fi
exec .venv/bin/python app.py "$@"
