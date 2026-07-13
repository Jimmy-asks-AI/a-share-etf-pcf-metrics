---
name: a-share-etf-pcf-metrics
description: 使用确定性脚本生成 ETF 底层资产穿透指标。用户要求 ETF 穿透、PCF 申赎篮子、股息率、PE/PB、Sortino、组合风险、单股权重约束，或处理 A 股上市 ETF、港股/美股成分 QDII、美股上市 ETF 和跨市场 ETF 组合时使用。
---

# A 股 ETF PCF 穿透指标

## 适用场景

当用户要用脚本而不是模型能力，基于交易所 PCF 或公开持仓数据穿透 ETF 底层资产，并计算股息率、PE、PB、收益和风险指标时，使用本 skill。

按标的选择流程：

- 批量港股或美股成分 ETF 排名：`scripts/run_pcf_metrics.py`
- 指定 ETF 或 ETF 组合穿透：`scripts/selected_etf_lookthrough.py`
- 美股上市 ETF 代码穿透：`scripts/us_listed_etf_lookthrough.py`
- A 股“红利/分红/股息”ETF 发现与排名：`scripts/run_a_share_dividend_etf_pcf_metrics.py`

先解析 skill 目录。`$SKILL_DIR` 指向包含本 `SKILL.md` 的目录；如果当前目录就是 skill 或仓库目录：

```powershell
$SKILL_DIR = Resolve-Path .
```

## 批量港股 ETF 排名

指定 ETF 代码：

```powershell
python "$SKILL_DIR\scripts\run_pcf_metrics.py" --etf 513690,159569 --out-dir pcf-metrics-output
```

从 CSV 读取 ETF 列表：

```powershell
python "$SKILL_DIR\scripts\run_pcf_metrics.py" --input ".\my_etf_list.csv" --code-column "ETF代码" --out-dir pcf-metrics-output
```

默认输入是 `lookthrough-hk-all-ranking/all_etf_summary.csv`。该文件不随 skill 打包；如果不存在，显式传入 `--etf`，或先生成 ETF 发现结果。

默认最终输出：

- `pcf_full_metrics_table.xlsx`
- `pcf_full_metrics_table.csv`

默认会删除中间 CSV/JSON/MD 文件。调试或审计来源数据时加 `--keep-intermediates`。

## 批量美股成分 ETF 排名

用于 A 股上市、底层为美股的 ETF/QDII：

```powershell
python "$SKILL_DIR\scripts\run_pcf_metrics.py" --market us --etf 513100,513500 --out-dir lookthrough-us-pcf-risk
```

最终输出仍是 `pcf_full_metrics_table.xlsx` 和 `pcf_full_metrics_table.csv`。

## 美股上市 ETF 穿透

当 ETF 本身在美股上市，例如 `QQQ.US`、`DRAM.US`，优先使用专用脚本：

```powershell
python "$SKILL_DIR\scripts\us_listed_etf_lookthrough.py" --ticker QQQ.US --out-dir us-etf-output
python "$SKILL_DIR\scripts\us_listed_etf_lookthrough.py" --ticker QQQ.US,DRAM.US --weights 50,50 --out-dir us-etf-portfolio-output
python "$SKILL_DIR\scripts\us_listed_etf_lookthrough.py" --ticker QQQ.US --skip-metrics --out-dir us-qqq-holdings
python "$SKILL_DIR\scripts\us_listed_etf_lookthrough.py" --ticker QQQ.US,DRAM.US --full-output --out-dir us-etf-full-audit
```

默认只输出核心文件：`lookthrough_detail.csv`、`lookthrough_summary.csv`、`etf_summary.csv`、`metrics_summary.csv`、`lookthrough_report.xlsx`、`run_manifest.json`。只有需要额外 CSV、Markdown、HTML 审计文件时才加 `--full-output`。

数据来源行为：

- 美股 ETF ticker 解析先用 SEC mutual fund/class ticker map，再用 SEC exchange ticker map，不限于手写交易所样例。
- SEC NPORT fallback 能给出完整持仓，但相对当前日持仓有滞后。
- `DRAM` 等 Roundhill ETF 优先使用发行商每日持仓 CSV。
- `QQQ` 在 Invesco 接口拦截脚本请求时，可退回 SEC NPORT 完整持仓。
- 成分股 PE/PB、价格、股息率优先来自 Yahoo quoteSummary。
- SEC N-PORT 只给 CUSIP 时，先用无需 API key 的 OpenFIGI 映射美股 ticker，再用 Yahoo 搜索兜底。
- sector 和 industry 使用 Nasdaq quote endpoint 补充。
- ETF 收益和风险指标优先使用 Nasdaq chart 数据；组合风险用 ETF 价格曲线重新计算。
- 默认不写缓存。加 `--cache` 后，持仓、成分股指标和 ETF 价格历史写入 `out-dir/cache`；加 `--refresh-cache` 可先清理旧缓存。
- `--lookback-days` 是 `--nav-lookback-days` 的别名。

## 指定 ETF 或组合穿透

单只 ETF：

```powershell
python "$SKILL_DIR\scripts\selected_etf_lookthrough.py" --etf 159569 --out-dir selected-etf-output
```

带权重组合：

```powershell
python "$SKILL_DIR\scripts\selected_etf_lookthrough.py" --etf 159569,159758 --weights 60,40 --out-dir selected-etf-output
```

美股成分 QDII：

```powershell
python "$SKILL_DIR\scripts\selected_etf_lookthrough.py" --etf 513100 --markets us --out-dir selected-us-etf-output
```

跨 A 股、港股、美股成分、直接美股上市 ETF 的组合：

```powershell
python "$SKILL_DIR\scripts\selected_etf_lookthrough.py" --etf 510880,159569,513100 --markets a,hk,us --weights 40,30,30 --out-dir selected-global-etf-output
python "$SKILL_DIR\scripts\selected_etf_lookthrough.py" --etf 510880,159569,513100,QQQ.US --markets a,hk,us,us_listed --weights 25,25,25,25 --out-dir selected-global-us-listed-output
```

`--weights` 支持百分数或小数，并会自动归一化。未提供权重时等权。`--markets` 可传 `auto`、`hk`、`a`、`us`、`us_listed`；除非自动识别错误，否则使用 `auto`。以 `.US` 结尾的 ticker 会自动进入 `us_listed` 模式。

## 指定组合输出

`selected_etf_lookthrough.py` 默认只生成：

- `metrics_summary.csv`：每只 ETF 一行，加一行 `PORTFOLIO`。
- `lookthrough_summary.csv`：按底层股票聚合后的组合持仓。
- `lookthrough_detail.csv`：每只 ETF 的底层明细，含股票 PE/PB/股息率。
- `etf_summary.csv`：ETF 持仓来源和指标汇总。
- `lookthrough_report.xlsx`：核心表和增强审计工作表。
- `run_manifest.json`：参数、字段和行数。

加 `--full-output` 后才额外生成：

- `classified_detail.csv`：加上板块、规则行业、主题分类的明细。
- `structure_analysis.csv`：持仓数量、Top5/10/20 集中度、最大单股、现金/其他估计。
- `market_board_exposure.csv`：A/HK/US/现金及 A 股板块暴露。
- `industry_theme_exposure.csv`：规则估计的行业和主题暴露。
- `valuation_buckets.csv`：PE/PB/股息率分层。
- `profit_quality.csv`、`dividend_quality.csv`：仅输出已有数据支持的质量摘要，不写空占位指标。
- `risk_analysis.csv`：收益风险指标及不可用字段诊断。
- `overlap_pairs.csv`、`common_holdings.csv`：ETF 之间的重合度和共同持仓。
- `pcf_quality.csv`、`cross_validation.csv`、`constraint_checks.csv`、`historical_tracking.csv`。
- `enhanced_report.md`、`enhanced_report.html`、`run_summary.json`。

常用参数：

- `--max-stock-weight 7`：检查穿透后单一股票是否超过 7%。
- `--max-industry-weight`、`--min-dividend-yield`、`--max-pe`、`--max-pb`、`--max-drawdown`、`--target-a-weight`、`--target-hk-weight`、`--target-us-weight`：组合约束检查。
- `--us-script`：覆盖 A 股上市美股成分 ETF helper 路径。
- `--us-listed-script`：覆盖直接美股上市 ETF helper 路径。
- `--full-output`：写出辅助 CSV、Markdown、HTML 和 run_summary；默认关闭。
- `--cache` / `--no-cache`：是否在 `cache/` 写入本次持仓快照；默认关闭。
- `--refresh-cache`：运行前删除旧 `holdings_*.csv` 快照。
- `--compare`、`--portfolio`、`--industry`、`--theme`、`--risk`、`--html-report`：兼容语义参数，增强输出默认生成。

## 指标口径

- 股息率：底层成分股股息率按穿透权重加权。
- PE：使用 earnings-yield 聚合，公式为 `sum(weight) / sum(weight / PE)`；非正 PE 不进入分母，并记录负 PE 权重。
- PB：只对有效正 PB 成分做加权平均。
- ETF 年化收益、波动率、Sortino、最大回撤、Sharpe、Calmar、VaR/CVaR、下行波动率：来自 ETF NAV 或价格历史。
- 所有入口共用同一套收益/风险公式：风险窗口最多一年，MAR/无风险收益率为 0，年化交易日为 252。
- 组合收益/风险：按初始权重买入并持有的组合曲线重新计算，不把单只 ETF 指标简单加权。
- 半年、一年、三年收益至少覆盖 150、330、900 个日历日；不足时留空。
- 优先使用累计净值、前复权或 adjusted close；只能取得未复权价格时必须标记“价格收益(未计现金分红)”。
- 自选跨市场组合以 CNY 为收益币种；直接美股上市 ETF 先乘历史 USD/CNY。美股专用脚本单独运行时以 USD 计价。
- PE/PB/股息率约束默认要求对应覆盖权重至少 80%；不足时结果为“数据不足”，不得显示“通过”。
- 行业/主题：当前为规则估计，除非外部官方映射接入；输出会标注数据来源状态。
- US ticker 保持 ticker 形态，不补零；例如 `AAPL`、`MSFT`、`NVDA`、`BRK.B`、`BRK-B`。
- A 股上市美股成分 QDII 的 PE/PB/股息率是 AkShare best-effort；直接美股上市 ETF 成分股使用 Yahoo quoteSummary，并用 Nasdaq 补 sector/industry。

## PCF 规则

修改来源优先级或权重公式前，先读 `references/pcf-method.md`。

关键规则：

- PCF 是申赎篮子估算，不等同于基金实际持仓；季报、SEC N-PORT 和发行商持仓必须在 `穿透口径` 中区分。
- 保留原始股票行数、有效权重行数和未定价/缺失权重行数；不得在质量检查前静默丢弃。

- 上交所 ETF PCF：`SUBSTITUTION_CASH_AMOUNT / NAVPERCU`。
- 深交所现金替代行：`CreationCashSubstitute / (1 + PremiumRatio) / NAVperCU`。
- 深交所港股实物行且替代现金为 0：`ComponentShare * HK spot price * HKD/CNY / NAVperCU`。
- 美股行优先用 PCF 现金替代金额除以 `NAVperCU`；只有数量时，可用 `ComponentShare * US latest price * USD/CNY / NAVperCU` 估算，并在 `权重来源` 记录公式。
- 深交所下载页出现多个 XML 候选时，选择股票成分最相关的 XML。

## 参考文件

只加载当前任务需要的文件：

- `references/pcf-method.md`：数据源优先级和 PCF 公式。
- `references/output-schema.md`：字段定义、单位和空值语义。
- `references/troubleshooting.md`：网络或数据源失败诊断。

## 失败规则与禁止事项

- 如果部分 ETF 失败，最终表仍保留该 ETF，并写 `数据状态=失败` 和 `错误`；不得静默缩小排名样本。
- 如果估值覆盖率低于 80%，保留数值供审计，但不得参与有效股息率排名或通过估值约束。
- 不要把不足 900 天的收益标成近 3 年收益。
- 不要用股票名称作为唯一持仓身份；始终按“底层市场 + 标准化股票代码”合并。
- 不要把 PCF 申赎篮子表述成精确基金持仓。
- 不要把规则行业标成官方申万/中信行业。
- ETF-of-ETF、ETN、商品/加密信托和衍生品只报告当前层级及未穿透权重；不要声称已递归展开。
- 默认清理只能删除脚本明确生成的中间文件，不得删除输出目录中的其他文件。

## 依赖

只有缺包时才安装：

```powershell
python -m pip install -r "$SKILL_DIR\requirements.txt"
```

需要网络访问交易所 PCF、股票估值/股息数据、港股/美股价格、HKD/CNY、USD/CNY 汇率和 ETF 历史行情。

## 验证

快速离线检查：

```powershell
python -m py_compile "$SKILL_DIR\scripts\pcf_common.py" "$SKILL_DIR\scripts\pcf_enhanced_analytics.py" "$SKILL_DIR\scripts\run_pcf_metrics.py" "$SKILL_DIR\scripts\pcf_lookthrough.py" "$SKILL_DIR\scripts\run_a_share_dividend_etf_pcf_metrics.py" "$SKILL_DIR\scripts\selected_etf_lookthrough.py" "$SKILL_DIR\scripts\us_etf_lookthrough.py"
python -m py_compile "$SKILL_DIR\scripts\us_listed_etf_lookthrough.py"
python -m unittest discover -s "$SKILL_DIR\tests"
```

网络 smoke test：

```powershell
python "$SKILL_DIR\scripts\run_pcf_metrics.py" --etf 513690,159569 --out-dir pcf-metrics-smoke-test
python "$SKILL_DIR\scripts\selected_etf_lookthrough.py" --etf 513100 --markets us --skip-metrics --out-dir selected-us-smoke-test
python "$SKILL_DIR\scripts\us_listed_etf_lookthrough.py" --ticker QQQ.US,DRAM.US --out-dir us-listed-smoke-test
```

批量流程的预期结果是最终 `.csv` 和 `.xlsx`；中间文件只在传入 `--keep-intermediates` 时保留。
