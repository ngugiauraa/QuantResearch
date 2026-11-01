import io
import lzma
import struct
import tempfile
import urllib.error
from datetime import date
from pathlib import Path

import pytest

from quantresearch.core.config import Paths
from quantresearch.dukascopy_download import exclusive_end, month_chunks
from quantresearch.warehouse import dukascopy as dk
from quantresearch.warehouse import fetch


def compress(data: bytes) -> bytes:
    return lzma.compress(data, format=lzma.FORMAT_ALONE)


def candle(sec, o, c, l, h, v):
    return struct.pack(">IIIIIf", sec, o, c, l, h, v)


def test_month_end_regression_every_month_reaches_its_last_day():
    # Regression: the last day of each month was lost because -to is exclusive.
    for start, end in month_chunks(date(2024, 1, 1), date(2024, 12, 31)):
        last = date.fromisoformat(exclusive_end(end))
        assert last.day == 1
        assert last > end


def test_exclusive_end_handles_year_end_and_leap_day():
    assert exclusive_end(date(2024, 12, 31)) == "2025-01-01"
    assert exclusive_end(date(2024, 2, 29)) == "2024-03-01"


def test_provider_month_is_zero_based():
    assert "/2024/00/31/BID_candles_min_1.bi5" in dk.m1_url(date(2024, 1, 31), "bid")
    assert "/2026/09/27/10h_ticks.bi5" in dk.tick_url(date(2026, 10, 27), 10)


def test_decode_candles_maps_columns_and_units_and_drops_flats():
    raw = compress(candle(0, 2036365, 2036535, 2036304, 2036685, 0.0045) +
                   candle(60, 2036535, 2036535, 2036535, 2036535, 0.0))
    frame = dk.decode_candles(raw, date(2024, 1, 31))
    assert len(frame) == 1
    row = frame.iloc[0]
    assert (row.open, row.close, row.low, row.high) == (2036.365, 2036.535, 2036.304, 2036.685)
    assert row.volume == 4500
    assert str(row.ts) == "2024-01-31 00:00:00+00:00"
    assert len(dk.decode_candles(raw, date(2024, 1, 31), keep_flat=True)) == 2


def test_decode_ticks_uses_ask_then_bid_order():
    raw = compress(struct.pack(">IIIff", 1500, 2000500, 2000100, 0.00075, 0.00087))
    frame = dk.decode_ticks(raw, date(2024, 1, 2), 10)
    row = frame.iloc[0]
    assert (row.ask, row.bid, row.ask_volume, row.bid_volume) == (2000.5, 2000.1, 750, 870)
    assert str(row.ts) == "2024-01-02 10:00:01.500000+00:00"


def test_html_error_body_is_rejected_not_saved():
    with pytest.raises(dk.CorruptFile):
        dk.validate_payload(b"<html><body><h1>503 Service Unavailable</h1></body></html>", "m1")


def test_truncated_record_is_rejected():
    with pytest.raises(dk.CorruptFile):
        dk.validate_payload(compress(b"\x00" * 25), "m1")


def test_backwards_time_is_rejected():
    raw = compress(candle(60, 1, 1, 1, 1, 1.0) + candle(0, 1, 1, 1, 1, 1.0))
    with pytest.raises(dk.CorruptFile):
        dk.validate_payload(raw, "m1")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _job(tmp_path, name="x.bi5", kind="m1_bid"):
    return fetch.Job("key", kind, "http://example.invalid/x", tmp_path / name)


def test_fetch_retries_then_saves_and_records_checksum(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    calls = []
    good = compress(candle(0, 1, 1, 1, 1, 1.0))

    def opener(request, timeout):
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, None)
        return FakeResponse(good)

    result = fetch.fetch_one(_job(tmp_path), tmp_path, 5, 1, opener)
    assert result["status"] == "ok" and result["attempts"] == 3 and len(result["sha256"]) == 64
    assert (tmp_path / "x.bi5").read_bytes() == good


def test_fetch_404_is_empty_and_writes_nothing(tmp_path):
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "none", {}, None)

    assert fetch.fetch_one(_job(tmp_path), tmp_path, 3, 1, opener)["status"] == "empty"
    assert not (tmp_path / "x.bi5").exists()


def test_fetch_403_stops_rather_than_retrying(tmp_path):
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "no", {}, None)

    with pytest.raises(fetch.AccessDenied):
        fetch.fetch_one(_job(tmp_path), tmp_path, 3, 1, opener)


def test_fetch_gives_up_after_retries_without_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)

    def opener(request, timeout):
        return FakeResponse(b"<html>503</html>")

    result = fetch.fetch_one(_job(tmp_path), tmp_path, 2, 1, opener)
    assert result["status"] == "failed" and "CorruptFile" in result["error"]
    assert not (tmp_path / "x.bi5").exists()


def test_run_resumes_and_skips_completed_files(tmp_path):
    p = Paths(tmp_path)
    jobs = list(fetch.m1_jobs(p, date(2024, 1, 2), date(2024, 1, 3), sides=("bid",)))
    seen = []

    def fake(job, tmp, retries, timeout):
        seen.append(job.key)
        return dict(status="empty", attempts=1, bytes=0, records=0)

    assert fetch.run(jobs, workers=1, p=p, fetcher=fake)["empty"] == 2
    second = fetch.run(jobs, workers=1, p=p, fetcher=fake)
    assert second["skipped"] == 2 and len(seen) == 2


def test_run_stops_on_access_denied_and_commits_progress(tmp_path):
    p = Paths(tmp_path)
    jobs = list(fetch.m1_jobs(p, date(2024, 1, 2), date(2024, 1, 9), sides=("bid",)))

    def fake(job, tmp, retries, timeout):
        raise fetch.AccessDenied("403")

    result = fetch.run(jobs, workers=1, p=p, fetcher=fake)
    assert result["stop_reason"] == "403"
