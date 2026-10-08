"""Live, public read of the protocol, without the bot database.

Used by the Streamlit dashboard on Streamlit Cloud (or without a local bot): reads the Papertrade
contracts on HyperEVM and Hyperliquid prices. No personal data: a wallet can be typed into the
sidebar to see its balances.
"""
from __future__ import annotations

import time

import requests

from .protocol import OnchainProtocol, emission_rate
from .strategies import BOOTSTRAP, DECAY, INSOLVENT, SWEEP, current_phase

HL_INFO = "https://api.hyperliquid.xyz/info"
PHASE_NAME = {-1: "Pre-launch", 0: "Phase 0 · predeposit", 1: "Phase 1 · frontend only",
              2: "Phase 2 · open contract", 4: "Phase 4 · PAPER transferable"}


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def hl_mids(assets) -> dict:
    try:
        mids = requests.post(HL_INFO, json={"type": "allMids"}, timeout=8).json()
        return {a: float(mids[a]) for a in assets if a in mids}
    except Exception:
        return {}


def regime_of(eff_lp: float, queue: float, th: dict) -> str:
    if queue > 0 or eff_lp <= 0:
        return INSOLVENT
    if eff_lp < th["emission_decay_usd"]:
        return BOOTSTRAP
    if eff_lp < th["sweep_usd"]:
        return DECAY
    return SWEEP


def live_state(cfg: dict, wallet: str | None = None, protocol: OnchainProtocol | None = None) -> dict:
    """Snapshot of the protocol state. `protocol` is only for tests (fake reader)."""
    from datetime import datetime
    p = protocol or OnchainProtocol(cfg, cfg["thresholds"])
    ud, pd_ = p.ud, p.pd
    th = cfg["thresholds"]
    r = lambda spec, *a: _safe(lambda: p.raw(spec, *a))

    tracked = (r("tokenomics:trackedLpUsd") or 0) / ud
    tail = (r("tokenomics:tailProgressUsd") or 0) / ud
    queue = (r("exchange:totalQueued") or 0) / ud
    side = (r("exchange:sideBucket") or 0) / ud
    eff = tracked + side - queue
    supply = (r("paper:totalSupply") or 0) / pd_
    staked = (r("paper:balanceOf@staking") or 0) / pd_
    now = time.time()
    launch = datetime.fromisoformat(cfg["launch_time"]).timestamp()

    ids = cfg.get("instrument_ids", {})
    mids = hl_mids(list(ids))
    markets = []
    for asset, iid in ids.items():
        words = [r("exchange:marketOi(uint32)#%d" % i, iid) for i in range(5)]
        conf = [r("exchange:instruments(uint32)#%d" % i, iid) for i in range(8)]
        px = mids.get(asset)
        oi_l = (words[0] or 0) / 1e18
        oi_s = (words[1] or 0) / 1e18
        cap_l = (words[3] or 0) / 1e18
        cap_s = (words[4] or 0) / 1e18
        markets.append({
            "asset": asset, "price": px,
            "OI long": oi_l, "OI short": oi_s,
            "OI long $": oi_l * px if px else None, "OI short $": oi_s * px if px else None,
            "long cap $": cap_l * px if px else None, "short cap $": cap_s * px if px else None,
            "max leverage": conf[3],
            "liq. buffer bps": (conf[5] or 0) / 1e18 * 1e4 if conf[5] is not None else None,
        })

    out = {
        "ts": now,
        "phase": PHASE_NAME.get(current_phase(cfg, now, launch), "?"),
        "regime": regime_of(eff, queue, th),
        "tracked_lp": tracked, "tail": tail, "queue": queue, "side": side, "eff_lp": eff,
        "emission": emission_rate(tracked, tail, th),
        "treasury": (r("exchange:treasury") or 0) / ud,
        "deposits": (r("exchange:totalBalances") or 0) / ud,
        "locked_margin": (r("exchange:totalLockedMargin") or 0) / ud,
        "positions_opened": r("exchange:nextPositionId") or 0,
        "trading_paused": bool(r("exchange:tradingPaused") or 0),
        "staker_fees": (r("exchange:stakerFeeAccumulator") or 0) / ud,
        "paper_supply": supply, "paper_staked": staked,
        "markets": markets,
        "wallet": None,
    }
    if wallet:
        w = p.Web3.to_checksum_address(wallet)
        out["wallet"] = {
            "address": w,
            "balance": (r("exchange:balances", w) or 0) / ud,
            "queued": (r("exchange:userQueuedTotal", w) or 0) / ud,
            "PAPER": (r("paper:balanceOf", w) or 0) / pd_,
            "rewards": _safe(lambda: p.raw("staking:pendingReward", w) / ud),
        }
    return out
