"""With MT5 attached, the overview shows the broker's live quote, not the last stored bar."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from quantresearch.api.server import create_app
from quantresearch.api.services import Services
from quantresearch.core.config import Paths
from quantresearch.execution.mt5_adapter import Mt5Broker
from test_risk_execution import FakeMt5


class Terminal(FakeMt5):
    def account_info(self):
        return SimpleNamespace(**vars(super().account_info()), login=1, server="Demo")


@pytest.fixture()
def svc(tmp_path):
    return Services(Paths(tmp_path))


def attach(svc, fake):
    broker = Mt5Broker(module=fake, sleep=lambda s: None, max_reconnects=1)
    broker.connect()
    svc.broker = broker
    return TestClient(create_app(svc))


def test_overview_quote_is_the_brokers_live_quote(svc):
    quote = attach(svc, Terminal()).get("/api/overview").json()["quote"]
    assert quote["source"] == "mt5" and quote["bid"] == 2000.0 and quote["ask"] == 2000.3


def test_overview_falls_back_to_the_labelled_stored_price_when_the_broker_quote_fails(svc):
    class NoTick(Terminal):
        def symbol_info_tick(self, s):
            return None

    quote = attach(svc, NoTick()).get("/api/overview").json()["quote"]
    assert quote["source"] in ("none", "stored_history_not_live")


def test_paper_overview_is_unchanged(svc):
    quote = TestClient(create_app(svc)).get("/api/overview").json()["quote"]
    assert quote["source"] in ("none", "stored_history_not_live")
