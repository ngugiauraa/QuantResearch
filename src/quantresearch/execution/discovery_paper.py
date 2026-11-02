"""Controlled forward paper trading for registered discovery candidates.

    PYTHONPATH=src python -m quantresearch.execution.discovery_paper --run-id main

Each call processes bars that have closed since the last call and opened after the candidate's registration
time: the definition is frozen, so these bars are genuinely unseen. Every order goes through the independent
risk engine and the order router in PAPER mode, to a paper broker with its own account file
(data/app/discovery_paper.sqlite), separate from the dashboard's paper account. This module refuses any broker
that is not the paper broker, and it never changes the router's mode or live activation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from quantresearch.core.config import Paths, paths
from quantresearch.discovery import features as feat
from quantresearch.discovery import forward
from quantresearch.discovery import spec as S
from quantresearch.discovery.expr import Evaluator, compile_spec
from quantresearch.execution.broker import PaperBroker
from quantresearch.execution.router import AuditLog, Mode, OrderRequest, OrderRouter
from quantresearch.risk.engine import RiskEngine, RiskLimits, RiskState


def _store(tf: str, folder: Path, start: pd.Timestamp, p: Paths, bars_override=None):
    from quantresearch.discovery.store import FeatureStore, _cross, _events, _write_tf
    from quantresearch.warehouse.store import load_bars
    far = pd.Timestamp("2100-01-01", tz="UTC")
    if bars_override is not None:
        bars, higher, cross, events = bars_override[tf], {h: bars_override[h] for h in feat.HIGHER.get(tf, ()) if h in bars_override}, {}, None
    else:
        bars = load_bars(tf, start, None, p=p)
        higher = {h: load_bars(h, start, None, p=p) for h in feat.HIGHER.get(tf, ())}
        cross, events = _cross(far, p), _events(far, p)
    prices, cat = feat.build_catalog(tf, bars, higher, cross, events)
    _write_tf(folder / tf, tf, prices, cat, far, "forward", {"kind": "forward_paper"})
    return FeatureStore(folder / tf, allow_holdout=True)


def make_router(p: Paths) -> OrderRouter:
    p.app.mkdir(parents=True, exist_ok=True)
    broker = PaperBroker(p.app / "discovery_paper.sqlite")
    risk = RiskEngine(RiskLimits(), RiskState(p.app / "discovery_paper_risk.sqlite"))
    return OrderRouter(broker, risk, AuditLog(p.app / "discovery_paper_audit.sqlite"), Mode.PAPER)


def run_forward(run_id: str, p: Paths | None = None, router: OrderRouter | None = None, bars_override=None) -> list[dict]:
    p = p or paths()
    router = router or make_router(p)
    if not isinstance(router.broker, PaperBroker) or router.mode is not Mode.PAPER:
        raise PermissionError("Discovery candidates may only trade on the paper broker in paper mode")
    state_path = p.app / "discovery_paper_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    out = []
    for reg in forward.registered(run_id, p):
        spec = S.decompress(reg["spec_z"])
        tf = spec["timeframe"]
        start = pd.Timestamp(reg["forward_start"])
        start = start.tz_localize("UTC") if start.tzinfo is None else start
        folder = p.app / "discovery_forward_store" / reg["cid"]
        store = _store(tf, folder, start - pd.DateOffset(years=3), p, bars_override)
        a, b = store.bounds(start, pd.Timestamp("2100-01-01", tz="UTC"))
        seen = state.get(reg["cid"], {}).get("last_close_ns", 0)
        log = {"cid": reg["cid"], "forward_start": str(start), "bars_available": int(b - a), "orders": [], "rejections": []}
        if b - a <= 0:
            log["note"] = "no bars after registration yet"
            out.append(log)
            continue
        ev = Evaluator(store, a, b)
        c = compile_spec(spec, ev)
        broker = router.broker
        close_px = np.asarray(store.prices["close"], float)
        opened: dict = state.get(reg["cid"], {}).get("opened", {})
        for i in range(a, b):
            if store.close_ns[i] <= seen:
                continue
            j = i - ev.lo
            bid_l, bid_h, bid_c = (float(store.prices[k][i]) for k in ("bid_low", "bid_high", "bid_close"))
            spread = float(store.prices["ask_close"][i] - bid_c) if np.isfinite(store.prices["ask_close"][i]) else 0.0
            when = str(pd.Timestamp(int(store.close_ns[i]), tz="UTC"))
            for bid in (bid_l, bid_h, bid_c):
                broker.on_quote(bid, bid + spread, when)
            mine = [x for x in broker.positions() if str(x.ticket) in opened]
            for pos in mine:
                held = i - opened[str(pos.ticket)]
                ex = c.arrays["exit_l"][j] if pos.side > 0 else c.arrays["exit_s"][j]
                if held >= c.params["max_hold"] or ex or (c.params["use_day_end"] and c.arrays["day_end"][j]):
                    router.close(pos.ticket, f"discovery:{reg['cid']}")
            if mine or not (c.want_long[j] or c.want_short[j]):
                continue
            side = 1 if c.want_long[j] else -1
            dist = c.arrays["stop_l"][j] if side > 0 else c.arrays["stop_s"][j]
            if not np.isfinite(dist):
                continue
            entry = bid_c + spread if side > 0 else bid_c
            tgt = c.arrays["tgt_l"][j] if side > 0 else c.arrays["tgt_s"][j]
            equity = broker.account().equity
            lots = round(min(50.0, equity * spec.get("sizing", {}).get("risk_pct", 0.5) / 100 / (dist * 100)), 2)
            req = OrderRequest(side, lots, float(entry), float(close_px[i] - side * dist), None if not np.isfinite(tgt) else float(close_px[i] + side * tgt),
                               f"discovery:{reg['cid']}", float(store.prices["atr_14"][i]))
            res = router.submit(req)
            (log["orders"] if res.status == "sent" else log["rejections"]).append({"time": when, "status": res.status, "message": res.message})
            if res.status == "sent" and broker.positions():
                opened[str(broker.positions()[-1].ticket)] = i
        state[reg["cid"]] = {"last_close_ns": int(store.close_ns[b - 1]), "opened": opened}
        out.append(log)
    state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Forward paper trading of registered discovery candidates (paper only).")
    ap.add_argument("--run-id", default="main")
    args = ap.parse_args()
    print(json.dumps(run_forward(args.run_id), indent=1, default=str))


if __name__ == "__main__":
    main()
