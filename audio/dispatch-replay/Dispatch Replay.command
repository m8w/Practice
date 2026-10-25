#!/bin/bash
# Double-click to start Dispatch Replay. Its control page opens in your browser.
# Close this window (or press Control-C) to stop it.
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:/opt/local/bin:$HOME/miniforge3/bin:$PATH"
PY=""
for p in /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 "$HOME/miniforge3/bin/python3" /usr/bin/python3; do
  if [ -x "$p" ]; then PY="$p"; break; fi
done
[ -z "$PY" ] && PY="$(command -v python3)"
if [ -z "$PY" ]; then
  echo "Python 3 was not found. Install it from python.org, then double-click this again."
  read -r -p "Press Return to close."
  exit 1
fi
exec "$PY" dispatch_replay.py "$@"
