---
name: a-share-etf-pcf-metrics
description: Generate deterministic A-share listed ETF PCF look-through metric tables, including dividend yield, PE, PB, return/risk metrics, holding structure, industry/theme exposure, valuation buckets, ETF overlap, PCF quality checks, cache manifests, and portfolio constraint checks. Use for Hong Kong/H-share/Hang Seng ETF ranking, A-share dividend ETF look-through, US-stock ETF look-through, or selected ETF portfolio look-through without model-based calculation.
---

# A-share ETF PCF Metrics

## Overview

Use this skill when the user wants deterministic PCF look-through calculations for A-share listed ETFs. The bundled scripts read exchange PCF baskets, compute constituent-level valuation/dividend data, and aggregate ETF or portfolio metrics for A-share, Hong Kong, and US-stock underlyings when exposed by SSE/SZSE PCF files. The selected ETF workflow also produces audit tables for concentration, market/board split, industry/theme exposure, valuation buckets, risk diagnostics, overlap, PCF quality, cross-source coverage, and constraint checks.

Three workflows are supported:

- Batch HK or US ETF ranking: `scripts/run_pcf_metrics.py`
- Selected ETF or ETF portfolio look-through: `scripts/selected_etf_lookthrough.py`
- US-listed ETF ticker look-through: `scripts/us_listed_etf_lookthrough.py`

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

## Batch US ETF Ranking

Run explicit US-stock/QDII ETF codes:

```powershell
python "$SKILL_DIR\scripts\run_pcf_metrics.py" --market us --etf 513100,513500 --out-dir lookthrough-us-pcf-risk
```

Default final outputs are still `pcf_full_metrics_table.xlsx` and `pcf_full_metrics_table.csv`. Use `--keep-intermediates` to retain selected-workflow diagnostics.

## US-listed ETF Ticker Look-through

Use this workflow when the ETF itself is listed in the US, for example `QQQ.US` or `DRAM.US`. Do not pass these tickers to `selected_etf_lookthrough.py --etf`; that command expects A-share listed six-digit ETF codes.

```powershell
python "$SKILL_DIR\scripts\us_listed_etf_lookthrough.py" --ticker QQQ.US --out-dir us-etf-output
python "$SKILL_DIR\scripts\us_listed_etf_lookthrough.py" --ticker QQQ.US,DRAM.US --weights 50,50 --out-dir us-etf-portfolio-output
python "$SKILL_DIR\scripts\us_listed_etf_lookthrough.py" --ticker QQQ.US --skip-metrics --out-dir us-qqq-holdings
python "$SKILL_DIR\scripts\us_listed_etf_lookthrough.py" --ticker QQQ.US,DRAM.US --full-output --out-dir us-etf-full-audit
```

Compact default outputs: `lookthrough_detail.csv`, `lookthrough_summary.csv`, `etf_summary.csv`, `metrics_summary.csv`, `lookthrough_report.xlsx`, and `run_manifest.json`. Use `--full-output` only when auxiliary CSV, Markdown, HTML, and extra audit files are needed.

Current source behavior:

- US-listed ETF ticker resolution first uses the SEC mutual fund/class ticker map (`company_tickers_mf.json`), then the SEC exchange ticker map, so it is not limited to hand-coded NYSE Arca/Nasdaq/Cboe examples.
- SEC NPORT fallback provides full holdings when available; NPORT is complete but delayed.
- Roundhill ETFs such as `DRAM` use issuer daily holdings CSV files when available.
- `QQQ` can use SEC NPORT as a full-holdings fallback when the Invesco API blocks script requests.
- Constituent PE/PB, price, and dividend yield use Yahoo quoteSummary with crumb authentication when available.
- Sector and industry use Nasdaq quote endpoints when available.
- ETF return/risk metrics use Nasdaq chart data when available; portfolio risk is recomputed from the combined ETF price curves.
- Default mode does not write cache files. Add `--cache` to store holdings, constituent metrics, and ETF price histories under `out-dir/cache`; use `--refresh-cache` to clear cache files before a cached run.
- `--lookback-days` is accepted as an alias for `--nav-lookback-days`.

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
- `classified_detail.csv`: detail rows with board, rule-industry, and theme classification.
- `structure_analysis.csv`: holding count, top 5/10/20 concentration, max stock, and cash/other estimate.
- `market_board_exposure.csv`: A/HK/cash and A-share board exposure.
- `industry_theme_exposure.csv`: rule-based SW/CITIC placeholder, rule industry, and theme exposure.
- `valuation_buckets.csv`: PE/PB/dividend-yield bucket exposure.
- `profit_quality.csv` and `dividend_quality.csv`: quality summaries with explicit coverage/source notes.
- `risk_analysis.csv`: return-risk metrics plus unavailable-field diagnostics.
- `overlap_pairs.csv` and `common_holdings.csv`: ETF pair overlap and duplicated holdings.
- `pcf_quality.csv`, `cross_validation.csv`, `constraint_checks.csv`, `historical_tracking.csv`.
- `lookthrough_report.xlsx`: workbook containing the base sheets and enhanced sheets.
- `enhanced_report.md`, `enhanced_report.html`, `run_summary.json`, `run_manifest.json`.

`--weights` accepts percentages or decimals and is normalized internally. If omitted, ETFs are equal weighted. `--markets` can force `auto`, `hk`, `a`, `us`, or `us_listed`; use `auto` unless the parser chooses the wrong market. Tickers ending in `.US` auto-select `us_listed`.

Run a US ETF:

```powershell
python "$SKILL_DIR\scripts\selected_etf_lookthrough.py" --etf 513100 --markets us --out-dir selected-us-etf-output
```

Run a mixed A/H/US portfolio:

```powershell
python "$SKILL_DIR\scripts\selected_etf_lookthrough.py" --etf 510880,159569,513100 --markets a,hk,us --weights 40,30,30 --out-dir selected-global-etf-output
python "$SKILL_DIR\scripts\selected_etf_lookthrough.py" --etf 510880,159569,513100,QQQ.US --markets a,hk,us,us_listed --weights 25,25,25,25 --out-dir selected-global-us-listed-output
```

Useful selected-workflow options:

- `--max-stock-weight 7`: flag single-stock look-through exposure above 7%.
- `--max-industry-weight`, `--min-dividend-yield`, `--max-pe`, `--max-pb`, `--max-drawdown`, `--target-a-weight`, `--target-hk-weight`, `--target-us-weight`: portfolio constraint checks.
- `--us-script`: override the US-stock PCF helper path.
- `--cache` / `--no-cache`: write or skip the current holdings snapshot under `cache/`; cache is on by default.
- `--refresh-cache`: remove old `holdings_*.csv` snapshots in the selected output cache before the run.
- `--compare`, `--portfolio`, `--industry`, `--theme`, `--risk`, `--html-report`: accepted semantic flags; the corresponding enhanced outputs are generated by default.

## Metric Rules

- Dividend yield: weighted look-through constituent dividend yield.
- PE: earnings-yield aggregation (`sum(weight) / sum(weight / PE)`), ignoring non-positive PE in the denominator and reporting negative PE coverage.
- PB: weighted average over covered positive PB constituents.
- ETF annualized return, volatility, Sortino, max drawdown, Sharpe, Calmar, VaR/CVaR, and downside volatility: preferred ETF NAV/price history source.
- Portfolio return/risk metrics: calculated from the combined ETF NAV/price curve, not from weighted precomputed ETF metrics.
- Three-year return stays blank when available history is too short.
- Industry/theme fields are rule estimates unless an official external mapping is supplied later; the output marks this data-source status explicitly.
- US tickers are uppercased but not zero-filled; tickers such as `AAPL`, `MSFT`, `NVDA`, `BRK.B`, and `BRK-B` stay ticker-shaped.
- A-share-listed US/QDII PE/PB/dividend yield are best-effort AkShare lookups. Direct US-listed ETF constituent PE/PB/price/dividend fields use Yahoo quoteSummary when available, with Nasdaq for sector/industry fallback.

## PCF Rules

Read `references/pcf-method.md` before changing source priority or weight formulas. Critical rules:

- Shanghai ETF PCF: `SUBSTITUTION_CASH_AMOUNT / NAVPERCU`.
- Shenzhen cash-substitute rows: `CreationCashSubstitute / (1 + PremiumRatio) / NAVperCU`.
- Shenzhen in-kind HK rows with zero substitute cash: `ComponentShare * HK spot price * HKD/CNY / NAVperCU`.
- US rows prefer PCF cash substitute amount divided by `NAVperCU`; when only quantity exists, weight may be estimated with `ComponentShare * US latest price * USD/CNY / NAVperCU` and that formula is recorded in `权重来源`.
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

Network access is required for exchange PCF files, stock valuation/dividend data, HK/US spot prices, HKD/CNY and USD/CNY FX quotes, and ETF return histories.

## Validation

Fast offline checks:

```powershell
python -m py_compile "$SKILL_DIR\scripts\pcf_common.py" "$SKILL_DIR\scripts\pcf_enhanced_analytics.py" "$SKILL_DIR\scripts\run_pcf_metrics.py" "$SKILL_DIR\scripts\pcf_lookthrough.py" "$SKILL_DIR\scripts\run_a_share_dividend_etf_pcf_metrics.py" "$SKILL_DIR\scripts\selected_etf_lookthrough.py" "$SKILL_DIR\scripts\us_etf_lookthrough.py"
python -m py_compile "$SKILL_DIR\scripts\us_listed_etf_lookthrough.py"
python -m unittest discover -s "$SKILL_DIR\tests"
```

Network smoke test:

```powershell
python "$SKILL_DIR\scripts\run_pcf_metrics.py" --etf 513690,159569 --out-dir pcf-metrics-smoke-test
python "$SKILL_DIR\scripts\selected_etf_lookthrough.py" --etf 513100 --markets us --skip-metrics --out-dir selected-us-smoke-test
python "$SKILL_DIR\scripts\us_listed_etf_lookthrough.py" --ticker QQQ.US,DRAM.US --out-dir us-listed-smoke-test
```

Expected batch result: final `.csv` and `.xlsx`; intermediate files only when `--keep-intermediates` is set.
