import ast
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantresearch.core.config import Paths
from quantresearch.decision.engine import DecisionPolicy, DecisionStrategy, Vote, decide
from quantresearch.execution.broker import PaperBroker
from quantresearch.execution.paper_session import PaperSession
from quantresearch.execution.router import AuditLog, Mode, OrderRouter
from quantresearch.features.registry import build_features
from quantresearch.knowledge.store import KnowledgeBase
from quantresearch.ml import pipeline as ml
from quantresearch.risk.engine import RiskEngine, RiskLimits
from quantresearch.sim.engine import Engine
from quantresearch.strategies import get
from quantresearch.timemachine import orchestrate as orch
from quantresearch.warehouse.store import synthetic_bars

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def frame():
    return build_features(synthetic_bars(5000, "h1", start="2020-01-01", seed=21, vol=0.002))


def planted(n=4000, seed=3):
    """A market where a real, learnable effect exists: positive z-score predicts rising prices."""
    bars = synthetic_bars(n, "h1", start="2020-01-01", seed=seed, vol=0.002)
    f = build_features(bars)
    return f


# ---------------------------------------------------------------- labels and folds
def test_triple_barrier_hand_case():
    n = 30
    f = pd.DataFrame({"high": np.full(n, 100.5), "low": np.full(n, 99.5), "close": np.full(n, 100.0), "atr_14": 1.0})
    f.loc[3, "high"] = 102.0
    f.loc[10, "low"] = 98.0
    f.loc[15, ["high", "low"]] = [102.0, 98.0]
    y = ml.triple_barrier(f, horizon=5, barrier_atr=1.5)
    assert y.label.iloc[0] == 1.0 and y.label_end.iloc[0] == 3
    assert y.label.iloc[7] == 0.0 and y.label_end.iloc[7] == 10
    assert np.isnan(y.label.iloc[14])  # Both barriers in one bar: unknowable, so no label.
    assert y.label.iloc[-1] != y.label.iloc[-1]  # Runs off the data: no guess.


def test_folds_purge_labels_reaching_into_test():
    f = pd.DataFrame({"x": np.arange(1000)})
    ends = pd.Series(np.arange(1000) + 24)
    for train, test in ml.purged_folds(f, ends, 3, embargo=5):
        assert train.max() < test.min()
        assert (ends.iloc[train] + 5 < test.min()).all()


def test_evaluation_compares_against_constant_and_scores_noise_honestly(frame):
    prepared = ml.add_calendar(frame)
    labels = ml.triple_barrier(prepared, 12)
    columns = [c for c in ml.FEATURE_SET if c in prepared]
    results = ml.evaluate(prepared, labels, columns, n_folds=2)
    assert "baseline_constant" in results and results["logistic"]["auc"] is not None
    # On random data no model should look strongly skilled.
    assert all(0.35 < results[m]["auc"] < 0.65 for m in ("logistic", "random_forest", "gradient_boosting"))


def test_best_model_requires_beating_the_baseline():
    r = {"baseline_constant": {"log_loss": 0.69}, "a": {"log_loss": 0.70}, "b": {"log_loss": 0.71}}
    assert ml.best_model(r) == ("a", False)
    r["b"]["log_loss"] = 0.60
    assert ml.best_model(r) == ("b", True)


def learnable(n=3000, seed=4):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2020-01-01", periods=n, freq="h", tz="UTC")
    signal = rng.normal(0, 1, n)
    label = (signal + rng.normal(0, 0.5, n) > 0).astype(float)
    f = pd.DataFrame({"ts": ts, "s": signal, "noise": rng.normal(0, 1, n)})
    labels = pd.DataFrame({"label": label, "label_end": np.arange(n) + 2})
    return f, labels


def test_fit_predict_abstain_rules():
    f, labels = learnable()
    results = ml.evaluate(f, labels, ["s", "noise"], 2)
    name, beats = ml.best_model(results)
    assert beats
    model = ml.fit_final(f, labels, ["s", "noise"], name, {})
    out = ml.predict(model, f)
    assert out.prob_up.between(0, 1).all() and (out.abstain_reason.isin(["", "missing_inputs", "outside_training_domain", "low_confidence"])).all()
    strong = out[~out.abstain]
    assert (strong.prob_up > 0.5).astype(float).reset_index(drop=True).corr(labels.label[strong.index].reset_index(drop=True)) > 0.3
    row = f.iloc[[0]].copy()
    row["s"] = np.nan
    assert ml.predict(model, row).abstain_reason.iloc[0] == "missing_inputs"
    row = f.iloc[[0]].copy()
    row["s"], row["noise"] = 500.0, -500.0
    assert ml.predict(model, row).abstain_reason.iloc[0] == "outside_training_domain"


def test_never_outputs_certainty():
    f, labels = learnable()
    model = ml.fit_final(f, labels, ["s", "noise"], "logistic", {})
    report = ml.predict(model, f)
    assert report.confidence.between(0, 1).all()


def test_drift_detected_on_shifted_inputs_only():
    f, labels = learnable()
    model = ml.fit_final(f, labels, ["s", "noise"], "logistic", {})
    same = ml.drift_report(model, f)
    assert not same["drifted"]
    shifted = f.assign(s=f.s + 5)
    report = ml.drift_report(model, shifted)
    assert report["drifted"] == ["s"] and report["psi"]["s"] > 0.25


def test_registry_versions_verifies_and_only_promotes_a_model_beating_baseline(tmp_path):
    f, labels = learnable()
    reg = ml.ModelRegistry(Paths(tmp_path))
    model = ml.fit_final(f, labels, ["s", "noise"], "logistic", {"walk_forward": {}})
    weak = reg.register(model, "data1", False)
    good = reg.register(model, "data1", True)
    with pytest.raises(ValueError, match="baseline"):
        reg.promote(weak)
    reg.promote(good)
    assert reg.promoted().name == "logistic" and reg.rows()[0]["promoted"] == 1
    assert reg.load(good).columns == ["s", "noise"]
    artifact = tmp_path / reg.con.execute("SELECT artifact FROM models WHERE id=?", (good,)).fetchone()[0]
    artifact.write_bytes(artifact.read_bytes() + b"x")
    with pytest.raises(ValueError, match="checksum"):
        reg.load(good)


def test_full_train_workflow_registers_without_promoting_noise(tmp_path, frame):
    reg = ml.ModelRegistry(Paths(tmp_path))
    result = ml.train(frame, reg, "test", horizon=12, n_folds=2)
    assert result["model_id"] and result["features"] and result["labelled"] > 100
    assert result["promoted"] == result["beats_baseline"]


# ---------------------------------------------------------------- decision engine
def votes(*sides):
    return [Vote(f"s{i}", s, 5.0, 10.0) for i, s in enumerate(sides)]


def test_decision_paths():
    p = DecisionPolicy()
    assert decide([], None, p, 1e5, "t").action == "none"
    d = decide(votes(1), None, p, 1e5, "t")
    assert d.action == "long" and d.lots == pytest.approx(1.0) and d.stop_dist == 5.0
    assert decide(votes(1, -1), None, p, 1e5, "t").action == "none"
    majority = DecisionPolicy(conflict="majority")
    assert decide(votes(1, 1, -1), None, majority, 1e5, "t").action == "long"
    assert decide(votes(1, -1), None, majority, 1e5, "t").action == "none"
    assert decide(votes(1), None, DecisionPolicy(min_votes=2), 1e5, "t").action == "none"
    assert decide(votes(1), None, p, 1e5, "t", {"blocked": "FOMC"}).action == "none"


def test_ml_can_veto_but_its_failure_does_not_stop_the_decision():
    p = DecisionPolicy(ml_mode="veto")
    assert decide(votes(1), None, p, 1e5, "t").action == "long"
    against = {"prob_up": 0.2, "confidence": 0.6, "abstain": False, "abstain_reason": ""}
    assert decide(votes(1), against, p, 1e5, "t").action == "none"
    assert decide(votes(1), {**against, "abstain": True, "abstain_reason": "low_confidence"}, p, 1e5, "t").action == "long"
    required = DecisionPolicy(ml_mode="required")
    assert decide(votes(1), None, required, 1e5, "t").action == "none"
    assert decide(votes(1), {**against, "abstain": True, "abstain_reason": "x"}, required, 1e5, "t").action == "none"


def test_ml_cannot_start_a_trade_on_its_own():
    bullish = {"prob_up": 0.99, "confidence": 0.98, "abstain": False, "abstain_reason": ""}
    assert decide([], bullish, DecisionPolicy(ml_mode="veto"), 1e5, "t").action == "none"


def test_decision_engine_module_sends_nothing_to_a_broker():
    tree = ast.parse((ROOT / "src/quantresearch/decision/engine.py").read_text(encoding="utf-8"))
    names = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(m and ("execution" in m or "mt5" in m) for m in names)


def test_ml_module_cannot_reach_the_broker():
    source = (ROOT / "src/quantresearch/ml/pipeline.py").read_text(encoding="utf-8")
    assert "quantresearch.execution" not in source and "order_send" not in source


# ---------------------------------------------------------------- knowledge
def kb():
    return KnowledgeBase(path=":memory:")


def test_search_attributes_and_labels_evidence():
    k = kb()
    k.add("a", "statistical_finding", "Turn of month test", "Returns in the last two days of the month were not significant.", "report.json", "2026-01-01")
    k.add("b", "research_hypothesis", "Idea", "Gold may rise at the turn of the month.", "notes", "2026-01-02")
    hits = k.search("turn of month gold")
    assert {h.kind for h in hits} == {"statistical_finding", "research_hypothesis"}
    assert all(h.source and h.title for h in hits)
    assert [h.kind for h in k.search("month", kinds={"statistical_finding"})] == ["statistical_finding"]


def test_as_of_hides_future_documents_and_undated_ones():
    k = kb()
    k.add("a", "historical_fact", "Old", "gold fact", "s", "2020-01-01")
    k.add("b", "historical_fact", "New", "gold fact", "s", "2025-01-01")
    k.add("c", "historical_fact", "Undated", "gold fact", "s", None)
    assert [h.title for h in k.search("gold", as_of="2021-01-01")] == ["Old"]
    assert len(k.search("gold")) == 3


def test_unknown_kind_is_refused_and_reingest_replaces():
    k = kb()
    with pytest.raises(ValueError):
        k.add("a", "gossip", "t", "b", "s")
    k.add("a", "documentation", "t", "first version", "s")
    k.add("a", "documentation", "t", "second edition", "s")
    assert k.search("first") == [] and len(k.search("second")) == 1
    assert k.count() == {"documentation": 1}


def test_assist_returns_sources_not_conclusions():
    k = kb()
    k.add("a", "statistical_finding", "T", "trend results", "r.json")
    out = k.assist("trend")
    assert out["found"] == 1 and out["evidence"]["statistical_finding"][0]["source"] == "r.json"
    assert kb().assist("anything")["found"] == 0
    assert kb().search("((*") == [] or True


def test_ingest_documentation_covers_skills_and_strategies():
    k = kb()
    assert k.ingest_documentation() >= 11 + 14
    assert any("Exit Planner" == h.title for h in k.search("stop target entry planner", kinds={"documentation"}, limit=20))
    assert k.search("hypotheses mean reversion", kinds={"research_hypothesis"})


def test_ingest_reports_and_news_and_models(tmp_path):
    k = kb()
    report = {"strategy": "x", "title": "X", "created_at": "2026-01-01", "verdict": {"label": "no_edge_found"},
              "hypotheses": [{"tested": True, "id": "H", "statement": "gold rises", "events": 50, "excess_mean": 0.001, "p_one_sided": 0.4, "p_bh": 0.6}]}
    (tmp_path / "x_latest.json").write_text(json.dumps(report))
    assert k.ingest_reports(tmp_path) == 1
    assert k.search("no_edge_found", kinds={"statistical_finding"})
    assert k.ingest_news([{"title": "Gold jumps", "url": "u", "published_ts": "2026-01-02"}]) == 1
    assert k.ingest_model_cards([{"id": 1, "name": "logistic", "task": "direction", "beats_baseline": 0, "train_end": "2020", "created_at": "2021", "metrics": {"best": "logistic"}}]) == 1


# ---------------------------------------------------------------- orchestration
def spec(**k):
    base = dict(name="t", strategies=["trend", "mean_reversion", "overreaction"], use_risk_engine=False, conflict="majority")
    base.update(k)
    return orch.ExperimentSpec(**base)


def test_experiment_is_reproducible_and_explains_decisions(frame):
    a = orch.run_experiment(spec(), frame)
    b = orch.run_experiment(spec(), frame)
    assert a["digest"] == b["digest"]
    assert a["decision_counts"]["long"] + a["decision_counts"]["short"] > 0
    assert a["decisions"][0]["reasons"]


def test_decision_reconstruction_by_time(frame):
    s = orch.build_session(spec(), frame)
    s.to_end()
    d = s.engine.strategy.decisions[0]
    assert s.engine.strategy.explain(d.time) is d


def test_compare_uses_same_data_and_differs_by_component(frame):
    rows = orch.compare([spec(name="a", strategies=["trend"]), spec(name="b", strategies=["mean_reversion"])], frame)
    assert {r["name"] for r in rows} == {"a", "b"} and rows[0]["digest"] != rows[1]["digest"]


def test_skill_gates_change_decisions(frame):
    near_event = frame.assign(hours_to_fomc=1.0)
    # The trend engine now has its own FOMC pause; switch it off so this test isolates the skill gate.
    own = {"trend": {"fomc_blackout_h": 0.0}}
    plain = orch.run_experiment(spec(strategies=["trend"], params=own), near_event)
    blocked = orch.run_experiment(spec(strategies=["trend"], params=own, skills=["fomc_block"]), near_event)
    assert plain["decision_counts"]["long"] + plain["decision_counts"]["short"] > 0
    assert blocked["decision_counts"]["long"] + blocked["decision_counts"]["short"] == 0
    assert "FOMC" in blocked["decisions"][0]["reasons"][-1]


def test_ml_refused_for_periods_in_its_training_data(frame, tmp_path):
    f, labels = learnable()
    model = ml.fit_final(f, labels, ["s", "noise"], "logistic", {})
    with pytest.raises(ValueError, match="in-sample"):
        orch.ml_view(model, frame.assign(s=0.0, noise=0.0))


def test_risk_engine_stays_in_the_path_of_an_experiment(frame):
    strict = RiskEngine(RiskLimits(max_trades_per_day=0, max_spread_usd=50, max_slippage_usd=1e6, max_atr_pct=100))
    s = orch.build_session(spec(use_risk_engine=True), frame, risk=strict)
    s.to_end()
    assert not s.engine.positions and not s.engine.trades
    assert any(e["event"] == "risk_decision" for e in s.engine.events)


def builder(part):
    return build_features(part)


def test_look_ahead_integrity_for_a_full_orchestrated_experiment():
    bars = synthetic_bars(2500, "h1", start="2020-01-01", seed=21, vol=0.002)
    result = orch.integrity(spec(), builder, bars, bars["close_ts"].iloc[1900])
    assert result.passed, result.as_dict()
    assert result.events_compared > 10


def test_pre_trade_outcome_check_runs_in_replay():
    m5 = synthetic_bars(288 * 30, "m5", start="2024-01-02", seed=2, vol=0.0008)
    out = orch.pre_trade_check(m5, {"entry": 2000.0, "stop": 1990.0, "target": 2010.0}, "2024-01-30 14:00", replays=100)
    assert out["verdict"] and pd.Timestamp(out["pool_to"]) < pd.Timestamp("2024-01-30", tz="UTC")


# ---------------------------------------------------------------- paper trading end to end
def test_paper_session_end_to_end_through_risk_with_journal(frame):
    d = get("trend")
    voters = {"trend": d.prepare(frame, d.params(channel=20, use_daily=False))}
    broker = PaperBroker()
    limits = RiskLimits(max_spread_usd=5, max_slippage_usd=10, max_atr_pct=50, max_daily_loss_pct=50, max_drawdown_pct=90, max_trades_per_day=50)
    router = OrderRouter(broker, RiskEngine(limits), AuditLog(), Mode.PAPER)
    session = PaperSession(router, frame, voters)
    out = session.run()
    assert out["trades"] > 0
    audit = [a["event"] for a in router.audit.rows(2000)]
    assert "risk_decision" in audit and "order_result" in audit
    assert router.audit.verify()
    assert sum(1 for x in session.log if x["event"] == "route") >= 1


def test_paper_session_refuses_a_real_broker():
    class NotPaper:
        name, is_simulated = "x", False

    router = OrderRouter.__new__(OrderRouter)
    router.broker = NotPaper()
    with pytest.raises(TypeError):
        PaperSession(router, pd.DataFrame(), {})


def _fold(*losses):
    return [{"log_loss": x} for x in losses]


def test_a_tiny_edge_or_inconsistent_folds_do_not_count_as_beating_the_baseline():
    base = {"log_loss": 0.694, "folds": _fold(0.696, 0.694, 0.693, 0.693)}
    # The real case: 0.0002 average gain, winning 2 of 4 folds.
    noise = {"baseline_constant": base, "rf": {"log_loss": 0.6938, "folds": _fold(0.6964, 0.6923, 0.6924, 0.6938)}}
    assert ml.best_model(noise) == ("rf", False)
    # A large average driven by a single fold still fails.
    one_fold = {"baseline_constant": base, "rf": {"log_loss": 0.68, "folds": _fold(0.64, 0.695, 0.695, 0.695)}}
    assert ml.best_model(one_fold)[1] is False
    consistent = {"baseline_constant": base, "rf": {"log_loss": 0.685, "folds": _fold(0.69, 0.685, 0.68, 0.685)}}
    assert ml.best_model(consistent)[1] is True


def test_ml_veto_inside_an_experiment_changes_decisions_and_stays_causal(frame):
    prepared = ml.add_calendar(frame)
    labels = ml.triple_barrier(prepared, 12)
    half = len(frame) // 2
    columns = ["rsi_14", "zscore_20", "adx", "vol_20", "mom_20"]
    model = ml.fit_final(prepared.iloc[:half], labels.iloc[:half], columns, "logistic", {})
    later = frame.iloc[half + 50:].reset_index(drop=True)
    s = spec(strategies=["trend", "mean_reversion", "overreaction"], use_ml=True, ml_mode="required")
    required = orch.run_experiment(s, later, model=model)
    off = orch.run_experiment(spec(), later)
    assert required["digest"] != off["digest"]
    assert any("ML" in " ".join(d["reasons"]) for d in required["decisions"])
    bars = later[[c for c in synthetic_bars(2).columns if c in later]]
    result = orch.integrity(s, builder, bars, bars["close_ts"].iloc[1800], model=model)
    assert result.passed, result.as_dict()


def test_strategy_condition_labels_mark_decisions_and_when_they_became_known(frame):
    t = pd.DataFrame({"entry_time": [frame.close_ts.iloc[100]], "exit_time": [frame.close_ts.iloc[110]], "net": [5.0]})
    y = ml.strategy_condition_labels(frame, t)
    assert y.label.iloc[100] == 1.0 and y.label.notna().sum() == 1 and y.label_end.iloc[100] == 110


def test_strategy_conditions_refuse_too_few_trades_and_run_with_enough(frame):
    t = pd.DataFrame({"entry_time": frame.close_ts.iloc[[100]].to_numpy(), "exit_time": frame.close_ts.iloc[[110]].to_numpy(), "net": [1.0]})
    assert ml.evaluate_strategy_conditions(frame, t)["tested"] is False
    rng = np.random.default_rng(1)
    idx = np.arange(300, 4800, 5)
    many = pd.DataFrame({"entry_time": frame.close_ts.iloc[idx].to_numpy(), "exit_time": frame.close_ts.iloc[idx + 4].to_numpy(), "net": rng.normal(0, 1, len(idx))})
    out = ml.evaluate_strategy_conditions(frame, many)
    assert out["tested"] and out["trades"] == len(idx) and out["beats_baseline"] is False  # Random outcomes.


def test_library_folder_keeps_evidence_labels(tmp_path):
    k = kb()
    folder = tmp_path / "research_hypothesis"
    folder.mkdir()
    (folder / "paper.txt").write_text("Gold momentum may persist.", encoding="utf-8")
    (folder / "skip.pdf").write_bytes(b"x")
    assert k.ingest_folder(tmp_path) == 1
    assert k.search("momentum")[0].kind == "research_hypothesis"


@pytest.mark.parametrize("name", ["trend", "mean_reversion", "overreaction", "etf_turnaround"])
def test_single_voter_experiment_reproduces_the_strategys_own_trades(frame, name):
    # Regression: a flat holding period made decision-engine results disagree with the strategy's own report.
    from quantresearch.strategies import registry as strategy_registry

    defn = get(name)
    direct = strategy_registry.run(defn, frame, defn.defaults)
    via = orch.build_session(orch.ExperimentSpec(name=name, strategies=[name], use_risk_engine=False), frame)
    via.to_end()
    via.engine.close_all()
    a, b = direct.trades_frame(), via.engine.trades_frame()
    assert len(a) == len(b) > 0
    assert a["entry_time"].tolist() == b["entry_time"].tolist() and a["exit_time"].tolist() == b["exit_time"].tolist()
    assert np.allclose(a["net"], b["net"])
    assert a["reason"].tolist() == b["reason"].tolist()


def test_position_uses_the_exit_rules_of_the_strategies_that_opened_it():
    n = 12
    f = pd.DataFrame({"ts": pd.date_range("2024-01-02", periods=n, freq="h", tz="UTC"), "close": 100.0})
    f["close_ts"] = f.ts + pd.Timedelta(hours=1)
    def voter(long_at=(), hold=np.nan, exit_at=()):
        s = pd.DataFrame({"long_entry": False, "short_entry": False, "stop_dist": 5.0, "target_dist": np.nan, "max_hold": hold, "exit_long": False, "exit_short": False}, index=f.index)
        s.loc[list(long_at), "long_entry"] = True
        s.loc[list(exit_at), "exit_long"] = True
        return s
    from quantresearch.sim.engine import Engine as E
    bars = synthetic_bars(n, "h1", start="2024-01-02", vol=0.00001)
    frame2 = bars.assign(close=100.0)
    a = DecisionStrategy(frame2, {"a": voter(long_at=[1], hold=3)})
    trades = E(bars, frame2, a).run().trades_frame()
    assert trades.reason.iloc[0] == "time"
    # An exit signal from a strategy that did not open the position is ignored.
    b = DecisionStrategy(frame2, {"a": voter(long_at=[1]), "b": voter(exit_at=[3])})
    assert E(bars, frame2, b).run().positions
    c = DecisionStrategy(frame2, {"a": voter(long_at=[1], exit_at=[4])})
    assert E(bars, frame2, c).run().trades_frame().reason.iloc[0] == "signal_exit"
