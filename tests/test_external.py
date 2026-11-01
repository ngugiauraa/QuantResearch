import io
import json
import zipfile
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from quantresearch.core.config import Paths
from quantresearch.external import sources as src, store
from quantresearch.features import pit
from quantresearch.warehouse.store import synthetic_bars


def test_fred_parser_drops_missing_values_and_applies_publication_lag():
    raw = b"observation_date,DGS10\n2024-01-02,3.95\n2024-01-03,.\n2024-01-04,4.00\n"
    frame = src.parse_fred_csv(raw, "DGS10", timedelta(days=1, hours=22))
    assert frame.value.tolist() == [3.95, 4.0]
    assert frame.published_ts.iloc[0] == pd.Timestamp("2024-01-03 22:00", tz="UTC")
    assert (frame.pub_basis == "assumed_lag").all()


def test_a_value_is_invisible_before_its_assumed_publication():
    frame = src.parse_fred_csv(b"observation_date,DGS10\n2024-01-02,3.95\n", "DGS10", timedelta(days=1, hours=22))
    assert pit.as_of(frame, "2024-01-03 21:59").empty
    assert len(pit.as_of(frame, "2024-01-03 22:00")) == 1


def test_fomc_historical_parser_ignores_other_text():
    html = """<h5>January 28-29 Meeting - 2015</h5><p>Statement</p>
              <h5>March 17-18 Meeting - 2015</h5><h5>Conference Call - 2015</h5>"""
    assert src.parse_fomc_historical(html, 2015) == [pd.Timestamp("2015-01-29"), pd.Timestamp("2015-03-18")]


def test_fomc_current_parser_uses_decision_day_and_handles_split_months():
    html = """<h4>2026 FOMC Meetings</h4>
    <div class="fomc-meeting__month"><strong>January</strong></div><div class="fomc-meeting__date">27-28</div>
    <div class="fomc-meeting__month"><strong>Apr/May</strong></div><div class="fomc-meeting__date">28-29</div>
    <div class="fomc-meeting__month"><strong>March</strong></div><div class="fomc-meeting__date">17-18*</div>"""
    dates = src.parse_fomc_current(html)
    assert pd.Timestamp("2026-01-28") in dates and pd.Timestamp("2026-03-18") in dates


def test_fomc_time_is_14_00_new_york_across_dst():
    e = src.fomc_events([pd.Timestamp("2024-01-31"), pd.Timestamp("2024-07-31")])
    assert e.event_ts.tolist() == [pd.Timestamp("2024-01-31 19:00", tz="UTC"), pd.Timestamp("2024-07-31 18:00", tz="UTC")]


def cot_zip():
    frame = pd.DataFrame({
        "Market_and_Exchange_Names": ["GOLD - COMMODITY EXCHANGE INC.", "SILVER - COMMODITY EXCHANGE INC."],
        "Report_Date_as_YYYY-MM-DD": ["2024-01-02", "2024-01-02"],
        "M_Money_Positions_Long_All": [100, 1], "M_Money_Positions_Short_All": [40, 1],
        "Prod_Merc_Positions_Long_All": [10, 1], "Prod_Merc_Positions_Short_All": [30, 1],
        "Swap_Positions_Long_All": [5, 1], "Swap__Positions_Short_All": [8, 1],
        "Open_Interest_All": [500, 1],
    })
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("f_year.txt", frame.to_csv(index=False))
    return buffer.getvalue()


def test_cot_uses_friday_publication_not_tuesday_report_date():
    frame = src.parse_cot(cot_zip())
    net = frame[frame.series == "COT_MM_NET"].iloc[0]
    assert net.value == 60
    assert net.event_ts == pd.Timestamp("2024-01-02", tz="UTC")
    assert net.published_ts == pd.Timestamp("2024-01-05 20:30", tz="UTC")
    assert set(frame.series) == {"COT_MM_NET", "COT_PROD_NET", "COT_SWAP_NET", "COT_OI"}


def test_gdelt_parser_and_empty_response():
    raw = json.dumps({"articles": [{"url": "u", "title": "Gold rises", "seendate": "20260110T121500Z", "domain": "d", "language": "English"}]}).encode()
    frame = src.parse_gdelt(raw)
    assert frame.title.iloc[0] == "Gold rises"
    assert frame.published_ts.iloc[0] == pd.Timestamp("2026-01-10 12:15", tz="UTC")
    assert src.parse_gdelt(b'{"articles": []}').empty


def test_one_failed_source_does_not_stop_others(tmp_path, monkeypatch):
    p = Paths(tmp_path)

    def fake(series, getter=None):
        if series == "DGS10":
            raise OSError("down")
        return src.parse_fred_csv(b"d,v\n2024-01-02,1\n", series, timedelta(days=1))

    monkeypatch.setattr(src, "fetch_fred", fake)
    monkeypatch.setattr(src, "fetch_fomc", lambda: src.fomc_events([pd.Timestamp("2024-01-31")]))
    monkeypatch.setattr(src, "fetch_cot", lambda: src.parse_cot(cot_zip()))
    report = store.refresh_all(p, cross=False)
    assert "failed" in report["status"]["DGS10"]
    assert report["status"]["DGS2"] == "ok" and report["status"]["fomc"].startswith("ok")
    assert (store.read_events(p).series == "DGS2").any()


def test_macro_features_respect_publication_and_system_runs_without_data(tmp_path):
    bars = synthetic_bars(500, "h1", start="2024-01-01")
    p = Paths(tmp_path)
    empty = store.macro_features(bars, p=p)
    assert empty.isna().all().all()
    events = src.parse_fred_csv(b"d,v\n2024-01-02,3.0\n2024-01-03,3.1\n", "DGS10", timedelta(days=1, hours=22))
    out = store.macro_features(bars, events)
    before = bars.close_ts < pd.Timestamp("2024-01-03 22:00", tz="UTC")
    assert out.loc[before, "yield_10y"].isna().all()
    assert out.loc[~before, "yield_10y"].iloc[0] == 3.0


def test_macro_truncation_invariance():
    bars = synthetic_bars(400, "h1", start="2024-01-01")
    events = src.parse_fred_csv(b"d,v\n2024-01-02,3.0\n2024-01-05,3.1\n2024-01-09,3.2\n", "DGS10", timedelta(days=1, hours=22))
    full = store.macro_features(bars, events)
    cut = store.macro_features(bars.iloc[:100].reset_index(drop=True), events)
    assert np.allclose(full.iloc[:100]["yield_10y"], cut["yield_10y"], equal_nan=True)


def test_event_proximity_counts_hours_to_next_event():
    times = pd.Series(pd.to_datetime(["2024-01-01 00:00", "2024-01-01 12:00", "2024-01-03 00:00"], utc=True))
    schedule = pd.Series(pd.to_datetime(["2024-01-02 00:00"], utc=True))
    out = store.event_proximity(times, schedule)
    assert out.iloc[0] == pytest.approx(24) and out.iloc[1] == pytest.approx(12)
    assert np.isnan(out.iloc[2])


def test_attach_cross_uses_only_closed_cross_bars(tmp_path):
    p = Paths(tmp_path)
    cross = pd.DataFrame({"ts": pd.date_range("2024-01-02 09:00", periods=3, freq="h", tz="UTC"), "open": 1.0, "high": 1.0, "low": 1.0,
                          "close": [10.0, 20.0, 30.0], "volume": 1.0})
    path = p.external / "cross" / "xagusd_h1.parquet"
    path.parent.mkdir(parents=True)
    cross.to_parquet(path)
    base = synthetic_bars(3, "m5", start="2024-01-02 10:00")
    out = store.attach_cross(base, ["xagusd", "missing"], p)
    # At 10:05 only the 09:00 bar (closed 10:00) has finished.
    assert out.xagusd_close.iloc[0] == 10.0
    assert out.missing_close.isna().all()
