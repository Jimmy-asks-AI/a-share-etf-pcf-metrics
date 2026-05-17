# A 股 ETF PCF 穿透指标表 Skill

这是一个 Codex / Claude skill，用来对 A 股上市、投资港股/H 股/恒生/港股通标的的 ETF 做 PCF 底层穿透，并生成最终指标表。

默认只保留两个最终输出文件：

- `pcf_full_metrics_table.xlsx`
- `pcf_full_metrics_table.csv`

过程文件，例如单只 ETF 的 holdings、JSON、Markdown 报告和中间 `summary.csv`，默认会自动清理。

## 能计算什么

最终表包含以下字段：

- 股息率
- PE，使用盈利收益率聚合口径
- PB
- 年化收益
- 索提诺比率（Sortino Ratio）
- 波动率
- 近半年收益
- 近一年收益
- 近 3 年收益
- 港股持仓权重
- 持仓来源：`sse_pcf` 或 `szse_pcf`

近 3 年收益在历史数据不足时会留空，不会用短历史冒充 3 年收益。

## PCF 穿透口径

### 上交所 ETF

使用上交所 ETF PCF 接口。

权重口径：

```text
SUBSTITUTION_CASH_AMOUNT / NAVPERCU
```

### 深交所 ETF

使用深交所申购赎回清单 XML。

现金替代行权重口径：

```text
CreationCashSubstitute / (1 + PremiumRatio) / NAVperCU
```

实物申赎港股行如果 `CreationCashSubstitute` 为 0，则使用：

```text
ComponentShare * 港股现价 * HKD/CNY / NAVperCU
```

如果同一条深交所下载记录里有多个 XML 候选文件，会选择港股成分数量最多的 XML，避免只读到“申赎现金”行。

更详细的规则见：

[references/pcf-method.md](references/pcf-method.md)

## 安装

把本仓库放到 Codex 或 Claude 的 skills 目录下即可。

Codex 示例：

```powershell
git clone https://github.com/Jimmy-asks-AI/a-share-etf-pcf-metrics.git C:\Users\81901\.codex\skills\a-share-etf-pcf-metrics
```

Claude 示例：

```powershell
git clone https://github.com/Jimmy-asks-AI/a-share-etf-pcf-metrics.git C:\Users\81901\.claude\skills\a-share-etf-pcf-metrics
```

## 使用方法

在包含旧 ETF 收录清单的工作目录中运行：

```powershell
python C:\Users\81901\.codex\skills\a-share-etf-pcf-metrics\scripts\run_pcf_metrics.py
```

默认输入文件：

```text
lookthrough-hk-all-ranking/all_etf_summary.csv
```

默认代码列：

```text
ETF代码
```

默认输出目录：

```text
lookthrough-hk-all-ranking-pcf-risk
```

## 指定 ETF 运行

```powershell
python C:\Users\81901\.codex\skills\a-share-etf-pcf-metrics\scripts\run_pcf_metrics.py --etf 513690,159569 --out-dir pcf-metrics-output
```

传入 `--etf` 时，脚本只运行指定 ETF，不会再读取默认输入清单。

## 使用自定义清单

```powershell
python C:\Users\81901\.codex\skills\a-share-etf-pcf-metrics\scripts\run_pcf_metrics.py --input .\my_etf_list.csv --code-column ETF代码 --out-dir pcf-metrics-output
```

## 保留过程文件用于调试

默认只保留最终 CSV/XLSX。如果需要检查单只 ETF 的成分和中间 JSON，可加：

```powershell
python C:\Users\81901\.codex\skills\a-share-etf-pcf-metrics\scripts\run_pcf_metrics.py --keep-intermediates
```

## 示例输出

以下示例截取自 `pcf_full_metrics_table.csv` 的前几列：

| 排名_按股息率 | ETF代码 | ETF名称 | 持仓来源 | 港股持仓权重% | 股息率% | PE | PB |
|---:|---:|:---|:---|---:|---:|---:|---:|
| 1 | 159569 | 港股红利低波ETF景顺 | szse_pcf | 100.00 | 6.36 | 8.90 | 1.14 |
| 2 | 513830 | 港股通高股息ETF嘉实 | sse_pcf | 100.02 | 6.09 | 8.63 | 1.27 |
| 3 | 520810 | XD港股通红利ETF易方达 | sse_pcf | 99.40 | 6.09 | 8.63 | 1.27 |
| 4 | 513530 | 港股通红利ETF华泰柏瑞 | sse_pcf | 100.00 | 6.09 | 8.63 | 1.27 |
| 5 | 513820 | 港股通红利ETF汇添富 | sse_pcf | 100.00 | 6.09 | 8.63 | 1.27 |
| 6 | 159302 | 港股高股息ETF银华 | szse_pcf | 98.87 | 6.08 | 8.63 | 1.27 |

## 依赖

缺依赖时再安装：

```powershell
python -m pip install akshare pandas requests openpyxl tabulate
```

运行时需要联网访问：

- 上交所 / 深交所 PCF
- 港股估值数据
- 港股现价
- HKD/CNY 汇率
- ETF 行情和净值历史

## 在 Codex / Claude 中调用

安装后可以直接说：

```text
用 a-share-etf-pcf-metrics 重新生成 ETF PCF 穿透指标表
```

也可以说：

```text
用 a-share-etf-pcf-metrics 只跑 513690 和 159569，并输出最终 CSV/XLSX
```

