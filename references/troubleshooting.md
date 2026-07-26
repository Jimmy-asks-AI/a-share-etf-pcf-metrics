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
- Check `PCF原始股票行数`, `有效权重行数`, and `未定价/缺失权重行数` before treating the residual as cash.

## Suspicious PE/PB Values

Checks:

- Inspect `PE覆盖权重%`, `PB覆盖权重%`, and `负PE权重%`.
- For PE, the aggregate uses earnings-yield aggregation and excludes non-positive PE from the denominator.
- Very low coverage means the metric should not be used as a ranking signal without manual review.
- Valuation constraints require at least 80% coverage by default; lower coverage is reported as `数据不足`.
- Direct US-listed ETF constituents reject Yahoo PB values below `0.05` or above `1000` as likely share-class/source mismatches. The original value is retained in `估值错误`.

## US-listed Holdings Sources Are Blocked

The Invesco edge can return `406`, and SEC endpoints can return `403`, depending on IP reputation or temporary traffic controls. The script tries issuer data first where supported and SEC N-PORT next. If every source is blocked, it writes the six core audit files, records each source error, and exits with code `1`; it does not substitute a top-10 table or stale cache.

For Invesco, ticker is resolved through the public product catalog to CUSIP before the holdings request. A `406` on the catalog is therefore an upstream access failure, not evidence that the ticker is invalid.

## Requested ETF Missing From Ranking

Requested ETFs are no longer silently removed. Check `数据状态` and `错误` in the final CSV/XLSX. `有效` requires dividend yield, PE and PB to be present with at least 80% coverage each. `覆盖不足` rows remain visible but do not receive a dividend rank; `失败` rows retain the source error. In `auto` mode, a real A/HK/US parser failure is also retained instead of being hidden by another market's partial result.

Old unversioned cache files are intentionally ignored. Run with `--refresh-cache` to remove them; the flag works even when the current run does not enable `--cache`.

For `us_listed_etf_lookthrough.py`, an all-failed holdings run still writes the six core audit files but exits with code `1`. Read `etf_summary.csv`, `metrics_summary.csv`, and `run_manifest.json` for the per-ticker errors.

If a portfolio has any failed ETF or unresolved component row, holdings-dependent constraints report `数据不足`. The observed exposure remains in the report for diagnosis, but it is not treated as proof that the threshold passed.

## Generated Files Were Removed

By default, `run_pcf_metrics.py` keeps only:

- `pcf_full_metrics_table.csv`
- `pcf_full_metrics_table.xlsx`

Use this when debugging:

```powershell
python scripts\run_pcf_metrics.py --etf 513690,159569 --keep-intermediates
```

The selected ETF script keeps only core CSV/XLSX/manifest files by default. Add `--full-output` for auxiliary CSV/Markdown/HTML files and `--cache` for holdings snapshots.

## Rate Limits

Batch runs touch exchange, valuation, FX, and ETF history endpoints. Use conservative throttling:

```powershell
python scripts\run_pcf_metrics.py --etf 513690,159569 --sleep 0.15
python scripts\selected_etf_lookthrough.py --etf 159569,159758 --hk-sleep 0.15 --metrics-workers 4
```

If failures are intermittent, re-run the same command after a few minutes.
