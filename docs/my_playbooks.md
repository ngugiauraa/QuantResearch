# My XAUUSD playbooks: exact definitions

**Status: definitions confirmed 2026-11-28 (with the 17:00 time exit added); examples awaiting confirmation. No full test has been run.** The code is `src/quantresearch/playbooks/`. The 15
examples per setup are on the dashboard's **My Playbooks** page. Live trading stays off and nothing here sends orders.

Prices for patterns are mid prices (average of bid and ask). Fills use the real bid and ask. Times are local and
daylight-saving aware (New York, London); UTC where stated.

## Building blocks

- **Candle colour.** Up-close: close > open. Down-close: close < open.
- **ATR(14) and ADX(14).** Wilder's definitions, on the stated timeframe, using closed bars only.
- **Swing high / low.** A bar whose high is higher than the 2 bars before it and not lower than the 2 bars after
  it (swing low mirrored). It is only known 2 bars later.
- **Structure break.** A bullish break is the first close above the most recent known swing high. A bearish break
  is the first close below the most recent known swing low. Each swing can be broken once.
- **Trend (Setup 3).** On a timeframe, the last two structure breaks point the same way. Long needs both 4H and 1H
  bullish; short needs both bearish.
- **FVG (fair value gap).** Three candles. Bullish: candle 1's high is below candle 3's low. The zone runs from
  candle 1's high to candle 3's low. Bearish is the mirror. The gap exists from candle 3's close.
- **Order block (4H/1H, Setup 1 only).** At a bullish structure break, the leg that broke it starts at the lowest
  low between the broken swing high and the break. The order block is the last down-close candle at or before that
  low, and its zone is that candle's low to high. Bearish is the mirror. It exists from the break candle's close.
- **Unmitigated.** A zone is unmitigated until price trades through its far edge: below the low of a bullish
  zone, or above the high of a bearish one. Touching or entering the zone does not mitigate it, because that
  touch is the reaction the setups look for.
- **CISD (change in state of delivery).** After a high: take the run of consecutive up-close candles that ran
  into the high. The CISD level is the open of the run's first candle, and a candle closing below it is a bearish
  CISD. A bullish CISD after a low is the mirror.
- **iFVG (inversion FVG).** A gap formed during the spike into the extreme (a bullish gap on the way up to a swept
  high), which a later candle's body closes through (closes below its low). If the spike made several, the most
  recent one is used.
- **Wick buffer.** "Beyond the wick" means the wick extreme plus (or minus) 0.1 x ATR(14, M5) at the entry candle.

## Global rules

- **At most one trade per day.** For the combined system, the earliest entry signal of the New York calendar day
  is the only trade. If two arrive on the same candle, the lower setup number wins. Each setup tested alone also
  takes at most one trade a day.
- **Entry chart is M5.** A signal acts at its candle's close and fills at the next M1 open (the ask for buys, the
  bid for sells).
- **Risk and target.** R is the distance from the fill to the stop. Every target is at least 2R, as each setup
  states.
- **Breakeven at 1R.** Once price reaches the fill plus 1R, the stop moves to the fill from the next M1 bar.
- **Choppy filter.** Skip that setup for the day if ADX(14, H1) of the last closed hour before its window starts
  is below 20.
- **News filter.** No entry whose candle closes within 30 minutes either side of an FOMC decision (14:00 New York),
  a payrolls release or a CPI release (both 08:30 New York, on the actual release dates from ALFRED).
- **Spread filter.** No entry when the entry candle's spread is more than 2x its normal level. Normal is the median
  spread of the same 5-minute slot over the previous 20 days.
- **Time exit (added 2026-11-28 at your request).** Any trade still open at 17:00 New York on its entry day is closed at the
  close of the last minute before 17:00 (bid for longs, ask for shorts). Every trade closes the same day.
- **Stops and targets** are checked on M1 bid (longs) or ask (shorts). When one minute touches both, the stop wins.
  A gap through a level fills at the bar's open.

## Setup 1: NY Open Sweep + iFVG (08:30-10:30 New York)

1. **Liquidity levels.**
   - Previous day's high and low (PDH/PDL): the last completed New York day, 17:00 to 17:00.
   - Previous week's high and low (PWH/PWL): the previous Monday-to-Friday New York days.
   - Asian high and low: 00:00-06:00 UTC on that date.
   - London high and low: 08:00 London to 08:30 New York.
2. **Sweep.** An M5 candle in the window trades beyond one of those levels. The level must not have been traded
   beyond earlier that New York day (from 18:00 the evening before), or it would not be liquidity. That same candle
   must overlap an unmitigated HTF zone of the trade's direction, formed in the last 5 days:
   - zones are 1H or 4H FVGs or order blocks;
   - for a long, the zone must be bullish and its midpoint below 50% of the previous day's range (discount);
   - for a short, bearish and above 50% (premium).
3. **V-shape.** The extreme is the furthest point of the move from the sweep candle until the first close back
   past the swept level. That close must come no more than 3 M5 candles after the extreme.
4. **Entry.** The first M5 candle, at or after both the extreme and the close back, whose close goes through the
   spike's iFVG. If the spike left no FVG, the CISD level is used instead.
5. **Stop.** Beyond the sweep wick extreme, up to the entry, plus the buffer.
6. **Target.** The nearest opposing liquidity level (from the step 1 list) beyond the entry, if it is at least 2R
   away. Otherwise 2R.

## Setup 2: London Open Asian-Range Sweep (07:00-09:30 London)

1. **Asian range.** 00:00-06:00 UTC. Skip the day if its width is more than ATR(14) of the last completed UTC day.
2. **Sweep.** The first M5 candle in the window that trades above the Asian high (short) or below the Asian low
   (long).
3. **Confirmation.** From the sweep candle onwards (the sweep candle itself can qualify), the first candle in the
   window that does all three:
   - closes back inside the Asian range, in the trade's direction (a down candle for a short);
   - has a body at least 1.5x the average body of the previous 20 M5 candles;
   - closes through the CISD level or the spike's iFVG (the "CISD/iFVG" in your rule).
4. **Entry.** At that candle's close.
5. **Stop.** Beyond the furthest wick between the sweep and the entry, plus the buffer.
6. **Target.** The opposite side of the Asian range if at least 2R away. Otherwise 2R.

## Setup 3: 15m/1H FVG Trend Continuation (04:00-12:00 New York)

1. **Trend.** Checked at each M5 candle, using the 4H and 1H bars closed by then (see Building blocks).
2. **Pullback.** An M5 candle trades into an unmitigated 15m or 1H FVG of the trend's direction. The FVG must have
   formed in the last 5 days and be in discount for longs (premium for shorts). Discount here is below the
   midpoint of the current 1H leg, from the last known 1H swing low to the last known 1H swing high.
3. **Zone cancelled.** An M5 close through the zone's far edge cancels it.
4. **Confirmation.** After the touch, the first M5 candle that either:
   - closes through the CISD level of the pullback (the run into the pullback's extreme); or
   - completes a fresh M5 FVG in the trend's direction (all three of its candles after the touch).
5. **Entry.** At that candle's close.
6. **Stop.** Beyond the zone's far edge or the pullback's extreme, whichever is further, plus the buffer.
7. **Target.** The most recent known 1H swing high above the entry (swing low below, for shorts). Only if it is at
   least 2R from the fill; otherwise no trade from that signal.

## Choices made, and your answers (2026-11-28)

1. **Time exit.** Added at your request: close at 17:00 New York the same day (see Global rules).
2. **Swing size.** 2 bars on each side. Confirmed.
3. **Discount and premium, Setup 3.** Measured on the current 1H leg (last known 1H swing low to swing high).
   Confirmed.
4. **Zone age, Setup 3.** 15m/1H FVGs from the last 5 days. Confirmed.
5. **London high/low, Setup 1.** 08:00 London to 08:30 New York. Confirmed.
6. **Setup 2 confirmation.** Must also be a CISD or iFVG close. Confirmed.
7. **Overlapping trades.** Settled by the time exit: every trade closes the same day.
8. **Costs.** The real bid and ask spread. The stressed case adds $1.07/oz per round trip ($0.60 spread, $0.20
   slippage each way, $7 per lot commission). Confirmed.
