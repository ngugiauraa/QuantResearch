"""Market-structure and SMC/ICT features as explicit, testable rules.

These concepts have no single standard definition, so each rule here is a project definition,
not a claim about how anyone else draws them.

Swings need `right` later bars to confirm. A swing at bar i therefore first appears at bar
i + right. This delay is what stops the swing logic from looking ahead.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantresearch.core import timeframes as tfs


def swings(high: np.ndarray, low: np.ndarray, left: int = 3, right: int = 3):
    """Confirmed swings as (confirm_index, swing_index, price) lists for highs and lows."""
    highs, lows = [], []
    for i in range(left, len(high) - right):
        window_h = high[i - left: i + right + 1]
        window_l = low[i - left: i + right + 1]
        if high[i] == window_h.max() and (window_h == high[i]).sum() == 1:
            highs.append((i + right, i, float(high[i])))
        if low[i] == window_l.min() and (window_l == low[i]).sum() == 1:
            lows.append((i + right, i, float(low[i])))
    return highs, lows


def market_structure(df: pd.DataFrame, left: int = 3, right: int = 3, equal_atr: float = 0.1,
                     atr: pd.Series | None = None) -> pd.DataFrame:
    """Swings, break of structure, change of character, sweeps, equal levels, premium/discount.

    Rules, evaluated bar by bar on what has been confirmed so far:
      BOS    close beyond the last swing in the direction of the current trend, or the first break
      CHoCH  close beyond the last swing against the current trend, which flips the trend
      Sweep  wick beyond the last swing with the close back inside
      Equal  the last two swing highs (or lows) within equal_atr x ATR of each other
      Premium/discount  position of the close in the last swing range: 0 is the low, 1 the high
    """
    h, l, c = (df[x].to_numpy(dtype="float64") for x in ("high", "low", "close"))
    n = len(df)
    sw_h, sw_l = swings(h, l, left, right)
    by_confirm_h = {conf: px for conf, _, px in sw_h}
    by_confirm_l = {conf: px for conf, _, px in sw_l}
    atr_values = atr.to_numpy(dtype="float64") if atr is not None else np.full(n, np.nan)
    columns = ["swing_high", "swing_low", "bos_up", "bos_down", "choch_up", "choch_down",
               "sweep_high", "sweep_low", "equal_highs", "equal_lows", "premium_discount", "structure_trend"]
    out = {name: np.full(n, np.nan) for name in columns}
    last_high = last_low = prev_high = prev_low = np.nan
    trend = 0
    broken_high = broken_low = False
    for i in range(n):
        if i in by_confirm_h:
            prev_high, last_high = last_high, by_confirm_h[i]
            broken_high = False
        if i in by_confirm_l:
            prev_low, last_low = last_low, by_confirm_l[i]
            broken_low = False
        out["swing_high"][i], out["swing_low"][i] = last_high, last_low
        for name in ("bos_up", "bos_down", "choch_up", "choch_down", "sweep_high", "sweep_low"):
            out[name][i] = 0.0
        if not np.isnan(last_high):
            if c[i] > last_high and not broken_high:
                out["choch_up" if trend < 0 else "bos_up"][i] = 1.0
                trend = 1
                broken_high = True
            elif h[i] > last_high and c[i] <= last_high and not broken_high:
                out["sweep_high"][i] = 1.0
        if not np.isnan(last_low):
            if c[i] < last_low and not broken_low:
                out["choch_down" if trend > 0 else "bos_down"][i] = 1.0
                trend = -1
                broken_low = True
            elif l[i] < last_low and c[i] >= last_low and not broken_low:
                out["sweep_low"][i] = 1.0
        out["structure_trend"][i] = trend
        tol = equal_atr * atr_values[i]
        out["equal_highs"][i] = float(abs(last_high - prev_high) <= tol) if not np.isnan(tol + last_high + prev_high) else np.nan
        out["equal_lows"][i] = float(abs(last_low - prev_low) <= tol) if not np.isnan(tol + last_low + prev_low) else np.nan
        span = last_high - last_low
        if not np.isnan(span) and span > 0:
            out["premium_discount"][i] = (c[i] - last_low) / span
    return pd.DataFrame(out, index=df.index)


def fair_value_gaps(df: pd.DataFrame, max_active: int = 20) -> pd.DataFrame:
    """Fair value gaps and inverse fair value gaps.

    Bullish FVG at bar i: low[i] > high[i-2]. Zone is (high[i-2], low[i]). Bearish is the mirror.
    It is known at bar i's close. A bar that closes through the far side invalidates the FVG
    and turns it into an inverse FVG of the opposite direction. Reports the nearest active
    zones of each kind, by distance from the close.
    """
    h, l, c = (df[x].to_numpy(dtype="float64") for x in ("high", "low", "close"))
    n = len(df)
    names = ["fvg_up", "fvg_down", "fvg_up_low", "fvg_up_high", "fvg_down_low", "fvg_down_high",
             "ifvg_up", "ifvg_down", "fvg_up_count", "fvg_down_count"]
    out = {name: np.full(n, np.nan) for name in names}
    bull: list[tuple[float, float]] = []
    bear: list[tuple[float, float]] = []
    for i in range(n):
        new_up = new_down = 0.0
        inv_up = inv_down = 0.0
        keep_bull = []
        for low_edge, high_edge in bull:
            if c[i] < low_edge:
                inv_down = 1.0  # A bullish gap closed through becomes bearish resistance.
            else:
                keep_bull.append((low_edge, high_edge))
        bull = keep_bull
        keep_bear = []
        for low_edge, high_edge in bear:
            if c[i] > high_edge:
                inv_up = 1.0
            else:
                keep_bear.append((low_edge, high_edge))
        bear = keep_bear
        if i >= 2:
            if l[i] > h[i - 2]:
                bull.append((h[i - 2], l[i]))
                new_up = 1.0
            if h[i] < l[i - 2]:
                bear.append((h[i], l[i - 2]))
                new_down = 1.0
        bull, bear = bull[-max_active:], bear[-max_active:]
        out["fvg_up"][i], out["fvg_down"][i] = new_up, new_down
        out["ifvg_up"][i], out["ifvg_down"][i] = inv_up, inv_down
        out["fvg_up_count"][i], out["fvg_down_count"][i] = len(bull), len(bear)
        if bull:
            nearest = min(bull, key=lambda z: abs(c[i] - z[1]))
            out["fvg_up_low"][i], out["fvg_up_high"][i] = nearest
        if bear:
            nearest = min(bear, key=lambda z: abs(c[i] - z[0]))
            out["fvg_down_low"][i], out["fvg_down_high"][i] = nearest
    return pd.DataFrame(out, index=df.index)


def session_levels(df: pd.DataFrame) -> pd.DataFrame:
    """Running high and low of the current New York trading day, and the previous day's.

    Previous-day levels appear only after that day has ended. Today's are running values that
    include the current bar, which is known at its close.
    """
    day = tfs.bucket(df["ts"], "d1ny")
    today_high = df["high"].groupby(day).cummax()
    today_low = df["low"].groupby(day).cummin()
    daily = pd.DataFrame({"h": df["high"].groupby(day).max(), "l": df["low"].groupby(day).min(),
                          "c": df["close"].groupby(day).last()})
    prev = daily.shift(1)
    return pd.DataFrame({
        "day_high": today_high, "day_low": today_low,
        "pdh": day.map(prev["h"]).astype("float64"), "pdl": day.map(prev["l"]).astype("float64"),
        "pdc": day.map(prev["c"]).astype("float64"),
    }, index=df.index)


def structure_features(df: pd.DataFrame, atr: pd.Series | None = None) -> pd.DataFrame:
    return pd.concat([market_structure(df, atr=atr), fair_value_gaps(df), session_levels(df)], axis=1).astype("float64")
