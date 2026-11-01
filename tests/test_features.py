import numpy as np
import pandas as pd
import pytest

from quantresearch.features import microstructure as micro
from quantresearch.features import pit, structure, technical as tech
from quantresearch.features.registry import DEFINITIONS, build_features, feature_columns
from quantresearch.warehouse.store import synthetic_bars


def frame(highs, lows=None, closes=None):
    highs = np.array(highs, dtype=float)
    lows = highs - 1 if lows is None else np.array(lows, dtype=float)
    closes = (highs + lows) / 2 if closes is None else np.array(closes, dtype=float)
    ts = pd.date_range("2024-01-02 10:00", periods=len(highs), freq="min", tz="UTC")
    return pd.DataFrame({"ts": ts, "high": highs, "low": lows, "close": closes, "open": closes})


def test_every_feature_is_defined_and_every_definition_is_built():
    built = build_features(synthetic_bars(300))
    missing = [c for c in feature_columns() if c not in built]
    assert not missing
    extra = set(built.columns) - set(feature_columns()) - set(synthetic_bars(5).columns)
    assert not extra


@pytest.mark.parametrize("cut", [120, 257, 1319, 1320, 1321, 2800, 3600])
def test_no_feature_changes_when_the_future_is_removed(cut):
    # Spans several New York day rolls, so previous-day levels are exercised too.
    bars = synthetic_bars(4000)
    full = build_features(bars)
    partial = build_features(bars.iloc[:cut].reset_index(drop=True))
    for column in feature_columns():
        a, b = full[column].iloc[:cut].to_numpy(), partial[column].to_numpy()
        assert np.array_equal(a, b, equal_nan=True) or np.allclose(a, b, equal_nan=True, rtol=1e-12, atol=1e-12), column


def test_feature_warmups_match_their_definitions():
    built = build_features(synthetic_bars(400))
    for name in ("sma_200", "atr_14", "rsi_14", "zscore_20", "donchian_high_20"):
        first = built[name].first_valid_index()
        assert first == DEFINITIONS[name][1], name


def test_rsi_extremes():
    up = pd.Series(np.arange(1.0, 40.0))
    assert tech.rsi(up).iloc[-1] == 100.0
    flat = pd.Series(np.full(40, 5.0))
    assert tech.rsi(flat).iloc[-1] == 50.0
    assert tech.rsi(-up + 100).iloc[-1] == pytest.approx(0.0)


def test_rsi_matches_hand_calculation():
    close = pd.Series([10, 11, 10, 11, 10, 11.0])
    # Gains and losses alternate equally over the first 4 changes, so RS starts at 1.
    assert tech.rsi(close, 4).iloc[4] == pytest.approx(50.0)
    assert tech.rsi(close, 4).iloc[5] == pytest.approx(100 - 100 / (1 + (0.5 * 3 + 1) / 4 / (0.5 * 3 / 4)))


def test_atr_hand_calculation():
    f = frame([11, 12, 13, 14], [9, 10, 11, 12], [10, 11, 12, 13])
    # True ranges from bar 1: all 2.0, so the Wilder average stays 2.0.
    assert tech.atr(f.high, f.low, f.close, 2).iloc[2] == 2.0
    assert np.isnan(tech.atr(f.high, f.low, f.close, 2).iloc[1])


def test_bollinger_at_constant_price_has_no_percent():
    b = tech.bollinger(pd.Series(np.full(30, 7.0)))
    assert b.bb_mid.iloc[-1] == 7.0 and np.isnan(b.bb_pct.iloc[-1])


def test_donchian_excludes_the_current_bar():
    f = frame([1, 2, 3, 100], [0, 1, 2, 3], [1, 2, 3, 100])
    d = tech.donchian(f.high, f.low, 3)
    assert d["donchian_high_3"].iloc[3] == 3


def test_adx_rises_in_a_steady_trend():
    n = 80
    f = frame(np.arange(n) + 2.0, np.arange(n) + 1.0, np.arange(n) + 1.5)
    a = tech.adx(f.high, f.low, f.close)
    assert a.adx.iloc[-1] > 90 and a.plus_di.iloc[-1] > a.minus_di.iloc[-1]


def test_swing_needs_confirmation_bars():
    high = np.array([1, 2, 3, 9, 3, 2, 1, 1, 1.0])
    low = high - 0.5
    sw_h, _ = structure.swings(high, low, 3, 3)
    assert sw_h[0] == (6, 3, 9.0)
    f = frame(high, low, high - 0.2)
    ms = structure.market_structure(f)
    assert np.isnan(ms.swing_high.iloc[5]) and ms.swing_high.iloc[6] == 9.0


def _structure_path():
    # Swing high 10 at bar 3 then swing low 5 at bar 8, then a break up, then a break down.
    highs = [8, 9, 9.5, 10, 9.5, 9, 8, 7, 6, 7, 8, 9, 9.5, 11, 10.5, 9, 8, 6, 5, 4]
    lows = [h - 1 for h in highs]
    closes = [h - 0.5 for h in highs]
    return frame(highs, lows, closes)


def test_break_of_structure_then_change_of_character():
    ms = structure.market_structure(_structure_path())
    assert ms.bos_up.sum() >= 1
    first_up = ms.index[ms.bos_up == 1][0]
    assert ms.structure_trend.iloc[first_up] == 1
    assert ms.choch_down.sum() >= 1
    down = ms.index[ms.choch_down == 1][0]
    assert down > first_up and ms.structure_trend.iloc[down] == -1


def test_liquidity_sweep_needs_close_back_inside():
    highs = [8, 9, 10, 11, 10, 9, 8, 9, 11.5, 9]
    lows = [h - 1 for h in highs]
    closes = [h - 0.5 for h in highs]
    closes[8] = 10.5  # wick above swing high 11, close back below
    ms = structure.market_structure(frame(highs, lows, closes))
    assert ms.sweep_high.iloc[8] == 1 and ms.bos_up.iloc[8] == 0


def test_equal_highs_uses_atr_tolerance():
    highs = [8, 9, 10, 11, 10, 9, 8, 9, 10, 11.05, 10, 9, 8, 7]
    f = frame(highs)
    atr_ = pd.Series(np.full(len(highs), 1.0))
    ms = structure.market_structure(f, equal_atr=0.1, atr=atr_)
    assert ms.equal_highs.iloc[-1] == 1


def test_fvg_forms_and_inverts():
    highs = [10, 11, 20, 21, 21, 21]
    lows = [9, 10, 15, 16, 16, 5]
    closes = [9.5, 10.5, 19, 20, 20, 8]
    f = structure.fair_value_gaps(frame(highs, lows, closes))
    assert f.fvg_up.iloc[2] == 1
    assert (f.fvg_up_low.iloc[2], f.fvg_up_high.iloc[2]) == (10.0, 15.0)
    assert f.ifvg_down.iloc[5] == 1
    assert f.fvg_up_count.iloc[5] < f.fvg_up_count.iloc[4]


def test_bearish_fvg_mirror():
    f = structure.fair_value_gaps(frame([20, 19, 10], [15, 14, 5], [17, 16, 7]))
    assert f.fvg_down.iloc[2] == 1
    assert (f.fvg_down_low.iloc[2], f.fvg_down_high.iloc[2]) == (10.0, 15.0)


def test_previous_day_levels_only_after_the_day_ends():
    # Monday 2024-01-08 in January: the New York day rolls at 22:00 UTC.
    ts = pd.to_datetime(["2024-01-08 21:00", "2024-01-08 21:59", "2024-01-08 22:00", "2024-01-08 23:00"], utc=True)
    f = pd.DataFrame({"ts": ts, "high": [10.0, 12, 11, 15], "low": [9.0, 8, 10, 11], "close": [9.5, 11, 10.5, 14]})
    s = structure.session_levels(f)
    assert s.pdh.isna().iloc[:2].all()
    assert s.pdh.iloc[2] == 12 and s.pdl.iloc[2] == 8 and s.pdc.iloc[2] == 11
    assert s.day_high.iloc[3] == 15 and s.day_low.iloc[2] == 10


def test_spread_features_use_observed_quotes_and_stay_null_without_ask():
    bars = synthetic_bars(100, spread=0.4)
    m = micro.microstructure_features(bars)
    assert m.spread.iloc[50] == pytest.approx(0.4)
    no_ask = bars.copy()
    no_ask["ask_close"] = np.nan
    assert micro.microstructure_features(no_ask).spread.isna().all()


def test_tick_rule_carries_direction():
    ts = pd.date_range("2024-01-02", periods=5, freq="s", tz="UTC")
    ticks = pd.DataFrame({"ts": ts, "ask": [2, 3, 3, 2, 2.0], "bid": [1, 2, 2, 1, 1.0]})
    assert micro.tick_features(ticks, 2).tick_rule.tolist() == [0, 1, 1, -1, -1]


def events():
    t = lambda s: pd.Timestamp(s, tz="UTC")
    return pd.DataFrame([
        ("cpi", t("2024-01-01"), t("2024-02-13 13:30"), t("2024-02-13 14:00"), 0, 3.1, "bls"),
        ("cpi", t("2024-02-01"), t("2024-03-12 12:30"), t("2024-03-12 13:00"), 0, 3.2, "bls"),
        ("cpi", t("2024-01-01"), t("2024-03-20 12:30"), t("2024-03-20 13:00"), 1, 3.0, "bls"),
    ], columns=pit.EVENT_COLUMNS)


def test_as_of_hides_unpublished_values_and_later_revisions():
    e = events()
    assert pit.as_of(e, "2024-02-13 13:29").empty
    assert pit.as_of(e, "2024-02-13 13:30").value.tolist() == [3.1]
    mid = pit.as_of(e, "2024-03-15")
    assert mid.value.tolist() == [3.1, 3.2]
    assert pit.as_of(e, "2024-03-21").value.tolist() == [3.0, 3.2]


def test_align_events_uses_publication_time_and_newest_period():
    e = events()
    index = pd.Series(pd.to_datetime(["2024-02-01", "2024-02-14", "2024-03-13", "2024-03-21"], utc=True))
    out = pit.align_events(index, e, "cpi")
    assert np.isnan(out.iloc[0])
    assert out.iloc[1:].tolist() == [3.1, 3.2, 3.2]


def test_higher_timeframe_join_cannot_see_an_open_bar():
    base = synthetic_bars(10, "m5", start="2024-01-02 10:00")
    higher = synthetic_bars(2, "h1", start="2024-01-02 09:00")
    higher["marker"] = [1.0, 2.0]
    out = pit.align_higher_tf(base, higher, ["marker"], "h1_")
    # The 10:00-11:00 H1 bar closes at 11:00, after all these M5 bars: only 09:00's is visible.
    assert (out["h1_marker"] == 1.0).all()
    assert not (out["h1_marker"] == 2.0).any()
