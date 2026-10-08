# Installing and testing the Papertrade Bot

This guide takes the bot from zero to launch-ready, with the tests to run in order. GUIDE.md explains the strategies; this file covers only the practical steps. Times are Rome time.

---

## Part 1: installation

### 1.1 Requirements
- Python 3.9 or newer (Anaconda's is fine). Check with `python --version`.
- A computer that stays on and online during the launch.

### 1.2 Install

**Windows, easiest way:** extract the zip into a new folder and double-click `setup.bat`. It creates the virtual environment, installs dependencies and runs a quick test. After that use `simulation.bat`, `start_bot.bat`, `dashboard.bat`, and `shell.bat` for terminal commands.

**Mac / Linux:** `bash setup.sh`, then `bash start_bot.sh`.

**By hand:** see README.md, "Installation (virtual environment)".

### 1.3 Check it works
```bash
python run.py sim --hours 24
```
It should print a report with RUSH and S3 results, time in regimes and the latest action card.

### 1.4 Dashboards
```bash
python dashboard.py                      # local, no extra dependencies: http://localhost:8765
python dashboard.py --db data/sim.db     # simulation data
```
Optional Streamlit version (also has a live on-chain mode):
```bash
pip install -r requirements-streamlit.txt
streamlit run streamlit_dashboard.py -- --db data/sim.db
```

### 1.5 Telegram (recommended)
1. Message @BotFather on Telegram, `/newbot`, copy the token.
2. Send any message to your new bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`.
3. Put them in `config.local.yaml` → `alerts`.
4. Test: `python run.py test-alert --config config.local.yaml`.

In phase 1 signals are manual: Telegram is the easiest way to get them on your phone.

---

## Part 2: tests before launch

### Test 1: full simulation
```bash
python run.py sim --hours 72
```
Check:
- **RUSH:** net cost per PAPER in pairs mode (with the on-chain curve, around $0.008).
- **Regimes:** the bot moves from INSOLVENT/BOOTSTRAP to DECAY or SWEEP as the simulated LP grows.
- **Haircut:** after a few closes it shows "fitted from trades" with b and K close to the simulation's hidden values (`sim.true_base_rate`, `sim.true_k`). This proves calibration works.

### Test 2: audit of the maths
```bash
python run.py audit --hours 24
```
Independently recomputes, trade by trade: PAPER minted (margin × emission on liquidations, loss × emission × 0.98 on losing closes with an empty queue), the haircut on wins, and the liquidation distance (1/leverage − 0.05%). Expect 0 wrong.

### Test 3: scenarios
```bash
python run.py scenarios --seeds 5 --hours 12
python run.py scenarios --param thresholds.drain_mode --values defensive,opportunistic --seeds 5 --hours 12
```
Runs your configuration in five scenarios: weak, strong or huge rush, whales draining the LP, a winning crowd with an insolvent LP. Pick settings that work in all of them, looking mostly at the "worst" column.

### Test 4: parameter sweeps
```bash
python run.py sweep --param <parameter> --values a,b,c --seeds 5 --hours 4
```
Reading the table: **cost/PAPER** lower is better; **PAPER** how many you mint; **worst PnL** your real risk.

| Sweep | What it decides |
|---|---|
| `--param haircut.target_max --values 0.25,0.35,0.45` | S3 pair distance: lower = wider pairs, cheaper PAPER, slower cycles |
| `--param strategies.farming.margin_usd --values 10,25,50` | Margin per S3 leg |
| `--param strategies.farming.max_cost_per_paper --values 0.003,0.004,0.005` | How much you pay per PAPER |
| `--param rush.pair_target_minutes --values 15,30,60` | Rush speed vs cost |
| `--param rush.mode --values pairs,burn` | Pairs or burn: watch the worst-PnL column |

**Warning:** simulation results depend on assumptions about the crowd. Use them to compare settings, not to predict profits.

### Test 5: live Hyperliquid prices
Keep `market_source: hyperliquid`, `protocol_source: sim`, `mode: dry_run`, then `python run.py run`. Let it run 2–3 hours. Check there are no network errors, that spread and oracle checks don't block BTC/ETH ("market not suitable" in the log), and that signals make sense. Stop with Ctrl+C; state stays in `data/papertrade.db`.

### Test 6: kill switch
```bash
python run.py kill      # the bot closes everything and stops
python run.py unkill
```
Try it while the bot is running, so you know how to stop it fast.

---

## Part 3: reading the real contracts (phase 0)

1. Deposit on each wallet and register the session key (see GUIDE.md §5).
2. `copy config.yaml config.local.yaml` (Mac/Linux: `cp`), then fill in `wallets.B/C.address` and `onchain.my_address`, and set `protocol_source: onchain`. Contract addresses are already in the config.
3. Check:
```bash
python run.py check-onchain --config config.local.yaml
```
Every mapped function prints its value. Before launch expect tracked LP 0, queue 0, emission 100/$ and your correct balance.

Tools for the unverified contracts:
```bash
python run.py probe                                   # Exchange: tries likely getter names
python run.py probe --address tokenomics              # or paper, staking, oracle...
python run.py abi-recover                             # lists function selectors and resolves known names
python run.py call "exchange:instruments(uint32)" 0   # raw call, prints every returned value
```

---

## Part 4: tests at launch

### Test 7: relayer confirmation times
For each order you execute by hand, note the seconds between sending and confirmation:
```bash
python run.py delay add --seconds 45 --notional 10000 --kind open
python run.py delay add --seconds 160 --notional 800 --kind close
python run.py delay show
```
The bot suggests values for `congestion.base_delay_s` and `small_extra_delay_s`.

### Test 8: emission check
After your first loss compare PAPER received with: liquidation → margin × 100; losing close with empty queue → loss × 0.98 × 100; losing close with active queue → loss × 100.

### Test 9: haircut calibration
Close 3 winning trades at different distances (about 0.3%, 1%, 2%) and record them (GUIDE.md §6):
```bash
python run.py calib add --move 0.01 --gross 14.25 --net 6.40
python run.py calib show
```
If the frontend shows the payout before closing, each preview is a free observation.

### Test 10: staking and rewards
After the first stake, `check-onchain` should show staked PAPER rising and `pendingReward` growing over time. Do a test claim to see where the USDC lands.

### Test 11: PAPER redemption
Once PAPER supply is above zero:
```bash
python run.py call "tokenomics:quoteRedemption(uint256)" 1000000000000000000
```
If it returns a value, that's what 1 PAPER redeems for: a floor price.

---

## Part 5: after launch, improving the strategy

1. **Re-run the sweeps** with measured values: real delays, the real haircut curve, real rush LP speed (`sim.rush_loss_per_hour`) and crowd losses (`sim.crowd_net_to_lp_per_hour`).
2. **Compare cost per PAPER** by strategy in the dashboard; keep the cheapest, disable the rest.
3. **Measure the staker share**: compare reward growth with LP growth and update `sim.staker_share_of_carve`.
4. **Review the budget daily**: the daily loss cap is your PAPER buying pace.

---

## Common problems

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError` | Virtual environment not active | Activate `.venv` and rerun `pip install -r requirements.txt` |
| `No time zone found with key Europe/Rome` | Missing time zone data (Windows) | `pip install tzdata` |
| `SyntaxError` after an update | Extracted over an old version | Extract into a new folder and reinstall |
| Constant network errors | Connection or Hyperliquid API limits | Raise `loops.fast_seconds` to 5 |
| No orders in `run` | Before launch, market not suitable, or kill switch on | Check the log in the dashboard or `status` |
| "Incomplete onchain configuration" | Missing addresses | Fill in `onchain` (Part 3) |
| Dashboard shows nothing | Different database from the bot's | `python dashboard.py --db data/papertrade.db` |
