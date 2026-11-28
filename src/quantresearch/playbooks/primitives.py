"""Exact definitions behind the three personal playbooks (docs/my_playbooks.md states them in words).

Every function reads bars in time order and uses only bars that have closed by the time it reports something.
Prices are mid (the warehouse's `open/high/low/close`); fills elsewhere use bid and ask.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SWING_K = 2  # A swing high is higher than the 2 bars on each side; it is known 2 bars after it.


def wilder(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    valid = np.flatnonzero(~np.isnan(x))
    if len(valid) < n:
        return out
    s = valid[0]
    if s + n > len(x):
        return out
    out[s + n - 1] = np.nanmean(x[s:s + n])
    for i in range(s + n, len(x)):
        out[i] = out[i - 1] + (x[i] - out[i - 1]) / n
    return out


def atr(h, l, c, n: int = 14) -> np.ndarray:
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    tr[0] = h[0] - l[0]
    return wilder(tr, n)


def adx(h, l, c, n: int = 14) -> np.ndarray:
    """Wilder's ADX(14)."""
    up, down = h - np.r_[np.nan, h[:-1]], np.r_[np.nan, l[:-1]] - l
    plus = np.where((up > down) & (up > 0), up, 0.0)
    minus = np.where((down > up) & (down > 0), down, 0.0)
    plus[0] = minus[0] = np.nan
    a = atr(h, l, c, n)
    sp, sm = wilder(plus, n), wilder(minus, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        pdi, mdi = 100 * sp / a, 100 * sm / a
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    return wilder(dx, n)


@dataclass
class Swing:
    idx: int        # Bar of the swing
    known: int      # First bar at whose close it is known (idx + SWING_K)
    price: float
    high: bool


def swings(h: np.ndarray, l: np.ndarray, k: int = SWING_K) -> list[Swing]:
    out = []
    for i in range(k, len(h) - k):
        if h[i] > h[i - k:i].max() and h[i] >= h[i + 1:i + k + 1].max():
            out.append(Swing(i, i + k, float(h[i]), True))
        if l[i] < l[i - k:i].min() and l[i] <= l[i + 1:i + k + 1].min():
            out.append(Swing(i, i + k, float(l[i]), False))
    return sorted(out, key=lambda s: (s.known, s.idx))


@dataclass
class Break:
    idx: int        # Bar whose close broke structure
    direction: int  # +1 broke a swing high, -1 broke a swing low
    level: float
    swing_idx: int


def structure_breaks(o, h, l, c, sw: list[Swing]) -> list[Break]:
    """A bullish break: the first close above the most recent known swing high. Each swing can be broken once."""
    out = []
    last_high = last_low = None
    j = 0
    for i in range(len(c)):
        while j < len(sw) and sw[j].known <= i:
            if sw[j].high:
                last_high = sw[j]
            else:
                last_low = sw[j]
            j += 1
        if last_high is not None and c[i] > last_high.price:
            out.append(Break(i, 1, last_high.price, last_high.idx))
            last_high = None
        if last_low is not None and c[i] < last_low.price:
            out.append(Break(i, -1, last_low.price, last_low.idx))
            last_low = None
    return out


@dataclass
class Zone:
    kind: str       # "fvg" or "ob"
    tf: str
    direction: int  # +1 bullish (support), -1 bearish (resistance)
    lo: float
    hi: float
    formed_ts: np.datetime64   # When it became known (close of the bar that completed it)
    filled_ts: np.datetime64 | None = None  # First time price traded through its far edge

    @property
    def mid(self) -> float:
        return (self.lo + self.hi) / 2

    def unmitigated_at(self, t) -> bool:
        return self.formed_ts <= t and (self.filled_ts is None or self.filled_ts > t)


def fvgs(o, h, l, c, ts_open, ts_close, tf: str) -> list[Zone]:
    """Three-candle gaps. Bullish: candle 1's high below candle 3's low, zone [high1, low3]; bearish mirrored."""
    out = []
    for i in range(2, len(c)):
        if h[i - 2] < l[i]:
            out.append(Zone("fvg", tf, 1, float(h[i - 2]), float(l[i]), ts_close[i]))
        elif l[i - 2] > h[i]:
            out.append(Zone("fvg", tf, -1, float(h[i]), float(l[i - 2]), ts_close[i]))
    return out


def order_blocks(o, h, l, c, ts_close, breaks: list[Break], tf: str) -> list[Zone]:
    """The last opposite-colour candle before the leg that broke structure. Bullish: the last down-close candle at or
    before the lowest low between the broken swing high and the break; zone that candle's low to high."""
    out = []
    for b in breaks:
        lo_i, hi_i = b.swing_idx, b.idx
        if hi_i <= lo_i:
            continue
        seg = slice(lo_i, hi_i + 1)
        start = lo_i + int(np.argmin(l[seg]) if b.direction > 0 else np.argmax(h[seg]))
        k = start
        while k >= max(0, lo_i - 50):
            if (c[k] < o[k]) if b.direction > 0 else (c[k] > o[k]):
                out.append(Zone("ob", tf, b.direction, float(l[k]), float(h[k]), ts_close[b.idx]))
                break
            k -= 1
    return out


def mark_fills(zones: list[Zone], h, l, ts_open) -> None:
    """A zone is mitigated once price trades through its far edge: below lo for bullish, above hi for bearish."""
    t = ts_open  # Bars are in time order.
    for z in zones:
        a = int(np.searchsorted(t, z.formed_ts, side="left"))
        hit = np.flatnonzero(l[a:] < z.lo) if z.direction > 0 else np.flatnonzero(h[a:] > z.hi)
        z.filled_ts = t[a + hit[0]] if len(hit) else None


def cisd_level(o, c, extreme_idx: int, direction: int, floor_idx: int = 0) -> float | None:
    """Change in state of delivery. For a bearish CISD after a high: the run of consecutive up-close candles that ran
    into the extreme; the level is the open of the run's first candle. A close below it is the CISD. Bullish mirrored."""
    k = extreme_idx
    want_up = direction < 0
    while k >= floor_idx and not ((c[k] > o[k]) if want_up else (c[k] < o[k])):
        k -= 1
    if k < floor_idx:
        return None
    first = k
    while first - 1 >= floor_idx and ((c[first - 1] > o[first - 1]) if want_up else (c[first - 1] < o[first - 1])):
        first -= 1
    return float(o[first])


def run_start(o, c, extreme_idx: int, direction: int, floor_idx: int = 0) -> int:
    k = extreme_idx
    want_up = direction < 0
    while k >= floor_idx and not ((c[k] > o[k]) if want_up else (c[k] < o[k])):
        k -= 1
    first = max(k, floor_idx)
    while first - 1 >= floor_idx and ((c[first - 1] > o[first - 1]) if want_up else (c[first - 1] < o[first - 1])):
        first -= 1
    return first
