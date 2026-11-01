import ast
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from quantresearch.execution import secrets
from quantresearch.execution.broker import BrokerError, PaperBroker
from quantresearch.execution.mt5_adapter import Mt5Broker
from quantresearch.execution.router import ACTIVATION_PHRASE, AuditLog, Mode, OrderRequest, OrderRouter, is_explicit_yes
from quantresearch.risk.engine import Account, Market, Proposal, RiskEngine, RiskLimits, RiskState, simulation_gate
from quantresearch.sim.engine import Engine, EngineConfig, OrderIntent, Strategy
from quantresearch.warehouse.store import synthetic_bars

NOW = datetime(2024, 5, 7, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def good(**k):
    base = dict(side=1, lots=0.5, entry=2000.2, stop=1990.0, target=2030.0, source="test")
    base.update(k)
    return Proposal(**base)


def acct(**k):
    base = dict(equity=100_000.0, balance=100_000.0, open_lots=0.0, open_positions=0)
    base.update(k)
    return Account(**base)


def mkt(**k):
    base = dict(bid=2000.0, ask=2000.2, atr=8.0, now=NOW)
    base.update(k)
    return Market(**base)


def engine(**limits):
    return RiskEngine(RiskLimits(**limits))


# ---------------------------------------------------------------- independence
def test_risk_engine_imports_nothing_from_strategy_ml_or_skills():
    tree = ast.parse((ROOT / "src/quantresearch/risk/engine.py").read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    banned = ("strategies", "ml", "skills", "knowledge", "decision", "research")
    assert not [m for m in imports if any(part in m.split(".") for part in banned)]


def test_a_good_order_is_approved():
    d = engine().evaluate(good(), acct(), mkt())
    assert d.approved and d.lots == 0.49  # 0.5% of 100k / (10.2 x 100) = 0.49 lots
    assert d.modified and "risk_per_trade" in d.checks["size"]


# ---------------------------------------------------------------- every limit, pass and block
@pytest.mark.parametrize("change,limit_kw,reason", [
    (dict(p=dict(stop=None)), {}, "stop-loss is required"),
    (dict(p=dict(stop=2005.0)), {}, "wrong side"),
    (dict(p=dict(stop=1800.0)), {}, "Stop distance"),
    (dict(p=dict(target=2005.0)), {}, "Reward:risk"),
    (dict(p=dict(target=1995.0)), {}, "target is on the wrong side"),
    (dict(m=dict(ask=2002.0)), {}, "Spread"),
    (dict(m=dict(bid=2001.0, ask=2000.0)), {}, "Crossed"),
    (dict(m=dict(bid=None)), {}, "No live bid"),
    (dict(p=dict(entry=2005.0, stop=1995.0, target=2030.0)), {}, "moved"),
    (dict(m=dict(atr=100.0)), {}, "ATR"),
    (dict(m=dict(atr=None)), {}, "No volatility"),
    (dict(a=dict(open_positions=3)), {}, "positions already open"),
    (dict(a=dict(open_lots=5.0)), {}, "Exposure"),
    (dict(a=dict(equity=0.0)), {}, "equity"),
    (dict(a=dict(equity=None)), {}, "equity"),
    (dict(p=dict(lots=0.5)), dict(risk_per_trade_pct=0.001), "under the"),
])
def test_each_limit_blocks(change, limit_kw, reason):
    d = engine(**limit_kw).evaluate(good(**change.get("p", {})), acct(**change.get("a", {})), mkt(**change.get("m", {})))
    assert not d.approved and d.lots == 0
    assert any(reason in r for r in d.reasons), d.reasons


def test_max_order_and_exposure_reduce_rather_than_block():
    d = engine(max_order_lots=0.2).evaluate(good(), acct(), mkt())
    assert d.approved and d.lots == 0.2 and "max_order" in d.checks["size"]
    d = engine().evaluate(good(lots=2.0, stop=1999.0), acct(equity=10_000_000.0, open_lots=4.7), mkt())
    assert d.approved and d.lots == pytest.approx(0.3)


def test_trades_per_day_limit_and_daily_reset():
    r = engine(max_trades_per_day=2)
    r.observe(100_000, NOW)
    r.record_entry()
    r.record_entry()
    assert any("entries today" in x for x in r.evaluate(good(), acct(), mkt()).reasons)
    r.observe(100_000, NOW + timedelta(days=1))
    assert r.evaluate(good(), acct(), mkt(now=NOW + timedelta(days=1))).approved


def test_daily_loss_locks_out_and_survives_restart(tmp_path):
    path = tmp_path / "risk.sqlite"
    r = RiskEngine(RiskLimits(), RiskState(path))
    r.observe(100_000, NOW)
    r.observe(97_900, NOW + timedelta(hours=1))
    d = r.evaluate(good(), acct(equity=97_900), mkt())
    assert not d.approved and any("locked out" in x for x in d.reasons)
    # A restart must not clear it.
    r2 = RiskEngine(RiskLimits(), RiskState(path))
    assert not r2.evaluate(good(), acct(equity=97_900), mkt(now=NOW + timedelta(hours=2))).approved
    # It ends after the configured period.
    later = NOW + timedelta(hours=26)
    r2.observe(97_900, later)
    assert r2.evaluate(good(), acct(equity=97_900), mkt(now=later)).approved


def test_max_drawdown_locks_out_until_a_person_clears_it():
    r = engine()
    r.observe(100_000, NOW)
    for i, e in enumerate((98_500, 97_000, 95_500, 94_000, 92_500, 91_000, 89_900)):
        r.observe(e, NOW + timedelta(days=i + 1))
    far = NOW + timedelta(days=400)
    assert not r.evaluate(good(), acct(equity=89_900), mkt(now=far)).approved
    r.clear_lockout()
    assert r.evaluate(good(), acct(equity=89_900), mkt(now=far)).approved


def test_emergency_stop_blocks_entries_but_never_exits_and_persists(tmp_path):
    path = tmp_path / "risk.sqlite"
    r = RiskEngine(RiskLimits(), RiskState(path))
    r.set_emergency_stop(True, "test")
    assert not r.evaluate(good(), acct(), mkt()).approved
    exit_ = r.evaluate(Proposal(-1, 1.0, 2000.0, reduces_position=True), acct(open_lots=1.0, open_positions=1), mkt())
    assert exit_.approved and exit_.lots == 1.0
    assert RiskEngine(RiskLimits(), RiskState(path)).emergency_stop


def test_exit_cannot_exceed_or_invent_a_position():
    r = engine()
    assert r.evaluate(Proposal(-1, 5.0, 2000.0, reduces_position=True), acct(open_lots=1.0), mkt()).lots == 1.0
    assert not r.evaluate(Proposal(-1, 1.0, 2000.0, reduces_position=True), acct(open_lots=0.0), mkt()).approved


def test_exits_ignore_entry_limits_even_with_no_market_data():
    d = engine().evaluate(Proposal(-1, 1.0, 2000.0, reduces_position=True), acct(open_lots=1.0), Market(None, None, None, NOW))
    assert d.approved


def test_news_window_blocks_and_a_broken_calendar_fails_closed():
    event = NOW + timedelta(minutes=10)
    r = RiskEngine(RiskLimits(), news_schedule=lambda: [event])
    assert any("restriction" in x for x in r.evaluate(good(), acct(), mkt()).reasons)
    clear = RiskEngine(RiskLimits(), news_schedule=lambda: [NOW + timedelta(hours=5)])
    assert clear.evaluate(good(), acct(), mkt()).approved

    def broken():
        raise OSError("calendar down")

    assert any("unavailable" in x for x in RiskEngine(RiskLimits(), news_schedule=broken).evaluate(good(), acct(), mkt()).reasons)


def test_invalid_input_is_rejected():
    assert not engine().evaluate(good(side=0), acct(), mkt()).approved
    assert not engine().evaluate(good(lots=-1), acct(), mkt()).approved
    with pytest.raises(ValueError):
        RiskLimits(max_spread_usd=-1).validate()


def test_every_decision_is_recorded():
    r = engine()
    r.evaluate(good(), acct(), mkt())
    r.evaluate(good(stop=None), acct(), mkt())
    rows = r.state.recent()
    assert [x["approved"] for x in rows] == [False, True]
    assert rows[0]["reasons"]


class Spammer(Strategy):
    def on_bar(self, ctx):
        if ctx.index % 5 == 0:
            ctx.buy(1.0, stop=ctx.bar["close"] - 5, target=ctx.bar["close"] + 20, tag="spam")


def test_simulation_passes_through_the_same_risk_engine():
    bars = synthetic_bars(200)
    r = RiskEngine(RiskLimits(max_concurrent_positions=1, max_trades_per_day=3))
    e = Engine(bars, bars, Spammer(), EngineConfig(), risk_gate=simulation_gate(r, atr_of=lambda _: 5.0)).run()
    assert len(e.positions) + len(e.trades) <= 3
    assert any(x["event"] == "risk_decision" and not x["approved"] for x in e.events)


# ---------------------------------------------------------------- paper broker
def paper(tmp_path=None):
    b = PaperBroker(None if tmp_path is None else tmp_path / "paper.sqlite")
    b.on_quote(2000.0, 2000.2)
    return b


def test_paper_fill_pnl_and_stop():
    b = paper()
    r = b.market_order(1, 1.0, stop=1995.0, target=2010.0)
    assert r.ok and r.price == 2000.2
    b.on_quote(2001.0, 2001.2)
    assert b.account().equity == pytest.approx(100_000 + 80)
    hits = b.on_quote(1994.0, 1994.2)  # Gap below the stop fills at the market.
    assert hits[0].price == 1994.0 and not b.positions()
    assert b.balance == pytest.approx(100_000 - 620)
    assert b.history()[0]["reason"] == "stop"


def test_paper_short_and_target_and_pending():
    b = paper()
    b.market_order(-1, 1.0, target=1990.0)
    b.on_quote(1989.0, 1989.2)
    assert not b.positions() and b.balance > 100_000
    b.pending_order(1, 1.0, "limit", 1980.0)
    assert len(b.pending_orders()) == 1
    b.on_quote(1979.0, 1979.5)
    assert len(b.positions()) == 1 and not b.pending_orders()
    o = b.pending_order(1, 1.0, "stop", 2100.0)
    assert b.cancel_order(o.ticket).ok and not b.pending_orders()


def test_paper_state_survives_restart(tmp_path):
    b = paper(tmp_path)
    b.market_order(1, 0.5, stop=1990.0)
    again = PaperBroker(tmp_path / "paper.sqlite")
    assert len(again.positions()) == 1 and again.positions()[0].stop == 1990.0
    assert again.quote().bid == 2000.0


def test_paper_needs_a_quote():
    with pytest.raises(BrokerError):
        PaperBroker().market_order(1, 1.0)


# ---------------------------------------------------------------- router and modes
def router(mode=Mode.PAPER, tmp=None, limits=None):
    return OrderRouter(paper(), RiskEngine(limits or RiskLimits()), AuditLog(tmp), mode)


REQ = dict(side=1, lots=0.2, entry=2000.2, stop=1990.0, target=2030.0, atr=8.0, source="test")


def test_research_and_simulation_modes_send_nothing():
    for mode in (Mode.RESEARCH, Mode.SIMULATION):
        r = router(mode)
        result = r.submit(OrderRequest(**REQ))
        assert result.status == "blocked_by_mode" and not r.broker.positions()


def test_paper_mode_full_path_with_audit():
    r = router()
    result = r.submit(OrderRequest(**REQ))
    assert result.status == "sent" and len(r.broker.positions()) == 1
    events = [x["event"] for x in r.audit.rows()]
    assert {"order_proposed", "risk_decision", "order_result"} <= set(events)
    assert r.risk.state.get("trades_today") == 1


def test_risk_rejection_stops_the_order_reaching_the_broker():
    r = router()
    assert r.submit(OrderRequest(**{**REQ, "stop": None})).status == "rejected"
    assert not r.broker.positions()


def test_no_path_bypasses_risk_from_any_source():
    r = router(limits=RiskLimits(max_concurrent_positions=0))
    for source in ("ml_model", "strategy", "skill", "manual"):
        assert r.submit(OrderRequest(**{**REQ, "source": source})).status == "rejected"
    assert not r.broker.positions()


def test_assisted_needs_an_explicit_yes_for_that_order():
    r = router(Mode.ASSISTED)
    pending = r.submit(OrderRequest(**REQ))
    assert pending.status == "awaiting_approval" and "BUY" in pending.ticket and not r.broker.positions()
    assert r.approve(pending.fingerprint, "sure").status == "awaiting_approval"
    assert r.approve(pending.fingerprint, "yes to all").status == "awaiting_approval"
    assert not r.broker.positions()
    assert r.approve(pending.fingerprint, "yes").status == "sent"
    assert len(r.broker.positions()) == 1
    assert r.approve(pending.fingerprint, "yes").status == "rejected"  # One yes, one order.


def test_assisted_rechecks_risk_after_the_yes():
    r = router(Mode.ASSISTED)
    pending = r.submit(OrderRequest(**REQ))
    r.risk.set_emergency_stop(True)
    assert r.approve(pending.fingerprint, "yes").status == "rejected"
    assert not r.broker.positions()


@pytest.mark.parametrize("reply,ok", [("yes", True), ("Yes.", True), ("confirm", True), ("sure", False), ("ok", False),
                                      ("yes to all", False), ("yes buy 1 XAUUSD", True), ("yes sell xauusd", False), ("no", False)])
def test_yes_rule(reply, ok):
    assert is_explicit_yes(reply, OrderRequest(**REQ)) is ok


def test_emergency_stop_flattens_and_blocks(tmp_path):
    r = router()
    r.submit(OrderRequest(**REQ))
    r.broker.pending_order(1, 0.1, "limit", 1900.0)
    report = r.emergency_stop()
    assert len(report["closed"]) == 1 and not report["errors"]
    assert not r.broker.positions() and not r.broker.pending_orders()
    assert r.submit(OrderRequest(**REQ)).status == "rejected"


def test_audit_chain_detects_tampering_and_redacts_secrets(tmp_path):
    log = AuditLog(tmp_path / "a.sqlite")
    log.write("one", {"password": "hunter2", "nested": {"api_key": "abc", "fine": 1}})
    log.write("two", {})
    assert log.verify()
    assert "hunter2" not in json.dumps(log.rows()) and log.rows()[-1]["data"]["nested"]["fine"] == 1
    log.con.execute("UPDATE audit SET data='{}' WHERE id=1")
    log.con.commit()
    assert not log.verify()


# ---------------------------------------------------------------- MT5 with a fake terminal (no broker involved)
class FakeMt5:
    ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_REAL = 0, 2
    TRADE_ACTION_DEAL, TRADE_ACTION_PENDING, TRADE_ACTION_SLTP, TRADE_ACTION_REMOVE = 1, 5, 6, 8
    ORDER_TYPE_BUY, ORDER_TYPE_SELL, ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_SELL_LIMIT, ORDER_TYPE_BUY_STOP, ORDER_TYPE_SELL_STOP = 0, 1, 2, 3, 4, 5
    ORDER_TIME_GTC, ORDER_FILLING_IOC, ORDER_FILLING_RETURN, POSITION_TYPE_BUY, DEAL_TYPE_BUY = 0, 1, 2, 0, 0

    def __init__(self, trade_mode=0):
        self.trade_mode, self.up, self.sent, self.replies, self.init_calls = trade_mode, True, [], [], 0
        self.init_ok = True
        self.open = []

    def initialize(self, **kw):
        self.init_calls += 1
        self.kw = kw
        self.up = self.init_ok
        return self.init_ok

    def shutdown(self):
        self.up = False

    def last_error(self):
        return (1, "error")

    def symbol_select(self, *a):
        return True

    def terminal_info(self):
        return SimpleNamespace(connected=self.up)

    def account_info(self):
        return SimpleNamespace(balance=1000.0, equity=1010.0, margin_free=900.0, currency="USD", trade_mode=self.trade_mode, trade_allowed=True)

    def symbol_info_tick(self, s):
        return SimpleNamespace(bid=2000.0, ask=2000.3, time=1700000000)

    def symbol_info(self, s):
        return SimpleNamespace(digits=3, volume_min=0.01, volume_max=50.0, volume_step=0.01, trade_contract_size=100.0, point=0.001, trade_stops_level=10)

    def positions_get(self, symbol=None):
        return self.open

    def orders_get(self, symbol=None):
        return (SimpleNamespace(ticket=9, type=self.ORDER_TYPE_SELL_STOP, volume_current=0.1, price_open=1990.0, sl=0.0, tp=0.0),)

    def history_deals_get(self, a, b, group=None):
        return (SimpleNamespace(ticket=1, position_id=7, time=1, type=0, volume=0.1, price=2000.0, profit=5.0, commission=-1.0, swap=0.0, comment="x"),)

    def order_send(self, request):
        self.sent.append(dict(request))
        if self.replies:
            return self.replies.pop(0)
        return SimpleNamespace(retcode=10009, order=55, price=request.get("price", 0), volume=request.get("volume", 0), comment="done")


def mt5(trade_mode=0, **k):
    fake = FakeMt5(trade_mode)
    broker = Mt5Broker(module=fake, sleep=lambda s: None, **k)
    broker.connect()
    return broker, fake


def test_mt5_reads_map_fields():
    b, f = mt5()
    a = b.account()
    assert a.equity == 1010.0 and a.is_real is False
    assert b.quote().ask == 2000.3 and b.spread() == pytest.approx(0.3)
    assert b.symbol().lot_step == 0.01
    assert b.pending_orders()[0].kind == "stop" and b.pending_orders()[0].side == -1
    assert b.history()[0]["profit"] == 5.0
    f.open = [SimpleNamespace(ticket=7, type=1, volume=0.2, price_open=2000.0, sl=2010.0, tp=0.0, profit=1.0, comment="c")]
    p = b.positions()[0]
    assert p.side == -1 and p.stop == 2010.0 and p.target is None


def test_mt5_orders_are_off_by_default():
    b, f = mt5()
    assert not b.market_order(1, 0.1).ok and not f.sent
    assert not b.pending_order(1, 0.1, "limit", 1990).ok and not b.close_position(1).ok and not b.cancel_order(1).ok


def test_mt5_real_account_is_refused_without_activation():
    b, f = mt5(trade_mode=2)
    b.orders_enabled = True
    r = b.market_order(1, 0.1)
    assert not r.ok and "real-money" in r.message and not f.sent
    b.live_activated = True
    assert b.market_order(1, 0.1).ok


def test_mt5_order_request_fields():
    b, f = mt5()
    b.orders_enabled = True
    assert b.market_order(-1, 0.5, stop=2010.0, target=1990.0, comment="z" * 50).ok
    s = f.sent[0]
    assert s["type"] == f.ORDER_TYPE_SELL and s["price"] == 2000.0 and s["sl"] == 2010.0 and s["tp"] == 1990.0 and len(s["comment"]) == 31


def test_mt5_requote_retries_but_unknown_outcome_never_resends():
    b, f = mt5()
    b.orders_enabled = True
    f.replies = [SimpleNamespace(retcode=10004, order=0, price=0, volume=0, comment="requote")]
    assert b.market_order(1, 0.1).ok and len(f.sent) == 2
    f.sent.clear()
    f.order_send = lambda r: (f.sent.append(r), None)[1]
    assert not b.market_order(1, 0.1).ok and len(f.sent) == 1


def test_mt5_rejection_reports_the_code():
    b, f = mt5()
    b.orders_enabled = True
    f.replies = [SimpleNamespace(retcode=10019, order=0, price=0, volume=0, comment="no money")]
    r = b.market_order(1, 0.1)
    assert not r.ok and r.code == 10019 and r.message == "no money"


def test_mt5_modify_close_cancel():
    b, f = mt5()
    b.orders_enabled = True
    f.open = [SimpleNamespace(ticket=7, type=0, volume=0.2, price_open=2000.0, sl=1990.0, tp=0.0, profit=1.0, comment="")]
    assert b.modify_position(7, target=2050.0).ok and f.sent[-1]["sl"] == 1990.0 and f.sent[-1]["tp"] == 2050.0
    assert b.close_position(7).ok and f.sent[-1]["type"] == f.ORDER_TYPE_SELL and f.sent[-1]["position"] == 7
    assert b.cancel_order(9).ok and f.sent[-1]["action"] == f.TRADE_ACTION_REMOVE
    assert not b.modify_position(99).ok


def test_mt5_reconnects_after_a_drop_and_gives_up_cleanly():
    b, f = mt5()
    f.up = False
    assert b.account().balance == 1000.0 and f.init_calls >= 2
    f.up, f.init_ok = False, False
    with pytest.raises(BrokerError):
        b.account()


def test_router_refuses_live_modes_on_mt5_without_activation():
    b, f = mt5()
    r = OrderRouter(b, RiskEngine(RiskLimits()), mode=Mode.RESEARCH)
    with pytest.raises(PermissionError):
        r.set_mode(Mode.AUTOMATED)
    with pytest.raises(PermissionError):
        r.set_mode(Mode.ASSISTED)
    with pytest.raises(PermissionError):
        r.set_mode(Mode.PAPER)
    assert r.submit(OrderRequest(**REQ)).status == "blocked_by_mode" and not f.sent


def test_live_activation_needs_switch_and_phrase_and_does_not_persist(monkeypatch):
    b, f = mt5()
    r = OrderRouter(b, RiskEngine(RiskLimits()), mode=Mode.RESEARCH)
    monkeypatch.delenv("QR_LIVE_ENABLED", raising=False)
    assert not r.activate_live(ACTIVATION_PHRASE)
    monkeypatch.setenv("QR_LIVE_ENABLED", "1")
    assert not r.activate_live("enable")
    assert r.activate_live(ACTIVATION_PHRASE)
    r.set_mode(Mode.ASSISTED)
    assert b.orders_enabled
    # A new process starts with nothing activated.
    b2, _ = mt5()
    r2 = OrderRouter(b2, RiskEngine(RiskLimits()), mode=Mode.RESEARCH)
    assert not r2.live_active and not b2.orders_enabled
    r.deactivate_live()
    assert not b.orders_enabled and r.mode is Mode.RESEARCH


def test_router_default_mode_on_paper_is_paper():
    assert OrderRouter(paper(), RiskEngine()).mode is Mode.PAPER


# ---------------------------------------------------------------- secrets
def test_credentials_never_appear_in_repr(monkeypatch):
    monkeypatch.setenv("QR_MT5_LOGIN", "123")
    monkeypatch.setenv("QR_MT5_PASSWORD", "hunter2")
    c = secrets.load_mt5_credentials(keyring_module=None) if False else None
    c = secrets.Mt5Credentials(123, "hunter2", "srv")
    assert "hunter2" not in repr(c) and "123" not in repr(c)
    assert c.as_kwargs()["password"] == "hunter2"


def test_credentials_prefer_keyring_then_environment(monkeypatch):
    store = {"login": "42", "password": "pw", "server": "s"}
    fake = SimpleNamespace(get_password=lambda service, key: store.get(key))
    assert secrets.load_mt5_credentials(fake).login == 42
    empty = SimpleNamespace(get_password=lambda service, key: None)
    monkeypatch.delenv("QR_MT5_LOGIN", raising=False)
    monkeypatch.delenv("QR_MT5_PASSWORD", raising=False)
    assert secrets.load_mt5_credentials(empty) is None
    monkeypatch.setenv("QR_MT5_LOGIN", "7")
    monkeypatch.setenv("QR_MT5_PASSWORD", "x")
    assert secrets.load_mt5_credentials(empty).login == 7


def test_redact_handles_nesting():
    out = secrets.redact({"password": "a", "x": [{"token": "b", "y": 1}], "login": 5, "z": 2})
    assert out == {"password": "<redacted>", "x": [{"token": "<redacted>", "y": 1}], "login": "<redacted>", "z": 2}


def test_mt5_audit_never_receives_credentials():
    log = []
    fake = FakeMt5()
    b = Mt5Broker(module=fake, credentials=secrets.Mt5Credentials(5, "hunter2", "s"), audit=lambda e, d: log.append((e, d)), sleep=lambda s: None)
    b.connect()
    assert fake.kw["password"] == "hunter2"
    assert "hunter2" not in json.dumps(log, default=str)


def test_no_credentials_committed_to_tracked_files():
    import re
    import subprocess
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    pattern = re.compile(r"(QR_MT5_PASSWORD\s*=\s*\S+|password\s*[:=]\s*[\"'][^\"']{6,}[\"'])", re.I)
    hits = []
    for name in files:
        path = ROOT / name
        if path.suffix in (".py", ".json", ".toml", ".txt", ".env", ".md", ".yaml", ".yml") and "tests/" not in name:
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                hits.append(name)
    assert not hits
