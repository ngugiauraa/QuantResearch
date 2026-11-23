"""Market regime label. A state used to filter other strategies, not a trading signal.

Every value at row i uses rows 0..i only and is known at row i's close, so the label is causal.

Codes (float column, because the warm-up is NaN):
    NaN  warm-up, not enough history to classify
    0    UNCLASSIFIED: none of the four definitions holds (for example ADX between 20 and 25)
    1    TRENDING_UP    adx > 25 and ema_20 > ema_50 and slope_20 > 0
    2    TRENDING_DOWN  adx > 25 and ema_20 < ema_50 and slope_20 < 0
    3    RANGING        adx < 20 and bb_width below its trailing 200-bar 20th percentile
    4    VOLATILE       vol_ratio > 1.5 (short-term volatility expanding fast)

The definitions can overlap (a fast volatility expansion can also have ADX above 25). VOLATILE wins
any overlap because it is the state the other strategies most need to step aside for. The trend and
range definitions cannot overlap each other, since they need ADX above 25 and below 20.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

UNCLASSIFIED, TRENDING_UP, TRENDING_DOWN, RANGING, VOLATILE = 0, 1, 2, 3, 4
NAMES = {UNCLASSIFIED: "unclassified", TRENDING_UP: "trending_up", TRENDING_DOWN: "trending_down",
         RANGING: "ranging", VOLATILE: "volatile"}

ADX_TREND, ADX_RANGE = 25.0, 20.0
VOL_RATIO_MAX = 1.5
BB_WINDOW, BB_QUANTILE = 200, 0.20
WARMUP = 200


def classify(adx: pd.Series, ema_20: pd.Series, ema_50: pd.Series, slope_20: pd.Series,
             bb_width: pd.Series, vol_ratio: pd.Series) -> pd.Series:
    """The regime code for each bar, from trailing inputs only."""
    width_floor = bb_width.rolling(BB_WINDOW, min_periods=BB_WINDOW).quantile(BB_QUANTILE)
    up = (adx > ADX_TREND) & (ema_20 > ema_50) & (slope_20 > 0)
    down = (adx > ADX_TREND) & (ema_20 < ema_50) & (slope_20 < 0)
    ranging = (adx < ADX_RANGE) & (bb_width < width_floor)
    volatile = vol_ratio > VOL_RATIO_MAX
    code = np.select([volatile, up, down, ranging], [VOLATILE, TRENDING_UP, TRENDING_DOWN, RANGING], default=UNCLASSIFIED)
    out = pd.Series(code.astype("float64"), index=adx.index)
    # A row is only classified once every input and the 200-bar width history exist.
    ready = pd.concat([adx, ema_20, ema_50, slope_20, bb_width, vol_ratio, width_floor], axis=1).notna().all(axis=1)
    return out.where(ready)


def regime_features(technical: pd.DataFrame) -> pd.DataFrame:
    """The `regime` column, from the technical feature frame."""
    return pd.DataFrame({"regime": classify(
        technical["adx"], technical["ema_20"], technical["ema_50"], technical["slope_20"],
        technical["bb_width"], technical["vol_ratio"],
    )}, index=technical.index)
