from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def import_script():
    path = SCRIPTS_DIR / "us_listed_etf_lookthrough.py"
    spec = importlib.util.spec_from_file_location("test_us_listed_etf_lookthrough_script", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class USListedETFTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = import_script()

    def test_parse_tickers_normalizes_suffix_and_dedupes(self) -> None:
        self.assertEqual(self.mod.parse_tickers(" qqq.us,DRAM.US;qqq "), ["QQQ", "DRAM"])
        self.assertEqual(self.mod.normalize_us_ticker("brk/b.us"), "BRK.B")

    def test_parse_weights_accepts_percent_and_decimal(self) -> None:
        self.assertEqual(self.mod.parse_weights(None, 2), [0.5, 0.5])
        self.assertEqual(self.mod.parse_weights("60,40", 2), [0.6, 0.4])
        self.assertEqual(self.mod.parse_weights("0.6 0.4", 2), [0.6, 0.4])
        with self.assertRaises(ValueError):
            self.mod.parse_weights("1,2,3", 2)

    def test_parse_roundhill_csv_fixture(self) -> None:
        csv = """Date,Account,StockTicker,CUSIP,SecurityName,Shares,Price,MarketValue,Weightings,NetAssets,SharesOutstanding,CreationUnits,MoneyMarketFlag
06/29/2026,DRAM,MU,595112103,Micron Technology Inc,10,100,1000,5.00%,10000,1000,1,N
06/29/2026,DRAM,912797UP0,912797UP0,United States Treasury Bill 07/14/2026,1,99,99,1.00%,10000,1000,1,N
"""
        holdings, meta = self.mod.parse_roundhill_holdings_csv(csv, "DRAM", "fixture.csv")
        self.assertEqual(len(holdings), 2)
        self.assertEqual(holdings[0].ticker, "MU")
        self.assertEqual(holdings[1].market, "BOND")
        self.assertEqual(meta["source"], "roundhill_csv")

    def test_rebuild_summary_merges_duplicate_holdings(self) -> None:
        detail = pd.DataFrame(
            [
                {self.mod.C_MARKET: "US", self.mod.C_STOCK_CODE: "AAPL", self.mod.C_STOCK_NAME: "Apple", self.mod.C_PORTFOLIO_WEIGHT: 3.0, self.mod.C_ETF_CODE: "QQQ"},
                {self.mod.C_MARKET: "US", self.mod.C_STOCK_CODE: "AAPL", self.mod.C_STOCK_NAME: "Apple Inc.", self.mod.C_PORTFOLIO_WEIGHT: 2.0, self.mod.C_ETF_CODE: "DRAM"},
            ]
        )
        summary = self.mod.rebuild_summary(detail)
        self.assertEqual(len(summary), 1)
        self.assertAlmostEqual(summary.iloc[0][self.mod.C_PORTFOLIO_WEIGHT], 5.0)
        self.assertEqual(summary.iloc[0]["覆盖ETF数"], 2)

    def test_overlap_pairs_merges_duplicate_codes(self) -> None:
        detail = pd.DataFrame(
            [
                {self.mod.C_ETF_CODE: "A", self.mod.C_STOCK_CODE: "AAPL", self.mod.C_INNER_WEIGHT: 2.0},
                {self.mod.C_ETF_CODE: "A", self.mod.C_STOCK_CODE: "AAPL", self.mod.C_INNER_WEIGHT: 3.0},
                {self.mod.C_ETF_CODE: "B", self.mod.C_STOCK_CODE: "AAPL", self.mod.C_INNER_WEIGHT: 4.0},
                {self.mod.C_ETF_CODE: "B", self.mod.C_STOCK_CODE: "MSFT", self.mod.C_INNER_WEIGHT: 5.0},
            ]
        )
        overlap = self.mod.overlap_pairs(detail)
        self.assertEqual(overlap.iloc[0].iloc[2], 1)
        self.assertAlmostEqual(overlap.iloc[0].iloc[3], 4.0)

    def test_aggregate_valuation_uses_earnings_yield_pe(self) -> None:
        frame = pd.DataFrame(
            [
                {self.mod.C_PORTFOLIO_WEIGHT: 60.0, self.mod.C_PE: 10.0, self.mod.C_PB: 1.0, self.mod.C_DY: 2.0},
                {self.mod.C_PORTFOLIO_WEIGHT: 40.0, self.mod.C_PE: 20.0, self.mod.C_PB: 2.0, self.mod.C_DY: 4.0},
            ]
        )
        metrics = self.mod.aggregate_valuation(frame, self.mod.C_PORTFOLIO_WEIGHT)
        self.assertAlmostEqual(metrics["PE"], 12.5)
        self.assertAlmostEqual(metrics["PB"], 1.4)
        self.assertAlmostEqual(metrics["股息率%"], 2.8)

    def test_skip_metrics_keeps_metric_columns(self) -> None:
        detail = pd.DataFrame([{self.mod.C_MARKET: "US", self.mod.C_STOCK_CODE: "AAPL"}])
        enriched = self.mod.enrich_metrics(detail, skip_metrics=True)
        self.assertIn(self.mod.C_PE, enriched.columns)
        self.assertTrue(pd.isna(enriched.iloc[0][self.mod.C_PE]))

    def test_short_history_does_not_masquerade_as_long_windows(self) -> None:
        dates = pd.date_range("2026-01-01", periods=40, freq="D")
        metrics = self.mod.return_metrics(pd.Series(range(100, 140), index=dates, dtype=float))
        self.assertIsNone(metrics["近半年收益%"])
        self.assertIsNone(metrics["近一年收益%"])
        self.assertIsNone(metrics["近3年收益%"])

    def test_yahoo_raw_number_and_compact_outputs(self) -> None:
        self.assertAlmostEqual(self.mod.raw_number({"raw": 12.34, "fmt": "12.34"}), 12.34)
        compact = self.mod.output_files(False)
        self.assertIn(self.mod.OUT_XLSX, compact)
        self.assertIn(self.mod.OUT_METRICS, compact)
        self.assertNotIn(self.mod.OUT_HTML, compact)
        self.assertIn(self.mod.OUT_HTML, self.mod.output_files(True))

    def test_fetch_holdings_reads_local_cache(self) -> None:
        with TemporaryDirectory() as tmpdir:
            cache = Path(tmpdir)
            payload = {
                "holdings": [self.mod.asdict(self.mod.Holding(ticker="AAPL", name="Apple", weight_pct=10.0, market="US"))],
                "meta": {"etf_name": "Cached ETF", "source": "fixture"},
            }
            self.mod.write_json_cache(cache / "holdings_QQQ_auto.json", payload)
            holdings, meta = self.mod.fetch_holdings("QQQ", "auto", cache_dir=cache)

        self.assertEqual(holdings[0].ticker, "AAPL")
        self.assertEqual(meta["source"], "fixture")

    def test_select_sec_nport_filing_matches_series_id(self) -> None:
        recent = {
            "form": ["NPORT-P", "NPORT-P"],
            "accessionNumber": ["0001-26-000001", "0001-26-000002"],
            "primaryDocument": ["primary_doc.xml", "primary_doc.xml"],
            "filingDate": ["2026-01-01", "2026-01-02"],
        }
        old_request_text = self.mod.request_text
        try:
            self.mod.request_text = lambda url, **kwargs: "S000OTHER" if "000126000001" in url else "S000MATCH"
            filing = self.mod.select_sec_nport_filing(recent, "1", "S000MATCH")
        finally:
            self.mod.request_text = old_request_text

        self.assertEqual(filing[1], "0001-26-000002")

    def test_resolve_security_ticker_uses_cusip_search(self) -> None:
        old_request_json = self.mod.request_json
        old_openfigi = self.mod.openfigi_ticker
        self.mod.YAHOO_SEARCH_CACHE.clear()
        try:
            self.mod.openfigi_ticker = lambda cusip: ""
            self.mod.request_json = lambda *args, **kwargs: {"quotes": [{"symbol": "META.MI", "quoteType": "EQUITY", "exchange": "MIL"}, {"symbol": "META", "quoteType": "EQUITY", "exchange": "NMS"}]}
            ticker = self.mod.resolve_security_ticker("Meta Platforms, Inc., Class A", "30303M102", {})
        finally:
            self.mod.request_json = old_request_json
            self.mod.openfigi_ticker = old_openfigi
        self.assertEqual(ticker, "META")

    def test_resolve_security_ticker_prefers_openfigi_cusip(self) -> None:
        old_openfigi = self.mod.openfigi_ticker
        self.mod.YAHOO_SEARCH_CACHE.clear()
        try:
            self.mod.openfigi_ticker = lambda cusip: "GOOGL"
            ticker = self.mod.resolve_security_ticker("Alphabet Inc., Class A", "02079K305", {})
        finally:
            self.mod.openfigi_ticker = old_openfigi
        self.assertEqual(ticker, "GOOGL")


if __name__ == "__main__":
    unittest.main()
