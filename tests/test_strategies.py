import numpy as np
import pandas as pd
import pytest

from quantresearch.core.config import Paths
from quantresearch.features.registry import build_features
from quantresearch.research import experiment as ex, lab, stats
from quantresearch.sim.costs import CostModel
from quantresearch.strategies import REGISTRY, engines, get, registry
from quantresearch.strategies.base import SIGNAL_COLUMNS, SignalStrategy, attach_signals, trading_day_position
from quantresearch.timemachine import lookahead
from quantresearch.warehouse.store import synthetic_bars

ALL = list(REGISTRY)


def market(n=6000, tf="h1", seed=5):
    bars = synthetic_bars(n, tf, start="2020-01-01", seed=seed, vol=0.002)
    frame = build_features(bars)
    rng = np.random.default_rng(seed)
    # A linked market, so the spread logic has something to work on.
    for name in ("xagusd", "eurusd", "dollaridxusd"):
        frame[f"{name}_close"] = np.exp(0.8 * np.log(frame["close"]) + rng.normal(0, 0.01, n).cumsum() * 0.05 + 1)
    frame["hours_to_fomc"] = 100.0
    return frame


@pytest.fixture(scope="module")
def frame():
    return market()


def test_there_are_exactly_seven_engines():
    assert len(REGISTRY) == 7
    assert set(ALL) == {"mean_reversion", "trend", "stat_arb", "behavioral", "etf_turnaround", "seasonal", "overreaction"}


@pytest.mark.parametrize("name", ALL)
def test_engine_has_a_complete_research_record(name):
    d = get(name)
    assert d.methodology and d.limitations and d.grid and d.defaults and d.title
    assert set(d.grid) <= set(d.defaults)


@pytest.mark.parametrize("name", ALL)
def test_prepare_returns_all_signal_columns(name, frame):
    d = get(name)
    params = d.defaults
    if d.fit:
        params = {**params, **d.fit(frame.iloc[:3000], params)}
    out = d.prepare(frame, params)
    assert set(SIGNAL_COLUMNS) <= set(out.columns) and len(out) == len(frame)


@pytest.mark.parametrize("name", ALL)
def test_signals_do_not_read_the_future(name, frame):
    d = get(name)
    params = d.defaults
    if d.fit:
        params = {**params, **d.fit(frame.iloc[:2000], params)}
    full = d.prepare(frame, params)
    for cut in (2500, 4100):
        short = d.prepare(frame.iloc[:cut].reset_index(drop=True), params)
        for col in SIGNAL_COLUMNS:
            a, b = full[col].iloc[:cut].to_numpy(dtype="float64"), short[col].to_numpy(dtype="float64")
            assert np.allclose(a, b, equal_nan=True, rtol=1e-9, atol=1e-9), (name, col, cut)


@pytest.mark.parametrize("name", ALL)
def test_full_look_ahead_integrity_through_the_engine(name):
    frame = market(3000)
    d = get(name)
    params = d.defaults
    if d.fit:
        params = {**params, **d.fit(frame.iloc[:1500], params)}

    def featurize(bars):
        out = market(3000).iloc[:len(bars)].reset_index(drop=True)
        return attach_signals(out, d.prepare(out, params))

    prepared = featurize(frame)
    cols = [c for c in frame.columns if c.startswith(("bid_", "ask_"))]
    factory = lambda: SignalStrategy(prepared, d.name)
    result = lookahead.check(frame, frame, factory, frame["close_ts"].iloc[2200], featurize=featurize)
    assert result.passed, result.as_dict()


@pytest.mark.parametrize("name", ALL)
def test_engine_runs_and_is_reproducible(name, frame):
    d = get(name)
    params = d.defaults
    if d.fit:
        params = {**params, **d.fit(frame.iloc[:3000], params)}
    a = registry.run(d, frame, params)
    b = registry.run(d, frame, params)
    assert a.digest() == b.digest()
    assert a.account.equity > 0


def scene(close, **extra):
    close = np.array(close, dtype=float)
    ts = pd.date_range("2024-01-02", periods=len(close), freq="h", tz="UTC")
    d = {"ts": ts, "close": close, "high": close + 1, "low": close - 1, "open": close, "bid_open": close, "bid_close": close,
         "atr_14": 2.0, "atr_50": 2.0, "adx": 15.0, "rsi_14": 50.0, "zscore_20": 0.0, "sma_20": close, "ema_20": close, "ema_50": close,
         "logret_1": 0.0, "vol_60": 0.01, "vol_20": 0.01, "volume_ratio": 2.0, "sweep_low": 0.0, "sweep_high": 0.0, "tr": 2.0}
    d.update(extra)
    return pd.DataFrame(d)


def test_mean_reversion_needs_extreme_and_calm_market():
    df = scene([100, 100], zscore_20=[-3, -3], rsi_14=[20, 20], adx=[15, 40], sma_20=[105, 105])
    s = engines.prepare_mean_reversion(df, get("mean_reversion").defaults)
    assert s.long_entry.tolist() == [True, False]
    assert s.target_dist.iloc[0] == 5


def test_trend_entry_needs_breakout_alignment_and_adx():
    n = 60
    close = np.linspace(100, 110, n)
    close[-1] = 130
    df = scene(close, ema_20=120.0, ema_50=110.0, adx=30.0)
    # This scene is a synthetic 11:00 UTC bar, so the session and volatility gates are switched off here; test_trend_gates covers them.
    params = get("trend").params(channel=20, use_daily=False, use_session=False, use_vol_band=False)
    assert engines.prepare_trend(df, params).long_entry.iloc[-1]
    df2 = df.assign(adx=5.0)
    assert not engines.prepare_trend(df2, params).long_entry.iloc[-1]


def test_stat_arb_goes_long_when_gold_is_cheap_to_its_leg():
    n = 1200  # The residual's own volatility needs a second full window to warm up.
    rng = np.random.default_rng(1)
    leg = np.exp(np.cumsum(rng.normal(0, 0.002, n)) + 3)
    gold = np.exp(0.9 * np.log(leg) + 4 + rng.normal(0, 0.002, n))
    gold[-1] *= 0.95
    df = scene(gold, xagusd_close=leg)
    p = get("stat_arb").params(window=500, corr_min=0.0)
    s = engines.prepare_stat_arb(df, p)
    assert s.long_entry.iloc[-1] and not s.short_entry.iloc[-1]


def test_stat_arb_quietly_does_nothing_without_cross_data():
    df = scene(np.arange(1, 900.0))
    assert not engines.prepare_stat_arb(df, get("stat_arb").defaults).long_entry.any()


def test_behavioral_fades_panic_with_activity():
    df = scene(np.full(150, 100.0), logret_1=np.r_[np.zeros(149), -0.05], volume_ratio=2.0)
    s = engines.prepare_behavioral(df, get("behavioral").defaults)
    assert s.long_entry.iloc[-1]
    assert not engines.prepare_behavioral(df.assign(volume_ratio=0.5), get("behavioral").defaults).long_entry.iloc[-1]


def test_turn_of_month_position_and_window():
    ts = pd.Series(pd.date_range("2024-01-29", "2024-02-06", freq="D", tz="UTC"))
    pos = trading_day_position(ts)
    table = dict(zip(ts.dt.date.astype(str), pos.tdoe))
    assert table["2024-01-31"] == 1 and table["2024-01-30"] == 2
    assert dict(zip(ts.dt.date.astype(str), pos.tdom))["2024-02-01"] == 1
    df = scene(np.full(len(ts), 100.0))
    df["ts"] = ts
    s = engines.prepare_etf_turnaround(df, get("etf_turnaround").defaults)
    assert s.long_entry.sum() == 1 and s.long_entry.iloc[1]  # enters on 01-30
    # The weekend belongs to the window: Sunday-session bars must not exit and re-enter.
    assert not s.exit_long.iloc[5] and not s.exit_long.iloc[6]
    assert s.exit_long.iloc[8]  # 02-06 is the fourth trading day, the first outside the window


def test_first_notice_window_precedes_active_months():
    ts = pd.Series(pd.date_range("2024-01-01", "2024-03-31", freq="D", tz="UTC"))
    w = engines.first_notice_windows(ts, 2)
    marked = set(ts[w].dt.date.astype(str))
    assert "2024-01-31" in marked and "2024-01-30" in marked  # before February delivery
    assert "2024-02-28" not in marked  # March is not active


def test_seasonal_selection_uses_only_training_data():
    train = market(2500)
    test_like = market(4000)
    p = get("seasonal").defaults
    a = engines.fit_seasonal(train, p)
    b = engines.fit_seasonal(test_like.iloc[:2500], p)
    assert isinstance(a["long_cells"], list) and isinstance(b["short_cells"], list)
    planted = train.copy()
    wed10 = (planted.ts.dt.day_of_week == 2) & (planted.ts.dt.hour == 10)
    # Plant a strong effect in the bar after Wednesday 10:00.
    planted["close"] = planted["close"] * np.exp(np.cumsum(np.where(wed10.shift(1, fill_value=False), 0.02, 0.0)))
    assert [2, 10] in engines.fit_seasonal(planted, {**p, "min_obs": 5})["long_cells"]


def test_gap_is_measured_only_after_a_closure():
    ts = pd.to_datetime(["2024-01-05 20:00", "2024-01-05 21:00", "2024-01-07 23:00", "2024-01-08 00:00"], utc=True)
    df = pd.DataFrame({"ts": ts, "bid_open": [100, 100, 105, 105.0], "bid_close": [100, 100, 105, 105.0], "atr_14": 2.0,
                       "logret_1": 0.0, "vol_60": 0.01})
    m = engines.gap_measures(df)
    assert m.gap.iloc[2] == 5 and m.gap_atr.iloc[2] == 2.5
    assert m.gap.isna().iloc[[0, 1, 3]].all()


def test_overreaction_fade_and_follow_oppose_each_other():
    n = 80
    df = scene(np.full(n, 100.0), logret_1=np.r_[np.zeros(n - 1), 0.1])
    fade = engines.prepare_overreaction(df, get("overreaction").defaults)
    follow = engines.prepare_overreaction(df, get("overreaction").params(mode="follow"))
    assert fade.short_entry.iloc[-1] and follow.long_entry.iloc[-1]


# ---------------------------------------------------------------- research tools
def test_folds_are_chronological_without_test_overlap():
    folds = ex.make_folds(pd.Timestamp("2010-01-01", tz="UTC"), pd.Timestamp("2020-01-01", tz="UTC"), 4, 12)
    assert folds
    for a, b in zip(folds, folds[1:]):
        assert a.train_end <= a.test_end and a.test_end <= b.test_end
        assert b.train_end == a.test_end  # The next test starts where this one ends.
    assert folds[-1].test_end <= pd.Timestamp("2020-01-01", tz="UTC")


def test_holdout_is_never_used_in_walk_forward():
    frame = market(16000)
    d = get("mean_reversion")
    d2 = ex.StrategyDef(**{**d.__dict__, "grid": {"z_entry": [2.0]}})
    report = ex.walk_forward(d2, frame, CostModel(), train_years=1, test_months=3, holdout_years=0)
    assert report["fold_count"] >= 1
    with_holdout = ex.walk_forward(d2, frame, CostModel(), train_years=1, test_months=3, holdout_years=1)
    cut = pd.Timestamp(with_holdout["holdout_start"], tz="UTC")
    for fold in with_holdout["folds"]:
        assert pd.Timestamp(fold["test_end"], tz="UTC") <= cut
    assert with_holdout["fold_count"] < report["fold_count"]


def test_full_report_contains_required_record_and_ledger_stores_it(tmp_path):
    frame = market(16000)
    d = get("overreaction")
    d2 = ex.StrategyDef(**{**d.__dict__, "grid": {"gap_atr": [0.5]}})
    report = ex.full_report(d2, frame, train_years=1, test_months=3, holdout_years=1, dataset="test")
    for key in ("hypotheses", "walk_forward", "holdout", "verdict", "limitations", "methodology", "dataset_version", "period", "cost_model"):
        assert key in report
    assert set(report["holdout"]["scenarios"]) == {"observed", "spread_x2_equiv", "stress"}
    ledger = ex.Ledger(Paths(tmp_path))
    path = ledger.add(report)
    assert path.exists() and len(ledger.all()) == 1
    ledger.close()


def test_verdict_never_approves_on_too_few_trades():
    wf = {"out_of_sample": {"trades": 5, "net_profit": 999}, "oos_sharpe": 3.0, "deflated_sharpe": {"dsr": 0.99}}
    assert ex.verdict(wf, {"observed": {"net_profit": 1}, "stress": {"net_profit": 1}})["label"] == "insufficient_evidence"


def test_stress_loss_blocks_the_strong_label():
    wf = {"out_of_sample": {"trades": 500, "net_profit": 999}, "oos_sharpe": 2.0, "deflated_sharpe": {"dsr": 0.99}}
    assert ex.verdict(wf, {"observed": {"net_profit": 10}, "stress": {"net_profit": -1}})["label"] == "weak_or_not_significant"
    wf["out_of_sample"]["net_profit"] = -5
    wf["oos_sharpe"] = -1
    assert ex.verdict(wf, {"observed": {"net_profit": -1}, "stress": {"net_profit": -1}})["label"] == "no_edge_found"


def test_conditional_test_removes_drift_from_a_drifting_market():
    rng = np.random.default_rng(1)
    f = pd.Series(rng.normal(0.01, 0.01, 2000))
    mask = pd.Series(rng.random(2000) < 0.2)
    r = stats.conditional_test(f, mask, 1, 1)
    assert r["p_one_sided"] > 0.01  # The drift alone is not a signal.


def test_conditional_test_finds_a_planted_effect_and_refuses_few_events():
    rng = np.random.default_rng(1)
    f = pd.Series(rng.normal(0, 0.01, 2000))
    mask = pd.Series(rng.random(2000) < 0.2)
    f[mask] += 0.01
    assert stats.conditional_test(f, mask, 1, 1)["p_one_sided"] < 0.001
    assert not stats.conditional_test(f, pd.Series(False, index=f.index), 1, 1)["tested"]


def test_benjamini_hochberg_is_stricter_than_raw():
    out = stats.benjamini_hochberg([0.001, 0.04, 0.5, 0.9])
    assert out[0]["significant"] and not out[1]["significant"]
    assert out[1]["p_adjusted"] > 0.04


def test_deflated_sharpe_falls_as_trials_increase():
    rng = np.random.default_rng(2)
    r = rng.normal(0.0005, 0.01, 1000)
    few = stats.deflated_sharpe(r, list(rng.normal(0, 0.02, 3)))
    many = stats.deflated_sharpe(r, list(rng.normal(0, 0.02, 500)))
    assert many["expected_max_sr"] > few["expected_max_sr"]
    assert many["dsr"] < few["dsr"]


def test_stationarity_and_cointegration_on_known_series():
    rng = np.random.default_rng(3)
    noise = pd.Series(rng.normal(0, 1, 800))
    walk = noise.cumsum()
    assert stats.stationarity(noise)["verdict"] == "stationary"
    assert stats.stationarity(walk)["verdict"] == "non_stationary"
    linked = 0.5 * walk + rng.normal(0, 0.5, 800)
    c = stats.cointegration(linked, walk)
    assert c["cointegrated_5pct"] and c["beta"] == pytest.approx(0.5, abs=0.05)
    other = pd.Series(rng.normal(0, 1, 800)).cumsum()
    assert stats.cointegration(other, walk)["p"] > 0.01


def test_block_bootstrap_contains_true_mean_and_is_seeded():
    x = np.random.default_rng(4).normal(1.0, 0.5, 500)
    low, high = stats.block_bootstrap_ci(x)
    assert low < 1.0 < high
    assert stats.block_bootstrap_ci(x) == (low, high)


def test_monte_carlo_reports_loss_probability():
    r = stats.monte_carlo_trades(np.array([-1.0] * 50), 100.0)
    assert r["prob_loss"] == 1.0 and r["max_dd_p50"] > 0


def test_regression_recovers_slope():
    rng = np.random.default_rng(5)
    x = pd.Series(rng.normal(0, 1, 500), name="x")
    y = 2 * x + rng.normal(0, 0.1, 500)
    r = stats.regression(y, x.to_frame())
    assert r["coef"]["x"] == pytest.approx(2.0, abs=0.05) and r["r2"] > 0.9


def test_describe_returns_fields():
    d = stats.describe_returns(pd.Series(np.random.default_rng(6).normal(0, 1, 200)))
    assert d["n"] == 200 and "excess_kurtosis" in d and "abs_autocorr_1" in d


def test_regime_labels_are_ordered_by_volatility():
    frame = market(3000)
    frame = frame.dropna(subset=lab.REGIME_INPUTS)
    fitted = lab.fit_regimes(frame.iloc[:1500])
    out = lab.predict_regimes(fitted, frame.iloc[1500:])
    assert set(out.regime) <= {"quiet", "normal", "volatile"}
    joined = frame.loc[out.index].assign(regime=out.regime)
    means = joined.groupby("regime").vol_20.mean()
    if {"quiet", "volatile"} <= set(means.index):
        assert means["quiet"] < means["volatile"]
    assert out.confidence.between(0, 1).all()


def test_cusum_finds_a_level_shift_and_is_causal():
    rng = np.random.default_rng(7)
    s = pd.Series(np.r_[rng.normal(0, 1, 400), rng.normal(5, 1, 100)])
    points = lab.change_points(s)
    assert points and 395 <= points[0] <= 420
    assert all(p < 410 for p in lab.change_points(s.iloc[:410]))


def test_anomaly_model_flags_outliers_from_training_only():
    rng = np.random.default_rng(8)
    train = pd.DataFrame(rng.normal(0, 1, (1000, 2)), columns=["a", "b"])
    test = pd.DataFrame([[0.0, 0.0], [50.0, 50.0]], columns=["a", "b"])
    flags = lab.anomalies(train, test, ["a", "b"])
    assert flags.tolist() == [False, True]


def test_mutual_information_ranks_the_real_driver_first():
    rng = np.random.default_rng(9)
    df = pd.DataFrame(rng.normal(0, 1, (800, 2)), columns=["real", "noise"])
    y = df.real ** 2 + rng.normal(0, 0.1, 800)
    assert lab.mutual_information(df, ["real", "noise"], y).index[0] == "real"


def test_kmeans_and_importance_run():
    rng = np.random.default_rng(10)
    df = pd.DataFrame(rng.normal(0, 1, (600, 2)), columns=["a", "b"])
    df["y"] = df.a * 2 + rng.normal(0, 0.1, 600)
    assert lab.cluster(df, ["a", "b"], 3).nunique() == 3
    assert lab.feature_importance(df.iloc[:400], df.iloc[400:], ["a", "b"], "y").index[0] == "a"


def test_regime_labels_are_causal_and_appear_in_reports():
    frame = market(3000)
    full = ex.regime_labels(frame)
    cut = ex.regime_labels(frame.iloc[:1500].reset_index(drop=True))
    assert full.iloc[:len(cut)].tolist() == cut.tolist()
    assert set(full) <= {"trending_high_vol", "trending_normal_vol", "ranging_high_vol", "ranging_normal_vol"}
