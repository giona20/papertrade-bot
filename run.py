"""Papertrade bot — comandi:

  python run.py run                 avvia il bot (config.yaml)
  python run.py sim --hours 72      simulazione offline veloce + report
  python run.py status              ultima scheda azioni e posizioni
  python run.py calib add --gross 4.0 --net 3.1 --move 0.004
                                    registra una trattenuta osservata su un trade reale
  python run.py calib show          curva della trattenuta stimata
  python run.py kill | unkill       ferma / riabilita il bot (chiude tutte le posizioni)
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
            print(f"Parametri curva non leggibili: {e}")
    Ex = LiveExecutor if cfg["mode"] == "live" else DryRunExecutor
    ex = Ex(protocol, hm, store, cfg)
    launch = datetime.fromisoformat(cfg["launch_time"]).timestamp()
    if cfg["protocol_source"] == "sim":            # in simulazione il lancio è "adesso"
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
    print(f"Avvio: modalità={cfg['mode']} mercato={cfg['market_source']} protocollo={cfg['protocol_source']}")
    eng.run()


def sim_engine(cfg, hours, quiet=True, db="data/sim.db"):
    """Esegue una simulazione e ritorna (engine, store)."""
    cfg = copy.deepcopy(cfg)
    cfg["market_source"] = cfg["protocol_source"] = "sim"
    cfg["mode"] = "dry_run"
    cfg["db_path"] = db
    if os.path.exists(db):
        os.remove(db)
    clock = SimClock(time.time())
    every = cfg["sim"]["event_every_hours"] * 3600
    events = [{"name": f"Evento sim {i + 1}", "ts": clock.now() + every * (i + 1), "assets": ["BTC", "ETH"],
               "expected_move_bps": cfg["sim"]["event_move_bps"]} for i in range(int(hours * 3600 // every))]
    eng, store = build(cfg, clock, events, quiet=quiet)
    eng.run(duration_s=hours * 3600)
    eng.close_all(clock.now(), "fine simulazione")
    return eng, store


def set_path(cfg, path, value):
    d = cfg
    keys = path.split(".")
    for k in keys[:-1]:
        d = d[k]
    old = d[keys[-1]]
    d[keys[-1]] = type(old)(value) if not isinstance(old, bool) else value in ("1", "true", "True", "si")


def cmd_sweep(cfg, param, values, seeds, hours):
    """Confronta varianti di un parametro su più seed: costo per PAPER, PnL, PAPER coniati."""
    print(f"Sweep {param} su {seeds} seed × {hours}h simulate\n")
    print(f"{'valore':>12} | {'PnL USDC':>10} | {'PAPER':>9} | {'costo/PAPER':>11} | {'corsa $/PAPER':>13} | peggior PnL")
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
    print("\ncosto/PAPER = perdita netta media / PAPER coniati (più basso è meglio; negativo = profitto).")


SCENARIOS = {
    "corsa debole": {"sim.rush_loss_per_hour": 500000},
    "corsa forte": {"sim.rush_loss_per_hour": 2000000},
    "corsa enorme": {"sim.rush_loss_per_hour": 6000000},
    "balene": {"sim.whale_prob_per_hour": 0.15, "sim.whale_win_usd": 1500000},
    "folla vincente": {"sim.crowd_net_to_lp_per_hour": -15000, "sim.rush_loss_per_hour": 300000},
}


def cmd_scenarios(cfg, param, values, seeds, hours):
    """Confronta varianti di un parametro su scenari diversi di folla e mercato."""
    vals = values.split(",") if param else ["(base)"]
    print(f"Scenari × {seeds} seed × {hours}h" + (f" — confronto {param}" if param else "") + "\n")
    print(f"{'scenario':15} | {'valore':>9} | {'USDC netto':>10} | {'PAPER':>8} | {'$/PAPER':>8} | {'peggiore':>9}")
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
    print("\n$/PAPER = costo netto medio per PAPER coniato (più basso è meglio). 'peggiore' = PnL del seed peggiore.")


def cmd_check_onchain(cfg):
    """Prova di lettura del contratto: da fare l'8/10 dopo aver compilato onchain in config.yaml."""
    try:
        p = OnchainProtocol(cfg, cfg["thresholds"])
    except Exception as e:
        print(f"Configurazione onchain incompleta: {e}")
        return
    for key, name in cfg["onchain"]["functions"].items():
        if not name:
            print(f"  {key:24} — non mappata")
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
            print(f"  {key:24} ERRORE: {e}")
    st = p.state(time.time())
    print(f"\nLP effettiva ${st.effective_lp:,.2f} | coda ${st.queue_usd:,.2f} | emissione {st.emission_per_usd:.1f}/$")
    prm = p.haircut_params()
    print("Parametri curva: " + (str(prm) if prm else "non mappati → servirà la calibrazione"))


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
    """Legge un contratto NON verificato provando i nomi dei getter pubblici più probabili (dai docs).
    Scrive le funzioni trovate in un ABI minimo, così il bot può usarle senza l'ABI ufficiale."""
    import json as _json
    from web3 import Web3
    oc = cfg["onchain"]
    w3 = Web3(Web3.HTTPProvider(oc["rpc_url"]))
    target = address or oc["contract_address"]
    target = (oc.get("contracts") or {}).get(target, target)          # accetta anche "tokenomics", "oracle"...
    addr = Web3.to_checksum_address(target)
    me = Web3.to_checksum_address(oc["my_address"]) if oc.get("my_address") else None
    print(f"Contratto {addr} - chain id {w3.eth.chain_id}, codice {len(w3.eth.get_code(addr))} byte")
    impl = w3.eth.get_storage_at(addr, IMPL_SLOT)[-20:]
    if int.from_bytes(impl, "big"):
        print(f"Proxy EIP-1967 -> implementazione {Web3.to_checksum_address(impl)}")
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

    print("\nValori numerici (grezzi; USDC di solito 6 decimali, PAPER 18):")
    for n in PROBE_UINT:
        v = call(n, [], "uint256")
        if v is not None:
            print(f"  {n:24} = {v}")
    print("\nIndirizzi collegati:")
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
        print(f"\nSaldi wallet {label} {m}:")
        for n in PROBE_USER:
            v = call(n, [("address", m)], "uint256")
            if v is not None:
                print(f"  {n:24} = {v}")
    Path("abi").mkdir(exist_ok=True)
    out = Path("abi") / f"probe_{addr[:10]}.json"
    out.write_text(_json.dumps(found, indent=1))
    print(f"\n{len(found)} funzioni trovate -> {out}. Incolla questo output in chat: lo mappo io in config.yaml.")
    if not found:
        print("Nessun getter riconosciuto: servira' l'ABI ufficiale o la decompilazione del bytecode.")


def cmd_abi_recover(cfg, address=None):
    """Ricostruisce l'elenco delle funzioni di un contratto non verificato: estrae i selettori dal
    bytecode dell'implementazione e li traduce in nomi con i database pubblici (openchain, 4byte)."""
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
    print(f"{addr} (codice da {code_addr}): {len(sels)} selettori candidati")
    names = {}
    try:
        r = _rq.get("https://api.openchain.xyz/signature-database/v1/lookup",
                    params={"function": ",".join("0x" + x for x in sels), "filter": "true"}, timeout=20).json()
        for k, v in (r.get("result", {}).get("function") or {}).items():
            if v:
                names[k[2:]] = v[0]["name"]
    except Exception as e:
        print(f"openchain non raggiungibile: {e}")
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
    print(f"\nSalvato in {out}. Incollami l'elenco: i nomi con '?' li ricostruisco dai docs.")


def cmd_call(cfg, signature, args):
    """Chiamata grezza a una funzione qualsiasi, anche se ritorna una struct:
    python run.py call "exchange:instruments(uint32)" 0
    Stampa ogni parola da 32 byte come intero (e diviso per 1e18)."""
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
    print(f"{cname}.{sig} {args} -> {len(words)} valori")
    for i, w in enumerate(words):
        n = int.from_bytes(w, "big")
        if n >= 2 ** 255:
            n -= 2 ** 256
        hint = f"  (/1e18 = {n / 1e18:,.6f})" if abs(n) >= 10 ** 12 else ""
        if 0 < n < 2 ** 160 and n > 2 ** 150:
            hint = f"  (indirizzo {Web3.to_checksum_address(w[-20:])})"
        print(f"  [{i}] {n}{hint}")


def cmd_test_alert(cfg):
    store = Store(cfg["db_path"])
    Alerter(cfg["alerts"], store).send(time.time(), "Test alert Papertrade bot: se lo leggi su Telegram funziona.", "ALERT")
    if not cfg["alerts"].get("telegram_token"):
        print("Telegram non configurato: compila alerts.telegram_token e telegram_chat_id.")


def cmd_delay(cfg, args):
    """Registra i tempi di conferma reali dei relayer per tarare il modello di congestione."""
    store = Store(cfg["db_path"])
    store.db.execute("CREATE TABLE IF NOT EXISTS delays(ts REAL, seconds REAL, notional REAL, kind TEXT)")
    if args.action == "add":
        store.db.execute("INSERT INTO delays VALUES(?,?,?,?)", (time.time(), args.seconds, args.notional, args.kind))
        store.db.commit()
        print(f"Registrato: {args.kind} nozionale ${args.notional:,.0f} confermato in {args.seconds:.0f}s")
    rows = store.db.execute("SELECT seconds, notional FROM delays").fetchall()
    if not rows:
        print("Nessun ritardo registrato.")
        return
    thr = cfg["congestion"]["small_notional_usd"]
    big = sorted(s for s, n in rows if n >= thr)
    small = sorted(s for s, n in rows if n < thr)
    med = lambda x: x[len(x) // 2] if x else None
    fmt = lambda v: "n/d" if v is None else f"{v:.0f}s"
    print(f"Osservazioni: {len(rows)} | mediana nozionale ≥ ${thr:,.0f}: {fmt(med(big))} | sotto: {fmt(med(small))}")
    if big:
        print(f"Suggerito: congestion.base_delay_s = {med(big):.0f}")
    if big and small:
        print(f"Suggerito: congestion.small_extra_delay_s = {max(med(small) - med(big), 0):.0f}")


def cmd_audit(cfg, hours):
    """Verifica indipendente dei calcoli su una simulazione: conio, trattenuta, liquidazioni."""
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
    ok = {"conio": [0, 0], "trattenuta": [0, 0], "liquidazione": [0, 0]}
    worst = {}
    for (tid, side, margin, lev, entry, exit_, pnl, minted, h, why, cts) in db.execute(
            "SELECT id, side, margin, leverage, entry, exit, pnl, paper_minted, haircut, close_reason, closed_ts "
            "FROM trades WHERE status='closed'"):
        mv = (exit_ / entry - 1) * (1 if side == "long" else -1)
        _, em, q = at(cts)
        if pnl < 0:
            liq = why.startswith("liquidazione")
            basis = margin if liq else (-pnl if q > 0 else -pnl * (1 - fee))
            exp = basis * em
            good = abs(minted - exp) <= max(1.0, 0.02 * exp)     # 2%: l'emissione può cambiare tra due snapshot
            ok["conio"][0 if good else 1] += 1
            if not good:
                worst.setdefault("conio", []).append((tid, minted, round(exp, 1)))
            if liq:
                gap = -mv - (1 / lev - lb)
                goodl = gap >= -1e-9
                ok["liquidazione"][0 if goodl else 1] += 1
        elif h is not None:
            exp_h = 1 - scale_for(mv, b, k)
            goodh = abs(h - exp_h) < 1e-6
            ok["trattenuta"][0 if goodh else 1] += 1
    print("\n=== AUDIT ===")
    for kname, (g, bad) in ok.items():
        print(f"{kname:13}: {g} corretti, {bad} errati")
    if worst:
        print("Esempi di errori:", worst)
    print("Conio atteso: liquidazione = margine × emissione; chiusura in perdita = perdita × emissione "
          "(× 0,98 se la coda è vuota).")


def cmd_sim(cfg, hours):
    cfg = copy.deepcopy(cfg)
    cfg["market_source"] = cfg["protocol_source"] = "sim"
    cfg["mode"] = "dry_run"
    cfg["db_path"] = "data/sim.db"
    if os.path.exists(cfg["db_path"]):
        os.remove(cfg["db_path"])
    clock = SimClock(time.time())
    every = cfg["sim"]["event_every_hours"] * 3600
    events = [{"name": f"Evento sim {i + 1}", "ts": clock.now() + every * (i + 1), "assets": ["BTC", "ETH"],
               "expected_move_bps": cfg["sim"]["event_move_bps"]} for i in range(int(hours * 3600 // every))]
    eng, store = build(cfg, clock, events, quiet=True)
    eng.run(duration_s=hours * 3600)
    eng.close_all(clock.now(), "fine simulazione")
    report(store, eng)


def report(store, eng):
    db = store.db
    print("\n=== REPORT SIMULAZIONE ===")
    rows = db.execute("SELECT strategy, COUNT(*), SUM(pnl), SUM(paper_minted), SUM(queued_usd), "
                      "SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) FROM trades WHERE status='closed' "
                      "GROUP BY strategy").fetchall()
    for s, n, pnl, paper, q, wins in rows:
        print(f"{s}: {n} trade, vinti {wins}, PnL USDC ${pnl:+.2f}, PAPER coniati {paper:,.0f}, "
              f"finiti in coda ${q:,.2f}")
    regs = db.execute("SELECT regime, COUNT(*) FROM snapshots GROUP BY regime").fetchall()
    tot = sum(n for _, n in regs) or 1
    print("Tempo nei regimi: " + ", ".join(f"{r} {n / tot:.0%}" for r, n in regs))
    st = eng.protocol.state(eng.clock.now())
    val = (st.my_paper + st.my_staked) * (st.paper_price or 0)
    print(f"PAPER tuoi: {st.my_paper + st.my_staked:,.0f} (in staking {st.my_staked:,.0f}), valore "
          f"${val:,.2f} a prezzo {st.paper_price}; ricompense non ritirate ${st.my_pending_rewards:,.2f}")
    claims = db.execute("SELECT msg FROM events WHERE msg LIKE 'Claim%'").fetchall()
    tot_claim = sum(float(m[0].split('$')[1].replace(',', '')) for m in claims)
    usdc = db.execute("SELECT COALESCE(SUM(pnl),0) FROM trades").fetchone()[0]
    print(f"Ricompense ritirate ${tot_claim:,.2f} | Totale (USDC + claim + valore PAPER) ${usdc + tot_claim + val:+,.2f}")
    print("NB: il valore dei PAPER in simulazione dipende da ipotesi (prezzo, quota staker): non è una previsione.")
    r = eng.rush
    print(f"Corsa ({r.mode}): {r.end_reason or 'non conclusa'} | costo netto ${r.lost:,.2f} | PAPER coniati {r.minted:,.0f}"
          + (f" | costo netto ${r.lost / r.minted:.4f}/PAPER" if r.minted else ""))
    print(f"Trattenuta: {len(eng.book.h.obs)} osservazioni, calibrata={eng.book.h.calibrated}")
    print("\nUltima scheda azioni:\n" + eng.last_card)


def cmd_status(cfg):
    db = sqlite3.connect(cfg["db_path"])
    r = db.execute("SELECT ts, card FROM snapshots ORDER BY ts DESC LIMIT 1").fetchone()
    print(r[1] if r else "Nessun dato: avvia prima `python run.py run`.")
    for t in db.execute("SELECT id, strategy, asset, side, margin, leverage, entry, opened_ts FROM trades "
                        "WHERE status='open'").fetchall():
        print(f"Aperta #{t[0]} {t[1]} {t[2]} {t[3]} margine ${t[4]} leva {t[5]:.0f}x entry {t[6]} dalle {rome(t[7])}")


def cmd_calib(cfg, args):
    store = Store(cfg["db_path"])
    if args.action == "add":
        h = 1 - args.net / args.gross
        store.haircut_obs(time.time(), args.move, h)
        print(f"Registrata: movimento {args.move:.3%} → trattenuta {h:.1%}")
    hm = haircut_model(cfg, store)
    print("Curva: " + hm.describe())
    for d in GRID:
        print(f"  movimento {d:.2%} → trattenuta stimata {hm.estimate(d):.0%}")
    print(f"Distanza TP/SL scelta: {hm.choose_distance(cfg['haircut']['target_max']):.2%}")


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
        print("Kill switch attivo: il bot chiude tutto e si ferma.")
    elif a.cmd == "unkill":
        Path(c["kill_file"]).unlink(missing_ok=True)
        print("Kill switch rimosso.")
