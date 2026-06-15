# A 股 ETF PCF 穿透指标 Skill

这个仓库提供一组纯脚本和 Codex/Claude skill，用于对 A 股上市 ETF 做 PCF 底层穿透，并计算股息率、PE、PB、收益、波动率、Sortino Ratio、回撤、持仓重合、行业主题暴露和组合约束等指标。

脚本优先读取交易所完整 PCF 申赎清单，而不是只使用前十大持仓。

## 工作流

### 批量港股 ETF 排名

```powershell
# 请在仓库目录或已安装的 skill 目录中运行。
$SKILL_DIR = Resolve-Path .
python "$SKILL_DIR\scripts\run_pcf_metrics.py" --etf 513690,159569 --out-dir pcf-metrics-output
```

最终输出：

- `pcf_full_metrics_table.csv`
- `pcf_full_metrics_table.xlsx`

也可以从自定义 ETF 清单读取：

```powershell
python "$SKILL_DIR\scripts\run_pcf_metrics.py" --input ".\my_etf_list.csv" --code-column "ETF代码"
```

默认输入路径是 `lookthrough-hk-all-ranking/all_etf_summary.csv`。这个文件不会随 skill 自带，必须由前置 ETF 发现流程生成；如果没有这个文件，请直接使用 `--etf` 指定 ETF 代码。

### 自选 ETF 或 ETF 组合穿透

```powershell
python "$SKILL_DIR\scripts\selected_etf_lookthrough.py" --etf 159569,159758 --weights 60,40 --out-dir selected-etf-output
```

输出文件：

- `metrics_summary.csv`：每只 ETF 一行，另有一行 `PORTFOLIO` 组合指标。
- `lookthrough_summary.csv`：组合穿透后的底层股票汇总。
- `lookthrough_detail.csv`：每只 ETF 的底层持仓明细，并包含股票 PE、PB、股息率。
- `etf_summary.csv`：ETF PCF 来源、持仓期和指标汇总。
- `classified_detail.csv`：带板块、规则行业、主题分类的持仓明细。
- `structure_analysis.csv`：持仓数量、前 5/10/20 集中度、最大个股和现金/其他估算。
- `market_board_exposure.csv`：A 股/港股/现金及 A 股板块暴露。
- `industry_theme_exposure.csv`：规则行业、主题，以及显式标注为估计的申万/中信占位分类。
- `valuation_buckets.csv`：PE、PB、股息率分层。
- `profit_quality.csv`、`dividend_quality.csv`：盈利质量和分红质量摘要，含覆盖率和数据源说明。
- `risk_analysis.csv`：收益风险指标及不可得原因。
- `overlap_pairs.csv`、`common_holdings.csv`：ETF 两两重合度和共同持仓。
- `pcf_quality.csv`、`cross_validation.csv`、`constraint_checks.csv`、`historical_tracking.csv`。
- `lookthrough_report.xlsx`：包含基础表和增强表的 Excel 工作簿。
- `enhanced_report.md`、`enhanced_report.html`、`run_summary.json`、`run_manifest.json`。

组合行的股息率、PE、PB 会按底层股票穿透权重重新计算。组合的收益、波动率、Sortino Ratio、最大回撤、Sharpe、Calmar、VaR/CVaR 和下行波动率会用 ETF 历史净值或价格合成组合曲线后计算，不是简单加权 ETF 表格里的指标。

常用参数：

```powershell
python "$SKILL_DIR\scripts\selected_etf_lookthrough.py" --etf 159569,159758 --weights 60,40 --max-stock-weight 7 --max-industry-weight 30 --min-dividend-yield 3 --refresh-cache
```

- `--max-stock-weight 7`：检查穿透后单一股票是否超过 7%。
- `--max-industry-weight`、`--min-dividend-yield`、`--max-pe`、`--max-pb`、`--max-drawdown`、`--target-a-weight`、`--target-hk-weight`：组合约束检查。
- `--cache` / `--no-cache`：是否在输出目录缓存当次持仓快照；默认开启。
- `--refresh-cache`：运行前删除该输出目录下旧的 `cache/holdings_*.csv`。
- `--compare`、`--portfolio`、`--industry`、`--theme`、`--risk`、`--html-report`：兼容语义参数；对应增强输出默认都会生成。

## 指标口径

- 股息率：底层股票股息率按穿透权重加权。
- PE：使用盈利收益率聚合口径 `sum(weight) / sum(weight / PE)`。
- PB：对有覆盖且为正的 PB 做加权平均。
- 收益和风险：来自 ETF 净值或价格历史；如果 ETF 历史太短，指标留空，并在 `收益错误` 中记录数据源诊断。
- 集中度：前 5/10/20 和最大个股会先按股票代码合并，再计算真实组合穿透权重。
- 行业/主题：当前为规则估计，输出中会标注不是官方申万/中信映射。
- 近 3 年收益：历史不足时留空，不用短窗口冒充 3 年收益。

详细说明：

- [PCF 计算方法](references/pcf-method.md)
- [输出字段说明](references/output-schema.md)
- [故障排查](references/troubleshooting.md)

## 安装依赖

```powershell
python -m pip install -r requirements.txt
```

运行时需要联网访问交易所 PCF、股票估值/分红、港股现价、HKD/CNY 汇率和 ETF 历史行情。

## 验证

```powershell
python -m py_compile scripts\pcf_common.py scripts\pcf_enhanced_analytics.py scripts\run_pcf_metrics.py scripts\pcf_lookthrough.py scripts\run_a_share_dividend_etf_pcf_metrics.py scripts\selected_etf_lookthrough.py
python -m unittest discover -s tests
```

可选联网 smoke test：

```powershell
python scripts\run_pcf_metrics.py --etf 513690,159569 --out-dir pcf-metrics-smoke-test
python scripts\selected_etf_lookthrough.py --etf 159012 --out-dir selected-etf-smoke-test --max-stock-weight 7
```

## 注意

- `run_pcf_metrics.py` 默认只保留最终 CSV/XLSX；调试时请加 `--keep-intermediates`。
- `selected_etf_lookthrough.py` 默认保留穿透明细和 Excel 报告。
- 常见运行结果已经加入 `.gitignore`，避免误提交中间文件。
