#!/bin/bash
# ============================================================
# FoodShield AI — local launcher
# ------------------------------------------------------------
# Double-click this file to run FoodShield correctly.
#
# WHY: opening index.html directly (file://) makes the browser BLOCK every
# data fetch (CORS: "Cross origin requests are only supported for http/https").
# That's why the Trade Flow Atlas showed empty/fabricated beef data. A local
# web server serves the files over http:// so the data loads normally — exactly
# like the live Vercel site.
# ============================================================
cd "$(dirname "$0")" || exit 1
PORT=8000
# pick a free port if 8000 is taken
while lsof -i :$PORT >/dev/null 2>&1; do PORT=$((PORT+1)); done

echo "Starting FoodShield on http://localhost:$PORT ..."
# open the browser a moment after the server starts
( sleep 1; open "http://localhost:$PORT/index.html" ) &

# serve with no-cache headers so you always get the latest build
python3 -m http.server $PORT
