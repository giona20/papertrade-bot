@echo off
REM === Papertrade Bot setup (Windows, double-click) ===
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3.9+ from python.org and tick "Add to PATH".
  pause & exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"
if errorlevel 1 (
  echo Python 3.9 or newer is required. Current version:
  python --version
  pause & exit /b 1
)
if not exist .venv (
  echo Creating the virtual environment .venv ...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip -q
echo Installing dependencies ...
pip install -r requirements.txt -q
if errorlevel 1 (
  echo Dependency install failed. Read the error above.
  pause & exit /b 1
)
echo Quick test (6-hour simulation) ...
python run.py sim --hours 6
if errorlevel 1 (
  echo The test failed. Copy the error above.
  pause & exit /b 1
)
echo.
echo ==== Setup complete ====
echo You can now use: simulation.bat, start_bot.bat, dashboard.bat, shell.bat
pause
