#!/usr/bin/env python
"""US-stock look-through helper for A-share listed cross-border ETFs."""

from __future__ import annotations

import concurrent.futures
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import akshare as ak
import pandas as pd

from run_a_share_dividend_etf_pcf_metrics import (
    SSE_ETF_BASIC_SQL,
    SSE_ETF_COMPONENT_SQL,
    clean_float,
    extract_szse_pcf_downloads,
    fetch_szse_pcf_xml as fetch_szse_pcf_xml_a,
    query_sse_pcf,
    request_json,
    request_text,
    xml_child_text,
    SZSE_REPORTDOCS_URL,
    SZSE_REPORT_URL,
)


US_SOURCE_CODES = {"9999", "US", "USA", "NASDAQ", "NYSE", "AMEX"}
US_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")


@dataclass(frozen=True)
class USMetric:
    ticker: str
    price: float | None
    pe: float | None
    pb: float | None
    dividend_yield_pct: float | None
    source: str
    error: str


def normalize_us_ticker(value: Any) -> str:
    text = str(value or "").strip().upper()
    return re.sub(r"[^A-Z0-9.-]", "", text)


def is_us_ticker(value: Any) -> bool:
    ticker = normalize_us_ticker(value)
    if not ticker or ticker.isdigit():
        return False
    return bool(US_TICKER_RE.fullmatch(ticker))


def _weight_from_amount(amount: float | None, nav_per_cu: float) -> float | None:
    if amount is None or amount <= 0:
        return None
    return amount / nav_per_cu * 100


def get_sse_pcf_holdings(etf: str) -> tuple[pd.DataFrame, str]:
    basic_rows = (query_sse_pcf(etf, SSE_ETF_BASIC_SQL).get("result") or [])
    component_rows = (query_sse_pcf(etf, SSE_ETF_COMPONENT_SQL).get("result") or [])
    if not basic_rows or not component_rows:
        raise RuntimeError("SSE PCF returned no rows")
    basic = basic_rows[0]
    nav_per_cu = clean_float(basic.get("NAVPERCU"))
    if nav_per_cu is None or nav_per_cu <= 0:
        raise RuntimeError("SSE PCF missing NAVPERCU")
    trading_day = str(basic.get("TRADING_DAY") or "")
    period = f"PCF:{trading_day}" if trading_day else "PCF:SSE"
    rows: list[dict[str, Any]] = []
    for row in component_rows:
        raw_code = row.get("INSTRUMENT_ID")
        ticker = normalize_us_ticker(raw_code)
        source = str(row.get("UNDERLYION_SECURITY_ID") or "").strip()
        if source not in US_SOURCE_CODES and not is_us_ticker(ticker):
            continue
        amount = clean_float(row.get("SUBSTITUTION_CASH_AMOUNT"))
        weight = _weight_from_amount(amount, nav_per_cu)
        if not ticker or weight is None:
            continue
        quantity = clean_float(row.get("QUANTITY"))
        rows.append(
            {
                "股票代码": ticker,
                "股票名称": str(row.get("INSTRUMENT_NAME") or ticker),
                "市场": "US",
                "权重%": weight,
                "市值": amount,
                "数量": quantity,
                "隐含价格": None if amount is None or not quantity else amount / quantity,
                "替代标志": str(row.get("SUBSTITUTION_FLAG") or ""),
                "权重来源": "SUBSTITUTION_CASH_AMOUNT/NAVPERCU",
                "来源明细": f"SSE ETF PCF {trading_day}".strip(),
                "原始代码": str(raw_code or ""),
                "原始市场代码": source,
                "NAVperCU": nav_per_cu,
            }
        )
    if not rows:
        raise RuntimeError("SSE PCF had no US component rows")
    return pd.DataFrame(rows), period


def score_szse_pcf_xml(text: str) -> tuple[int, int]:
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError:
        return -1, -1
    ns = {"f": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
    component_path = ".//f:Component" if ns else ".//Component"
    components = root.findall(component_path, ns)
    us_count = 0
    for node in components:
        find_name = lambda name: xml_child_text(node, name, ns) if ns else (node.findtext(name) or "").strip()
        source = find_name("UnderlyingSecurityIDSource").strip()
        code = normalize_us_ticker(find_name("UnderlyingSecurityID"))
        if source in US_SOURCE_CODES or is_us_ticker(code):
            us_count += 1
    return us_count, len(components)


def fetch_szse_pcf_xml(etf: str) -> tuple[str, str]:
    payload = request_json(
        SZSE_REPORT_URL,
        params={"CATALOGID": "sgshqd", "loading": "first", "txtJCorDH": etf},
        referer="https://www.szse.cn/disclosure/fund/currency/index.html",
    )
    report_rows: list[dict[str, Any]] = []
    for block in payload if isinstance(payload, list) else []:
        report_rows.extend(block.get("data") or [])
    if not report_rows:
        raise RuntimeError("SZSE PCF report list returned no rows")
    import html

    html_cell = html.unescape(str(report_rows[0].get("jjdm") or ""))
    download_names = extract_szse_pcf_downloads(html_cell)
    if not download_names:
        raise RuntimeError("SZSE PCF row did not include a download filename")
    candidates: list[tuple[int, int, str, str]] = []
    last_error = ""
    for _, name in download_names:
        for suffix in (".xml", ".txt"):
            url = f"{SZSE_REPORTDOCS_URL}/{name}{suffix}"
            try:
                text = request_text(url, referer="https://www.szse.cn/disclosure/fund/currency/index.html")
                if "<html" not in text[:300].lower():
                    us_count, component_count = score_szse_pcf_xml(text)
                    candidates.append((us_count, component_count, text, url))
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best = candidates[0]
        if best[0] > 0:
            return best[2], best[3]
    try:
        return fetch_szse_pcf_xml_a(etf)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"SZSE PCF file download failed: {last_error}; fallback: {exc}") from exc


def get_usd_cny_rate() -> float:
    try:
        df = ak.currency_boc_sina()
        value = pd.to_numeric(df.iloc[-1, 4], errors="coerce")
        if pd.notna(value) and value > 0:
            return float(value) / 100
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"USD/CNY lookup failed: {exc}") from exc
    raise RuntimeError("USD/CNY lookup returned no usable value")


def get_us_latest_price(ticker: str) -> float | None:
    df = ak.stock_us_daily(symbol=ticker)
    if df is None or df.empty or "close" not in df.columns:
        return None
    price = pd.to_numeric(df["close"], errors="coerce").dropna()
    return None if price.empty else float(price.iloc[-1])


def get_szse_pcf_holdings(etf: str) -> tuple[pd.DataFrame, str]:
    text, source_url = fetch_szse_pcf_xml(etf)
    root = ET.fromstring(text.encode("utf-8"))
    ns = {"f": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}

    def find_name(node: ET.Element, name: str) -> str:
        return xml_child_text(node, name, ns) if ns else (node.findtext(name) or "").strip()

    nav_per_cu = clean_float(find_name(root, "NAVperCU"))
    if nav_per_cu is None or nav_per_cu <= 0:
        raise RuntimeError("SZSE PCF missing NAVperCU")
    trading_day = find_name(root, "TradingDay")
    period = f"PCF:{trading_day}" if trading_day else "PCF:SZSE"
    component_path = ".//f:Component" if ns else ".//Component"
    rows: list[dict[str, Any]] = []
    fx_rate: float | None = None
    fx_error = ""
    for node in root.findall(component_path, ns):
        source = find_name(node, "UnderlyingSecurityIDSource").strip()
        ticker = normalize_us_ticker(find_name(node, "UnderlyingSecurityID"))
        if source not in US_SOURCE_CODES and not is_us_ticker(ticker):
            continue
        quantity = clean_float(find_name(node, "ComponentShare"))
        create_cash = clean_float(find_name(node, "CreationCashSubstitute"))
        premium_ratio = clean_float(find_name(node, "PremiumRatio")) or 0.0
        if abs(premium_ratio) > 1:
            premium_ratio /= 100
        amount = None
        weight_source = "CreationCashSubstitute/(1+PremiumRatio)/NAVperCU"
        error = ""
        if create_cash is not None and create_cash > 0:
            amount = create_cash / (1 + premium_ratio) if premium_ratio > -0.999 else create_cash
        elif quantity is not None and quantity > 0:
            try:
                if fx_rate is None:
                    fx_rate = get_usd_cny_rate()
                price = get_us_latest_price(ticker)
                if price is not None and price > 0:
                    amount = quantity * price * fx_rate
                    weight_source = "ComponentShare*US latest price*USD/CNY/NAVperCU"
            except Exception as exc:  # noqa: BLE001
                fx_error = str(exc)
                error = fx_error
        weight = _weight_from_amount(amount, nav_per_cu)
        if not ticker or weight is None:
            continue
        rows.append(
            {
                "股票代码": ticker,
                "股票名称": find_name(node, "UnderlyingSymbol") or ticker,
                "市场": "US",
                "权重%": weight,
                "市值": amount,
                "数量": quantity,
                "隐含价格": None if amount is None or not quantity else amount / quantity,
                "替代标志": find_name(node, "SubstituteFlag"),
                "权重来源": weight_source,
                "来源明细": source_url,
                "原始代码": find_name(node, "UnderlyingSecurityID"),
                "原始市场代码": source,
                "估值错误": error,
                "NAVperCU": nav_per_cu,
            }
        )
    if not rows:
        raise RuntimeError(f"SZSE PCF had no US component rows. {fx_error}".strip())
    return pd.DataFrame(rows), period


def get_pcf_holdings(etf: str) -> tuple[pd.DataFrame, str, str]:
    if etf.startswith("5"):
        df, period = get_sse_pcf_holdings(etf)
        return df, period, "sse_pcf_us"
    if etf.startswith(("15", "16")):
        df, period = get_szse_pcf_holdings(etf)
        return df, period, "szse_pcf_us"
    raise RuntimeError(f"No US PCF adapter for ETF exchange: {etf}")


def _latest_numeric(df: pd.DataFrame) -> float | None:
    if df is None or df.empty:
        return None
    numeric = df.apply(pd.to_numeric, errors="coerce")
    values = numeric.stack().dropna()
    if values.empty:
        return None
    value = float(values.iloc[-1])
    return None if math.isnan(value) or math.isinf(value) else value


def _valuation(ticker: str, indicator: str) -> tuple[float | None, str]:
    try:
        return _latest_numeric(ak.stock_us_valuation_baidu(symbol=ticker, indicator=indicator)), ""
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)[:160]


def metric_for_ticker(ticker: str) -> USMetric:
    errors: list[str] = []
    price = None
    try:
        price = get_us_latest_price(ticker)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"price:{str(exc)[:120]}")
    pe, err = _valuation(ticker, "市盈率(TTM)")
    if err:
        errors.append(f"pe:{err}")
    pb, err = _valuation(ticker, "市净率")
    if err:
        errors.append(f"pb:{err}")
    dy, err = _valuation(ticker, "股息率")
    if err:
        errors.append(f"dy:{err}")
    return USMetric(
        ticker=ticker,
        price=price,
        pe=pe,
        pb=pb,
        dividend_yield_pct=dy,
        source="akshare_us_daily+baidu_valuation",
        error="; ".join(errors),
    )


def build_metrics(tickers: list[str], workers: int = 8) -> dict[str, USMetric]:
    unique = sorted({normalize_us_ticker(ticker) for ticker in tickers if normalize_us_ticker(ticker)})
    out: dict[str, USMetric] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(metric_for_ticker, ticker): ticker for ticker in unique}
        for future in concurrent.futures.as_completed(futures):
            ticker = futures[future]
            try:
                out[ticker] = future.result()
            except Exception as exc:  # noqa: BLE001
                out[ticker] = USMetric(ticker, None, None, None, None, "akshare_us", str(exc)[:160])
    return out
