from __future__ import annotations

import json

import numpy as np

import build_snapshot_v63_real_chain_four_asset_cycle_bl_rp_macro as v63


def test_v63_builds_real_factor_chain_snapshot() -> None:
    snapshot = v63.build_snapshot()
    assert snapshot["schema_version"] == "6.3.0"
    assert snapshot["asset_order"] == ["equity", "bond", "gold", "commodity"]
    assert snapshot["recommended"]["primary_model"] == "macro_factor"
    assert snapshot["recommended"]["sharpe_champion_full_report_only"] == "risk_parity"
    assert snapshot["governance"]["selection_uses_test"] is False
    assert snapshot["governance"]["deployment_allowed"] is False

    cycle = snapshot["cycle_tracking"]
    assert cycle["candidate_factor_count"] >= 150
    assert cycle["selected_factor_count"] >= 50
    assert len(cycle["factor_engine"]["selected_by_axis"]) == 8
    assert cycle["production_admitted_cycles"] == []
    assert "D2实算因子真实进入研究权重" in cycle["truth_boundary"]


def test_v63_macro_primary_has_positive_pretest_excess_and_ir() -> None:
    snapshot = v63.build_snapshot()
    macro = snapshot["allocation_models"]["macro_factor"]["metrics"]
    for split in ("train", "validation"):
        assert macro[split]["annual_excess_return"] > 0.0
        assert macro[split]["information_ratio"] > 0.0
        assert macro[split]["sharpe"] > macro[split]["benchmark_sharpe"]
    assert snapshot["allocation_models"]["risk_parity"]["metrics"]["validation"]["annual_excess_return"] < 0.0
    assert snapshot["recommended"]["selection_score_pretest_only"]["risk_parity"] < 0.0


def test_v63_cycle_views_feed_black_litterman_q() -> None:
    snapshot = v63.build_snapshot()
    diag = snapshot["allocation_models"]["black_litterman"]["current_diagnostics"]
    p = np.asarray(diag["view_matrix"], dtype=float)
    q = np.asarray(diag["view_q"], dtype=float)
    alpha = np.asarray(diag["cycle_alpha"], dtype=float)
    pi = np.asarray(diag["black_litterman"]["pi"], dtype=float)
    np.testing.assert_allclose(q, p @ pi + p @ alpha, atol=1.0e-12)
    state = diag["cycle_state"]
    assert set(state["selected_axis_scores"]) == set(v63.AXIS_ORDER)
    assert diag["factor_engine_selected_axes"]


def test_v63_selected_factor_rows_are_research_not_production() -> None:
    snapshot = v63.build_snapshot()
    rows = snapshot["cycle_tracking"]["factor_rows"]
    selected = [row for row in rows if row["enters_current_weight"] == "yes_research_D2_factor_selected"]
    assert len(selected) == snapshot["cycle_tracking"]["selected_factor_count"]
    assert all(row["production_admitted"] is False for row in rows)
    assert all("release_time" in row["pit_requirement"] for row in rows)


def test_v63_json_is_strict_and_deterministic() -> None:
    first = v63.build_snapshot()
    second = v63.build_snapshot()
    assert first["content_sha256"] == second["content_sha256"]
    encoded = json.dumps(first, ensure_ascii=False, sort_keys=True, allow_nan=False, default=v63._json_default)
    assert "NaN" not in encoded
