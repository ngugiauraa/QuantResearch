"""Signals for the seven strategies, computed with the research's own code on bars closed by a given time.

The bars come from a loader: warehouse history plus the broker's own closed M1 bars for the live test, the
warehouse (also plus broker bars) for the replay. From there it is `research_frame` and the strategy's `prepare`,
the same path every catalogue backtest took, starting at a fixed date (start minus HISTORY_YEARS) so live and
replay see identical indicator histories.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from quantresearch.core.config import Paths
from quantresearch.forward import plan
from quantresearch.strategies.base import SIGNAL_COLUMNS, attach_signals

Loader = Callable[[str, pd.Timestamp, pd.Timestamp], pd.DataFrame]


def strategy_tf(name: str) -> str:
    from quantresearch.strategies.catalogue import CATALOGUE

    return CATALOGUE[name].defn.signal_tf


class SignalSource:
    def __init__(self, p: Paths, load_start: pd.Timestamp, loader: Loader):
        self.p, self.load_start, self.loader = p, pd.Timestamp(load_start), loader

    def frames(self, as_of: pd.Timestamp) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
        """Per strategy: closed signal bars (ts, close_ts, close, atr_14 and the signal columns). Plus H1 ATR(14)."""
        from quantresearch.research.catalogue import research_frame
        from quantresearch.strategies.catalogue import CATALOGUE

        as_of = pd.Timestamp(as_of)
        bars: dict[str, pd.DataFrame] = {}
        out: dict[str, pd.DataFrame] = {}
        h1_atr = None
        for name in plan.STRATEGIES:
            item = CATALOGUE[name]
            tf = item.defn.signal_tf
            if tf not in bars:
                bars[tf] = self.loader(tf, self.load_start, as_of)
            features = research_frame(item, self.p, bars=bars[tf])
            sig = attach_signals(features, item.defn.prepare(features, item.defn.params()))
            sig = sig[sig["close_ts"] <= as_of]
            out[name] = sig[["ts", "close_ts", "close", "atr_14", *SIGNAL_COLUMNS]].reset_index(drop=True)
            if tf == "h1" and h1_atr is None:
                h1_atr = out[name][["close_ts", "atr_14"]]
        if h1_atr is None:
            from quantresearch.features.registry import build_features

            f = build_features(self.loader("h1", self.load_start, as_of))
            h1_atr = f.loc[f["close_ts"] <= as_of, ["close_ts", "atr_14"]].reset_index(drop=True)
        return out, h1_atr


def atr_at(h1_atr: pd.DataFrame, when: pd.Timestamp) -> float | None:
    """The latest closed H1 ATR(14) at a time: the volatility reading the risk engine checks."""
    if h1_atr is None or h1_atr.empty:
        return None
    i = int(h1_atr["close_ts"].searchsorted(pd.Timestamp(when), side="right")) - 1
    if i < 0:
        return None
    v = float(h1_atr["atr_14"].iloc[i])
    return v if np.isfinite(v) else None


def held_bars(frame: pd.DataFrame, entry_signal_ts, now_ts) -> int:
    """Bars since the fill bar (the bar after the entry signal), as the simulator counts them."""
    times = frame["close_ts"]
    a = int(times.searchsorted(pd.Timestamp(entry_signal_ts), side="left"))
    b = int(times.searchsorted(pd.Timestamp(now_ts), side="left"))
    return b - a - 1
