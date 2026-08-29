import unittest

import numpy as np

from framework.backtest.robust_covariance import robust_covariance


class RobustCovarianceTests(unittest.TestCase):
    def test_estimate_is_symmetric_finite_and_positive_semidefinite(self) -> None:
        rng = np.random.default_rng(20260731)
        market = rng.normal(0.0, 0.008, size=(504, 1))
        history = 0.45 * market + rng.normal(0.0, 0.010, size=(504, 8))
        history[17, 2] = np.nan
        covariance, diagnostics = robust_covariance(
            history,
            annualization=252.0,
            half_life=63.0,
            newey_west_lags=2,
            diagonal_shrinkage=0.25,
            regime_lookback=126,
            regime_half_life=20.0,
            return_diagnostics=True,
        )
        self.assertTrue(np.isfinite(covariance).all())
        self.assertTrue(np.allclose(covariance, covariance.T, atol=1.0e-12))
        self.assertGreater(float(np.linalg.eigvalsh(covariance).min()), 0.0)
        self.assertEqual(diagnostics["observations"], 504)
        self.assertEqual(diagnostics["assets"], 8)

    def test_regime_scaling_responds_to_recent_volatility_without_future_data(self) -> None:
        rng = np.random.default_rng(7)
        calm = rng.normal(0.0, 0.004, size=(220, 4))
        stressed = rng.normal(0.0, 0.020, size=(32, 4))
        history = np.vstack([calm, stressed])
        _, diagnostics = robust_covariance(
            history,
            annualization=252.0,
            half_life=90.0,
            newey_west_lags=1,
            diagonal_shrinkage=0.30,
            regime_lookback=32,
            regime_half_life=8.0,
            return_diagnostics=True,
        )
        self.assertGreater(diagnostics["regime_variance_multiplier"], 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
