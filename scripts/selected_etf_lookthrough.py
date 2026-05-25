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
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd


WORKSPACE = Path(__file__).resolve().parent
DEFAULT_A_SCRIPT = WORKSPACE / "run_a_share_dividend_etf_pcf_metrics.py"
DEFAULT_HK_SCRIPT = WORKSPACE / "pcf_lookthrough.py"

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
C_PE_COVERAGE = "\u0050\u0045\u8986\u76d6\u6743\u91cd%"
C_DY_COVERAGE = "\u80a1\u606f\u7387\u8986\u76d6\u6743\u91cd%"
C_PB_COVERAGE = "\u0050\u0042\u8986\u76d6\u6743\u91cd%"
C_NEGATIVE_PE_WEIGHT = "\u8d1f\u0050\u0045\u6743\u91cd%"


@dataclass(frozen=True)
class SelectedETF:
    code: str
    weight: float
    mode: str


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
    code = re.sub(r"\D", "", str(value))
    if not code:
        raise ValueError(f"Invalid ETF code: {value!r}")
    return code.zfill(6)


def parse_codes(raw: str) -> list[str]:
    codes = [normalize_etf_code(item) for item in re.split(r"[,;\s]+", raw.strip()) if item.strip()]
    if not codes:
        raise ValueError("No ETF codes were supplied.")
    return codes


def parse_weights(raw: str | None, count: int) -> list[float]:
    if raw is None or raw.strip() == "":
        return [1.0 / count] * count
    values = [float(item) for item in re.split(r"[,;\s]+", raw.strip()) if item.strip()]
    if len(values) != count:
        raise ValueError(f"--weights count ({len(values)}) must match ETF count ({count}).")
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
    allowed = {"auto", "a", "hk"}
    invalid = sorted(set(values) - allowed)
    if invalid:
        raise ValueError(f"Invalid --markets value(s): {', '.join(invalid)}")
    return values


def selected_etfs(codes_raw: str, weights_raw: str | None, markets_raw: str | None) -> list[SelectedETF]:
    codes = parse_codes(codes_raw)
    weights = parse_weights(weights_raw, len(codes))
    modes = parse_modes(markets_raw, len(codes))
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
    if C_PRICE not in holdings.columns:
        holdings[C_PRICE] = None
    result = holdings.loc[stock_mask, [C_STOCK_CODE, C_STOCK_NAME, C_MARKET, C_A_WEIGHT, C_PRICE, C_WEIGHT_SOURCE]].copy()
    result[C_STOCK_CODE] = result[C_STOCK_CODE].astype(str).map(lambda value: re.sub(r"\D", "", value).zfill(6))
    result[C_MARKET] = "A"
    result.rename(columns={C_A_WEIGHT: C_ETF_INNER_WEIGHT, C_PRICE: C_STOCK_PRICE}, inplace=True)
    result[C_ETF_INNER_WEIGHT] = pd.to_numeric(result[C_ETF_INNER_WEIGHT], errors="coerce")
    result[C_STOCK_PRICE] = pd.to_numeric(result[C_STOCK_PRICE], errors="coerce")
    result = result.dropna(subset=[C_ETF_INNER_WEIGHT])
    meta = {
        C_PERIOD: period,
        C_SOURCE: source,
        C_MODE: "a",
        C_STOCK_COUNT: int(len(result)),
        "\u0045\u0054\u0046\u5185\u80a1\u7968\u6743\u91cd\u5408\u8ba1%": float(result[C_ETF_INNER_WEIGHT].sum()),
    }
    return result, meta


def get_hk_stock_holdings(hk_module: Any, etf: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    holdings, period = retry(lambda: hk_module.get_pcf_holdings(etf))
    stock_mask = holdings[C_STOCK_CODE].astype(str).str.strip().str.fullmatch(r"\d{1,5}")
    result = holdings.loc[stock_mask, [C_STOCK_CODE, C_STOCK_NAME, C_HK_WEIGHT]].copy()
    result[C_STOCK_CODE] = result[C_STOCK_CODE].astype(str).map(lambda value: re.sub(r"\D", "", value).zfill(5))
    result[C_MARKET] = "HK"
    result[C_WEIGHT_SOURCE] = "PCF look-through"
    result[C_STOCK_PRICE] = None
    result.rename(columns={C_HK_WEIGHT: C_ETF_INNER_WEIGHT}, inplace=True)
    result[C_ETF_INNER_WEIGHT] = pd.to_numeric(result[C_ETF_INNER_WEIGHT], errors="coerce")
    result = result.dropna(subset=[C_ETF_INNER_WEIGHT])
    meta = {
        C_PERIOD: period,
        C_SOURCE: "pcf_lookthrough",
        C_MODE: "hk",
        C_STOCK_COUNT: int(len(result)),
        "\u0045\u0054\u0046\u5185\u80a1\u7968\u6743\u91cd\u5408\u8ba1%": float(result[C_ETF_INNER_WEIGHT].sum()),
    }
    return result, meta


def resolve_holdings(
    selected: SelectedETF,
    a_module: Any,
    hk_module: Any,
    min_auto_weight: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if selected.mode == "hk":
        return get_hk_stock_holdings(hk_module, selected.code)
    if selected.mode == "a":
        return get_a_stock_holdings(a_module, selected.code)

    hk_error = ""
    try:
        hk_df, hk_meta = get_hk_stock_holdings(hk_module, selected.code)
        if not hk_df.empty and hk_meta["\u0045\u0054\u0046\u5185\u80a1\u7968\u6743\u91cd\u5408\u8ba1%"] >= min_auto_weight:
            return hk_df, hk_meta
    except Exception as exc:  # noqa: BLE001
        hk_error = str(exc)

    try:
        a_df, a_meta = get_a_stock_holdings(a_module, selected.code)
        a_meta["auto_hk_error"] = hk_error
        return a_df, a_meta
    except Exception as a_exc:
        if hk_error:
            raise RuntimeError(f"auto mode failed. HK parser: {hk_error}; A parser: {a_exc}") from a_exc
        raise


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
    total_weight = 0.0
    total = 0.0
    for row in rows:
        value = as_float(row.get(key))
        weight = as_float(row.get("weight_pct")) or 0.0
        if value is None:
            continue
        if positive_only and value <= 0:
            continue
        total += weight * value
        total_weight += weight
    if total_weight <= 0:
        return None, 0.0
    return total / total_weight, total_weight


def earnings_yield_pe_metric(rows: list[dict[str, Any]]) -> tuple[float | None, float, float]:
    total_weight = 0.0
    earnings_yield = 0.0
    negative_weight = 0.0
    for row in rows:
        pe = as_float(row.get("pe"))
        weight = as_float(row.get("weight_pct")) or 0.0
        if pe is None:
            continue
        if pe <= 0:
            negative_weight += weight
            continue
        earnings_yield += weight / pe
        total_weight += weight
    if total_weight <= 0 or earnings_yield <= 0:
        return None, total_weight, negative_weight
    return total_weight / earnings_yield, total_weight, negative_weight


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
    except Exception as exc:  # noqa: BLE001
        errors.append(f"eastmoney_nav: {exc}")

    try:
        price = retry(
            lambda: ak_module.fund_etf_hist_em(symbol=etf, period="daily", start_date=start, end_date=end, adjust=""),
            tries=2,
        )
        series = normalize_price_series(price, best_column(price, ["\u65e5\u671f"], 0), best_column(price, ["\u6536\u76d8"], 2))
        if len(series) >= 30:
            return series, "eastmoney_price"
    except Exception as exc:  # noqa: BLE001
        errors.append(f"eastmoney_price: {exc}")

    try:
        sina = retry(lambda: ak_module.fund_etf_hist_sina(symbol=etf_exchange_symbol(etf)), tries=2)
        series = normalize_price_series(sina, best_column(sina, ["date"], 0), best_column(sina, ["close"], 4))
        if len(series) >= 30:
            return series, "sina_price"
    except Exception as exc:  # noqa: BLE001
        errors.append(f"sina_price: {exc}")

    raise RuntimeError("; ".join(errors) or f"No return series for {etf}")


def annualized_from_values(first_value: float, last_value: float, first_date: date, last_date: date) -> float | None:
    days = (last_date - first_date).days
    if days <= 0 or first_value <= 0 or last_value <= 0:
        return None
    return ((last_value / first_value) ** (365.25 / days) - 1.0) * 100


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
    series = series.dropna().sort_index()
    if len(series) < 2:
        return {C_RETURN_SOURCE: source, C_RETURN_ERROR: "not enough observations"}

    one_year = value_window(series, days_back=365, min_days=330)
    returns = series.pct_change().dropna()
    last_ts = series.index[-1]
    one_year_returns = returns[returns.index >= last_ts - pd.Timedelta(days=365)]
    sample = one_year_returns if len(one_year_returns) >= 60 else returns
    volatility = None
    sortino = None
    risk_window = ""
    if len(sample) >= 2:
        volatility = float(sample.std(ddof=1)) * math.sqrt(252) * 100
        downside = sample[sample < 0]
        downside_dev = float((downside.pow(2).mean()) ** 0.5) if not downside.empty else None
        mean_daily = float(sample.mean())
        annual_return = (1 + mean_daily) ** 252 - 1 if mean_daily > -1 else None
        if annual_return is not None and downside_dev and downside_dev > 0:
            sortino = annual_return / (downside_dev * math.sqrt(252))
        risk_window = f"{sample.index[0].date()} to {sample.index[-1].date()}"

    return {
        C_ANNUAL_RETURN: None
        if one_year.get("error") or one_year.get("short_window")
        else one_year.get("annualized_return_pct"),
        C_SORTINO: sortino,
        C_VOLATILITY: volatility,
        C_HALF_YEAR_RETURN: trailing_return(series, days_back=183, min_days=150),
        C_ONE_YEAR_RETURN: trailing_return(series, days_back=365, min_days=330),
        C_THREE_YEAR_RETURN: trailing_return(series, days_back=365 * 3, min_days=900),
        C_RETURN_SOURCE: source,
        C_RETURN_WINDOW: ""
        if one_year.get("error")
        else f"{one_year.get('first_date')} to {one_year.get('last_date')}",
        C_RISK_WINDOW: risk_window,
    }


def portfolio_return_metrics(selections: list[SelectedETF], ak_module: Any, lookback_days: int) -> dict[str, Any]:
    weight_by_code: dict[str, float] = {}
    for selected in selections:
        weight_by_code[selected.code] = weight_by_code.get(selected.code, 0.0) + selected.weight

    series_map: dict[str, pd.Series] = {}
    source_map: dict[str, str] = {}
    errors: dict[str, str] = {}
    for etf in weight_by_code:
        try:
            series, source = fetch_etf_price_series(ak_module, etf, lookback_days=lookback_days)
            series_map[etf] = series.rename(etf)
            source_map[etf] = source
        except Exception as exc:  # noqa: BLE001
            errors[etf] = str(exc)
    if errors:
        return {C_RETURN_SOURCE: "weighted_etf_nav_or_price", C_RETURN_ERROR: json.dumps(errors, ensure_ascii=False)}
    if not series_map:
        return {C_RETURN_SOURCE: "weighted_etf_nav_or_price", C_RETURN_ERROR: "no ETF return series"}

    price_df = pd.concat(series_map.values(), axis=1, sort=True).sort_index().ffill().dropna()
    if price_df.empty or len(price_df) < 2:
        return {C_RETURN_SOURCE: "weighted_etf_nav_or_price", C_RETURN_ERROR: "not enough aligned observations"}
    normalized = price_df.div(price_df.iloc[0])
    portfolio = sum(normalized[code] * weight_by_code[code] for code in weight_by_code)
    metrics = summarize_price_series(portfolio, "weighted_etf_nav_or_price")
    detail = ",".join(f"{code}:{source_map[code]}" for code in weight_by_code)
    metrics[C_RETURN_SOURCE] = f"weighted_etf_nav_or_price({detail})"
    return metrics


def enrich_detail_stock_metrics(
    detail: pd.DataFrame,
    a_module: Any,
    hk_module: Any,
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
        grouped = hk_detail.groupby([C_STOCK_CODE, C_STOCK_NAME], dropna=False, as_index=False)[C_PORTFOLIO_WEIGHT].sum()
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

    for idx, row in detail.iterrows():
        market = str(row.get(C_UNDERLYING_MARKET) or "")
        code = str(row.get(C_STOCK_CODE) or "")
        if market == "HK":
            code = code.zfill(5)
        elif market == "A":
            code = code.zfill(6)
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
    name_map: dict[str, str],
    min_auto_weight: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    detail_rows: list[dict[str, Any]] = []
    etf_rows: list[dict[str, Any]] = []
    for selected in selections:
        print(f"Reading latest PCF for ETF {selected.code} mode={selected.mode}...")
        holdings, meta = resolve_holdings(selected, a_module, hk_module, min_auto_weight)
        etf_name = name_map.get(selected.code, "")
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
                C_STOCK_COUNT: meta[C_STOCK_COUNT],
                "\u0045\u0054\u0046\u5185\u80a1\u7968\u6743\u91cd\u5408\u8ba1%": meta[
                    "\u0045\u0054\u0046\u5185\u80a1\u7968\u6743\u91cd\u5408\u8ba1%"
                ],
            }
        )

    detail = pd.DataFrame(detail_rows)
    etf_summary = pd.DataFrame(etf_rows)
    summary = (
        detail.groupby([C_UNDERLYING_MARKET, C_STOCK_CODE, C_STOCK_NAME], as_index=False)
        .agg(
            **{
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


def rebuild_summary_from_detail(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame()
    summary = (
        detail.groupby([C_UNDERLYING_MARKET, C_STOCK_CODE, C_STOCK_NAME], as_index=False)
        .agg(
            **{
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
    workers: int,
    hk_alt_limit: int,
    hk_sleep: float,
    lookback_days: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if detail.empty:
        return summary, detail, etf_summary, pd.DataFrame()

    print("Looking up stock-level PE/PB/dividend yield...")
    detail = enrich_detail_stock_metrics(
        detail,
        a_module=a_module,
        hk_module=hk_module,
        workers=workers,
        hk_alt_limit=hk_alt_limit,
        hk_sleep=hk_sleep,
    )
    summary = rebuild_summary_from_detail(detail)

    ak_module = getattr(a_module, "ak", None) or getattr(hk_module, "ak", None)
    metric_rows: list[dict[str, Any]] = []
    etf_summary = etf_summary.copy()
    for idx, etf_row in etf_summary.iterrows():
        etf = str(etf_row[C_ETF_CODE]).zfill(6)
        etf_detail = detail[detail[C_ETF_CODE].astype(str).str.zfill(6).eq(etf)]
        valuation = aggregate_valuation_rows(valuation_rows_from_detail(etf_detail, C_ETF_INNER_WEIGHT))
        returns = safe_return_metrics(ak_module, etf, lookback_days=lookback_days) if ak_module is not None else {}
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
                C_STOCK_COUNT: etf_row.get(C_STOCK_COUNT),
                **valuation,
                **returns,
            }
        )

    portfolio_valuation = aggregate_valuation_rows(valuation_rows_from_detail(detail, C_PORTFOLIO_WEIGHT))
    portfolio_returns = portfolio_return_metrics(selections, ak_module, lookback_days=lookback_days) if ak_module is not None else {}
    metric_rows.append(
        {
            C_TYPE: "\u7ec4\u5408",
            C_ETF_CODE: "PORTFOLIO",
            C_ETF_NAME: ",".join(selected.code for selected in selections),
            C_ETF_WEIGHT: 100.0,
            C_MODE: "\u7ec4\u5408\u7a7f\u900f",
            C_PERIOD: ",".join(sorted(str(x) for x in detail[C_PERIOD].dropna().unique())),
            C_SOURCE: ",".join(sorted(str(x) for x in detail[C_SOURCE].dropna().unique())),
            C_STOCK_COUNT: int(len(summary)),
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
        C_STOCK_COUNT,
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
        C_RETURN_SOURCE,
        C_RETURN_WINDOW,
        C_RISK_WINDOW,
        C_RETURN_ERROR,
        C_PE_COVERAGE,
        C_DY_COVERAGE,
        C_PB_COVERAGE,
        C_NEGATIVE_PE_WEIGHT,
    ]
    metrics_summary = metrics_summary[[col for col in preferred_cols if col in metrics_summary.columns]]
    return summary, detail, etf_summary, metrics_summary


def write_outputs(
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
        },
        "max_single_stock_exposure_pct": None if summary.empty else float(summary["\u7ec4\u5408\u7a7f\u900f\u6743\u91cd%"].max()),
        "pcf_periods": sorted(str(x) for x in detail[C_PERIOD].dropna().unique()) if not detail.empty else [],
    }
    (out_dir / OUT_JSON).write_text(json.dumps(run_summary, ensure_ascii=False, indent=2), encoding="utf-8")

    with pd.ExcelWriter(out_dir / OUT_XLSX, engine="openpyxl") as writer:
        summary.to_excel(writer, index=False, sheet_name="\u7a7f\u900f\u6c47\u603b")
        detail.to_excel(writer, index=False, sheet_name="\u7a7f\u900f\u660e\u7ec6")
        etf_summary.to_excel(writer, index=False, sheet_name="\u0045\u0054\u0046\u6c47\u603b")
        metrics_summary.to_excel(writer, index=False, sheet_name="\u6307\u6807\u6c47\u603b")
        for sheet_name, width_map in {
            "\u7a7f\u900f\u6c47\u603b": {1: 8, 2: 12, 3: 14, 4: 24, 5: 16, 6: 12},
            "\u7a7f\u900f\u660e\u7ec6": {1: 12, 2: 24, 3: 12, 4: 12, 5: 12, 6: 14, 7: 24},
            "\u0045\u0054\u0046\u6c47\u603b": {1: 12, 2: 24, 3: 12, 4: 12, 5: 16, 6: 16},
            "\u6307\u6807\u6c47\u603b": {1: 10, 2: 12, 3: 24, 4: 12, 5: 14, 6: 16, 7: 16},
        }.items():
            ws = writer.book[sheet_name]
            ws.freeze_panes = "A2"
            for col_idx in range(1, ws.max_column + 1):
                ws.column_dimensions[ws.cell(1, col_idx).column_letter].width = width_map.get(col_idx, 14)
            for row in ws.iter_rows(min_row=2):
                for cell in row:
                    if isinstance(cell.value, float):
                        cell.number_format = "0.0000"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--etf", required=True, help="ETF codes separated by comma/space, e.g. 159569,159758")
    parser.add_argument("--weights", help="Optional weights. Percent or decimal. Defaults to equal weights.")
    parser.add_argument(
        "--markets",
        help="Optional per-ETF modes: auto, hk, a. One value applies to all; or provide one per ETF. Default: auto.",
    )
    parser.add_argument("--out-dir", default="selected_etf_lookthrough_output", help="Output directory.")
    parser.add_argument("--a-script", default=str(DEFAULT_A_SCRIPT), help="Path to A-share PCF helper script.")
    parser.add_argument("--hk-script", default=str(DEFAULT_HK_SCRIPT), help="Path to HK PCF helper script.")
    parser.add_argument(
        "--min-auto-weight",
        type=float,
        default=30.0,
        help="In auto mode, accept HK parser when HK stock weight is at least this percent.",
    )
    parser.add_argument("--skip-metrics", action="store_true", help="Only export holdings weights; skip PE/PB/dividend/return lookups.")
    parser.add_argument("--metrics-workers", type=int, default=8, help="Concurrent A-share stock metric lookups.")
    parser.add_argument("--hk-alt-limit", type=int, default=0, help="HK alternate valuation cross-check count; 0 keeps primary source only.")
    parser.add_argument("--hk-sleep", type=float, default=0.05, help="Sleep seconds between HK constituent metric requests.")
    parser.add_argument("--nav-lookback-days", type=int, default=365 * 3 + 120, help="ETF NAV/price lookback window for return metrics.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    selections = selected_etfs(args.etf, args.weights, args.markets)
    a_module = import_module(Path(args.a_script), "selected_etf_a_pcf")
    hk_module = import_module(Path(args.hk_script), "selected_etf_hk_pcf")
    name_map = maybe_load_name_map()
    summary, detail, etf_summary = build_tables(
        selections,
        a_module=a_module,
        hk_module=hk_module,
        name_map=name_map,
        min_auto_weight=args.min_auto_weight,
    )
    metrics_summary = pd.DataFrame()
    if not args.skip_metrics:
        summary, detail, etf_summary, metrics_summary = add_metrics_to_tables(
            selections,
            summary=summary,
            detail=detail,
            etf_summary=etf_summary,
            a_module=a_module,
            hk_module=hk_module,
            workers=args.metrics_workers,
            hk_alt_limit=args.hk_alt_limit,
            hk_sleep=args.hk_sleep,
            lookback_days=args.nav_lookback_days,
        )
    out_dir = Path(args.out_dir)
    write_outputs(out_dir, summary, detail, etf_summary, metrics_summary, args)
    print(f"Saved: {out_dir / OUT_SUMMARY}")
    print(f"Saved: {out_dir / OUT_DETAIL}")
    print(f"Saved: {out_dir / OUT_ETF_SUMMARY}")
    print(f"Saved: {out_dir / OUT_METRICS}")
    print(f"Saved: {out_dir / OUT_XLSX}")
    if not summary.empty:
        print("\nTop look-through holdings:")
        print(summary.head(15).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
