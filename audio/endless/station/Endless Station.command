#!/bin/bash
# Endless Station — double-click to start. Closing this window stops the station.
# Options can be added after station.py below, e.g.  --mode say   --page symphony   --voice Samantha
cd "$(dirname "$0")" || exit 1
if ! /usr/bin/env python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)' >/dev/null 2>&1; then
  osascript -e 'display alert "Endless Station needs Python 3" message "Open Terminal and run:  xcode-select --install   (or install Python from python.org), then double-click Endless Station again."' >/dev/null 2>&1
  echo "Python 3.7 or newer is needed. Run: xcode-select --install"
  exit 1
fi
echo "Endless Station — close this window to stop it."
exec /usr/bin/env python3 station.py --chrome "$@"
