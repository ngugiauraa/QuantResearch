"""The dashboard's API. Binds to localhost only; it has no login and must not be exposed to a network.

    python -m quantresearch.api.server
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from quantresearch.api import cf_access
from quantresearch.api.services import Services, to_jsonable
from quantresearch.core.config import ROOT
from quantresearch.execution.broker import BrokerError, account_key
from quantresearch.execution.mt5_check import health_snapshot
from quantresearch.execution.router import Mode, OrderRequest
from quantresearch.external import sources as src, store as ext
from quantresearch.features import pit
from quantresearch.ml import pipeline as ml
from quantresearch.research import experiment as ex, lab, stats
from quantresearch.skills import outcome, registry as skills, upstream, xauusd
from quantresearch.strategies import REGISTRY, get
from quantresearch.timemachine import orchestrate as orch
from quantresearch.warehouse import calendar as cal
from quantresearch.warehouse import store as bars
from quantresearch.warehouse.build import FLAG_BROKER

TIMEFRAMES = {"m1", "m5", "m15", "m30", "h1", "h4", "d1", "d1ny", "mn1"}
NEWS_DEADLINE_S = 20  # Well inside the 100 s Cloudflare tunnel limit.
NEWS_COOLDOWN_S = 60  # After GDELT says 429, asking again sooner only extends the block.


class OrderBody(BaseModel):
    side: int = Field(ge=-1, le=1)
    lots: float = Field(gt=0)
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    atr: float | None = None
    source: str = "dashboard"


class ReplayBody(BaseModel):
    timeframe: str = "h1"
    start: str
    end: str
    execution: str | None = None
    strategies: list[str] = ["trend"]
    skills: list[str] = []
    use_ml: bool = False
    conflict: str = "stand_down"
    min_votes: int = 1
    use_risk_engine: bool = True
    name: str = "dashboard replay"


class ControlBody(BaseModel):
    action: str
    value: int | None = None
    when: str | None = None


class ApprovalBody(BaseModel):
    fingerprint: str
    reply: str


class LiveBody(BaseModel):
    phrase: str


class StopBody(BaseModel):
    flatten: bool = True
    reason: str = "dashboard"


class SkillBody(BaseModel):
    action: str
    args: list = []
    kwargs: dict = {}


class SimulateBody(BaseModel):
    entry: float
    stop: float
    target: float
    entry_time: str
    spread: float = 0.0
    replays: int = Field(default=5000, ge=100, le=100000)
    seed: int = 19
    pool_days: int = 365


def create_app(services: Services | None = None) -> FastAPI:
    app = FastAPI(title="QuantResearch")
    # Optional second lock: verify the Cloudflare Access token on every request (see api/cf_access.py). Added BEFORE the CORS
    # middleware so CORS is the outer layer: browser preflights, which carry no token, are answered by CORS and never reach this check.
    access = cf_access.from_env()
    if access is not None:
        app.add_middleware(cf_access.CfAccessMiddleware, verifier=access)
    # Extra browser origins (the hosted dashboard, for example) are opt-in and must be exact: QR_CORS_ORIGINS="https://dashboard.example.com".
    # Credentials are allowed so the browser can send the Cloudflare Access cookie, and that is only legal with an explicit list, never "*".
    extra_origins = [o.strip().rstrip("/") for o in os.environ.get("QR_CORS_ORIGINS", "").split(",") if o.strip()]
    if any("*" in o for o in extra_origins):
        raise ValueError("QR_CORS_ORIGINS must list exact origins; wildcards are not allowed because the API has no login of its own")
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:3000", "http://localhost:3000", *extra_origins],
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    svc = services or Services()
    app.state.services = svc

    def ok(value):
        return to_jsonable(value)

    def guard_tf(tf: str) -> str:
        if tf not in TIMEFRAMES:
            raise HTTPException(400, f"Unsupported timeframe {tf}")
        return tf

    def has_bars(tf: str = "h1") -> bool:
        return any((svc.p.bars / tf).glob("year=*/month=*/bars.parquet")) if (svc.p.bars / tf).exists() else False

    # ------------------------------------------------------------ overview
    @app.get("/api/health")
    def health():
        external = {}
        status_path = svc.p.external / "status.json"
        if status_path.exists():
            external = json.loads(status_path.read_text(encoding="utf-8"))
        broker_ok = False
        try:
            broker_ok = svc.broker.connected()
        except BrokerError:
            pass
        return ok({"status": "ok", "started_at": svc.started_at, "bar_feed": svc.live.status(), "warehouse_sync": svc.sync_status, "warehouse_ready": has_bars("m1"), "broker": svc.broker.name, "broker_connected": broker_ok,
                   "mt5": health_snapshot(svc.broker), "live_active": svc.router.live_active, "audit_chain_intact": svc.audit.verify(), "external": external})

    def current_regime():
        frame = svc.feature_window("h1", 500, closed_only=True)
        usable = frame.dropna(subset=lab.REGIME_INPUTS)
        if len(usable) > 100:
            # Fitted on earlier bars only, so the latest bar is not labelling itself.
            regimes = lab.predict_regimes(lab.fit_regimes(usable.iloc[:-1]), usable.tail(1))
            if len(regimes):
                return regimes.iloc[-1].to_dict()
        return None

    def overview_quote() -> dict:
        """The broker's live quote when a real broker is attached; otherwise, or if that read fails, the paper
        price, which is the last stored bar and is labelled so."""
        if not svc.broker.is_simulated:
            try:
                q = svc.broker.quote()
                return {"source": svc.broker.name, "time": q.time, "bid": q.bid, "ask": q.ask}
            except BrokerError:
                pass
        return svc.refresh_paper_quote()

    @app.get("/api/overview")
    def overview():
        quote = overview_quote()
        # Regime and signals use closed bars only, so they change at most once a minute. Computing them took seconds
        # per call, and every open page polls this endpoint, so requests queued without limit. Share one result a minute.
        latest_regime = svc.cached("overview_regime", 60, current_regime) if has_bars("h1") else None
        recent_signals = svc.cached("recent_signals", 60, svc.recent_signals)
        now = pd.Timestamp.now(tz="UTC")
        account, error = None, None
        try:
            account = asdict(svc.broker.account())
        except BrokerError as exc:
            error = str(exc)
        risk_view = svc.risk.observe(account["equity"], now.to_pydatetime(), account_key(svc.broker)) if account else {}
        return ok({"account": account, "account_error": error, "quote": quote, "market_open_by_schedule": cal.market_open(now.to_pydatetime()), "session": cal.session_of(pd.Series([now])).iloc[0] if cal.market_open(now.to_pydatetime()) else "closed",
                   "regime": latest_regime, "mode": svc.router.mode.value, "broker": svc.broker.name, "risk": {**risk_view, "emergency_stop": svc.risk.emergency_stop, "lockout_until": svc.risk.lockout_until()},
                   "active_strategies": list(REGISTRY), "recent_signals": sorted(recent_signals, key=lambda s: str(s["time"]), reverse=True)[:10]})

    # ------------------------------------------------------------ charts
    @app.get("/api/bars")
    def get_bars(tf: str = "h1", limit: int = 500, end: str | None = None):
        guard_tf(tf)
        limit = max(10, min(limit, 5000))
        if not has_bars(tf):
            raise HTTPException(503, "The warehouse has no bars for this timeframe yet")
        frame = svc.feature_window(tf, limit, end)
        columns = ["ts", "close_ts", "open", "high", "low", "close", "volume", "bid_close", "ask_close", "spread", "sma_20", "sma_50", "sma_200", "ema_20", "bb_upper", "bb_lower", "bb_mid", "rsi_14", "atr_14",
                   "swing_high", "swing_low", "pdh", "pdl", "day_high", "day_low", "bos_up", "bos_down", "choch_up", "choch_down", "sweep_high", "sweep_low", "fvg_up", "fvg_down", "ifvg_up", "ifvg_down",
                   "fvg_up_low", "fvg_up_high", "fvg_down_low", "fvg_down_high", "premium_discount", "equal_highs", "equal_lows", "flags"]
        out = frame[columns].copy()
        out["time"] = out["ts"].map(lambda t: int(t.timestamp()))
        return ok({"timeframe": tf, "feed": bar_feed(frame) if end is None else {"live": False, "state": "history", "reason": "a past end time was requested"},
                   "bars": out.drop(columns=["ts", "close_ts"]).assign(close_time=out["close_ts"].map(lambda t: int(t.timestamp()))).to_dict("records")})

    def bar_feed(frame: pd.DataFrame) -> dict:
        """What the chart is really showing: stored history only, or history extended by the broker's bars."""
        if not svc.live.enabled:
            return {"live": False, "state": "history", "source": None, "reason": f"no live broker is attached ({svc.broker.name})"}
        status = svc.live.status()
        from_broker = bool(len(frame)) and bool(int(frame["flags"].iloc[-1]) & FLAG_BROKER)
        state = "error" if status["error"] else ("live" if status["broker_end"] is not None else "waiting")
        return {"live": state == "live", "state": state, "source": svc.broker.name, "warehouse_end": status["warehouse_end"],
                "broker_end": status["broker_end"], "error": status["error"], "last_bar_from_broker": from_broker}

    @app.get("/api/coverage")
    def coverage():
        report = svc.p.reports / "audit.json"
        return ok({"coverage": bars.coverage(svc.p).to_dict("records") if svc.p.bars.exists() else [], "audit": json.loads(report.read_text(encoding="utf-8")) if report.exists() else None})

    # ------------------------------------------------------------ market intelligence
    @app.get("/api/intel/calendar")
    def calendar(days_back: int = 90, days_forward: int = 180):
        events = ext.read_events(svc.p)
        rows = events[events["series"] == "FOMC_DECISION"].copy()
        now = pd.Timestamp.now(tz="UTC")
        rows = rows[(rows["event_ts"] >= now - pd.Timedelta(days=days_back)) & (rows["event_ts"] <= now + pd.Timedelta(days=days_forward))].sort_values("event_ts")
        return ok({"events": [{"series": "FOMC decision", "time": t, "basis": b} for t, b in zip(rows["event_ts"], rows["pub_basis"])],
                   "gaps": ["CPI and payroll release times are unavailable: the BLS schedule refused automated access."]})

    @app.get("/api/intel/macro")
    def macro():
        events = ext.read_events(svc.p)
        now = pd.Timestamp.now(tz="UTC")
        out = []
        for series, (description, _, kind) in src.FRED_SERIES.items():
            view = pit.as_of(events, now, series)
            if view.empty:
                out.append({"series": series, "description": description, "kind": kind, "available": False})
                continue
            view = view.sort_values("event_ts")
            last = view.iloc[-1]
            previous = view.iloc[-2]["value"] if len(view) > 1 else None
            out.append({"series": series, "description": description, "kind": kind, "available": True, "value": last["value"], "previous": previous, "as_of": last["event_ts"], "basis": last["pub_basis"]})
        for cot in ("COT_MM_NET", "COT_PROD_NET", "COT_SWAP_NET", "COT_OI"):
            view = pit.as_of(events, now, cot).sort_values("event_ts")
            if len(view):
                last = view.iloc[-1]
                out.append({"series": cot, "description": "CFTC disaggregated gold positioning", "kind": "positioning", "available": True, "value": last["value"],
                            "previous": view.iloc[-2]["value"] if len(view) > 1 else None, "as_of": last["event_ts"], "basis": last["pub_basis"]})
        return ok({"series": out})

    @app.get("/api/intel/cross")
    def cross():
        out = []
        gold = None
        if has_bars("h1"):
            gold = svc.live.load("h1", pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=120))
        for symbol, label in src.CROSS_SYMBOLS.items():
            try:
                other = ext.load_cross(symbol, "h1", svc.p)
            except FileNotFoundError:
                out.append({"symbol": symbol, "label": label, "available": False})
                continue
            record = {"symbol": symbol, "label": label, "available": True, "last": other["close"].iloc[-1], "as_of": other["ts"].iloc[-1],
                      "change_5d": other["close"].iloc[-1] / other["close"].iloc[-120] - 1 if len(other) > 120 else None}
            if gold is not None and len(gold):
                joined = pit.align_higher_tf(gold, other, ["close"], "x_")
                series = np.log(gold["close"]).diff().rolling(240).corr(np.log(joined["x_close"]).diff())
                record["corr_10d"] = series.iloc[-1]
            out.append(record)
        return ok({"symbols": out})

    @app.get("/api/intel/news")
    def news(limit: int = 40):
        path = svc.p.external / "news.json"
        if not path.exists():
            return {"items": [], "fetched_at": None, "note": "No headlines fetched yet."}
        stored = json.loads(path.read_text(encoding="utf-8"))
        return ok({**stored, "items": stored["items"][:limit]})

    news_gate = {"retry_after": 0.0}

    def stored_news(error: str) -> dict:
        path = svc.p.external / "news.json"
        stored = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"items": [], "fetched_at": None}
        return ok({**stored, "items": stored["items"][:40], "refreshed": False, "refresh_error": error})

    @app.post("/api/intel/news/refresh")
    def refresh_news():
        """Fetch fresh headlines from GDELT, or say plainly why not, within NEWS_DEADLINE_S.

        GDELT allows about one request per five seconds per IP and answers 429 beyond that. Retrying (up to 3 x 90 s)
        outlasted Cloudflare's 100 s tunnel limit and the failure surfaced as a bare 502. Now: one attempt, a hard
        deadline, a cooldown after a refusal, and a 200 that keeps the stored headlines and carries the reason.
        """
        import concurrent.futures as cf
        import time as _time

        wait = news_gate["retry_after"] - _time.monotonic()
        if wait > 0:
            return stored_news(f"GDELT refused the last request (too many requests). Try again in {int(wait) + 1} s; showing the stored headlines.")
        pool = cf.ThreadPoolExecutor(max_workers=1)
        job = pool.submit(src.fetch_news_gdelt, getter=lambda url: src.http_get(url, timeout=NEWS_DEADLINE_S, retries=1))
        pool.shutdown(wait=False)
        try:
            frame = job.result(timeout=NEWS_DEADLINE_S)
        except cf.TimeoutError:
            return stored_news(f"GDELT did not answer within {NEWS_DEADLINE_S} s. Showing the stored headlines; try again later.")
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code == 429:
                news_gate["retry_after"] = _time.monotonic() + NEWS_COOLDOWN_S
                return stored_news(f"GDELT refused the request: too many requests from this IP (it allows about one every 5 s). "
                                   f"Wait {NEWS_COOLDOWN_S} s and try again; showing the stored headlines.")
            return stored_news(f"GDELT failed: {type(exc).__name__}: {str(exc)[:200]}. Showing the stored headlines.")
        rows = frame.to_dict("records")
        tagged = xauusd.tag_headlines(rows)
        items = [{"title": t["title"], "url": t["link"], "published_at": t["time"], "tags": t["tags"], "gold_relevant": t["gold_relevant"], "domain": r.get("domain")} for t, r in zip(tagged, rows)]
        payload = {"items": sorted(items, key=lambda i: str(i["published_at"]), reverse=True), "fetched_at": pd.Timestamp.now(tz="UTC"), "source": "GDELT",
                   "note": "Headlines are leads, not facts. Coverage is partial."}
        (svc.p.external).mkdir(parents=True, exist_ok=True)
        (svc.p.external / "news.json").write_text(json.dumps(to_jsonable(payload)), encoding="utf-8")
        svc.knowledge.ingest_news(rows)
        return ok({**payload, "items": payload["items"][:40], "refreshed": True, "refresh_error": None})

    # ------------------------------------------------------------ research
    @app.get("/api/research/strategies")
    def strategies():
        out = []
        folder = svc.p.research / "reports"
        for name, d in REGISTRY.items():
            path = folder / f"{name}_latest.json"
            verdict = json.loads(path.read_text(encoding="utf-8")).get("verdict") if path.exists() else None
            out.append({"name": name, "title": d.title, "timeframe": d.signal_tf, "methodology": d.methodology, "limitations": d.limitations, "defaults": d.defaults, "grid": d.grid, "verdict": verdict, "has_report": path.exists()})
        return ok({"strategies": out})

    @app.get("/api/research/report/{name}")
    def strategy_report(name: str):
        path = svc.p.research / "reports" / f"{name}_latest.json"
        if name not in REGISTRY or not path.exists():
            raise HTTPException(404, f"No report for {name} yet")
        return ok(json.loads(path.read_text(encoding="utf-8")))

    @app.post("/api/research/run/{name}")
    def run_research(name: str):
        if name not in REGISTRY:
            raise HTTPException(404, f"Unknown strategy {name}")
        if not has_bars(get(name).signal_tf):
            raise HTTPException(503, "The warehouse has no bars for this strategy yet")

        def work():
            defn = get(name)
            report = ex.full_report(defn, ex.load_research_frame(defn, p=svc.p), dataset=ex.dataset_version(svc.p))
            ledger = svc.ledger()
            ledger.add(report)
            ledger.close()
            svc.knowledge.ingest_reports(svc.p.research / "reports")
            return report["verdict"]

        return {"job": svc.start_job(f"research:{name}", work)}

    @app.get("/api/research/experiments")
    def experiments():
        ledger = svc.ledger()
        rows = ledger.all()
        ledger.close()
        return ok({"experiments": rows.to_dict("records")})

    @app.get("/api/research/analysis")
    def analysis(tf: str = "h1", days: int = 730, leg: str = "xagusd"):
        guard_tf(tf)
        frame = bars.load_bars(tf, pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days), p=svc.p)
        if frame.empty:
            raise HTTPException(503, "No bars")
        ret = np.log(frame["close"]).diff()
        out = {"timeframe": tf, "bars": len(frame), "returns": stats.describe_returns(ret), "stationarity_price": stats.stationarity(np.log(frame["close"])), "stationarity_returns": stats.stationarity(ret.dropna())}
        hist, edges = np.histogram(ret.dropna(), bins=40)
        out["histogram"] = {"counts": hist, "edges": edges}
        try:
            other = ext.load_cross(leg, "h1", svc.p)
            joined = pit.align_higher_tf(frame, other, ["close"], "x_")["x_close"]
            out["cointegration"] = stats.cointegration(np.log(frame["close"]), np.log(joined))
            out["regression"] = stats.regression(ret, np.log(joined).diff().rename(leg).to_frame())
            rolling = stats.rolling_correlation(ret, np.log(joined).diff(), 240)
            out["rolling_corr"] = [{"time": int(t.timestamp()), "value": v} for t, v in zip(frame["ts"][::24], rolling[::24]) if np.isfinite(v)]
        except FileNotFoundError:
            out["cross_market_note"] = f"No data for {leg}"
        return ok(out)

    @app.get("/api/research/search")
    def search(q: str, as_of: str | None = None):
        return ok(svc.knowledge.assist(q, as_of))

    @app.post("/api/research/reindex")
    def reindex():
        count = (svc.knowledge.ingest_documentation() + svc.knowledge.ingest_reports(svc.p.research / "reports")
                 + svc.knowledge.ingest_model_cards(svc.models.rows()) + svc.knowledge.ingest_folder(svc.p.research / "library"))
        return {"indexed": count, "counts": svc.knowledge.count()}

    # ------------------------------------------------------------ machine learning
    @app.get("/api/ml/models")
    def models():
        return ok({"models": svc.models.rows()})

    @app.post("/api/ml/train")
    def train_model(tf: str = "h1", days: int = 3650):
        guard_tf(tf)
        if not has_bars(tf):
            raise HTTPException(503, "No bars")

        def work():
            frame = svc.feature_window(tf, days * 24 if tf == "h1" else 20000)
            result = ml.train(frame, svc.models, ex.dataset_version(svc.p))
            result.pop("model", None)
            return result

        return {"job": svc.start_job("ml_train", work)}

    @app.post("/api/ml/promote/{model_id}")
    def promote(model_id: int):
        try:
            svc.models.promote(model_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc))
        return {"promoted": model_id}

    @app.get("/api/ml/inference")
    def inference(tf: str = "h1", limit: int = 200):
        guard_tf(tf)
        model = None
        rows = svc.models.rows()
        promoted = svc.models.promoted()
        if promoted is not None:
            model = promoted
        elif rows:
            model = svc.models.load(rows[0]["id"])
        if model is None:
            return {"available": False, "note": "No model has been trained yet."}
        frame = ml.add_calendar(svc.feature_window(tf, limit))
        preds = ml.predict(model, frame)
        preds.insert(0, "time", frame["close_ts"])
        regime_frame = frame.dropna(subset=lab.REGIME_INPUTS)
        regimes = lab.predict_regimes(lab.fit_regimes(regime_frame), regime_frame.tail(1)) if len(regime_frame) > 50 else pd.DataFrame()
        return ok({"available": True, "model": model.name, "is_promoted": promoted is not None, "train_end": model.train_end, "predictions": preds.tail(100).to_dict("records"),
                   "latest": preds.iloc[-1].to_dict(), "regime": regimes.iloc[-1].to_dict() if len(regimes) else None, "drift": ml.drift_report(model, frame),
                   "note": "Probabilities are estimates, not certainties. They cannot place an order or bypass the risk engine."})

    # ------------------------------------------------------------ time machine
    @app.post("/api/replay")
    def new_replay(body: ReplayBody):
        if body.timeframe != "s1":  # S1 bars are built from ticks on demand.
            guard_tf(body.timeframe)
        unknown = [s for s in body.strategies if s not in REGISTRY]
        if unknown or not body.strategies:
            raise HTTPException(400, f"Unknown or empty strategies: {unknown}")
        spec = orch.ExperimentSpec(name=body.name, strategies=body.strategies, skills=body.skills, use_ml=body.use_ml, conflict=body.conflict, min_votes=body.min_votes, use_risk_engine=body.use_risk_engine)
        try:
            with svc.lock:
                rid, session = svc.new_replay(spec, body.timeframe, body.start, body.end, body.execution)
        except (ValueError, FileNotFoundError, KeyError) as exc:
            raise HTTPException(400, str(exc))
        return ok({"id": rid, "state": session.snapshot()})

    def session_or_404(rid: str):
        session = svc.replays.get(rid)
        if session is None:
            raise HTTPException(404, "No such replay")
        return session

    @app.get("/api/replay/{rid}")
    def replay_state(rid: str):
        return ok(session_or_404(rid).snapshot())

    @app.post("/api/replay/{rid}/control")
    def replay_control(rid: str, body: ControlBody):
        session = session_or_404(rid)
        with svc.lock:
            try:
                if body.action == "play":
                    session.play()
                elif body.action == "pause":
                    session.pause()
                elif body.action == "step":
                    session.pause()
                    session.step(body.value or 1)
                elif body.action == "reset":
                    session.reset()
                elif body.action == "speed":
                    session.set_speed(body.value or 1)
                elif body.action == "seek":
                    session.seek(body.when)
                elif body.action == "tick":
                    session.tick()
                elif body.action == "end":
                    session.to_end()
                else:
                    raise HTTPException(400, f"Unknown action {body.action}")
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            return ok(session.snapshot())

    @app.get("/api/replay/{rid}/report")
    def replay_report(rid: str):
        from quantresearch.sim import metrics

        session = session_or_404(rid)
        engine = session.engine
        decisions = [asdict(d) for d in getattr(engine.strategy, "decisions", [])][-100:]
        return ok({"record": session.record(), "report": metrics.report(engine.trades_frame(), engine.equity_curve(), session.config.initial_equity), "decisions": decisions,
                   "equity": [{"time": int(t.timestamp()), "value": v} for t, v in list(zip(engine.equity_times, engine.equity_values))[:: max(1, len(engine.equity_values) // 800)]]})

    @app.websocket("/ws/replay/{rid}")
    async def replay_stream(socket: WebSocket, rid: str):
        await socket.accept()
        session = svc.replays.get(rid)
        if session is None:
            await socket.close(code=4404)
            return
        try:
            while True:
                with svc.lock:
                    moved = session.tick()
                    state = to_jsonable(session.snapshot(candles=300, events=40))
                await socket.send_text(json.dumps(state))
                if session.engine.finished and not moved:
                    await asyncio.sleep(1.0)
                await asyncio.sleep(0.25 if session.playing else 0.75)
        except WebSocketDisconnect:
            return

    @app.post("/api/outcome")
    def outcome_sim(body: SimulateBody):
        when = pd.Timestamp(body.entry_time, tz="UTC")
        m5 = bars.load_bars("m5", when - pd.Timedelta(days=body.pool_days), when + pd.Timedelta(days=1), as_of=when, p=svc.p)
        try:
            result = outcome.simulate_plan(m5, body.entry, body.stop, body.target, when, replays=body.replays, seed=body.seed, spread=body.spread)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        result["mc"].pop("sample_paths", None)
        svc.skill_log.record("timemachine", "simulate", "ok", {"verdict": result["verdict"]})
        return ok(result)

    # ------------------------------------------------------------ skills
    @app.get("/api/skills")
    def list_skills():
        return ok({"skills": [s.info() for s in skills.SKILLS.values()], "outcome_simulator": skills.outcome_info()})

    @app.get("/api/skills/{key}")
    def skill_detail(key: str):
        if key not in skills.SKILLS:
            raise HTTPException(404, "Unknown skill")
        s = skills.SKILLS[key]
        return ok({**s.info(), "instructions": s.instructions(), "runs": svc.skill_log.recent(key)})

    @app.post("/api/skills/{key}/run")
    def run_skill(key: str, body: SkillBody):
        if key not in skills.SKILLS:
            raise HTTPException(404, "Unknown skill")
        try:
            kwargs = _resolve_skill_inputs(key, body.action, body.kwargs)
            output = skills.run(key, body.action, *body.args, log=svc.skill_log, **kwargs)
        except xauusd.PlanRefused as exc:
            return {"status": "refused", "reason": str(exc)}
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(400, f"{type(exc).__name__}: {exc}")
        return ok({"status": "ok", "output": output})

    def _resolve_skill_inputs(key: str, action: str, kwargs: dict) -> dict:
        """Skills that act on bars take them from the warehouse, not from the caller."""
        if key == "scanner" and action == "timeframes":
            return {"frames": {tf: svc.feature_window(tf, 260) for tf in kwargs.get("frames", ["d1ny", "h4", "h1"])}}
        if key == "entry" and action == "check":
            tf = kwargs.pop("timeframe", "m5")
            now = pd.Timestamp(kwargs.get("now") or pd.Timestamp.now(tz="UTC"))
            now = now.tz_localize("UTC") if now.tzinfo is None else now
            kwargs["now"] = now
            kwargs["day"] = kwargs.get("day") or str(now.date())
            kwargs["bars"] = svc.live.load(tf, now - pd.Timedelta(days=3), now + pd.Timedelta(minutes=1), as_of=now)
        return kwargs

    @app.get("/api/skills/logs/all")
    def skill_logs():
        return ok({"runs": svc.skill_log.recent(limit=100)})

    # ------------------------------------------------------------ terminal
    def terminal_state() -> dict:
        svc.refresh_paper_quote()
        state: dict = {"broker": svc.broker.name, "simulated": svc.broker.is_simulated, "connected": False, "mode": svc.router.mode.value, "live_active": svc.router.live_active,
                       "live_switch_on": os.environ.get("QR_LIVE_ENABLED") == "1", "modes": [m.value for m in Mode], "awaiting_approval": [{"fingerprint": k, "ticket": v.ticket_text()} for k, v in svc.router.pending_approvals.items()]}
        try:
            state.update(connected=svc.broker.connected(), account=asdict(svc.broker.account()), quote=asdict(svc.broker.quote()), positions=[asdict(p) for p in svc.broker.positions()],
                         pending=[asdict(o) for o in svc.broker.pending_orders()], history=svc.broker.history()[:100], symbol=asdict(svc.broker.symbol()))
        except BrokerError as exc:
            state["error"] = str(exc)
        return ok(state)

    @app.get("/api/terminal")
    def terminal():
        return terminal_state()

    @app.post("/api/terminal/order")
    def place_order(body: OrderBody):
        if body.side == 0:
            raise HTTPException(400, "Side must be +1 or -1")
        svc.refresh_paper_quote()
        try:
            quote = svc.broker.quote()
        except BrokerError as exc:
            raise HTTPException(503, str(exc))
        entry = body.entry if body.entry is not None else (quote.ask if body.side > 0 else quote.bid)
        atr = body.atr
        if atr is None and has_bars("h1"):
            atr = svc.feature_window("h1", 30, closed_only=True)["atr_14"].iloc[-1]
        request = OrderRequest(body.side, body.lots, entry, body.stop, body.target, body.source, None if atr is None or not np.isfinite(atr) else float(atr))
        result = svc.router.submit(request)
        return ok({"status": result.status, "message": result.message, "lots": result.lots, "fingerprint": result.fingerprint, "ticket": result.ticket, "reasons": result.risk_reasons})

    @app.post("/api/terminal/approve")
    def approve(body: ApprovalBody):
        result = svc.router.approve(body.fingerprint, body.reply)
        return ok({"status": result.status, "message": result.message, "lots": result.lots, "reasons": result.risk_reasons})

    @app.post("/api/terminal/decline/{fingerprint}")
    def decline(fingerprint: str):
        svc.router.decline(fingerprint)
        return {"status": "declined"}

    @app.post("/api/terminal/close/{ticket}")
    def close_position(ticket: int):
        result = svc.router.close(ticket, "dashboard")
        if result.status != "sent":
            raise HTTPException(400, result.message)
        return ok({"status": "closed", "price": result.broker.price if result.broker else None})

    @app.post("/api/terminal/mode/{mode}")
    def set_mode(mode: str):
        try:
            svc.router.set_mode(Mode(mode))
        except ValueError:
            raise HTTPException(400, f"Unknown mode {mode}")
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        return {"mode": svc.router.mode.value}

    @app.post("/api/terminal/activate-live")
    def activate_live(body: LiveBody):
        if not svc.router.activate_live(body.phrase):
            raise HTTPException(403, "Live trading was not activated. It needs the QR_LIVE_ENABLED switch and the exact phrase.")
        return {"live_active": True}

    @app.post("/api/terminal/deactivate-live")
    def deactivate_live():
        svc.router.deactivate_live()
        return {"live_active": False, "mode": svc.router.mode.value}

    # ------------------------------------------------------------ risk
    @app.get("/api/risk")
    def risk_state():
        snapshot, error, exposure = {}, None, {}
        try:
            svc.refresh_paper_quote()
            account = svc.broker.account()
            positions = svc.broker.positions()
            snapshot = svc.risk.observe(account.equity, pd.Timestamp.now(tz="UTC").to_pydatetime(), account_key(svc.broker))
            exposure = {"open_lots": sum(p.lots for p in positions), "positions": len(positions), "net_lots": sum(p.side * p.lots for p in positions), "equity": account.equity}
        except BrokerError as exc:
            error = str(exc)
        return ok({"limits": svc.risk.limits.__dict__, "snapshot": snapshot, "exposure": exposure, "error": error, "emergency_stop": svc.risk.emergency_stop, "emergency_reason": svc.risk.state.get("emergency_reason"),
                   "lockout_until": svc.risk.lockout_until(), "lockout_reason": svc.risk.state.get("lockout_reason"), "decisions": svc.risk.state.recent(50)})

    @app.put("/api/risk/limits")
    def set_limits(values: dict):
        try:
            limits = svc.save_limits(values)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc))
        return ok(limits.__dict__)

    @app.post("/api/risk/size")
    def size(values: dict):
        try:
            return ok(xauusd.size_trade(values["plan"], values["equity"], values["max_loss_usd"]))
        except (KeyError, xauusd.PlanRefused) as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/risk/emergency-stop")
    def emergency(body: StopBody):
        return ok(svc.router.emergency_stop(body.flatten, body.reason))

    @app.post("/api/risk/emergency-clear")
    def emergency_clear():
        svc.risk.set_emergency_stop(False)
        svc.audit.write("emergency_stop_cleared", {})
        return {"emergency_stop": False}

    @app.post("/api/risk/clear-lockout")
    def clear_lockout():
        svc.risk.clear_lockout()
        svc.audit.write("lockout_cleared", {})
        return {"lockout_until": None}

    # ------------------------------------------------------------ performance and audit
    @app.get("/api/performance")
    def performance():
        from quantresearch.sim import metrics

        history = svc.broker.history() if hasattr(svc.broker, "history") else []
        trades = pd.DataFrame(history)
        reports = []
        folder = svc.p.research / "reports"
        for name, d in REGISTRY.items():
            path = folder / f"{name}_latest.json"
            if path.exists():
                r = json.loads(path.read_text(encoding="utf-8"))
                oos = r["walk_forward"]["out_of_sample"]
                reports.append({"strategy": name, "title": d.title, "verdict": r["verdict"]["label"], "trades": oos.get("trades"), "net": oos.get("net_profit"), "sharpe": r["walk_forward"]["oos_sharpe"], "max_dd": oos.get("max_drawdown"),
                                "profit_factor": oos.get("profit_factor"), "win_rate": oos.get("win_rate"), "expectancy": oos.get("expectancy"), "deflated_sharpe": r["verdict"]["deflated_sharpe"], "holdout_net": r["verdict"]["holdout_net"], "holdout_stress": r["verdict"]["holdout_net_stress"]})
        start = 100000.0
        equity_points = []
        if len(trades) and "net" in trades:
            ordered = trades.sort_values("closed_at")
            balance = start
            for at, net in zip(ordered["closed_at"], ordered["net"]):
                balance += net
                equity_points.append({"time": at, "equity": balance})
        return ok({"paper_trades": history[:200], "equity": equity_points, "strategy_comparison": reports})

    @app.get("/api/audit")
    def audit_log(limit: int = 200):
        return ok({"rows": svc.audit.rows(min(limit, 1000)), "chain_intact": svc.audit.verify()})

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        if job_id not in svc.jobs:
            raise HTTPException(404, "No such job")
        return ok(svc.jobs[job_id])

    # ------------------------------------------------------------ strategy discovery (read-only)
    from quantresearch.api import discovery_api
    discovery_api.register(app, svc, ok)

    # ------------------------------------------------------------ the built dashboard
    built = ROOT / "frontend" / "out"
    if built.exists():
        app.mount("/_next", StaticFiles(directory=built / "_next"), name="next")

        @app.get("/{path:path}", include_in_schema=False)
        def page(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "Not found")
            target = (built / path).resolve()
            if built.resolve() in target.parents and target.is_file():
                return FileResponse(target)
            html = built / f"{path}.html"
            if path and html.exists():
                return FileResponse(html)
            return FileResponse(built / "index.html")

    return app


def main() -> None:
    import uvicorn

    # Local only. This API has no authentication, so it must never listen on a public address.
    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("QR_PORT", "8000")), log_level="warning")


if __name__ == "__main__":
    main()
