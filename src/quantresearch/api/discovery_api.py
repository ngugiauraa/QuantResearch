"""Read-only API for the Strategy Discovery dashboard section. It opens discovery databases read-only, so it can
watch a running controller without interfering, and it offers no control over trading."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from fastapi import HTTPException

from quantresearch.discovery import lab
from quantresearch.discovery import spec as S
from quantresearch.discovery.config import HOLDOUT_NOTE, discovery_root
from quantresearch.discovery.ranking import DASHBOARD_GROUPS, STAGE_LABELS, STAGES


def register(app, svc, ok) -> None:
    def runs_root() -> Path:
        return discovery_root(svc.p) / "runs"

    def open_run(run: str):
        path = runs_root() / run / "lab.sqlite"
        if not path.exists() or "/" in run or "\\" in run or ".." in run:
            raise HTTPException(404, f"No discovery run {run}")
        return lab.connect(path, readonly=True)

    def latest_run() -> str | None:
        root = runs_root()
        if not root.exists():
            return None
        runs = [d for d in root.iterdir() if (d / "lab.sqlite").exists()]
        if not runs:
            return None
        return max(runs, key=lambda d: (d / "progress.json").stat().st_mtime if (d / "progress.json").exists() else 0).name

    @app.get("/api/discovery/runs")
    def discovery_runs():
        root = runs_root()
        out = []
        if root.exists():
            for d in sorted(root.iterdir()):
                f = d / "progress.json"
                if f.exists():
                    pr = json.loads(f.read_text(encoding="utf-8"))
                    out.append({"run_id": d.name, "status": pr.get("status"), "updated_at": pr.get("updated_at"), "round": pr.get("round"),
                                "elapsed_s": pr.get("elapsed_s")})
        return ok({"runs": out, "latest": latest_run()})

    @app.get("/api/discovery/status")
    def discovery_status(run: str | None = None):
        run = run or latest_run()
        if run is None:
            return ok({"run_id": None, "note": "No discovery run yet. Start one with: python -m quantresearch.discovery.controller run"})
        con = open_run(run)
        try:
            row = con.execute("SELECT * FROM runs WHERE run_id=?", (run,)).fetchone()
            f = runs_root() / run / "progress.json"
            progress = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
            rounds = [dict(r) for r in con.execute("SELECT round, ended_at, experiments, tasks FROM rounds WHERE run_id=? ORDER BY round", (run,))]
            cum = 0
            for r in rounds:
                cum += r["experiments"] or 0
                r["cumulative"] = cum
            if len(rounds) > 400:
                step = len(rounds) // 400 + 1
                rounds = rounds[::step] + rounds[-1:]
            resources = [dict(r) for r in con.execute("SELECT ts, round, cpu_pct, rss_mb, sys_avail_mb, workers FROM resources WHERE run_id=? ORDER BY rowid DESC LIMIT 200", (run,))][::-1]
            stages = {ds: {r[0]: r[1] for r in con.execute("SELECT stage, count(*) FROM candidates WHERE run_id=? AND dataset=? GROUP BY stage", (run, ds))}
                      for ds in ("real", "null")}
            reached = {ds: {s: int(sum(v for k, v in stages[ds].items() if STAGES.index(k) >= STAGES.index(s))) for s in STAGES} for ds in stages}
            groups = {g: int(sum(stages["real"].get(s, 0) for s in members)) for g, members in DASHBOARD_GROUPS.items()}
            families = con.execute("SELECT count(DISTINCT family) FROM candidates WHERE run_id=? AND dataset='real'", (run,)).fetchone()[0]
            ckpt = con.execute("SELECT round, created_at, sha256 FROM checkpoints WHERE run_id=? ORDER BY round DESC LIMIT 1", (run,)).fetchone()
            events = [dict(r) for r in con.execute("SELECT ts, level, message FROM events WHERE run_id=? ORDER BY id DESC LIMIT 25", (run,))]
            rejected = con.execute("SELECT count(*) FROM candidates WHERE run_id=? AND dataset='real' AND rejected_at IS NOT NULL", (run,)).fetchone()[0]
            qualified = groups.get("Qualified", 0) + groups.get("Paper-trading", 0)
            return ok({"run_id": run, "status": row["status"], "stop_reason": row["stop_reason"], "started_at": row["started_at"], "ended_at": row["ended_at"],
                       "progress": progress, "rounds": rounds, "resources": resources, "stages": stages, "reached": reached, "groups": groups,
                       "stage_labels": STAGE_LABELS, "families": families, "rejected": rejected, "checkpoint": dict(ckpt) if ckpt else None,
                       "events": events, "holdout_note": HOLDOUT_NOTE, "sessions": (row["sessions"] or 0) + 1,
                       "qualification": f"{qualified} qualified for paper trading" if qualified else "No candidate has qualified"})
        finally:
            con.close()

    @app.get("/api/discovery/candidates")
    def discovery_candidates(run: str | None = None, group: str | None = None, dataset: str = "real", limit: int = 50):
        run = run or latest_run()
        if run is None:
            return ok({"candidates": []})
        if group is not None and group not in DASHBOARD_GROUPS:
            raise HTTPException(400, f"Unknown group {group}")
        con = open_run(run)
        try:
            stages = DASHBOARD_GROUPS[group] if group else STAGES[2:]
            marks = ",".join("?" * len(stages))
            rows = con.execute(f"SELECT cid, stage, tf, vintage, direction, origin, complexity, spec_z, oos, stage3, ranking, rejected_at, reject_reason, fitness, it "
                               f"FROM candidates WHERE run_id=? AND dataset=? AND stage IN ({marks})", (run, dataset, *stages)).fetchall()
            out = []
            for r in rows:
                oos = json.loads(r["oos"]) if r["oos"] else {}
                s3 = json.loads(r["stage3"]) if r["stage3"] else {}
                rk = json.loads(r["ranking"]) if r["ranking"] else {}
                it = json.loads(r["it"]) if r["it"] else {}
                out.append({"cid": r["cid"], "stage": r["stage"], "stage_label": STAGE_LABELS[r["stage"]], "tf": r["tf"], "vintage": r["vintage"],
                            "direction": r["direction"], "origin": r["origin"], "complexity": r["complexity"], "logic": S.describe(S.decompress(r["spec_z"])),
                            "oos_trades": oos.get("trades"), "oos_exp_r": oos.get("exp_r"), "oos_sharpe": oos.get("sharpe"), "oos_max_dd": oos.get("max_dd"),
                            "train_sharpe": it.get("sharpe"), "dsr": s3.get("dsr"), "p_bh": s3.get("p_bh"), "score": rk.get("score"),
                            "rejected_at": r["rejected_at"], "reject_reason": r["reject_reason"]})
            out.sort(key=lambda c: (-STAGES.index(c["stage"]), -(c["score"] if c["score"] is not None else -9), -(c["oos_sharpe"] or -9), c["cid"]))
            return ok({"run_id": run, "candidates": out[: max(1, min(limit, 500))], "total": len(out)})
        finally:
            con.close()

    @app.get("/api/discovery/candidate/{cid}")
    def discovery_candidate(cid: str, run: str | None = None):
        from quantresearch.discovery.report import candidate_detail
        run = run or latest_run()
        if run is None:
            raise HTTPException(404, "No discovery run")
        con = open_run(run)
        try:
            row = con.execute("SELECT c.*, NULL AS store_meta FROM candidates c WHERE run_id=? AND cid=?", (run, cid)).fetchone()
            if row is None:
                raise HTTPException(404, f"No candidate {cid}")
            row = dict(row)
            meta = discovery_root(svc.p) / "store" / "real" / row["tf"] / "meta.json"
            row["store_meta"] = str(meta) if meta.exists() else None
            detail = candidate_detail(con, run, row)
            series = con.execute("SELECT days, rets FROM oos_series WHERE run_id=? AND cid=?", (run, cid)).fetchone()
            if series:
                from quantresearch.discovery.validate import unpack
                days, rets = unpack(series["days"]), unpack(series["rets"])
                eq = np.cumsum(rets)
                step = max(1, len(eq) // 600)
                detail["equity_r"] = [{"time": int(days[i] // 1_000_000_000), "value": float(eq[i])} for i in range(0, len(eq), step)]
            detail["not_proven_note"] = ("Experimental and research-stage candidates are hypotheses, not proven strategies. Even a qualified "
                                         "candidate may only be paper traded.")
            return ok(detail)
        finally:
            con.close()

    @app.get("/api/discovery/distribution")
    def discovery_distribution(run: str | None = None):
        run = run or latest_run()
        if run is None:
            return ok({})
        con = open_run(run)
        try:
            out = {}
            for ds in ("real", "null"):
                xs = [r[0] for r in con.execute("SELECT s.sharpe FROM oos_series s JOIN candidates c ON c.run_id=s.run_id AND c.cid=s.cid "
                                                "WHERE s.run_id=? AND c.dataset=?", (run, ds)) if r[0] is not None]
                hist, edges = np.histogram(np.clip(xs, -3, 3), bins=24, range=(-3, 3)) if xs else (np.zeros(24), np.linspace(-3, 3, 25))
                out[ds] = {"n": len(xs), "histogram": hist.tolist(), "edges": edges.tolist(), "median": float(np.median(xs)) if xs else None}
            folds = [dict(r) for r in con.execute("SELECT f.fold, count(*) AS candidates, avg(f.exp_r) AS mean_exp_r, sum(f.total_r>0) AS positive "
                                                  "FROM folds f JOIN candidates c ON c.run_id=f.run_id AND c.cid=f.cid WHERE f.run_id=? AND c.dataset='real' "
                                                  "GROUP BY f.fold ORDER BY f.fold", (run,))]
            methods = [dict(r) for r in con.execute("SELECT kind, dataset, count(*) AS tasks, sum(evaluated) AS evaluated, sum(selected) AS selected "
                                                    "FROM tasks WHERE run_id=? GROUP BY kind, dataset ORDER BY kind, dataset", (run,))]
            return ok({"oos_sharpe": out, "folds": folds, "methods": methods})
        finally:
            con.close()

    @app.get("/api/discovery/report")
    def discovery_report(run: str | None = None):
        run = run or latest_run()
        path = svc.p.research / "reports" / f"discovery_{run}_latest.json"
        if run is None or not path.exists():
            raise HTTPException(404, "No discovery report yet")
        return ok(json.loads(path.read_text(encoding="utf-8")))
