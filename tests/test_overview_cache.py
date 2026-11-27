"""The overview's slow parts are computed once a minute and shared, so polling pages cannot pile up work."""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from quantresearch.api.server import create_app
from quantresearch.api.services import Services
from quantresearch.core.config import Paths


@pytest.fixture()
def svc(tmp_path):
    return Services(Paths(tmp_path))


def test_cached_reuses_a_fresh_result_and_recomputes_after_ttl(svc):
    calls = []
    compute = lambda: (calls.append(1), len(calls))[1]
    assert svc.cached("k", 60, compute) == 1 and svc.cached("k", 60, compute) == 1
    assert svc.cached("k", 0, compute) == 2
    assert len(calls) == 2


def test_concurrent_callers_share_one_computation(svc):
    calls, results = [], []

    def slow():
        calls.append(1)
        time.sleep(0.3)
        return "value"

    threads = [threading.Thread(target=lambda: results.append(svc.cached("slow", 60, slow))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1 and results == ["value"] * 8


def test_a_failing_computation_is_not_cached(svc):
    def boom():
        raise RuntimeError("no bars")

    with pytest.raises(RuntimeError):
        svc.cached("x", 60, boom)
    assert svc.cached("x", 60, lambda: "ok") == "ok"


def test_overview_computes_signals_once_across_polls(svc, monkeypatch):
    calls = []
    monkeypatch.setattr(svc, "recent_signals", lambda: (calls.append(1), [])[1])
    client = TestClient(create_app(svc))
    for _ in range(3):
        assert client.get("/api/overview").status_code == 200
    assert len(calls) == 1
