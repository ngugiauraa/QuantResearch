"""Research laboratory: clustering, regimes, change points, anomalies, feature importance."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.ensemble import IsolationForest, RandomForestRegressor
from sklearn.feature_selection import mutual_info_regression
from sklearn.inspection import permutation_importance
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

REGIME_INPUTS = ["vol_20", "vol_ratio", "adx", "bb_width", "mom_20"]


def _matrix(df: pd.DataFrame, columns: list[str]) -> tuple[pd.DataFrame, np.ndarray]:
    frame = df[columns].replace([np.inf, -np.inf], np.nan).dropna()
    return frame, StandardScaler().fit_transform(frame)


def cluster(df: pd.DataFrame, columns: list[str], k: int = 4, seed: int = 19) -> pd.Series:
    frame, x = _matrix(df, columns)
    labels = KMeans(n_clusters=k, n_init=10, random_state=seed).fit_predict(x)
    return pd.Series(labels, index=frame.index, name="cluster")


def fit_regimes(train: pd.DataFrame, columns=None, k: int = 3, seed: int = 19):
    """Fit a Gaussian mixture on training data. Labels are ordered by volatility, so they are comparable."""
    columns = list(columns or REGIME_INPUTS)
    frame = train[columns].replace([np.inf, -np.inf], np.nan).dropna()
    scaler = StandardScaler().fit(frame)
    model = GaussianMixture(n_components=k, covariance_type="full", random_state=seed).fit(scaler.transform(frame))
    order = np.argsort(model.means_[:, columns.index("vol_20")] if "vol_20" in columns else np.arange(k))
    return {"columns": columns, "scaler": scaler, "model": model, "rank": {int(c): int(r) for r, c in enumerate(order)}}


REGIME_NAMES = {0: "quiet", 1: "normal", 2: "volatile"}


def predict_regimes(fitted: dict, df: pd.DataFrame) -> pd.DataFrame:
    frame = df[fitted["columns"]].replace([np.inf, -np.inf], np.nan).dropna()
    if frame.empty:
        return pd.DataFrame(columns=["regime", "confidence"])
    x = fitted["scaler"].transform(frame)
    probs = fitted["model"].predict_proba(x)
    raw = probs.argmax(axis=1)
    ranks = [fitted["rank"][int(c)] for c in raw]
    names = [REGIME_NAMES.get(r, f"r{r}") for r in ranks]
    return pd.DataFrame({"regime": names, "confidence": probs.max(axis=1)}, index=frame.index)


def change_points(series: pd.Series, threshold: float = 8.0, drift: float = 0.5) -> list:
    """Two-sided CUSUM on standardized values, with expanding statistics so it stays causal."""
    s = pd.Series(series).dropna()
    mean = s.expanding(min_periods=50).mean().shift(1)
    std = s.expanding(min_periods=50).std().shift(1)
    z = ((s - mean) / std.where(std > 0)).fillna(0.0).to_numpy()
    up = down = 0.0
    points = []
    for i, value in enumerate(z):
        up = max(0.0, up + value - drift)
        down = min(0.0, down + value + drift)
        if up > threshold or down < -threshold:
            points.append(s.index[i])
            up = down = 0.0
    return points


def anomalies(train: pd.DataFrame, test: pd.DataFrame, columns: list[str], contamination: float = 0.005, seed: int = 19) -> pd.Series:
    """Flags outliers in `test` with a model fitted only on `train`."""
    frame, x = _matrix(train, columns)
    scaler = StandardScaler().fit(frame)
    model = IsolationForest(contamination=contamination, random_state=seed, n_estimators=100).fit(scaler.transform(frame))
    target = test[columns].replace([np.inf, -np.inf], np.nan).dropna()
    return pd.Series(model.predict(scaler.transform(target)) == -1, index=target.index, name="anomaly")


def mutual_information(df: pd.DataFrame, columns: list[str], target: pd.Series, seed: int = 19) -> pd.Series:
    joined = pd.concat([df[columns], target.rename("_y")], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    scores = mutual_info_regression(joined[columns], joined["_y"], random_state=seed)
    return pd.Series(scores, index=columns).sort_values(ascending=False)


def feature_importance(train: pd.DataFrame, valid: pd.DataFrame, columns: list[str], target: str, seed: int = 19) -> pd.Series:
    """Permutation importance scored on later data, which is less flattering than in-sample importance."""
    a = train[columns + [target]].replace([np.inf, -np.inf], np.nan).dropna()
    b = valid[columns + [target]].replace([np.inf, -np.inf], np.nan).dropna()
    model = RandomForestRegressor(n_estimators=60, max_depth=6, min_samples_leaf=50, random_state=seed, n_jobs=2)
    model.fit(a[columns], a[target])
    result = permutation_importance(model, b[columns], b[target], n_repeats=3, random_state=seed, n_jobs=1)
    return pd.Series(result.importances_mean, index=columns).sort_values(ascending=False)
