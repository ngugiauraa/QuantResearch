import gzip
from datetime import date

import pandas as pd
import pytest

from quantresearch.core.config import Paths
from quantresearch.warehouse import build, fetch_months as fm


def test_command_uses_pinned_tool_and_exclusive_end(tmp_path):
    cmd = fm._command("m1", "ask", date(2024, 1, 1), date(2024, 1, 31), tmp_path, "out")
    assert "dukascopy-node@1.51.1" in cmd
    assert cmd[cmd.index("-to") + 1] == "2024-02-01"
    assert cmd[cmd.index("-p") + 1] == "ask"


def test_coverage_check_rejects_the_original_defect():
    month = pd.Period("2024-01")
    assert "before the last trading day" in fm.verify_coverage(pd.Timestamp("2024-01-30 23:59", tz="UTC"), month, True)
    assert fm.verify_coverage(pd.Timestamp("2024-01-31 23:59", tz="UTC"), month, True) is None
    assert fm.verify_coverage(pd.Timestamp("2024-01-10 23:59", tz="UTC"), month, False) is None


def test_month_ending_on_a_weekend_needs_only_the_last_trading_day():
    assert fm.verify_coverage(pd.Timestamp("2024-08-30 20:59", tz="UTC"), pd.Period("2024-08"), True) is None
    # 2024-03-31 is Easter Sunday and 03-29 is Good Friday: 03-28 is the last trading weekday.
    assert fm.verify_coverage(pd.Timestamp("2024-03-28 20:59", tz="UTC"), pd.Period("2024-03"), True) is None


def test_newest_first_order():
    months = fm.month_list("2024-01", "2024-03")
    assert [str(m) for m in months] == ["2024-03", "2024-02", "2024-01"]


def test_partial_current_month_ends_yesterday():
    begin, end, complete = fm.month_bounds(pd.Period("2026-11"), date(2026, 11, 2))
    assert (begin, end, complete) == (date(2026, 11, 1), date(2026, 11, 1), False)
    assert fm.month_bounds(pd.Period("2026-10"), date(2026, 11, 2))[2]


def write_month(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "timestamp,open,high,low,close,volume\n" + "\n".join(rows) + "\n"
    if path.suffix == ".gz":
        with gzip.open(path, "wt") as f:
            f.write(body)
    else:
        path.write_text(body)


def test_source_priority_and_legacy_fallback(tmp_path):
    p = Paths(tmp_path)
    legacy = p.root / "raw/xauusd/m1/2024/xauusd_bid_m1_2024_01.csv"
    write_month(legacy, ["2024-01-30T00:00:00.000Z,1,2,0.5,1.5,10"])
    frame, inputs, source = build.load_month_side(2024, 1, "bid", p)
    assert source == "legacy" and len(frame) == 1 and len(inputs) == 1
    write_month(fm.m1_path(p, "bid", pd.Period("2024-01")),
                ["2024-01-30T00:00:00.000Z,5,6,4.5,5.5,10", "2024-01-31T00:00:00.000Z,5,6,4.5,5.5,10"])
    frame, inputs, source = build.load_month_side(2024, 1, "bid", p)
    assert source == "monthly" and len(frame) == 2 and frame.open.iloc[0] == 5


def test_build_month_records_each_sides_source(tmp_path):
    p = Paths(tmp_path)
    write_month(fm.m1_path(p, "bid", pd.Period("2024-01")), ["2024-01-31T00:00:00.000Z,5,6,4.5,5.5,10"])
    write_month(fm.m1_path(p, "ask", pd.Period("2024-01")), ["2024-01-31T00:00:00.000Z,5.3,6.3,4.8,5.8,12"])
    meta = build.build_month(2024, 1, p)
    assert meta["sources"] == {"bid": "monthly", "ask": "monthly"}
    bars = build.read_tf("m1", p)
    assert bars.ask_close.iloc[0] == 5.8 and bars.bid_volume.iloc[0] == 10
