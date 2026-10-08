@echo off
REM === Start the bot + dashboard in a second window (double-click) ===
cd /d "%~dp0"
if not exist .venv ( echo Run setup.bat first & pause & exit /b 1 )
set CFG=config.yaml
if exist config.local.yaml set CFG=config.local.yaml
start "Papertrade dashboard" cmd /k "cd /d %~dp0 && call .venv\Scripts\activate.bat && python dashboard.py --config %CFG%"
timeout /t 2 >nul
start "" http://localhost:8765
call .venv\Scripts\activate.bat
python run.py run --config %CFG%
pause
