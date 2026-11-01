"""Statistical tests for the research laboratory.

Forward returns overlap and are autocorrelated, so plain t-statistics overstate significance.
Conditional tests therefore use Newey-West errors and a block bootstrap.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as sps
from statsmodels.regression.linear_model import OLS
from statsmodels.stats.multitest import multipletests
from statsmodels.tools.tools import add_constant
from statsmodels.tsa.stattools import adfuller, coint, kpss


def describe_returns(returns: pd.Series) -> dict:
    r = pd.Series(returns).dropna()
    if len(r) < 3:
        return {"n": int(len(r))}
    return {
        "n": int(len(r)), "mean": float(r.mean()), "std": float(r.std()),
        "skew": float(sps.skew(r)), "excess_kurtosis": float(sps.kurtosis(r)),
        "min": float(r.min()), "max": float(r.max()),
        "p01": float(r.quantile(0.01)), "p99": float(r.quantile(0.99)),
        "autocorr_1": float(r.autocorr(1)), "abs_autocorr_1": float(r.abs().autocorr(1)),
    }


def stationarity(series: pd.Series) -> dict:
    """ADF's null is a unit root. KPSS's null is stationarity. Both are reported because they can disagree."""
    s = pd.Series(series).dropna()
    adf = adfuller(s, autolag="AIC")
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        k = kpss(s, regression="c", nlags="auto")
    return {"n": int(len(s)), "adf_stat": float(adf[0]), "adf_p": float(adf[1]), "kpss_stat": float(k[0]), "kpss_p": float(k[1]),
            "verdict": "stationary" if adf[1] < 0.05 and k[1] > 0.05 else
                       ("non_stationary" if adf[1] >= 0.05 and k[1] <= 0.05 else "ambiguous")}


def cointegration(y: pd.Series, x: pd.Series) -> dict:
    frame = pd.concat([y, x], axis=1).dropna()
    t, p, crit = coint(frame.iloc[:, 0], frame.iloc[:, 1])
    fit = OLS(frame.iloc[:, 0], add_constant(frame.iloc[:, 1])).fit()
    residual = fit.resid
    lagged = residual.shift(1).dropna()
    slope = np.polyfit(lagged, residual.loc[lagged.index] - lagged, 1)[0]
    half_life = float(-np.log(2) / slope) if slope < 0 else None
    return {"n": int(len(frame)), "t": float(t), "p": float(p), "beta": float(fit.params.iloc[1]), "half_life_bars": half_life,
            "cointegrated_5pct": bool(p < 0.05)}


def rolling_correlation(a: pd.Series, b: pd.Series, window: int) -> pd.Series:
    return a.rolling(window, min_periods=window).corr(b)


def regression(y: pd.Series, xs: pd.DataFrame, hac_lags: int = 5) -> dict:
    frame = pd.concat([y.rename("y"), xs], axis=1).dropna()
    fit = OLS(frame["y"], add_constant(frame.drop(columns="y"))).fit(cov_type="HAC", cov_kwds={"maxlags": hac_lags})
    return {"n": int(fit.nobs), "r2": float(fit.rsquared),
            "coef": {k: float(v) for k, v in fit.params.items()},
            "t": {k: float(v) for k, v in fit.tvalues.items()},
            "p": {k: float(v) for k, v in fit.pvalues.items()}}


def block_bootstrap_ci(values: np.ndarray, block: int = 20, n_boot: int = 2000, alpha: float = 0.05, seed: int = 19,
                       statistic=np.mean) -> tuple[float, float]:
    """Confidence interval by resampling contiguous blocks, which keeps short-range dependence."""
    x = np.asarray(values, dtype="float64")
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    block = max(1, min(block, n))
    count = int(np.ceil(n / block))
    starts = rng.integers(0, n - block + 1, size=(n_boot, count))
    out = np.empty(n_boot)
    for i in range(n_boot):
        out[i] = statistic(np.concatenate([x[s:s + block] for s in starts[i]])[:n])
    return float(np.quantile(out, alpha / 2)), float(np.quantile(out, 1 - alpha / 2))


def conditional_test(forward: pd.Series, mask: pd.Series, side: int, horizon: int, min_events: int = 30) -> dict:
    """Does the forward return after a condition differ from the unconditional one, in the expected way?

    Tests excess = forward return minus the unconditional mean, so gold's long upward drift cannot
    pass for a signal. Reports a one-sided p-value in the hypothesised direction, using Newey-West
    errors with lags equal to the horizon because the forward windows overlap.
    """
    frame = pd.DataFrame({"f": forward, "m": mask.fillna(False).astype(bool)}).dropna(subset=["f"])
    selected = frame.loc[frame["m"], "f"]
    n = int(len(selected))
    if n < min_events:
        return {"events": n, "tested": False, "reason": f"fewer than {min_events} events"}
    drift = float(frame["f"].mean())
    excess = (selected - drift).to_numpy()
    fit = OLS(excess, np.ones(n)).fit(cov_type="HAC", cov_kwds={"maxlags": max(1, horizon)})
    t = float(fit.tvalues[0])
    p_one = float(sps.norm.sf(t * side))
    low, high = block_bootstrap_ci(excess, block=max(2, horizon), n_boot=500)
    return {"events": n, "tested": True, "mean_forward": float(selected.mean()), "unconditional_mean": drift,
            "excess_mean": float(excess.mean()), "t_hac": t, "p_one_sided": p_one, "ci95_excess": [low, high],
            "hit_rate": float((np.sign(selected) == side).mean())}


def benjamini_hochberg(p_values: list[float], alpha: float = 0.05) -> list[dict]:
    """False-discovery-rate control across a family of tests."""
    p = np.asarray(p_values, dtype="float64")
    if len(p) == 0:
        return []
    reject, adjusted, _, _ = multipletests(p, alpha=alpha, method="fdr_bh")
    return [{"p": float(a), "p_adjusted": float(b), "significant": bool(c)} for a, b, c in zip(p, adjusted, reject)]


def sharpe(returns: np.ndarray, periods: float = 252) -> float:
    r = np.asarray(returns, dtype="float64")
    r = r[~np.isnan(r)]
    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    return float(r.mean() / sd * np.sqrt(periods)) if sd > 0 else 0.0


def probabilistic_sharpe(returns: np.ndarray, benchmark_sr: float = 0.0) -> float:
    """Chance the true per-period Sharpe exceeds a benchmark, allowing for skew and fat tails."""
    r = np.asarray(returns, dtype="float64")
    r = r[~np.isnan(r)]
    n = len(r)
    sd = r.std(ddof=1) if n > 1 else 0.0
    if n < 3 or sd == 0:
        return 0.0
    sr = r.mean() / sd
    denominator = np.sqrt(max(1e-12, 1 - sps.skew(r) * sr + (sps.kurtosis(r) / 4) * sr ** 2))
    return float(sps.norm.cdf((sr - benchmark_sr) * np.sqrt(n - 1) / denominator))


def deflated_sharpe(returns: np.ndarray, trial_sharpes: list[float]) -> dict:
    """Deflated Sharpe: raises the bar according to how many variants were tried, per Bailey and Lopez de Prado."""
    trials = np.asarray([s for s in trial_sharpes if np.isfinite(s)], dtype="float64")
    n = len(trials)
    if n < 2 or trials.std(ddof=1) == 0:
        return {"trials": int(n), "expected_max_sr": 0.0, "dsr": probabilistic_sharpe(returns, 0.0)}
    gamma = 0.5772156649
    z1, z2 = sps.norm.ppf(1 - 1 / n), sps.norm.ppf(1 - 1 / (n * np.e))
    expected = trials.std(ddof=1) * ((1 - gamma) * z1 + gamma * z2)
    return {"trials": int(n), "expected_max_sr": float(expected), "dsr": probabilistic_sharpe(returns, expected)}


def monte_carlo_trades(net: np.ndarray, initial: float, n_paths: int = 2000, seed: int = 19) -> dict:
    """Reshuffles trade order. Shows how much of the drawdown depends on the sequence that happened."""
    x = np.asarray(net, dtype="float64")
    if len(x) == 0:
        return {"paths": 0}
    rng = np.random.default_rng(seed)
    draws = rng.choice(x, size=(n_paths, len(x)), replace=True)
    equity = initial + np.cumsum(draws, axis=1)
    peaks = np.maximum.accumulate(np.c_[np.full(n_paths, initial), equity], axis=1)
    depth = ((peaks[:, 1:] - equity) / peaks[:, 1:]).max(axis=1)
    return {"paths": n_paths, "final_p05": float(np.quantile(equity[:, -1], 0.05)), "final_p50": float(np.quantile(equity[:, -1], 0.5)),
            "final_p95": float(np.quantile(equity[:, -1], 0.95)), "max_dd_p50": float(np.quantile(depth, 0.5)),
            "max_dd_p95": float(np.quantile(depth, 0.95)), "prob_loss": float((equity[:, -1] < initial).mean())}
