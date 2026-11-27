"""Risk baselines belong to one account, and every breaker trip is reported with the numbers behind it.

The incident: the paper account (100,000) set the peak, then MT5 (500) was attached and the first reading
computed a 99.50% drawdown, locking trading out indefinitely.
"""
from datetime import datetime, timedelta, timezone

import pytest

from quantresearch.execution.broker import PaperBroker, account_key
from quantresearch.risk.engine import RiskEngine, RiskLimits, RiskState

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


def engine(tmp_path=None):
    events = []
    state = RiskState(tmp_path / "risk.sqlite") if tmp_path else RiskState()
    return RiskEngine(RiskLimits(), state, on_event=lambda e, d: events.append((e, d))), events


def test_switching_from_paper_to_mt5_resets_the_baseline_instead_of_locking_out():
    r, events = engine()
    r.observe(100_000, NOW, "paper")
    view = r.observe(500, NOW + timedelta(minutes=5), "mt5:1@Demo")
    assert view["drawdown_pct"] == 0.0 and view["peak_equity"] == 500
    assert r.lockout_until() is None
    reset = [d for e, d in events if e == "risk_baseline_reset"][-1]
    assert reset["reason"] == "account changed" and reset["previous"]["account"] == "paper" and reset["previous"]["peak_equity"] == 100_000


def test_without_an_account_the_old_behaviour_and_breaker_remain():
    r, events = engine()
    r.observe(100_000, NOW)
    view = r.observe(500, NOW + timedelta(minutes=5))
    assert view["drawdown_pct"] == pytest.approx(99.5)
    assert r.lockout_until() == datetime(9999, 1, 1, tzinfo=timezone.utc)
    trip = [d for e, d in events if e == "risk_lockout_set" and d["kind"] == "drawdown"][0]
    assert trip["equity"] == 500 and trip["peak_equity"] == 100_000 and trip["peak_at"] == NOW.isoformat()


def test_drawdown_on_the_same_account_still_locks_out_and_is_logged_once():
    r, events = engine()
    r.observe(1_000, NOW, "mt5:1@Demo")
    for i in range(3):
        r.observe(850, NOW + timedelta(days=1, minutes=i), "mt5:1@Demo")
    assert r.lockout_until() == datetime(9999, 1, 1, tzinfo=timezone.utc)
    trips = [d for e, d in events if e == "risk_lockout_set" and d["kind"] == "drawdown"]
    assert len(trips) == 1 and trips[0]["account"] == "mt5:1@Demo" and trips[0]["equity"] == 850


def test_a_stored_peak_without_an_account_is_rebased_and_an_existing_lockout_is_kept(tmp_path):
    old = RiskEngine(RiskLimits(), RiskState(tmp_path / "risk.sqlite"))
    old.observe(1087.43, NOW)
    old.observe(500, NOW + timedelta(minutes=1))  # The legacy state: 54% drawdown lockout, no account recorded.
    assert old.lockout_until() is not None
    r, events = engine(tmp_path)
    view = r.observe(500, NOW + timedelta(minutes=2), "mt5:1@Demo")
    assert view["peak_equity"] == 500 and view["drawdown_pct"] == 0.0
    assert r.lockout_until() == datetime(9999, 1, 1, tzinfo=timezone.utc)  # A person still clears it.
    reset = [d for e, d in events if e == "risk_baseline_reset"][0]
    assert reset["reason"] == "first reading tied to an account" and reset["previous"]["peak_equity"] == 1087.43
    assert "Drawdown" in reset["lockout_kept"]


def test_the_same_account_keeps_its_peak_across_restarts(tmp_path):
    r, _ = engine(tmp_path)
    r.observe(600, NOW, "mt5:1@Demo")
    r2, events = engine(tmp_path)
    assert r2.observe(580, NOW + timedelta(hours=1), "mt5:1@Demo")["peak_equity"] == 600
    assert not [e for e, _ in events if e == "risk_baseline_reset"]


def test_clearing_then_tripping_again_is_logged_again():
    r, events = engine()
    r.observe(1_000, NOW, "a")
    r.observe(850, NOW + timedelta(minutes=1), "a")
    r.clear_lockout()
    r.observe(850, NOW + timedelta(minutes=2), "a")
    assert len([1 for e, d in events if e == "risk_lockout_set" and d["kind"] == "drawdown"]) == 2


def test_paper_broker_reports_its_account_and_unknown_brokers_report_none(tmp_path):
    assert account_key(PaperBroker(tmp_path / "p.sqlite")) == "paper"
    assert account_key(object()) is None
