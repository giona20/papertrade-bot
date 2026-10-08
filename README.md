# Papertrade Bot (locale)

Bot di monitoraggio e segnali per Papertrade (perp sintetici su Hyperliquid, lancio 10/10/2026).

## Configurazione personale

`config.yaml` contiene solo indirizzi pubblici del protocollo. I tuoi dati (wallet, Telegram, budget) vanno in una copia locale, esclusa da git:

```bash
cp config.yaml config.local.yaml       # Windows: copy config.yaml config.local.yaml
# compila wallets.B/C.address, onchain.my_address, alerts.telegram_*
python run.py run --config config.local.yaml
```

Attenzione: `dashboard.py` legge `config.yaml`; con la config locale usa `python dashboard.py --config config.local.yaml`.

## Installazione (ambiente virtuale)

Requisito: Python 3.9 o superiore (va bene quello di Anaconda). Controlla con `python --version`.

Estrai lo zip in una cartella nuova, apri un terminale dentro la cartella del bot (quella con `run.py`) e segui i passi per il tuo sistema.

### Windows — PowerShell
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py sim --hours 24
```
Se `Activate.ps1` viene bloccato, esegui una volta `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, oppure usa il Prompt dei comandi (sotto).

### Windows — Prompt dei comandi (cmd)
```bat
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
python run.py sim --hours 24
```

### Mac / Linux
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py sim --hours 24
```

### Ogni volta che apri un terminale nuovo
L'ambiente va riattivato prima di usare il bot:
- PowerShell: `.venv\Scripts\Activate.ps1`
- cmd: `.venv\Scripts\activate.bat`
- Mac/Linux: `source .venv/bin/activate`

Quando è attivo, la riga del terminale inizia con `(.venv)`. Per uscire: `deactivate`.

## Uso
```bash
python run.py sim --hours 72           # simulazione + report
python run.py run                      # bot con la configurazione di config.yaml
python dashboard.py                    # dashboard locale: http://localhost:8765 (secondo terminale)
python dashboard.py --db data/sim.db   # dashboard sui dati della simulazione
python run.py status                   # scheda azioni e posizioni aperte
python run.py audit --hours 24         # verifica indipendente di conio, trattenuta e liquidazioni
python run.py scenarios --seeds 3 --hours 12   # prova la config su 5 scenari di mercato
python run.py sweep --param rush.pair_target_minutes --values 5,15,30   # confronto parametri
python run.py probe                    # legge il contratto Exchange anche senza ABI (getter probabili)
python run.py check-onchain            # dall'8/10: verifica lettura contratto
python run.py calib add --move 0.01 --gross 14.25 --net 12.10           # trattenuta osservata
python run.py delay add --seconds 45 --notional 10000                   # ritardi relayer
python run.py test-alert               # prova Telegram
python run.py kill  /  python run.py unkill
```

## Dashboard Streamlit (opzionale)

Oltre alla dashboard locale senza dipendenze (`python dashboard.py`), c'è una versione Streamlit:

```bash
pip install -r requirements-streamlit.txt
streamlit run streamlit_dashboard.py                               # database in config.yaml
streamlit run streamlit_dashboard.py -- --db data/sim.db           # risultati di una simulazione
streamlit run streamlit_dashboard.py -- --config config.local.yaml # con la tua config locale
```

Si apre su http://localhost:8501 e si aggiorna ogni 5 secondi (`-- --refresh 10` per cambiarlo). Mostra gli stessi dati: regime, metriche, scheda azioni, grafico LP/coda, posizioni, risultati per strategia, trade e registro.

## Documentazione
- **INSTALLAZIONE_E_TEST.md**: test in ordine dal pre-lancio al lancio.
- **GUIDA.md**: ogni dato, strategia e la procedura del giorno di lancio.

## Problemi comuni
- `ModuleNotFoundError`: l'ambiente non è attivo → riattivalo (vedi sopra).
- `No time zone found with key Europe/Rome`: `pip install tzdata`.
- `SyntaxError` dopo un aggiornamento: hai estratto sopra la versione vecchia → estrai in una cartella nuova e rifai l'installazione.

Facoltativo su Windows: `setup.bat` fa l'installazione con un doppio clic, e `avvia_bot.bat`, `simulazione.bat`, `dashboard.bat` avviano tutto senza attivare l'ambiente a mano.

## Avvertenze

Software sperimentale, non un consiglio finanziario. I contratti Papertrade non sono verificati: nomi delle funzioni e parametri della curva sono ricostruiti dal bytecode e possono cambiare (i contratti sono aggiornabili). Usa solo capitale che puoi perdere.
