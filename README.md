# Papertrade Bot

A local monitoring and signal bot for [Papertrade](https://papertrade.xyz): synthetic perpetuals on Hyperliquid with up to 1000x leverage, where traders face a single LP and losses mint the PAPER token.

The bot reads the protocol state on-chain and Hyperliquid prices, classifies the protocol into a regime, and tells you what to do: open delta-neutral pairs to mint PAPER cheaply, cancel, close early before the payout queue, stake, claim. In phase 1 (frontend-only trading) it gives manual signals; from phase 2 it can execute.

- **GUIDE.md**: how the protocol works, every data point, every strategy, the launch procedure.
- **INSTALL_AND_TEST.md**: installation and the tests to run, in order.

> **Disclaimer.** Experimental software, not financial advice. The Papertrade contracts are unverified and upgradeable: function names and curve parameters are reconstructed from bytecode and on-chain data and may be wrong or change. Only use capital you can afford to lose.

## Installation (virtual environment)

Requires Python 3.9+ (Anaconda's is fine). Check with `python --version`.

Extract the zip into a new folder (or clone the repo), open a terminal in the folder that contains `run.py`, and follow the steps for your system.

### Windows — PowerShell
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py sim --hours 24
```
If `Activate.ps1` is blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or use cmd (below).

### Windows — Command Prompt (cmd)
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

### Every time you open a new terminal
Reactivate the environment before using the bot:
- PowerShell: `.venv\Scripts\Activate.ps1`
- cmd: `.venv\Scripts\activate.bat`
- Mac/Linux: `source .venv/bin/activate`

When active, the prompt starts with `(.venv)`. To leave: `deactivate`.

Optional on Windows: `setup.bat` installs everything with a double-click; `start_bot.bat`, `simulation.bat` and `dashboard.bat` run without activating the environment by hand.

## Personal configuration

`config.yaml` only contains public protocol addresses. Put your own data (wallets, Telegram, budgets) in a local copy that git ignores:

```bash
cp config.yaml config.local.yaml       # Windows: copy config.yaml config.local.yaml
# fill in wallets.B/C.address, onchain.my_address, alerts.telegram_*; set protocol_source: onchain
python run.py check-onchain --config config.local.yaml
python run.py run --config config.local.yaml
```

## Usage
```bash
python run.py sim --hours 72           # simulation + report
python run.py run                      # run the bot with config.yaml
python run.py status                   # action card and open positions
python dashboard.py                    # local dashboard: http://localhost:8765 (second terminal)
python dashboard.py --db data/sim.db   # dashboard on simulation data
python run.py audit --hours 24         # independent check of minting, haircut and liquidations
python run.py scenarios --seeds 3 --hours 12   # test the config across 5 market scenarios
python run.py sweep --param haircut.target_max --values 0.25,0.35,0.45   # compare parameter values
python run.py check-onchain            # verify on-chain reads
python run.py probe                    # read the Exchange without an ABI (likely getters)
python run.py abi-recover              # list contract function selectors and known names
python run.py call "exchange:instruments(uint32)" 0   # raw call, prints every returned value
python run.py calib add --move 0.01 --gross 14.25 --net 6.40   # record an observed haircut
python run.py delay add --seconds 45 --notional 10000          # record relayer delays
python run.py test-alert               # test Telegram
python run.py kill  /  python run.py unkill
```

## Streamlit dashboard (optional)

Besides the dependency-free local dashboard (`python dashboard.py`), there is a Streamlit version:

```bash
pip install -r requirements-streamlit.txt
streamlit run streamlit_dashboard.py                               # database from config.yaml
streamlit run streamlit_dashboard.py -- --db data/sim.db           # simulation results
streamlit run streamlit_dashboard.py -- --config config.local.yaml # your local config
```

It opens at http://localhost:8501. Pick the data source in the sidebar:
- **Live on-chain**: reads the Papertrade contracts and Hyperliquid prices directly (LP, queue, emission, PAPER, deposits, OI and caps per market). You can type a wallet to see its balance, queue and PAPER. This is the mode that works on **Streamlit Cloud**, where there is no bot database.
- **Local bot**: your bot's database (action card, positions, trades, log).

### Deploying on Streamlit Cloud
share.streamlit.io → New app → this repository → main file `streamlit_dashboard.py`. It opens in live mode and redeploys on every `git push`.

## Project layout
- `run.py`: command-line entry point (bot, simulation, tests, on-chain tools)
- `ptbot/market.py`: Hyperliquid prices (BBO, oracle, candles) and the price simulator
- `ptbot/protocol.py`: ABI-free on-chain reader and simulated LP with FIFO queue
- `ptbot/live.py`: public live snapshot of the protocol (used by the Streamlit dashboard)
- `ptbot/haircut.py`: haircut curve and calibration
- `ptbot/strategies.py`: regimes, strategies, rush mode, action card
- `ptbot/engine.py`: execution (dry run / live), relayer delays, queue guard, main loop
- `ptbot/infra.py`: SQLite store, Telegram alerts, risk checks
- `dashboard.py` / `streamlit_dashboard.py`: dashboards
- `config.yaml`: configuration (public addresses only) · `events.yaml`: event calendar for S2

## Common problems
- `ModuleNotFoundError`: the environment isn't active → reactivate it (see above).
- `No time zone found with key Europe/Rome`: `pip install tzdata`.
- `SyntaxError` after an update: you extracted over an old version → extract into a new folder and reinstall.
