from __future__ import annotations

import importlib.util
import io
import sys
import types
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def import_script(name: str, relative_path: str):
    if "akshare" not in sys.modules:
        sys.modules["akshare"] = types.SimpleNamespace()
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class CoreBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.a_metrics = import_script("test_a_metrics", "scripts/run_a_share_dividend_etf_pcf_metrics.py")
        cls.hk_metrics = import_script("test_hk_metrics", "scripts/pcf_lookthrough.py")
        cls.us_metrics = import_script("test_us_metrics", "scripts/us_etf_lookthrough.py")
        cls.selected = import_script("test_selected", "scripts/selected_etf_lookthrough.py")
        cls.enhanced = import_script("test_enhanced", "scripts/pcf_enhanced_analytics.py")
        cls.runner = import_script("test_runner", "scripts/run_pcf_metrics.py")

    def test_load_a_stock_snapshot_does_not_recurse(self) -> None:
        frame = pd.DataFrame(
            [
                {"code": "600000", "name": "浦发银行", "price": "10.5"},
                {"code": "000001", "name": "平安银行", "price": "11.2"},
            ]
        )
        self.a_metrics.ak.stock_zh_a_spot = lambda: frame

        with redirect_stdout(io.StringIO()):
            snapshot = self.a_metrics.load_a_stock_snapshot()

        self.assertEqual(snapshot["600000"]["name"], "浦发银行")
        self.assertEqual(snapshot["600000"]["price"], 10.5)
        self.assertEqual(snapshot["000001"]["price"], 11.2)

    def test_clean_float_rejects_inf_and_nan(self) -> None:
        import math
        self.assertIsNone(self.a_metrics.clean_float(math.inf))
        self.assertIsNone(self.a_metrics.clean_float(-math.inf))
        self.assertIsNone(self.a_metrics.clean_float(math.nan))
        self.assertIsNone(self.a_metrics.clean_float("inf"))
        self.assertIsNone(self.a_metrics.clean_float("-inf"))
        self.assertIsNone(self.a_metrics.clean_float("Infinity"))
        self.assertAlmostEqual(self.a_metrics.clean_float("10.5%"), 10.5)

    def test_is_a_stock_code_rejects_short_padded_codes(self) -> None:
        self.assertFalse(self.a_metrics.is_a_stock_code("5"))
        self.assertFalse(self.a_metrics.is_a_stock_code("6000"))
        self.assertFalse(self.a_metrics.is_a_stock_code("abc"))
        self.assertFalse(self.a_metrics.is_a_stock_code("60000a"))
        self.assertTrue(self.a_metrics.is_a_stock_code("600000"))
        self.assertTrue(self.a_metrics.is_a_stock_code("000005"))
        self.assertTrue(self.a_metrics.is_a_stock_code("301000"))
        self.assertFalse(self.a_metrics.is_a_stock_code("159919"))

    def test_clean_percent_accepts_percent_and_whole_number_percent(self) -> None:
        self.assertAlmostEqual(self.hk_metrics.clean_percent("10%"), 0.10)
        self.assertAlmostEqual(self.hk_metrics.clean_percent("10"), 0.10)
        self.assertAlmostEqual(self.hk_metrics.clean_percent("0.1"), 0.10)
        self.assertAlmostEqual(self.hk_metrics.clean_percent("-5"), -0.05)

    def test_earnings_yield_pe_aggregation(self) -> None:
        rows = [
            {"weight_pct": 60.0, "pe": 10.0},
            {"weight_pct": 40.0, "pe": 20.0},
            {"weight_pct": 5.0, "pe": -3.0},
        ]

        pe, coverage, negative = self.a_metrics.earnings_yield_pe(rows)

        self.assertAlmostEqual(pe, 12.5)
        self.assertAlmostEqual(coverage, 100.0)
        self.assertAlmostEqual(negative, 5.0)

    def test_selected_portfolio_valuation_uses_underlying_weights(self) -> None:
        rows = [
            {"weight_pct": 30.0, "pe": 10.0, "pb": 1.0, "dividend_yield_pct": 5.0},
            {"weight_pct": 70.0, "pe": 20.0, "pb": 2.0, "dividend_yield_pct": 7.0},
        ]

        metrics = self.selected.aggregate_valuation_rows(rows)

        self.assertAlmostEqual(metrics["PE"], 100.0 / (30.0 / 10.0 + 70.0 / 20.0))
        self.assertAlmostEqual(metrics["PB"], 1.7)
        self.assertAlmostEqual(metrics["股息率%"], 6.4)
        self.assertAlmostEqual(metrics["PE覆盖权重%"], 100.0)

    def test_enhanced_structure_groups_duplicate_stock_before_concentration(self) -> None:
        cols = {
            "etf": "\u0045\u0054\u0046\u4ee3\u7801",
            "code": "\u80a1\u7968\u4ee3\u7801",
            "name": "\u80a1\u7968\u540d\u79f0",
            "market": "\u5e95\u5c42\u5e02\u573a",
            "inner_weight": "\u0045\u0054\u0046\u5185\u6743\u91cd%",
            "portfolio_weight": "\u7ec4\u5408\u7a7f\u900f\u6743\u91cd%",
        }
        detail = pd.DataFrame(
            [
                {cols["etf"]: "159001", cols["code"]: "000001", cols["name"]: "Ping An Bank", cols["market"]: "A", cols["inner_weight"]: 6.0, cols["portfolio_weight"]: 3.6},
                {cols["etf"]: "159002", cols["code"]: "000001", cols["name"]: "Ping An Bank", cols["market"]: "A", cols["inner_weight"]: 1.0, cols["portfolio_weight"]: 0.4},
                {cols["etf"]: "159002", cols["code"]: "000002", cols["name"]: "Vanke A", cols["market"]: "A", cols["inner_weight"]: 7.5, cols["portfolio_weight"]: 3.0},
            ]
        )

        structure, _ = self.enhanced.build_structure_analysis(detail)
        max_stock = structure.loc[structure["\u6307\u6807"].eq("\u5355\u4e00\u80a1\u7968\u6700\u5927\u6743\u91cd"), "\u6570\u503c"].iloc[0]
        stock_count = structure.loc[structure["\u6307\u6807"].eq("\u5e95\u5c42\u6301\u4ed3\u6570\u91cf"), "\u6570\u503c"].iloc[0]

        self.assertAlmostEqual(max_stock, 4.0)
        self.assertEqual(stock_count, 2)

    def test_latest_dividend_yield_handles_datetime64_ex_date(self) -> None:
        self.a_metrics.ak.stock_history_dividend_detail = lambda symbol, indicator: pd.DataFrame(
            [
                {
                    "\u6d3e\u606f": 2.0,
                    "\u8fdb\u5ea6": "\u5b9e\u65bd",
                    "\u9664\u6743\u9664\u606f\u65e5": pd.Timestamp("2026-01-15"),
                }
            ]
        )

        yield_pct, source = self.a_metrics.latest_dividend_yield("688981", 20.0, pd.Timestamp("2026-06-15").date())

        self.assertAlmostEqual(yield_pct, 1.0)
        self.assertIn("trailing_12m_ex_date", source)

    def test_run_pcf_metrics_default_missing_input_has_actionable_error(self) -> None:
        args = Namespace(etf=None, input=self.runner.DEFAULT_INPUT, code_column="ETF代码")

        with TemporaryDirectory() as tmpdir:
            old_cwd = Path.cwd()
            try:
                import os

                os.chdir(tmpdir)
                with self.assertRaises(SystemExit) as ctx:
                    self.runner.load_codes(args)
            finally:
                os.chdir(old_cwd)

        self.assertIn("--etf", str(ctx.exception))
        self.assertIn(self.runner.DEFAULT_INPUT, str(ctx.exception))

    def test_clean_intermediates_keeps_only_final_outputs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            keep_csv = out_dir / self.runner.FINAL_CSV
            keep_xlsx = out_dir / self.runner.FINAL_XLSX
            remove_json = out_dir / "513690_lookthrough.json"
            keep_csv.write_text("csv", encoding="utf-8")
            keep_xlsx.write_text("xlsx", encoding="utf-8")
            remove_json.write_text("json", encoding="utf-8")

            self.runner.clean_intermediates(out_dir)

            self.assertTrue(keep_csv.exists())
            self.assertTrue(keep_xlsx.exists())
            self.assertFalse(remove_json.exists())

    def test_us_ticker_normalization_preserves_ticker_shape(self) -> None:
        self.assertEqual(self.us_metrics.normalize_us_ticker(" brk.b "), "BRK.B")
        self.assertEqual(self.us_metrics.normalize_us_ticker("BRK-B"), "BRK-B")
        self.assertEqual(self.us_metrics.normalize_us_ticker("googl"), "GOOGL")
        self.assertFalse(self.us_metrics.is_us_ticker("600000"))

    def test_selected_parse_modes_accepts_us(self) -> None:
        self.assertEqual(self.selected.parse_modes("us", 2), ["us", "us"])
        self.assertEqual(self.selected.parse_modes("auto,a,hk,us,us_listed", 5), ["auto", "a", "hk", "us", "us_listed"])
        self.assertEqual(self.selected.selected_etfs("510880,QQQ.US", "60,40", None)[1].mode, "us_listed")

    def test_selected_can_adapt_us_listed_holdings(self) -> None:
        fake = types.SimpleNamespace(
            C_ETF_CODE=self.selected.C_ETF_CODE,
            C_ETF_NAME=self.selected.C_ETF_NAME,
            C_ETF_WEIGHT=self.selected.C_ETF_WEIGHT,
            C_MODE=self.selected.C_MODE,
            C_MARKET=self.selected.C_UNDERLYING_MARKET,
            C_STOCK_CODE=self.selected.C_STOCK_CODE,
            C_STOCK_NAME=self.selected.C_STOCK_NAME,
            C_INNER_WEIGHT=self.selected.C_ETF_INNER_WEIGHT,
            C_PRICE=self.selected.C_STOCK_PRICE,
            C_WEIGHT_SOURCE=self.selected.C_WEIGHT_SOURCE,
            C_SOURCE_DETAIL=self.selected.C_DETAIL_SOURCE,
            C_VAL_ERROR=self.selected.C_VALUATION_ERROR,
            C_PERIOD=self.selected.C_PERIOD,
            C_SOURCE=self.selected.C_SOURCE,
            normalize_us_ticker=lambda value: str(value).upper().replace(".US", ""),
        )
        detail = pd.DataFrame(
            [
                {
                    fake.C_STOCK_CODE: "AAPL",
                    fake.C_STOCK_NAME: "Apple",
                    fake.C_MARKET: "US",
                    fake.C_INNER_WEIGHT: 10.0,
                    fake.C_PRICE: 200.0,
                    fake.C_WEIGHT_SOURCE: "fixture",
                    fake.C_SOURCE_DETAIL: "fixture.csv",
                    fake.C_VAL_ERROR: "",
                }
            ]
        )
        etf_summary = pd.DataFrame([{fake.C_ETF_NAME: "Invesco QQQ", fake.C_PERIOD: "NPORT", fake.C_SOURCE: "sec_nport"}])
        fake.build_tables = lambda tickers, weights, source, cache_dir=None: (pd.DataFrame(), etf_summary, detail, [], [])

        holdings, meta = self.selected.get_us_listed_stock_holdings(fake, "QQQ.US")

        self.assertEqual(holdings.iloc[0][self.selected.C_STOCK_CODE], "AAPL")
        self.assertEqual(meta[self.selected.C_MODE], "us_listed")
        self.assertEqual(meta[self.selected.C_ETF_NAME], "Invesco QQQ")
        self.assertAlmostEqual(meta["\u0045\u0054\u0046\u5185\u80a1\u7968\u6743\u91cd\u5408\u8ba1%"], 10.0)

    def test_selected_summary_groups_same_market_code_with_different_names(self) -> None:
        detail = pd.DataFrame(
            [
                {
                    self.selected.C_UNDERLYING_MARKET: "US",
                    self.selected.C_STOCK_CODE: "NVDA",
                    self.selected.C_STOCK_NAME: "NVDA",
                    self.selected.C_PORTFOLIO_WEIGHT: 1.0,
                    self.selected.C_ETF_CODE: "513100",
                },
                {
                    self.selected.C_UNDERLYING_MARKET: "US",
                    self.selected.C_STOCK_CODE: "NVDA",
                    self.selected.C_STOCK_NAME: "NVIDIA Corp.",
                    self.selected.C_PORTFOLIO_WEIGHT: 2.0,
                    self.selected.C_ETF_CODE: "QQQ.US",
                },
            ]
        )

        summary = self.selected.rebuild_summary_from_detail(detail)

        self.assertEqual(len(summary), 1)
        self.assertAlmostEqual(summary.iloc[0][self.selected.C_PORTFOLIO_WEIGHT], 3.0)
        self.assertEqual(summary.iloc[0]["\u8986\u76d6\u0045\u0054\u0046\u6570"], 2)

    def test_portfolio_return_metrics_uses_us_listed_price_series(self) -> None:
        dates = pd.date_range("2025-01-01", periods=40, freq="D")
        us_listed = types.SimpleNamespace(
            normalize_us_ticker=lambda value: str(value).upper().replace(".US", ""),
            price_series=lambda ticker, lookback_days, cache_dir=None: (pd.Series(range(100, 140), index=dates, dtype=float), f"fixture_{ticker}"),
        )

        metrics = self.selected.portfolio_return_metrics(
            [self.selected.SelectedETF("QQQ.US", 1.0, "us_listed")],
            ak_module=None,
            us_listed_module=us_listed,
            lookback_days=90,
        )

        self.assertIn("fixture_QQQ", metrics[self.selected.C_RETURN_SOURCE])
        self.assertNotIn(self.selected.C_RETURN_ERROR, metrics)

    def test_us_metrics_prefer_us_listed_helper_when_available(self) -> None:
        detail = pd.DataFrame(
            [
                {
                    self.selected.C_UNDERLYING_MARKET: "US",
                    self.selected.C_STOCK_CODE: "AAPL",
                    self.selected.C_STOCK_NAME: "Apple",
                    self.selected.C_PORTFOLIO_WEIGHT: 10.0,
                    self.selected.C_ETF_INNER_WEIGHT: 10.0,
                }
            ]
        )
        us_listed = types.SimpleNamespace(
            C_STOCK_CODE=self.selected.C_STOCK_CODE,
            C_PRICE=self.selected.C_STOCK_PRICE,
            C_PE=self.selected.C_STOCK_PE,
            C_PB=self.selected.C_STOCK_PB,
            C_DY=self.selected.C_STOCK_DY,
            C_VAL_SOURCE=self.selected.C_VALUATION_SOURCE,
            C_VAL_ERROR=self.selected.C_VALUATION_ERROR,
            C_MARKET=self.selected.C_UNDERLYING_MARKET,
            enrich_metrics=lambda frame, skip_metrics, workers=8: frame.assign(
                **{
                    self.selected.C_UNDERLYING_MARKET: frame[self.selected.C_UNDERLYING_MARKET],
                    self.selected.C_STOCK_PRICE: 200.0,
                    self.selected.C_STOCK_PE: 30.0,
                    self.selected.C_STOCK_PB: 8.0,
                    self.selected.C_STOCK_DY: 0.5,
                    self.selected.C_VALUATION_SOURCE: "fixture_yahoo",
                    self.selected.C_VALUATION_ERROR: "",
                }
            ),
        )
        old_build = self.us_metrics.build_metrics
        try:
            self.us_metrics.build_metrics = lambda *args, **kwargs: self.fail("fallback US metrics should not run")
            enriched = self.selected.enrich_detail_stock_metrics(detail, object(), object(), self.us_metrics, us_listed, 1, 0, 0.0)
        finally:
            self.us_metrics.build_metrics = old_build

        self.assertEqual(enriched.iloc[0][self.selected.C_VALUATION_SOURCE], "fixture_yahoo")
        self.assertAlmostEqual(enriched.iloc[0][self.selected.C_STOCK_PE], 30.0)

    def test_sse_us_pcf_uses_cash_amount_over_nav(self) -> None:
        def fake_query(etf, sql):
            if sql == self.us_metrics.SSE_ETF_BASIC_SQL:
                return {"result": [{"NAVPERCU": "1000", "TRADING_DAY": "20260626"}]}
            return {
                "result": [
                    {
                        "INSTRUMENT_ID": "AAPL",
                        "INSTRUMENT_NAME": "Apple",
                        "UNDERLYION_SECURITY_ID": "9999",
                        "SUBSTITUTION_CASH_AMOUNT": "50",
                        "QUANTITY": "2",
                        "SUBSTITUTION_FLAG": "1",
                    }
                ]
            }

        old_query = self.us_metrics.query_sse_pcf
        try:
            self.us_metrics.query_sse_pcf = fake_query
            df, period = self.us_metrics.get_sse_pcf_holdings("513100")
        finally:
            self.us_metrics.query_sse_pcf = old_query

        self.assertEqual(period, "PCF:20260626")
        self.assertEqual(df.iloc[0]["\u80a1\u7968\u4ee3\u7801"], "AAPL")
        self.assertEqual(df.iloc[0]["\u5e02\u573a"], "US")
        self.assertAlmostEqual(df.iloc[0]["\u6743\u91cd%"], 5.0)
        self.assertEqual(df.iloc[0]["\u6743\u91cd\u6765\u6e90"], "SUBSTITUTION_CASH_AMOUNT/NAVPERCU")

    def test_szse_us_pcf_can_estimate_quantity_price_fx_weight(self) -> None:
        xml = """<Root>
        <NAVperCU>1000</NAVperCU><TradingDay>20260626</TradingDay>
        <Component>
          <UnderlyingSecurityIDSource>9999</UnderlyingSecurityIDSource>
          <UnderlyingSecurityID>MSFT</UnderlyingSecurityID>
          <UnderlyingSymbol>Microsoft</UnderlyingSymbol>
          <ComponentShare>2</ComponentShare>
          <CreationCashSubstitute>0</CreationCashSubstitute>
          <PremiumRatio>0</PremiumRatio>
          <SubstituteFlag>1</SubstituteFlag>
        </Component>
        </Root>"""
        old_fetch = self.us_metrics.fetch_szse_pcf_xml
        old_fx = self.us_metrics.get_usd_cny_rate
        old_price = self.us_metrics.get_us_latest_price
        try:
            self.us_metrics.fetch_szse_pcf_xml = lambda etf: (xml, "fixture.xml")
            self.us_metrics.get_usd_cny_rate = lambda: 7.0
            self.us_metrics.get_us_latest_price = lambda ticker: 10.0
            df, _ = self.us_metrics.get_szse_pcf_holdings("159941")
        finally:
            self.us_metrics.fetch_szse_pcf_xml = old_fetch
            self.us_metrics.get_usd_cny_rate = old_fx
            self.us_metrics.get_us_latest_price = old_price

        self.assertEqual(df.iloc[0]["\u80a1\u7968\u4ee3\u7801"], "MSFT")
        self.assertAlmostEqual(df.iloc[0]["\u6743\u91cd%"], 14.0)
        self.assertEqual(df.iloc[0]["\u6743\u91cd\u6765\u6e90"], "ComponentShare*US latest price*USD/CNY/NAVperCU")

    def test_auto_mode_can_choose_us_best_weight(self) -> None:
        key = "\u0045\u0054\u0046\u5185\u80a1\u7968\u6743\u91cd\u5408\u8ba1%"

        def fake_result(mode, weight):
            return pd.DataFrame({"x": [1]}), {key: weight, self.selected.C_MODE: mode}

        old_hk = self.selected.get_hk_stock_holdings
        old_a = self.selected.get_a_stock_holdings
        old_us = self.selected.get_us_stock_holdings
        try:
            self.selected.get_hk_stock_holdings = lambda module, etf: fake_result("hk", 20)
            self.selected.get_a_stock_holdings = lambda module, etf: fake_result("a", 50)
            self.selected.get_us_stock_holdings = lambda module, etf: fake_result("us", 90)
            _, meta = self.selected.resolve_holdings(self.selected.SelectedETF("513100", 1.0, "auto"), object(), object(), object(), 30)
        finally:
            self.selected.get_hk_stock_holdings = old_hk
            self.selected.get_a_stock_holdings = old_a
            self.selected.get_us_stock_holdings = old_us

        self.assertEqual(meta[self.selected.C_MODE], "us")

    def test_constraint_checks_include_target_us_weight(self) -> None:
        detail = pd.DataFrame(
            [
                {
                    "\u80a1\u7968\u4ee3\u7801": "AAPL",
                    "\u80a1\u7968\u540d\u79f0": "Apple",
                    "\u5e95\u5c42\u5e02\u573a": "US",
                    "\u7ec4\u5408\u7a7f\u900f\u6743\u91cd%": 60.0,
                    "\u0045\u0054\u0046\u5185\u6743\u91cd%": 60.0,
                }
            ]
        )
        metrics = pd.DataFrame([{"ETF\u4ee3\u7801": "PORTFOLIO"}])
        config = self.enhanced.ConstraintConfig(target_us_weight=50.0)

        checks = self.enhanced.build_constraint_checks(detail, metrics, pd.DataFrame(), config)
        row = checks[checks["\u7ea6\u675f"].eq("\u7f8e\u80a1\u6bd4\u4f8b\u7ea6\u675f")].iloc[0]

        self.assertAlmostEqual(row["\u5b9e\u9645\u503c"], 60.0)
        self.assertEqual(row["\u7ed3\u679c"], "\u901a\u8fc7")

    def test_run_pcf_metrics_us_final_table_from_selected_metrics(self) -> None:
        with TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            pd.DataFrame(
                [
                    {
                        "ETF\u4ee3\u7801": "513100",
                        "ETF\u540d\u79f0": "Nasdaq ETF",
                        "\u6301\u4ed3\u671f": "PCF:20260626",
                        "\u6301\u4ed3\u6765\u6e90": "sse_pcf_us",
                        "\u80a1\u7968\u6743\u91cd\u5408\u8ba1%": 98.5,
                        "\u80a1\u606f\u7387%": 1.2,
                        "PE": 30,
                        "PB": 6,
                        "\u5e74\u5316\u6536\u76ca%": 8,
                        "\u7d22\u63d0\u8bfa\u6bd4\u7387": 1.1,
                        "\u6ce2\u52a8\u7387%": 20,
                        "\u8fd1\u534a\u5e74\u6536\u76ca%": 3,
                        "\u8fd1\u4e00\u5e74\u6536\u76ca%": 7,
                        "\u8fd13\u5e74\u6536\u76ca%": 25,
                        "\u6536\u76ca\u6765\u6e90": "eastmoney_nav",
                        "\u6536\u76ca\u533a\u95f4": "window",
                        "\u98ce\u9669\u6307\u6807\u533a\u95f4": "risk",
                        "PE\u8986\u76d6\u6743\u91cd%": 90,
                        "\u8d1fPE\u6743\u91cd%": 0,
                    },
                    {"ETF\u4ee3\u7801": "PORTFOLIO"},
                ]
            ).to_csv(out_dir / "metrics_summary.csv", index=False, encoding="utf-8-sig")

            report = self.runner.build_final_tables(out_dir, "us")

            self.assertEqual(len(report), 1)
            self.assertTrue((out_dir / self.runner.FINAL_CSV).exists())
            self.assertTrue((out_dir / self.runner.FINAL_XLSX).exists())
            self.assertEqual(report.iloc[0]["ETF\u4ee3\u7801"], "513100")
            self.assertAlmostEqual(report.iloc[0]["\u7f8e\u80a1\u6301\u4ed3\u6743\u91cd%"], 98.5)


if __name__ == "__main__":
    unittest.main()
