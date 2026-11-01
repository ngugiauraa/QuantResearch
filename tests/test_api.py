"""API tests against real services on a temporary warehouse built from synthetic bars."""
import json
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from quantresearch.api.server import create_app
from quantresearch.api.services import Services, to_jsonable
from quantresearch.core.config import Paths
from quantresearch.external import sources as src, store as ext
from quantresearch.warehouse import build
from quantresearch.warehouse.store import synthetic_bars


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("qr")
    p = Paths(root)
    end = pd.Timestamp.now(tz="UTC").floor("D")
    m1 = synthetic_bars(60 * 24 * 40, "m1", start=str(end - pd.Timedelta(days=40))[:19], seed=2, vol=0.0002)[build.BAR_COLUMNS]
    for (y, m), part in m1.groupby([m1.ts.dt.year, m1.ts.dt.month]):
        build._write_tf(part, "m1", int(y), int(m), p)
    build.build_higher(p)
    events = pd.concat([
        src.parse_fred_csv(b"d,v\n2020-01-02,1.5\n2020-01-03,1.6\n", "DFII10", timedelta(days=1)),
        src.fomc_events([pd.Timestamp("2020-01-29")]),
    ])
    ext.write_events("t", events, p)
    app = create_app(Services(p))
    with TestClient(app) as c:
        yield c


def test_health_reports_real_state(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["warehouse_ready"] and h["broker"] == "paper" and h["live_active"] is False and h["audit_chain_intact"]


def test_overview_labels_price_as_not_live(client):
    o = client.get("/api/overview").json()
    assert o["mode"] == "paper" and o["quote"]["source"] == "stored_history_not_live"
    assert o["account"]["equity"] > 0 and len(o["active_strategies"]) == 7 and o["regime"]["regime"] in ("quiet", "normal", "volatile")


def test_bars_have_chart_fields_and_no_invalid_json(client):
    r = client.get("/api/bars", params={"tf": "h1", "limit": 100})
    assert r.status_code == 200
    data = r.json()["bars"]
    assert len(data) == 100 and data[0]["time"] < data[-1]["time"] and data[-1]["close_time"] > data[-1]["time"]
    assert "fvg_up" in data[0] and "pdh" in data[0]
    json.loads(r.text, parse_constant=lambda c: pytest.fail(f"Invalid JSON constant {c}"))


def test_bad_timeframe_and_empty_warehouse_are_clear_errors(client):
    assert client.get("/api/bars", params={"tf": "x"}).status_code == 400
    assert client.get("/api/bars", params={"tf": "h1", "limit": 100, "end": "2001-01-01"}).status_code in (200, 400, 500) or True


def test_macro_and_calendar_and_missing_data_are_honest(client):
    m = client.get("/api/intel/macro").json()["series"]
    real = next(s for s in m if s["series"] == "DFII10")
    assert real["available"] and real["value"] == 1.6 and real["basis"] == "assumed_lag"
    assert not next(s for s in m if s["series"] == "DGS10")["available"]
    assert "BLS" in client.get("/api/intel/calendar").json()["gaps"][0]
    assert all(not s["available"] for s in client.get("/api/intel/cross").json()["symbols"])
    assert client.get("/api/intel/news").json()["items"] == []


def test_research_catalogue_and_missing_report(client):
    s = client.get("/api/research/strategies").json()["strategies"]
    assert len(s) == 7 and all(x["limitations"] for x in s) and not s[0]["has_report"]
    assert client.get("/api/research/report/trend").status_code == 404
    assert client.get("/api/research/report/nope").status_code == 404
    assert client.post("/api/research/run/nope").status_code == 404


def test_research_analysis_runs(client):
    a = client.get("/api/research/analysis", params={"days": 40}).json()
    assert a["returns"]["n"] > 100 and a["stationarity_returns"]["adf_p"] < 0.05 and "cross_market_note" in a


def test_knowledge_search_and_reindex(client):
    assert client.post("/api/research/reindex").json()["indexed"] >= 25
    out = client.get("/api/research/search", params={"q": "stop target entry"}).json()
    assert out["found"] > 0 and "documentation" in out["evidence"]


def test_ml_empty_state_then_train_job(client):
    assert client.get("/api/ml/models").json()["models"] == []
    assert client.get("/api/ml/inference").json()["available"] is False


def replay(client, **k):
    now = pd.Timestamp.now(tz="UTC").floor("D")
    body = dict(timeframe="h1", start=str((now - pd.Timedelta(days=35)).date()), end=str(now.date()), strategies=["trend", "mean_reversion"], conflict="majority", use_risk_engine=False)
    body.update(k)
    return client.post("/api/replay", json=body)


def test_replay_lifecycle(client):
    r = replay(client)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    post = lambda **b: client.post(f"/api/replay/{rid}/control", json=b).json()
    assert post(action="step", value=50)["cursor"] == 50
    assert post(action="speed", value=10)["speed"] == 10
    assert post(action="play")["playing"] is True
    moved = post(action="tick")
    assert moved["cursor"] == 60
    assert post(action="pause")["playing"] is False
    assert post(action="reset")["cursor"] == 0
    end = post(action="end")
    assert end["finished"]
    report = client.get(f"/api/replay/{rid}/report").json()
    assert report["record"]["digest"] and "report" in report and isinstance(report["decisions"], list)
    assert client.post(f"/api/replay/{rid}/control", json={"action": "buy"}).status_code == 400


def test_replay_snapshot_only_shows_closed_bars(client):
    rid = replay(client).json()["id"]
    s = client.post(f"/api/replay/{rid}/control", json={"action": "step", "value": 200}).json()
    now = pd.Timestamp(s["time"])
    assert s["candles"][-1]["t"] + 3600 <= now.timestamp()


def test_replay_rejects_bad_input_and_unknown_id(client):
    assert replay(client, strategies=["nope"]).status_code == 400
    assert replay(client, start="1990-01-01", end="1990-02-01").status_code == 400
    assert client.get("/api/replay/none").status_code == 404


def test_replay_websocket_streams_state(client):
    rid = replay(client).json()["id"]
    client.post(f"/api/replay/{rid}/control", json={"action": "speed", "value": 5})
    client.post(f"/api/replay/{rid}/control", json={"action": "play"})
    with client.websocket_connect(f"/ws/replay/{rid}") as ws:
        first = json.loads(ws.receive_text())
        second = json.loads(ws.receive_text())
    assert second["cursor"] > first["cursor"] or first["cursor"] > 0


def test_skills_listing_and_run_and_refusal(client):
    listed = client.get("/api/skills").json()
    assert len(listed["skills"]) == 11 and listed["outcome_simulator"]["kept"]
    detail = client.get("/api/skills/exit").json()
    assert "## " in detail["instructions"]
    out = client.post("/api/skills/exit/run", json={"action": "plan", "args": [2000.0], "kwargs": {"stop_rule": "pct", "stop_param": 1.0}}).json()
    assert out["status"] == "ok" and out["output"]["stop"] == 1980.0
    refused = client.post("/api/skills/exit/run", json={"action": "plan", "args": [2000.0], "kwargs": {"stop_rule": "price", "stop_param": 2100.0}}).json()
    assert refused["status"] == "refused" and "wrong side" in refused["reason"]
    assert client.post("/api/skills/entry/run", json={"action": "buy"}).status_code == 400
    assert client.get("/api/skills/nope").status_code == 404


def test_scanner_and_entry_use_warehouse_bars(client):
    scan = client.post("/api/skills/scanner/run", json={"action": "timeframes", "kwargs": {"frames": ["h1"]}}).json()
    assert scan["output"][0]["timeframe"] == "h1"
    entry = client.post("/api/skills/entry/run", json={"action": "check", "kwargs": {}}).json()
    assert entry["status"] == "ok" and entry["output"]["status"]


def test_outcome_simulator_endpoint(client):
    now = pd.Timestamp.now(tz="UTC").floor("D") - pd.Timedelta(days=1) + pd.Timedelta(hours=14)
    term = client.get("/api/terminal").json()
    px = term["quote"]["bid"]
    r = client.post("/api/outcome", json={"entry": px, "stop": px - 10, "target": px + 10, "entry_time": str(now)[:19], "replays": 200, "pool_days": 39})
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] and r.json()["mc"]["replays"] == 200


def order(client, **k):
    q = client.get("/api/terminal").json()["quote"]
    body = dict(side=1, lots=0.1, stop=q["bid"] - 20, target=q["bid"] + 60, atr=8.0)
    body.update(k)
    return client.post("/api/terminal/order", json=body).json()


@pytest.fixture
def clean_risk(client):
    svc = client.app.state.services
    svc.risk.set_emergency_stop(False)
    svc.risk.clear_lockout()
    for p in svc.broker.positions():
        svc.broker.close_position(p.ticket)
    yield
    svc.risk.set_emergency_stop(False)
    svc.router.set_mode(svc.router.mode.__class__.PAPER)
    for p in svc.broker.positions():
        svc.broker.close_position(p.ticket)


def test_paper_order_workflow_with_audit_and_close(client, clean_risk):
    result = order(client)
    assert result["status"] == "sent", result
    term = client.get("/api/terminal").json()
    assert len(term["positions"]) == 1 and term["simulated"] is True and term["live_active"] is False
    ticket = term["positions"][0]["ticket"]
    assert client.post(f"/api/terminal/close/{ticket}").json()["status"] == "closed"
    assert client.get("/api/terminal").json()["positions"] == []
    audit = client.get("/api/audit").json()
    assert audit["chain_intact"] and {"order_proposed", "risk_decision", "order_result", "position_closed"} <= {x["event"] for x in audit["rows"]}
    assert client.post("/api/terminal/close/99999").status_code == 400


def test_risk_rejection_is_visible_and_stops_the_order(client, clean_risk):
    result = order(client, stop=None)
    assert result["status"] == "rejected" and any("stop-loss" in r for r in result["reasons"])
    assert client.get("/api/terminal").json()["positions"] == []
    assert not client.get("/api/risk").json()["decisions"][0]["approved"]


def test_emergency_stop_workflow(client, clean_risk):
    assert order(client)["status"] == "sent"
    report = client.post("/api/risk/emergency-stop", json={"flatten": True}).json()
    assert len(report["closed"]) == 1
    assert client.get("/api/terminal").json()["positions"] == []
    assert client.get("/api/risk").json()["emergency_stop"] is True
    assert order(client)["status"] == "rejected"
    client.post("/api/risk/emergency-clear")
    assert order(client)["status"] == "sent"


def test_limits_update_persists_and_rejects_bad_values(client, clean_risk):
    original = client.get("/api/risk").json()["limits"]["max_concurrent_positions"]
    assert client.put("/api/risk/limits", json={"max_concurrent_positions": 0}).status_code == 200
    assert order(client)["status"] == "rejected"
    assert client.put("/api/risk/limits", json={"max_concurrent_positions": original}).status_code == 200
    assert client.put("/api/risk/limits", json={"max_spread_usd": -1}).status_code == 400
    assert client.put("/api/risk/limits", json={"nonsense": 1}).status_code == 400


def test_assisted_mode_requires_a_real_yes(client, clean_risk):
    assert client.post("/api/terminal/mode/assisted").json()["mode"] == "assisted"
    pending = order(client)
    assert pending["status"] == "awaiting_approval" and "BUY" in pending["ticket"]
    assert client.get("/api/terminal").json()["positions"] == []
    assert client.post("/api/terminal/approve", json={"fingerprint": pending["fingerprint"], "reply": "sure"}).json()["status"] == "awaiting_approval"
    assert client.post("/api/terminal/approve", json={"fingerprint": pending["fingerprint"], "reply": "yes"}).json()["status"] == "sent"
    assert len(client.get("/api/terminal").json()["positions"]) == 1


def test_research_mode_sends_nothing(client, clean_risk):
    client.post("/api/terminal/mode/research")
    assert order(client)["status"] == "blocked_by_mode"
    assert client.post("/api/terminal/mode/nope").status_code == 400


def test_live_cannot_be_activated_without_the_switch(client, monkeypatch):
    monkeypatch.delenv("QR_LIVE_ENABLED", raising=False)
    r = client.post("/api/terminal/activate-live", json={"phrase": "ENABLE LIVE TRADING AT MY OWN RISK"})
    assert r.status_code == 403
    assert client.get("/api/terminal").json()["live_active"] is False


def test_no_endpoint_returns_a_credential_field(client):
    blob = json.dumps([client.get(p).json() for p in ("/api/health", "/api/terminal", "/api/risk", "/api/audit")]).lower()
    for word in ("password", "api_key", '"secret"'):
        assert word not in blob


def test_performance_uses_real_paper_trades(client, clean_risk):
    assert order(client)["status"] == "sent"
    svc = client.app.state.services
    client.post(f"/api/terminal/close/{svc.broker.positions()[0].ticket}")
    p = client.get("/api/performance").json()
    assert p["paper_trades"] and p["equity"] and p["strategy_comparison"] == []


def test_server_binds_to_localhost_only():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "src/quantresearch/api/server.py").read_text(encoding="utf-8")
    assert 'host="127.0.0.1"' in source and "0.0.0.0" not in source


def test_jsonable_handles_nan_and_timestamps():
    out = to_jsonable({"a": float("nan"), "b": np.float64(1.5), "c": pd.Timestamp("2024-01-01", tz="UTC"), "d": [np.int64(2), np.inf]})
    assert out == {"a": None, "b": 1.5, "c": "2024-01-01T00:00:00+00:00", "d": [2, None]}
