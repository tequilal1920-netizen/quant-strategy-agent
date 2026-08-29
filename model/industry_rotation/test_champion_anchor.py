import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

import engine
import six_dimension_model as six
import build_tracking


class ChampionAnchorTests(unittest.TestCase):
    def test_frozen_parameter_contract_is_complete_and_finite(self):
        payload = json.loads(
            (MODULE_DIR / "champion_r32_directions.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["contract_count"], 248)
        self.assertEqual(len(payload["contracts"]), 248)
        self.assertEqual(
            payload["archive_sha256"],
            "BA03C519D01A2D5D076FF0484172BC799C4DAC003C3ADF1196EA0BB1564BE466",
        )
        signs = {int(row["sign"]) for row in payload["contracts"].values()}
        self.assertEqual(signs, {-1, 1})
        for key, row in payload["contracts"].items():
            self.assertEqual(key, f"{row['industry']}|{row['variable']}")
            self.assertTrue(np.isfinite(float(row["train_spearman_ic"])))
            self.assertEqual(int(row["sign"]), -1 if float(row["train_spearman_ic"]) < 0 else 1)

    def test_champion_lookup_matches_every_frozen_parameter(self):
        payload = engine._champion_parameters()
        for row in payload["contracts"].values():
            expected = float(row["train_spearman_ic"])
            self.assertEqual(engine._champion_ic(row["industry"], row["variable"]), expected)
            self.assertEqual(engine._champion_sign(row["industry"], row["variable"]), -1.0 if expected < 0 else 1.0)

    def test_orthogonal_overlay_has_zero_cross_sectional_anchor_beta(self):
        index = pd.DatetimeIndex(["2024-01-31", "2024-02-29"])
        columns = [f"行业{i:02d}" for i in range(25)]
        anchor = pd.DataFrame(
            np.tile(np.linspace(0.02, 0.98, len(columns)), (len(index), 1)),
            index=index,
            columns=columns,
        )
        independent = pd.DataFrame(
            np.tile(np.sin(np.arange(len(columns))), (len(index), 1)),
            index=index,
            columns=columns,
        )
        signal = anchor.mul(7.0).add(independent)
        residual = six._cross_section_residual(signal, [anchor], minimum=20)
        for date in index:
            pair = pd.concat([residual.loc[date], anchor.loc[date]], axis=1).dropna()
            self.assertAlmostEqual(float(pair.iloc[:, 0].corr(pair.iloc[:, 1])), 0.0, places=10)

    def test_tracking_source_is_governed_production_champion(self):
        source = (MODULE_DIR / "build_tracking.py").read_text(encoding="utf-8")
        self.assertIn("selected = production_candidate", source)
        self.assertIn('"model_scope": "production_champion"', source)
        self.assertNotIn("research_candidate =", source)

    def test_zero_overlay_weight_cannot_rewrite_champion(self):
        index = pd.DatetimeIndex(["2024-01-31"])
        columns = [f"行业{i:02d}" for i in range(25)]
        anchor = pd.DataFrame([np.linspace(0.02, 0.98, len(columns))], index=index, columns=columns)
        overlay = pd.DataFrame([np.linspace(0.98, 0.02, len(columns))], index=index, columns=columns)
        result = six._champion_overlay_score(anchor, {"technical": overlay}, {"technical": 0.0})
        expected = anchor.rank(axis=1, pct=True, method="average")
        pd.testing.assert_frame_equal(result, expected)


if __name__ == "__main__":
    unittest.main()