#!/bin/bash
# Double-click this file in Finder to launch Λεξιλόγιο.
# A Terminal window opens, the server starts, and the browser opens automatically.

# Always run from the directory this script lives in
cd "$(dirname "$0")"

# Pick python3 — try conda first, then system python
PYTHON=""
for candidate in \
    "$HOME/opt/anaconda3/bin/python3" \
    "$HOME/anaconda3/bin/python3" \
    "$HOME/miniforge3/bin/python3" \
    "$(which python3 2>/dev/null)"; do
  if [ -x "$candidate" ]; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "❌  Could not find python3. Install Python and try again."
  read -p "Press Enter to close..."
  exit 1
fi

PORT=5003
URL="http://localhost:$PORT"

# Kill any stale server already on this port
if lsof -ti :$PORT &>/dev/null; then
  echo "  ⚠️  Stopping old server on port $PORT..."
  lsof -ti :$PORT | xargs kill -9 2>/dev/null
  sleep 0.5
fi

echo ""
echo "  🇬🇷  Λεξιλόγιο — Greek Trainer"
echo "  $URL"
echo "  Press Ctrl+C to stop"
echo ""

# Open browser after a short delay (gives Flask time to start)
(sleep 1.5 && open "$URL") &

"$PYTHON" app.py
