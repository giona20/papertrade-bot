@echo off
REM === Apre un terminale con l'ambiente gia' attivo, per i comandi (calib, delay, sweep, kill...) ===
cd /d "%~dp0"
if not exist .venv ( echo Esegui prima setup.bat & pause & exit /b 1 )
cmd /k ".venv\Scripts\activate.bat && echo Ambiente attivo. Esempi: python run.py status ^| python run.py kill ^| python run.py calib show"
