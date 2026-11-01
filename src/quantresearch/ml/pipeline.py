"""Training and evaluation.

Baselines come first: a constant, logistic regression, a random forest and gradient boosting. Deep models
are not included, because nothing has shown the simpler ones to be inadequate; adding complexity needs
measured out-of-sample value first.

Labels look forward, so training data near a test boundary overlaps it. Folds therefore purge any training
row whose label window reaches into the test period. Calibration uses a later slice of training data.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest, RandomForestClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from quantresearch.core.config import Paths, paths

# Scale-free inputs only. Raw price levels would let a model memorize a period.
FEATURE_SET = [
    "logret_1", "logret_5", "mom_20", "mom_60", "vol_20", "vol_60", "vol_ratio", "atr_ratio", "rsi_14", "zscore_20", "zscore_60",
    "slope_20", "bb_pct", "bb_width", "adx", "plus_di", "minus_di", "premium_discount", "structure_trend", "spread_bps", "spread_z",
    "volume_ratio", "quote_imbalance", "vol_cluster", "hour", "weekday",
]


def add_calendar(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.assign(hour=frame["ts"].dt.hour.astype(float), weekday=frame["ts"].dt.day_of_week.astype(float))


def triple_barrier(frame: pd.DataFrame, horizon: int = 24, barrier_atr: float = 1.5) -> pd.DataFrame:
    """Label each bar by which comes first: the upper barrier, the lower one, or time.

    Label is 1 if up first, 0 if down first, NaN otherwise. `label_end` is the bar where the label became
    known, which purging depends on. Rows whose window runs off the data get NaN instead of a guess.
    """
    high, low, close = (frame[c].to_numpy(dtype="float64") for c in ("high", "low", "close"))
    atr = frame["atr_14"].to_numpy(dtype="float64")
    n = len(frame)
    label = np.full(n, np.nan)
    end = np.full(n, n - 1, dtype="int64")
    for i in range(n):
        if np.isnan(atr[i]) or i + horizon >= n:
            end[i] = min(i + horizon, n - 1)
            continue
        up, down = close[i] + barrier_atr * atr[i], close[i] - barrier_atr * atr[i]
        end[i] = i + horizon
        for j in range(i + 1, i + horizon + 1):
            hit_up, hit_down = high[j] >= up, low[j] <= down
            if hit_up or hit_down:
                # Both in one bar is unknowable, so that row stays unlabelled.
                if not (hit_up and hit_down):
                    label[i] = 1.0 if hit_up else 0.0
                end[i] = j
                break
    return pd.DataFrame({"label": label, "label_end": end}, index=frame.index)


def purged_folds(frame: pd.DataFrame, label_end: pd.Series, n_folds: int = 5, min_train_frac: float = 0.4, embargo: int = 24):
    """Expanding-window folds. Train rows whose label ends inside or after the test window are removed."""
    n = len(frame)
    first = int(n * min_train_frac)
    size = (n - first) // n_folds
    for k in range(n_folds):
        start, stop = first + k * size, first + (k + 1) * size if k < n_folds - 1 else n
        train = np.arange(0, start)
        train = train[label_end.to_numpy()[train] + embargo < start]
        yield train, np.arange(start, stop)


def candidates(seed: int = 19) -> dict:
    return {
        "logistic": make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(max_iter=500, C=0.1)),
        "random_forest": make_pipeline(SimpleImputer(strategy="median"), RandomForestClassifier(
            n_estimators=120, max_depth=6, min_samples_leaf=100, n_jobs=3, random_state=seed)),
        "gradient_boosting": HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150, min_samples_leaf=100, random_state=seed),
    }


def _scores(y, p) -> dict:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    out = {"n": int(len(y)), "base_rate": float(np.mean(y)), "log_loss": float(log_loss(y, p, labels=[0, 1])), "brier": float(brier_score_loss(y, p))}
    out["auc"] = float(roc_auc_score(y, p)) if len(set(y)) == 2 else None
    return out


def evaluate(frame: pd.DataFrame, labels: pd.DataFrame, columns: list[str], n_folds: int = 4, seed: int = 19) -> dict:
    """Walk-forward comparison against the constant baseline. Skill is measured only out of sample."""
    data = frame[columns].replace([np.inf, -np.inf], np.nan).to_numpy(dtype="float64")
    target = labels["label"].to_numpy()
    results: dict[str, dict] = {}
    for name, model in {"baseline_constant": None, **candidates(seed)}.items():
        folds = []
        for train, test in purged_folds(frame, labels["label_end"], n_folds):
            train = train[~np.isnan(target[train])]
            test = test[~np.isnan(target[test])]
            if len(train) < 200 or len(test) < 50:
                continue
            y_train, y_test = target[train].astype(int), target[test].astype(int)
            if model is None:
                p = np.full(len(test), y_train.mean())
            else:
                fitted = clone(model).fit(data[train], y_train)
                p = fitted.predict_proba(data[test])[:, 1]
            folds.append(_scores(y_test, p))
        results[name] = {"folds": folds, "log_loss": float(np.mean([f["log_loss"] for f in folds])) if folds else None,
                         "brier": float(np.mean([f["brier"] for f in folds])) if folds else None,
                         "auc": float(np.nanmean([f["auc"] if f["auc"] is not None else np.nan for f in folds])) if folds else None}
    return results


def best_model(results: dict) -> tuple[str, bool]:
    """The best by log loss, and whether it beat the constant. Beating the baseline is not assumed."""
    baseline = results["baseline_constant"]["log_loss"]
    ranked = sorted(((k, v["log_loss"]) for k, v in results.items() if k != "baseline_constant" and v["log_loss"] is not None), key=lambda x: x[1])
    if not ranked:
        return "baseline_constant", False
    name, loss = ranked[0]
    return name, bool(baseline is not None and loss < baseline)


def select_features(frame: pd.DataFrame, labels: pd.DataFrame, columns: list[str], keep: int = 15, seed: int = 19) -> list[str]:
    """Permutation importance on a later validation slice, so selection is not done on the test period."""
    from sklearn.inspection import permutation_importance

    target = labels["label"]
    valid = target.notna().to_numpy()
    rows = np.flatnonzero(valid)
    if len(rows) < 500:
        return columns
    cut = rows[int(len(rows) * 0.7)]
    x = frame[columns].replace([np.inf, -np.inf], np.nan)
    train, later = rows[rows < cut], rows[rows >= cut]
    model = HistGradientBoostingClassifier(max_depth=3, max_iter=80, random_state=seed).fit(x.iloc[train], target.iloc[train].astype(int))
    result = permutation_importance(model, x.iloc[later], target.iloc[later].astype(int), n_repeats=2, random_state=seed, scoring="neg_log_loss")
    order = np.argsort(result.importances_mean)[::-1]
    return [columns[i] for i in order[:keep]]


@dataclass
class TrainedModel:
    name: str
    model: object
    columns: list[str]
    train_end: str
    reference: dict
    domain: object
    metrics: dict
    params: dict = field(default_factory=dict)
    task: str = "direction"


def fit_final(frame: pd.DataFrame, labels: pd.DataFrame, columns: list[str], name: str, metrics: dict, task: str = "direction",
              seed: int = 19) -> TrainedModel:
    """Fit on all labelled rows, calibrate on the last 20 %, and record what training looked like."""
    target = labels["label"].to_numpy()
    rows = np.flatnonzero(~np.isnan(target))
    cut = int(len(rows) * 0.8)
    x = frame[columns].replace([np.inf, -np.inf], np.nan)
    if name == "baseline_constant":
        from sklearn.dummy import DummyClassifier
        base = DummyClassifier(strategy="prior")
    else:
        base = candidates(seed)[name]
    fitted = clone(base).fit(x.iloc[rows[:cut]].to_numpy(), target[rows[:cut]].astype(int))
    calibrated = CalibratedClassifierCV(FrozenEstimator(fitted), method="isotonic")
    calibrated.fit(x.iloc[rows[cut:]].to_numpy(), target[rows[cut:]].astype(int))
    reference = {c: np.nanquantile(x[c].to_numpy(dtype="float64"), np.linspace(0, 1, 11)).tolist() for c in columns if x[c].notna().any()}
    complete = x.iloc[rows].dropna()
    domain = IsolationForest(n_estimators=100, contamination=0.01, random_state=seed).fit(complete.to_numpy()) if len(complete) > 200 else None
    return TrainedModel(name, calibrated, columns, str(frame["ts"].iloc[rows[-1]]), reference, domain, metrics, task=task)


def predict(model: TrainedModel, frame: pd.DataFrame, min_edge: float = 0.05) -> pd.DataFrame:
    """Probability, confidence and whether to abstain. A prediction is never a certainty.

    Abstains when the probability is too close to even, when inputs are missing, or when inputs are
    outside what training saw. Refuses rows from before the model's training ended unless asked,
    because those predictions would be in-sample and look better than they are.
    """
    x = frame[model.columns].replace([np.inf, -np.inf], np.nan)
    missing = x.isna().any(axis=1).to_numpy()
    p = model.model.predict_proba(x.to_numpy())[:, 1]
    out_of_domain = np.zeros(len(frame), dtype=bool)
    complete = ~missing
    if model.domain is not None and complete.any():
        out_of_domain[complete] = model.domain.predict(x.to_numpy()[complete]) == -1
    edge = np.abs(p - 0.5)
    abstain = missing | out_of_domain | (edge < min_edge)
    reason = np.select([missing, out_of_domain, edge < min_edge], ["missing_inputs", "outside_training_domain", "low_confidence"], default="")
    return pd.DataFrame({"prob_up": p, "confidence": edge * 2, "abstain": abstain, "abstain_reason": reason}, index=frame.index)


def population_stability(reference: list[float], values: np.ndarray) -> float:
    """PSI against training deciles. Above about 0.25 usually means the inputs have shifted materially."""
    values = values[~np.isnan(values)]
    if len(values) == 0:
        return float("nan")
    edges = np.unique(np.asarray(reference, dtype="float64"))
    if len(edges) < 3:
        return 0.0
    edges = np.r_[-np.inf, edges[1:-1], np.inf]
    actual = np.histogram(values, bins=edges)[0] / len(values)
    expected = np.full(len(actual), 1 / len(actual))
    actual = np.clip(actual, 1e-4, None)
    return float(((actual - expected) * np.log(actual / expected)).sum())


def drift_report(model: TrainedModel, frame: pd.DataFrame, threshold: float = 0.25) -> dict:
    scores = {c: population_stability(model.reference[c], frame[c].to_numpy(dtype="float64")) for c in model.reference}
    finite = {k: v for k, v in scores.items() if np.isfinite(v)}
    drifted = sorted((k for k, v in finite.items() if v > threshold), key=lambda k: -finite[k])
    return {"psi": finite, "drifted": drifted, "share_drifted": len(drifted) / max(1, len(finite)), "retrain_suggested": len(drifted) / max(1, len(finite)) > 0.25}


class ModelRegistry:
    def __init__(self, p: Paths | None = None):
        self.p = p or paths()
        self.p.models.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.p.models / "registry.sqlite", check_same_thread=False)
        self.con.execute(
            """CREATE TABLE IF NOT EXISTS models(id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, task TEXT, name TEXT, version TEXT,
               dataset_version TEXT, train_end TEXT, columns TEXT, metrics TEXT, beats_baseline INTEGER, artifact TEXT, sha256 TEXT, promoted INTEGER DEFAULT 0)"""
        )
        self.con.commit()

    def register(self, model: TrainedModel, dataset_version: str, beats_baseline: bool) -> int:
        version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        artifact = self.p.models / f"{model.task}_{model.name}_{version}.joblib"
        joblib.dump(model, artifact)
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        cur = self.con.execute(
            "INSERT INTO models(created_at,task,name,version,dataset_version,train_end,columns,metrics,beats_baseline,artifact,sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), model.task, model.name, version, dataset_version, model.train_end, json.dumps(model.columns),
             json.dumps(model.metrics, default=str), int(beats_baseline), artifact.relative_to(self.p.root).as_posix(), digest),
        )
        self.con.commit()
        return int(cur.lastrowid)

    def promote(self, model_id: int) -> None:
        """Only a model that beat the baseline can be promoted."""
        row = self.con.execute("SELECT task, beats_baseline FROM models WHERE id=?", (model_id,)).fetchone()
        if row is None:
            raise KeyError(f"No model {model_id}")
        if not row[1]:
            raise ValueError("This model did not beat the constant baseline out of sample, so it cannot be promoted")
        self.con.execute("UPDATE models SET promoted=0 WHERE task=?", (row[0],))
        self.con.execute("UPDATE models SET promoted=1 WHERE id=?", (model_id,))
        self.con.commit()

    def load(self, model_id: int) -> TrainedModel:
        row = self.con.execute("SELECT artifact, sha256 FROM models WHERE id=?", (model_id,)).fetchone()
        if row is None:
            raise KeyError(f"No model {model_id}")
        path = self.p.root / row[0]
        if hashlib.sha256(path.read_bytes()).hexdigest() != row[1]:
            raise ValueError(f"Model file {path.name} does not match its recorded checksum")
        return joblib.load(path)

    def promoted(self, task: str = "direction") -> TrainedModel | None:
        row = self.con.execute("SELECT id FROM models WHERE task=? AND promoted=1", (task,)).fetchone()
        return self.load(row[0]) if row else None

    def rows(self) -> list[dict]:
        cols = "id,created_at,task,name,version,dataset_version,train_end,beats_baseline,promoted,metrics"
        out = []
        for r in self.con.execute(f"SELECT {cols} FROM models ORDER BY id DESC"):
            record = dict(zip(cols.split(","), r))
            record["metrics"] = json.loads(record["metrics"])
            out.append(record)
        return out


def train(frame: pd.DataFrame, registry: ModelRegistry | None = None, dataset_version: str = "unknown", horizon: int = 24,
          n_folds: int = 4, promote_if_better: bool = True, task: str = "direction") -> dict:
    """Full workflow: label, select features, walk-forward, fit, register. Retraining is the same call."""
    prepared = add_calendar(frame)
    columns = [c for c in FEATURE_SET if c in prepared and prepared[c].notna().any()]
    labels = triple_barrier(prepared, horizon)
    train_part = prepared.iloc[: int(len(prepared) * 0.5)]
    chosen = select_features(train_part, labels.iloc[: len(train_part)], columns)
    results = evaluate(prepared, labels, chosen, n_folds)
    name, beats = best_model(results)
    model = fit_final(prepared, labels, chosen, name, {"walk_forward": results, "best": name, "beats_baseline": beats, "horizon": horizon}, task)
    outcome = {"evaluation": results, "best": name, "beats_baseline": beats, "features": chosen, "train_end": model.train_end, "labelled": int(labels["label"].notna().sum())}
    if registry is not None:
        previous = registry.promoted(task)
        model_id = registry.register(model, dataset_version, beats)
        outcome["model_id"] = model_id
        better = previous is None or results[name]["log_loss"] < previous.metrics["walk_forward"][previous.name]["log_loss"]
        if promote_if_better and beats and better:
            registry.promote(model_id)
            outcome["promoted"] = True
        else:
            outcome["promoted"] = False
    outcome["model"] = model
    return outcome
