"""Dashboard endpoints for the strategy catalogue: list, run one, queue, run all queued."""
import time

import pytest
from fastapi.testclient import TestClient

from quantresearch.api.server import create_app
from quantresearch.api.services import Services
from quantresearch.core.config import Paths
from quantresearch.research import catalogue as runner
from quantresearch.strategies.catalogue import CATALOGUE


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(runner, "run_subprocess", lambda name, p: ran.append(name) or None)
    svc = Services(Paths(tmp_path))
    return TestClient(create_app(svc)), svc, ran


def wait_for(cond, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_list_shows_every_strategy_with_its_rules(setup):
    client, _, _ = setup
    body = client.get("/api/catalogue").json()
    assert len(body["strategies"]) == len(CATALOGUE)
    first = body["strategies"][0]
    assert first["entry"] and first["exit"] and first["stop"] and first["source"] and first["report"] is None and first["queue"] is None


def test_run_one_goes_to_the_worker(setup):
    client, svc, ran = setup
    name = next(iter(CATALOGUE))
    assert client.post(f"/api/catalogue/run/{name}").json()["status"] == "pending"
    assert wait_for(lambda: runner.queue_state(svc.p).get(name, {}).get("status") == "done")
    assert ran == [name]


def test_queued_strategies_wait_until_run_all_queued(setup):
    client, svc, ran = setup
    names = list(CATALOGUE)[:3]
    client.post("/api/catalogue/queue", json={"names": names})
    client.post("/api/catalogue/dequeue", json={"names": [names[2]]})
    svc.catalogue_kick()
    time.sleep(0.3)
    assert ran == []  # Queued, not started.
    assert client.post("/api/catalogue/run-queued").json()["started"] == 2
    assert wait_for(lambda: len(ran) == 2)
    assert sorted(ran) == sorted(names[:2])


def test_queue_all_not_yet_run(setup):
    client, svc, _ = setup
    queued = client.post("/api/catalogue/queue", json={"all_unrun": True}).json()["queued"]
    assert set(queued) == set(CATALOGUE)


def test_unknown_names_are_refused(setup):
    client, _, _ = setup
    assert client.post("/api/catalogue/run/nope").status_code == 404
    assert client.get("/api/catalogue/report/nope").status_code == 404
