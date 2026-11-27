"""Hard rules around catalogue runs: fixed parameters, every variant counted, nothing deleted, strict 'promising'."""
import json

import numpy as np
import pytest

from quantresearch.core.config import Paths
from quantresearch.research import catalogue as runner
from quantresearch.research import experiment as ex
from quantresearch.research.trials import TrialBook, project_deflated_sharpe
from quantresearch.sim.costs import CostModel
from quantresearch.strategies.catalogue import CATALOGUE
from test_catalogue import market


@pytest.fixture()
def p(tmp_path):
    return Paths(tmp_path)


def test_parameters_lock_at_the_first_run_and_a_change_is_refused(p):
    runner.lock_params("cat_x", {"n": 20}, p)
    runner.lock_params("cat_x", {"n": 20}, p)  # Same parameters: fine.
    with pytest.raises(runner.ParamsChanged, match="new name"):
        runner.lock_params("cat_x", {"n": 21}, p)


def engine_report(name, grid, sensitivity):
    return {"strategy": name, "grid": grid, "defaults": {}, "walk_forward": {"parameter_sensitivity": sensitivity, "oos_sharpe": 0.0, "out_of_sample": {"trades": 0}}}


def test_the_trial_book_counts_every_engine_variant_and_catalogue_strategy_once(p):
    book = TrialBook(p)
    grid = {"a": [1, 2, 3], "b": [True, False]}
    sens = {json.dumps({"a": a, "b": b}, sort_keys=True): {"mean_train_sharpe": 0.1 * a} for a in (1, 2, 3) for b in (True, False)}
    assert book.record_report(engine_report("trend", grid, sens)) == 6
    assert book.record_report(engine_report("trend", grid, sens)) == 6  # A rerun updates, it does not add.
    cat = {"strategy": "cat_y", "grid": {}, "defaults": {"n": 5}, "catalogue": True, "walk_forward": {"oos_sharpe": 0.5, "out_of_sample": {"trades": 40}}}
    book.record_report(cat)
    summary = book.summary()
    assert summary["variants"] == 7 and summary["strategies"] == 2 and summary["by_kind"] == {"engine_grid": 6, "catalogue": 1}
    book.close()


def test_a_grid_that_never_traded_still_counts_towards_n(p):
    book = TrialBook(p)
    book.record_report(engine_report("stat_arb", {"leg": ["x", "y"], "w": [1, 2, 3]}, {}))
    n, known = book.sharpes()
    assert n == 6 and known == []
    book.close()


def test_more_trials_raise_the_bar(p):
    rng = np.random.default_rng(3)
    returns = rng.normal(0.0008, 0.01, 1500)
    book = TrialBook(p)
    for i in range(5):
        book._upsert(f"s{i}", "{}", "catalogue", rng.normal(0, 0.03), None)
    few = project_deflated_sharpe(returns, book)["dsr"]
    for i in range(5, 300):
        book._upsert(f"s{i}", "{}", "catalogue", rng.normal(0, 0.03), None)
    many = project_deflated_sharpe(returns, book)
    book.close()
    assert many["trials"] == 300 and many["dsr"] < few


def base_report(oos_net=100.0, oos_sharpe=0.5, trades=60, holdout=50.0, stress=10.0):
    return {"walk_forward": {"out_of_sample": {"trades": trades, "net_profit": oos_net}, "oos_sharpe": oos_sharpe, "deflated_sharpe": {"dsr": 0.99}},
            "holdout": {"scenarios": {"observed": {"net_profit": holdout}, "stress": {"net_profit": stress}}}}


@pytest.mark.parametrize("change, label", [
    ({}, "promising_needs_paper_trading"),
    ({"trades": 10}, "insufficient_evidence"),
    ({"stress": -1.0}, "weak_or_not_significant"),
    ({"holdout": -1.0}, "weak_or_not_significant"),
    ({"oos_net": -5.0}, "no_edge_found"),
])
def test_promising_needs_every_condition(change, label):
    v = runner.catalogue_verdict(base_report(**change), {"dsr": 0.97, "trials": 400, "expected_max_sr": 0.05})
    assert v["label"] == label and v["promising"] == (label == "promising_needs_paper_trading")


def test_a_project_deflated_sharpe_below_the_bar_is_not_promising():
    v = runner.catalogue_verdict(base_report(), {"dsr": 0.6, "trials": 400, "expected_max_sr": 0.05})
    assert v["promising"] is False and v["checks"]["project_deflated_sharpe"] is False


def test_queue_flow_and_recovery(p):
    names = list(CATALOGUE)[:3]
    runner.enqueue(names[:2], p)
    runner.enqueue([names[2]], p, now=True)
    assert runner.claim_next(p) == names[2]  # 'Run' goes first; queued ones wait for 'Run all queued'.
    assert runner.claim_next(p) is None
    assert runner.start_batch(p) == 2
    first = runner.claim_next(p)
    assert runner.recover(p) == 2  # The run in progress and the earlier one are interrupted.
    runner.finish(first, "boom", p)
    state = runner.queue_state(p)
    assert state[first]["status"] == "failed" and state[first]["error"] == "boom"
    with pytest.raises(KeyError):
        runner.enqueue(["not_a_strategy"], p)


def test_hours_since_the_last_event():
    import pandas as pd

    times = pd.Series(pd.to_datetime(["2026-01-01 10:00", "2026-01-01 15:00", "2026-01-02 15:00"], utc=True))
    schedule = pd.Series(pd.to_datetime(["2026-01-01 14:00"], utc=True))
    out = runner.hours_since(times, schedule)
    assert np.isnan(out.iloc[0]) and out.iloc[1] == 1.0 and out.iloc[2] == 25.0


def test_full_report_carries_daily_returns_privately_and_the_ledger_drops_them(p):
    d = CATALOGUE["cat_ema_cross_h1"].defn
    report = ex.full_report(d, market("h1", 9000), CostModel(), train_years=1, test_months=3, holdout_years=0, dataset="test")
    assert isinstance(report["_oos_daily"], np.ndarray)
    ledger = ex.Ledger(p)
    path = ledger.add(report)
    ledger.close()
    assert "_oos_daily" not in json.loads(path.read_text(encoding="utf-8"))


def test_engine_training_sharpes_count_towards_n_but_not_the_spread(p):
    rng = np.random.default_rng(5)
    returns = rng.normal(0.0006, 0.01, 1500)
    book = TrialBook(p)
    for i in range(10):
        book._upsert(f"cat_{i}", "{}", "catalogue", rng.normal(0, 0.02), None)
    before = project_deflated_sharpe(returns, book)
    for i in range(50):  # Wild in-sample figures from near-empty grid variants.
        book._upsert("trend", f"v{i}", "engine_grid", rng.choice([-0.4, 0.15]), None)
    after = project_deflated_sharpe(returns, book)
    book.close()
    assert after["trials"] == 60 and before["trials"] == 10
    assert after["expected_max_sr"] > before["expected_max_sr"]  # More trials: a higher bar...
    assert after["expected_max_sr"] < 0.1  # ...but not one set by the engines' training noise (that would be ~0.6).


def test_rescore_judges_against_the_current_trial_count(p, monkeypatch):
    name = next(iter(CATALOGUE))
    folder = p.research / "reports"
    folder.mkdir(parents=True)
    report = {**base_report(), "strategy": name, "catalogue_verdict": {"label": "weak_or_not_significant"}}
    (folder / f"{name}_latest.json").write_text(json.dumps(report), encoding="utf-8")
    (p.research / "catalogue_returns").mkdir()
    np.save(p.research / "catalogue_returns" / f"{name}.npy", np.random.default_rng(1).normal(0.003, 0.01, 2000))
    book = TrialBook(p)
    for i in range(6):
        book._upsert(f"cat_{i}", "{}", "catalogue", 0.001 * i, None)
    book.con.commit()
    book.close()
    out = runner.rescore(p)
    saved = json.loads((folder / f"{name}_latest.json").read_text(encoding="utf-8"))
    assert out[name]["previous_label"] == "weak_or_not_significant" and saved["project_multiple_testing"]["trials"] == 6
    assert saved["catalogue_verdict"]["label"] == "promising_needs_paper_trading"  # A strong synthetic series clears the bar.
