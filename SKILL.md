---
name: a-share-etf-pcf-metrics
description: Generate final A-share ETF PCF look-through metrics tables for Hong Kong/H-share/Hang Seng exposure, including dividend yield, PE, PB, annualized return, Sortino ratio, volatility, half-year/one-year/three-year returns, and HK holding coverage. Use when the user asks to rerun the ETF look-through workflow, update pcf_full_metrics_table.csv/xlsx, rank A-share listed Hong Kong ETFs by dividends or valuation, or reproduce the cleaned final output table without keeping intermediate per-ETF files.
---

# A-share ETF PCF Metrics

## Overview

Use this skill to run the fixed workflow that starts from an ETF code list, performs PCF look-through for A-share listed ETFs, computes valuation and return/risk metrics, and leaves only the final CSV/XLSX tables.

It also includes a standalone selected ETF workflow for user-specified ETF
portfolios. That script can look through A-share and Hong Kong constituents,
then compute ETF-level and portfolio-level dividend yield, PE, PB, return,
volatility, and Sortino metrics without model assistance.

Default final outputs:

- `pcf_full_metrics_table.xlsx`
- `pcf_full_metrics_table.csv`

## Quick Start

From the workspace containing `lookthrough-hk-all-ranking/all_etf_summary.csv`:

```powershell
python C:\Users\81901\.codex\skills\a-share-etf-pcf-metrics\scripts\run_pcf_metrics.py
```

Useful options:

- `--input <csv>`: source ETF list; default is `lookthrough-hk-all-ranking/all_etf_summary.csv`.
- `--code-column <name>`: ETF code column; default is `ETF代码`.
- `--etf 513690,159569`: run explicit ETF codes and ignore the default input CSV.
- `--out-dir <dir>`: output directory; default is `lookthrough-hk-all-ranking-pcf-risk`.
- `--keep-intermediates`: keep per-ETF reports, holdings files, and `summary.csv` for debugging.

Run selected ETF or ETF portfolio look-through:

```powershell
python C:\Users\81901\.codex\skills\a-share-etf-pcf-metrics\scripts\selected_etf_lookthrough.py --etf 159569,159758 --weights 60,40 --out-dir selected-etf-output
```

Selected workflow outputs:

- `metrics_summary.csv`: ETF rows plus one `PORTFOLIO` row.
- `lookthrough_summary.csv`: aggregated underlying stock exposure.
- `lookthrough_detail.csv`: per-ETF underlying holdings with stock PE/PB/dividend yield.
- `etf_summary.csv`: ETF holding-source and metric summary.
- `lookthrough_report.xlsx`: workbook containing all sheets.

## Workflow

1. Read ETF codes from the prior collection table or explicit `--etf` values.
2. Run `scripts/pcf_lookthrough.py` with:
   - `--holdings-source auto`
   - `--alt-limit 0`
   - `--sleep 0`
3. Generate the final Chinese metrics table with `scripts/run_pcf_metrics.py`.
4. Unless debugging, delete intermediate single-ETF CSV/JSON/MD outputs and script summaries.
5. Return links to only the final `.xlsx` and `.csv` files.

## Metrics

The final table includes:

- Dividend yield: look-through weighted constituent dividend yield.
- PE: earnings-yield aggregation PE.
- PB: weighted PB over covered positive PB constituents.
- Annualized return: preferred source from ETF NAV/price history.
- Sortino ratio and volatility: recent daily-return risk metrics.
- Half-year, one-year, and three-year return.
- HK holding weight and holding source for quality control.

Leave three-year return blank when available history is too short; do not label a short window as a three-year return.

## PCF Rules

Read `references/pcf-method.md` when modifying source priority or weight calculations. Critical rules from prior validation:

- Shanghai ETF PCF: use `SUBSTITUTION_CASH_AMOUNT / NAVPERCU`.
- Shenzhen ETF cash-substitute rows: use `CreationCashSubstitute / (1 + PremiumRatio) / NAVperCU`.
- Shenzhen in-kind HK rows with zero substitute cash: use `ComponentShare * HK spot price * HKD/CNY / NAVperCU`.
- For Shenzhen rows with multiple downloadable XML candidates, choose the candidate with the most HK components.

## Dependencies

Install missing packages only if needed:

```powershell
python -m pip install akshare pandas requests openpyxl tabulate
```

Network access is required for exchange PCF, HK valuation, HK spot price, FX rate, and ETF return data.

## Validation

For a quick smoke test, run:

```powershell
python C:\Users\81901\.codex\skills\a-share-etf-pcf-metrics\scripts\run_pcf_metrics.py --etf 513690,159569 --out-dir pcf-metrics-smoke-test
```

Expected: two final files and no intermediate files unless `--keep-intermediates` is set.
