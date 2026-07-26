#!/usr/bin/env python
"""Look through selected A-listed ETFs using exchange PCF data.

This script is intentionally deterministic and does not require any model
capability. It pulls the latest PCF data, keeps stock components, fills missing
A-share PCF weights when possible, and exports portfolio look-through tables.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from pcf_common import (
    annualized_return as common_annualized_return,
    combine_price_series,
    earnings_yield_pe as common_earnings_yield_pe,
    normalize_price_series as common_normalize_price_series,
    price_series_metrics,
    publish_staged_files,
    weighted_average as common_weighted_average,
)
from pcf_enhanced_analytics import (
    ConstraintConfig,
    build_enhanced_outputs,
    build_html_report,
    build_markdown_report,
)


WORKSPACE = Path(__file__).resolve().parent
DEFAULT_A_SCRIPT = WORKSPACE / "run_a_share_dividend_etf_pcf_metrics.py"
DEFAULT_HK_SCRIPT = WORKSPACE / "pcf_lookthrough.py"
DEFAULT_US_SCRIPT = WORKSPACE / "us_etf_lookthrough.py"
DEFAULT_US_LISTED_SCRIPT = WORKSPACE / "us_listed_etf_lookthrough.py"

C_STOCK_CODE = "\u80a1\u7968\u4ee3\u7801"
C_STOCK_NAME = "\u80a1\u7968\u540d\u79f0"
C_MARKET = "\u5e02\u573a"
C_A_WEIGHT = "\u6743\u91cd%"
C_HK_WEIGHT = "\u5360\u51c0\u503c\u6bd4\u4f8b"
C_QUANTITY = "\u6570\u91cf"
C_PRICE = "\u9690\u542b\u4ef7\u683c"
C_WEIGHT_SOURCE = "\u6743\u91cd\u6765\u6e90"
C_PERIOD = "\u6301\u4ed3\u671f"
C_SOURCE = "\u6301\u4ed3\u6765\u6e90"
C_DETAIL_SOURCE = "\u6765\u6e90\u660e\u7ec6"

OUT_DETAIL = "lookthrough_detail.csv"
OUT_SUMMARY = "lookthrough_summary.csv"
OUT_ETF_SUMMARY = "etf_summary.csv"
OUT_METRICS = "metrics_summary.csv"
OUT_JSON = "run_summary.json"
OUT_XLSX = "lookthrough_report.xlsx"
OUT_MARKDOWN = "enhanced_report.md"
OUT_HTML = "enhanced_report.html"

ENHANCED_CSV_OUTPUTS = {
    "classified_detail": "classified_detail.csv",
    "structure_analysis": "structure_analysis.csv",
    "market_board_exposure": "market_board_exposure.csv",
    "industry_theme_exposure": "industry_theme_exposure.csv",
    "valuation_buckets": "valuation_buckets.csv",
    "profit_quality": "profit_quality.csv",
    "dividend_quality": "dividend_quality.csv",
    "risk_analysis": "risk_analysis.csv",
    "overlap_pairs": "overlap_pairs.csv",
    "common_holdings": "common_holdings.csv",
    "pcf_quality": "pcf_quality.csv",
    "cross_validation": "cross_validation.csv",
    "constraint_checks": "constraint_checks.csv",
    "historical_tracking": "historical_tracking.csv",
}

ENHANCED_SHEETS = {
    "classified_detail": "\u5206\u7c7b\u660e\u7ec6",
    "structure_analysis": "\u7ed3\u6784\u5206\u6790",
    "market_board_exposure": "\u5e02\u573a\u677f\u5757",
    "industry_theme_exposure": "\u884c\u4e1a\u4e3b\u9898",
    "valuation_buckets": "\u4f30\u503c\u5206\u5c42",
    "profit_quality": "\u76c8\u5229\u8d28\u91cf",
    "dividend_quality": "\u5206\u7ea2\u8d28\u91cf",
    "risk_analysis": "\u98ce\u9669\u6307\u6807",
    "overlap_pairs": "\u91cd\u5408\u5ea6",
    "common_holdings": "\u5171\u540c\u6301\u4ed3",
    "pcf_quality": "\u0050\u0043\u0046\u8d28\u91cf",
    "cross_validation": "\u4ea4\u53c9\u9a8c\u8bc1",
    "constraint_checks": "\u7ea6\u675f\u68c0\u67e5",
    "historical_tracking": "\u5386\u53f2\u8ffd\u8e2a",
}

C_ETF_CODE = "\u0045\u0054\u0046\u4ee3\u7801"
C_ETF_NAME = "\u0045\u0054\u0046\u540d\u79f0"
C_ETF_WEIGHT = "\u0045\u0054\u0046\u6743\u91cd%"
C_MODE = "\u7a7f\u900f\u6a21\u5f0f"
C_UNDERLYING_MARKET = "\u5e95\u5c42\u5e02\u573a"
C_ETF_INNER_WEIGHT = "\u0045\u0054\u0046\u5185\u6743\u91cd%"
C_PORTFOLIO_WEIGHT = "\u7ec4\u5408\u7a7f\u900f\u6743\u91cd%"
C_STOCK_PRICE = "\u4f30\u503c\u4ef7\u683c"
C_STOCK_PE = "\u80a1\u7968\u0050\u0045"
C_STOCK_PB = "\u80a1\u7968\u0050\u0042"
C_STOCK_DY = "\u80a1\u7968\u80a1\u606f\u7387%"
C_VALUATION_SOURCE = "\u4f30\u503c\u6765\u6e90"
C_VALUATION_ERROR = "\u4f30\u503c\u9519\u8bef"
C_DIVIDEND_SOURCE = "\u80a1\u606f\u7387\u6765\u6e90"

C_TYPE = "\u7c7b\u578b"
C_HOLDING_WEIGHT_TOTAL = "\u80a1\u7968\u6743\u91cd\u5408\u8ba1%"
C_STOCK_COUNT = "\u80a1\u7968\u6570"
C_DIVIDEND_YIELD = "\u80a1\u606f\u7387%"
C_ANNUAL_RETURN = "\u5e74\u5316\u6536\u76ca%"
C_SORTINO = "\u7d22\u63d0\u8bfa\u6bd4\u7387"
C_VOLATILITY = "\u6ce2\u52a8\u7387%"
C_HALF_YEAR_RETURN = "\u8fd1\u534a\u5e74\u6536\u76ca%"
C_ONE_YEAR_RETURN = "\u8fd1\u4e00\u5e74\u6536\u76ca%"
C_THREE_YEAR_RETURN = "\u8fd13\u5e74\u6536\u76ca%"
C_RETURN_SOURCE = "\u6536\u76ca\u6765\u6e90"
C_RETURN_WINDOW = "\u6536\u76ca\u533a\u95f4"
C_RISK_WINDOW = "\u98ce\u9669\u6307\u6807\u533a\u95f4"
C_RETURN_ERROR = "\u6536\u76ca\u9519\u8bef"
C_RETURN_BASIS = "\u6536\u76ca\u53e3\u5f84"
C_RETURN_CURRENCY = "\u6536\u76ca\u5e01\u79cd"
C_PORTFOLIO_METHOD = "\u7ec4\u5408\u6784\u9020"
C_MAX_DRAWDOWN = "\u6700\u5927\u56de\u64a4%"
C_SHARPE = "Sharpe Ratio"
C_CALMAR = "Calmar Ratio"
C_BETA = "Beta"
C_VAR_95 = "VaR95%"
C_CVAR_95 = "CVaR95%"
C_DOWNSIDE_VOL = "\u4e0b\u884c\u6ce2\u52a8\u7387%"
C_PE_COVERAGE = "\u0050\u0045\u8986\u76d6\u6743\u91cd%"
C_DY_COVERAGE = "\u80a1\u606f\u7387\u8986\u76d6\u6743\u91cd%"
C_PB_COVERAGE = "\u0050\u0042\u8986\u76d6\u6743\u91cd%"
C_NEGATIVE_PE_WEIGHT = "\u8d1f\u0050\u0045\u6743\u91cd%"
C_ERROR = "\u9519\u8bef"
C_LOOKTHROUGH_BASIS = "\u7a7f\u900f\u53e3\u5f84"
C_RAW_STOCK_ROWS = "PCF\u539f\u59cb\u80a1\u7968\u884c\u6570"
C_EFFECTIVE_STOCK_ROWS = "\u6709\u6548\u6743\u91cd\u884c\u6570"
C_MISSING_WEIGHT_ROWS = "\u672a\u5b9a\u4ef7/\u7f3a\u5931\u6743\u91cd\u884c\u6570"


@dataclass(frozen=True)
class SelectedETF:
    code: str
    weight: float
    mode: str


class NoApplicableHoldings(RuntimeError):
    pass


def import_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def retry(callable_obj: Callable[[], Any], tries: int = 3, sleep: float = 1.2) -> Any:
    last_exc: Exception | None = None
    for attempt in range(tries):
        try:
            return callable_obj()
        except Exception as exc:  # noqa: BLE001 - data sources often fail transiently
            last_exc = exc
            if attempt < tries - 1:
                time.sleep(sleep * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def normalize_etf_code(value: Any) -> str:
    text = str(value).strip().upper()
    if text.endswith(".US"):
        ticker = text[:-3]
        if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker):
            return f"{ticker}.US"
    match = re.fullmatch(r"(\d{1,6})(?:\.0)?", text)
    if not match:
        raise ValueError(f"Invalid ETF code: {value!r}")
    code = match.group(1)
    return code.zfill(6)


def parse_codes(raw: str) -> list[str]:
    codes = [normalize_etf_code(item) for item in re.split(r"[,;\s]+", raw.strip()) if item.strip()]
    if not codes:
        raise ValueError("No ETF codes were supplied.")
    if len(set(codes)) != len(codes):
        raise ValueError("Duplicate ETF codes are not allowed; combine their weights before running.")
    return codes


def parse_weights(raw: str | None, count: int) -> list[float]:
    if raw is None or raw.strip() == "":
        return [1.0 / count] * count
    values = [float(item) for item in re.split(r"[,;\s]+", raw.strip()) if item.strip()]
    if len(values) != count:
        raise ValueError(f"--weights count ({len(values)}) must match ETF count ({count}).")
    if any(not math.isfinite(value) for value in values):
        raise ValueError("--weights must contain only finite numbers.")
    if any(value < 0 for value in values):
        raise ValueError("--weights cannot contain negative values.")
    total = sum(values)
    if total <= 0:
        raise ValueError("--weights total must be positive.")
    if total > 1.5:
        values = [value / 100.0 for value in values]
        total = sum(values)
    return [value / total for value in values]


def parse_modes(raw: str | None, count: int) -> list[str]:
    if raw is None or raw.strip() == "":
        return ["auto"] * count
    values = [item.strip().lower() for item in re.split(r"[,;\s]+", raw.strip()) if item.strip()]
    if len(values) == 1 and count > 1:
        values = values * count
    if len(values) != count:
        raise ValueError(f"--markets count ({len(values)}) must match ETF count ({count}).")
    allowed = {"auto", "a", "hk", "us", "us_listed"}
    invalid = sorted(set(values) - allowed)
    if invalid:
        raise ValueError(f"Invalid --markets value(s): {', '.join(invalid)}")
    return values


def selected_etfs(codes_raw: str, weights_raw: str | None, markets_raw: str | None) -> list[SelectedETF]:
    codes = parse_codes(codes_raw)
    weights = parse_weights(weights_raw, len(codes))
    modes = parse_modes(markets_raw, len(codes))
    modes = ["us_listed" if code.endswith(".US") and mode == "auto" else mode for code, mode in zip(codes, modes)]
    return [SelectedETF(code=code, weight=weight, mode=mode) for code, weight, mode in zip(codes, weights, modes)]


def maybe_load_name_map() -> dict[str, str]:
    paths = [
        WORKSPACE / "lookthrough-a-share-dividend-keywords-pcf-metrics/a_share_dividend_etf_pcf_metrics.csv",
        WORKSPACE / "lookthrough-dividend-keywords-pcf-metrics/pcf_full_metrics_table.csv",
        WORKSPACE / "lookthrough-hk-all-ranking-pcf-risk/pcf_full_metrics_table.csv",
    ]
    name_map: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, encoding="utf-8-sig")
        except Exception:
            continue
        code_col = "\u0045\u0054\u0046\u4ee3\u7801"
        name_col = "\u0045\u0054\u0046\u540d\u79f0"
        if code_col not in df.columns or name_col not in df.columns:
            continue
        for _, row in df.iterrows():
            try:
                name_map[normalize_etf_code(row[code_col])] = str(row[name_col])
            except Exception:
                continue
    return name_map


def fill_missing_a_weights(a_module: Any, holdings: pd.DataFrame, stock_mask: pd.Series) -> pd.DataFrame:
    holdings = holdings.copy()
    holdings[C_A_WEIGHT] = pd.to_numeric(holdings[C_A_WEIGHT], errors="coerce")
    missing_mask = stock_mask & holdings[C_A_WEIGHT].isna()
    if not missing_mask.any():
        return holdings

    missing_codes: list[str] = []
    for _, row in holdings.loc[missing_mask].iterrows():
        stock_code = a_module.normalize_stock_code(row[C_STOCK_CODE])
        price = a_module.clean_float(row.get(C_PRICE))
        if stock_code and (price is None or price <= 0):
            missing_codes.append(stock_code)
    price_map = retry(lambda: a_module.fetch_sina_prices(sorted(set(missing_codes)))) if missing_codes else {}

    for idx, row in holdings.loc[missing_mask].iterrows():
        stock_code = a_module.normalize_stock_code(row[C_STOCK_CODE])
        quantity = a_module.clean_float(row.get(C_QUANTITY))
        price = a_module.clean_float(row.get(C_PRICE))
        if price is None or price <= 0:
            price = price_map.get(stock_code)
        nav_per_cu = a_module.clean_float(row.get("NAVperCU"))
        if quantity and price and nav_per_cu:
            holdings.at[idx, C_A_WEIGHT] = quantity * price / nav_per_cu * 100
            holdings.at[idx, C_PRICE] = price
            holdings.at[idx, C_WEIGHT_SOURCE] = "ComponentShare*Sina latest price/NAVperCU"
    return holdings


def get_a_stock_holdings(a_module: Any, etf: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    holdings, period, source = retry(lambda: a_module.get_pcf_holdings(etf, {}))
    stock_mask = holdings[C_MARKET].isin(a_module.A_MARKETS) & holdings[C_STOCK_CODE].astype(str).map(a_module.is_a_stock_code)
    holdings = fill_missing_a_weights(a_module, holdings, stock_mask)
    raw_stock_rows = int(stock_mask.sum())
    missing_weight_rows = int(pd.to_numeric(holdings.loc[stock_mask, C_A_WEIGHT], errors="coerce").isna().sum())
    if C_PRICE not in holdings.columns:
        holdings[C_PRICE] = None
    result = holdings.loc[stock_mask, [C_STOCK_CODE, C_STOCK_NAME, C_MARKET, C_A_WEIGHT, C_PRICE, C_WEIGHT_SOURCE]].copy()
    result[C_STOCK_CODE] = result[C_STOCK_CODE].astype(str).map(lambda value: re.sub(r"\D", "", value).zfill(6))
    result[C_MARKET] = "A"
    result.rename(columns={C_A_WEIGHT: C_ETF_INNER_WEIGHT, C_PRICE: C_STOCK_PRICE}, inplace=True)
    result[C_ETF_INNER_WEIGHT] = pd.to_numeric(result[C_ETF_INNER_WEIGHT], errors="coerce")
    result[C_STOCK_PRICE] = pd.to_numeric(result[C_STOCK_PRICE], errors="coerce")
    result = result.dropna(subset=[C_ETF_INNER_WEIGHT])
    if result.empty:
        raise NoApplicableHoldings("A-share parser returned no weighted A-share stock rows")
    meta = {
        C_PERIOD: period,
        C_SOURCE: source,
        C_MODE: "a",
        C_STOCK_COUNT: int(len(result)),
        C_RAW_STOCK_ROWS: raw_stock_rows,
        C_EFFECTIVE_STOCK_ROWS: int(len(result)),
        C_MISSING_WEIGHT_ROWS: missing_weight_rows,
        "\u0045\u0054\u0046\u5185\u80a1\u7968\u6743\u91cd\u5408\u8ba1%": float(result[C_ETF_INNER_WEIGHT].sum()),
    }
    return result, meta


def get_hk_stock_holdings(hk_module: Any, etf: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    holdings, period = retry(lambda: hk_module.get_pcf_holdings(etf))
    stock_mask = holdings[C_STOCK_CODE].astype(str).str.strip().str.fullmatch(r"\d{1,5}")
    raw_stock_rows = int(stock_mask.sum())
    missing_weight_rows = int(pd.to_numeric(holdings.loc[stock_mask, C_HK_WEIGHT], errors="coerce").isna().sum())
    result = holdings.loc[stock_mask, [C_STOCK_CODE, C_STOCK_NAME, C_HK_WEIGHT]].copy()
    result[C_STOCK_CODE] = result[C_STOCK_CODE].astype(str).map(lambda value: re.sub(r"\D", "", value).zfill(5))
    result[C_MARKET] = "HK"
    result[C_WEIGHT_SOURCE] = "PCF look-through"
    result[C_STOCK_PRICE] = None
    result.rename(columns={C_HK_WEIGHT: C_ETF_INNER_WEIGHT}, inplace=True)
    result[C_ETF_INNER_WEIGHT] = pd.to_numeric(result[C_ETF_INNER_WEIGHT], errors="coerce")
    result = result.dropna(subset=[C_ETF_INNER_WEIGHT])
    if result.empty:
        raise NoApplicableHoldings("HK parser returned no weighted Hong Kong stock rows")
    meta = {
        C_PERIOD: period,
        C_SOURCE: "pcf_lookthrough",
        C_MODE: "hk",
        C_STOCK_COUNT: int(len(result)),
        C_RAW_STOCK_ROWS: raw_stock_rows,
        C_EFFECTIVE_STOCK_ROWS: int(len(result)),
        C_MISSING_WEIGHT_ROWS: missing_weight_rows,
        "\u0045\u0054\u0046\u5185\u80a1\u7968\u6743\u91cd\u5408\u8ba1%": float(result[C_ETF_INNER_WEIGHT].sum()),
    }
    return result, meta


def get_us_stock_holdings(us_module: Any, etf: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    holdings, period, source = retry(lambda: us_module.get_pcf_holdings(etf))
    if C_STOCK_PRICE not in holdings.columns and C_PRICE in holdings.columns:
        holdings[C_STOCK_PRICE] = holdings[C_PRICE]
    needed = [C_STOCK_CODE, C_STOCK_NAME, C_MARKET, C_A_WEIGHT, C_STOCK_PRICE, C_WEIGHT_SOURCE]
    for column in needed + [C_DETAIL_SOURCE, C_VALUATION_ERROR]:
        if column not in holdings.columns:
            holdings[column] = None
    stock_mask = holdings[C_MARKET].eq("US")
    raw_stock_rows = int(stock_mask.sum())
    missing_weight_rows = int(pd.to_numeric(holdings.loc[stock_mask, C_A_WEIGHT], errors="coerce").isna().sum())
    result = holdings.loc[stock_mask, needed + [C_DETAIL_SOURCE, C_VALUATION_ERROR]].copy()
    result[C_STOCK_CODE] = result[C_STOCK_CODE].astype(str).map(us_module.normalize_us_ticker)
    result[C_MARKET] = "US"
    result.rename(columns={C_A_WEIGHT: C_ETF_INNER_WEIGHT}, inplace=True)
    result[C_ETF_INNER_WEIGHT] = pd.to_numeric(result[C_ETF_INNER_WEIGHT], errors="coerce")
    result[C_STOCK_PRICE] = pd.to_numeric(result[C_STOCK_PRICE], errors="coerce")
    result = result.dropna(subset=[C_ETF_INNER_WEIGHT])
    if result.empty:
        raise NoApplicableHoldings("US parser returned no weighted US stock rows")
    meta = {
        C_PERIOD: period,
        C_SOURCE: source,
        C_MODE: "us",
        C_STOCK_COUNT: int(len(result)),
        C_RAW_STOCK_ROWS: raw_stock_rows,
        C_EFFECTIVE_STOCK_ROWS: int(len(result)),
        C_MISSING_WEIGHT_ROWS: missing_weight_rows,
        "\u0045\u0054\u0046\u5185\u80a1\u7968\u6743\u91cd\u5408\u8ba1%": float(result[C_ETF_INNER_WEIGHT].sum()),
    }
    return result, meta


def get_us_listed_stock_holdings(us_listed_module: Any, etf: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    ticker = us_listed_module.normalize_us_ticker(etf)
    _summary, etf_summary, detail, _sources, failed = us_listed_module.build_tables([ticker], [1.0], "auto", cache_dir=None)
    if detail.empty:
        raise RuntimeError(f"No US-listed ETF holdings for {etf}: {failed}")
    result = pd.DataFrame(
        {
            C_STOCK_CODE: detail[us_listed_module.C_STOCK_CODE].astype(str).map(
                lambda value: value
                if value.upper().startswith(("CUSIP:", "UNMAPPED:"))
                else us_listed_module.normalize_us_ticker(value)
            ),
            C_STOCK_NAME: detail[us_listed_module.C_STOCK_NAME],
            C_MARKET: detail[us_listed_module.C_MARKET],
            C_ETF_INNER_WEIGHT: pd.to_numeric(detail[us_listed_module.C_INNER_WEIGHT], errors="coerce"),
            C_STOCK_PRICE: pd.to_numeric(detail.get(us_listed_module.C_PRICE), errors="coerce"),
            C_WEIGHT_SOURCE: detail.get(us_listed_module.C_WEIGHT_SOURCE, ""),
            C_DETAIL_SOURCE: detail.get(us_listed_module.C_SOURCE_DETAIL, ""),
            C_VALUATION_ERROR: detail.get(us_listed_module.C_VAL_ERROR, ""),
        }
    ).dropna(subset=[C_ETF_INNER_WEIGHT])
    if result.empty:
        raise RuntimeError(f"No weighted US-listed ETF holdings for {etf}")
    row = etf_summary.iloc[0] if not etf_summary.empty else {}
    meta = {
        C_PERIOD: row.get(us_listed_module.C_PERIOD, ""),
        C_SOURCE: row.get(us_listed_module.C_SOURCE, "us_listed"),
        C_MODE: "us_listed",
        C_STOCK_COUNT: int(len(result)),
        C_RAW_STOCK_ROWS: int(len(detail)),
        C_EFFECTIVE_STOCK_ROWS: int(len(result)),
        C_MISSING_WEIGHT_ROWS: int(pd.to_numeric(detail[us_listed_module.C_INNER_WEIGHT], errors="coerce").isna().sum()),
        "\u0045\u0054\u0046\u5185\u80a1\u7968\u6743\u91cd\u5408\u8ba1%": float(result[C_ETF_INNER_WEIGHT].sum()),
        C_ETF_NAME: row.get(us_listed_module.C_ETF_NAME, ticker),
    }
    return result, meta


def resolve_holdings(
    selected: SelectedETF,
    a_module: Any,
    hk_module: Any,
    us_module: Any,
    min_auto_weight: float,
    us_listed_module: Any | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if selected.mode == "hk":
        return get_hk_stock_holdings(hk_module, selected.code)
    if selected.mode == "a":
        return get_a_stock_holdings(a_module, selected.code)
    if selected.mode == "us":
        return get_us_stock_holdings(us_module, selected.code)
    if selected.mode == "us_listed":
        if us_listed_module is None:
            raise RuntimeError("us_listed mode requires --us-listed-script")
        return get_us_listed_stock_holdings(us_listed_module, selected.code)

    candidates: list[tuple[pd.DataFrame, dict[str, Any]]] = []
    errors: dict[str, str] = {}
    try:
        hk_df, hk_meta = get_hk_stock_holdings(hk_module, selected.code)
        candidates.append((hk_df, hk_meta))
    except NoApplicableHoldings:
        pass
    except Exception as exc:  # noqa: BLE001
        errors["HK"] = str(exc)

    try:
        a_df, a_meta = get_a_stock_holdings(a_module, selected.code)
        candidates.append((a_df, a_meta))
    except NoApplicableHoldings:
        pass
    except Exception as exc:  # noqa: BLE001
        errors["A"] = str(exc)

    try:
        us_df, us_meta = get_us_stock_holdings(us_module, selected.code)
        candidates.append((us_df, us_meta))
    except NoApplicableHoldings:
        pass
    except Exception as exc:  # noqa: BLE001
        errors["US"] = str(exc)

    candidates = [(frame, meta) for frame, meta in candidates if not frame.empty]
    if candidates:
        combined = pd.concat([frame for frame, _ in candidates], ignore_index=True)
        if {C_MARKET, C_STOCK_CODE}.issubset(combined.columns):
            combined = combined.drop_duplicates([C_MARKET, C_STOCK_CODE], keep="first").reset_index(drop=True)
        total_weight = float(pd.to_numeric(combined[C_ETF_INNER_WEIGHT], errors="coerce").fillna(0).sum())
        if total_weight < min_auto_weight:
            details = "; ".join(f"{market} parser: {error}" for market, error in errors.items())
            raise RuntimeError(
                f"auto mode resolved only {total_weight:.2f}% stock weight, below {min_auto_weight:.2f}%."
                + (f" {details}" if details else "")
            )
        periods = sorted({str(meta.get(C_PERIOD) or "") for _, meta in candidates if meta.get(C_PERIOD)})
        sources = sorted({str(meta.get(C_SOURCE) or "") for _, meta in candidates if meta.get(C_SOURCE)})
        names = [str(meta.get(C_ETF_NAME) or "") for _, meta in candidates if meta.get(C_ETF_NAME)]
        parser_errors = "; ".join(f"{market} parser: {error}" for market, error in errors.items())
        meta = {
            C_ETF_NAME: names[0] if names else "",
            C_MODE: "auto",
            C_PERIOD: ";".join(periods),
            C_SOURCE: ";".join(sources),
            C_STOCK_COUNT: len(combined),
            C_RAW_STOCK_ROWS: sum(int(meta.get(C_RAW_STOCK_ROWS, len(frame)) or 0) for frame, meta in candidates),
            C_EFFECTIVE_STOCK_ROWS: len(combined),
            C_MISSING_WEIGHT_ROWS: sum(int(meta.get(C_MISSING_WEIGHT_ROWS, 0) or 0) for _, meta in candidates),
            "\u0045\u0054\u0046\u5185\u80a1\u7968\u6743\u91cd\u5408\u8ba1%": total_weight,
            C_ERROR: f"auto mode incomplete: {parser_errors}" if parser_errors else "",
        }
        return combined, meta
    raise RuntimeError("auto mode failed. " + "; ".join(f"{market} parser: {error}" for market, error in errors.items()))


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    text = str(value).strip().replace(",", "")
    if text.endswith("%"):
        text = text[:-1]
    if text in {"", "-", "--", "None", "nan", "NaN", "NaT"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def first_valid(values: pd.Series) -> Any:
    valid = values.dropna()
    return None if valid.empty else valid.iloc[0]


def weighted_average_metric(rows: list[dict[str, Any]], key: str, positive_only: bool = False) -> tuple[float | None, float]:
    return common_weighted_average(rows, key, positive_only=positive_only)


def earnings_yield_pe_metric(rows: list[dict[str, Any]]) -> tuple[float | None, float, float]:
    return common_earnings_yield_pe(rows)


def aggregate_valuation_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pe, pe_cov, neg_pe = earnings_yield_pe_metric(rows)
    pb, pb_cov = weighted_average_metric(rows, "pb", positive_only=True)
    dy, dy_cov = weighted_average_metric(rows, "dividend_yield_pct")
    return {
        C_DIVIDEND_YIELD: dy,
        "PE": pe,
        "PB": pb,
        C_PE_COVERAGE: pe_cov,
        C_DY_COVERAGE: dy_cov,
        C_PB_COVERAGE: pb_cov,
        C_NEGATIVE_PE_WEIGHT: neg_pe,
        C_HOLDING_WEIGHT_TOTAL: sum(as_float(row.get("weight_pct")) or 0.0 for row in rows),
    }


def valuation_rows_from_detail(detail: pd.DataFrame, weight_col: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in detail.iterrows():
        rows.append(
            {
                "weight_pct": as_float(row.get(weight_col)) or 0.0,
                "pe": as_float(row.get(C_STOCK_PE)),
                "pb": as_float(row.get(C_STOCK_PB)),
                "dividend_yield_pct": as_float(row.get(C_STOCK_DY)),
            }
        )
    return rows


def best_column(df: pd.DataFrame, candidates: list[str], fallback_index: int) -> str | None:
    for candidate in candidates:
        if candidate in df.columns:
            return candidate
    if len(df.columns) > fallback_index:
        return str(df.columns[fallback_index])
    return None


def normalize_price_series(df: pd.DataFrame, date_col: str | None, value_col: str | None) -> pd.Series:
    if df is None or df.empty or date_col is None or value_col is None:
        return pd.Series(dtype=float)
    if date_col not in df.columns or value_col not in df.columns:
        return pd.Series(dtype=float)
    temp = df[[date_col, value_col]].copy()
    temp.columns = ["date", "value"]
    temp["date"] = pd.to_datetime(temp["date"], errors="coerce")
    temp["value"] = pd.to_numeric(temp["value"], errors="coerce")
    temp = temp.dropna(subset=["date", "value"]).sort_values("date").drop_duplicates("date", keep="last")
    if temp.empty:
        return pd.Series(dtype=float)
    return pd.Series(temp["value"].to_numpy(dtype=float), index=pd.DatetimeIndex(temp["date"]), name="value")


def etf_exchange_symbol(etf: str) -> str:
    return ("sh" if etf.startswith("5") else "sz") + etf


def fetch_etf_price_series(ak_module: Any, etf: str, lookback_days: int) -> tuple[pd.Series, str]:
    start = (date.today() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    errors: list[str] = []

    try:
        nav = retry(lambda: ak_module.fund_etf_fund_info_em(fund=etf, start_date=start, end_date=end), tries=2)
        date_col = best_column(nav, ["\u51c0\u503c\u65e5\u671f"], 0)
        value_col = "\u7d2f\u8ba1\u51c0\u503c" if "\u7d2f\u8ba1\u51c0\u503c" in nav.columns else best_column(nav, ["\u5355\u4f4d\u51c0\u503c"], 1)
        series = normalize_price_series(nav, date_col, value_col)
        if len(series) >= 30:
            return series, "eastmoney_nav"
        errors.append(f"eastmoney_nav: {len(series)} observations")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"eastmoney_nav: {exc}")

    try:
        price = retry(
            lambda: ak_module.fund_etf_hist_em(symbol=etf, period="daily", start_date=start, end_date=end, adjust="qfq"),
            tries=2,
        )
        series = normalize_price_series(price, best_column(price, ["\u65e5\u671f"], 0), best_column(price, ["\u6536\u76d8"], 2))
        if len(series) >= 30:
            return series, "eastmoney_price_qfq"
        errors.append(f"eastmoney_price_qfq: {len(series)} observations")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"eastmoney_price_qfq: {exc}")

    try:
        sina = retry(lambda: ak_module.fund_etf_hist_sina(symbol=etf_exchange_symbol(etf)), tries=2)
        series = normalize_price_series(sina, best_column(sina, ["date"], 0), best_column(sina, ["close"], 4))
        if len(series) >= 30:
            return series, "sina_price"
        errors.append(f"sina_price: {len(series)} observations")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"sina_price: {exc}")

    raise RuntimeError("; ".join(errors) or f"No return series for {etf}")


def annualized_from_values(first_value: float, last_value: float, first_date: date, last_date: date) -> float | None:
    annualized = common_annualized_return(first_value, last_value, first_date, last_date)
    return None if annualized is None else annualized * 100


def value_window(series: pd.Series, days_back: int, min_days: int) -> dict[str, Any]:
    series = series.dropna().sort_index()
    if len(series) < 2:
        return {"error": "not enough observations"}
    last_date = series.index[-1].date()
    target = last_date - timedelta(days=days_back)
    eligible = series[pd.Series(series.index.date, index=series.index) <= target]
    first_date = eligible.index[-1].date() if not eligible.empty else series.index[0].date()
    first_value = float(eligible.iloc[-1] if not eligible.empty else series.iloc[0])
    last_value = float(series.iloc[-1])
    days = (last_date - first_date).days
    if first_value <= 0 or last_value <= 0 or days <= 0:
        return {"error": "invalid values"}
    return {
        "total_return_pct": (last_value / first_value - 1.0) * 100,
        "annualized_return_pct": annualized_from_values(first_value, last_value, first_date, last_date),
        "first_date": first_date.isoformat(),
        "last_date": last_date.isoformat(),
        "days": days,
        "short_window": days < min_days,
    }


def trailing_return(series: pd.Series, days_back: int, min_days: int) -> float | None:
    window = value_window(series, days_back=days_back, min_days=min_days)
    if window.get("error") or window.get("short_window"):
        return None
    return as_float(window.get("total_return_pct"))


def summarize_price_series(series: pd.Series, source: str) -> dict[str, Any]:
    metrics = price_series_metrics(series)
    if metrics.get("error"):
        return {C_RETURN_SOURCE: source, C_RETURN_ERROR: metrics["error"]}
    adjusted = "nav" in source or "qfq" in source or "adjusted" in source
    return {
        C_ANNUAL_RETURN: metrics["annualized_return_pct"],
        C_SORTINO: metrics["sortino_ratio"],
        C_VOLATILITY: metrics["volatility_pct"],
        C_HALF_YEAR_RETURN: metrics["half_year_return_pct"],
        C_ONE_YEAR_RETURN: metrics["one_year_return_pct"],
        C_THREE_YEAR_RETURN: metrics["three_year_return_pct"],
        C_MAX_DRAWDOWN: metrics["max_drawdown_pct"],
        C_SHARPE: metrics["sharpe_ratio"],
        C_CALMAR: metrics["calmar_ratio"],
        C_BETA: None,
        C_VAR_95: metrics["var95_pct"],
        C_CVAR_95: metrics["cvar95_pct"],
        C_DOWNSIDE_VOL: metrics["downside_volatility_pct"],
        C_RETURN_SOURCE: source,
        C_RETURN_BASIS: "\u603b\u6536\u76ca/\u590d\u6743" if adjusted else "\u4ef7\u683c\u6536\u76ca(\u672a\u8ba1\u73b0\u91d1\u5206\u7ea2)",
        C_RETURN_CURRENCY: "CNY",
        C_RETURN_WINDOW: metrics["return_window"],
        C_RISK_WINDOW: metrics["risk_window"],
    }


def selected_price_series(selected: SelectedETF, ak_module: Any, us_listed_module: Any | None, lookback_days: int) -> tuple[pd.Series, str]:
    if selected.mode == "us_listed":
        if us_listed_module is None:
            raise RuntimeError("us_listed mode requires --us-listed-script for return metrics")
        ticker = us_listed_module.normalize_us_ticker(selected.code)
        series, source = us_listed_module.price_series(ticker, lookback_days, cache_dir=None)
        series = common_normalize_price_series(series)
        if len(series) < 30:
            raise RuntimeError(f"{source}: {len(series)} observations")
        if not hasattr(us_listed_module, "fx_series"):
            raise RuntimeError("US-listed ETF return conversion requires USD/CNY history")
        fx, fx_source = us_listed_module.fx_series(lookback_days, cache_dir=None)
        fx = common_normalize_price_series(fx)
        if fx.empty:
            raise RuntimeError("USD/CNY return series is empty")
        common_end = min(series.index[-1], fx.index[-1])
        aligned = pd.concat([series.rename("etf"), fx.rename("fx")], axis=1, sort=True).sort_index().loc[:common_end].ffill().dropna()
        if len(aligned) < 30:
            raise RuntimeError("USD/CNY-aligned return series is too short")
        return aligned["etf"] * aligned["fx"], f"{source}*{fx_source}"
    return fetch_etf_price_series(ak_module, selected.code, lookback_days=lookback_days)


def portfolio_return_metrics(selections: list[SelectedETF], ak_module: Any, us_listed_module: Any | None, lookback_days: int) -> dict[str, Any]:
    weight_by_code: dict[str, float] = {}
    selected_by_code: dict[str, SelectedETF] = {}
    for selected in selections:
        weight_by_code[selected.code] = weight_by_code.get(selected.code, 0.0) + selected.weight
        selected_by_code[selected.code] = selected

    series_map: dict[str, pd.Series] = {}
    source_map: dict[str, str] = {}
    errors: dict[str, str] = {}
    for etf in weight_by_code:
        try:
            series, source = selected_price_series(selected_by_code[etf], ak_module, us_listed_module, lookback_days=lookback_days)
            series_map[etf] = series.rename(etf)
            source_map[etf] = source
        except Exception as exc:  # noqa: BLE001
            errors[etf] = str(exc)
    if errors:
        return {C_RETURN_SOURCE: "weighted_etf_nav_or_price", C_RETURN_ERROR: json.dumps(errors, ensure_ascii=False)}
    if not series_map:
        return {C_RETURN_SOURCE: "weighted_etf_nav_or_price", C_RETURN_ERROR: "no ETF return series"}

    portfolio = combine_price_series(series_map, weight_by_code)
    if portfolio.empty or len(portfolio) < 2:
        return {C_RETURN_SOURCE: "weighted_etf_nav_or_price", C_RETURN_ERROR: "not enough aligned observations"}
    metrics = summarize_price_series(portfolio, "weighted_etf_nav_or_price")
    detail = ",".join(f"{code}:{source_map[code]}" for code in weight_by_code)
    metrics[C_RETURN_SOURCE] = f"weighted_etf_nav_or_price({detail})"
    metrics[C_PORTFOLIO_METHOD] = "\u521d\u59cb\u6743\u91cd\u4e70\u5165\u5e76\u6301\u6709"
    return metrics


def enrich_detail_stock_metrics(
    detail: pd.DataFrame,
    a_module: Any,
    hk_module: Any,
    us_module: Any,
    us_listed_module: Any | None,
    workers: int,
    hk_alt_limit: int,
    hk_sleep: float,
) -> pd.DataFrame:
    if detail.empty:
        return detail
    detail = detail.copy()
    for column in [C_STOCK_PRICE, C_STOCK_PE, C_STOCK_PB, C_STOCK_DY, C_VALUATION_SOURCE, C_VALUATION_ERROR, C_DIVIDEND_SOURCE]:
        if column not in detail.columns:
            detail[column] = None

    stock_metrics: dict[tuple[str, str], dict[str, Any]] = {}
    a_detail = detail[detail[C_UNDERLYING_MARKET].eq("A")]
    if not a_detail.empty:
        a_codes = sorted(set(a_detail[C_STOCK_CODE].astype(str)))
        price_map: dict[str, float | None] = {}
        missing_prices: list[str] = []
        for code in a_codes:
            prices = pd.to_numeric(a_detail.loc[a_detail[C_STOCK_CODE].astype(str).eq(code), C_STOCK_PRICE], errors="coerce")
            prices = prices[prices > 0]
            price = None if prices.empty else float(prices.iloc[0])
            price_map[code] = price
            if price is None:
                missing_prices.append(code)
        if missing_prices:
            try:
                fetched_prices = retry(lambda: a_module.fetch_sina_prices(missing_prices), tries=2)
                for code, price in fetched_prices.items():
                    price_map[code] = price
            except Exception as exc:  # noqa: BLE001
                print(f"A-share Sina price fallback failed: {exc}")
        try:
            a_metrics = a_module.enrich_stock_metrics(price_map, workers=workers)
        except Exception as exc:  # noqa: BLE001
            print(f"A-share stock metric lookups failed: {exc}")
            a_metrics = {}
        for code in a_codes:
            metric = a_metrics.get(code) or {}
            stock_metrics[("A", code)] = {
                C_STOCK_PRICE: metric.get("price") or price_map.get(code),
                C_STOCK_PE: metric.get("pe"),
                C_STOCK_PB: metric.get("pb"),
                C_STOCK_DY: metric.get("dividend_yield_pct"),
                C_DIVIDEND_SOURCE: metric.get("dividend_source", ""),
                C_VALUATION_SOURCE: "baidu_valuation+dividend_history",
                C_VALUATION_ERROR: "",
            }

    hk_detail = detail[detail[C_UNDERLYING_MARKET].eq("HK")]
    if not hk_detail.empty:
        hk_rows: list[dict[str, Any]] = []
        grouped = hk_detail.groupby(C_STOCK_CODE, dropna=False, as_index=False).agg(
            **{C_STOCK_NAME: (C_STOCK_NAME, "first"), C_PORTFOLIO_WEIGHT: (C_PORTFOLIO_WEIGHT, "sum")}
        )
        for _, row in grouped.iterrows():
            hk_rows.append(
                {
                    C_STOCK_CODE: str(row[C_STOCK_CODE]).zfill(5),
                    C_STOCK_NAME: row[C_STOCK_NAME],
                    C_HK_WEIGHT: as_float(row[C_PORTFOLIO_WEIGHT]) or 0.0,
                }
            )
        try:
            hk_metrics = hk_module.build_metrics(pd.DataFrame(hk_rows), alt_limit=hk_alt_limit, sleep_seconds=hk_sleep)
        except Exception as exc:  # noqa: BLE001
            print(f"HK stock metric lookups failed: {exc}")
            hk_metrics = []
        for item in hk_metrics:
            stock_metrics[("HK", str(item.code).zfill(5))] = {
                C_STOCK_PRICE: None,
                C_STOCK_PE: item.pe,
                C_STOCK_PB: item.pb,
                C_STOCK_DY: item.dividend_yield_pct,
                C_DIVIDEND_SOURCE: item.source,
                C_VALUATION_SOURCE: item.source,
                C_VALUATION_ERROR: item.valuation_error,
            }

    us_detail = detail[detail[C_UNDERLYING_MARKET].eq("US")]
    if not us_detail.empty:
        us_codes = sorted({us_module.normalize_us_ticker(code) for code in us_detail[C_STOCK_CODE].astype(str)})
        try:
            if us_listed_module is not None:
                us_frame = pd.DataFrame({us_listed_module.C_STOCK_CODE: us_codes, us_listed_module.C_MARKET: "US"})
                enriched = us_listed_module.enrich_metrics(us_frame, skip_metrics=False, workers=workers)
                us_metrics = {
                    str(row[us_listed_module.C_STOCK_CODE]): {
                        C_STOCK_PRICE: row.get(us_listed_module.C_PRICE),
                        C_STOCK_PE: row.get(us_listed_module.C_PE),
                        C_STOCK_PB: row.get(us_listed_module.C_PB),
                        C_STOCK_DY: row.get(us_listed_module.C_DY),
                        C_DIVIDEND_SOURCE: row.get(us_listed_module.C_VAL_SOURCE, ""),
                        C_VALUATION_SOURCE: row.get(us_listed_module.C_VAL_SOURCE, ""),
                        C_VALUATION_ERROR: row.get(us_listed_module.C_VAL_ERROR, ""),
                    }
                    for _, row in enriched.iterrows()
                }
            else:
                us_metrics = us_module.build_metrics(us_codes, workers=workers)
        except Exception as exc:  # noqa: BLE001
            print(f"US stock metric lookups failed: {exc}")
            us_metrics = {}
        for code in us_codes:
            item = us_metrics.get(code)
            if item is None:
                continue
            stock_metrics[("US", code)] = item if isinstance(item, dict) else {
                C_STOCK_PRICE: item.price,
                C_STOCK_PE: item.pe,
                C_STOCK_PB: item.pb,
                C_STOCK_DY: item.dividend_yield_pct,
                C_DIVIDEND_SOURCE: item.source,
                C_VALUATION_SOURCE: item.source,
                C_VALUATION_ERROR: item.error,
            }

    for idx, row in detail.iterrows():
        market = str(row.get(C_UNDERLYING_MARKET) or "")
        code = str(row.get(C_STOCK_CODE) or "")
        if market == "HK":
            code = code.zfill(5)
        elif market == "A":
            code = code.zfill(6)
        elif market == "US":
            code = us_module.normalize_us_ticker(code)
        metric = stock_metrics.get((market, code))
        if not metric:
            continue
        for column, value in metric.items():
            detail.at[idx, column] = value
    return detail


def build_tables(
    selections: list[SelectedETF],
    a_module: Any,
    hk_module: Any,
    us_module: Any,
    us_listed_module: Any | None,
    name_map: dict[str, str],
    min_auto_weight: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    detail_rows: list[dict[str, Any]] = []
    etf_rows: list[dict[str, Any]] = []
    for selected in selections:
        print(f"Reading latest PCF for ETF {selected.code} mode={selected.mode}...")
        try:
            holdings, meta = resolve_holdings(selected, a_module, hk_module, us_module, min_auto_weight, us_listed_module)
        except Exception as exc:  # noqa: BLE001 - retain failed ETF in portfolio audit output
            etf_rows.append(
                {
                    C_ETF_CODE: selected.code,
                    C_ETF_NAME: name_map.get(selected.code, ""),
                    C_ETF_WEIGHT: selected.weight * 100,
                    C_MODE: selected.mode,
                    C_STOCK_COUNT: 0,
                    C_RAW_STOCK_ROWS: 0,
                    C_EFFECTIVE_STOCK_ROWS: 0,
                    C_MISSING_WEIGHT_ROWS: 0,
                    C_ERROR: str(exc),
                }
            )
            continue
        etf_name = name_map.get(selected.code, "") or str(meta.get(C_ETF_NAME) or "")
        for _, row in holdings.iterrows():
            etf_weight_pct = selected.weight * 100
            holding_weight = float(row[C_ETF_INNER_WEIGHT])
            detail_rows.append(
                {
                    C_ETF_CODE: selected.code,
                    C_ETF_NAME: etf_name,
                    C_ETF_WEIGHT: etf_weight_pct,
                    C_MODE: meta[C_MODE],
                    C_UNDERLYING_MARKET: row[C_MARKET],
                    C_STOCK_CODE: row[C_STOCK_CODE],
                    C_STOCK_NAME: row[C_STOCK_NAME],
                    C_ETF_INNER_WEIGHT: holding_weight,
                    C_PORTFOLIO_WEIGHT: selected.weight * holding_weight,
                    C_STOCK_PRICE: row.get(C_STOCK_PRICE),
                    C_PERIOD: meta[C_PERIOD],
                    C_SOURCE: meta[C_SOURCE],
                    C_WEIGHT_SOURCE: row.get(C_WEIGHT_SOURCE, ""),
                    C_DETAIL_SOURCE: row.get(C_DETAIL_SOURCE, ""),
                    C_VALUATION_ERROR: row.get(C_VALUATION_ERROR, ""),
                }
            )
        etf_rows.append(
            {
                C_ETF_CODE: selected.code,
                C_ETF_NAME: etf_name,
                C_ETF_WEIGHT: selected.weight * 100,
                C_MODE: meta[C_MODE],
                C_PERIOD: meta[C_PERIOD],
                C_SOURCE: meta[C_SOURCE],
                C_LOOKTHROUGH_BASIS: (
                    "SEC N-PORT/\u53d1\u884c\u5546\u62a5\u544a\u6301\u4ed3"
                    if meta[C_MODE] == "us_listed"
                    else ("\u57fa\u91d1\u5b63\u62a5\u6301\u4ed3" if "reported" in str(meta[C_SOURCE]).lower() else "PCF\u7533\u8d4e\u7bee\u5b50\u4f30\u7b97")
                ),
                C_STOCK_COUNT: meta[C_STOCK_COUNT],
                C_RAW_STOCK_ROWS: meta.get(C_RAW_STOCK_ROWS, meta[C_STOCK_COUNT]),
                C_EFFECTIVE_STOCK_ROWS: meta.get(C_EFFECTIVE_STOCK_ROWS, meta[C_STOCK_COUNT]),
                C_MISSING_WEIGHT_ROWS: meta.get(C_MISSING_WEIGHT_ROWS, 0),
                C_ERROR: str(meta.get(C_ERROR) or ""),
                "\u0045\u0054\u0046\u5185\u80a1\u7968\u6743\u91cd\u5408\u8ba1%": meta[
                    "\u0045\u0054\u0046\u5185\u80a1\u7968\u6743\u91cd\u5408\u8ba1%"
                ],
            }
        )

    detail = pd.DataFrame(detail_rows)
    etf_summary = pd.DataFrame(etf_rows)
    if detail.empty:
        return pd.DataFrame(), detail, etf_summary
    summary = (
        detail.groupby([C_UNDERLYING_MARKET, C_STOCK_CODE], as_index=False)
        .agg(
            **{
                C_STOCK_NAME: (C_STOCK_NAME, first_valid),
                C_PORTFOLIO_WEIGHT: (C_PORTFOLIO_WEIGHT, "sum"),
                "\u8986\u76d6\u0045\u0054\u0046\u6570": (C_ETF_CODE, "nunique"),
            }
        )
        .sort_values(C_PORTFOLIO_WEIGHT, ascending=False)
        .reset_index(drop=True)
    )
    summary.insert(0, "\u6392\u540d", range(1, len(summary) + 1))
    return summary, detail, etf_summary


def safe_return_metrics(ak_module: Any, etf: str, lookback_days: int) -> dict[str, Any]:
    try:
        series, source = fetch_etf_price_series(ak_module, etf, lookback_days=lookback_days)
        return summarize_price_series(series, source)
    except Exception as exc:  # noqa: BLE001
        return {C_RETURN_SOURCE: "", C_RETURN_ERROR: str(exc)}


def safe_selected_return_metrics(selected: SelectedETF, ak_module: Any, us_listed_module: Any | None, lookback_days: int) -> dict[str, Any]:
    try:
        series, source = selected_price_series(selected, ak_module, us_listed_module, lookback_days=lookback_days)
        return summarize_price_series(series, source)
    except Exception as exc:  # noqa: BLE001
        return {C_RETURN_SOURCE: "", C_RETURN_ERROR: str(exc)}


def rebuild_summary_from_detail(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame()
    detail = detail.copy()
    for column in [C_STOCK_NAME, C_STOCK_PRICE, C_STOCK_PE, C_STOCK_PB, C_STOCK_DY, C_VALUATION_SOURCE, C_DIVIDEND_SOURCE, C_VALUATION_ERROR]:
        if column not in detail.columns:
            detail[column] = None
    summary = (
        detail.groupby([C_UNDERLYING_MARKET, C_STOCK_CODE], as_index=False)
        .agg(
            **{
                C_STOCK_NAME: (C_STOCK_NAME, first_valid),
                C_PORTFOLIO_WEIGHT: (C_PORTFOLIO_WEIGHT, "sum"),
                "\u8986\u76d6\u0045\u0054\u0046\u6570": (C_ETF_CODE, "nunique"),
                C_STOCK_PRICE: (C_STOCK_PRICE, first_valid),
                C_STOCK_PE: (C_STOCK_PE, first_valid),
                C_STOCK_PB: (C_STOCK_PB, first_valid),
                C_STOCK_DY: (C_STOCK_DY, first_valid),
                C_VALUATION_SOURCE: (C_VALUATION_SOURCE, first_valid),
                C_DIVIDEND_SOURCE: (C_DIVIDEND_SOURCE, first_valid),
                C_VALUATION_ERROR: (C_VALUATION_ERROR, first_valid),
            }
        )
        .sort_values(C_PORTFOLIO_WEIGHT, ascending=False)
        .reset_index(drop=True)
    )
    summary.insert(0, "\u6392\u540d", range(1, len(summary) + 1))
    return summary


def add_metrics_to_tables(
    selections: list[SelectedETF],
    summary: pd.DataFrame,
    detail: pd.DataFrame,
    etf_summary: pd.DataFrame,
    a_module: Any,
    hk_module: Any,
    us_module: Any,
    us_listed_module: Any | None,
    workers: int,
    hk_alt_limit: int,
    hk_sleep: float,
    lookback_days: int,
    skip_metrics: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not skip_metrics:
        print("Looking up stock-level PE/PB/dividend yield...")
        detail = enrich_detail_stock_metrics(
            detail,
            a_module=a_module,
            hk_module=hk_module,
            us_module=us_module,
            us_listed_module=us_listed_module,
            workers=workers,
            hk_alt_limit=hk_alt_limit,
            hk_sleep=hk_sleep,
        )
    summary = rebuild_summary_from_detail(detail)

    ak_module = getattr(a_module, "ak", None) or getattr(hk_module, "ak", None) or getattr(us_module, "ak", None)
    metric_rows: list[dict[str, Any]] = []
    etf_summary = etf_summary.copy()
    for idx, etf_row in etf_summary.iterrows():
        etf = normalize_etf_code(etf_row[C_ETF_CODE])
        normalized_detail_codes = (
            detail[C_ETF_CODE].map(normalize_etf_code)
            if C_ETF_CODE in detail.columns
            else pd.Series(dtype=str, index=detail.index)
        )
        etf_detail = detail[normalized_detail_codes.eq(etf)]
        valuation = aggregate_valuation_rows(valuation_rows_from_detail(etf_detail, C_ETF_INNER_WEIGHT))
        mode = str(etf_row.get(C_MODE) or "")
        error_value = etf_row.get(C_ERROR)
        holding_error = "" if error_value is None or pd.isna(error_value) else str(error_value)
        returns = (
            {C_RETURN_SOURCE: "", C_RETURN_ERROR: holding_error}
            if holding_error
            else (
                {}
                if skip_metrics
                else safe_selected_return_metrics(SelectedETF(etf, 1.0, mode), ak_module, us_listed_module, lookback_days=lookback_days)
            )
        )
        for key, value in {**valuation, **returns}.items():
            etf_summary.at[idx, key] = value
        metric_rows.append(
            {
                C_TYPE: "\u0045\u0054\u0046",
                C_ETF_CODE: etf,
                C_ETF_NAME: etf_row.get(C_ETF_NAME, ""),
                C_ETF_WEIGHT: etf_row.get(C_ETF_WEIGHT),
                C_MODE: etf_row.get(C_MODE),
                C_PERIOD: etf_row.get(C_PERIOD),
                C_SOURCE: etf_row.get(C_SOURCE),
                C_LOOKTHROUGH_BASIS: etf_row.get(C_LOOKTHROUGH_BASIS),
                C_STOCK_COUNT: etf_row.get(C_STOCK_COUNT),
                C_RAW_STOCK_ROWS: etf_row.get(C_RAW_STOCK_ROWS),
                C_EFFECTIVE_STOCK_ROWS: etf_row.get(C_EFFECTIVE_STOCK_ROWS),
                C_MISSING_WEIGHT_ROWS: etf_row.get(C_MISSING_WEIGHT_ROWS),
                C_ERROR: holding_error,
                **valuation,
                **returns,
            }
        )

    portfolio_valuation = aggregate_valuation_rows(valuation_rows_from_detail(detail, C_PORTFOLIO_WEIGHT))
    portfolio_returns = (
        {}
        if skip_metrics
        else (portfolio_return_metrics(selections, ak_module, us_listed_module, lookback_days=lookback_days) if ak_module is not None or us_listed_module is not None else {})
    )
    holding_errors = {
        str(row.get(C_ETF_CODE)): str(row.get(C_ERROR))
        for _, row in etf_summary.iterrows()
        if row.get(C_ERROR) is not None and not pd.isna(row.get(C_ERROR)) and str(row.get(C_ERROR)).strip()
    }
    raw_rows = pd.to_numeric(etf_summary.get(C_RAW_STOCK_ROWS, pd.Series(dtype=float)), errors="coerce").fillna(0)
    effective_rows = pd.to_numeric(etf_summary.get(C_EFFECTIVE_STOCK_ROWS, pd.Series(dtype=float)), errors="coerce").fillna(0)
    missing_rows = pd.to_numeric(etf_summary.get(C_MISSING_WEIGHT_ROWS, pd.Series(dtype=float)), errors="coerce").fillna(0)
    metric_rows.append(
        {
            C_TYPE: "\u7ec4\u5408",
            C_ETF_CODE: "PORTFOLIO",
            C_ETF_NAME: ",".join(selected.code for selected in selections),
            C_ETF_WEIGHT: 100.0,
            C_MODE: "\u7ec4\u5408\u7a7f\u900f",
            C_PERIOD: ",".join(
                sorted(str(x) for x in detail.get(C_PERIOD, pd.Series(dtype=str)).dropna().unique())
            ),
            C_SOURCE: ",".join(
                sorted(str(x) for x in detail.get(C_SOURCE, pd.Series(dtype=str)).dropna().unique())
            ),
            C_LOOKTHROUGH_BASIS: ",".join(sorted(str(x) for x in etf_summary.get(C_LOOKTHROUGH_BASIS, pd.Series(dtype=str)).dropna().unique())),
            C_STOCK_COUNT: int(len(summary)),
            C_RAW_STOCK_ROWS: int(raw_rows.sum()),
            C_EFFECTIVE_STOCK_ROWS: int(effective_rows.sum()),
            C_MISSING_WEIGHT_ROWS: int(missing_rows.sum()),
            C_ERROR: json.dumps(holding_errors, ensure_ascii=False) if holding_errors else "",
            **portfolio_valuation,
            **portfolio_returns,
        }
    )
    metrics_summary = pd.DataFrame(metric_rows)
    preferred_cols = [
        C_TYPE,
        C_ETF_CODE,
        C_ETF_NAME,
        C_ETF_WEIGHT,
        C_MODE,
        C_PERIOD,
        C_SOURCE,
        C_LOOKTHROUGH_BASIS,
        C_STOCK_COUNT,
        C_RAW_STOCK_ROWS,
        C_EFFECTIVE_STOCK_ROWS,
        C_MISSING_WEIGHT_ROWS,
        C_HOLDING_WEIGHT_TOTAL,
        C_DIVIDEND_YIELD,
        "PE",
        "PB",
        C_ANNUAL_RETURN,
        C_SORTINO,
        C_VOLATILITY,
        C_HALF_YEAR_RETURN,
        C_ONE_YEAR_RETURN,
        C_THREE_YEAR_RETURN,
        C_MAX_DRAWDOWN,
        C_SHARPE,
        C_CALMAR,
        C_BETA,
        C_VAR_95,
        C_CVAR_95,
        C_DOWNSIDE_VOL,
        C_RETURN_SOURCE,
        C_RETURN_WINDOW,
        C_RISK_WINDOW,
        C_RETURN_ERROR,
        C_RETURN_BASIS,
        C_RETURN_CURRENCY,
        C_PORTFOLIO_METHOD,
        C_PE_COVERAGE,
        C_DY_COVERAGE,
        C_PB_COVERAGE,
        C_NEGATIVE_PE_WEIGHT,
        C_ERROR,
    ]
    for column in preferred_cols:
        if column not in metrics_summary.columns:
            metrics_summary[column] = None
    metrics_summary = metrics_summary[preferred_cols]
    return summary, detail, etf_summary, metrics_summary


def constraint_config_from_args(args: argparse.Namespace) -> ConstraintConfig:
    return ConstraintConfig(
        max_stock_weight=getattr(args, "max_stock_weight", None),
        max_industry_weight=getattr(args, "max_industry_weight", None),
        min_dividend_yield=getattr(args, "min_dividend_yield", None),
        max_pe=getattr(args, "max_pe", None),
        max_pb=getattr(args, "max_pb", None),
        max_drawdown=getattr(args, "max_drawdown", None),
        target_a_weight=getattr(args, "target_a_weight", None),
        target_hk_weight=getattr(args, "target_hk_weight", None),
        target_us_weight=getattr(args, "target_us_weight", None),
        min_metric_coverage=getattr(args, "min_metric_coverage", 80.0),
    )


def refresh_cache_if_requested(out_dir: Path, args: argparse.Namespace) -> None:
    if not getattr(args, "refresh_cache", False):
        return
    cache_dir = out_dir / "cache"
    if not cache_dir.exists():
        return
    for path in cache_dir.glob("holdings_*.csv"):
        if path.is_file():
            path.unlink()


def _write_outputs_direct(
    out_dir: Path,
    summary: pd.DataFrame,
    detail: pd.DataFrame,
    etf_summary: pd.DataFrame,
    metrics_summary: pd.DataFrame,
    args: argparse.Namespace,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(out_dir / OUT_SUMMARY, index=False, encoding="utf-8-sig")
    detail.to_csv(out_dir / OUT_DETAIL, index=False, encoding="utf-8-sig")
    etf_summary.to_csv(out_dir / OUT_ETF_SUMMARY, index=False, encoding="utf-8-sig")
    metrics_summary.to_csv(out_dir / OUT_METRICS, index=False, encoding="utf-8-sig")
    enhanced_outputs = build_enhanced_outputs(
        summary,
        detail,
        etf_summary,
        metrics_summary,
        args=vars(args),
        out_dir=out_dir,
        constraints=constraint_config_from_args(args),
        use_cache=getattr(args, "use_cache", True),
    )
    if args.full_output:
        for key, filename in ENHANCED_CSV_OUTPUTS.items():
            df = enhanced_outputs.get(key)
            if df is not None:
                df.to_csv(out_dir / filename, index=False, encoding="utf-8-sig")
    report_outputs = {
        "summary": summary,
        "detail": detail,
        "etf_summary": etf_summary,
        "metrics_summary": metrics_summary,
        **enhanced_outputs,
    }
    if args.full_output:
        (out_dir / OUT_MARKDOWN).write_text(build_markdown_report(report_outputs), encoding="utf-8")
        (out_dir / OUT_HTML).write_text(build_html_report(report_outputs), encoding="utf-8")
    run_summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "etfs": args.etf,
        "weights": args.weights,
        "markets": args.markets,
        "rows": {
            "summary": len(summary),
            "detail": len(detail),
            "etf_summary": len(etf_summary),
            "metrics_summary": len(metrics_summary),
            **{key: len(df) for key, df in enhanced_outputs.items()},
        },
        "reports": [OUT_XLSX, OUT_MARKDOWN, OUT_HTML, "run_manifest.json"],
        "max_single_stock_exposure_pct": None if summary.empty else float(summary["\u7ec4\u5408\u7a7f\u900f\u6743\u91cd%"].max()),
        "pcf_periods": sorted(str(x) for x in detail[C_PERIOD].dropna().unique()) if not detail.empty else [],
    }
    if args.full_output:
        (out_dir / OUT_JSON).write_text(json.dumps(run_summary, ensure_ascii=False, indent=2), encoding="utf-8")

    with pd.ExcelWriter(out_dir / OUT_XLSX, engine="openpyxl") as writer:
        summary.to_excel(writer, index=False, sheet_name="\u7a7f\u900f\u6c47\u603b")
        detail.to_excel(writer, index=False, sheet_name="\u7a7f\u900f\u660e\u7ec6")
        etf_summary.to_excel(writer, index=False, sheet_name="\u0045\u0054\u0046\u6c47\u603b")
        metrics_summary.to_excel(writer, index=False, sheet_name="\u6307\u6807\u6c47\u603b")
        for key, sheet_name in ENHANCED_SHEETS.items():
            df = enhanced_outputs.get(key)
            if df is not None:
                df.to_excel(writer, index=False, sheet_name=sheet_name[:31])
        for sheet_name, width_map in {
            "\u7a7f\u900f\u6c47\u603b": {1: 8, 2: 12, 3: 14, 4: 24, 5: 16, 6: 12},
            "\u7a7f\u900f\u660e\u7ec6": {1: 12, 2: 24, 3: 12, 4: 12, 5: 12, 6: 14, 7: 24},
            "\u0045\u0054\u0046\u6c47\u603b": {1: 12, 2: 24, 3: 12, 4: 12, 5: 16, 6: 16},
            "\u6307\u6807\u6c47\u603b": {1: 10, 2: 12, 3: 24, 4: 12, 5: 14, 6: 16, 7: 16},
            **{sheet_name: {} for sheet_name in ENHANCED_SHEETS.values()},
        }.items():
            ws = writer.book[sheet_name[:31]]
            ws.freeze_panes = "A2"
            for col_idx in range(1, ws.max_column + 1):
                ws.column_dimensions[ws.cell(1, col_idx).column_letter].width = width_map.get(col_idx, 14)
            for row in ws.iter_rows(min_row=2):
                for cell in row:
                    if isinstance(cell.value, float):
                        cell.number_format = "0.0000"


def write_outputs(
    out_dir: Path,
    summary: pd.DataFrame,
    detail: pd.DataFrame,
    etf_summary: pd.DataFrame,
    metrics_summary: pd.DataFrame,
    args: argparse.Namespace,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    refresh_cache_if_requested(out_dir, args)
    with tempfile.TemporaryDirectory(prefix=".pcf-stage-", dir=out_dir) as tmpdir:
        stage = Path(tmpdir)
        existing_cache = out_dir / "cache"
        if getattr(args, "use_cache", False) and existing_cache.exists():
            stage_cache = stage / "cache"
            stage_cache.mkdir(parents=True, exist_ok=True)
            for path in existing_cache.glob("holdings_*.csv"):
                if path.is_file():
                    shutil.copy2(path, stage_cache / path.name)
        _write_outputs_direct(stage, summary, detail, etf_summary, metrics_summary, args)
        required = [OUT_SUMMARY, OUT_DETAIL, OUT_ETF_SUMMARY, OUT_METRICS, OUT_XLSX, "run_manifest.json"]
        missing = [name for name in required if not (stage / name).is_file()]
        if missing:
            raise RuntimeError(f"staged output validation failed; missing: {', '.join(missing)}")

        manifest = stage / "run_manifest.json"
        root_files = [path for path in stage.iterdir() if path.is_file() and path != manifest]
        stage_cache = stage / "cache"
        cache_files = [path for path in stage_cache.iterdir() if path.is_file()] if stage_cache.exists() else []
        stale_targets: list[Path] = []
        if not args.full_output:
            stale_names = {
                OUT_JSON,
                OUT_MARKDOWN,
                OUT_HTML,
                *ENHANCED_CSV_OUTPUTS.values(),
            }
            stale_targets = [out_dir / name for name in stale_names]

        publish_staged_files(
            [(path, out_dir / path.name) for path in root_files]
            + [(path, out_dir / "cache" / path.name) for path in cache_files],
            stale_targets=stale_targets,
            commit_file=(manifest, out_dir / manifest.name),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--etf", required=True, help="ETF codes separated by comma/space, e.g. 159569,159758")
    parser.add_argument("--weights", help="Optional weights. Percent or decimal. Defaults to equal weights.")
    parser.add_argument(
        "--markets",
        help="Optional per-ETF modes: auto, hk, a, us, us_listed. One value applies to all; or provide one per ETF. Default: auto.",
    )
    parser.add_argument("--out-dir", default="selected_etf_lookthrough_output", help="Output directory.")
    parser.add_argument("--full-output", action="store_true", help="Write auxiliary CSV, Markdown, HTML, and run_summary files.")
    parser.add_argument("--a-script", default=str(DEFAULT_A_SCRIPT), help="Path to A-share PCF helper script.")
    parser.add_argument("--hk-script", default=str(DEFAULT_HK_SCRIPT), help="Path to HK PCF helper script.")
    parser.add_argument("--us-script", default=str(DEFAULT_US_SCRIPT), help="Path to US-stock PCF helper script.")
    parser.add_argument("--us-listed-script", default=str(DEFAULT_US_LISTED_SCRIPT), help="Path to US-listed ETF helper script.")
    parser.add_argument(
        "--min-auto-weight",
        type=float,
        default=30.0,
        help="Minimum combined A/HK/US stock weight required for auto mode.",
    )
    parser.add_argument("--skip-metrics", action="store_true", help="Only export holdings weights; skip PE/PB/dividend/return lookups.")
    parser.add_argument("--metrics-workers", type=int, default=8, help="Concurrent A-share stock metric lookups.")
    parser.add_argument("--hk-alt-limit", type=int, default=0, help="HK alternate valuation cross-check count; 0 keeps primary source only.")
    parser.add_argument("--hk-sleep", type=float, default=0.05, help="Sleep seconds between HK constituent metric requests.")
    parser.add_argument("--nav-lookback-days", type=int, default=365 * 3 + 120, help="ETF NAV/price lookback window for return metrics.")
    parser.add_argument("--compare", action="store_true", help="Generate cross-source validation outputs. Included by default.")
    parser.add_argument("--portfolio", action="store_true", help="Compatibility flag; portfolio mode is automatic when multiple ETFs or weights are supplied.")
    parser.add_argument("--industry", action="store_true", help="Generate industry exposure outputs. Included by default.")
    parser.add_argument("--theme", action="store_true", help="Generate theme exposure outputs. Included by default.")
    parser.add_argument("--risk", action="store_true", help="Generate risk analysis outputs. Included by default.")
    parser.add_argument("--html-report", action="store_true", help="Write enhanced_report.html. Included by default.")
    cache_group = parser.add_mutually_exclusive_group()
    cache_group.add_argument("--cache", dest="use_cache", action="store_true", default=False, help="Cache the current look-through holdings snapshot.")
    cache_group.add_argument("--no-cache", dest="use_cache", action="store_false", help="Do not write a holdings snapshot under output cache. Default.")
    parser.add_argument("--refresh-cache", action="store_true", help="Remove existing holdings snapshots in the output cache before this run.")
    parser.add_argument("--max-stock-weight", type=float, help="Constraint check: max allowed single-stock look-through weight percent.")
    parser.add_argument("--max-industry-weight", type=float, help="Constraint check: max allowed rule-industry look-through weight percent.")
    parser.add_argument("--min-dividend-yield", type=float, help="Constraint check: minimum portfolio dividend yield percent.")
    parser.add_argument("--max-pe", type=float, help="Constraint check: maximum portfolio PE.")
    parser.add_argument("--max-pb", type=float, help="Constraint check: maximum portfolio PB.")
    parser.add_argument("--max-drawdown", type=float, help="Constraint check: maximum drawdown lower bound, e.g. -20 means no worse than -20%%.")
    parser.add_argument("--target-a-weight", type=float, help="Constraint check: minimum A-share look-through weight percent.")
    parser.add_argument("--target-hk-weight", type=float, help="Constraint check: minimum Hong Kong look-through weight percent.")
    parser.add_argument("--target-us-weight", type=float, help="Constraint check: minimum US-stock look-through weight percent.")
    parser.add_argument("--min-metric-coverage", type=float, default=80.0, help="Minimum PE/PB/dividend coverage required before a valuation constraint can pass.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.full_output = args.full_output or any(
        (args.compare, args.industry, args.theme, args.risk, args.html_report)
    )
    selections = selected_etfs(args.etf, args.weights, args.markets)
    a_module = import_module(Path(args.a_script), "selected_etf_a_pcf")
    hk_module = import_module(Path(args.hk_script), "selected_etf_hk_pcf")
    us_module = import_module(Path(args.us_script), "selected_etf_us_pcf")
    us_listed_module = import_module(Path(args.us_listed_script), "selected_etf_us_listed_pcf")
    name_map = maybe_load_name_map()
    summary, detail, etf_summary = build_tables(
        selections,
        a_module=a_module,
        hk_module=hk_module,
        us_module=us_module,
        us_listed_module=us_listed_module,
        name_map=name_map,
        min_auto_weight=args.min_auto_weight,
    )
    summary, detail, etf_summary, metrics_summary = add_metrics_to_tables(
        selections,
        summary=summary,
        detail=detail,
        etf_summary=etf_summary,
        a_module=a_module,
        hk_module=hk_module,
        us_module=us_module,
        us_listed_module=us_listed_module,
        workers=max(1, args.metrics_workers),
        hk_alt_limit=max(0, args.hk_alt_limit),
        hk_sleep=max(0.0, args.hk_sleep),
        lookback_days=max(60, args.nav_lookback_days),
        skip_metrics=args.skip_metrics,
    )
    out_dir = Path(args.out_dir)
    write_outputs(out_dir, summary, detail, etf_summary, metrics_summary, args)
    print(f"Saved: {out_dir / OUT_SUMMARY}")
    print(f"Saved: {out_dir / OUT_DETAIL}")
    print(f"Saved: {out_dir / OUT_ETF_SUMMARY}")
    print(f"Saved: {out_dir / OUT_METRICS}")
    print(f"Saved: {out_dir / OUT_XLSX}")
    if args.full_output:
        print(f"Saved: {out_dir / OUT_MARKDOWN}")
        print(f"Saved: {out_dir / OUT_HTML}")
    print(f"Saved: {out_dir / 'run_manifest.json'}")
    if not summary.empty:
        print("\nTop look-through holdings:")
        print(summary.head(15).to_string(index=False))
    failures = etf_summary.get(C_ERROR, pd.Series(dtype=str)).fillna("").astype(str).str.strip().ne("")
    return 1 if len(etf_summary) > 0 and bool(failures.all()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
