"""Market data.

HyperliquidMarket reads the BBO (best bid/ask) that Papertrade prices from (BBO mid), plus
mark/oracle for divergence checks and 1m candles for volatility and breakouts.
SimMarket generates fake prices for offline tests.
"""
from __future__ import annotations

import math
import random

import requests

from .models import MarketSnapshot, bar_stats

HL_URL = "https://api.hyperliquid.xyz/info"


class HyperliquidMarket:
    def __init__(self, assets: list[str], lookback_min: int = 30, timeout: float = 5.0):
        self.assets = assets
        self.lookback = lookback_min
        self.timeout = timeout
        self.s = requests.Session()
        self._bars: dict[str, list] = {}
        self._bars_ts = 0.0

    def _post(self, body: dict):
        r = self.s.post(HL_URL, json=body, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _contexts(self) -> dict:
        meta, ctxs = self._post({"type": "metaAndAssetCtxs"})
        return {u["name"]: c for u, c in zip(meta["universe"], ctxs)}

    def _refresh_bars(self, now: float) -> None:
        if now - self._bars_ts < 55:
            return
        end = int(now * 1000)
        start = end - (max(self.lookback, 60) + 5) * 60_000
        for a in self.assets:
            try:
                cs = self._post({"type": "candleSnapshot",
                                 "req": {"coin": a, "interval": "1m", "startTime": start, "endTime": end}})
                self._bars[a] = [(float(c["h"]), float(c["l"]), float(c["c"])) for c in cs]
            except Exception:
                pass
        self._bars_ts = now

    def snapshot(self, now: float) -> dict[str, MarketSnapshot]:
        ctx = self._contexts()
        self._refresh_bars(now)
        out = {}
        for a in self.assets:
            book = self._post({"type": "l2Book", "coin": a})
            bids, asks = book["levels"]
            if not bids or not asks:
                continue
            c = ctx.get(a, {})
            mark = float(c["markPx"]) if c.get("markPx") else None
            oracle = float(c["oraclePx"]) if c.get("oraclePx") else None
            vol, hi, lo = bar_stats(self._bars.get(a, []), self.lookback)
            out[a] = MarketSnapshot(a, float(bids[0]["px"]), float(asks[0]["px"]), mark, oracle, vol, hi, lo, now)
        return out


class SimMarket:
    START = {"BTC": 100_000, "ETH": 4_000, "HYPE": 40, "SOL": 200}
    SIGMA_BPS_MIN = {"BTC": 5, "ETH": 7, "HYPE": 12, "SOL": 9}

    def __init__(self, assets, clock, lookback_min=30, events=None, seed=7):
        self.assets = assets
        self.clock = clock
        self.lookback = lookback_min
        self.rng = random.Random(seed)
        self.px = {a: float(self.START.get(a, 100)) for a in assets}
        self.bars = {a: [] for a in assets}
        self.cur = {a: [self.px[a]] * 3 for a in assets}
        self.last = clock.now()
        self.minute = int(self.last // 60)
        self.events = sorted(events or [], key=lambda e: e["ts"])

    def _step(self) -> None:
        now = self.clock.now()
        dt = now - self.last
        if dt <= 0:
            return
        for a in self.assets:
            sig = self.SIGMA_BPS_MIN.get(a, 8) / 1e4
            self.px[a] *= math.exp(sig * math.sqrt(dt / 60) * self.rng.gauss(0, 1))
        for ev in self.events:
            if not ev.get("_done") and ev["ts"] <= now:
                ev["_done"] = True
                move = ev["expected_move_bps"] / 1e4 * self.rng.lognormvariate(0, 0.5)
                sign = self.rng.choice([-1, 1])
                for a in ev["assets"]:
                    if a in self.px:
                        self.px[a] *= 1 + sign * move
        for a in self.assets:
            h, l, _ = self.cur[a]
            self.cur[a] = [max(h, self.px[a]), min(l, self.px[a]), self.px[a]]
        m = int(now // 60)
        if m != self.minute:
            for a in self.assets:
                self.bars[a].append(tuple(self.cur[a]))
                self.bars[a] = self.bars[a][-200:]
                self.cur[a] = [self.px[a]] * 3
            self.minute = m
        self.last = now

    def snapshot(self, now: float) -> dict[str, MarketSnapshot]:
        self._step()
        out = {}
        for a in self.assets:
            p = self.px[a]
            half = p * 0.5e-4
            vol, hi, lo = bar_stats(self.bars[a], self.lookback)
            oracle = p * (1 + self.rng.gauss(0, 2e-4))
            out[a] = MarketSnapshot(a, p - half, p + half, p, oracle, vol, hi, lo, now)
        return out
