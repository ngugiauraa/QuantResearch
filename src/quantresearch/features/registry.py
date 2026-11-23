"""The feature set, with a definition and warm-up for every column.

Timestamp convention for all: a value on a bar is known at that bar's close_ts, never earlier.
`build_features` is deterministic and uses each bar's past only. The look-ahead test proves it.
"""
from __future__ import annotations

import pandas as pd

from quantresearch.features.microstructure import microstructure_features
from quantresearch.features.regime import regime_features
from quantresearch.features.structure import structure_features
from quantresearch.features.technical import technical_features

FEATURE_VERSION = "v3"

DEFINITIONS: dict[str, tuple[str, int]] = {
    "ret_1": ("close / previous close - 1", 1),
    "logret_1": ("ln(close / previous close)", 1),
    "logret_5": ("ln(close / close 5 bars ago)", 5),
    "mom_20": ("close / close 20 bars ago - 1", 20),
    "mom_60": ("close / close 60 bars ago - 1", 60),
    "vol_20": ("sample std of logret_1, 20 bars", 20),
    "vol_60": ("sample std of logret_1, 60 bars", 60),
    "vol_ratio": ("vol_20 / vol_60; above 1 means expansion", 60),
    "tr": ("max(high-low, |high-prev close|, |low-prev close|)", 1),
    "atr_14": ("Wilder average of tr, 14", 14),
    "atr_50": ("Wilder average of tr, 50", 50),
    "atr_ratio": ("atr_14 / atr_50", 50),
    "sma_20": ("mean close, 20", 19), "sma_50": ("mean close, 50", 49), "sma_200": ("mean close, 200", 199),
    "ema_20": ("EMA span 20", 19), "ema_50": ("EMA span 50", 49),
    "rsi_14": ("Wilder RSI, 14", 14),
    "zscore_20": ("(close - sma_20) / std 20", 19), "zscore_60": ("(close - mean 60) / std 60", 59),
    "slope_20": ("least-squares slope of ln close per bar, 20", 19),
    "bb_mid": ("sma_20", 19), "bb_upper": ("mid + 2 std", 19), "bb_lower": ("mid - 2 std", 19),
    "bb_pct": ("(close - lower) / (upper - lower)", 19), "bb_width": ("(upper - lower) / mid", 19),
    "plus_di": ("Wilder +DI, 14", 14), "minus_di": ("Wilder -DI, 14", 14), "adx": ("Wilder ADX, 14", 28),
    "donchian_high_20": ("highest high of the PRIOR 20 bars", 20), "donchian_low_20": ("lowest low of the PRIOR 20 bars", 20),
    "donchian_high_55": ("highest high of the PRIOR 55 bars", 55), "donchian_low_55": ("lowest low of the PRIOR 55 bars", 55),
    "breakout_up_20": ("close > donchian_high_20", 20), "breakout_down_20": ("close < donchian_low_20", 20),
    "swing_high": ("last confirmed fractal high, 3 left and 3 right; appears 3 bars after the swing", 6),
    "swing_low": ("last confirmed fractal low, 3 left and 3 right; appears 3 bars after the swing", 6),
    "bos_up": ("close breaks above last swing high, trend not down", 6),
    "bos_down": ("close breaks below last swing low, trend not up", 6),
    "choch_up": ("close breaks above last swing high while trend is down", 6),
    "choch_down": ("close breaks below last swing low while trend is up", 6),
    "sweep_high": ("high beyond last swing high, close back at or below it", 6),
    "sweep_low": ("low beyond last swing low, close back at or above it", 6),
    "equal_highs": ("last two swing highs within 0.1 x atr_14", 20),
    "equal_lows": ("last two swing lows within 0.1 x atr_14", 20),
    "premium_discount": ("position of close in last swing range; above 0.5 is premium", 6),
    "structure_trend": ("+1 or -1 from the latest structure break, 0 before any", 6),
    "fvg_up": ("low > high two bars ago", 2), "fvg_down": ("high < low two bars ago", 2),
    "fvg_up_low": ("lower edge of nearest active bullish FVG", 2), "fvg_up_high": ("upper edge of nearest active bullish FVG", 2),
    "fvg_down_low": ("lower edge of nearest active bearish FVG", 2), "fvg_down_high": ("upper edge of nearest active bearish FVG", 2),
    "ifvg_up": ("a bearish FVG was closed through, becoming an inverse bullish FVG", 2),
    "ifvg_down": ("a bullish FVG was closed through, becoming an inverse bearish FVG", 2),
    "fvg_up_count": ("active bullish FVGs", 0), "fvg_down_count": ("active bearish FVGs", 0),
    "day_high": ("running high of this New York trading day", 0), "day_low": ("running low of this New York trading day", 0),
    "pdh": ("previous New York day's high", 0), "pdl": ("previous New York day's low", 0), "pdc": ("previous New York day's close", 0),
    "spread": ("ask_close - bid_close, observed", 0), "spread_bps": ("spread / mid in basis points", 0),
    "spread_change": ("spread minus previous spread", 1), "spread_z": ("z-score of spread, 60 bars", 59),
    "activity": ("M1 bars with quotes inside this bar", 0), "volume_ratio": ("volume / mean volume, 60 bars", 59),
    "quote_imbalance": ("(ask volume - bid volume) / total", 0), "amihud": ("|logret_1| / volume, a price-impact proxy", 1),
    "liquidity": ("volume / spread", 0), "vol_cluster": ("autocorrelation of |logret_1|, 60 bars", 61),
    "regime": ("0 unclassified, 1 trending up, 2 trending down, 3 ranging, 4 volatile; trailing data only. "
               "See features/regime.py", 218),
}


def build_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Bars plus all features. Input must be sorted by time with a default index."""
    if not bars["ts"].is_monotonic_increasing:
        raise ValueError("Bars must be sorted by time")
    technical = technical_features(bars)
    structure = structure_features(bars, atr=technical["atr_14"])
    micro = microstructure_features(bars)
    regime = regime_features(technical)
    return pd.concat([bars, technical, structure, micro, regime], axis=1)


def feature_columns() -> list[str]:
    return list(DEFINITIONS)
