# Strategy catalogue

20 gold (XAUUSD) strategies that are commonly claimed to be automatable, plus some of this project's own ideas. Each is implemented in `src/quantresearch/strategies/catalogue/` and runs through exactly the same research as the 8 engines: walk-forward out-of-sample folds, the untouched two-year holdout, observed and stressed costs, and the same report.

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
