"""The personal playbooks: the building blocks, the trade simulation and the one-trade-a-day rule."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from quantresearch.playbooks import examples, primitives as P, setups as S


def arr(*x):
    return np.asarray(x, dtype="float64")


def test_fvg_is_the_three_candle_gap_known_at_candle_three():
    o, h, l, c = arr(10, 11, 13, 13), arr(11, 13, 15, 14), arr(9, 10.5, 12, 12.5), arr(11, 13, 14, 13)
    ts = np.arange(4).astype("datetime64[h]").astype("datetime64[ns]")
    z = P.fvgs(o, h, l, c, ts, ts + np.timedelta64(1, "h"), "h1")
    assert [(g.direction, g.lo, g.hi) for g in z] == [(1, 11.0, 12.0), (1, 13.0, 12.5)][:1]
    assert z[0].formed_ts == ts[2] + np.timedelta64(1, "h")


def test_a_zone_is_mitigated_only_when_price_trades_through_its_far_edge():
    z = P.Zone("fvg", "h1", 1, 100.0, 102.0, np.datetime64("2024-01-01T01:00", "ns"))
    t = np.array(["2024-01-01T01:00", "2024-01-01T02:00", "2024-01-01T03:00"], dtype="datetime64[ns]")
    P.mark_fills([z], arr(103, 103, 103), arr(101, 100.5, 99.9), t)  # Touches, then trades through.
    assert z.filled_ts == t[2] and z.unmitigated_at(t[1]) and not z.unmitigated_at(t[2])


def test_structure_break_and_cisd():
    h = arr(10, 11, 15, 11, 10, 12, 16, 13)
    l = arr(9, 10, 12, 9, 8, 10, 13, 11)
    o, c = l + 0.5, h - 0.5
    sw = P.swings(h, l, k=2)
    br = P.structure_breaks(o, h, l, c, sw)
    assert br and br[0].direction == 1 and br[0].level == 15.0 and br[0].idx == 6  # First close above the swing high.
    # Bearish CISD after a high: the open of the first candle of the up-close run into the extreme.
    o2, c2 = arr(5, 4, 4.5, 5, 6), arr(4, 4.5, 5, 6, 5.5)
    assert P.cisd_level(o2, c2, 3, -1) == 4.0


class FakeFrames:
    """Just enough of Frames for simulate(): one minute bar per row."""

    def __init__(self, bid_low, bid_high, bid_open=None, spread=0.2, start="2024-01-02 14:00", bid_close=None):
        n = len(bid_low)
        ts = pd.date_range(start, periods=n, freq="1min")
        self.m1_ts = ts.to_numpy("datetime64[ns]")
        bo = arr(*(bid_open if bid_open is not None else bid_low))
        bc = arr(*bid_close) if bid_close is not None else (arr(*bid_low) + arr(*bid_high)) / 2
        self.m1_cols = {"bid_open": bo, "bid_high": arr(*bid_high), "bid_low": arr(*bid_low), "bid_close": bc, "ask_open": bo + spread,
                        "ask_high": arr(*bid_high) + spread, "ask_low": arr(*bid_low) + spread, "ask_close": bc + spread,
                        "spread_mean": np.full(n, spread)}


def sig(side=1, stop=99.0, rule="2r", level=None):
    return S.Signal("x", "2024-01-02", side, "2024-01-02 14:00:00+00:00", 100.0, stop, rule, level)


def test_long_fills_at_the_ask_and_reaches_2r():
    F = FakeFrames([100, 100, 101.5, 102.5], [100.5, 101, 102, 103.5], bid_open=[100, 100.2, 101.5, 102.5])
    r = S.simulate(F, sig(stop=99.2))
    assert r["fill"] == pytest.approx(100.2) and r["risk"] == pytest.approx(1.0) and r["exit_reason"] == "target" and r["r"] == pytest.approx(2.0)


def test_breakeven_moves_the_stop_after_1r():
    F = FakeFrames([100, 100.5, 100.1], [100.5, 101.3, 100.6], bid_open=[100, 100.6, 100.5])
    r = S.simulate(F, sig(stop=99.2))  # Fill 100.2, risk 1.0: 1R reached on bar 1, then back to the fill.
    assert r["exit_reason"] == "breakeven" and r["r"] == pytest.approx(0.0)


def test_stop_wins_when_one_minute_touches_both():
    F = FakeFrames([100, 99.0], [100.5, 103.0], bid_open=[100, 100.3])
    assert S.simulate(F, sig(stop=99.2))["exit_reason"] == "stop"


def test_a_gap_through_the_stop_fills_at_the_open():
    F = FakeFrames([100, 98.0], [100.5, 98.5], bid_open=[100, 98.2])
    r = S.simulate(F, sig(stop=99.2))
    assert r["exit"] == pytest.approx(98.2) and r["r"] < -1


def test_setup3_trades_only_if_the_swing_is_2r_away():
    F = FakeFrames([100, 101], [100.5, 103], bid_open=[100, 101])
    assert "skipped" in S.simulate(F, sig(stop=99.2, rule="level_only", level=101.5))
    assert S.simulate(F, sig(stop=99.2, rule="level_only", level=102.3))["target_kind"] == "level"


def test_pool_target_only_when_at_least_2r():
    F = FakeFrames([100, 101], [100.5, 104], bid_open=[100, 101])
    assert S.simulate(F, sig(stop=99.2, rule="level_or_2r", level=101.0))["target_kind"] == "2R"
    assert S.simulate(F, sig(stop=99.2, rule="level_or_2r", level=103.0))["target"] == pytest.approx(103.0)


def test_short_uses_the_ask_for_stops():
    F = FakeFrames([100, 100.0], [100.2, 100.9], bid_open=[100, 100.0], spread=0.3)
    r = S.simulate(F, sig(side=-1, stop=101.0))  # Bid 100.9 + 0.3 spread = ask 101.2 crosses the 101 stop.
    assert r["exit_reason"] == "stop"


def test_combined_takes_the_earliest_signal_of_the_day():
    def row(setup, ts, day="2024-01-02"):
        return {"setup": setup, "day": day, "signal": {"signal_ts": ts}, "trade": {"r": 1.0}}
    rows = [row("fvg_continuation", "2024-01-02 09:10:00+00:00"), row("london_asian_sweep", "2024-01-02 08:00:00+00:00"),
            row("ny_sweep_ifvg", "2024-01-02 08:00:00+00:00"), row("ny_sweep_ifvg", "2024-01-03 14:00:00+00:00", "2024-01-03")]
    out = S.combined(rows)
    assert [(r["day"], r["setup"]) for r in out] == [("2024-01-02", "ny_sweep_ifvg"), ("2024-01-03", "ny_sweep_ifvg")]


def test_examples_spread_across_years_without_choosing_by_result():
    rows = [{"day": f"{y}-03-{d:02d}", "r": y * d} for y in range(2004, 2027) for d in range(1, 6)]
    a, b = examples.pick(rows), examples.pick(rows)
    assert a == b and len(a) == 15 and len({r["day"][:4] for r in a}) == 15


def test_an_open_trade_closes_at_17_00_new_york():
    # 2024-01-02 is winter: 17:00 New York is 22:00 UTC. Start at 21:57 UTC; three minutes in range, then 22:00 onwards.
    F = FakeFrames([100, 100.1, 100.2, 100.3, 90], [100.5, 100.6, 100.7, 100.8, 91], bid_open=[100, 100.1, 100.2, 100.3, 90],
                   start="2024-01-02 21:57", bid_close=[100.4, 100.5, 100.6, 100.7, 90.5])
    s = S.Signal("x", "2024-01-02", 1, "2024-01-02 21:57:00+00:00", 100.0, 99.2, "2r", None)
    r = S.simulate(F, s)
    assert r["exit_reason"] == "time_1700" and r["exit"] == pytest.approx(100.6)  # The 21:59 close; the 22:00 crash is never seen.
    assert r["exit_ts"].startswith("2024-01-02 21:59")


def test_no_signal_after_17_00():
    F = FakeFrames([100, 100], [100.5, 100.5], start="2024-01-02 22:05")
    assert "skipped" in S.simulate(F, S.Signal("x", "2024-01-02", 1, "2024-01-02 22:05:00+00:00", 100.0, 99.2, "2r", None))


def test_report_streak_and_drawdown():
    from quantresearch.playbooks import report as R

    r = np.array([2.0, -1, -1, -1, 0.0, -1, 2.0])
    assert R.max_streak(r) == 3  # A breakeven (0R) ends a losing streak.
    dd_r, dd_pct = R.drawdown(r)
    assert dd_r == pytest.approx(4.0) and 1.9 < dd_pct < 2.1  # -4R from the +2R peak, about 2% at 0.5% risk.


def test_report_folds_need_twenty_training_trades():
    from quantresearch.playbooks import report as R

    days = pd.bdate_range("2003-06-01", "2012-12-31")
    rng = np.random.default_rng(1)
    df = pd.DataFrame({"setup": "x", "day": days[::20], "side": 1, "fill_ts": days[::20], "r": rng.choice([-1.0, 2.0], len(days[::20])),
                       "r_stress": -1.0, "exit_reason": "stop"})
    gold = pd.DataFrame({"ts": pd.date_range("2003-05-05", "2012-12-31", freq="D", tz="UTC")})
    gold["close_ts"] = gold["ts"] + pd.Timedelta(days=1)
    gold["close"] = 400 * np.cumprod(1 + rng.normal(0, 0.01, len(gold)))
    rep, oos = R.evaluate("ny_sweep_ifvg", df, gold, pd.Timestamp("2011-01-01", tz="UTC"), gold["close_ts"].iloc[-1], gold["ts"].iloc[0])
    assert all(f["train_trades"] >= 20 for f in rep["folds"]) and rep["walk_forward"]["out_of_sample"]["trades"] > 0
    assert rep["holdout_stats"]["trades"] == int((df["day"] >= "2011-01-01").sum())
