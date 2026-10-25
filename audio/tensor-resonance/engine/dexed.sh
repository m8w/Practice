#!/bin/bash
# Dexed helper (uses the engine's .venv when there is one):
#   ./dexed.sh --watch            show every Program Change / Bank Select / SysEx arriving on any MIDI input
#   ./dexed.sh --list             the Dexed cartridges found
#   ./dexed.sh --send Tensor      send Dexed one random voice as SysEx (never a Program Change)
cd "$(dirname "$0")"
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
exec "$PY" dexed.py "$@"
