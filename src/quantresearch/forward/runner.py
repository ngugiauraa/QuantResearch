"""Forward test 001 on the MT5 demo account: start checks, the periodic tick, alerts and status.

    python -m quantresearch.forward.runner status
    python -m quantresearch.forward.runner start      # once, after resetting the demo balance (see the pre-registration)

The API runs `tick()` every 15 seconds in a background thread once the test has started, so it resumes by itself
after every restart. A tick: checks the account is a demo (otherwise: alert, nothing sent), records exits the
broker made, and after each hour boundary computes the seven strategies' signals on bars closed since the last
scan and acts on them through the order router and the test's own risk engine.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from datetime import datetime, timezone

import pandas as pd

from quantresearch.core.config import Paths, paths
from quantresearch.execution.broker import BrokerError
from quantresearch.execution.router import AuditLog, Mode, OrderRouter
from quantresearch.forward import plan
from quantresearch.forward.evaluate import evaluate
from quantresearch.forward.guard import DemoOnlyMt5Broker
from quantresearch.forward.journal import Journal
from quantresearch.forward.portfolio import BarSignal, Portfolio, RouterExecutor
from quantresearch.forward.source import SignalSource, atr_at, held_bars, strategy_tf
from quantresearch.risk.engine import RiskEngine, RiskState

SCAN_DELAY_S = 5
EQUITY_EVERY_S = 60
MACRO_EVERY_S = 6 * 3600
PREDICT_EVERY_S = 3600


def fomc_schedule(p: Paths):
    def schedule():
        from quantresearch.external import store as ext

        rows = ext.read_events(p)
        rows = rows[rows["series"] == "FOMC_DECISION"]
        if rows.empty:
            raise RuntimeError("the FOMC calendar has not been downloaded")
        return [t.to_pydatetime() for t in rows["event_ts"]]
    return schedule


class ForwardTest:
    def __init__(self, p: Paths | None = None, broker=None, loader=None, clock=None, macro_refresh=None):
        self.p = p or paths()
        self.p.app.mkdir(parents=True, exist_ok=True)
        self.journal = Journal(self.p.app / "forward_001.sqlite")
        self.audit = AuditLog(self.p.app / "forward_001_audit.sqlite")
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._broker = broker
        self._loader = loader
        self._router = None
        self._macro_refresh = macro_refresh
        self.lock = threading.RLock()
        self.last_error: str | None = None

    # ------------------------------------------------------------ wiring
    @property
    def broker(self) -> DemoOnlyMt5Broker:
        if self._broker is None:
            from quantresearch.execution.secrets import load_mt5_credentials

            symbol = os.environ.get("QR_FORWARD_SYMBOL") or os.environ.get("QR_MT5_SYMBOL") or "XAUUSDm"
            self._broker = DemoOnlyMt5Broker(symbol=symbol, credentials=load_mt5_credentials(), audit=self.audit.write)
            self._broker.connect()
        self._broker.on_refused = self._refused
        return self._broker

    @property
    def router(self) -> OrderRouter:
        if self._router is None:
            risk = RiskEngine(plan.limits(), RiskState(self.p.app / "forward_001_risk.sqlite"), news_schedule=fomc_schedule(self.p),
                              on_event=lambda e, d: self.audit.write(e, d))
            self._router = OrderRouter(self.broker, risk, self.audit, Mode.PAPER, clock=self.clock)
        return self._router

    def loader(self, tf: str, start, as_of):
        if self._loader is not None:
            return self._loader(tf, start, as_of)
        from quantresearch.warehouse.live import LiveBars

        if not hasattr(self, "_live"):
            self._live = LiveBars(self.broker, self.p)
        return self._live.load(tf, start, None, as_of)

    def now(self) -> pd.Timestamp:
        return pd.Timestamp(self.clock())

    # ------------------------------------------------------------ alerts
    def _refused(self, data: dict) -> None:
        if not self.journal.get("alert"):
            self.journal.set("alert", {"at": self.now().isoformat(), "kind": "not_a_demo_account", **data})
        self.journal.event("alert", reason="not_a_demo_account", **data)

    def clear_alert(self, note: str = "") -> dict:
        alert = self.journal.get("alert")
        self.journal.set("alert", None)
        self.journal.event("alert_cleared", note=note, previous=alert)
        self.audit.write("forward_test_alert_cleared", {"note": note})
        return {"cleared": bool(alert)}

    # ------------------------------------------------------------ start
    def readiness(self) -> dict:
        checks = {}
        try:
            checks["pre_registration_committed"] = {"ok": True, "detail": plan.doc_commit()}
        except plan.PlanViolation as exc:
            checks["pre_registration_committed"] = {"ok": False, "detail": str(exc)}
        try:
            plan.check_locked(self.p)
            checks["parameters_locked"] = {"ok": True, "detail": "all seven match the registration"}
        except plan.PlanViolation as exc:
            checks["parameters_locked"] = {"ok": False, "detail": str(exc)}
        try:
            kind = self.broker.account_type()
            info = self.broker.mt5.account_info()
            checks["demo_account"] = {"ok": kind == "demo", "detail": f"MT5 reports a {kind} account ({getattr(info, 'server', '?')})"}
            checks["hedging_account"] = {"ok": self.broker.hedging(), "detail": "positions must not net against each other"}
            eq = float(self.broker.account().equity)
            checks["starting_equity"] = {"ok": eq >= plan.MIN_START_EQUITY, "detail": f"{eq:,.2f} (minimum {plan.MIN_START_EQUITY:,.0f})"}
        except Exception as exc:
            checks["demo_account"] = {"ok": False, "detail": f"MT5 unavailable: {type(exc).__name__}: {exc}"}
        checks["not_started"] = {"ok": self.journal.get("start") is None, "detail": "a test can be started once"}
        return {"ready": all(c["ok"] for c in checks.values()), "checks": checks}

    def start(self) -> dict:
        with self.lock:
            r = self.readiness()
            if not r["ready"]:
                raise plan.PlanViolation("Cannot start: " + "; ".join(f"{k}: {c['detail']}" for k, c in r["checks"].items() if not c["ok"]))
            now = self.now()
            info = self.broker.mt5.account_info()
            record = {"at": now.isoformat(), "commit": r["checks"]["pre_registration_committed"]["detail"],
                      "equity": float(self.broker.account().equity), "server": getattr(info, "server", None),
                      "symbol": self.broker.symbol_name, "load_start": (now - pd.DateOffset(years=plan.HISTORY_YEARS)).floor("D").isoformat()}
            self.journal.set("start", record)
            self.journal.set("last_close", {name: now.isoformat() for name in plan.STRATEGIES})  # Only bars closing after the start count.
            self.journal.event("started", **record)
            self.audit.write("forward_test_started", record)
            self._record_in_doc(record)
            return record

    def _record_in_doc(self, record: dict) -> None:
        text = plan.DOC.read_text(encoding="utf-8")
        line = (f"Started {record['at']} UTC from commit {record['commit'][:12]}, starting demo equity "
                f"{record['equity']:,.2f} {record['symbol']} on {record['server']}.")
        plan.DOC.write_text(text.replace("(Filled in at start: date and time UTC, commit hash, starting demo equity.)", line), encoding="utf-8")

    # ------------------------------------------------------------ the tick
    def tick(self) -> dict:
        with self.lock:
            start = self.journal.get("start")
            if not start:
                return {"state": "not_started"}
            now = self.now()
            paused = None
            if self.journal.get("alert"):
                paused = "alert: the account was not reported as a demo"
            if self.journal.get("result"):
                paused = "decided: the test has reached its decision"
            try:
                kind = self.broker.account_type()
                if kind != "demo":
                    self._refused({"action": "check", "account_type": kind})
                    return {"state": "alert", "account_type": kind}
                pf = Portfolio(self.journal, RouterExecutor(self.router))
                pf.sync()
            except (BrokerError, Exception) as exc:  # Disconnected terminal: retried next tick, bars are caught up later.
                self._note_error(exc)
                return {"state": "broker_unavailable", "error": self.last_error}
            self._maybe_refresh_macro(now)
            scanned = self._maybe_scan(now, start, pf, paused)
            self._maybe_sample(now, start, pf)
            self._maybe_decide(now, start)
            self.last_error = None
            return {"state": "running" if not paused else "paused", "scanned": scanned}

    def _note_error(self, exc: Exception) -> None:
        msg = f"{type(exc).__name__}: {exc}"
        if msg != self.last_error:
            self.journal.event("broker_unavailable", message=msg)
        self.last_error = msg

    def _maybe_scan(self, now: pd.Timestamp, start: dict, pf: Portfolio, paused: str | None) -> bool:
        boundary = now.floor("h")
        done = self.journal.get("scanned_boundary")
        if done and pd.Timestamp(done) >= boundary:
            return False
        if (now - boundary).total_seconds() < SCAN_DELAY_S:
            return False
        try:
            q = self.broker.quote()
            quiet = pd.Timestamp(q.time) < boundary - pd.Timedelta(minutes=1) if q.time else False
        except Exception:
            quiet = False
        if quiet:  # No tick since before the boundary (weekend, holiday): nothing has closed.
            self.journal.set("scanned_boundary", boundary.isoformat())
            return False
        frames, h1_atr = SignalSource(self.p, pd.Timestamp(start["load_start"]), self.loader).frames(now)
        h1 = frames.get("cat_ema_cross_h1")
        got_bar = h1 is not None and len(h1) and pd.Timestamp(h1["close_ts"].iloc[-1]) >= boundary
        window = pd.Timedelta(minutes=plan.LATE_FRACTION * plan.BAR_LENGTH_MIN["h1"])
        if not got_bar and now - boundary < window:
            return False  # The broker has not delivered the hour's last M1 bar yet: try again next tick.
        self._process(frames, h1_atr, now, pf, paused)
        self.journal.set("scanned_boundary", boundary.isoformat())
        return True

    def _process(self, frames: dict, h1_atr, now: pd.Timestamp, pf: Portfolio, paused: str | None) -> None:
        last = self.journal.get("last_close", {})
        due: dict[pd.Timestamp, list[tuple[str, int]]] = {}
        for name, f in frames.items():
            after = pd.Timestamp(last.get(name)) if last.get(name) else None
            for i in range(len(f)):
                ts = pd.Timestamp(f["close_ts"].iloc[i])
                if after is None or ts > after:
                    due.setdefault(ts, []).append((name, i))
        for ts in sorted(due):
            batch = []
            for name, i in due[ts]:
                f, tf = frames[name], strategy_tf(name)
                s = BarSignal.from_row(name, tf, f.iloc[i])
                s.late = (now - ts) > pd.Timedelta(minutes=plan.LATE_FRACTION * plan.BAR_LENGTH_MIN[tf])
                t = self.journal.open_trade(name)
                if t is not None:
                    s.held = held_bars(f, t["signal_close_ts"], ts)
                batch.append(s)
            pf.step(batch, atr_at(h1_atr, ts), paused)
            for name, _ in due[ts]:
                last[name] = ts.isoformat()
            self.journal.set("last_close", last)

    def _maybe_sample(self, now: pd.Timestamp, start: dict, pf: Portfolio) -> None:
        prev = self.journal.get("sampled_at")
        if prev and (now - pd.Timestamp(prev)).total_seconds() < EQUITY_EVERY_S:
            return
        acct = self.broker.account()
        self.journal.sample(now.isoformat(), float(acct.equity), pf.ledger_equity(float(start["equity"])), float(acct.balance))
        self.journal.set("sampled_at", now.isoformat())

    def _maybe_refresh_macro(self, now: pd.Timestamp) -> None:
        prev = self.journal.get("macro_refreshed_at")
        if prev and (now - pd.Timestamp(prev)).total_seconds() < MACRO_EVERY_S:
            return
        self.journal.set("macro_refreshed_at", now.isoformat())
        try:
            (self._macro_refresh or refresh_macro)(self.p)
        except Exception as exc:  # Stale macro data is reported; the strategy keeps the last published value.
            self.journal.event("macro_refresh_failed", message=f"{type(exc).__name__}: {exc}")

    def gold_d1(self, since, until) -> pd.DataFrame:
        try:
            return self.loader("d1", since, until)
        except Exception:
            return pd.DataFrame(columns=["ts", "close_ts", "close"])

    def evaluation(self) -> dict | None:
        start = self.journal.get("start")
        if not start:
            return None
        result = self.journal.get("result")
        if result:
            return {**result, "frozen": True}
        now = self.now()
        return evaluate(self.journal, start, self.gold_d1(pd.Timestamp(start["at"]).floor("D"), now), now)

    def _maybe_decide(self, now: pd.Timestamp, start: dict) -> None:
        if self.journal.get("result"):
            return
        t0 = pd.Timestamp(start["at"])
        if now < t0 + pd.DateOffset(months=plan.EVAL_MONTHS):
            return
        res = evaluate(self.journal, start, self.gold_d1(t0.floor("D"), now), now)
        if res["due"]:
            self.journal.set("result", res)
            self.journal.event("decided", passed=res["passed"])
            self.audit.write("forward_test_decided", {"passed": res["passed"]})

    # ------------------------------------------------------------ prediction
    def prediction(self, force: bool = False) -> dict | None:
        from quantresearch.forward import predict

        start = self.journal.get("start")
        if not start:
            return None
        path = self.p.app / "forward_001_prediction.json"
        if path.exists() and not force:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if (self.now() - pd.Timestamp(cached["computed_at"])).total_seconds() < PREDICT_EVERY_S:
                return cached
        now = self.now()
        run = predict.replay(self.p, start, now, self.loader, fomc_schedule(self.p))
        out = {"computed_at": now.isoformat(), **predict.summarise(run["journal"], float(start["equity"])), "bars": run["bars"],
               "evaluation": evaluate(run["journal"], start, self.gold_d1(pd.Timestamp(start["at"]).floor("D"), now), now)}
        path.write_text(predict.to_json(out), encoding="utf-8")
        return out

    # ------------------------------------------------------------ status
    def status(self, with_readiness: bool = True) -> dict:
        from quantresearch.forward import predict

        start = self.journal.get("start")
        live = predict.summarise(self.journal, float(start["equity"])) if start else None
        cached = self.p.app / "forward_001_prediction.json"
        return {"test_id": plan.TEST_ID, "strategies": list(plan.STRATEGIES), "start": start, "alert": self.journal.get("alert"),
                "readiness": self.readiness() if (with_readiness and not start) else None,
                "evaluation": self.evaluation(), "live": live,
                "prediction": json.loads(cached.read_text(encoding="utf-8")) if cached.exists() else None,
                "events": self.journal.events(150), "counts": self.journal.counts(), "last_error": self.last_error,
                "rules": {"risk_pct": plan.RISK_PCT, "direction_cap_pct": plan.DIRECTION_CAP_PCT, "eval_months": plan.EVAL_MONTHS,
                          "min_trades": plan.MIN_TRADES, "max_months": plan.MAX_MONTHS, "max_drawdown_pct": plan.MAX_DRAWDOWN_PCT,
                          "max_mean_slippage": plan.MAX_MEAN_SLIPPAGE, "max_mean_spread": plan.MAX_MEAN_SPREAD}}


def refresh_macro(p: Paths) -> None:
    """DFII10 (the real-yield strategy) and the FOMC calendar (the news blackout)."""
    from quantresearch.external import sources as src
    from quantresearch.external import store as ext

    ext.write_events("fred_DFII10", src.fetch_fred("DFII10"), p)
    ext.write_events("fomc", src.fetch_fomc(), p)


def loop(test: ForwardTest, stop: threading.Event, every_s: float = 15.0) -> None:
    """The API's background worker: tick, and refresh the prediction hourly. Never raises."""
    last_predict = 0.0
    while not stop.is_set():
        try:
            if test.journal.get("start"):
                test.tick()
                if time.monotonic() - last_predict > PREDICT_EVERY_S:
                    last_predict = time.monotonic()
                    test.prediction()
        except Exception as exc:
            test._note_error(exc)
        stop.wait(every_s)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Forward test 001 (MT5 demo only).")
    ap.add_argument("command", choices=["status", "start", "tick", "predict"])
    args = ap.parse_args(argv)
    test = ForwardTest()
    if args.command == "start":
        out = test.start()
    elif args.command == "tick":
        out = test.tick()
    elif args.command == "predict":
        out = test.prediction(force=True)
    else:
        out = test.status()
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
