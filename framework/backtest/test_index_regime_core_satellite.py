from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from framework.backtest.index_regime_core_satellite import (
    BayesianAlphaConfig,
    CoreSatelliteConfig,
    add_bayesian_regime_alpha,
    backtest_core_satellite,
    optimize_core_satellite_weights,
)


def _monthly_panel(months: int = 24, securities: int = 30) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for month in range(months):
        date = f"{2020 + month // 12}{month % 12 + 1:02d}28"
        for security in range(securities):
            quality = security / max(securities - 1, 1)
            cycle = np.sin(month / 3.0 + security / 5.0)
            rows.append(
                {
                    "trade_date": date,
                    "ts_code": f"{security:06d}.SZ",
                    "industry_name": "金融" if security < securities // 2 else "制造",
                    "index_weight": 100.0 / securities,
                    "label_next_ret": 0.03 * (quality - 0.5) + 0.01 * cycle,
                    "quality_value_low_crowding_v8": quality,
                    "fundamental_quality_v4": quality * 0.9 + 0.05,
                    "domain_quality_neutral_v9": quality,
                    "domain_value_neutral_v9": 1.0 - quality * 0.2,
                    "factor_domain_agent_v9": quality * 0.8 + 0.1,
                    "domain_money_neutral_v9": 0.5 + 0.4 * cycle,
                    "domain_technical_neutral_v9": 0.5 + 0.3 * cycle,
                    "trend_quality_v4": 0.5 + 0.2 * cycle,
                    "kline_context_agent_v8": 0.5 + 0.2 * cycle,
                    "kline_executable_skill_v11": 0.5 + 0.15 * cycle,
                    "total_mv": 100.0 + security * 20.0,
                    "pb": 1.0 + security * 0.05,
                    "roe": 0.05 + quality * 0.15,
                    "mom60": 0.02 * cycle,
                    "mom120": 0.03 * cycle,
                    "turnover_rate": 1.0 + security * 0.05,
                }
            )
    return pd.DataFrame(rows)


class BayesianCoreSatelliteTests(unittest.TestCase):
    def test_bayesian_alpha_never_reads_current_holding_period_label(self) -> None:
        panel = _monthly_panel()
        changed = panel.copy()
        final_date = panel["trade_date"].max()
        mask = changed["trade_date"] == final_date
        changed.loc[mask, "label_next_ret"] = np.linspace(5.0, -5.0, int(mask.sum()))
        config = BayesianAlphaConfig(minimum_history=6)
        first, first_diagnostics = add_bayesian_regime_alpha(panel, config)
        second, second_diagnostics = add_bayesian_regime_alpha(changed, config)
        columns = ["bayesian_regime_alpha_v15", "bayesian_active_confidence_v15"]
        pd.testing.assert_frame_equal(
            first.loc[mask, columns].reset_index(drop=True),
            second.loc[mask, columns].reset_index(drop=True),
        )
        self.assertFalse(first_diagnostics["monthly_diagnostics"][-1]["uses_current_label"])
        self.assertFalse(second_diagnostics["monthly_diagnostics"][-1]["uses_current_label"])

    def test_optimizer_keeps_full_benchmark_beta_and_soft_industry_budgets(self) -> None:
        panel, _ = add_bayesian_regime_alpha(
            _monthly_panel(), BayesianAlphaConfig(minimum_history=6)
        )
        final = panel[panel["trade_date"] == panel["trade_date"].max()].copy()
        final["active_risk_volatility"] = 0.20
        config = CoreSatelliteConfig(
            turnover_penalty=0.0,
            max_industry_deviation=0.01,
        )
        weights, diagnostics = optimize_core_satellite_weights(
            final,
            "bayesian_regime_alpha_v15",
            "bayesian_active_confidence_v15",
            config=config,
        )
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=10)
        self.assertTrue(diagnostics["fully_invested"])
        self.assertFalse(diagnostics["absolute_market_timing"])
        self.assertLessEqual(
            diagnostics["max_industry_deviation"],
            config.max_industry_deviation + 1.0e-9,
        )
        self.assertGreaterEqual(diagnostics["benchmark_core_weight"], 0.0)

    def test_negative_or_absent_evidence_shrinks_overlay_instead_of_flipping(self) -> None:
        final = _monthly_panel(months=1)
        final["bayesian_regime_alpha_v15"] = np.linspace(0.0, 1.0, len(final))
        final["bayesian_active_confidence_v15"] = 0.0
        final["active_risk_volatility"] = 0.20
        weights, diagnostics = optimize_core_satellite_weights(
            final,
            "bayesian_regime_alpha_v15",
            "bayesian_active_confidence_v15",
            config=CoreSatelliteConfig(turnover_penalty=0.0),
        )
        base = final.set_index("ts_code")["index_weight"]
        base = base / base.sum()
        for code, weight in weights.items():
            self.assertAlmostEqual(weight, float(base.loc[code]), places=12)
        self.assertEqual(diagnostics["active_share"], 0.0)
        self.assertEqual(diagnostics["target_tracking_error"], 0.0)

    def test_next_return_does_not_change_signal_date_weights(self) -> None:
        panel, _ = add_bayesian_regime_alpha(
            _monthly_panel(), BayesianAlphaConfig(minimum_history=6)
        )
        final = panel[panel["trade_date"] == panel["trade_date"].max()].copy()
        final["active_risk_volatility"] = 0.20
        changed = final.copy()
        changed["label_next_ret"] = np.linspace(10.0, -10.0, len(changed))
        first, _ = optimize_core_satellite_weights(
            final,
            "bayesian_regime_alpha_v15",
            "bayesian_active_confidence_v15",
            config=CoreSatelliteConfig(turnover_penalty=0.0),
        )
        second, _ = optimize_core_satellite_weights(
            changed,
            "bayesian_regime_alpha_v15",
            "bayesian_active_confidence_v15",
            config=CoreSatelliteConfig(turnover_penalty=0.0),
        )
        self.assertEqual(first, second)

    def test_backtest_reports_benchmark_relative_path_and_cost(self) -> None:
        panel, _ = add_bayesian_regime_alpha(
            _monthly_panel(), BayesianAlphaConfig(minimum_history=6)
        )
        returns, benchmark, nav, signals, evidence = backtest_core_satellite(
            panel,
            "bayesian_regime_alpha_v15",
            "bayesian_active_confidence_v15",
            cost_rate=0.001,
            config=CoreSatelliteConfig(volatility_min_periods=3),
        )
        self.assertEqual(len(returns), len(benchmark))
        self.assertEqual(len(nav), len(returns))
        self.assertTrue(signals)
        self.assertIn("relative_nav", nav[-1])
        self.assertGreaterEqual(nav[-1]["transaction_cost"], 0.0)
        self.assertFalse(evidence["promotion_eligible"])
        self.assertFalse(evidence["selection_uses_test"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
