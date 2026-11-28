# Strategy catalogue

124 gold (XAUUSD) strategies that are commonly claimed to be automatable, plus some of this project's own ideas. Each is implemented in `src/quantresearch/strategies/catalogue/` and runs through exactly the same research as the 8 engines: walk-forward out-of-sample folds, the untouched two-year holdout, observed and stressed costs, and the same report.

**Hard rules.** Parameters are fixed as written here and are never tuned (each strategy has one variant; the first run locks it). Every run is recorded in the experiment ledger and the trial book, and nothing is deleted. The deflated Sharpe that decides 'promising' counts every variant ever tested by the engines and the catalogue (N), with the luck spread taken from the catalogue's out-of-sample Sharpes; all strategies are re-scored against the final count. A strategy is only promising if it has at least 30 out-of-sample trades, is positive out of sample, positive in the holdout, positive in the holdout under stressed costs, and has a project-wide deflated Sharpe above 0.95, and beats a long-only gold position at the same risk in both the out-of-sample folds and the holdout (added 2026-11-28, after gold's 6.7x rise was seen to flatter long-biased rules). The deflation method was changed once, after batch 1's results, and is now frozen with these criteria.

**Conventions.** Signals act at a bar's close and fill on a later event, so 'breaks above X' means 'closes above X'. ATR is Wilder ATR(14) on the strategy's timeframe. Every strategy has a stop because positions are sized from it (0.5% risk). Times are local market time with daylight saving. 'Needs data we don't have' strategies are implemented but cannot trade until that data exists.

Regenerate with `python -m quantresearch.strategies.catalogue.docs`. Run with `python -m quantresearch.research.catalogue --all` or from the dashboard's Strategy Catalogue table.

## Needs data we don't have

- `cat_nfp_fade_m15`: the official BLS release calendar (the BLS site refused automated access); the first-Friday rule is an approximation that misses some months
- `cat_nfp_follow_m15`: the official BLS release calendar (the BLS site refused automated access); the first-Friday rule is an approximation that misses some months
- `cat_cpi_fade_m15`: US CPI release times (the BLS schedule refused automated access); no trades until a hours_since_cpi column is supplied
- `cat_options_expiry_d1`: the exchange's options expiry calendar; expiry is approximated as five business days before month end, which ignores holidays
- `cat_cpi_surprise_d1`: CPI consensus forecasts and first-release values (no free source; FRED has revised values only)

## Batch 1

### `cat_turtle_s1_d1`: Turtle System 1 (20/10-day Donchian)

- **Family:** Trend and breakout. **Timeframe:** D1.
- **Source:** Curtis Faith, 'Way of the Turtle' (2007)
- **Logic:** Buy when gold makes a new 20-day high and sell when it makes a new 20-day low; get out on a 10-day break the other way.
- **Entry:** Long when the daily close is above the highest high of the prior 20 days; short when below the lowest low of the prior 20 days.
- **Exit:** Exit long when the close is below the prior 10-day low; exit short when above the prior 10-day high; an opposite entry also reverses.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe from the entry close.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"entry_n": 20, "exit_n": 10, "stop_atr": 2.0}`

### `cat_turtle_s2_d1`: Turtle System 2 (55/20-day Donchian)

- **Family:** Trend and breakout. **Timeframe:** D1.
- **Source:** Curtis Faith, 'Way of the Turtle' (2007)
- **Logic:** Buy a new 55-day high and sell a new 55-day low; exit on a 20-day break the other way.
- **Entry:** Long when the daily close is above the prior 55-day high; short when below the prior 55-day low.
- **Exit:** Exit long below the prior 20-day low; exit short above the prior 20-day high; an opposite entry reverses.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"entry_n": 55, "exit_n": 20, "stop_atr": 2.0}`

### `cat_donchian20_h4`: Donchian 20/10 on H4

- **Family:** Trend and breakout. **Timeframe:** H4.
- **Source:** Richard Donchian's channel rule, as popularised by the Turtles; H4 application is common in retail gold systems
- **Logic:** The 20/10 channel breakout, faster, on four-hour bars.
- **Entry:** Long when the H4 close is above the prior 20-bar high; short when below the prior 20-bar low.
- **Exit:** Exit long below the prior 10-bar low; exit short above the prior 10-bar high; opposite entry reverses.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"entry_n": 20, "exit_n": 10, "stop_atr": 2.0}`

### `cat_golden_cross_d1`: Golden/death cross (50/200-day SMA)

- **Family:** Trend and breakout. **Timeframe:** D1.
- **Source:** Classic chart rule; e.g. Brock, Lakonishok and LeBaron, Journal of Finance 47(5), 1992
- **Logic:** Hold gold long after the 50-day average crosses above the 200-day, and short after it crosses below.
- **Entry:** Long on the day the 50-day SMA crosses above the 200-day SMA; short on the day it crosses below.
- **Exit:** The opposite cross reverses the position; no other exit.
- **Stop:** 4 x the project's Wilder ATR(14) on the signal timeframe, a catastrophic stop only (the classic rule has none); after a stop-out it waits for the next cross.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"kind": "sma", "fast": 50, "slow": 200, "stop_atr": 4.0}`

### `cat_ema_cross_h1`: EMA 20/50 cross on H1

- **Family:** Trend and breakout. **Timeframe:** H1.
- **Source:** Common retail moving-average crossover system
- **Logic:** Follow the hourly 20/50 EMA cross.
- **Entry:** Long when EMA(20) crosses above EMA(50); short when it crosses below.
- **Exit:** Opposite cross reverses.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"kind": "ema", "fast": 20, "slow": 50, "stop_atr": 3.0}`

### `cat_faber_10m_mn1`: Faber 10-month SMA timing

- **Family:** Trend and breakout. **Timeframe:** MN1.
- **Source:** Mebane Faber, 'A Quantitative Approach to Tactical Asset Allocation' (2007), https://papers.ssrn.com/sol3/papers.cfm?abstract_id=962461
- **Logic:** Own gold only while its monthly close is above the 10-month moving average.
- **Entry:** Long at any monthly close above the 10-month SMA of monthly closes (so it re-enters the month after a stop-out if still above).
- **Exit:** Exit when a monthly close is below the 10-month SMA. Long only.
- **Stop:** 4 x the monthly ATR(14), catastrophic only.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"months": 10, "stop_atr": 4.0}`

### `cat_tsmom_12m_d1`: Time-series momentum (12-month, monthly rebalance)

- **Family:** Trend and breakout. **Timeframe:** D1.
- **Source:** Moskowitz, Ooi and Pedersen, 'Time Series Momentum', Journal of Financial Economics 104(2), 2012, https://doi.org/10.1016/j.jfineco.2011.11.003
- **Logic:** Be long gold if it is up over the past year and short if it is down, checking once a month.
- **Entry:** On the first trading day of each month: long if close / close 252 days ago - 1 > 0, short if < 0.
- **Exit:** Reverses on a later monthly check with the opposite sign; otherwise holds.
- **Stop:** 4 x the project's Wilder ATR(14) on the signal timeframe, catastrophic; re-entry only at the next monthly check.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"lookback": 252, "stop_atr": 4.0}`

### `cat_keltner_breakout_h1`: Keltner channel breakout (EMA20 +/- 2 ATR)

- **Family:** Trend and breakout. **Timeframe:** H1.
- **Source:** Chester Keltner (1960); ATR form popularised by Linda Bradford Raschke
- **Logic:** A close outside the ATR channel starts a trend trade that lasts until price returns to the average.
- **Entry:** Long when the close crosses above EMA(20) + 2 x ATR(14); short when it crosses below EMA(20) - 2 x ATR(14).
- **Exit:** Exit long when the close is below EMA(20); exit short when above EMA(20).
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"n": 20, "k": 2.0, "stop_atr": 2.0}`

### `cat_bollinger_breakout_h4`: Bollinger band breakout on H4

- **Family:** Trend and breakout. **Timeframe:** H4.
- **Source:** John Bollinger, 'Bollinger on Bollinger Bands' (2001)
- **Logic:** A close outside the bands is treated as the start of a move.
- **Entry:** Long when the H4 close crosses above the upper band (SMA20 + 2 SD); short when it crosses below the lower band.
- **Exit:** Exit long when the close is below the 20-bar SMA; exit short when above it.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"stop_atr": 2.0}`

### `cat_di_cross_h1`: Wilder DI cross with ADX > 25

- **Family:** Trend and breakout. **Timeframe:** H1.
- **Source:** J. Welles Wilder, 'New Concepts in Technical Trading Systems' (1978)
- **Logic:** When +DI crosses above -DI in a trending market, buy; the reverse, sell.
- **Entry:** Long when +DI(14) crosses above -DI(14) and ADX(14) > 25; short when -DI crosses above +DI and ADX > 25.
- **Exit:** Exit on the opposite DI cross (regardless of ADX).
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"adx_min": 25.0, "stop_atr": 2.0}`

### `cat_supertrend_h1`: Supertrend (10, 3)

- **Family:** Trend and breakout. **Timeframe:** H1.
- **Source:** Olivier Seban's Supertrend indicator; standard definition as in most charting packages
- **Logic:** Follow the Supertrend's direction, reversing when it flips.
- **Entry:** Long on the bar the Supertrend (ATR 10, multiplier 3, on (high+low)/2) flips up; short when it flips down.
- **Exit:** The opposite flip reverses.
- **Stop:** Distance from the entry close to the Supertrend line, at least 0.5 x ATR(14).
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"n": 10, "mult": 3.0}`

### `cat_macd_zero_h4`: MACD signal cross with zero-line filter

- **Family:** Trend and breakout. **Timeframe:** H4.
- **Source:** Gerald Appel, 'Technical Analysis: Power Tools for Active Investors' (2005)
- **Logic:** Buy MACD bullish crosses only when MACD is above zero, and sell bearish crosses only below zero.
- **Entry:** Long when MACD(12,26) crosses above its 9-EMA signal while MACD > 0; short when it crosses below while MACD < 0.
- **Exit:** Exit long on any bearish signal cross; exit short on any bullish cross.
- **Stop:** 2.5 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"fast": 12, "slow": 26, "signal": 9, "stop_atr": 2.5}`

### `cat_ichimoku_h4`: Ichimoku cloud break with TK alignment

- **Family:** Trend and breakout. **Timeframe:** H4.
- **Source:** Goichi Hosoda's Ichimoku Kinko Hyo; standard 9/26/52 settings
- **Logic:** Price above the cloud with the fast line above the slow line is an uptrend; the reverse a downtrend.
- **Entry:** Long on the first bar where the close is above both cloud spans (each shifted 26 bars) and Tenkan(9) > Kijun(26); short mirrored.
- **Exit:** Exit long when the close is below Kijun; exit short when above.
- **Stop:** 2.5 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"stop_atr": 2.5}`

### `cat_52w_high_d1`: 52-week closing-high breakout

- **Family:** Trend and breakout. **Timeframe:** D1.
- **Source:** Adapted from George and Hwang, 'The 52-Week High and Momentum Investing', Journal of Finance 59(5), 2004 (stocks)
- **Logic:** New yearly highs tend to keep going, so buy them and hold until the trend weakens.
- **Entry:** Long when the daily close crosses above the highest close of the prior 252 days. Long only.
- **Exit:** Exit when the close is below the 50-day SMA.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"lookback": 252, "exit_sma": 50, "stop_atr": 3.0}`

### `cat_nr7_d1`: NR7 breakout

- **Family:** Trend and breakout. **Timeframe:** D1.
- **Source:** Toby Crabel, 'Day Trading with Short Term Price Patterns and Opening Range Breakout' (1990)
- **Logic:** Very narrow days come before expansion; trade the direction of the break.
- **Entry:** If yesterday's high-low range was the smallest of the last 7 days: long when today's close is above yesterday's high, short when below yesterday's low.
- **Exit:** Exit after 5 days.
- **Stop:** The far side of the NR7 day's range (its low for a long, its high for a short).
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"n": 7, "hold": 5}`

### `cat_inside_bar_h4`: Inside-bar breakout on H4

- **Family:** Trend and breakout. **Timeframe:** H4.
- **Source:** Common price-action pattern, e.g. Al Brooks, 'Trading Price Action Trends' (2011)
- **Logic:** A bar inside the previous bar is a pause; the break of the bigger bar shows direction.
- **Entry:** If the previous bar was inside the bar before it: long when the close is above the mother bar's high, short when below its low.
- **Exit:** Target 1 x the mother bar's range from entry; otherwise exit after 12 bars.
- **Stop:** The mother bar's opposite extreme.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"target_range": 1.0, "hold": 12}`

### `cat_dual_thrust_h1`: Dual Thrust (4-day range, k = 0.5)

- **Family:** Trend and breakout. **Timeframe:** H1.
- **Source:** Michael Chalek's Dual Thrust system, widely published in futures-system literature
- **Logic:** Buy when price moves half a recent range above the day's open, sell the mirror, and close everything late in the day.
- **Entry:** Range = max(HH - LC, HC - LL) over the prior 4 COMEX days. Long on the first H1 close above day open + 0.5 x Range; short on the first close below open - 0.5 x Range; no new entries from 15:00 New York.
- **Exit:** Flat at the first bar from 16:00 New York; an opposite entry reverses.
- **Stop:** 0.5 x Range (back to the day's open).
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"days": 4, "k": 0.5, "last_entry_ny": 15, "flat_ny": 16}`

### `cat_chandelier_h4`: 20-bar breakout with chandelier exit

- **Family:** Trend and breakout. **Timeframe:** H4.
- **Source:** Chuck LeBeau's chandelier exit, as described in Alexander Elder, 'Come into My Trading Room' (2002)
- **Logic:** Enter breakouts and let a trailing stop hung from the recent high decide the exit.
- **Entry:** Long when the H4 close is above the prior 20-bar high; short when below the prior 20-bar low.
- **Exit:** Exit long when the close is below the highest high of the last 22 bars - 3 x ATR(14); short mirrored.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"entry_n": 20, "trail_n": 22, "trail_atr": 3.0, "stop_atr": 3.0}`

### `cat_ema_pullback_h1`: Pullback to EMA20 in an aligned trend

- **Family:** Trend and breakout. **Timeframe:** H1.
- **Source:** Common trend-pullback rule; own exact parameters
- **Logic:** In a clean uptrend, a touch of the 20-EMA that closes back above it is a chance to join the trend.
- **Entry:** Long when EMA20 > EMA50 > SMA200, the bar's low touches EMA20, and the bar closes above EMA20 and above its open; short mirrored.
- **Exit:** Target 3 x ATR(14); otherwise exit after 48 bars.
- **Stop:** 1.5 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"stop_atr": 1.5, "target_atr": 3.0, "hold": 48}`

### `cat_weekly_breakout_h4`: Prior-week high/low breakout

- **Family:** Trend and breakout. **Timeframe:** H4.
- **Source:** Own idea, a weekly variant of the channel breakout
- **Logic:** Breaking last week's range shows a new weekly trend.
- **Entry:** Long on the first H4 close of the week above the previous calendar week's (UTC) high; short on the first close below its low.
- **Exit:** Exit after 30 H4 bars (about a week); an opposite entry reverses.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"stop_atr": 2.0, "hold": 30}`

## Batch 2

### `cat_rsi2_d1`: Connors RSI(2) reversion, daily

- **Family:** Mean reversion. **Timeframe:** D1.
- **Source:** Larry Connors and Cesar Alvarez, 'Short Term Trading Strategies That Work' (2008)
- **Logic:** In an uptrend, a very oversold two-day RSI tends to bounce; the mirror in a downtrend.
- **Entry:** Long when close > SMA(200) and RSI(2) < 10; short when close < SMA(200) and RSI(2) > 90.
- **Exit:** Exit long when the close is above SMA(5); exit short when below SMA(5); at most 10 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe (the book uses no stop; this one is catastrophic).
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"low": 10.0, "high": 90.0, "stop_atr": 3.0, "hold": 10}`

### `cat_rsi2_h1`: Connors RSI(2) reversion, hourly

- **Family:** Mean reversion. **Timeframe:** H1.
- **Source:** Connors and Alvarez (2008), applied intraday; own timeframe choice
- **Logic:** The same oversold-bounce idea on hourly bars.
- **Entry:** Long when close > SMA(200) and RSI(2) < 10; short when close < SMA(200) and RSI(2) > 90 (hourly bars).
- **Exit:** Exit long above SMA(5), short below SMA(5); at most 24 bars.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"low": 10.0, "high": 90.0, "stop_atr": 2.0, "hold": 24}`

### `cat_three_down_d1`: Three down days in an uptrend

- **Family:** Mean reversion. **Timeframe:** D1.
- **Source:** Larry Connors, 'How Markets Really Work' (2004)
- **Logic:** Several losing days in a row inside an uptrend are a pullback to buy.
- **Entry:** Long after three consecutive lower closes with close > SMA(200); short after three higher closes with close < SMA(200).
- **Exit:** Exit long above SMA(5), short below SMA(5); at most 10 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"stop_atr": 3.0, "hold": 10}`

### `cat_double_seven_d1`: Double 7s

- **Family:** Mean reversion. **Timeframe:** D1.
- **Source:** Larry Connors and Cesar Alvarez, 'Short Term Trading Strategies That Work' (2008)
- **Logic:** A one-week low in an uptrend tends to revert to a one-week high.
- **Entry:** Long when the close is the lowest of the last 7 closes and above SMA(200); short mirrored below SMA(200).
- **Exit:** Exit long at a 7-day closing high; exit short at a 7-day closing low; at most 15 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"stop_atr": 3.0, "hold": 15}`

### `cat_ibs_d1`: Internal bar strength

- **Family:** Mean reversion. **Timeframe:** D1.
- **Source:** Pagonidis, 'The IBS Effect: Mean Reversion in Equity ETFs' (NAAIM, 2013)
- **Logic:** A close at the bottom of the day's range tends to be followed by a better day.
- **Entry:** IBS = (close - low) / (high - low). Long when IBS < 0.2; short when IBS > 0.8.
- **Exit:** Exit after one day.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"low": 0.2, "high": 0.8, "stop_atr": 2.0}`

### `cat_bb_fade_h1`: Bollinger fade in low ADX

- **Family:** Mean reversion. **Timeframe:** H1.
- **Source:** John Bollinger (2001); ranging filter by ADX from Wilder (1978)
- **Logic:** In a range, a close outside the bands tends to come back to the middle.
- **Entry:** When ADX(14) < 20: long when the close is below the lower band (SMA20 - 2 SD); short when above the upper band.
- **Exit:** Exit at the 20-bar SMA; at most 24 bars.
- **Stop:** 1.5 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"adx_max": 20.0, "stop_atr": 1.5, "hold": 24}`

### `cat_zscore60_h4`: 60-bar z-score fade on H4

- **Family:** Mean reversion. **Timeframe:** H4.
- **Source:** Generic statistical mean reversion; own parameters
- **Logic:** Stretched far from its average, gold tends to snap back.
- **Entry:** Long when the 60-bar z-score crosses below -2; short when it crosses above +2.
- **Exit:** Exit at the 60-bar mean; at most 30 bars.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"z": 2.0, "stop_atr": 2.0, "hold": 30}`

### `cat_keltner_fade_h1`: Keltner fade (2.5 ATR) in low ADX

- **Family:** Mean reversion. **Timeframe:** H1.
- **Source:** Linda Bradford Raschke's Keltner usage; own fade parameters
- **Logic:** Outside a wide ATR channel in a quiet market is too far; trade back to the average.
- **Entry:** When ADX(14) < 20: long when the close crosses below EMA(20) - 2.5 x ATR; short when it crosses above EMA(20) + 2.5 x ATR.
- **Exit:** Exit at EMA(20); at most 24 bars.
- **Stop:** 1.5 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"k": 2.5, "adx_max": 20.0, "stop_atr": 1.5, "hold": 24}`

### `cat_rsi_pullback_h4`: RSI(14) pullback with the 200-bar trend

- **Family:** Mean reversion. **Timeframe:** H4.
- **Source:** Common trend-pullback rule (e.g. Andrew Cardwell's RSI ranges); own parameters
- **Logic:** In an uptrend RSI rarely stays below 35, so a dip there is a pullback to buy.
- **Entry:** Long when close > SMA(200) and RSI(14) crosses below 35; short when close < SMA(200) and RSI(14) crosses above 65.
- **Exit:** Exit long when RSI > 55, short when RSI < 45; at most 30 bars.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"low": 35.0, "high": 65.0, "exit_long": 55.0, "exit_short": 45.0, "stop_atr": 2.0, "hold": 30}`

### `cat_quiet_fade_h1`: Fade stretches when volatility is contracting

- **Family:** Mean reversion. **Timeframe:** H1.
- **Source:** Own idea
- **Logic:** When the market is calming down, extremes are more likely noise than a new trend.
- **Entry:** When ATR(14)/ATR(50) < 0.8: long when the 20-bar z-score < -2; short when > +2.
- **Exit:** Exit when the z-score crosses zero; at most 24 bars.
- **Stop:** 1.5 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"atr_ratio_max": 0.8, "z": 2.0, "stop_atr": 1.5, "hold": 24}`

### `cat_turtle_soup_d1`: Turtle Soup (failed 20-day breakout)

- **Family:** Mean reversion. **Timeframe:** D1.
- **Source:** Linda Bradford Raschke and Laurence Connors, 'Street Smarts' (1995)
- **Logic:** Breakout traders get trapped when a new 20-day extreme fails the same day.
- **Entry:** Long when the low breaks the prior 20-day low (made at least 4 days earlier) and the close is back above it; short mirrored.
- **Exit:** Target 1.5 x the stop distance; otherwise exit after 5 days.
- **Stop:** Just beyond the day's extreme: close - low for a long (at least 0.25 ATR).
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"n": 20, "age": 4, "target_r": 1.5, "hold": 5}`

### `cat_80_20_d1`: 80-20 reversal

- **Family:** Mean reversion. **Timeframe:** D1.
- **Source:** Linda Bradford Raschke and Laurence Connors, 'Street Smarts' (1995)
- **Logic:** A day that runs from top to bottom often sees a final flush, then a reversal the next day.
- **Entry:** If yesterday opened in its top 20% and closed in its bottom 20%: long today when the low breaks yesterday's low and the close is back above it. Short mirrored.
- **Exit:** Exit after 3 days.
- **Stop:** Today's extreme (at least 0.25 ATR).
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"hold": 3}`

### `cat_pdh_pdl_fade_h1`: Failed break of the previous day's high/low

- **Family:** Mean reversion. **Timeframe:** H1.
- **Source:** Common intraday level rule; own parameters
- **Logic:** Stops sit beyond yesterday's extremes; once taken, price often returns into the range.
- **Entry:** First time each COMEX day: long when an H1 bar's low is below yesterday's low and it closes above it; short mirrored at yesterday's high.
- **Exit:** Target the middle of yesterday's range; otherwise exit after 12 bars.
- **Stop:** The bar's extreme (at least 0.3 ATR).
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"hold": 12}`

### `cat_weekend_gap_fade_h1`: Weekend gap fade

- **Family:** Mean reversion. **Timeframe:** H1.
- **Source:** Common gap-fill belief; see also the project's overreaction engine
- **Logic:** Weekend gaps in gold often fill as the week's trading starts.
- **Entry:** On the first H1 bar after the weekend: if open - Friday close exceeds 0.5 x ATR and the bar has not closed back through Friday's close, fade the gap.
- **Exit:** Target Friday's close; otherwise exit after 24 bars.
- **Stop:** The size of the gap.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"gap_atr": 0.5, "hold": 24}`

### `cat_vwap_fade_m15`: Session VWAP 2-SD fade

- **Family:** Mean reversion. **Timeframe:** M15.
- **Source:** Common intraday VWAP-band reversion; own parameters
- **Logic:** Far from the day's volume-weighted average, price tends to come back to it.
- **Entry:** From the 8th M15 bar of the COMEX day: long on the first close below VWAP - 2 x the volume-weighted SD; short above VWAP + 2 SD.
- **Exit:** Exit at VWAP; at most 16 bars.
- **Stop:** 1.5 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars; provider tick volume as the volume weight
- **Parameters (fixed):** `{"k": 2.0, "min_bars": 8, "stop_atr": 1.5, "hold": 16}`

### `cat_bb_squeeze_h4`: Bollinger squeeze breakout

- **Family:** Volatility. **Timeframe:** H4.
- **Source:** John Bollinger, 'Bollinger on Bollinger Bands' (2001), the Squeeze
- **Logic:** Very narrow bands come before big moves; trade the direction of the break.
- **Entry:** If band width was within 5% of its 125-bar low at any of the last 5 bars: long when the close crosses above the upper band, short below the lower band.
- **Exit:** Exit long below the 20-bar SMA, short above it.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"lookback": 125, "tolerance": 0.05, "within": 5, "stop_atr": 2.0}`

### `cat_ttm_squeeze_h1`: TTM Squeeze release

- **Family:** Volatility. **Timeframe:** H1.
- **Source:** John Carter, 'Mastering the Trade' (2005)
- **Logic:** Bands inside the Keltner channel mean stored energy; the first bar out shows the release direction.
- **Entry:** After at least 6 bars with BB(20, 2) inside KC(EMA20 +/- 1.5 ATR), on the first bar outside: long if close - mean(Donchian mid, SMA20) > 0, short if < 0.
- **Exit:** Exit when that momentum falls two bars in a row (rises for shorts); at most 24 bars.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"kc": 1.5, "min_squeeze": 6, "stop_atr": 2.0, "hold": 24}`

### `cat_range_expansion_h1`: Range-expansion bar follow-through

- **Family:** Volatility. **Timeframe:** H1.
- **Source:** Own idea, after Crabel's range-expansion observations
- **Logic:** A very wide bar closing at its high shows strong buying that often continues briefly.
- **Entry:** If the bar's range > 2 x the previous bar's ATR(14): long if it closes in its top quarter, short if in its bottom quarter.
- **Exit:** Exit after 6 bars.
- **Stop:** 1.5 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"k": 2.0, "stop_atr": 1.5, "hold": 6}`

### `cat_nr4_ib_h4`: NR4 inside-bar breakout

- **Family:** Volatility. **Timeframe:** H4.
- **Source:** Toby Crabel (1990), the NR4/IB pattern
- **Logic:** An inside bar that is also the narrowest of four is maximum compression; the break shows direction.
- **Entry:** If the previous bar was NR4 and inside: long when the close is above its high, short when below its low.
- **Exit:** Exit after 6 bars.
- **Stop:** The setup bar's opposite extreme.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"hold": 6}`

### `cat_calm_breakout_h1`: Breakout from low ATR percentile

- **Family:** Volatility. **Timeframe:** H1.
- **Source:** Own idea (volatility-regime breakout filter)
- **Logic:** Breakouts out of quiet markets are more likely to start real moves than breakouts in noisy ones.
- **Entry:** If the previous bar's ATR(14) percentile over 500 bars was below 20%: long when the close is above the prior 24-bar high, short below the prior 24-bar low.
- **Exit:** Exit after 24 bars.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"lookback": 500, "calm": 0.2, "n": 24, "stop_atr": 2.0, "hold": 24}`

### `cat_vix_spike_d1`: Long gold after a VIX spike

- **Family:** Volatility. **Timeframe:** D1.
- **Source:** Safe-haven literature, e.g. Baur and Lucey, 'Is Gold a Hedge or a Safe Haven?', Financial Review 45(2), 2010
- **Logic:** When fear jumps, money tends to flow into gold for a while.
- **Entry:** Long on the day the VIX's 5-observation change exceeds 25% of its level 5 observations ago (VIX as published by that close). Long only.
- **Exit:** Exit after 10 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD daily bars; VIX close (FRED VIXCLS)
- **Parameters (fixed):** `{"rise": 0.25, "stop_atr": 3.0, "hold": 10}`

## Batch 3

### `cat_asian_range_london_m15`: Asian range breakout at the London open

- **Family:** Session and time of day. **Timeframe:** M15.
- **Source:** Widely taught 'London breakout' rule for gold and FX; own exact windows
- **Logic:** London traders often push price out of the quiet Asian range; follow the first break.
- **Entry:** Asian range = high/low of M15 bars opening 00:00-07:00 London time. Between 07:00 and 10:00 London, enter on the first close above the range high (long) or below the low (short); one trade a day.
- **Exit:** Target 1 x the range width; flat at the first bar from 16:00 London.
- **Stop:** 1 x the range width (at least 0.5 ATR).
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"stop_range": 1.0, "target_range": 1.0}`

### `cat_london_orb_m15`: London 30-minute opening-range breakout

- **Family:** Session and time of day. **Timeframe:** M15.
- **Source:** Toby Crabel's opening-range breakout (1990), applied to the London open
- **Logic:** The first half hour of London sets a range; a break of it shows the morning's direction.
- **Entry:** Range = 08:00-08:30 London. Between 08:30 and 11:00, first close above the range (long) or below it (short); one trade a day.
- **Exit:** Target 1.5 x range width; flat from 16:00 London.
- **Stop:** 1 x the range width (at least 0.5 ATR).
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"stop_range": 1.0, "target_range": 1.5}`

### `cat_ny_orb_m15`: New York 30-minute opening-range breakout

- **Family:** Session and time of day. **Timeframe:** M15.
- **Source:** Toby Crabel (1990); COMEX gold opens 08:20 New York
- **Logic:** The first US half hour sets the day's range; trade its break.
- **Entry:** Range = 08:30-09:00 New York. Between 09:00 and 12:00, first close above (long) or below (short) it; one trade a day.
- **Exit:** Target 1.5 x range width; flat from 15:00 New York.
- **Stop:** 1 x the range width (at least 0.5 ATR).
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"stop_range": 1.0, "target_range": 1.5}`

### `cat_london_close_reversal_m15`: London close reversal

- **Family:** Session and time of day. **Timeframe:** M15.
- **Source:** Commonly taught FX/gold session rule (London close profit-taking); own thresholds
- **Logic:** When London has pushed far, positions are often squared as London closes.
- **Entry:** At the bar closing 15:00 London: if close - the 08:00 London open exceeds 0.5 x yesterday's range, short (or long if the move was down).
- **Exit:** Flat from 19:00 London.
- **Stop:** 0.25 x yesterday's high-low range.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"move_range": 0.5, "stop_range": 0.25}`

### `cat_pm_fix_drift_m15`: Pre-auction drift into the 15:00 London gold price

- **Family:** Scheduled events. **Timeframe:** M15.
- **Source:** Caminschi and Heaney, 'Fixing a Leaky Fixing', Journal of Futures Markets 34(11), 2014
- **Logic:** Prices drifted in the direction of the fix before it was announced; follow that drift into the auction.
- **Entry:** On the M15 bar 14:30-14:45 London: if its close is more than 1 x ATR(14) from its open, enter in that direction at 14:45.
- **Exit:** Exit after one M15 bar (15:00, the auction start).
- **Stop:** 1.5 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"move_atr": 1.0, "stop_atr": 1.5, "hold": 1}`

### `cat_am_fix_drift_m15`: Pre-auction drift into the 10:30 London gold price

- **Family:** Scheduled events. **Timeframe:** M15.
- **Source:** Caminschi and Heaney (2014), applied to the morning auction; own extension
- **Logic:** The same drift idea, at the morning fix.
- **Entry:** On the M15 bar 10:15-10:30 London: if its close is more than 1 x ATR(14) from its open, enter in that direction at 10:30.
- **Exit:** Exit after one M15 bar (10:45, as the auction runs).
- **Stop:** 1.5 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"move_atr": 1.0, "stop_atr": 1.5, "hold": 1}`

### `cat_overnight_long_h1`: Overnight hold: COMEX open to London open

- **Family:** Session and time of day. **Timeframe:** H1.
- **Source:** Adapted from Cooper, Cliff and Gulen, 'Return Differences between Trading and Non-trading Hours', Journal of Financial Markets 11(2), 2008
- **Logic:** Much of some markets' return arrives outside the main trading session; test that for gold's Asian hours.
- **Entry:** Long on the first H1 bar of each COMEX trading day (opens 18:00 New York). Long only.
- **Exit:** Exit at the bar opening 03:00 New York; at most 12 bars.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"stop_atr": 2.0, "max_hold": 12}`

### `cat_us_session_short_h1`: Short the US session

- **Family:** Session and time of day. **Timeframe:** H1.
- **Source:** Own test of the often-repeated claim that gold rises in Asian hours and falls in US hours
- **Logic:** If US hours are systematically weak for gold, a daily short across them should show it.
- **Entry:** Short at the bar opening 08:00 New York, Monday to Friday.
- **Exit:** Exit at the bar opening 16:00 New York; at most 8 bars.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"stop_atr": 2.0, "max_hold": 8}`

### `cat_shanghai_session_long_m15`: Long the Shanghai day session

- **Family:** Session and time of day. **Timeframe:** M15.
- **Source:** Own test of the commonly claimed Chinese-demand bid during Shanghai hours
- **Logic:** If Chinese buying lifts gold during the Shanghai day session, a long across it should earn.
- **Entry:** Long at the M15 bar opening 09:00 Asia/Shanghai, Monday to Friday. Long only.
- **Exit:** Exit at the bar opening 15:30 Asia/Shanghai; at most 26 bars.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"stop_atr": 3.0, "max_hold": 26}`

### `cat_weekend_hold_h1`: Hold gold over the weekend

- **Family:** Calendar. **Timeframe:** H1.
- **Source:** Own test of the weekend geopolitical risk-premium claim
- **Logic:** Weekend news risk may be priced as a premium for holding gold into Monday.
- **Entry:** Long at the bar opening 15:00 New York on Friday.
- **Exit:** Exit at the bar opening 03:00 New York on Monday; at most 60 bars.
- **Stop:** 2.5 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"stop_atr": 2.5, "max_hold": 60}`

### `cat_monday_short_h1`: Monday weakness (weekend effect)

- **Family:** Calendar. **Timeframe:** H1.
- **Source:** Ball, Torous and Tschoegl, 'Gold and the Weekend Effect', Journal of Futures Markets 2(2), 1982
- **Logic:** Monday returns in gold were historically weak; test whether that still holds.
- **Entry:** Short on the first H1 bar after the weekend (the Sunday-evening reopening).
- **Exit:** Exit at the bar opening 16:00 New York on Monday.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"stop_atr": 2.0, "max_hold": 30}`

### `cat_fomc_pre_drift_h1`: Pre-FOMC drift

- **Family:** Scheduled events. **Timeframe:** H1.
- **Source:** Lucca and Moench, 'The Pre-FOMC Announcement Drift', Journal of Finance 70(1), 2015 (equities)
- **Logic:** Prices drifted up ahead of Fed decisions in equities; test whether gold does the same.
- **Entry:** Long on the first H1 close less than 24 hours before a scheduled FOMC decision (customary 14:00 New York statement time). Long only.
- **Exit:** Exit on the first bar closing less than 30 minutes before the decision; at most 26 bars.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD H1 bars; FOMC decision calendar (federalreserve.gov)
- **Parameters (fixed):** `{"hours_before": 24.0, "exit_hours_before": 0.5, "stop_atr": 2.0, "max_hold": 26}`

### `cat_fomc_post_follow_h1`: Follow the first two hours after FOMC

- **Family:** Scheduled events. **Timeframe:** H1.
- **Source:** Own idea, after the post-announcement drift literature
- **Logic:** A strong first reaction to a Fed decision may continue as the market digests it.
- **Entry:** Reference = the close of the bar ending at the decision. On the bar closing 2 hours later: if the close is more than 1 x ATR(14) from the reference, enter in that direction.
- **Exit:** Exit after 24 bars.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD H1 bars; FOMC decision calendar
- **Parameters (fixed):** `{"after_hours": 2.0, "move_atr": 1.0, "stop_atr": 2.0, "hold": 24}`

### `cat_nfp_fade_m15`: Fade the first payrolls hour

- **Family:** Scheduled events. **Timeframe:** M15.
- **Source:** Commonly taught 'fade the NFP spike' rule; own thresholds
- **Logic:** The first reaction to payrolls often overshoots and partly reverses.
- **Entry:** On the first Friday of the month, at the bar closing 09:30 New York: if the close is more than 4 x ATR(14, M15) from the 08:30 level, enter against the move.
- **Exit:** Flat from 15:00 New York.
- **Stop:** 0.5 x the move (at least 1 ATR).
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"move_atr": 4.0, "stop_move": 0.5}`
- **Needs data we don't have:** the official BLS release calendar (the BLS site refused automated access); the first-Friday rule is an approximation that misses some months

### `cat_nfp_follow_m15`: Follow the first payrolls hour

- **Family:** Scheduled events. **Timeframe:** M15.
- **Source:** Own idea, the opposite test of the NFP fade
- **Logic:** A strong payrolls reaction may set the day's trend.
- **Entry:** On the first Friday of the month, at the bar closing 09:30 New York: if the close is more than 4 x ATR(14, M15) from the 08:30 level, enter with the move.
- **Exit:** Flat from 15:00 New York.
- **Stop:** 0.5 x the move (at least 1 ATR).
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"move_atr": 4.0, "stop_move": 0.5}`
- **Needs data we don't have:** the official BLS release calendar (the BLS site refused automated access); the first-Friday rule is an approximation that misses some months

### `cat_cpi_fade_m15`: Fade the first hour after US CPI

- **Family:** Scheduled events. **Timeframe:** M15.
- **Source:** Commonly taught data-release fade; own thresholds
- **Logic:** The first CPI reaction tends to overshoot.
- **Entry:** Reference = the close of the bar ending at the 08:30 New York release. On the bar closing one hour later: if the close is more than 4 x ATR(14, M15) from the reference, enter against the move.
- **Exit:** Exit after 20 bars.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD M15 bars; US CPI release times
- **Parameters (fixed):** `{"move_atr": 4.0, "stop_atr": 3.0, "hold": 20}`
- **Needs data we don't have:** US CPI release times (the BLS schedule refused automated access); no trades until a hours_since_cpi column is supplied

### `cat_autumn_long_d1`: Autumn effect

- **Family:** Calendar. **Timeframe:** D1.
- **Source:** Dirk Baur, 'The Autumn Effect of Gold', Research in International Business and Finance 27(1), 2013
- **Logic:** Gold has historically done best in September to November, linked to Indian wedding-season demand.
- **Entry:** Long on the first trading day of September. Long only.
- **Exit:** Exit on the last trading day of November.
- **Stop:** 4 x the project's Wilder ATR(14) on the signal timeframe, catastrophic.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"stop_atr": 4.0}`

### `cat_january_long_d1`: January strength

- **Family:** Calendar. **Timeframe:** D1.
- **Source:** Own test of the commonly repeated 'January is gold's best month' claim
- **Logic:** Year-start allocation and Lunar New Year demand are said to lift gold in January.
- **Entry:** Long on the first trading day of January. Long only.
- **Exit:** Exit on the last trading day of January.
- **Stop:** 4 x the project's Wilder ATR(14) on the signal timeframe, catastrophic.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"stop_atr": 4.0}`

### `cat_options_expiry_d1`: Rebound after COMEX options expiry

- **Family:** Scheduled events. **Timeframe:** D1.
- **Source:** Own test of the 'options pinning then release' claim
- **Logic:** Price may be held down into option expiry and released afterwards.
- **Entry:** Long on the trading day four days before the end of Jan, Mar, May, Jul, Sep and Nov (the day after the usual expiry). Long only.
- **Exit:** Exit after 3 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"stop_atr": 3.0, "hold": 3}`
- **Needs data we don't have:** the exchange's options expiry calendar; expiry is approximated as five business days before month end, which ignores holidays

### `cat_settlement_momentum_m15`: Momentum into the COMEX settlement

- **Family:** Session and time of day. **Timeframe:** M15.
- **Source:** Own idea (settlement-window order flow)
- **Logic:** Hedgers trading at the settlement may extend a move that is already under way.
- **Entry:** At the bar closing 13:30 New York: if the close is more than 1.5 x ATR(14) from the 12:30 open, enter in that direction.
- **Exit:** Exit after 4 bars (one hour).
- **Stop:** 1.5 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD bars (mid prices)
- **Parameters (fixed):** `{"move_atr": 1.5, "stop_atr": 1.5, "hold": 4}`

## Batch 4

### `cat_real_yield_momentum_d1`: Real-yield moves (10-year TIPS)

- **Family:** Macro. **Timeframe:** D1.
- **Source:** Erb and Harvey, 'The Golden Dilemma' (NBER w18706, 2013), https://www.nber.org/papers/w18706
- **Logic:** Gold pays no yield, so falling real yields make it more attractive and rising ones less.
- **Entry:** Long when DFII10 - DFII10 20 days earlier crosses below -0.15; short when it crosses above +0.15.
- **Exit:** Exit after 10 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD daily bars; FRED DFII10 (10-year TIPS yield). Macro: DFII10
- **Parameters (fixed):** `{"stop_atr": 3.0, "hold": 10}`

### `cat_real_yield_trend_d1`: Falling real-yield regime with gold uptrend

- **Family:** Macro. **Timeframe:** D1.
- **Source:** Erb and Harvey (2013); regime form is own idea
- **Logic:** Hold gold only when both the macro tailwind and the price trend agree.
- **Entry:** Long when DFII10 < its 120-day SMA and the close > SMA(50), on the first day both hold. Long only.
- **Exit:** Exit when either condition fails.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD daily bars; FRED DFII10. Macro: DFII10
- **Parameters (fixed):** `{"ry_sma": 120, "stop_atr": 3.0}`

### `cat_dollar_momentum_d1`: Broad-dollar moves

- **Family:** Macro. **Timeframe:** D1.
- **Source:** Gold-dollar inverse relationship, e.g. Capie, Mills and Wood, 'Gold as a Hedge against the Dollar', J. Int. Financial Markets 15(4), 2005
- **Logic:** Gold is priced in dollars, so a weaker dollar tends to lift it.
- **Entry:** Long when the 20-day % change in DTWEXBGS crosses below -1%; short when it crosses above +1%.
- **Exit:** Exit after 10 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD daily bars; FRED DTWEXBGS (weekly, assumed 8-day lag). Macro: DTWEXBGS
- **Parameters (fixed):** `{"stop_atr": 3.0, "hold": 10}`

### `cat_dollar_yield_combo_d1`: Dollar and real yield falling together

- **Family:** Macro. **Timeframe:** D1.
- **Source:** Own idea combining the two main gold macro drivers
- **Logic:** When both main headwinds ease at once, gold should do well; when both tighten, badly.
- **Entry:** Long on the first day both DTWEXBGS and DFII10 are below their values 5 days earlier; short on the first day both are above.
- **Exit:** Exit after 5 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD daily bars; FRED DTWEXBGS; FRED DFII10. Macro: DTWEXBGS, DFII10
- **Parameters (fixed):** `{"days": 5, "stop_atr": 3.0, "hold": 5}`

### `cat_breakeven_trend_d1`: Breakeven inflation moves

- **Family:** Macro. **Timeframe:** D1.
- **Source:** Gold as an inflation hedge, e.g. Bampinas and Panagiotidis, 'Are Gold and Silver a Hedge against Inflation?', Int. Review of Financial Analysis 41, 2015
- **Logic:** Rising inflation expectations are said to lift gold.
- **Entry:** Long when T10YIE - its value 20 days earlier crosses above +0.10; short when it crosses below -0.10.
- **Exit:** Exit after 10 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD daily bars; FRED T10YIE. Macro: T10YIE
- **Parameters (fixed):** `{"stop_atr": 3.0, "hold": 10}`

### `cat_fed_cut_cycle_d1`: Fed cutting cycle

- **Family:** Macro. **Timeframe:** D1.
- **Source:** Commonly claimed 'gold rallies in easing cycles'; own exact rule
- **Logic:** Rate cuts lower the cost of holding gold.
- **Entry:** Long on the first day DFF - DFF 60 days earlier < -0.25. Long only.
- **Exit:** Exit when that change is back at or above zero.
- **Stop:** 4 x the project's Wilder ATR(14) on the signal timeframe, catastrophic.
- **Data:** XAUUSD daily bars; FRED DFF. Macro: DFF
- **Parameters (fixed):** `{"days": 60, "threshold": 0.25, "stop_atr": 4.0}`

### `cat_curve_steepener_d1`: Yield-curve steepening

- **Family:** Macro. **Timeframe:** D1.
- **Source:** Own idea (steepening as an easing/inflation signal)
- **Logic:** A steepening curve often signals expected easing or rising inflation, both said to help gold.
- **Entry:** Long when (DGS10 - DGS2) minus its value 20 days earlier crosses above +0.15. Long only.
- **Exit:** Exit after 20 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD daily bars; FRED DGS10; FRED DGS2. Macro: DGS10, DGS2
- **Parameters (fixed):** `{"days": 20, "threshold": 0.15, "stop_atr": 3.0, "hold": 20}`

### `cat_vix_regime_d1`: High-VIX regime

- **Family:** Macro. **Timeframe:** D1.
- **Source:** Baur and Lucey, 'Is Gold a Hedge or a Safe Haven?', Financial Review 45(2), 2010
- **Logic:** In market stress gold is said to act as a safe haven.
- **Entry:** Long when the VIX close crosses above 30. Long only.
- **Exit:** Exit when the VIX is below 25.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD daily bars; FRED VIXCLS. Macro: VIXCLS
- **Parameters (fixed):** `{"enter": 30.0, "exit": 25.0, "stop_atr": 3.0}`

### `cat_gvz_panic_d1`: Gold-volatility spike reversal

- **Family:** Macro. **Timeframe:** D1.
- **Source:** Own idea (panic reversal using CBOE's gold volatility index)
- **Logic:** A spike in gold's implied volatility during a fall often marks capitulation.
- **Entry:** Long when the 250-day z-score of GVZ crosses above 2 and the close is below SMA(50). Long only.
- **Exit:** Exit after 10 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD daily bars; FRED GVZCLS (CBOE Gold ETF Volatility Index). Macro: GVZCLS
- **Parameters (fixed):** `{"window": 250, "z": 2.0, "stop_atr": 3.0, "hold": 10}`

### `cat_oil_inflation_d1`: Oil surge as an inflation signal

- **Family:** Macro. **Timeframe:** D1.
- **Source:** Commonly claimed oil-gold inflation link; own exact rule
- **Logic:** A sharp oil rise feeds inflation fears, which are said to lift gold.
- **Entry:** Long when WTI (FRED DCOILWTICO) / its value 20 days earlier - 1 crosses above 10%. Long only.
- **Exit:** Exit after 20 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD daily bars; FRED DCOILWTICO (assumed 8-day lag). Macro: DCOILWTICO
- **Parameters (fixed):** `{"days": 20, "threshold": 0.1, "stop_atr": 3.0, "hold": 20}`

### `cat_cpi_surprise_d1`: CPI surprise

- **Family:** Macro. **Timeframe:** D1.
- **Source:** Event-study literature on gold and macro news, e.g. Elder, Miao and Ramchander, 'Impact of Macroeconomic News on Metal Futures', J. Banking & Finance 36(1), 2012
- **Logic:** Higher-than-expected inflation is said to lift gold.
- **Entry:** On the release day: long if actual CPI m/m minus consensus > +0.1 points, short if < -0.1.
- **Exit:** Exit after 5 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD daily bars; CPI first releases and consensus forecasts. Macro: CPI_SURPRISE
- **Parameters (fixed):** `{"threshold": 0.1, "stop_atr": 3.0, "hold": 5}`
- **Needs data we don't have:** CPI consensus forecasts and first-release values (no free source; FRED has revised values only)

### `cat_cot_contrarian_d1`: COT managed-money extremes (contrarian)

- **Family:** Macro. **Timeframe:** D1.
- **Source:** Common sentiment rule; see e.g. Sanders, Boris and Manfredo, 'Hedgers, Funds, and Small Speculators in the Energy Futures Markets', Energy Economics 26(3), 2004
- **Logic:** When funds are extremely long, few buyers remain, so the price is vulnerable.
- **Entry:** Long when the 104-week z-score of CFTC managed-money net (published Friday 15:30 New York) crosses below -2; short when it crosses above +2.
- **Exit:** Exit after 10 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD daily bars; CFTC disaggregated COT, gold
- **Parameters (fixed):** `{"z": 2.0, "stop_atr": 3.0, "hold": 10}`

### `cat_cot_momentum_d1`: COT managed-money trend

- **Family:** Macro. **Timeframe:** D1.
- **Source:** Own idea (flow persistence in positioning)
- **Logic:** Funds adding to positions week after week may keep buying.
- **Entry:** On a new COT publication: long if managed-money net rose in each of the last 3 reports; short if it fell in each.
- **Exit:** Exit after 5 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD daily bars; CFTC disaggregated COT, gold. Macro: COT_MM_NET
- **Parameters (fixed):** `{"reports": 3, "stop_atr": 3.0, "hold": 5}`

### `cat_gold_silver_ratio_d1`: Gold/silver ratio extremes

- **Family:** Cross-market. **Timeframe:** D1.
- **Source:** Commonly traded gold/silver ratio reversion; e.g. Baur and Tran, 'The Long-run Relationship of Gold and Silver', Applied Economics Letters 21(13), 2014
- **Logic:** When gold is unusually expensive relative to silver, the ratio tends to fall back, which can mean gold falls.
- **Entry:** z = 250-day z-score of ln(gold / silver). Short gold when z crosses above 2; long gold when it crosses below -2.
- **Exit:** Exit when |z| < 0.5.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD daily bars; silver (XAGUSD) H1 closes. Cross-market: xagusd
- **Parameters (fixed):** `{"window": 250, "z": 2.0, "z_exit": 0.5, "stop_atr": 3.0}`

### `cat_silver_lead_h1`: Silver jump, gold lag

- **Family:** Cross-market. **Timeframe:** H1.
- **Source:** Own idea (lead-lag between related metals)
- **Logic:** Silver sometimes moves first; gold may follow within a few hours.
- **Entry:** If silver's 1-bar log return exceeds 2 x its 100-bar SD while gold's is under 0.5 x its own SD: enter gold in silver's direction.
- **Exit:** Exit after 3 bars.
- **Stop:** 1.5 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD H1 bars; silver (XAGUSD) H1 closes. Cross-market: xagusd
- **Parameters (fixed):** `{"window": 100, "k": 2.0, "gold_k": 0.5, "stop_atr": 1.5, "hold": 3}`

### `cat_dollar_catchup_h1`: Gold catching up with a dollar move

- **Family:** Cross-market. **Timeframe:** H1.
- **Source:** Own idea (gold-dollar lag)
- **Logic:** If the dollar has weakened and gold has not reacted yet, gold may catch up.
- **Entry:** Over 24 H1 bars: long when the dollar index is down more than 0.5% and gold is up less than 0.2%; short mirrored. First bar of each condition.
- **Exit:** Exit after 12 bars.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD H1 bars; dollar index H1 closes. Cross-market: dollaridxusd
- **Parameters (fixed):** `{"n": 24, "dollar_move": 0.005, "gold_max": 0.002, "stop_atr": 2.0, "hold": 12}`

### `cat_dollar_breakout_h4`: Dollar-index channel break

- **Family:** Cross-market. **Timeframe:** H4.
- **Source:** Own idea (trend in the dollar transmitted to gold)
- **Logic:** A new dollar trend is a headwind or tailwind for gold.
- **Entry:** Long gold when the dollar index close crosses below its prior 55-bar low; short when it crosses above its prior 55-bar high.
- **Exit:** Exit after 30 bars.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD H4 bars; dollar index H1 closes. Cross-market: dollaridxusd
- **Parameters (fixed):** `{"n": 55, "stop_atr": 2.0, "hold": 30}`

### `cat_eurusd_confirm_h1`: Gold breakout confirmed by EURUSD

- **Family:** Cross-market. **Timeframe:** H1.
- **Source:** Own idea (dollar confirmation of gold moves)
- **Logic:** A gold breakout backed by a weaker dollar is more likely to be real.
- **Entry:** Long when gold closes above its prior 20-bar high and EURUSD above its prior 20-bar high; short when both break their lows.
- **Exit:** Exit after 24 bars.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD H1 bars; EURUSD H1 closes. Cross-market: eurusd
- **Parameters (fixed):** `{"n": 20, "stop_atr": 2.0, "hold": 24}`

### `cat_usdjpy_riskoff_h1`: Yen-strength risk-off

- **Family:** Cross-market. **Timeframe:** H1.
- **Source:** Own idea (yen and gold as twin havens)
- **Logic:** A sharp yen rally signals risk aversion, which may also lift gold.
- **Entry:** Long gold when USDJPY's 24-bar return crosses below -1%. Long only.
- **Exit:** Exit after 24 bars.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD H1 bars; USDJPY H1 closes. Cross-market: usdjpy
- **Parameters (fixed):** `{"stop_atr": 2.0, "hold": 24}`

### `cat_spx_selloff_d1`: Equity sell-off flight to gold

- **Family:** Cross-market. **Timeframe:** D1.
- **Source:** Baur and Lucey (2010), gold as a safe haven against equities
- **Logic:** When stocks fall hard, money may move into gold.
- **Entry:** Long gold when the S&P 500's 5-day return crosses below -3%. Long only.
- **Exit:** Exit after 5 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD daily bars; S&P 500 CFD H1 closes. Cross-market: usa500idxusd
- **Parameters (fixed):** `{"stop_atr": 3.0, "hold": 5}`

## Batch 5

### `cat_bos_follow_h1`: Break of structure continuation

- **Family:** SMC / ICT structure. **Timeframe:** H1.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication
- **Logic:** A close beyond the last swing in the trend's direction confirms the trend; join it.
- **Entry:** Long on a bullish BOS (close above the last confirmed swing high, trend not down); short on a bearish BOS.
- **Exit:** Target 2 x the stop distance; otherwise exit after 48 bars; an opposite signal reverses.
- **Stop:** Beyond the opposite confirmed swing (swing low for longs), at least 0.5 and at most 4 x ATR(14).
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"target_r": 2.0, "hold": 48}`

### `cat_choch_reversal_h1`: Change of character reversal

- **Family:** SMC / ICT structure. **Timeframe:** H1.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication
- **Logic:** The first close beyond a swing against the trend marks a possible reversal.
- **Entry:** Long on a bullish CHoCH (close above the last swing high while the trend is down); short on a bearish CHoCH.
- **Exit:** Target 2R; otherwise exit after 48 bars.
- **Stop:** Beyond the opposite confirmed swing (swing low for longs), at least 0.5 and at most 4 x ATR(14).
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"target_r": 2.0, "hold": 48}`

### `cat_mss_displacement_h1`: Market-structure shift with displacement

- **Family:** SMC / ICT structure. **Timeframe:** H1.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication
- **Logic:** A structure shift made with a big, fast candle shows real intent behind the reversal.
- **Entry:** As the CHoCH rule, but only when the breaking bar's high-low range exceeds 1.5 x ATR(14).
- **Exit:** Target 2R; otherwise exit after 48 bars.
- **Stop:** Beyond the opposite confirmed swing (swing low for longs), at least 0.5 and at most 4 x ATR(14).
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"displacement": 1.5, "target_r": 2.0, "hold": 48}`

### `cat_htf_bias_bos_h1`: BOS with the daily bias

- **Family:** SMC / ICT structure. **Timeframe:** H1.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication (higher-timeframe bias)
- **Logic:** Trade lower-timeframe structure only with the higher-timeframe trend.
- **Entry:** Long on a bullish H1 BOS when the last completed New York day closed above its 20-day average; short mirrored.
- **Exit:** Target 2R; otherwise exit after 48 bars.
- **Stop:** Beyond the opposite confirmed swing (swing low for longs), at least 0.5 and at most 4 x ATR(14).
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"htf_trend": 20, "target_r": 2.0, "hold": 48}`

### `cat_ny_kz_bos_m15`: BOS in the New York killzone

- **Family:** SMC / ICT structure. **Timeframe:** M15.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication (killzones)
- **Logic:** Structure breaks in the most active US hours, with the daily trend, carry follow-through.
- **Entry:** Long on a bullish M15 BOS with a bar opening 07:00-10:00 New York and an up daily trend (20-day); short mirrored.
- **Exit:** Target 2R; flat from 12:00 New York; at most 20 bars.
- **Stop:** Beyond the opposite confirmed swing (swing low for longs), at least 0.5 and at most 4 x ATR(14).
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"kz": [7, 10], "flat_ny": 12, "htf_trend": 20, "target_r": 2.0, "hold": 20}`

### `cat_choch_ny_kz_m15`: CHoCH in the New York killzone

- **Family:** SMC / ICT structure. **Timeframe:** M15.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication (killzones)
- **Logic:** Reversals that start in the New York killzone often set the day's direction.
- **Entry:** Long on a bullish M15 CHoCH with a bar opening 07:00-10:00 New York; short on a bearish one.
- **Exit:** Target 2R; flat from 12:00 New York; at most 20 bars.
- **Stop:** Beyond the opposite confirmed swing (swing low for longs), at least 0.5 and at most 4 x ATR(14).
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"kz": [7, 10], "flat_ny": 12, "target_r": 2.0, "hold": 20}`

### `cat_fvg_retest_h1`: Fair value gap retest with trend

- **Family:** SMC / ICT structure. **Timeframe:** H1.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication
- **Logic:** Price often returns to an imbalance and continues; enter on the return.
- **Entry:** Up structure: long when the bar's low reaches the nearest active bullish FVG's upper edge and it closes above the gap's lower edge and above its open. Short mirrored.
- **Exit:** Target 2R; otherwise exit after 24 bars.
- **Stop:** Beyond the gap's far edge + 0.1 ATR (0.3-4 ATR).
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"target_r": 2.0, "hold": 24}`

### `cat_fvg_ce_h1`: FVG consequent encroachment

- **Family:** SMC / ICT structure. **Timeframe:** H1.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication ('consequent encroachment')
- **Logic:** The midpoint of a gap is where it is often defended.
- **Entry:** Up structure: long when the low reaches the midpoint of the nearest bullish FVG and the close is above it. Short mirrored.
- **Exit:** Target 2R; otherwise exit after 24 bars.
- **Stop:** Beyond the gap's far edge + 0.1 ATR (0.3-4 ATR).
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"target_r": 2.0, "hold": 24}`

### `cat_ifvg_h1`: Inverse FVG

- **Family:** SMC / ICT structure. **Timeframe:** H1.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication
- **Logic:** When a gap fails and price closes through it, the move tends to continue.
- **Entry:** Long when a bearish FVG is closed through (inverse bullish FVG); short when a bullish FVG is closed through.
- **Exit:** Target 3 x ATR(14); otherwise exit after 24 bars.
- **Stop:** 1.5 x ATR(14).
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"stop_atr": 1.5, "target_atr": 3.0, "hold": 24}`

### `cat_sweep_reversal_h1`: Liquidity sweep reversal

- **Family:** SMC / ICT structure. **Timeframe:** H1.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication ('stop hunt' / 'liquidity grab')
- **Logic:** Stops cluster beyond swings; once taken, price often reverses.
- **Entry:** Long when the low goes below the last swing low and the close is back above it; short mirrored at the swing high.
- **Exit:** Target 2R; otherwise exit after 24 bars.
- **Stop:** Beyond the sweep bar's extreme + 0.1 ATR (0.3-4 ATR).
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"target_r": 2.0, "hold": 24}`

### `cat_sweep_choch_h1`: Sweep followed by CHoCH

- **Family:** SMC / ICT structure. **Timeframe:** H1.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication (sweep plus market-structure shift)
- **Logic:** A stop hunt followed by a structure break against it is a classic reversal set-up.
- **Entry:** Long on a bullish CHoCH if a low sweep happened in the last 6 bars; short on a bearish CHoCH after a high sweep.
- **Exit:** Target 2R; otherwise exit after 36 bars.
- **Stop:** Beyond the lowest low (highest high) of the last 6 bars + 0.1 ATR.
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"within": 6, "target_r": 2.0, "hold": 36}`

### `cat_equal_lows_raid_h1`: Raid on equal highs or lows

- **Family:** SMC / ICT structure. **Timeframe:** H1.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication ('equal highs/lows are liquidity')
- **Logic:** Equal highs or lows attract stops; the raid on them often reverses.
- **Entry:** Long on a low sweep when equal lows (two swing lows within 0.1 ATR) were flagged in the last 10 bars; short on a high sweep after equal highs.
- **Exit:** Target 2R; otherwise exit after 24 bars.
- **Stop:** Beyond the sweep bar's extreme + 0.1 ATR (0.3-4 ATR).
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"need_equal": 10, "target_r": 2.0, "hold": 24}`

### `cat_london_kz_sweep_m15`: Sweep reversal in the London killzone

- **Family:** SMC / ICT structure. **Timeframe:** M15.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication (London killzone)
- **Logic:** London often raids stops left in Asia before the real move.
- **Entry:** Long on an M15 low sweep with a bar opening 02:00-05:00 New York; short on a high sweep.
- **Exit:** Target 2R; flat from 12:00 New York; at most 16 bars.
- **Stop:** Beyond the sweep bar's extreme + 0.1 ATR.
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"kz": [2, 5], "flat_ny": 12, "target_r": 2.0, "hold": 16}`

### `cat_ny_kz_sweep_m15`: Sweep reversal in the New York killzone

- **Family:** SMC / ICT structure. **Timeframe:** M15.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication (New York killzone)
- **Logic:** The New York open often raids London's extremes before reversing.
- **Entry:** Long on an M15 low sweep with a bar opening 07:00-10:00 New York; short on a high sweep.
- **Exit:** Target 2R; flat from 12:00 New York; at most 16 bars.
- **Stop:** Beyond the sweep bar's extreme + 0.1 ATR.
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"kz": [7, 10], "flat_ny": 12, "target_r": 2.0, "hold": 16}`

### `cat_pdh_pdl_kz_m15`: Previous-day high/low raid in the NY killzone

- **Family:** SMC / ICT structure. **Timeframe:** M15.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication
- **Logic:** Yesterday's extremes hold resting orders; a raid on them in the killzone tends to fail.
- **Entry:** First per day: short when an M15 bar opening 07:00-10:00 New York trades above yesterday's high and closes below it; long mirrored at yesterday's low.
- **Exit:** Target 2R; flat from 12:00 New York.
- **Stop:** Beyond the bar's extreme + 0.1 ATR.
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"kz": [7, 10], "flat_ny": 12, "target_r": 2.0}`

### `cat_judas_swing_m15`: Judas swing at the London open

- **Family:** SMC / ICT structure. **Timeframe:** M15.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication ('Judas swing')
- **Logic:** The London open often fakes one way out of the Asian range before the real move the other way.
- **Entry:** Asian range = 20:00-00:00 New York. Between 02:00 and 05:00 New York, first time per day: short when the high breaks the range high and the close is back below it; long mirrored.
- **Exit:** Target = the Asian range width; flat from 12:00 New York.
- **Stop:** Beyond the bar's extreme + 0.1 ATR (0.3-4 ATR).
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"flat_ny": 12}`

### `cat_silver_bullet_m5`: ICT Silver Bullet (10-11 New York)

- **Family:** SMC / ICT structure. **Timeframe:** M5.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication ('Silver Bullet')
- **Logic:** An imbalance created in the 10-11 New York hour is often retested and respected.
- **Entry:** M5, bars opening 10:00-11:00 New York, first per day: long when a bullish FVG formed this hour is retested (low into the gap, close above its lower edge); short mirrored.
- **Exit:** Target 2R; flat from 11:30 New York.
- **Stop:** Beyond the gap's far edge + 0.1 ATR (0.3-4 ATR).
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"target_r": 2.0, "flat_ny": 11.5}`

### `cat_premium_discount_h1`: Buy discount, sell premium

- **Family:** SMC / ICT structure. **Timeframe:** H1.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication (premium and discount arrays)
- **Logic:** Buy cheap within the current range in an uptrend, sell dear in a downtrend.
- **Entry:** Up structure: long on the first bullish bar with premium_discount < 0.25 (bottom quarter of the last swing range); short mirrored above 0.75.
- **Exit:** Target the opposite swing; otherwise exit after 48 bars.
- **Stop:** Beyond the swing low (high) + 0.1 ATR (0.3-4 ATR).
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"discount": 0.25, "hold": 48}`

### `cat_order_block_h1`: Order-block retest

- **Family:** SMC / ICT structure. **Timeframe:** H1.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication ('order blocks')
- **Logic:** The last down candle before a strong up break marks where buyers stepped in; it often holds on a retest.
- **Entry:** At a bullish BOS, the order block is the last bearish candle in the prior 10 bars. Within 24 bars, long when a bar's low enters the block and it closes above the block's high. Short mirrored. A close through the block cancels it.
- **Exit:** Target 2R; otherwise exit after 36 bars.
- **Stop:** Beyond the block's far edge + 0.1 ATR (0.3-4 ATR).
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"lookback": 10, "valid": 24, "target_r": 2.0, "hold": 36}`

### `cat_ote_h1`: Optimal trade entry (62-79% retracement)

- **Family:** SMC / ICT structure. **Timeframe:** H1.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication ('optimal trade entry', Fibonacci 62-79%)
- **Logic:** Deep but not full retracements after a structure break offer good risk-reward entries.
- **Entry:** Within 24 bars of a bullish BOS, in an up structure: long on the first bullish bar whose close is 62-79% of the way down from the swing high to the swing low. Short mirrored.
- **Exit:** Target the swing high (low); otherwise exit after 48 bars.
- **Stop:** Beyond the swing low (high) + 0.1 ATR (0.3-4 ATR).
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"within": 24, "lo": 0.62, "hi": 0.79, "hold": 48}`

### `cat_power_of_three_m15`: Power of three (AMD) at 08:30 New York

- **Family:** SMC / ICT structure. **Timeframe:** M15.
- **Source:** ICT (Michael J. Huddleston) public video teaching; no formal publication ('power of three': accumulation, manipulation, distribution)
- **Logic:** A false early move against the day's open, then a recovery through it, often precedes the day's real move.
- **Entry:** At the bar closing 08:30 New York: long if today's low was more than 2 x ATR(14, M15) below the 00:00 New York open and the close is back above that open (and no equal push above); short mirrored.
- **Exit:** Flat from 16:00 New York.
- **Stop:** Beyond the day's extreme so far + 0.1 ATR (0.5-8 ATR).
- **Data:** XAUUSD bars (mid prices); project structure detectors: swings, BOS/CHoCH, sweeps, FVGs
- **Parameters (fixed):** `{"manip_atr": 2.0, "flat_ny": 16}`

## Batch 6

### `cat_nfp_surprise_proxy_h1`: Payrolls surprise (first release vs its recent median)

- **Family:** Scheduled events. **Timeframe:** H1.
- **Source:** Event studies of metals and macro news: Elder, Miao and Ramchander, 'Impact of Macroeconomic News on Metal Futures', J. Banking & Finance 36(1), 2012, which finds gold falls on stronger-than-expected US activity and inflation news
- **Logic:** Strong jobs data lifts expected rates and the dollar, which weighs on gold; the move is said to continue after the first minutes.
- **Entry:** At the first H1 close after the 08:30 New York release: short if first-release payrolls change minus the median of the previous 12 first releases is above +75k; long if below -75k.
- **Exit:** Exit after 24 bars.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD H1 bars (mid prices); ALFRED first-release vintages (St. Louis Fed), surprise proxy = first release minus median of the previous 12 first releases. Macro: ALFRED_PAYEMS_SURP
- **Parameters (fixed):** `{"threshold": 75.0, "stop_atr": 2.0, "hold": 24}`

### `cat_nfp_surprise_proxy_d1`: Payrolls surprise, daily hold

- **Family:** Scheduled events. **Timeframe:** D1.
- **Source:** Event studies of metals and macro news: Elder, Miao and Ramchander, 'Impact of Macroeconomic News on Metal Futures', J. Banking & Finance 36(1), 2012, which finds gold falls on stronger-than-expected US activity and inflation news
- **Logic:** As the hourly rule, but measured over days, where costs matter less.
- **Entry:** At the close of the release day: short if the payrolls surprise proxy is above +75k; long if below -75k.
- **Exit:** Exit after 5 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD D1 bars (mid prices); ALFRED first-release vintages (St. Louis Fed), surprise proxy = first release minus median of the previous 12 first releases. Macro: ALFRED_PAYEMS_SURP
- **Parameters (fixed):** `{"threshold": 75.0, "stop_atr": 3.0, "hold": 5}`

### `cat_core_cpi_surprise_proxy_h1`: Core CPI surprise (first release vs its recent median)

- **Family:** Scheduled events. **Timeframe:** H1.
- **Source:** Event studies of metals and macro news: Elder, Miao and Ramchander, 'Impact of Macroeconomic News on Metal Futures', J. Banking & Finance 36(1), 2012, which finds gold falls on stronger-than-expected US activity and inflation news
- **Logic:** A hot core inflation print raises expected policy rates, which the cited study finds weighs on gold in the short run.
- **Entry:** At the first H1 close after the 08:30 New York CPI release: short if first-release core CPI m/m minus the median of the previous 12 first releases is above +0.1 points; long if below -0.1.
- **Exit:** Exit after 24 bars.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD H1 bars (mid prices); ALFRED first-release vintages (St. Louis Fed), surprise proxy = first release minus median of the previous 12 first releases. Macro: ALFRED_CORE_SURP
- **Parameters (fixed):** `{"threshold": 0.1, "stop_atr": 2.0, "hold": 24}`

### `cat_payroll_revision_d1`: Payrolls revisions

- **Family:** Scheduled events. **Timeframe:** D1.
- **Source:** Own idea, using first-release vintages: revisions are information the headline number hides
- **Logic:** Revisions show how the labour market really went; a large downward revision is a dovish surprise even when the headline is fine.
- **Entry:** At the close of the release day: long if the previous month's change was revised down by more than 50k; short if revised up by more than 50k.
- **Exit:** Exit after 10 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD D1 bars (mid prices); ALFRED first-release vintages (St. Louis Fed), surprise proxy = first release minus median of the previous 12 first releases. Macro: ALFRED_PAYEMS_REV
- **Parameters (fixed):** `{"threshold": 50.0, "stop_atr": 3.0, "hold": 10}`

### `cat_unrate_jump_d1`: Unemployment-rate jump

- **Family:** Scheduled events. **Timeframe:** D1.
- **Source:** Claudia Sahm's recession rule (2019) as the idea; this is a simpler first-release form, own parameters
- **Logic:** A sudden rise in unemployment brings forward rate cuts, which supports gold.
- **Entry:** At the close of the release day: long if the first-release unemployment rate is at least 0.2 points above the median of the previous 12 first releases. Long only.
- **Exit:** Exit after 20 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD D1 bars (mid prices); ALFRED first-release vintages (St. Louis Fed), surprise proxy = first release minus median of the previous 12 first releases. Macro: ALFRED_UNRATE_SURP
- **Parameters (fixed):** `{"threshold": 0.2, "stop_atr": 3.0, "hold": 20, "long_only": true}`

### `cat_cot_producer_z_d1`: Producer hedgers unusually light (COT)

- **Family:** Positioning (COT). **Timeframe:** D1.
- **Source:** The 'commercials are the smart money' COT reading, e.g. Larry Williams, 'Trade Stocks and Commodities with the Insiders' (2005)
- **Logic:** Producers hedge less when they expect higher prices; their positioning is said to lead.
- **Entry:** Long when the z-score of producer/merchant net position over the last 520 daily bars (about two years) crosses above +1.5; short when it crosses below -1.5.
- **Exit:** Exit after 20 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD D1 bars (mid prices); CFTC disaggregated Commitments of Traders, gold (COMEX), published Friday 15:30 New York for the Tuesday. Macro: COT_PROD_NET
- **Parameters (fixed):** `{"window": 520, "z": 1.5, "stop_atr": 3.0, "hold": 20}`

### `cat_cot_swap_z_d1`: Swap dealers unusually long (COT)

- **Family:** Positioning (COT). **Timeframe:** D1.
- **Source:** Same COT 'commercials' reading applied to swap dealers (bank hedging desks); own parameters
- **Logic:** Swap dealers sit on the commercial side of the market; extreme moves in their book may reflect informed client flow.
- **Entry:** Long when the swap-dealer net position z-score (520 daily bars) crosses above +1.5; short when it crosses below -1.5.
- **Exit:** Exit after 20 days.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD D1 bars (mid prices); CFTC disaggregated Commitments of Traders, gold (COMEX), published Friday 15:30 New York for the Tuesday. Macro: COT_SWAP_NET
- **Parameters (fixed):** `{"window": 520, "z": 1.5, "stop_atr": 3.0, "hold": 20}`

### `cat_cot_other_flow_d1`: Other-reportables flow with the trend (COT)

- **Family:** Positioning (COT). **Timeframe:** D1.
- **Source:** Own idea using the 'other reportables' category of the disaggregated report
- **Logic:** Trend moves backed by fresh positioning are more likely to last.
- **Entry:** Long on the first day the other-reportables net position is higher than 20 daily bars earlier and the close is above SMA(50); short mirrored.
- **Exit:** Exit when either condition no longer holds.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD D1 bars (mid prices); CFTC disaggregated Commitments of Traders, gold (COMEX), published Friday 15:30 New York for the Tuesday. Macro: COT_OTHER_NET
- **Parameters (fixed):** `{"days": 20, "sma": 50, "stop_atr": 3.0}`

### `cat_oi_breakout_d1`: Breakout confirmed by rising open interest

- **Family:** Positioning (COT). **Timeframe:** D1.
- **Source:** Classic volume/open-interest confirmation, e.g. John Murphy, 'Technical Analysis of the Financial Markets' (1999), ch. 7
- **Logic:** A breakout with rising open interest has new money behind it; one with falling open interest is short covering.
- **Entry:** Long when the close is above the prior 20-day high and COT open interest is above its value 20 daily bars earlier; short when the close is below the prior 20-day low with open interest rising.
- **Exit:** Exit long on a close below the prior 10-day low; exit short on a close above the prior 10-day high.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe.
- **Data:** XAUUSD D1 bars (mid prices); CFTC disaggregated Commitments of Traders, gold (COMEX), published Friday 15:30 New York for the Tuesday (open interest is weekly; daily futures open interest is not free). Macro: COT_OI
- **Parameters (fixed):** `{"n": 20, "exit_n": 10, "oi_days": 20, "stop_atr": 3.0}`

## Batch 7

### `cat_macd_zero_d1`: MACD signal cross with zero-line filter (daily bars)

- **Family:** Trend and breakout. **Timeframe:** D1.
- **Source:** Slower version of cat_macd_zero_h4: Gerald Appel, 'Technical Analysis: Power Tools for Active Investors' (2005)
- **Logic:** Buy MACD bullish crosses only when MACD is above zero, and sell bearish crosses only below zero.
- **Entry:** Long when MACD(12,26) crosses above its 9-EMA signal while MACD > 0; short when it crosses below while MACD < 0. (on daily bars)
- **Exit:** Exit long on any bearish signal cross; exit short on any bullish cross.
- **Stop:** 2.5 x the project's Wilder ATR(14) on the signal timeframe. On daily bars.
- **Data:** XAUUSD D1 bars (mid prices)
- **Parameters (fixed):** `{"fast": 12, "slow": 26, "signal": 9, "stop_atr": 2.5}`

### `cat_chandelier_d1`: 20-bar breakout with chandelier exit (daily bars)

- **Family:** Trend and breakout. **Timeframe:** D1.
- **Source:** Slower version of cat_chandelier_h4: Chuck LeBeau's chandelier exit, as described in Alexander Elder, 'Come into My Trading Room' (2002)
- **Logic:** Enter breakouts and let a trailing stop hung from the recent high decide the exit.
- **Entry:** Long when the close is above the prior 20-bar high; short when below the prior 20-bar low. (on daily bars)
- **Exit:** Exit long when the close is below the highest high of the last 22 bars - 3 x ATR(14); short mirrored.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe. On daily bars.
- **Data:** XAUUSD D1 bars (mid prices)
- **Parameters (fixed):** `{"entry_n": 20, "trail_n": 22, "trail_atr": 3.0, "stop_atr": 3.0}`

### `cat_bollinger_breakout_d1`: Bollinger band breakout (daily bars)

- **Family:** Trend and breakout. **Timeframe:** D1.
- **Source:** Slower version of cat_bollinger_breakout_h4: John Bollinger, 'Bollinger on Bollinger Bands' (2001)
- **Logic:** A close outside the bands is treated as the start of a move.
- **Entry:** Long when the close crosses above the upper band (SMA20 + 2 SD); short when it crosses below the lower band. (on daily bars)
- **Exit:** Exit long when the close is below the 20-bar SMA; exit short when above it.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe. On daily bars.
- **Data:** XAUUSD D1 bars (mid prices)
- **Parameters (fixed):** `{"stop_atr": 2.0}`

### `cat_ichimoku_d1`: Ichimoku cloud break with TK alignment (daily bars)

- **Family:** Trend and breakout. **Timeframe:** D1.
- **Source:** Slower version of cat_ichimoku_h4: Goichi Hosoda's Ichimoku Kinko Hyo; standard 9/26/52 settings
- **Logic:** Price above the cloud with the fast line above the slow line is an uptrend; the reverse a downtrend.
- **Entry:** Long on the first bar where the close is above both cloud spans (each shifted 26 bars) and Tenkan(9) > Kijun(26); short mirrored. (on daily bars)
- **Exit:** Exit long when the close is below Kijun; exit short when above.
- **Stop:** 2.5 x the project's Wilder ATR(14) on the signal timeframe. On daily bars.
- **Data:** XAUUSD D1 bars (mid prices)
- **Parameters (fixed):** `{"stop_atr": 2.5}`

### `cat_ema_cross_d1`: EMA 20/50 cross (daily bars)

- **Family:** Trend and breakout. **Timeframe:** D1.
- **Source:** Slower version of cat_ema_cross_h1: Common retail moving-average crossover system
- **Logic:** Follow the hourly 20/50 EMA cross.
- **Entry:** Long when EMA(20) crosses above EMA(50); short when it crosses below. (on daily bars)
- **Exit:** Opposite cross reverses.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe. On daily bars.
- **Data:** XAUUSD D1 bars (mid prices)
- **Parameters (fixed):** `{"kind": "ema", "fast": 20, "slow": 50, "stop_atr": 3.0}`

### `cat_calm_breakout_d1`: Breakout from low ATR percentile (daily bars)

- **Family:** Volatility. **Timeframe:** D1.
- **Source:** Slower version of cat_calm_breakout_h1: Own idea (volatility-regime breakout filter)
- **Logic:** Breakouts out of quiet markets are more likely to start real moves than breakouts in noisy ones.
- **Entry:** If the previous bar's ATR(14) percentile over 500 bars was below 20%: long when the close is above the prior 24-bar high, short below the prior 24-bar low. (on daily bars)
- **Exit:** Exit after 24 bars.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe. On daily bars.
- **Data:** XAUUSD D1 bars (mid prices)
- **Parameters (fixed):** `{"lookback": 500, "calm": 0.2, "n": 24, "stop_atr": 2.0, "hold": 24}`

### `cat_macd_zero_w1`: MACD signal cross with zero-line filter (weekly bars)

- **Family:** Trend and breakout. **Timeframe:** W1.
- **Source:** Slower version of cat_macd_zero_h4: Gerald Appel, 'Technical Analysis: Power Tools for Active Investors' (2005)
- **Logic:** Buy MACD bullish crosses only when MACD is above zero, and sell bearish crosses only below zero.
- **Entry:** Long when MACD(12,26) crosses above its 9-EMA signal while MACD > 0; short when it crosses below while MACD < 0. (on weekly bars)
- **Exit:** Exit long on any bearish signal cross; exit short on any bullish cross.
- **Stop:** 2.5 x the project's Wilder ATR(14) on the signal timeframe. On weekly bars.
- **Data:** XAUUSD W1 bars (mid prices)
- **Parameters (fixed):** `{"fast": 12, "slow": 26, "signal": 9, "stop_atr": 2.5}`

### `cat_chandelier_w1`: 20-bar breakout with chandelier exit (weekly bars)

- **Family:** Trend and breakout. **Timeframe:** W1.
- **Source:** Slower version of cat_chandelier_h4: Chuck LeBeau's chandelier exit, as described in Alexander Elder, 'Come into My Trading Room' (2002)
- **Logic:** Enter breakouts and let a trailing stop hung from the recent high decide the exit.
- **Entry:** Long when the close is above the prior 20-bar high; short when below the prior 20-bar low. (on weekly bars)
- **Exit:** Exit long when the close is below the highest high of the last 22 bars - 3 x ATR(14); short mirrored.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe. On weekly bars.
- **Data:** XAUUSD W1 bars (mid prices)
- **Parameters (fixed):** `{"entry_n": 20, "trail_n": 22, "trail_atr": 3.0, "stop_atr": 3.0}`

### `cat_bollinger_breakout_w1`: Bollinger band breakout (weekly bars)

- **Family:** Trend and breakout. **Timeframe:** W1.
- **Source:** Slower version of cat_bollinger_breakout_h4: John Bollinger, 'Bollinger on Bollinger Bands' (2001)
- **Logic:** A close outside the bands is treated as the start of a move.
- **Entry:** Long when the close crosses above the upper band (SMA20 + 2 SD); short when it crosses below the lower band. (on weekly bars)
- **Exit:** Exit long when the close is below the 20-bar SMA; exit short when above it.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe. On weekly bars.
- **Data:** XAUUSD W1 bars (mid prices)
- **Parameters (fixed):** `{"stop_atr": 2.0}`

### `cat_ichimoku_w1`: Ichimoku cloud break with TK alignment (weekly bars)

- **Family:** Trend and breakout. **Timeframe:** W1.
- **Source:** Slower version of cat_ichimoku_h4: Goichi Hosoda's Ichimoku Kinko Hyo; standard 9/26/52 settings
- **Logic:** Price above the cloud with the fast line above the slow line is an uptrend; the reverse a downtrend.
- **Entry:** Long on the first bar where the close is above both cloud spans (each shifted 26 bars) and Tenkan(9) > Kijun(26); short mirrored. (on weekly bars)
- **Exit:** Exit long when the close is below Kijun; exit short when above.
- **Stop:** 2.5 x the project's Wilder ATR(14) on the signal timeframe. On weekly bars.
- **Data:** XAUUSD W1 bars (mid prices)
- **Parameters (fixed):** `{"stop_atr": 2.5}`

### `cat_ema_cross_w1`: EMA 20/50 cross (weekly bars)

- **Family:** Trend and breakout. **Timeframe:** W1.
- **Source:** Slower version of cat_ema_cross_h1: Common retail moving-average crossover system
- **Logic:** Follow the hourly 20/50 EMA cross.
- **Entry:** Long when EMA(20) crosses above EMA(50); short when it crosses below. (on weekly bars)
- **Exit:** Opposite cross reverses.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe. On weekly bars.
- **Data:** XAUUSD W1 bars (mid prices)
- **Parameters (fixed):** `{"kind": "ema", "fast": 20, "slow": 50, "stop_atr": 3.0}`

### `cat_calm_breakout_w1`: Breakout from low ATR percentile (weekly bars)

- **Family:** Volatility. **Timeframe:** W1.
- **Source:** Slower version of cat_calm_breakout_h1: Own idea (volatility-regime breakout filter)
- **Logic:** Breakouts out of quiet markets are more likely to start real moves than breakouts in noisy ones.
- **Entry:** If the previous bar's ATR(14) percentile over 500 bars was below 20%: long when the close is above the prior 24-bar high, short below the prior 24-bar low. (on weekly bars)
- **Exit:** Exit after 24 bars.
- **Stop:** 2 x the project's Wilder ATR(14) on the signal timeframe. On weekly bars.
- **Data:** XAUUSD W1 bars (mid prices)
- **Parameters (fixed):** `{"lookback": 500, "calm": 0.2, "n": 24, "stop_atr": 2.0, "hold": 24}`

### `cat_real_yield_trend_w1`: Falling real-yield regime with gold uptrend (weekly bars)

- **Family:** Macro. **Timeframe:** W1.
- **Source:** Slower version of cat_real_yield_trend_d1: Erb and Harvey (2013); regime form is own idea
- **Logic:** Hold gold only when both the macro tailwind and the price trend agree.
- **Entry:** Long when DFII10 < its 120-day SMA and the close > SMA(50), on the first day both hold. Long only. (on weekly bars)
- **Exit:** Exit when either condition fails.
- **Stop:** 3 x the project's Wilder ATR(14) on the signal timeframe. On weekly bars.
- **Data:** XAUUSD W1 bars (mid prices); FRED DFII10. Macro: DFII10
- **Parameters (fixed):** `{"ry_sma": 120, "stop_atr": 3.0}`
