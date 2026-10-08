#!/usr/bin/env bash
# Papertrade Bot setup (Mac/Linux): bash setup.sh
set -e
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python)
$PY -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" || { echo "Python 3.9+ required"; exit 1; }
[ -d .venv ] || $PY -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
python run.py sim --hours 6
echo "==== Setup complete ===="
