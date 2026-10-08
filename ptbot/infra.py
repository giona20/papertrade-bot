"""Persistence (SQLite), alerts (console + Telegram) and risk management."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import requests

from .models import Intent, MarketSnapshot, Position, ProtocolState, rome, rome_date


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS snapshots(ts REAL, regime TEXT, lp REAL, queue REAL, queue_len INT,
            eff_lp REAL, emission REAL, paper_price REAL, apr REAL, my_paper REAL, my_staked REAL,
            my_rewards REAL, my_queued REAL, card TEXT);
        CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY, strategy TEXT, asset TEXT, side TEXT,
            margin REAL, leverage REAL, entry REAL, exit REAL, opened_ts REAL, closed_ts REAL, pnl REAL,
            paper_minted REAL, queued_usd REAL, haircut REAL, status TEXT, reason TEXT, close_reason TEXT,
            grp TEXT);
        CREATE TABLE IF NOT EXISTS events(ts REAL, level TEXT, msg TEXT);
        CREATE TABLE IF NOT EXISTS haircut_obs(ts REAL, move REAL, h REAL);
        """)

    def snapshot(self, st: ProtocolState, regime: str, card: str) -> None:
        self.db.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            st.ts, regime, st.lp_usd, st.queue_usd, st.queue_len, st.effective_lp, st.emission_per_usd,
            st.paper_price, st.staking_apr, st.my_paper, st.my_staked, st.my_pending_rewards,
            st.my_queued_usd, card))
        self.db.commit()

    def next_id(self) -> int:
        r = self.db.execute("SELECT COALESCE(MAX(id),0)+1 FROM trades").fetchone()
        return r[0]

    def save_trade(self, p: Position) -> None:
        self.db.execute("INSERT OR REPLACE INTO trades VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            p.id, p.strategy, p.asset, p.side, p.margin, p.leverage, p.entry, p.exit, p.opened_ts,
            p.closed_ts, p.pnl, p.paper_minted, p.queued_usd, p.haircut, p.status, p.reason,
            p.close_reason, p.group))
        self.db.commit()

    def event(self, ts: float, level: str, msg: str) -> None:
        self.db.execute("INSERT INTO events VALUES(?,?,?)", (ts, level, msg))
        self.db.commit()

    def haircut_obs(self, ts: float, move: float, h: float) -> None:
        self.db.execute("INSERT INTO haircut_obs VALUES(?,?,?)", (ts, move, h))
        self.db.commit()

    def load_haircut_obs(self) -> list:
        return self.db.execute("SELECT move, h FROM haircut_obs").fetchall()

    def daily_pnl(self, ts: float) -> float:
        day = rome_date(ts)
        rows = self.db.execute("SELECT closed_ts, pnl FROM trades WHERE status='closed' AND strategy!='RUSH'").fetchall()
        return sum(p for t, p in rows if t and rome_date(t) == day)


class Alerter:
    def __init__(self, cfg: dict, store: Store):
        self.token = cfg.get("telegram_token")
        self.chat = cfg.get("telegram_chat_id")
        self.store = store

    def send(self, ts: float, msg: str, level: str = "INFO", quiet: bool = False) -> None:
        self.store.event(ts, level, msg)
        if not quiet:
            print(f"[{rome(ts)}] {level:5} {msg}")
        if self.token and self.chat and level in ("WARN", "ALERT", "TRADE", "REGIME"):
            try:
                requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                              json={"chat_id": self.chat, "text": f"[{level}] {msg}"}, timeout=5)
            except Exception:
                pass


class RiskManager:
    def __init__(self, cfg: dict, store: Store):
        self.cfg = cfg
        self.store = store
        self.halted_reason = ""

    def market_problems(self, s: MarketSnapshot, now: float) -> list[str]:
        r = self.cfg["risk"]
        out = []
        if s.spread_bps > r["max_spread_bps"]:
            out.append(f"spread {s.spread_bps:.1f}bps")
        if s.oracle_div_bps > r["max_oracle_div_bps"]:
            out.append(f"oracle divergence {s.oracle_div_bps:.1f}bps")
        if now - s.ts > r["stale_seconds"]:
            out.append("stale data")
        return out

    def kill_requested(self) -> bool:
        return Path(self.cfg["kill_file"]).exists()

    def wallet_for(self, it: Intent) -> str:
        if it.wallet:
            return it.wallet
        m = self.cfg.get("strategy_wallets", {})
        return m.get(it.strategy) or m.get(it.side) or ""

    def approve(self, it: Intent, open_pos: list, snap: MarketSnapshot, now: float) -> tuple[bool, str]:
        c = self.cfg["capital"]
        if self.kill_requested():
            return False, "kill switch active"
        it.wallet = self.wallet_for(it)
        for p in open_pos:                             # same account: no long and short on the same asset
            if getattr(p, "wallet", "") == it.wallet and p.asset == it.asset and p.side != it.side:
                return False, f"wallet {it.wallet} already has {p.side} on {p.asset}"
        if it.strategy == "RUSH":                      # the rush has its own budget, decided upfront
            probs = self.market_problems(snap, now)
            return (False, "market not suitable: " + ", ".join(probs)) if probs else (True, "ok")
        if self.store.daily_pnl(now) <= -c["daily_loss_cap_usd"]:
            return False, "daily loss cap reached"
        if len([p for p in open_pos if p.strategy != "RUSH"]) >= c["max_open_positions"]:
            return False, "too many open positions"
        if it.margin > c["max_margin_per_trade_usd"]:
            return False, "margin above the limit"
        committed = sum(p.margin for p in open_pos if p.strategy != "RUSH")
        if committed + it.margin > c["trading_budget_usd"]:
            return False, "trading budget exhausted"
        probs = self.market_problems(snap, now)
        if probs:
            return False, "market not suitable: " + ", ".join(probs)
        return True, "ok"
