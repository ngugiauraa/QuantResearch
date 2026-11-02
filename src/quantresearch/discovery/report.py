"""The discovery report: everything a reader needs to judge, and reproduce, a run.

Written to data/research/reports/discovery_<run>_<stamp>.json, discovery_<run>_latest.json and a readable
discovery_<run>_latest.html. Every number comes from the run's database. Nothing is summarised away: rejected
candidates and the null-market calibration are reported beside the survivors.
"""
from __future__ import annotations

import html
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from quantresearch.core.config import Paths, paths
from quantresearch.discovery import lab
from quantresearch.discovery import spec as S
from quantresearch.discovery.config import HOLDOUT_NOTE, run_dir
from quantresearch.discovery.ranking import DASHBOARD_GROUPS, STAGE_LABELS, STAGES
from quantresearch.discovery.validate import GATES

LIMITATIONS = [
    HOLDOUT_NOTE,
    "The only truly unseen evidence is forward paper trading, which starts when a candidate is registered. No forward data exists yet.",
    "Spreads are the data provider's observed spreads, not a broker's. Spreads before about 2010 look processed (few distinct values, a hard ceiling).",
    "Financing is an assumed model (fed funds +/- 2.5 %), not broker swap rates.",
    "Bars cannot show the path inside a bar. Stops count first when a bar touches both levels. Finalists are re-run on M1 execution bars.",
    "Out-of-sample periods of later vintages are short, so their candidates rarely have enough trades to qualify.",
    "Earlier research (the seven engines) has already looked at 2003-2024, so this period is not new to the project as a whole.",
    "Macro releases (CPI, payrolls, GDP) are excluded: only revised values are available without a vintage source. Payroll timing is approximated by rule.",
    "Volume is provider activity, not exchange volume. No order-book or futures data is available.",
    "Statistical gates count every out-of-sample look. The in-sample search count is far larger and is reported, but it is not used to "
    "deflate out-of-sample results, because out-of-sample periods were never searched.",
]


def _q(con, sql, *args):
    return con.execute(sql, args).fetchall()


def _j(text):
    return json.loads(text) if text else None


def ancestry(con, run: str, row, depth: int = 3) -> list[dict]:
    out, frontier = [], json.loads(row["parents"] or "[]")
    for level in range(depth):
        nxt = []
        for h in frontier:
            r = con.execute("SELECT spec_hash, origin, operator, parents, fitness FROM candidates WHERE run_id=? AND spec_hash=? AND dataset=? AND vintage=?",
                            (run, h, row["dataset"], row["vintage"])).fetchone()
            if r is None:
                out.append({"level": level + 1, "spec_hash": h, "note": "parent evaluated in another cell"})
                continue
            out.append({"level": level + 1, "spec_hash": h, "origin": r["origin"], "operator": r["operator"], "training_fitness": r["fitness"]})
            nxt += json.loads(r["parents"] or "[]")
        frontier = nxt
        if not frontier:
            break
    return out


def candidate_detail(con, run: str, row) -> dict:
    spec = S.decompress(row["spec_z"])
    meta = json.loads(Path(row["store_meta"]).read_text(encoding="utf-8"))["catalog"] if row["store_meta"] else {}
    feats = S.leaves(spec)
    folds = [dict(r) for r in _q(con, "SELECT fold, trades, total_r, exp_r FROM folds WHERE run_id=? AND cid=? ORDER BY fold", run, row["cid"])]
    oos = _j(row["oos"]) or {}
    return {
        "cid": row["cid"], "spec_hash": row["spec_hash"], "stage": row["stage"], "stage_label": STAGE_LABELS.get(row["stage"]),
        "timeframe": row["tf"], "vintage_cutoff": row["vintage"], "direction": row["direction"], "origin": row["origin"],
        "operator": row["operator"], "complexity": row["complexity"], "family": row["family"],
        "logic": S.describe(spec), "definition": spec,
        "features": [{"name": f, **(meta.get(f) or {})} for f in feats],
        "training": {"inner_train": _j(row["it"]), "inner_validation": _j(row["iv"]), "fitness": row["fitness"]},
        "out_of_sample": {k: v for k, v in oos.items() if k not in ("regimes",)}, "regimes": oos.get("regimes"),
        "folds": folds, "statistics": _j(row["stage3"]), "robustness_and_adversarial": _j(row["robust"]),
        "engine_confirmation": _j(row["confirm"]), "ranking": _j(row["ranking"]),
        "max_abs_correlation_with_existing_engines": row["max_corr_existing"],
        "rejected_at": row["rejected_at"], "reject_reason": row["reject_reason"],
        "ancestry": ancestry(con, run, row),
    }


def build(run_id: str, p: Paths | None = None, final: bool = False) -> dict:
    p = p or paths()
    d = run_dir(run_id, p)
    con = lab.connect(d / "lab.sqlite", readonly=True)
    run = con.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if run is None:
        raise FileNotFoundError(f"no run {run_id}")
    cfg = json.loads(run["config"])
    root_real = Path(cfg.get("store_dir") or (p.research / "discovery" / "store" / "real"))

    def cand_rows(where, *args, limit=None):
        sql = (f"SELECT c.*, ? AS store_meta FROM candidates c WHERE c.run_id=? AND {where}" + (f" LIMIT {int(limit)}" if limit else ""))
        rows = con.execute(sql, (None, run_id, *args)).fetchall()
        out = []
        for r in rows:
            r = dict(r)
            r["store_meta"] = str(root_real / r["tf"] / "meta.json") if (root_real / r["tf"] / "meta.json").exists() else None
            out.append(r)
        return out

    def count(sql, *a):
        return con.execute(sql, (run_id, *a)).fetchone()[0]

    stage_counts = {ds: {r[0]: r[1] for r in _q(con, "SELECT stage, count(*) FROM candidates WHERE run_id=? AND dataset=? GROUP BY stage", run_id, ds)}
                    for ds in ("real", "null")}
    reached = {}
    for ds in ("real", "null"):
        sc = stage_counts[ds]
        reached[ds] = {s: int(sum(v for k, v in sc.items() if STAGES.index(k) >= STAGES.index(s))) for s in STAGES}
    rejections = {ds: [{"stage": r[0], "reason": r[1], "count": r[2]} for r in _q(
        con, "SELECT rejected_at, reject_reason, count(*) FROM candidates WHERE run_id=? AND dataset=? AND rejected_at IS NOT NULL "
             "GROUP BY rejected_at, reject_reason ORDER BY count(*) DESC LIMIT 40", run_id, ds)] for ds in ("real", "null")}
    exp_by_stage = [{"stage": r[0], "passed": bool(r[1]), "count": r[2]} for r in _q(
        con, "SELECT stage, passed, count(*) FROM experiments WHERE run_id=? AND superseded=0 GROUP BY stage, passed", run_id)]
    exp_reasons = [{"stage": r[0], "reason": r[1], "count": r[2]} for r in _q(
        con, "SELECT stage, reason, count(*) FROM experiments WHERE run_id=? AND superseded=0 AND passed=0 GROUP BY stage, reason "
             "ORDER BY count(*) DESC LIMIT 30", run_id)]
    families = {s: count(f"SELECT count(DISTINCT family) FROM candidates WHERE run_id=? AND dataset='real' AND stage IN ({','.join('?' * len(STAGES[i:]))})", *STAGES[i:])
                for i, s in enumerate(STAGES)}
    # method comparison: what each search method contributes, on real and on null data
    methods = []
    for r in _q(con, "SELECT kind, dataset, count(*), sum(evaluated), sum(selected), sum(runtime_s), sum(cpu_s) FROM tasks WHERE run_id=? AND superseded=0 "
                     "GROUP BY kind, dataset ORDER BY kind, dataset", run_id):
        methods.append({"method": r[0], "dataset": r[1], "tasks": r[2], "evaluated": r[3], "selected": r[4], "runtime_s": r[5], "cpu_s": r[6]})
    origin_rows = _q(con, "SELECT dataset, CASE WHEN instr(origin, ':')>0 THEN substr(origin, 1, instr(origin, ':')-1) WHEN origin LIKE 'gp%' THEN 'gp' ELSE origin END AS o, "
                          "count(*), sum(stage!='selected' AND oos IS NOT NULL), sum(oos IS NOT NULL) FROM candidates WHERE run_id=? AND stage NOT IN ('experimental','screened') "
                          "GROUP BY dataset, o", run_id)
    origin = [{"dataset": r[0], "origin": r[1], "frozen": r[2], "passed_walk_forward": r[3], "oos_evaluated": r[4]} for r in origin_rows]
    # the best candidates, and the most promising-looking failures
    ranked = cand_rows("dataset='real' AND oos IS NOT NULL AND stage NOT IN ('selected')")
    ranked.sort(key=lambda r: (-(STAGES.index(r["stage"])), -((_j(r["ranking"]) or {}).get("score") or -9)))
    best = [candidate_detail(con, run_id, r) for r in ranked[:10]]
    looks = cand_rows("dataset='real' AND oos IS NOT NULL")
    looks.sort(key=lambda r: -((_j(r["oos"]) or {}).get("sharpe") or -9))
    promising_failures = []
    for r in looks:
        if r["stage"] in ("qualified", "paper_trading"):
            continue
        oos = _j(r["oos"]) or {}
        promising_failures.append({"cid": r["cid"], "logic": S.describe(S.decompress(r["spec_z"])), "stage": r["stage"],
                                   "oos_sharpe": oos.get("sharpe"), "oos_trades": oos.get("trades"), "oos_exp_r": oos.get("exp_r"),
                                   "rejected_at": r["rejected_at"], "reasons": r["reject_reason"],
                                   "failed_tests": (_j(r["robust"]) or {}).get("failed")})
        if len(promising_failures) >= 15:
            break
    # null calibration
    null_looks = count("SELECT count(*) FROM candidates WHERE run_id=? AND dataset='null' AND oos IS NOT NULL")
    real_looks = len(looks)
    calibration = {
        "method": "The same pipeline run on sign-randomised markets: each bar keeps its real size, wicks, spread, activity and timing, "
                  "but the direction of every move is a coin flip, so no rule can have an edge. Survivors there are false discoveries.",
        "real": reached["real"], "null": reached["null"], "real_oos_looks": real_looks, "null_oos_looks": null_looks,
        "pass_rates": {s: {"real": (reached["real"][s] / real_looks) if real_looks else None,
                           "null": (reached["null"][s] / null_looks) if null_looks else None}
                       for s in ("walk_forward", "statistically_validated", "robust", "qualified")},
    }
    sharpe_dist = {}
    for ds in ("real", "null"):
        xs = [r[0] for r in _q(con, "SELECT s.sharpe FROM oos_series s JOIN candidates c ON c.run_id=s.run_id AND c.cid=s.cid WHERE s.run_id=? AND c.dataset=?", run_id, ds)]
        if xs:
            sharpe_dist[ds] = {"n": len(xs), "mean": float(np.mean(xs)), "p05": float(np.quantile(xs, 0.05)), "p50": float(np.median(xs)),
                               "p95": float(np.quantile(xs, 0.95)), "max": float(np.max(xs)),
                               "histogram": np.histogram(np.clip(xs, -3, 3), bins=24, range=(-3, 3))[0].tolist()}
    # statistical findings from the mining and regression tasks
    cells, exprs = [], []
    for r in _q(con, "SELECT kind, tf, vintage, payload FROM findings WHERE run_id=? AND dataset='real' AND superseded=0", run_id):
        f = json.loads(r[3])
        for c in f.get("top_cells", [])[:5]:
            if c.get("significant"):
                cells.append({"tf": r[1], "vintage": r[2], "horizon": f.get("horizon"), **c})
        for e in f.get("expressions", [])[:2]:
            exprs.append({"tf": r[1], "vintage": r[2], "horizon": f.get("horizon"), "rank_ic": e["rank_ic"], "expression": e["expression"]})
    cells.sort(key=lambda c: -abs(c["t"]))
    exprs.sort(key=lambda e: -abs(e["rank_ic"]))
    findings_summary = {"significant_cells_after_bh_within_task": len(cells), "top_cells": cells[:20], "top_expressions": exprs[:10],
                        "note": "Training-window relationships without costs. Significance is corrected within each mining task only; "
                                "they are leads that became trading candidates, not evidence of an edge."}
    # runtime and resources
    res = _q(con, "SELECT avg(cpu_pct), max(peak_rss_mb), min(sys_avail_mb), max(workers), avg(rss_mb) FROM resources WHERE run_id=?", run_id)[0]
    totals = _q(con, "SELECT sum(runtime_s), sum(cpu_s), count(*) FROM tasks WHERE run_id=? AND superseded=0", run_id)[0]
    progress = _j((d / "progress.json").read_text(encoding="utf-8")) if (d / "progress.json").exists() else {}
    qualified = [b for b in best if b["stage"] in ("qualified", "paper_trading")]
    holdout = [dict(r) for r in _q(con, "SELECT * FROM holdout_access WHERE run_id=?", run_id)]
    forward = [dict(r) for r in _q(con, "SELECT cid, spec_hash, registered_at, forward_start, status, note FROM forward_registry WHERE run_id=?", run_id)]
    stage3_any = next((json.loads(r[0]) for r in _q(con, "SELECT stage3 FROM candidates WHERE run_id=? AND dataset='real' AND stage3 IS NOT NULL LIMIT 1", run_id)), None)
    report = {
        "kind": "strategy_discovery", "run_id": run_id, "final": final, "created_at": datetime.now(timezone.utc).isoformat(),
        "status": run["status"], "stop_reason": run["stop_reason"],
        "headline": {
            "qualified_strategies": len(qualified),
            "statement": (f"{len(qualified)} candidate(s) qualified for controlled paper trading. Qualification is a research result, not permission to trade live."
                          if qualified else "Zero strategies qualified. No candidate survived every statistical, robustness and adversarial gate."),
        },
        "holdout": {"note": HOLDOUT_NOTE, "accesses": holdout},
        "totals": {"hypotheses_generated": count("SELECT count(*) FROM candidates WHERE run_id=?"),
                   "hypotheses_real": count("SELECT count(*) FROM candidates WHERE run_id=? AND dataset='real'"),
                   "hypotheses_null": count("SELECT count(*) FROM candidates WHERE run_id=? AND dataset='null'"),
                   "experiments_executed": count("SELECT count(*) FROM experiments WHERE run_id=? AND superseded=0"),
                   "tasks": totals[2], "rounds": count("SELECT count(*) FROM rounds WHERE run_id=?"),
                   "out_of_sample_looks_real": real_looks, "out_of_sample_looks_null": null_looks},
        "stages": {"definitions": STAGE_LABELS, "dashboard_groups": DASHBOARD_GROUPS, "current_real": stage_counts["real"],
                   "reached_real": reached["real"], "current_null": stage_counts["null"], "reached_null": reached["null"]},
        "rejections": rejections, "experiments_by_stage": exp_by_stage, "experiment_failure_reasons": exp_reasons,
        "families": {"distinct_real_families_reaching_stage": families},
        "best_candidates": best, "promising_looking_failures": promising_failures,
        "multiple_testing": {"out_of_sample_looks": real_looks, "fdr_method": "Benjamini-Hochberg across every out-of-sample look", "q": GATES["fdr_q"],
                             "deflated_sharpe_threshold": GATES["dsr_min"], "deflated_sharpe_trials": real_looks,
                             "expected_max_daily_sharpe_under_null": (stage3_any or {}).get("dsr_expected_max_sr_daily"),
                             "pbo": "CSCV with 8 blocks within each timeframe and vintage group; at most 0.5 allowed",
                             "null_gate": f"out-of-sample Sharpe above the {GATES['null_quantile']:.0%} quantile of null-market candidates",
                             "in_sample_trials": count("SELECT count(*) FROM experiments WHERE run_id=? AND stage='screen' AND superseded=0")},
        "null_calibration": calibration, "oos_sharpe_distribution": sharpe_dist,
        "search_methods": methods, "by_origin": origin, "statistical_findings": findings_summary,
        "gates": GATES,
        "runtime": {"elapsed_hours": (progress.get("elapsed_s") or 0) / 3600,
                    "computer_asleep_hours": (progress.get("suspended_s") or 0) / 3600,
                    "note": "elapsed_hours is active research time; time with the computer asleep is excluded and listed separately", "task_runtime_hours": (totals[0] or 0) / 3600,
                    "task_cpu_hours": (totals[1] or 0) / 3600, "mean_cpu_pct": res[0], "peak_rss_mb": res[1], "min_free_memory_mb": res[2],
                    "max_workers": res[3], "sessions": run["sessions"] + 1 if run["sessions"] is not None else 1},
        "forward_paper_registry": forward,
        "limitations": LIMITATIONS,
        "next_steps": next_steps(len(qualified), reached["real"], calibration),
        "reproducibility": {"config": cfg, "seed": cfg.get("seed"), "fingerprint": run["fingerprint"], "code_version": run["code_version"],
                            "datasets": _j(run["datasets"]), "database": str(d / "lab.sqlite"), "checkpoints": str(d / "checkpoints"),
                            "command": f"PYTHONPATH=src python -m quantresearch.discovery.controller run --run-id {run_id} --seed {cfg.get('seed')}",
                            "note": "Rounds are applied in task order and seeded from (seed, round, task), so a run reproduces on any worker count. "
                                    "Where a run stops by wall-clock time, a rerun with the same round limit reproduces it."},
    }
    con.close()
    return report


def next_steps(n_qualified: int, reached: dict, calibration: dict) -> list[str]:
    steps = []
    if n_qualified:
        steps.append("Register each qualified candidate for forward paper trading (done automatically) and let at least 6 to 12 months of "
                     "new bars accumulate before judging it. Calibrate spreads and swap against your MT5 broker first.")
    else:
        steps.append("Treat the result as evidence that simple, bar-based rules over this feature set do not carry a cost-surviving edge in XAUUSD "
                     "that is distinguishable from the null market. Do not loosen the gates to manufacture a survivor.")
    if reached.get("walk_forward", 0) and not reached.get("statistically_validated", 0):
        steps.append("Some candidates were positive out of sample but failed after multiple-testing correction. More independent data "
                     "(forward bars, a second provider, gold futures) would test them without new looks at the same history.")
    steps += ["Add data that is genuinely new to the search: first-release macro surprises (ALFRED vintages), exchange volume and futures "
              "curves, broker-specific costs.",
              "Keep the contaminated holdout closed; let forward paper trading become the next independent test set.",
              "Rerun with a different seed to measure how much of any survivor is search luck."]
    return steps


# ---------------------------------------------------------------- output
def _h(x) -> str:
    return html.escape(str(x))


def _fmt(v, d=3):
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.{d}f}"
    return _h(v)


def render_html(r: dict) -> str:
    def table(headers, rows):
        head = "".join(f"<th>{_h(h)}</th>" for h in headers)
        body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body or '<tr><td>None</td></tr>'}</tbody></table>"

    t = r["totals"]
    parts = [f"<h1>Strategy discovery: run {_h(r['run_id'])}</h1>",
             f"<p class='head'>{_h(r['headline']['statement'])}</p>",
             f"<p>Status {_h(r['status'])}. Stop reason: {_h(r['stop_reason'])}. Report {_h(r['created_at'][:19])} UTC.</p>",
             f"<p class='warn'>{_h(r['holdout']['note'])}</p>",
             "<h2>Totals</h2>", table(["Measure", "Value"], [[_h(k.replace('_', ' ')), _fmt(v, 0)] for k, v in t.items()]),
             "<h2>Stages reached (real data vs null market)</h2>",
             table(["Stage", "Real", "Null"], [[_h(STAGE_LABELS[s]), _fmt(r['stages']['reached_real'][s], 0), _fmt(r['stages']['reached_null'][s], 0)] for s in STAGES]),
             "<h2>Null calibration</h2>", f"<p>{_h(r['null_calibration']['method'])}</p>",
             table(["Stage", "Real pass rate", "Null pass rate"], [[_h(k), _fmt(v['real']), _fmt(v['null'])] for k, v in r["null_calibration"]["pass_rates"].items()]),
             "<h2>Rejections (real)</h2>", table(["Stage", "Reason", "Count"], [[_h(x["stage"]), _h(x["reason"]), _fmt(x["count"], 0)] for x in r["rejections"]["real"][:25]]),
             "<h2>Multiple testing</h2>", table(["Item", "Value"], [[_h(k), _h(v)] for k, v in r["multiple_testing"].items()]),
             "<h2>Search methods</h2>", table(["Method", "Data", "Tasks", "Evaluated", "Selected", "CPU h"],
                                              [[_h(m["method"]), _h(m["dataset"]), _fmt(m["tasks"], 0), _fmt(m["evaluated"], 0), _fmt(m["selected"], 0),
                                                _fmt((m["cpu_s"] or 0) / 3600, 2)] for m in r["search_methods"]])]
    parts.append("<h2>Best candidates</h2>")
    for b in r["best_candidates"]:
        o = b["out_of_sample"]
        parts.append(f"<h3>{_h(b['stage_label'])}: {_h(b['cid'])}</h3><p><code>{_h(b['logic'])}</code></p>")
        parts.append(table(["OOS trades", "Exp R", "Sharpe", "Max DD", "Pos. years", "Long exp R", "Short exp R", "Corr. existing"],
                           [[_fmt(o.get("trades"), 0), _fmt(o.get("exp_r")), _fmt(o.get("sharpe"), 2), _fmt(o.get("max_dd")), _fmt(o.get("positive_year_share"), 2),
                             _fmt(((o.get("sides") or {}).get("long") or {}).get("exp_r")), _fmt(((o.get("sides") or {}).get("short") or {}).get("exp_r")),
                             _fmt(b["max_abs_correlation_with_existing_engines"], 2)]]))
        if b["statistics"]:
            s3 = b["statistics"]
            parts.append(table(["p (BH)", "DSR", "Null q95", "PBO", "CI", "Reasons"],
                               [[_fmt(s3.get("p_bh")), _fmt(s3.get("dsr")), _fmt(s3.get("null_sharpe_q95"), 2), _fmt(s3.get("pbo_group")),
                                 _h(s3.get("sharpe_ci95")), _h(", ".join(s3.get("reasons", [])))]]))
        if b["robustness_and_adversarial"]:
            tests = b["robustness_and_adversarial"]["tests"]
            parts.append(table(["Test", "Result", "Note"], [[_h(k), {True: "pass", False: "<b>fail</b>", None: "info"}[v["passed"]], _h(v.get("note", ""))]
                                                            for k, v in tests.items()]))
    parts.append("<h2>Promising-looking candidates that were rejected</h2>")
    parts.append(table(["Candidate", "Logic", "OOS Sharpe", "Trades", "Rejected at", "Why"],
                       [[_h(x["cid"]), f"<code>{_h(x['logic'])}</code>", _fmt(x["oos_sharpe"], 2), _fmt(x["oos_trades"], 0), _h(x["rejected_at"]),
                         _h(x["reasons"] or x["failed_tests"])] for x in r["promising_looking_failures"]]))
    parts.append("<h2>Runtime and resources</h2>" + table(["Measure", "Value"], [[_h(k), _fmt(v, 2)] for k, v in r["runtime"].items()]))
    parts.append("<h2>Limitations</h2><ul>" + "".join(f"<li>{_h(x)}</li>" for x in r["limitations"]) + "</ul>")
    parts.append("<h2>Next steps</h2><ul>" + "".join(f"<li>{_h(x)}</li>" for x in r["next_steps"]) + "</ul>")
    parts.append(f"<h2>Reproduce</h2><pre>{_h(r['reproducibility']['command'])}</pre><p>{_h(r['reproducibility']['note'])}</p>"
                 f"<p>Code {_h(r['reproducibility']['code_version'])}, config fingerprint {_h(r['reproducibility']['fingerprint'])}.</p>")
    style = ("body{font:14px/1.5 system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;background:#fff;color:#111}"
             "table{border-collapse:collapse;margin:8px 0 16px;font-size:13px}td,th{border:1px solid #ccc;padding:4px 8px;text-align:left;vertical-align:top}"
             "th{background:#f2f2f2}.warn{background:#fff6dd;padding:8px}.head{font-size:18px;font-weight:600}code{font-size:12px}"
             "@media (prefers-color-scheme:dark){body{background:#111;color:#eee}th{background:#222}td,th{border-color:#444}.warn{background:#3a3000}}")
    return f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Discovery report</title><style>{style}</style></head><body>{''.join(parts)}</body></html>"


def write(run_id: str, p: Paths | None = None, final: bool = False) -> Path:
    p = p or paths()
    rep = build(run_id, p, final)
    folder = p.research / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = rep["created_at"].replace(":", "").replace("-", "")[:15]
    body = json.dumps(lab.jsonable(rep), indent=1)
    path = folder / f"discovery_{run_id}_{stamp}.json"
    if final:
        path.write_text(body, encoding="utf-8")
    (folder / f"discovery_{run_id}_latest.json").write_text(body, encoding="utf-8")
    (folder / f"discovery_{run_id}_latest.html").write_text(render_html(rep), encoding="utf-8")
    if final:
        con = sqlite3.connect(p.research / "experiments.sqlite")
        con.execute("""CREATE TABLE IF NOT EXISTS experiments(id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, strategy TEXT, dataset_version TEXT,
                       period TEXT, methodology TEXT, params TEXT, verdict TEXT, limitations TEXT, report_path TEXT)""")
        con.execute("INSERT INTO experiments(created_at,strategy,dataset_version,period,methodology,params,verdict,limitations,report_path) VALUES(?,?,?,?,?,?,?,?,?)",
                    (rep["created_at"], f"discovery:{run_id}", json.dumps(rep["reproducibility"]["datasets"])[:500], json.dumps({"research_end": "2024-10-30"}),
                     "autonomous discovery with nested chronological validation", json.dumps({"seed": rep["reproducibility"]["seed"]}),
                     json.dumps({"label": "qualified_for_paper" if rep["headline"]["qualified_strategies"] else "no_strategy_qualified",
                                 "qualified": rep["headline"]["qualified_strategies"]}), json.dumps(rep["limitations"][:3]),
                     path.relative_to(p.root).as_posix()))
        con.commit()
        con.close()
    return folder / f"discovery_{run_id}_latest.json"
