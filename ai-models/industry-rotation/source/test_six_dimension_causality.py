import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

import engine as engine_model
import six_dimension_model as model


def _month_end_signals(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    series = pd.Series(index, index=index)
    return [pd.Timestamp(value) for value in series.groupby(index.to_period("M")).max()]


def _online_weight_fixture() -> tuple[
    dict[str, pd.DataFrame],
    pd.DataFrame,
    list[pd.Timestamp],
]:
    index = pd.bdate_range("2018-01-02", "2023-12-29")
    industries = [f"行业{number:02d}" for number in range(25)]
    time_axis = np.arange(len(index), dtype=float)[:, None]
    industry_axis = np.arange(len(industries), dtype=float)[None, :]
    base = np.linspace(0.05, 0.95, len(industries), dtype=float)[None, :]

    daily_drift = np.linspace(-0.00015, 0.00065, len(industries), dtype=float)
    close = pd.DataFrame(
        100.0 * np.exp(np.outer(np.arange(len(index), dtype=float), daily_drift)),
        index=index,
        columns=industries,
    )

    def frame(values: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(
            np.clip(values, 0.0, 1.0),
            index=index,
            columns=industries,
        )

    dimensions = {
        "prosperity": frame(base + 0.07 * np.sin(time_axis / 31.0 + industry_axis * 0.73)),
        "fundamental": frame(base + 0.16 * np.cos(time_axis / 23.0 + industry_axis * 0.47)),
        "technical": frame(1.0 - base + 0.10 * np.sin(time_axis / 17.0 + industry_axis)),
        "valuation": frame(base + 0.24 * np.sin(time_axis / 19.0 + industry_axis * 1.31)),
        "funds": frame(0.5 + 0.38 * np.cos(time_axis / 13.0 + industry_axis * 1.67)),
        "crowding": frame(0.15 + 0.05 * np.sin(time_axis / 29.0 + industry_axis * 0.41)),
    }
    return dimensions, close, _month_end_signals(index)


class SixDimensionCausalityTests(unittest.TestCase):
    def test_execution_windows_do_not_overlap_and_maturity_is_exact(self):
        index = pd.bdate_range("2024-01-02", "2024-07-10")
        industries = ["电子", "银行", "医药"]
        close = pd.DataFrame(
            {
                industry: 100.0 + (number + 1) * np.arange(len(index), dtype=float)
                for number, industry in enumerate(industries)
            },
            index=index,
        )
        signal_dates = _month_end_signals(index)[:6]

        future, maturities = model._non_overlapping_forward_excess(close, signal_dates)

        self.assertEqual(list(maturities.index), signal_dates[:-1])
        self.assertNotIn(signal_dates[-1], maturities.index)
        self.assertTrue(future.loc[signal_dates[-1]].isna().all())

        half_open_windows: list[set[pd.Timestamp]] = []
        for signal, next_signal in zip(signal_dates[:-1], signal_dates[1:]):
            start_pos = int(index.searchsorted(signal, side="right"))
            end_pos = int(index.searchsorted(next_signal, side="right"))
            execution = index[start_pos]
            expected_maturity = index[end_pos]

            self.assertLess(execution, expected_maturity)
            self.assertEqual(pd.Timestamp(maturities.loc[signal]), expected_maturity)

            expected = close.loc[expected_maturity].div(close.loc[execution]).sub(1.0)
            expected = expected.sub(expected.mean(skipna=True))
            pd.testing.assert_series_equal(
                future.loc[signal],
                expected,
                check_names=False,
                rtol=1e-12,
                atol=1e-12,
            )
            half_open_windows.append(set(index[start_pos:end_pos]))

        for current, following in zip(half_open_windows[:-1], half_open_windows[1:]):
            self.assertTrue(current.isdisjoint(following))

    def test_future_price_changes_do_not_rewrite_current_or_prior_online_weights(self):
        dimensions, close, signal_dates = _online_weight_fixture()
        cutoff = signal_dates[30]

        _, baseline_weights = model._online_ic_score(dimensions, close, signal_dates)

        perturbed = close.copy()
        future_mask = perturbed.index > cutoff
        future_steps = np.arange(1, int(future_mask.sum()) + 1, dtype=float)[:, None]
        industry_load = np.where(np.arange(perturbed.shape[1]) % 2 == 0, 1.0, -1.0)[None, :]
        perturbed.loc[future_mask] = (
            perturbed.loc[future_mask].to_numpy()
            * np.exp(0.0015 * future_steps * industry_load)
        )

        _, revised_weights = model._online_ic_score(dimensions, perturbed, signal_dates)

        self.assertGreater(len(baseline_weights.loc[:cutoff]), 12)
        pd.testing.assert_frame_equal(
            baseline_weights.loc[:cutoff],
            revised_weights.loc[:cutoff],
            check_exact=False,
            rtol=1e-12,
            atol=1e-12,
        )

    def test_future_prices_do_not_rewrite_online_champion_overlay_weights(self):
        dimensions, close, signal_dates = _online_weight_fixture()
        anchor = dimensions["prosperity"]
        overlays = {
            "fundamental": dimensions["fundamental"],
            "technical": dimensions["technical"],
            "funds": dimensions["funds"],
        }
        cutoff = signal_dates[30]
        _, baseline = model._online_champion_overlay_score(
            anchor,
            overlays,
            close,
            signal_dates,
            {"fundamental": 0.15, "technical": 0.20, "funds": 0.10},
            lookback=36,
            minimum_history=12,
        )
        perturbed = close.copy()
        future_mask = perturbed.index > cutoff
        steps = np.arange(1, int(future_mask.sum()) + 1, dtype=float)[:, None]
        loads = np.where(np.arange(perturbed.shape[1]) % 2 == 0, 1.0, -1.0)[None, :]
        perturbed.loc[future_mask] = perturbed.loc[future_mask].to_numpy() * np.exp(0.002 * steps * loads)
        _, revised = model._online_champion_overlay_score(
            anchor,
            overlays,
            perturbed,
            signal_dates,
            {"fundamental": 0.15, "technical": 0.20, "funds": 0.10},
            lookback=36,
            minimum_history=12,
        )
        pd.testing.assert_frame_equal(
            baseline.loc[:cutoff],
            revised.loc[:cutoff],
            check_exact=False,
            rtol=1e-12,
            atol=1e-12,
        )

    def test_online_champion_overlay_starts_at_zero_weight(self):
        dimensions, close, signal_dates = _online_weight_fixture()
        _, weights = model._online_champion_overlay_score(
            dimensions["prosperity"],
            {
                "fundamental": dimensions["fundamental"],
                "technical": dimensions["technical"],
                "funds": dimensions["funds"],
            },
            close,
            signal_dates,
            {"fundamental": 0.15, "technical": 0.20, "funds": 0.10},
            lookback=36,
            minimum_history=12,
        )
        self.assertTrue(bool(weights.iloc[:12].eq(0.0).all().all()))
        self.assertTrue(bool(weights.ge(0.0).all().all()))
        self.assertTrue(bool(weights.le(pd.Series({"fundamental": 0.15, "technical": 0.20, "funds": 0.10}) + 1e-12).all().all()))
    def test_direction_labels_are_non_overlapping_and_purged_at_train_boundary(self):
        index = pd.bdate_range("2017-01-02", "2019-03-29")
        industries = ["电子", "银行"]
        close = pd.DataFrame(
            {
                "电子": 100.0 * np.exp(np.arange(len(index)) * 0.0003),
                "银行": 100.0 * np.exp(np.arange(len(index)) * -0.0001),
            },
            index=index,
        )
        signals = engine_model._signal_dates(index, "monthly")
        future, maturities, executions = engine_model._non_overlapping_direction_labels(close, signals)
        for previous, following in zip(maturities.index[:-1], maturities.index[1:]):
            self.assertEqual(pd.Timestamp(maturities.loc[previous]), pd.Timestamp(executions.loc[following]))
        eligible = maturities[
            maturities.index.to_series().ge(pd.Timestamp("2015-01-01"))
            & maturities.le(pd.Timestamp("2018-12-31"))
        ].index
        self.assertTrue(bool(maturities.loc[eligible].le(pd.Timestamp("2018-12-31")).all()))
        boundary = maturities[
            maturities.index.to_series().le(pd.Timestamp("2018-12-31"))
            & maturities.gt(pd.Timestamp("2018-12-31"))
        ]
        self.assertGreater(len(boundary), 0)
        perturbed = close.copy()
        perturbed.loc[perturbed.index > pd.Timestamp("2018-12-31"), "电子"] *= 100.0
        revised, _, _ = engine_model._non_overlapping_direction_labels(perturbed, signals)
        pd.testing.assert_frame_equal(future.loc[eligible], revised.loc[eligible])

    def test_ic_splits_use_maturity_and_purge_cross_boundary_labels(self):
        ic = pd.Series(
            [0.1, 0.2, 0.3],
            index=pd.to_datetime(["2021-11-30", "2021-12-31", "2022-01-31"]),
        )
        maturities = pd.Series(
            pd.to_datetime(["2021-12-31", "2022-02-01", "2022-02-28"]),
            index=ic.index,
        )
        stats = model._split_ic_stats(
            ic,
            maturities,
            {
                "validation": ("2019-01-01", "2021-12-31"),
                "test": ("2022-01-01", "2022-12-31"),
            },
        )
        self.assertEqual(stats["validation"]["observations"], 1)
        self.assertEqual(stats["validation"]["purged_boundary_labels"], 1)
        self.assertEqual(stats["validation"]["latest_maturity"], "2021-12-31")
        self.assertEqual(stats["test"]["observations"], 1)
        self.assertEqual(stats["test"]["latest_maturity"], "2022-02-28")

    def test_capped_weights_are_a_non_negative_capped_simplex(self):
        evidence = pd.Series(
            {
                "prosperity": -4.0,
                "fundamental": 0.0,
                "technical": 1.0,
                "valuation": 5.0,
                "funds": np.inf,
            }
        )

        weights = model._capped_weights(evidence, cap=0.30)

        self.assertEqual(list(weights.index), list(evidence.index))
        self.assertTrue(bool(np.isfinite(weights.to_numpy()).all()))
        self.assertTrue(bool(weights.ge(-1e-12).all()))
        self.assertAlmostEqual(float(weights.sum()), 1.0, places=12)
        self.assertLessEqual(float(weights.max()), 0.30 + 1e-12)


if __name__ == "__main__":
    unittest.main()
