"""Protocol regime + strategies + action card.

Key formula (explained in GUIDE.md): for a trade with TP and SL at the same distance d,
EV per $1 of margin ≈ 0.5 × (E × P_eff − 1 + q × G × (1 − h(d)))
  E = PAPER minted per $1 lost, P_eff = discounted PAPER price, q = probability the queue pays,
  G = gross profit per $ of margin at the TP, h(d) = haircut on the profit at that distance.
"""
from __future__ import annotations

import math
from collections import deque
from datetime import datetime
from pathlib import Path

import yaml

from .haircut import HaircutModel
from .models import ROME, Intent, MarketSnapshot, Position, ProtocolState, lev_for_distance, liq_distance, rome

INSOLVENT, BOOTSTRAP, DECAY, SWEEP = "INSOLVENT", "BOOTSTRAP", "DECAY", "SWEEP"

PHASE_TEXT = {
    -1: "PRE-LAUNCH: set up the wallets and test the bot. Predeposit opens on 08/10.",
    0: "PHASE 0 – PREDEPOSIT: deposit and create the account NOW. Trading paused. Later, deposits get low priority.",
    1: "PHASE 1 – FRONTEND ONLY: orders only via papertrade.xyz relayers. The bot gives MANUAL signals. "
       "Small notional = slow confirmations; pending orders can be cancelled. PAPER not transferable.",
    2: "PHASE 2 – OPEN CONTRACT: automatic execution possible. Watch out for MEV and competing bots.",
    4: "PHASE 4 – PAPER TRANSFERABLE: buying/selling possible, consider the secondary market.",
}


def _ts(v: str) -> float | None:
    return datetime.fromisoformat(v).timestamp() if v else None


def current_phase(cfg: dict, now: float, launch_ts: float) -> int:
    ph = cfg.get("phases", {})
    pre = _ts(ph.get("predeposit_time", ""))
    if now < launch_ts:
        return 0 if pre and now >= pre else -1
    t4, t2 = _ts(ph.get("transferable_time", "")), _ts(ph.get("open_contract_time", ""))
    if t4 and now >= t4:
        return 4
    if t2 and now >= t2:
        return 2
    return 1


def paper_value(cfg: dict, st: ProtocolState, phase: int) -> tuple[float | None, str]:
    """Market price if PAPER is transferable, otherwise an implied value from staking cash flows."""
    pc = cfg["paper"]
    if phase >= 4 and st.paper_price:
        return st.paper_price * (1 - pc["liquidity_discount"]), "market"
    if st.paper_staked > 0 and st.staker_rewards_24h_usd > 0:
        # early on supply is tiny and grows fast: dividing by today's supply inflates the value.
        # Use the supply expected in N days at the last 24h mint rate.
        future = st.paper_staked + st.paper_minted_24h * pc.get("dilution_horizon_days", 90)
        per_token = st.staker_rewards_24h_usd * 365 / future
        return per_token / pc["implied_required_yield"] * (1 - pc["nontransferable_discount"]), "implied"
    return None, "n/d"


def contrarian_side(cfg: dict, st: ProtocolState, asset: str):
    """Side opposite the crowd when OI is skewed. If the crowd wins, the LP goes under and the queue
    grows: the other side loses while emission is at its max and wins when the LP is full."""
    c = cfg.get("crowd", {})
    if not c.get("enabled") or not st.oi or asset not in st.oi:
        return None
    lo, sh = st.oi[asset]
    if lo >= c["skew_ratio"] * max(sh, 1.0):
        return "short"
    if sh >= c["skew_ratio"] * max(lo, 1.0):
        return "long"
    return None


def fee_per_margin(cfg: dict, lev: float, gross_win: float) -> tuple[float, float]:
    """(fixed cost per $ of margin, share taken from wins)."""
    f = cfg.get("fees", {})
    r, base = f.get("frontend_rate", 0.0), f.get("frontend_base", "margin")
    if base == "margin":
        return r, 0.0
    if base == "notional":
        return r * lev, 0.0
    return 0.0, r


class RegimeDetector:
    def __init__(self, th: dict, risk: dict):
        self.th = th
        self.risk = risk
        self.peak = 0.0
        self.qhist: deque = deque()
        self.regime = None
        self.drain = False
        self.queue_alert = False
        self.queue_growing = False

    def update(self, st: ProtocolState) -> str:
        eff = st.effective_lp
        self.peak = max(self.peak, eff)
        if st.queue_usd > 0 or eff <= 0:
            r = INSOLVENT
        elif eff < self.th["emission_decay_usd"]:
            r = BOOTSTRAP
        elif eff < self.th["sweep_usd"]:
            r = DECAY
        else:
            r = SWEEP
        self.drain = self.peak >= self.th["emission_decay_usd"] and eff < self.peak * (1 - self.th["drain_drop_pct"])
        self.qhist.append((st.ts, st.queue_usd))
        while self.qhist and self.qhist[0][0] < st.ts - 3600:
            self.qhist.popleft()
        q0 = self.qhist[0][1]
        self.queue_alert = q0 > 1000 and st.queue_usd > q0 * (1 + self.risk["max_queue_growth_1h"])
        self.queue_growing = st.queue_usd > 0 and st.queue_usd > q0 * 1.05
        self.regime = r
        return r


def load_events(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text()) or {}
    out = []
    for e in data.get("events") or []:
        dt = datetime.strptime(e["time"], "%Y-%m-%d %H:%M").replace(tzinfo=ROME)
        out.append({"name": e["name"], "ts": dt.timestamp(), "assets": e.get("assets", ["BTC"]),
                    "expected_move_bps": float(e.get("expected_move_bps", 50))})
    return out


class StrategyBook:
    def __init__(self, cfg: dict, haircut: HaircutModel, launch_ts: float, events: list[dict]):
        self.cfg = cfg
        self.h = haircut
        self.launch_ts = launch_ts
        self.events = events
        self.opened_events: set = set()

    # ---------- shared sizing ----------
    def sizing(self, vol_1m: float) -> tuple[float, float, float]:
        lv = self.cfg["leverage"]
        d = self.h.choose_distance(self.cfg["haircut"]["target_max"])
        noise = lv["noise_multiple"] * vol_1m * math.sqrt(15)
        d = max(d, noise)
        lev = lev_for_distance(d, lv["liq_buffer"], lv["min"], lv["max"])
        liq = liq_distance(lev, lv["liq_buffer"])
        return d, lev, liq

    def farming_ev(self, st: ProtocolState, regime: str, d: float, lev: float, phase: int,
                   with_paper: bool = True) -> float | None:
        """EV per $1 of margin of a trade with TP = d and SL = liquidation (≈ 50/50)."""
        p_eff, _ = paper_value(self.cfg, st, phase)
        if with_paper and p_eff is None:
            return None
        q = self.cfg["paper"]["queue_pay_prob"].get(regime, 1.0)
        gross = lev * d                                  # gross profit per $ if the TP is hit
        fixed, on_win = fee_per_margin(self.cfg, lev, gross)
        win = q * gross * (1 - self.h.estimate(d)) * (1 - on_win)
        paper = st.emission_per_usd * (p_eff or 0.0) if with_paper else 0.0
        # fee is paid on close: always on the winning branch, on the losing one (liquidation) only if configured
        loss_fee = fixed if self.cfg.get("fees", {}).get("charged_on_liquidation", False) else 0.0
        return 0.5 * (paper - 1 - loss_fee + win - fixed)

    def cost_per_paper(self, st: ProtocolState, regime: str, d: float, lev: float) -> float | None:
        """Expected cost per PAPER of a TP = SL trade (or a pair): (1 − q·G·(1−h)) / E."""
        if st.emission_per_usd <= 0:
            return None
        q = self.cfg["paper"]["queue_pay_prob"].get(regime, 1.0)
        g = lev * d
        return max(1 - q * g * (1 - self.h.estimate(d)), 0.0) / st.emission_per_usd

    @staticmethod
    def _open_by(open_pos, strategy, asset=None):
        return [p for p in open_pos if p.strategy == strategy and (asset is None or p.asset == asset)]

    # ---------- order generation ----------
    def intents(self, now: float, st: ProtocolState, regime: str, drain: bool,
                snaps: dict[str, MarketSnapshot], open_pos: list[Position], phase: int = 1) -> list[Intent]:
        out: list[Intent] = []
        s = self.cfg["strategies"]
        if phase < 1:
            return out                                   # pre-launch / predeposit: no trading

        # S1 — launch window: small positions, TP=SL in the low-haircut zone
        e = s["early"]
        in_window = 0 <= now - self.launch_ts <= e["days_from_launch"] * 86400
        drain_off = drain and self.cfg["thresholds"].get("drain_mode", "defensive") == "defensive"
        if e["enabled"] and in_window and regime in (INSOLVENT, BOOTSTRAP) and not drain_off:
            for a, sn in snaps.items():
                if self._open_by(open_pos, "S1", a):
                    continue
                side = "long" if sn.mid > sn.range_high else "short" if sn.mid < sn.range_low else None
                cs = contrarian_side(self.cfg, st, a)
                if cs and side:
                    side = cs                     # with a skewed crowd, positioning decides the side
                if not side:
                    continue
                d, lev, liq = self.sizing(sn.vol_1m)
                out.append(Intent("S1", a, side, e["margin_usd"], lev, d, liq, e["max_hold_minutes"],
                                  f"breakout {e['breakout_lookback_min']}m, d={d:.2%}, h≈{self.h.estimate(d):.0%}"))

        # S2 — events: straddle (long + short). The losing leg mints PAPER, the winner runs
        c = s["catalyst"]
        if c["enabled"]:
            for ev in self.events:
                start = ev["ts"] - c["open_minutes_before"] * 60
                if not (start <= now < ev["ts"]):
                    continue
                m = ev["expected_move_bps"] / 1e4
                for a in ev["assets"]:
                    sn = snaps.get(a)
                    grp = f"{ev['name']}:{a}"
                    if not sn or grp in self.opened_events:
                        continue
                    lv = self.cfg["leverage"]
                    liq = max(c["liq_fraction_of_move"] * m, lv["noise_multiple"] * sn.vol_1m * math.sqrt(15))
                    lev = lev_for_distance(liq, lv["liq_buffer"], lv["min"], lv["max"])
                    liq = liq_distance(lev, lv["liq_buffer"])
                    hold = c["open_minutes_before"] + c["close_minutes_after"]
                    for side in ("long", "short"):
                        out.append(Intent("S2", a, side, c["margin_usd"], lev, c["tp_fraction_of_move"] * m, liq,
                                          hold, f"{ev['name']} expected {ev['expected_move_bps']:.0f}bps",
                                          group=grp))

        # S3 — continuous farming: by maximum price per PAPER or by EV
        f = s["farming"]
        drain_block = drain and self.cfg["thresholds"].get("drain_mode", "defensive") == "defensive"
        if f["enabled"] and not drain_block:
            pv, src = paper_value(self.cfg, st, phase)
            for a, sn in snaps.items():
                if self._open_by(open_pos, "S3", a):
                    continue
                d, lev, liq = self.sizing(sn.vol_1m)
                if f.get("mode", "ev") == "price":
                    cost = self.cost_per_paper(st, regime, d, lev)
                    if cost is None or cost > f["max_cost_per_paper"]:
                        continue
                    why = f"estimated cost ${cost:.4f}/PAPER ≤ ${f['max_cost_per_paper']}"
                else:
                    ev = self.farming_ev(st, regime, d, lev, phase)
                    if pv is None or ev is None or ev < f["min_ev_per_margin"]:
                        continue
                    why = f"EV {ev:+.2f}/$ (PAPER {src} {pv:.5f})"
                if f.get("use_pairs", False):
                    grp = f"S3pair:{a}:{int(now)}"
                    for side in ("long", "short"):
                        out.append(Intent("S3", a, side, f["margin_usd"], lev, d, liq, f["max_hold_minutes"],
                                          f"pair ±{d:.2%}, {why}", group=grp,
                                          wallet=self.cfg["strategy_wallets"].get(side, "")))
                else:
                    side = contrarian_side(self.cfg, st, a) or ("long" if sn.mid >= (sn.range_high + sn.range_low) / 2 else "short")
                    out.append(Intent("S3", a, side, f["margin_usd"], lev, d, liq, f["max_hold_minutes"], why))
        prio = {"S2": 0, "S3": 1, "S1": 2}
        return sorted(out, key=lambda i: prio[i.strategy])

    # ---------- action card ----------
    def action_card(self, now: float, st: ProtocolState, regime: str, drain: bool, queue_alert: bool,
                    snaps: dict[str, MarketSnapshot], phase: int = 1, spent=(0.0, 0.0)) -> tuple[str, dict]:
        pc = self.cfg["paper"]
        vol = sum(s.vol_1m for s in snaps.values()) / max(len(snaps), 1)
        d, lev, liq = self.sizing(vol)
        ev = self.farming_ev(st, regime, d, lev, phase)
        ev0 = self.farming_ev(st, regime, d, lev, phase, with_paper=False)
        pv, src = paper_value(self.cfg, st, phase)
        apr = st.staking_apr if phase >= 4 else None
        acts = {"stake": False, "claim": False}
        L = [f"REGIME: {regime}{' + DRAIN' if drain else ''}  |  Rome time {rome(now)}",
             PHASE_TEXT.get(phase, PHASE_TEXT[1]),
             f"LP ${st.lp_usd:,.0f}  queue ${st.queue_usd:,.0f} ({st.queue_len})  effective LP ${st.effective_lp:,.0f}",
             f"Emission {st.emission_per_usd:.1f} PAPER/$  |  PAPER value ({src}, discounted) "
             f"{'n/a' if pv is None else f'${pv:.6f}'}  |  staking APR "
             f"{'n/a' if apr is None else f'{apr:.0%}'}",
             f"PAPER cost: symmetric trade ≈ ${-ev0 / (0.5 * st.emission_per_usd):.4f}/PAPER "
             f"(EV {ev0:+.3f}/$ without PAPER) | deliberate liquidation ≈ ${1 / st.emission_per_usd:.4f}/PAPER",
             f"Haircut: {self.h.describe()} → TP/SL distance {d:.2%}, leverage {lev:.0f}x, h≈{self.h.estimate(d):.0%}"]
        in_window = 0 <= now - self.launch_ts <= self.cfg["strategies"]["early"]["days_from_launch"] * 86400
        drain_off = drain and self.cfg["thresholds"].get("drain_mode", "defensive") == "defensive"
        st_cfg = self.cfg["strategies"]
        s1 = st_cfg["early"]["enabled"] and in_window and regime in (INSOLVENT, BOOTSTRAP) and not drain_off
        L.append(f"S1 launch window: {'ACTIVE' if s1 else 'off'}")
        nxt = [e for e in self.events if e["ts"] > now]
        if not st_cfg["catalyst"]["enabled"]:
            L.append("S2 events: off")
        else:
            L.append("S2 events: " + (f"next {nxt[0]['name']} at {rome(nxt[0]['ts'])}" if nxt else "none scheduled"))
        f = self.cfg["strategies"]["farming"]
        if f.get("mode", "ev") == "price":
            c3 = self.cost_per_paper(st, regime, d, lev)
            on = c3 is not None and c3 <= f["max_cost_per_paper"] and not drain_off
            L.append(f"S3 farming (max price ${f['max_cost_per_paper']}): estimated cost "
                     f"{'n/a' if c3 is None else f'${c3:.4f}'}/PAPER → " +
                     ("off (drain)" if drain_off else "ACTIVE" if on else "off (too expensive)"))
        else:
            L.append("S3 farming: " + ("needs a PAPER value (price or staking rewards)" if ev is None else
                                       f"EV {ev:+.2f}/$ → " + ("off (drain)" if drain_off else
                                       "ACTIVE" if ev >= f['min_ev_per_margin'] else "off (EV below threshold)")))
        # PAPER
        if phase < 4:
            paper = ("NOT TRANSFERABLE: no buying/selling possible. Stake all minted PAPER, "
                     "its only value today is the USDC rewards.")
            acts["stake"] = phase >= 1
        elif drain:
            paper = "REDUCE: LP falling hard, emissions rising again. Don't buy, trim free PAPER."
        elif not st.paper_price:
            paper = "Not listed: accumulate only from losses and stake what you receive."
            acts["stake"] = True
        elif regime in (INSOLVENT, BOOTSTRAP):
            paper = "DON'T BUY: max emissions (100/$) → supply growing. Stake the PAPER you receive."
            acts["stake"] = True
        elif regime == DECAY:
            ok = apr is not None and apr >= pc["target_apr"]
            paper = ("ACCUMULATE + STAKE: APR above threshold" if ok else
                     f"WAIT: APR {'n/a' if apr is None else f'{apr:.0%}'} < {pc['target_apr']:.0%}") + "; stake the PAPER you hold."
            acts["stake"] = True
        else:
            paper = "STAKE EVERYTHING: sweep active, every $ of LP gain above $5M goes to stakers."
            acts["stake"] = True
        L.append("PAPER: " + paper)
        lost, minted = spent        # lost = NET loss of all closed trades (losses − profits)
        if minted > 0:
            L.append(f"Net PAPER cost: ${lost / minted:.4f}/PAPER (trading result ${-lost:+,.2f}, {minted:,.0f} PAPER minted)"
                     if lost > 0 else f"Zero-cost PAPER: trades up ${-lost:,.2f} with {minted:,.0f} PAPER minted")
        if phase >= 4 and st.paper_price:
            if apr is not None and apr < pc["sell_apr"]:
                L.append(f"SELL: APR {apr:.0%} < {pc['sell_apr']:.0%} → the price discounts more than the real cash flows. "
                         "Sell half your PAPER (unstake if needed).")
            elif apr is not None and apr < pc["prepare_unstake_apr"] and pc["unstake_wait_hours"] > 0:
                L.append(f"PREPARE: APR {apr:.0%} near the sell threshold, start unstaking half "
                         f"(wait {pc['unstake_wait_hours']}h).")
            if minted > 0 and lost > 0 and st.paper_price >= pc["recoup_multiple"] * lost / minted:
                n = lost / st.paper_price
                L.append(f"RECOUP CAPITAL: price ≥ {pc['recoup_multiple']:.0f}× average cost. Sell ~{n:,.0f} PAPER "
                         f"(= ${lost:,.0f} spent) and keep the rest staked at zero cost.")
        if st.my_pending_rewards >= pc["claim_min_usd"]:
            L.append(f"CLAIM: ${st.my_pending_rewards:,.2f} of rewards available")
            acts["claim"] = True
        if st.my_queued_usd > 0:
            L.append(f"Queued in your favour: ${st.my_queued_usd:,.2f} (usable as margin for new opens)")
            qr = self.cfg.get("queue_recycle", {})
            if qr.get("enabled") and st.my_queued_usd >= qr["min_queued_usd"] and st.queue_len >= qr["min_queue_len"]:
                L.append("RECYCLE QUEUE: use the queued balance as margin for S1/S3. If you lose, the destroyed credit "
                         f"mints PAPER at {st.emission_per_usd:.0f}/$; if you win, the profit goes back to the queue.")
        if queue_alert:
            L.append("ALERT: the queue grew a lot in the last hour")
        return "\n".join(L), acts


class RushTracker:
    """Rush mode: the first stretch after launch, when the crowd liquidates on purpose to mint
    PAPER at 100 per $1. The window closes when emission drops below min_emission (or after max_minutes),
    not at a fixed time: the bot measures LP growth speed and estimates when that will happen."""

    def __init__(self, cfg: dict, launch_ts: float):
        self.cfg = cfg
        self.r = cfg.get("rush", {})
        self.launch = launch_ts
        self.hist: deque = deque()
        self.ended = False
        self.end_reason = ""
        self.spent = 0.0          # margin committed to rush orders
        self.minted = 0.0
        self.lost = 0.0
        self.rr = 0
        self.mode, self.mode_why = "pairs", ""

    def update(self, now: float, st: ProtocolState) -> None:
        self.hist.append((now, st.effective_lp))
        win = self.r.get("rate_window_min", 10) * 60
        while len(self.hist) > 2 and self.hist[0][0] < now - win:
            self.hist.popleft()
        if not self.ended and now >= self.launch:
            if st.emission_per_usd < self.r.get("min_emission", 95):
                self.ended, self.end_reason = True, f"emission below {self.r.get('min_emission', 95)} PAPER/$"
            elif now - self.launch > self.r.get("max_minutes", 120) * 60:
                self.ended, self.end_reason = True, "maximum rush time exceeded"

    def active(self, now: float) -> bool:
        return bool(self.r.get("enabled")) and not self.ended and now >= self.launch

    def rate(self) -> float:
        """$ per second of effective LP growth over the recent window."""
        if len(self.hist) < 2:
            return 0.0
        (t0, l0), (t1, l1) = self.hist[0], self.hist[-1]
        return (l1 - l0) / (t1 - t0) if t1 > t0 else 0.0

    def eta(self, target: float, st: ProtocolState) -> float:
        r = self.rate()
        gap = target - st.effective_lp
        if gap <= 0:
            return 0.0
        return gap / r if r > 0 else float("inf")

    def target_lp(self) -> float:
        """LP at which emission falls below min_emission: threshold + S·(√(100/E) − 1)."""
        th = self.cfg["thresholds"]
        e = self.r.get("min_emission", 95)
        return th["emission_decay_usd"] + th["tail_decay_scale_usd"] * (math.sqrt(100 / e) - 1)

    def projected_emission(self, st: ProtocolState, seconds: float) -> float:
        """Estimated emission in `seconds` at the current LP growth rate."""
        th = self.cfg["thresholds"]
        S, thr = th["tail_decay_scale_usd"], th["emission_decay_usd"]
        rate = max(self.rate(), 0.0)
        lp_f = st.effective_lp + rate * seconds
        if lp_f < thr:
            return 100.0
        h_now = S * (math.sqrt(100 / st.emission_per_usd) - 1) if st.emission_per_usd < 100 else 0.0
        h_f = h_now + rate * seconds if st.effective_lp >= thr else lp_f - thr
        return 100.0 * (S / (S + h_f)) ** 2

    def choose_mode(self, st: ProtocolState, snaps: dict, delay_s: float) -> tuple[str, str]:
        """Delta-neutral pairs cost less per PAPER; burning mints more per order.
        Burning only pays off if emission drops fast within one pair cycle."""
        m = self.r.get("mode", "auto")
        if m != "auto":
            return m, "manual choice"
        cycle = self.r["pair_target_minutes"] * 60 + delay_s
        e_f = self.projected_emission(st, cycle)
        drop = 1 - e_f / st.emission_per_usd
        if drop > self.r["burn_if_emission_drop"]:
            return "burn", f"estimated emission −{drop:.0%} within a pair cycle"
        return "pairs", f"stable emission (−{drop:.0%} per cycle)"

    def intents(self, now: float, st: ProtocolState, snaps: dict, busy: list, delay_s: float) -> list[Intent]:
        r = self.r
        if not self.active(now):
            return []
        eta2 = self.eta(self.target_lp(), st)
        left = self.r["max_minutes"] * 60 - (now - self.launch)
        eta2 = min(eta2, left)
        if eta2 < delay_s + r["cancel_buffer_s"]:
            return []                       # the order would confirm after the window closed
        mode, why = self.choose_mode(st, snaps, delay_s)
        self.mode, self.mode_why = mode, why
        rush_busy = [b for b in busy if b.strategy == "RUSH"]
        out = []
        lv = self.cfg["leverage"]
        eta_txt = "n/d" if eta2 == float("inf") else f"{eta2 / 60:.0f} min"
        if mode == "pairs":
            for sn in sorted(snaps.values(), key=lambda s: -s.vol_1m):
                if len(rush_busy) + 2 > r["max_open"] or self.spent + 2 * r["margin_usd"] > r["budget_usd"]:
                    break
                if any(b.asset == sn.asset for b in rush_busy):
                    continue                # one pair per asset at a time
                d = max(sn.vol_1m * math.sqrt(r["pair_target_minutes"]), r["pair_min_distance"])
                lev = lev_for_distance(d, lv["liq_buffer"], lv["min"], min(r["leverage"], lv["max"]))
                liq = liq_distance(lev, lv["liq_buffer"])
                self.rr += 1
                for side, w in (("long", self.cfg["strategy_wallets"].get("long", "B")),
                                ("short", self.cfg["strategy_wallets"].get("short", "C"))):
                    it = Intent("RUSH", sn.asset, side, r["margin_usd"], lev, liq, liq, r["max_minutes"],
                                f"delta-neutral pair ±{liq:.2%} ({why}); rush ends in {eta_txt}",
                                group=f"pair:{sn.asset}:{self.rr}", wallet=w)
                    out.append(it)
                    rush_busy.append(it)
                self.spent += 2 * r["margin_usd"]
            return out
        assets = sorted(snaps.values(), key=lambda s: -s.vol_1m)   # more volatile = faster liquidation
        while len(rush_busy) < r["max_open"] and self.spent + r["margin_usd"] <= r["budget_usd"] and assets:
            sn = assets[self.rr % len(assets)]
            fixed = "long" if self.cfg["assets"].index(sn.asset) % 2 == 0 else "short"
            side = fixed                      # wallet A: a single side per asset
            lev = min(r["leverage"], lv["max"])
            liq = liq_distance(lev, lv["liq_buffer"])
            it = Intent("RUSH", sn.asset, side, r["margin_usd"], lev, 1.0, liq, r["max_minutes"],
                        f"burn ({why}); rush ends in {eta_txt}, notional ${r['margin_usd'] * lev:,.0f}",
                        wallet=self.cfg["strategy_wallets"].get("RUSH", "A"))
            out.append(it)
            rush_busy.append(it)
            self.spent += r["margin_usd"]
            self.rr += 1
        return out

    def card(self, now: float, st: ProtocolState) -> list[str]:
        if not self.r.get("enabled") or now < self.launch:
            return []
        th = self.cfg["thresholds"]
        if self.ended:
            return [f"RUSH OVER ({self.end_reason}). Normal strategies based on the regime."]
        e2, e5 = self.eta(self.target_lp(), st), self.eta(th["sweep_usd"], st)
        fmt = lambda x: "n/a" if x == float("inf") else f"{x / 60:.0f} min"
        avg = self.lost / self.minted if self.minted else 0.0
        share = (st.my_paper + st.my_staked) / st.paper_supply if st.paper_supply else 0.0
        cap = st.paper_supply * 0.01
        return [
            f"RUSH ACTIVE for {(now - self.launch) / 60:.0f} min | LP +${self.rate() * 60:,.0f}/min | "
            f"emission {st.emission_per_usd:.1f}/$ | below {self.r.get('min_emission', 95)}/$ in {fmt(e2)} | $5M sweep in {fmt(e5)}",
            f"Rush budget: ${self.spent:,.0f}/${self.r['budget_usd']:,.0f} | net cost ${self.lost:,.2f} | "
            f"PAPER minted {self.minted:,.0f} (net cost ${avg:.4f}/PAPER) | supply share {share:.4%}",
            f"Valuation at mint: supply {st.paper_supply:,.0f} × $0.01 = ${cap:,.0f}. A 100% yield "
            f"needs ${cap:,.0f}/year to stakers.",
            (f"PAIRS MODE ({self.mode_why}): same asset, same size, long on B and short on C together. "
             "One leg liquidates and mints PAPER, the other closes in profit (or liquidates too and mints)."
             if self.mode == "pairs" else
             f"BURN MODE ({self.mode_why}): wallet A at max leverage, no TP, let it liquidate."),
            "STAKE the PAPER you receive right away. Cancel pending orders when the bot says so.",
        ]
