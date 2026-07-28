import math
import sys
import unittest
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from research_metrics import (  # noqa: E402
    annualized_information_ratio,
    annualized_sharpe,
    compounded_annual_return,
    effective_observations,
    hac_information_ratio,
)


class ResearchMetricsTests(unittest.TestCase):
    def test_sharpe_uses_arithmetic_period_return_not_cagr(self):
        returns = np.array([0.20, -0.10, 0.08, -0.04] * 6, dtype=float)
        expected = float(
            np.mean(returns) / np.std(returns, ddof=1) * math.sqrt(12.0)
        )
        result = annualized_sharpe(returns, 12)
        cagr_over_vol = compounded_annual_return(returns, 12) / (
            np.std(returns, ddof=1) * math.sqrt(12.0)
        )
        self.assertAlmostEqual(result, expected, places=12)
        self.assertNotAlmostEqual(result, cagr_over_vol, places=6)

    def test_information_ratio_uses_active_arithmetic_mean(self):
        active = np.array([0.02, -0.01, 0.015, 0.0] * 6, dtype=float)
        expected = float(
            np.mean(active) / np.std(active, ddof=1) * math.sqrt(12.0)
        )
        self.assertAlmostEqual(
            annualized_information_ratio(active, 12),
            expected,
            places=12,
        )

    def test_hac_reduces_serially_correlated_ic_overstatement(self):
        rng = np.random.default_rng(29)
        innovations = rng.normal(0.0, 0.01, 500)
        values = np.empty(500)
        values[0] = 0.01
        for index in range(1, len(values)):
            values[index] = (
                0.01
                + 0.82 * (values[index - 1] - 0.01)
                + innovations[index]
            )
        naive = float(
            np.mean(values) / np.std(values, ddof=1) * math.sqrt(252.0)
        )
        adjusted = hac_information_ratio(values, 252, minimum_lag=4)
        self.assertLess(adjusted, naive)
        self.assertLess(effective_observations(values, minimum_lag=4), len(values))

    def test_degenerate_series_is_finite(self):
        self.assertEqual(annualized_sharpe([0.01], 12), 0.0)
        self.assertEqual(annualized_sharpe([0.01, 0.01], 12), 0.0)
        self.assertEqual(hac_information_ratio([], 252), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
