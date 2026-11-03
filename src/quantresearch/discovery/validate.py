"""Out-of-sample, statistical, robustness and adversarial validation of frozen candidates.

Stage 2  walk-forward: a candidate frozen at its vintage cutoff is run on [cutoff, holdout start), which its
         search never saw. Each calendar year is recorded as its own fold.
Stage 3  statistics across every candidate that has been looked at out of sample (computed by the controller):
         Newey-West t with Benjamini-Hochberg across all looks, block-bootstrap Sharpe interval, deflated Sharpe
         with the number of looks as trials, PBO by combinatorially symmetric cross-validation, and a null gate:
         the Sharpe must beat the 95th percentile reached by candidates found the same way on sign-randomised data.
Stage 4  robustness: costs, delay, fills, parameter perturbation, regimes, sessions, sides, years, features.
Stage 5  adversarial: trying to explain the result away (drift, beta, random entries, a few big trades, timing).
Stage 6  confirmation in the authoritative event engine, on signal bars and on M1 execution bars.

Thresholds are fixed here, before any result was seen, and are the same for every candidate.
"""
from __future__ import annotations

import copy
import itertools
import time
import zlib

import numpy as np
import pandas as pd
from scipy import stats as sps

from quantresearch.discovery import spec as S
from quantresearch.discovery.evaluate import daily_series, evaluate, regime_split, sharpe, summarize
from quantresearch.discovery.expr import Costs, Evaluator, SCENARIOS, compile_spec, run_compiled
from quantresearch.discovery.store import open_store
from quantresearch.research import stats as rstats

GATES = {
    "oos_min_trades": 50, "oos_min_positive_year_share": 0.5,
    "fdr_q": 0.05, "dsr_min": 0.95, "null_quantile": 0.95, "null_min_samples": 20, "pbo_max": 0.5, "pbo_min_candidates": 6,
    "perturb_variants": 30, "perturb_positive_share": 0.7, "perturb_median_sharpe_ratio": 0.5,
    "min_bucket_trades": 15, "regime_concentration_max": 0.8, "years_positive_share": 0.6, "top_year_share_max": 0.5,
    "top_trades_removed": 0.05, "random_entry_permutations": 200, "random_entry_p_max": 0.05, "alpha_p_max": 0.05,
}


def pack(arr: np.ndarray) -> bytes:
    a = np.ascontiguousarray(arr)
    return zlib.compress(a.dtype.str.encode() + b"|" + a.tobytes(), 6)


def unpack(blob: bytes) -> np.ndarray:
    raw = zlib.decompress(blob)
    dt, data = raw.split(b"|", 1)
    return np.frombuffer(data, dtype=np.dtype(dt.decode())).copy()


def _oos_evaluator(task: dict) -> Evaluator:
    store = open_store(task["store_root"], task["tf"])
    w = task["vintage"]["oos"]
    a, b = store.bounds(pd.Timestamp(w["start"], tz="UTC"), pd.Timestamp(w["end"], tz="UTC"))
    return Evaluator(store, a, b)


# ---------------------------------------------------------------- stage 2
def stage2_gate(m: dict) -> tuple[bool, str]:
    if m["trades"] < GATES["oos_min_trades"]:
        return False, "insufficient_oos_trades"
    if m["exp_r"] <= 0 or m["total_r"] <= 0:
        return False, "negative_oos_expectancy"
    share = m.get("positive_year_share")
    if share is None or share < GATES["oos_min_positive_year_share"]:
        return False, "inconsistent_oos_folds"
    if (m.get("first_fold") or {}).get("total_r", -1) <= 0:
        return False, "failed_first_oos_fold"
    return True, "passed"


def run_oos(task: dict) -> dict:
    """Stage 2 for a batch of frozen candidates of one vintage."""
    t0, c0 = time.perf_counter(), time.process_time()
    ev = _oos_evaluator(task)
    cutoff = pd.Timestamp(task["vintage"]["cutoff"], tz="UTC")
    first_end = (cutoff + pd.DateOffset(years=2)).value
    out = []
    for item in task["params"]["candidates"]:
        spec = item["spec"]
        m, tr = evaluate(spec, ev, "observed", task.get("risk_pct", 0.5))
        entry_ns = np.asarray(ev.store.open_ns)[tr.lo + tr.entry] if len(tr) else np.zeros(0, np.int64)
        first = entry_ns < first_end
        m["first_fold"] = {"trades": int(first.sum()), "total_r": float(tr.r[first].sum()) if len(tr) else 0.0}
        ok, reason = stage2_gate(m)
        days, rets = daily_series(tr, ev, task.get("risk_pct", 0.5))
        m["t_nw"], m["p_nw"] = nw_pvalue(rets)
        out.append({"hash": item["hash"], "metrics": m, "passed": ok, "reason": reason,
                    "days": pack(days.astype(np.int64)), "rets": pack(rets.astype(np.float64)),
                    "regimes": regime_split(tr, ev)})
    return {"task_id": task["task_id"], "results": out, "runtime_s": time.perf_counter() - t0, "cpu_s": time.process_time() - c0}


# ---------------------------------------------------------------- stage 3 (pure functions used by the controller)
def nw_pvalue(rets: np.ndarray, lags: int = 10) -> tuple[float, float]:
    """One-sided p that mean daily return > 0, Newey-West errors."""
    from statsmodels.regression.linear_model import OLS
    r = np.asarray(rets, float)
    if len(r) < 30 or r.std() == 0:
        return 0.0, 1.0
    fit = OLS(r, np.ones(len(r))).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    t = float(fit.tvalues[0])
    return t, float(sps.norm.sf(t))


def sharpe_ci(rets: np.ndarray, n_boot: int = 1000, seed: int = 19) -> tuple[float, float]:
    return rstats.block_bootstrap_ci(rets, block=20, n_boot=n_boot, seed=seed, statistic=lambda x: sharpe(x))


def pbo(matrix: np.ndarray, blocks: int = 8) -> float | None:
    """Probability of backtest overfitting by CSCV (Bailey, Borwein, Lopez de Prado, Zhu)."""
    t, n = matrix.shape
    if n < 2 or t < blocks * 20:
        return None
    parts = np.array_split(np.arange(t), blocks)
    logits = []
    for combo in itertools.combinations(range(blocks), blocks // 2):
        is_rows = np.concatenate([parts[i] for i in combo])
        oos_rows = np.concatenate([parts[i] for i in range(blocks) if i not in combo])

        def sr(rows):
            x = matrix[rows]
            sd = x.std(axis=0, ddof=1)
            return np.where(sd > 0, x.mean(axis=0) / np.where(sd > 0, sd, 1), 0.0)

        best = int(np.argmax(sr(is_rows)))
        oos = sr(oos_rows)
        rank = (sps.rankdata(oos)[best]) / (n + 1)
        logits.append(np.log(rank / (1 - rank)))
    return float(np.mean(np.asarray(logits) <= 0))


def stage3(pool: list[dict], null_sharpes: dict[str, list[float]]) -> dict[str, dict]:
    """Statistical validation of every candidate that passed stage 2, accounting for ALL out-of-sample looks.

    `pool` holds every real-data candidate evaluated out of sample (passed or not), each with hash, tf, vintage,
    rets, sharpe, stage2_passed. Multiple-testing corrections use the whole pool, not just the survivors.
    """
    from statsmodels.stats.multitest import multipletests
    if not pool:
        return {}
    m = len(pool)
    tp = [(c["t_nw"], c["p_nw"]) if c.get("p_nw") is not None else nw_pvalue(c["rets"]) for c in pool]
    pvals = [p for _, p in tp]
    _, p_bh, _, _ = multipletests(pvals, alpha=GATES["fdr_q"], method="fdr_bh")
    trial_daily = [c["sharpe"] / np.sqrt(252) for c in pool]
    # The deflated-Sharpe benchmark depends only on the set of trials, so it is computed once for everyone
    # (identical to research.stats.deflated_sharpe, which would recompute it per candidate).
    trials = np.asarray([x for x in trial_daily if np.isfinite(x)], dtype="float64")
    if len(trials) < 2 or trials.std(ddof=1) == 0:
        expected_max = 0.0
    else:
        gamma = 0.5772156649
        z1, z2 = sps.norm.ppf(1 - 1 / len(trials)), sps.norm.ppf(1 - 1 / (len(trials) * np.e))
        expected_max = float(trials.std(ddof=1) * ((1 - gamma) * z1 + gamma * z2))
    pooled_null = [s for v in null_sharpes.values() for s in v]
    groups: dict[tuple, list[int]] = {}
    for i, c in enumerate(pool):
        groups.setdefault((c["tf"], c["vintage"]), []).append(i)
    group_pbo = {}
    for key, idx in groups.items():
        # PBO uses every group member whose series was loaded (the controller loads all walk-forward survivors and a
        # fixed, deterministic sample of the rest, so memory stays bounded on long runs).
        loaded = [i for i in idx if pool[i].get("rets") is not None]
        if len(loaded) >= GATES["pbo_min_candidates"]:
            lens = {len(pool[i]["rets"]) for i in loaded}
            if len(lens) == 1:
                group_pbo[key] = pbo(np.column_stack([pool[i]["rets"] for i in loaded]))
    out = {}
    for i, c in enumerate(pool):
        if not c["stage2_passed"]:
            continue
        r = c["rets"]
        dsr = {"trials": int(len(trials)), "expected_max_sr": expected_max, "dsr": rstats.probabilistic_sharpe(r, expected_max)}
        nulls = null_sharpes.get(c["tf"], [])
        nulls = nulls if len(nulls) >= GATES["null_min_samples"] else pooled_null
        null_q = float(np.quantile(nulls, GATES["null_quantile"])) if len(nulls) >= GATES["null_min_samples"] else None
        g_pbo = group_pbo.get((c["tf"], c["vintage"]))
        res = {"oos_looks": m, "t_nw": tp[i][0], "p_nw": tp[i][1], "p_bh": float(p_bh[i]), "dsr": dsr["dsr"],
               "dsr_expected_max_sr_daily": dsr["expected_max_sr"], "null_sharpe_q95": null_q, "null_samples": len(nulls),
               "pbo_group": g_pbo, "pbo_group_size": len(groups[(c["tf"], c["vintage"])])}
        reasons = []
        if res["p_bh"] > GATES["fdr_q"]:
            reasons.append("not_significant_after_fdr")
        if res["dsr"] < GATES["dsr_min"]:
            reasons.append("deflated_sharpe_below_0.95")
        if null_q is None:
            reasons.append("null_calibration_unavailable")
        elif c["sharpe"] <= null_q:
            reasons.append("not_better_than_null_market")
        if g_pbo is None:
            reasons.append("pbo_not_computable")
        elif g_pbo > GATES["pbo_max"]:
            reasons.append("pbo_above_0.5")
        if not reasons:
            lo, hi = c.get("ci") or sharpe_ci(r)
            res["sharpe_ci95"] = [lo, hi]
            if not lo > 0:
                reasons.append("sharpe_ci_includes_zero")
        res["passed"] = not reasons
        res["reasons"] = reasons
        out[c["hash"]] = res
    return out


# ---------------------------------------------------------------- stage 4 and 5
def _numeric_paths(node, path=()):
    """(path, kind) for every perturbable number in a spec."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "meta":
                continue
            p = path + (k,)
            if isinstance(v, (dict, list)):
                yield from _numeric_paths(v, p)
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                if k in ("c", "lo", "hi"):
                    yield p, "threshold"
                elif k in ("n", "max_hold", "lookback", "expiry"):
                    yield p, "integer"
                elif k in ("mult", "r", "trail_atr", "breakeven_r", "offset_atr", "buffer_atr"):
                    yield p, "scale"
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _numeric_paths(v, path + (i,))


def _get(d, path):
    for p in path:
        d = d[p]
    return d


def _set(d, path, value):
    for p in path[:-1]:
        d = d[p]
    d[path[-1]] = value


def _threshold_leaf(spec, path):
    """The leaf a threshold compares against, to scale its perturbation by that leaf's spread."""
    parent = _get(spec, path[:-2]) if path[-1] == "c" else _get(spec, path[:-1])
    return parent.get("a") if isinstance(parent, dict) else None


def perturb(spec: dict, ev_train: Evaluator, rng: np.random.Generator, size: float = 0.15) -> dict:
    s = copy.deepcopy(S.normalize(spec))
    s.pop("meta", None)
    for path, kind in list(_numeric_paths(s)):
        v = _get(s, path)
        if kind == "integer":
            new = max(1, int(round(v * (1 + rng.uniform(-size, size)))))
        elif kind == "scale":
            new = float(v * (1 + rng.uniform(-size, size)))
        else:
            leaf = _threshold_leaf(s, path)
            spread = 1.0
            if isinstance(leaf, dict) and ("f" in leaf or "op" in leaf):
                x = ev_train.num(leaf)[ev_train.window]
                x = x[np.isfinite(x)]
                if len(x) > 100:
                    spread = float(np.quantile(x, 0.75) - np.quantile(x, 0.25)) or 1.0
            new = float(v + rng.uniform(-size, size) * spread)
        _set(s, path, new)
    for block, name in S.RANGES:
        v = s.get(block, {}).get(name)
        if v is not None and not isinstance(v, bool):
            lo, hi = S.RANGES[(block, name)]
            s[block][name] = type(v)(min(max(v, lo), hi))
    _fix_between(s)
    return s


def _fix_between(node):
    if isinstance(node, dict):
        if node.get("op") == "between" and node["lo"] >= node["hi"]:
            node["lo"], node["hi"] = min(node["lo"], node["hi"]) - 1e-6, max(node["lo"], node["hi"]) + 1e-6
        for v in node.values():
            _fix_between(v)
    elif isinstance(node, list):
        for v in node:
            _fix_between(v)


def _gold_daily(ev: Evaluator, days: np.ndarray) -> np.ndarray:
    store = ev.store
    day = np.asarray(store.day_ns[ev.start:ev.end])
    close = np.asarray(store.prices["close"][ev.start:ev.end], float)
    last = pd.Series(close).groupby(day).last()
    g = np.log(last).diff().reindex(days).to_numpy()
    return np.nan_to_num(g)


def run_robust(task: dict) -> dict:
    """Stages 4 and 5 for one candidate, on its out-of-sample window only."""
    t0, c0 = time.perf_counter(), time.process_time()
    spec = task["params"]["spec"]
    ev = _oos_evaluator(task)
    store = ev.store
    v = task["vintage"]
    ev_train = Evaluator(store, *store.bounds(pd.Timestamp(v["discovery"]["start"], tz="UTC"), pd.Timestamp(v["discovery"]["end"], tz="UTC")))
    rng = np.random.default_rng(task["seed"])
    compiled = compile_spec(spec, ev)
    base_tr = run_compiled(compiled, ev, SCENARIOS["observed"])
    base = summarize(base_tr, ev)
    tests: dict[str, dict] = {}

    def add(name, passed, value=None, note="", stage=4):
        tests[name] = {"passed": None if passed is None else bool(passed), "value": value, "note": note, "stage": stage}

    # ---- costs, delay, fills
    for sc in ("spread_x2_equiv", "stress", "delay_1bar", "stress_delay", "no_swap"):
        m = summarize(run_compiled(compiled, ev, SCENARIOS[sc]), ev, detail=False)
        add(f"cost_{sc}", m["exp_r"] > 0 if sc != "no_swap" else None, {"exp_r": m["exp_r"], "sharpe": m["sharpe"], "trades": m["trades"]},
            "informational: financing removed" if sc == "no_swap" else "")
    if S.normalize(spec)["entry"]["type"] == "limit":
        m = summarize(run_compiled(compiled, ev, SCENARIOS["limit_through"]), ev, detail=False)
        add("fill_limit_needs_trade_through", m["exp_r"] > 0, {"exp_r": m["exp_r"]})
    # ---- parameter perturbation
    variants = []
    for _ in range(GATES["perturb_variants"]):
        sv = perturb(spec, ev_train, rng)
        if S.validate(sv, store.names, store.tf):
            continue
        m, _ = evaluate(sv, ev, "observed", detail=False)
        variants.append((m["exp_r"], m["sharpe"]))
    if variants:
        er = np.array([x for x, _ in variants])
        srs = np.array([y for _, y in variants])
        share = float((er > 0).mean())
        med = float(np.median(srs))
        add("parameter_perturbation", share >= GATES["perturb_positive_share"] and med >= GATES["perturb_median_sharpe_ratio"] * base["sharpe"],
            {"variants": len(variants), "positive_share": share, "median_sharpe": med, "base_sharpe": base["sharpe"],
             "worst_exp_r": float(er.min())}, "+-15 % on every threshold, window and exit parameter")
    else:
        add("parameter_perturbation", False, None, "no valid variants")
    # ---- regimes and sessions
    reg = regime_split(base_tr, ev)
    for name in ("volatility", "trend", "session"):
        table = reg.get(name, {})
        big = {k: x for k, x in table.items() if x["trades"] >= GATES["min_bucket_trades"]}
        pos = {k: x["total_r"] for k, x in big.items() if x["total_r"] > 0}
        total_pos = sum(pos.values())
        conc = max(pos.values()) / total_pos if total_pos > 0 else 1.0
        if name == "session":
            add("session_dependence", None, {"table": table, "concentration": conc}, "informational", 4)
            continue
        positive_buckets = sum(1 for x in big.values() if x["exp_r"] > 0)
        if len(big) < 2:
            # Trades fall in one state only (often by design, through a regime filter): untestable, and penalised in ranking.
            add(f"regime_{name}", None, {"table": table, "concentration": conc}, "only one state has enough trades to test")
        else:
            ok = positive_buckets >= 2 and conc <= GATES["regime_concentration_max"]
            add(f"regime_{name}", ok, {"table": table, "concentration": conc, "positive_buckets": positive_buckets})
    # ---- sides
    s_norm = S.normalize(spec)
    sides = base.get("sides", {})
    if s_norm["direction"] == "both":
        bad = [k for k, x in sides.items() if x["trades"] >= GATES["min_bucket_trades"] and (x["exp_r"] or 0) <= 0]
        add("sides_independent", not bad, sides, f"negative side: {bad}" if bad else "")
    else:
        add("sides_independent", None, sides, "single-direction strategy")
    # ---- years and episodes
    ys = base.get("positive_year_share")
    add("years_consistency", ys is not None and ys >= GATES["years_positive_share"], {"positive_year_share": ys, "years": base.get("years_detail")})
    tys = base.get("top_year_share")
    add("not_one_episode", tys is not None and tys < GATES["top_year_share_max"], {"top_year_share": tys})
    # ---- feature dependency (stage 4, informational except collapse report)
    deps = {}
    for f in S.leaves(spec):
        if f not in store.index:
            continue
        arr = np.asarray(store.feature(f))
        shifted = np.roll(arr, len(arr) // 3)
        ev_f = Evaluator(store, ev.start, ev.end, overrides={f: shifted})
        m, _ = evaluate(spec, ev_f, "observed", detail=False)
        deps[f] = {"exp_r_without": m["exp_r"], "trades": m["trades"],
                   "share_of_edge_lost": float(1 - m["exp_r"] / base["exp_r"]) if base.get("exp_r") else None}
    dominant = max(deps.items(), key=lambda kv: kv[1]["share_of_edge_lost"] or 0)[0] if deps else None
    add("feature_dependency", None, {"features": deps, "most_important": dominant}, "each feature replaced by a time-shifted copy of itself")
    # ---- adversarial (stage 5)
    r = base_tr.r
    if len(r) >= 20:
        cut = max(1, int(np.ceil(len(r) * GATES["top_trades_removed"])))
        rest = np.sort(r)[:-cut]
        add("without_top_trades", rest.mean() > 0, {"removed": cut, "exp_r_rest": float(rest.mean())}, "top 5 % of trades removed", 5)
    days, rets = daily_series(base_tr, ev)
    gold = _gold_daily(ev, days)
    from statsmodels.regression.linear_model import OLS
    from statsmodels.tools.tools import add_constant
    if len(rets) > 60 and rets.std() > 0:
        fit = OLS(rets, add_constant(gold)).fit(cov_type="HAC", cov_kwds={"maxlags": 5})
        alpha_t = float(fit.tvalues[0])
        add("alpha_beyond_gold_drift", float(sps.norm.sf(alpha_t)) < GATES["alpha_p_max"],
            {"alpha_daily": float(fit.params[0]), "alpha_t": alpha_t, "beta": float(fit.params[1])}, "daily returns regressed on gold's daily return", 5)
    # falling-gold periods: entries when gold's trailing ~6 months was negative
    if len(base_tr):
        close = np.asarray(store.prices["close"], float)
        bars_6m = {"m15": 26 * 4 * 24 * 5, "h1": 26 * 24 * 5, "h4": 26 * 6 * 5, "d1ny": 126}[store.tf]
        idx = base_tr.lo + base_tr.entry - 1
        prior = np.clip(idx - bars_6m, 0, None)
        falling = close[idx] < close[prior]
        n_f = int(falling.sum())
        if n_f >= GATES["min_bucket_trades"]:
            add("works_when_gold_falls", r[falling].mean() > 0, {"trades": n_f, "exp_r": float(r[falling].mean())}, "entries after a falling 6 months", 5)
        else:
            add("works_when_gold_falls", None, {"trades": n_f}, "too few entries in falling periods to test", 5)
    # random entries with the same exits and the same number of signals per side
    wl, ws = compiled.want_long, compiled.want_short
    win = ev.window
    nl, ns = int(wl[win].sum()), int(ws[win].sum())
    ok_l = np.flatnonzero(np.isfinite(compiled.arrays["stop_l"][win]))
    ok_s = np.flatnonzero(np.isfinite(compiled.arrays["stop_s"][win]))
    if len(r) >= 20 and (nl + ns) > 0:
        totals = []
        for _ in range(GATES["random_entry_permutations"]):
            rl, rs = np.zeros_like(wl), np.zeros_like(ws)
            if nl and len(ok_l):
                rl[win.start + rng.choice(ok_l, min(nl, len(ok_l)), replace=False)] = True
            if ns and len(ok_s):
                rs[win.start + rng.choice(ok_s, min(ns, len(ok_s)), replace=False)] = True
            totals.append(run_compiled(compiled, ev, SCENARIOS["observed"], rl, rs).r.sum())
        totals = np.asarray(totals)
        p = float((1 + (totals >= r.sum()).sum()) / (len(totals) + 1))
        add("beats_random_entries", p < GATES["random_entry_p_max"], {"p": p, "actual_total_r": float(r.sum()),
                                                                     "random_median_total_r": float(np.median(totals))},
            "same exits, same signal count per side, random bars", 5)
    shifted = []
    for k in (2, 3, 5, 8):
        sl, ss = np.r_[np.zeros(k, bool), wl[:-k]], np.r_[np.zeros(k, bool), ws[:-k]]
        shifted.append(summarize(run_compiled(compiled, ev, SCENARIOS["observed"], sl, ss), ev, detail=False)["exp_r"])
    med = float(np.median(shifted))
    add("timing_specific", None, {"shifted_exp_r": shifted, "median": med, "base_exp_r": base.get("exp_r")},
        "signals acted on 2-8 bars late; similar results mean the edge is exposure rather than timing", 5)
    stage4 = all(t["passed"] for t in tests.values() if t["stage"] == 4 and t["passed"] is not None)
    stage5 = all(t["passed"] for t in tests.values() if t["stage"] == 5 and t["passed"] is not None)
    failed = sorted(k for k, t in tests.items() if t["passed"] is False)
    return {"task_id": task["task_id"], "hash": task["params"]["hash"], "tests": tests, "stage4_passed": stage4,
            "stage5_passed": stage4 and stage5, "failed": failed, "timing_ratio": (med / base["exp_r"]) if base.get("exp_r") else None,
            "runtime_s": time.perf_counter() - t0, "cpu_s": time.process_time() - c0}


# ---------------------------------------------------------------- stage 6
def run_confirm(task: dict) -> dict:
    """Authoritative engine on signal bars (with compounding), and on M1 execution bars year by year."""
    from quantresearch.discovery.confirm import engine_run
    from quantresearch.warehouse.store import load_bars
    t0, c0 = time.perf_counter(), time.process_time()
    spec = task["params"]["spec"]
    ev = _oos_evaluator(task)
    costs = Costs("observed_no_swap", swap=False)
    compiled = compile_spec(spec, ev)
    kern = run_compiled(compiled, ev, costs)
    eng = engine_run(spec, ev, costs, risk_pct=task.get("risk_pct", 0.5))
    tf_ = eng.trades_frame()
    parity = len(tf_) == len(kern) and (len(tf_) == 0 or float(np.max(np.abs((tf_["net"] / (tf_["lots"] * 100)).to_numpy() - kern.pnl_oz))) < 1e-6)
    from quantresearch.sim import metrics
    rep = metrics.report(tf_, eng.equity_curve(), 100_000.0)
    rep.pop("breakdowns", None)
    out = {"engine_signal_bars": {"parity_with_kernel": bool(parity), "report": rep, "note": "compounding, lot rounding, no financing"}}
    if task["params"].get("m1", True):
        store = ev.store
        start = pd.Timestamp(np.asarray(store.open_ns)[ev.start], tz="UTC")
        end = pd.Timestamp(task["vintage"]["oos"]["end"], tz="UTC")
        yearly, total_r, n = [], 0.0, 0
        y = start
        while y < end:
            ny = min(end, pd.Timestamp(year=y.year + 1, month=1, day=1, tz="UTC"))
            a, b = store.bounds(y, ny)
            if b - a > 10:
                part = Evaluator(store, a, b)
                m1 = load_bars("m1", y, ny)
                m1 = m1[m1["close_ts"] <= ny].reset_index(drop=True)
                if len(m1):
                    e = engine_run(spec, part, costs, execution=m1, risk_pct=task.get("risk_pct", 0.5))
                    t = e.trades_frame()
                    rr = t["r_multiple"].dropna().to_numpy() if len(t) else np.zeros(0)
                    yearly.append({"year": y.year, "trades": int(len(t)), "total_r": float(rr.sum())})
                    total_r += float(rr.sum())
                    n += len(rr)
                del m1
            y = ny
        out["engine_m1_execution"] = {"trades": n, "total_r": total_r, "exp_r": total_r / n if n else None, "by_year": yearly,
                                      "passed": bool(n > 0 and total_r > 0),
                                      "note": "M1 bars decide fills inside each signal bar. Run per calendar year, so positions close at each year end."}
    out["passed"] = bool(parity and out.get("engine_m1_execution", {"passed": True})["passed"])
    return {"task_id": task["task_id"], "hash": task["params"]["hash"], "confirm": out,
            "runtime_s": time.perf_counter() - t0, "cpu_s": time.process_time() - c0}
