"""A compiled bar simulator with the same execution rules as `sim.engine.Engine`.

The event engine stays the reference. This kernel exists because discovery evaluates millions of candidates; a
parity test proves that, for the same signals, both produce the same trades, prices and per-ounce results.

Rules, identical to the engine and to `strategies.base.SignalStrategy`:
  - A decision is taken at a bar's close. Any order or close it creates is handled from the NEXT bar.
  - At each bar: requested closes fill at the open, then pending orders, then stops and targets on the same bar.
  - Longs buy at the ask and sell at the bid; shorts the reverse. Stops and targets test the side that fills.
  - A bar touching both stop and target counts as the stop. A gap through a stop fills at the open.
  - Market orders pay slippage; stops pay slippage; targets and limits do not (limits fill at the price or better).
  - A limit or stop entry that fills inside a bar (not at the open) may be stopped on that bar but may not reach its
    target on it: the bar cannot show whether its other extreme came before or after the fill. This matches the
    engine with EngineConfig(intrabar_entry_target=False).
  - One position at a time. A flip closes, then the new order fills on the next bar.
  - Time exits count bars from the fill bar's close, as SignalStrategy does.
  - Positions still open at the window's end close at the last bar's close.
Results are per ounce. Sizing is applied afterwards as R multiples, so results do not depend on equity.
"""
from __future__ import annotations

import numpy as np

try:
    from numba import njit
    HAVE_NUMBA = True
except ImportError:  # the pure-Python path is the same function, slower
    HAVE_NUMBA = False

    def njit(*a, **k):
        def wrap(f):
            f.py_func = f
            return f
        return wrap(a[0]) if a and callable(a[0]) else wrap

REASONS = ("stop", "stop_gap", "target", "time", "signal_exit", "flip", "day_end", "end_of_data")


@njit(cache=True)
def simulate(bo, bh, bl, bc, ao, ah, al, ac, mid_c,
             want_long, want_short, stop_l, stop_s, tgt_l, tgt_s,
             exit_l, exit_s, day_end, trail, offset,
             entry_type, expiry, max_hold, use_day_end, flip, be_r,
             extra_spread, slip, comm_oz, delay, through,
             swap_l, swap_s, start, end):
    n_max = end - start + 1
    o_entry = np.empty(n_max, np.int64)
    o_exit = np.empty(n_max, np.int64)
    o_side = np.empty(n_max, np.int8)
    o_epx = np.empty(n_max, np.float64)
    o_xpx = np.empty(n_max, np.float64)
    o_stop = np.empty(n_max, np.float64)
    o_pnl = np.empty(n_max, np.float64)
    o_risk = np.empty(n_max, np.float64)
    o_reason = np.empty(n_max, np.int8)
    k = 0

    pos = 0
    e_px = 0.0
    stp = np.nan
    stp0 = np.nan
    tgt = np.nan
    e_idx = -1
    e_intrabar = False
    pend_close = False
    pend_reason = 0
    o_s = 0          # pending order side
    o_type = 0
    o_px = 0.0
    o_ref = 0.0
    o_sl = 0.0
    o_tp = np.nan
    o_active = 0
    o_expiry = 0

    for t in range(start, end):
        a_o = ao[t] + extra_spread
        a_h = ah[t] + extra_spread
        a_l = al[t] + extra_spread
        # 1. requested close fills at the open
        if pos != 0 and pend_close:
            px = bo[t] - slip if pos > 0 else a_o + slip
            sw = (swap_l[t] - swap_l[e_idx]) if pos > 0 else (swap_s[t] - swap_s[e_idx])
            o_entry[k] = e_idx; o_exit[k] = t; o_side[k] = pos; o_epx[k] = e_px; o_xpx[k] = px; o_stop[k] = stp0
            o_pnl[k] = pos * (px - e_px) - 2.0 * comm_oz + sw; o_risk[k] = abs(e_px - stp0); o_reason[k] = pend_reason
            k += 1
            pos = 0
            pend_close = False
        # 2. pending entry order
        if o_s != 0 and t >= o_active:
            if o_type != 0 and t > o_expiry:
                o_s = 0
            else:
                filled = False
                intrabar = False
                px = 0.0
                if o_type == 0:
                    px = a_o + slip if o_s > 0 else bo[t] - slip
                    filled = True
                elif o_type == 1:  # stop entry
                    if o_s > 0 and a_h >= o_px:
                        px = max(o_px, a_o) + slip
                        filled = True
                        intrabar = a_o < o_px
                    elif o_s < 0 and bl[t] <= o_px:
                        px = min(o_px, bo[t]) - slip
                        filled = True
                        intrabar = bo[t] > o_px
                else:  # limit entry
                    if o_s > 0 and a_l <= o_px - through:
                        px = min(o_px, a_o)
                        filled = True
                        intrabar = a_o > o_px
                    elif o_s < 0 and bh[t] >= o_px + through:
                        px = max(o_px, bo[t])
                        filled = True
                        intrabar = bo[t] < o_px
                if filled:
                    pos = o_s
                    e_px = px
                    stp = o_ref - pos * o_sl
                    stp0 = stp
                    tgt = o_ref + pos * o_tp if not np.isnan(o_tp) else np.nan
                    e_idx = t
                    e_intrabar = intrabar
                    o_s = 0
        # 3. stop and target on this bar, the entry bar included
        if pos != 0:
            if pos > 0:
                op_, hi_, lo_ = bo[t], bh[t], bl[t]
                hit_s = lo_ <= stp
                hit_t = (not np.isnan(tgt)) and hi_ >= tgt
            else:
                op_, hi_, lo_ = a_o, a_h, a_l
                hit_s = hi_ >= stp
                hit_t = (not np.isnan(tgt)) and lo_ <= tgt
            if t == e_idx and e_intrabar:
                hit_t = False
            done = False
            px = 0.0
            reason = 0
            if hit_s:
                gap = op_ <= stp if pos > 0 else op_ >= stp
                base = op_ if gap else stp
                px = base - slip if pos > 0 else base + slip
                reason = 1 if gap else 0
                done = True
            elif hit_t:
                gap = op_ >= tgt if pos > 0 else op_ <= tgt
                px = op_ if gap else tgt
                reason = 2
                done = True
            if done:
                sw = (swap_l[t] - swap_l[e_idx]) if pos > 0 else (swap_s[t] - swap_s[e_idx])
                o_entry[k] = e_idx; o_exit[k] = t; o_side[k] = pos; o_epx[k] = e_px; o_xpx[k] = px; o_stop[k] = stp0
                o_pnl[k] = pos * (px - e_px) - 2.0 * comm_oz + sw; o_risk[k] = abs(e_px - stp0); o_reason[k] = reason
                k += 1
                pos = 0
        if t == end - 1:
            break
        # 4. decisions at this bar's close
        w = 1 if want_long[t] else (-1 if want_short[t] else 0)
        closing = False
        if pos != 0:
            held = t - e_idx
            reason = -1
            if max_hold > 0 and held >= max_hold:
                reason = 3
            elif (pos > 0 and exit_l[t]) or (pos < 0 and exit_s[t]):
                reason = 4
            elif flip and w == -pos:
                reason = 5
            elif use_day_end and day_end[t]:
                reason = 6
            if reason >= 0:
                pend_close = True
                pend_reason = reason
                closing = True
            else:
                if not np.isnan(trail[t]):
                    cand = mid_c[t] - pos * trail[t]
                    if (pos > 0 and cand > stp) or (pos < 0 and cand < stp):
                        stp = cand
                if not np.isnan(be_r):
                    if pos * (mid_c[t] - e_px) >= be_r * abs(e_px - stp0):
                        if (pos > 0 and e_px > stp) or (pos < 0 and e_px < stp):
                            stp = e_px
        # A stop or limit order lives for `expiry` bars; it is cancelled at the close of its last bar.
        if o_s != 0 and o_type != 0 and t >= o_expiry:
            o_s = 0
        if (pos == 0 or closing) and o_s == 0 and w != 0:
            sl = stop_l[t] if w > 0 else stop_s[t]
            if not np.isnan(sl) and sl > 0:
                o_s = w
                o_type = entry_type
                o_sl = sl
                o_tp = tgt_l[t] if w > 0 else tgt_s[t]
                if entry_type == 0:
                    o_ref = mid_c[t]
                    o_px = mid_c[t]
                elif entry_type == 1:
                    o_px = mid_c[t] + w * offset[t]
                    o_ref = o_px
                else:
                    o_px = mid_c[t] - w * offset[t]
                    o_ref = o_px
                o_active = t + 1 + delay
                o_expiry = t + delay + expiry
                if np.isnan(o_px):
                    o_s = 0
    # positions still open at the end close at the last close
    if pos != 0:
        t = end - 1
        px = bc[t] if pos > 0 else ac[t] + extra_spread
        sw = (swap_l[t] - swap_l[e_idx]) if pos > 0 else (swap_s[t] - swap_s[e_idx])
        o_entry[k] = e_idx; o_exit[k] = t; o_side[k] = pos; o_epx[k] = e_px; o_xpx[k] = px; o_stop[k] = stp0
        o_pnl[k] = pos * (px - e_px) - 2.0 * comm_oz + sw; o_risk[k] = abs(e_px - stp0); o_reason[k] = 7
        k += 1
    return (o_entry[:k], o_exit[:k], o_side[:k], o_epx[:k], o_xpx[:k], o_stop[:k], o_pnl[:k], o_risk[:k], o_reason[:k])


def python_simulate(*args):
    """The same algorithm without compilation, for the parity test and environments without numba."""
    fn = getattr(simulate, "py_func", simulate)
    return fn(*args)
