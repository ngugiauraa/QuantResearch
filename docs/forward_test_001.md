# Forward test 001: the seven catalogue near-misses as one demo portfolio

**Status: approved 2026-11-28, not started. Nothing trades until the start is recorded below. The start requires this
file to be committed with no uncommitted changes; from then on it is binding.**

Pre-registered before any forward data exists. After the start commit, nothing in this file changes except the
"Start" and "Result" sections. If a rule turns out to be unworkable, the test is stopped and a new pre-registration
(forward_test_002) is written; this one is never edited to fit.

## Why

The final catalogue rescore (commit 30bc71c) found nothing promising. Seven slow trend followers passed every
rule except the project-wide deflated Sharpe. A forward test is new, unseen evidence: it cannot be overfitted by
the search, because the data does not exist yet. They are tested together because, individually, none is
strong enough to judge from six months of trades.

## What runs (parameters locked, identical to the catalogue manifest docs/strategy_catalogue.json)

| # | Strategy | Timeframe | Parameters |
|---|---|---|---|
| 1 | `cat_real_yield_trend_d1` | D1 | `{"ry_sma": 120, "stop_atr": 3.0}` |
| 2 | `cat_macd_zero_h4` | H4 | `{"fast": 12, "slow": 26, "signal": 9, "stop_atr": 2.5}` |
| 3 | `cat_chandelier_h4` | H4 | `{"entry_n": 20, "trail_n": 22, "trail_atr": 3.0, "stop_atr": 3.0}` |
| 4 | `cat_bollinger_breakout_h4` | H4 | `{"stop_atr": 2.0}` |
| 5 | `cat_ichimoku_h4` | H4 | `{"stop_atr": 2.5}` |
| 6 | `cat_ema_cross_h1` | H1 | `{"kind": "ema", "fast": 20, "slow": 50, "stop_atr": 3.0}` |
| 7 | `cat_calm_breakout_h1` | H1 | `{"lookback": 500, "calm": 0.2, "n": 24, "stop_atr": 2.0, "hold": 24}` |

The runner refuses to start if any strategy's parameters differ from this table (checked against the
catalogue's parameter lock). The order of the table is also the priority order when two signals arrive at the
same bar close; at each close, all exits are processed before any entry.

## Execution

- Account: the Exness MT5 **demo** account only. Symbol XAUUSDm. Signals are computed from that account's own
  closed bars, at bar close, as in the backtest.
- Every order goes through the existing order router and the independent risk engine, with a separate risk
  state and audit file for this test (data/app/forward_001_*.sqlite) so it cannot touch the dashboard's limits.
- Sizing: 0.25% of equity at risk per trade, from the strategy's own stop distance. The stop is placed at the
  broker with the order, so a position stays protected while the PC is off.
- One position per strategy, as in the backtest. Exits (signal exit, flip, time limit, the chandelier trail) are
  decided at bar close and executed by the runner with a market order, as in the backtest. The broker-side stop
  and target are never moved.
- **Portfolio limit:** open risk in one direction (sum of each open position's distance to its stop, in money)
  may not exceed **1.0% of equity**, i.e. at most four of the seven long, or short, at once. A signal that would
  exceed it is not placed and is logged as `missed: portfolio_cap`.
- Risk engine limits for this test (its own instance; the dashboard's limits are untouched): 0.25% per trade, at
  most 7 open positions, 20 entries per day, 2% daily loss lockout, 10% drawdown stop, spread limit $1.50,
  slippage limit $1.00 from the planned price, at most 2 lots per order and 5 lots open, the existing news
  blackout (15 minutes either side of FOMC decisions). Two settings differ from the dashboard's defaults, because
  the defaults would block these strategies outright and the backtests had no such limits: the stop may be up to
  **10%** of price (default 5%; the D1 strategy's 3 x ATR stop is about 8% of price), and the volatility check
  reads the **H1** ATR(14) for every strategy, against the default 3% of price (the D1 ATR alone is near 3%).
  The money at risk is 0.25% of equity either way. Orders the risk engine rejects are logged as missed signals
  with the reason.
- Starting equity must be at least $120,000, so that the broker's 0.01-lot minimum fits within 0.25% risk for
  the widest stop. With less, the start is refused.

## Hard safety guard

Before every order and every stop modification the runner reads `account_info().trade_mode` from MT5. Orders are
allowed only when it equals `ACCOUNT_TRADE_MODE_DEMO`. A real or contest account, or a failed read, means the order
is refused, an audit entry `forward_test_real_account_refused` is written, the test pauses, and the dashboard shows a
red alert until a person clears it. `QR_LIVE_ENABLED` is not used, read or set by the forward test, and the live
trading path is unchanged. Tests prove a real account is refused.

## What is recorded

Every signal (taken or not), every order request, every fill (price, lots, time), slippage against the reference
price at send time, the spread at entry and exit, commission and swap, every rejection and every missed signal.
Missed signals include: PC or API off at the bar close, MT5 disconnected, risk engine rejection, portfolio cap,
lot size below the broker minimum. An entry signal is only acted on if it is seen within 10% of its bar's length
after the close (6 minutes for H1, 24 for H4, 2.4 hours for D1). Otherwise it is `missed: late` and is not
chased. A due exit is always executed when found, and logged as late if it is late.

## Backtest prediction for the same period

For the comparison, the same seven strategies are replayed with the research simulator over the forward period
on the same MT5 bars, with the backtest's observed cost model, the same 0.25% sizing, the same portfolio cap and
the same priority order, and the same news blackout (the original backtests had none, so the blackout is applied
to the prediction to keep the comparison like for like). The dashboard shows live demo results next to this prediction, trade by trade and as
equity curves.

## Pass or fail (one decision for the portfolio)

Decided once, at the evaluation date: the start date plus six calendar months. If fewer than 30 trades have
closed by then, the test continues until the 30th closed trade or twelve months, whichever comes first, and is
then decided. Interim figures on the dashboard do not change the decision date.

The portfolio **passes only if all four hold**:

1. **Net positive:** the portfolio's net P&L after the demo account's actual spread, commission and swap is above zero.
2. **Beats risk-matched long-only gold:** the portfolio's daily Sharpe is higher than long-only XAUUSD's over the
   same days. This is the same method as the research benchmark (`risk_matched_buy_and_hold`). Gold is held at
   the portfolio's own volatility, so the two are compared at equal risk.
3. **Max drawdown under 10%:** the peak-to-trough fall of the portfolio's equity (closed plus open P&L, sampled
   every bar), measured from the starting equity, is less than 10%.
4. **Costs within the backtest's model:** the mean adverse slippage per fill is at most $0.20 per ounce, and the
   mean spread paid per fill is at most **$1.35** per ounce: the backtest's mean observed spread at its fills
   (bar opens, 2024-10-30 to 2026-10-30: $0.76 H1, $0.69 H4, $1.05 D1; $0.75 taken) plus $0.60. These are the
   allowances of the stressed cost scenario.

After the decision the runner opens no new positions; open ones exit by their own rules and are reported, but do
not change the decision.

Individual strategy results are reported but **cannot pass on their own**. A pass means "worth a longer forward
test with more capital at risk on demo". It does not mean live trading.

## Start

(Filled in at start: date and time UTC, commit hash, starting demo equity.)

## Result

(Filled in once, at the evaluation date.)
