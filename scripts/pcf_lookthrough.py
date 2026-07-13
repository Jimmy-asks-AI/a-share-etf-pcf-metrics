#!/usr/bin/env python
"""Look-through valuation report for A-share listed Hong Kong equity ETFs."""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
import xml.etree.ElementTree as ET

import pandas as pd
import requests

from pcf_common import (
    annualized_return as common_annualized_return,
    earnings_yield_pe as common_earnings_yield_pe,
    normalize_price_frame as common_normalize_price_frame,
    price_series_metrics,
    weighted_average as common_weighted_average,
)

try:
    import akshare as ak
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "Missing dependency: akshare. Install with: python -m pip install akshare pandas requests beautifulsoup4 lxml openpyxl"
    ) from exc


DEFAULT_SAMPLE_ETFS = ["513690", "513950", "513530", "513820", "513830"]
DEFAULT_DIVIDEND_CANDIDATES = [
    "513690",
    "513950",
    "513530",
    "513820",
    "513830",
    "159726",
    "159277",
    "159118",
    "159220",
    "520550",
    "520610",
    "520810",
    "520890",
    "513910",
]
TRADING_DAYS_PER_YEAR = 252
PRIMARY_VALUATION_CACHE: dict[str, dict[str, float | None]] = {}
ALT_VALUATION_CACHE: dict[str, dict[str, Any]] = {}
HK_SPOT_PRICE_CACHE: dict[str, float] | None = None
HKD_CNY_RATE_CACHE: float | None = None
HK_SCOPE_NAME_RE = re.compile(
    r"(\u6e2f\u80a1|\u6e2f\u80a1\u901a|\u6052\u751f|\u9999\u6e2f|H\u80a1|\u4e2d\u6982|\u6caa\u6e2f\u6df1)"
)
DIVIDEND_NAME_RE = re.compile(
    r"(\u6e2f\u80a1|\u6e2f\u80a1\u901a|\u6052\u751f|\u9999\u6e2f|H\u80a1).*(\u7ea2\u5229|\u9ad8\u80a1\u606f|\u80a1\u606f|\u4f4e\u6ce2|\u592e\u4f01)|"
    r"(\u7ea2\u5229|\u9ad8\u80a1\u606f|\u80a1\u606f|\u4f4e\u6ce2|\u592e\u4f01).*(\u6e2f\u80a1|\u6e2f\u80a1\u901a|\u6052\u751f|\u9999\u6e2f|H\u80a1)"
)
HK_CODE_RE = re.compile(r"^\d{5}$")
SSE_ETF_BASIC_SQL = "COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_JBXX_C"
SSE_ETF_COMPONENT_SQL = "COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_COMPONENT_C"
SSE_QUERY_URL = "https://query.sse.com.cn/commonQuery.do"
SZSE_REPORT_URL = "https://www.szse.cn/api/report/ShowReport/data"
SZSE_REPORTDOCS_URL = "https://reportdocs.static.szse.cn/files/text/ETFDown"
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
}
SSE_MARKET_MAP = {
    "101": "SSE",
    "102": "SZSE",
    "103": "HK",
    "105": "INTERBANK",
    "106": "BSE",
    "9999": "OTHER",
}


@dataclass
class HoldingMetric:
    code: str
    name: str
    weight_pct: float
    pe: float | None = None
    pb: float | None = None
    dividend_yield_pct: float | None = None
    market_cap_hkd: float | None = None
    source: str = "eastmoney_hk_f10"
    valuation_error: str = ""
    alt_pe: float | None = None
    alt_pb: float | None = None
    alt_dividend_yield_pct: float | None = None
    alt_date: str = ""
    alt_source: str = ""
    alt_error: str = ""
    holding_source: str = ""
    holding_source_detail: str = ""
    market: str = ""
    weight_source: str = ""
    pcf_quantity: float | None = None
    pcf_substitution_flag: str = ""


def clean_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = (
        str(value)
        .strip()
        .replace(",", "")
        .replace("￥", "")
        .replace("¥", "")
        .replace("元", "")
        .replace(" ", "")
        .replace("\u3000", "")
    )
    if text in {"", "-", "--", "None", "nan", "NaN"}:
        return None
    multiplier = 1.0
    for suffix, factor in (("万亿", 1e12), ("亿", 1e8), ("万", 1e4), ("%", 1.0)):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            multiplier = factor
            break
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def clean_percent(value: Any) -> float | None:
    number = clean_float(value)
    if number is None:
        return None
    if "%" in str(value) or abs(number) > 1:
        return number / 100
    return number


def normalize_hk_code(value: Any) -> str:
    text = str(value).strip()
    if text.endswith(".HK"):
        text = text[:-3]
    digits = re.sub(r"\D", "", text)
    return digits.zfill(5) if digits else text


def parse_jsonp(text: str) -> dict[str, Any]:
    text = text.strip()
    match = re.match(r"^[^(]+\((.*)\)\s*;?$", text, flags=re.S)
    if not match:
        return json.loads(text)
    return json.loads(match.group(1))


def request_json(url: str, *, params: dict[str, Any] | None = None, referer: str = "", attempts: int = 3) -> Any:
    text = request_text(url, params=params, referer=referer, attempts=attempts)
    return json.loads(text)


def request_text(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    referer: str = "",
    attempts: int = 3,
    delay: float = 1.0,
) -> str:
    headers = dict(HTTP_HEADERS)
    if referer:
        headers["Referer"] = referer
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=25)
            response.raise_for_status()
            if response.encoding is None or response.encoding.lower() == "iso-8859-1":
                response.encoding = response.apparent_encoding or "utf-8"
            return response.text
        except Exception as exc:  # noqa: BLE001 - remote data sources fail transiently
            last_exc = exc
            if attempt < attempts:
                time.sleep(delay * attempt)
    raise last_exc or RuntimeError(f"request failed: {url}")


def make_holdings_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    columns = [
        "股票代码",
        "股票名称",
        "占净值比例",
        "持仓市值",
        "季度",
        "holding_source",
        "holding_source_detail",
        "market",
        "weight_source",
        "pcf_quantity",
        "pcf_substitution_flag",
    ]
    return pd.DataFrame(rows, columns=columns)


def get_hkd_cny_rate() -> float:
    global HKD_CNY_RATE_CACHE
    if HKD_CNY_RATE_CACHE is not None:
        return HKD_CNY_RATE_CACHE
    df = ak.fx_spot_quote()
    if df is None or df.empty:
        raise RuntimeError("empty FX quote frame")
    row = df[df["货币对"].astype(str).str.upper() == "HKD/CNY"]
    if row.empty:
        raise RuntimeError("HKD/CNY quote not found")
    bid = clean_float(row.iloc[0].get("买报价"))
    ask = clean_float(row.iloc[0].get("卖报价"))
    if bid is None and ask is None:
        raise RuntimeError("HKD/CNY quote missing bid/ask")
    HKD_CNY_RATE_CACHE = ((bid or ask or 0) + (ask or bid or 0)) / 2
    return HKD_CNY_RATE_CACHE


def get_hk_spot_prices() -> dict[str, float]:
    global HK_SPOT_PRICE_CACHE
    if HK_SPOT_PRICE_CACHE is not None:
        return HK_SPOT_PRICE_CACHE
    df = ak.stock_hk_spot()
    if df is None or df.empty:
        raise RuntimeError("empty HK spot frame")
    prices: dict[str, float] = {}
    for _, row in df.iterrows():
        code = normalize_hk_code(row.get("代码"))
        price = clean_float(row.get("最新价"))
        if code and price is not None and price > 0:
            prices[code] = price
    HK_SPOT_PRICE_CACHE = prices
    return HK_SPOT_PRICE_CACHE


def get_hk_spot_price_hkd(code: str) -> float | None:
    return get_hk_spot_prices().get(normalize_hk_code(code))


def stock_exchange_prefix(code: str) -> str:
    return "sh" + code if code.startswith(("5", "6")) else "sz" + code


def parse_quarter(value: Any) -> tuple[int, int]:
    text = str(value)
    match = re.search(r"(\d{4}).*?([1-4])\s*季度", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.search(r"(\d{4}).*?([1-4])", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    return 0, 0


def get_etf_name_map() -> dict[str, str]:
    try:
        df = ak.fund_etf_spot_em()
        return {str(row["代码"]).zfill(6): str(row["名称"]) for _, row in df.iterrows()}
    except Exception:
        return {}


def get_hk_dividend_candidates(name_map: dict[str, str]) -> list[str]:
    candidates = [code for code, name in name_map.items() if DIVIDEND_NAME_RE.search(str(name))]
    combined = list(dict.fromkeys(candidates + DEFAULT_DIVIDEND_CANDIDATES))
    return combined


def get_hk_candidates(name_map: dict[str, str]) -> list[str]:
    return [code for code, name in name_map.items() if HK_SCOPE_NAME_RE.search(str(name))]


def query_sse_pcf(etf: str, sql_id: str) -> dict[str, Any]:
    text = request_text(
        SSE_QUERY_URL,
        params={
            "isPagination": "false",
            "FUNDID2": etf,
            "sqlId": sql_id,
            "jsonCallBack": "jsonpCallback1",
        },
        referer="https://www.sse.com.cn/disclosure/fund/etflist/",
    )
    return parse_jsonp(text)


def get_sse_pcf_holdings(etf: str) -> tuple[pd.DataFrame, str]:
    basic_payload = query_sse_pcf(etf, SSE_ETF_BASIC_SQL)
    component_payload = query_sse_pcf(etf, SSE_ETF_COMPONENT_SQL)
    basic_rows = basic_payload.get("result") or []
    component_rows = component_payload.get("result") or []
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
        market = SSE_MARKET_MAP.get(str(row.get("UNDERLYION_SECURITY_ID") or ""), "OTHER")
        code = normalize_hk_code(row.get("INSTRUMENT_ID"))
        amount = clean_float(row.get("SUBSTITUTION_CASH_AMOUNT"))
        quantity = clean_float(row.get("QUANTITY"))
        weight_source = "SUBSTITUTION_CASH_AMOUNT/NAVPERCU"
        if (amount is None or amount <= 0) and market == "HK" and quantity and quantity > 0:
            price_hkd = get_hk_spot_price_hkd(code)
            if price_hkd is not None:
                amount = quantity * price_hkd * get_hkd_cny_rate()
                weight_source = "QUANTITY*HK spot price*HKD/CNY/NAVPERCU"
        if market != "HK" or not re.fullmatch(r"\d{5}", code):
            continue
        rows.append(
            {
                "股票代码": code,
                "股票名称": str(row.get("INSTRUMENT_NAME") or ""),
                "占净值比例": None if amount is None or amount <= 0 else amount / nav_per_cu * 100,
                "持仓市值": amount,
                "季度": period,
                "holding_source": "sse_pcf",
                "holding_source_detail": f"SSE ETF PCF {trading_day}".strip(),
                "market": market,
                "weight_source": weight_source,
                "pcf_quantity": quantity,
                "pcf_substitution_flag": str(row.get("SUBSTITUTION_FLAG") or ""),
            }
        )
    df = make_holdings_frame(rows)
    if df.empty:
        raise RuntimeError("SSE PCF had no priced component rows")
    return df, period


def extract_szse_pcf_downloads(report_html: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    for href in re.findall(r"href=['\"]?([^'\"\s>]*eft_download_new\.html\?[^'\"\s>]*)", report_html):
        parsed = urlparse(html.unescape(href))
        query = parse_qs(parsed.query)
        raw_path = unquote((query.get("path") or [""])[0]).strip("/")
        raw_names = unquote((query.get("filename") or [""])[0])
        for name in raw_names.split(";"):
            if name:
                links.append((raw_path, name))
    if not links:
        encode_open = re.findall(r"encode-open=['\"]([^'\"]+)['\"]", report_html)
        for link in encode_open:
            name = Path(link).stem
            if name:
                links.append(("files/text/ETFDown", name))
    return links


def score_szse_pcf_xml(text: str) -> tuple[int, int]:
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError:
        return -1, -1
    ns = {"f": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
    component_path = ".//f:Component" if ns else ".//Component"
    components = root.findall(component_path, ns)
    hk_count = 0
    for node in components:
        source_node = node.find("f:UnderlyingSecurityIDSource", ns) if ns else node.find("UnderlyingSecurityIDSource")
        if source_node is not None and (source_node.text or "").strip() == "103":
            hk_count += 1
    return hk_count, len(components)


def fetch_szse_pcf_xml(etf: str) -> tuple[str, str]:
    payload = request_json(
        SZSE_REPORT_URL,
        params={"CATALOGID": "sgshqd", "loading": "first", "txtJCorDH": etf},
        referer="https://www.szse.cn/disclosure/fund/currency/index.html",
    )
    blocks = payload if isinstance(payload, list) else []
    report_rows = []
    for block in blocks:
        report_rows.extend(block.get("data") or [])
    if not report_rows:
        raise RuntimeError("SZSE PCF report list returned no rows")

    html_cell = html.unescape(str(report_rows[0].get("jjdm") or ""))
    download_names = extract_szse_pcf_downloads(html_cell)
    if not download_names:
        raise RuntimeError("SZSE PCF report row did not include a download filename")

    candidates: list[tuple[int, int, str, str]] = []
    last_error = ""
    for _, name in download_names:
        for suffix in (".xml", ".txt"):
            url = f"{SZSE_REPORTDOCS_URL}/{name}{suffix}"
            try:
                text = request_text(url, referer="https://www.szse.cn/disclosure/fund/currency/index.html")
                if "<html" not in text[:300].lower():
                    hk_count, component_count = score_szse_pcf_xml(text)
                    candidates.append((hk_count, component_count, text, url))
            except Exception as exc:
                last_error = str(exc)
    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best = candidates[0]
        if best[0] > 0 or best[1] > 0:
            return best[2], best[3]
    raise RuntimeError(f"SZSE PCF file download failed: {last_error}")


def xml_child_text(node: ET.Element, name: str, ns: dict[str, str]) -> str:
    child = node.find(f"f:{name}", ns)
    return "" if child is None or child.text is None else child.text.strip()


def get_szse_pcf_holdings(etf: str) -> tuple[pd.DataFrame, str]:
    text, source_url = fetch_szse_pcf_xml(etf)
    root = ET.fromstring(text.encode("utf-8"))
    ns = {"f": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
    find_name = lambda node, name: xml_child_text(node, name, ns) if ns else (node.findtext(name) or "").strip()

    nav_per_cu = clean_float(find_name(root, "NAVperCU"))
    if nav_per_cu is None or nav_per_cu <= 0:
        raise RuntimeError("SZSE PCF missing NAVperCU")
    trading_day = find_name(root, "TradingDay")
    period = f"PCF:{trading_day}" if trading_day else "PCF:SZSE"
    component_path = ".//f:Component" if ns else ".//Component"

    rows: list[dict[str, Any]] = []
    for node in root.findall(component_path, ns):
        market = "HK" if find_name(node, "UnderlyingSecurityIDSource") == "103" else "OTHER"
        code = normalize_hk_code(find_name(node, "UnderlyingSecurityID"))
        quantity = clean_float(find_name(node, "ComponentShare"))
        create_cash = clean_float(find_name(node, "CreationCashSubstitute"))
        premium_ratio = clean_percent(find_name(node, "PremiumRatio")) or 0.0
        underlying_amount = None
        weight_source = "CreationCashSubstitute/(1+PremiumRatio)/NAVperCU"
        if create_cash is not None and create_cash > 0:
            underlying_amount = create_cash / (1 + premium_ratio) if premium_ratio > -0.999 else create_cash
        elif market == "HK" and quantity is not None and quantity > 0:
            price_hkd = get_hk_spot_price_hkd(code)
            if price_hkd is not None:
                underlying_amount = quantity * price_hkd * get_hkd_cny_rate()
                weight_source = "ComponentShare*HK spot price*HKD/CNY/NAVperCU"
        if market != "HK" or not re.fullmatch(r"\d{5}", code):
            continue
        rows.append(
            {
                "股票代码": code,
                "股票名称": find_name(node, "UnderlyingSymbol"),
                "占净值比例": None if underlying_amount is None or underlying_amount <= 0 else underlying_amount / nav_per_cu * 100,
                "持仓市值": underlying_amount,
                "季度": period,
                "holding_source": "szse_pcf",
                "holding_source_detail": source_url,
                "market": market,
                "weight_source": weight_source,
                "pcf_quantity": quantity,
                "pcf_substitution_flag": find_name(node, "SubstituteFlag"),
            }
        )
    df = make_holdings_frame(rows)
    if df.empty:
        raise RuntimeError("SZSE PCF had no priced component rows")
    if not df["股票代码"].astype(str).str.match(HK_CODE_RE).any():
        raise RuntimeError("SZSE PCF had no HK component rows")
    return df, period


def get_pcf_holdings(etf: str) -> tuple[pd.DataFrame, str]:
    if etf.startswith("5"):
        return get_sse_pcf_holdings(etf)
    if etf.startswith(("15", "16")):
        return get_szse_pcf_holdings(etf)
    raise RuntimeError(f"No PCF adapter for ETF exchange: {etf}")


def get_reported_holdings(etf: str, years_back: int = 4) -> tuple[pd.DataFrame, str]:
    frames: list[pd.DataFrame] = []
    current_year = date.today().year
    errors: list[str] = []
    for year in range(current_year, current_year - years_back - 1, -1):
        try:
            df = ak.fund_portfolio_hold_em(symbol=etf, date=str(year))
            if df is not None and not df.empty:
                df = df.copy()
                df["_source_year"] = year
                frames.append(df)
        except Exception as exc:
            errors.append(f"{year}: {exc}")
    if not frames:
        raise RuntimeError(f"No holdings found for {etf}. Errors: {'; '.join(errors[:3])}")
    all_holdings = pd.concat(frames, ignore_index=True)
    all_holdings[["_year", "_quarter"]] = all_holdings["季度"].apply(lambda x: pd.Series(parse_quarter(x)))
    latest = all_holdings.sort_values(["_year", "_quarter"]).iloc[-1]
    year, quarter = int(latest["_year"]), int(latest["_quarter"])
    latest_holdings = all_holdings[(all_holdings["_year"] == year) & (all_holdings["_quarter"] == quarter)].copy()
    period = f"{year}Q{quarter}" if year and quarter else str(latest.get("季度", "unknown"))
    latest_holdings["holding_source"] = "reported_holdings"
    latest_holdings["holding_source_detail"] = "ak.fund_portfolio_hold_em"
    latest_holdings["market"] = latest_holdings["股票代码"].astype(str).str.match(HK_CODE_RE).map({True: "HK", False: ""})
    latest_holdings["weight_source"] = "reported_holding_weight"
    latest_holdings["pcf_quantity"] = None
    latest_holdings["pcf_substitution_flag"] = ""
    return latest_holdings, period


def get_latest_holdings(etf: str, years_back: int = 4, holdings_source: str = "auto") -> tuple[pd.DataFrame, str]:
    if holdings_source not in {"auto", "pcf", "reported"}:
        raise ValueError(f"Unknown holdings source: {holdings_source}")
    if holdings_source == "reported":
        return get_reported_holdings(etf, years_back=years_back)
    try:
        return get_pcf_holdings(etf)
    except Exception as exc:
        if holdings_source == "pcf":
            raise RuntimeError(f"PCF holdings failed for {etf}: {exc}") from exc
        reported, period = get_reported_holdings(etf, years_back=years_back)
        reported["holding_source_detail"] = reported["holding_source_detail"].astype(str) + f"; PCF failed: {exc}"
        return reported, period


def get_hk_primary_valuation(code: str) -> dict[str, float | None]:
    if code in PRIMARY_VALUATION_CACHE:
        return PRIMARY_VALUATION_CACHE[code]
    df = ak.stock_hk_financial_indicator_em(symbol=code)
    if df is None or df.empty:
        raise RuntimeError("empty valuation frame")
    row = df.iloc[0]
    result = {
        "pe": clean_float(row.get("市盈率")),
        "pb": clean_float(row.get("市净率")),
        "dividend_yield_pct": clean_float(row.get("股息率TTM(%)")),
        "market_cap_hkd": clean_float(row.get("总市值(港元)")) or clean_float(row.get("港股市值(港元)")),
    }
    PRIMARY_VALUATION_CACHE[code] = result
    return result


def latest_from_indicator_frame(df: pd.DataFrame, value_col: str) -> tuple[float | None, str]:
    if df is None or df.empty or value_col not in df.columns:
        return None, ""
    temp = df.copy()
    if "date" in temp.columns:
        temp["_date"] = pd.to_datetime(temp["date"], errors="coerce")
        temp = temp.dropna(subset=["_date"]).sort_values("_date")
        if temp.empty:
            return None, ""
        row = temp.iloc[-1]
        return clean_float(row.get(value_col)), str(row["_date"].date())
    row = temp.iloc[-1]
    return clean_float(row.get(value_col)), ""


def get_alt_valuation(code: str) -> dict[str, Any]:
    if code in ALT_VALUATION_CACHE:
        return ALT_VALUATION_CACHE[code]
    out: dict[str, Any] = {"source": "", "error": "", "date": ""}
    # Baidu is preferred when available because it is more likely to be recent.
    try:
        baidu_pe = ak.stock_hk_valuation_baidu(symbol=code, indicator="市盈率(TTM)", period="近一年")
        baidu_pb = ak.stock_hk_valuation_baidu(symbol=code, indicator="市净率", period="近一年")
        pe, pe_date = latest_from_indicator_frame(baidu_pe, "value")
        pb, pb_date = latest_from_indicator_frame(baidu_pb, "value")
        if pe is not None or pb is not None:
            out.update({"source": "baidu", "pe": pe, "pb": pb, "date": pe_date or pb_date})
            ALT_VALUATION_CACHE[code] = out
            return out
    except Exception as exc:
        out["error"] = f"baidu failed: {exc}"

    try:
        eniu_pe = ak.stock_hk_indicator_eniu(symbol="hk" + code, indicator="市盈率")
        eniu_pb = ak.stock_hk_indicator_eniu(symbol="hk" + code, indicator="市净率")
        eniu_dy = ak.stock_hk_indicator_eniu(symbol="hk" + code, indicator="股息率")
        pe, pe_date = latest_from_indicator_frame(eniu_pe, "pe")
        pb, pb_date = latest_from_indicator_frame(eniu_pb, "pb")
        dy_col = "dividend_yield" if "dividend_yield" in eniu_dy.columns else eniu_dy.columns[-1]
        dy, dy_date = latest_from_indicator_frame(eniu_dy, dy_col)
        out.update(
            {
                "source": "eniu",
                "pe": pe,
                "pb": pb,
                "dividend_yield_pct": dy,
                "date": pe_date or pb_date or dy_date,
            }
        )
    except Exception as exc:
        out["error"] = (out.get("error", "") + f"; eniu failed: {exc}").strip("; ")
    ALT_VALUATION_CACHE[code] = out
    return out


def is_stale(date_text: str, max_age_days: int = 180) -> bool:
    if not date_text:
        return True
    try:
        dt = datetime.fromisoformat(date_text).date()
    except ValueError:
        return True
    return (date.today() - dt).days > max_age_days


def annualized_return(first_value: float, last_value: float, first_date: date, last_date: date) -> float | None:
    return common_annualized_return(first_value, last_value, first_date, last_date)


def normalize_price_frame(df: pd.DataFrame, date_col: str, value_col: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "value"])
    if date_col not in df.columns or value_col not in df.columns:
        return pd.DataFrame(columns=["date", "value"])
    return common_normalize_price_frame(df, date_col, value_col)


def value_window(temp: pd.DataFrame, days_back: int, min_days: int) -> dict[str, Any]:
    if len(temp) < 2:
        return {"error": "not enough return observations"}
    last = temp.iloc[-1]
    target = last["date"].date() - timedelta(days=days_back)
    eligible = temp[temp["date"].dt.date <= target]
    first = eligible.iloc[-1] if not eligible.empty else temp.iloc[0]
    first_date = first["date"].date()
    last_date = last["date"].date()
    first_value = float(first["value"])
    last_value = float(last["value"])
    if first_value <= 0 or last_value <= 0:
        return {"error": "invalid return values"}
    total_return = last_value / first_value - 1.0
    ann_return = annualized_return(first_value, last_value, first_date, last_date)
    return {
        "annualized_return_pct": None if ann_return is None else ann_return * 100,
        "total_return_pct": total_return * 100,
        "first_date": str(first_date),
        "last_date": str(last_date),
        "first_value": first_value,
        "last_value": last_value,
        "days": (last_date - first_date).days,
        "short_window": (last_date - first_date).days < min_days,
    }


def pick_return_window(df: pd.DataFrame, date_col: str, value_col: str, min_days: int = 330) -> dict[str, Any]:
    temp = normalize_price_frame(df, date_col, value_col)
    if temp.empty:
        return {"error": "empty return frame"}
    return value_window(temp, days_back=365, min_days=min_days)


def trailing_total_return(temp: pd.DataFrame, days_back: int, min_days: int) -> float | None:
    if temp.empty:
        return None
    window = value_window(temp, days_back=days_back, min_days=min_days)
    if window.get("error") or window.get("short_window"):
        return None
    return clean_float(window.get("total_return_pct"))


def risk_metrics_from_price_frame(temp: pd.DataFrame) -> dict[str, Any]:
    if temp.empty:
        return {"volatility_pct": None, "sortino_ratio": None, "risk_observations": 0}
    series = pd.Series(temp["value"].to_numpy(float), index=pd.DatetimeIndex(temp["date"]))
    metrics = price_series_metrics(series)
    return {
        "volatility_pct": metrics.get("volatility_pct"),
        "sortino_ratio": metrics.get("sortino_ratio"),
        "risk_observations": max(len(temp) - 1, 0),
        "risk_window": metrics.get("risk_window", ""),
    }


def summarize_return_series(temp: pd.DataFrame, source: str) -> dict[str, Any]:
    if temp.empty:
        return {"source": source, "error": "empty return frame"}
    series = pd.Series(temp["value"].to_numpy(float), index=pd.DatetimeIndex(temp["date"]))
    metrics = price_series_metrics(series)
    if metrics.get("error"):
        return {"source": source, "error": metrics["error"]}
    return {
        "source": source,
        "return_basis": "total_return_or_adjusted" if "nav" in source or "qfq" in source else "unadjusted_price_return",
        "annualized_return_pct": metrics["annualized_return_pct"],
        "half_year_return_pct": metrics["half_year_return_pct"],
        "one_year_return_pct": metrics["one_year_return_pct"],
        "three_year_return_pct": metrics["three_year_return_pct"],
        "first_date": metrics["return_window"].split(" to ")[0] if metrics["return_window"] else None,
        "last_date": metrics["return_window"].split(" to ")[-1] if metrics["return_window"] else None,
        "short_window": metrics["annualized_return_pct"] is None,
        "volatility_pct": metrics["volatility_pct"],
        "sortino_ratio": metrics["sortino_ratio"],
        "risk_window": metrics["risk_window"],
        "max_drawdown_pct": metrics["max_drawdown_pct"],
    }


def get_returns(etf: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    start = (date.today() - timedelta(days=365 * 3 + 90)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    try:
        em = None
        last_exc: Exception | None = None
        for _ in range(2):
            try:
                em = ak.fund_etf_hist_em(symbol=etf, period="daily", start_date=start, end_date=end, adjust="qfq")
                break
            except Exception as exc:
                last_exc = exc
                time.sleep(1)
        if em is None:
            raise RuntimeError(str(last_exc))
        em_frame = normalize_price_frame(em, "日期", "收盘")
        result["eastmoney_price_qfq"] = summarize_return_series(em_frame, "eastmoney_price_qfq")
    except Exception as exc:
        result["eastmoney_price_qfq"] = {"source": "eastmoney_price_qfq", "error": str(exc)}
    try:
        nav = ak.fund_etf_fund_info_em(fund=etf, start_date=start, end_date=end)
        value_col = "累计净值" if "累计净值" in nav.columns else "单位净值"
        nav_frame = normalize_price_frame(nav, "净值日期", value_col)
        result["eastmoney_nav"] = summarize_return_series(nav_frame, "eastmoney_nav")
    except Exception as exc:
        result["eastmoney_nav"] = {"source": "eastmoney_nav", "error": str(exc)}
    try:
        sina = ak.fund_etf_hist_sina(symbol=stock_exchange_prefix(etf))
        sina_frame = normalize_price_frame(sina, "date", "close")
        result["sina_price"] = summarize_return_series(sina_frame, "sina_price")
    except Exception as exc:
        result["sina_price"] = {"source": "sina_price", "error": str(exc)}
    return result


def weighted_average(items: list[HoldingMetric], attr: str, positive_only: bool = False) -> tuple[float | None, float]:
    rows = [{"weight_pct": item.weight_pct, attr: getattr(item, attr)} for item in items]
    return common_weighted_average(rows, attr, positive_only=positive_only)


def earnings_yield_pe(items: list[HoldingMetric]) -> tuple[float | None, float, float]:
    rows = [{"weight_pct": item.weight_pct, "pe": item.pe} for item in items]
    return common_earnings_yield_pe(rows)


def summarize(items: list[HoldingMetric]) -> dict[str, Any]:
    total_hk_weight = sum(item.weight_pct for item in items)
    pe_simple, pe_cov = weighted_average(items, "pe", positive_only=False)
    pe_pos_simple, pe_pos_cov = weighted_average(items, "pe", positive_only=True)
    pb, pb_cov = weighted_average(items, "pb", positive_only=True)
    negative_pb_weight = sum(item.weight_pct for item in items if item.pb is not None and item.pb <= 0)
    dy, dy_cov = weighted_average(items, "dividend_yield_pct")
    pe_ey, pe_ey_cov, neg_weight = earnings_yield_pe(items)
    alt_items = [
        item
        for item in items
        if item.alt_source and item.alt_date and not is_stale(item.alt_date) and item.alt_error == ""
    ]
    alt_pe, alt_pe_cov = weighted_average(alt_items, "alt_pe", positive_only=True)
    alt_pb, alt_pb_cov = weighted_average(alt_items, "alt_pb")
    alt_dy, alt_dy_cov = weighted_average(alt_items, "alt_dividend_yield_pct")
    return {
        "total_hk_weight_pct": total_hk_weight,
        "pe_simple": pe_simple,
        "pe_positive_simple": pe_pos_simple,
        "pe_earnings_yield": pe_ey,
        "pb": pb,
        "dividend_yield_pct": dy,
        "coverage": {
            "pe_simple_weight_pct": pe_cov,
            "pe_positive_weight_pct": pe_pos_cov,
            "pe_earnings_yield_weight_pct": pe_ey_cov,
            "negative_pe_weight_pct": neg_weight,
            "negative_pb_weight_pct": negative_pb_weight,
            "pb_weight_pct": pb_cov,
            "dividend_yield_weight_pct": dy_cov,
        },
        "alt_recent": {
            "pe_positive_simple": alt_pe,
            "pb": alt_pb,
            "dividend_yield_pct": alt_dy,
            "pe_weight_pct": alt_pe_cov,
            "pb_weight_pct": alt_pb_cov,
            "dividend_yield_weight_pct": alt_dy_cov,
        },
    }


def pct_gap(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return (a - b) / abs(b) * 100


def build_metrics(holdings: pd.DataFrame, alt_limit: int, sleep_seconds: float) -> list[HoldingMetric]:
    rows: list[HoldingMetric] = []
    hk_holdings = holdings[holdings["股票代码"].astype(str).str.match(HK_CODE_RE)].copy()
    hk_holdings["占净值比例"] = hk_holdings["占净值比例"].apply(clean_float)
    hk_holdings = hk_holdings.dropna(subset=["占净值比例"]).sort_values("占净值比例", ascending=False)
    for idx, (_, row) in enumerate(hk_holdings.iterrows()):
        item = HoldingMetric(
            code=str(row["股票代码"]).zfill(5),
            name=str(row["股票名称"]),
            weight_pct=float(row["占净值比例"]),
            holding_source=str(row.get("holding_source") or ""),
            holding_source_detail=str(row.get("holding_source_detail") or ""),
            market=str(row.get("market") or ""),
            weight_source=str(row.get("weight_source") or ""),
            pcf_quantity=clean_float(row.get("pcf_quantity")),
            pcf_substitution_flag=str(row.get("pcf_substitution_flag") or ""),
        )
        try:
            values = get_hk_primary_valuation(item.code)
            item.pe = values["pe"]
            item.pb = values["pb"]
            item.dividend_yield_pct = values["dividend_yield_pct"]
            item.market_cap_hkd = values["market_cap_hkd"]
        except Exception as exc:
            item.valuation_error = str(exc)
        if idx < alt_limit:
            alt = get_alt_valuation(item.code)
            item.alt_source = str(alt.get("source") or "")
            item.alt_error = str(alt.get("error") or "")
            item.alt_date = str(alt.get("date") or "")
            item.alt_pe = clean_float(alt.get("pe"))
            item.alt_pb = clean_float(alt.get("pb"))
            item.alt_dividend_yield_pct = clean_float(alt.get("dividend_yield_pct"))
        if sleep_seconds:
            time.sleep(sleep_seconds)
        rows.append(item)
    return rows


def format_num(value: Any, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "NA"
    try:
        if isinstance(value, float) and math.isnan(value):
            return "NA"
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def best_return_source(returns: dict[str, Any]) -> tuple[str, float | None]:
    for source in ("eastmoney_nav", "eastmoney_price_qfq", "sina_price"):
        value = returns.get(source, {}).get("annualized_return_pct")
        if value is not None:
            return source, value
    return "", None


def return_table(returns: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    _, primary = best_return_source(returns)
    for source, data in returns.items():
        row = {"source": source}
        if "error" in data:
            row["annualized_return_pct"] = None
            row["window"] = "NA"
            row["gap_vs_reference_pctpt"] = None
            row["note"] = data["error"]
        else:
            value = data.get("annualized_return_pct")
            row["annualized_return_pct"] = value
            row["window"] = f"{data.get('first_date')} to {data.get('last_date')}"
            row["gap_vs_reference_pctpt"] = None if primary is None or value is None else value - primary
            row["note"] = "short window" if data.get("short_window") else ""
        rows.append(row)
    return rows


def report_markdown(result: dict[str, Any], top_n: int = 20) -> str:
    summary = result["summary"]
    returns = result["returns"]
    lines = [
        f"# ETF Look-through Report: {result['etf']} {result['name']}",
        "",
        f"- Generated: {result['generated_at']}",
        f"- Holdings period: {result['holdings_period']}",
        f"- Holdings source: {result.get('holdings_source', 'unknown')}",
        f"- Holdings source detail: {result.get('holdings_source_detail', '')}",
        f"- Total HK holding weight found: {format_num(summary['total_hk_weight_pct'], 2, '%')}",
        "",
        "## Portfolio Metrics",
        "",
        "| Metric | Value | Coverage | Notes |",
        "|---|---:|---:|---|",
        f"| Dividend yield TTM | {format_num(summary['dividend_yield_pct'], 2, '%')} | {format_num(summary['coverage']['dividend_yield_weight_pct'], 2, '%')} | Weighted over covered HK holdings |",
        f"| PB | {format_num(summary['pb'], 2)} | {format_num(summary['coverage']['pb_weight_pct'], 2, '%')} | Excludes non-positive PB; non-positive PB weight {format_num(summary['coverage']['negative_pb_weight_pct'], 2, '%')} |",
        f"| PE simple weighted | {format_num(summary['pe_simple'], 2)} | {format_num(summary['coverage']['pe_simple_weight_pct'], 2, '%')} | Includes negative PE if source reports it |",
        f"| PE positive weighted | {format_num(summary['pe_positive_simple'], 2)} | {format_num(summary['coverage']['pe_positive_weight_pct'], 2, '%')} | Excludes non-positive PE |",
        f"| PE earnings-yield | {format_num(summary['pe_earnings_yield'], 2)} | {format_num(summary['coverage']['pe_earnings_yield_weight_pct'], 2, '%')} | 1 / weighted earnings yield; negative PE weight {format_num(summary['coverage']['negative_pe_weight_pct'], 2, '%')} |",
        "",
        "## Annualized Return Cross-check",
        "",
        "| Source | Annualized return | Window | Gap vs reference | Note |",
        "|---|---:|---|---:|---|",
    ]
    for row in return_table(returns):
        lines.append(
            f"| {row['source']} | {format_num(row['annualized_return_pct'], 2, '%')} | {row['window']} | {format_num(row['gap_vs_reference_pctpt'], 2, ' pctpt')} | {row['note']} |"
        )
    ref_source, _ = best_return_source(returns)
    if ref_source:
        lines.append(f"\nReturn gap reference source: `{ref_source}`.")
    alt = summary["alt_recent"]
    lines.extend(
        [
            "",
            "## Recent Alternate Valuation Cross-check",
            "",
            "| Metric | Primary | Alternate recent | Alt coverage | Gap |",
            "|---|---:|---:|---:|---:|",
            f"| PE positive weighted | {format_num(summary['pe_positive_simple'], 2)} | {format_num(alt['pe_positive_simple'], 2)} | {format_num(alt['pe_weight_pct'], 2, '%')} | {format_num(pct_gap(summary['pe_positive_simple'], alt['pe_positive_simple']), 2, '%')} |",
            f"| PB | {format_num(summary['pb'], 2)} | {format_num(alt['pb'], 2)} | {format_num(alt['pb_weight_pct'], 2, '%')} | {format_num(pct_gap(summary['pb'], alt['pb']), 2, '%')} |",
            f"| Dividend yield | {format_num(summary['dividend_yield_pct'], 2, '%')} | {format_num(alt['dividend_yield_pct'], 2, '%')} | {format_num(alt['dividend_yield_weight_pct'], 2, '%')} | {format_num(pct_gap(summary['dividend_yield_pct'], alt['dividend_yield_pct']), 2, '%')} |",
            "",
            "Alternate valuation rows are ignored when their latest date is older than 180 days.",
            "",
            f"## Top Holdings (top {top_n})",
            "",
            "| Code | Name | Weight | Holding source | PE | PB | Div yield | Alt source/date | Error |",
            "|---|---|---:|---|---:|---:|---:|---|---|",
        ]
    )
    for item in result["holdings"][:top_n]:
        alt_label = f"{item.get('alt_source', '')} {item.get('alt_date', '')}".strip()
        error = item.get("valuation_error") or item.get("alt_error") or ""
        lines.append(
            f"| {item['code']} | {item['name']} | {format_num(item['weight_pct'], 2, '%')} | {item.get('holding_source', '')} | {format_num(item.get('pe'), 2)} | {format_num(item.get('pb'), 2)} | {format_num(item.get('dividend_yield_pct'), 2, '%')} | {alt_label} | {str(error).replace('|', '/')} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation Notes",
            "",
            "- This report is a data calculation, not investment advice.",
            "- Holdings can be stale when only quarterly disclosure is available.",
            "- Price annualized return is not a dividend-reinvested total return unless the selected NAV series captures distributions.",
        ]
    )
    return "\n".join(lines) + "\n"


def analyze_etf(
    etf: str,
    name_map: dict[str, str],
    alt_limit: int,
    sleep_seconds: float,
    holdings_source: str = "auto",
) -> dict[str, Any]:
    holdings, period = get_latest_holdings(etf, holdings_source=holdings_source)
    hk_mask = holdings["股票代码"].astype(str).str.match(HK_CODE_RE)
    raw_hk_rows = int(hk_mask.sum())
    missing_weight_rows = int(pd.to_numeric(holdings.loc[hk_mask, "占净值比例"], errors="coerce").isna().sum())
    metrics = build_metrics(holdings, alt_limit=alt_limit, sleep_seconds=sleep_seconds)
    summary = summarize(metrics)
    summary["raw_hk_rows"] = raw_hk_rows
    summary["effective_hk_rows"] = len(metrics)
    summary["missing_weight_rows"] = missing_weight_rows
    returns = get_returns(etf)
    actual_sources = sorted({item.holding_source for item in metrics if item.holding_source})
    source_details = sorted({item.holding_source_detail for item in metrics if item.holding_source_detail})
    return {
        "etf": etf,
        "name": name_map.get(etf, ""),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "holdings_period": period,
        "holdings_source": ",".join(actual_sources) or holdings_source,
        "holdings_source_detail": "; ".join(source_details[:3]),
        "summary": summary,
        "returns": returns,
        "holdings": [asdict(item) for item in metrics],
    }


def write_outputs(result: dict[str, Any], out_dir: Path, top_n: int) -> None:
    etf = result["etf"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{etf}_lookthrough.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / f"{etf}_lookthrough.md").write_text(report_markdown(result, top_n=top_n), encoding="utf-8")
    pd.DataFrame(result["holdings"]).to_csv(out_dir / f"{etf}_holdings.csv", index=False, encoding="utf-8-sig")


def write_summary(results: list[dict[str, Any]], out_dir: Path, rank_by_dividend: bool = False, top: int | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    ordered = list(results)
    if rank_by_dividend:
        ordered.sort(
            key=lambda item: (
                item["summary"].get("dividend_yield_pct") is not None,
                item["summary"].get("dividend_yield_pct") or -1,
            ),
            reverse=True,
        )
    if top is not None:
        ordered = ordered[:top]
    for result in ordered:
        s = result["summary"]
        ref_source, ref_return = best_return_source(result["returns"])
        r = result["returns"].get(ref_source, {}) if ref_source else {}
        rows.append(
            {
                "etf": result["etf"],
                "name": result["name"],
                "holdings_period": result["holdings_period"],
                "holdings_source": result.get("holdings_source", ""),
                "hk_weight_pct": s["total_hk_weight_pct"],
                "raw_hk_rows": s.get("raw_hk_rows"),
                "effective_hk_rows": s.get("effective_hk_rows"),
                "missing_weight_rows": s.get("missing_weight_rows"),
                "dividend_yield_pct": s["dividend_yield_pct"],
                "dividend_yield_coverage_pct": s["coverage"]["dividend_yield_weight_pct"],
                "pb": s["pb"],
                "pb_coverage_pct": s["coverage"]["pb_weight_pct"],
                "pe_simple": s["pe_simple"],
                "pe_positive": s["pe_positive_simple"],
                "pe_earnings_yield": s["pe_earnings_yield"],
                "pe_coverage_pct": s["coverage"]["pe_earnings_yield_weight_pct"],
                "negative_pe_weight_pct": s["coverage"]["negative_pe_weight_pct"],
                "annualized_return_source": ref_source,
                "return_basis": r.get("return_basis"),
                "annualized_return_pct": ref_return,
                "sortino_ratio": r.get("sortino_ratio"),
                "volatility_pct": r.get("volatility_pct"),
                "half_year_return_pct": r.get("half_year_return_pct"),
                "one_year_return_pct": r.get("one_year_return_pct"),
                "three_year_return_pct": r.get("three_year_return_pct"),
                "risk_window": r.get("risk_window"),
                "return_window": f"{r.get('first_date')} to {r.get('last_date')}" if "error" not in r else r.get("error"),
            }
        )
    df = pd.DataFrame(rows)
    csv_name = "ranked_top5.csv" if rank_by_dividend else "summary.csv"
    md_name = "ranked_top5.md" if rank_by_dividend else "summary.md"
    df.to_csv(out_dir / csv_name, index=False, encoding="utf-8-sig")
    lines = [
        "# ETF Look-through Dividend Ranking" if rank_by_dividend else "# ETF Look-through Batch Summary",
        "",
        "| ETF | Name | Holdings | Source | Div yield | PB | PE EY | Annualized | Sortino | Volatility | 6M | 1Y | 3Y |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['etf']} | {row['name']} | {row['holdings_period']} | {row['holdings_source']} | {format_num(row['dividend_yield_pct'], 2, '%')} | {format_num(row['pb'], 2)} | {format_num(row['pe_earnings_yield'], 2)} | {format_num(row['annualized_return_pct'], 2, '%')} | {format_num(row['sortino_ratio'], 2)} | {format_num(row['volatility_pct'], 2, '%')} | {format_num(row['half_year_return_pct'], 2, '%')} | {format_num(row['one_year_return_pct'], 2, '%')} | {format_num(row['three_year_return_pct'], 2, '%')} |"
        )
    (out_dir / md_name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_errors(errors: list[dict[str, str]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    if errors:
        pd.DataFrame(errors).to_csv(out_dir / "errors.csv", index=False, encoding="utf-8-sig")
        lines = ["# ETF Look-through Errors", "", "| ETF | Error |", "|---|---|"]
        for row in errors:
            lines.append(f"| {row['etf']} | {row['error'].replace('|', '/')} |")
        (out_dir / "errors.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        for name in ("errors.csv", "errors.md"):
            path = out_dir / name
            if path.exists():
                path.unlink()


def write_progress_summaries(results: list[dict[str, Any]], errors: list[dict[str, str]], out_dir: Path, args: argparse.Namespace) -> None:
    write_summary(results, out_dir=out_dir)
    if args.rank_high_dividend or args.rank_hk_all:
        write_summary(results, out_dir=out_dir, rank_by_dividend=True, top=args.rank_top)
    write_errors(errors, out_dir=out_dir)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--etf", action="append", help="ETF code. Repeat or use comma-separated values.")
    parser.add_argument("--sample-hk", action="store_true", help="Run default five A-share listed Hong Kong dividend ETF samples.")
    parser.add_argument("--rank-high-dividend", action="store_true", help="Rank Hong Kong dividend/high-yield ETF candidates and write ranked_top5 outputs.")
    parser.add_argument("--rank-hk-all", action="store_true", help="Rank all A-share ETFs whose names indicate Hong Kong/H-share/Hang Seng exposure by look-through dividend yield.")
    parser.add_argument("--rank-top", type=int, default=5, help="Number of ranked ETFs to include in ranked_top5 outputs.")
    parser.add_argument("--out-dir", default="lookthrough-output", help="Output directory.")
    parser.add_argument("--alt-limit", type=int, default=3, help="Number of top holdings to cross-check with alternate valuation sources.")
    parser.add_argument("--top-n", type=int, default=20, help="Number of holdings to include in the markdown report.")
    parser.add_argument("--sleep", type=float, default=0.15, help="Sleep seconds between constituent requests.")
    parser.add_argument(
        "--holdings-source",
        choices=("auto", "pcf", "reported"),
        default="auto",
        help="Holdings source: auto prefers exchange PCF and falls back to reported quarterly holdings.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    etfs: list[str] = []
    if args.sample_hk:
        etfs.extend(DEFAULT_SAMPLE_ETFS)
    for raw in args.etf or []:
        etfs.extend(part.strip() for part in raw.split(",") if part.strip())
    etfs = [code.zfill(6) for code in dict.fromkeys(etfs)]
    if not etfs:
        if not args.rank_high_dividend and not args.rank_hk_all:
            raise SystemExit("Provide --etf <code>, --sample-hk, --rank-high-dividend, or --rank-hk-all.")
    out_dir = Path(args.out_dir)
    name_map = get_etf_name_map()
    if args.rank_hk_all:
        etfs.extend(get_hk_candidates(name_map))
    if args.rank_high_dividend:
        etfs.extend(get_hk_dividend_candidates(name_map))
    etfs = [code.zfill(6) for code in dict.fromkeys(etfs)]
    results = []
    errors: list[dict[str, str]] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for etf in etfs:
        print(f"Analyzing {etf}...", file=sys.stderr)
        try:
            result = analyze_etf(
                etf,
                name_map=name_map,
                alt_limit=args.alt_limit,
                sleep_seconds=args.sleep,
                holdings_source=args.holdings_source,
            )
            write_outputs(result, out_dir=out_dir, top_n=args.top_n)
            results.append(result)
        except Exception as exc:
            errors.append({"etf": etf, "error": str(exc)})
            print(f"ERROR {etf}: {exc}", file=sys.stderr)
        write_progress_summaries(results, errors, out_dir, args)
    if not results:
        raise SystemExit(f"No ETF completed. See {out_dir / 'errors.md'}")
    write_summary(results, out_dir=out_dir)
    if args.rank_high_dividend or args.rank_hk_all:
        write_summary(results, out_dir=out_dir, rank_by_dividend=True, top=args.rank_top)
        print(str(out_dir / "ranked_top5.md"))
    else:
        print(str(out_dir / "summary.md"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
