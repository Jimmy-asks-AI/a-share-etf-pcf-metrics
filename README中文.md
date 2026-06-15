# A 股 ETF PCF 穿透指标 Skill

这个仓库提供一组纯脚本和 Codex/Claude skill，用于对 A 股上市 ETF 做 PCF 底层穿透，并计算股息率、PE、PB、收益、波动率和 Sortino Ratio 等指标。

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
- `lookthrough_report.xlsx`：包含以上表格的 Excel 工作簿。

组合行的股息率、PE、PB 会按底层股票穿透权重重新计算。组合的收益、波动率和 Sortino Ratio 会用 ETF 历史净值或价格合成组合曲线后计算，不是简单加权 ETF 表格里的指标。

## 指标口径

- 股息率：底层股票股息率按穿透权重加权。
- PE：使用盈利收益率聚合口径 `sum(weight) / sum(weight / PE)`。
- PB：对有覆盖且为正的 PB 做加权平均。
- 收益和风险：来自 ETF 净值或价格历史。
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
python -m py_compile scripts\run_pcf_metrics.py scripts\pcf_lookthrough.py scripts\run_a_share_dividend_etf_pcf_metrics.py scripts\selected_etf_lookthrough.py
python -m unittest discover -s tests
```

可选联网 smoke test：

```powershell
python scripts\run_pcf_metrics.py --etf 513690,159569 --out-dir pcf-metrics-smoke-test
```

## 注意

- `run_pcf_metrics.py` 默认只保留最终 CSV/XLSX；调试时请加 `--keep-intermediates`。
- `selected_etf_lookthrough.py` 默认保留穿透明细和 Excel 报告。
- 常见运行结果已经加入 `.gitignore`，避免误提交中间文件。
