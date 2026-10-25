#!/bin/bash
# Double-click to open the DUDLEY window. Public domain (CC0).
cd "$(dirname "$0")"

candidates=(
  "$HOME/anaconda3/bin/python3" "$HOME/opt/anaconda3/bin/python3" "/opt/anaconda3/bin/python3"
  "$HOME/miniconda3/bin/python3" "$HOME/myenv/bin/python3"
  "/Library/Frameworks/Python.framework/Versions/Current/bin/python3"
  "/opt/homebrew/bin/python3" "/usr/local/bin/python3" "$(command -v python3)"
)

pick=""
for p in "${candidates[@]}"; do
  [ -x "$p" ] || continue
  if "$p" -c "import tkinter, numpy, scipy; tkinter.Tk().destroy()" >/dev/null 2>&1; then
    pick="$p"; break
  fi
done

if [ -z "$pick" ]; then
  # a Python with Tk but missing numpy/scipy: install them for this user, once
  for p in "${candidates[@]}"; do
    [ -x "$p" ] || continue
    if "$p" -c "import tkinter" >/dev/null 2>&1; then
      echo "Installing numpy + scipy for $p (one time)..."
      "$p" -m pip install --user numpy scipy && pick="$p" && break
    fi
  done
fi

if [ -z "$pick" ]; then
  osascript -e 'display dialog "DUDLEY needs Python 3 with Tk. Install it from python.org (the macOS installer includes Tk), then double-click this again." buttons {"OK"} with title "DUDLEY"'
  exit 1
fi

command -v ffmpeg >/dev/null 2>&1 || echo "note: ffmpeg not found, so DUDLEY will read WAV files only."
echo "DUDLEY using $pick"
exec "$pick" dudley_gui.py "$@"
