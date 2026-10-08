@echo off
cd /d "%~dp0"
if not exist .venv ( echo Run setup.bat first & pause & exit /b 1 )
set CFG=config.yaml
if exist config.local.yaml set CFG=config.local.yaml
call .venv\Scripts\activate.bat
start "" http://localhost:8765
python dashboard.py --config %CFG%
