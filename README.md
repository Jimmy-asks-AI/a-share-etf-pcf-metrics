# A-share ETF PCF Metrics Skill

Codex/Claude skill for generating final PCF look-through metric tables for A-share listed ETFs with Hong Kong, H-share, Hang Seng, or Stock Connect exposure.

The workflow starts from an ETF code list, looks through exchange PCF creation/redemption baskets, computes valuation and return/risk metrics, and leaves only two final files by default:

- `pcf_full_metrics_table.xlsx`
- `pcf_full_metrics_table.csv`

## What It Computes

The final table includes:

- Look-through dividend yield
- PE, using earnings-yield aggregation
- PB
- Annualized return
- Sortino ratio
- Volatility
- Half-year return
- One-year return
- Three-year return
- HK holding weight and PCF source for quality control

## PCF Method

- Shanghai ETF PCF: `SUBSTITUTION_CASH_AMOUNT / NAVPERCU`
- Shenzhen cash-substitute PCF rows: `CreationCashSubstitute / (1 + PremiumRatio) / NAVperCU`
- Shenzhen in-kind HK rows with zero substitute cash: `ComponentShare * HK spot price * HKD/CNY / NAVperCU`
- Shenzhen rows with multiple XML candidates: choose the file with the most HK components

See [`references/pcf-method.md`](references/pcf-method.md) for the detailed source and validation notes.

## Usage

From the workspace containing `lookthrough-hk-all-ranking/all_etf_summary.csv`:

```powershell
python C:\Users\81901\.codex\skills\a-share-etf-pcf-metrics\scripts\run_pcf_metrics.py
```

Run explicit ETF codes:

```powershell
python C:\Users\81901\.codex\skills\a-share-etf-pcf-metrics\scripts\run_pcf_metrics.py --etf 513690,159569 --out-dir pcf-metrics-output
```

Use a custom ETF list:

```powershell
python C:\Users\81901\.codex\skills\a-share-etf-pcf-metrics\scripts\run_pcf_metrics.py --input .\my_etf_list.csv --code-column ETF代码 --out-dir pcf-metrics-output
```

Keep intermediate per-ETF reports for debugging:

```powershell
python C:\Users\81901\.codex\skills\a-share-etf-pcf-metrics\scripts\run_pcf_metrics.py --keep-intermediates
```

Run selected ETFs or an ETF portfolio:

```powershell
python C:\Users\81901\.codex\skills\a-share-etf-pcf-metrics\scripts\selected_etf_lookthrough.py --etf 159569,159758 --weights 60,40 --out-dir selected-etf-output
```

This standalone script supports both single ETF and weighted portfolio
look-through. It exports underlying stock weights, stock-level PE/PB/dividend
yield, ETF-level metrics, and a `PORTFOLIO` row whose valuation metrics are
aggregated from underlying stock weights. Portfolio return, volatility, and
Sortino are calculated from the combined ETF NAV/price history instead of from
weighted precomputed ETF table values.

Selected workflow outputs:

- `metrics_summary.csv`
- `lookthrough_summary.csv`
- `lookthrough_detail.csv`
- `etf_summary.csv`
- `lookthrough_report.xlsx`

## Example Output

Excerpt from `pcf_full_metrics_table.csv`:

|   排名_按股息率 |   ETF代码 | ETF名称         | 持仓来源     |   港股持仓权重% |   股息率% |   PE |   PB |
|----------:|--------:|:--------------|:---------|----------:|-------:|-----:|-----:|
|         1 |  159569 | 港股红利低波ETF景顺   | szse_pcf |    100    |   6.36 | 8.9  | 1.14 |
|         2 |  513830 | 港股通高股息ETF嘉实   | sse_pcf  |    100.02 |   6.09 | 8.63 | 1.27 |
|         3 |  520810 | XD港股通红利ETF易方达 | sse_pcf  |     99.4  |   6.09 | 8.63 | 1.27 |
|         4 |  513530 | 港股通红利ETF华泰柏瑞  | sse_pcf  |    100    |   6.09 | 8.63 | 1.27 |
|         5 |  513820 | 港股通红利ETF汇添富   | sse_pcf  |    100    |   6.09 | 8.63 | 1.27 |
|         6 |  159302 | 港股高股息ETF银华    | szse_pcf |     98.87 |   6.08 | 8.63 | 1.27 |

## Dependencies

Install missing packages only when needed:

```powershell
python -m pip install akshare pandas requests openpyxl tabulate
```

Network access is required for exchange PCF files, HK valuation data, HK spot prices, HKD/CNY FX quotes, and ETF return histories.

## Skill Invocation

After installing the folder into `~/.codex/skills` or `~/.claude/skills`, invoke it naturally:

```text
用 a-share-etf-pcf-metrics 重新生成 ETF PCF 穿透指标表
```
