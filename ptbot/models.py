"""Bot data structures."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")


def rome(ts: float) -> str:
    return datetime.fromtimestamp(ts, ROME).strftime("%Y-%m-%d %H:%M:%S")


def rome_date(ts: float) -> str:
    return datetime.fromtimestamp(ts, ROME).strftime("%Y-%m-%d")


@dataclass
class MarketSnapshot:
    asset: str
    bid: float
    ask: float
    mark: Optional[float]
    oracle: Optional[float]
    vol_1m: float          # standard deviation of 1-minute returns (fraction)
    range_high: float      # high of the breakout window (current candle excluded)
    range_low: float
    ts: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> float:
        return (self.ask - self.bid) / self.mid * 1e4

    @property
    def oracle_div_bps(self) -> float:
        if not self.oracle:
            return 0.0
        return abs(self.mid / self.oracle - 1) * 1e4


@dataclass
class ProtocolState:
    ts: float
    lp_usd: float
    queue_usd: float
    queue_len: int
    paper_supply: float
    paper_staked: float
    emission_per_usd: float
    staker_rewards_24h_usd: float
    paper_price: Optional[float]
    my_paper: float = 0.0
    my_staked: float = 0.0
    my_pending_rewards: float = 0.0
    my_queued_usd: float = 0.0
    open_pnl_total: Optional[float] = None   # aggregate open PnL of all traders, if readable
    oi: Optional[dict] = None                # {asset: (oi_long_usd, oi_short_usd)}, if readable
    paper_minted_24h: float = 0.0            # PAPER minted in the last 24h (for dilution)

    @property
    def effective_lp(self) -> float:
        """LP net of queued debt: the number that decides the regime."""
        return self.lp_usd - self.queue_usd

    @property
    def staking_apr(self) -> Optional[float]:
        if not self.paper_price or self.paper_staked <= 0:
            return None
        return self.staker_rewards_24h_usd * 365 / (self.paper_staked * self.paper_price)


@dataclass
class Intent:
    strategy: str
    asset: str
    side: str              # long | short
    margin: float
    leverage: float
    tp_move: float         # favourable move for the take profit (fraction)
    sl_move: float         # adverse move for the stop (fraction, ≤ liquidation distance)
    max_hold_min: float
    reason: str
    group: str = ""
    wallet: str = ""


@dataclass
class Position:
    id: int
    strategy: str
    asset: str
    side: str
    margin: float
    leverage: float
    entry: float
    opened_ts: float
    tp_move: float
    sl_move: float
    liq_move: float
    max_hold_min: float
    group: str = ""
    reason: str = ""
    status: str = "open"
    exit: Optional[float] = None
    closed_ts: Optional[float] = None
    pnl: float = 0.0
    paper_minted: float = 0.0
    queued_usd: float = 0.0
    haircut: Optional[float] = None
    close_reason: str = ""
    pending_close_ts: Optional[float] = None   # close requested, waiting for the relayer
    pending_why: str = ""
    wallet: str = ""

    def move(self, price: float) -> float:
        sign = 1 if self.side == "long" else -1
        return (price / self.entry - 1) * sign


def bar_stats(bars: list, lookback: int) -> tuple[float, float, float]:
    """bars = [(high, low, close)]. Returns (vol_1m, high, low) of the previous window."""
    closes = [b[2] for b in bars[-61:]]
    if len(closes) >= 3:
        rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
        vol = statistics.pstdev(rets) if len(rets) >= 2 else 0.0
    else:
        vol = 0.0
    window = bars[-(lookback + 1):-1] if len(bars) > 1 else bars
    if not window:
        return vol, float("inf"), 0.0
    return vol, max(b[0] for b in window), min(b[1] for b in window)


def liq_distance(lev: float, buffer: float = 0.0005) -> float:
    """Docs: liquidation (hard bust) triggers ~5 bps before the zero-equity price."""
    return max(1 / lev - buffer, 1e-5)


def lev_for_distance(d: float, buffer: float, lo: float, hi: float) -> float:
    """Leverage that puts the liquidation exactly at distance d."""
    return min(max(1 / (d + buffer), lo), hi)
