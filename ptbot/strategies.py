"""Regime del protocollo + strategie + scheda azioni.

Formula chiave (spiegata in GUIDA.md): per un trade con TP e SL alla stessa distanza d,
EV per $1 di margine ≈ 0,5 × (E × P_eff − 1 + q × (1 − h(d)))
  E = PAPER emessi per $1 perso, P_eff = prezzo PAPER scontato, q = probabilità che la coda
  paghi, h(d) = trattenuta sul profitto a quella distanza. Senza prezzo PAPER il costo
  atteso per trade è circa h/2 del margine: stai comprando PAPER a sconto.
"""
from __future__ import annotations

import math
from collections import deque
from datetime import datetime
from pathlib import Path

import yaml

from .haircut import HaircutModel
from .models import ROME, Intent, MarketSnapshot, Position, ProtocolState, lev_for_distance, liq_distance, rome

INSOLVENTE, BOOTSTRAP, DECADIMENTO, SWEEP = "INSOLVENTE", "BOOTSTRAP", "DECADIMENTO", "SWEEP"

PHASE_TEXT = {
    -1: "PRE-LANCIO: prepara i wallet e testa il bot. Il predeposito apre l'08/10.",
    0: "FASE 0 – PREDEPOSITO: deposita e crea l'account ADESSO. Trading in pausa. Dopo, i depositi avranno priorità bassa.",
    1: "FASE 1 – SOLO FRONTEND: ordini solo da papertrade.xyz tramite relayer. Il bot dà segnali MANUALI. "
       "Nozionale piccolo = conferme lente; gli ordini in attesa si possono annullare. PAPER non trasferibile.",
    2: "FASE 2 – CONTRATTO APERTO: esecuzione automatica possibile. Attenzione a MEV e bot concorrenti.",
    4: "FASE 4 – PAPER TRASFERIBILE: acquisto/vendita possibili, valuta il mercato secondario.",
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
    """Prezzo di mercato se PAPER è trasferibile, altrimenti valore implicito dai flussi di staking."""
    pc = cfg["paper"]
    if phase >= 4 and st.paper_price:
        return st.paper_price * (1 - pc["liquidity_discount"]), "mercato"
    if st.paper_staked > 0 and st.staker_rewards_24h_usd > 0:
        # all'inizio la supply è minuscola e cresce in fretta: dividere per la supply di oggi gonfia il valore.
        # Si usa la supply attesa tra N giorni al ritmo di conio delle ultime 24h.
        future = st.paper_staked + st.paper_minted_24h * pc.get("dilution_horizon_days", 90)
        per_token = st.staker_rewards_24h_usd * 365 / future
        return per_token / pc["implied_required_yield"] * (1 - pc["nontransferable_discount"]), "implicito"
    return None, "n/d"


def contrarian_side(cfg: dict, st: ProtocolState, asset: str):
    """Lato opposto alla folla se l'OI è sbilanciato. Se la folla vince, l'LP va sotto e la coda si
    allunga: chi sta dall'altra parte perde quando l'emissione è al massimo e vince quando l'LP è piena."""
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
    """(costo fisso per $ di margine, quota tolta dalle vincite)."""
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
            r = INSOLVENTE
        elif eff < self.th["emission_decay_usd"]:
            r = BOOTSTRAP
        elif eff < self.th["sweep_usd"]:
            r = DECADIMENTO
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

    # ---------- dimensionamento comune ----------
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
        """EV per $1 di margine di un trade con TP = d e SL = liquidazione (≈ 50/50)."""
        p_eff, _ = paper_value(self.cfg, st, phase)
        if with_paper and p_eff is None:
            return None
        q = self.cfg["paper"]["queue_pay_prob"].get(regime, 1.0)
        gross = lev * d                                  # profitto lordo per $ se tocca il TP
        fixed, on_win = fee_per_margin(self.cfg, lev, gross)
        win = q * gross * (1 - self.h.estimate(d)) * (1 - on_win)
        paper = st.emission_per_usd * (p_eff or 0.0) if with_paper else 0.0
        # la fee si paga alla chiusura: sul ramo vincente sempre, sul perdente (liquidazione) solo se configurato
        loss_fee = fixed if self.cfg.get("fees", {}).get("charged_on_liquidation", False) else 0.0
        return 0.5 * (paper - 1 - loss_fee + win - fixed)

    def cost_per_paper(self, st: ProtocolState, regime: str, d: float, lev: float) -> float | None:
        """Costo atteso per PAPER di un trade TP = SL (o di una coppia): (1 − q·G·(1−h)) / E."""
        if st.emission_per_usd <= 0:
            return None
        q = self.cfg["paper"]["queue_pay_prob"].get(regime, 1.0)
        g = lev * d
        return max(1 - q * g * (1 - self.h.estimate(d)), 0.0) / st.emission_per_usd

    @staticmethod
    def _open_by(open_pos, strategy, asset=None):
        return [p for p in open_pos if p.strategy == strategy and (asset is None or p.asset == asset)]

    # ---------- generazione ordini ----------
    def intents(self, now: float, st: ProtocolState, regime: str, drain: bool,
                snaps: dict[str, MarketSnapshot], open_pos: list[Position], phase: int = 1) -> list[Intent]:
        out: list[Intent] = []
        s = self.cfg["strategies"]
        if phase < 1:
            return out                                   # pre-lancio / predeposito: niente trading

        # S1 — finestra di lancio: posizioni piccole, TP=SL nella zona a trattenuta bassa
        e = s["early"]
        in_window = 0 <= now - self.launch_ts <= e["days_from_launch"] * 86400
        drain_off = drain and self.cfg["thresholds"].get("drain_mode", "defensive") == "defensive"
        if e["enabled"] and in_window and regime in (INSOLVENTE, BOOTSTRAP) and not drain_off:
            for a, sn in snaps.items():
                if self._open_by(open_pos, "S1", a):
                    continue
                side = "long" if sn.mid > sn.range_high else "short" if sn.mid < sn.range_low else None
                cs = contrarian_side(self.cfg, st, a)
                if cs and side:
                    side = cs                     # con folla sbilanciata il lato lo decide il posizionamento
                if not side:
                    continue
                d, lev, liq = self.sizing(sn.vol_1m)
                out.append(Intent("S1", a, side, e["margin_usd"], lev, d, liq, e["max_hold_minutes"],
                                  f"breakout {e['breakout_lookback_min']}m, d={d:.2%}, h≈{self.h.estimate(d):.0%}"))

        # S2 — eventi: straddle (long + short). La gamba perdente conia PAPER, la vincente corre
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
                                          hold, f"{ev['name']} atteso {ev['expected_move_bps']:.0f}bps",
                                          group=grp))

        # S3 — farming continuo: per prezzo massimo per PAPER oppure per EV
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
                    why = f"costo stimato ${cost:.4f}/PAPER ≤ ${f['max_cost_per_paper']}"
                else:
                    ev = self.farming_ev(st, regime, d, lev, phase)
                    if pv is None or ev is None or ev < f["min_ev_per_margin"]:
                        continue
                    why = f"EV {ev:+.2f}/$ (PAPER {src} {pv:.5f})"
                if f.get("use_pairs", False):
                    grp = f"S3pair:{a}:{int(now)}"
                    for side in ("long", "short"):
                        out.append(Intent("S3", a, side, f["margin_usd"], lev, d, liq, f["max_hold_minutes"],
                                          f"coppia ±{d:.2%}, {why}", group=grp,
                                          wallet=self.cfg["strategy_wallets"].get(side, "")))
                else:
                    side = contrarian_side(self.cfg, st, a) or ("long" if sn.mid >= (sn.range_high + sn.range_low) / 2 else "short")
                    out.append(Intent("S3", a, side, f["margin_usd"], lev, d, liq, f["max_hold_minutes"], why))
        prio = {"S2": 0, "S3": 1, "S1": 2}
        return sorted(out, key=lambda i: prio[i.strategy])

    # ---------- scheda azioni ----------
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
        L = [f"REGIME: {regime}{' + DRENAGGIO' if drain else ''}  |  ora Roma {rome(now)}",
             PHASE_TEXT.get(phase, PHASE_TEXT[1]),
             f"LP ${st.lp_usd:,.0f}  coda ${st.queue_usd:,.0f} ({st.queue_len})  LP effettiva ${st.effective_lp:,.0f}",
             f"Emissione {st.emission_per_usd:.1f} PAPER/$  |  valore PAPER ({src}, scontato) "
             f"{'n/d' if pv is None else f'${pv:.6f}'}  |  APR staking "
             f"{'n/d' if apr is None else f'{apr:.0%}'}",
             f"Costo PAPER: trade simmetrico ≈ ${-ev0 / (0.5 * st.emission_per_usd):.4f}/PAPER "
             f"(EV {ev0:+.3f}/$ senza PAPER) | liquidazione voluta ≈ ${1 / st.emission_per_usd:.4f}/PAPER",
             f"Trattenuta: {self.h.describe()} → distanza TP/SL {d:.2%}, leva {lev:.0f}x, h≈{self.h.estimate(d):.0%}"]
        in_window = 0 <= now - self.launch_ts <= self.cfg["strategies"]["early"]["days_from_launch"] * 86400
        drain_off = drain and self.cfg["thresholds"].get("drain_mode", "defensive") == "defensive"
        s1 = in_window and regime in (INSOLVENTE, BOOTSTRAP) and not drain_off
        L.append(f"S1 finestra di lancio: {'ATTIVA' if s1 else 'spenta'}")
        nxt = [e for e in self.events if e["ts"] > now]
        L.append("S2 eventi: " + (f"prossimo {nxt[0]['name']} alle {rome(nxt[0]['ts'])}" if nxt else "nessuno in calendario"))
        f = self.cfg["strategies"]["farming"]
        if f.get("mode", "ev") == "price":
            c3 = self.cost_per_paper(st, regime, d, lev)
            on = c3 is not None and c3 <= f["max_cost_per_paper"] and not drain_off
            L.append(f"S3 farming (prezzo max ${f['max_cost_per_paper']}): costo stimato "
                     f"{'n/d' if c3 is None else f'${c3:.4f}'}/PAPER → " +
                     ("spento (drenaggio)" if drain_off else "ATTIVO" if on else "spento (troppo caro)"))
        else:
            L.append("S3 farming: " + ("serve un valore PAPER (prezzo o ricompense staking)" if ev is None else
                                       f"EV {ev:+.2f}/$ → " + ("spento (drenaggio)" if drain_off else
                                       "ATTIVO" if ev >= f['min_ev_per_margin'] else "spento (EV sotto soglia)")))
        # PAPER
        if phase < 4:
            paper = ("NON TRASFERIBILE: nessun acquisto/vendita possibile. Stake di tutti i PAPER coniati, "
                     "il loro valore oggi sono solo le ricompense in USDC.")
            acts["stake"] = phase >= 1
        elif drain:
            paper = "RIDUCI: LP in calo forte, emissioni in risalita. Non comprare, alleggerisci i PAPER liberi."
        elif not st.paper_price:
            paper = "Non quotato: accumula solo dalle perdite e metti in staking ciò che ricevi."
            acts["stake"] = True
        elif regime in (INSOLVENTE, BOOTSTRAP):
            paper = "NON COMPRARE: emissioni massime (100/$) → offerta in crescita. Stake dei PAPER ricevuti."
            acts["stake"] = True
        elif regime == DECADIMENTO:
            ok = apr is not None and apr >= pc["target_apr"]
            paper = ("ACCUMULA + STAKE: APR sopra soglia" if ok else
                     f"ASPETTA: APR {'n/d' if apr is None else f'{apr:.0%}'} < {pc['target_apr']:.0%}") + "; stake dei PAPER già in mano."
            acts["stake"] = True
        else:
            paper = "STAKE TUTTO: sweep attivo, ogni $ di guadagno LP sopra 5M va agli staker."
            acts["stake"] = True
        L.append("PAPER: " + paper)
        lost, minted = spent        # lost = perdita NETTA di tutti i trade chiusi (perdite − profitti)
        if minted > 0:
            L.append(f"Costo netto PAPER: ${lost / minted:.4f}/PAPER (risultato trade ${-lost:+,.2f}, {minted:,.0f} PAPER coniati)"
                     if lost > 0 else f"PAPER a costo zero: trade in utile di ${-lost:,.2f} con {minted:,.0f} PAPER coniati")
        if phase >= 4 and st.paper_price:
            if apr is not None and apr < pc["sell_apr"]:
                L.append(f"VENDI: APR {apr:.0%} < {pc['sell_apr']:.0%} → il prezzo sconta più dei flussi reali. "
                         "Vendi metà dei PAPER (unstake se serve).")
            elif apr is not None and apr < pc["prepare_unstake_apr"] and pc["unstake_wait_hours"] > 0:
                L.append(f"PREPARA: APR {apr:.0%} vicino alla soglia di vendita, avvia l'unstake di metà "
                         f"(attesa {pc['unstake_wait_hours']}h).")
            if minted > 0 and lost > 0 and st.paper_price >= pc["recoup_multiple"] * lost / minted:
                n = lost / st.paper_price
                L.append(f"RECUPERA CAPITALE: prezzo ≥ {pc['recoup_multiple']:.0f}× costo medio. Vendi ~{n:,.0f} PAPER "
                         f"(= ${lost:,.0f} spesi) e tieni il resto in staking a costo zero.")
        if st.my_pending_rewards >= pc["claim_min_usd"]:
            L.append(f"CLAIM: ${st.my_pending_rewards:,.2f} di ricompense disponibili")
            acts["claim"] = True
        if st.my_queued_usd > 0:
            L.append(f"In coda a tuo favore: ${st.my_queued_usd:,.2f} (usabile come margine per nuove aperture)")
            qr = self.cfg.get("queue_recycle", {})
            if qr.get("enabled") and st.my_queued_usd >= qr["min_queued_usd"] and st.queue_len >= qr["min_queue_len"]:
                L.append("RICICLA CODA: usa il saldo in coda come margine per S1/S3. Se perdi, il credito distrutto "
                         f"conia PAPER a {st.emission_per_usd:.0f}/$; se vinci, il profitto torna in coda.")
        if queue_alert:
            L.append("ALERT: la coda è cresciuta molto nell'ultima ora")
        return "\n".join(L), acts


class RushTracker:
    """Modalità corsa: la prima fase dopo il lancio, quando la folla si liquida apposta per coniare
    PAPER a 100 per $1. La finestra si chiude quando l'LP effettiva supera i $2M (emissione in
    decadimento), non a un orario fisso: il bot misura la velocità dell'LP e stima quando succederà."""

    def __init__(self, cfg: dict, launch_ts: float):
        self.cfg = cfg
        self.r = cfg.get("rush", {})
        self.launch = launch_ts
        self.hist: deque = deque()
        self.ended = False
        self.end_reason = ""
        self.spent = 0.0          # margine impegnato in ordini della corsa
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
                self.ended, self.end_reason = True, f"emissione sotto {self.r.get('min_emission', 95)} PAPER/$"
            elif now - self.launch > self.r.get("max_minutes", 120) * 60:
                self.ended, self.end_reason = True, "tempo massimo della corsa superato"

    def active(self, now: float) -> bool:
        return bool(self.r.get("enabled")) and not self.ended and now >= self.launch

    def rate(self) -> float:
        """$ al secondo di crescita dell'LP effettiva nella finestra recente."""
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
        """LP a cui l'emissione scende sotto min_emission: soglia + S·(√(100/E) − 1)."""
        th = self.cfg["thresholds"]
        e = self.r.get("min_emission", 95)
        return th["emission_decay_usd"] + th["tail_decay_scale_usd"] * (math.sqrt(100 / e) - 1)

    def projected_emission(self, st: ProtocolState, seconds: float) -> float:
        """Emissione stimata tra `seconds` al ritmo attuale di crescita dell'LP."""
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
        """Coppie delta neutral costano meno per PAPER; il burn conia di più per ordine.
        Il burn conviene solo se l'emissione cala in fretta durante il tempo di un ciclo coppia."""
        m = self.r.get("mode", "auto")
        if m != "auto":
            return m, "scelta manuale"
        cycle = self.r["pair_target_minutes"] * 60 + delay_s
        e_f = self.projected_emission(st, cycle)
        drop = 1 - e_f / st.emission_per_usd
        if drop > self.r["burn_if_emission_drop"]:
            return "burn", f"emissione stimata −{drop:.0%} in un ciclo coppia"
        return "pairs", f"emissione stabile (−{drop:.0%} in un ciclo)"

    def intents(self, now: float, st: ProtocolState, snaps: dict, busy: list, delay_s: float) -> list[Intent]:
        r = self.r
        if not self.active(now):
            return []
        eta2 = self.eta(self.target_lp(), st)
        left = self.r["max_minutes"] * 60 - (now - self.launch)
        eta2 = min(eta2, left)
        if eta2 < delay_s + r["cancel_buffer_s"]:
            return []                       # l'ordine verrebbe confermato a finestra chiusa
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
                    continue                # una coppia per asset alla volta
                d = max(sn.vol_1m * math.sqrt(r["pair_target_minutes"]), r["pair_min_distance"])
                lev = lev_for_distance(d, lv["liq_buffer"], lv["min"], min(r["leverage"], lv["max"]))
                liq = liq_distance(lev, lv["liq_buffer"])
                self.rr += 1
                for side, w in (("long", self.cfg["strategy_wallets"].get("long", "B")),
                                ("short", self.cfg["strategy_wallets"].get("short", "C"))):
                    it = Intent("RUSH", sn.asset, side, r["margin_usd"], lev, liq, liq, r["max_minutes"],
                                f"coppia delta neutral ±{liq:.2%} ({why}); fine corsa tra {eta_txt}",
                                group=f"pair:{sn.asset}:{self.rr}", wallet=w)
                    out.append(it)
                    rush_busy.append(it)
                self.spent += 2 * r["margin_usd"]
            return out
        assets = sorted(snaps.values(), key=lambda s: -s.vol_1m)   # più volatile = liquidazione più rapida
        while len(rush_busy) < r["max_open"] and self.spent + r["margin_usd"] <= r["budget_usd"] and assets:
            sn = assets[self.rr % len(assets)]
            fixed = "long" if self.cfg["assets"].index(sn.asset) % 2 == 0 else "short"
            side = fixed                      # wallet A: un solo lato per asset
            lev = min(r["leverage"], lv["max"])
            liq = liq_distance(lev, lv["liq_buffer"])
            it = Intent("RUSH", sn.asset, side, r["margin_usd"], lev, 1.0, liq, r["max_minutes"],
                        f"burn ({why}); fine corsa tra {eta_txt}, nozionale ${r['margin_usd'] * lev:,.0f}",
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
            return [f"CORSA FINITA ({self.end_reason}). Strategie normali in base al regime."]
        e2, e5 = self.eta(self.target_lp(), st), self.eta(th["sweep_usd"], st)
        fmt = lambda x: "n/d" if x == float("inf") else f"{x / 60:.0f} min"
        avg = self.lost / self.minted if self.minted else 0.0
        share = (st.my_paper + st.my_staked) / st.paper_supply if st.paper_supply else 0.0
        cap = st.paper_supply * 0.01
        return [
            f"CORSA ATTIVA da {(now - self.launch) / 60:.0f} min | LP +${self.rate() * 60:,.0f}/min | "
            f"emissione {st.emission_per_usd:.1f}/$ | sotto {self.r.get('min_emission', 95)}/$ tra {fmt(e2)} | sweep $5M tra {fmt(e5)}",
            f"Budget corsa: ${self.spent:,.0f}/${self.r['budget_usd']:,.0f} | costo netto ${self.lost:,.2f} | "
            f"PAPER coniati {self.minted:,.0f} (costo netto ${avg:.4f}/PAPER) | quota supply {share:.4%}",
            f"Valutazione al conio: supply {st.paper_supply:,.0f} × $0,01 = ${cap:,.0f}. Per un rendimento del 100% "
            f"servono ${cap:,.0f}/anno agli staker.",
            (f"MODALITÀ COPPIE ({self.mode_why}): stesso asset, stessa size, long su B e short su C insieme. "
             "Una gamba si liquida e conia PAPER, l'altra chiude in profitto (o si liquida a sua volta e conia)."
             if self.mode == "pairs" else
             f"MODALITÀ BURN ({self.mode_why}): wallet A a leva massima, niente TP, lascia liquidare."),
            "STAKE subito i PAPER ricevuti. Annulla gli ordini in attesa quando il bot lo segnala.",
        ]
