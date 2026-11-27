"""The chart's forming candle: the broker's current M1 bar in mid prices, read-only, MT5 only."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from quantresearch.api.server import create_app
from quantresearch.api.services import Services
from quantresearch.core.config import Paths
from quantresearch.execution.mt5_adapter import Mt5Broker
from test_overview_quote import Terminal

RATE = np.dtype([("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"), ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8")])


class Forming(Terminal):
    TIMEFRAME_M1, TIMEFRAME_H1, TIMEFRAME_D1 = 1, 16385, 16408

    def __init__(self, rows=((1795806060, 1999.0, 2001.0, 1998.5, 2000.0),)):
        super().__init__()
        self.rows = rows
        self.asked = []

    def copy_rates_from_pos(self, symbol, tf, start, count):
        self.asked.append((symbol, tf, start, count))
        return np.array([(t, o, h, l, c, 5, 300, 0) for t, o, h, l, c in self.rows], dtype=RATE)


@pytest.fixture()
def svc(tmp_path):
    return Services(Paths(tmp_path))


def attach(svc, fake):
    broker = Mt5Broker(module=fake, sleep=lambda s: None, max_reconnects=1)
    broker.connect()
    svc.broker = broker
    svc.live.broker = broker
    return TestClient(create_app(svc))


def test_forming_bar_is_the_brokers_current_minute_in_mid_prices(svc):
    fake = Forming()
    body = attach(svc, fake).get("/api/bars/forming").json()
    assert body["live"] is True and body["source"] == "mt5"
    # Bid bar plus half the live spread (2000.3 - 2000.0) / 2.
    assert body["bar"] == {"time": 1795806060, "open": pytest.approx(1999.15), "high": pytest.approx(2001.15), "low": pytest.approx(1998.65), "close": pytest.approx(2000.15)}
    assert fake.asked == [("XAUUSD", 1, 0, 1)]  # Position 0, one bar: the forming minute only.


def test_paper_has_no_forming_bar(svc):
    body = TestClient(create_app(svc)).get("/api/bars/forming").json()
    assert body["live"] is False and "paper" in body["reason"]


def test_no_bar_from_the_broker_is_reported_not_raised(svc):
    body = attach(svc, Forming(rows=())).get("/api/bars/forming").json()
    assert body["live"] is False and "no forming bar" in body["reason"]


def test_a_quote_failure_is_reported(svc):
    class NoTick(Forming):
        def symbol_info_tick(self, s):
            return None

    body = attach(svc, NoTick()).get("/api/bars/forming").json()
    assert body["live"] is False and body["reason"]
