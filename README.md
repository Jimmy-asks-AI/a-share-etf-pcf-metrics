# A 股 ETF PCF 穿透指标

这个仓库提供一组不依赖模型推理的 Python 脚本和 Codex/Claude skill，用于做 ETF 底层资产穿透，并计算股息率、PE、PB、收益、波动率、Sortino Ratio、回撤、重合度、市场/行业暴露和组合约束等指标。

脚本优先读取上交所/深交所 PCF 申赎清单，而不是只取前十大持仓。PCF 是申赎篮子估算，不等同于精确基金持仓；输出通过 `穿透口径` 区分 PCF、基金季报、SEC N-PORT 和发行商持仓。当前支持 A 股、港股、美股成分，以及直接在美股上市的 ETF。

## 工作流

### 批量港股 ETF 排名

```powershell
$SKILL_DIR = Resolve-Path .
python "$SKILL_DIR\scripts\run_pcf_metrics.py" --etf 513690,159569 --out-dir pcf-metrics-output
```

最终输出：

- `pcf_full_metrics_table.csv`
- `pcf_full_metrics_table.xlsx`

从 CSV 读取 ETF 清单：

```powershell
python "$SKILL_DIR\scripts\run_pcf_metrics.py" --input ".\my_etf_list.csv" --code-column "ETF代码"
```

默认输入路径是 `lookthrough-hk-all-ranking/all_etf_summary.csv`。这个文件不会随仓库自带；如果没有，请先生成 ETF 发现结果，或直接用 `--etf` 指定代码。

### 批量美股成分 ETF 排名

用于 A 股上市、底层为美股的 ETF/QDII：

```powershell
python "$SKILL_DIR\scripts\run_pcf_metrics.py" --market us --etf 513100,513500 --out-dir lookthrough-us-pcf-risk
```

US mode 内部复用自选 ETF 引擎，并把 ETF 行抽取到同名最终输出。调试时加 `--keep-intermediates` 保留 `lookthrough_detail.csv`、`metrics_summary.csv` 和诊断文件。

### 美股上市 ETF 穿透

当 ETF 本身在美股上市，例如 `QQQ.US`、`DRAM.US`，使用专用脚本：

```powershell
python "$SKILL_DIR\scripts\us_listed_etf_lookthrough.py" --ticker QQQ.US --out-dir us-etf-output
python "$SKILL_DIR\scripts\us_listed_etf_lookthrough.py" --ticker QQQ.US,DRAM.US --weights 50,50 --out-dir us-etf-portfolio-output
python "$SKILL_DIR\scripts\us_listed_etf_lookthrough.py" --ticker QQQ.US --skip-metrics --out-dir us-qqq-holdings
python "$SKILL_DIR\scripts\us_listed_etf_lookthrough.py" --ticker QQQ.US,DRAM.US --full-output --out-dir us-etf-full-audit
```

默认核心输出：

- `lookthrough_detail.csv`
- `lookthrough_summary.csv`
- `etf_summary.csv`
- `metrics_summary.csv`
- `lookthrough_report.xlsx`
- `run_manifest.json`

只有需要额外 CSV、Markdown、HTML 和更多审计文件时才加 `--full-output`。

当前来源行为：

- 美股 ETF ticker 解析先用 SEC mutual fund/class ticker map，再用 SEC exchange ticker map，不限于手写 NYSE Arca/Nasdaq/Cboe 样例。
- SEC NPORT fallback 能提供完整持仓，但相对当前日有滞后。
- `DRAM` 等 Roundhill ETF 优先使用发行商每日持仓 CSV。
- `QQQ` 在 Invesco 接口拒绝脚本请求时，可退回 SEC NPORT 完整持仓。
- 成分股 PE/PB、价格、股息率优先使用 Yahoo quoteSummary。
- SEC N-PORT 只有 CUSIP 的成分先通过无需 API key 的 OpenFIGI 映射 ticker，Yahoo 搜索作为兜底。
- sector 和 industry 使用 Nasdaq quote endpoint 补充。
- ETF 收益/风险指标优先使用 Nasdaq chart 数据；组合收益/风险从合成 ETF 价格曲线重新计算。
- 默认不写缓存。加 `--cache` 后，持仓、成分股指标和 ETF 价格历史写入 `out-dir/cache`；加 `--refresh-cache` 可清理旧缓存。
- `--lookback-days` 是 `--nav-lookback-days` 的别名。

### 自选 ETF 或 ETF 组合

```powershell
python "$SKILL_DIR\scripts\selected_etf_lookthrough.py" --etf 159569,159758 --weights 60,40 --out-dir selected-etf-output
```

强制识别为美股成分 QDII：

```powershell
python "$SKILL_DIR\scripts\selected_etf_lookthrough.py" --etf 513100 --markets us --out-dir selected-us-etf-output
```

跨 A 股、港股、美股成分、直接美股上市 ETF 的组合：

```powershell
python "$SKILL_DIR\scripts\selected_etf_lookthrough.py" --etf 510880,159569,513100 --markets a,hk,us --weights 40,30,30 --out-dir selected-global-etf-output
python "$SKILL_DIR\scripts\selected_etf_lookthrough.py" --etf 510880,159569,513100,QQQ.US --markets a,hk,us,us_listed --weights 25,25,25,25 --out-dir selected-global-us-listed-output
```

自选流程默认输出：

- `metrics_summary.csv`
- `lookthrough_summary.csv`
- `lookthrough_detail.csv`
- `etf_summary.csv`
- `lookthrough_report.xlsx`
- `run_manifest.json`

加 `--full-output` 后才写出增强审计 CSV、Markdown、HTML 和 `run_summary.json`。加 `--cache` 后才写 `cache/holdings_*.csv`；快照按 ETF 与权重签名隔离，不会比较两个不同组合。

常用参数：

- `--markets auto,a,hk,us,us_listed`：给全部 ETF 指定同一模式，或为每只 ETF 指定一个模式。
- `--us-script`：覆盖 A 股上市美股成分 ETF helper 路径。
- `--us-listed-script`：覆盖直接美股上市 ETF helper 路径。
- `--target-a-weight`、`--target-hk-weight`、`--target-us-weight`：最低市场穿透暴露检查。
- `--max-stock-weight`、`--max-industry-weight`、`--min-dividend-yield`、`--max-pe`、`--max-pb`、`--max-drawdown`：组合约束检查。
- `--full-output`：写出辅助 CSV、Markdown、HTML 和 run_summary。
- `--cache` / `--no-cache`：写入或跳过本次持仓快照，默认不缓存。
- `--min-metric-coverage`：估值约束可通过的最低覆盖率，默认 80%。
- `--refresh-cache`：运行前删除旧 `cache/holdings_*.csv` 快照。

## 指标口径

- 股息率：底层成分股股息率按穿透权重加权。
- PE：盈利收益率聚合，公式为 `sum(weight) / sum(weight / PE)`。
- PB：对有覆盖且为正的 PB 做加权平均。
- 收益/风险：四条入口共用同一计算函数；优先累计净值、前复权或 adjusted close，未复权 fallback 会明确标记未计现金分红。
- 组合收益/风险：按初始权重买入并持有的组合曲线重新计算，不把单只 ETF 指标简单加权。
- 自选跨市场组合统一换算为 CNY；美股专用脚本单独运行时保持 USD。
- 集中度：Top5/10/20 和最大单股暴露会先合并重复股票，再计算真实组合穿透权重。
- 行业/主题：当前是规则估计，并明确标注不是官方申万/中信映射。
- 近 3 年收益：历史不足时留空，不用短窗口冒充。
- 约束检查：同一股票按“底层市场 + 标准化代码”合并；PE/PB/股息率覆盖低于 80% 时显示“数据不足”。
- US ticker：去空格、转大写，并保留字母、数字、点和连字符；不会把 `AAPL`、`MSFT`、`NVDA`、`BRK.B`、`BRK-B` 补零。
- US PCF 权重：优先用 PCF 现金替代金额除以 `NAVperCU`；只有数量时可估算 `ComponentShare * US latest price * USD/CNY / NAVperCU`，并在权重来源中记录。
- US 估值：A 股上市美股成分 ETF 使用现有 AkShare endpoint best effort；直接美股上市 ETF 使用 Yahoo quoteSummary，并用 Nasdaq 补行业信息。缺失或限流时保留空值和诊断。

参考：

- [PCF method](references/pcf-method.md)
- [Output schema](references/output-schema.md)
- [Troubleshooting](references/troubleshooting.md)

## 安装

```powershell
python -m pip install -r requirements.txt
```

美股支持不额外引入新依赖。运行时需要联网访问交易所 PCF、股票估值/股息数据、港股/美股价格、HKD/CNY、USD/CNY 汇率和 ETF 历史行情。

## 验证

```powershell
python -m py_compile scripts\pcf_common.py scripts\pcf_enhanced_analytics.py scripts\run_pcf_metrics.py scripts\pcf_lookthrough.py scripts\run_a_share_dividend_etf_pcf_metrics.py scripts\selected_etf_lookthrough.py scripts\us_etf_lookthrough.py
python -m py_compile scripts\us_listed_etf_lookthrough.py
python -m unittest discover -s tests
```

可选联网 smoke test：

```powershell
python scripts\run_pcf_metrics.py --etf 513690,159569 --out-dir pcf-metrics-smoke-test
python scripts\selected_etf_lookthrough.py --etf 513100 --markets us --skip-metrics --out-dir selected-us-smoke-test
python scripts\selected_etf_lookthrough.py --etf 510880,159569,513100,QQQ.US --markets a,hk,us,us_listed --weights 25,25,25,25 --skip-metrics --out-dir selected-global-plus-us-listed-smoke-test
python scripts\run_pcf_metrics.py --market us --etf 513100 --out-dir batch-us-smoke-test --keep-intermediates
python scripts\us_listed_etf_lookthrough.py --ticker QQQ.US,DRAM.US --out-dir us-listed-smoke-test
```

## 最近一次跨市场 smoke 结果

修复后联网测试日期：2026-07-11。

```text
python scripts\selected_etf_lookthrough.py --etf 510880,QQQ.US --markets a,us_listed --weights 50,50 --max-stock-weight 7 --max-pe 40 --min-metric-coverage 80 --out-dir <temp-output>
```

观察结果：

- `510880` 解析 50 行、`QQQ.US` 解析 102 行，未定价权重行均为 0；QQQ 的 SEC N-PORT CUSIP 已映射为 NVDA、META、GOOGL、GOOG、COST 等 ticker。
- 组合以 CNY 计价，按初始权重买入并持有计算：PE `14.8609`、PB `8.9894`、股息率 `3.2524%`、年化收益 `14.5993%`、Sortino `1.0341`。
- PE/PB/股息率覆盖权重分别为 `93.4211%`、`96.1766%`、`84.3144%`；最大单股 NVDA 为 `4.3406%`，正确通过 7% 限制。
- 默认只生成 6 个核心文件：4 个 CSV、`lookthrough_report.xlsx` 和 `run_manifest.json`。

以下 2026-06-28 记录保留为旧版四市场持仓解析基线。

运行日期：2026-06-28。

```text
python scripts\selected_etf_lookthrough.py --etf 510880,159569,513100,QQQ.US --markets a,hk,us,us_listed --weights 25,25,25,25 --skip-metrics --no-cache --out-dir selected-global-plus-us-listed-smoke-test
```

结果摘要：

- 运行成功，并写出 `lookthrough_summary.csv`、`lookthrough_detail.csv`、`etf_summary.csv`、`lookthrough_report.xlsx` 和增强审计 CSV/HTML/MD 文件。
- ETF 持仓行数：`510880` A 股模式 50 只，`159569` 港股模式 30 只，`513100` A 股上市美股成分模式 101 只，`QQQ.US` 美股上市模式 102 只。
- `QQQ.US` 来源：SEC NPORT，持仓日期 `2026-03-31`。
- 穿透市场暴露：A 股 24.3746%，港股 25.0000%，美股 48.8264%，其他海外市场 1.1927%，现金/其他 0.6064%。
- 合并同 ticker 后 Top 持仓：NVDA 4.1328%，AAPL 3.6674%，MSFT 2.5046%，AMZN 2.1695%，MU 1.8863%。

这次 smoke 使用 `--skip-metrics`，验证的是跨市场 PCF 解析、组合权重、市场暴露和报告生成；PE/PB/股息率/风险 endpoint 覆盖应使用非 skip run 测试。

## 注意

- `run_pcf_metrics.py` 默认删除中间文件，除非传入 `--keep-intermediates`。
- 清理仅删除脚本明确生成的中间文件，不会删除输出目录中的其他文件。
- 批量任务即使部分 ETF 失败，也会在最终表保留失败行、状态和错误信息。
- `selected_etf_lookthrough.py` 默认只保留核心 CSV、Excel 和 manifest。
- 生成输出已写入 `.gitignore`，避免误提交中间文件。
- 跨境 PCF 可能出现现金替代行、成分元数据不完整或交易所市场代码差异；脚本保留来源代码和诊断，不静默猜测。
- ETF-of-ETF、ETN、商品/加密信托及衍生品不会递归展开，当前层级和未穿透权重会保留。
- 美股市场和估值 endpoint 可能限流或缺字段；缺失的成分指标记录在 `估值错误`，ETF 级 PE/PB 会在覆盖率足够时继续聚合。
- 输出是数据计算结果，不是投资建议。
