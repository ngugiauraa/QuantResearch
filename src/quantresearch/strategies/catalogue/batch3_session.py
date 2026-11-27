"""Catalogue batch 3: sessions, time of day, calendar and scheduled events (20 strategies).

Times are local to the named market and follow its daylight saving (zoneinfo). M15 bars are labelled by their
open time; a rule "at the 14:30 bar close" uses the bar opening at 14:15. Scheduled events use only their published
calendar time, which is known in advance; no event outcome is ever read before it happens.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantresearch.strategies.base import trading_day_position
from quantresearch.strategies.catalogue.batch1_trend import ny_trading_day
from quantresearch.strategies.catalogue.common import LONDON, NEW_YORK, cross_down, entry, first_per_day, local_clock, signals, window_range

B = 3
SES, CAL, EVT = "session", "calendar", "event"
OHLC = ["XAUUSD bars (mid prices)"]
SHANGHAI = "Asia/Shanghai"


def _in(clock: pd.DataFrame, start_hm: tuple[int, int], end_hm: tuple[int, int]) -> pd.Series:
    """Bars whose OPEN time is in [start, end) local time."""
    a, b = start_hm[0] * 60 + start_hm[1], end_hm[0] * 60 + end_hm[1]
    return (clock["minute"] >= a) & (clock["minute"] < b)


def _bar_minutes(df: pd.DataFrame) -> int:
    step = df["ts"].diff().dt.total_seconds().div(60).mode()
    return int(step.iloc[0]) if len(step) else 15


# ---------------------------------------------------------------- Opening-range and range breakouts
def range_breakout(zone: str, range_window, entry_window, flat_at, stop_mode: str = "range"):
    def prepare(df, p):
        clock = local_clock(df["ts"], zone)
        day = clock["date"]
        rng = window_range(df, _in(clock, *range_window), day)
        ready = _in(clock, *entry_window)
        width = rng["high"] - rng["low"]
        up, down = ready & (df["close"] > rng["high"]), ready & (df["close"] < rng["low"])
        first = first_per_day(up | down, day)
        flat = _in(clock, flat_at, (23, 59))
        stop = width * p["stop_range"]
        return signals(df, long=first & up, short=first & down, stop=np.maximum(stop, 0.5 * df["atr_14"]),
                       target=p["target_range"] * width if p.get("target_range") else None, exit_long=flat, exit_short=flat)
    return prepare


def london_close_reversal(df, p):
    clock = local_clock(df["ts"], LONDON)
    day = clock["date"]
    open_px = df["open"].where(_in(clock, (8, 0), (8, 15))).groupby(day).transform("max")
    move = df["close"] - open_px
    prior_range = df["pdh"] - df["pdl"]
    at = _in(clock, (14, 45), (15, 0))  # The bar closing at 15:00 London.
    big = move.abs() > p["move_range"] * prior_range
    flat = _in(clock, (19, 0), (23, 59))
    return signals(df, long=at & big & (move < 0), short=at & big & (move > 0), stop=p["stop_range"] * prior_range,
                   exit_long=flat, exit_short=flat)


def fix_drift(start_hm, decision_hm):
    """The move over the M15 bar opening at start_hm (local London time), followed for one bar into the auction."""
    def prepare(df, p):
        clock = local_clock(df["ts"], LONDON)
        at = clock["minute"] == start_hm[0] * 60 + start_hm[1]
        move = df["close"] - df["open"]
        big = move.abs() > p["move_atr"] * df["atr_14"]
        return signals(df, long=at & big & (move > 0), short=at & big & (move < 0), stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])
    return prepare


# ---------------------------------------------------------------- Time-of-day holds
def timed_hold(zone: str, enter_hm, exit_hm, side: int, weekdays: set[int] | None = None):
    """Enter at the first bar opening at or after enter_hm (local), exit at the first bar opening at or after exit_hm."""
    def prepare(df, p):
        clock = local_clock(df["ts"], zone)
        a, b = enter_hm[0] * 60 + enter_hm[1], exit_hm[0] * 60 + exit_hm[1]
        start = clock["minute"] == a
        if weekdays is not None:
            start &= clock["weekday"].isin(weekdays)
        stop_ = clock["minute"] == b
        mask = {"long": start} if side > 0 else {"short": start}
        return signals(df, **mask, stop=p["stop_atr"] * df["atr_14"], hold=p["max_hold"], exit_long=stop_, exit_short=stop_)
    return prepare


def weekend_hold(df, p):
    clock = local_clock(df["ts"], NEW_YORK)
    enter = (clock["weekday"] == 4) & (clock["minute"] == 15 * 60)
    leave = (clock["weekday"] == 0) & (clock["minute"] == 3 * 60)
    return signals(df, long=enter, stop=p["stop_atr"] * df["atr_14"], hold=p["max_hold"], exit_long=leave)


def monday_short(df, p):
    clock = local_clock(df["ts"], NEW_YORK)
    gap = df["ts"].diff().dt.total_seconds() > 24 * 3600
    first_of_week = gap & (clock["weekday"] == 6)
    leave = (clock["weekday"] == 0) & (clock["minute"] == 16 * 60)
    return signals(df, short=first_of_week, stop=p["stop_atr"] * df["atr_14"], hold=p["max_hold"], exit_short=leave)


def overnight_long(df, p):
    day = ny_trading_day(df["ts"])
    clock = local_clock(df["ts"], NEW_YORK)
    first_bar = day != day.shift(1)
    leave = clock["minute"] == 3 * 60
    return signals(df, long=first_bar, stop=p["stop_atr"] * df["atr_14"], hold=p["max_hold"], exit_long=leave)


# ---------------------------------------------------------------- FOMC
def fomc_pre_drift(df, p):
    if "hours_to_fomc" not in df:
        return signals(df)
    h = df["hours_to_fomc"]
    enter = cross_down(h, p["hours_before"])
    leave = h < p["exit_hours_before"]
    return signals(df, long=enter, stop=p["stop_atr"] * df["atr_14"], hold=p["max_hold"], exit_long=leave)


def after_release(df: pd.DataFrame, hours: pd.Series, after: float, follow: bool, p: dict) -> pd.DataFrame:
    """`hours` = hours since the latest release at each bar's close. The reference is the close of the bar ending at
    the release (0 <= hours < one bar); the decision is on the bar closing `after` hours later."""
    bar_h = df["ts"].diff().dt.total_seconds().median() / 3600
    ref = df["close"].where((hours >= 0) & (hours < bar_h)).ffill()
    at = (hours >= after) & (hours < after + bar_h)
    move = df["close"] - ref
    big = move.abs() > p["move_atr"] * df["atr_14"]
    up, down = at & big & (move > 0), at & big & (move < 0)
    return signals(df, long=up if follow else down, short=down if follow else up, stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])


def fomc_post_follow(df, p):
    if "hours_since_fomc" not in df:
        return signals(df)
    return after_release(df, df["hours_since_fomc"], p["after_hours"], True, p)


# ---------------------------------------------------------------- Payrolls (first-Friday approximation)
def nfp_days(clock: pd.DataFrame) -> pd.Series:
    """First Friday of the month: the usual US payrolls release day. An approximation; see the entry's missing_data."""
    return (clock["weekday"] == 4) & (clock["day"] <= 7)


def nfp_reaction(follow: bool):
    def prepare(df, p):
        clock = local_clock(df["ts"], NEW_YORK)
        day = clock["date"]
        on = nfp_days(clock)
        before = df["close"].where(on & _in(clock, (8, 15), (8, 30))).groupby(day).transform("max")  # Close of the 08:15 bar.
        at = on & (clock["minute"] == 9 * 60 + 15)  # The bar closing at 09:30.
        move = df["close"] - before
        big = move.abs() > p["move_atr"] * df["atr_14"]
        up, down = at & big & (move > 0), at & big & (move < 0)
        flat = _in(clock, (15, 0), (23, 59))
        stop = np.maximum(p["stop_move"] * move.abs(), df["atr_14"])
        return signals(df, long=up if follow else down, short=down if follow else up, stop=stop, exit_long=flat, exit_short=flat)
    return prepare


def cpi_fade(df, p):
    if "hours_since_cpi" not in df or df["hours_since_cpi"].notna().sum() == 0:
        return signals(df)
    return after_release(df, df["hours_since_cpi"], 1.0, False, p)


# ---------------------------------------------------------------- Calendar months
def month_window(enter_month: int, exit_month: int):
    def prepare(df, p):
        pos = trading_day_position(df["ts"])
        month = df["ts"].dt.month
        enter = (month == enter_month) & (pos["tdom"] == 1)
        leave = (month == exit_month) & (pos["tdoe"] == 1)
        return signals(df, long=enter, stop=p["stop_atr"] * df["atr_14"], exit_long=leave)
    return prepare


def options_expiry_rebound(df, p):
    pos = trading_day_position(df["ts"])
    month = df["ts"].dt.month
    expiry_month = month.isin([1, 3, 5, 7, 9, 11])  # COMEX gold options on the Feb, Apr, Jun, Aug, Oct, Dec futures.
    after = expiry_month & (pos["tdoe"] == 4)  # The day after expiry, which is four business days before month end.
    return signals(df, long=after, stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])


def settlement_momentum(df, p):
    clock = local_clock(df["ts"], NEW_YORK)
    day = clock["date"]
    ref = df["open"].where(_in(clock, (12, 30), (12, 45))).groupby(day).transform("max")
    at = clock["minute"] == 13 * 60 + 15  # The bar closing at 13:30, COMEX settlement.
    move = df["close"] - ref
    big = move.abs() > p["move_atr"] * df["atr_14"]
    return signals(df, long=at & big & (move > 0), short=at & big & (move < 0), stop=p["stop_atr"] * df["atr_14"], hold=p["hold"])


ATR = "the project's Wilder ATR(14) on the signal timeframe"
NFP_MISSING = ["the official BLS release calendar (the BLS site refused automated access); the first-Friday rule is an approximation that misses some months"]

ENTRIES = [
    entry("cat_asian_range_london_m15", "Asian range breakout at the London open", "m15", dict(stop_range=1.0, target_range=1.0),
          range_breakout(LONDON, ((0, 0), (7, 0)), ((7, 0), (10, 0)), (16, 0)),
          "Trades the first M15 close outside the Asian-session range during the London morning.",
          family=SES, batch=B, source="Widely taught 'London breakout' rule for gold and FX; own exact windows",
          logic="London traders often push price out of the quiet Asian range; follow the first break.",
          entry_rule="Asian range = high/low of M15 bars opening 00:00-07:00 London time. Between 07:00 and 10:00 London, enter on the first close above the range high (long) or below the low (short); one trade a day.",
          exit_rule="Target 1 x the range width; flat at the first bar from 16:00 London.", stop_rule="1 x the range width (at least 0.5 ATR).", data=OHLC),
    entry("cat_london_orb_m15", "London 30-minute opening-range breakout", "m15", dict(stop_range=1.0, target_range=1.5),
          range_breakout(LONDON, ((8, 0), (8, 30)), ((8, 30), (11, 0)), (16, 0)),
          "Opening-range breakout on the first 30 minutes of the London session.",
          family=SES, batch=B, source="Toby Crabel's opening-range breakout (1990), applied to the London open",
          logic="The first half hour of London sets a range; a break of it shows the morning's direction.",
          entry_rule="Range = 08:00-08:30 London. Between 08:30 and 11:00, first close above the range (long) or below it (short); one trade a day.",
          exit_rule="Target 1.5 x range width; flat from 16:00 London.", stop_rule="1 x the range width (at least 0.5 ATR).", data=OHLC),
    entry("cat_ny_orb_m15", "New York 30-minute opening-range breakout", "m15", dict(stop_range=1.0, target_range=1.5),
          range_breakout(NEW_YORK, ((8, 30), (9, 0)), ((9, 0), (12, 0)), (15, 0)),
          "Opening-range breakout on 08:30-09:00 New York, when US data and the COMEX open land.",
          family=SES, batch=B, source="Toby Crabel (1990); COMEX gold opens 08:20 New York",
          logic="The first US half hour sets the day's range; trade its break.",
          entry_rule="Range = 08:30-09:00 New York. Between 09:00 and 12:00, first close above (long) or below (short) it; one trade a day.",
          exit_rule="Target 1.5 x range width; flat from 15:00 New York.", stop_rule="1 x the range width (at least 0.5 ATR).", data=OHLC),
    entry("cat_london_close_reversal_m15", "London close reversal", "m15", dict(move_range=0.5, stop_range=0.25), london_close_reversal,
          "After a big London-session move, fades it into the New York afternoon.",
          family=SES, batch=B, source="Commonly taught FX/gold session rule (London close profit-taking); own thresholds",
          logic="When London has pushed far, positions are often squared as London closes.",
          entry_rule="At the bar closing 15:00 London: if close - the 08:00 London open exceeds 0.5 x yesterday's range, short (or long if the move was down).",
          exit_rule="Flat from 19:00 London.", stop_rule="0.25 x yesterday's high-low range.", data=OHLC),
    entry("cat_pm_fix_drift_m15", "Pre-auction drift into the 15:00 London gold price", "m15", dict(move_atr=1.0, stop_atr=1.5, hold=1),
          fix_drift((14, 30), (15, 0)),
          "Follows the half hour of drift before the LBMA afternoon gold price into the auction.",
          family=EVT, batch=B, source="Caminschi and Heaney, 'Fixing a Leaky Fixing', Journal of Futures Markets 34(11), 2014",
          logic="Prices drifted in the direction of the fix before it was announced; follow that drift into the auction.",
          entry_rule="On the M15 bar 14:30-14:45 London: if its close is more than 1 x ATR(14) from its open, enter in that direction at 14:45.",
          exit_rule="Exit after one M15 bar (15:00, the auction start).", stop_rule=f"1.5 x {ATR}.", data=OHLC,
          limitations=["The fixing became an electronic auction in 2015; the paper's sample predates that."]),
    entry("cat_am_fix_drift_m15", "Pre-auction drift into the 10:30 London gold price", "m15", dict(move_atr=1.0, stop_atr=1.5, hold=1),
          fix_drift((10, 15), (10, 30)),
          "The same pre-auction drift rule for the morning auction.",
          family=EVT, batch=B, source="Caminschi and Heaney (2014), applied to the morning auction; own extension",
          logic="The same drift idea, at the morning fix.",
          entry_rule="On the M15 bar 10:15-10:30 London: if its close is more than 1 x ATR(14) from its open, enter in that direction at 10:30.",
          exit_rule="Exit after one M15 bar (10:45, as the auction runs).", stop_rule=f"1.5 x {ATR}.", data=OHLC),
    entry("cat_overnight_long_h1", "Overnight hold: COMEX open to London open", "h1", dict(stop_atr=2.0, max_hold=12), overnight_long,
          "Long gold from the 18:00 New York session open to 03:00 New York (the London open).",
          family=SES, batch=B, source="Adapted from Cooper, Cliff and Gulen, 'Return Differences between Trading and Non-trading Hours', Journal of Financial Markets 11(2), 2008",
          logic="Much of some markets' return arrives outside the main trading session; test that for gold's Asian hours.",
          entry_rule="Long on the first H1 bar of each COMEX trading day (opens 18:00 New York). Long only.",
          exit_rule="Exit at the bar opening 03:00 New York; at most 12 bars.", stop_rule=f"2 x {ATR}.", data=OHLC),
    entry("cat_us_session_short_h1", "Short the US session", "h1", dict(stop_atr=2.0, max_hold=8),
          timed_hold(NEW_YORK, (8, 0), (16, 0), -1, {0, 1, 2, 3, 4}),
          "Short gold from 08:00 to 16:00 New York on weekdays.",
          family=SES, batch=B, source="Own test of the often-repeated claim that gold rises in Asian hours and falls in US hours",
          logic="If US hours are systematically weak for gold, a daily short across them should show it.",
          entry_rule="Short at the bar opening 08:00 New York, Monday to Friday.", exit_rule="Exit at the bar opening 16:00 New York; at most 8 bars.",
          stop_rule=f"2 x {ATR}.", data=OHLC),
    entry("cat_shanghai_session_long_m15", "Long the Shanghai day session", "m15", dict(stop_atr=3.0, max_hold=26),
          timed_hold(SHANGHAI, (9, 0), (15, 30), 1, {0, 1, 2, 3, 4}),
          "Long gold from the Shanghai Gold Exchange's 09:00 open to 15:30 Beijing time.",
          family=SES, batch=B, source="Own test of the commonly claimed Chinese-demand bid during Shanghai hours",
          logic="If Chinese buying lifts gold during the Shanghai day session, a long across it should earn.",
          entry_rule="Long at the M15 bar opening 09:00 Asia/Shanghai, Monday to Friday. Long only.",
          exit_rule="Exit at the bar opening 15:30 Asia/Shanghai; at most 26 bars.", stop_rule=f"3 x {ATR}.", data=OHLC),
    entry("cat_weekend_hold_h1", "Hold gold over the weekend", "h1", dict(stop_atr=2.5, max_hold=60), weekend_hold,
          "Long from Friday 15:00 New York to Monday 03:00 New York.",
          family=CAL, batch=B, source="Own test of the weekend geopolitical risk-premium claim",
          logic="Weekend news risk may be priced as a premium for holding gold into Monday.",
          entry_rule="Long at the bar opening 15:00 New York on Friday.", exit_rule="Exit at the bar opening 03:00 New York on Monday; at most 60 bars.",
          stop_rule=f"2.5 x {ATR}.", data=OHLC, limitations=["The weekend gap is filled at the reopening price; the stop cannot act while closed."]),
    entry("cat_monday_short_h1", "Monday weakness (weekend effect)", "h1", dict(stop_atr=2.0, max_hold=30), monday_short,
          "Short from the Sunday reopening to Monday 16:00 New York.",
          family=CAL, batch=B, source="Ball, Torous and Tschoegl, 'Gold and the Weekend Effect', Journal of Futures Markets 2(2), 1982",
          logic="Monday returns in gold were historically weak; test whether that still holds.",
          entry_rule="Short on the first H1 bar after the weekend (the Sunday-evening reopening).", exit_rule="Exit at the bar opening 16:00 New York on Monday.",
          stop_rule=f"2 x {ATR}.", data=OHLC),
    entry("cat_fomc_pre_drift_h1", "Pre-FOMC drift", "h1", dict(hours_before=24.0, exit_hours_before=0.5, stop_atr=2.0, max_hold=26), fomc_pre_drift,
          "Long gold in the 24 hours before a scheduled FOMC decision.",
          family=EVT, batch=B, source="Lucca and Moench, 'The Pre-FOMC Announcement Drift', Journal of Finance 70(1), 2015 (equities)",
          logic="Prices drifted up ahead of Fed decisions in equities; test whether gold does the same.",
          entry_rule="Long on the first H1 close less than 24 hours before a scheduled FOMC decision (customary 14:00 New York statement time). Long only.",
          exit_rule="Exit on the first bar closing less than 30 minutes before the decision; at most 26 bars.", stop_rule=f"2 x {ATR}.",
          data=["XAUUSD H1 bars", "FOMC decision calendar (federalreserve.gov)"]),
    entry("cat_fomc_post_follow_h1", "Follow the first two hours after FOMC", "h1", dict(after_hours=2.0, move_atr=1.0, stop_atr=2.0, hold=24), fomc_post_follow,
          "Follows a large move in the two hours after an FOMC decision.",
          family=EVT, batch=B, source="Own idea, after the post-announcement drift literature",
          logic="A strong first reaction to a Fed decision may continue as the market digests it.",
          entry_rule="Reference = the close of the bar ending at the decision. On the bar closing 2 hours later: if the close is more than 1 x ATR(14) from the reference, enter in that direction.",
          exit_rule="Exit after 24 bars.", stop_rule=f"2 x {ATR}.", data=["XAUUSD H1 bars", "FOMC decision calendar"]),
    entry("cat_nfp_fade_m15", "Fade the first payrolls hour", "m15", dict(move_atr=4.0, stop_move=0.5), nfp_reaction(False),
          "Fades a big 08:30-09:30 New York move on payrolls Friday.",
          family=EVT, batch=B, source="Commonly taught 'fade the NFP spike' rule; own thresholds",
          logic="The first reaction to payrolls often overshoots and partly reverses.",
          entry_rule="On the first Friday of the month, at the bar closing 09:30 New York: if the close is more than 4 x ATR(14, M15) from the 08:30 level, enter against the move.",
          exit_rule="Flat from 15:00 New York.", stop_rule="0.5 x the move (at least 1 ATR).", data=OHLC, missing=NFP_MISSING),
    entry("cat_nfp_follow_m15", "Follow the first payrolls hour", "m15", dict(move_atr=4.0, stop_move=0.5), nfp_reaction(True),
          "Follows a big 08:30-09:30 New York move on payrolls Friday.",
          family=EVT, batch=B, source="Own idea, the opposite test of the NFP fade",
          logic="A strong payrolls reaction may set the day's trend.",
          entry_rule="On the first Friday of the month, at the bar closing 09:30 New York: if the close is more than 4 x ATR(14, M15) from the 08:30 level, enter with the move.",
          exit_rule="Flat from 15:00 New York.", stop_rule="0.5 x the move (at least 1 ATR).", data=OHLC, missing=NFP_MISSING),
    entry("cat_cpi_fade_m15", "Fade the first hour after US CPI", "m15", dict(move_atr=4.0, stop_atr=3.0, hold=20), cpi_fade,
          "Fades a large first-hour move after the US CPI release.",
          family=EVT, batch=B, source="Commonly taught data-release fade; own thresholds",
          logic="The first CPI reaction tends to overshoot.",
          entry_rule="Reference = the close of the bar ending at the 08:30 New York release. On the bar closing one hour later: if the close is more than 4 x ATR(14, M15) from the reference, enter against the move.",
          exit_rule="Exit after 20 bars.", stop_rule=f"3 x {ATR}.", data=["XAUUSD M15 bars", "US CPI release times"],
          missing=["US CPI release times (the BLS schedule refused automated access); no trades until a hours_since_cpi column is supplied"]),
    entry("cat_autumn_long_d1", "Autumn effect", "d1", dict(stop_atr=4.0), month_window(9, 11),
          "Long gold from the first trading day of September to the last trading day of November.",
          family=CAL, batch=B, source="Dirk Baur, 'The Autumn Effect of Gold', Research in International Business and Finance 27(1), 2013",
          logic="Gold has historically done best in September to November, linked to Indian wedding-season demand.",
          entry_rule="Long on the first trading day of September. Long only.", exit_rule="Exit on the last trading day of November.",
          stop_rule=f"4 x {ATR}, catastrophic.", data=OHLC, limitations=["One trade a year: too few for a walk-forward verdict; expect insufficient evidence."]),
    entry("cat_january_long_d1", "January strength", "d1", dict(stop_atr=4.0), month_window(1, 1),
          "Long gold through January.",
          family=CAL, batch=B, source="Own test of the commonly repeated 'January is gold's best month' claim",
          logic="Year-start allocation and Lunar New Year demand are said to lift gold in January.",
          entry_rule="Long on the first trading day of January. Long only.", exit_rule="Exit on the last trading day of January.",
          stop_rule=f"4 x {ATR}, catastrophic.", data=OHLC, limitations=["One trade a year: expect insufficient evidence."]),
    entry("cat_options_expiry_d1", "Rebound after COMEX options expiry", "d1", dict(stop_atr=3.0, hold=3), options_expiry_rebound,
          "Long for three days after the approximate COMEX gold options expiry.",
          family=EVT, batch=B, source="Own test of the 'options pinning then release' claim",
          logic="Price may be held down into option expiry and released afterwards.",
          entry_rule="Long on the trading day four days before the end of Jan, Mar, May, Jul, Sep and Nov (the day after the usual expiry). Long only.",
          exit_rule="Exit after 3 days.", stop_rule=f"3 x {ATR}.", data=OHLC,
          missing=["the exchange's options expiry calendar; expiry is approximated as five business days before month end, which ignores holidays"]),
    entry("cat_settlement_momentum_m15", "Momentum into the COMEX settlement", "m15", dict(move_atr=1.5, stop_atr=1.5, hold=4), settlement_momentum,
          "Follows a strong last hour into the 13:30 New York COMEX settlement.",
          family=SES, batch=B, source="Own idea (settlement-window order flow)",
          logic="Hedgers trading at the settlement may extend a move that is already under way.",
          entry_rule="At the bar closing 13:30 New York: if the close is more than 1.5 x ATR(14) from the 12:30 open, enter in that direction.",
          exit_rule="Exit after 4 bars (one hour).", stop_rule=f"1.5 x {ATR}.", data=OHLC),
]
