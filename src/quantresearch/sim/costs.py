"""Execution costs, with every number labelled by where it came from.

Each fill records a cost source:
  observed_tick          the provider's quote at that instant
  observed_ask_candle    bid and ask candles from the provider
  assumed                the configured spread, used only where no ask was observed

Observed means observed at the data provider. A live broker's spread will differ, so
`extra_spread_usd` lets a run add a broker adjustment or a stress on top.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace

OZ_PER_LOT = 100.0


class MissingCostData(ValueError):
    """No observed ask and no assumed spread: refuse to pretend the trade was free."""


@dataclass(frozen=True)
class CostModel:
    assumed_spread_usd: float | None = None
    extra_spread_usd: float = 0.0
    slippage_usd: float = 0.0
    commission_per_lot: float = 0.0
    swap_long_per_lot_day: float = 0.0
    swap_short_per_lot_day: float = 0.0
    delay_seconds: float = 0.0
    max_volume_participation: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def scenarios(base: CostModel) -> dict[str, CostModel]:
    """Standard cost cases. Every strategy report shows results under all of them."""
    return {
        "observed": base,
        "spread_x2_equiv": replace(base, extra_spread_usd=base.extra_spread_usd + 0.30, slippage_usd=base.slippage_usd + 0.05),
        "stress": replace(base, extra_spread_usd=base.extra_spread_usd + 0.60, slippage_usd=base.slippage_usd + 0.20,
                          commission_per_lot=base.commission_per_lot + 7.0),
    }
