#!/bin/bash
# Keeps Dexed running: if it quits or crashes, it is opened again within a few seconds
# (in the background, so it doesn't steal focus from what you're streaming).
# Double-click this file, or run it in Terminal. Close the window (or Ctrl+C) to stop.
# Every restart is logged to ~/Music/TensorResonance/dexed_restarts.log
APP="${1:-Dexed}"
LOG="$HOME/Music/TensorResonance/dexed_restarts.log"
mkdir -p "$(dirname "$LOG")"
echo "Keeping $APP running (checks every 5 s). Close this window to stop."
echo "Restarts are logged to $LOG"
while true; do
  if ! pgrep -xq "$APP"; then
    echo "$(date '+%Y-%m-%d %H:%M:%S')  $APP was not running -> opening it" | tee -a "$LOG"
    open -g -a "$APP" || { echo "could not open $APP (is it in /Applications?)"; sleep 30; }
    sleep 10
  fi
  sleep 5
done
