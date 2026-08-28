import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


FACTOR_MODEL_PATH = Path(__file__).resolve().parent
if str(FACTOR_MODEL_PATH) not in sys.path:
    sys.path.insert(0, str(FACTOR_MODEL_PATH))

from unified_factor_panel import extend_with_screened_factors, screen_factor_frame
from domain_factor_timing import build_domain_factor_timing_report


def synthetic_frame(date_count=80, asset_count=40):
    rng = np.random.default_rng(20260814)
    dates = [f"202601{i + 1:02d}" for i in range(date_count)]
    assets = [f"{i:06d}.SZ" for i in range(asset_count)]
    rows = []
    asset_signal = np.linspace(-1.0, 1.0, asset_count)
    for d_i, date in enumerate(dates):
        day_noise = rng.normal(0, 0.03, asset_count)
        target = asset_signal + day_noise
        for a_i, asset in enumerate(assets):
            rows.append({
                "trade_date": date,
                "ts_code": asset,
                "target_5": target[a_i],
                "good_factor": asset_signal[a_i] + rng.normal(0, 0.02),
                "duplicate_good_factor": asset_signal[a_i] + rng.normal(0, 0.02),
                "weak_factor": 0.25 * asset_signal[a_i] + rng.normal(0, 0.50),
                "test_only_factor": rng.normal(0, 1.0),
                "low_coverage_factor": np.nan if a_i % 8 else asset_signal[a_i],
                "core_a": rng.normal(0, 1.0),
                "core_b": rng.normal(0, 1.0),
                "industry_name": "行业A" if a_i < asset_count // 2 else "行业B",
                "log_mv": float(a_i) / max(asset_count - 1, 1),
                "value_ep": asset_signal[a_i] + rng.normal(0, 0.03),
                "value_bp": asset_signal[a_i] + rng.normal(0, 0.03),
                "value_sp": asset_signal[a_i] + rng.normal(0, 0.03),
                "dividend": asset_signal[a_i] + rng.normal(0, 0.03),
                "growth_revenue": -asset_signal[a_i] + rng.normal(0, 0.03),
                "growth_operating_profit": -asset_signal[a_i] + rng.normal(0, 0.03),
                "growth_net_profit": -asset_signal[a_i] + rng.normal(0, 0.03),
                "ret_20": -asset_signal[a_i] + rng.normal(0, 0.03),
                "ret_60": asset_signal[a_i] + rng.normal(0, 0.03),
                "vol_20": abs(asset_signal[a_i]) + rng.normal(0, 0.01),
                "down_vol_20": abs(asset_signal[a_i]) + rng.normal(0, 0.01),
                "turnover": abs(asset_signal[a_i]) + rng.normal(0, 0.01),
                "range_1": abs(asset_signal[a_i]) + rng.normal(0, 0.01),
                "quality_roe": asset_signal[a_i] + rng.normal(0, 0.03),
                "quality_roa": asset_signal[a_i] + rng.normal(0, 0.03),
                "model_eligible": True,
            })
    frame = pd.DataFrame(rows)
    test_mask = frame["trade_date"].isin(dates[65:])
    frame.loc[test_mask, "target_5"] = frame.loc[test_mask, "test_only_factor"] * 100.0
    split = {"train": (10, 50), "valid": (50, 65), "test": (65, 75)}
    return frame, dates, assets, split


class UnifiedFactorPanelTests(unittest.TestCase):
    def test_screening_ignores_test_period_even_when_test_has_shock(self):
        frame, dates, _, split = synthetic_frame()
        selected = screen_factor_frame(
            frame,
            candidate_features=["good_factor", "test_only_factor", "weak_factor"],
            target_col="target_5",
            date_order=dates,
            split=split,
            top_n=1,
            lookback_days=55,
            rebalance_days=20,
            min_coverage=0.50,
            min_dates=20,
            min_assets_per_date=20,
            max_pair_corr=0.95,
        ).selected_features
        self.assertEqual(selected, ["good_factor"])

        shocked = frame.copy()
        test_mask = shocked["trade_date"].isin(dates[65:])
        shocked.loc[test_mask, "target_5"] = shocked.loc[test_mask, "test_only_factor"] * -1000.0
        selected_after_test_rewrite = screen_factor_frame(
            shocked,
            candidate_features=["good_factor", "test_only_factor", "weak_factor"],
            target_col="target_5",
            date_order=dates,
            split=split,
            top_n=1,
            lookback_days=55,
            rebalance_days=20,
            min_coverage=0.50,
            min_dates=20,
            min_assets_per_date=20,
            max_pair_corr=0.95,
        ).selected_features
        self.assertEqual(selected_after_test_rewrite, selected)

    def test_screening_removes_redundant_and_low_coverage_factors(self):
        frame, dates, _, split = synthetic_frame()
        result = screen_factor_frame(
            frame,
            candidate_features=[
                "good_factor",
                "duplicate_good_factor",
                "weak_factor",
                "low_coverage_factor",
            ],
            target_col="target_5",
            date_order=dates,
            split=split,
            top_n=2,
            lookback_days=55,
            rebalance_days=20,
            min_coverage=0.50,
            min_dates=20,
            min_assets_per_date=20,
            max_pair_corr=0.90,
        )
        near_duplicate_pair = {"good_factor", "duplicate_good_factor"}
        self.assertEqual(len(near_duplicate_pair.intersection(result.selected_features)), 1)
        self.assertTrue(any(item["factor_name"] == "low_coverage_factor" and item["reason"] == "low_coverage" for item in result.diagnostics["excluded_sample"]))
        self.assertGreaterEqual(result.diagnostics["selected_count"], 1)

    def test_extend_with_screened_factors_loads_warehouse_values(self):
        frame, dates, assets, split = synthetic_frame()
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "warehouse.sqlite3"
            con = sqlite3.connect(db_path)
            try:
                con.execute(
                    "create table factor_value_daily("
                    "trade_date text not null, ts_code text not null, factor_name text not null, "
                    "factor_value real, factor_group text, source_agent text)"
                )
                rows = []
                lookup = frame.set_index(["trade_date", "ts_code"])
                for (date, asset), row in lookup.iterrows():
                    rows.append((date, asset, "good_ext", float(row["good_factor"]), "llm", "unit_test"))
                    rows.append((date, asset, "bad_ext", float(row["weak_factor"]), "llm", "unit_test"))
                con.executemany("insert into factor_value_daily values(?,?,?,?,?,?)", rows)
                con.commit()
            finally:
                con.close()

            merged, features, report = extend_with_screened_factors(
                frame[["trade_date", "ts_code", "target_5", "core_a", "core_b", "model_eligible"]].copy(),
                database_path=db_path,
                base_features=["core_a", "core_b"],
                target_col="target_5",
                date_order=dates,
                assets=assets,
                split=split,
                config={
                    "factor_universe_mode": "screened_full",
                    "factor_screen_top_n": 2,
                    "factor_screen_lookback_days": 55,
                    "factor_screen_rebalance_days": 20,
                    "factor_screen_min_coverage": 0.50,
                    "factor_screen_min_dates": 20,
                    "factor_screen_min_assets_per_date": 20,
                    "factor_screen_max_pair_corr": 0.95,
                },
            )
        self.assertIn("good_ext", merged.columns)
        self.assertIn("good_ext", features)
        self.assertEqual(report["source_table"], "factor_value_daily")
        self.assertEqual(report["test_usage"], "excluded_from_factor_screening")

    def test_domain_factor_timing_report_uses_train_validation_only(self):
        frame, dates, _, split = synthetic_frame(date_count=90, asset_count=45)
        report = build_domain_factor_timing_report(
            frame,
            candidate_features=["good_factor", "weak_factor", "test_only_factor"],
            selected_features=["good_factor"],
            base_features=["core_a", "core_b", "ret_20", "ret_60", "value_ep", "growth_revenue"],
            target_col="target_5",
            date_order=dates,
            split=split,
            config={
                "domain_timing_candidate_limit": 8,
                "domain_timing_lookback_days": 55,
                "domain_timing_rebalance_days": 20,
                "domain_timing_min_assets_per_domain": 5,
                "domain_timing_min_dates": 8,
                "domain_timing_permutations": 25,
                "factor_screen_min_coverage": 0.50,
                "factor_screen_min_assets_per_date": 20,
            },
        )
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["test_usage"], "excluded_from_domain_factor_timing_and_heterogeneity_tests")
        self.assertIn("industry", report["domain_schemes"])
        self.assertIn("size", report["domain_schemes"])
        self.assertIn("style", report["domain_schemes"])
        self.assertIn("supervised_pricing", report["domain_schemes"])
        self.assertGreaterEqual(report["construction_audit"]["audited_count"], 3)
        self.assertTrue(report["quarterly_factor_timing"])



if __name__ == "__main__":
    unittest.main(verbosity=2)
