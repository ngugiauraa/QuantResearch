"""Catalogue strategies meet the same bar as the engines: causal signals, fixed parameters, a complete record."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantresearch.features.registry import build_features
from quantresearch.strategies import REGISTRY
from quantresearch.strategies.base import SIGNAL_COLUMNS, SignalStrategy, attach_signals
from quantresearch.strategies.catalogue import CATALOGUE, ENTRIES
from quantresearch.strategies.registry import run as run_engine
from quantresearch.timemachine import lookahead
from quantresearch.warehouse.store import synthetic_bars

MANIFEST = Path(__file__).resolve().parents[1] / "docs" / "strategy_catalogue.json"
CROSS = ("xagusd", "eurusd", "gbpusd", "usdjpy", "dollaridxusd", "usa500idxusd", "lightcmdusd")
MACRO = ("DFII10", "DTWEXBGS", "T10YIE", "DFF", "DGS10", "DGS2", "VIXCLS", "GVZCLS", "DCOILWTICO", "COT_MM_NET", "CPI_SURPRISE")
SIZES = {"m5": 9000, "m15": 7000, "h1": 5000, "h4": 3000, "d1": 1500, "mn1": 300}


def market(tf: str, n: int | None = None, seed: int = 7) -> pd.DataFrame:
    n = n or SIZES[tf]
    if tf == "mn1":  # Synthetic bars have fixed steps only; re-stamp daily ones at month starts.
        bars = synthetic_bars(n, "d1", start="1995-01-01", seed=seed, vol=0.04)
        bars["ts"] = pd.date_range("1995-01-01", periods=n, freq="MS", tz="UTC")
        if "close_ts" in bars:
            bars["close_ts"] = bars["ts"] + pd.offsets.MonthBegin(1)
    else:
        bars = synthetic_bars(n, tf, start="2019-01-01", seed=seed, vol=0.002 if tf in ("m5", "m15", "h1") else 0.01)
    frame = build_features(bars)
    rng = np.random.default_rng(seed)
    for name in CROSS:
        frame[f"{name}_close"] = np.exp(0.8 * np.log(frame["close"]) + rng.normal(0, 0.01, n).cumsum() * 0.05 + 1)
    for series in MACRO:
        # Step series that change every ~20 bars, like published data seen from intraday bars.
        steps = rng.normal(0, 0.2, n // 20 + 1).cumsum() + 3
        frame[f"m_{series}"] = np.repeat(steps, 20)[:n]
    frame["m_VIXCLS"] = 20 + 15 * np.abs(np.sin(np.arange(n) / 37))
    frame["cot_mm_z"] = np.sin(np.arange(n) / 50) * 3
    frame["vix"] = frame["m_VIXCLS"]
    frame["vix_chg5"] = frame["vix"].diff(5)
    hours = (np.arange(n) * {"m5": 1 / 12, "m15": 0.25, "h1": 1, "h4": 4, "d1": 24, "mn1": 720}[tf]) % (24 * 42)
    frame["hours_since_fomc"] = hours
    frame["hours_to_fomc"] = 24 * 42 - hours
    frame["hours_since_cpi"] = (hours * 0.7) % (24 * 30)
    return frame


FRAMES: dict[str, pd.DataFrame] = {}


def frame_for(tf: str) -> pd.DataFrame:
    if tf not in FRAMES:
        FRAMES[tf] = market(tf)
    return FRAMES[tf]


NAMES = [e.name for e in ENTRIES]


def test_catalogue_names_are_unique_prefixed_and_apart_from_the_engines():
    assert len(NAMES) == len(set(NAMES)) and all(n.startswith("cat_") for n in NAMES)
    assert not set(NAMES) & set(REGISTRY) and len(REGISTRY) == 8


def test_no_strategy_ever_leaves_the_catalogue():
    recorded = json.loads(MANIFEST.read_text(encoding="utf-8"))["strategies"]
    missing = [s["name"] for s in recorded if s["name"] not in CATALOGUE]
    assert not missing, f"Removed from the catalogue (failing strategies must stay on record): {missing}"


def test_the_manifest_matches_the_catalogue():
    recorded = {s["name"]: s for s in json.loads(MANIFEST.read_text(encoding="utf-8"))["strategies"]}
    assert set(recorded) == set(CATALOGUE), "Regenerate docs with python -m quantresearch.strategies.catalogue.docs"
    for name, item in CATALOGUE.items():
        assert recorded[name]["params"] == json.loads(json.dumps(item.defn.defaults)), f"{name}: parameters differ from the documented ones"


@pytest.mark.parametrize("name", NAMES)
def test_parameters_are_fixed_and_documented(name):
    item = CATALOGUE[name]
    d = item.defn
    assert d.grid == {} and d.fit is None, "Catalogue strategies are not tuned"
    assert d.defaults and d.title and d.methodology and d.limitations
    for field in ("source", "logic", "entry", "exit", "stop", "family"):
        assert getattr(item, field), (name, field)
    assert item.data and item.batch in (1, 2, 3, 4, 5)


@pytest.mark.parametrize("name", NAMES)
def test_signals_do_not_read_the_future(name):
    d = CATALOGUE[name].defn
    frame = frame_for(d.signal_tf)
    full = d.prepare(frame, d.defaults)
    assert set(SIGNAL_COLUMNS) <= set(full.columns) and len(full) == len(frame)
    n = len(frame)
    for cut in (int(n * 0.55), int(n * 0.8)):
        part = d.prepare(frame.iloc[:cut].reset_index(drop=True), d.defaults)
        for col in SIGNAL_COLUMNS:
            a, b = full[col].iloc[:cut].to_numpy(dtype="float64"), part[col].to_numpy(dtype="float64")
            assert np.allclose(a, b, equal_nan=True, rtol=1e-9, atol=1e-9), (name, col, cut)


@pytest.mark.parametrize("name", NAMES)
def test_full_look_ahead_integrity_through_the_engine(name):
    d = CATALOGUE[name].defn
    frame = frame_for(d.signal_tf)

    def featurize(bars):
        out = frame.iloc[:len(bars)].reset_index(drop=True)
        return attach_signals(out, d.prepare(out, d.defaults))

    prepared = featurize(frame)
    result = lookahead.check(frame, frame, lambda: SignalStrategy(prepared, d.name), frame["close_ts"].iloc[int(len(frame) * 0.7)], featurize=featurize)
    assert result.passed, result.as_dict()


@pytest.mark.parametrize("name", NAMES)
def test_runs_through_the_shared_engine(name):
    d = CATALOGUE[name].defn
    engine = run_engine(d, frame_for(d.signal_tf), d.defaults)
    assert engine.account.equity > 0
