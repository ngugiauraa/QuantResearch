"""Run catalogue strategies through the same research as the 8 engines, under hard rules against fooling ourselves.

    python -m quantresearch.research.catalogue cat_turtle_s1_d1 ...   # run these now
    python -m quantresearch.research.catalogue --all                  # every catalogue strategy, in order
    python -m quantresearch.research.catalogue --queued               # whatever the dashboard queued

Each run is `experiment.full_report`: the same walk-forward folds, the same untouched holdout (the last two years),
the same cost scenarios including stressed costs, and the same report. On top of that:

  1. Every run goes into the experiment ledger, and every variant into the trial book (research/trials.py). Nothing
     is ever deleted from either.
  2. The deflated Sharpe that decides "promising" counts every variant in the trial book: all grid variants of the
     8 engines and every catalogue strategy, not only this one.
  3. Parameters are fixed. The first run of a strategy locks its parameters; a later run with different ones under
     the same name is refused, so nobody can tune after seeing the holdout. A changed rule needs a new name, and both
     stay on the record.
  4. "Promising" needs all of: at least 30 out-of-sample trades, positive out-of-sample net profit and Sharpe,
     positive holdout net profit at observed costs AND under stressed costs, and a project-wide deflated Sharpe above
     0.95 (the probability that the true Sharpe beats the best expected by luck across all trials).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone

import numpy as np

from quantresearch.core.config import Paths, paths
from quantresearch.research import experiment as ex
from quantresearch.research.trials import TrialBook, project_deflated_sharpe, variant_key
from quantresearch.strategies import catalogue as cat

PROMISING_DSR = 0.95
MIN_TRADES = 30


class ParamsChanged(RuntimeError):
    pass


def _db(p: Paths) -> sqlite3.Connection:
    p.research.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p.research / "experiments.sqlite", timeout=30)
    con.execute("""CREATE TABLE IF NOT EXISTS catalogue_params(
        strategy TEXT PRIMARY KEY, params_hash TEXT, params TEXT, locked_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS catalogue_queue(
        strategy TEXT PRIMARY KEY, status TEXT, queued_at TEXT, started_at TEXT, finished_at TEXT, error TEXT)""")
    con.commit()
    return con


def params_hash(params: dict) -> str:
    return hashlib.sha256(variant_key(params).encode()).hexdigest()[:16]


def lock_params(name: str, params: dict, p: Paths) -> None:
    """Record a strategy's parameters at its first run, and refuse any later change under the same name."""
    con = _db(p)
    try:
        row = con.execute("SELECT params_hash, params FROM catalogue_params WHERE strategy=?", (name,)).fetchone()
        digest = params_hash(params)
        if row is None:
            con.execute("INSERT INTO catalogue_params VALUES(?,?,?,?)", (name, digest, variant_key(params), datetime.now(timezone.utc).isoformat()))
            con.commit()
        elif row[0] != digest:
            raise ParamsChanged(f"{name} was first run with {row[1]}; it now has {variant_key(params)}. Parameters are fixed once tested, so "
                                "changing them after seeing results is not allowed. Give the changed rule a new name; both stay on record.")
    finally:
        con.close()


def catalogue_verdict(report: dict, project_dsr: dict) -> dict:
    wf = report["walk_forward"]
    oos = wf["out_of_sample"]
    scen = report["holdout"]["scenarios"]
    observed = scen.get("observed", {}).get("net_profit") or 0.0
    stressed = scen.get("stress", {}).get("net_profit") or 0.0
    checks = {
        "enough_oos_trades": oos.get("trades", 0) >= MIN_TRADES,
        "oos_positive": (oos.get("net_profit") or 0.0) > 0 and (wf.get("oos_sharpe") or 0.0) > 0,
        "holdout_positive": observed > 0,
        "stressed_costs_positive": stressed > 0,
        "project_deflated_sharpe": project_dsr["dsr"] > PROMISING_DSR,
        # Added 2026-11-28: beat long-only gold at the same risk, out of sample and in the holdout.
        "beats_buy_and_hold": bool(report.get("risk_matched_benchmark", {}).get("beats_both", False)),
    }
    if not checks["enough_oos_trades"]:
        label = "insufficient_evidence"
    elif all(checks.values()):
        label = "promising_needs_paper_trading"
    elif checks["oos_positive"]:
        label = "weak_or_not_significant"
    else:
        label = "no_edge_found"
    return {"label": label, "promising": all(checks.values()), "checks": checks, "oos_trades": oos.get("trades", 0), "oos_sharpe": wf.get("oos_sharpe"),
            "oos_net": oos.get("net_profit"), "holdout_net": observed, "holdout_net_stress": stressed,
            "deflated_sharpe_own": wf["deflated_sharpe"]["dsr"], "deflated_sharpe_project": project_dsr["dsr"],
            "beats_buy_and_hold": checks["beats_buy_and_hold"], "buy_hold": _bh_summary(report),
            "project_trials": project_dsr["trials"], "expected_max_sr_daily": project_dsr["expected_max_sr"],
            "note": "Not a recommendation. Promising means it survived these tests, not that it will make money. Paper trading comes next."}


def _bh_summary(report: dict) -> dict:
    b = report.get("risk_matched_benchmark", {})
    return {k: {"strategy_sharpe": b.get(k, {}).get("strategy_sharpe"), "buy_hold_sharpe": b.get(k, {}).get("buy_hold_sharpe"),
                "beats": b.get(k, {}).get("beats")} for k in ("out_of_sample", "holdout")}


def run(name: str, p: Paths | None = None) -> dict:
    """One full research run of a catalogue strategy. Returns the saved report."""
    p = p or paths()
    item = cat.get(name)
    defn = item.defn
    lock_params(name, defn.defaults, p)
    features = research_frame(item, p)
    report = ex.full_report(defn, features, dataset=ex.dataset_version(p))
    daily = np.asarray(report.pop("_oos_daily", []), dtype="float64")
    returns_dir = p.research / "catalogue_returns"
    returns_dir.mkdir(parents=True, exist_ok=True)
    np.save(returns_dir / f"{name}.npy", daily)  # Kept so rescore() can judge every strategy against the final trial count.
    report.update(catalogue=True, family=item.family, batch=item.batch, documentation=item.as_dict(),
                  data_coverage={need: _coverage(features, need) for need in defn.needs})
    book = TrialBook(p)
    try:
        if not book.summary()["variants"]:
            book.backfill()  # First catalogue run: count every earlier engine report.
        book.record_report(report)
        project = project_deflated_sharpe(daily, book)
        report["project_multiple_testing"] = {**project, "book": book.summary(),
                                              "note": "Counts every strategy variant ever evaluated by the engines and the catalogue."}
    finally:
        book.close()
    report["catalogue_verdict"] = catalogue_verdict(report, project)
    ledger = ex.Ledger(p)
    try:
        path = ledger.add(report)
    finally:
        ledger.close()
    report["report_path"] = path.relative_to(p.root).as_posix()
    # Store the dated file's path in both copies, so a later rescore updates the right one.
    for target in (path, path.parent / f"{name}_latest.json"):
        saved = json.loads(target.read_text(encoding="utf-8"))
        saved["report_path"] = report["report_path"]
        target.write_text(json.dumps(saved, indent=1, default=ex._json_default), encoding="utf-8")
    return report


def research_frame(item, p: Paths, start=None, end=None, bars=None):
    """The engines' research frame plus what catalogue rules read: m_<SERIES> for each macro series, as published by
    each bar's close, and hours since the latest scheduled FOMC decision (the calendar is known in advance)."""
    from quantresearch.external import store as ext
    from quantresearch.features import pit

    frame = ex.load_research_frame(item.defn, start, end, p=p, bars=bars)
    events = ext.read_events(p)
    for series in item.macro:
        frame[f"m_{series}"] = pit.align_events(frame["close_ts"], events, series)
        # Hours since that series was last published, for rules that act right after a release (batch 6 onwards).
        frame[f"hrs_{series}"] = hours_since(frame["close_ts"], events.loc[events["series"] == series, "published_ts"])
    frame["hours_since_fomc"] = hours_since(frame["close_ts"], events.loc[events["series"] == "FOMC_DECISION", "event_ts"])
    return frame


def hours_since(times, schedule):
    """Hours since the latest scheduled event at or before each time; NaN before the first."""
    import pandas as pd

    if schedule is None or len(schedule) == 0:
        return pd.Series(np.nan, index=times.index)
    grid = np.sort(pd.DatetimeIndex(schedule).as_unit("ns").asi8)
    now = pd.DatetimeIndex(times).as_unit("ns").asi8
    pos = np.searchsorted(grid, now, side="right") - 1
    out = np.full(len(now), np.nan)
    seen = pos >= 0
    out[seen] = (now[seen] - grid[pos[seen]]) / 3.6e12
    return pd.Series(out, index=times.index)


def rescore(p: Paths | None = None) -> dict[str, dict]:
    """Re-judge every catalogue strategy's latest report against the trial book as it stands now.

    A strategy run early faced a smaller trial count than one run late. Rescoring puts all of them against the same,
    final count. It changes only the project-wide deflation and the verdict built on it; the run's own numbers are not
    touched. The report records when it was rescored and the previous verdict.
    """
    p = p or paths()
    folder = p.research / "reports"
    book = TrialBook(p)
    out = {}
    try:
        for name in cat.CATALOGUE:
            latest, returns = folder / f"{name}_latest.json", p.research / "catalogue_returns" / f"{name}.npy"
            if not latest.exists() or not returns.exists():
                continue
            report = json.loads(latest.read_text(encoding="utf-8"))
            project = project_deflated_sharpe(np.load(returns), book)
            previous = report.get("catalogue_verdict", {}).get("label")
            report["project_multiple_testing"] = {**project, "book": book.summary(), "rescored_at": datetime.now(timezone.utc).isoformat(),
                                                  "note": "Counts every strategy variant ever evaluated by the engines and the catalogue."}
            report["catalogue_verdict"] = {**catalogue_verdict(report, project), "previous_label": previous}
            text = json.dumps(report, indent=1, default=ex._json_default)
            latest.write_text(text, encoding="utf-8")
            if report.get("report_path"):
                (p.root / report["report_path"]).write_text(text, encoding="utf-8")
            out[name] = report["catalogue_verdict"]
    finally:
        book.close()
    return out


def _coverage(features, need: str) -> dict:
    column = f"{need}_close"
    if column not in features:
        return {"bars": 0}
    present = features[column].notna()
    return {"bars": int(present.sum()), "first": str(features.loc[present, "ts"].min()) if present.any() else None}


# ------------------------------------------------------------ queue (dashboard)
def enqueue(names: list[str], p: Paths | None = None, now: bool = False) -> list[str]:
    """Queue strategies. now=True runs them next; otherwise they wait for start_batch ('Run all queued')."""
    p = p or paths()
    for name in names:
        cat.get(name)
    con = _db(p)
    try:
        stamp = datetime.now(timezone.utc).isoformat()
        for name in names:
            con.execute("""INSERT INTO catalogue_queue(strategy,status,queued_at) VALUES(?,?,?)
                           ON CONFLICT(strategy) DO UPDATE SET status=excluded.status, queued_at=excluded.queued_at, error=NULL
                           WHERE catalogue_queue.status != 'running'""", (name, "pending" if now else "queued", stamp))
        con.commit()
    finally:
        con.close()
    return names


def dequeue(names: list[str], p: Paths | None = None) -> None:
    con = _db(p or paths())
    try:
        con.executemany("DELETE FROM catalogue_queue WHERE strategy=? AND status IN ('queued','pending')", [(n,) for n in names])
        con.commit()
    finally:
        con.close()


def start_batch(p: Paths | None = None) -> int:
    """'Run all queued': every queued strategy becomes pending, to run one after another."""
    con = _db(p or paths())
    try:
        changed = con.execute("UPDATE catalogue_queue SET status='pending' WHERE status='queued'").rowcount
        con.commit()
        return changed
    finally:
        con.close()


def queue_state(p: Paths | None = None) -> dict[str, dict]:
    con = _db(p or paths())
    try:
        rows = con.execute("SELECT strategy,status,queued_at,started_at,finished_at,error FROM catalogue_queue").fetchall()
    finally:
        con.close()
    return {r[0]: {"status": r[1], "queued_at": r[2], "started_at": r[3], "finished_at": r[4], "error": r[5]} for r in rows}


def claim_next(p: Paths | None = None) -> str | None:
    """Take the oldest pending strategy and mark it running."""
    con = _db(p or paths())
    try:
        row = con.execute("SELECT strategy FROM catalogue_queue WHERE status='pending' ORDER BY queued_at, strategy LIMIT 1").fetchone()
        if row is None:
            return None
        con.execute("UPDATE catalogue_queue SET status='running', started_at=?, finished_at=NULL, error=NULL WHERE strategy=?",
                    (datetime.now(timezone.utc).isoformat(), row[0]))
        con.commit()
        return row[0]
    finally:
        con.close()


def finish(name: str, error: str | None, p: Paths | None = None) -> None:
    con = _db(p or paths())
    try:
        con.execute("UPDATE catalogue_queue SET status=?, finished_at=?, error=? WHERE strategy=?",
                    ("failed" if error else "done", datetime.now(timezone.utc).isoformat(), error, name))
        con.commit()
    finally:
        con.close()


def recover(p: Paths | None = None) -> int:
    """After a restart, a strategy left 'running' was interrupted: run it again."""
    con = _db(p or paths())
    try:
        n = con.execute("UPDATE catalogue_queue SET status='pending' WHERE status='running'").rowcount
        con.commit()
        return n
    finally:
        con.close()


def latest_reports(p: Paths | None = None) -> dict[str, dict]:
    """The newest report summary per catalogue strategy (verdict and the main numbers)."""
    p = p or paths()
    folder = p.research / "reports"
    out = {}
    for name in cat.CATALOGUE:
        path = folder / f"{name}_latest.json"
        if path.exists():
            report = json.loads(path.read_text(encoding="utf-8"))
            out[name] = {"created_at": report.get("created_at"), "verdict": report.get("verdict"), "catalogue_verdict": report.get("catalogue_verdict"),
                         "out_of_sample": {k: report["walk_forward"]["out_of_sample"].get(k) for k in ("trades", "net_profit", "win_rate", "profit_factor", "max_drawdown_pct")},
                         "holdout": {k: (v.get("net_profit"), v.get("trades")) for k, v in report["holdout"]["scenarios"].items()}}
    return out


def run_subprocess(name: str, p: Paths) -> str | None:
    """Run one strategy in a child process, so a long run cannot stall the API. Returns an error or None."""
    import os
    import subprocess
    import sys

    env = {**os.environ, "QR_DATA_ROOT": str(p.root)} if hasattr(p, "root") else dict(os.environ)
    done = subprocess.run([sys.executable, "-m", "quantresearch.research.catalogue", "--one", name], capture_output=True, text=True,
                          errors="replace", env=env, timeout=6 * 3600)
    if done.returncode != 0:
        return (done.stderr or done.stdout).strip()[-800:] or f"exit code {done.returncode}"
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run catalogue strategies through walk-forward, holdout and stressed costs.")
    ap.add_argument("names", nargs="*")
    ap.add_argument("--all", action="store_true", help="Every catalogue strategy")
    ap.add_argument("--batch", type=int, help="Only strategies from this catalogue batch")
    ap.add_argument("--queued", action="store_true", help="Run what the dashboard queued, then stop")
    ap.add_argument("--one", help=argparse.SUPPRESS)
    ap.add_argument("--rescore", action="store_true", help="Re-judge every catalogue report against the current trial count")
    args = ap.parse_args(argv)
    p = paths()
    if args.rescore:
        for name, v in rescore(p).items():
            print(json.dumps({"strategy": name, **v}, default=ex._json_default), flush=True)
        return 0
    if args.one:
        report = run(args.one, p)
        print(json.dumps({"strategy": args.one, **report["catalogue_verdict"]}, default=ex._json_default))
        return 0
    if args.queued:
        start_batch(p)
        names = []
        while (name := claim_next(p)) is not None:
            error = None
            try:
                run(name, p)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            finish(name, error, p)
            names.append(name)
            print(json.dumps({"strategy": name, "error": error}), flush=True)
        return 0
    names = list(args.names)
    if args.all or args.batch:
        names = [e.name for e in cat.ENTRIES if not args.batch or e.batch == args.batch]
    for name in names:
        try:
            report = run(name, p)
            print(json.dumps({"strategy": name, **report["catalogue_verdict"]}, default=ex._json_default), flush=True)
        except Exception as exc:
            print(json.dumps({"strategy": name, "error": f"{type(exc).__name__}: {exc}"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
