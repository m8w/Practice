#!/bin/bash
# run_all.sh -- rework every long video in a folder into a new video of the same length.
#
#   bash ~/Music/xik6_mosaic/run_all.sh "/Volumes/Seagate/your folder"            start / resume (gentle)
#   bash ~/Music/xik6_mosaic/run_all.sh "/Volumes/Seagate/your folder" fast       start / resume (full speed)
#   bash ~/Music/xik6_mosaic/run_all.sh "/Volumes/Seagate/your folder" status     what's done / part-way / not started
#   bash ~/Music/xik6_mosaic/run_all.sh "/Volumes/Seagate/your folder" watch      follow the progress live
#   bash ~/Music/xik6_mosaic/run_all.sh "/Volumes/Seagate/your folder" stop       stop it (resume later with the first line)
#
# New videos are saved next to the originals as  xik6_remix_<original name>_remix.mp4
# Originals are only read, never changed. Finished videos are never redone.

DIR="$(cd "$(dirname "$0")" && pwd)"
FOLDER="${1%/}"
WHAT="${2:-start}"
LOG="$DIR/run_all.log"
# working files live on the SAME drive as the videos (never the Mac's internal disk)
CACHE="$FOLDER/.xik6_cache"
PIDFILE="$DIR/run_all.pid"

if [ -z "$FOLDER" ] || [ ! -d "$FOLDER" ]; then
  echo "Usage: bash $0 \"/Volumes/Seagate/your folder\" [fast|status|watch|stop]"
  echo "(tip: type 'bash $0 ' then drag the folder into this window)"
  exit 1
fi

running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

case "$WHAT" in
  status)
    python3 "$DIR/xik6_mosaic.py" full --corpus "$FOLDER" --cache-dir "$CACHE" --status
    running && echo "  (a job is running now -- 'watch' to follow it)" || echo "  (nothing running now)"
    exit 0 ;;
  watch)
    echo "(press Ctrl-C to stop watching -- the job keeps running)"
    tail -n 30 -f "$LOG"; exit 0 ;;
  stop)
    if running; then
      kill "$(cat "$PIDFILE")" 2>/dev/null; pkill -P "$(cat "$PIDFILE")" 2>/dev/null
      pkill -f "xik6_mosaic.py full" 2>/dev/null
      rm -f "$PIDFILE"; echo "stopped. Run the start line again later to carry on where it left off."
    else
      echo "nothing is running."
    fi
    exit 0 ;;
esac

if running; then
  echo "already running (pid $(cat "$PIDFILE")). Use: bash $0 \"$FOLDER\" watch"
  exit 0
fi

# ---- check the tools once
command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg is missing. Install it with:  brew install ffmpeg   (or: sudo port install ffmpeg)"; exit 1; }
if ! python3 -c "import numpy, cv2" 2>/dev/null; then
  echo "installing numpy + opencv for python3 (one time) ..."
  python3 -m pip install --user --quiet numpy opencv-python || { echo "could not install; run: python3 -m pip install numpy opencv-python"; exit 1; }
fi

# ---- room check: about 4 GB of working files per 11-hour video + about 30 GB per finished video
FREE_GB=$(df -g "$FOLDER" | awk 'NR==2{print $4}')
echo "free space on the video drive: ${FREE_GB} GB"
if [ -n "$FREE_GB" ] && [ "$FREE_GB" -lt 150 ]; then
  echo "Not enough room on that drive to start safely (want at least 150 GB free)."; exit 1
fi

SPEED=""
[ "$WHAT" = "fast" ] && SPEED="--full-speed"

echo "=== $(date)  starting on: $FOLDER  ${SPEED:-(gentle)}" >> "$LOG"
# caffeinate keeps the Mac awake while it works; nohup keeps it going if Terminal closes
nohup caffeinate -i python3 -u "$DIR/xik6_mosaic.py" full --corpus "$FOLDER" \
      --cache-dir "$CACHE" $SPEED >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"
sleep 3
python3 "$DIR/xik6_mosaic.py" full --corpus "$FOLDER" --cache-dir "$CACHE" --status
echo
echo "Running in the background. You can close this window."
echo "  follow it:   bash $0 \"$FOLDER\" watch"
echo "  check it:    bash $0 \"$FOLDER\" status"
echo "  stop it:     bash $0 \"$FOLDER\" stop"
