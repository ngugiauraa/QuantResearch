# New evidence, 2026-11-28 (Part B)

Rules unchanged and frozen: fixed parameters, every variant in the trial book (now **293**: 117 engine grid
variants, 128 catalogue and portfolio variants, 48 multi-market components), the same walk-forward split, the
two-year holdout, stressed costs, the project-wide deflated Sharpe above 0.95, and beating risk-matched long-only.
Every strategy was re-scored against that final count.

## Data

| Asked for | Result |
|---|---|
| Economic surprises from first releases | **Got first releases**: ALFRED vintages for payrolls, unemployment, headline and core CPI, 329 releases each, 1999-06 to 2026-11. **No consensus forecasts**: no free, reliable archive exists, so "surprise" is a proxy (first release minus the median of the previous 12 first releases). |
| Gold futures volume and open interest | **Not obtained daily** (CME history is paid; Yahoo has volume only, unofficially; Stooq blocks scripts). Weekly open interest from the CFTC is used instead. |
| Gold futures curve | **Not obtained**: expired contracts' history is not free. |
| Positioning (COT) | **Extended** to 14 series (all trader categories, long/short legs, trader counts, concentration), 1,067 weekly reports from 2006. |

## Results

**Nothing passed every rule.** 124 catalogue strategies: 0 promising, 36 weak, 58 no edge, 30 insufficient evidence.

- **New-data strategies (batch 6, 9):** no edge. Payrolls surprise lost money out of sample (H1 Sharpe -0.14,
  D1 -0.69); COT other-reportables flow -0.11 over 323 trades; open-interest-confirmed breakout +0.18 over 104
  trades but fails the deflated Sharpe and the buy-and-hold check. Five event/positioning rules trade too rarely to
  be judged (see the protocol note below).
- **Slower bars (batch 7, 13):** D1 versions of MACD (Sharpe 0.43), chandelier (0.34) and Ichimoku (0.20) pass
  everything except the deflated Sharpe, like their H1/H4 originals. Bollinger and EMA cross on D1 also fail the
  buy-and-hold check. **All seven W1 versions are unjudgeable** under the shared protocol.
- **Multi-market trend portfolio (12 MT5 markets, 4 variants):** out-of-sample Sharpe -0.10 to -0.25 (2014-2024;
  gold only until 2019, then about ten markets), positive in the 2024-2026 holdout, never beats long-only at the
  same risk in both periods. Per market, only EURUSD, AUDUSD, USDCHF and NZDUSD beat their own long-only in both
  periods for the 252-day momentum variant, at Sharpes near zero. WTI and the S&P 500 only have MT5 history from
  2023-06, so they appear in the holdout only.
- **Ten strategies fail only the deflated Sharpe:** MACD H4 (OOS Sharpe 0.77), chandelier H4 (0.45), MACD D1
  (0.43), real-yield trend D1 (0.42), EMA cross H1 (0.41), calm breakout H1 (0.40), Bollinger H4 (0.39),
  chandelier D1 (0.34), Ichimoku H4 (0.32), Ichimoku D1 (0.20). The best-by-luck bar for 293 trials is about 2.7
  annualised Sharpe, far above any of them. Seven of them are in forward test 001.

## Protocol note (not changed)

The shared walk-forward needs 20 trades in each 4-year training window before a fold counts. Weekly trend rules
and monthly-event rules make far fewer, so every fold is skipped and they show "insufficient evidence". Judging
them would need a longer training window, which is a protocol change and has not been made.

## Discovery rerun (B4)

The first run crashed at 9.4 h of 48 when the disk filled. It was resumed to a 12-hour cap (3 workers, below-normal
priority). A second 12-hour run with seed 20261128 then starts automatically, and `discovery/compare.py` writes
`data/research/reports/discovery_seed_check_main_seed2.json`: how many of the first run's best 10 and best 50
candidates reappear (identical rule, identical trades, same rule shape, same features).
