"""Keep the warehouse current: provider first, then the broker's (MT5) M1 bars for whatever the provider lacks.

    python -m quantresearch.warehouse.mt5_sync                 # Dukascopy current month, then MT5, then rebuild
    python -m quantresearch.warehouse.mt5_sync --no-provider   # MT5 only
    python -m quantresearch.warehouse.mt5_sync --since 2026-11-23

Read-only on the broker side: it copies bars and nothing else. MT5 rows are archived per UTC day under
warehouse/broker/mt5/m1 and enter the M1 bars only where the provider has nothing (see build.broker_fill), flagged
FLAG_BROKER. When a later provider download covers those days, the next build replaces them. It then tops up the
cross-market series (silver, FX, dollar index, S&P 500, WTI) from MT5. The outcome of each run is written to
reports/mt5_sync.json, which /api/health shows.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

import pandas as pd

from quantresearch.core.config import Paths, paths
from quantresearch.warehouse import build
from quantresearch.warehouse.live import broker_rows_to_bars, warehouse_end


def archive_broker_days(broker, since: pd.Timestamp, until: pd.Timestamp, p: Paths) -> dict:
    """Copy broker M1 bars in [since, until) to one file per UTC day. Days are rewritten, so a partial day fills in later."""
    written = {}
    day = since.normalize()
    while day < until:
        stop = min(day + pd.Timedelta(days=1), until)
        rows = broker.bars_m1(max(day, since).to_pydatetime(), stop.to_pydatetime())
        frame = broker_rows_to_bars(rows)
        target = build.broker_m1_path(p, day.date())
        if len(frame):
            if target.exists():
                # Keep minutes already archived before `since`, and replace the rest with the broker's current copy.
                old = pd.read_parquet(target)
                old["ts"] = pd.to_datetime(old["ts"], utc=True)
                frame = pd.concat([old[old["ts"] < since], frame], ignore_index=True).drop_duplicates("ts", keep="last").sort_values("ts", ignore_index=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_suffix(".tmp")
            frame.to_parquet(temp, index=False, compression="zstd")
            os.replace(temp, target)
            written[str(day.date())] = int(len(frame))
        day += pd.Timedelta(days=1)
    return written


def try_provider(month: pd.Period, p: Paths) -> dict:
    """Best effort: refresh the current month from Dukascopy. A failure (such as a 403) is reported, not raised."""
    try:
        from quantresearch.warehouse import fetch_months

        return fetch_months.run("m1", ["bid", "ask"], str(month), str(month), workers=2, timeout=900, retries=2, min_free_gb=1.0, p=p)
    except Exception as exc:  # The broker fill below still runs.
        return {"error": f"{type(exc).__name__}: {exc}"}


def sync(broker, p: Paths | None = None, since: pd.Timestamp | None = None, provider: bool = True, now: pd.Timestamp | None = None,
         cross: bool = True) -> dict:
    p = p or paths()
    now = (now or pd.Timestamp.now(tz="UTC")).floor("min")
    report: dict = {"started_at": datetime.now(timezone.utc).isoformat()}
    if provider:
        report["provider"] = try_provider(pd.Period(now.strftime("%Y-%m"), "M"), p)
    end = warehouse_end(p)
    report["warehouse_end_before"] = str(end)
    start = since if since is not None else ((end + pd.Timedelta(minutes=1)) if end is not None else now - pd.Timedelta(days=30))
    report["broker_days"] = archive_broker_days(broker, start, now, p)
    months = pd.period_range(pd.Period(start.strftime("%Y-%m"), "M"), pd.Period(now.strftime("%Y-%m"), "M"), freq="M")
    report["months"] = {str(m): build.build_month(m.year, m.month, p) for m in months}
    report["higher"] = build.build_higher_since(months[0], p)
    report["warehouse_end_after"] = str(warehouse_end(p))
    if cross and hasattr(broker, "rates"):
        # Cross-market series from MT5 only: a full Dukascopy retry downloads two decades per symbol, so it stays
        # in `python -m quantresearch.external.store`. A later successful Dukascopy download still takes priority.
        from quantresearch.external import store as ext

        try:
            report["cross"] = ext.save_status(ext.refresh_cross(p, broker, provider=False), p)["status"]
        except Exception as exc:
            report["cross"] = {"error": f"{type(exc).__name__}: {exc}"}
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    p.reports.mkdir(parents=True, exist_ok=True)
    (p.reports / "mt5_sync.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bring the warehouse up to date from Dukascopy, then MT5 bars for any gap.")
    ap.add_argument("--since", help="UTC date or time to copy broker bars from (default: the warehouse's last bar)")
    ap.add_argument("--no-provider", action="store_true", help="Skip the Dukascopy refresh")
    ap.add_argument("--symbol", default=os.environ.get("QR_MT5_SYMBOL", "XAUUSD"))
    args = ap.parse_args(argv)
    from quantresearch.execution.mt5_adapter import Mt5Broker
    from quantresearch.execution.secrets import load_mt5_credentials

    broker = Mt5Broker(symbol=args.symbol, credentials=load_mt5_credentials())
    if not broker.connect():
        print("Could not connect to the MT5 terminal. Is it running and logged in?")
        return 1
    try:
        since = pd.Timestamp(args.since, tz="UTC") if args.since else None
        report = sync(broker, since=since, provider=not args.no_provider)
    finally:
        broker.disconnect()
    print(json.dumps({k: v for k, v in report.items() if k != "months"}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
