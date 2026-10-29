
# QuantResearch

A local quantitative research platform for systematic market analysis, historical
data quality control, strategy backtesting, and—later—simulated live-market
monitoring and paper trading.

**Initial instruments:** XAUUSD and US100/NASDAQ  
**Primary language:** Python  
**Development environment:** VS Code + Git Bash on Windows

> Research software, not a profit guarantee. Synthetic data is used for software
> testing only. No live orders are placed by the current project.

## Project status

**Current milestone:** Historical CSV ingestion and data-quality auditing.

| Milestone | Status | Evidence |
|---|---|---|
| Local development environment and Git repository | Complete | Python, Git, VS Code, and Docker available |
| Canonical candle schema and synthetic data generator | Complete | `src/quantresearch/market_data.py` |
| Initial market-data tests | Complete | 7 tests passed |
| Historical CSV importer and audit report | In progress | `src/quantresearch/ingestion.py` |
| Importer test suite | In progress | `tests/test_ingestion.py` |
| Real historical dataset validation | Not started | Requires a source CSV |
| Backtesting engine and execution-cost model | Not started | Depends on validated data |
| Interactive research dashboard | Not started | Depends on reliable data and research APIs |
| Kafka streaming pipeline | Not started | Add after the initial research workflow is useful |
| Strategy evaluation and paper trading | Not started | Requires robust out-of-sample validation |

Update this table as work progresses. A milestone should be marked complete
only when its acceptance criteria have been tested.

## Current capabilities

### Market data

The canonical candle schema is:

| Field | Meaning |
|---|---|
| `timestamp` | Candle timestamp in UTC, ISO 8601 format |
| `symbol` | Instrument identifier, currently `XAUUSD` or `US100` |
| `timeframe` | Candle interval; currently `1m` |
| `open` | Opening price |
| `high` | Highest price in the interval |
| `low` | Lowest price in the interval |
| `close` | Closing price |
| `volume` | Provider-reported volume or tick volume |

Synthetic one-minute candles can be generated reproducibly for software tests.
They do not represent real market behavior and must not be used to infer
strategy profitability.

### Historical CSV ingestion

The importer is designed to:

- Recognize common provider column names.
- Normalize timestamps to UTC.
- Validate candle fields and OHLC relationships.
- Report malformed rows, duplicate timestamps, and out-of-order input.
- Detect gaps between consecutive one-minute candles.
- Save normalized data and a JSON audit report separately from the source.

A detected gap is not automatically an error: market closures, trading sessions,
and provider conventions must be considered before classifying it.

## Repository layout

```text
QuantResearch/
├── data/
│   ├── raw/          # Original source datasets; never overwrite originals
│   ├── processed/    # Normalized data and audit reports
│   └── synthetic/    # Generated test data
├── src/
│   └── quantresearch/
│       ├── __init__.py
│       ├── market_data.py
│       └── ingestion.py
├── tests/
│   ├── test_market_data.py
│   └── test_ingestion.py
├── .gitignore
└── README.md
```

Generated datasets and the virtual environment are excluded from Git to keep
the repository small. Preserve original market-data files separately and do
not commit licensed or restricted datasets without permission.

## Setup

Run commands from the repository root in Git Bash.

Activate the existing virtual environment:

```bash
source venv/Scripts/activate
```

The project currently uses Python's standard library for the data generator,
validation, CSV ingestion, and JSON reports. No additional package installation
is required for these components.

## Run tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Tests should pass before a milestone is marked complete.

## Generate synthetic test data

```bash
PYTHONPATH=src python -c "from quantresearch.market_data import *; save_candles(generate_candles('XAUUSD', 500), 'data/synthetic/xauusd_1m.csv'); save_candles(generate_candles('US100', 500), 'data/synthetic/us100_1m.csv')"
```

These files are for testing the pipeline, not evaluating trading strategies.

## Import and audit a historical CSV

1. Put the original provider CSV in `data/raw/`.
2. Confirm its timestamp convention and whether it contains a symbol column.
3. Run the importer, supplying the symbol if needed.

Example:

```bash
PYTHONPATH=src python -m quantresearch.ingestion \
  data/raw/xauusd.csv \
  --symbol XAUUSD \
  --timeframe 1m \
  --assumed-timezone UTC \
  --output data/processed/xauusd_1m.csv \
  --report data/processed/xauusd_audit.json
```

Use `--assumed-timezone Africa/Nairobi` if naive timestamps represent Nairobi
local time. If timestamps already contain timezone offsets, those offsets are
used to convert them to UTC.

Review the audit report before using the normalized file for research. The
importer does not invent candles or automatically repair invalid rows.

## Engineering principles

1. **Data integrity first:** preserve source files and record data-quality issues.
2. **Reproducibility:** deterministic tests, explicit configuration, and logged
   experiment settings.
3. **No look-ahead bias:** strategies must use only information available at the
   time of each simulated decision.
4. **Realistic execution:** account for spread, commissions, slippage, contract
   specifications, and market sessions where relevant.
5. **Out-of-sample validation:** distinguish research, validation, and final
   holdout periods; avoid selecting strategies only on historical fit.
6. **Risk before automation:** build and validate backtesting and paper trading
   before considering live execution.
7. **Evidence-based progress:** report test results and limitations; never treat
   synthetic data or an unvalidated backtest as evidence of profitability.

## Roadmap

1. Finish importer tests and inspect the audit output.
2. Import and validate a real historical dataset for one instrument.
3. Establish dataset metadata, integrity checks, and reproducible storage.
4. Build a backtesting engine with position sizing and realistic execution costs.
5. Add strategy metrics, experiment tracking, and out-of-sample evaluation.
6. Build an interactive dashboard around verified data and research results.
7. Add streaming infrastructure and paper-trading monitoring when justified.
8. Consider machine learning only after a reliable baseline and evaluation
   protocol exist.

## Progress log

### 2026-10-29 — Initial market-data layer

- Created a canonical OHLCV candle schema.
- Implemented reproducible synthetic one-minute candles for XAUUSD and US100.
- Added validation for timestamps, symbols, timeframes, numeric values, and OHLC
  consistency.
- Added 7 initial unit tests; all passed.
- Generated 500 synthetic candles per instrument for pipeline testing.

### 2026-10-29 — Historical ingestion layer

- Implemented CSV column alias mapping and UTC timestamp normalization.
- Added row validation, duplicate detection, input-order auditing, and gap reporting.
- Added normalized CSV and JSON audit report outputs.
- Installed the timezone database dependency required for named timezones.
- Verified all 14 project tests pass.
- Imported 500 synthetic XAUUSD candles: all accepted, with zero detected issues.
- Real historical dataset validation remains pending.

### 2026-10-29 — Session-aware data quality audit

- Added a separate audit module for classifying gaps against regular XAUUSD weekly trading hours.
- Used the America/New_York timezone to account for daylight-saving transitions.
- Classified 23 gaps: 20 expected-closure candidates, 2 unexpected trading gaps, and 1 mixed gap.
- Identified 27 potentially unexpected missing trading minutes.
- Preserved the original normalized dataset; no candles were fabricated.
- Historical holiday schedules and exceptional provider sessions remain unmodelled.
- Added automated tests for session boundaries, timezone handling, and gap classification.