"""Gap filling from the broker: the provider always wins, broker rows are flagged and replaced by a later provider download."""
from datetime import date

import pandas as pd

from quantresearch.core.config import Paths
from quantresearch.warehouse import build, mt5_sync, store
from quantresearch.warehouse.live import broker_rows_to_bars
from test_warehouse_build import write_day


class FakeBroker:
    """Mt5Broker.bars_m1 stand-in: one bar per minute from `first` for `n` minutes."""

    def __init__(self, first: str, n: int):
        start = pd.Timestamp(first, tz="UTC")
        self.rows = [{"ts": (start + pd.Timedelta(minutes=i)).to_pydatetime(), "open": 2000.0, "high": 2001.0, "low": 1999.0, "close": 2000.5,
                      "tick_volume": 4.0, "spread": 0.25} for i in range(n)]

    def bars_m1(self, start, end):
        return [r for r in self.rows if start <= r["ts"] < end]


def provider_days(p: Paths, *days: date):
    for day in days:
        write_day(p, "bid", day)
        write_day(p, "ask", day, spread=300)


def test_broker_fills_only_days_the_provider_lacks(tmp_path):
    p = Paths(tmp_path)
    provider_days(p, date(2024, 1, 30))
    broker = FakeBroker("2024-01-30 00:00", 2 * 1440)  # 30th (provider has it) and 31st (it does not)
    frame = broker.bars_m1(pd.Timestamp("2024-01-30", tz="UTC"), pd.Timestamp("2024-02-01", tz="UTC"))
    for day in (date(2024, 1, 30), date(2024, 1, 31)):
        rows = [r for r in frame if r["ts"].date() == day]
        target = build.broker_m1_path(p, day)
        target.parent.mkdir(parents=True, exist_ok=True)
        broker_rows_to_bars(rows).to_parquet(target)
    meta = build.build_month(2024, 1, p)
    bars = build.read_tf("m1", p)
    on_30 = bars[bars["ts"].dt.day == 30]
    on_31 = bars[bars["ts"].dt.day == 31]
    # The provider's minutes on the 30th stay provider rows; broker rows fill only after the provider's last bar.
    assert ((on_30["flags"].iloc[:3] & build.FLAG_BROKER) == 0).all() and ((on_30["flags"].iloc[3:] & build.FLAG_BROKER) != 0).all()
    assert (on_31["flags"] & build.FLAG_BROKER).all() and len(on_31) == 1440
    assert "broker" in meta["sources"]
    assert not bars["ts"].duplicated().any()


def test_a_later_provider_download_replaces_broker_rows(tmp_path):
    p = Paths(tmp_path)
    provider_days(p, date(2024, 1, 30))
    mt5_sync.sync(FakeBroker("2024-01-31 00:00", 1440), p, since=pd.Timestamp("2024-01-31", tz="UTC"), provider=False,
                  now=pd.Timestamp("2024-02-01", tz="UTC"))
    assert ((build.read_tf("m1", p)["flags"] & build.FLAG_BROKER) != 0).sum() == 1440
    provider_days(p, date(2024, 1, 31))
    build.build_month(2024, 1, p)
    bars = build.read_tf("m1", p)
    on_31 = bars[bars["ts"].dt.day == 31]
    # The provider's minutes replace the broker's; broker minutes remain only after the provider's last bar.
    assert ((on_31["flags"].iloc[:3] & build.FLAG_BROKER) == 0).all()
    assert ((on_31["flags"] & build.FLAG_BROKER) != 0).sum() == 1440 - 3
    assert not bars["ts"].duplicated().any()


def test_sync_archives_days_builds_months_and_higher_timeframes(tmp_path):
    p = Paths(tmp_path)
    provider_days(p, date(2024, 1, 30))
    build.build_month(2024, 1, p)
    build.build_higher(p)
    report = mt5_sync.sync(FakeBroker("2024-01-30 00:03", 3 * 1440), p, provider=False, now=pd.Timestamp("2024-02-01 12:00", tz="UTC"))
    assert set(report["broker_days"]) == {"2024-01-30", "2024-01-31", "2024-02-01"}
    assert set(report["months"]) == {"2024-01", "2024-02"}
    assert report["warehouse_end_after"].startswith("2024-02-01 11:59")
    h1 = store.load_bars("h1", p=p)
    assert h1["ts"].iloc[-1] == pd.Timestamp("2024-02-01 11:00", tz="UTC")
    assert h1[h1["ts"] == pd.Timestamp("2024-01-31 05:00", tz="UTC")]["m1_count"].iloc[0] == 60
    assert (p.reports / "mt5_sync.json").exists()


def test_a_rerun_extends_a_partial_day_without_losing_earlier_minutes(tmp_path):
    p = Paths(tmp_path)
    broker = FakeBroker("2024-01-31 00:00", 1440)
    mt5_sync.sync(broker, p, since=pd.Timestamp("2024-01-31", tz="UTC"), provider=False, now=pd.Timestamp("2024-01-31 06:00", tz="UTC"))
    mt5_sync.sync(broker, p, provider=False, now=pd.Timestamp("2024-01-31 12:00", tz="UTC"))
    day = pd.read_parquet(build.broker_m1_path(p, date(2024, 1, 31)))
    assert len(day) == 720
