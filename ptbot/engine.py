"""Order execution + main loop."""
from __future__ import annotations

import time
from collections import deque

from .haircut import HaircutModel, scale_for
from .infra import Alerter, RiskManager, Store
from .models import Intent, MarketSnapshot, Position, liq_distance
from .strategies import PHASE_TEXT, RegimeDetector, RushTracker, StrategyBook, current_phase


class DryRunExecutor:
    """Simulates opens/closes at the BBO mid, like Papertrade does. With SimProtocol it actually settles
    against the simulated LP (queue, emissions); with the real protocol it estimates emissions and haircut."""

    def __init__(self, protocol, haircut: HaircutModel, store: Store, cfg: dict):
        self.protocol = protocol
        self.h = haircut
        self.store = store
        self.cfg = cfg
        self.true_hc = (cfg["sim"]["true_base_rate"], cfg["sim"]["true_k"]) if protocol.simulated else None
        self.last_emission = 100.0
        self.last_queue = 0.0

    def open(self, it: Intent, sn: MarketSnapshot, now: float) -> Position:
        liq = liq_distance(it.leverage, self.cfg["leverage"]["liq_buffer"])
        p = Position(self.store.next_id(), it.strategy, it.asset, it.side, it.margin, it.leverage, sn.mid, now,
                     it.tp_move, min(it.sl_move, liq), liq, it.max_hold_min, it.group, it.reason,
                     wallet=it.wallet)
        self.store.save_trade(p)
        return p

    def close(self, p: Position, price: float, now: float, why: str) -> Position:
        mv = p.move(price)
        raw = p.margin * p.leverage * mv
        if why == "liquidation":
            raw = -p.margin
        p.exit, p.closed_ts, p.status, p.close_reason = price, now, "closed", why
        if raw < 0:
            loss = min(-raw, p.margin)
            liq = why == "liquidation"
            if self.protocol.simulated:
                minted = self.protocol.settle_loss(loss, liquidation=liq)
            else:   # docs: liquidations = full margin; solvent losing closes = loss − 2%
                basis = loss if (liq or self.last_queue > 0) else loss * (1 - self.cfg["thresholds"]["loss_fee_lp"])
                minted = basis * self.last_emission
            p.pnl, p.paper_minted = -loss, minted or 0.0
        else:
            if self.protocol.simulated:
                h = 1 - scale_for(mv, *self.true_hc)
                paid, queued = self.protocol.settle_win(raw, h)
                self.h.add(mv, h)                        # in sim the haircut is "observed" automatically
                self.store.haircut_obs(now, mv, h)
            else:
                h = self.h.estimate(mv)                  # estimate: record the real one with `calib add`
                queued = 0.0
            p.pnl, p.haircut, p.queued_usd = raw * (1 - h), h, queued
        f = self.cfg.get("fees", {})
        r, base = f.get("frontend_rate", 0.0), f.get("frontend_base", "margin")
        if why == "liquidation" and not f.get("charged_on_liquidation", False):
            r = 0.0                                       # fee taken on close: not on liquidations (to be verified)
        if base == "margin":
            p.pnl -= r * p.margin
        elif base == "notional":
            p.pnl -= r * p.margin * p.leverage
        elif base == "profit" and p.pnl > 0:
            p.pnl *= 1 - r
        self.store.save_trade(p)
        return p


class LiveExecutor(DryRunExecutor):
    """Real orders on the contract. Requires the open/close functions (relayer-only until phase 2)."""

    def open(self, it, sn, now):
        raise NotImplementedError("Live execution not wired yet: needs the official open/close functions (phase 2).")

    def close(self, p, price, now, why):
        raise NotImplementedError("Live execution not wired yet.")


class Engine:
    def __init__(self, cfg, clock, market, protocol, executor, risk: RiskManager, store: Store,
                 alerter: Alerter, book: StrategyBook, quiet: bool = False):
        self.cfg, self.clock, self.market, self.protocol = cfg, clock, market, protocol
        self.ex, self.risk, self.store, self.alert, self.book = executor, risk, store, alerter, book
        self.dry = executor if type(executor) is DryRunExecutor else DryRunExecutor(protocol, book.h, store, cfg)
        self.pending_open: list = []          # (ready_ts, intent) waiting for the relayer
        self.supply_hist = deque()
        self.rush = RushTracker(cfg, book.launch_ts)
        self.rush_was_active = False
        self.phase = None
        self.regime = RegimeDetector(cfg["thresholds"], cfg["risk"])
        self.open: list[Position] = []
        self.quiet = quiet
        self.last_slow = 0.0
        self.last_card = ""
        self.last_regime = None
        self.snaps: dict[str, MarketSnapshot] = {}
        self.stopped = False
        self.was_drain = False

    # ---------- fast loop: position management ----------
    def delay(self, margin: float, lev: float) -> float:
        c = self.cfg.get("congestion", {})
        if self.phase not in c.get("enabled_phases", []):
            return 0.0
        extra = c["small_extra_delay_s"] if margin * lev < c["small_notional_usd"] else 0.0
        base = c.get("rush_base_delay_s", c["base_delay_s"]) if self.rush.active(self.clock.now()) else c["base_delay_s"]
        return base + extra

    def executor(self):
        return self.ex if (self.cfg["mode"] == "live" and (self.phase or 0) >= 2) else self.dry

    # ---------- fast loop: position management ----------
    def fast(self, now: float) -> None:
        self.snaps = self.market.snapshot(now)
        if self.risk.kill_requested():
            self.pending_open.clear()
            self.close_all(now, "kill switch")
            self.stopped = True
            return
        ttl = self.cfg.get("congestion", {}).get("intent_ttl_s", 3600)
        for ready, it in list(self.pending_open):       # opens confirmed by the relayer
            if ready - getattr(it, "_sent", ready) > ttl and now >= getattr(it, "_sent", now) + ttl:
                self.pending_open.remove((ready, it))
                if it.strategy == "RUSH":
                    self.rush.spent -= it.margin
                self.alert.send(now, f"Intent EXPIRED (pending over 1h): {it.strategy} {it.asset} {it.side}", "WARN", self.quiet)
                continue
            sn = self.snaps.get(it.asset)
            if ready <= now and sn:
                self.pending_open.remove((ready, it))
                p = self.executor().open(it, sn, now)
                self.open.append(p)
                self.alert.send(now, f"FILLED #{p.id} {p.strategy} {p.asset} {p.side} entry {p.entry:.4f}",
                                "INFO", self.quiet)
        for p in list(self.open):
            sn = self.snaps.get(p.asset)
            if not sn:
                continue
            mv = p.move(sn.mid)
            if -mv >= p.liq_move:                        # liquidation takes priority over everything
                self._close(p, sn.mid, now, "liquidation")
                continue
            if p.pending_close_ts is not None:
                if now >= p.pending_close_ts:
                    self._close(p, sn.mid, now, p.pending_why)
                continue
            why = None
            if -mv >= p.sl_move:
                why = "stop"
            elif mv >= p.tp_move:
                why = "take profit"
            elif now - p.opened_ts >= p.max_hold_min * 60:
                why = "max hold time"
            if why:
                self.request_close(p, sn.mid, now, why)

    def request_close(self, p, price, now, why):
        dl = self.delay(p.margin, p.leverage)
        if dl > 0:
            p.pending_close_ts, p.pending_why = now + dl, why + " (delayed)"
            if self.phase == 1:
                self.alert.send(now, f"MANUAL SIGNAL: CLOSE #{p.id} [wallet {p.wallet}] {p.asset} {p.side} ({why})",
                                "TRADE", self.quiet)
        else:
            self._close(p, price, now, why)

    def queue_guard(self, now, st):
        """Closes winning trades early when the LP risks not being able to pay them."""
        qg = self.cfg.get("queue_guard", {})
        if not qg.get("enabled"):
            return
        winners = []
        for p in self.open:
            sn = self.snaps.get(p.asset)
            if not sn or p.pending_close_ts is not None:
                continue
            mv = p.move(sn.mid)
            if mv > 0:
                net = p.margin * p.leverage * mv * (1 - self.book.h.estimate(mv))
                if net >= qg["min_gain_usd"]:
                    winners.append((p, sn.mid, net, mv))
        if not winners:
            return
        mine = sum(w[2] for w in winners)
        exposure = max(st.open_pnl_total or 0.0, mine)
        free, rate = st.effective_lp, self.rush.rate()
        dl = max(self.delay(w[0].margin, w[0].leverage) for w in winners)
        reason = None
        if st.queue_usd > 0:
            if self.regime.queue_growing:   # growing queue: close only trades already halfway to the TP
                winners = [w for w in winners if w[3] >= 0.5 * w[0].tp_move]
                reason = "FIFO queue growing: closing now = earlier spot in line"
        elif free < qg["coverage_min"] * exposure:
            reason = f"low coverage (free LP ${free:,.0f} vs open profits ${exposure:,.0f})"
        elif rate < 0 and (free - exposure) / -rate < dl + qg["buffer_s"]:
            reason = f"LP falling ${-rate * 60:,.0f}/min: would not cover profits before confirmation"
        if reason:
            for p, price, net, _ in winners:
                self.request_close(p, price, now, "queue guard: " + reason)

    def _close(self, p, price, now, why):
        self.executor().close(p, price, now, why)
        if p.strategy == "RUSH":
            self.rush.minted += p.paper_minted
            self.rush.lost -= p.pnl                      # net cost: losses minus profits of the winning legs
        self.open.remove(p)
        extra = f", PAPER +{p.paper_minted:,.0f}" if p.paper_minted else ""
        extra += f", queued ${p.queued_usd:,.2f}" if p.queued_usd else ""
        extra += f", haircut {p.haircut:.0%}" if p.haircut is not None else ""
        self.alert.send(now, f"CLOSED #{p.id} {p.strategy} {p.asset} {p.side} {why}: PnL ${p.pnl:+.2f}{extra}",
                        "TRADE", self.quiet)

    def close_all(self, now, why):
        for p in list(self.open):
            sn = self.snaps.get(p.asset)
            if sn:
                self._close(p, sn.mid, now, why)

    # ---------- slow loop: regime, strategies, action card ----------
    def slow(self, now: float) -> None:
        ph = current_phase(self.cfg, now, self.book.launch_ts)
        if ph != self.phase:
            self.alert.send(now, "Phase change → " + PHASE_TEXT.get(ph, ""), "REGIME", self.quiet)
            if self.cfg["mode"] == "live" and ph < 2:
                self.alert.send(now, "Live mode disabled until the contract opens (phase 2): manual signals only.",
                                "WARN", self.quiet)
            self.phase = ph
        st = self.protocol.state(now)
        self.supply_hist.append((now, st.paper_supply))      # mint rate for dilution
        while len(self.supply_hist) > 2 and self.supply_hist[0][0] < now - 86400:
            self.supply_hist.popleft()
        t0, s0 = self.supply_hist[0]
        st.paper_minted_24h = (st.paper_supply - s0) * 86400 / max(now - t0, 3600)
        for e in {self.ex, self.dry}:
            e.last_emission, e.last_queue = st.emission_per_usd, st.queue_usd
        reg = self.regime.update(st)
        if reg != self.last_regime:
            self.alert.send(now, f"Regime change: {self.last_regime} → {reg}", "REGIME", self.quiet)
            self.last_regime = reg
        if self.regime.drain and not self.was_drain:
            self.alert.send(now, "DRAIN: effective LP fell past the threshold from its peak", "ALERT", self.quiet)
        self.was_drain = self.regime.drain
        self.rush.update(now, st)
        self.queue_guard(now, st)
        rush_on = self.rush.active(now) and self.phase >= 1
        if self.rush_was_active and not rush_on:
            self.alert.send(now, f"RUSH OVER: {self.rush.end_reason}. Switching to normal strategies.", "REGIME", self.quiet)
        self.rush_was_active = rush_on
        lost, minted = self.store.db.execute(
            "SELECT COALESCE(SUM(-pnl),0), COALESCE(SUM(paper_minted),0) FROM trades WHERE status='closed'").fetchone()
        card, acts = self.book.action_card(now, st, reg, self.regime.drain, self.regime.queue_alert, self.snaps,
                                           self.phase, (lost, minted))
        rc = self.rush.card(now, st) if self.phase >= 1 else []
        if rc:
            lines = [("S1 launch window: paused during the rush" if l.startswith("S1 ") else
                      "S3 farming: paused during the rush" if l.startswith("S3 ") else l)
                     for l in card.split("\n")] if rush_on else card.split("\n")
            card = "\n".join(lines[:2] + rc + lines[2:])
        if rush_on:                                       # cancel orders that would confirm after the window closes
            eta2 = min(self.rush.eta(self.rush.target_lp(), st),
                       self.cfg["rush"]["max_minutes"] * 60 - (now - self.book.launch_ts))
            for ready, it in list(self.pending_open):
                if it.strategy == "RUSH" and eta2 < (ready - now) + self.cfg["rush"]["cancel_buffer_s"]:
                    self.pending_open.remove((ready, it))
                    self.rush.spent -= it.margin
                    self.alert.send(now, f"MANUAL SIGNAL: CANCEL pending order {it.asset} {it.side} "
                                         f"(rush window closes in {eta2 / 60:.0f} min)", "TRADE", self.quiet)
        if self.protocol.simulated:
            if acts["stake"] and st.my_paper > 0 and self.phase >= 1:
                self.protocol.stake_all()
            if acts["claim"]:
                got = self.protocol.claim()
                self.alert.send(now, f"Claim staking rewards: ${got:,.2f}", "INFO", self.quiet)
        self.store.snapshot(st, reg, card)
        if card.split("\n", 1)[1] != self.last_card.split("\n", 1)[-1] and not self.quiet:
            print("\n" + card + "\n")
        self.last_card = card
        busy = self.open + [i for _, i in self.pending_open]
        if rush_on:      # no symmetric trades during the rush: profitable closes stay stuck
            m, lv = self.cfg["rush"]["margin_usd"], self.cfg["rush"]["leverage"]
            new = self.rush.intents(now, st, self.snaps, busy, self.delay(m, lv))
        else:
            new = self.book.intents(now, st, reg, self.regime.drain, self.snaps, busy, self.phase)
        for it in new:
            sn = self.snaps[it.asset]
            ok, why = self.risk.approve(it, busy, sn, now)
            if not ok:
                if it.strategy == "RUSH":
                    self.rush.spent -= it.margin
                self.alert.send(now, f"Rejected {it.strategy} {it.asset} {it.side}: {why}", "INFO", True)
                continue
            if it.group:
                self.book.opened_events.add(it.group)
            busy.append(it)
            it._sent = now
            self.pending_open.append((now + self.delay(it.margin, it.leverage), it))
            tag = "MANUAL SIGNAL (papertrade.xyz)" if self.phase == 1 else "ORDER"
            tp_txt = "no TP, let it liquidate" if it.strategy == "RUSH" else f"TP {it.tp_move:.2%}"
            self.alert.send(now, f"{tag}: [wallet {it.wallet}] OPEN {it.strategy} {it.asset} {it.side} margin ${it.margin} "
                                 f"leverage {it.leverage:.0f}x, {tp_txt}, liq {liq_distance(it.leverage, self.cfg['leverage']['liq_buffer']):.2%} ({it.reason})",
                            "TRADE", self.quiet)

    def run(self, duration_s: float | None = None) -> None:
        start = self.clock.now()
        fast_s, slow_s = self.cfg["loops"]["fast_seconds"], self.cfg["loops"]["slow_seconds"]
        while not self.stopped:
            now = self.clock.now()
            if duration_s is not None and now - start >= duration_s:
                break
            try:
                self.fast(now)
                interval = self.cfg["rush"]["slow_seconds"] if self.rush.active(now) else slow_s
                if now - self.last_slow >= interval:
                    self.slow(now)
                    self.last_slow = now
            except Exception as e:  # network, RPC, API: keep going, report it
                self.alert.send(now, f"Error: {e}", "WARN", self.quiet)
                if not self.protocol.simulated:
                    time.sleep(5)
            self.clock.sleep(fast_s)
