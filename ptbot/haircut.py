"""Asymmetric impact on profits — official formula (docs, QueueLib.applyMarketImpact):

  scale = (1 − baseRate) / (1 + 1/(move·rateMultiplier) + referenceNotional/(move·positionMultiplier))

Both terms have the same shape in `move`, so they collapse into a single parameter:
  K = 1/rateMultiplier + referenceNotional/positionMultiplier
  scale = (1 − b) · move / (move + K)          haircut h = 1 − scale

Before the curve there is the anti-jitter deadband: profits from a move < 0.2 bps (entry/50000) are
zeroed, larger ones lose 0.2 bps of move. The haircut does NOT depend on position size.
If the contract exposes the parameters they are used exactly; otherwise b and K are fitted from trades.
"""
from __future__ import annotations

import math

GRID = [0.001, 0.002, 0.003, 0.004, 0.006, 0.008, 0.010, 0.015, 0.020, 0.030, 0.050, 0.070, 0.100]
DEADBAND = 1 / 50000


def scale_for(move: float, b: float, k: float) -> float:
    m = move - DEADBAND
    if m <= 0:
        return 0.0
    return (1 - b) * m / (m + k)


class HaircutModel:
    def __init__(self, base_rate: float, k: float, min_samples: int = 3):
        self.b, self.k = base_rate, k
        self.default = (base_rate, k)
        self.min_samples = min_samples
        self.obs: list[tuple[float, float]] = []
        self.exact = False           # True if the parameters come from the contract

    def set_exact(self, base_rate: float, rate_mult: float, pos_mult: float, ref_notional: float) -> None:
        self.b = base_rate
        self.k = 1 / rate_mult + ref_notional / pos_mult
        self.exact = True

    def add(self, move: float, h: float) -> None:
        if move > DEADBAND and 0 <= h < 1:
            self.obs.append((move, h))
            if not self.exact:
                self._fit()

    def _fit(self) -> None:
        """Grid least squares over (b, K): 2-3 observations at different distances are enough."""
        if len(self.obs) < self.min_samples:
            return
        best = None
        for bi in range(0, 31):
            b = bi / 100
            for ki in range(0, 81):
                k = 10 ** (-5 + ki * 0.04375)         # from 1e-5 to 3e-2
                err = sum((scale_for(m, b, k) - (1 - h)) ** 2 for m, h in self.obs)
                if best is None or err < best[0]:
                    best = (err, b, k)
        _, self.b, self.k = best

    @property
    def calibrated(self) -> bool:
        return self.exact or len(self.obs) >= self.min_samples

    def estimate(self, move: float) -> float:
        return 1 - scale_for(max(move, 0.0), self.b, self.k)

    def choose_distance(self, target_max: float) -> float:
        """Smallest move with haircut ≤ target: becomes the TP/SL distance."""
        for d in GRID:
            if self.estimate(d) <= target_max:
                return d
        return GRID[-1]

    def describe(self) -> str:
        src = "contract parameters" if self.exact else ("fitted from trades" if self.calibrated else "prudent assumption")
        return f"{src} (b={self.b:.3f}, K={self.k:.5f}, {len(self.obs)} obs.)"
