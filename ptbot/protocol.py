"""Stato del protocollo Papertrade.

OnchainProtocol: legge il contratto su HyperEVM. Indirizzo e ABI non sono pubblici prima
del lancio: vanno compilati in config.yaml → onchain.
SimProtocol: simula LP, coda FIFO, emissioni PAPER e ricompense staker per i test.
"""
from __future__ import annotations

import json
import math
import random
from collections import deque
from pathlib import Path

from .models import ProtocolState


def emission_rate(tracked_lp: float, tail_progress: float, th: dict) -> float:
    """Docs (PaperTokenomics): 100 PAPER/$ finché l'LP tracciata è sotto $2M (anche negativa);
    sopra, 100 × (S / (S + H))² con S = $120M e H = guadagno LP cumulato oltre soglia (high-water mark)."""
    if tracked_lp < th["emission_decay_usd"]:
        return 100.0
    s = th.get("tail_decay_scale_usd", 120e6)
    return 100.0 * (s / (s + max(tail_progress, 0.0))) ** 2


class OnchainProtocol:
    """Legge lo stato dai contratti Papertrade SENZA ABI ufficiale (contratti non verificati).

    In config ogni funzione si scrive "contratto:nome", ad esempio "exchange:treasury" o
    "tokenomics:tailProgressUsd"; gli indirizzi stanno in onchain.contracts. L'ABI minimo di ogni
    getter (view, ritorno uint256, eventuale argomento address) viene costruito al volo.
    """
    simulated = False

    def __init__(self, cfg: dict, thresholds: dict):
        oc = cfg["onchain"]
        contracts = dict(oc.get("contracts") or {})
        if oc.get("contract_address"):
            contracts.setdefault("exchange", oc["contract_address"])
        if not contracts.get("exchange"):
            raise RuntimeError("Indirizzo dell'Exchange non configurato (onchain.contracts.exchange).")
        from web3 import Web3  # import solo se serve
        self.Web3 = Web3
        self.w3 = Web3(Web3.HTTPProvider(oc["rpc_url"]))
        self.contracts = {k: Web3.to_checksum_address(v) for k, v in contracts.items() if v}
        self.fn = oc["functions"]
        self.me = Web3.to_checksum_address(oc["my_address"]) if oc.get("my_address") else None
        self.ud = 10 ** oc["usd_decimals"]          # contabilità interna dell'Exchange (18 decimali)
        self.pd = 10 ** oc["paper_decimals"]
        self.th = thresholds
        self.cfg_ids = cfg.get("instrument_ids", {})
        self._rewards_hist: deque = deque()

    def raw(self, spec: str, *args):
        """Chiama un getter. Formato: "contratto:nome", con estensioni opzionali:
        "@altro_contratto" passa quell'indirizzo come argomento (es. "paper:balanceOf@staking"),
        "#n" prende l'n-esimo valore di una struct (es. "exchange:marketOi#1"),
        "(uint32)" forza il tipo degli argomenti (es. "exchange:marketOi(uint32)#1")."""
        idx = 0
        if "#" in spec:
            spec, i = spec.rsplit("#", 1)
            idx = int(i)
        if "@" in spec:
            spec, ref = spec.split("@", 1)
            args = (self.contracts[ref],) + tuple(args)
        cname, name = spec.split(":", 1) if ":" in spec else ("exchange", spec)
        forced = None
        if "(" in name:
            name, t = name[:-1].split("(", 1)
            forced = [x for x in t.split(",") if x]
        addr = self.contracts[cname]
        types = forced or ["address" if isinstance(x, str) else "uint256" for x in args]
        abi = [{"type": "function", "name": name, "stateMutability": "view",
                "inputs": [{"name": f"a{i}", "type": t} for i, t in enumerate(types)],
                "outputs": [{"name": f"o{i}", "type": "uint256"} for i in range(idx + 1)]}]
        c = self.w3.eth.contract(address=addr, abi=abi)
        out = getattr(c.functions, name)(*args).call()
        return out[idx] if isinstance(out, (list, tuple)) else out

    def _call(self, key: str, scale: float, with_me: bool = False) -> float:
        spec = self.fn.get(key)
        if not spec:
            return 0.0
        args = (self.me,) if with_me and self.me else ()
        return self.raw(spec, *args) / scale

    def state(self, now: float, paper_price=None) -> ProtocolState:
        treasury = self._call("lp_balance", self.ud)
        side = self._call("side_bucket", self.ud)
        q = self._call("queue_total", self.ud)
        tracked = self._call("tracked_lp", self.ud) if self.fn.get("tracked_lp") else treasury
        h = self._call("tail_progress", self.ud)
        return ProtocolState(
            # se l'LP tracciata è leggibile usala per il regime: "treasury" potrebbe includere i depositi utenti
            ts=now, lp_usd=(tracked if self.fn.get("tracked_lp") else treasury) + side, queue_usd=q, queue_len=int(self._call("queue_length", 1)),
            paper_supply=self._call("paper_supply", self.pd), paper_staked=self._call("paper_staked", self.pd),
            emission_per_usd=emission_rate(tracked, h, self.th),
            staker_rewards_24h_usd=0.0,  # TODO: dagli eventi di distribuzione quando noti
            paper_price=paper_price,
            my_paper=self._call("my_paper", self.pd, True), my_staked=self._call("my_staked", self.pd, True),
            my_pending_rewards=self._call("my_pending_rewards", self.ud, True),
            my_queued_usd=self._call("my_queued", self.ud, True),
            open_pnl_total=self._call("open_pnl_total", self.ud) if self.fn.get("open_pnl_total") else None,
            oi=self._oi())

    def _oi(self):
        if not (self.fn.get("oi_long") and self.fn.get("oi_short")):
            return None
        return {a: (self.raw(self.fn["oi_long"], iid) / self.ud, self.raw(self.fn["oi_short"], iid) / self.ud)
                for a, iid in self.cfg_ids.items()}

    def haircut_params(self):
        """Parametri esatti della curva, se le funzioni sono mappate in config."""
        keys = ["hc_base_rate", "hc_rate_multiplier", "hc_position_multiplier", "hc_reference_notional"]
        if not all(self.fn.get(k) for k in keys):
            return None
        return [self._call(k, 1) for k in keys]   # attenzione alle scale (1e18 ecc.)

    # Nel protocollo reale il regolamento lo fa il contratto
    def settle_loss(self, amount, owner="me"):
        return None

    def settle_win(self, gross, h, owner="me"):
        return None


class SimProtocol:
    simulated = True

    def __init__(self, clock, sim: dict, thresholds: dict):
        self.clock = clock
        self.cfg = sim
        self.th = thresholds
        self.rng = random.Random(sim.get("seed", 7) + 1)
        self.lp = 0.0
        self.queue: deque = deque()          # [owner, importo]
        self.supply = 0.0
        self.my_paper = 0.0
        self.my_staked = 0.0
        self.my_rewards = 0.0
        self.rewards: deque = deque()        # (ts, usd) per il calcolo 24h
        self.start = clock.now()
        self.last = self.start
        self.price = None
        self.crowd_h = 0.10                  # trattenuta media sulle vincite della folla
        self.tail = 0.0                      # H: high-water mark del guadagno LP cumulato oltre la soglia
        self.cum = 0.0

    # ---------- stato ----------
    @property
    def queue_total(self) -> float:
        return sum(x[1] for x in self.queue)

    def staked_total(self) -> float:
        return (self.supply - self.my_paper - self.my_staked) * self.cfg["crowd_stake_ratio"] + self.my_staked

    def _reward(self, usd: float) -> None:
        if usd <= 0:
            return
        self.rewards.append((self.clock.now(), usd))
        st = self.staked_total()
        if self.my_staked > 0 and st > 0:
            self.my_rewards += usd * self.my_staked / st

    def _pay_queue(self) -> None:
        while self.queue and self.lp >= self.queue[0][1]:
            owner, amt = self.queue.popleft()
            self.lp -= amt

    def _sweep(self) -> None:
        cap = self.th["sweep_usd"]
        if self.lp - self.queue_total > cap and not self.queue:
            excess = self.lp - cap
            self.lp = cap
            self._reward(excess)

    # ---------- regolamento ----------
    def settle_loss(self, amount: float, owner: str = "me", liquidation: bool = False) -> float:
        solvent = not self.queue
        fee = self.th["loss_fee_lp"] if solvent else 0.0       # 2% lato LP, solo se la coda è vuota
        basis = amount if liquidation else amount * (1 - fee)  # liquidazioni: mint sul margine pieno
        minted = basis * emission_rate(self.lp, self.tail, self.th)
        self.supply += minted
        if owner == "me":
            self.my_paper += minted
        credited = amount * (1 - fee)
        self._reward(amount * fee * self.cfg["staker_share_of_carve"])
        if self.lp >= self.th["emission_decay_usd"]:
            self.cum += credited
            self.tail = max(self.tail, self.cum)
        self.lp += credited
        self._pay_queue()
        self._sweep()
        return minted

    def settle_win(self, gross: float, h: float, owner: str = "me") -> tuple[float, float]:
        """Ritorna (pagato_subito, messo_in_coda). Il margine torna sempre: qui si regola solo il profitto."""
        net = gross * (1 - h)                                  # la trattenuta resta all'LP
        self._reward(gross * h * self.cfg.get("win_carve", 0.0) * self.cfg["staker_share_of_carve"])
        if self.lp >= self.th["emission_decay_usd"]:
            self.cum -= net
        if not self.queue and self.lp >= net:
            self.lp -= net
            return net, 0.0
        self.queue.append([owner, net])
        return 0.0, net

    def my_queued(self) -> float:
        return sum(x[1] for x in self.queue if x[0] == "me")

    # ---------- folla simulata ----------
    def step(self) -> None:
        now = self.clock.now()
        dt_h = (now - self.last) / 3600
        if dt_h <= 0:
            return
        c = self.cfg
        net = self.rng.gauss(c["crowd_net_to_lp_per_hour"] * dt_h, c["crowd_sd_per_hour"] * math.sqrt(dt_h))
        if net > 0:
            self.settle_loss(net, owner="crowd")
        else:
            self.settle_win(-net / (1 - self.crowd_h), self.crowd_h, owner="crowd")
        if now - self.start < c.get("rush_minutes", 0) * 60:          # corsa iniziale: liquidazioni volute
            self.settle_loss(c["rush_loss_per_hour"] * dt_h * self.rng.uniform(0.5, 1.5), owner="crowd")
        if self.rng.random() < c["whale_prob_per_hour"] * dt_h:
            self.settle_win(c["whale_win_usd"], 0.05, owner="whale")
        hours = (now - self.start) / 3600
        if hours >= c["paper_listing_after_hours"]:
            r24 = self._rewards_24h()
            st = max(self.staked_total(), 1.0)
            fair = max(r24 * 365 / st / c["market_required_yield"], 1e-6)
            self.price = fair if self.price is None else self.price * 0.9 + fair * 0.1 * self.rng.lognormvariate(0, 0.2)
        self.last = now

    def _rewards_24h(self) -> float:
        now = self.clock.now()
        while self.rewards and self.rewards[0][0] < now - 86400:
            self.rewards.popleft()
        return sum(x[1] for x in self.rewards)

    def stake_all(self) -> float:
        amt = self.my_paper
        self.my_staked += amt
        self.my_paper = 0.0
        return amt

    def claim(self) -> float:
        r, self.my_rewards = self.my_rewards, 0.0
        return r

    def state(self, now: float, paper_price=None) -> ProtocolState:
        self.step()
        q = self.queue_total
        return ProtocolState(
            ts=now, lp_usd=self.lp, queue_usd=q, queue_len=len(self.queue), paper_supply=self.supply,
            paper_staked=self.staked_total(),
            emission_per_usd=emission_rate(self.lp, self.tail, self.th),
            staker_rewards_24h_usd=self._rewards_24h(), paper_price=self.price,
            my_paper=self.my_paper, my_staked=self.my_staked, my_pending_rewards=self.my_rewards,
            my_queued_usd=self.my_queued())
