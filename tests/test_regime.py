import numpy as np
import pandas as pd
import pytest

from quantresearch.features import regime as rg
from quantresearch.features.registry import DEFINITIONS, build_features
from quantresearch.warehouse.store import synthetic_bars

N = 300


def inputs(**over):
    """Neutral inputs: ADX in the dead zone (20-25), flat averages, calm volatility, constant band width."""
    base = dict(adx=25.0, ema_20=100.0, ema_50=100.0, slope_20=0.0, bb_width=0.02, vol_ratio=1.0)
    base.update(over)
    return {k: pd.Series(np.full(N, v, dtype="float64")) for k, v in base.items()}


def last(**over) -> float:
    return rg.classify(**inputs(**over)).iloc[-1]


def test_trending_up_and_down():
    assert last(adx=30.0, ema_20=101.0, ema_50=100.0, slope_20=0.001) == rg.TRENDING_UP
    assert last(adx=30.0, ema_20=99.0, ema_50=100.0, slope_20=-0.001) == rg.TRENDING_DOWN


def test_a_trend_needs_all_three_conditions():
    assert last(adx=30.0, ema_20=101.0, ema_50=100.0, slope_20=-0.001) == rg.UNCLASSIFIED  # slope disagrees
    assert last(adx=30.0, ema_20=99.0, ema_50=100.0, slope_20=0.001) == rg.UNCLASSIFIED
    assert last(adx=24.0, ema_20=101.0, ema_50=100.0, slope_20=0.001) == rg.UNCLASSIFIED  # ADX too low


def test_ranging_needs_low_adx_and_a_squeezed_band():
    width = pd.Series(np.concatenate([np.full(N - 1, 0.05), [0.01]]))  # Latest width far below its history.
    out = rg.classify(**{**inputs(adx=15.0), "bb_width": width})
    assert out.iloc[-1] == rg.RANGING
    flat = rg.classify(**inputs(adx=15.0))  # Constant width is not below its own 20th percentile.
    assert flat.iloc[-1] == rg.UNCLASSIFIED
    assert rg.classify(**{**inputs(adx=22.0), "bb_width": width}).iloc[-1] == rg.UNCLASSIFIED


def test_volatile_wins_an_overlap():
    assert last(adx=30.0, ema_20=101.0, ema_50=100.0, slope_20=0.001, vol_ratio=1.6) == rg.VOLATILE
    assert last(vol_ratio=1.5) == rg.UNCLASSIFIED  # Strictly above 1.5.


def test_warmup_is_nan_then_classified():
    out = rg.classify(**inputs())
    assert out.iloc[: rg.BB_WINDOW - 1].isna().all() and out.iloc[rg.BB_WINDOW - 1:].notna().all()
    bad = inputs()
    bad["adx"].iloc[-1] = np.nan
    assert np.isnan(rg.classify(**bad).iloc[-1])


def test_regime_is_a_built_feature_with_a_true_warmup():
    built = build_features(synthetic_bars(500))
    col = built["regime"]
    assert col.first_valid_index() == DEFINITIONS["regime"][1]
    assert set(col.dropna().unique()) <= {0.0, 1.0, 2.0, 3.0, 4.0}


@pytest.mark.parametrize("cut", [250, 333, 480])
def test_regime_does_not_change_when_the_future_is_removed(cut):
    bars = synthetic_bars(600)
    full = build_features(bars)["regime"].iloc[:cut].to_numpy()
    partial = build_features(bars.iloc[:cut].reset_index(drop=True))["regime"].to_numpy()
    assert np.array_equal(full, partial, equal_nan=True)
