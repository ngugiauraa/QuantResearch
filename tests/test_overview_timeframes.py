import pandas as pd
import pytest

from quantresearch.api.services import Services
from quantresearch.core.config import Paths
from quantresearch.features.registry import build_features
from quantresearch.strategies import REGISTRY
from quantresearch.warehouse.store import synthetic_bars

STEP = {"m5": "5min", "m15": "15min", "h1": "h"}


@pytest.fixture()
def svc(tmp_path):
    return Services(Paths(tmp_path))


def tagged(tf):
    frame = build_features(synthetic_bars(600, tf, start="2024-01-02", seed=3, vol=0.002))
    frame["bar_tf"] = tf  # Lets a strategy's prepare() show which bars it was handed.
    return frame


def test_each_engine_is_computed_on_its_own_timeframe(svc, monkeypatch):
    loaded, prepared = [], {}
    monkeypatch.setattr(svc, "feature_window", lambda tf, limit, end=None: (loaded.append(tf), tagged(tf))[1])
    for name, defn in REGISTRY.items():
        original = defn.prepare

        def spy(frame, params, _name=name, _original=original):
            prepared[_name] = frame["bar_tf"].iloc[0]
            return _original(frame, params)

        monkeypatch.setattr(defn, "prepare", spy)
    svc.recent_signals()
    assert prepared["behavioral"] == "m15" and prepared["london_open_momentum"] == "m5"
    assert prepared["trend"] == "h1" and prepared["mean_reversion"] == "h1"
    assert all(prepared[n] == REGISTRY[n].signal_tf for n in prepared)
    assert not {"stat_arb", "seasonal"} & set(prepared)  # Engines needing a fit or cross-market data stay out.
    assert sorted(set(loaded)) == ["h1", "m15", "m5"] and len(loaded) == 3  # Each timeframe is loaded once.


def test_a_timeframe_with_no_bars_is_skipped_and_the_others_still_report(svc, monkeypatch):
    def window(tf, limit, end=None):
        if tf == "m5":
            raise FileNotFoundError("No bars built for m5")
        return tagged(tf)

    monkeypatch.setattr(svc, "feature_window", window)
    seen = []
    for name, defn in REGISTRY.items():
        monkeypatch.setattr(defn, "prepare", lambda f, p, _n=name, _o=defn.prepare: (seen.append(_n), _o(f, p))[1])
    svc.recent_signals()
    assert "london_open_momentum" not in seen and "behavioral" in seen and "trend" in seen


def test_one_failing_engine_does_not_break_the_rest(svc, monkeypatch):
    monkeypatch.setattr(svc, "feature_window", lambda tf, limit, end=None: tagged(tf))

    def boom(frame, params):
        raise RuntimeError("engine bug")

    monkeypatch.setattr(REGISTRY["behavioral"], "prepare", boom)
    assert isinstance(svc.recent_signals(), list)  # No exception reaches the caller.


def test_reported_signals_name_their_timeframe(svc, monkeypatch):
    frame = tagged("m5")
    n = len(frame)
    fake = pd.DataFrame({"long_entry": [False] * (n - 1) + [True], "short_entry": False}, index=frame.index)
    monkeypatch.setattr(svc, "feature_window", lambda tf, limit, end=None: frame if tf == "m5" else (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(REGISTRY["london_open_momentum"], "prepare", lambda f, p: fake)
    out = svc.recent_signals()
    assert out == [{"strategy": "london_open_momentum", "timeframe": "m5", "time": frame["close_ts"].iloc[-1], "side": "long"}]
