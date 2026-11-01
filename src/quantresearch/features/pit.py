"""Point-in-time alignment.

Four separate times are kept for external data, because using the wrong one leaks the future:
  event_ts      the period or moment the value describes (March CPI)
  published_ts  when the world could first see it
  ingested_ts   when this system stored it
  revision      revision number; later revisions replace earlier ones only from their own
                published_ts onward
A query "as of T" sees only rows with published_ts <= T, and the latest revision among those.
"""
from __future__ import annotations

import pandas as pd

from quantresearch.core import timeframes as tfs

EVENT_COLUMNS = ["series", "event_ts", "published_ts", "ingested_ts", "revision", "value", "source"]


def _utc(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def as_of(events: pd.DataFrame, when, series: str | None = None) -> pd.DataFrame:
    """The view of an event table that was knowable at `when`."""
    when = _utc(when)
    visible = events[events["published_ts"] <= when]
    if series is not None:
        visible = visible[visible["series"] == series]
    if visible.empty:
        return visible.copy()
    ordered = visible.sort_values(["series", "event_ts", "published_ts", "revision"])
    return ordered.groupby(["series", "event_ts"], as_index=False, sort=True).tail(1).reset_index(drop=True)


def align_events(index: pd.Series, events: pd.DataFrame, series: str, column: str = "value") -> pd.Series:
    """For each time in `index`, the latest value of `series` that had been published by then.

    Values are picked by publication order, so a revision of an old period can appear when
    it was published. A newer event with an older revision is never confused for "latest":
    the newest event period visible at that moment wins.
    """
    rows = events[events["series"] == series].sort_values("published_ts")
    result = pd.Series(float("nan"), index=index.index, dtype="float64", name=series)
    if rows.empty:
        return result
    times = index.to_numpy()
    order = pd.Series(index.to_numpy()).sort_values().index.to_numpy()
    best_event, best_value, pos = None, float("nan"), 0
    published = rows["published_ts"].to_numpy()
    event_ts = rows["event_ts"].to_numpy()
    values = rows[column].to_numpy()
    out = result.to_numpy().copy()
    for idx in order:
        while pos < len(rows) and published[pos] <= times[idx]:
            if best_event is None or event_ts[pos] >= best_event:
                best_event, best_value = event_ts[pos], values[pos]
            pos += 1
        out[idx] = best_value
    return pd.Series(out, index=index.index, name=series)


def align_higher_tf(base: pd.DataFrame, higher: pd.DataFrame, columns: list[str], prefix: str) -> pd.DataFrame:
    """Attach higher-timeframe columns using only higher bars closed by each base bar's close.

    This is the guard against the commonest multi-timeframe leak: joining on the open time
    would hand the base bar a higher-timeframe close that lies in the future.
    """
    right = higher[["close_ts"] + columns].rename(columns={c: f"{prefix}{c}" for c in columns})
    right = right.rename(columns={"close_ts": "_htf_close"}).sort_values("_htf_close")
    left = base[["close_ts"]].reset_index().sort_values("close_ts")
    merged = pd.merge_asof(left, right, left_on="close_ts", right_on="_htf_close", direction="backward")
    merged = merged.set_index("index").sort_index()
    return merged[[f"{prefix}{c}" for c in columns]]


def attach_times(frame: pd.DataFrame, tf: str) -> pd.DataFrame:
    if "close_ts" not in frame:
        frame = frame.assign(close_ts=tfs.close_times(frame["ts"], tf))
    return frame
