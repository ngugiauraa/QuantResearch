# Strategy catalogue

41 gold (XAUUSD) strategies that are commonly claimed to be automatable, plus some of this project's own ideas. Each is implemented in `src/quantresearch/strategies/catalogue/` and runs through exactly the same research as the 8 engines: walk-forward out-of-sample folds, the untouched two-year holdout, observed and stressed costs, and the same report.

**Hard rules.** Parameters are fixed as written here and are never tuned (each strategy has one variant; the first run locks it). Every run is recorded in the experiment ledger and the trial book, and nothing is deleted. The deflated Sharpe that decides 'promising' counts every variant ever tested by the engines and the catalogue. A strategy is only promising if it has at least 30 out-of-sample trades, is positive out of sample, positive in the holdout, positive in the holdout under stressed costs, and has a project-wide deflated Sharpe above 0.95.

**Conventions.** Signals act at a bar's close and fill on a later event, so 'breaks above X' means 'closes above X'. ATR is Wilder ATR(14) on the strategy's timeframe. Every strategy has a stop because positions are sized from it (0.5% risk). Times are local market time with daylight saving. 'Needs data we don't have' strategies are implemented but cannot trade until that data exists.

Regenerate with `python -m quantresearch.strategies.catalogue.docs`. Run with `python -m quantresearch.research.catalogue --all` or from the dashboard's Strategy Catalogue table.

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
