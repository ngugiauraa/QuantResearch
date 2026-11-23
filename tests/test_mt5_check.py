import io
from types import SimpleNamespace

import pytest

from quantresearch.execution import mt5_adapter, mt5_check
from quantresearch.execution.broker import BrokerError, PaperBroker
from quantresearch.execution.mt5_adapter import Mt5Broker
from quantresearch.execution.secrets import Mt5Credentials
from test_risk_execution import FakeMt5  # The terminal stand-in the adapter tests already use.


class CheckMt5(FakeMt5):
    """FakeMt5 plus the extra reads the check makes. order_send fails the test if it is ever called."""
    TIMEFRAME_H1 = 16385

    def __init__(self, trade_mode=0, symbol_known=True, bars=True):
        super().__init__(trade_mode)
        self.symbol_known, self.bars = symbol_known, bars
        self.rate_calls = []

    def account_info(self):
        return SimpleNamespace(login=5012345, server="Demo-Server", balance=1000.0, equity=1010.0, margin_free=900.0, currency="USD",
                               leverage=500, trade_mode=self.trade_mode, trade_allowed=True)

    def symbol_info(self, s):
        return super().symbol_info(s) if self.symbol_known else None

    def symbols_get(self, *a):
        return [SimpleNamespace(name=f"SYM{i}") for i in range(25)]

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        self.rate_calls.append((symbol, timeframe, start, count))
        if not self.bars:
            return None
        return [{"time": 1_700_000_000 + 3600 * i, "open": 2000.0 + i, "high": 2002.0 + i, "low": 1999.0 + i, "close": 2001.0 + i,
                 "tick_volume": 100 + i} for i in range(count)]

    def order_send(self, request):
        raise AssertionError("the check must never place an order")


def run(fake=None, symbol="XAUUSD", credentials=None):
    fake = fake or CheckMt5()
    broker = Mt5Broker(module=fake, credentials=credentials, sleep=lambda s: None, max_reconnects=1)
    out = io.StringIO()
    code = mt5_check.run_check(broker, symbol, out)
    return code, out.getvalue(), fake, broker


def test_pass_prints_account_quote_and_five_closed_bars_and_places_no_orders():
    code, text, fake, broker = run()
    assert code == 0 and "PASS" in text and "FAIL" not in text
    for expected in ("5012345", "Demo-Server", "demo", "1000.00 USD", "1:500", "bid     2000.0", "ask     2000.3", "300 points", "$0.300 per ounce"):
        assert expected in text, expected
    assert sum(1 for line in text.splitlines() if line.startswith("  20") or line.startswith("  2023")) == 5
    assert fake.rate_calls == [("XAUUSD", fake.TIMEFRAME_H1, 1, 5)]  # Start at 1: the forming bar is skipped.
    assert broker.orders_enabled is False and fake.sent == [] and fake.up is False  # Disconnected afterwards.


def test_real_account_gets_a_warning_and_the_password_is_never_printed():
    code, text, *_ = run(CheckMt5(trade_mode=2), credentials=Mt5Credentials(5012345, "hunter2", "Demo-Server"))
    assert code == 0 and "real" in text and "WARNING" in text and "hunter2" not in text


def test_connection_failure_is_a_clear_fail():
    fake = CheckMt5()
    fake.init_ok = False
    code, text, *_ = run(fake)
    assert code == 1 and "FAIL" in text and "MT5 said" in text and "PASS" not in text


def test_a_terminal_not_connected_to_the_server_fails():
    fake = CheckMt5()
    fake.terminal_info = lambda: SimpleNamespace(connected=False)
    code, text, *_ = run(fake)
    assert code == 1 and "trade server" in text


def test_unknown_symbol_lists_the_first_ten_available():
    code, text, *_ = run(CheckMt5(symbol_known=False), symbol="GOLD.x")
    assert code == 1 and "GOLD.x" in text and "--symbol" in text
    listed = text.split("First symbols available: ")[1].split("\n")[0].split(", ")
    assert listed == [f"SYM{i}" for i in range(10)]


def test_missing_bars_fail():
    code, text, *_ = run(CheckMt5(bars=False))
    assert code == 1 and "no H1 bars" in text


def test_missing_package_fails_cleanly(monkeypatch):
    def boom():
        raise BrokerError("The MetaTrader5 package is not installed")

    monkeypatch.setattr(mt5_adapter, "_import_mt5", boom)
    out = io.StringIO()
    assert mt5_check.run_check(Mt5Broker(), "XAUUSD", out) == 1
    assert "not installed" in out.getvalue() and "pip install MetaTrader5" in out.getvalue()


def test_main_runs_end_to_end_with_a_stand_in_terminal(monkeypatch, capsys):
    fake = CheckMt5()
    monkeypatch.setattr(mt5_check, "load_mt5_credentials", lambda: None)
    monkeypatch.setattr(mt5_check, "Mt5Broker", lambda **kw: Mt5Broker(module=fake, sleep=lambda s: None, **kw))
    assert mt5_check.main(["--symbol", "XAUUSD"]) == 0
    text = capsys.readouterr().out
    assert "terminal's saved login" in text and "PASS" in text and fake.sent == []


# ---------------------------------------------------------------- health block
def snapshot(fake=None, real_name=False, **kw):
    fake = fake or CheckMt5()
    if real_name:
        fake.__name__ = "MetaTrader5"
    broker = Mt5Broker(module=fake, sleep=lambda s: None, **kw)
    broker.connect()
    return mt5_check.health_snapshot(broker), fake, broker


def test_health_is_not_configured_for_the_paper_broker():
    snap = mt5_check.health_snapshot(PaperBroker())
    assert snap == {"status": "not_configured", "account_type": None, "balance": None, "symbol": "XAUUSD", "bid": None, "ask": None}


def test_health_reports_mock_for_a_stand_in_terminal_with_live_fields():
    snap, *_ = snapshot()
    assert snap == {"status": "mock", "account_type": "demo", "balance": 1000.0, "symbol": "XAUUSD", "bid": 2000.0, "ask": 2000.3}


def test_health_connected_real_terminal_and_real_account_type():
    snap, *_ = snapshot(CheckMt5(trade_mode=2), real_name=True)
    assert snap["status"] == "connected" and snap["account_type"] == "real"


def test_health_disconnected_has_no_account_fields_and_never_reconnects():
    _, fake, broker = snapshot(real_name=True)
    fake.up = False
    calls = fake.init_calls
    snap = mt5_check.health_snapshot(broker)
    assert snap["status"] == "disconnected" and snap["balance"] is None and snap["bid"] is None
    assert fake.init_calls == calls  # A status poll must not trigger the adapter's reconnect-and-sleep loop.


def test_health_never_raises_when_the_terminal_misbehaves():
    _, fake, broker = snapshot(real_name=True)
    fake.terminal_info = lambda: (_ for _ in ()).throw(RuntimeError("terminal gone"))
    assert mt5_check.health_snapshot(broker)["status"] == "disconnected"
    fake.terminal_info = lambda: SimpleNamespace(connected=True)
    fake.account_info = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    assert mt5_check.health_snapshot(broker)["status"] == "connected"
