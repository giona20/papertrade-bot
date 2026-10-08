#!/usr/bin/env bash
# Start bot + dashboard (Mac/Linux): bash start_bot.sh
cd "$(dirname "$0")"
source .venv/bin/activate
CFG=config.yaml; [ -f config.local.yaml ] && CFG=config.local.yaml
python dashboard.py --config "$CFG" & DASH=$!
trap "kill $DASH" EXIT
echo "Dashboard: http://localhost:8765"
python run.py run --config "$CFG"
