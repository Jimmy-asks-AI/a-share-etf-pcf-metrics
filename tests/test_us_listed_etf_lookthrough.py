from __future__ import annotations

import importlib.util
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

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
        with self.assertRaises(ValueError):
            self.mod.parse_weights("inf,1", 2)

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

    def test_invesco_holdings_resolve_ticker_to_cusip(self) -> None:
        requested = []
        old_request = self.mod.request_invesco_json
        try:
            def fake_request(url):
                requested.append(url)
                if "/product/search" in url:
                    return {
                        "response": {
                            "docs": [
                                {
                                    "ticker": ["QQQ"],
                                    "cusip": ["46090E103"],
                                    "accountName": ["Invesco QQQ Trust"],
                                }
                            ]
                        }
                    }
                return {
                    "effectiveDate": "2026-07-24",
                    "holdings": [
                        {
                            "ticker": "AAPL",
                            "issuerName": "Apple Inc.",
                            "percentageOfTotalNetAssets": "7.5",
                            "marketValueBase": "1000",
                            "units": "4",
                            "currency": "USD",
                        }
                    ],
                }

            self.mod.request_invesco_json = fake_request
            holdings, meta = self.mod.fetch_invesco_holdings("QQQ")
        finally:
            self.mod.request_invesco_json = old_request

        self.assertIn("/46090E103/holdings/fund?idType=cusip", requested[1])
        self.assertEqual(holdings[0].ticker, "AAPL")
        self.assertEqual(meta["etf_name"], "Invesco QQQ Trust")

    def test_international_holding_symbols_are_mapped_for_yahoo(self) -> None:
        self.assertEqual(self.mod.yahoo_symbol_for_holding("005930 KS", "KR"), "005930.KS")
        self.assertEqual(self.mod.yahoo_symbol_for_holding("2408 TT", "TW"), "2408.TW")
        self.assertEqual(self.mod.yahoo_symbol_for_holding("285A JP", "JP"), "285A.T")
        self.assertEqual(self.mod.yahoo_symbol_for_holding("603986 C1", "CN"), "603986.SS")
        self.assertEqual(self.mod.yahoo_symbol_for_holding("BHP AU", "AU"), "BHP.AX")
        self.assertEqual(self.mod.yahoo_symbol_for_holding("SHEL LN", "GB"), "SHEL.L")
        self.assertEqual(self.mod.yahoo_symbol_for_holding("RY CN", "CA"), "RY.TO")
        self.assertEqual(self.mod.yahoo_symbol_for_holding("SAP GY", "DE"), "SAP.DE")
        self.assertEqual(self.mod.yahoo_symbol_for_holding("912797VB0", "BOND"), "")

    def test_enrich_metrics_includes_supported_non_us_equities(self) -> None:
        detail = pd.DataFrame(
            [
                {self.mod.C_MARKET: "KR", self.mod.C_STOCK_CODE: "005930 KS"},
                {self.mod.C_MARKET: "BOND", self.mod.C_STOCK_CODE: "912797VB0"},
            ]
        )
        calls = []
        old_metric = self.mod.metric_for_ticker
        try:
            def fake_metric(symbol, use_nasdaq=True):
                calls.append((symbol, use_nasdaq))
                return {self.mod.C_PE: 10.0, self.mod.C_PB: 1.5, self.mod.C_DY: 2.0}

            self.mod.metric_for_ticker = fake_metric
            enriched = self.mod.enrich_metrics(detail, skip_metrics=False, workers=1)
        finally:
            self.mod.metric_for_ticker = old_metric

        self.assertEqual(calls, [("005930.KS", False)])
        self.assertAlmostEqual(enriched.iloc[0][self.mod.C_PE], 10.0)
        self.assertTrue(pd.isna(enriched.iloc[1][self.mod.C_PE]))

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

    def test_dividend_zero_and_implausible_pb_are_handled_explicitly(self) -> None:
        self.assertEqual(
            self.mod.dividend_yield_from_summary({"trailingAnnualDividendRate": {"raw": 0.0}}),
            0.0,
        )
        self.assertIsNone(self.mod.dividend_yield_from_summary({}))
        self.assertIsNone(self.mod.plausible_pb(0.0009))
        self.assertAlmostEqual(self.mod.plausible_pb(1.5), 1.5)

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

    def test_stale_holdings_cache_is_not_reused(self) -> None:
        with TemporaryDirectory() as tmpdir:
            cache = Path(tmpdir)
            path = cache / "holdings_QQQ_auto.json"
            self.mod.write_json_cache(
                path,
                {
                    "holdings": [self.mod.asdict(self.mod.Holding(ticker="AAPL", name="Apple", weight_pct=10.0, market="US"))],
                    "meta": {"etf_name": "Stale ETF", "source": "fixture"},
                },
            )
            stale = time.time() - (self.mod.HOLDINGS_CACHE_MAX_HOURS + 1) * 3600
            os.utime(path, (stale, stale))
            old_issuer = self.mod.fetch_invesco_holdings
            old_sec = self.mod.fetch_sec_nport_holdings
            try:
                self.mod.fetch_invesco_holdings = lambda ticker: (_ for _ in ()).throw(RuntimeError("fresh source failed"))
                self.mod.fetch_sec_nport_holdings = lambda ticker: (_ for _ in ()).throw(RuntimeError("fresh SEC failed"))
                with self.assertRaises(RuntimeError):
                    self.mod.fetch_holdings("QQQ", "auto", cache_dir=cache)
            finally:
                self.mod.fetch_invesco_holdings = old_issuer
                self.mod.fetch_sec_nport_holdings = old_sec

    def test_legacy_unversioned_caches_are_rejected(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            json_path = root / "metrics_AAPL.json"
            json_path.write_text('{"PE": 10}', encoding="utf-8")
            price_path = root / "prices_QQQ.csv"
            pd.DataFrame(
                {
                    "date": pd.date_range(end=pd.Timestamp.today(), periods=5),
                    "close": [1, 2, 3, 4, 5],
                }
            ).to_csv(price_path, index=False)

            self.assertIsNone(self.mod.read_json_cache(json_path, 24))
            self.assertIsNone(self.mod.read_price_cache(price_path, 2))

    def test_global_yahoo_search_result_is_used_when_no_us_listing_exists(self) -> None:
        old_request_json = self.mod.request_json
        old_openfigi = self.mod.openfigi_ticker
        self.mod.YAHOO_SEARCH_CACHE.clear()
        try:
            self.mod.openfigi_ticker = lambda cusip: ""
            self.mod.request_json = lambda *args, **kwargs: {
                "quotes": [
                    {
                        "symbol": "SHEL.L",
                        "quoteType": "EQUITY",
                        "exchange": "LSE",
                    }
                ]
            }
            ticker = self.mod.resolve_security_ticker(
                "Shell plc",
                "123456789",
                {},
            )
        finally:
            self.mod.request_json = old_request_json
            self.mod.openfigi_ticker = old_openfigi

        self.assertEqual(ticker, "SHEL.L")

    def test_select_sec_nport_filing_matches_series_id(self) -> None:
        recent = {
            "form": ["NPORT-P", "NPORT-P"],
            "accessionNumber": ["0001-26-000001", "0001-26-000002"],
            "primaryDocument": ["first.xml", "matched.xml"],
            "filingDate": ["2026-01-01", "2026-01-02"],
        }
        requested = []
        old_request_text = self.mod.request_text
        try:
            def fake_request(url, **kwargs):
                requested.append(url)
                return "S000OTHER" if "000126000001" in url else "S000MATCH"

            self.mod.request_text = fake_request
            filing = self.mod.select_sec_nport_filing(recent, "1", "S000MATCH")
        finally:
            self.mod.request_text = old_request_text

        self.assertEqual(filing[1], "0001-26-000002")
        self.assertTrue(requested[0].endswith("/first.xml"))
        self.assertTrue(requested[1].endswith("/matched.xml"))

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

    def test_all_failed_holdings_still_write_auditable_outputs(self) -> None:
        old_fetch = self.mod.fetch_holdings
        try:
            self.mod.fetch_holdings = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fixture failure"))
            summary, etf_summary, detail, sources, failed = self.mod.build_tables(["BAD"], [1.0], "auto")
        finally:
            self.mod.fetch_holdings = old_fetch

        self.assertTrue(summary.empty)
        self.assertTrue(detail.empty)
        self.assertEqual(failed[0]["ticker"], "BAD")
        metrics = self.mod.build_metrics(["BAD"], [1.0], detail, etf_summary, 365, skip_metrics=True)
        args = self.mod.build_parser().parse_args(["--ticker", "BAD", "--skip-metrics"])
        manifest = {"sources": sources}
        with TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            stale = out_dir / self.mod.OUT_HTML
            stale.write_text("old", encoding="utf-8")
            self.mod.write_outputs(out_dir, summary, etf_summary, detail, metrics, manifest, args)
            self.assertTrue((out_dir / self.mod.OUT_XLSX).exists())
            self.assertTrue((out_dir / self.mod.OUT_METRICS).exists())
            self.assertFalse(stale.exists())
        self.assertEqual(metrics.loc[metrics[self.mod.C_ETF_CODE].eq("BAD"), "错误"].iloc[0], "fixture failure")

    def test_price_failure_is_recorded_without_aborting_other_metrics(self) -> None:
        detail = pd.DataFrame(
            [{self.mod.C_ETF_CODE: "BAD", self.mod.C_INNER_WEIGHT: 100.0, self.mod.C_PORTFOLIO_WEIGHT: 100.0}]
        )
        etf_summary = pd.DataFrame([{self.mod.C_ETF_CODE: "BAD", "错误": ""}])
        old_price = self.mod.price_series
        try:
            self.mod.price_series = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("price failed"))
            metrics = self.mod.build_metrics(["BAD"], [1.0], detail, etf_summary, 365, skip_metrics=False)
        finally:
            self.mod.price_series = old_price
        self.assertEqual(metrics.loc[metrics[self.mod.C_ETF_CODE].eq("BAD"), "收益错误"].iloc[0], "price failed")

    def test_refresh_cache_works_when_new_cache_writes_are_disabled(self) -> None:
        with TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            cache_dir = out_dir / "cache"
            cache_dir.mkdir()
            (cache_dir / "holdings_BAD.json").write_text("stale", encoding="utf-8")
            (cache_dir / "price_BAD.csv").write_text("stale", encoding="utf-8")

            self.mod.refresh_cache_if_requested(out_dir, requested=True)

            self.assertEqual(list(cache_dir.iterdir()), [])

    def test_main_all_failure_returns_nonzero_with_core_outputs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            with (
                mock.patch.object(
                    self.mod,
                    "fetch_holdings",
                    side_effect=RuntimeError("fixture failure"),
                ),
                mock.patch("builtins.print"),
            ):
                exit_code = self.mod.main(
                    [
                        "--ticker",
                        "BAD",
                        "--skip-metrics",
                        "--out-dir",
                        str(out_dir),
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertTrue((out_dir / self.mod.OUT_XLSX).is_file())
            self.assertTrue((out_dir / self.mod.OUT_MANIFEST).is_file())
            failures = pd.read_csv(out_dir / self.mod.OUT_ETF_SUMMARY, encoding="utf-8-sig")
            self.assertEqual(failures.loc[0, "错误"], "fixture failure")

    def test_staging_failure_preserves_previous_outputs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            old_summary = out_dir / self.mod.OUT_SUMMARY
            old_manifest = out_dir / self.mod.OUT_MANIFEST
            old_summary.write_text("old-summary", encoding="utf-8")
            old_manifest.write_text("old-manifest", encoding="utf-8")
            args = self.mod.build_parser().parse_args(["--ticker", "BAD", "--skip-metrics"])

            def fail_after_partial_stage(stage: Path, *unused) -> None:
                (stage / self.mod.OUT_SUMMARY).write_text("new-summary", encoding="utf-8")
                raise RuntimeError("fixture staging failure")

            with (
                mock.patch.object(
                    self.mod,
                    "_write_outputs_direct",
                    side_effect=fail_after_partial_stage,
                ),
                self.assertRaisesRegex(RuntimeError, "fixture staging failure"),
            ):
                self.mod.write_outputs(
                    out_dir,
                    pd.DataFrame(),
                    pd.DataFrame(),
                    pd.DataFrame(),
                    pd.DataFrame(),
                    {},
                    args,
                )

            self.assertEqual(old_summary.read_text(encoding="utf-8"), "old-summary")
            self.assertEqual(old_manifest.read_text(encoding="utf-8"), "old-manifest")


if __name__ == "__main__":
    unittest.main()
