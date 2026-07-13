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
MIN_RANK_COVERAGE = 80.0


def column_or_default(df: pd.DataFrame, name: str, default=None) -> pd.Series:
    return df[name] if name in df.columns else pd.Series([default] * len(df), index=df.index)


def append_failed_rows(report: pd.DataFrame, expected_codes: list[str] | None, errors: dict[str, str]) -> pd.DataFrame:
    if not expected_codes:
        return report
    completed = set(report["ETF代码"].astype(str).str.zfill(6)) if not report.empty else set()
    rows = []
    for code in expected_codes:
        code = str(code).zfill(6)
        if code in completed:
            continue
        row = {column: None for column in report.columns}
        row.update({"ETF代码": code, "数据状态": "失败", "错误": errors.get(code, "未产出结果")})
        rows.append(row)
    return pd.concat([report, pd.DataFrame(rows)], ignore_index=True) if rows else report


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


def build_hk_final_tables(out_dir: Path, expected_codes: list[str] | None = None) -> pd.DataFrame:
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
        "dividend_yield_coverage_pct",
        "pb_coverage_pct",
        "missing_weight_rows",
    ]
    for column in numeric_columns:
        if column in summary.columns:
            summary[column] = pd.to_numeric(summary[column], errors="coerce")

    summary["_rankable"] = (
        pd.to_numeric(column_or_default(summary, "dividend_yield_coverage_pct"), errors="coerce").ge(MIN_RANK_COVERAGE)
        & pd.to_numeric(summary["dividend_yield_pct"], errors="coerce").notna()
    )
    summary = summary.sort_values(["_rankable", "dividend_yield_pct"], ascending=[False, False], na_position="last").reset_index(drop=True)
    ranks = pd.Series(pd.NA, index=summary.index, dtype="Int64")
    ranks.loc[summary["_rankable"]] = range(1, int(summary["_rankable"].sum()) + 1)
    report = pd.DataFrame(
        {
            "排名_按股息率": ranks,
            "ETF代码": summary["etf"].astype(str).str.zfill(6),
            "ETF名称": summary["name"],
            "持仓期": summary["holdings_period"],
            "持仓来源": summary["holdings_source"],
            "穿透口径": summary["holdings_source"].astype(str).map(lambda value: "基金季报持仓" if "reported" in value.lower() else "PCF申赎篮子估算"),
            "港股持仓权重%": summary["hk_weight_pct"],
            "股息率%": summary["dividend_yield_pct"],
            "股息率覆盖权重%": column_or_default(summary, "dividend_yield_coverage_pct"),
            "PE": summary["pe_earnings_yield"],
            "PB": summary["pb"],
            "PB覆盖权重%": column_or_default(summary, "pb_coverage_pct"),
            "年化收益%": summary["annualized_return_pct"],
            "索提诺比率": summary["sortino_ratio"],
            "波动率%": summary["volatility_pct"],
            "近半年收益%": summary["half_year_return_pct"],
            "近一年收益%": summary["one_year_return_pct"],
            "近3年收益%": summary["three_year_return_pct"],
            "收益来源": summary["annualized_return_source"],
            "收益口径": column_or_default(summary, "return_basis"),
            "收益币种": "CNY",
            "收益区间": summary["return_window"],
            "风险指标区间": summary["risk_window"],
            "PE覆盖权重%": summary["pe_coverage_pct"],
            "负PE权重%": summary["negative_pe_weight_pct"],
            "PCF原始股票行数": column_or_default(summary, "raw_hk_rows"),
            "有效权重行数": column_or_default(summary, "effective_hk_rows"),
            "未定价/缺失权重行数": column_or_default(summary, "missing_weight_rows"),
            "数据状态": summary["_rankable"].map({True: "有效", False: "覆盖不足"}),
            "错误": "",
        }
    )
    errors: dict[str, str] = {}
    error_path = out_dir / "errors.csv"
    if error_path.exists():
        error_df = pd.read_csv(error_path, encoding="utf-8-sig")
        if {"etf", "error"}.issubset(error_df.columns):
            errors = {str(row["etf"]).zfill(6): str(row["error"]) for _, row in error_df.iterrows()}
    report = append_failed_rows(report, expected_codes, errors)
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


def build_us_final_tables(out_dir: Path, expected_codes: list[str] | None = None) -> pd.DataFrame:
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
        "股息率覆盖权重%",
        "PB覆盖权重%",
    ]
    for column in numeric_columns:
        if column in metrics.columns:
            metrics[column] = pd.to_numeric(metrics[column], errors="coerce")
    holding_errors = column_or_default(metrics, "错误", "").fillna("").astype(str)
    metrics["_rankable"] = (
        holding_errors.eq("")
        & pd.to_numeric(column_or_default(metrics, "股息率覆盖权重%"), errors="coerce").ge(MIN_RANK_COVERAGE)
        & pd.to_numeric(metrics["股息率%"], errors="coerce").notna()
    )
    metrics = metrics.sort_values(["_rankable", "股息率%"], ascending=[False, False], na_position="last").reset_index(drop=True)
    ranks = pd.Series(pd.NA, index=metrics.index, dtype="Int64")
    ranks.loc[metrics["_rankable"]] = range(1, int(metrics["_rankable"].sum()) + 1)
    report = pd.DataFrame(
        {
            "排名_按股息率": ranks,
            "ETF代码": metrics["ETF代码"].astype(str).str.zfill(6),
            "ETF名称": metrics["ETF名称"],
            "持仓期": metrics["持仓期"],
            "持仓来源": metrics["持仓来源"],
            "穿透口径": column_or_default(metrics, "穿透口径", "PCF申赎篮子估算"),
            "美股持仓权重%": metrics["股票权重合计%"],
            "股息率%": metrics["股息率%"],
            "股息率覆盖权重%": column_or_default(metrics, "股息率覆盖权重%"),
            "PE": metrics["PE"],
            "PB": metrics["PB"],
            "PB覆盖权重%": column_or_default(metrics, "PB覆盖权重%"),
            "年化收益%": metrics["年化收益%"],
            "索提诺比率": metrics["索提诺比率"],
            "波动率%": metrics["波动率%"],
            "近半年收益%": metrics["近半年收益%"],
            "近一年收益%": metrics["近一年收益%"],
            "近3年收益%": metrics["近3年收益%"],
            "收益来源": metrics["收益来源"],
            "收益口径": column_or_default(metrics, "收益口径"),
            "收益币种": column_or_default(metrics, "收益币种", "CNY"),
            "收益区间": metrics["收益区间"],
            "风险指标区间": metrics["风险指标区间"],
            "PE覆盖权重%": metrics["PE覆盖权重%"],
            "负PE权重%": metrics["负PE权重%"],
            "数据状态": ["有效" if rankable else ("失败" if error else "覆盖不足") for rankable, error in zip(metrics["_rankable"], column_or_default(metrics, "错误", "").fillna("").astype(str))],
            "错误": column_or_default(metrics, "错误", ""),
        }
    )
    errors = {
        str(code).zfill(6): str(error)
        for code, error in zip(metrics["ETF代码"], column_or_default(metrics, "错误", ""))
        if str(error or "")
    }
    report = append_failed_rows(report, expected_codes, errors)
    report.to_csv(out_dir / FINAL_CSV, index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(out_dir / FINAL_XLSX, engine="openpyxl") as writer:
        report.to_excel(writer, index=False, sheet_name="ETF穿透汇总")
    return report


def build_final_tables(out_dir: Path, market: str = "hk", expected_codes: list[str] | None = None) -> pd.DataFrame:
    if market == "us":
        return build_us_final_tables(out_dir, expected_codes)
    return build_hk_final_tables(out_dir, expected_codes)


def clean_intermediates(out_dir: Path) -> None:
    names = {
        "summary.csv", "summary.md", "errors.csv", "errors.md", "ranked_top5.csv", "ranked_top5.md",
        "metrics_summary.csv", "lookthrough_summary.csv", "lookthrough_detail.csv", "etf_summary.csv",
        "lookthrough_report.xlsx", "enhanced_report.md", "enhanced_report.html", "run_summary.json", "run_manifest.json",
        "classified_detail.csv", "structure_analysis.csv", "market_board_exposure.csv", "industry_theme_exposure.csv",
        "valuation_buckets.csv", "profit_quality.csv", "dividend_quality.csv", "risk_analysis.csv", "overlap_pairs.csv",
        "common_holdings.csv", "pcf_quality.csv", "cross_validation.csv", "constraint_checks.csv", "historical_tracking.csv",
    }
    paths = {out_dir / name for name in names}
    for pattern in ("*_lookthrough.json", "*_lookthrough.md", "*_holdings.csv"):
        paths.update(out_dir.glob(pattern))
    for path in paths:
        if path.is_file() and path.name not in {FINAL_CSV, FINAL_XLSX}:
            path.unlink()


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    codes = load_codes(args)
    try:
        run_lookthrough(args, codes, out_dir)
        report = build_final_tables(out_dir, args.market, codes)
    except subprocess.CalledProcessError as exc:
        errors: dict[str, str] = {}
        error_path = out_dir / "errors.csv"
        if error_path.exists():
            error_df = pd.read_csv(error_path, encoding="utf-8-sig")
            if {"etf", "error"}.issubset(error_df.columns):
                errors = {str(row["etf"]).zfill(6): str(row["error"]) for _, row in error_df.iterrows()}
        report = pd.DataFrame(
            {
                "排名_按股息率": pd.Series([pd.NA] * len(codes), dtype="Int64"),
                "ETF代码": codes,
                "数据状态": "失败",
                "错误": [errors.get(code, f"look-through process exited with code {exc.returncode}") for code in codes],
            }
        )
        report.to_csv(out_dir / FINAL_CSV, index=False, encoding="utf-8-sig")
        with pd.ExcelWriter(out_dir / FINAL_XLSX, engine="openpyxl") as writer:
            report.to_excel(writer, index=False, sheet_name="ETF穿透汇总")
    if not args.keep_intermediates:
        clean_intermediates(out_dir)
    print(out_dir / FINAL_XLSX)
    print(out_dir / FINAL_CSV)
    print(f"rows={len(report)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
