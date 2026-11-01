"""Microstructure proxies from observed quotes.

These describe one provider's feed. The volumes are its activity units, not market-wide trade
volume, and its spreads will differ from any particular broker's.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def microstructure_features(df: pd.DataFrame, window: int = 60) -> pd.DataFrame:
    spread = df["ask_close"] - df["bid_close"]
    mid = (df["ask_close"] + df["bid_close"]) / 2
    volume = df["bid_volume"].fillna(0) + df["ask_volume"].fillna(0)
    total = volume.where(volume > 0)
    ret = np.log(df["close"] / df["close"].shift(1))
    spread_mean = spread.rolling(window, min_periods=window).mean()
    spread_sd = spread.rolling(window, min_periods=window).std()
    abs_ret = ret.abs()
    out = pd.DataFrame({
        "spread": spread,
        "spread_bps": spread / mid * 1e4,
        "spread_change": spread.diff(),
        "spread_z": (spread - spread_mean) / spread_sd.where(spread_sd > 0),
        "activity": df["m1_count"].astype("float64") if "m1_count" in df else volume,
        "volume_ratio": volume / volume.rolling(window, min_periods=window).mean(),
        "quote_imbalance": (df["ask_volume"] - df["bid_volume"]) / total,
        "amihud": abs_ret / total,
        "liquidity": total / spread.where(spread > 0),
        "vol_cluster": abs_ret.rolling(window, min_periods=window).corr(abs_ret.shift(1)),
    }, index=df.index)
    return out.astype("float64")


def tick_features(ticks: pd.DataFrame, window: int = 100) -> pd.DataFrame:
    """Per-quote features. `tick_rule` is +1 on an up-move in mid, -1 on a down-move, else carried."""
    mid = (ticks["ask"] + ticks["bid"]) / 2
    sign = np.sign(mid.diff()).replace(0, np.nan).ffill().fillna(0)
    seconds = ticks["ts"].diff().dt.total_seconds()
    return pd.DataFrame({
        "mid": mid,
        "spread": ticks["ask"] - ticks["bid"],
        "tick_rule": sign,
        "tick_imbalance": sign.rolling(window, min_periods=window).mean(),
        "tick_rate": window / seconds.rolling(window, min_periods=window).sum().where(lambda s: s > 0),
    }, index=ticks.index)
