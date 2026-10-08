#!/usr/bin/env bash
# Avvia bot + dashboard (Mac/Linux): bash avvia_bot.sh
cd "$(dirname "$0")"
source .venv/bin/activate
python dashboard.py & DASH=$!
trap "kill $DASH" EXIT
echo "Dashboard: http://localhost:8765"
python run.py run
