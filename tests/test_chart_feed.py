"""The Market Charts banner reflects the real feed: /api/bars says whether bars after the history come from MT5."""
import pytest
from fastapi.testclient import TestClient

from quantresearch.api.server import create_app
from quantresearch.api.services import Services
from quantresearch.features.registry import build_features
from quantresearch.warehouse import store
from quantresearch.warehouse.live import LiveBars
from test_live_bars import FakeBroker, make_warehouse


@pytest.fixture()
def setup(tmp_path):
    p = make_warehouse(tmp_path)
    svc = Services(p)
    svc.feature_window = lambda tf, limit, end=None, **kw: svc.live.load("h1").pipe(build_features).tail(limit).reset_index(drop=True)
    return svc, p


def feed(svc):
    return TestClient(create_app(svc)).get("/api/bars?tf=h1&limit=10").json()["feed"]


def test_paper_is_history_only(setup):
    svc, _ = setup
    f = feed(svc)
    assert f["live"] is False and f["state"] == "history" and "paper" in f["reason"]


def test_mt5_bars_after_the_history_show_as_live(setup):
    svc, p = setup
    svc.live = LiveBars(FakeBroker(), p)
    f = feed(svc)
    assert f["live"] is True and f["state"] == "live" and f["last_bar_from_broker"] is True
    assert f["broker_end"] > f["warehouse_end"]


def test_a_failing_feed_says_so(setup):
    svc, p = setup
    svc.live = LiveBars(FakeBroker(fail=True), p)
    f = feed(svc)
    assert f["live"] is False and f["state"] == "error" and "terminal gone" in f["error"]


def test_attached_without_newer_bars_is_waiting(setup):
    svc, p = setup
    svc.live = LiveBars(FakeBroker(n=0), p)
    f = feed(svc)
    assert f["state"] == "waiting" and f["live"] is False
