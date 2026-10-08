@echo off
REM === Installazione Papertrade Bot (Windows) — doppio clic ===
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python non trovato. Installa Python 3.9+ da python.org e spunta "Add to PATH".
  pause & exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"
if errorlevel 1 (
  echo Serve Python 3.9 o superiore. Versione attuale:
  python --version
  pause & exit /b 1
)
if not exist .venv (
  echo Creo l'ambiente virtuale .venv ...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip -q
echo Installo le dipendenze ...
pip install -r requirements.txt -q
if errorlevel 1 (
  echo Installazione dipendenze fallita. Leggi l'errore sopra.
  pause & exit /b 1
)
echo Test rapido (simulazione di 6 ore) ...
python run.py sim --hours 6
if errorlevel 1 (
  echo Il test non e' andato a buon fine. Copia l'errore sopra.
  pause & exit /b 1
)
echo.
echo ==== Installazione completata ====
echo Ora puoi usare: simulazione.bat, avvia_bot.bat, dashboard.bat
pause
