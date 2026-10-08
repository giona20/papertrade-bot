@echo off
REM === Opens a terminal with the environment already active, for commands (calib, delay, sweep, kill...) ===
cd /d "%~dp0"
if not exist .venv ( echo Run setup.bat first & pause & exit /b 1 )
cmd /k ".venv\Scripts\activate.bat && echo Environment active. Examples: python run.py status ^| python run.py kill ^| python run.py calib show"
