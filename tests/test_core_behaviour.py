from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import types
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

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
        cls.common = import_script("test_common", "scripts/pcf_common.py")

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
            user_file = out_dir / "user_notes.txt"
            keep_csv.write_text("csv", encoding="utf-8")
            keep_xlsx.write_text("xlsx", encoding="utf-8")
            remove_json.write_text("json", encoding="utf-8")
            user_file.write_text("keep", encoding="utf-8")

            self.runner.clean_intermediates(out_dir, ["513690"])

            self.assertTrue(keep_csv.exists())
            self.assertTrue(keep_xlsx.exists())
            self.assertFalse(remove_json.exists())
            self.assertTrue(user_file.exists())

    def test_clean_intermediates_does_not_delete_unrequested_holding_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            requested = out_dir / "513690_holdings.csv"
            unrelated = out_dir / "manual_holdings.csv"
            requested.write_text("generated", encoding="utf-8")
            unrelated.write_text("user", encoding="utf-8")

            self.runner.clean_intermediates(out_dir, ["513690"])

            self.assertFalse(requested.exists())
            self.assertTrue(unrelated.exists())

    def test_us_ticker_normalization_preserves_ticker_shape(self) -> None:
        self.assertEqual(self.us_metrics.normalize_us_ticker(" brk.b "), "BRK.B")
        self.assertEqual(self.us_metrics.normalize_us_ticker("BRK-B"), "BRK-B")
        self.assertEqual(self.us_metrics.normalize_us_ticker("googl"), "GOOGL")
        self.assertFalse(self.us_metrics.is_us_ticker("600000"))

    def test_selected_parse_modes_accepts_us(self) -> None:
        self.assertEqual(self.selected.parse_modes("us", 2), ["us", "us"])
        self.assertEqual(self.selected.parse_modes("auto,a,hk,us,us_listed", 5), ["auto", "a", "hk", "us", "us_listed"])
        self.assertEqual(self.selected.selected_etfs("510880,QQQ.US", "60,40", None)[1].mode, "us_listed")
        self.assertEqual(self.selected.normalize_etf_code("510880.0"), "510880")
        with self.assertRaises(ValueError):
            self.selected.normalize_etf_code("bad510880")
        with self.assertRaises(ValueError):
            self.selected.normalize_etf_code("QQQ!.US")
        with self.assertRaises(ValueError):
            self.selected.selected_etfs("510880,510880", "50,50", None)
        with self.assertRaises(ValueError):
            self.selected.parse_weights("nan,1", 2)

    def test_batch_code_loader_normalizes_csv_style_float_codes(self) -> None:
        args = Namespace(etf=["513690.0,159569"], input="unused.csv", code_column="ETF代码")
        self.assertEqual(self.runner.load_codes(args), ["513690", "159569"])

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
            fx_series=lambda lookback_days, cache_dir=None: (pd.Series(1.0, index=dates, dtype=float), "fixture_fx"),
        )

        metrics = self.selected.portfolio_return_metrics(
            [self.selected.SelectedETF("QQQ.US", 1.0, "us_listed")],
            ak_module=None,
            us_listed_module=us_listed,
            lookback_days=90,
        )

        self.assertIn("fixture_QQQ", metrics[self.selected.C_RETURN_SOURCE])
        self.assertNotIn(self.selected.C_RETURN_ERROR, metrics)

    def test_stock_constraint_merges_same_market_code_with_different_names(self) -> None:
        detail = pd.DataFrame(
            [
                {self.enhanced.C_ETF_CODE: "A", self.enhanced.C_MARKET: "US", self.enhanced.C_STOCK_CODE: "NVDA", self.enhanced.C_STOCK_NAME: "NVIDIA CORP", self.enhanced.C_ETF_INNER_WEIGHT: 4.0, self.enhanced.C_PORTFOLIO_WEIGHT: 4.0},
                {self.enhanced.C_ETF_CODE: "B", self.enhanced.C_MARKET: "US", self.enhanced.C_STOCK_CODE: "NVDA", self.enhanced.C_STOCK_NAME: "NVIDIA Corporation", self.enhanced.C_ETF_INNER_WEIGHT: 4.0, self.enhanced.C_PORTFOLIO_WEIGHT: 4.0},
            ]
        )
        checks = self.enhanced.build_constraint_checks(
            detail,
            pd.DataFrame([{self.enhanced.C_ETF_CODE: "PORTFOLIO"}]),
            pd.DataFrame(),
            self.enhanced.ConstraintConfig(max_stock_weight=7),
        )
        row = checks[checks["约束"].eq("单一股票权重上限")].iloc[0]
        self.assertAlmostEqual(row["实际值"], 8.0)
        self.assertEqual(row["结果"], "不通过")

    def test_valuation_constraint_requires_minimum_coverage(self) -> None:
        detail = pd.DataFrame(
            [{self.enhanced.C_ETF_CODE: "A", self.enhanced.C_MARKET: "US", self.enhanced.C_STOCK_CODE: "X", self.enhanced.C_STOCK_NAME: "X", self.enhanced.C_ETF_INNER_WEIGHT: 5.0, self.enhanced.C_PORTFOLIO_WEIGHT: 5.0}]
        )
        metrics = pd.DataFrame([{self.enhanced.C_ETF_CODE: "PORTFOLIO", "PE": 8.0, "PE覆盖权重%": 5.0}])
        checks = self.enhanced.build_constraint_checks(detail, metrics, pd.DataFrame(), self.enhanced.ConstraintConfig(max_pe=10))
        row = checks[checks["约束"].eq("最高PE")].iloc[0]
        self.assertEqual(row["结果"], "数据不足")

    def test_shared_return_metrics_reject_short_named_windows(self) -> None:
        dates = pd.date_range("2026-01-01", periods=40, freq="D")
        metrics = self.common.price_series_metrics(pd.Series(range(100, 140), index=dates, dtype=float))
        self.assertIsNone(metrics["half_year_return_pct"])
        self.assertIsNone(metrics["one_year_return_pct"])
        self.assertIsNone(metrics["three_year_return_pct"])

    def test_combined_price_series_stops_at_earliest_last_observation(self) -> None:
        short_dates = pd.date_range("2026-01-01", periods=5, freq="D")
        long_dates = pd.date_range("2026-01-01", periods=10, freq="D")
        combined = self.common.combine_price_series(
            {
                "A": pd.Series(range(100, 105), index=short_dates, dtype=float),
                "B": pd.Series(range(200, 210), index=long_dates, dtype=float),
            },
            {"A": 0.5, "B": 0.5},
        )
        self.assertEqual(combined.index[-1], short_dates[-1])
        self.assertTrue(self.common.combine_price_series({"A": pd.Series([1.0], index=short_dates[:1])}, {"A": 0.5, "B": 0.5}).empty)

    def test_incomplete_lookthrough_cannot_pass_structural_or_valuation_constraints(self) -> None:
        detail = pd.DataFrame(
            [{self.enhanced.C_ETF_CODE: "A", self.enhanced.C_MARKET: "US", self.enhanced.C_STOCK_CODE: "X", self.enhanced.C_STOCK_NAME: "X", self.enhanced.C_ETF_INNER_WEIGHT: 5.0, self.enhanced.C_PORTFOLIO_WEIGHT: 5.0}]
        )
        metrics = pd.DataFrame(
            [{self.enhanced.C_ETF_CODE: "PORTFOLIO", "PE": 8.0, "PE覆盖权重%": 100.0, "错误": '{"B":"holdings failed"}'}]
        )
        checks = self.enhanced.build_constraint_checks(
            detail,
            metrics,
            pd.DataFrame(),
            self.enhanced.ConstraintConfig(max_stock_weight=7, max_pe=10),
        )
        self.assertEqual(checks.loc[checks["约束"].eq("单一股票权重上限"), "结果"].iloc[0], "数据不足")
        self.assertEqual(checks.loc[checks["约束"].eq("最高PE"), "结果"].iloc[0], "数据不足")

    def test_skip_metrics_still_preserves_portfolio_holding_errors(self) -> None:
        detail = pd.DataFrame(
            [
                {
                    self.selected.C_ETF_CODE: "510001",
                    self.selected.C_ETF_NAME: "A",
                    self.selected.C_ETF_WEIGHT: 50.0,
                    self.selected.C_UNDERLYING_MARKET: "A",
                    self.selected.C_STOCK_CODE: "600000",
                    self.selected.C_STOCK_NAME: "浦发银行",
                    self.selected.C_ETF_INNER_WEIGHT: 10.0,
                    self.selected.C_PORTFOLIO_WEIGHT: 5.0,
                    self.selected.C_PERIOD: "fixture",
                    self.selected.C_SOURCE: "fixture",
                }
            ]
        )
        etf_summary = pd.DataFrame(
            [
                {self.selected.C_ETF_CODE: "510001", self.selected.C_ETF_WEIGHT: 50.0, self.selected.C_ERROR: "", self.selected.C_RAW_STOCK_ROWS: 1, self.selected.C_EFFECTIVE_STOCK_ROWS: 1, self.selected.C_MISSING_WEIGHT_ROWS: 0},
                {self.selected.C_ETF_CODE: "510002", self.selected.C_ETF_WEIGHT: 50.0, self.selected.C_ERROR: "fixture failure", self.selected.C_RAW_STOCK_ROWS: 0, self.selected.C_EFFECTIVE_STOCK_ROWS: 0, self.selected.C_MISSING_WEIGHT_ROWS: 0},
            ]
        )
        _, _, _, metrics = self.selected.add_metrics_to_tables(
            [self.selected.SelectedETF("510001", 0.5, "a"), self.selected.SelectedETF("510002", 0.5, "a")],
            pd.DataFrame(),
            detail,
            etf_summary,
            object(),
            object(),
            object(),
            None,
            1,
            0,
            0.0,
            365,
            skip_metrics=True,
        )
        portfolio = metrics[metrics[self.selected.C_ETF_CODE].eq("PORTFOLIO")].iloc[0]
        self.assertIn("510002", portfolio[self.selected.C_ERROR])
        self.assertEqual(portfolio[self.selected.C_RAW_STOCK_ROWS], 1)

    def test_a_share_dividend_output_retains_failures_and_gates_rank(self) -> None:
        with TemporaryDirectory() as tmpdir:
            candidates = pd.DataFrame(
                [{"ETF代码": "510001", "ETF名称": "低覆盖红利"}, {"ETF代码": "510002", "ETF名称": "失败红利"}]
            )
            report = pd.DataFrame(
                [{"ETF代码": "510001", "ETF名称": "低覆盖红利", "股息率%": 6.0, "股息率覆盖权重%": 50.0}]
            )
            self.a_metrics.write_outputs(
                Path(tmpdir),
                report,
                candidates,
                [{"ETF代码": "510002", "ETF名称": "失败红利", "说明": "fixture failure"}],
            )
            output = pd.read_csv(Path(tmpdir) / "a_share_dividend_etf_pcf_metrics.csv", encoding="utf-8-sig")

        self.assertEqual(len(output), 2)
        self.assertTrue(output["排名_按股息率"].isna().all())
        self.assertEqual(output.loc[output["ETF代码"].eq(510001), "数据状态"].iloc[0], "覆盖不足")
        self.assertEqual(output.loc[output["ETF代码"].eq(510002), "数据状态"].iloc[0], "失败")

    def test_batch_failure_rows_are_retained(self) -> None:
        report = pd.DataFrame([{"ETF代码": "513690", "数据状态": "有效", "错误": ""}])
        result = self.runner.append_failed_rows(report, ["513690", "159569"], {"159569": "fixture failure"})
        failed = result[result["ETF代码"].eq("159569")].iloc[0]
        self.assertEqual(failed["数据状态"], "失败")
        self.assertEqual(failed["错误"], "fixture failure")

    def test_batch_all_failure_report_has_full_schema(self) -> None:
        report = self.runner.build_failure_report(
            ["513690"],
            {"513690": "fixture failure"},
            "hk",
            "fallback",
        )
        self.assertEqual(report.iloc[0]["数据状态"], "失败")
        self.assertEqual(report.iloc[0]["错误"], "fixture failure")
        for column in ["股息率%", "PE", "PB", "年化收益%", "PCF原始股票行数"]:
            self.assertIn(column, report.columns)

    def test_batch_us_rejects_hk_only_options(self) -> None:
        args = Namespace(
            market="us",
            holdings_source="reported",
            alt_limit=0,
            sleep=0.05,
            top_n=20,
        )
        with self.assertRaises(SystemExit):
            self.runner.run_lookthrough(args, ["513100"], Path("unused"))

    def test_pcf_quality_uses_pre_filter_missing_row_count(self) -> None:
        detail = pd.DataFrame(
            [{self.enhanced.C_ETF_CODE: "A", self.enhanced.C_MARKET: "US", self.enhanced.C_STOCK_CODE: "X", self.enhanced.C_STOCK_NAME: "X", self.enhanced.C_PORTFOLIO_WEIGHT: 90.0}]
        )
        etf_summary = pd.DataFrame(
            [{self.enhanced.C_ETF_CODE: "A", "ETF内股票权重合计%": 90.0, "PCF原始股票行数": 11, "有效权重行数": 10, "未定价/缺失权重行数": 1}]
        )
        quality = self.enhanced.build_pcf_quality(detail, etf_summary)
        self.assertEqual(quality.iloc[0]["异常成分行数"], 1)

    def test_selected_default_output_is_compact(self) -> None:
        args = self.selected.build_parser().parse_args(["--etf", "510880"])
        self.assertFalse(args.full_output)
        self.assertFalse(args.use_cache)

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

    def test_auto_mode_merges_all_resolved_markets(self) -> None:
        key = "\u0045\u0054\u0046\u5185\u80a1\u7968\u6743\u91cd\u5408\u8ba1%"

        def fake_result(mode, market, code, weight):
            return (
                pd.DataFrame(
                    [
                        {
                            self.selected.C_MARKET: market,
                            self.selected.C_STOCK_CODE: code,
                            self.selected.C_ETF_INNER_WEIGHT: weight,
                        }
                    ]
                ),
                {
                    key: weight,
                    self.selected.C_MODE: mode,
                    self.selected.C_PERIOD: "fixture",
                    self.selected.C_SOURCE: f"fixture_{mode}",
                    self.selected.C_STOCK_COUNT: 1,
                },
            )

        old_hk = self.selected.get_hk_stock_holdings
        old_a = self.selected.get_a_stock_holdings
        old_us = self.selected.get_us_stock_holdings
        try:
            self.selected.get_hk_stock_holdings = lambda module, etf: fake_result("hk", "HK", "00005", 20)
            self.selected.get_a_stock_holdings = lambda module, etf: fake_result("a", "A", "600000", 50)
            self.selected.get_us_stock_holdings = lambda module, etf: fake_result("us", "US", "AAPL", 30)
            _, meta = self.selected.resolve_holdings(self.selected.SelectedETF("513100", 1.0, "auto"), object(), object(), object(), 30)
        finally:
            self.selected.get_hk_stock_holdings = old_hk
            self.selected.get_a_stock_holdings = old_a
            self.selected.get_us_stock_holdings = old_us

        self.assertEqual(meta[self.selected.C_MODE], "auto")
        self.assertEqual(meta[self.selected.C_STOCK_COUNT], 3)
        self.assertAlmostEqual(meta[key], 100.0)

    def test_auto_mode_retains_real_parser_failures_in_audit_output(self) -> None:
        key = "\u0045\u0054\u0046\u5185\u80a1\u7968\u6743\u91cd\u5408\u8ba1%"
        a_frame = pd.DataFrame(
            [
                {
                    self.selected.C_MARKET: "A",
                    self.selected.C_STOCK_CODE: "600000",
                    self.selected.C_STOCK_NAME: "Fixture",
                    self.selected.C_ETF_INNER_WEIGHT: 50.0,
                    self.selected.C_STOCK_PRICE: 10.0,
                    self.selected.C_WEIGHT_SOURCE: "fixture",
                    self.selected.C_DETAIL_SOURCE: "",
                    self.selected.C_VALUATION_ERROR: "",
                }
            ]
        )
        a_meta = {
            key: 50.0,
            self.selected.C_MODE: "a",
            self.selected.C_PERIOD: "fixture",
            self.selected.C_SOURCE: "fixture_a",
            self.selected.C_STOCK_COUNT: 1,
        }
        with (
            mock.patch.object(
                self.selected,
                "get_hk_stock_holdings",
                side_effect=RuntimeError("fixture HK transport failure"),
            ),
            mock.patch.object(
                self.selected,
                "get_a_stock_holdings",
                return_value=(a_frame, a_meta),
            ),
            mock.patch.object(
                self.selected,
                "get_us_stock_holdings",
                side_effect=self.selected.NoApplicableHoldings("no US rows"),
            ),
        ):
            _, _, etf_summary = self.selected.build_tables(
                [self.selected.SelectedETF("510001", 1.0, "auto")],
                object(),
                object(),
                object(),
                None,
                {},
                30.0,
            )

        error = etf_summary.iloc[0][self.selected.C_ERROR]
        self.assertIn("auto mode incomplete", error)
        self.assertIn("fixture HK transport failure", error)

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
                        "\u80a1\u606f\u7387\u8986\u76d6\u6743\u91cd%": 90,
                        "PB\u8986\u76d6\u6743\u91cd%": 90,
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
            self.assertEqual(report.iloc[0]["数据状态"], "有效")

    def test_final_table_requires_pe_and_pb_before_marking_row_valid(self) -> None:
        result = {
            "etf": "513100",
            "name": "Fixture",
            "holdings_period": "fixture",
            "holdings_source": "fixture",
            "summary": {
                "total_hk_weight_pct": 100.0,
                "raw_hk_rows": 1,
                "effective_hk_rows": 1,
                "missing_weight_rows": 0,
                "dividend_yield_pct": 5.0,
                "pb": None,
                "pe_simple": 10.0,
                "pe_positive_simple": 10.0,
                "pe_earnings_yield": 10.0,
                "coverage": {
                    "dividend_yield_weight_pct": 100.0,
                    "pb_weight_pct": 0.0,
                    "pe_earnings_yield_weight_pct": 100.0,
                    "negative_pe_weight_pct": 0.0,
                },
            },
            "returns": {},
        }
        with TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            self.hk_metrics.write_summary([result], out_dir)
            report = self.runner.build_hk_final_tables(out_dir, ["513100"])

        self.assertEqual(report.iloc[0]["数据状态"], "覆盖不足")
        self.assertTrue(pd.isna(report.iloc[0]["排名_按股息率"]))

    def test_hk_empty_summary_becomes_auditable_failure_table(self) -> None:
        with TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            self.hk_metrics.write_summary([], out_dir)
            pd.DataFrame(
                [{"etf": "513100", "error": "fixture holdings failure"}]
            ).to_csv(out_dir / "errors.csv", index=False, encoding="utf-8-sig")

            report = self.runner.build_hk_final_tables(out_dir, ["513100"])

            self.assertEqual(report.iloc[0]["数据状态"], "失败")
            self.assertEqual(report.iloc[0]["错误"], "fixture holdings failure")
            self.assertTrue((out_dir / self.runner.FINAL_XLSX).is_file())

    def test_a_share_main_returns_nonzero_when_every_candidate_fails(self) -> None:
        candidates = pd.DataFrame([{"ETF代码": "510001", "ETF名称": "Fixture"}])
        with TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            with (
                mock.patch.object(self.a_metrics, "load_etf_candidates", return_value=candidates),
                mock.patch.object(
                    self.a_metrics,
                    "get_pcf_holdings",
                    side_effect=RuntimeError("fixture PCF failure"),
                ),
                mock.patch.object(self.a_metrics, "enrich_stock_metrics", return_value={}),
                redirect_stdout(io.StringIO()),
            ):
                exit_code = self.a_metrics.main(
                    ["--out-dir", str(out_dir), "--sleep", "0"]
                )

            report = pd.read_csv(
                out_dir / "a_share_dividend_etf_pcf_metrics.csv",
                encoding="utf-8-sig",
            )
            self.assertEqual(exit_code, 1)
            self.assertEqual(report.iloc[0]["数据状态"], "失败")
            self.assertIn("fixture PCF failure", report.iloc[0]["错误"])

    def test_publication_failure_rolls_back_all_previous_files(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            stage = root / "stage"
            stage.mkdir()
            old_a = root / "a.csv"
            old_b = root / "b.xlsx"
            old_manifest = root / "run_manifest.json"
            old_a.write_text("old-a", encoding="utf-8")
            old_b.write_text("old-b", encoding="utf-8")
            old_manifest.write_text("old-manifest", encoding="utf-8")
            new_a = stage / "a.csv"
            new_b = stage / "b.xlsx"
            new_manifest = stage / "run_manifest.json"
            new_a.write_text("new-a", encoding="utf-8")
            new_b.write_text("new-b", encoding="utf-8")
            new_manifest.write_text("new-manifest", encoding="utf-8")
            real_replace = self.common._replace_file

            def fail_second_publication(source: Path, target: Path) -> None:
                if source == new_b:
                    raise PermissionError("fixture locked workbook")
                real_replace(source, target)

            with (
                mock.patch.object(
                    self.common,
                    "_replace_file",
                    side_effect=fail_second_publication,
                ),
                self.assertRaisesRegex(PermissionError, "fixture locked workbook"),
            ):
                self.common.publish_staged_files(
                    [(new_a, old_a), (new_b, old_b)],
                    commit_file=(new_manifest, old_manifest),
                )

            self.assertEqual(old_a.read_text(encoding="utf-8"), "old-a")
            self.assertEqual(old_b.read_text(encoding="utf-8"), "old-b")
            self.assertEqual(old_manifest.read_text(encoding="utf-8"), "old-manifest")


    def test_shared_return_metrics_accept_minimum_documented_windows(self) -> None:
        half_year_dates = pd.date_range("2025-01-01", periods=161, freq="D")
        one_year_dates = pd.date_range("2025-01-01", periods=341, freq="D")
        three_year_dates = pd.date_range("2023-01-01", periods=951, freq="D")

        self.assertIsNotNone(
            self.common.price_series_metrics(pd.Series(range(100, 261), index=half_year_dates, dtype=float))[
                "half_year_return_pct"
            ]
        )
        self.assertIsNotNone(
            self.common.price_series_metrics(pd.Series(range(100, 441), index=one_year_dates, dtype=float))[
                "one_year_return_pct"
            ]
        )
        self.assertIsNotNone(
            self.common.price_series_metrics(pd.Series(range(100, 1051), index=three_year_dates, dtype=float))[
                "three_year_return_pct"
            ]
        )

    def test_combined_price_series_aligns_same_calendar_day(self) -> None:
        dates = pd.date_range("2026-01-01", periods=40, freq="D")
        combined = self.common.combine_price_series(
            {
                "ETF": pd.Series(range(100, 140), index=dates + pd.Timedelta(hours=13, minutes=30), dtype=float),
                "FX": pd.Series(7.0, index=dates + pd.Timedelta(hours=23), dtype=float),
            },
            {"ETF": 0.5, "FX": 0.5},
        )
        self.assertEqual(len(combined), 40)
        self.assertTrue(all(timestamp.time().isoformat() == "00:00:00" for timestamp in combined.index))

    def test_a_share_pcf_retains_unresolved_legal_stock_rows(self) -> None:
        def fake_sse_query(etf, sql):
            if sql == self.a_metrics.SSE_ETF_BASIC_SQL:
                return {"result": [{"NAVPERCU": "1000", "TRADING_DAY": "20260725"}]}
            return {
                "result": [
                    {
                        "INSTRUMENT_ID": "600000",
                        "INSTRUMENT_NAME": "浦发银行",
                        "UNDERLYION_SECURITY_ID": "101",
                        "SUBSTITUTION_CASH_AMOUNT": "",
                        "QUANTITY": "0",
                    }
                ]
            }

        old_query = self.a_metrics.query_sse_pcf
        try:
            self.a_metrics.query_sse_pcf = fake_sse_query
            frame, _ = self.a_metrics.get_sse_pcf_holdings("510001")
        finally:
            self.a_metrics.query_sse_pcf = old_query

        self.assertEqual(len(frame), 1)
        self.assertTrue(pd.isna(frame.iloc[0]["权重%"]))
        self.assertEqual(frame.iloc[0]["权重来源"], "unresolved PCF component")

    def test_szse_pcf_retains_unresolved_legal_stock_rows(self) -> None:
        xml = """<Root>
        <NAVperCU>1000</NAVperCU><TradingDay>20260725</TradingDay>
        <Component>
          <UnderlyingSecurityIDSource>102</UnderlyingSecurityIDSource>
          <UnderlyingSecurityID>000001</UnderlyingSecurityID>
          <UnderlyingSymbol>平安银行</UnderlyingSymbol>
          <ComponentShare>0</ComponentShare>
          <CreationCashSubstitute>0</CreationCashSubstitute>
          <PremiumRatio>0</PremiumRatio>
        </Component>
        </Root>"""
        old_fetch = self.a_metrics.fetch_szse_pcf_xml
        try:
            self.a_metrics.fetch_szse_pcf_xml = lambda etf: (xml, "fixture.xml")
            frame, _ = self.a_metrics.get_szse_pcf_holdings("159001", {})
        finally:
            self.a_metrics.fetch_szse_pcf_xml = old_fetch

        self.assertEqual(len(frame), 1)
        self.assertTrue(pd.isna(frame.iloc[0]["权重%"]))
        self.assertEqual(frame.iloc[0]["权重来源"], "unresolved PCF component")

    def test_hk_dividend_rank_excludes_low_coverage_rows(self) -> None:
        def result(etf, dividend, coverage):
            return {
                "etf": etf,
                "name": etf,
                "holdings_period": "fixture",
                "holdings_source": "fixture",
                "summary": {
                    "total_hk_weight_pct": 100.0,
                    "raw_hk_rows": 1,
                    "effective_hk_rows": 1,
                    "missing_weight_rows": 0,
                    "dividend_yield_pct": dividend,
                    "pb": 1.0,
                    "pe_simple": 10.0,
                    "pe_positive_simple": 10.0,
                    "pe_earnings_yield": 10.0,
                    "coverage": {
                        "dividend_yield_weight_pct": coverage,
                        "pb_weight_pct": 100.0,
                        "pe_earnings_yield_weight_pct": 100.0,
                        "negative_pe_weight_pct": 0.0,
                    },
                },
                "returns": {},
            }

        with TemporaryDirectory() as tmpdir:
            self.hk_metrics.write_summary(
                [result("LOW", 9.0, 50.0), result("GOOD", 5.0, 90.0)],
                Path(tmpdir),
                rank_by_dividend=True,
                top=5,
            )
            ranked = pd.read_csv(Path(tmpdir) / "ranked_top5.csv", encoding="utf-8-sig")

        self.assertEqual(ranked["etf"].tolist(), ["GOOD"])

    def test_selected_short_us_ticker_is_not_zero_filled(self) -> None:
        detail = pd.DataFrame(
            [
                {
                    self.selected.C_ETF_CODE: "VO.US",
                    self.selected.C_STOCK_CODE: "AAPL",
                    self.selected.C_STOCK_NAME: "Apple",
                    self.selected.C_UNDERLYING_MARKET: "US",
                    self.selected.C_ETF_INNER_WEIGHT: 10.0,
                    self.selected.C_PORTFOLIO_WEIGHT: 10.0,
                }
            ]
        )
        etf_summary = pd.DataFrame(
            [
                {
                    self.selected.C_ETF_CODE: "VO.US",
                    self.selected.C_ETF_WEIGHT: 100.0,
                    self.selected.C_MODE: "us_listed",
                    self.selected.C_ERROR: "",
                }
            ]
        )
        _, _, _, metrics = self.selected.add_metrics_to_tables(
            [self.selected.SelectedETF("VO.US", 1.0, "us_listed")],
            pd.DataFrame(),
            detail,
            etf_summary,
            object(),
            object(),
            object(),
            None,
            1,
            0,
            0.0,
            365,
            skip_metrics=True,
        )
        self.assertIn("VO.US", metrics[self.selected.C_ETF_CODE].tolist())
        self.assertNotIn("0VO.US", metrics[self.selected.C_ETF_CODE].tolist())

    def test_selected_all_failed_run_writes_auditable_core_outputs(self) -> None:
        etf_summary = pd.DataFrame(
            [
                {
                    self.selected.C_ETF_CODE: "510001",
                    self.selected.C_ETF_NAME: "Fixture",
                    self.selected.C_ETF_WEIGHT: 100.0,
                    self.selected.C_MODE: "a",
                    self.selected.C_STOCK_COUNT: 0,
                    self.selected.C_RAW_STOCK_ROWS: 0,
                    self.selected.C_EFFECTIVE_STOCK_ROWS: 0,
                    self.selected.C_MISSING_WEIGHT_ROWS: 0,
                    self.selected.C_ERROR: "fixture failure",
                }
            ]
        )
        summary, detail, etf_summary, metrics = self.selected.add_metrics_to_tables(
            [self.selected.SelectedETF("510001", 1.0, "a")],
            pd.DataFrame(),
            pd.DataFrame(),
            etf_summary,
            object(),
            object(),
            object(),
            None,
            1,
            0,
            0.0,
            365,
            skip_metrics=True,
        )
        with TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            stale = out_dir / self.selected.OUT_HTML
            stale.write_text("old", encoding="utf-8")
            args = self.selected.build_parser().parse_args(
                ["--etf", "510001", "--skip-metrics", "--out-dir", str(out_dir)]
            )
            self.selected.write_outputs(out_dir, summary, detail, etf_summary, metrics, args)

            self.assertTrue((out_dir / self.selected.OUT_XLSX).exists())
            self.assertTrue((out_dir / "run_manifest.json").exists())
            self.assertFalse(stale.exists())
            metric_rows = pd.read_csv(out_dir / self.selected.OUT_METRICS, encoding="utf-8-sig")

        self.assertIn("510001", metric_rows[self.selected.C_ETF_CODE].astype(str).str.zfill(6).tolist())
        self.assertIn("PORTFOLIO", metric_rows[self.selected.C_ETF_CODE].astype(str).tolist())

    def test_run_pcf_metrics_main_returns_failure_and_writes_final_tables(self) -> None:
        with TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            with (
                mock.patch.object(
                    self.runner,
                    "run_lookthrough",
                    side_effect=subprocess.CalledProcessError(9, ["fixture"]),
                ),
                mock.patch.object(self.runner, "build_final_tables", side_effect=SystemExit("missing")),
                redirect_stdout(io.StringIO()),
            ):
                exit_code = self.runner.main(
                    ["--etf", "510001", "--out-dir", str(out_dir)]
                )

            self.assertEqual(exit_code, 1)
            self.assertTrue((out_dir / self.runner.FINAL_CSV).is_file())
            self.assertTrue((out_dir / self.runner.FINAL_XLSX).is_file())
            report = pd.read_csv(out_dir / self.runner.FINAL_CSV, encoding="utf-8-sig")
            self.assertEqual(report.loc[0, "数据状态"], "失败")
            self.assertIn("code 9", report.loc[0, "错误"])

    def test_selected_main_all_failure_returns_nonzero_with_core_outputs(self) -> None:
        etf_summary = pd.DataFrame(
            [
                {
                    self.selected.C_ETF_CODE: "510001",
                    self.selected.C_ETF_NAME: "Fixture",
                    self.selected.C_ETF_WEIGHT: 100.0,
                    self.selected.C_MODE: "a",
                    self.selected.C_STOCK_COUNT: 0,
                    self.selected.C_RAW_STOCK_ROWS: 0,
                    self.selected.C_EFFECTIVE_STOCK_ROWS: 0,
                    self.selected.C_MISSING_WEIGHT_ROWS: 0,
                    self.selected.C_ERROR: "fixture failure",
                }
            ]
        )
        with TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            with (
                mock.patch.object(self.selected, "import_module", return_value=object()),
                mock.patch.object(self.selected, "maybe_load_name_map", return_value={}),
                mock.patch.object(
                    self.selected,
                    "build_tables",
                    return_value=(pd.DataFrame(), pd.DataFrame(), etf_summary),
                ),
                redirect_stdout(io.StringIO()),
            ):
                exit_code = self.selected.main(
                    [
                        "--etf",
                        "510001",
                        "--markets",
                        "a",
                        "--skip-metrics",
                        "--out-dir",
                        str(out_dir),
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertTrue((out_dir / self.selected.OUT_XLSX).is_file())
            self.assertTrue((out_dir / "run_manifest.json").is_file())

    def test_selected_staging_failure_preserves_previous_outputs(self) -> None:
        with TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            old_summary = out_dir / self.selected.OUT_SUMMARY
            old_manifest = out_dir / "run_manifest.json"
            old_summary.write_text("old-summary", encoding="utf-8")
            old_manifest.write_text("old-manifest", encoding="utf-8")
            args = self.selected.build_parser().parse_args(
                ["--etf", "510001", "--skip-metrics", "--out-dir", str(out_dir)]
            )

            def fail_after_partial_stage(stage: Path, *unused) -> None:
                (stage / self.selected.OUT_SUMMARY).write_text("new-summary", encoding="utf-8")
                raise RuntimeError("fixture staging failure")

            with (
                mock.patch.object(
                    self.selected,
                    "_write_outputs_direct",
                    side_effect=fail_after_partial_stage,
                ),
                self.assertRaisesRegex(RuntimeError, "fixture staging failure"),
            ):
                self.selected.write_outputs(
                    out_dir,
                    pd.DataFrame(),
                    pd.DataFrame(),
                    pd.DataFrame(),
                    pd.DataFrame(),
                    args,
                )

            self.assertEqual(old_summary.read_text(encoding="utf-8"), "old-summary")
            self.assertEqual(old_manifest.read_text(encoding="utf-8"), "old-manifest")


if __name__ == "__main__":
    unittest.main()
