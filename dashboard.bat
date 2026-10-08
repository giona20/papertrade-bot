@echo off
cd /d "%~dp0"
if not exist .venv ( echo Esegui prima setup.bat & pause & exit /b 1 )
call .venv\Scripts\activate.bat
start "" http://localhost:8765
python dashboard.py
