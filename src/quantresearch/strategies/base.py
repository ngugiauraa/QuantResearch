"""Common strategy machinery.

A strategy is a causal `prepare` function that turns feature bars into entry and exit columns, plus
the research record that goes with it. Execution, stops, sizing and costs are shared, so the seven
engines differ only in their logic. The columns are computed before the run for speed. The look-ahead
integrity test re-computes them on truncated data, which proves they do not read the future.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from quantresearch.core import timeframes as tfs
from quantresearch.sim.costs import OZ_PER_LOT
from quantresearch.sim.engine import Context, OrderIntent, Strategy
from quantresearch.warehouse import calendar as cal

SIGNAL_COLUMNS = ["long_entry", "short_entry", "stop_dist", "target_dist", "max_hold", "exit_long", "exit_short"]


@dataclass
class Hypothesis:
    id: str
    statement: str
    mask: pd.Series  # When the condition holds.
    side: int  # Expected direction of the forward return: +1 or -1.
    horizon: int  # Bars.


@dataclass
class StrategyDef:
    name: str
    title: str
    signal_tf: str
    defaults: dict
    grid: dict
    prepare: Callable[[pd.DataFrame, dict], pd.DataFrame]
    hypotheses: Callable[[pd.DataFrame, dict], list[Hypothesis]]
    methodology: str
    limitations: list[str]
    needs: list[str] = field(default_factory=list)
    fit: Callable[[pd.DataFrame, dict], dict] | None = None

    def params(self, **overrides) -> dict:
        return {**self.defaults, **overrides}


def empty_signals(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "long_entry": False, "short_entry": False, "stop_dist": np.nan, "target_dist": np.nan,
        "max_hold": np.nan, "exit_long": False, "exit_short": False,
    }, index=df.index)


class SignalStrategy(Strategy):
    """Runs prepared columns: one position at a time, risk-sized from the stop distance.

    A signal is acted on at its bar's close and the order fills on a later event.
    """

    def __init__(self, signals: pd.DataFrame, name: str, risk_pct: float = 0.5, max_lots: float = 50.0,
                 flip: bool = True):
        self.name = name
        self.risk_pct, self.max_lots, self.flip = risk_pct, max_lots, flip
        self.long = signals["long_entry"].to_numpy(dtype=bool)
        self.short = signals["short_entry"].to_numpy(dtype=bool)
        self.stop = signals["stop_dist"].to_numpy(dtype="float64")
        self.target = signals["target_dist"].to_numpy(dtype="float64")
        self.hold = signals["max_hold"].to_numpy(dtype="float64")
        self.exit_long = signals["exit_long"].to_numpy(dtype=bool)
        self.exit_short = signals["exit_short"].to_numpy(dtype=bool)
        self.close_px = signals["close"].to_numpy(dtype="float64") if "close" in signals else None
        self.entry_index: dict[int, int] = {}

    def _lots(self, ctx: Context, distance: float) -> float:
        risk = ctx.account.equity * self.risk_pct / 100.0
        return float(min(self.max_lots, risk / (distance * OZ_PER_LOT)))

    def on_bar(self, ctx: Context) -> None:
        i = ctx.index
        positions = ctx.positions
        wants = 1 if self.long[i] else (-1 if self.short[i] else 0)
        for pos in positions:
            started = self.entry_index.setdefault(pos.id, i)
            timed_out = not np.isnan(self.hold[i]) and i - started >= self.hold[i]
            signal_exit = self.exit_long[i] if pos.side > 0 else self.exit_short[i]
            flipped = self.flip and wants == -pos.side
            if timed_out or signal_exit or flipped:
                ctx.close(pos, "time" if timed_out else ("flip" if flipped else "signal_exit"))
                positions = [p for p in positions if p is not pos]
        if wants == 0 or positions or ctx.orders:
            return
        distance = self.stop[i]
        if np.isnan(distance) or distance <= 0:
            return
        lots = self._lots(ctx, distance)
        price = self.close_px[i]
        target = self.target[i]
        ctx.submit(OrderIntent(
            wants, lots, stop=price - wants * distance,
            target=None if np.isnan(target) else price + wants * target, tag=self.name,
        ))


def attach_signals(features: pd.DataFrame, signals: pd.DataFrame) -> pd.DataFrame:
    extra = [c for c in signals.columns if c not in features.columns]
    return pd.concat([features, signals[extra]], axis=1)


# ---------------------------------------------------------------- shared causal context
def rolling_rank(series: pd.Series, window: int = 500) -> pd.Series:
    """Percentile of the latest value within its trailing window."""
    return series.rolling(window, min_periods=max(50, window // 5)).rank(pct=True)


def daily_trend(df: pd.DataFrame, n: int = 50) -> pd.Series:
    """+1 or -1: last COMPLETED New York day's close against its n-day average."""
    day = tfs.bucket(df["ts"], "d1ny")
    closes = df["close"].groupby(day).last()
    trend = np.sign(closes - closes.rolling(n, min_periods=n).mean()).shift(1)
    return day.map(trend).astype("float64")


def trading_day_position(ts: pd.Series) -> pd.DataFrame:
    """Trading-day number in the month from the start and from the end.

    The calendar is knowable in advance, so using the month's end here is not look-ahead. It comes
    from weekday and holiday rules, never from bars that have not happened.
    """
    dates = ts.dt.date
    unique = sorted(set(dates))
    if not unique:
        return pd.DataFrame({"tdom": [], "tdoe": []}, dtype="float64")
    first, last = unique[0].replace(day=1), unique[-1]
    from datetime import date as _date, timedelta as _td
    horizon = _date(last.year + (last.month == 12), 1 if last.month == 12 else last.month + 1, 28) + _td(days=8)
    days = cal.trading_weekdays(first, horizon)
    table = pd.DataFrame({"d": days})
    table["ym"] = [d.year * 100 + d.month for d in days]
    table["tdom"] = table.groupby("ym").cumcount() + 1
    table["tdoe"] = table.groupby("ym").cumcount(ascending=False) + 1
    tdom, tdoe = dict(zip(table["d"], table["tdom"])), dict(zip(table["d"], table["tdoe"]))
    # Weekend and holiday bars keep the most recent trading day's position, so a Sunday-evening
    # session stays inside a turn-of-month window instead of dropping out and back in.
    ordered = pd.Index(table["d"])
    def position(day, values):
        where = ordered.searchsorted(day, side="right") - 1
        return float(values[ordered[where]]) if where >= 0 else np.nan
    return pd.DataFrame({
        "tdom": dates.map(lambda d: position(d, tdom)).astype("float64"),
        "tdoe": dates.map(lambda d: position(d, tdoe)).astype("float64"),
    }, index=ts.index)


def forward_return(close: pd.Series, horizon: int) -> pd.Series:
    """Research-only label. Used for hypothesis tests, never as a signal input."""
    return np.log(close.shift(-horizon) / close)
