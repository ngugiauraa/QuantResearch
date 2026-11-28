"""Part B data and research: weekly bars, ALFRED first releases, extended COT, the multi-market trend portfolio."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from quantresearch.core import timeframes as tfs
from quantresearch.core.config import Paths
from quantresearch.external import alfred
from quantresearch.research import multimarket as mm


# ------------------------------------------------------------ weekly bars
def test_a_week_runs_saturday_to_saturday_so_sunday_evening_starts_the_new_week():
    ts = pd.Series(pd.to_datetime(["2026-11-22 22:05", "2026-11-27 21:59", "2026-11-28 00:00", "2026-11-21 23:00"], utc=True))
    b = tfs.bucket(ts, "w1")
    assert list(b.dt.strftime("%Y-%m-%d")) == ["2026-11-21", "2026-11-21", "2026-11-28", "2026-11-21"]
    assert (tfs.close_times(b, "w1") - b == pd.Timedelta(days=7)).all()


def test_weekly_bars_aggregate_daily_ones_and_respect_as_of(monkeypatch):
    from quantresearch.warehouse import store
    from quantresearch.warehouse.store import synthetic_bars

    daily = synthetic_bars(40, "d1", start="2026-01-03", seed=2, vol=0.01)
    original = store.load_bars

    def fake(tf, start=None, end=None, as_of=None, p=None):
        if tf == "d1":
            out = daily if start is None else daily[daily["ts"] >= pd.Timestamp(start)]
            return out.reset_index(drop=True)
        return original(tf, start, end, as_of, p)
    monkeypatch.setattr(store, "load_bars", fake)
    weeks = original("w1", None, None, as_of=pd.Timestamp("2026-01-31", tz="UTC"), p=Paths("."))
    assert list(weeks["ts"].dt.strftime("%Y-%m-%d")) == ["2026-01-03", "2026-01-10", "2026-01-17", "2026-01-24"]  # The open week is left out.
    first = daily[(daily["ts"] >= "2026-01-03") & (daily["ts"] < "2026-01-10")]
    w = weeks.iloc[0]
    assert w["bid_open"] == first["bid_open"].iloc[0] and w["bid_close"] == first["bid_close"].iloc[-1]
    assert w["bid_high"] == first["bid_high"].max() and w["bid_low"] == first["bid_low"].min()


# ------------------------------------------------------------ ALFRED first releases
def fake_alfred(vintages: dict[str, list[tuple[str, float]]]):
    page = "".join(f'<option value="{d}">' for d in vintages).encode()

    def getter(url, *a, **k):
        if "downloaddata" in url:
            return page
        v = url.split("vintage_date=")[1].split("&")[0]
        rows = "\n".join(f"{d},{x}" for d, x in vintages[v])
        return f"observation_date,PAYEMS_{v.replace('-', '')}\n{rows}\n".encode()
    return getter


def test_first_releases_use_what_was_printed_then_and_skip_corrections(tmp_path):
    vint = {
        "2020-01-10": [("2019-10-01", 100.0), ("2019-11-01", 110.0), ("2019-12-01", 115.0)],
        "2020-01-20": [("2019-10-01", 100.0), ("2019-11-01", 111.0), ("2019-12-01", 116.0)],  # A correction: no new month.
        "2020-02-07": [("2019-11-01", 112.0), ("2019-12-01", 120.0), ("2020-01-01", 130.0)],
    }
    rel = alfred.first_releases("PAYEMS", Paths(tmp_path), getter=fake_alfred(vint), pause=0)
    assert list(rel["period"].dt.strftime("%Y-%m")) == ["2019-12", "2020-01"]
    assert rel["first"].tolist() == [5.0, 10.0]  # December's first print was +5; January's +10.
    assert rel["revision"].iloc[1] == pytest.approx((120.0 - 112.0) - 5.0)  # December revised from +5 to +8.
    ev = alfred.to_events("PAYEMS", rel)
    pub = ev.loc[ev["series"] == "ALFRED_PAYEMS_CHG", "published_ts"].iloc[1]
    assert pub == pd.Timestamp("2020-02-07 13:30", tz="UTC")  # 08:30 New York in winter.
    assert set(ev["series"]) >= {"ALFRED_PAYEMS_CHG", "ALFRED_PAYEMS_REV"}
    # Cached: a second pass makes no requests.
    again = alfred.first_releases("PAYEMS", Paths(tmp_path), getter=lambda *a, **k: pytest.fail("refetched"), pause=0, offline=True)
    assert again["first"].tolist() == [5.0, 10.0]


def test_the_surprise_proxy_needs_twelve_earlier_releases():
    rel = pd.DataFrame({"first": np.arange(14, dtype=float)})
    s = rel["first"] - rel["first"].shift(1).rolling(alfred.SURPRISE_WINDOW, min_periods=alfred.SURPRISE_WINDOW).median()
    assert s.iloc[:12].isna().all() and s.iloc[12] == pytest.approx(12 - 5.5)


# ------------------------------------------------------------ the multi-market portfolio
def synthetic_markets(n=1600, seed=4):
    rng = np.random.default_rng(seed)
    days = pd.date_range("2018-01-01", periods=n, freq="D", tz="UTC")
    out = {}
    for k, sym in enumerate(("AAAm", "BBBm", "CCCm")):
        drift = (0.0006, -0.0004, 0.0)[k]
        close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))
        out[sym] = pd.DataFrame({"ts": days, "open": close, "high": close * 1.002, "low": close * 0.998, "close": close,
                                 "tick_volume": 1.0, "spread": close * 0.0002})
    return out


@pytest.mark.parametrize("variant", list(mm.VARIANTS))
def test_signals_only_use_closed_bars(variant):
    bars = synthetic_markets()["AAAm"]
    full = mm.market_signal(bars, mm.VARIANTS[variant])
    for cut in (900, 1234):
        part = mm.market_signal(bars.iloc[:cut], mm.VARIANTS[variant])
        assert np.allclose(full.iloc[:cut].to_numpy(), part.to_numpy()), (variant, cut)


def test_a_weekly_signal_is_not_used_before_its_week_closes():
    bars = synthetic_markets()["AAAm"]
    sig = mm.market_signal(bars, mm.VARIANTS["mm_tsmom_w1"])
    week = tfs.bucket(bars["ts"], "w1")
    for _, days in sig.groupby(week.to_numpy()):
        assert days.nunique() == 1 or days.index[0].weekday() == 5  # Constant within a week (changes only at the boundary).


def test_stressed_costs_cost_more_and_long_only_is_the_benchmark():
    bars = synthetic_markets()["BBBm"]
    sig = mm.market_signal(bars, mm.VARIANTS["mm_ema_d1"])
    o, s = mm.market_returns(bars, sig), mm.market_returns(bars, sig, mm.STRESS_X)
    assert s["ret"].sum() < o["ret"].sum() and (o["held"].abs() <= mm.MAX_LEV + 1e-9).all()
    bh = mm.market_returns(bars, sig, long_only=True)
    assert (bh["held"] >= 0).all()


def test_a_variant_is_judged_by_the_catalogue_rules(tmp_path):
    from quantresearch.research.catalogue import catalogue_verdict

    data = synthetic_markets()
    report, oos, comp = mm.evaluate_variant("mm_tsmom_d1", data, pd.Timestamp("2021-06-01", tz="UTC"))
    v = catalogue_verdict(report, {"dsr": 0.5, "trials": 300, "expected_max_sr": 0.1})
    assert set(v["checks"]) == {"enough_oos_trades", "oos_positive", "holdout_positive", "stressed_costs_positive", "project_deflated_sharpe", "beats_buy_and_hold"}
    assert v["promising"] is False  # The project bar is not met here.
    assert set(report["markets"]) == set(data) and set(comp) == set(data) and len(oos) > 100


# ------------------------------------------------------------ discovery seed check
def test_reappearance_is_counted_at_each_level(tmp_path):
    import sqlite3

    from quantresearch.discovery import compare
    from quantresearch.discovery import spec as S

    def spec(feature, k):
        return {"timeframe": "h1", "direction": "long", "long": {"op": "gt", "a": {"f": feature}, "b": {"c": k}}}

    def make(run, rows):
        d = tmp_path / run
        d.mkdir()
        con = sqlite3.connect(d / "lab.sqlite")
        con.execute("CREATE TABLE candidates(run_id, cid, spec_hash, dataset, tf, direction, stage, spec_z, family, signature)")
        con.execute("CREATE TABLE oos_series(run_id, cid, sharpe)")
        for cid, h, fam, sig, feat, k, sharpe, ds in rows:
            con.execute("INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?,?,?)", (run, cid, h, ds, "h1", "long", "walk_forward", S.compress(spec(feat, k)), fam, sig))
            con.execute("INSERT INTO oos_series VALUES(?,?,?)", (run, cid, sharpe))
        con.commit()
        con.close()
    make("a", [("a1", "h1", "f1", "s1", "x", 1, 2.0, "real"), ("a2", "h2", "f2", "s2", "y", 1, 1.5, "real"), ("a3", "h3", "f3", "s3", "z", 1, 1.0, "real"),
               ("a4", "h4", "f4", "s4", "w", 1, 0.5, "real"), ("n1", "hn", "fn", "sn", "q", 1, 9.0, "null")])
    make("b", [("b1", "h1", "f1", "s1", "x", 1, 0.1, "real"),    # identical rule
               ("b2", "hX", "fX", "s2", "y", 2, 0.2, "real"),    # same trades, different rule
               ("b3", "hY", "f3", "sY", "z", 3, 0.3, "real"),    # same shape, other constants
               ("b4", "hZ", "fZ", "sZ", "w", 4, 0.4, "real")])   # same features only
    r = compare.compare(compare.load("a", tmp_path), compare.load("b", tmp_path), tops=(4,))
    detail = {c["cid"]: c["in_b_evaluated"] for c in r["tops"]["4"]["candidates"]}
    assert detail == {"a1": "identical_rule", "a2": "identical_trades", "a3": "same_rule_shape", "a4": "same_ingredients"}
    assert r["tops"]["4"]["reappear"]["evaluated"]["identical_rule"] == 1  # Null-market candidates never count.
