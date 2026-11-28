"""Forward test 001: the demo-only guard, the portfolio rules, the runner and the prediction replay."""
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from quantresearch.core.config import Paths
from quantresearch.execution.router import AuditLog, Mode, OrderRouter
from quantresearch.forward import plan
from quantresearch.forward.evaluate import evaluate, max_drawdown_pct
from quantresearch.forward.guard import REFUSED_EVENT, DemoOnlyMt5Broker
from quantresearch.forward.journal import Journal
from quantresearch.forward.portfolio import BarSignal, Portfolio, RouterExecutor
from quantresearch.forward.predict import ReplayBroker, replay
from quantresearch.forward.runner import ForwardTest
from quantresearch.risk.engine import RiskEngine, RiskState
from test_risk_execution import FakeMt5


class Fake(FakeMt5):
    ACCOUNT_TRADE_MODE_CONTEST = 1
    ACCOUNT_MARGIN_MODE_RETAIL_HEDGING = 2

    def __init__(self, trade_mode=0, equity=1_000_000.0):
        super().__init__(trade_mode)
        self.equity, self.info_fails = equity, False

    def account_info(self):
        if self.info_fails:
            return None
        return SimpleNamespace(balance=self.equity, equity=self.equity, margin_free=self.equity, currency="USD", trade_mode=self.trade_mode,
                               trade_allowed=True, server="Test-MT5Trial", login=1, margin_mode=2)


def demo_broker(trade_mode=0, **kw):
    fake = Fake(trade_mode, **kw)
    events, refused = [], []
    b = DemoOnlyMt5Broker(symbol="XAUUSDm", module=fake, sleep=lambda s: None, audit=lambda e, d: events.append((e, d)), on_refused=refused.append)
    b.connect()
    return b, fake, events, refused


# ------------------------------------------------------------ the hard safety guard
@pytest.mark.parametrize("mode", [2, 1, 7])  # real, contest, unknown
def test_every_write_is_refused_unless_mt5_reports_a_demo_account(mode):
    b, fake, events, refused = demo_broker(mode)
    fake.open = [SimpleNamespace(ticket=7, type=0, volume=0.1, price_open=2000.0, sl=1990.0, tp=0.0, profit=1.0, comment="")]
    results = [b.market_order(1, 0.1, 1990.0), b.pending_order(1, 0.1, "limit", 1995.0, 1990.0), b.modify_position(7, 1985.0),
               b.close_position(7), b.cancel_order(9)]
    assert all(not r.ok and "demo-only" in r.message for r in results)
    assert fake.sent == []  # Nothing reached the terminal.
    assert sum(1 for e, _ in events if e == REFUSED_EVENT) == 5 and len(refused) == 5


def test_a_failed_account_reading_is_refused():
    b, fake, events, refused = demo_broker(0)
    fake.info_fails = True
    assert not b.market_order(1, 0.1, 1990.0).ok and fake.sent == [] and refused[0]["account_type"] == "unknown"


def test_a_module_without_the_demo_constant_is_never_demo():
    b, fake, _, _ = demo_broker(0)
    type(fake).ACCOUNT_TRADE_MODE_DEMO = None
    try:
        assert b.account_type() == "unknown" and not b.market_order(1, 0.1, 1990.0).ok and fake.sent == []
    finally:
        type(fake).ACCOUNT_TRADE_MODE_DEMO = 0


def test_live_activation_does_not_open_the_guard(monkeypatch):
    monkeypatch.setenv("QR_LIVE_ENABLED", "1")
    b, fake, _, _ = demo_broker(2)
    b.live_activated = True
    assert not b.market_order(1, 0.1, 1990.0).ok and fake.sent == []


def test_a_demo_account_can_trade_without_the_live_switch(monkeypatch):
    monkeypatch.delenv("QR_LIVE_ENABLED", raising=False)
    b, fake, _, refused = demo_broker(0)
    assert b.market_order(1, 0.1, 1990.0).ok and len(fake.sent) == 1 and fake.sent[0]["magic"] == plan.MAGIC and refused == []


def test_the_live_router_is_untouched():
    from quantresearch.execution.mt5_adapter import Mt5Broker

    assert Mt5Broker.is_simulated is False  # The dashboard's MT5 broker still needs live activation for orders.


# ------------------------------------------------------------ the registration
def test_locked_parameters_match_the_catalogue_and_its_manifest(tmp_path):
    from quantresearch.strategies.catalogue import CATALOGUE

    manifest = {s["name"]: s for s in json.loads((plan.REPO / "docs" / "strategy_catalogue.json").read_text(encoding="utf-8"))["strategies"]}
    for name in plan.STRATEGIES:
        assert CATALOGUE[name].defn.defaults == plan.LOCKED[name]
        assert manifest[name]["defn"]["defaults"] == plan.LOCKED[name] if "defn" in manifest[name] else True
    plan.check_locked(Paths(tmp_path))


def test_the_pre_registration_states_the_numbers_the_code_uses():
    text = plan.DOC.read_text(encoding="utf-8")
    for needle in ("0.25%", "1.0% of equity", "$120,000", "$0.20 per ounce", "$1.35", "six calendar months", "30th closed trade",
                   "less than 10%", "**10%** of price", "**H1** ATR(14)"):
        assert needle in text, needle
    for name, params in plan.LOCKED.items():
        assert f"`{name}`" in text and json.dumps(params) in text


# ------------------------------------------------------------ portfolio rules
def replay_router(equity=1_000_000.0):
    broker = ReplayBroker(equity)
    broker.on_quote(2000.0, 2000.3, "2026-01-05T10:00:00+00:00")
    clock = {"now": pd.Timestamp("2026-01-05 10:00", tz="UTC").to_pydatetime()}
    risk = RiskEngine(plan.limits(), RiskState(), news_schedule=lambda: [])
    return OrderRouter(broker, risk, AuditLog(), Mode.PAPER, clock=lambda: clock["now"]), broker


def sig(name, side=1, close=2000.0, dist=25.0, ts="2026-01-05 10:00", **kw):
    return BarSignal(name, "h1", pd.Timestamp(ts, tz="UTC"), close, side > 0, side < 0, dist, float("nan"), float("nan"),
                     kw.get("exit_long", False), kw.get("exit_short", False), kw.get("held"), kw.get("late", False))


def test_the_same_direction_cap_admits_four_and_misses_the_rest_in_priority_order():
    router, broker = replay_router()
    j = Journal()
    pf = Portfolio(j, RouterExecutor(router))
    pf.step([sig(n) for n in reversed(plan.STRATEGIES)], atr_h1=5.0)  # Order given does not matter: priority does.
    opened = [t["strategy"] for t in j.trades("open")]
    assert opened == list(plan.STRATEGIES[:4])
    missed = j.events(50, ("missed",))
    assert {e["strategy"] for e in missed} == set(plan.STRATEGIES[4:]) and all(e["reason"] == "portfolio_cap" for e in missed)
    risk = sum(t["lots"] * 100 * abs(t["fill_price"] - t["stop"]) for t in j.trades("open"))
    assert risk <= 0.01 * 1_000_000 + 1e-6


def test_opposite_directions_do_not_share_the_cap_and_exits_free_room_first():
    router, _ = replay_router()
    j = Journal()
    pf = Portfolio(j, RouterExecutor(router))
    names = plan.STRATEGIES
    pf.step([sig(n) for n in names[:4]], atr_h1=5.0)
    pf.step([sig(names[4], -1), sig(names[5], -1)], atr_h1=5.0)
    assert len(j.trades("open")) == 6
    # names[0] exits on a signal at the same close where names[6] wants to go long: the exit runs first.
    pf.step([sig(names[0], 0, exit_long=True), sig(names[6], 1)], atr_h1=5.0)
    assert j.open_trade(names[0]) is None and j.open_trade(names[6]) is not None


def test_late_signals_are_missed_and_late_exits_still_run():
    router, _ = replay_router()
    j = Journal()
    pf = Portfolio(j, RouterExecutor(router))
    pf.step([sig("cat_ema_cross_h1")], atr_h1=5.0)
    pf.step([sig("cat_calm_breakout_h1", late=True), sig("cat_ema_cross_h1", -1, late=True)], atr_h1=5.0)
    assert j.open_trade("cat_ema_cross_h1") is None  # The flip's exit ran although late...
    reasons = sorted(e["reason"] for e in j.events(50, ("missed",)))
    assert reasons == ["late", "late"]  # ...but neither late entry was taken.
    assert j.trades("closed")[0]["late_exit"] == 1


def test_sizing_is_quarter_percent_and_too_small_is_missed():
    router, _ = replay_router(equity=100_000.0)
    j = Journal()
    pf = Portfolio(j, RouterExecutor(router))
    pf.step([sig("cat_ema_cross_h1", dist=25.0)], atr_h1=5.0)
    t = j.trades("open")[0]
    # 250 / (25 x 100) = 0.1 from the signal close; the risk engine measures from the 2000.3 ask and trims to 0.09,
    # so the money at risk never exceeds 0.25%. The stop is from the signal close, as in the simulator.
    assert t["lots"] == 0.09 and t["stop"] == 1975.0
    assert t["lots"] * 100 * (t["fill_price"] - t["stop"]) <= 0.0025 * 100_000
    pf.step([sig("cat_calm_breakout_h1", dist=400.0)], atr_h1=5.0)
    assert j.events(5, ("missed",))[0]["reason"] == "below_min_lot"


def test_time_exit_counts_bars_like_the_simulator():
    router, _ = replay_router()
    j = Journal()
    pf = Portfolio(j, RouterExecutor(router))
    s = sig("cat_calm_breakout_h1")
    s.max_hold = 24.0
    pf.step([s], atr_h1=5.0)
    keep = sig("cat_calm_breakout_h1", 0, held=23)
    keep.max_hold = 24.0
    pf.step([keep], atr_h1=5.0)
    assert j.open_trade("cat_calm_breakout_h1") is not None
    out = sig("cat_calm_breakout_h1", 0, held=24)
    out.max_hold = 24.0
    pf.step([out], atr_h1=5.0)
    assert j.trades("closed")[0]["exit_reason"] == "time"


def test_broker_stops_are_recorded_with_their_slippage():
    router, broker = replay_router()
    j = Journal()
    pf = Portfolio(j, RouterExecutor(router))
    pf.step([sig("cat_ema_cross_h1")], atr_h1=5.0)
    broker.on_quote(1970.0, 1970.3, "2026-01-05T11:00:00+00:00", continuous=False)  # Gapped through the 1975 stop.
    pf.sync()
    t = j.trades("closed")[0]
    assert t["exit_reason"] == "broker_stop" and t["exit_price"] == 1970.0 and t["slippage_exit"] == pytest.approx(5.0)


# ------------------------------------------------------------ the runner
def test_a_tick_on_a_real_account_sends_nothing_and_raises_the_alert(tmp_path):
    b, fake, _, _ = demo_broker(2)
    reads = []
    test = ForwardTest(Paths(tmp_path), broker=b, loader=lambda *a: reads.append(a) or pd.DataFrame(columns=["ts", "close_ts", "close"]),
                       macro_refresh=lambda p: None)
    test.journal.set("start", {"at": "2026-01-05T00:00:00+00:00", "equity": 1_000_000.0, "load_start": "2023-01-01T00:00:00+00:00"})
    out = test.tick()
    assert out["state"] == "alert" and fake.sent == [] and reads == []  # Stopped before any signal work.
    status = test.status()
    assert status["alert"]["kind"] == "not_a_demo_account" and status["alert"]["account_type"] == "real"
    assert test.clear_alert("checked")["cleared"] and test.journal.get("alert") is None


def test_start_is_refused_on_a_real_account_or_a_small_balance(tmp_path, monkeypatch):
    monkeypatch.setattr(plan, "doc_commit", lambda: "abc123")
    for mode, equity, failing in ((2, 1_000_000.0, "demo_account"), (0, 500.0, "starting_equity")):
        b, fake, _, _ = demo_broker(mode, equity=equity)
        test = ForwardTest(Paths(tmp_path / f"{mode}_{int(equity)}"), broker=b)
        r = test.readiness()
        assert not r["ready"] and not r["checks"][failing]["ok"]
        with pytest.raises(plan.PlanViolation):
            test.start()
        assert fake.sent == [] and test.journal.get("start") is None


def test_start_records_once_and_needs_a_committed_registration(tmp_path, monkeypatch):
    monkeypatch.setattr(plan, "DOC", tmp_path / "doc.md")
    plan.DOC.write_text("(Filled in at start: date and time UTC, commit hash, starting demo equity.)", encoding="utf-8")
    monkeypatch.setattr(plan, "doc_commit", lambda: "abc1234567890")
    b, _, _, _ = demo_broker(0)
    test = ForwardTest(Paths(tmp_path), broker=b, clock=lambda: pd.Timestamp("2026-12-01 10:00", tz="UTC").to_pydatetime())
    rec = test.start()
    assert rec["equity"] == 1_000_000.0 and rec["load_start"].startswith("2023-12-01")
    assert "abc123456789" in plan.DOC.read_text(encoding="utf-8")
    with pytest.raises(plan.PlanViolation, match="not_started"):
        test.start()

    def uncommitted():
        raise plan.PlanViolation("docs/forward_test_001.md has uncommitted changes")
    monkeypatch.setattr(plan, "doc_commit", uncommitted)
    fresh = ForwardTest(Paths(tmp_path / "fresh"), broker=b)
    assert not fresh.readiness()["checks"]["pre_registration_committed"]["ok"]


# ------------------------------------------------------------ replay and evaluation
@pytest.fixture()
def synthetic(monkeypatch):
    from quantresearch.features.registry import build_features
    from quantresearch.research import catalogue as cat_runner
    from quantresearch.warehouse.build import aggregate
    from quantresearch.warehouse.store import _finish, synthetic_bars

    m1 = synthetic_bars(40 * 1440, "m1", start="2026-01-01", seed=11, vol=0.0006)
    raw = m1.drop(columns=[c for c in m1.columns if c not in ("ts", "bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high",
                                                                "ask_low", "ask_close", "bid_volume", "ask_volume", "m1_count", "spread_mean", "spread_max", "flags")])
    bars = {"m1": m1, **{tf: _finish(aggregate(raw, tf), tf, None) for tf in ("h1", "h4", "d1")}}

    def loader(tf, start, as_of):
        b = bars[tf]
        return b[(b["ts"] >= pd.Timestamp(start)) & (b["close_ts"] <= pd.Timestamp(as_of))].reset_index(drop=True)

    def frame(item, p, start=None, end=None, bars=None):
        f = build_features(bars)
        for s in item.macro:
            f[f"m_{s}"] = np.repeat(np.random.default_rng(1).normal(0, 0.2, len(f) // 20 + 1).cumsum(), 20)[:len(f)]
        f["hours_since_fomc"] = 500.0
        return f
    monkeypatch.setattr(cat_runner, "research_frame", frame)
    return bars, loader


def test_replay_fills_entries_at_the_next_m1_open_with_stops_from_the_signal_close(tmp_path, synthetic):
    bars, loader = synthetic
    start = {"at": "2026-01-26T00:00:00+00:00", "equity": 1_000_000.0, "load_start": "2026-01-01T00:00:00+00:00"}
    run = replay(Paths(tmp_path), start, pd.Timestamp("2026-02-09 23:00", tz="UTC"), loader, lambda: [])
    j = run["journal"]
    trades = j.trades()
    assert trades, "the synthetic market should produce some trades"
    m1 = bars["m1"].set_index("ts")
    for t in trades:
        fill_bar = m1.loc[pd.Timestamp(t["signal_close_ts"])]
        expected = fill_bar["ask_open"] if t["side"] > 0 else fill_bar["bid_open"]
        assert t["fill_price"] == pytest.approx(expected)
        assert t["stop"] == pytest.approx(t["signal_close"] - t["side"] * abs(t["signal_close"] - t["stop"]))
    for t in j.trades("closed"):
        if t["exit_reason"] == "broker_stop":
            assert t["exit_price"] <= t["stop"] + 1e-9 if t["side"] > 0 else t["exit_price"] >= t["stop"] - 1e-9
    open_risk = {1: 0.0, -1: 0.0}
    assert all(e["reason"] in ("portfolio_cap", "below_min_lot", "risk_rejected", "no_stop") for e in j.events(1000, ("missed",)))
    assert not j.equity_curve().empty and open_risk


def test_evaluation_applies_the_four_rules_and_waits_for_the_date(tmp_path):
    j = Journal()
    t0 = pd.Timestamp("2026-01-01", tz="UTC")
    days = pd.date_range(t0, periods=200, freq="D")
    rng = np.random.default_rng(3)
    equity = 1_000_000 * np.cumprod(1 + rng.normal(0.0008, 0.004, len(days)))
    for d, e in zip(days, equity):
        j.sample(d.isoformat(), e, e)
    for i in range(35):
        j.add_trade(ticket=i + 1, strategy=plan.STRATEGIES[i % 7], side=1, lots=0.1, fill_price=2000.0, stop=1975.0, ref_price=2000.0,
                    spread_entry=0.2, slippage_entry=0.05, spread_exit=0.2, slippage_exit=0.0, profit=100.0, commission=0.0, swap=-1.0,
                    status="closed", fill_at=days[i].isoformat())
    gold = pd.DataFrame({"ts": days, "close_ts": days + pd.Timedelta(days=1), "close": 2000 * np.cumprod(1 + rng.normal(0, 0.01, len(days)))})
    start = {"at": t0.isoformat(), "equity": 1_000_000.0}
    early = evaluate(j, start, gold, t0 + pd.Timedelta(days=100))
    assert early["due"] is False and early["passed"] is None
    late = evaluate(j, start, gold, days[-1])
    assert late["due"] is True and late["criteria"]["net_positive"]["pass"] and late["criteria"]["costs_within_model"]["pass"]
    assert late["criteria"]["drawdown_under_10"]["value"] == pytest.approx(max_drawdown_pct(j.equity_curve()))
    assert late["passed"] == all(c["pass"] for c in late["criteria"].values())


# ------------------------------------------------------------ dashboard endpoints
def test_forward_endpoints_show_readiness_refuse_a_bad_start_and_clear_alerts(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from quantresearch.api.server import create_app
    from quantresearch.api.services import Services

    monkeypatch.setenv("QR_FORWARD_TEST", "0")
    monkeypatch.setattr(plan, "doc_commit", lambda: "abc123")
    svc = Services(Paths(tmp_path))
    b, fake, _, _ = demo_broker(2)
    svc._forward = ForwardTest(svc.p, broker=b)
    client = TestClient(create_app(svc))
    body = client.get("/api/forward").json()
    data = body.get("data", body)
    assert data["start"] is None and data["readiness"]["ready"] is False and data["readiness"]["checks"]["demo_account"]["ok"] is False
    r = client.post("/api/forward/start")
    assert r.status_code == 409 and "demo_account" in r.text and fake.sent == []
    svc._forward._refused({"account_type": "real"})
    svc._cache.pop("forward_status", None)
    alert = client.get("/api/forward").json()
    assert (alert.get("data", alert))["alert"]["account_type"] == "real"
    assert client.post("/api/forward/clear-alert", json={"note": "test"}).status_code == 200
