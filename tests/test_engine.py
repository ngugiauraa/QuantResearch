"""Backtester tests with hand-calculated results."""
import numpy as np
import pandas as pd
import pytest

from quantresearch.sim import metrics
from quantresearch.sim.costs import CostModel, MissingCostData
from quantresearch.sim.engine import Decision, Engine, EngineConfig, OrderIntent, Strategy


def bars(rows, spread=0.2, start="2024-01-02 10:00", freq="1min"):
    """rows: list of (open, high, low, close) on the bid side."""
    ts = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC")
    a = np.array(rows, dtype=float)
    frame = pd.DataFrame({"ts": ts, "close_ts": ts + pd.Timedelta(freq),
                          "bid_open": a[:, 0], "bid_high": a[:, 1], "bid_low": a[:, 2], "bid_close": a[:, 3]})
    for i, name in enumerate(("open", "high", "low", "close")):
        frame[f"ask_{name}"] = a[:, i] + spread
    frame["bid_volume"] = 1000.0
    frame["ask_volume"] = 1000.0
    return frame


class Once(Strategy):
    """Sends given intents at the close of given signal bars."""

    def __init__(self, plan):
        self.plan = plan

    def on_bar(self, ctx):
        for intent in self.plan.get(ctx.index, []):
            ctx.submit(intent)


def run(rows, plan, costs=None, equity=100_000.0, spread=0.2, gate=None, **kw):
    b = bars(rows, spread=spread)
    engine = Engine(b, b, Once(plan), EngineConfig(initial_equity=equity, costs=costs or CostModel(), **kw), risk_gate=gate)
    return engine.run()


FLAT = (100.0, 100.5, 99.5, 100.0)


def test_market_buy_fills_at_next_ask_open_not_the_signal_close():
    e = run([FLAT, (101, 101.5, 100.5, 101), FLAT], {0: [OrderIntent(1, 1.0)]})
    assert e.positions[0].entry_price == pytest.approx(101.2)
    assert e.positions[0].entry_time == pd.Timestamp("2024-01-02 10:01", tz="UTC")


def test_market_sell_fills_at_next_bid_open():
    e = run([FLAT, (101, 101.5, 100.5, 101)], {0: [OrderIntent(-1, 1.0)]})
    assert e.positions[0].entry_price == 101.0


def test_long_round_trip_pnl_includes_spread():
    # Buy at 100.2 ask, target at 101 on bid: 0.8 x 100 oz = 80.
    e = run([FLAT, FLAT, (100.5, 101.5, 100.2, 101)], {0: [OrderIntent(1, 1.0, target=101.0)]})
    t = e.trades[0]
    assert t.reason == "target" and t.gross == pytest.approx(80.0)
    assert e.account.balance == pytest.approx(100_080.0)


def test_short_stop_is_tested_against_the_ask():
    # Short at 100.0 bid. The bid high of 100.9 stays under the 101 stop, but the ask reaches 101.1.
    e = run([FLAT, FLAT, (100, 100.9, 99.9, 100)], {0: [OrderIntent(-1, 1.0, stop=101.0)]})
    assert e.trades and e.trades[0].reason == "stop"
    assert e.trades[0].exit_price == 101.0
    assert e.trades[0].gross == pytest.approx(-100.0)


def test_both_touched_in_one_bar_counts_as_the_stop():
    e = run([FLAT, FLAT, (100, 102, 98, 100)], {0: [OrderIntent(1, 1.0, stop=99.0, target=101.0)]})
    assert e.trades[0].reason == "stop"


def test_gap_through_stop_fills_at_the_open_not_the_stop():
    e = run([FLAT, FLAT, (95, 96, 94, 95)], {0: [OrderIntent(1, 1.0, stop=99.0)]})
    t = e.trades[0]
    assert t.reason == "stop_gap" and t.exit_price == 95.0
    assert t.gross == pytest.approx((95 - 100.2) * 100)


def test_gap_through_target_fills_at_the_better_open():
    e = run([FLAT, FLAT, (105, 106, 104, 105)], {0: [OrderIntent(1, 1.0, target=101.0)]})
    assert e.trades[0].exit_price == 105.0


def test_slippage_and_commission_hand_calculation():
    costs = CostModel(slippage_usd=0.1, commission_per_lot=7.0)
    e = run([FLAT, FLAT, (100, 100.5, 98, 99)], {0: [OrderIntent(1, 2.0, stop=99.0)]}, costs=costs)
    t = e.trades[0]
    assert t.entry_price == pytest.approx(100.3)  # ask 100.2 + 0.1
    assert t.exit_price == pytest.approx(98.9)  # stop 99 - 0.1
    assert t.gross == pytest.approx((98.9 - 100.3) * 2 * 100)
    assert t.commission == pytest.approx(28.0)  # 7 x 2 lots, each way
    assert t.net == pytest.approx(t.gross - 28.0)
    assert e.account.balance == pytest.approx(100_000 + t.net)


def test_execution_delay_postpones_the_fill():
    costs = CostModel(delay_seconds=90)
    e = run([FLAT, (101, 101, 101, 101), (102, 102, 102, 102), (103, 103, 103, 103)], {0: [OrderIntent(1, 1.0)]}, costs=costs)
    # Submitted at 10:01. Eligible at 10:02:30, so the 10:03 bar is the first that qualifies.
    assert e.positions[0].entry_time == pd.Timestamp("2024-01-02 10:03", tz="UTC")


def test_extra_spread_stress_only_worsens_the_ask_side():
    e = run([FLAT, FLAT], {0: [OrderIntent(1, 1.0)]}, costs=CostModel(extra_spread_usd=0.5))
    assert e.positions[0].entry_price == pytest.approx(100.7)


def test_limit_order_waits_and_fills_at_its_price():
    rows = [FLAT, FLAT, (100, 100, 98, 99)]
    e = run(rows, {0: [OrderIntent(1, 1.0, kind="limit", price=99.0)]})
    assert e.positions[0].entry_price == 99.0
    assert e.positions[0].entry_time == pd.Timestamp("2024-01-02 10:02", tz="UTC")


def test_stop_entry_triggers_on_break():
    rows = [FLAT, (100, 100.3, 99.8, 100), (100.5, 103, 100.5, 102)]
    e = run(rows, {0: [OrderIntent(1, 1.0, kind="stop", price=102.0)]})
    assert e.positions[0].entry_price == 102.0


def test_expired_order_never_fills():
    ex = pd.Timestamp("2024-01-02 10:02", tz="UTC")
    e = run([FLAT, FLAT, (100, 100, 90, 91)], {0: [OrderIntent(1, 1.0, kind="limit", price=95.0, expires_at=ex)]})
    assert not e.positions and any(x["event"] == "order_expired" for x in e.events)


def test_missing_ask_without_assumption_refuses():
    b = bars([FLAT, FLAT])
    for c in ("open", "high", "low", "close"):
        b[f"ask_{c}"] = np.nan
    e = Engine(b, b, Once({0: [OrderIntent(1, 1.0)]}))
    with pytest.raises(MissingCostData):
        e.run()


def test_assumed_spread_is_labelled_as_assumed():
    b = bars([FLAT, FLAT, FLAT])
    for c in ("open", "high", "low", "close"):
        b[f"ask_{c}"] = np.nan
    e = Engine(b, b, Once({0: [OrderIntent(1, 1.0)]}), EngineConfig(costs=CostModel(assumed_spread_usd=0.4)))
    e.run()
    assert e.positions[0].entry_price == pytest.approx(100.4)
    assert e.positions[0].cost_source == "assumed"


def test_observed_cost_label():
    e = run([FLAT, FLAT], {0: [OrderIntent(1, 1.0)]})
    assert e.positions[0].cost_source == "observed_ask_candle"


def test_partial_fills_respect_participation_cap():
    b = bars([FLAT, FLAT, FLAT, FLAT])
    b["bid_volume"], b["ask_volume"] = 50.0, 50.0
    costs = CostModel(max_volume_participation=0.1)  # 0.1 x 100 / 100 = 0.10 lots a bar
    e = Engine(b, b, Once({0: [OrderIntent(1, 0.25)]}), EngineConfig(costs=costs)).run()
    assert [p.lots for p in e.positions] == pytest.approx([0.10, 0.10, 0.05])
    assert any(x["event"] == "partial_fill" for x in e.events)


def test_equity_marks_long_at_bid_and_short_at_ask():
    e = run([FLAT, FLAT, FLAT], {0: [OrderIntent(1, 1.0)]})
    assert e.account.equity == pytest.approx(100_000 + (100.0 - 100.2) * 100)
    e2 = run([FLAT, FLAT, FLAT], {0: [OrderIntent(-1, 1.0)]})
    assert e2.account.equity == pytest.approx(100_000 + (100.0 - 100.2) * 100)


def test_position_sizing_step_rounding_and_zero_rejection():
    e = run([FLAT, FLAT], {0: [OrderIntent(1, 0.126), OrderIntent(1, 0.001)]})
    assert e.positions[0].lots == pytest.approx(0.13)
    assert any(x["event"] == "order_rejected" for x in e.events)


def test_wrong_side_bracket_is_rejected():
    e = run([FLAT, FLAT], {0: [OrderIntent(1, 1.0, stop=105.0, target=95.0)]})
    assert not e.positions


def test_risk_gate_can_reject_and_reduce():
    def no(intent, engine):
        return Decision(False, reason="limit")

    assert not run([FLAT, FLAT], {0: [OrderIntent(1, 1.0)]}, gate=no).positions

    def half(intent, engine):
        return Decision(True, lots=0.5, reason="cap")

    assert run([FLAT, FLAT], {0: [OrderIntent(1, 1.0)]}, gate=half).positions[0].lots == 0.5


def test_r_multiple():
    e = run([FLAT, FLAT, (100.5, 102.5, 100.2, 102)], {0: [OrderIntent(1, 1.0, stop=99.2, target=102.2)]})
    assert e.trades[0].r_multiple == pytest.approx(2.0)


def test_swap_accrues_per_night():
    rows = [FLAT] * 3
    ts = pd.to_datetime(["2024-01-02 23:58", "2024-01-02 23:59", "2024-01-03 00:00"], utc=True)
    b = bars(rows)
    b["ts"], b["close_ts"] = ts, ts + pd.Timedelta("1min")
    e = Engine(b, b, Once({0: [OrderIntent(1, 2.0)]}), EngineConfig(costs=CostModel(swap_long_per_lot_day=-5.0))).run()
    assert e.positions[0].swap == pytest.approx(-10.0)


def test_strategy_context_only_shows_closed_bars():
    seen = []

    class Peek(Strategy):
        def on_bar(self, ctx):
            history = ctx.history()
            seen.append((ctx.index, len(history), history["close_ts"].iloc[-1] <= ctx.now))

    b = bars([FLAT] * 5)
    Engine(b, b, Peek()).run()
    assert seen == [(i, i + 1, True) for i in range(5)]


def test_signals_on_a_higher_timeframe_fire_at_their_close():
    m1 = bars([FLAT] * 10)
    m5 = bars([FLAT] * 2, freq="5min")
    times = []

    class Rec(Strategy):
        def on_bar(self, ctx):
            times.append(ctx.now)

    Engine(m1, m5, Rec()).run()
    assert times == [pd.Timestamp("2024-01-02 10:05", tz="UTC"), pd.Timestamp("2024-01-02 10:10", tz="UTC")]


def test_tick_execution_uses_actual_quotes():
    ts = pd.date_range("2024-01-02 10:00:00", periods=4, freq="s", tz="UTC")
    ticks = pd.DataFrame({"ts": ts, "bid": [100, 100.2, 99, 98.0], "ask": [100.3, 100.5, 99.3, 98.3]})
    sig = pd.DataFrame({"ts": [ts[0]], "close_ts": [ts[1]]})
    e = Engine(ticks, sig, Once({0: [OrderIntent(1, 1.0, stop=99.0)]})).run()
    assert e.trades[0].entry_price == pytest.approx(100.5)
    assert e.trades[0].exit_price == 99.0
    assert e.trades[0].cost_source == "observed_tick"


def test_close_all_and_digest_are_deterministic():
    a = run([FLAT, FLAT, FLAT], {0: [OrderIntent(1, 1.0)]})
    b = run([FLAT, FLAT, FLAT], {0: [OrderIntent(1, 1.0)]})
    assert a.digest() == b.digest()
    a.close_all()
    assert not a.positions and a.trades[0].reason == "end_of_data"


def test_metrics_hand_calculation():
    trades = pd.DataFrame({
        "tag": ["a", "a", "b", "b"], "side": [1, 1, -1, -1], "net": [100.0, -50.0, 200.0, -50.0],
        "r_multiple": [1.0, -0.5, 2.0, -0.5], "commission": [1.0] * 4, "swap": [0.0] * 4,
        "entry_time": pd.to_datetime(["2024-01-02 10:00"] * 4, utc=True),
        "exit_time": pd.to_datetime(["2024-01-02 12:00"] * 4, utc=True), "cost_source": ["assumed"] * 4,
    })
    s = metrics.trade_stats(trades)
    assert s["net_profit"] == 200 and s["win_rate"] == 0.5
    assert s["profit_factor"] == 3.0 and s["expectancy"] == 50
    assert s["average_win"] == 150 and s["average_loss"] == -50
    assert s["average_r"] == 0.5 and s["average_holding_hours"] == 2


def test_equity_metrics_hand_calculation():
    idx = pd.date_range("2024-01-01", periods=5, freq="D", tz="UTC")
    eq = pd.Series([100.0, 110, 99, 121, 121], index=idx)
    s = metrics.equity_stats(eq)
    assert s["max_drawdown"] == pytest.approx(0.1)
    assert s["max_drawdown_usd"] == pytest.approx(11)
    assert s["total_return"] == pytest.approx(0.21)
    assert s["recovery_factor"] == pytest.approx(21 / 11)


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
def test_clock_is_correct_when_time_units_differ_between_columns(unit):
    # Regression: raw integer comparison across pandas time units put events in the wrong order.
    exec_bars = bars([FLAT] * 6)
    signals = bars([FLAT] * 6)
    signals["close_ts"] = signals["close_ts"].astype(f"datetime64[{unit}, UTC]")
    exec_bars["ts"] = exec_bars["ts"].astype("datetime64[us, UTC]")
    seen = []

    class Rec(Strategy):
        def on_bar(self, ctx):
            seen.append((ctx.index, ctx._e.cursor))

    Engine(exec_bars, signals, Rec()).run()
    assert seen[:3] == [(0, 1), (1, 2), (2, 3)]


def test_run_until_stops_at_the_right_time_whatever_the_unit():
    b = bars([FLAT] * 10)
    b["ts"] = b["ts"].astype("datetime64[us, UTC]")
    e = Engine(b, b, Strategy()).run(until=pd.Timestamp("2024-01-02 10:03", tz="UTC"))
    assert e.cursor == 3
