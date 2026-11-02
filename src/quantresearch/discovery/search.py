"""Search tasks, run inside worker processes. Each is a pure function of its task description and the store.

A task reads ONLY its vintage's discovery window: fitness on the inner-training part, selection on the
inner-validation part. Out-of-sample rows are never touched here. Every candidate evaluated is returned as an
experiment record, including invalid, duplicate, equivalent and rejected ones.

Determinism: everything random comes from the task's seed; iteration orders are sorted; so a task gives the same
result in any process, on any worker count.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quantresearch.discovery import generate as G
from quantresearch.discovery import spec as S
from quantresearch.discovery.evaluate import entry_signature, evaluate
from quantresearch.discovery.expr import Evaluator, SCENARIOS
from quantresearch.discovery.store import open_store

MIN_IT_TRADES = 40
MIN_IV_TRADES = 20


def _window(store, w: dict) -> tuple[int, int]:
    return store.bounds(pd.Timestamp(w["start"], tz="UTC"), pd.Timestamp(w["end"], tz="UTC"))


@dataclass
class Context:
    task: dict
    store: object
    ev_it: Evaluator
    ev_iv: Evaluator
    rng: np.random.Generator
    max_complexity: int
    records: list = field(default_factory=list)
    by_hash: dict = field(default_factory=dict)
    sigs: dict = field(default_factory=dict)
    years_it: np.ndarray | None = None

    def year_of(self, tr):
        return self.years_it[tr.entry + tr.lo - self.ev_it.start] if self.years_it is not None else None


def make_context(task: dict) -> Context:
    store = open_store(task["store_root"], task["tf"])
    v = task["vintage"]
    it, iv = _window(store, v["inner_train"]), _window(store, v["inner_validation"])
    ctx = Context(task, store, Evaluator(store, *it), Evaluator(store, *iv), np.random.default_rng(task["seed"]),
                  int(task.get("max_complexity", 24)))
    ctx.years_it = pd.DatetimeIndex(np.asarray(store.open_ns[it[0]:it[1]]), tz="UTC").year.to_numpy()
    return ctx


def fitness(m: dict, complexity: int, pos_year_share: float | None) -> float:
    """Training-window objective. Sharpe of daily returns, discounted for few trades, complexity, exposure, inconsistency."""
    n = m["trades"]
    if n < MIN_IT_TRADES:
        return -2.0 + n / MIN_IT_TRADES
    fit = m["sharpe"] * min(1.0, n / (3 * MIN_IT_TRADES)) ** 0.5
    fit -= 0.015 * complexity
    if m.get("exposure", 0) > 0.7:
        fit -= 2.0 * (m["exposure"] - 0.7)
    if pos_year_share is not None:
        fit += 0.3 * (pos_year_share - 0.5)
    return float(fit)


def screen(ctx: Context, spec: dict, origin: str, parents=(), operator: str = "") -> dict:
    """Stage 1 on the inner-training window. Returns the experiment record (also kept on the context)."""
    rec = {"origin": origin, "parents": list(parents), "operator": operator, "stage": "screen"}
    if spec is None:
        rec.update(passed=False, reason="generation_failed", hash=None)
        ctx.records.append(rec)
        return rec
    errors = S.validate(spec, ctx.store.names, ctx.store.tf, ctx.max_complexity)
    try:
        h = S.spec_hash(spec)
    except Exception:
        h = None
    rec["hash"] = h
    if errors:
        rec.update(passed=False, reason="invalid: " + errors[0][:160])
        ctx.records.append(rec)
        return rec
    if h in ctx.by_hash:
        prior = ctx.by_hash[h]
        rec.update(passed=False, reason="duplicate", fitness=prior.get("fitness"), duplicate_of=h)
        ctx.records.append(rec)
        return prior
    norm = S.normalize(spec)
    norm["meta"] = {"origin": origin, "parents": list(parents), "operator": operator}
    rec["spec"] = norm
    rec["complexity"] = S.complexity(norm)
    try:
        m, tr = evaluate(norm, ctx.ev_it, "observed", detail=False)
    except Exception as exc:  # a spec that cannot be evaluated is rejected and recorded, never fatal
        rec.update(passed=False, reason=f"evaluation_error: {type(exc).__name__}: {str(exc)[:120]}")
        ctx.records.append(rec)
        ctx.by_hash[h] = rec
        return rec
    sig = entry_signature(tr, ctx.ev_it)
    rec["sig"] = sig
    rec["it"] = {k: m.get(k) for k in ("trades", "exp_r", "sharpe", "total_r", "win_rate", "max_dd", "exposure", "top_trade_share")}
    years = ctx.year_of(tr) if len(tr) else None
    pos_share = None
    if years is not None and len(tr):
        tab = pd.Series(tr.r).groupby(years).agg(["count", "sum"])
        tab = tab[tab["count"] >= 3]
        pos_share = float((tab["sum"] > 0).mean()) if len(tab) else None
    rec["it"]["positive_year_share"] = pos_share
    rec["fitness"] = fitness(m, rec["complexity"], pos_share)
    if sig in ctx.sigs and len(tr):
        rec.update(passed=False, reason="equivalent", equivalent_to=ctx.sigs[sig])
        ctx.records.append(rec)
        ctx.by_hash[h] = rec
        return rec
    ctx.sigs[sig] = h
    if m["trades"] < MIN_IT_TRADES:
        reason = "too_few_trades"
    elif m.get("exposure", 0) > 0.9:
        reason = "always_in_market"
    elif m["exp_r"] <= 0:
        reason = "negative_expectancy_after_costs"
    elif m.get("top_trade_share", 1) > 0.5:
        reason = "outlier_dependent"
    else:
        reason = "passed"
    rec.update(passed=reason == "passed", reason=reason)
    ctx.records.append(rec)
    ctx.by_hash[h] = rec
    return rec


def validate_inner(ctx: Context, rec: dict) -> dict:
    """Selection inside training: the inner-validation window, still before the vintage cutoff."""
    m, tr = evaluate(rec["spec"], ctx.ev_iv, "observed", detail=False)
    iv = {k: m.get(k) for k in ("trades", "exp_r", "sharpe", "total_r", "win_rate", "max_dd", "exposure")}
    ok = m["trades"] >= MIN_IV_TRADES and m["exp_r"] > 0 and m["sharpe"] > 0
    out = {"hash": rec["hash"], "stage": "inner_validation", "passed": bool(ok), "iv": iv,
           "reason": "passed" if ok else ("too_few_trades" if m["trades"] < MIN_IV_TRADES else "failed_inner_validation"),
           "entries": (tr.entry + tr.lo).tolist() if ok else None,
           "select_score": float(min(rec["it"]["sharpe"], m["sharpe"]) - 0.02 * rec["complexity"]) if ok else None}
    ctx.records.append({k: v for k, v in out.items() if k != "entries"})
    return out


def pareto(recs: list[dict]) -> list[dict]:
    pts = sorted(recs, key=lambda r: (-r["fitness"], r["complexity"], r["hash"]))
    front, best_c = [], 10 ** 9
    for r in pts:
        if r["complexity"] < best_c:
            front.append(r)
            best_c = r["complexity"]
    return front


def select(ctx: Context, pool: list[dict], k: int) -> list[str]:
    """Inner-validate the strongest training candidates and keep up to k diverse ones."""
    passed = [r for r in pool if r.get("passed")]
    if not passed:
        return []
    top = sorted(passed, key=lambda r: (-r["fitness"], r["hash"]))[:15]
    shortlist = {r["hash"]: r for r in top + pareto(passed)}
    results = [validate_inner(ctx, shortlist[h]) for h in sorted(shortlist)]
    good = sorted([x for x in results if x["passed"]], key=lambda x: (-x["select_score"], x["hash"]))
    chosen, entry_sets = [], []
    for x in good:
        e = set(x["entries"])
        if any(len(e & o) / max(1, len(e | o)) > 0.5 for o in entry_sets):
            continue
        chosen.append(x["hash"])
        entry_sets.append(e)
        if len(chosen) >= k:
            break
    return chosen


def _tournament(ctx: Context, pop: list[dict], size: int = 3) -> dict:
    idx = ctx.rng.choice(len(pop), size=min(size, len(pop)), replace=False)
    return max((pop[int(i)] for i in idx), key=lambda r: (r.get("fitness", -9), r["hash"]))


# ---------------------------------------------------------------- task kinds
def task_gp(ctx: Context, grammar: G.Grammar) -> dict:
    p = ctx.task["params"]
    size, gens = int(p.get("population", 48)), int(p.get("generations", 10))
    pop: list[dict] = []
    for seed_spec in p.get("seeds", []):
        r = screen(ctx, seed_spec, "seed", seed_spec.get("meta", {}).get("parents", []) if isinstance(seed_spec, dict) else [], "archive")
        if r.get("spec") is not None and r.get("fitness") is not None:
            pop.append(r)
    tries = 0
    while len(pop) < size and tries < size * 6:
        tries += 1
        r = screen(ctx, grammar.random_spec(), "gp_random")
        if r.get("spec") is not None and r.get("fitness") is not None and r["reason"] not in ("equivalent",):
            pop.append(r)
    if not pop:
        return {"archive": [], "selected": []}
    for g in range(gens):
        elites = sorted(pop, key=lambda r: (-r["fitness"], r["hash"]))[:4]
        children = []
        attempts = 0
        while len(children) < size and attempts < size * 4:
            attempts += 1
            u = ctx.rng.random()
            if u < 0.08:
                spec, op, parents = grammar.random_spec(), "immigrant", []
            elif u < 0.38 and len(pop) > 1:
                a, b = _tournament(ctx, pop), _tournament(ctx, pop)
                spec, op = grammar.crossover(a["spec"], b["spec"])
                parents = sorted({a["hash"], b["hash"]})
            else:
                a = _tournament(ctx, pop)
                spec, op = grammar.mutate(a["spec"])
                parents = [a["hash"]]
            r = screen(ctx, spec, f"gp_gen{g + 1}", parents, op)
            if r.get("spec") is not None and r.get("fitness") is not None and r.get("reason") != "equivalent":
                children.append(r)
        merged = {r["hash"]: r for r in elites + children + pop}
        pop = sorted(merged.values(), key=lambda r: (-r["fitness"], r["hash"]))[:size]
    archive = [r["spec"] for r in sorted(pop, key=lambda r: (-r["fitness"], r["hash"]))[:8]]
    pool = [r for r in ctx.by_hash.values() if r.get("spec") is not None and r.get("fitness") is not None]
    return {"archive": archive, "selected": select(ctx, pool, int(p.get("selected_per_task", 3)))}


def task_random(ctx: Context, grammar: G.Grammar) -> dict:
    """Unguided control: random grammar samples. Shows what search adds over chance."""
    n = int(ctx.task["params"].get("samples", 150))
    for _ in range(n):
        screen(ctx, grammar.random_spec(), "random")
    pool = [r for r in ctx.by_hash.values() if r.get("spec") is not None and r.get("fitness") is not None]
    return {"archive": [], "selected": select(ctx, pool, int(ctx.task["params"].get("selected_per_task", 3)))}


def task_template(ctx: Context, grammar: G.Grammar) -> dict:
    """Bayesian optimisation (tree-structured Parzen estimator over discrete choices) of a behaviour template."""
    p = ctx.task["params"]
    name = p["template"]
    t = G.TEMPLATES[name]
    space = t["space"]
    keys = sorted(space)
    budget, warm = int(p.get("budget", 36)), int(p.get("warmup", 12))
    history: list[tuple[dict, float]] = []

    def draw_random():
        return {k: space[k][int(ctx.rng.integers(len(space[k])))] for k in keys}

    def draw_tpe():
        ranked = sorted(history, key=lambda x: -x[1])
        cut = max(2, len(ranked) // 4)
        good, bad = ranked[:cut], ranked[cut:]
        best, best_score = None, -np.inf
        for _ in range(48):
            cand, score = {}, 0.0
            for k in keys:
                opts = space[k]
                gc = np.array([sum(1 for h, _ in good if h[k] == o) for o in opts], float) + 1.0
                bc = np.array([sum(1 for h, _ in bad if h[k] == o) for o in opts], float) + 1.0
                pg = gc / gc.sum()
                j = int(ctx.rng.choice(len(opts), p=pg))
                cand[k] = opts[j]
                score += np.log(pg[j]) - np.log(bc[j] / bc.sum())
            if score > best_score:
                best, best_score = cand, score
        return best

    for i in range(budget):
        params = draw_random() if i < warm else draw_tpe()
        try:
            spec = G.build_template(name, params, ctx.store.tf)
        except KeyError:
            spec = None
        r = screen(ctx, spec, f"template:{name}", [], "bayesian_optimisation")
        score = r.get("fitness")
        # Constraint handling: invalid or untradeable configurations score as the worst case.
        history.append((params, score if score is not None else -3.0))
        if spec is not None and r.get("spec") is not None:
            r["spec"]["meta"]["template"] = {"name": name, "params": params, "family": t["family"]}
    pool = [r for r in ctx.by_hash.values() if r.get("spec") is not None and r.get("fitness") is not None]
    best = sorted(pool, key=lambda r: (-r["fitness"], r["hash"]))[:6]
    return {"archive": [r["spec"] for r in best], "selected": select(ctx, pool, int(p.get("selected_per_task", 3)))}


def _forward(close: np.ndarray, h: int) -> np.ndarray:
    out = np.full(len(close), np.nan)
    with np.errstate(all="ignore"):
        out[:-h] = np.log(close[h:] / close[:-h])
    return out


def _mutual_information(x: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 500:
        return 0.0
    qx = np.quantile(x, np.linspace(0, 1, bins + 1)[1:-1])
    bx = np.searchsorted(qx, x)
    by = (y > 0).astype(int)
    joint = np.zeros((bins, 2))
    np.add.at(joint, (bx, by), 1)
    joint /= joint.sum()
    px, py = joint.sum(1, keepdims=True), joint.sum(0, keepdims=True)
    nz = joint > 0
    return float((joint[nz] * np.log(joint[nz] / (px @ py)[nz])).sum())


def task_miner(ctx: Context, grammar: G.Grammar) -> dict:
    """Conditional-probability and interaction discovery on inner-training forward returns.

    The label (forward return) uses only bars inside the inner-training window. Cells are tested against the
    unconditional mean with an overlap-adjusted t, corrected by Benjamini-Hochberg across every cell examined.
    Mutual information ranks features for the pairwise search. Strong cells become rules for the trading screen.
    """
    from statsmodels.stats.multitest import multipletests
    from scipy import stats as sps
    p = ctx.task["params"]
    h = int(p.get("horizon", G.HOLDS[ctx.store.tf][2]))
    ev = ctx.ev_it
    win = ev.window
    close = ev.price("close")
    fwd = _forward(close, h)
    fwd[ev.end - ev.lo - h:] = np.nan  # the label may not reach past the inner-training end
    fwd = fwd[win]
    base = np.nanmean(fwd)
    names = [n for n in grammar.numeric][:]
    cells = []
    mi = {}
    for name in names:
        x = ev.feature(name)[win]
        mi[name] = _mutual_information(x, fwd - base)
        q = grammar.info[name].quantiles
        edges = np.unique(q[[2, 3, 5, 7, 9, 10]])  # 0.1 .. 0.9 deciles-ish
        b = np.searchsorted(edges, x)
        for k in range(len(edges) + 1):
            m = (b == k) & np.isfinite(x) & np.isfinite(fwd)
            n = int(m.sum())
            if n < 30 * h:
                continue
            e = fwd[m] - base
            t = e.mean() / (e.std(ddof=1) / np.sqrt(n / h)) if e.std() > 0 else 0.0
            lo = float(edges[k - 1]) if k > 0 else None
            hi = float(edges[k]) if k < len(edges) else None
            cells.append({"features": [name], "bounds": [[lo, hi]], "n": n, "excess": float(e.mean()), "t": float(t)})
    top = sorted(mi, key=lambda k: (-mi[k], k))[: int(p.get("pair_features", 6))]
    for i in range(len(top)):
        for j in range(i + 1, len(top)):
            a, b_ = top[i], top[j]
            xa, xb = ev.feature(a)[win], ev.feature(b_)[win]
            qa, qb = grammar.info[a].quantiles, grammar.info[b_].quantiles
            ea, eb = np.array([qa[4], qa[8]]), np.array([qb[4], qb[8]])  # terciles-ish: 0.3 / 0.7
            ba, bb = np.searchsorted(ea, xa), np.searchsorted(eb, xb)
            for ka in range(3):
                for kb in range(3):
                    m = (ba == ka) & (bb == kb) & np.isfinite(xa) & np.isfinite(xb) & np.isfinite(fwd)
                    n = int(m.sum())
                    if n < 30 * h:
                        continue
                    e = fwd[m] - base
                    t = e.mean() / (e.std(ddof=1) / np.sqrt(n / h)) if e.std() > 0 else 0.0
                    bounds = [[float(ea[ka - 1]) if ka else None, float(ea[ka]) if ka < 2 else None],
                              [float(eb[kb - 1]) if kb else None, float(eb[kb]) if kb < 2 else None]]
                    cells.append({"features": [a, b_], "bounds": bounds, "n": n, "excess": float(e.mean()), "t": float(t)})
    if cells:
        pv = [float(2 * sps.norm.sf(abs(c["t"]))) for c in cells]
        rej, adj, _, _ = multipletests(pv, alpha=0.05, method="fdr_bh")
        for c, p_, a_, r_ in zip(cells, pv, adj, rej):
            c.update(p=p_, p_bh=float(a_), significant=bool(r_))
    ranked = sorted(cells, key=lambda c: (-abs(c["t"]), str(c["features"])))
    findings = {"horizon": h, "cells_tested": len(cells), "significant_bh": int(sum(c.get("significant", False) for c in cells)),
                "unconditional_mean": float(base), "top_cells": ranked[:15],
                "mutual_information_top": [[k, mi[k]] for k in sorted(mi, key=lambda k: (-mi[k], k))[:15]]}
    for c in ranked[: int(p.get("rules", 10))]:
        conds = []
        for f, (lo, hi) in zip(c["features"], c["bounds"]):
            if lo is not None and hi is not None:
                conds.append({"op": "between", "a": {"f": f}, "lo": lo, "hi": hi})
            elif lo is not None:
                conds.append({"op": "gt", "a": {"f": f}, "b": {"c": lo}})
            elif hi is not None:
                conds.append({"op": "lt", "a": {"f": f}, "b": {"c": hi}})
        side = "long" if c["excess"] > 0 else "short"
        for stop in (1.5, 3.0):
            for target in (None, 2.0):
                spec = {"timeframe": ctx.store.tf, "direction": side, side: G.join(conds), "stop": {"type": "atr", "mult": stop},
                        "target": {"type": "none"} if target is None else {"type": "r", "r": target},
                        "exit": {"max_hold": h, "flip": False}}
                screen(ctx, spec, "miner", [], f"conditional_cell t={c['t']:.2f}")
    pool = [r for r in ctx.by_hash.values() if r.get("spec") is not None and r.get("fitness") is not None]
    return {"archive": [], "findings": findings, "selected": select(ctx, pool, int(p.get("selected_per_task", 3)))}


def _expr_random(grammar: G.Grammar, depth: int = 0) -> dict:
    rng = grammar.rng
    if depth >= 2 or rng.random() < 0.4:
        return grammar.numeric_leaf()
    op = grammar.choice(["sub", "add", "mul", "div", "zs", "delta", "rank", "abs"])
    if op in ("zs", "delta", "rank"):
        n = int(grammar.choice([3, 6, 12, 24] if op == "delta" else [20, 50, 100]))
        return {"op": op, "a": _expr_random(grammar, depth + 1), "n": n}
    if op == "abs":
        return {"op": "abs", "a": _expr_random(grammar, depth + 1)}
    return {"op": op, "a": _expr_random(grammar, depth + 1), "b": _expr_random(grammar, depth + 1)}


def _expr_size(node) -> int:
    return sum(1 for _ in S.nodes(node))


def _mutate_expr(grammar: G.Grammar, node: dict) -> dict:
    import copy
    node = copy.deepcopy(node)
    if grammar.rng.random() < 0.3 or "f" in node:
        return _expr_random(grammar, 1)
    for key in ("a", "b"):
        if key in node and grammar.rng.random() < 0.5:
            node[key] = _mutate_expr(grammar, node[key])
            return node
    if "n" in node:
        node["n"] = int(np.clip(node["n"] * grammar.choice([0.5, 2.0]), 2, 100))
    return node


def task_symreg(ctx: Context, grammar: G.Grammar) -> dict:
    """Symbolic regression: evolve numeric expressions whose rank correlates with forward returns in training."""
    from scipy.stats import spearmanr
    p = ctx.task["params"]
    h = int(p.get("horizon", G.HOLDS[ctx.store.tf][2]))
    ev = ctx.ev_it
    win = ev.window
    fwd = _forward(ev.price("close"), h)
    fwd[ev.end - ev.lo - h:] = np.nan
    fwd = fwd[win]
    sub = np.arange(0, len(fwd), max(1, h))  # non-overlapping labels

    def score(e):
        x = ev.num(e)[win][sub]
        y = fwd[sub]
        ok = np.isfinite(x) & np.isfinite(y)
        if ok.sum() < 300 or np.nanstd(x[ok]) == 0:
            return 0.0, 0.0
        rho = spearmanr(x[ok], y[ok]).statistic
        rho = 0.0 if not np.isfinite(rho) else float(rho)
        return abs(rho) - 0.002 * _expr_size(e), rho

    pop = []
    for _ in range(int(p.get("population", 40))):
        e = _expr_random(grammar)
        s, rho = score(e)
        pop.append((s, rho, e))
    for _ in range(int(p.get("generations", 8))):
        pop.sort(key=lambda x: (-x[0], S.canonical(x[2])))
        parents = pop[:12]
        kids = []
        for _ in range(len(pop) - len(parents)):
            base = parents[int(ctx.rng.integers(len(parents)))][2]
            e = _mutate_expr(grammar, base)
            if _expr_size(e) > 9:
                e = _expr_random(grammar)
            s, rho = score(e)
            kids.append((s, rho, e))
        pop = parents + kids
    pop.sort(key=lambda x: (-x[0], S.canonical(x[2])))
    found = []
    for s, rho, e in pop[: int(p.get("rules", 4))]:
        found.append({"expression": e, "rank_ic": rho, "score": s})
        hi_level, lo_level = (0.85, 0.15) if rho > 0 else (0.15, 0.85)
        c_hi = grammar.quantile_of(e, hi_level)
        c_lo = grammar.quantile_of(e, lo_level)
        if c_hi is None or c_lo is None:
            continue
        long_ = {"op": "gt" if rho > 0 else "lt", "a": e, "b": {"c": c_hi}}
        short = {"op": "lt" if rho > 0 else "gt", "a": e, "b": {"c": c_lo}}
        for direction in ("long", "short", "both"):
            spec = {"timeframe": ctx.store.tf, "direction": direction, "stop": {"type": "atr", "mult": 2.0}, "target": {"type": "none"},
                    "exit": {"max_hold": h, "flip": direction == "both"}}
            if direction in ("long", "both"):
                spec["long"] = long_
            if direction in ("short", "both"):
                spec["short"] = short
            screen(ctx, spec, "symbolic_regression", [], f"rank_ic={rho:.3f}")
    pool = [r for r in ctx.by_hash.values() if r.get("spec") is not None and r.get("fitness") is not None]
    return {"archive": [], "findings": {"horizon": h, "expressions": found},
            "selected": select(ctx, pool, int(p.get("selected_per_task", 3)))}


def task_proposal(ctx: Context, grammar: G.Grammar) -> dict:
    """Externally proposed specs (for example, from a person or a language model): the same screen and selection."""
    for spec in ctx.task["params"].get("specs", []):
        screen(ctx, spec, "proposal")
    pool = [r for r in ctx.by_hash.values() if r.get("spec") is not None and r.get("fitness") is not None]
    return {"archive": [], "selected": select(ctx, pool, int(ctx.task["params"].get("selected_per_task", 3)))}


KINDS = {"gp": task_gp, "random": task_random, "template": task_template, "miner": task_miner, "symreg": task_symreg,
         "proposal": task_proposal}


def run_search(task: dict) -> dict:
    t0, c0 = time.perf_counter(), time.process_time()
    ctx = make_context(task)
    features = task["params"].get("features")
    grammar = G.Grammar(ctx.ev_it, ctx.rng, features)
    out = KINDS[task["kind"]](ctx, grammar)
    selected = set(out.get("selected", []))
    for r in ctx.records:
        if r.get("hash") in selected and r.get("stage") == "inner_validation":
            r["selected"] = True
    specs = {r["hash"]: r["spec"] for r in ctx.by_hash.values() if r.get("spec") is not None}
    return {"task_id": task["task_id"], "records": ctx.records, "selected": sorted(selected),
            "selected_specs": {h: specs[h] for h in sorted(selected)}, "archive": out.get("archive", []),
            "findings": out.get("findings"), "signatures": sorted({r["sig"] for r in ctx.by_hash.values() if r.get("sig")}),
            "runtime_s": time.perf_counter() - t0, "cpu_s": time.process_time() - c0, "pid": os.getpid()}
