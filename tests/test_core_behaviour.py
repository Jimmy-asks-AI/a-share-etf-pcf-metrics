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
        cls.selected = import_script("test_selected", "scripts/selected_etf_lookthrough.py")
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


if __name__ == "__main__":
    unittest.main()
