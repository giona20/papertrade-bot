"""Papertrade bot — commands:

  python run.py run                 start the bot (config.yaml)
  python run.py sim --hours 72      fast offline simulation + report
  python run.py status              latest action card and open positions
  python run.py calib add --gross 4.0 --net 3.1 --move 0.004
                                    record a haircut observed on a real trade
  python run.py calib show          estimated haircut curve
  python run.py kill | unkill       stop / re-enable the bot (closes all positions)
  python run.py audit | scenarios | sweep | probe | abi-recover | call | check-onchain | delay | test-alert
                                    see README.md
"""
from __future__ import annotations

import argparse
import copy
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import yaml

from ptbot.engine import DryRunExecutor, Engine, LiveExecutor
from ptbot.haircut import GRID, HaircutModel
from ptbot.infra import Alerter, RiskManager, Store
from ptbot.market import HyperliquidMarket, SimMarket
from ptbot.models import rome
from ptbot.protocol import OnchainProtocol, SimProtocol
from ptbot.strategies import StrategyBook, load_events


class RealClock:
    def now(self): return time.time()
    def sleep(self, s): time.sleep(s)


class SimClock:
    def __init__(self, t): self.t = t
    def now(self): return self.t
    def sleep(self, s): self.t += s


def load_cfg(path="config.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text())


def haircut_model(cfg, store) -> HaircutModel:
    h = HaircutModel(cfg["haircut"]["base_rate"], cfg["haircut"]["k"], cfg["haircut"]["min_samples"])
    for m, v in store.load_haircut_obs():
        h.add(m, v)
    return h


def build(cfg, clock, events, quiet=False):
    store = Store(cfg["db_path"])
    alert = Alerter(cfg["alerts"], store)
    hm = haircut_model(cfg, store)
    look = cfg["strategies"]["early"]["breakout_lookback_min"]
    if cfg["market_source"] == "sim":
        market = SimMarket(cfg["assets"], clock, look, events, cfg["sim"]["seed"])
    else:
        market = HyperliquidMarket(cfg["assets"], look)
    if cfg["protocol_source"] == "sim":
        protocol = SimProtocol(clock, cfg["sim"], cfg["thresholds"])
    else:
        protocol = OnchainProtocol(cfg, cfg["thresholds"])
    if hasattr(protocol, "haircut_params"):
        try:
            prm = protocol.haircut_params()
            if prm:
                hm.set_exact(*prm)
        except Exception as e:
            print(f"Curve parameters not readable: {e}")
    Ex = LiveExecutor if cfg["mode"] == "live" else DryRunExecutor
    ex = Ex(protocol, hm, store, cfg)
    launch = datetime.fromisoformat(cfg["launch_time"]).timestamp()
    if cfg["protocol_source"] == "sim":            # in simulation, launch is "now"
        launch = clock.now()
        iso = lambda h: datetime.fromtimestamp(launch + h * 3600).astimezone().isoformat()
        cfg["phases"] = {"predeposit_time": iso(-48), "open_contract_time": iso(cfg["sim"]["open_contract_after_hours"]),
                         "transferable_time": iso(cfg["sim"]["paper_listing_after_hours"])}
    book = StrategyBook(cfg, hm, launch, events)
    return Engine(cfg, clock, market, protocol, ex, RiskManager(cfg, store), store, alert, book, quiet), store


def cmd_run(cfg):
    clock = RealClock()
    events = load_events(cfg["strategies"]["catalyst"]["events_file"])
    eng, _ = build(cfg, clock, events)
    print(f"Starting: mode={cfg['mode']} market={cfg['market_source']} protocol={cfg['protocol_source']}")
    eng.run()


def sim_engine(cfg, hours, quiet=True, db="data/sim.db"):
    """Runs a simulation and returns (engine, store)."""
    cfg = copy.deepcopy(cfg)
    cfg["market_source"] = cfg["protocol_source"] = "sim"
    cfg["mode"] = "dry_run"
    cfg["db_path"] = db
    if os.path.exists(db):
        os.remove(db)
    clock = SimClock(time.time())
    every = cfg["sim"]["event_every_hours"] * 3600
    events = [{"name": f"Sim event {i + 1}", "ts": clock.now() + every * (i + 1), "assets": ["BTC", "ETH"],
               "expected_move_bps": cfg["sim"]["event_move_bps"]} for i in range(int(hours * 3600 // every))]
    eng, store = build(cfg, clock, events, quiet=quiet)
    eng.run(duration_s=hours * 3600)
    eng.close_all(clock.now(), "end of simulation")
    return eng, store


def set_path(cfg, path, value):
    d = cfg
    keys = path.split(".")
    for k in keys[:-1]:
        d = d[k]
    old = d[keys[-1]]
    d[keys[-1]] = type(old)(value) if not isinstance(old, bool) else value in ("1", "true", "True", "si")


def cmd_sweep(cfg, param, values, seeds, hours):
    """Compares variants of a parameter across seeds: cost per PAPER, PnL, PAPER minted."""
    print(f"Sweep {param} over {seeds} seeds × {hours}h simulated\n")
    print(f"{'value':>12} | {'PnL USDC':>10} | {'PAPER':>9} | {'cost/PAPER':>11} | {'rush $/PAPER':>13} | worst PnL")
    for v in values.split(","):
        pnls, papers, rushc, worst = [], [], [], None
        for sd in range(seeds):
            c = copy.deepcopy(cfg)
            set_path(c, param, v)
            c["sim"]["seed"] = 100 + sd
            eng, store = sim_engine(c, hours, db=f"data/sweep_{sd}.db")
            pnl, paper = store.db.execute("SELECT COALESCE(SUM(pnl),0), COALESCE(SUM(paper_minted),0) FROM trades").fetchone()
            pnls.append(pnl); papers.append(paper)
            if eng.rush.minted:
                rushc.append(eng.rush.lost / eng.rush.minted)
            worst = pnl if worst is None else min(worst, pnl)
        mp, mpp = sum(pnls) / seeds, sum(papers) / seeds
        cost = (-mp / mpp) if mpp else float("nan")
        rc = sum(rushc) / len(rushc) if rushc else float("nan")
        print(f"{v:>12} | {mp:>+10.2f} | {mpp:>9,.0f} | {cost:>11.4f} | {rc:>13.4f} | {worst:+.2f}")
    print("\ncost/PAPER = average net loss / PAPER minted (lower is better; negative = profit).")


SCENARIOS = {
    "weak rush": {"sim.rush_loss_per_hour": 500000},
    "strong rush": {"sim.rush_loss_per_hour": 2000000},
    "huge rush": {"sim.rush_loss_per_hour": 6000000},
    "whales": {"sim.whale_prob_per_hour": 0.15, "sim.whale_win_usd": 1500000},
    "winning crowd": {"sim.crowd_net_to_lp_per_hour": -15000, "sim.rush_loss_per_hour": 300000},
}


def cmd_scenarios(cfg, param, values, seeds, hours):
    """Compares variants of a parameter across different crowd and market scenarios."""
    vals = values.split(",") if param else ["(base)"]
    print(f"Scenarios × {seeds} seeds × {hours}h" + (f" — comparing {param}" if param else "") + "\n")
    print(f"{'scenario':15} | {'value':>9} | {'net USDC':>10} | {'PAPER':>8} | {'$/PAPER':>8} | {'worst':>9}")
    for name, over in SCENARIOS.items():
        for v in vals:
            pnls, papers = [], []
            for sd in range(seeds):
                c = copy.deepcopy(cfg)
                for kk, vv in over.items():
                    set_path(c, kk, str(vv))
                if param:
                    set_path(c, param, v)
                c["sim"]["seed"] = 200 + sd
                eng, store = sim_engine(c, hours, db=f"data/scen_{os.getpid()}_{sd}.db")
                pnl, paper = store.db.execute("SELECT COALESCE(SUM(pnl),0), COALESCE(SUM(paper_minted),0) FROM trades").fetchone()
                pnls.append(pnl); papers.append(paper)
            mp, mpp = sum(pnls) / seeds, sum(papers) / seeds
            cost = -mp / mpp if mpp else float("nan")
            print(f"{name:15} | {v:>9} | {mp:>+10.2f} | {mpp:>8,.0f} | {cost:>8.4f} | {min(pnls):>+9.2f}")
    print("\n$/PAPER = average net cost per PAPER minted (lower is better). 'worst' = PnL of the worst seed.")


def cmd_check_onchain(cfg):
    """Contract read test: run it after filling in onchain in config.yaml."""
    try:
        p = OnchainProtocol(cfg, cfg["thresholds"])
    except Exception as e:
        print(f"Incomplete onchain configuration: {e}")
        return
    for key, name in cfg["onchain"]["functions"].items():
        if not name:
            print(f"  {key:24} — not mapped")
            continue
        try:
            with_me = key.startswith("my_")
            scale = p.pd if "paper" in key and "pending" not in key else p.ud
            if key.startswith("oi_"):
                vals = {asset: p.raw(name, iid) / p.ud for asset, iid in p.cfg_ids.items()}
                print(f"  {key:24} = {vals}")
                continue
            print(f"  {key:24} = {p._call(key, scale, with_me)}")
        except Exception as e:
            print(f"  {key:24} ERROR: {e}")
    st = p.state(time.time())
    print(f"\nEffective LP ${st.effective_lp:,.2f} | queue ${st.queue_usd:,.2f} | emission {st.emission_per_usd:.1f}/$")
    prm = p.haircut_params()
    print("Curve parameters: " + (str(prm) if prm else "not mapped → calibration needed"))


PROBE_UINT = ["treasury", "sideBucket", "queueTotal", "queueLength", "queueHead", "queueTail", "trackedLpUsd",
              "lpBalance", "stakerFeeAccumulator", "tailProgressUsd", "totalSupply", "totalStaked",
              "accRewardPerShare", "devFeeAccumulator", "nextPositionId", "positionCount", "lossFeeBps",
              "tradingPaused", "lpFlatThresholdUsd", "tailDecayScaleUsd", "stakerRewardCapUsd",
              "flatRate", "currentRate", "mintRate", "currentMintRate", "totalQueued", "queueTotalUsd",
              "totalDebt", "debtTotal", "queueSize", "queueEnd", "queueStart", "totalQueueDebt",
              "stakerRewardCap", "lpFlatThreshold", "tailDecayScale", "tailProgress", "trackedLp",
              "lossFeeRate", "stakerShareBps", "devShareBps", "builderShareBps", "totalDeposits",
              "totalBalances", "openInterest", "rewardRate", "totalRewards", "pendingStakerFees"]
PROBE_ADDR = ["paperToken", "paperStaking", "paperTokenomics", "priceOracle", "oracle", "batchExecutor",
              "sessionKeyManager", "depositProxyFactory", "owner", "pauseGuardian", "marketKeeper", "usdc",
              "tokenomics", "staking", "exchange", "paper", "token", "stakingContract", "batchExecutor",
              "executor", "relayer", "feeRecipient", "devFeeRecipient", "implementation"]
PROBE_USER = ["balances", "queuedBalance", "queuedBalances", "queueBalance", "queueBalances", "debtOf",
              "availableBalance", "pendingReward", "stakedBalance", "stakes", "balanceOf", "userQueued"]
IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"


def cmd_probe(cfg, address=None):
    """Reads an UNVERIFIED contract by trying the most likely public getter names (from the docs).
    Writes the functions found to a minimal ABI, so the bot can use them without the official ABI."""
    import json as _json
    from web3 import Web3
    oc = cfg["onchain"]
    w3 = Web3(Web3.HTTPProvider(oc["rpc_url"]))
    target = address or oc["contract_address"]
    target = (oc.get("contracts") or {}).get(target, target)          # also accepts "tokenomics", "oracle"...
    addr = Web3.to_checksum_address(target)
    me = Web3.to_checksum_address(oc["my_address"]) if oc.get("my_address") else None
    print(f"Contract {addr} - chain id {w3.eth.chain_id}, code {len(w3.eth.get_code(addr))} bytes")
    impl = w3.eth.get_storage_at(addr, IMPL_SLOT)[-20:]
    if int.from_bytes(impl, "big"):
        print(f"EIP-1967 proxy -> implementation {Web3.to_checksum_address(impl)}")
    found = []

    def call(name, args, out):
        abi = [{"type": "function", "name": name, "stateMutability": "view",
                "inputs": [{"name": "a", "type": t} for t, _ in args],
                "outputs": [{"name": "", "type": out}]}]
        try:
            v = getattr(w3.eth.contract(address=addr, abi=abi).functions, name)(*[x for _, x in args]).call()
            found.append(abi[0])
            return v
        except Exception:
            return None

    print("\nNumeric values (raw; the Exchange uses 18 decimals for USD, PAPER 18):")
    for n in PROBE_UINT:
        v = call(n, [], "uint256")
        if v is not None:
            print(f"  {n:24} = {v}")
    print("\nLinked addresses:")
    for n in PROBE_ADDR:
        v = call(n, [], "address")
        if v is not None:
            print(f"  {n:24} = {v}")
    mine = {"onchain": me} if me else {}
    for k, w in cfg.get("wallets", {}).items():
        if w.get("address"):
            mine[k] = Web3.to_checksum_address(w["address"])
    seen = set()
    for label, m in mine.items():
        if m in seen:
            continue
        seen.add(m)
        print(f"\nBalances for wallet {label} {m}:")
        for n in PROBE_USER:
            v = call(n, [("address", m)], "uint256")
            if v is not None:
                print(f"  {n:24} = {v}")
    Path("abi").mkdir(exist_ok=True)
    out = Path("abi") / f"probe_{addr[:10]}.json"
    out.write_text(_json.dumps(found, indent=1))
    print(f"\n{len(found)} functions found -> {out}. Map them in config.yaml (onchain.functions).")
    if not found:
        print("No getter recognised: you need the official ABI or `abi-recover`.")


def cmd_abi_recover(cfg, address=None):
    """Rebuilds the function list of an unverified contract: extracts the selectors from the
    implementation bytecode and resolves them to names via public databases (openchain, 4byte)."""
    import re as _re
    import requests as _rq
    from web3 import Web3
    oc = cfg["onchain"]
    target = address or oc["contract_address"]
    target = (oc.get("contracts") or {}).get(target, target)
    w3 = Web3(Web3.HTTPProvider(oc["rpc_url"]))
    addr = Web3.to_checksum_address(target)
    impl = w3.eth.get_storage_at(addr, IMPL_SLOT)[-20:]
    code_addr = Web3.to_checksum_address(impl) if int.from_bytes(impl, "big") else addr
    code = w3.eth.get_code(code_addr).hex()
    code = code[2:] if code.startswith("0x") else code
    sels = sorted(set(m.group(1) for m in _re.finditer(r"63([0-9a-f]{8})(?=14|11|8[0-9a-f])", code)))
    print(f"{addr} (code from {code_addr}): {len(sels)} candidate selectors")
    names = {}
    try:
        r = _rq.get("https://api.openchain.xyz/signature-database/v1/lookup",
                    params={"function": ",".join("0x" + x for x in sels), "filter": "true"}, timeout=20).json()
        for k, v in (r.get("result", {}).get("function") or {}).items():
            if v:
                names[k[2:]] = v[0]["name"]
    except Exception as e:
        print(f"openchain unreachable: {e}")
    for x in sels:
        if x in names:
            continue
        try:
            r = _rq.get("https://www.4byte.directory/api/v1/signatures/", params={"hex_signature": "0x" + x},
                        timeout=10).json()
            if r.get("results"):
                names[x] = r["results"][-1]["text_signature"]
        except Exception:
            pass
    for x in sels:
        print(f"  0x{x}  {names.get(x, '?')}")
    out = Path("abi") / f"selectors_{addr[:10]}.txt"
    Path("abi").mkdir(exist_ok=True)
    out.write_text("\n".join(f"0x{x} {names.get(x, '?')}" for x in sels))
    print(f"\nSaved to {out}. Names marked '?' are not in public databases.")


def cmd_call(cfg, signature, args):
    """Raw call to any function, even one returning a struct:
    python run.py call "exchange:instruments(uint32)" 0
    Prints each 32-byte word as an integer (and divided by 1e18)."""
    from eth_abi import encode
    from web3 import Web3
    oc = cfg["onchain"]
    cname, sig = signature.split(":", 1) if ":" in signature else ("exchange", signature)
    addr = Web3.to_checksum_address((oc.get("contracts") or {}).get(cname, cname))
    types = [t for t in sig[sig.index("(") + 1:-1].split(",") if t]
    vals = []
    for t, v in zip(types, args):
        vals.append(Web3.to_checksum_address(v) if t == "address" else (v.lower() in ("1", "true") if t == "bool" else int(v)))
    data = Web3.keccak(text=sig)[:4] + encode(types, vals)
    w3 = Web3(Web3.HTTPProvider(oc["rpc_url"]))
    out = w3.eth.call({"to": addr, "data": "0x" + data.hex().removeprefix("0x")})
    words = [out[i:i + 32] for i in range(0, len(out), 32)]
    print(f"{cname}.{sig} {args} -> {len(words)} values")
    for i, w in enumerate(words):
        n = int.from_bytes(w, "big")
        if n >= 2 ** 255:
            n -= 2 ** 256
        hint = f"  (/1e18 = {n / 1e18:,.6f})" if abs(n) >= 10 ** 12 else ""
        if 0 < n < 2 ** 160 and n > 2 ** 150:
            hint = f"  (address {Web3.to_checksum_address(w[-20:])})"
        print(f"  [{i}] {n}{hint}")


def cmd_test_alert(cfg):
    store = Store(cfg["db_path"])
    Alerter(cfg["alerts"], store).send(time.time(), "Papertrade bot test alert: if you read this on Telegram, it works.", "ALERT")
    if not cfg["alerts"].get("telegram_token"):
        print("Telegram not configured: fill in alerts.telegram_token and telegram_chat_id.")


def cmd_delay(cfg, args):
    """Records real relayer confirmation times to tune the congestion model."""
    store = Store(cfg["db_path"])
    store.db.execute("CREATE TABLE IF NOT EXISTS delays(ts REAL, seconds REAL, notional REAL, kind TEXT)")
    if args.action == "add":
        store.db.execute("INSERT INTO delays VALUES(?,?,?,?)", (time.time(), args.seconds, args.notional, args.kind))
        store.db.commit()
        print(f"Recorded: {args.kind} notional ${args.notional:,.0f} confirmed in {args.seconds:.0f}s")
    rows = store.db.execute("SELECT seconds, notional FROM delays").fetchall()
    if not rows:
        print("No delays recorded.")
        return
    thr = cfg["congestion"]["small_notional_usd"]
    big = sorted(s for s, n in rows if n >= thr)
    small = sorted(s for s, n in rows if n < thr)
    med = lambda x: x[len(x) // 2] if x else None
    fmt = lambda v: "n/a" if v is None else f"{v:.0f}s"
    print(f"Observations: {len(rows)} | median notional ≥ ${thr:,.0f}: {fmt(med(big))} | below: {fmt(med(small))}")
    if big:
        print(f"Suggested: congestion.base_delay_s = {med(big):.0f}")
    if big and small:
        print(f"Suggested: congestion.small_extra_delay_s = {max(med(small) - med(big), 0):.0f}")


def cmd_audit(cfg, hours):
    """Independent check of the maths on a simulation: minting, haircut, liquidations."""
    from ptbot.haircut import scale_for
    eng, store = sim_engine(cfg, hours, db="data/audit.db")
    db = store.db
    snaps = db.execute("SELECT ts, emission, queue FROM snapshots ORDER BY ts").fetchall()
    def at(ts):
        prev = snaps[0]
        for r in snaps:
            if r[0] > ts:
                break
            prev = r
        return prev
    lb = cfg["leverage"]["liq_buffer"]
    fee = cfg["thresholds"]["loss_fee_lp"]
    b, k = cfg["sim"]["true_base_rate"], cfg["sim"]["true_k"]
    ok = {"mint": [0, 0], "haircut": [0, 0], "liquidation": [0, 0]}
    worst = {}
    for (tid, side, margin, lev, entry, exit_, pnl, minted, h, why, cts) in db.execute(
            "SELECT id, side, margin, leverage, entry, exit, pnl, paper_minted, haircut, close_reason, closed_ts "
            "FROM trades WHERE status='closed'"):
        mv = (exit_ / entry - 1) * (1 if side == "long" else -1)
        _, em, q = at(cts)
        if pnl < 0:
            liq = why.startswith("liquidation")
            basis = margin if liq else (-pnl if q > 0 else -pnl * (1 - fee))
            exp = basis * em
            good = abs(minted - exp) <= max(1.0, 0.02 * exp)     # 2%: emission can change between two snapshots
            ok["mint"][0 if good else 1] += 1
            if not good:
                worst.setdefault("mint", []).append((tid, minted, round(exp, 1)))
            if liq:
                gap = -mv - (1 / lev - lb)
                goodl = gap >= -1e-9
                ok["liquidation"][0 if goodl else 1] += 1
        elif h is not None:
            exp_h = 1 - scale_for(mv, b, k)
            goodh = abs(h - exp_h) < 1e-6
            ok["haircut"][0 if goodh else 1] += 1
    print("\n=== AUDIT ===")
    for kname, (g, bad) in ok.items():
        print(f"{kname:13}: {g} correct, {bad} wrong")
    if worst:
        print("Error examples:", worst)
    print("Expected mint: liquidation = margin × emission; losing close = loss × emission "
          "(× 0.98 when the queue is empty).")


def cmd_sim(cfg, hours):
    cfg = copy.deepcopy(cfg)
    cfg["market_source"] = cfg["protocol_source"] = "sim"
    cfg["mode"] = "dry_run"
    cfg["db_path"] = "data/sim.db"
    if os.path.exists(cfg["db_path"]):
        os.remove(cfg["db_path"])
    clock = SimClock(time.time())
    every = cfg["sim"]["event_every_hours"] * 3600
    events = [{"name": f"Sim event {i + 1}", "ts": clock.now() + every * (i + 1), "assets": ["BTC", "ETH"],
               "expected_move_bps": cfg["sim"]["event_move_bps"]} for i in range(int(hours * 3600 // every))]
    eng, store = build(cfg, clock, events, quiet=True)
    eng.run(duration_s=hours * 3600)
    eng.close_all(clock.now(), "end of simulation")
    report(store, eng)


def report(store, eng):
    db = store.db
    print("\n=== SIMULATION REPORT ===")
    rows = db.execute("SELECT strategy, COUNT(*), SUM(pnl), SUM(paper_minted), SUM(queued_usd), "
                      "SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) FROM trades WHERE status='closed' "
                      "GROUP BY strategy").fetchall()
    for s, n, pnl, paper, q, wins in rows:
        print(f"{s}: {n} trades, won {wins}, PnL USDC ${pnl:+.2f}, PAPER minted {paper:,.0f}, "
              f"queued ${q:,.2f}")
    regs = db.execute("SELECT regime, COUNT(*) FROM snapshots GROUP BY regime").fetchall()
    tot = sum(n for _, n in regs) or 1
    print("Time in regimes: " + ", ".join(f"{r} {n / tot:.0%}" for r, n in regs))
    st = eng.protocol.state(eng.clock.now())
    val = (st.my_paper + st.my_staked) * (st.paper_price or 0)
    print(f"Your PAPER: {st.my_paper + st.my_staked:,.0f} (staked {st.my_staked:,.0f}), value "
          f"${val:,.2f} at price {st.paper_price}; unclaimed rewards ${st.my_pending_rewards:,.2f}")
    claims = db.execute("SELECT msg FROM events WHERE msg LIKE 'Claim%'").fetchall()
    tot_claim = sum(float(m[0].split('$')[1].replace(',', '')) for m in claims)
    usdc = db.execute("SELECT COALESCE(SUM(pnl),0) FROM trades").fetchone()[0]
    print(f"Rewards claimed ${tot_claim:,.2f} | Total (USDC + claims + PAPER value) ${usdc + tot_claim + val:+,.2f}")
    print("NB: PAPER value in simulation depends on assumptions (price, staker share): it is not a forecast.")
    r = eng.rush
    print(f"Rush ({r.mode}): {r.end_reason or 'not finished'} | net cost ${r.lost:,.2f} | PAPER minted {r.minted:,.0f}"
          + (f" | net cost ${r.lost / r.minted:.4f}/PAPER" if r.minted else ""))
    print(f"Haircut: {len(eng.book.h.obs)} observations, calibrated={eng.book.h.calibrated}")
    print("\nLatest action card:\n" + eng.last_card)


def cmd_status(cfg):
    db = sqlite3.connect(cfg["db_path"])
    r = db.execute("SELECT ts, card FROM snapshots ORDER BY ts DESC LIMIT 1").fetchone()
    print(r[1] if r else "No data: start `python run.py run` first.")
    for t in db.execute("SELECT id, strategy, asset, side, margin, leverage, entry, opened_ts FROM trades "
                        "WHERE status='open'").fetchall():
        print(f"Open #{t[0]} {t[1]} {t[2]} {t[3]} margin ${t[4]} leverage {t[5]:.0f}x entry {t[6]} since {rome(t[7])}")


def cmd_calib(cfg, args):
    store = Store(cfg["db_path"])
    if args.action == "add":
        h = 1 - args.net / args.gross
        store.haircut_obs(time.time(), args.move, h)
        print(f"Recorded: move {args.move:.3%} → haircut {h:.1%}")
    hm = haircut_model(cfg, store)
    print("Curve: " + hm.describe())
    for d in GRID:
        print(f"  move {d:.2%} → estimated haircut {hm.estimate(d):.0%}")
    print(f"Chosen TP/SL distance: {hm.choose_distance(cfg['haircut']['target_max']):.2%}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "sim", "status", "calib", "kill", "unkill", "sweep", "check-onchain",
                                    "test-alert", "delay", "audit", "scenarios", "probe", "abi-recover", "call"])
    ap.add_argument("action", nargs="?", default="show")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--hours", type=float, default=72)
    ap.add_argument("--gross", type=float)
    ap.add_argument("--net", type=float)
    ap.add_argument("--move", type=float)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--param")
    ap.add_argument("--address")
    ap.add_argument("--values")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--seconds", type=float)
    ap.add_argument("--notional", type=float)
    ap.add_argument("--kind", default="apertura")
    a = ap.parse_args()
    c = load_cfg(a.config)
    if a.cmd == "run":
        cmd_run(c)
    elif a.cmd == "sim":
        cmd_sim(c, a.hours)
    elif a.cmd == "status":
        cmd_status(c)
    elif a.cmd == "calib":
        cmd_calib(c, a)
    elif a.cmd == "sweep":
        cmd_sweep(c, a.param, a.values, a.seeds, a.hours)
    elif a.cmd == "call":
        cmd_call(c, a.action, a.args)
    elif a.cmd == "abi-recover":
        cmd_abi_recover(c, a.address)
    elif a.cmd == "probe":
        cmd_probe(c, a.address)
    elif a.cmd == "audit":
        cmd_audit(c, a.hours)
    elif a.cmd == "scenarios":
        cmd_scenarios(c, a.param, a.values, a.seeds, a.hours)
    elif a.cmd == "check-onchain":
        cmd_check_onchain(c)
    elif a.cmd == "test-alert":
        cmd_test_alert(c)
    elif a.cmd == "delay":
        cmd_delay(c, a)
    elif a.cmd == "kill":
        Path(c["kill_file"]).touch()
        print("Kill switch active: the bot closes everything and stops.")
    elif a.cmd == "unkill":
        Path(c["kill_file"]).unlink(missing_ok=True)
        print("Kill switch removed.")
