"""Long-lived platform objects shared by the API: broker, risk engine, router, stores and replays.

Bound to one local process. The broker defaults to paper. Setting QR_BROKER=mt5 attaches the MT5 adapter in research
mode, which can read but not trade until the owner activates live trading. Nothing else can select a real broker.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from quantresearch.core.config import Paths, paths
from quantresearch.execution.broker import BrokerError, PaperBroker
from quantresearch.execution.router import AuditLog, Mode, OrderRouter
from quantresearch.external import store as ext
from quantresearch.features.registry import build_features
from quantresearch.knowledge.store import KnowledgeBase
from quantresearch.ml.pipeline import ModelRegistry
from quantresearch.research.experiment import Ledger
from quantresearch.risk.engine import RiskEngine, RiskLimits, RiskState
from quantresearch.skills.registry import SkillLog
from quantresearch.timemachine import orchestrate as orch
from quantresearch.timemachine.replay import ReplaySession
from quantresearch.warehouse import store as bars
from quantresearch.warehouse.live import LiveBars

log = logging.getLogger(__name__)


class Services:
    def __init__(self, p: Paths | None = None):
        self.p = p or paths()
        self.p.app.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._cache: dict[str, tuple[float, object]] = {}
        self._cache_gates: dict[str, threading.Lock] = {}
        self.limits_path = self.p.app / "risk_limits.json"
        self.audit = AuditLog(self.p.app / "audit.sqlite")
        self.risk = RiskEngine(self._load_limits(), RiskState(self.p.app / "risk.sqlite"), news_schedule=self._event_schedule,
                               on_event=lambda event, data: self.audit.write(event, data))
        self.broker = self._make_broker()
        # Warehouse history plus, with MT5 attached, the broker's bars after the warehouse ends.
        self.live = LiveBars(self.broker, self.p)
        mode = Mode.PAPER if self.broker.is_simulated else Mode.RESEARCH
        self.router = OrderRouter(self.broker, self.risk, self.audit, mode)
        self.knowledge = KnowledgeBase(path=self.p.app / "knowledge.sqlite")
        self.models = ModelRegistry(self.p)
        self.skill_log = SkillLog(self.p.app / "skills.sqlite")
        self.replays: dict[str, ReplaySession] = {}
        self.jobs: dict[str, dict] = {}
        self.started_at = datetime.now(timezone.utc)
        self.router.reconcile()
        self.sync_status: dict = {"enabled": False}
        if self.live.enabled and os.environ.get("QR_WAREHOUSE_SYNC", "1") != "0":
            threading.Thread(target=self._daily_sync, name="warehouse-sync", daemon=True).start()
        self.catalogue_worker: dict = {"current": None, "started": False}
        self._catalogue_wake = threading.Event()
        # Forward test 001: resumes by itself after every restart once started. QR_FORWARD_TEST=0 turns it off.
        self._forward = None
        self._forward_stop = threading.Event()
        if os.environ.get("QR_FORWARD_TEST", "1") != "0":
            threading.Thread(target=self._forward_loop, name="forward-test", daemon=True).start()

    def forward_test(self):
        with self.lock:
            if self._forward is None:
                from quantresearch.forward.runner import ForwardTest

                self._forward = ForwardTest(self.p)
            return self._forward

    def _forward_loop(self) -> None:
        from quantresearch.forward.runner import loop

        loop(self.forward_test(), self._forward_stop)

    def catalogue_kick(self) -> None:
        """Start the catalogue worker on first use, and wake it when work is queued."""
        with self.lock:
            if not self.catalogue_worker["started"]:
                from quantresearch.research import catalogue as cat_runner

                cat_runner.recover(self.p)  # A run cut off by a restart goes again.
                self.catalogue_worker["started"] = True
                threading.Thread(target=self._catalogue_loop, name="catalogue-worker", daemon=True).start()
        self._catalogue_wake.set()

    def _catalogue_loop(self) -> None:
        """Runs pending catalogue strategies one at a time, each in a child process so the API stays responsive."""
        from quantresearch.research import catalogue as cat_runner

        while True:
            name = cat_runner.claim_next(self.p)
            if name is None:
                self._catalogue_wake.wait(timeout=60)
                self._catalogue_wake.clear()
                continue
            self.catalogue_worker["current"] = name
            try:
                error = cat_runner.run_subprocess(name, self.p)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            cat_runner.finish(name, error, self.p)
            self.audit.write("catalogue_run", {"strategy": name, "error": error})
            self.catalogue_worker["current"] = None

    def _daily_sync(self, check_every_s: float = 900.0) -> None:
        """Once per UTC day while the API runs: Dukascopy for the current month, then MT5 bars for any gap.

        Read-only towards the broker. Turn off with QR_WAREHOUSE_SYNC=0; run by hand with python -m quantresearch.warehouse.mt5_sync.
        """
        import time

        from quantresearch.warehouse import mt5_sync

        self.sync_status = {"enabled": True, "last_day": None, "last_error": None}
        while True:
            today = datetime.now(timezone.utc).date().isoformat()
            if self.sync_status["last_day"] != today:
                try:
                    report = mt5_sync.sync(self.broker, self.p)
                    self.sync_status.update(last_day=today, last_error=None, warehouse_end=report.get("warehouse_end_after"))
                    self.audit.write("warehouse_synced", {"warehouse_end": report.get("warehouse_end_after"), "days": list(report.get("broker_days", {}))})
                except Exception as exc:  # Retried at the next check; the API keeps serving warehouse plus live bars.
                    log.exception("Daily warehouse sync failed")
                    self.sync_status["last_error"] = f"{type(exc).__name__}: {exc}"
            time.sleep(check_every_s)

    def _make_broker(self):
        if os.environ.get("QR_BROKER") == "mt5":
            from quantresearch.execution.mt5_adapter import Mt5Broker
            from quantresearch.execution.secrets import load_mt5_credentials

            broker = Mt5Broker(symbol=os.environ.get("QR_MT5_SYMBOL", "XAUUSD"), credentials=load_mt5_credentials(), audit=lambda e, d: self.audit.write(e, d))
            broker.connect()
            return broker
        return PaperBroker(self.p.app / "paper.sqlite")

    def _load_limits(self) -> RiskLimits:
        if self.limits_path.exists():
            return RiskLimits(**json.loads(self.limits_path.read_text(encoding="utf-8")))
        return RiskLimits()

    def save_limits(self, values: dict) -> RiskLimits:
        merged = {**self.risk.limits.__dict__, **values}
        limits = RiskLimits(**merged)
        limits.validate()
        self.risk.limits = limits
        self.limits_path.write_text(json.dumps(limits.__dict__, indent=1), encoding="utf-8")
        self.audit.write("risk_limits_changed", values)
        return limits

    def _event_schedule(self) -> list[datetime]:
        """Published FOMC decision times. A missing calendar is reported by the risk engine as a block.

        No calendar on disk is not the same as no events, so it raises: the risk engine turns that into a failed
        news check (fail closed). Exits are unaffected.
        """
        events = ext.read_events(self.p)
        rows = events[events["series"] == "FOMC_DECISION"]
        if rows.empty:
            raise RuntimeError("the FOMC calendar has not been downloaded; run python -m quantresearch.external.store")
        return [t.to_pydatetime() for t in rows["event_ts"]]

    # ------------------------------------------------------------ shared results
    def cached(self, key: str, ttl: float, compute):
        """`compute()` at most once per `ttl` seconds. Concurrent callers wait for the one computation in progress
        instead of starting their own, so a slow result polled by many pages cannot pile up work on the server."""
        import time

        with self.lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < ttl:
                return hit[1]
            gate = self._cache_gates.setdefault(key, threading.Lock())
        with gate:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < ttl:
                return hit[1]
            value = compute()
            self._cache[key] = (time.monotonic(), value)
            return value

    # ------------------------------------------------------------ market data
    def latest_bar(self, tf: str = "m1") -> dict | None:
        try:
            con_bars = bars.load_bars(tf, start=pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=45), p=self.p)
        except FileNotFoundError:
            return None
        return None if con_bars.empty else con_bars.iloc[-1].to_dict()

    def refresh_paper_quote(self) -> dict:
        """The paper broker's price. Without a live feed this is the last stored bar, and is labelled so."""
        latest = self.latest_bar("m1")
        if latest is None:
            return {"source": "none"}
        if isinstance(self.broker, PaperBroker):
            ask = latest["ask_close"]
            self.broker.on_quote(float(latest["bid_close"]), float(ask if ask == ask else latest["bid_close"]), str(latest["close_ts"]))
        return {"source": "stored_history_not_live", "time": str(latest["close_ts"]), "bid": latest["bid_close"], "ask": latest["ask_close"]}

    def recent_signals(self, window: int = 500, tail: int = 48) -> list[dict]:
        """The latest entry signal of each engine, computed on that engine's own signal timeframe.

        An engine's rule is defined for its timeframe (Behavioral on M15, London Open on M5), so running it
        on other bars gives results that mean nothing. `tail` is counted in that engine's own bars.
        Engines that need a fit or cross-market data are skipped, and so is any engine whose bars are not built.
        """
        from quantresearch.strategies import REGISTRY

        frames: dict[str, pd.DataFrame | None] = {}
        found: list[dict] = []
        for name, defn in REGISTRY.items():
            if defn.fit or defn.needs:
                continue
            tf = defn.signal_tf
            if tf not in frames:
                try:
                    frames[tf] = self.feature_window(tf, window, closed_only=True)
                except FileNotFoundError:
                    frames[tf] = None
            frame = frames[tf]
            if frame is None:
                continue
            try:
                signals = defn.prepare(frame, defn.defaults).tail(tail)
            except Exception:  # One engine failing must not take the whole overview down.
                log.exception("Signals for %s on %s bars failed", name, tf)
                continue
            hit = signals[signals["long_entry"] | signals["short_entry"]]
            if len(hit):
                index = hit.index[-1]
                found.append({"strategy": name, "timeframe": tf, "time": frame["close_ts"].iloc[index],
                              "side": "long" if hit["long_entry"].iloc[-1] else "short"})
        return found

    def feature_window(self, tf: str, limit: int, end=None, closed_only: bool = False, live: bool = True) -> pd.DataFrame:
        """The most recent `limit` bars with features. Extra bars are loaded first so warm-up is complete.

        `live` includes the broker's bars after the warehouse ends (MT5 only). `closed_only` drops a bar still forming,
        which signals need so a rule cannot fire and then vanish within the same bar.
        """
        seconds = {"m1": 60, "m5": 300, "m15": 900, "m30": 1800, "h1": 3600, "h4": 14400, "d1": 86400, "d1ny": 86400, "mn1": 2678400}[tf]
        stop = pd.Timestamp.now(tz="UTC") if end is None else pd.Timestamp(end, tz="UTC")
        begin = stop - pd.Timedelta(seconds=seconds * (limit + 400) * 1.7)
        as_of = stop if closed_only else None
        frame = self.live.load(tf, begin, stop, as_of) if live else bars.load_bars(tf, begin, stop, as_of, p=self.p)
        return build_features(frame).tail(limit).reset_index(drop=True)

    # ------------------------------------------------------------ replays
    def new_replay(self, spec: orch.ExperimentSpec, tf: str, start: str, end: str, execution: str | None = None) -> tuple[str, ReplaySession]:
        from quantresearch.research.experiment import load_research_frame
        from quantresearch.strategies import get

        frame = bars.load_bars(tf, start, end, p=self.p)
        if frame.empty:
            raise ValueError(f"No {tf} bars between {start} and {end}")
        features = build_features(frame)
        needs = sorted({n for s in spec.strategies for n in get(s).needs})
        if needs:
            features = ext.attach_cross(features, needs, self.p)
        macro = ext.macro_features(features, p=self.p)
        features = pd.concat([features, macro], axis=1)
        model = self.models.promoted() if spec.use_ml else None
        exec_bars = None
        if execution == "tick":
            exec_bars = bars.load_ticks(start, end, p=self.p)
            if exec_bars.empty:
                raise ValueError(f"No tick data between {start} and {end}. Tick history is still partial; see the README.")
        elif execution:
            exec_bars = bars.load_bars(execution, start, end, p=self.p)
        session = orch.build_session(spec, features, exec_bars, model)
        rid = uuid.uuid4().hex[:10]
        self.replays[rid] = session
        self.audit.write("replay_created", {"id": rid, "tf": tf, "start": start, "end": end, "strategies": spec.strategies, "skills": spec.skills})
        return rid, session

    # ------------------------------------------------------------ background jobs
    def start_job(self, kind: str, work) -> str:
        job_id = uuid.uuid4().hex[:10]
        self.jobs[job_id] = {"id": job_id, "kind": kind, "status": "running", "started_at": datetime.now(timezone.utc).isoformat()}

        def runner():
            try:
                result = work()
                self.jobs[job_id].update(status="done", result=result)
            except Exception as exc:  # Reported to the dashboard rather than lost in a thread.
                self.jobs[job_id].update(status="failed", error=f"{type(exc).__name__}: {exc}")

        threading.Thread(target=runner, daemon=True).start()
        return job_id

    def ledger(self) -> Ledger:
        return Ledger(self.p)


def to_jsonable(value):
    """NaN and infinity are not valid JSON, and pandas and numpy types need converting."""
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, pd.Timedelta):
        return str(value)
    if value is pd.NaT:
        return None
    if isinstance(value, (pd.DataFrame,)):
        return to_jsonable(value.to_dict("records"))
    if isinstance(value, (pd.Series,)):
        return to_jsonable(value.to_dict())
    if isinstance(value, np.ndarray):
        return to_jsonable(value.tolist())
    return value
