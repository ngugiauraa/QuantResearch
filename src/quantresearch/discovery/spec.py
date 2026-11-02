"""The candidate strategy format: a validated, declarative JSON document. Nothing in it is ever executed as code.

A strategy is data. Conditions are expression trees built only from the operators listed here, over leaves that
are feature names from the store's catalogue or constants. Every operator looks backwards only (lags and windows
are positive), so a valid tree cannot read the future by construction; the feature catalogue is separately
proven causal by truncation tests. Generated candidates, template candidates and externally proposed candidates
(including any from a language model) all pass through `validate` and the same evaluation pipeline.

Example:
{
  "schema": "qr.discovery.strategy/1", "instrument": "XAUUSD", "timeframe": "h1", "direction": "long",
  "long": {"op": "and", "args": [{"op": "lt", "a": {"f": "rsi14"}, "b": {"c": 30}},
                                  {"op": "gt", "a": {"f": "h4_trend"}, "b": {"c": 0}}]},
  "short": null, "regime": null, "no_trade": null, "sessions": ["london", "new_york"],
  "entry": {"type": "market"}, "stop": {"type": "atr", "mult": 2.0}, "target": {"type": "r", "r": 2.0},
  "exit": {"max_hold": 24, "long_exit": null, "short_exit": null, "trail_atr": null, "breakeven_r": null,
           "day_end": false, "flip": true},
  "guards": {"max_spread_z": 3.0, "fomc_hours": 0.0}, "sizing": {"risk_pct": 0.5, "max_lots": 50.0}
}
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import zlib
from typing import Any, Iterable

SCHEMA = "qr.discovery.strategy/1"
COMPARE = {"gt", "lt"}
BOOL_BINARY = {"and", "or"}
BOOL_OPS = COMPARE | BOOL_BINARY | {"not", "between", "cross_above", "cross_below", "persist", "recent", "in", "is"}
NUM_BINARY = {"add", "sub", "mul", "div"}
NUM_UNARY = {"abs", "neg"}
NUM_WINDOW = {"lag", "delta", "rmean", "rstd", "rmax", "rmin", "rank", "zs"}
NUM_OPS = NUM_BINARY | NUM_UNARY | NUM_WINDOW
CALENDAR_SETS = {"hour_utc", "ny_hour", "dow", "month", "tdom", "tdoe"}
SESSIONS = ("asia", "london", "new_york", "overlap")
MAX_WINDOW = 500
MAX_LAG = 100
MAX_DEPTH = 7
MAX_NODES = 60
WARMUP_BARS = 2 * MAX_WINDOW + MAX_LAG

DEFAULTS = {
    "schema": SCHEMA, "instrument": "XAUUSD", "timeframe": "h1", "direction": "long",
    "long": None, "short": None, "regime": None, "no_trade": None, "sessions": None,
    "entry": {"type": "market"}, "stop": {"type": "atr", "mult": 2.0}, "target": {"type": "none"},
    "exit": {"max_hold": 24, "long_exit": None, "short_exit": None, "trail_atr": None, "breakeven_r": None,
             "day_end": False, "flip": True},
    "guards": {"max_spread_z": 3.0, "fomc_hours": 0.0},
    "sizing": {"risk_pct": 0.5, "max_lots": 50.0},
}
RANGES = {
    ("entry", "offset_atr"): (0.05, 2.0), ("entry", "expiry"): (1, 24),
    ("stop", "mult"): (0.5, 6.0), ("stop", "lookback"): (2, 100), ("stop", "buffer_atr"): (0.0, 2.0),
    ("target", "r"): (0.5, 8.0), ("target", "mult"): (0.5, 12.0),
    ("exit", "max_hold"): (1, 2000), ("exit", "trail_atr"): (0.5, 8.0), ("exit", "breakeven_r"): (0.3, 4.0),
    ("guards", "max_spread_z"): (1.0, 10.0), ("guards", "fomc_hours"): (0.0, 48.0),
    ("sizing", "risk_pct"): (0.01, 2.0), ("sizing", "max_lots"): (0.01, 50.0),
}


class InvalidSpec(ValueError):
    pass


# ---------------------------------------------------------------- helpers
def normalize(spec: dict) -> dict:
    """Fill defaults, round constants and order commutative arguments, so equivalent specs hash the same."""
    out = copy.deepcopy(DEFAULTS)
    for key, value in spec.items():
        if key == "meta":
            continue
        if isinstance(value, dict) and isinstance(out.get(key), dict) and key not in ("long", "short", "regime", "no_trade"):
            out[key] = {**out[key], **value}
        else:
            out[key] = copy.deepcopy(value)
    for key in ("long", "short", "regime", "no_trade"):
        out[key] = _norm_node(out[key]) if out[key] is not None else None
    for key in ("long_exit", "short_exit"):
        out["exit"][key] = _norm_node(out["exit"][key]) if out["exit"].get(key) is not None else None
    keep = {("entry", "market"): ("type",), ("entry", "stop"): ("type", "offset_atr", "expiry"),
            ("entry", "limit"): ("type", "offset_atr", "expiry"), ("stop", "atr"): ("type", "mult"),
            ("stop", "swing"): ("type", "lookback", "buffer_atr"), ("target", "none"): ("type",),
            ("target", "r"): ("type", "r"), ("target", "atr"): ("type", "mult")}
    for block in ("entry", "stop", "target"):
        fields = keep.get((block, out[block].get("type")))
        if fields:  # parameters that do not apply to the chosen type are dropped, so they cannot change the hash
            out[block] = {k: out[block][k] for k in fields if k in out[block]}
    if isinstance(out["sessions"], list):
        out["sessions"] = sorted(set(out["sessions"]))
    if out["direction"] == "long":
        out["short"], out["exit"]["short_exit"] = None, None
    if out["direction"] == "short":
        out["long"], out["exit"]["long_exit"] = None, None
    for (block, name), _ in RANGES.items():
        v = out.get(block, {}).get(name)
        if isinstance(v, float):
            out[block][name] = _round(v)
    return out


def _round(x: float, digits: int = 5) -> float:
    if x == 0 or not math.isfinite(x):
        return x
    return round(x, -int(math.floor(math.log10(abs(x)))) + (digits - 1))


def _norm_node(node: Any) -> Any:
    if not isinstance(node, dict):
        return node
    if "c" in node:
        v = node["c"]
        return {"c": _round(float(v))} if isinstance(v, (int, float)) and not isinstance(v, bool) else node
    if "f" in node:
        return {"f": node["f"]}
    out = {k: (_norm_node(v) if isinstance(v, dict) else v) for k, v in node.items()}
    if out.get("op") in BOOL_BINARY:
        args = [_norm_node(a) for a in out["args"]]
        flat = []
        for a in args:  # (a and (b and c)) is (a and b and c)
            if isinstance(a, dict) and a.get("op") == out["op"]:
                flat.extend(a["args"])
            else:
                flat.append(a)
        uniq = {canonical(a): a for a in flat}
        out["args"] = [uniq[k] for k in sorted(uniq)]
    if out.get("op") in {"add", "mul"}:
        a, b = sorted([out["a"], out["b"]], key=canonical)
        out["a"], out["b"] = a, b
    if out.get("op") == "in":
        out["set"] = sorted({int(v) for v in out["set"]})
    if out.get("op") == "between":
        out["lo"], out["hi"] = _round(float(out["lo"])), _round(float(out["hi"]))
    return out


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def spec_hash(spec: dict) -> str:
    return hashlib.sha256(canonical(normalize(spec)).encode()).hexdigest()[:20]


def compress(spec: dict) -> bytes:
    return zlib.compress(canonical(spec).encode(), 6)


def decompress(blob: bytes) -> dict:
    return json.loads(zlib.decompress(blob).decode())


def nodes(node: Any) -> Iterable[dict]:
    if isinstance(node, dict):
        yield node
        for key in ("a", "b"):
            if key in node:
                yield from nodes(node[key])
        for child in node.get("args", []):
            yield from nodes(child)


def leaves(spec: dict) -> list[str]:
    """Feature names a spec reads, in a stable order."""
    names = []
    for tree in condition_trees(spec):
        for n in nodes(tree):
            if "f" in n:
                names.append(n["f"])
    return sorted(set(names))


def condition_trees(spec: dict) -> list:
    out = [spec.get(k) for k in ("long", "short", "regime", "no_trade")]
    ex = spec.get("exit") or {}
    out += [ex.get("long_exit"), ex.get("short_exit")]
    return [t for t in out if t is not None]


def complexity(spec: dict) -> int:
    """Node count of all conditions plus one for each optional exit or entry mechanism."""
    s = normalize(spec)
    count = sum(1 for t in condition_trees(s) for _ in nodes(t))
    count += s["entry"]["type"] != "market"
    count += s["stop"]["type"] != "atr"
    count += s["target"]["type"] != "none"
    count += s["exit"]["trail_atr"] is not None
    count += s["exit"]["breakeven_r"] is not None
    count += bool(s["exit"]["day_end"])
    count += s["sessions"] is not None
    return int(count)


def depth(node: Any) -> int:
    if not isinstance(node, dict) or "f" in node or "c" in node:
        return 1
    kids = [node[k] for k in ("a", "b") if k in node] + list(node.get("args", []))
    return 1 + max((depth(k) for k in kids), default=0)


# ---------------------------------------------------------------- validation
def _check_num(node: Any, catalog: set[str], errors: list[str], path: str) -> None:
    if not isinstance(node, dict):
        errors.append(f"{path}: numeric node must be an object")
        return
    if "f" in node:
        if node["f"] not in catalog:
            errors.append(f"{path}: unknown or forbidden feature {node['f']!r}")
        return
    if "c" in node:
        if not isinstance(node["c"], (int, float)) or isinstance(node["c"], bool) or not math.isfinite(float(node["c"])):
            errors.append(f"{path}: constant must be a finite number")
        return
    op = node.get("op")
    if op not in NUM_OPS:
        errors.append(f"{path}: operator {op!r} is not a numeric operator")
        return
    if op in NUM_WINDOW:
        n = node.get("n")
        limit = MAX_LAG if op in ("lag", "delta") else MAX_WINDOW
        low = 1 if op in ("lag", "delta") else 2
        if not isinstance(n, int) or isinstance(n, bool) or not low <= n <= limit:
            errors.append(f"{path}: window n must be an integer in [{low}, {limit}] (no negative lags exist)")
        _check_num(node.get("a"), catalog, errors, path + ".a")
    elif op in NUM_UNARY:
        _check_num(node.get("a"), catalog, errors, path + ".a")
    else:
        _check_num(node.get("a"), catalog, errors, path + ".a")
        _check_num(node.get("b"), catalog, errors, path + ".b")


def _check_bool(node: Any, catalog: set[str], errors: list[str], path: str) -> None:
    if not isinstance(node, dict) or node.get("op") not in BOOL_OPS:
        errors.append(f"{path}: expected a boolean operator, got {node!r:.80}")
        return
    op = node["op"]
    if op in COMPARE or op in ("cross_above", "cross_below"):
        _check_num(node.get("a"), catalog, errors, path + ".a")
        _check_num(node.get("b"), catalog, errors, path + ".b")
        if "c" in node.get("a", {}) and "c" in node.get("b", {}):
            errors.append(f"{path}: comparing two constants is meaningless")
    elif op == "between":
        _check_num(node.get("a"), catalog, errors, path + ".a")
        lo, hi = node.get("lo"), node.get("hi")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lo, hi)) or not lo < hi:
            errors.append(f"{path}: between needs numbers lo < hi")
    elif op in BOOL_BINARY:
        args = node.get("args")
        if not isinstance(args, list) or not 2 <= len(args) <= 6:
            errors.append(f"{path}: {op} takes 2 to 6 arguments")
            return
        for i, a in enumerate(args):
            _check_bool(a, catalog, errors, f"{path}.args[{i}]")
    elif op == "not":
        _check_bool(node.get("a"), catalog, errors, path + ".a")
    elif op in ("persist", "recent"):
        n = node.get("n")
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= MAX_LAG:
            errors.append(f"{path}: n must be in [1, {MAX_LAG}]")
        _check_bool(node.get("a"), catalog, errors, path + ".a")
    elif op == "in":
        a = node.get("a", {})
        if not isinstance(a, dict) or a.get("f") not in CALENDAR_SETS or a.get("f") not in catalog:
            errors.append(f"{path}: 'in' applies to a calendar feature ({sorted(CALENDAR_SETS)})")
        s = node.get("set")
        if not isinstance(s, list) or not s or not all(isinstance(v, int) and not isinstance(v, bool) for v in s):
            errors.append(f"{path}: 'in' needs a non-empty list of integers")
    elif op == "is":
        a = node.get("a", {})
        if not isinstance(a, dict) or a.get("f") not in catalog:
            errors.append(f"{path}: 'is' needs a known feature")


def validate(spec: dict, catalog: Iterable[str], timeframe: str | None = None, max_complexity: int | None = None) -> list[str]:
    """All problems with a spec. An empty list means valid. Never raises on bad input."""
    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["spec must be an object"]
    unknown = set(spec) - set(DEFAULTS) - {"meta"}
    if unknown:
        errors.append(f"unknown keys {sorted(unknown)}")
    try:
        s = normalize(spec)
    except Exception as exc:  # malformed input is a validation failure, not a crash
        return [f"cannot normalise: {type(exc).__name__}: {exc}"]
    catalog = set(catalog)
    from quantresearch.discovery.features import FORBIDDEN_LEAVES
    if s["schema"] != SCHEMA:
        errors.append("wrong schema")
    if s["instrument"] != "XAUUSD":
        errors.append("instrument must be XAUUSD")
    if timeframe is not None and s["timeframe"] != timeframe:
        errors.append(f"timeframe {s['timeframe']} does not match store {timeframe}")
    if s["direction"] not in ("long", "short", "both"):
        errors.append("direction must be long, short or both")
    need = {"long": ["long"], "short": ["short"], "both": ["long", "short"]}.get(s["direction"], [])
    for side in need:
        if s[side] is None:
            errors.append(f"direction {s['direction']} needs a {side} condition")
    for key in ("long", "short", "regime", "no_trade"):
        if s[key] is not None:
            _check_bool(s[key], catalog, errors, key)
            if depth(s[key]) > MAX_DEPTH:
                errors.append(f"{key}: deeper than {MAX_DEPTH}")
    for key in ("long_exit", "short_exit"):
        if s["exit"].get(key) is not None:
            _check_bool(s["exit"][key], catalog, errors, f"exit.{key}")
    for tree in condition_trees(s):
        for n in nodes(tree):
            if "f" in n and any(n["f"] == x or n["f"].startswith(x) for x in FORBIDDEN_LEAVES):
                errors.append(f"feature {n['f']} is forbidden (revised data)")
    if sum(1 for t in condition_trees(s) for _ in nodes(t)) > MAX_NODES:
        errors.append(f"more than {MAX_NODES} nodes")
    if s["sessions"] is not None and (not s["sessions"] or any(x not in SESSIONS for x in s["sessions"])):
        errors.append(f"sessions must be a non-empty subset of {SESSIONS}")
    if s["entry"].get("type") not in ("market", "stop", "limit"):
        errors.append("entry.type must be market, stop or limit")
    if s["entry"]["type"] != "market":
        for k in ("offset_atr", "expiry"):
            if k not in s["entry"]:
                errors.append(f"entry.{k} is required for {s['entry']['type']} entries")
    if s["stop"].get("type") not in ("atr", "swing"):
        errors.append("stop.type must be atr or swing: every candidate needs a stop")
    if s["stop"]["type"] == "atr" and "mult" not in s["stop"]:
        errors.append("stop.mult is required")
    if s["stop"]["type"] == "swing" and not {"lookback", "buffer_atr"} <= set(s["stop"]):
        errors.append("swing stops need lookback and buffer_atr")
    if s["target"].get("type") not in ("none", "r", "atr"):
        errors.append("target.type must be none, r or atr")
    if s["target"]["type"] == "r" and "r" not in s["target"]:
        errors.append("target.r is required")
    if s["target"]["type"] == "atr" and "mult" not in s["target"]:
        errors.append("target.mult is required")
    for (block, name), (lo, hi) in RANGES.items():
        v = s.get(block, {}).get(name)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            errors.append(f"{block}.{name}={v!r} outside [{lo}, {hi}]")
    for name in ("max_hold", "expiry", "lookback"):
        for block in ("exit", "entry", "stop"):
            v = s.get(block, {}).get(name)
            if v is not None and not (isinstance(v, int) and not isinstance(v, bool)):
                errors.append(f"{block}.{name} must be an integer")
    if not isinstance(s["exit"].get("flip"), bool) or not isinstance(s["exit"].get("day_end"), bool):
        errors.append("exit.flip and exit.day_end must be booleans")
    if max_complexity is not None and complexity(s) > max_complexity:
        errors.append(f"complexity {complexity(s)} exceeds {max_complexity}")
    return errors


def require_valid(spec: dict, catalog: Iterable[str], timeframe: str | None = None) -> dict:
    errors = validate(spec, catalog, timeframe)
    if errors:
        raise InvalidSpec("; ".join(errors[:5]))
    return normalize(spec)


def describe(spec: dict) -> str:
    """A readable one-line rendering of the logic, for reports."""
    s = normalize(spec)

    def r(n):
        if n is None:
            return "-"
        if "f" in n:
            return n["f"]
        if "c" in n:
            return f"{n['c']:g}"
        op = n["op"]
        if op in COMPARE:
            return f"{r(n['a'])} {'>' if op == 'gt' else '<'} {r(n['b'])}"
        if op in BOOL_BINARY:
            return "(" + f" {op.upper()} ".join(r(a) for a in n["args"]) + ")"
        if op == "not":
            return f"NOT {r(n['a'])}"
        if op == "between":
            return f"{n['lo']:g} < {r(n['a'])} < {n['hi']:g}"
        if op == "in":
            return f"{r(n['a'])} in {n['set']}"
        if op == "is":
            return f"{r(n['a'])}"
        if op in ("cross_above", "cross_below"):
            return f"{r(n['a'])} crosses {'above' if op == 'cross_above' else 'below'} {r(n['b'])}"
        if op in ("persist", "recent"):
            return f"{op}({r(n['a'])}, {n['n']})"
        if op in NUM_WINDOW:
            return f"{op}({r(n['a'])}, {n['n']})"
        if op in NUM_UNARY:
            return f"{op}({r(n['a'])})"
        sym = {"add": "+", "sub": "-", "mul": "*", "div": "/"}[op]
        return f"({r(n['a'])} {sym} {r(n['b'])})"

    parts = [f"{s['timeframe'].upper()} {s['direction']}"]
    if s["long"] is not None:
        parts.append(f"LONG when {r(s['long'])}")
    if s["short"] is not None:
        parts.append(f"SHORT when {r(s['short'])}")
    if s["regime"] is not None:
        parts.append(f"only if {r(s['regime'])}")
    if s["no_trade"] is not None:
        parts.append(f"never if {r(s['no_trade'])}")
    if s["sessions"]:
        parts.append(f"sessions {','.join(s['sessions'])}")
    e, st, tg, ex = s["entry"], s["stop"], s["target"], s["exit"]
    parts.append("entry " + (e["type"] if e["type"] == "market" else f"{e['type']} {e['offset_atr']:g} ATR, {e['expiry']} bars"))
    parts.append(f"stop {st['mult']:g} ATR" if st["type"] == "atr" else f"stop beyond {st['lookback']}-bar extreme + {st['buffer_atr']:g} ATR")
    parts.append("no target" if tg["type"] == "none" else (f"target {tg['r']:g}R" if tg["type"] == "r" else f"target {tg['mult']:g} ATR"))
    extra = [f"max hold {ex['max_hold']} bars"]
    if ex["trail_atr"] is not None:
        extra.append(f"trail {ex['trail_atr']:g} ATR")
    if ex["breakeven_r"] is not None:
        extra.append(f"breakeven at {ex['breakeven_r']:g}R")
    if ex["day_end"]:
        extra.append("exit at the first bar after the 17:00 NY roll")
    if ex["long_exit"] is not None:
        extra.append(f"exit long when {r(ex['long_exit'])}")
    if ex["short_exit"] is not None:
        extra.append(f"exit short when {r(ex['short_exit'])}")
    parts.append(", ".join(extra))
    return "; ".join(parts)
