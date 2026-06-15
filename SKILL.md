---
name: a-share-etf-pcf-metrics
description: Generate A-share listed ETF PCF look-through metric tables, including dividend yield, PE, PB, annualized return, Sortino ratio, volatility, half-year/one-year/three-year returns, and holding coverage. Use for Hong Kong/H-share/Hang Seng ETF ranking, A-share dividend ETF look-through, or selected ETF portfolio look-through without model-based calculation.
---

# A-share ETF PCF Metrics

## Overview

Use this skill when the user wants deterministic PCF look-through calculations for A-share listed ETFs. The bundled scripts read exchange PCF baskets, compute constituent-level valuation/dividend data, and aggregate ETF or portfolio metrics.

Two workflows are supported:

- Batch HK ETF ranking: `scripts/run_pcf_metrics.py`
- Selected ETF or ETF portfolio look-through: `scripts/selected_etf_lookthrough.py`

Resolve the skill directory first. `$SKILL_DIR` means the directory containing this `SKILL.md`; if the current directory is the skill/repo directory, use:

```powershell
$SKILL_DIR = Resolve-Path .
```

## Batch HK ETF Ranking

Run explicit ETF codes:

```powershell
python "$SKILL_DIR\scripts\run_pcf_metrics.py" --etf 513690,159569 --out-dir pcf-metrics-output
```

Run from a CSV list:

```powershell
python "$SKILL_DIR\scripts\run_pcf_metrics.py" --input ".\my_etf_list.csv" --code-column "ETF代码" --out-dir pcf-metrics-output
```

The default input is `lookthrough-hk-all-ranking/all_etf_summary.csv`. That file is not bundled; it must already exist from the earlier HK ETF discovery workflow. If it is missing, pass explicit `--etf` codes or generate the discovery CSV first.

Default final outputs:

- `pcf_full_metrics_table.xlsx`
- `pcf_full_metrics_table.csv`

By default, intermediate per-ETF CSV/JSON/MD files are removed. Use `--keep-intermediates` while debugging, auditing, or validating source data.

## Selected ETF Portfolio

Run a single ETF:

```powershell
python "$SKILL_DIR\scripts\selected_etf_lookthrough.py" --etf 159569 --out-dir selected-etf-output
```

Run a weighted ETF portfolio:

```powershell
python "$SKILL_DIR\scripts\selected_etf_lookthrough.py" --etf 159569,159758 --weights 60,40 --out-dir selected-etf-output
```

Selected workflow outputs:

- `metrics_summary.csv`: ETF rows plus one `PORTFOLIO` row.
- `lookthrough_summary.csv`: aggregated underlying stock exposure.
- `lookthrough_detail.csv`: per-ETF underlying holdings with stock PE/PB/dividend yield.
- `etf_summary.csv`: ETF holding-source and metric summary.
- `lookthrough_report.xlsx`: workbook containing all sheets.

`--weights` accepts percentages or decimals and is normalized internally. If omitted, ETFs are equal weighted. `--markets` can force `auto`, `hk`, or `a`; use `auto` unless the parser chooses the wrong market.

## Metric Rules

- Dividend yield: weighted look-through constituent dividend yield.
- PE: earnings-yield aggregation (`sum(weight) / sum(weight / PE)`), ignoring non-positive PE in the denominator and reporting negative PE coverage.
- PB: weighted average over covered positive PB constituents.
- ETF annualized return, volatility, and Sortino: preferred ETF NAV/price history source.
- Portfolio annualized return, volatility, and Sortino: calculated from the combined ETF NAV/price curve, not from weighted precomputed ETF metrics.
- Three-year return stays blank when available history is too short.

## PCF Rules

Read `references/pcf-method.md` before changing source priority or weight formulas. Critical rules:

- Shanghai ETF PCF: `SUBSTITUTION_CASH_AMOUNT / NAVPERCU`.
- Shenzhen cash-substitute rows: `CreationCashSubstitute / (1 + PremiumRatio) / NAVperCU`.
- Shenzhen in-kind HK rows with zero substitute cash: `ComponentShare * HK spot price * HKD/CNY / NAVperCU`.
- When a Shenzhen download row exposes multiple XML candidates, choose the XML with the most relevant stock components.

## References

Load only the file needed for the current task:

- `references/pcf-method.md`: data source priority and PCF formulas.
- `references/output-schema.md`: column definitions, units, and null semantics.
- `references/troubleshooting.md`: network/data-source failures and diagnostics.

## Dependencies

Install missing packages only when needed:

```powershell
python -m pip install -r "$SKILL_DIR\requirements.txt"
```

Network access is required for exchange PCF files, stock valuation/dividend data, HK spot prices, HKD/CNY FX quotes, and ETF return histories.

## Validation

Fast offline checks:

```powershell
python -m py_compile "$SKILL_DIR\scripts\run_pcf_metrics.py" "$SKILL_DIR\scripts\pcf_lookthrough.py" "$SKILL_DIR\scripts\run_a_share_dividend_etf_pcf_metrics.py" "$SKILL_DIR\scripts\selected_etf_lookthrough.py"
python -m unittest discover -s "$SKILL_DIR\tests"
```

Network smoke test:

```powershell
python "$SKILL_DIR\scripts\run_pcf_metrics.py" --etf 513690,159569 --out-dir pcf-metrics-smoke-test
```

Expected batch result: final `.csv` and `.xlsx`; intermediate files only when `--keep-intermediates` is set.
