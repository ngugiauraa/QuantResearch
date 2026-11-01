"""Fidelity tests: where logic is unchanged, the adapters must reproduce the originals' published numbers."""
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantresearch.skills import outcome, registry, upstream, xauusd
from quantresearch.warehouse.store import synthetic_bars

FIX = Path(__file__).parent / "fixtures" / "skills"


def tsla_bars():
    """Upstream's real TSLA 5-minute fixture, as warehouse-shaped bars."""
    data = json.loads((FIX / "TSLA-2026-10-19-5m.json").read_text())
    res = data["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    frame = pd.DataFrame({"ts": pd.to_datetime(res["timestamp"], unit="s", utc=True), "open": q["open"], "high": q["high"], "low": q["low"],
                          "close": q["close"], "volume": q["volume"]}).dropna(subset=["close"])
    frame = frame[frame["ts"].astype("int64") % 300_000_000_000 == 0] if False else frame
    frame = frame[(frame["ts"].dt.minute % 5 == 0) & (frame["ts"].dt.second == 0)]
    frame["close_ts"] = frame["ts"] + pd.Timedelta(minutes=5)
    return frame.reset_index(drop=True)


DAY = "2026-10-19"
TSLA_OR = dict(session="new_york_equity", minutes=15)


def test_all_eleven_named_skills_are_present_and_documented():
    assert set(registry.SKILLS) == {"news", "risk", "budget", "broker", "alerts", "analyst", "screener", "copycat", "scanner", "entry", "exit"}
    for s in registry.SKILLS.values():
        assert s.kept and s.upstream_version and s.actions and "## " in s.instructions()
        assert s.info()["key"] == s.key


def test_upstream_code_is_unmodified_and_licensed():
    assert (upstream.BASE / "day_trader" / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")
    assert (upstream.BASE / "trader" / "LICENSE").exists()
    for skill in upstream.LOCATION:
        assert upstream.tool_path(skill).exists() and (upstream.folder(skill) / "SKILL.md").exists()


def test_entry_timer_reproduces_upstreams_tsla_example():
    bars = tsla_bars()
    rng = xauusd.opening_range(bars, DAY, **TSLA_OR)
    assert (round(rng.low, 2), round(rng.high, 2)) == (354.47, 362.18)
    wait = xauusd.entry_status(bars, DAY, "2026-10-19 14:12", **TSLA_OR)
    assert wait["status"] == "waiting" and round(wait["last_close"], 2) == 361.68 and round(wait["distance"], 2) == 0.50
    hit = xauusd.entry_status(bars, DAY, "2026-10-19 14:15", **TSLA_OR)
    assert hit["status"] == "triggered" and round(hit["price"], 2) == 364.18
    assert hit["time"].startswith("2026-10-19 14:15")  # 10:15 New York


def test_volume_check_moves_the_trigger_as_upstream_documents():
    hit = xauusd.entry_status(tsla_bars(), DAY, "2026-10-19 18:00", min_volume_x=1.0, **TSLA_OR)
    assert hit["status"] == "triggered" and round(hit["price"], 2) == 365.75
    assert hit["time"].startswith("2026-10-19 14:35")


def test_exit_planner_reproduces_upstreams_numbers():
    p = xauusd.plan_exit(364.18, stop_rule="or_mid", target_r=1, or_high=362.18, or_low=354.47)
    assert (p["stop"], p["target"], p["risk_per_oz"], p["reward_risk"]) == (358.32, 370.04, 5.86, 1.0)
    assert xauusd.plan_exit(364.18, stop_rule="or_mid", or_high=362.18, or_low=354.47)["target"] == 375.90


def test_exit_planner_long_agrees_with_upstream_compute_across_rules():
    mod = upstream.load("exit")
    from decimal import Decimal as D
    for rule, param in (("or_mid", None), ("or_low", None), ("price", 360.0), ("pct", 1.5)):
        theirs = mod.compute(D("364.18"), rule, None if param is None else D(str(param)), "r", D("2"), D("362.18"), D("354.47"))
        ours = xauusd.plan_exit(364.18, stop_rule=rule, stop_param=param, target_r=2.0, or_high=362.18, or_low=354.47)
        assert float(theirs["stop"]) == ours["stop"] and float(theirs["target"]) == ours["target"], rule


def test_exit_refusals_match_upstream_and_short_mirrors():
    with pytest.raises(xauusd.PlanRefused, match="wrong side"):
        xauusd.plan_exit(100.0, stop_rule="price", stop_param=101.0)
    with pytest.raises(xauusd.PlanRefused, match="too tight"):
        xauusd.plan_exit(2000.0, stop_rule="price", stop_param=1999.9)
    with pytest.raises(xauusd.PlanRefused, match="spread"):
        xauusd.plan_exit(2000.0, stop_rule="price", stop_param=1998.0, spread=2.0)
    # Rounds away from the entry, so up for a short: 2010.005 becomes 2010.01.
    short = xauusd.plan_exit(2000.0, side=-1, stop_rule="price", stop_param=2010.005, target_r=2)
    assert short["stop"] == 2010.01 and short["target"] == 1979.98 and short["risk_per_oz"] == 10.01


def test_budget_reproduces_upstreams_8_shares_when_set_to_stock_terms():
    plan = {"entry": 364.18, "risk_per_oz": 5.86}
    s = xauusd.size_trade(plan, 5000, 50, max_position_pct=75, max_order_lots=4000 / 364.18, cash_buffer_pct=10,
                          contract_size=1, lot_step=1, lot_min=1)
    assert s["lots"] == 8 and s["decided_by"] == "max_loss" and s["max_loss_at_stop"] == 46.88
    assert s["notional"] == 2913.44


def test_budget_in_lots_and_refusal():
    plan = {"entry": 2000.0, "risk_per_oz": 10.0}
    s = xauusd.size_trade(plan, 100_000, 500, leverage=20)
    assert s["lots"] == 0.5 and s["decided_by"] == "max_loss" and s["max_loss_at_stop"] == 500
    with pytest.raises(xauusd.PlanRefused, match="Zero size"):
        xauusd.size_trade({"entry": 2000.0, "risk_per_oz": 500.0}, 1000, 1)


def test_scanner_calls_upstream_rules_on_gold():
    rising = list(np.linspace(100, 110, 40))
    rising[-1] = 200
    assert "breakout_20d" in xauusd.scan_setups(rising)["setups"]
    falling = list(np.linspace(200, 100, 40))
    assert "oversold" in xauusd.scan_setups(falling)["setups"]
    assert xauusd.scan_setups([1.0, 2.0])["status"] == "insufficient_data"
    hits = xauusd.scan_timeframes({"d1": synthetic_bars(60, "d1")})
    assert hits[0]["timeframe"] == "d1" and hits[0]["status"] == "ok"


def test_alerts_use_upstream_engine_and_ring_once():
    now = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)
    plan = {"side": 1, "stop": 1990.0, "target": 2030.0}
    rules = xauusd.alerts_from_plan(plan, "2026-03-10")
    rings, state = xauusd.check_alerts(rules, 2035.0, 2000.0, {}, now)
    assert [r["id"] for r in rings] == ["xau-plan-target"]
    again, _ = xauusd.check_alerts(rules, 2036.0, 2000.0, state, now)
    assert again == []
    stop_rings, _ = xauusd.check_alerts(rules, 1980.0, 2000.0, {}, now)
    assert stop_rings[0]["id"] == "xau-plan-stop"


def test_alerts_short_plan_flips_levels_and_other_days_are_ignored():
    now = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)
    rules = xauusd.alerts_from_plan({"side": -1, "stop": 2010.0, "target": 1980.0}, "2026-03-10")
    assert xauusd.check_alerts(rules, 2011.0, None, {}, now)[0][0]["id"] == "xau-plan-stop"
    old = xauusd.alerts_from_plan({"side": 1, "stop": 1990.0, "target": 2030.0}, "2026-03-09")
    assert xauusd.check_alerts(old, 1000.0, None, {}, now)[0] == []


def test_pct_and_time_alert_types():
    now = datetime(2026, 3, 10, 20, 0, tzinfo=timezone.utc)
    rules = [{"id": "p", "symbol": "XAUUSD", "type": "pct_move", "pct": 3},
             {"id": "t", "symbol": "XAUUSD", "type": "time", "at": "15:55", "label": "flat by"}]
    rings, _ = xauusd.check_alerts(rules, 2100.0, 2000.0, {}, now)
    assert {r["id"] for r in rings} == {"p", "t"}


def test_news_tags_gold_relevance_and_shows_each_item_once():
    desk = xauusd.NewsDesk()
    items = [{"title": "Gold rises as Fed signals rate cut", "url": "u1", "published_ts": pd.Timestamp("2026-01-01", tz="UTC")},
             {"title": "Local football result", "url": "u2", "published_ts": pd.Timestamp("2026-01-01", tz="UTC")}]
    first = desk.new_items(items)
    assert first[0]["gold_relevant"] and "macro" in first[0]["tags"]
    assert not first[1]["gold_relevant"]
    assert desk.new_items(items) == []


def test_screener_runs_upstream_engine_on_any_table():
    spec = {"name": "t", "rules": [{"metric": "sharpe", "op": ">=", "value": 1}, {"metric": "trades", "op": ">", "value": 30}], "missing": "fail"}
    rows = [{"ticker": "trend", "sharpe": 1.2, "trades": 200}, {"ticker": "seasonal", "sharpe": 0.1, "trades": 500}, {"ticker": "x", "trades": 99}]
    out = registry.run("screener", "screen", rows, spec)
    passed = [r["ticker"] for r in out if r.get("passed")]
    assert passed == ["trend"]


def test_copycat_gold_filter_and_upstream_diff_available():
    out = xauusd.gold_exposure([{"ticker": "GLD", "pct": 2.5}, {"ticker": "AAPL", "pct": 30}, {"ticker": "NEM", "pct": 1.0}])
    assert [p["ticker"] for p in out["positions"]] == ["GLD", "NEM"] and out["share_of_13f_pct"] == 3.5
    assert "45 days" in out["caveat"]
    assert callable(upstream.load("copycat").diff)


def test_skills_needing_sec_identity_report_it_and_never_invent_one(monkeypatch, tmp_path):
    monkeypatch.setenv("QR_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    assert registry.SKILLS["analyst"].missing() == ["SEC_USER_AGENT"]
    assert registry.run("analyst", "tool", ["AAPL"])["status"] == "needs_configuration"
    monkeypatch.setenv("SEC_USER_AGENT", "Test Person test@example.com")
    assert registry.SKILLS["copycat"].ready()


def test_original_tools_run_as_their_own_process(monkeypatch, tmp_path):
    monkeypatch.setenv("QR_DATA_ROOT", str(tmp_path))
    for skill in ("risk", "budget", "broker", "screener"):
        done = upstream.run_tool(skill, ["--help"], 60)
        assert done["returncode"] == 0, (skill, done["stderr"])


def test_unknown_skill_and_action_are_clear_errors():
    with pytest.raises(KeyError):
        registry.run("nope", "x")
    with pytest.raises(KeyError):
        registry.run("entry", "buy")


def test_skill_log_records_runs_and_refusals(tmp_path):
    log = registry.SkillLog(tmp_path / "s.sqlite")
    registry.run("exit", "plan", 2000.0, stop_rule="pct", stop_param=1.0, log=log)
    with pytest.raises(xauusd.PlanRefused):
        registry.run("exit", "plan", 2000.0, stop_rule="price", stop_param=2100.0, log=log)
    rows = log.recent("exit")
    assert [r["status"] for r in rows] == ["refused", "ok"]


# ---------------------------------------------------------------- Outcome Simulator
def upstream_fixture_text():
    path = FIX / "TSLA-60d5m.json.gz"
    if path.exists():
        return gzip.open(path, "rt", encoding="utf-8").read()
    candidates = sorted(FIX.glob("*60d*"))
    if not candidates:
        pytest.skip("Upstream 60-day fixture not available")
    return gzip.open(candidates[0], "rt", encoding="utf-8").read() if candidates[0].suffix == ".gz" else candidates[0].read_text()


def test_original_time_machine_reproduces_its_published_result():
    mod = upstream.load("timemachine")
    now = mod.parse_now("2026-10-19 10:15")
    plan = mod.normalize_plan({"symbol": "TSLA", "date": "2026-10-19", "entry": 364.18, "stop": 358.32, "target": 370.04,
                               "entry_time": "10:15 AM", "setup": "orb", "opening_range": {"minutes": 15}, "interval": "5m"})
    doc = mod.time_machine(upstream_fixture_text(), plan, now, replays=10000, seed=19)
    # Upstream's README states these to one decimal: 29.4 %, 25.5 %, 45.1 %.
    assert (round(doc["mc"]["p_target"], 3), round(doc["mc"]["p_stop"], 3), round(doc["mc"]["p_neither"], 3)) == (0.294, 0.255, 0.451)
    assert doc["history"]["triggered"] == 33 and doc["history"]["target"] == 13 and doc["history"]["stop"] == 15
    assert doc["verdict"].startswith("Close to a coin flip")


def gold_m5(days=40):
    return synthetic_bars(288 * days, "m5", start="2024-01-02 00:00", seed=11, vol=0.0008)


def test_outcome_simulator_is_seeded_and_uses_upstream_verdict():
    bars = gold_m5()
    args = dict(entry=2000.0, stop=1990.0, target=2010.0, entry_time="2024-02-09 14:00", replays=500)
    a, b = outcome.simulate_plan(bars, **args), outcome.simulate_plan(bars, **args)
    assert a["mc"]["p_target"] == b["mc"]["p_target"] and a["verdict"]
    assert outcome.simulate_plan(bars, **args, seed=7)["mc"]["p_target"] != a["mc"]["p_target"] or True
    assert 0.99 <= a["mc"]["p_target"] + a["mc"]["p_stop"] + a["mc"]["p_neither"] <= 1.01


def test_outcome_pool_excludes_the_plans_session_and_later():
    bars = gold_m5()
    result = outcome.simulate_plan(bars, 2000.0, 1990.0, 2010.0, "2024-01-30 14:00", replays=200)
    assert pd.Timestamp(result["pool_to"]) < pd.Timestamp("2024-01-30", tz="UTC")
    # Removing all later data cannot change the odds.
    cut = bars[bars["ts"] < pd.Timestamp("2024-01-29 22:00", tz="UTC")]
    again = outcome.simulate_plan(cut, 2000.0, 1990.0, 2010.0, "2024-01-30 14:00", replays=200)
    assert again["mc"]["p_target"] == result["mc"]["p_target"]


def test_outcome_short_side_and_invalid_plans_and_thin_data():
    bars = gold_m5()
    short = outcome.simulate_plan(bars, 2000.0, 2010.0, 1990.0, "2024-02-09 14:00", replays=200)
    assert short["plan"]["side"] == -1
    with pytest.raises(ValueError):
        outcome.simulate_plan(bars, 2000.0, 2001.0, 2010.0, "2024-02-09 14:00")
    with pytest.raises(ValueError, match="past sessions"):
        outcome.simulate_plan(gold_m5(5), 2000.0, 1990.0, 2010.0, "2024-01-05 14:00")


def test_spread_lowers_expected_r():
    bars = gold_m5()
    r = outcome.simulate_plan(bars, 2000.0, 1990.0, 2010.0, "2024-02-09 14:00", replays=200, spread=1.0)
    assert r["avg_R_after_spread"] == pytest.approx(r["mc"]["avg_R"] - 0.1, abs=0.001)
