# My playbooks: full test results (2026-11-28)

Rules as confirmed in `docs/my_playbooks.md`, including the 17:00 New York exit. History from 2004 to 2026-11-27, M5
signals, M1 bid/ask fills. Hard rules unchanged:
- out of sample: the catalogue's walk-forward windows from 2007-05;
- holdout: 2024-11-27 onwards, untouched;
- costs: observed and stressed (+$1.07/oz per round trip);
- deflated Sharpe counting all 297 variants now in the trial book (these 4 included);
- the risk-matched long-only gold check.

Sizing for the money figures: 0.5% of equity at risk per trade. "R" results already include the real spread;
"stressed" adds the extra cost. A win is a trade above 0R; breakevens count as not winning.

## Verdict: no edge in any of the four

| | Setup 1 NY sweep + iFVG | Setup 2 London Asian sweep | Setup 3 FVG continuation | Combined, 1 a day |
|---|---:|---:|---:|---:|
| Trades (2004-2026) | 340 | 1,817 | 118 | 2,096 |
| Trades per year | 15 | 79 | 5 | 92 |
| Win rate | 31.8% | 17.8% | 18.6% | 19.0% |
| Breakeven exits | 21% | 24% | 24% | 24% |
| Average R (real spread) | -0.02 | -0.19 | -0.18 | -0.17 |
| Expectancy R, stressed costs | -0.32 | -0.71 | -0.66 | -0.67 |
| Max losing streak | 6 | 18 | 10 | 20 |
| Max drawdown | 25R (12%) | 350R (83%) | 30R (14%) | 384R (86%) |
| Out of sample: trades, avg R | 253, -0.02 | 1,387, -0.13 | 56, -0.23 | 1,584, -0.13 |
| Holdout 2024-11 to 2026-11: trades, avg R (stressed) | 23, +0.25 (+0.16) | 129, -0.03 (-0.19) | 19, -0.13 (-0.25) | 161, +0.02 (-0.13) |
| OOS Sharpe vs long-only gold, same days | -0.06 vs 0.34 | -0.89 vs 0.34 | -0.49 vs 0.50 | -0.97 vs 0.34 |
| Rules failed | OOS positive, deflated Sharpe, beats gold | all but trade count | all but trade count | OOS positive, stressed holdout, deflated Sharpe, beats gold |

**Setup 1** is the only one close to breakeven (10 losing years of 23). It lost slightly over 17 out-of-sample years and made +0.25R a
trade in the 23 holdout trades. That is too few trades to mean much, and it did not beat long-only gold.

**Setup 2** loses in 20 of 23 years. Its median stop is only $2.36, so the real spread takes a large share of each
R before price moves. Under stressed costs it loses 0.71R a trade.

**Setup 3** is rare (5 trades a year; 8 of its 17 out-of-sample windows had too few prior trades to count) and loses
in 16 of 23 years.

**Combined:** about 87% of its trades come from Setup 2, so it behaves like Setup 2.

## By year (trades / average R, real spread)

| Year | Setup 1 | Setup 2 | Setup 3 | Combined |
|---|---|---|---|---|
| 2004 | 17 / -0.30 | 96 / -0.65 | 2 / -1.00 | 110 / -0.62 |
| 2005 | 14 / -0.22 | 75 / -0.70 | 2 / -0.52 | 86 / -0.65 |
| 2006 | 14 / -0.42 | 68 / -0.33 | 2 / +0.53 | 78 / -0.31 |
| 2007 | 14 / -0.42 | 86 / -0.51 | 3 / -0.67 | 94 / -0.48 |
| 2008 | 15 / +0.08 | 89 / -0.07 | 2 / +0.00 | 98 / -0.02 |
| 2009 | 12 / +0.25 | 99 / -0.11 | 3 / +1.35 | 105 / -0.09 |
| 2010 | 14 / +0.04 | 77 / -0.19 | 1 / -1.00 | 85 / -0.15 |
| 2011 | 14 / -0.23 | 88 / -0.09 | 5 / -0.74 | 99 / -0.15 |
| 2012 | 17 / +0.04 | 80 / +0.08 | 6 / -0.22 | 93 / +0.06 |
| 2013 | 14 / -0.27 | 71 / -0.09 | 4 / -0.75 | 82 / -0.11 |
| 2014 | 12 / +0.44 | 73 / -0.06 | 4 / +0.11 | 85 / -0.01 |
| 2015 | 15 / +0.02 | 87 / -0.22 | 7 / -0.34 | 98 / -0.22 |
| 2016 | 20 / +0.59 | 71 / -0.06 | 8 / -0.18 | 91 / -0.02 |
| 2017 | 7 / -0.35 | 79 / -0.08 | 7 / -0.86 | 86 / -0.13 |
| 2018 | 15 / -0.54 | 73 / +0.17 | 5 / -0.20 | 87 / +0.08 |
| 2019 | 15 / +0.02 | 77 / -0.14 | 7 / -0.43 | 90 / -0.15 |
| 2020 | 16 / -0.35 | 65 / -0.35 | 4 / +0.77 | 77 / -0.29 |
| 2021 | 20 / +0.23 | 96 / -0.41 | 3 / -0.76 | 107 / -0.40 |
| 2022 | 16 / -0.61 | 92 / -0.05 | 5 / -1.00 | 105 / -0.14 |
| 2023 | 17 / +0.38 | 87 / -0.10 | 10 / +0.24 | 103 / -0.06 |
| 2024 | 19 / +0.28 | 66 / -0.01 | 9 / +0.63 | 83 / +0.02 |
| 2025 | 10 / +0.51 | 81 / -0.09 | 8 / -0.22 | 92 / -0.02 |
| 2026 | 13 / +0.04 | 41 / +0.05 | 11 / -0.06 | 62 / +0.05 |

Spreads in 2004-2007 were wider than today, which hurts the early years most. Setups 2 and 3 still lose in most
later years. Full reports: `data/research/reports/pb_<setup>_latest.json`. Trades:
`data/research/playbooks/trades_<year>.json`.
