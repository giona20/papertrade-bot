@echo off
REM === Avvia il bot + dashboard in una seconda finestra — doppio clic ===
cd /d "%~dp0"
if not exist .venv ( echo Esegui prima setup.bat & pause & exit /b 1 )
start "Dashboard Papertrade" cmd /k "cd /d %~dp0 && call .venv\Scripts\activate.bat && python dashboard.py"
timeout /t 2 >nul
start "" http://localhost:8765
call .venv\Scripts\activate.bat
python run.py run
pause
