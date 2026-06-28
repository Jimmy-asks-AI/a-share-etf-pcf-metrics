#!/usr/bin/env python
"""Generate final A-share ETF PCF metrics CSV/XLSX tables."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pandas as pd


FINAL_CSV = "pcf_full_metrics_table.csv"
FINAL_XLSX = "pcf_full_metrics_table.xlsx"
DEFAULT_INPUT = "lookthrough-hk-all-ranking/all_etf_summary.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT,
        help=f"CSV containing ETF codes. Defaults to {DEFAULT_INPUT}.",
    )
    parser.add_argument("--code-column", default="ETF代码", help="ETF code column in --input.")
    parser.add_argument("--etf", action="append", help="ETF code. Repeat or use comma-separated values. When set, --input is ignored.")
    parser.add_argument("--out-dir", default="lookthrough-hk-all-ranking-pcf-risk", help="Output directory.")
    parser.add_argument("--market", choices=("hk", "us"), default="hk", help="Underlying market workflow. Default: hk.")
    parser.add_argument("--holdings-source", choices=("auto", "pcf", "reported"), default="auto")
    parser.add_argument("--alt-limit", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.05, help="Sleep seconds between constituent valuation requests.")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--keep-intermediates", action="store_true", help="Keep per-ETF reports and summary files.")
    return parser.parse_args()


def load_codes(args: argparse.Namespace) -> list[str]:
    codes: list[str] = []
    for raw in args.etf or []:
        codes.extend(part.strip() for part in raw.split(",") if part.strip())
    input_path = Path(args.input)
    if not codes and input_path.exists():
        df = pd.read_csv(input_path, encoding="utf-8-sig")
        if args.code_column not in df.columns:
            raise SystemExit(f"Input CSV missing code column: {args.code_column}")
        codes.extend(str(value).strip() for value in df[args.code_column].dropna())
    if not codes:
        if str(input_path).replace("\\", "/") == DEFAULT_INPUT:
            raise SystemExit(
                "No ETF codes found. Provide --etf, or generate the default input first at "
                f"{DEFAULT_INPUT}. The default input is produced by the earlier HK ETF discovery "
                "workflow; for an explicit run use: python scripts/run_pcf_metrics.py --etf 513690,159569"
            )
        raise SystemExit(f"No ETF codes found. Provide --etf or a valid --input CSV: {input_path}")
    return [code.zfill(6) for code in dict.fromkeys(codes)]


def run_lookthrough(args: argparse.Namespace, codes: list[str], out_dir: Path) -> None:
    if args.market == "us":
        script = Path(__file__).with_name("selected_etf_lookthrough.py")
        subprocess.run(
            [
                sys.executable,
                str(script),
                "--etf",
                ",".join(codes),
                "--markets",
                "us",
                "--out-dir",
                str(out_dir),
            ],
            check=True,
        )
        return

    script = Path(__file__).with_name("pcf_lookthrough.py")
    command = [
        sys.executable,
        str(script),
        "--etf",
        ",".join(codes),
        "--holdings-source",
        args.holdings_source,
        "--alt-limit",
        str(args.alt_limit),
        "--sleep",
        str(args.sleep),
        "--top-n",
        str(args.top_n),
        "--out-dir",
        str(out_dir),
    ]
    subprocess.run(command, check=True)


def build_hk_final_tables(out_dir: Path) -> pd.DataFrame:
    summary_path = out_dir / "summary.csv"
    if not summary_path.exists():
        raise SystemExit(f"Missing look-through summary: {summary_path}")
    summary = pd.read_csv(summary_path, encoding="utf-8-sig")
    numeric_columns = [
        "hk_weight_pct",
        "dividend_yield_pct",
        "pb",
        "pe_earnings_yield",
        "annualized_return_pct",
        "sortino_ratio",
        "volatility_pct",
        "half_year_return_pct",
        "one_year_return_pct",
        "three_year_return_pct",
        "pe_coverage_pct",
        "negative_pe_weight_pct",
    ]
    for column in numeric_columns:
        if column in summary.columns:
            summary[column] = pd.to_numeric(summary[column], errors="coerce")

    summary = summary.sort_values("dividend_yield_pct", ascending=False, na_position="last").reset_index(drop=True)
    report = pd.DataFrame(
        {
            "排名_按股息率": range(1, len(summary) + 1),
            "ETF代码": summary["etf"].astype(str).str.zfill(6),
            "ETF名称": summary["name"],
            "持仓期": summary["holdings_period"],
            "持仓来源": summary["holdings_source"],
            "港股持仓权重%": summary["hk_weight_pct"],
            "股息率%": summary["dividend_yield_pct"],
            "PE": summary["pe_earnings_yield"],
            "PB": summary["pb"],
            "年化收益%": summary["annualized_return_pct"],
            "索提诺比率": summary["sortino_ratio"],
            "波动率%": summary["volatility_pct"],
            "近半年收益%": summary["half_year_return_pct"],
            "近一年收益%": summary["one_year_return_pct"],
            "近3年收益%": summary["three_year_return_pct"],
            "收益来源": summary["annualized_return_source"],
            "收益区间": summary["return_window"],
            "风险指标区间": summary["risk_window"],
            "PE覆盖权重%": summary["pe_coverage_pct"],
            "负PE权重%": summary["negative_pe_weight_pct"],
        }
    )
    report.to_csv(out_dir / FINAL_CSV, index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(out_dir / FINAL_XLSX, engine="openpyxl") as writer:
        report.to_excel(writer, index=False, sheet_name="ETF穿透汇总")
        ws = writer.book["ETF穿透汇总"]
        ws.freeze_panes = "A2"
        widths = {
            "A": 12,
            "B": 12,
            "C": 24,
            "D": 14,
            "E": 14,
            "F": 14,
            "G": 12,
            "H": 10,
            "I": 10,
            "J": 12,
            "K": 12,
            "L": 12,
            "M": 12,
            "N": 12,
            "O": 12,
            "P": 16,
            "Q": 24,
            "R": 24,
            "S": 14,
            "T": 12,
        }
        for column, width in widths.items():
            ws.column_dimensions[column].width = width
        for row in ws.iter_rows(min_row=2, min_col=6, max_col=15):
            for cell in row:
                cell.number_format = "0.00"
    return report


def build_us_final_tables(out_dir: Path) -> pd.DataFrame:
    metrics_path = out_dir / "metrics_summary.csv"
    if not metrics_path.exists():
        raise SystemExit(f"Missing selected ETF metrics summary: {metrics_path}")
    metrics = pd.read_csv(metrics_path, encoding="utf-8-sig")
    metrics = metrics[metrics["ETF代码"].astype(str).ne("PORTFOLIO")].copy()
    numeric_columns = [
        "股票权重合计%",
        "股息率%",
        "PE",
        "PB",
        "年化收益%",
        "索提诺比率",
        "波动率%",
        "近半年收益%",
        "近一年收益%",
        "近3年收益%",
        "PE覆盖权重%",
        "负PE权重%",
    ]
    for column in numeric_columns:
        if column in metrics.columns:
            metrics[column] = pd.to_numeric(metrics[column], errors="coerce")
    metrics = metrics.sort_values("股息率%", ascending=False, na_position="last").reset_index(drop=True)
    report = pd.DataFrame(
        {
            "排名_按股息率": range(1, len(metrics) + 1),
            "ETF代码": metrics["ETF代码"].astype(str).str.zfill(6),
            "ETF名称": metrics["ETF名称"],
            "持仓期": metrics["持仓期"],
            "持仓来源": metrics["持仓来源"],
            "美股持仓权重%": metrics["股票权重合计%"],
            "股息率%": metrics["股息率%"],
            "PE": metrics["PE"],
            "PB": metrics["PB"],
            "年化收益%": metrics["年化收益%"],
            "索提诺比率": metrics["索提诺比率"],
            "波动率%": metrics["波动率%"],
            "近半年收益%": metrics["近半年收益%"],
            "近一年收益%": metrics["近一年收益%"],
            "近3年收益%": metrics["近3年收益%"],
            "收益来源": metrics["收益来源"],
            "收益区间": metrics["收益区间"],
            "风险指标区间": metrics["风险指标区间"],
            "PE覆盖权重%": metrics["PE覆盖权重%"],
            "负PE权重%": metrics["负PE权重%"],
        }
    )
    report.to_csv(out_dir / FINAL_CSV, index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(out_dir / FINAL_XLSX, engine="openpyxl") as writer:
        report.to_excel(writer, index=False, sheet_name="ETF穿透汇总")
    return report


def build_final_tables(out_dir: Path, market: str = "hk") -> pd.DataFrame:
    if market == "us":
        return build_us_final_tables(out_dir)
    return build_hk_final_tables(out_dir)


def clean_intermediates(out_dir: Path) -> None:
    keep = {FINAL_CSV, FINAL_XLSX}
    for path in out_dir.iterdir():
        if path.is_file() and path.name not in keep:
            path.unlink()


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    codes = load_codes(args)
    run_lookthrough(args, codes, out_dir)
    report = build_final_tables(out_dir, args.market)
    if not args.keep_intermediates:
        clean_intermediates(out_dir)
    print(out_dir / FINAL_XLSX)
    print(out_dir / FINAL_CSV)
    print(f"rows={len(report)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
