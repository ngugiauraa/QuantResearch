"""Shared pieces for catalogue strategies.

Every catalogue strategy is a StrategyDef with FIXED parameters: an empty grid and no `fit`, so nothing is tuned in
the walk-forward and nothing can be tuned after the holdout is seen. The parameters are exactly the ones written in
the strategy's documented rules. `CatalogueEntry` carries that documentation (source, logic, exact rules, data).

All signals are computed from each bar's close and earlier bars. A signal acts at its bar's close and the order fills
on a later event (the shared engine's rule), so a "breakout above X" here is a close above X, not a stop order at X.
Stops are distances in price from the entry close; every strategy has one because positions are risk-sized from it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quantresearch.strategies.base import Hypothesis, StrategyDef, empty_signals

LONDON = "Europe/London"
NEW_YORK = "America/New_York"


@dataclass
class CatalogueEntry:
    defn: StrategyDef
    family: str
    source: str  # A link, a citation, or "own idea".
    logic: str  # One or two plain sentences.
    entry: str
    exit: str
    stop: str
    data: list[str]  # What it reads, in plain words.
    batch: int
    missing_data: list[str] = field(default_factory=list)  # Data we do not have; the strategy cannot trade without it.
    macro: list[str] = field(default_factory=list)  # Event series attached as m_<SERIES> (FRED, COT), as published by each bar's close.

    @property
    def name(self) -> str:
        return self.defn.name

    def as_dict(self) -> dict:
        d = self.defn
        return {"name": d.name, "title": d.title, "family": self.family, "batch": self.batch, "timeframe": d.signal_tf, "source": self.source,
                "logic": self.logic, "entry": self.entry, "exit": self.exit, "stop": self.stop, "data": self.data,
                "missing_data": self.missing_data, "params": d.defaults, "needs": d.needs, "macro": self.macro}


def auto_hypotheses(prepare, horizon_key: str = "max_hold", default_horizon: int = 24):
    """Two hypotheses per strategy, from its own entries: longs are followed by a rise, shorts by a fall."""
    def build(df: pd.DataFrame, p: dict) -> list[Hypothesis]:
        s = prepare(df, p)
        horizon = int(p.get(horizon_key) or default_horizon)
        out = []
        if s["long_entry"].any():
            out.append(Hypothesis("long", "After a long entry signal the price rises over the hold", s["long_entry"].astype(bool), 1, horizon))
        if s["short_entry"].any():
            out.append(Hypothesis("short", "After a short entry signal the price falls over the hold", s["short_entry"].astype(bool), -1, horizon))
        return out
    return build


def entry(name: str, title: str, tf: str, params: dict, prepare, methodology: str, *, family: str, source: str, logic: str,
          entry_rule: str, exit_rule: str, stop_rule: str, data: list[str], batch: int, needs: list[str] | None = None,
          missing: list[str] | None = None, limitations: list[str] | None = None, macro: list[str] | None = None) -> CatalogueEntry:
    lim = list(limitations or [])
    lim.append("Catalogue strategy: parameters are fixed as written and were not tuned. One of many tested; read the project-wide "
               "deflated Sharpe, not the raw result.")
    if missing:
        lim.append("Needs data the project does not have: " + "; ".join(missing) + ". Without it the strategy cannot trade.")
    defn = StrategyDef(name, title, tf, dict(params), {}, prepare, auto_hypotheses(prepare), methodology, lim, needs=list(needs or []))
    return CatalogueEntry(defn, family, source, logic, entry_rule, exit_rule, stop_rule, data, batch, list(missing or []), list(macro or []))


def signals(df: pd.DataFrame, long=None, short=None, stop=None, target=None, hold=None, exit_long=None, exit_short=None) -> pd.DataFrame:
    """Fill the standard signal columns. Masks are coerced to bool with NaN as False."""
    s = empty_signals(df)
    def mask(x):
        return pd.Series(False, index=df.index) if x is None else pd.Series(x, index=df.index).fillna(False).astype(bool)
    s["long_entry"], s["short_entry"] = mask(long), mask(short)
    s["exit_long"], s["exit_short"] = mask(exit_long), mask(exit_short)
    if stop is not None:
        s["stop_dist"] = pd.Series(stop, index=df.index).astype("float64").where(lambda v: v > 0)
    if target is not None:
        s["target_dist"] = pd.Series(target, index=df.index).astype("float64")
    if hold is not None:
        s["max_hold"] = pd.Series(hold, index=df.index).astype("float64") if not np.isscalar(hold) else float(hold)
    return s


def cross_up(a: pd.Series, b) -> pd.Series:
    b = b if isinstance(b, pd.Series) else pd.Series(b, index=a.index)
    return (a > b) & (a.shift(1) <= b.shift(1))


def cross_down(a: pd.Series, b) -> pd.Series:
    b = b if isinstance(b, pd.Series) else pd.Series(b, index=a.index)
    return (a < b) & (a.shift(1) >= b.shift(1))


def rsi(close: pd.Series, n: int) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / down.where(down > 0))


def sma(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).mean()


def ema(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(span=n, adjust=False, min_periods=n).mean()


def prior_high(df: pd.DataFrame, n: int) -> pd.Series:
    """Highest high of the n bars BEFORE this one."""
    return df["high"].shift(1).rolling(n, min_periods=n).max()


def prior_low(df: pd.DataFrame, n: int) -> pd.Series:
    return df["low"].shift(1).rolling(n, min_periods=n).min()


def local_clock(ts: pd.Series, zone: str) -> pd.DataFrame:
    """Local date, minute of day and weekday (Mon=0) in a market's time zone, daylight saving included."""
    local = ts.dt.tz_convert(zone)
    return pd.DataFrame({"date": local.dt.date, "minute": local.dt.hour * 60 + local.dt.minute, "weekday": local.dt.weekday,
                         "month": local.dt.month, "day": local.dt.day}, index=ts.index)


def window_range(df: pd.DataFrame, in_window: pd.Series, day: pd.Series) -> pd.DataFrame:
    """Running high and low of bars inside a daily window, carried forward through the rest of that day.

    The value on a bar uses that bar and earlier bars of the same day only.
    """
    hi = df["high"].where(in_window).groupby(day).cummax()
    lo = df["low"].where(in_window).groupby(day).cummin()
    return pd.DataFrame({"high": hi.groupby(day).ffill(), "low": lo.groupby(day).ffill()}, index=df.index)


def first_per_day(mask: pd.Series, day: pd.Series) -> pd.Series:
    mask = mask.fillna(False).astype(bool)
    return mask & (mask.astype(int).groupby(day).cumsum() == 1)


def bars_per_hour(tf: str) -> int:
    return {"m5": 12, "m15": 4, "m30": 2, "h1": 1}.get(tf, 1)
