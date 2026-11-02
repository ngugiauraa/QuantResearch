"""Strategy discovery: format, causality, isolation, reproducibility, accounting and safety.

Synthetic markets exercise the software only. They say nothing about gold.
"""
from __future__ import annotations

import ast
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantresearch.core.config import Paths
from quantresearch.discovery import confirm, evaluate as E, expr, features as feat, kernel, search, spec as S, store, validate as V
from quantresearch.discovery.config import DiscoveryConfig, make_vintage
from quantresearch.warehouse.store import synthetic_bars

SRC = Path(__file__).resolve().parents[1] / "src"
HOLDOUT = pd.Timestamp("2023-06-01", tz="UTC")


def synthetic_set(seed: int = 3, start: str = "2018-01-01 22:00", days: int = 5 * 365) -> dict:
    m15 = synthetic_bars(days * 96, "m15", start=start, seed=seed, vol=0.0012, spread=0.3)
    m15 = m15[m15["ts"].dt.day_of_week != 5].reset_index(drop=True)  # a weekly closure, so gap logic runs
    out = {"m15": m15}
    for tf in ("h1", "h4", "d1ny"):
        out[tf] = feat.finish_bars(feat.aggregate(m15, tf), tf)
    return out


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    root = tmp_path_factory.mktemp("store") / "real"
    bars = synthetic_set()
    cut = {tf: b[b["close_ts"] <= HOLDOUT].reset_index(drop=True) for tf, b in bars.items()}
    store.build("real", ("h1",), out_root=root, bars_override=cut, holdout_start=HOLDOUT, log=lambda m: None)
    return {"root": root, "bars": bars, "fs": store.FeatureStore(root / "h1", verify=True)}


SPECS = [
    {"timeframe": "h1", "direction": "both", "long": {"op": "lt", "a": {"f": "rsi14"}, "b": {"c": 35}},
     "short": {"op": "gt", "a": {"f": "rsi14"}, "b": {"c": 65}}, "stop": {"type": "atr", "mult": 1.5}, "target": {"type": "r", "r": 1.5},
     "exit": {"max_hold": 12}},
    {"timeframe": "h1", "direction": "long", "long": {"op": "gt", "a": {"f": "dist_dch20"}, "b": {"c": 0}},
     "entry": {"type": "stop", "offset_atr": 0.2, "expiry": 3}, "stop": {"type": "swing", "lookback": 10, "buffer_atr": 0.2},
     "target": {"type": "r", "r": 2.0}, "exit": {"max_hold": 48, "trail_atr": 2.0, "breakeven_r": 1.0, "day_end": True}},
    {"timeframe": "h1", "direction": "short", "short": {"op": "and", "args": [{"op": "gt", "a": {"f": "z20"}, "b": {"c": 1.0}},
                                                                              {"op": "in", "a": {"f": "ny_hour"}, "set": [8, 9, 10, 11]}]},
     "entry": {"type": "limit", "offset_atr": 0.3, "expiry": 2}, "stop": {"type": "atr", "mult": 2}, "target": {"type": "atr", "mult": 1.0},
     "exit": {"max_hold": 10, "short_exit": {"op": "lt", "a": {"f": "z20"}, "b": {"c": 0}}}},
]


# ---------------------------------------------------------------- strategy format
def test_valid_specs_pass_and_bad_specs_are_refused(synth):
    names = synth["fs"].names
    for sp in SPECS:
        assert S.validate(sp, names, "h1") == []
    bad = [
        ({**SPECS[0], "long": {"op": "lt", "a": {"f": "no_such_feature"}, "b": {"c": 1}}}, "unknown"),
        ({**SPECS[0], "long": {"op": "lt", "a": {"op": "lag", "a": {"f": "rsi14"}, "n": -1}, "b": {"c": 1}}}, "no negative lags"),
        ({**SPECS[0], "long": {"op": "lt", "a": {"f": "cpi"}, "b": {"c": 1}}}, "forbidden"),
        ({**SPECS[0], "stop": {"type": "none"}}, "needs a stop"),
        ({**SPECS[0], "stop": {"type": "atr", "mult": 0.1}}, "outside"),
        ({**SPECS[0], "long": {"op": "eval", "code": "__import__('os')"}}, "boolean operator"),
        ({**SPECS[0], "direction": "both", "short": None}, "needs a short"),
        ({**SPECS[0], "instrument": "EURUSD"}, "XAUUSD"),
    ]
    for sp, words in bad:
        errors = S.validate(sp, names, "h1")
        assert errors and any(words in e for e in errors), (words, errors)
    assert S.validate("not a spec", names) and S.validate({"long": 5}, names)  # never raises


def test_hash_is_canonical_and_ignores_irrelevant_parameters():
    a = {"timeframe": "h1", "direction": "long", "long": {"op": "and", "args": [{"op": "gt", "a": {"f": "x"}, "b": {"c": 1}}, {"op": "lt", "a": {"f": "y"}, "b": {"c": 2}}]}}
    b = {"timeframe": "h1", "direction": "long", "long": {"op": "and", "args": [{"op": "lt", "a": {"f": "y"}, "b": {"c": 2.0}}, {"op": "gt", "a": {"f": "x"}, "b": {"c": 1.0}}]},
         "entry": {"type": "market", "offset_atr": 0.5}, "meta": {"origin": "anything"}, "short": {"op": "is", "a": {"f": "z"}}}
    assert S.spec_hash(a) == S.spec_hash(b)
    c = json.loads(json.dumps(a))
    c["long"]["args"][0]["b"]["c"] = 1.5
    assert S.spec_hash(c) != S.spec_hash(a)


def test_screen_records_duplicates_and_equivalent_rules(synth):
    task = {"store_root": str(synth["root"]), "tf": "h1", "seed": 1, "vintage": make_vintage("2021-06-01", 3, 1, HOLDOUT).as_dict(),
            "params": {}, "task_id": "t", "kind": "random"}
    ctx = search.make_context(task)
    sp = SPECS[0]
    first = search.screen(ctx, sp, "test")
    again = search.screen(ctx, json.loads(json.dumps(sp)), "test")
    rounded = json.loads(json.dumps(sp))
    rounded["long"]["b"]["c"] = 35.0000001  # canonicalisation rounds constants: the same experiment
    assert search.screen(ctx, rounded, "test") is first
    tweaked = json.loads(json.dumps(sp))
    tweaked["long"] = {"op": "and", "args": [sp["long"], {"op": "lt", "a": {"f": "rsi14"}, "b": {"c": 90}}]}  # different rule, same trades
    third = search.screen(ctx, tweaked, "test")
    reasons = [r["reason"] for r in ctx.records]
    assert reasons[1] == "duplicate" and reasons[2] == "duplicate" and again is first
    assert third["reason"] == "equivalent" and third["hash"] != first["hash"]


# ---------------------------------------------------------------- execution: kernel against the authoritative engine
@pytest.mark.parametrize("cost", ["observed", "stress", "spread_x2_equiv"])
def test_kernel_matches_event_engine(synth, cost):
    fs = synth["fs"]
    a, b = fs.bounds(pd.Timestamp("2019-01-01", tz="UTC"), pd.Timestamp("2020-06-01", tz="UTC"))
    for sp in SPECS:
        ev = expr.Evaluator(fs, a, b)
        c = expr.Costs(**{**expr.SCENARIOS[cost].as_dict(), "swap": False})
        _, tr = E.evaluate(sp, ev, c)
        tf = confirm.engine_run(sp, ev, c).trades_frame()
        assert len(tr) == len(tf) and len(tr) > 10
        assert np.allclose(tr.pnl_oz, (tf["net"] / (tf["lots"] * 100)).to_numpy(), atol=1e-9)
        assert (np.asarray(fs.open_ns)[tr.lo + tr.entry] == pd.DatetimeIndex(tf["entry_time"]).as_unit("ns").asi8).all()


def test_compiled_and_python_kernels_agree(synth):
    fs = synth["fs"]
    a, b = fs.bounds(pd.Timestamp("2019-01-01", tz="UTC"), pd.Timestamp("2019-09-01", tz="UTC"))
    ev = expr.Evaluator(fs, a, b)
    c = expr.compile_spec(SPECS[1], ev)
    fast = expr.run_compiled(c, ev, expr.SCENARIOS["stress"])
    import quantresearch.discovery.expr as X
    original = X.simulate
    try:
        X.simulate = kernel.python_simulate
        slow = expr.run_compiled(c, ev, expr.SCENARIOS["stress"])
    finally:
        X.simulate = original
    assert np.array_equal(fast.entry, slow.entry) and np.allclose(fast.pnl_oz, slow.pnl_oz)


def _bars(rows):
    frame = pd.DataFrame(rows, columns=["bid_open", "bid_high", "bid_low", "bid_close"])
    for c in ("open", "high", "low", "close"):
        frame[f"ask_{c}"] = frame[f"bid_{c}"] + 0.2
    return frame


def _kernel(frame, want_long, stop, target, entry_type=2, offset=1.0, costs=(0.0, 0.0, 0.0), swap=None):
    n = len(frame)
    a = {k: frame[k].to_numpy(float) for k in frame}
    mid = (a["bid_close"] + a["ask_close"]) / 2
    z = np.zeros(n, bool)
    nan = np.full(n, np.nan)
    sw = swap if swap is not None else np.zeros(n)
    return kernel.python_simulate(a["bid_open"], a["bid_high"], a["bid_low"], a["bid_close"], a["ask_open"], a["ask_high"], a["ask_low"], a["ask_close"], mid,
                                  want_long, z, np.full(n, stop), np.full(n, stop), np.full(n, target), nan, z, z, z, nan, np.full(n, offset),
                                  entry_type, 3, 50, False, False, np.nan, costs[0], costs[1], costs[2], 0, 0.0, sw, np.zeros(n), 0, n)


def test_intrabar_limit_fill_cannot_also_take_its_target_on_the_same_bar():
    # Signal at bar 0 close (mid 100.1). Buy limit at 99.1. Bar 1 dips to 99 then (we cannot know when) reaches 100.5.
    frame = _bars([[100, 100.2, 99.8, 100.0], [100, 100.5, 98.8, 99.5], [99.5, 99.6, 99.4, 99.5], [99.5, 101, 99.4, 100.8]])
    want = np.array([True, False, False, False])
    out = _kernel(frame, want, stop=2.0, target=1.0)
    entry, exit_, reason = out[0], out[1], out[8]
    assert entry[0] == 1 and exit_[0] == 3 and kernel.REASONS[reason[0]] == "target"  # target only on a LATER bar


def test_costs_are_charged_exactly():
    frame = _bars([[100, 100.1, 99.9, 100.0], [100, 100.1, 99.9, 100.0], [100, 100.1, 99.9, 100.0], [101, 101.1, 100.9, 101.0]])
    want = np.array([True, False, False, False])
    swap = np.array([0.0, 0.0, -0.5, -1.0])  # cumulative financing per ounce
    base = _kernel(frame, want, stop=5.0, target=np.nan, entry_type=0)
    cost = _kernel(frame, want, stop=5.0, target=np.nan, entry_type=0, costs=(0.3, 0.05, 0.07), swap=swap)
    # extra spread on the ask entry, slippage on entry only (end-of-data exit at the close), commission both sides, financing.
    assert np.isclose(base[6][0] - cost[6][0], 0.3 + 0.05 + 2 * 0.07 + 1.0)


# ---------------------------------------------------------------- causality and look-ahead
def test_features_are_truncation_invariant(synth):
    bars = synth["bars"]
    cut_at = pd.Timestamp("2020-03-13 15:00", tz="UTC")
    full_prices, full = feat.build_catalog("h1", bars["h1"], {h: bars[h] for h in ("h4", "d1ny")})
    keep = {tf: b[b["close_ts"] <= cut_at].reset_index(drop=True) for tf, b in bars.items()}
    cut_prices, cut = feat.build_catalog("h1", keep["h1"], {h: keep[h] for h in ("h4", "d1ny")})
    n = len(cut_prices)
    assert n > 1000
    for name in full.columns:
        a, b = full.columns[name][:n], cut.columns[name]
        assert np.allclose(a, b, equal_nan=True, rtol=1e-5, atol=1e-6), name


def test_higher_timeframe_values_come_only_from_closed_bars(synth):
    fs, bars = synth["fs"], synth["bars"]
    h4 = feat.compact_htf(bars["h4"])
    vals = np.asarray(fs.feature("h4_rsi"))
    close = np.asarray(fs.close_ns)
    h4_close = pd.DatetimeIndex(h4["close_ts"]).as_unit("ns").asi8
    for i in range(3000, 3300):
        j = np.searchsorted(h4_close, close[i], side="right") - 1  # last h4 bar CLOSED by this bar's close
        assert np.isclose(vals[i], h4["rsi"].iloc[j], equal_nan=True)


def test_events_appear_only_after_publication():
    ts = pd.Series(pd.date_range("2020-01-01", periods=200, freq="h", tz="UTC"))
    bars = synthetic_bars(200, "h1", start="2020-01-01 00:00")
    events = pd.DataFrame({"series": "DFII10", "event_ts": [pd.Timestamp("2020-01-02", tz="UTC")], "published_ts": [pd.Timestamp("2020-01-04 12:00", tz="UTC")],
                           "ingested_ts": pd.Timestamp("2026-01-01", tz="UTC"), "revision": 0, "value": [1.25], "source": "test"})
    _, cat = feat.build_catalog("h1", bars, {}, {}, events)
    seen = np.asarray(cat.columns["real_yield"])
    close = bars["close_ts"]
    assert np.isnan(seen[(close < pd.Timestamp("2020-01-04 12:00", tz="UTC")).to_numpy()]).all()
    assert np.isclose(seen[(close >= pd.Timestamp("2020-01-04 12:00", tz="UTC")).to_numpy()], 1.25).all()
    assert ts is not None


def test_revised_macro_series_are_excluded(synth, tmp_path):
    p = Paths(tmp_path)
    from quantresearch.external import store as ext
    from quantresearch.external import sources as src
    from datetime import timedelta
    ext.write_events("cpi", src.parse_fred_csv(b"d,v\n2020-01-01,250\n", "CPIAUCSL", timedelta(days=40)), p)
    ext.write_events("tips", src.parse_fred_csv(b"d,v\n2020-01-02,1.5\n", "DFII10", timedelta(days=1)), p)
    kept = store._events(HOLDOUT, p)
    assert "CPIAUCSL" not in set(kept["series"]) and "DFII10" in set(kept["series"])
    assert not any(n.startswith(("cpi", "payrolls", "gdp")) for n in synth["fs"].names)


def test_candidate_trades_do_not_depend_on_future_bars(synth):
    """Full-history store versus a store truncated at T: identical trades for everything decided before T."""
    bars = synth["bars"]
    cut_at = pd.Timestamp("2020-06-10 00:00", tz="UTC")
    tmp = synth["root"].parent / "trunc"
    keep = {tf: b[b["close_ts"] <= cut_at].reset_index(drop=True) for tf, b in bars.items()}
    store.build("real", ("h1",), out_root=tmp, bars_override=keep, holdout_start=HOLDOUT, log=lambda m: None)
    full, short = synth["fs"], store.FeatureStore(tmp / "h1")
    start = pd.Timestamp("2019-06-01", tz="UTC")
    for sp in SPECS:
        a1, b1 = full.bounds(start, cut_at)
        a2, b2 = short.bounds(start, cut_at)
        _, t1 = E.evaluate(sp, expr.Evaluator(full, a1, b1), "observed")
        _, t2 = E.evaluate(sp, expr.Evaluator(short, a2, b2), "observed")
        assert np.array_equal(t1.entry, t2.entry) and np.allclose(t1.pnl_oz, t2.pnl_oz)


def test_walk_forward_isolation_search_ignores_data_after_its_cutoff(synth, tmp_path):
    """Scrambling every feature and price after the vintage cutoff must not change one search result."""
    import shutil
    vin = make_vintage("2021-06-01", 3, 1, HOLDOUT)
    copy_root = tmp_path / "real"
    shutil.copytree(synth["root"], copy_root)
    folder = copy_root / "h1"
    fs = store.FeatureStore(folder)
    cut = int(np.searchsorted(fs.open_ns, vin.cutoff.as_unit("ns").value))
    del fs
    store._OPEN.clear()
    mat = np.load(folder / "features.npy", mmap_mode="r+")
    rng = np.random.default_rng(0)
    mat[:, cut:] = rng.normal(size=mat[:, cut:].shape).astype("float32") * 50
    mat.flush()
    del mat
    pr = np.load(folder / "prices.npy")
    pr[:, cut:] *= 1.7
    np.save(folder / "prices.npy", pr)
    task = {"tf": "h1", "seed": 5, "vintage": vin.as_dict(), "kind": "gp", "task_id": "x", "params": {"population": 16, "generations": 3}}
    a = search.run_search({**task, "store_root": str(synth["root"])})
    store._OPEN.clear()
    b = search.run_search({**task, "store_root": str(copy_root)})
    store._OPEN.clear()
    strip = lambda recs: [{k: v for k, v in r.items() if k not in ("spec",)} for r in recs]  # noqa: E731
    assert strip(a["records"]) == strip(b["records"]) and a["selected"] == b["selected"]


def test_search_task_is_deterministic(synth):
    vin = make_vintage("2021-06-01", 3, 1, HOLDOUT).as_dict()
    for kind, params in (("gp", {"population": 12, "generations": 2}), ("template", {"template": "volatility_squeeze_breakout", "budget": 10}),
                         ("miner", {"horizon": 8}), ("symreg", {"horizon": 8, "population": 10, "generations": 2})):
        t = {"store_root": str(synth["root"]), "tf": "h1", "seed": 9, "vintage": vin, "kind": kind, "task_id": kind, "params": params}
        a, b = search.run_search(t), search.run_search(t)
        assert [r.get("hash") for r in a["records"]] == [r.get("hash") for r in b["records"]] and a["selected"] == b["selected"], kind
        assert len(a["records"]) > 3


# ---------------------------------------------------------------- holdout protection
def test_store_refuses_holdout_data(synth, tmp_path):
    bars = synth["bars"]
    early = pd.Timestamp("2022-06-01", tz="UTC")  # the synthetic market runs to the end of 2022
    with pytest.raises(store.HoldoutBreach):
        store.build("real", ("h1",), out_root=tmp_path / "x", bars_override=bars, holdout_start=early, log=lambda m: None)
    meta = json.loads((synth["root"] / "h1" / "meta.json").read_text())
    meta["last_close"] = str(HOLDOUT + pd.Timedelta(hours=5))
    folder = tmp_path / "bad"
    import shutil
    shutil.copytree(synth["root"] / "h1", folder)
    (folder / "meta.json").write_text(json.dumps(meta))
    with pytest.raises(store.HoldoutBreach):
        store.FeatureStore(folder)


def test_holdout_look_needs_qualification_and_happens_once(tmp_path, monkeypatch):
    from quantresearch.discovery import holdout, lab
    from quantresearch.discovery.config import run_dir
    p = Paths(tmp_path)
    con = lab.connect(run_dir("h", p) / "lab.sqlite")
    sp = S.normalize(SPECS[0])
    con.execute("INSERT INTO candidates(run_id, cid, spec_hash, dataset, tf, vintage, stage, spec_z) VALUES('h','c1',?,'real','h1','2021-06-01','walk_forward',?)",
                (S.spec_hash(sp), S.compress(sp)))
    con.commit()
    with pytest.raises(holdout.NotQualified):
        holdout.confirm_once("h", "c1", p)
    con.execute("UPDATE candidates SET stage='qualified' WHERE cid='c1'")
    con.execute("INSERT INTO holdout_access VALUES(?,?,?,?,?)", (S.spec_hash(sp), "h", "c1", "earlier", "{}"))
    con.commit()
    with pytest.raises(holdout.HoldoutAlreadyUsed):
        holdout.confirm_once("h", "c1", p)


# ---------------------------------------------------------------- statistics and multiple testing
def test_stage3_counts_every_out_of_sample_look_and_rejects_noise():
    rng = np.random.default_rng(1)
    pool = []
    for i in range(60):
        r = rng.normal(0, 0.005, 1500)
        pool.append({"hash": f"n{i}", "tf": "h1", "vintage": "2015-01-01", "rets": r, "sharpe": E.sharpe(r), "stage2_passed": i % 3 == 0})
    strong = rng.normal(0.0012, 0.005, 1500)
    pool.append({"hash": "strong", "tf": "h1", "vintage": "2015-01-01", "rets": strong, "sharpe": E.sharpe(strong), "stage2_passed": True})
    nulls = {"h1": list(rng.normal(0, 0.4, 200))}
    out = V.stage3(pool, nulls)
    assert set(out) == {c["hash"] for c in pool if c["stage2_passed"]}
    assert all(v["oos_looks"] == 61 for v in out.values())  # failures count as looks too
    assert not any(out[h]["passed"] for h in out if h != "strong")
    assert out["strong"]["passed"], out["strong"]
    # With the trial spread held fixed, more looks raise the bar: the expected best of N grows with N.
    from quantresearch.research import stats as rstats
    trials = rng.normal(0, 0.03, 5000)
    few, many = rstats.deflated_sharpe(strong, list(trials[:20])), rstats.deflated_sharpe(strong, list(trials))
    assert many["expected_max_sr"] > few["expected_max_sr"] and many["dsr"] <= few["dsr"]


def test_null_gate_blocks_when_null_calibration_is_missing():
    rng = np.random.default_rng(2)
    r = rng.normal(0.002, 0.005, 1500)
    out = V.stage3([{"hash": "a", "tf": "h1", "vintage": "v", "rets": r, "sharpe": E.sharpe(r), "stage2_passed": True}], {})
    assert not out["a"]["passed"] and "null_calibration_unavailable" in out["a"]["reasons"]


def test_pbo_is_high_for_noise_and_low_for_a_real_effect():
    rng = np.random.default_rng(3)
    noise = rng.normal(0, 1, (2000, 20))
    assert V.pbo(noise) > 0.25
    signal = noise.copy()
    signal[:, 0] += 0.3
    assert V.pbo(signal) < 0.1


def test_null_market_has_no_direction_but_keeps_size_and_spread(synth):
    m15 = synth["bars"]["m15"].iloc[:20000].reset_index(drop=True)
    null = feat.null_bars(m15, seed=4)
    real_r = np.log(m15["close"] / m15["open"]).abs().to_numpy()
    mid = (null["bid_close"] + null["ask_close"]) / 2
    mid_o = (null["bid_open"] + null["ask_open"]) / 2
    null_r = np.log(mid / mid_o)
    assert np.allclose(np.abs(null_r), real_r, atol=1e-9)
    assert np.allclose((null["ask_close"] - null["bid_close"]).to_numpy(), (m15["ask_close"] - m15["bid_close"]).to_numpy())
    assert abs(pd.Series(np.sign(null_r)).autocorr()) < 0.03


# ---------------------------------------------------------------- controller: reproducibility, checkpoints, recovery
def _cfg(root, run_id, **kw):
    base = dict(run_id=run_id, seed=11, max_hours=1.0, max_rounds=3, workers=1, timeframes=("h1",), vintages=("2021-06-01",), discovery_years=3,
                validation_years=1, null_share=0.0, tasks_per_round=3, gp_population=10, gp_generations=2, store_dir=str(root), holdout_start=str(HOLDOUT.date()),
                benchmarks=False, futility_min_hours=99.0)
    base.update(kw)
    return DiscoveryConfig(**base)


def _table(data_root: Path, run_id: str):
    con = sqlite3.connect(data_root / "research" / "discovery" / "runs" / run_id / "lab.sqlite")
    cands = sorted(con.execute("SELECT cid, stage, fitness, reject_reason FROM candidates").fetchall())
    exps = con.execute("SELECT count(*), sum(passed) FROM experiments").fetchone()
    probes = con.execute("SELECT count(*) FROM experiments WHERE stage='crash_probe'").fetchone()[0]
    con.close()
    return cands, exps, probes


def _run_cli(env_root: Path, args: list[str], extra_env: dict | None = None) -> int:
    env = {**os.environ, "QR_DATA_ROOT": str(env_root), "PYTHONPATH": str(SRC), **(extra_env or {})}
    return subprocess.call([sys.executable, "-m", "quantresearch.discovery.controller", *args], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900)


def _cli_args(cfg: DiscoveryConfig, resume=False, rounds=None):
    out = ["run", "--run-id", cfg.run_id, "--seed", str(cfg.seed), "--workers", str(cfg.workers), "--timeframes", *cfg.timeframes, "--vintages", *cfg.vintages,
           "--tasks-per-round", str(cfg.tasks_per_round), "--null-share", "0", "--store-dir", cfg.store_dir, "--gp-population", str(cfg.gp_population),
           "--gp-generations", str(cfg.gp_generations), "--max-rounds", str(rounds or cfg.max_rounds), "--futility-min-hours", "99"]
    return out + (["--resume"] if resume else [])


@pytest.fixture(scope="module")
def controller_root(tmp_path_factory, synth):
    root = tmp_path_factory.mktemp("qrdata")
    cfg = _cfg(synth["root"], "base")
    # Discovery years and holdout must match the synthetic data; the CLI reads them from a saved config.json.
    return root


def _write_config(data_root: Path, cfg: DiscoveryConfig) -> None:
    d = data_root / "research" / "discovery" / "runs" / cfg.run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps({k: (list(v) if isinstance(v, tuple) else v) for k, v in cfg.__dict__.items()}))


def test_runs_reproduce_across_worker_counts_and_interruption(synth, controller_root):
    root = controller_root
    one, two, split, crash = (_cfg(synth["root"], name, workers=w) for name, w in (("one", 1), ("two", 2), ("split", 1), ("crash", 2)))
    for cfg in (one, two, split, crash):
        _write_config(root, cfg)
    assert _run_cli(root, _cli_args(one, resume=True)) == 0
    assert _run_cli(root, _cli_args(two, resume=True)) == 0
    # Interrupted at a round boundary, then resumed.
    assert _run_cli(root, _cli_args(split, resume=True), {"QR_DISCOVERY_CRASH_AFTER_ROUND": "1"}) == 3
    assert _run_cli(root, _cli_args(split, resume=True)) == 0
    # Killed part-way through applying round 2 (after its tasks ran), then resumed.
    assert _run_cli(root, _cli_args(crash, resume=True), {"QR_DISCOVERY_CRASH_IN_ROUND": "2"}) == 4
    assert _run_cli(root, _cli_args(crash, resume=True)) == 0
    ref = _table(root, "one")
    assert len(ref[0]) > 20
    for name in ("two", "split", "crash"):
        got = _table(root, name)
        assert got[0] == ref[0], name
        assert got[1] == ref[1], name
        assert got[2] == 0  # the crashed round's partial writes were rolled back
    con = sqlite3.connect(root / "research" / "discovery" / "runs" / "crash" / "lab.sqlite")
    assert con.execute("SELECT sessions FROM runs").fetchone()[0] >= 1
    assert con.execute("SELECT count(*) FROM checkpoints").fetchone()[0] >= 3
    con.close()
    report = json.loads((root / "research" / "reports" / "discovery_one_latest.json").read_text())
    assert report["totals"]["experiments_executed"] == ref[1][0]
    assert "contaminated" in report["holdout"]["note"]
    assert report["headline"]["qualified_strategies"] == 0 or report["best_candidates"]


def test_resume_refuses_a_changed_configuration(synth, controller_root):
    cfg = _cfg(synth["root"], "one")
    from quantresearch.discovery.controller import Controller
    changed = _cfg(synth["root"], "one", gp_population=99)
    with pytest.raises(SystemExit):
        Controller(changed, Paths(controller_root), resume=True)
    with pytest.raises(SystemExit):
        Controller(cfg, Paths(controller_root), resume=False)  # an existing run is never silently overwritten


def test_discovery_api_reads_a_run(controller_root, synth):
    from fastapi.testclient import TestClient
    from quantresearch.api.server import create_app
    from quantresearch.api.services import Services
    client = TestClient(create_app(Services(Paths(controller_root))))
    status = client.get("/api/discovery/status?run=one").json()
    assert status["run_id"] == "one" and status["status"] == "completed" and "contaminated" in status["holdout_note"]
    assert set(status["groups"]) == {"Experimental", "Screened", "Statistically validated", "Qualified", "Paper-trading"}
    cands = client.get("/api/discovery/candidates?run=one&group=Screened").json()
    assert cands["total"] >= 0
    if cands["candidates"]:
        detail = client.get(f"/api/discovery/candidate/{cands['candidates'][0]['cid']}?run=one").json()
        assert detail["definition"]["schema"] == S.SCHEMA and "not_proven_note" in detail
    assert client.get("/api/discovery/candidate/nope?run=one").status_code == 404
    assert client.get("/api/discovery/status?run=..%2F..").status_code in (404, 422)
    assert client.get("/api/discovery/distribution?run=one").status_code == 200


# ---------------------------------------------------------------- risk and execution isolation
FORBIDDEN_IMPORTS = ("quantresearch.execution", "quantresearch.risk", "quantresearch.api", "quantresearch.decision", "MetaTrader5", "keyring")


def test_discovery_package_cannot_reach_brokers_or_the_risk_engine():
    folder = SRC / "quantresearch" / "discovery"
    for path in folder.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else ([node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for n in names:
                assert not n.startswith(FORBIDDEN_IMPORTS), f"{path.name} imports {n}"
        text = path.read_text(encoding="utf-8")
        assert "QR_LIVE_ENABLED" not in text and "activate_live" not in text and "order_send" not in text
    code = ("import sys, importlib, pkgutil, quantresearch.discovery as d\n"
            "[importlib.import_module('quantresearch.discovery.' + m.name) for m in pkgutil.iter_modules(d.__path__)]\n"
            f"bad = [m for m in sys.modules if m.startswith({FORBIDDEN_IMPORTS!r})]\nprint(bad)\nsys.exit(1 if bad else 0)")
    done = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": str(SRC)}, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_forward_paper_runs_only_on_the_paper_broker(tmp_path):
    from quantresearch.execution import discovery_paper
    from quantresearch.execution.router import AuditLog, Mode, OrderRouter
    from quantresearch.risk.engine import RiskEngine

    class FakeBroker:
        name = "fake"
        is_simulated = False

    class R:
        broker = FakeBroker()
        mode = Mode.PAPER

    with pytest.raises(PermissionError):
        discovery_paper.run_forward("none", Paths(tmp_path), router=R())
    router = discovery_paper.make_router(Paths(tmp_path))
    assert router.mode is Mode.PAPER and not router.live_active
    assert (tmp_path / "app" / "discovery_paper.sqlite").exists()  # its own account, not the dashboard's
    assert discovery_paper.run_forward("none", Paths(tmp_path), router=router) == []
    assert isinstance(router.risk, RiskEngine) and isinstance(router.audit, AuditLog) and isinstance(router, OrderRouter)


def test_forward_registry_accepts_only_qualified_candidates(tmp_path):
    from quantresearch.discovery import forward, lab
    from quantresearch.discovery.config import run_dir
    p = Paths(tmp_path)
    con = lab.connect(run_dir("f", p) / "lab.sqlite")
    sp = S.normalize(SPECS[0])
    con.execute("INSERT INTO candidates(run_id, cid, spec_hash, dataset, tf, vintage, stage, spec_z) VALUES('f','c',?,'real','h1','v','robust',?)", (S.spec_hash(sp), S.compress(sp)))
    con.commit()
    with pytest.raises(ValueError):
        forward.register("f", "c", p)
    con.execute("UPDATE candidates SET stage='qualified'")
    con.commit()
    reg = forward.register("f", "c", p)
    assert reg["status"] == "registered" and "live trading remains disabled" in reg["note"]
    assert con.execute("SELECT stage FROM candidates").fetchone()[0] == "paper_trading"


def test_robustness_battery_runs_and_reports_every_test(synth):
    vin = make_vintage("2021-06-01", 3, 1, HOLDOUT).as_dict()
    out = V.run_robust({"task_id": "r", "store_root": str(synth["root"]), "tf": "h1", "seed": 3, "vintage": vin, "params": {"hash": "x", "spec": SPECS[0]}})
    for name in ("cost_stress", "cost_delay_1bar", "parameter_perturbation", "regime_volatility", "years_consistency", "beats_random_entries",
                 "alpha_beyond_gold_drift", "without_top_trades", "feature_dependency", "timing_specific"):
        assert name in out["tests"], name
    # On a random market, a fixed rule should not survive the adversarial stage.
    assert not out["stage5_passed"]


def test_no_structural_family_is_frozen_more_than_twice_per_cell(controller_root):
    con = sqlite3.connect(controller_root / "research" / "discovery" / "runs" / "one" / "lab.sqlite")
    worst = con.execute("SELECT max(n) FROM (SELECT count(*) AS n FROM candidates WHERE stage NOT IN ('experimental','screened') "
                        "GROUP BY dataset, tf, vintage, family)").fetchone()[0]
    con.close()
    assert worst is None or worst <= 2
