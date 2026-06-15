# Troubleshooting

Use this guide when a run fails, produces blank metrics, or returns suspicious coverage.

## No ETF Codes Found

Symptom:

```text
No ETF codes found. Provide --etf or generate the default input first...
```

Cause:

- `scripts/run_pcf_metrics.py` defaults to `lookthrough-hk-all-ranking/all_etf_summary.csv`.
- That file is not bundled in this skill; it is produced by an earlier HK ETF discovery workflow.

Fix:

```powershell
python scripts\run_pcf_metrics.py --etf 513690,159569 --out-dir pcf-metrics-output
```

Or provide a CSV:

```powershell
python scripts\run_pcf_metrics.py --input .\my_etf_list.csv --code-column "ETF代码"
```

## Exchange PCF Download Failed

Likely causes:

- Exchange endpoint is temporarily unavailable.
- Request was rate-limited.
- The ETF code is not listed on the expected exchange.
- Shenzhen report page returned multiple or stale XML names.

Checks:

- Re-run with a small explicit ETF list.
- Increase throttling: `--sleep 0.15`.
- Keep intermediate files: `--keep-intermediates`.
- Confirm the ETF code prefix: Shanghai ETFs usually start with `5`; Shenzhen ETFs usually start with `15` or `16`.

## HK Valuation Is Blank

Likely causes:

- Eastmoney HK F10 has no current PE/PB/dividend-yield row.
- The constituent is suspended, recently listed, or reported with incomplete data.
- The source returned a transient empty table.

Interpretation:

- Blank `股息率%`, `PE`, or `PB` should be read together with coverage columns.
- Low `PE覆盖权重%` or `股息率覆盖权重%` means the aggregate metric is less reliable.

Fix:

- Re-run later or enable alternate valuation checks if available.
- Audit `lookthrough_detail.csv` for `估值错误`.

## A-share Dividend Yield Is Blank

Likely causes:

- No implemented cash dividend in the trailing 12-month ex-date window.
- AkShare dividend endpoint returned no table for that stock.
- The script could not determine a reliable current price.

Interpretation:

- A blank value is not forced to zero unless the source proves no trailing cash dividend.
- Use `股息率来源` in `lookthrough_detail.csv` to distinguish "no implemented dividend" from source failure.

## Return Metrics Are Blank

Likely causes:

- ETF NAV/price history is shorter than the required window.
- Eastmoney NAV failed and price fallback also failed.
- The ETF is newly listed.

Rules:

- Three-year return is blank when history is too short; do not annualize a short window and label it as three years.
- `收益错误` in `metrics_summary.csv` contains serialized source errors for selected ETF portfolio runs.

## Portfolio Weight Sum Is Not Exactly 100

This can be normal.

Reasons:

- PCF baskets include cash, substitutes, or rounding.
- Some ETF stock baskets are slightly above or below 100% due to exchange PCF conventions.
- The selected ETF script reports the actual sum used in `股票权重合计%`.

Audit:

- Check `ETF内股票权重合计%` in `etf_summary.csv`.
- Check `权重来源` in `lookthrough_detail.csv`.

## Suspicious PE/PB Values

Checks:

- Inspect `PE覆盖权重%`, `PB覆盖权重%`, and `负PE权重%`.
- For PE, the aggregate uses earnings-yield aggregation and excludes non-positive PE from the denominator.
- Very low coverage means the metric should not be used as a ranking signal without manual review.

## Generated Files Were Removed

By default, `run_pcf_metrics.py` keeps only:

- `pcf_full_metrics_table.csv`
- `pcf_full_metrics_table.xlsx`

Use this when debugging:

```powershell
python scripts\run_pcf_metrics.py --etf 513690,159569 --keep-intermediates
```

The selected ETF script keeps its detailed outputs by design.

## Rate Limits

Batch runs touch exchange, valuation, FX, and ETF history endpoints. Use conservative throttling:

```powershell
python scripts\run_pcf_metrics.py --etf 513690,159569 --sleep 0.15
python scripts\selected_etf_lookthrough.py --etf 159569,159758 --hk-sleep 0.15 --metrics-workers 4
```

If failures are intermittent, re-run the same command after a few minutes.

