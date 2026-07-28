import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

import build_snapshot
import engine
import event_cache


class PriceRotationCandidateTests(unittest.TestCase):
    def setUp(self):
        self.index = pd.bdate_range("2020-01-02", periods=420)
        self.columns = [f"industry_{index:02d}" for index in range(31)]
        rng = np.random.default_rng(20260726)
        innovations = rng.normal(0.0002, 0.012, (len(self.index), len(self.columns)))
        self.close = pd.DataFrame(
            100.0 * np.exp(np.cumsum(innovations, axis=0)),
            index=self.index,
            columns=self.columns,
        )

    def test_top_five_target_policy_is_fully_invested(self):
        score = self.close.rank(axis=1, pct=True)
        targets = engine._targets(score, "monthly", top_n=5)
        self.assertTrue(targets)
        for target in targets.values():
            positive = target[target > 0]
            self.assertEqual(len(positive), 5)
            self.assertAlmostEqual(float(positive.sum()), 1.0, places=12)
            self.assertTrue(np.allclose(positive.to_numpy(), 0.20))
        policy = engine._candidate_target_policy(
            "C18_monthly_residual_path_top5"
        )
        self.assertEqual(policy["top_n"], 5)
        self.assertAlmostEqual(policy["position_cap"], 0.20)

    def test_price_signal_is_causal_under_future_price_perturbation(self):
        cutoff = self.index[330]
        baseline = build_snapshot._price_rotation_scores(self.close, self.index)
        perturbed_close = self.close.copy()
        future = perturbed_close.index > cutoff
        shocks = np.linspace(0.7, 1.4, int(future.sum()))[:, None]
        perturbed_close.loc[future] = perturbed_close.loc[future].to_numpy() * shocks
        perturbed = build_snapshot._price_rotation_scores(
            perturbed_close, self.index
        )
        for key in ("monthly", "weekly", "crowding_percentile"):
            pd.testing.assert_frame_equal(
                baseline[key].loc[:cutoff],
                perturbed[key].loc[:cutoff],
                check_exact=True,
            )

    def test_crowding_percentile_is_bounded(self):
        result = build_snapshot._price_rotation_scores(self.close, self.index)
        values = result["crowding_percentile"].stack().dropna()
        self.assertGreater(len(values), 0)
        self.assertGreaterEqual(float(values.min()), 0.0)
        self.assertLessEqual(float(values.max()), 1.0)

    def test_enhanced_momentum_is_causal_and_bounded(self):
        long_index = pd.bdate_range("2014-01-02", periods=1700)
        rng = np.random.default_rng(96)
        close = pd.DataFrame(
            100.0 * np.exp(
                np.cumsum(
                    rng.normal(
                        0.0002, 0.012,
                        (len(long_index), len(self.columns)),
                    ),
                    axis=0,
                )
            ),
            index=long_index,
            columns=self.columns,
        )
        cutoff = long_index[1450]
        baseline = build_snapshot._enhanced_momentum_scores(
            close, long_index
        )
        perturbed = close.copy()
        perturbed.loc[perturbed.index > cutoff] *= 1.25
        changed = build_snapshot._enhanced_momentum_scores(
            perturbed, long_index
        )
        for key in ("enhanced_rank", "regime_adjusted_rank"):
            pd.testing.assert_frame_equal(
                baseline[key].loc[:cutoff],
                changed[key].loc[:cutoff],
                check_exact=True,
            )
            values = baseline[key].stack().dropna()
            self.assertTrue(values.between(0.0, 1.0).all())

    def test_electronics_has_a_distinct_pcb_contract_for_strict_reproduction(self):
        blueprints = event_cache.ROBUST_EVENTS["电子"]
        self.assertGreaterEqual(len(blueprints), 6)
        names = [name for name, _ in blueprints]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(any("PCB" in name for name in names))


if __name__ == "__main__":
    unittest.main(verbosity=2)
