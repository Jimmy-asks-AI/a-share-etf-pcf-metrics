# PCF look-through method

Use the method below when maintaining or debugging the bundled scripts.

## Holdings source priority

1. Use exchange PCF creation/redemption baskets first.
2. Use quarterly reported holdings only as fallback when PCF is unavailable.
3. Keep the final user-facing output limited to:
   - `pcf_full_metrics_table.xlsx`
   - `pcf_full_metrics_table.csv`

PCF is a creation/redemption basket estimate, not a guarantee of the fund's exact accounting holdings. Preserve `穿透口径`, raw component count, usable component count, unresolved component count, source date, and residual cash/other weight.

## Trading date awareness

- PCF files are exchange-published basket files and may reflect the latest available trading day, not necessarily the calendar date when the script is run.
- Always preserve the PCF period in output columns such as `持仓期`.
- A-share and Hong Kong holidays can differ. When markets are closed or one market is on holiday, the latest PCF/price/valuation source can be stale by one or more calendar days.
- Do not overwrite source dates with the run date; report source windows explicitly.

## Shanghai ETF PCF

- Endpoint: `https://query.sse.com.cn/commonQuery.do`.
- Basic SQL id: `COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_JBXX_C`.
- Component SQL id: `COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_COMPONENT_C`.
- HK marker: `UNDERLYION_SECURITY_ID == "103"`.
- Weight: `SUBSTITUTION_CASH_AMOUNT / NAVPERCU * 100`.
- Do not divide Shanghai `SUBSTITUTION_CASH_AMOUNT` by the premium ratio; validated baskets sum close to NAV.

## Shenzhen ETF PCF

- Report list endpoint: `https://www.szse.cn/api/report/ShowReport/data?CATALOGID=sgshqd`.
- Static XML host: `https://reportdocs.static.szse.cn/files/text/ETFDown/`.
- HK marker: `UnderlyingSecurityIDSource == "103"`.
- If `CreationCashSubstitute > 0`, use:
  `CreationCashSubstitute / (1 + PremiumRatio) / NAVperCU * 100`.
- If HK rows have zero `CreationCashSubstitute`, treat them as in-kind rows and use:
  `ComponentShare * HK spot price * HKD/CNY / NAVperCU * 100`.
- Some SZSE download rows expose multiple XML names. Choose the XML candidate with the most HK components; some candidates contain only the cash row.

## Final metrics table

Sort final rows by look-through dividend yield descending. Include:

- Dividend yield
- PE using earnings-yield aggregation
- PB
- Annualized return
- Sortino ratio
- Volatility
- Half-year return
- One-year return
- Three-year return
- HK holding weight and holdings source for quality control

Leave three-year return blank when the available price/NAV history is too short; do not annualize a short window and label it as three-year performance.

Do not rank rows whose dividend-yield coverage is below 80% by default. Keep them in the final table with `数据状态=覆盖不足`.

For column definitions and null semantics, read `output-schema.md`.
