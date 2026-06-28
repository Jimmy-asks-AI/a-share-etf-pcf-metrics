#!/usr/bin/env python
"""Look through US-listed ETFs by ticker.

This is deliberately separate from selected_etf_lookthrough.py: A-share ETF
PCF codes are numeric; US-listed ETF tickers are not.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

try:
    import akshare as ak
except Exception:  # pragma: no cover - tests can run without akshare network deps
    ak = None  # type: ignore[assignment]

from pcf_common import earnings_yield_pe, to_float, weighted_average


C_ETF_CODE = "ETF代码"
C_ETF_NAME = "ETF名称"
C_ETF_WEIGHT = "ETF权重%"
C_MODE = "穿透模式"
C_MARKET = "底层市场"
C_STOCK_CODE = "股票代码"
C_STOCK_NAME = "股票名称"
C_INNER_WEIGHT = "ETF内权重%"
C_PORTFOLIO_WEIGHT = "组合穿透权重%"
C_QUANTITY = "持仓数量"
C_MARKET_VALUE = "市值"
C_CURRENCY = "币种"
C_SECTOR = "sector"
C_INDUSTRY = "industry"
C_SOURCE = "持仓来源"
C_SOURCE_DETAIL = "来源明细"
C_WEIGHT_SOURCE = "权重来源"
C_PERIOD = "持仓期"
C_DATA_DATE = "数据日期"
C_PRICE = "估值价格"
C_PE = "股票PE"
C_PB = "股票PB"
C_DY = "股票股息率%"
C_VAL_SOURCE = "估值来源"
C_VAL_ERROR = "估值错误"

OUT_DETAIL = "lookthrough_detail.csv"
OUT_SUMMARY = "lookthrough_summary.csv"
OUT_ETF_SUMMARY = "etf_summary.csv"
OUT_METRICS = "metrics_summary.csv"
OUT_XLSX = "lookthrough_report.xlsx"
OUT_MARKDOWN = "enhanced_report.md"
OUT_HTML = "enhanced_report.html"
OUT_RUN_SUMMARY = "run_summary.json"
OUT_MANIFEST = "run_manifest.json"
CORE_OUTPUTS = [OUT_XLSX, OUT_SUMMARY, OUT_DETAIL, OUT_ETF_SUMMARY, OUT_METRICS, OUT_MANIFEST]
EXTRA_OUTPUTS = [
    OUT_MARKDOWN,
    OUT_HTML,
    OUT_RUN_SUMMARY,
    "industry_theme_exposure.csv",
    "market_board_exposure.csv",
    "valuation_buckets.csv",
    "dividend_quality.csv",
    "risk_analysis.csv",
    "structure_analysis.csv",
    "overlap_pairs.csv",
    "common_holdings.csv",
    "data_quality.csv",
    "constraint_checks.csv",
]

SEC_HEADERS = {
    "User-Agent": "Jimmy-asks-AI a-share-etf-pcf-metrics research contact jimmy@example.com",
    "Accept-Encoding": "gzip, deflate",
}
ROUNDHILL_TICKERS = {"DRAM"}


@dataclass(frozen=True)
class Holding:
    ticker: str
    name: str
    weight_pct: float
    market: str
    quantity: float | None = None
    market_value: float | None = None
    currency: str = "USD"
    sector: str = ""
    industry: str = ""
    source: str = ""
    source_detail: str = ""
    weight_source: str = ""
    data_date: str = ""
    price: float | None = None


def normalize_us_ticker(value: Any) -> str:
    text = str(value or "").strip().upper()
    text = re.sub(r"\.US$", "", text)
    text = text.replace("/", ".")
    return re.sub(r"[^A-Z0-9.\-]", "", text)


def parse_tickers(raw: str) -> list[str]:
    tickers: list[str] = []
    seen: set[str] = set()
    for item in re.split(r"[,;\s]+", raw.strip()):
        ticker = normalize_us_ticker(item)
        if ticker and ticker not in seen:
            tickers.append(ticker)
            seen.add(ticker)
    if not tickers:
        raise ValueError("No US ETF tickers were supplied.")
    return tickers


def parse_weights(raw: str | None, count: int) -> list[float]:
    if not raw:
        return [1.0 / count] * count
    values = [float(item) for item in re.split(r"[,;\s]+", raw.strip()) if item.strip()]
    if len(values) != count:
        raise ValueError(f"--weights count ({len(values)}) must match ticker count ({count}).")
    if any(value < 0 for value in values):
        raise ValueError("--weights cannot contain negative values.")
    total = sum(values)
    if total <= 0:
        raise ValueError("--weights total must be positive.")
    if total > 1.5:
        values = [value / 100.0 for value in values]
        total = sum(values)
    return [value / total for value in values]


def clean_number(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "").replace("\u202a", "").replace("\u202c", "").replace("$", "")
    if text.endswith("%"):
        text = text[:-1]
    if text in {"", "-", "--", "nan", "None"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def nested_value(payload: dict[str, Any], *keys: str) -> str:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    if isinstance(current, dict):
        return str(current.get("value") or "")
    return "" if current is None else str(current)


def raw_number(value: Any) -> float | None:
    if isinstance(value, dict) and "raw" in value:
        return clean_number(value.get("raw"))
    return clean_number(value)


def yahoo_quote_summary(ticker: str) -> dict[str, Any]:
    session = requests.Session()
    headers = {"User-Agent": "Mozilla/5.0"}
    session.get("https://fc.yahoo.com", headers=headers, timeout=12)
    crumb = session.get("https://query1.finance.yahoo.com/v1/test/getcrumb", headers=headers, timeout=12).text.strip()
    url = (
        f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
        f"?modules=summaryDetail,defaultKeyStatistics,price&crumb={crumb}"
    )
    payload = session.get(url, headers=headers, timeout=12).json()
    result = payload.get("quoteSummary", {}).get("result") or []
    if not result:
        raise RuntimeError(str(payload.get("quoteSummary", {}).get("error") or "empty Yahoo quoteSummary"))
    data = result[0]
    summary = data.get("summaryDetail") or {}
    stats = data.get("defaultKeyStatistics") or {}
    price = data.get("price") or {}
    return {
        C_PRICE: raw_number(price.get("regularMarketPrice")),
        C_PE: raw_number(summary.get("trailingPE") or stats.get("trailingPE")),
        C_PB: raw_number(stats.get("priceToBook")),
        C_DY: None if raw_number(summary.get("dividendYield")) is None else raw_number(summary.get("dividendYield")) * 100,
    }


def request_text(url: str, **kwargs: Any) -> str:
    headers = kwargs.pop("headers", {})
    merged = {"User-Agent": SEC_HEADERS["User-Agent"], **headers}
    response = requests.get(url, headers=merged, timeout=kwargs.pop("timeout", 25), **kwargs)
    response.raise_for_status()
    return response.text


def request_json(url: str, **kwargs: Any) -> Any:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            headers = kwargs.pop("headers", {})
            response = requests.get(url, headers={**SEC_HEADERS, **headers}, timeout=kwargs.pop("timeout", 25), **kwargs)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.7 * (attempt + 1))
    raise RuntimeError(f"JSON request failed for {url}: {last_error}") from last_error


def cache_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9._-]", "_", value.upper())


def read_json_cache(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json_cache(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def company_name_key(value: str) -> str:
    text = re.sub(r"[^A-Z0-9 ]", " ", value.upper())
    words = [w for w in text.split() if w not in {"INC", "CORP", "CORPORATION", "LTD", "PLC", "CO", "THE"}]
    return " ".join(words)


def sec_ticker_maps() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    data = request_json("https://www.sec.gov/files/company_tickers_exchange.json")
    cik_by_ticker: dict[str, str] = {}
    ticker_by_name: dict[str, str] = {}
    name_by_ticker: dict[str, str] = {}
    for cik, name, ticker, _exchange in data.get("data", []):
        ticker = normalize_us_ticker(ticker)
        cik_by_ticker[ticker] = str(cik).zfill(10)
        ticker_by_name.setdefault(company_name_key(str(name)), ticker)
        name_by_ticker[ticker] = str(name)
    return cik_by_ticker, ticker_by_name, name_by_ticker


def sec_fund_ticker_map() -> dict[str, dict[str, str]]:
    data = request_json("https://www.sec.gov/files/company_tickers_mf.json")
    funds: dict[str, dict[str, str]] = {}
    for cik, series_id, class_id, symbol in data.get("data", []):
        ticker = normalize_us_ticker(symbol)
        if ticker:
            funds[ticker] = {"cik": str(cik).zfill(10), "seriesId": str(series_id), "classId": str(class_id)}
    return funds


def select_sec_nport_filing(recent: dict[str, Any], cik: str, series_id: str | None) -> tuple[str, str, str, str]:
    filings = list(
        zip(
            recent.get("form", []),
            recent.get("accessionNumber", []),
            recent.get("primaryDocument", []),
            recent.get("filingDate", []),
        )
    )
    checked: list[str] = []
    for form, acc, doc, filing_date in filings:
        if "NPORT" not in form:
            continue
        if not series_id:
            return form, acc, doc, filing_date
        acc_no_dash = acc.replace("-", "")
        xml_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_no_dash}/primary_doc.xml"
        try:
            xml_text = request_text(xml_url, headers=SEC_HEADERS)
            checked.append(acc)
            if series_id in xml_text[:15000]:
                return form, acc, doc, filing_date
        except Exception:
            continue
    raise RuntimeError(f"SEC submissions have no matching NPORT filing for series={series_id}; checked={checked[:8]}")


def fetch_sec_nport_holdings(ticker: str) -> tuple[list[Holding], dict[str, Any]]:
    cik_by_ticker, ticker_by_name, name_by_ticker = sec_ticker_maps()
    fund_info = sec_fund_ticker_map().get(ticker)
    cik = (fund_info or {}).get("cik") or cik_by_ticker.get(ticker)
    if not cik:
        raise RuntimeError(f"SEC ticker maps have no CIK for {ticker}")
    submissions = request_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    recent = submissions.get("filings", {}).get("recent", {})
    form, accession, _doc, filing_date = select_sec_nport_filing(recent, cik, (fund_info or {}).get("seriesId"))
    acc_no_dash = accession.replace("-", "")
    xml_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_no_dash}/primary_doc.xml"
    root = ET.fromstring(request_text(xml_url, headers=SEC_HEADERS).encode("utf-8"))

    def find_text(node: ET.Element, name: str) -> str:
        found = node.find(f".//{{*}}{name}")
        return "" if found is None or found.text is None else found.text.strip()

    data_date = find_text(root, "repPdDate")
    fund_name = find_text(root, "seriesName") or name_by_ticker.get(ticker, ticker)
    holdings: list[Holding] = []
    for inv in root.findall(".//{*}invstOrSec"):
        name = find_text(inv, "title") or find_text(inv, "name")
        cusip = find_text(inv, "cusip")
        balance = clean_number(find_text(inv, "balance"))
        value_usd = clean_number(find_text(inv, "valUSD"))
        weight = clean_number(find_text(inv, "pctVal"))
        if weight is None:
            continue
        security_ticker = ticker_by_name.get(company_name_key(name), cusip or name)
        asset = find_text(inv, "assetCat")
        country = find_text(inv, "invCountry")
        market = "US" if asset == "EC" and country == "US" else (country or asset or "OTHER")
        price = value_usd / balance if value_usd and balance else None
        holdings.append(
            Holding(
                ticker=security_ticker,
                name=name,
                weight_pct=weight,
                market=market,
                quantity=balance,
                market_value=value_usd,
                currency=find_text(inv, "curCd") or "USD",
                source="sec_nport",
                source_detail=xml_url,
                weight_source="pctVal",
                data_date=data_date,
                price=price,
            )
        )
    if not holdings:
        raise RuntimeError(f"SEC NPORT parsed no holdings for {ticker}")
    return holdings, {
        "etf_name": fund_name,
        "period": data_date or filing_date,
        "source": "sec_nport",
        "source_detail": xml_url,
        "filing": f"{form} {accession} {filing_date}",
        "known_gap": "SEC NPORT is full holdings but delayed versus current issuer PCF.",
    }


def parse_roundhill_holdings_csv(text: str, ticker: str, source_url: str) -> tuple[list[Holding], dict[str, Any]]:
    from io import StringIO

    df = pd.read_csv(StringIO(text))
    ticker = normalize_us_ticker(ticker)
    account = df[df["Account"].astype(str).str.upper().eq(ticker)].copy()
    if account.empty:
        raise RuntimeError(f"Roundhill CSV has no Account={ticker}")
    holdings: list[Holding] = []
    data_date = str(account["Date"].dropna().iloc[0])
    for _, row in account.iterrows():
        raw_ticker = str(row.get("StockTicker") or "").strip()
        name = str(row.get("SecurityName") or raw_ticker).strip()
        weight = clean_number(row.get("Weightings"))
        if weight is None:
            continue
        currency = "USD"
        upper = raw_ticker.upper()
        if upper in {"CNY", "TWD", "KRW", "USD"} or str(row.get("MoneyMarketFlag") or "").upper() == "Y":
            market = "CASH"
        elif "TREASURY" in name.upper():
            market = "BOND"
        elif "TRS" in upper or "SWAP" in name.upper():
            market = "SWAP"
        elif upper.endswith(" KS"):
            market = "KR"
        elif upper.endswith(" TT"):
            market = "TW"
        else:
            market = "US"
        holdings.append(
            Holding(
                ticker=raw_ticker or name,
                name=name,
                weight_pct=weight,
                market=market,
                quantity=clean_number(row.get("Shares")),
                market_value=clean_number(row.get("MarketValue")),
                currency=currency,
                source="roundhill_csv",
                source_detail=source_url,
                weight_source="Weightings",
                data_date=data_date,
                price=clean_number(row.get("Price")),
            )
        )
    return holdings, {
        "etf_name": "Roundhill Memory ETF" if ticker == "DRAM" else ticker,
        "period": data_date,
        "source": "roundhill_csv",
        "source_detail": source_url,
        "known_gap": "",
    }


def fetch_roundhill_holdings(ticker: str) -> tuple[list[Holding], dict[str, Any]]:
    today = date.today()
    errors: list[str] = []
    # ponytail: try a small +/- window; if Roundhill changes naming, switch to page JS discovery.
    candidates = [today + timedelta(days=offset) for offset in range(2, -31, -1)]
    for day in candidates:
        stamp = day.strftime("%m%d%Y")
        url = f"https://www.roundhillinvestments.com/assets/data/FilepointRoundhill.40RU.RU_Holdings_{stamp}.csv"
        try:
            text = request_text(url)
            return parse_roundhill_holdings_csv(text, ticker, url)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{stamp}:{str(exc)[:80]}")
    raise RuntimeError("Roundhill holdings CSV lookup failed. " + "; ".join(errors[:5]))


def fetch_invesco_holdings(ticker: str) -> tuple[list[Holding], dict[str, Any]]:
    url = f"https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/{ticker}/holdings/fund?idType=ticker&productType=ETF"
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*", "Referer": "https://www.invesco.com/"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("holdings") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("Invesco holdings API returned no holdings list")
    holdings: list[Holding] = []
    for row in rows:
        weight = clean_number(row.get("weight") or row.get("percentageOfTotalNetAssets"))
        if weight is None:
            continue
        holdings.append(
            Holding(
                ticker=normalize_us_ticker(row.get("ticker") or row.get("securityIdentifier")),
                name=str(row.get("issuerName") or row.get("securityName") or row.get("ticker") or ""),
                weight_pct=weight,
                market="US",
                quantity=clean_number(row.get("units")),
                market_value=clean_number(row.get("marketValue") or row.get("marketValueBase")),
                currency=str(row.get("currency") or "USD"),
                sector=str(row.get("sectorName") or ""),
                industry=str(row.get("industryName") or ""),
                source="invesco_api",
                source_detail=url,
                weight_source="weight",
                data_date=str(payload.get("asOfDate") or payload.get("asOf") or ""),
            )
        )
    if not holdings:
        raise RuntimeError("Invesco holdings API parsed no weighted rows")
    return holdings, {"etf_name": f"Invesco {ticker}", "period": holdings[0].data_date, "source": "invesco_api", "source_detail": url, "known_gap": ""}


def fetch_holdings(ticker: str, source: str, cache_dir: Path | None = None) -> tuple[list[Holding], dict[str, Any]]:
    source = source.lower()
    cache_path = None if cache_dir is None else cache_dir / f"holdings_{cache_key(ticker)}_{source}.json"
    if cache_path is not None:
        cached = read_json_cache(cache_path)
        if cached:
            holdings = [Holding(**item) for item in cached["holdings"]]
            meta = dict(cached["meta"])
            meta["cache"] = str(cache_path)
            return holdings, meta
    errors: list[str] = []
    if source in {"auto", "issuer"} and ticker == "QQQ":
        try:
            result = fetch_invesco_holdings(ticker)
            if cache_path is not None:
                write_json_cache(cache_path, {"holdings": [asdict(item) for item in result[0]], "meta": result[1]})
            return result
        except Exception as exc:  # noqa: BLE001
            errors.append(f"issuer:{exc}")
    if source in {"auto", "issuer"} and ticker in ROUNDHILL_TICKERS:
        try:
            result = fetch_roundhill_holdings(ticker)
            if cache_path is not None:
                write_json_cache(cache_path, {"holdings": [asdict(item) for item in result[0]], "meta": result[1]})
            return result
        except Exception as exc:  # noqa: BLE001
            errors.append(f"issuer:{exc}")
    if source in {"auto", "sec"}:
        try:
            result = fetch_sec_nport_holdings(ticker)
            if cache_path is not None:
                write_json_cache(cache_path, {"holdings": [asdict(item) for item in result[0]], "meta": result[1]})
            return result
        except Exception as exc:  # noqa: BLE001
            errors.append(f"sec:{exc}")
    if source == "yfinance":
        raise RuntimeError("yfinance source is not implemented because yfinance is not installed.")
    raise RuntimeError(f"No holdings source succeeded for {ticker}. " + "; ".join(errors))


def latest_us_price(ticker: str) -> float | None:
    if ak is None:
        return None
    df = ak.stock_us_daily(symbol=ticker)
    if df is None or df.empty or "close" not in df.columns:
        return None
    values = pd.to_numeric(df["close"], errors="coerce").dropna()
    return None if values.empty else float(values.iloc[-1])


def valuation_indicator(ticker: str, indicator: str) -> float | None:
    if ak is None:
        return None
    df = ak.stock_us_valuation_baidu(symbol=ticker, indicator=indicator)
    values = df.apply(pd.to_numeric, errors="coerce").stack().dropna()
    return None if values.empty else float(values.iloc[-1])


def metric_for_ticker(ticker: str) -> dict[str, Any]:
    ticker = normalize_us_ticker(ticker)
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker):
        return {C_VAL_ERROR: "not a plain US ticker"}
    out = {C_VAL_SOURCE: "yahoo_quote_summary"}
    errors: list[str] = []
    try:
        out.update(yahoo_quote_summary(ticker))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"yahoo:{str(exc)[:120]}")
    try:
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*", "Referer": f"https://www.nasdaq.com/market-activity/stocks/{ticker.lower()}"}
        summary = requests.get(f"https://api.nasdaq.com/api/quote/{ticker}/summary?assetclass=stocks", headers=headers, timeout=12).json()
        info = requests.get(f"https://api.nasdaq.com/api/quote/{ticker}/info?assetclass=stocks", headers=headers, timeout=12).json()
        summary_data = summary.get("data", {}).get("summaryData", {})
        info_data = info.get("data", {})
        out[C_PRICE] = out.get(C_PRICE) or clean_number(nested_value(info_data, "primaryData", "lastSalePrice"))
        out[C_DY] = out.get(C_DY) or clean_number(nested_value(summary_data, "Yield"))
        out[C_SECTOR] = nested_value(summary_data, "Sector")
        out[C_INDUSTRY] = nested_value(summary_data, "Industry")
        out[C_VAL_SOURCE] = "yahoo_quote_summary+nasdaq_summary"
        if out.get(C_DY) is None:
            errors.append("nasdaq_summary:no dividend yield")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"nasdaq:{str(exc)[:120]}")
    out[C_VAL_ERROR] = "; ".join(errors)
    return out


def enrich_metrics(detail: pd.DataFrame, skip_metrics: bool, workers: int = 8, cache_dir: Path | None = None) -> pd.DataFrame:
    detail = detail.copy()
    for column in [C_PRICE, C_PE, C_PB, C_DY, C_SECTOR, C_INDUSTRY, C_VAL_SOURCE, C_VAL_ERROR]:
        if column not in detail.columns:
            detail[column] = None
    if skip_metrics:
        return detail
    tickers = sorted(
        {
            str(code)
            for code, market in zip(detail[C_STOCK_CODE], detail[C_MARKET])
            if str(market) == "US" and re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", str(code))
        }
    )
    metrics: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {}
        for ticker in tickers:
            cache_path = None if cache_dir is None else cache_dir / f"metrics_{cache_key(ticker)}.json"
            cached = read_json_cache(cache_path) if cache_path is not None else None
            if cached:
                metrics[ticker] = cached
                continue
            futures[executor.submit(metric_for_ticker, ticker)] = (ticker, cache_path)
        for future in concurrent.futures.as_completed(futures):
            ticker, cache_path = futures[future]
            try:
                metrics[ticker] = future.result()
            except Exception as exc:  # noqa: BLE001
                metrics[ticker] = {C_VAL_ERROR: str(exc)[:200]}
            if cache_path is not None:
                write_json_cache(cache_path, metrics[ticker])
    for idx, row in detail.iterrows():
        metric = metrics.get(str(row[C_STOCK_CODE]))
        if not metric:
            continue
        for key, value in metric.items():
            if key == C_PRICE and pd.notna(row.get(C_PRICE)):
                continue
            detail.at[idx, key] = value
    return detail


def build_tables(tickers: list[str], weights: list[float], source: str, cache_dir: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    detail_rows: list[dict[str, Any]] = []
    etf_rows: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for ticker, weight in zip(tickers, weights):
        try:
            holdings, meta = fetch_holdings(ticker, source, cache_dir=cache_dir)
        except Exception as exc:  # noqa: BLE001
            failed.append({"ticker": ticker, "error": str(exc)})
            etf_rows.append({C_ETF_CODE: ticker, C_ETF_WEIGHT: weight * 100, C_MODE: "us_listed", "错误": str(exc)})
            continue
        etf_name = str(meta.get("etf_name") or ticker)
        for holding in holdings:
            detail_rows.append(
                {
                    C_ETF_CODE: ticker,
                    C_ETF_NAME: etf_name,
                    C_ETF_WEIGHT: weight * 100,
                    C_MODE: "us_listed",
                    C_MARKET: holding.market,
                    C_STOCK_CODE: holding.ticker,
                    C_STOCK_NAME: holding.name,
                    C_INNER_WEIGHT: holding.weight_pct,
                    C_PORTFOLIO_WEIGHT: holding.weight_pct * weight,
                    C_QUANTITY: holding.quantity,
                    C_MARKET_VALUE: holding.market_value,
                    C_CURRENCY: holding.currency,
                    C_SECTOR: holding.sector,
                    C_INDUSTRY: holding.industry,
                    C_SOURCE: holding.source,
                    C_SOURCE_DETAIL: holding.source_detail,
                    C_WEIGHT_SOURCE: holding.weight_source,
                    C_PERIOD: meta.get("period"),
                    C_DATA_DATE: holding.data_date,
                    C_PRICE: holding.price,
                }
            )
        etf_rows.append(
            {
                C_ETF_CODE: ticker,
                C_ETF_NAME: etf_name,
                C_ETF_WEIGHT: weight * 100,
                C_MODE: "us_listed",
                C_PERIOD: meta.get("period"),
                C_SOURCE: meta.get("source"),
                C_SOURCE_DETAIL: meta.get("source_detail"),
                "底层持仓数": len(holdings),
                "ETF内权重合计%": sum(item.weight_pct for item in holdings),
                "数据缺口": meta.get("known_gap", ""),
            }
        )
        manifests.append({"ticker": ticker, "input_weight": weight, **meta})
    detail = pd.DataFrame(detail_rows)
    summary = rebuild_summary(detail)
    if detail.empty:
        raise RuntimeError("No ETF holdings could be fetched. " + "; ".join(f"{item['ticker']}: {item['error']}" for item in failed))
    return summary, pd.DataFrame(etf_rows), detail, manifests, failed


def first_valid(series: pd.Series) -> Any:
    valid = series.dropna()
    return None if valid.empty else valid.iloc[0]


def rebuild_summary(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame()
    detail = detail.copy()
    for column in [C_PRICE, C_PE, C_PB, C_DY, C_VAL_SOURCE, C_VAL_ERROR]:
        if column not in detail.columns:
            detail[column] = None
    summary = (
        detail.groupby([C_MARKET, C_STOCK_CODE], as_index=False)
        .agg(
            **{
                C_STOCK_NAME: (C_STOCK_NAME, first_valid),
                C_PORTFOLIO_WEIGHT: (C_PORTFOLIO_WEIGHT, "sum"),
                "覆盖ETF数": (C_ETF_CODE, "nunique"),
                C_PRICE: (C_PRICE, first_valid),
                C_PE: (C_PE, first_valid),
                C_PB: (C_PB, first_valid),
                C_DY: (C_DY, first_valid),
                C_VAL_SOURCE: (C_VAL_SOURCE, first_valid),
                C_VAL_ERROR: (C_VAL_ERROR, first_valid),
            }
        )
        .sort_values(C_PORTFOLIO_WEIGHT, ascending=False)
        .reset_index(drop=True)
    )
    summary.insert(0, "排名", range(1, len(summary) + 1))
    return summary


def valuation_rows(frame: pd.DataFrame, weight_col: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        rows.append(
            {
                "weight_pct": to_float(row.get(weight_col)) or 0.0,
                "pe": to_float(row.get(C_PE)),
                "pb": to_float(row.get(C_PB)),
                "dividend_yield_pct": to_float(row.get(C_DY)),
            }
        )
    return rows


def aggregate_valuation(frame: pd.DataFrame, weight_col: str) -> dict[str, Any]:
    pe, pe_cov, neg_pe = earnings_yield_pe(valuation_rows(frame, weight_col))
    pb, pb_cov = weighted_average(valuation_rows(frame, weight_col), "pb", positive_only=True)
    dy, dy_cov = weighted_average(valuation_rows(frame, weight_col), "dividend_yield_pct")
    return {
        "股票权重合计%": float(pd.to_numeric(frame[weight_col], errors="coerce").fillna(0).sum()) if weight_col in frame else 0.0,
        "股息率%": dy,
        "PE": pe,
        "PB": pb,
        "PE覆盖权重%": pe_cov,
        "股息率覆盖权重%": dy_cov,
        "PB覆盖权重%": pb_cov,
        "负PE权重%": neg_pe,
    }


def price_series(ticker: str, lookback_days: int, cache_dir: Path | None = None) -> tuple[pd.Series, str]:
    cache_path = None if cache_dir is None else cache_dir / f"prices_{cache_key(ticker)}_{lookback_days}.csv"
    if cache_path is not None and cache_path.exists():
        temp = pd.read_csv(cache_path)
        temp["date"] = pd.to_datetime(temp["date"], errors="coerce")
        temp["close"] = pd.to_numeric(temp["close"], errors="coerce")
        temp = temp.dropna().sort_values("date")
        if len(temp) >= 2:
            return pd.Series(temp["close"].to_numpy(float), index=pd.DatetimeIndex(temp["date"])), f"cache:{cache_path.name}"
    start_date = date.today() - timedelta(days=lookback_days)
    end_date = date.today()
    try:
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*", "Referer": f"https://www.nasdaq.com/market-activity/etf/{ticker.lower()}"}
        url = f"https://api.nasdaq.com/api/quote/{ticker}/chart?assetclass=etf&fromdate={start_date:%Y-%m-%d}&todate={end_date:%Y-%m-%d}"
        payload = requests.get(url, headers=headers, timeout=20).json()
        chart = (payload.get("data") or {}).get("chart") or []
        rows = [
            {"date": item.get("z", {}).get("dateTime"), "close": item.get("z", {}).get("close") or item.get("y")}
            for item in chart
        ]
        temp = pd.DataFrame(rows)
        if not temp.empty:
            temp["date"] = pd.to_datetime(temp["date"], errors="coerce")
            temp["close"] = pd.to_numeric(temp["close"], errors="coerce")
            temp = temp.dropna().sort_values("date").drop_duplicates("date", keep="last")
            if len(temp) >= 30:
                if cache_path is not None:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    temp.to_csv(cache_path, index=False, encoding="utf-8-sig")
                return pd.Series(temp["close"].to_numpy(float), index=pd.DatetimeIndex(temp["date"])), "nasdaq_chart"
    except Exception:
        pass
    if ak is None:
        return pd.Series(dtype=float), "akshare_unavailable"
    start = (date.today() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    try:
        df = ak.stock_us_hist(symbol=ticker, period="daily", start_date=start, end_date=end, adjust="qfq")
    except Exception:
        df = ak.stock_us_daily(symbol=ticker)
    if df is None or df.empty:
        return pd.Series(dtype=float), "akshare_us_price_empty"
    date_col = next((c for c in ["日期", "date"] if c in df.columns), df.columns[0])
    close_col = next((c for c in ["收盘", "close"] if c in df.columns), df.columns[-1])
    temp = df[[date_col, close_col]].copy()
    temp.columns = ["date", "close"]
    temp["date"] = pd.to_datetime(temp["date"], errors="coerce")
    temp["close"] = pd.to_numeric(temp["close"], errors="coerce")
    temp = temp.dropna().sort_values("date").drop_duplicates("date", keep="last")
    return pd.Series(temp["close"].to_numpy(float), index=pd.DatetimeIndex(temp["date"])), "akshare_us_price"


def return_metrics(series: pd.Series) -> dict[str, Any]:
    if len(series) < 30:
        return {"收益错误": f"price series too short: {len(series)}"}
    returns = series.pct_change().dropna()
    downside = returns[returns < 0]
    ann_return = (series.iloc[-1] / series.iloc[0]) ** (252 / max(len(returns), 1)) - 1
    volatility = returns.std() * math.sqrt(252)
    downside_vol = downside.std() * math.sqrt(252) if len(downside) > 1 else None
    roll_max = series.cummax()
    max_drawdown = (series / roll_max - 1).min()
    sharpe = None if volatility == 0 else ann_return / volatility
    calmar = None if max_drawdown >= 0 else ann_return / abs(max_drawdown)
    var95 = returns.quantile(0.05)
    cvar95 = returns[returns <= var95].mean() if not returns.empty else None

    def trailing(days: int) -> float | None:
        cutoff = series.index[-1] - pd.Timedelta(days=days)
        sub = series[series.index >= cutoff]
        if len(sub) < 2:
            return None
        return sub.iloc[-1] / sub.iloc[0] - 1

    return {
        "年化收益%": ann_return * 100,
        "索提诺比率": None if not downside_vol or downside_vol == 0 else ann_return / downside_vol,
        "波动率%": volatility * 100,
        "近半年收益%": None if trailing(183) is None else trailing(183) * 100,
        "近一年收益%": None if trailing(365) is None else trailing(365) * 100,
        "近3年收益%": None if trailing(365 * 3) is None else trailing(365 * 3) * 100,
        "最大回撤%": max_drawdown * 100,
        "Sharpe Ratio": sharpe,
        "Calmar Ratio": calmar,
        "VaR95%": var95 * 100,
        "CVaR95%": None if cvar95 is None else cvar95 * 100,
        "下行波动率%": None if downside_vol is None else downside_vol * 100,
        "收益区间": f"{series.index[0].date()}~{series.index[-1].date()}",
    }


def build_metrics(tickers: list[str], weights: list[float], detail: pd.DataFrame, etf_summary: pd.DataFrame, lookback_days: int, skip_metrics: bool, cache_dir: Path | None = None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    series_map: dict[str, pd.Series] = {}
    for ticker, weight in zip(tickers, weights):
        frame = detail[detail[C_ETF_CODE].eq(ticker)]
        row = {"类型": "ETF", C_ETF_CODE: ticker, C_ETF_WEIGHT: weight * 100, **aggregate_valuation(frame, C_INNER_WEIGHT)}
        if not skip_metrics:
            series, source = price_series(ticker, lookback_days, cache_dir=cache_dir)
            series_map[ticker] = series
            row.update(return_metrics(series))
            row["收益来源"] = row.get("收益来源") or source
        rows.append(row)
    portfolio = {"类型": "组合", C_ETF_CODE: "PORTFOLIO", C_ETF_WEIGHT: 100.0, **aggregate_valuation(detail, C_PORTFOLIO_WEIGHT)}
    if not skip_metrics and series_map:
        returns = []
        for ticker, weight in zip(tickers, weights):
            series = series_map.get(ticker)
            if series is None or len(series) < 2:
                continue
            returns.append(series.pct_change().dropna().rename(ticker) * weight)
        if returns:
            combo_returns = pd.concat(returns, axis=1).dropna().sum(axis=1)
            combo = (1 + combo_returns).cumprod()
            portfolio.update(return_metrics(combo))
            portfolio["收益来源"] = "portfolio_price_combination"
    rows.append(portfolio)
    return pd.DataFrame(rows)


def exposure_table(detail: pd.DataFrame, group_col: str, name: str) -> pd.DataFrame:
    if detail.empty or group_col not in detail:
        return pd.DataFrame(columns=[name, C_PORTFOLIO_WEIGHT])
    out = detail.groupby(group_col, as_index=False)[C_PORTFOLIO_WEIGHT].sum()
    return out.rename(columns={group_col: name}).sort_values(C_PORTFOLIO_WEIGHT, ascending=False)


def valuation_buckets(summary: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for label, cond in [
        ("PE缺失", summary[C_PE].isna() if C_PE in summary else pd.Series(dtype=bool)),
        ("PE<=15", pd.to_numeric(summary.get(C_PE), errors="coerce").le(15)),
        ("15<PE<=30", pd.to_numeric(summary.get(C_PE), errors="coerce").gt(15) & pd.to_numeric(summary.get(C_PE), errors="coerce").le(30)),
        ("PE>30", pd.to_numeric(summary.get(C_PE), errors="coerce").gt(30)),
    ]:
        rows.append({"估值分层": label, C_PORTFOLIO_WEIGHT: float(pd.to_numeric(summary.loc[cond, C_PORTFOLIO_WEIGHT], errors="coerce").sum()) if len(summary) else 0.0})
    return pd.DataFrame(rows)


def overlap_pairs(detail: pd.DataFrame) -> pd.DataFrame:
    rows = []
    etfs = sorted(detail[C_ETF_CODE].dropna().unique())
    for i, left in enumerate(etfs):
        for right in etfs[i + 1 :]:
            a = detail[detail[C_ETF_CODE].eq(left)].groupby(C_STOCK_CODE)[C_INNER_WEIGHT].sum()
            b = detail[detail[C_ETF_CODE].eq(right)].groupby(C_STOCK_CODE)[C_INNER_WEIGHT].sum()
            common = a.index.intersection(b.index)
            rows.append({"ETF1": left, "ETF2": right, "共同持仓数": len(common), "重合权重%": float(pd.concat([a[common], b[common]], axis=1).min(axis=1).sum()) if len(common) else 0.0})
    return pd.DataFrame(rows)


def constraint_checks(summary: pd.DataFrame, metrics: pd.DataFrame, args: argparse.Namespace, detail: pd.DataFrame | None = None) -> pd.DataFrame:
    rows = []
    if args.max_stock_weight is not None and not summary.empty:
        actual = float(pd.to_numeric(summary[C_PORTFOLIO_WEIGHT], errors="coerce").max())
        rows.append({"约束": "单一股票最大权重", "阈值": args.max_stock_weight, "实际值": actual, "结果": "通过" if actual <= args.max_stock_weight else "不通过"})
    portfolio = metrics[metrics[C_ETF_CODE].eq("PORTFOLIO")]
    if args.min_dividend_yield is not None and not portfolio.empty:
        actual = to_float(portfolio.iloc[0].get("股息率%"))
        rows.append({"约束": "组合最低股息率", "阈值": args.min_dividend_yield, "实际值": actual, "结果": "未知" if actual is None else ("通过" if actual >= args.min_dividend_yield else "不通过")})
    for attr, metric_name, label in [("max_pe", "PE", "组合最高PE"), ("max_pb", "PB", "组合最高PB")]:
        threshold = getattr(args, attr, None)
        if threshold is not None and not portfolio.empty:
            actual = to_float(portfolio.iloc[0].get(metric_name))
            rows.append({"约束": label, "阈值": threshold, "实际值": actual, "结果": "未知" if actual is None else ("通过" if actual <= threshold else "不通过")})
    if args.max_sector_weight is not None and detail is not None and C_PORTFOLIO_WEIGHT in detail:
        group_col = C_SECTOR if C_SECTOR in detail and detail[C_SECTOR].fillna("").astype(str).ne("").any() else C_INDUSTRY
        exposure = detail.groupby(group_col)[C_PORTFOLIO_WEIGHT].sum() if group_col in detail else pd.Series(dtype=float)
        actual = None if exposure.empty else float(exposure.max())
        rows.append({"约束": "单一sector最大权重", "阈值": args.max_sector_weight, "实际值": actual, "结果": "未知" if actual is None else ("通过" if actual <= args.max_sector_weight else "不通过")})
    return pd.DataFrame(rows)


def structure_analysis(summary: pd.DataFrame, detail: pd.DataFrame) -> pd.DataFrame:
    weights = pd.to_numeric(summary.get(C_PORTFOLIO_WEIGHT), errors="coerce").fillna(0).sort_values(ascending=False)
    rows = [
        {"指标": "底层持仓数量", "数值": int(len(summary)), "说明": "按底层市场+ticker合并后"},
        {"指标": "组合权重合计%", "数值": float(weights.sum()), "说明": ""},
        {"指标": "单一股票最大权重%", "数值": None if weights.empty else float(weights.iloc[0]), "说明": ""},
        {"指标": "Top5集中度%", "数值": float(weights.head(5).sum()), "说明": ""},
        {"指标": "Top10集中度%", "数值": float(weights.head(10).sum()), "说明": ""},
        {"指标": "Top20集中度%", "数值": float(weights.head(20).sum()), "说明": ""},
    ]
    if not detail.empty and C_MARKET in detail:
        market = detail.groupby(C_MARKET)[C_PORTFOLIO_WEIGHT].sum()
        for key in ["CASH", "BOND", "SWAP", "FUTURE", "OTHER"]:
            rows.append({"指标": f"{key}权重%", "数值": float(market.get(key, 0.0)), "说明": "按底层市场分类"})
    return pd.DataFrame(rows)


def write_report_md(path: Path, summary: pd.DataFrame, etf_summary: pd.DataFrame, metrics: pd.DataFrame, manifest: dict[str, Any]) -> None:
    lines = ["# US-listed ETF look-through report", ""]
    lines.append("## ETF summary")
    lines.append(etf_summary.head(20).to_markdown(index=False))
    lines.append("")
    lines.append("## Metrics")
    lines.append(metrics.to_markdown(index=False))
    lines.append("")
    lines.append("## Top holdings")
    lines.append(summary.head(30).to_markdown(index=False))
    lines.append("")
    lines.append("## Sources and gaps")
    for item in manifest.get("sources", []):
        lines.append(f"- {item.get('ticker')}: {item.get('source')} {item.get('period')} {item.get('known_gap', '')}")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_report_html(path: Path, summary: pd.DataFrame, etf_summary: pd.DataFrame, metrics: pd.DataFrame) -> None:
    html = "\n".join(
        [
            "<!doctype html><meta charset='utf-8'><title>US ETF Look-through</title>",
            "<style>body{font-family:Arial,sans-serif;margin:32px}table{border-collapse:collapse;margin:16px 0}td,th{border:1px solid #ddd;padding:6px 8px}th{background:#f4f4f4}</style>",
            "<h1>US-listed ETF look-through report</h1>",
            "<h2>ETF summary</h2>",
            etf_summary.to_html(index=False),
            "<h2>Metrics</h2>",
            metrics.to_html(index=False),
            "<h2>Top holdings</h2>",
            summary.head(50).to_html(index=False),
        ]
    )
    path.write_text(html, encoding="utf-8")


def output_files(full_output: bool) -> list[str]:
    return CORE_OUTPUTS + (EXTRA_OUTPUTS if full_output else [])


def write_outputs(out_dir: Path, summary: pd.DataFrame, etf_summary: pd.DataFrame, detail: pd.DataFrame, metrics: pd.DataFrame, manifest: dict[str, Any], args: argparse.Namespace) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    industry = exposure_table(detail, C_INDUSTRY, "行业")
    market = exposure_table(detail, C_MARKET, "市场")
    dividends = summary[[C_STOCK_CODE, C_STOCK_NAME, C_PORTFOLIO_WEIGHT, C_DY]].copy() if C_DY in summary else pd.DataFrame()
    risk_cols = [
        C_ETF_CODE,
        "年化收益%",
        "索提诺比率",
        "波动率%",
        "最大回撤%",
        "Sharpe Ratio",
        "Calmar Ratio",
        "VaR95%",
        "CVaR95%",
        "下行波动率%",
    ]
    risk = metrics[[col for col in risk_cols if col in metrics.columns]].copy() if "年化收益%" in metrics else pd.DataFrame()
    overlaps = overlap_pairs(detail)
    common = summary[summary.get("覆盖ETF数", 0).gt(1)] if "覆盖ETF数" in summary else pd.DataFrame()
    data_quality = pd.DataFrame(
        [
            {"项目": "持仓行数", "值": len(detail)},
            {"项目": "估值错误行数", "值": int(detail[C_VAL_ERROR].fillna("").astype(str).ne("").sum()) if C_VAL_ERROR in detail else 0},
            {"项目": "数据源", "值": "; ".join(sorted(set(str(x) for x in detail[C_SOURCE].dropna())) if not detail.empty else [])},
        ]
    )
    structure = structure_analysis(summary, detail)
    constraints = constraint_checks(summary, metrics, args, detail=detail)

    core_outputs = {
        OUT_SUMMARY: summary,
        OUT_DETAIL: detail,
        OUT_ETF_SUMMARY: etf_summary,
        OUT_METRICS: metrics,
    }
    extra_outputs = {
        "industry_theme_exposure.csv": industry,
        "market_board_exposure.csv": market,
        "valuation_buckets.csv": valuation_buckets(summary),
        "dividend_quality.csv": dividends,
        "risk_analysis.csv": risk,
        "structure_analysis.csv": structure,
        "overlap_pairs.csv": overlaps,
        "common_holdings.csv": common,
        "data_quality.csv": data_quality,
        "constraint_checks.csv": constraints,
    }
    outputs = {**core_outputs, **extra_outputs} if args.full_output else core_outputs
    for filename, frame in outputs.items():
        frame.to_csv(out_dir / filename, index=False, encoding="utf-8-sig")

    with pd.ExcelWriter(out_dir / OUT_XLSX, engine="openpyxl") as writer:
        for sheet, frame in [
            ("穿透汇总", summary),
            ("穿透明细", detail),
            ("ETF汇总", etf_summary),
            ("指标汇总", metrics),
            ("行业主题", industry),
            ("市场板块", market),
            ("估值分层", valuation_buckets(summary)),
            ("分红质量", dividends),
            ("风险指标", risk),
            ("结构分析", structure),
            ("重合度", overlaps),
            ("共同持仓", common),
            ("数据质量", data_quality),
            ("约束检查", constraints),
        ]:
            frame.to_excel(writer, index=False, sheet_name=sheet[:31])
            writer.book[sheet[:31]].freeze_panes = "A2"

    (out_dir / OUT_MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.full_output:
        (out_dir / OUT_RUN_SUMMARY).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        write_report_md(out_dir / OUT_MARKDOWN, summary, etf_summary, metrics, manifest)
        write_report_html(out_dir / OUT_HTML, summary, etf_summary, metrics)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", required=True, help="US ETF tickers, e.g. QQQ.US,DRAM.US")
    parser.add_argument("--weights", help="Optional decimal or percent weights. Defaults to equal weight.")
    parser.add_argument("--out-dir", default="us-etf-output")
    parser.add_argument("--skip-metrics", action="store_true")
    parser.add_argument("--full-output", action="store_true", help="Write auxiliary CSV, Markdown, HTML, and run_summary files. Default writes only core files.")
    parser.add_argument("--holdings-source", choices=["auto", "issuer", "sec", "yfinance"], default="auto")
    parser.add_argument("--refresh-cache", action="store_true", help="Remove local holdings, metrics, and price cache files before running.")
    cache_group = parser.add_mutually_exclusive_group()
    cache_group.add_argument("--cache", dest="use_cache", action="store_true", default=False, help="Write holdings, metrics, and price cache files.")
    cache_group.add_argument("--no-cache", dest="use_cache", action="store_false", help="Do not write cache files. Default.")
    parser.add_argument("--recursive-etf-lookthrough", action="store_true", help="Accepted but not expanded yet.")
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--max-stock-weight", type=float)
    parser.add_argument("--max-sector-weight", type=float)
    parser.add_argument("--min-dividend-yield", type=float)
    parser.add_argument("--max-pe", type=float)
    parser.add_argument("--max-pb", type=float)
    parser.add_argument("--nav-lookback-days", type=int, default=365 * 3 + 120)
    parser.add_argument("--lookback-days", dest="nav_lookback_days", type=int)
    parser.add_argument("--metrics-workers", type=int, default=8)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tickers = parse_tickers(args.ticker)
    weights = parse_weights(args.weights, len(tickers))
    out_dir = Path(args.out_dir)
    cache_dir = out_dir / "cache" if args.use_cache else None
    if args.refresh_cache and cache_dir is not None and cache_dir.exists():
        for path in cache_dir.glob("*"):
            if path.is_file():
                path.unlink()
    summary, etf_summary, detail, sources, failed = build_tables(tickers, weights, args.holdings_source, cache_dir=cache_dir)
    detail = enrich_metrics(detail, args.skip_metrics, workers=args.metrics_workers, cache_dir=cache_dir)
    summary = rebuild_summary(detail)
    metrics = build_metrics(tickers, weights, detail, etf_summary, args.nav_lookback_days, args.skip_metrics, cache_dir=cache_dir)
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {"ticker": args.ticker, "tickers": tickers, "weights": weights, "holdings_source": args.holdings_source},
        "sources": sources,
        "failed_tickers": failed,
        "known_gaps": [
            "SEC NPORT holdings are full but delayed.",
            "Recursive ETF-of-ETF expansion is accepted by CLI but not enabled in v1.",
        ],
        "row_counts": {"summary": len(summary), "detail": len(detail), "etf_summary": len(etf_summary), "metrics_summary": len(metrics)},
        "columns": {
            "summary": list(summary.columns),
            "detail": list(detail.columns),
            "etf_summary": list(etf_summary.columns),
            "metrics_summary": list(metrics.columns),
        },
        "cache_dir": None if cache_dir is None else str(cache_dir),
        "outputs": output_files(args.full_output),
    }
    write_outputs(out_dir, summary, etf_summary, detail, metrics, manifest, args)
    print(f"Saved: {out_dir / OUT_XLSX}")
    print(summary.head(20).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
