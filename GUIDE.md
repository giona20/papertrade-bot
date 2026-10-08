# Papertrade Bot: operating guide

This guide explains what the bot does, every data point it monitors, every strategy, and the launch procedure. All times are Rome time (Europe/Rome).

> Experimental software, not financial advice. The Papertrade contracts are unverified and upgradeable; several values below are reconstructed from on-chain data and may be wrong. Only use capital you can afford to lose.

---

## 1. How the bot works

The bot runs locally and has three parts.

**Monitor.** Reads two data sources:
- Hyperliquid: the BBO (best bid and ask) that Papertrade prices from, plus mark price, oracle price and 1-minute candles.
- The Papertrade contracts on HyperEVM: LP, FIFO queue, PAPER minted and staked, rewards, your balances.

**Strategy engine.** Classifies the protocol into a regime, estimates the cost and EV of each strategy, and generates orders.

**Execution + risk.** Every order goes through risk checks. It is then simulated (dry run), sent as a manual signal (phase 1) or sent to the contract (live, from phase 2).

There are two loops:
- **Fast, every 2 seconds.** Checks open positions: take profit, stop, liquidation, max hold time.
- **Slow, every 60 seconds** (10 during the rush). Updates protocol state and regime, writes the action card and opens new positions.

The **action card** is the main output: at any moment it tells you the regime and what to do with each strategy and with PAPER.

---

## 2. Commands

```bash
python run.py sim --hours 72     # full offline test, no risk
python run.py run                # start with config.yaml (use --config config.local.yaml for your own)
python run.py status             # latest action card + open positions
python dashboard.py              # local dashboard: http://localhost:8765
python run.py calib show         # estimated haircut curve
python run.py kill / unkill      # close everything and stop / re-enable
```

Main modes, set in `config.yaml`:

| Use | mode | market_source | protocol_source |
|---|---|---|---|
| Offline test | dry_run | sim | sim |
| Live prices, simulated LP | dry_run | hyperliquid | sim |
| Real data, manual signals (phase 1) | dry_run | hyperliquid | onchain |
| Real orders (phase 2+) | live | hyperliquid | onchain |

---

## 3. Launch phases and what to do in each

The launch announcement gave no time zone. If it is UTC, add 2 hours for Rome time.

| Phase | When | What happens | What you do | What the bot does |
|---|---|---|---|---|
| 0 Predeposit | from 08/10 | Deposits and account creation only, trading paused | Deposit and create the account now: after launch deposits get low priority | Monitors, card says "DEPOSIT NOW" |
| 1 Frontend only | from 10/10 | Orders only through papertrade.xyz and whitelisted relayers; intent system prioritised by type and notional; PAPER not transferable | Execute the bot's signals by hand; cancel orders pending too long | MANUAL signals on console/Telegram/dashboard, simulated delays |
| 2 Open contract | after congestion (date TBA) | Anyone (human, contract, agent) can open atomic positions on-chain | Wire `LiveExecutor`, switch to `mode: live` | Automatic execution |
| 3 Builder codes | later | Frontends receive 1% of the LP carve. No cost to users | Nothing | — |
| 4 PAPER transferable | unspecified | PAPER can be bought and sold | Full PAPER management | Uses the market price |

**What phase 1 means for the strategies**
- **Delays.** Small-notional orders confirm last. Prefer fewer orders with more notional: raise margin, not leverage (high leverage means a tight distance and a worse haircut).
- **Asymmetric priority.** Liquidations are processed before other actions. Your TP or stop may wait while a liquidation goes through, so phase 1 favours wider liquidation distances.
- **Cancellable orders.** A pending order can be cancelled. If price has already moved past your TP while waiting, consider cancelling the open.
- **PAPER can't be sold.** Its only value is staking rewards.
- **No shortcuts.** The bot does not automate the frontend in phase 1: the protocol wants real frontend users first, and bypassing it risks exclusion.

---

## 4. Wallets and capital

| Wallet | Role | Why |
|---|---|---|
| A | Rush in burn mode (optional) | Used only if the bot picks burn mode (one side per asset) |
| B | Long side | Long legs of pairs, S1/S3 longs, S2 long leg |
| C | Short side | Short legs of pairs, S1/S3 shorts, S2 short leg |
| Reserve | — | Never connect it to the site |

**Why separate long and short wallets.** On Papertrade one account cannot hold a long and a short on the same asset. Pairs and straddles therefore need two accounts. Every position has its own fixed, isolated margin, so within one wallet you can open as many positions as you like on the same side.
- The bot assigns each order to the right wallet and names the wallet in the signal.
- It rejects orders that would put opposite sides on the same asset in the same wallet.

**PAPER stays where it is minted.** Until phase 4 it isn't transferable, so stake from each wallet that mints it.

**Notional** = position value = margin × leverage. For example $10 at 1000x = $10,000. Relayer priority depends on notional, not margin.

**Deposits.** Send USDC to the personal deposit address shown by the site: from Hyperliquid spot (simplest), through the guided HyperEVM route, or via Circle CCTP from Arbitrum/Ethereum/Solana. A raw ERC-20 transfer to the proxy on HyperEVM is NOT detected. Keep a little HYPE on HyperEVM only for self-withdrawals.

---

## 5. Chronological checklist

**Before 08/10**
1. Create wallets B and C (A optional). Run `python run.py sim` and read the report.
2. Set up Telegram (`alerts`). Set budgets in `capital`, `rush.budget_usd`, `strategies.*.margin_usd`.
3. Run `run` with live prices and a simulated LP for a few hours to check connectivity, spreads and signals.

**08/10, phase 0 (top priority)**
1. On papertrade.xyz, for each wallet: Deposit and send USDC to the personal address.
2. Register the **session key** if the site offers it: then you sign orders without popups.
3. Copy `config.yaml` to `config.local.yaml`, fill in your wallet addresses and `onchain.my_address`, set `protocol_source: onchain`.
4. Run `python run.py check-onchain --config config.local.yaml`: you should see your balance, tracked LP 0 and emission 100/$.

**10/10, 30 minutes before launch**
1. Start the bot and a dashboard.
2. Open papertrade.xyz with both wallets connected, in separate windows or browser profiles.

**Operating rules from the docs**
- Markets at launch: BTC and ETH only, up to 1000x. Per-user cap: $10M.
- Each position has fixed margin: no adding margin, no partial closes. To scale up, open another position; to reduce, close and reopen smaller.
- One transaction can close up to 25 positions.
- Each signed order is valid for 1 hour; a session key lasts 7 days.

**At launch: the rush (about 1 hour)**
1. Execute the OPEN signals as they arrive, on the wallet indicated.
2. Execute CANCEL on orders still pending when told.
3. STAKE minted PAPER as soon as possible.

**After the rush: calibration**
1. If the curve parameters aren't confirmed, close 3 winning trades at different distances (about 0.3%, 1%, 2%) and record them (section 6).
2. Record relayer delays with `python run.py delay add` (see INSTALL_AND_TEST.md).
3. Then let S3 run on signals.

**Phase 2** (when announced): wire `LiveExecutor` and switch to `mode: live`.
**Phase 4** (when announced): fill in `phases.transferable_time`; PAPER sell signals turn on.

---

## 6. Recording the haircut

The official formula reduces to two effective parameters:

**haircut h = 1 − (1 − b) × m / (m + K)**, with m = move − 0.2 bps (anti-jitter deadband).

The bot fits b and K from the winning closes you record. For each winning close note:
- **Entry** and **exit**: the prices shown by the frontend (BBO mid).
- **Margin** and **leverage**.
- **Received**: profit credited, margin excluded.

Then compute **move** = exit/entry − 1 for a long (entry/exit − 1 for a short) and **gross** = margin × leverage × move.

**Example.** Long BTC, entry 100,000, exit 101,000 (move 0.01), margin $15, leverage 95x: gross = 15 × 95 × 0.01 = $14.25. Received $6.40.

```bash
python run.py calib add --move 0.01 --gross 14.25 --net 6.40
python run.py calib show
```

Haircut = 1 − 6.40 / 14.25 = 55%. There is no fee to separate: users pay nothing else.

---

## 7. Fees: confirmed, the user pays none

From the docs and the contracts:
- **Wins:** the only cost is the haircut (asymmetric impact) on realised profit. It doesn't depend on position size.
- **Losses:** you pay only the loss. The 2% loss fee is carved from the LP's gain, not from you:
  - queue empty: PAPER mints on loss − 2%;
  - queue active: no fee, PAPER mints on the full loss.
- **Liquidations:** PAPER always mints on the full margin.
- **Builder codes 1%:** comes from the LP carve, invisible to users.
- **Gas:** none on trades; only needed for self-withdrawals.

On-chain: `winFeeRate`, `lossFeeRate` and `liquidationFeeRate` all read 2%.

---

## 8. The data, one by one

### Protocol data

**Tracked LP** (`tokenomics.trackedLpUsd`): the LP that counts for emission. Starts at $0 and only grows from trader losses. Note: `exchange.treasury` is NOT the LP; it also holds user deposits.

**Queue** (`exchange.totalQueued`): profits owed to winners that the LP couldn't pay immediately, FIFO. The winner's margin always comes back at once; only the net profit is queued. Your queued profit (`userQueuedTotal`) can be used as margin for new positions.

**Effective LP = tracked LP + sideBucket − queue.** The docs' "net equity". The sideBucket collects losses paid while the queue is active, used to pay the queue through harvest. This number decides the regime.

**Emission (PAPER per $1)**
- Flat 100 while tracked LP is below $2M, even if negative.
- Above: 100 × (120M / (120M + H))², where H is the high-water mark of cumulative LP gain past $2M.
- The decay is slow: about 95/$ with $3M past the threshold, 88/$ with $8M, 70/$ with $23M, 51/$ with $48M.
- H never goes down: sweeps to stakers don't restore emission.
- On-chain values confirmed: threshold $2M, scale $120M, flat rate 100.

**PAPER supply and staked**: used to compute how much of the cash flow each staked token gets.

**Staker rewards (24h)**: USDC distributed to stakers from two sources: a share of LP revenue (haircuts and loss carves), and 100% of LP gain above $5M (the "sweep").

**PAPER price**: available only once PAPER trades on a secondary market (phase 4). Until then it's "n/a".

**Staking APR = 24h rewards × 365 / (PAPER staked × price).**

### Market data (Hyperliquid)

**BBO mid**: midpoint of the best bid and ask on Hyperliquid; Papertrade's open and close price, with no slippage.
**Spread (bps)**: bid-ask distance (1 bps = 0.01%). A wide spread means an unreliable mid: the bot won't open above `max_spread_bps`.
**Oracle divergence (bps)**: distance between mid and the Hyperliquid oracle. High means an anomalous or manipulated book: the bot won't open.
**1-minute volatility**: std. dev. of 1-minute returns over the last hour. Used by the noise rule: the liquidation distance must exceed the "normal" expected move.
**Breakout range**: high and low of the last N minutes (used by S1).

### Position data

**Liquidation distance = 1 / leverage − 0.05%.** From the docs, liquidation triggers about 5 bps before zero equity (on-chain: ~4.8 bps). At 1000x that's about 0.05%, at 100x about 0.95%. It's a hard bust: past that price you lose the whole margin.

**Haircut**: the share of gross profit the protocol keeps; highest on small moves, lower on large ones.

---

## 9. The haircut curve (important)

Each market's configuration (`exchange.instruments(id)`) holds eight values. Some are clear (max leverage 1000, liquidation buffer ~4.8 bps, the Hyperliquid asset index). Four match the curve parameters very well: rate multiplier 15,000, position multiplier 10,000,000, reference notional 100,000 and base rate 0.1. If that reading is right:

| Favourable move | Haircut |
|---|---|
| 0.1% | ~92% |
| 0.3% | ~79% |
| 1% | ~55% |
| 2% | ~40% |
| 3% | ~33% |
| 5% | ~25% |
| 10% | ~18% |

This is much heavier than a quick look at the docs suggests. Confirm it with the first real winning closes (section 6): if 1% gives a haircut around 55–65%, the reading is correct.

---

## 10. Regimes

| Regime | Condition | Meaning | Bot posture |
|---|---|---|---|
| INSOLVENT | queue > 0 or effective LP ≤ 0 | Profits wait, losses mint 100/$ | Mint, stake what you receive, no buying |
| BOOTSTRAP | effective LP < $2M | Max emission | Mint, stake what you receive, no buying |
| DECAY | $2M – $5M | Emission slowly declining | Buy PAPER only if APR ≥ threshold |
| SWEEP | ≥ $5M | All extra LP gain goes to stakers | Stake fully |

**DRAIN** is an extra flag, not a regime. It turns on when effective LP, after passing $2M, falls more than 30% from its peak: a whale drains the LP, emission rises again, PAPER's price falls and traders leave.

---

## 11. The key formula

Take a trade with take profit and stop at the same distance d (roughly 50/50, no edge). EV per $1 of margin:

**EV ≈ 0.5 × (E × P_eff − 1 + q × G × (1 − h(d)))**

- **E** = PAPER minted per $1 lost (100 below $2M).
- **P_eff** = PAPER price × (1 − liquidity discount).
- **q** = probability the queue pays (below 1 when INSOLVENT).
- **G** = gross profit per $ of margin at the TP = leverage × d. With leverage = 1 / (d + 0.05%), G ≈ d / (d + 0.0005).
- **h(d)** = haircut at that distance.

The **cost per PAPER** of such a trade (or a delta-neutral pair) is **(1 − q·G·(1 − h)) / E**. With the curve in section 9:

| Distance | Leverage | Haircut | Cost per PAPER |
|---|---|---|---|
| 0.3% | 286x | 79% | $0.0082 |
| 1% | 95x | 55% | $0.0057 |
| 2% | 49x | 40% | $0.0042 |
| 3% | 33x | 33% | $0.0034 |
| 5% | 20x | 25% | $0.0026 |
| 10% | 10x | 18% | $0.0019 |

A deliberate liquidation costs $0.01 per PAPER. **Cheap PAPER comes from wide pairs at low leverage, held for hours or days.** There's no hurry: emission stays at 100/$ until the real LP passes $2M and then declines slowly. The trade-off is time: a 3% move in BTC takes about a day on average, 5% a few days.

---

## 12. Strategies

### S0: launch rush (about the first hour)

**What happens.** At open the crowd liquidates on purpose to mint PAPER at 100 per $1. Under congestion that's rational: once the open confirms, liquidation is automatic and has top priority, while a profitable close waits.

**What the bot does.**
- Activates at the start of phase 1 and refreshes every 10 seconds.
- Measures LP growth speed and estimates future emission.
- Pauses S1 and S3.
- Picks between two modes (`rush.mode: auto`):
  - **PAIRS (default):** same asset, same size, long on B and short on C together. One leg liquidates and mints PAPER on the full margin; the other is in profit and closes at the TP (or liquidates too and mints).
  - **BURN:** single liquidations on wallet A at 1000x, no TP. Mints more PAPER per dollar but results are a lottery. Auto mode switches to burn only if emission is projected to drop more than 30% within a pair cycle, which with the real curve needs an enormous rush.
- Ends when emission falls below `rush.min_emission` (95/$) or after `max_minutes`.

**Cost.** With the on-chain curve, short pairs cost almost as much as burning (~$0.0085/PAPER). That's why `rush.budget_usd` defaults to a small amount ($150): keep the rest for wide S3 pairs.

**Signals.** OPEN (wallet and side included), CANCEL (when the rush would end before the order confirms), STAKE right away, RUSH OVER.

**OI caps.** Each instrument has a separate cap per side, about $5M of headroom above current OI. If an open fails on the cap, use the other asset.

**Valuation at mint.** The card shows supply × $0.01: what the whole supply "cost". A 100% yield would need that amount per year paid to stakers.

### S1: launch window (off by default)
- Small breakout positions in the first 7 days, TP and SL at the same distance.
- Scenario tests: same cost per PAPER as S3 but about twice the worst-case loss. Off (`strategies.early.enabled: false`).

### S2: event straddles (off by default)
- Long and short on the same asset around events listed in `events.yaml` (CPI, Fed, jobs), from 30 minutes before to 45 after. Liquidation at 35% of the expected move, winning leg TP at 120%.
- Simulated events are crude; enable it only for big events once there is real data.

### S3: continuous PAPER farming (main strategy)
Two modes (`strategies.farming.mode`):
- **price (default):** mint while the estimated cost per PAPER stays below `max_cost_per_paper` (default $0.004). You decide what PAPER is worth to you by setting the maximum price.
- **ev:** only when EV with an estimated PAPER value exceeds `min_ev_per_margin`. Very conservative before phase 4.

With `use_pairs: true` S3 opens long on B and short on C together: same expected cost, every cycle mints, much less variance. The distance comes from `haircut.target_max` (default ~3%, ~33x).

**Drain mode** (`thresholds.drain_mode`): `opportunistic` (default) keeps minting when a whale pushes the LP back below $2M (emission returns to 100/$) and only blocks PAPER purchases; `defensive` turns S1/S3 off.

### S4: PAPER management (action card)

| Situation | Action |
|---|---|
| Phases 1–3 (not transferable) | No buying or selling: stake everything you mint |
| INSOLVENT / BOOTSTRAP | Don't buy (supply growing fast), stake what you receive |
| DECAY | Buy and stake if APR ≥ `target_apr` (30%), otherwise wait |
| SWEEP | Stake everything |
| Rewards ≥ $5 | Claim |

### S5: defence (drain)
With the DRAIN flag on: PAPER purchases blocked, the card suggests trimming free PAPER; in `defensive` mode S1/S3 also stop.

### S6: queue guard (early close)
Profits are paid only if the LP has funds. The bot sends **CLOSE NOW (queue guard)** for winning trades when:
1. free LP is less than 3× open profits (everyone's if readable, otherwise yours);
2. the LP is falling fast enough not to cover profits before the close confirms;
3. the queue is active and growing (only for trades past halfway to their TP): closing now gets an earlier spot in the FIFO.

The margin always comes back; only the profit is at risk.

### S7: queue recycling
Queued balance can be used as margin, and losses funded with it mint PAPER on the destroyed credit. With at least $20 queued and a long queue (5+ entries) the card says **RECYCLE QUEUE**.

### When to sell PAPER (phase 4 only)

| Condition | Action |
|---|---|
| APR ≥ 30% | Hold and accumulate (DECAY/SWEEP) |
| APR between 15% and 30% | Hold, don't buy |
| APR < 15% | SELL half: the price discounts more than real cash flows |
| Price ≥ 2× your average cost | RECOUP CAPITAL: sell what you spent, keep the rest staked at zero cost |
| DRAIN | Trim free PAPER, don't buy |

Staking and unstaking are instant (no cooldown, no lockup). Rewards accrue in USDC and are claimed to your trading balance.

**PAPER redemption.** Tokenomics exposes `redeem(uint256)` and `quoteRedemption(uint256)`, which aren't in the docs. Before the first mint the quote reverts. Once supply exists, check `python run.py call "tokenomics:quoteRedemption(uint256)" 1000000000000000000`: if it pays something, that's a floor price for PAPER.

---

## 13. Risk and kill switch

An order is rejected if any of these is true:
- kill switch active (`KILL` file, `python run.py kill`);
- today's loss beyond `daily_loss_cap_usd` (Rome day; rush excluded);
- max open positions reached, margin above limit, or budget exhausted;
- spread or oracle divergence above threshold, or data older than 30 seconds.

Alerts to watch: regime change, drain, queue up >50% in an hour, network errors. They go to the console and, if configured, Telegram.

---

## 14. Main parameters (config.yaml)

| Parameter | What it controls |
|---|---|
| haircut.target_max | Max accepted haircut: lower means wider distance, lower leverage, longer trades |
| haircut.base_rate / k | Curve assumption until calibrated or read exactly |
| strategies.farming.max_cost_per_paper | The most you pay for one PAPER |
| strategies.farming.margin_usd | Margin per leg of S3 pairs |
| rush.budget_usd | Total you accept to lose in the rush |
| capital.daily_loss_cap_usd | Daily spending pace on PAPER |
| leverage.liq_buffer | Liquidation offset from zero equity (5 bps per docs) |
| leverage.noise_multiple | How much the liquidation distance must exceed 15-minute noise |
| paper.queue_pay_prob | Confidence the queue pays, per regime |
| thresholds.drain_drop_pct | Drain flag sensitivity |

---

## 15. Known contract addresses (HyperEVM, chain 999)

| Contract | Address |
|---|---|
| Exchange (proxy) | `0x6cd5661646289FB6E65EA5C032310fDEd797D0a2` |
| PaperTokenomics | `0x75174549dbBD44497D31a47E2E2D7406408d63D9` |
| PAPER token | `0xe40f17915DAa230324030003A197cdAEf2261C0e` |
| PaperStaking | `0xAAd6c7b0CC3014FC80FfEDAe0Ed7Ce5967b0F016` |
| PriceOracle | `0xa9B4c7b7Ca5835652d81E25739c870D49F39cC2e` |
| SessionKeyManager | `0x8326Eb8896e358717f10502Ec73CE8D28E37B108` |
| DepositProxyFactory | `0x1629b7D46bef6E13FF754174FC9F32dEa309b38F` |

Identified from on-chain activity (the docs list them as TBA). Function names were recovered from bytecode selectors; see `python run.py abi-recover` and `probe`.

---

## 16. Honest limits

- No strategy here has a proven directional edge: positive EV depends entirely on what PAPER turns out to be worth.
- Simulation numbers depend on assumptions (crowd behaviour, PAPER price). They test the logic; they don't forecast profits.
- New protocol, no known audit, unverified and upgradeable contracts: smart-contract risk is real. The owner can change parameters (curve, fees) and the implementation behind a timelock.
- **BBO manipulation** is the main protocol risk per the docs: there is no deviation check on price. The bot's spread and oracle-divergence checks exist for this.
- **Market retirement:** the pause guardian can retire a market; positions then close only at the BBO frozen at retirement. There is also a global pause on opens.
- At high leverage a single price glitch can wipe a position. Don't leave idle funds on the contract.
