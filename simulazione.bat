@echo off
REM === Simulazione di 24 ore + dashboard — doppio clic ===
cd /d "%~dp0"
if not exist .venv ( echo Esegui prima setup.bat & pause & exit /b 1 )
call .venv\Scripts\activate.bat
python run.py sim --hours 24
start "" http://localhost:8765
python dashboard.py --db data/sim.db
