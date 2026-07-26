#!/usr/bin/env python
"""PCF look-through metrics for A-share dividend themed ETFs.

This is a local A-share variant of the installed HK ETF PCF skill. It:
1. finds A-share listed ETFs whose names contain 红利/分红/股息;
2. reads full daily PCF baskets from SSE/SZSE;
3. keeps ETFs whose PCF basket is mainly A-share stocks;
4. computes weighted dividend yield, PE, PB, and ETF return/risk metrics.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import html
import json
import math
import re
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd
import requests

from pcf_common import (
    annualized_return as common_annualized_return,
    earnings_yield_pe as common_earnings_yield_pe,
    normalize_price_frame as common_normalize_price_frame,
    price_series_metrics,
    publish_staged_files,
    weighted_average as common_weighted_average,
)

try:
    import akshare as ak
except ImportError as exc:
    raise SystemExit("Missing dependency: akshare") from exc


TRADING_DAYS_PER_YEAR = 252
SSE_ETF_BASIC_SQL = "COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_JBXX_C"
SSE_ETF_COMPONENT_SQL = "COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_COMPONENT_C"
SSE_QUERY_URL = "https://query.sse.com.cn/commonQuery.do"
SZSE_REPORT_URL = "https://www.szse.cn/api/report/ShowReport/data"
SZSE_REPORTDOCS_URL = "https://reportdocs.static.szse.cn/files/text/ETFDown"
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
HTTP_SESSION = requests.Session()
HTTP_SESSION.trust_env = False
SSE_MARKET_MAP = {"101": "SSE", "102": "SZSE", "106": "BSE", "103": "HK"}
SZSE_MARKET_MAP = {"101": "SSE", "102": "SZSE", "106": "BSE", "103": "HK"}
A_MARKETS = {"SSE", "SZSE", "BSE"}
KEYWORD_RE = re.compile(r"(?:红利|分红|股息)")
CROSS_BORDER_RE = re.compile(r"(?:港股|港股通|恒生|香港|H股|中概|纳斯达克|标普500|日经|德国|沙特|QDII)", re.I)
MIN_RANK_COVERAGE = 80.0


def retry_call(label: str, func, attempts: int = 4, delay: float = 2.0):
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:
            last_exc = exc
            if attempt == attempts:
                break
            print(f"{label} failed on attempt {attempt}/{attempts}: {exc}; retrying...")
            time.sleep(delay * attempt)
    raise last_exc or RuntimeError(f"{label} failed")


def clean_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
    text = (
        str(value)
        .strip()
        .replace(",", "")
        .replace(" ", "")
        .replace("\u3000", "")
        .replace("￥", "")
        .replace("¥", "")
    )
    if text in {"", "-", "--", "None", "nan", "NaN", "NaT", "inf", "-inf", "Infinity", "-Infinity"}:
        return None
    if text.endswith("%"):
        text = text[:-1]
    try:
        number = float(text)
    except ValueError:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def parse_jsonp(text: str) -> dict[str, Any]:
    text = text.strip()
    match = re.match(r"^[^(]+\((.*)\)\s*;?$", text, flags=re.S)
    return json.loads(match.group(1)) if match else json.loads(text)


def request_text(url: str, *, params: dict[str, Any] | None = None, referer: str = "") -> str:
    headers = dict(HTTP_HEADERS)
    if referer:
        headers["Referer"] = referer
    last_exc: Exception | None = None
    for attempt in range(1, 3):
        try:
            response = HTTP_SESSION.get(url, params=params, headers=headers, timeout=12)
            response.raise_for_status()
            break
        except requests.HTTPError as exc:
            last_exc = exc
            if exc.response is not None and exc.response.status_code == 404:
                raise
        except requests.RequestException as exc:
            last_exc = exc
        if attempt == 2:
            raise last_exc or RuntimeError(f"HTTP request failed: {url}")
        time.sleep(0.8 * attempt)
    if response.encoding is None or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def request_json(url: str, *, params: dict[str, Any] | None = None, referer: str = "") -> Any:
    return json.loads(request_text(url, params=params, referer=referer))


def code_market(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("6", "5")):
        return "sh"
    return "sz"


def is_a_stock_code(code: str) -> bool:
    raw = re.sub(r"\D", "", str(code))
    if len(raw) != 6:
        return False
    code = raw
    if code.startswith(("15", "16", "18", "50", "51", "52", "56", "58")):
        return False
    return code.startswith(("000", "001", "002", "003", "300", "301", "6", "8", "4"))


def normalize_stock_code(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value))
    return digits.zfill(6) if digits else ""


def column(df: pd.DataFrame, names: list[str], fallback_index: int) -> str:
    for name in names:
        if name in df.columns:
            return name
    return str(df.columns[fallback_index])


def load_etf_candidates() -> pd.DataFrame:
    try:
        df = retry_call("fund_etf_spot_em", ak.fund_etf_spot_em)
    except Exception:
        fallback = Path("lookthrough-dividend-keywords/keyword_dividend_etfs_all_ascii.csv")
        if not fallback.exists():
            raise
        prior = pd.read_csv(fallback, encoding="utf-8-sig")
        prior = prior.rename(columns={"code": "ETF代码", "name": "ETF名称"})
        prior["ETF代码"] = prior["ETF代码"].astype(str).str.zfill(6)
        prior["ETF名称"] = prior["ETF名称"].astype(str)
        return prior[["ETF代码", "ETF名称"]].drop_duplicates("ETF代码")
    code_col = column(df, ["代码"], 0)
    name_col = column(df, ["名称"], 1)
    out = df[[code_col, name_col]].copy()
    out.columns = ["ETF代码", "ETF名称"]
    out["ETF代码"] = out["ETF代码"].astype(str).str.zfill(6)
    out["ETF名称"] = out["ETF名称"].astype(str)
    out = out[out["ETF名称"].str.contains(KEYWORD_RE, regex=True, na=False)]
    out = out[~out["ETF名称"].str.contains(CROSS_BORDER_RE, regex=True, na=False)]
    return out.drop_duplicates("ETF代码")


def load_a_stock_snapshot() -> dict[str, dict[str, Any]]:
    df = retry_call("stock_zh_a_spot", ak.stock_zh_a_spot)
    code_col = column(df, ["代码"], 0)
    name_col = column(df, ["名称"], 1)
    price_col = column(df, ["最新价"], 2)
    print("Loading A-share price snapshot...")
    snapshot: dict[str, dict[str, Any]] = {}
    for _, row in df.iterrows():
        code = normalize_stock_code(row.get(code_col))
        if not code:
            continue
        snapshot[code] = {
            "name": str(row.get(name_col) or ""),
            "price": clean_float(row.get(price_col)),
        }
    return snapshot


def sina_symbol(code: str) -> str:
    code = normalize_stock_code(code)
    if code.startswith("6"):
        return "sh" + code
    if code.startswith(("8", "4", "9")):
        return "bj" + code
    return "sz" + code


def fetch_sina_prices(codes: list[str], chunk_size: int = 180) -> dict[str, float]:
    out: dict[str, float] = {}
    clean_codes = [normalize_stock_code(code) for code in codes if is_a_stock_code(code)]
    symbols = [sina_symbol(code) for code in sorted(set(clean_codes))]
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i : i + chunk_size]
        if not chunk:
            continue
        url = "https://hq.sinajs.cn/list=" + ",".join(chunk)
        text = retry_call(
            "sina quote",
            lambda url=url: request_text(
                url,
                referer="https://finance.sina.com.cn",
            ),
            attempts=3,
            delay=1.0,
        )
        try:
            text = text.encode("iso-8859-1").decode("gbk")
        except Exception:
            pass
        for match in re.finditer(r"var hq_str_([a-z]{2})(\d{6})=\"([^\"]*)\";", text):
            code = match.group(2)
            fields = match.group(3).split(",")
            if len(fields) > 3:
                price = clean_float(fields[3])
                if price is not None and price > 0:
                    out[code] = price
    return out


def get_baidu_latest_valuation(code: str, indicator: str) -> float | None:
    def fetch() -> pd.DataFrame:
        return ak.stock_zh_valuation_baidu(symbol=code, indicator=indicator, period="近一年")

    try:
        df = retry_call(f"baidu valuation {code} {indicator}", fetch, attempts=2, delay=1.0)
        if df is None or df.empty:
            return None
        value_col = "value" if "value" in df.columns else str(df.columns[-1])
        value = pd.to_numeric(df[value_col], errors="coerce").dropna()
        return None if value.empty else float(value.iloc[-1])
    except Exception:
        return None


def enrich_stock_metrics(prices: dict[str, float], workers: int) -> dict[str, dict[str, Any]]:
    today = date.today()
    codes = sorted(prices)

    def fetch_one(code: str) -> tuple[str, dict[str, Any]]:
        price = prices.get(code)
        pe = get_baidu_latest_valuation(code, "市盈率(TTM)")
        pb = get_baidu_latest_valuation(code, "市净率")
        dy, source = latest_dividend_yield(code, price, today)
        return code, {
            "price": price,
            "pe": pe,
            "pb": pb,
            "dividend_yield_pct": dy,
            "dividend_source": source,
        }

    out: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_one, code): code for code in codes}
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            code, metrics = future.result()
            out[code] = metrics
            if index % 50 == 0:
                print(f"Stock metric lookups: {index}/{len(codes)}")
    return out


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
        amount = clean_float(row.get("SUBSTITUTION_CASH_AMOUNT"))
        code = normalize_stock_code(row.get("INSTRUMENT_ID"))
        quantity = clean_float(row.get("QUANTITY"))
        market = SSE_MARKET_MAP.get(str(row.get("UNDERLYION_SECURITY_ID") or ""), "OTHER")
        if market in A_MARKETS and not is_a_stock_code(code):
            market = "OTHER"
        if not code:
            continue
        if amount is not None and amount <= 0:
            amount = None
        if amount is None and not (market in A_MARKETS and is_a_stock_code(code)):
            continue
        rows.append(
            {
                "股票代码": code,
                "股票名称": str(row.get("INSTRUMENT_NAME") or ""),
                "市场": market,
                "权重%": None if amount is None else amount / nav_per_cu * 100,
                "市值": amount,
                "数量": quantity,
                "隐含价格": None
                if amount is None or not quantity or quantity == 0
                else amount / quantity,
                "替代标志": str(row.get("SUBSTITUTION_FLAG") or ""),
                "权重来源": "SUBSTITUTION_CASH_AMOUNT/NAVPERCU" if amount is not None else "unresolved PCF component",
                "NAVperCU": nav_per_cu,
            }
        )
    if not rows:
        raise RuntimeError("SSE PCF had no component rows")
    return pd.DataFrame(rows), period


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
        for link in re.findall(r"encode-open=['\"]([^'\"]+)['\"]", report_html):
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
    a_count = 0
    for node in components:
        source = xml_child_text(node, "UnderlyingSecurityIDSource", ns) if ns else (node.findtext("UnderlyingSecurityIDSource") or "")
        code = normalize_stock_code(xml_child_text(node, "UnderlyingSecurityID", ns) if ns else (node.findtext("UnderlyingSecurityID") or ""))
        if SZSE_MARKET_MAP.get(source.strip(), "OTHER") in A_MARKETS and is_a_stock_code(code):
            a_count += 1
    return a_count, len(components)


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
                    a_count, component_count = score_szse_pcf_xml(text)
                    candidates.append((a_count, component_count, text, url))
            except Exception as exc:
                last_error = str(exc)
    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best = candidates[0]
        if best[1] > 0:
            return best[2], best[3]
    raise RuntimeError(f"SZSE PCF file download failed: {last_error}")


def xml_child_text(node: ET.Element, name: str, ns: dict[str, str]) -> str:
    child = node.find(f"f:{name}", ns)
    return "" if child is None or child.text is None else child.text.strip()


def get_szse_pcf_holdings(etf: str, snapshot: dict[str, dict[str, Any]]) -> tuple[pd.DataFrame, str]:
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
    for node in root.findall(component_path, ns):
        source = find_name(node, "UnderlyingSecurityIDSource")
        market = SZSE_MARKET_MAP.get(source, "OTHER")
        code = normalize_stock_code(find_name(node, "UnderlyingSecurityID"))
        quantity = clean_float(find_name(node, "ComponentShare"))
        create_cash = clean_float(find_name(node, "CreationCashSubstitute"))
        premium_ratio = clean_float(find_name(node, "PremiumRatio")) or 0.0
        if abs(premium_ratio) > 1:
            premium_ratio /= 100
        amount = None
        weight_source = "CreationCashSubstitute/(1+PremiumRatio)/NAVperCU"
        if create_cash is not None and create_cash > 0:
            amount = create_cash / (1 + premium_ratio) if premium_ratio > -0.999 else create_cash
        elif market in A_MARKETS and quantity is not None and quantity > 0:
            price = (snapshot.get(code) or {}).get("price")
            if price is not None and price > 0:
                amount = quantity * price
                weight_source = "ComponentShare*A-share latest price/NAVperCU"
        if not code:
            continue
        if market in A_MARKETS and not is_a_stock_code(code):
            market = "OTHER"
        if amount is None and not (market in A_MARKETS and is_a_stock_code(code)):
            continue
        if amount is None:
            weight_source = "unresolved PCF component"
        rows.append(
            {
                "股票代码": code,
                "股票名称": find_name(node, "UnderlyingSymbol") or (snapshot.get(code) or {}).get("name", ""),
                "市场": market,
                "权重%": None if amount is None else amount / nav_per_cu * 100,
                "市值": amount,
                "数量": quantity,
                "隐含价格": None if amount is None or not quantity or quantity == 0 else amount / quantity,
                "替代标志": find_name(node, "SubstituteFlag"),
                "权重来源": weight_source,
                "持仓来源详情": source_url,
                "NAVperCU": nav_per_cu,
            }
        )
    if not rows:
        raise RuntimeError("SZSE PCF had no component rows")
    return pd.DataFrame(rows), period


def get_pcf_holdings(etf: str, snapshot: dict[str, dict[str, Any]]) -> tuple[pd.DataFrame, str, str]:
    if etf.startswith("5"):
        df, period = get_sse_pcf_holdings(etf)
        return df, period, "sse_pcf"
    if etf.startswith(("15", "16")):
        df, period = get_szse_pcf_holdings(etf, snapshot)
        return df, period, "szse_pcf"
    raise RuntimeError(f"No PCF adapter for ETF exchange: {etf}")


def latest_dividend_yield(code: str, price: float | None, today: date) -> tuple[float | None, str]:
    if price is None or price <= 0:
        return None, "missing price"
    try:
        df = ak.stock_history_dividend_detail(symbol=code, indicator="分红")
        if df is None or df.empty:
            return None, "empty dividend frame"
        amount_col = "派息" if "派息" in df.columns else str(df.columns[3])
        status_col = "进度" if "进度" in df.columns else str(df.columns[4])
        ex_col = "除权除息日" if "除权除息日" in df.columns else str(df.columns[5])
        temp = df.copy()
        temp["_ex_date"] = pd.to_datetime(temp[ex_col], errors="coerce").dt.normalize()
        temp["_cash_per_10"] = pd.to_numeric(temp[amount_col], errors="coerce")
        temp["_status"] = temp[status_col].astype(str)
        today_ts = pd.Timestamp(today)
        cutoff = today_ts - pd.Timedelta(days=365)
        implemented = temp[
            (temp["_ex_date"].notna())
            & (temp["_ex_date"] <= today_ts)
            & (temp["_ex_date"] >= cutoff)
            & temp["_status"].str.contains("实施", na=False)
            & (temp["_cash_per_10"] > 0)
        ]
        if implemented.empty:
            return 0.0, "no implemented cash dividend in trailing 12 months"
        cash_per_share = float(implemented["_cash_per_10"].sum()) / 10.0
        return cash_per_share / price * 100, f"trailing_12m_ex_date:{cutoff.date().isoformat()} to {today.isoformat()}"
    except Exception as exc:
        return None, str(exc)[:160]


def enrich_dividend_yields(snapshot: dict[str, dict[str, Any]], codes: list[str], workers: int) -> None:
    today = date.today()
    unique_codes = sorted(set(codes))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(latest_dividend_yield, code, (snapshot.get(code) or {}).get("price"), today): code
            for code in unique_codes
        }
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            code = futures[future]
            dy, source = future.result()
            snapshot.setdefault(code, {})["dividend_yield_pct"] = dy
            snapshot[code]["dividend_source"] = source
            if index % 100 == 0:
                print(f"Dividend lookups: {index}/{len(unique_codes)}")


def weighted_average(rows: list[dict[str, Any]], key: str, positive_only: bool = False) -> tuple[float | None, float]:
    return common_weighted_average(rows, key, positive_only=positive_only)


def earnings_yield_pe(rows: list[dict[str, Any]]) -> tuple[float | None, float, float]:
    return common_earnings_yield_pe(rows)


def annualized_return(first_value: float, last_value: float, first_date: date, last_date: date) -> float | None:
    return common_annualized_return(first_value, last_value, first_date, last_date)


def normalize_price_frame(df: pd.DataFrame, date_col: str, value_col: str) -> pd.DataFrame:
    return common_normalize_price_frame(df, date_col, value_col)


def summarize_period_return(temp: pd.DataFrame, days: int) -> float | None:
    if temp.empty:
        return None
    end = temp["date"].max()
    start_floor = end - pd.Timedelta(days=days)
    window = temp[temp["date"] >= start_floor]
    if len(window) < 2:
        return None
    first = float(window.iloc[0]["value"])
    last = float(window.iloc[-1]["value"])
    return (last / first - 1) * 100 if first > 0 else None


def risk_metrics_from_price_frame(temp: pd.DataFrame) -> dict[str, Any]:
    if temp.empty:
        return {"volatility_pct": None, "sortino_ratio": None, "risk_observations": 0}
    series = pd.Series(temp["value"].to_numpy(float), index=pd.DatetimeIndex(temp["date"]))
    metrics = price_series_metrics(series)
    return {
        "volatility_pct": metrics.get("volatility_pct"),
        "sortino_ratio": metrics.get("sortino_ratio"),
        "risk_observations": max(len(temp) - 1, 0),
    }


def summarize_return_series(temp: pd.DataFrame, source: str) -> dict[str, Any]:
    if len(temp) < 2:
        return {"source": source, "error": "not enough observations"}
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
        "return_window": metrics["return_window"],
        "risk_window": metrics["risk_window"],
        "volatility_pct": metrics["volatility_pct"],
        "sortino_ratio": metrics["sortino_ratio"],
        "max_drawdown_pct": metrics["max_drawdown_pct"],
    }


def get_returns(etf: str) -> dict[str, Any]:
    start = (date.today() - timedelta(days=366 * 4)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    candidates: list[dict[str, Any]] = []
    try:
        nav = ak.fund_etf_fund_info_em(fund=etf, start_date=start, end_date=end)
        value_col = "累计净值" if "累计净值" in nav.columns else "单位净值"
        candidates.append(summarize_return_series(normalize_price_frame(nav, "净值日期", value_col), "eastmoney_nav"))
    except Exception as exc:
        candidates.append({"source": "eastmoney_nav", "error": str(exc)})
    try:
        price = ak.fund_etf_hist_em(symbol=etf, period="daily", start_date=start, end_date=end, adjust="qfq")
        candidates.append(summarize_return_series(normalize_price_frame(price, "日期", "收盘"), "eastmoney_price_qfq"))
    except Exception as exc:
        candidates.append({"source": "eastmoney_price_qfq", "error": str(exc)})
    try:
        sina = ak.fund_etf_hist_sina(symbol=f"{code_market(etf)}{etf}")
        candidates.append(summarize_return_series(normalize_price_frame(sina, "date", "close"), "sina_price"))
    except Exception as exc:
        candidates.append({"source": "sina_price", "error": str(exc)})
    for source in ("eastmoney_nav", "eastmoney_price_qfq", "sina_price"):
        for item in candidates:
            if item.get("source") == source and not item.get("error") and item.get("annualized_return_pct") is not None:
                return item
    return next((item for item in candidates if not item.get("error")), candidates[0])


def build_metrics_for_etf(
    etf: str,
    name: str,
    metrics: dict[str, dict[str, Any]],
    min_a_weight: float,
    implied_prices: dict[str, float] | None = None,
    holdings: pd.DataFrame | None = None,
    period: str = "",
    source: str = "",
) -> tuple[dict[str, Any] | None, pd.DataFrame | None, str | None]:
    try:
        if holdings is None:
            holdings, period, source = get_pcf_holdings(etf, {})
        holdings = holdings.copy()
        holdings["权重%"] = pd.to_numeric(holdings["权重%"], errors="coerce")
        if implied_prices:
            fill_mask = (
                holdings["市场"].isin(A_MARKETS)
                & holdings["股票代码"].astype(str).map(is_a_stock_code)
                & holdings["权重%"].isna()
            )
            for idx, h in holdings.loc[fill_mask].iterrows():
                code = normalize_stock_code(h["股票代码"])
                quantity = clean_float(h.get("数量"))
                price = implied_prices.get(code)
                nav_per_cu = clean_float(h.get("NAVperCU"))
                if quantity and price and nav_per_cu:
                    amount = quantity * price
                    holdings.at[idx, "市值"] = amount
                    holdings.at[idx, "隐含价格"] = price
                    holdings.at[idx, "权重%"] = amount / nav_per_cu * 100
                    holdings.at[idx, "权重来源"] = "ComponentShare*implied price/NAVperCU"
        a_holdings = holdings[holdings["市场"].isin(A_MARKETS) & holdings["股票代码"].astype(str).map(is_a_stock_code)].copy()
        a_holdings["权重%"] = pd.to_numeric(a_holdings["权重%"], errors="coerce")
        a_weight = float(a_holdings["权重%"].sum())
        if a_weight < min_a_weight:
            return None, holdings, f"A-share PCF weight below threshold: {a_weight:.2f}%"
        rows: list[dict[str, Any]] = []
        for _, row in a_holdings.iterrows():
            code = normalize_stock_code(row["股票代码"])
            stock = metrics.get(code) or {}
            rows.append(
                {
                    "code": code,
                    "weight_pct": clean_float(row["权重%"]) or 0.0,
                    "pe": stock.get("pe"),
                    "pb": stock.get("pb"),
                    "dividend_yield_pct": stock.get("dividend_yield_pct"),
                }
            )
        pe, pe_cov, neg_pe = earnings_yield_pe(rows)
        pb, pb_cov = weighted_average(rows, "pb", positive_only=True)
        dy, dy_cov = weighted_average(rows, "dividend_yield_pct")
        returns = get_returns(etf)
        return (
            {
                "ETF代码": etf,
                "ETF名称": name,
                "持仓期": period,
                "持仓来源": source,
                "A股持仓权重%": a_weight,
                "股票持仓数": len(a_holdings),
                "PCF原始股票行数": len(a_holdings),
                "有效权重行数": int(a_holdings["权重%"].notna().sum()),
                "未定价/缺失权重行数": int(a_holdings["权重%"].isna().sum()),
                "股息率%": dy,
                "PE": pe,
                "PB": pb,
                "年化收益%": returns.get("annualized_return_pct"),
                "索提诺比率": returns.get("sortino_ratio"),
                "波动率%": returns.get("volatility_pct"),
                "近半年收益%": returns.get("half_year_return_pct"),
                "近一年收益%": returns.get("one_year_return_pct"),
                "近3年收益%": returns.get("three_year_return_pct"),
                "收益来源": returns.get("source"),
                "收益口径": returns.get("return_basis"),
                "收益区间": returns.get("return_window"),
                "风险指标区间": returns.get("risk_window"),
                "PE覆盖权重%": pe_cov,
                "股息率覆盖权重%": dy_cov,
                "PB覆盖权重%": pb_cov,
                "负PE权重%": neg_pe,
            },
            holdings,
            None,
        )
    except Exception as exc:
        return None, None, str(exc)


def write_outputs(out_dir: Path, report: pd.DataFrame, candidates: pd.DataFrame, errors: list[dict[str, Any]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    report = report.copy()
    required = [
        "ETF代码",
        "ETF名称",
        "穿透口径",
        "股息率%",
        "股息率覆盖权重%",
        "PE",
        "PE覆盖权重%",
        "PB",
        "PB覆盖权重%",
        "收益币种",
        "数据状态",
        "错误",
    ]
    for column in required:
        if column not in report.columns:
            report[column] = None
    report["ETF代码"] = report["ETF代码"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    report["穿透口径"] = report["穿透口径"].fillna("PCF申赎篮子估算")
    report["收益币种"] = report["收益币种"].fillna("CNY")

    error_map: dict[str, list[str]] = {}
    for item in errors:
        code = re.sub(r"\.0$", "", str(item.get("ETF代码") or "")).zfill(6)
        message = str(item.get("说明") or item.get("错误") or "未产出结果")
        if code.strip("0"):
            error_map.setdefault(code, []).append(message)
    completed = set(report["ETF代码"])
    failed_rows = []
    for _, candidate in candidates.iterrows():
        code = re.sub(r"\.0$", "", str(candidate.get("ETF代码") or "")).zfill(6)
        if code in completed:
            continue
        row = {column: None for column in report.columns}
        row.update(
            {
                "ETF代码": code,
                "ETF名称": str(candidate.get("ETF名称") or ""),
                "穿透口径": "PCF申赎篮子估算",
                "收益币种": "CNY",
                "数据状态": "失败",
                "错误": "; ".join(dict.fromkeys(error_map.get(code, ["未产出结果"]))),
            }
        )
        failed_rows.append(row)
    if failed_rows:
        report = pd.concat([report, pd.DataFrame(failed_rows)], ignore_index=True)

    coverage = pd.to_numeric(report["股息率覆盖权重%"], errors="coerce")
    dividend = pd.to_numeric(report["股息率%"], errors="coerce")
    pe_coverage = pd.to_numeric(report["PE覆盖权重%"], errors="coerce")
    pe = pd.to_numeric(report["PE"], errors="coerce")
    pb_coverage = pd.to_numeric(report["PB覆盖权重%"], errors="coerce")
    pb = pd.to_numeric(report["PB"], errors="coerce")
    errors_blank = report["错误"].fillna("").astype(str).str.strip().eq("")
    rankable = (
        errors_blank
        & coverage.ge(MIN_RANK_COVERAGE)
        & dividend.notna()
        & pe_coverage.ge(MIN_RANK_COVERAGE)
        & pe.notna()
        & pb_coverage.ge(MIN_RANK_COVERAGE)
        & pb.notna()
    )
    report.loc[errors_blank & rankable, "数据状态"] = "有效"
    report.loc[errors_blank & ~rankable, "数据状态"] = "覆盖不足"
    report["_rankable"] = rankable
    report = report.sort_values(["_rankable", "股息率%", "ETF代码"], ascending=[False, False, True], na_position="last").reset_index(drop=True)
    ranks = pd.Series(pd.NA, index=report.index, dtype="Int64")
    ranks.loc[report["_rankable"]] = range(1, int(report["_rankable"].sum()) + 1)
    report.insert(0, "排名_按股息率", ranks)
    report.drop(columns="_rankable", inplace=True)
    with tempfile.TemporaryDirectory(prefix=".pcf-a-stage-", dir=out_dir) as tmpdir:
        stage = Path(tmpdir)
        candidates_path = stage / "keyword_candidates.csv"
        report_csv = stage / "a_share_dividend_etf_pcf_metrics.csv"
        report_xlsx = stage / "a_share_dividend_etf_pcf_metrics.xlsx"
        candidates.to_csv(candidates_path, index=False, encoding="utf-8-sig")
        report.to_csv(report_csv, index=False, encoding="utf-8-sig")
        staged_files = [
            (candidates_path, out_dir / candidates_path.name),
            (report_csv, out_dir / report_csv.name),
        ]
        stale_targets: list[Path] = []
        if errors:
            errors_path = stage / "excluded_or_errors.csv"
            pd.DataFrame(errors).to_csv(errors_path, index=False, encoding="utf-8-sig")
            staged_files.append((errors_path, out_dir / errors_path.name))
        else:
            stale_targets.append(out_dir / "excluded_or_errors.csv")
        with pd.ExcelWriter(report_xlsx, engine="openpyxl") as writer:
            report.to_excel(writer, index=False, sheet_name="A股红利ETF穿透")
            ws = writer.book["A股红利ETF穿透"]
            ws.freeze_panes = "A2"
            for col in range(1, len(report.columns) + 1):
                width = 28 if col == 3 else 14
                ws.column_dimensions[ws.cell(1, col).column_letter].width = width
            for row in ws.iter_rows(min_row=2):
                for cell in row:
                    if isinstance(cell.value, float):
                        cell.number_format = "0.00"
        staged_files.append((report_xlsx, out_dir / report_xlsx.name))
        publish_staged_files(staged_files, stale_targets=stale_targets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="lookthrough-a-share-dividend-keywords-pcf-metrics")
    parser.add_argument("--min-a-weight", type=float, default=80.0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--sleep", type=float, default=0.05)
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    print("Loading ETF candidates...")
    candidates = load_etf_candidates()
    print(f"Keyword candidates: {len(candidates)}")
    snapshot: dict[str, dict[str, Any]] = {}

    preliminary: list[tuple[str, str, pd.DataFrame, str, str]] = []
    errors: list[dict[str, Any]] = []
    all_stock_codes: list[str] = []
    implied_prices: dict[str, float] = {}
    for _, row in candidates.iterrows():
        etf = str(row["ETF代码"]).zfill(6)
        name = str(row["ETF名称"])
        print(f"Reading PCF {etf} {name}...")
        try:
            holdings, period, source = get_pcf_holdings(etf, snapshot)
            a_mask = holdings["市场"].isin(A_MARKETS) & holdings["股票代码"].astype(str).map(is_a_stock_code)
            a_count = int(a_mask.sum())
            if a_count > 0:
                preliminary.append((etf, name, holdings, period, source))
                a_rows = holdings[holdings["市场"].isin(A_MARKETS) & holdings["股票代码"].astype(str).map(is_a_stock_code)].copy()
                all_stock_codes.extend(a_rows["股票代码"].astype(str).map(normalize_stock_code))
                for _, a_row in a_rows.iterrows():
                    stock_code = normalize_stock_code(a_row["股票代码"])
                    price = clean_float(a_row.get("隐含价格"))
                    if price is None or price <= 0:
                        price = clean_float((snapshot.get(stock_code) or {}).get("price"))
                    if stock_code and price is not None and price > 0:
                        implied_prices.setdefault(stock_code, price)
            else:
                errors.append({"ETF代码": etf, "ETF名称": name, "说明": "PCF 中未识别到 A 股股票成分"})
        except Exception as exc:
            errors.append({"ETF代码": etf, "ETF名称": name, "说明": str(exc)})
        time.sleep(args.sleep)

    print(f"A-share underlying ETFs: {len(preliminary)}")
    print(f"Unique A-share stocks for valuation/dividend lookup: {len(set(all_stock_codes))}")
    missing_price_codes = [code for code in sorted(set(all_stock_codes)) if code not in implied_prices]
    if missing_price_codes:
        print(f"Fetching Sina prices for missing PCF prices: {len(missing_price_codes)}")
        implied_prices.update(fetch_sina_prices(missing_price_codes))

    # Fill SZSE rows whose PCF lists stock quantities but no substitute cash amount.
    for _, _, holdings, _, _ in preliminary:
        fill_mask = (
            holdings["市场"].isin(A_MARKETS)
            & holdings["股票代码"].astype(str).map(is_a_stock_code)
            & pd.to_numeric(holdings["权重%"], errors="coerce").isna()
        )
        for idx, h in holdings.loc[fill_mask].iterrows():
            code = normalize_stock_code(h["股票代码"])
            quantity = clean_float(h.get("数量"))
            price = implied_prices.get(code)
            nav_per_cu = clean_float(h.get("NAVperCU"))
            if quantity and price and nav_per_cu:
                amount = quantity * price
                holdings.at[idx, "市值"] = amount
                holdings.at[idx, "隐含价格"] = price
                holdings.at[idx, "权重%"] = amount / nav_per_cu * 100
                holdings.at[idx, "权重来源"] = "ComponentShare*Sina latest price/NAVperCU"

    metrics = enrich_stock_metrics(implied_prices, workers=args.workers)

    results: list[dict[str, Any]] = []
    # Reuse already downloaded holdings by calculating directly.
    for etf, name, holdings, period, source in preliminary:
        print(f"Calculating {etf} {name}...")
        result, _, error = build_metrics_for_etf(
            etf,
            name,
            metrics,
            args.min_a_weight,
            implied_prices=implied_prices,
            holdings=holdings,
            period=period,
            source=source,
        )
        if error:
            errors.append({"ETF代码": etf, "ETF名称": name, "说明": error})
            continue
        if result is None:
            errors.append({"ETF代码": etf, "ETF名称": name, "说明": "A-share ETF 指标计算未产出结果"})
            continue
        results.append(result)
        time.sleep(args.sleep)

    report = pd.DataFrame(results)
    write_outputs(out_dir, report, candidates, errors)
    print(out_dir / "a_share_dividend_etf_pcf_metrics.xlsx")
    print(out_dir / "a_share_dividend_etf_pcf_metrics.csv")
    print(f"rows={len(report)}")
    return 1 if report.empty else 0


if __name__ == "__main__":
    raise SystemExit(main())
