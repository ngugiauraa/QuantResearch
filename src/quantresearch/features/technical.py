"""Technical features. Every value at row i uses rows 0..i only and is known at row i's close.

Windows count bars, not clock time, so they run across weekends and closures. Values are NaN
until the window is full. Prices are mid prices unless stated.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def log_return(close: pd.Series, n: int = 1) -> pd.Series:
    return np.log(close / close.shift(n))


def simple_return(close: pd.Series, n: int = 1) -> pd.Series:
    return close / close.shift(n) - 1.0


def momentum(close: pd.Series, n: int) -> pd.Series:
    return close / close.shift(n) - 1.0


def volatility(close: pd.Series, n: int) -> pd.Series:
    return log_return(close).rolling(n, min_periods=n).std()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    tr[prev.isna()] = np.nan
    return tr


def _wilder(values: pd.Series, n: int) -> pd.Series:
    """Wilder smoothing seeded by a simple mean of the first n valid values."""
    arr = values.to_numpy(dtype="float64")
    out = np.full(len(arr), np.nan)
    valid = np.flatnonzero(~np.isnan(arr))
    if len(valid) < n:
        return pd.Series(out, index=values.index)
    start = valid[n - 1]
    if np.isnan(arr[valid[0]:start + 1]).any():
        return values.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    current = arr[valid[0]:start + 1].mean()
    out[start] = current
    for i in range(start + 1, len(arr)):
        if not np.isnan(arr[i]):
            current += (arr[i] - current) / n
        out[i] = current
    return pd.Series(out, index=values.index)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return _wilder(true_range(high, low, close), n)


def sma(close: pd.Series, n: int) -> pd.Series:
    return close.rolling(n, min_periods=n).mean()


def ema(close: pd.Series, n: int) -> pd.Series:
    return close.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = _wilder(delta.clip(lower=0), n)
    down = _wilder((-delta).clip(lower=0), n)
    out = 100.0 - 100.0 / (1.0 + up / down)
    out[(down == 0) & (up > 0)] = 100.0
    out[(down == 0) & (up == 0)] = 50.0
    return out


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std()
    upper, lower = mid + k * sd, mid - k * sd
    width = upper - lower
    return pd.DataFrame({
        "bb_mid": mid, "bb_upper": upper, "bb_lower": lower,
        "bb_pct": (close - lower) / width.where(width > 0),
        "bb_width": width / mid,
    })


def zscore(close: pd.Series, n: int) -> pd.Series:
    sd = close.rolling(n, min_periods=n).std()
    return (close - sma(close, n)) / sd.where(sd > 0)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.DataFrame:
    up, down = high.diff(), -low.diff()
    plus = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    plus[up.isna()] = np.nan
    minus[up.isna()] = np.nan
    tr = _wilder(true_range(high, low, close), n)
    pdi = 100 * _wilder(plus, n) / tr.where(tr > 0)
    mdi = 100 * _wilder(minus, n) / tr.where(tr > 0)
    total = pdi + mdi
    dx = 100 * (pdi - mdi).abs() / total.where(total > 0)
    return pd.DataFrame({"plus_di": pdi, "minus_di": mdi, "adx": _wilder(dx, n)})


def donchian(high: pd.Series, low: pd.Series, n: int) -> pd.DataFrame:
    """Channel of the n bars BEFORE this one, so a breakout can be tested against it."""
    upper = high.rolling(n, min_periods=n).max().shift(1)
    lower = low.rolling(n, min_periods=n).min().shift(1)
    return pd.DataFrame({f"donchian_high_{n}": upper, f"donchian_low_{n}": lower})


def trend_slope(close: pd.Series, n: int) -> pd.Series:
    """Least-squares slope of log price per bar over the last n bars."""
    x = np.arange(n, dtype="float64")
    x = x - x.mean()
    denom = (x ** 2).sum()
    logs = np.log(close.to_numpy(dtype="float64"))
    out = np.full(len(logs), np.nan)
    if len(logs) >= n:
        windows = np.lib.stride_tricks.sliding_window_view(logs, n)
        out[n - 1:] = (windows - windows.mean(axis=1, keepdims=True)) @ x / denom
    return pd.Series(out, index=close.index)


def technical_features(df: pd.DataFrame) -> pd.DataFrame:
    h, l, c = df["high"], df["low"], df["close"]
    out = {
        "ret_1": simple_return(c, 1), "logret_1": log_return(c, 1), "logret_5": log_return(c, 5),
        "mom_20": momentum(c, 20), "mom_60": momentum(c, 60),
        "vol_20": volatility(c, 20), "vol_60": volatility(c, 60),
        "tr": true_range(h, l, c), "atr_14": atr(h, l, c, 14), "atr_50": atr(h, l, c, 50),
        "sma_20": sma(c, 20), "sma_50": sma(c, 50), "sma_200": sma(c, 200),
        "ema_20": ema(c, 20), "ema_50": ema(c, 50),
        "rsi_14": rsi(c, 14), "zscore_20": zscore(c, 20), "zscore_60": zscore(c, 60),
        "slope_20": trend_slope(c, 20),
    }
    frame = pd.DataFrame(out, index=df.index)
    frame["vol_ratio"] = frame["vol_20"] / frame["vol_60"]
    frame["atr_ratio"] = frame["atr_14"] / frame["atr_50"]
    frame = pd.concat([frame, bollinger(c), adx(h, l, c), donchian(h, l, 20), donchian(h, l, 55)], axis=1)
    frame["breakout_up_20"] = (c > frame["donchian_high_20"]).where(frame["donchian_high_20"].notna())
    frame["breakout_down_20"] = (c < frame["donchian_low_20"]).where(frame["donchian_low_20"].notna())
    return frame.astype("float64")
