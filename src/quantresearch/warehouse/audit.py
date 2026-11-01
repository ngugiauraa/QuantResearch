"""Day-level completeness and quality audit.

This is the check the first-generation validators lacked. It would have failed on the
month-end defect, and a test proves that. It reports facts and verdicts; it changes no data.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime, timezone

import pandas as pd

from quantresearch.core.config import Paths, paths
from quantresearch.warehouse import calendar as cal
from quantresearch.warehouse.build import FLAG_CROSSED, FLAG_NO_ASK, FLAG_NO_BID, FLAG_SPIKE, read_tf

THIN_DAY_FRACTION = 0.5


def audit_days(ts: pd.Series, start: date, end: date) -> dict:
    """Compare observed bar dates with expected trading weekdays."""
    observed = pd.Series(ts).dt.date.value_counts()
    expected = cal.trading_weekdays(start, end)
    missing = [d for d in expected if d not in observed.index]
    median = float(observed.median()) if len(observed) else 0.0
    thin = [d for d in expected if d in observed.index and observed[d] < THIN_DAY_FRACTION * median]
    month_ends = sorted({cal.last_trading_weekday(d.year, d.month) for d in expected})
    month_ends = [d for d in month_ends if start <= d <= end]
    missing_month_ends = [d for d in month_ends if d not in observed.index]
    return {
        "expected_days": len(expected),
        "observed_days": int(sum(1 for d in expected if d in observed.index)),
        "missing_days": [str(d) for d in missing],
        "thin_days": [str(d) for d in thin],
        "month_ends_checked": len(month_ends),
        "missing_month_ends": [str(d) for d in missing_month_ends],
        "passed": not missing_month_ends and not missing,
    }


def audit_tf(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"rows": 0}
    ts = frame["ts"]
    flags = frame["flags"]
    bid_bad = (frame["bid_high"] < frame["bid_low"]).sum()
    return {
        "rows": int(len(frame)),
        "first": str(ts.iloc[0]),
        "last": str(ts.iloc[-1]),
        "duplicates": int(ts.duplicated().sum()),
        "out_of_order": int((ts.diff().dropna() <= pd.Timedelta(0)).sum()),
        "bid_high_below_low": int(bid_bad),
        "no_ask": int((flags & FLAG_NO_ASK > 0).sum()),
        "no_bid": int((flags & FLAG_NO_BID > 0).sum()),
        "crossed": int((flags & FLAG_CROSSED > 0).sum()),
        "spike": int((flags & FLAG_SPIKE > 0).sum()),
    }


def manifest_summary(p: Paths) -> dict:
    if not p.manifest.exists():
        return {}
    con = sqlite3.connect(p.manifest)
    rows = con.execute(
        "SELECT kind, status, COUNT(*), COALESCE(SUM(bytes),0), COALESCE(SUM(records),0) FROM files GROUP BY 1,2"
    ).fetchall()
    failed = [r[0] for r in con.execute("SELECT key FROM files WHERE status='failed' LIMIT 200")]
    con.close()
    return {"by_kind": [dict(zip(("kind", "status", "files", "bytes", "records"), r)) for r in rows],
            "failed_keys": failed}


def compare_with_legacy(m1: pd.DataFrame, root: Paths) -> dict:
    """Cross-check new bid bars against the first-generation archive, where both exist."""
    import duckdb

    base = root.root / "processed" / "parquet" / "instrument=xauusd" / "timeframe=m1"
    if not base.exists() or m1.empty:
        return {"available": False}
    pattern = str(base).replace("\\", "/") + "/price_side=bid/year=*/month=*/*.parquet"
    con = duckdb.connect(":memory:")
    con.execute("SET TimeZone='UTC'")
    legacy = con.execute(
        f"SELECT timestamp AS ts, open, high, low, close FROM read_parquet('{pattern}', hive_partitioning=false)"
    ).df()
    con.close()
    legacy["ts"] = pd.to_datetime(legacy["ts"], utc=True)
    joined = m1[["ts", "bid_open", "bid_high", "bid_low", "bid_close"]].merge(legacy, on="ts", how="outer", indicator=True)
    both = joined[joined["_merge"] == "both"]
    only_new = joined[joined["_merge"] == "left_only"]
    only_old = joined[joined["_merge"] == "right_only"]
    diff = (both["bid_close"] - both["close"]).abs()
    return {
        "available": True, "joined": int(len(both)), "only_in_new": int(len(only_new)),
        "only_in_legacy": int(len(only_old)),
        "close_differs_over_0_002": int((diff > 0.002).sum()),
        "max_close_difference": float(diff.max()) if len(diff) else None,
        "new_rows_on_31st": int((only_new["ts"].dt.day == 31).sum()),
    }


def run_audit(p: Paths | None = None, start: date = date(2003, 5, 5), end: date | None = None) -> dict:
    p = p or paths()
    m1 = read_tf("m1", p)
    end = end or (m1["ts"].iloc[-1].date() if len(m1) else start)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "m1_days": audit_days(m1["ts"], start, end),
        "timeframes": {tf: audit_tf(read_tf(tf, p)) for tf in ("m1", "m5", "m15", "m30", "h1", "h4", "d1", "d1ny", "mn1")},
        "manifest": manifest_summary(p),
        "legacy_comparison": compare_with_legacy(m1, p),
    }
    p.reports.mkdir(parents=True, exist_ok=True)
    (p.reports / "audit.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report


def main() -> None:
    argparse.ArgumentParser(description="Audit warehouse completeness.").parse_args()
    report = run_audit()
    days = report["m1_days"]
    print(json.dumps({**report, "m1_days": {**days, "missing_days": days["missing_days"][:40],
                                            "thin_days": days["thin_days"][:40]}}, indent=1, default=str))


if __name__ == "__main__":
    main()
