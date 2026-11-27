"""Project-wide multiple-testing record: every strategy variant ever evaluated, so the deflated Sharpe counts them all.

A per-report deflated Sharpe only counts the variants that report itself tried. That is too lenient once a hundred
catalogue strategies sit next to the eight engines: with enough tries, one looks good by luck. The trial book keeps
one row per distinct (strategy, variant) ever evaluated, across the engines and the catalogue, and is never pruned.
`project_deflated_sharpe` raises the bar using all of them.

What a trial's Sharpe is:
  - engines (with a parameter grid): each grid variant's mean training Sharpe across folds, from the report's
    parameter-sensitivity table;
  - catalogue strategies (fixed parameters, one variant): the out-of-sample walk-forward Sharpe.
Both are daily-scaled (annual / sqrt(252)) before use, matching walk_forward's own deflation. Re-running the same
variant updates its Sharpe but does not add a trial; a changed parameter set is a new variant and adds one.

The discovery search (`quantresearch.discovery`) deflates against its own trial count and is reported separately.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from quantresearch.core.config import Paths, paths
from quantresearch.research import stats

DAILY = np.sqrt(252)


def variant_key(params: dict) -> str:
    return json.dumps(params, sort_keys=True, default=str)


class TrialBook:
    def __init__(self, p: Paths | None = None):
        self.p = p or paths()
        self.p.research.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.p.research / "experiments.sqlite")
        self.con.execute("""CREATE TABLE IF NOT EXISTS trials(
            strategy TEXT, variant TEXT, kind TEXT, sharpe_daily REAL, first_seen TEXT, last_seen TEXT, report_path TEXT,
            PRIMARY KEY(strategy, variant))""")
        self.con.commit()

    def close(self) -> None:
        self.con.close()

    def _upsert(self, strategy: str, variant: str, kind: str, sharpe_daily: float | None, report_path: str | None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        value = None if sharpe_daily is None or not np.isfinite(sharpe_daily) else float(sharpe_daily)
        self.con.execute(
            """INSERT INTO trials(strategy,variant,kind,sharpe_daily,first_seen,last_seen,report_path) VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(strategy, variant) DO UPDATE SET sharpe_daily=COALESCE(excluded.sharpe_daily, trials.sharpe_daily),
               last_seen=excluded.last_seen, report_path=excluded.report_path""",
            (strategy, variant, kind, value, now, now, report_path))

    def record_report(self, report: dict, report_path: str | None = None) -> int:
        """Add the report's variants. Returns how many it evaluated."""
        wf = report.get("walk_forward", {})
        name = report["strategy"]
        if report.get("grid"):
            table = wf.get("parameter_sensitivity") or {}
            for label, row in table.items():
                self._upsert(name, label, "engine_grid", row.get("mean_train_sharpe", np.nan) / DAILY, report_path)
            count = len(table)
            if not count:  # Nothing traded enough to score: the grid was still tried.
                for label in _grid_labels(report["grid"]):
                    self._upsert(name, label, "engine_grid", None, report_path)
                count = len(_grid_labels(report["grid"]))
        else:
            self._upsert(name, variant_key(report.get("defaults", {})), "catalogue" if report.get("catalogue") else "fixed",
                         (wf.get("oos_sharpe") or 0.0) / DAILY if wf.get("out_of_sample", {}).get("trades") else None, report_path)
            count = 1
        self.con.commit()
        return count

    def backfill(self, folder: Path | None = None) -> int:
        """Record every saved strategy report (not only the latest), so earlier runs of the eight engines count."""
        folder = folder or self.p.research / "reports"
        added = 0
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            if path.stem.endswith("_latest") or path.stem.startswith("discovery"):
                continue
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if "walk_forward" in report and "strategy" in report:
                added += self.record_report(report, path.relative_to(self.p.root).as_posix())
        return added

    def summary(self) -> dict:
        rows = self.con.execute("SELECT kind, COUNT(*), COUNT(sharpe_daily) FROM trials GROUP BY kind").fetchall()
        strategies = self.con.execute("SELECT COUNT(DISTINCT strategy) FROM trials").fetchone()[0]
        return {"variants": int(sum(r[1] for r in rows)), "with_sharpe": int(sum(r[2] for r in rows)), "strategies": int(strategies),
                "by_kind": {r[0]: int(r[1]) for r in rows}}

    def sharpes(self) -> tuple[int, list[float]]:
        """(number of variants, the daily Sharpes that are known). Unscored variants still count towards N."""
        total = self.con.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
        known = [r[0] for r in self.con.execute("SELECT sharpe_daily FROM trials WHERE sharpe_daily IS NOT NULL")]
        return int(total), known


def _grid_labels(grid: dict) -> list[str]:
    import itertools

    keys = list(grid)
    return [variant_key(dict(zip(keys, combo))) for combo in itertools.product(*[grid[k] for k in keys])]


def project_deflated_sharpe(daily_returns: np.ndarray, book: TrialBook) -> dict:
    """Deflated Sharpe of these returns against every variant in the book (Bailey and Lopez de Prado).

    N is the number of variants ever tried, including ones that never traded. The spread of trial Sharpes comes from
    the variants that have one. With fewer than two scored variants it falls back to the plain probabilistic Sharpe.
    """
    n, known = book.sharpes()
    trials = np.asarray([s for s in known if np.isfinite(s)], dtype="float64")
    if n < 2 or len(trials) < 2 or trials.std(ddof=1) == 0:
        return {"trials": n, "scored": int(len(trials)), "expected_max_sr": 0.0, "dsr": stats.probabilistic_sharpe(daily_returns, 0.0)}
    from scipy import stats as sps

    gamma = 0.5772156649
    z1, z2 = sps.norm.ppf(1 - 1 / n), sps.norm.ppf(1 - 1 / (n * np.e))
    expected = trials.std(ddof=1) * ((1 - gamma) * z1 + gamma * z2)
    return {"trials": n, "scored": int(len(trials)), "expected_max_sr": float(expected), "dsr": stats.probabilistic_sharpe(daily_returns, expected)}
