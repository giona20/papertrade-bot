@echo off
REM === 24-hour simulation + dashboard (double-click) ===
cd /d "%~dp0"
if not exist .venv ( echo Run setup.bat first & pause & exit /b 1 )
call .venv\Scripts\activate.bat
python run.py sim --hours 24
start "" http://localhost:8765
python dashboard.py --db data/sim.db
