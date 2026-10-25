#!/bin/bash
# Surge XT patch changer: opens the page and sends Surge OSC. Needs only Python 3 (no installs).
#   ./surge.sh                       Surge on this Mac
#   ./surge.sh --host 192.168.1.20   Surge on another Mac on your network (it must have OSC input on)
cd "$(dirname "$0")"
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
exec "$PY" surge.py --serve "$@"
