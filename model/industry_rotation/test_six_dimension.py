import json
import math
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd


MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

import six_dimension_model as model


def _resolve_project_root(root: Path) -> Path:
    for candidate in (root, *root.parents):
        if (candidate / "database" / "research_warehouse.db").exists() and (candidate / "board").exists():
            return candidate
    return root.parents[1]


PROJECT_ROOT = _resolve_project_root(MODULE_DIR)
RELEASE_SNAPSHOT = (
    PROJECT_ROOT
    / "output"
    / "industry_rotation"
    / "release_candidate"
    / "rotation_snapshot_v5.json"
)


def _strict_json(path: Path):
    def reject_constant(value: str):
        raise ValueError(f"non-standard JSON numeric constant: {value}")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)


def _assert_finite_json(test: unittest.TestCase, value, location: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _assert_finite_json(test, child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_finite_json(test, child, f"{location}[{index}]")
    elif isinstance(value, float):
        test.assertTrue(math.isfinite(value), f"non-finite JSON number at {location}: {value}")


def _plausibility_rows() -> pd.DataFrame:
    common = {
        "pb": 2.0,
        "ps_ttm": 4.0,
        "dv_ttm": 3.5,
        "roe": 12.0,
        "roa": 5.0,
        "total_revenue": 100.0,
        "gross_margin": 30.0,
        "netprofit_margin": 12.0,
        "debt_to_assets": 45.0,
        "current_ratio": 1.5,
        "assets_turn": 0.8,
        "op_yoy": 8.0,
        "tr_yoy": 10.0,
        "netprofit_yoy": 15.0,
    }
    return pd.DataFrame(
        [
            {**common, "pe_ttm": -10.0, "roe": np.nan},
            {**common, "pe_ttm": 20.0},
        ]
    )


class SixDimensionPureFunctionTests(unittest.TestCase):
    def test_membership_normalisation_is_unique_and_non_overlapping(self):
        rows = pd.DataFrame(
            [
                ["000001.SZ", "20200101", "20201231", "银行"],
                ["000001.SZ", "20200601", "20200831", "电子"],
                ["000001.SZ", "20200901", None, "银行"],
                ["000002.SZ", "20200301", "20200430", "银行"],
                ["000002.SZ", "20200301", "20200430", "电子"],
            ],
            columns=["ts_code", "start_date", "end_date", "industry_name"],
        )
        normalised = model._normalise_memberships(
            rows,
            "20200101",
            "20201231",
            {"银行", "电子"},
        )
        ordered = normalised.sort_values(["ts_code", "start_date", "end_date"])
        previous_end = ordered.groupby("ts_code")["end_date"].shift()
        overlap = previous_end.notna() & ordered["start_date"].le(previous_end.fillna(""))
        self.assertFalse(bool(overlap.any()))
        self.assertGreaterEqual(int(normalised.attrs["ambiguous_intervals_excluded"]), 1)

        july = normalised[
            normalised["start_date"].le("20200715")
            & normalised["end_date"].ge("20200715")
        ]
        self.assertEqual(len(july), 1)
        self.assertEqual(july.iloc[0]["industry_name"], "电子")

    def test_financial_anomaly_and_same_day_announcement_are_isolated(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute(
            """
            CREATE TABLE financial_report_visible (
                ts_code TEXT,
                visible_date TEXT,
                end_date TEXT,
                total_revenue REAL,
                gross_margin REAL,
                netprofit_margin REAL,
                roe REAL,
                roa REAL,
                debt_to_assets REAL,
                current_ratio REAL,
                assets_turn REAL,
                op_yoy REAL,
                tr_yoy REAL,
                netprofit_yoy REAL
            )
            """
        )
        rows = [
            ("000001.SZ", "20200119", "20200930", 100, 90, 90, 99, 99, 1, 9, 9, 99, 99, 99),
            ("000001.SZ", "20200430", "20191231", 100, 30, 12, 20, 8, 45, 1.5, 0.8, 8, 10, 15),
        ]
        connection.executemany(
            "INSERT INTO financial_report_visible VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        stocks = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2020-04-30", "2020-05-06"]),
                "ts_code": ["000001.SZ", "000001.SZ"],
            }
        )
        merged = model._merge_visible_financials(connection, stocks, "20200506")
        same_day = merged.loc[merged["trade_date"].eq(pd.Timestamp("2020-04-30"))].iloc[0]
        next_day = merged.loc[merged["trade_date"].eq(pd.Timestamp("2020-05-06"))].iloc[0]
        self.assertTrue(pd.isna(same_day["roe"]), "公告日无时间戳，禁止同日使用")
        self.assertEqual(float(next_day["roe"]), 20.0)
        self.assertFalse(bool(merged["roe"].eq(99.0).any()), "报告期末晚于可见日的异常记录未隔离")

    def test_negative_pe_is_not_converted_to_a_positive_valuation_signal(self):
        filtered = model._plausibility_filter(_plausibility_rows())
        self.assertTrue(pd.isna(filtered.loc[0, "earnings_yield"]))
        self.assertAlmostEqual(float(filtered.loc[1, "earnings_yield"]), 0.05, places=12)

    def test_dividend_yield_converts_percent_to_decimal(self):
        filtered = model._plausibility_filter(_plausibility_rows())
        self.assertAlmostEqual(float(filtered.loc[1, "dividend_yield"]), 0.035, places=12)

    def test_gross_profit_amount_is_normalised_to_gross_margin_percentage(self):
        rows = _plausibility_rows()
        rows.loc[1, "total_revenue"] = 200_000_000.0
        rows.loc[1, "gross_margin"] = 70_000_000.0
        filtered = model._plausibility_filter(rows)
        self.assertAlmostEqual(float(filtered.loc[1, "gross_margin"]), 35.0, places=12)

    def test_missing_factor_is_renormalised_and_never_imputed_as_zero(self):
        index = pd.DatetimeIndex(["2024-01-31"])
        columns = ["电子"]
        available = pd.DataFrame([[0.8]], index=index, columns=columns)
        missing = pd.DataFrame([[np.nan]], index=index, columns=columns)
        result = model._mean_available([missing, available], minimum=1)
        self.assertAlmostEqual(float(result.iloc[0, 0]), 0.8, places=12)
        self.assertTrue(pd.isna(model._mean_available([missing, available], minimum=2).iloc[0, 0]))

        dimensions = {
            "prosperity": pd.DataFrame([[0.8]], index=index, columns=columns),
            "fundamental": missing,
            "technical": pd.DataFrame([[0.6]], index=index, columns=columns),
            "valuation": pd.DataFrame([[0.4]], index=index, columns=columns),
            "funds": pd.DataFrame([[0.2]], index=index, columns=columns),
            "crowding": pd.DataFrame([[0.0]], index=index, columns=columns),
        }
        weights = {name: 0.2 for name in ("prosperity", "fundamental", "technical", "valuation", "funds")}
        score = model._weighted_dimension_score(dimensions, weights, crowding_penalty=0.0)
        self.assertAlmostEqual(float(score.iloc[0, 0]), 0.5, places=12)

    def test_moneyflow_uses_covered_turnover_and_ten_times_unit_conversion(self):
        index = pd.bdate_range("2023-01-02", periods=70)
        industry = "电子"
        daily = pd.DataFrame(
            {
                "trade_date": index,
                "industry_name": industry,
                "traded_amount": 1000.0,
                "flow_covered_amount": 500.0,
                "flow_total_amount": 10.0,
                "flow_large_amount": 6.0,
                "flow_extra_amount": 2.0,
                "flow_coverage": 0.5,
                "flow_positive_ratio": 1.0,
                "up_ratio": 0.6,
                "turnover_rate": 2.0,
                "volume_ratio": 1.1,
                "amount_concentration": 0.05,
                "limit_up_ratio": 0.0,
                "return_dispersion": 0.02,
            }
        )
        close = pd.DataFrame(
            {industry: np.linspace(100.0, 110.0, len(index))},
            index=index,
        )
        with patch.object(model, "_atomic_score", side_effect=lambda value, *args, **kwargs: value):
            _, funds, _ = model._daily_factor_scores(daily, close)
        self.assertAlmostEqual(float(funds["flow_total_5"].iloc[-1, 0]), 0.20, places=12)
        self.assertTrue(funds["flow_large_structure_5"].isna().all().all())
        self.assertTrue(funds["flow_extra_structure_20"].isna().all().all())

    def test_three_minus_one_month_momentum_excludes_the_latest_month(self):
        index = pd.bdate_range("2023-01-02", periods=90)
        industries = ["A", "B"]
        close = pd.DataFrame(
            {
                "A": 100.0 * np.power(1.0020, np.arange(len(index))),
                "B": 100.0 * np.power(1.0003, np.arange(len(index))),
            },
            index=index,
        )
        daily = pd.DataFrame(
            [
                {
                    "trade_date": date,
                    "industry_name": industry,
                    "traded_amount": 1000.0,
                    "flow_covered_amount": 500.0,
                    "flow_total_amount": 10.0,
                    "flow_large_amount": 6.0,
                    "flow_extra_amount": 2.0,
                    "flow_coverage": 0.5,
                    "flow_positive_ratio": 0.5,
                    "up_ratio": 0.5,
                    "turnover_rate": 2.0,
                    "volume_ratio": 1.0,
                    "amount_concentration": 0.05,
                    "limit_up_ratio": 0.0,
                    "return_dispersion": 0.02,
                }
                for date in index
                for industry in industries
            ]
        )
        with patch.object(model, "_atomic_score", side_effect=lambda value, *args, **kwargs: value):
            technical, _, _ = model._daily_factor_scores(daily, close)
        market = close.pct_change(fill_method=None).mean(axis=1, skipna=True)
        market_nav = market.fillna(0.0).add(1.0).cumprod()
        expected = (
            close.shift(21).div(close.shift(63)).sub(1.0)
            .sub(market_nav.shift(21).div(market_nav.shift(63)).sub(1.0), axis=0)
        )
        pd.testing.assert_frame_equal(technical["momentum_3_1"], expected)

    def test_moneyflow_structure_residuals_are_cross_sectionally_orthogonal(self):
        industries = [f"行业{number:02d}" for number in range(25)]
        dates = pd.DatetimeIndex(["2024-01-31", "2024-02-29"])
        total_values = np.linspace(-0.4, 0.6, len(industries))
        noise = 0.02 * np.sin(np.arange(len(industries), dtype=float))
        total = pd.DataFrame([total_values, total_values[::-1]], index=dates, columns=industries)
        large = pd.DataFrame(
            [2.0 * total_values + 0.3 + noise, 2.0 * total_values[::-1] + 0.3 - noise],
            index=dates,
            columns=industries,
        )
        large_structure = model._cross_section_residual(large, [total])
        extra = 3.0 * total + 2.0 * large_structure + pd.DataFrame(
            [0.01 * np.cos(np.arange(len(industries))), 0.01 * np.sin(np.arange(len(industries)))],
            index=dates,
            columns=industries,
        )
        extra_structure = model._cross_section_residual(extra, [total, large_structure])
        for date in dates:
            total_centered = total.loc[date] - total.loc[date].mean()
            large_centered = large_structure.loc[date] - large_structure.loc[date].mean()
            extra_centered = extra_structure.loc[date] - extra_structure.loc[date].mean()
            self.assertLess(abs(float(np.dot(large_centered, total_centered))), 1e-10)
            self.assertLess(abs(float(np.dot(extra_centered, total_centered))), 1e-10)
            self.assertLess(
                abs(float(np.dot(extra_centered, large_centered))),
                1e-10,
            )
        insufficient = total.iloc[:, :19]
        self.assertTrue(model._cross_section_residual(insufficient, [insufficient], minimum=20).isna().all().all())

    def test_crowding_is_a_nonnegative_monotonic_penalty_only(self):
        index = pd.DatetimeIndex(["2024-01-31"])
        columns = ["低拥挤", "中拥挤", "高拥挤"]
        dimensions = {
            name: pd.DataFrame([[0.8, 0.8, 0.8]], index=index, columns=columns)
            for name in ("prosperity", "fundamental", "technical", "valuation", "funds")
        }
        dimensions["crowding"] = pd.DataFrame([[0.0, 0.5, 1.0]], index=index, columns=columns)
        weights = {name: 0.2 for name in ("prosperity", "fundamental", "technical", "valuation", "funds")}
        baseline = model._weighted_dimension_score(dimensions, weights, crowding_penalty=0.0)
        penalised = model._weighted_dimension_score(dimensions, weights, crowding_penalty=0.4)
        reduction = baseline.sub(penalised).iloc[0]
        self.assertTrue((reduction >= -1e-12).all(), "低拥挤不得反向奖励")
        self.assertAlmostEqual(float(reduction["低拥挤"]), 0.0, places=12)
        self.assertAlmostEqual(float(reduction["中拥挤"]), 0.4 * 0.5**2, places=12)
        self.assertAlmostEqual(float(reduction["高拥挤"]), 0.4, places=12)
        self.assertLess(float(penalised["高拥挤"].iloc[0]), float(penalised["中拥挤"].iloc[0]))


class SixDimensionFormalArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for path in (model.DAILY_CACHE, model.MONTHLY_CACHE, model.MANIFEST, RELEASE_SNAPSHOT):
            if not path.exists():
                raise AssertionError(f"formal six-dimension artifact missing: {path}")
        cls.manifest = _strict_json(model.MANIFEST)
        cls.snapshot = _strict_json(RELEASE_SNAPSHOT)
        cls.daily = pd.read_csv(model.DAILY_CACHE, parse_dates=["trade_date"])
        cls.monthly = pd.read_csv(model.MONTHLY_CACHE, parse_dates=["trade_date"])

    def test_formal_cache_has_exactly_31_unique_industries_per_date(self):
        for label, frame in (("daily", self.daily), ("monthly", self.monthly)):
            self.assertEqual(int(frame["industry_name"].nunique()), 31, label)
            self.assertFalse(bool(frame.duplicated(["trade_date", "industry_name"]).any()), label)
            counts = frame.groupby("trade_date")["industry_name"].nunique()
            self.assertEqual(int(counts.min()), 31, label)
            self.assertEqual(int(counts.max()), 31, label)
            self.assertEqual(int(self.manifest[label]["industry_count"]), 31, label)
            self.assertEqual(int(self.manifest[label][f"minimum_{label}_industry_count"]), 31, label)

    def test_formal_manifest_confirms_zero_post_normalisation_overlap(self):
        pit = self.manifest["pit_membership"]
        self.assertEqual(int(pit["overlap_after_normalisation"]), 0)
        self.assertGreater(int(pit["normalised_rows"]), 0)
        self.assertIn("唯一行业", pit["rule"])

    def test_formal_cache_dividend_yield_is_decimal_not_percent(self):
        values = pd.to_numeric(self.monthly["dividend_yield"], errors="coerce").dropna()
        self.assertGreater(len(values), 0)
        self.assertGreater(float(values.max()), 0.0)
        self.assertLessEqual(float(values.max()), 0.30 + 1e-12)

    def test_five_return_dimension_weights_are_valid(self):
        weights = self.snapshot["six_dimension"]["current_weights"]
        self.assertEqual(
            set(weights),
            {
                "monthly_champion_anchor", "monthly_overlay", "monthly_online_ic",
                "weekly_overlay", "weekly_online_ic", "monthly_online_factor_stack",
                "weekly_online_factor_stack", "monthly_secondary_factor_cluster",
            },
        )
        self.assertEqual(float(weights["monthly_champion_anchor"]), 1.0)
        self.assertEqual(
            weights["monthly_overlay"],
            {"fundamental": 0.1, "valuation": 0.04, "technical": 0.18, "funds": 0.07, "crowding": 0.0},
        )
        self.assertEqual(
            weights["weekly_overlay"],
            {"fundamental": 0.04, "valuation": 0.02, "technical": 0.2, "funds": 0.1, "crowding": 0.0},
        )
        for profile, cap in (("monthly_online_ic", 0.20), ("weekly_online_ic", 0.25)):
            row = weights[profile]
            self.assertEqual(set(row), {"fundamental", "valuation", "technical", "funds"}, profile)
            self.assertTrue(all(0.0 <= float(value) <= cap + 1e-12 for value in row.values()), profile)
        secondary = weights["monthly_secondary_factor_cluster"]
        self.assertEqual(
            secondary,
            {
                "prosperity": 0.28, "fundamental": 0.26, "technical": 0.2,
                "valuation": 0.08, "funds": 0.18,
                "crowding_penalty": 0.16, "consensus_floor": 0.08,
            },
        )

    def test_data_cutoff_matches_cache_and_never_exceeds_outer_snapshot_date(self):
        daily_end = self.daily["trade_date"].max().strftime("%Y-%m-%d")
        monthly_end = self.monthly["trade_date"].max().strftime("%Y-%m-%d")
        expected = min(daily_end, monthly_end)
        self.assertEqual(self.manifest["daily"]["end"], daily_end)
        self.assertEqual(self.manifest["monthly"]["end"], monthly_end)
        self.assertEqual(self.snapshot["six_dimension"]["data_as_of"], expected)
        self.assertLessEqual(self.snapshot["six_dimension"]["data_as_of"], self.snapshot["as_of"])

    def test_formal_manifest_matches_current_database_signature(self):
        stat = model.WAREHOUSE.stat()
        database = self.manifest["database"]
        self.assertEqual(int(database["size"]), int(stat.st_size))
        self.assertEqual(int(database["mtime_ns"]), int(stat.st_mtime_ns))
        self.assertTrue(model._cache_valid())

    def test_all_declared_atomic_factors_are_effective_in_the_formal_snapshot(self):
        six = self.snapshot["six_dimension"]
        atomic = six["diagnostics"]["atomic_factors"]
        self.assertEqual(len(atomic), 78)
        ineffective = [
            row["factor"]
            for row in atomic
            if float(row.get("coverage") or 0.0) <= 0.0
            or not any(
                int((row.get("ic") or {}).get(split, {}).get("observations") or 0) > 0
                for split in ("train", "validation", "test")
            )
        ]
        self.assertEqual(ineffective, [], f"declared but ineffective factors: {ineffective}")
        effective = six["effective_factor_count"]
        self.assertEqual(sum(int(value) for value in effective.values()), 78)

    def test_json_artifacts_are_strict_and_contain_no_nan_or_infinity(self):
        _assert_finite_json(self, self.manifest, "manifest")
        _assert_finite_json(self, self.snapshot, "snapshot")
        for path in (model.MANIFEST, RELEASE_SNAPSHOT):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("NaN", text)
            self.assertNotIn("Infinity", text)

    def test_research_ranking_is_independent_and_funds_keep_fifteen_orthogonalised_factors(self):
        self.assertEqual(int(self.snapshot["six_dimension"]["factor_count"]["funds"]), 15)
        for frequency in ("monthly", "weekly"):
            payload = self.snapshot["industry"]["frequencies"][frequency]
            research = payload["six_dimension"]["research_ranking"]
            self.assertEqual(len(research), 31, frequency)
            self.assertEqual(sorted(int(row["rank"]) for row in research), list(range(1, 32)))
            self.assertTrue(all(len(row["components"]) == 7 for row in research), frequency)
            self.assertEqual(len(payload["candidate_audit"]), 2, frequency)
            expected_count = 9 if frequency == "monthly" else 5
            self.assertEqual(int(payload["six_dimension"]["candidate_search_count"]), expected_count)
            self.assertGreaterEqual(
                int(payload["six_dimension"]["all_candidate_count"]),
                expected_count,
            )

    def test_production_ranking_is_not_mixed_with_older_six_dimension_components(self):
        six_as_of = self.snapshot["six_dimension"]["data_as_of"]
        for frequency in ("monthly", "weekly"):
            payload = self.snapshot["industry"]["frequencies"][frequency]
            self.assertTrue(all(not row.get("components") for row in payload["ranking"]))
        for row in self.snapshot["high_frequency"]["industries"]:
            evidence = row["six_dimension"]
            self.assertEqual(evidence["as_of"], six_as_of)
            self.assertEqual(len(evidence["components"]), 7)
            self.assertNotIn("components", row)

    def test_method_text_distinguishes_production_and_research_semantics(self):
        method = self.snapshot["method"]
        self.assertIn("C6", method["industry_portfolio"])
        self.assertIn("\u7814\u7a76\u6311\u6218\u8005", method["industry_portfolio"])
        fundamental = next(row for row in method["factor_contract"] if "visible_date" in row)
        self.assertIn("\u4e25\u683c\u65e9\u4e8e", fundamental)

    def test_post_test_candidates_cannot_replace_the_production_champion(self):
        six = self.snapshot["six_dimension"]
        self.assertEqual(six["governance"]["selection"], "训练与验证")
        self.assertIn("仅报告", six["governance"]["test"])
        diagnostics = six["diagnostics"]
        for row in diagnostics["atomic_factors"]:
            self.assertTrue(row["ic"]["test"]["report_only"])
            self.assertFalse(row["ic"]["train"]["report_only"])
            self.assertFalse(row["ic"]["validation"]["report_only"])

        for frequency in ("monthly", "weekly"):
            payload = self.snapshot["industry"]["frequencies"][frequency]
            candidates = [
                row["candidate"]
                for row in payload["candidate_audit"]
                if "six_dimension" in str(row.get("candidate"))
            ]
            self.assertTrue(candidates, frequency)
            self.assertTrue(all("post_test_diagnostic" in name for name in candidates), frequency)
            self.assertNotIn("six_dimension", str(payload["selected_candidate"]), frequency)
            self.assertEqual(payload["promotion_gate"]["status"], "diagnostic_only", frequency)
            self.assertIn("never used to rank or tune", payload["promotion_gate"]["policy"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
