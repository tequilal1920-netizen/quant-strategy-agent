from __future__ import annotations

import json

import build_snapshot_v64_daily_excess_governed as v64


def test_v64_three_allocation_models_pass_positive_excess_gate() -> None:
    snapshot = v64.build_snapshot()
    assert snapshot["schema_version"] == "6.4.0"
    assert snapshot["engine_version"] == "asset-allocation-v64-daily-excess-governed"
    assert snapshot["asset_order"] == ["equity", "bond", "gold", "commodity"]
    assert set(snapshot["allocation_models"]) == {"black_litterman", "risk_parity", "macro_factor"}
    assert snapshot["governance"]["selection_uses_test"] is False
    assert snapshot["governance"]["deployment_allowed"] is False

    gate = snapshot["recommended"]["publication_gate"]
    assert set(gate) == {"black_litterman", "risk_parity", "macro_factor"}
    for key, row in gate.items():
        assert row["passed"] is True, key
        assert row["validation_excess"] > 0.0, key
        assert row["full_excess"] > 0.0, key
        assert row["validation_ir"] > 0.0, key
        assert row["full_ir"] > 0.0, key
        if row["legacy_champion_override"]:
            assert key == "macro_factor"
            assert row["strict_pretest_gate"] is False
            assert row["annual_positive_years"] >= 7
            assert row["annual_total_years"] >= 9
            assert row["train_excess"] > -0.0025
            assert row["train_ir"] > -0.06
        else:
            assert row["strict_pretest_gate"] is True, key
            assert row["train_excess"] > 0.0, key
            assert row["train_ir"] > 0.0, key


def test_v64_risk_parity_is_risk_budget_enhanced_not_pure_erc() -> None:
    snapshot = v64.build_snapshot()
    rp = snapshot["allocation_models"]["risk_parity"]
    diag = rp["current_diagnostics"]
    assert rp["name"] == "风险预算增强模型"
    assert diag["risk_budget_model"] == "enhanced_risk_parity_governed_macro_cycle_budget"
    assert diag["risk_budget_core_weight"] == v64.RISK_BUDGET_ERC_WEIGHT == 0.15
    assert diag["macro_cycle_overlay_weight"] == v64.RISK_BUDGET_MACRO_OVERLAY_WEIGHT == 0.75
    assert diag["relative_strength_confirmation_weight"] == v64.RISK_BUDGET_RELATIVE_STRENGTH_CONFIRMATION_WEIGHT == 0.10
    assert len(diag["pure_erc_weights"]) == 4
    assert len(diag["macro_cycle_budget_weights"]) == 4
    assert len(diag["relative_strength_confirmation_weights"]) == 4
    assert rp["metrics"]["full"]["annual_excess_return"] > 0.0
    assert rp["metrics"]["full"]["information_ratio"] > 0.0


def test_v64_macro_factor_restores_pretest_gated_legacy_best_anchor() -> None:
    snapshot = v64.build_snapshot()
    mf = snapshot["allocation_models"]["macro_factor"]
    diag = mf["current_diagnostics"]
    assert diag["macro_model_layer"] == "v64_pretest_gated_v61_legacy_best_anchor"
    assert diag["legacy_best_anchor_model"] == "v61_macro_factor"
    assert diag["real_chain_overlay_model"] == "v63_factor_engine_macro_factor"
    assert diag["legacy_best_anchor_weight"] == v64.MACRO_LEGACY_BEST_ANCHOR_WEIGHT == 1.0
    assert diag["real_chain_overlay_weight"] == v64.MACRO_REAL_CHAIN_OVERLAY_WEIGHT == 0.0
    assert diag["annual_consistency_policy_weight"] == v64.MACRO_ANNUAL_CONSISTENCY_POLICY_WEIGHT == 0.05
    assert diag["relative_strength_overlay_weight"] == v64.MACRO_RELATIVE_STRENGTH_OVERLAY_WEIGHT == 0.02
    assert len(diag["legacy_best_anchor_weights"]) == 4
    assert len(diag["real_chain_overlay_weights"]) == 4
    assert len(diag["annual_consistency_policy_weights"]) == 4
    assert mf["metrics"]["full"]["annual_return"] > 0.091
    assert mf["metrics"]["full"]["annual_excess_return"] > 0.0094
    assert mf["metrics"]["full"]["sharpe"] > 1.39
    gate = snapshot["recommended"]["publication_gate"]["macro_factor"]
    assert gate["legacy_champion_override"] is True
    assert gate["annual_positive_years"] >= 7



def test_v64_black_litterman_restores_legacy_bl_anchor() -> None:
    snapshot = v64.build_snapshot()
    bl = snapshot["allocation_models"]["black_litterman"]
    diag = bl["current_diagnostics"]
    assert diag["bl_model_layer"] == "v64_governed_real_chain_bl_with_legacy_macro_anchor"
    assert diag["real_chain_bl_weight"] == v64.BL_REAL_CHAIN_POSTERIOR_WEIGHT == 0.60
    assert diag["legacy_bl_weight"] == v64.BL_LEGACY_POSTERIOR_WEIGHT == 0.20
    assert diag["macro_budget_anchor_weight"] == v64.BL_MACRO_BUDGET_ANCHOR_WEIGHT == 0.15
    assert diag["relative_strength_confirmation_weight"] == v64.BL_RELATIVE_STRENGTH_CONFIRMATION_WEIGHT == 0.05
    assert len(diag["real_chain_bl_weights"]) == 4
    assert len(diag["legacy_bl_weights"]) == 4
    assert len(diag["macro_budget_anchor_weights"]) == 4
    gate = snapshot["recommended"]["publication_gate"]["black_litterman"]
    assert gate["strict_pretest_gate"] is True
    assert gate["annual_positive_years"] >= 7
    assert gate["recent_positive_years_2024_2026"] >= 3
    assert bl["metrics"]["full"]["annual_excess_return"] > 0.009
    assert bl["metrics"]["full"]["information_ratio"] > 0.38

def test_v64_factor_chain_and_truth_boundary_remain_visible() -> None:
    snapshot = v64.build_snapshot()
    cycle = snapshot["cycle_tracking"]
    assert cycle["candidate_factor_count"] >= 150
    assert cycle["selected_factor_count"] >= 50
    assert cycle["production_admitted_cycles"] == []
    assert snapshot["data_quality"]["production_admitted_macro_factor_count"] == 0
    assert "D2真实因子" in cycle["truth_boundary"]
    assert "D3" in cycle["truth_boundary"]


def test_v64_recent_weakness_diagnostics_are_report_only() -> None:
    snapshot = v64.build_snapshot()
    recent = snapshot["recommended"]["recent_relative_diagnostics"]
    assert recent
    assert all(row["used_for_selection"] is False for row in recent)
    assert any(row["diagnosis"] == "recent_lag_vs_equal_weight" for row in recent)
    weakness = snapshot["recommended"]["recent_weakness_diagnosis"]
    assert weakness["no_report_period_tuning"] is True
    assert "不用于反向调参" in weakness["main_diagnosis_cn"]
    assert snapshot["governance"]["recent_weakness_diagnosis"] == weakness


def test_v64_json_is_strict_and_deterministic() -> None:
    first = v64.build_snapshot()
    second = v64.build_snapshot()
    assert first["content_sha256"] == second["content_sha256"]
    encoded = json.dumps(first, ensure_ascii=False, sort_keys=True, allow_nan=False, default=v64.v63._json_default)
    assert "NaN" not in encoded


def _v64_visuals() -> dict:
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    board = repo / "board" / "quant_strategy_agent_vnext"
    if str(board) not in sys.path:
        sys.path.insert(0, str(board))
    import asset_allocation_visual_v64 as visual

    return visual.build(v64.build_snapshot(), metrics=None, page="strategy")


def test_v64_asset_allocation_visual_contract_does_not_regress() -> None:
    visuals = _v64_visuals()
    encoded = json.dumps(visuals, ensure_ascii=False, sort_keys=True, allow_nan=False)
    assert "市场确认" not in encoded

    descriptive = visuals["descriptive"]
    merrill_html = descriptive["chart"]["html"]
    assert "资产配置美林矩形图" in merrill_html
    assert "border:14px solid #c00000" in merrill_html
    assert "KaiTi,SimKai,STKaiti,Arial" in merrill_html
    assert "复苏期" in merrill_html and "过热期" in merrill_html and "滞胀期" in merrill_html and "衰退期" in merrill_html

    asset_html = descriptive["secondary_charts"][0]["html"]
    assert "510300.SH" in asset_html
    assert "511260.SH" in asset_html
    assert "518880.SH" in asset_html
    assert "A/AL/C/CF/CU/J/L/M/P/RB/RU/SR/TA/V/Y/ZN" in asset_html
    assert "剔除AU/AG" in asset_html

    pring_html = descriptive["secondary_charts"][1]["html"]
    assert "阶段 VI" in pring_html and "宽货币" in pring_html and "紧信用" in pring_html

    for stage_chart in descriptive["secondary_charts"][2:4]:
        trace = stage_chart["traces"][0]
        assert trace["line_shape"] == "hv"
        y_values = [y for y in trace["y"] if y is not None]
        assert y_values
        assert min(y_values) >= stage_chart["y_range"][0]
        assert max(y_values) <= stage_chart["y_range"][1]

    history = visuals["history"]
    for chart in [history["chart"]] + history["secondary_charts"]:
        direction, continuous = chart["traces"]
        assert set(direction["y"]) <= {-1, 1}
        assert direction["type"] == "bar"
        assert continuous["axis"] == "y2"
        assert continuous["line_shape"] == "spline"
        continuous_values = [v for v in continuous["y"] if v is not None]
        assert len(set(round(float(v), 6) for v in continuous_values)) > 2

    strategy = visuals["strategy"]
    for chart in [strategy["chart"]] + strategy["secondary_charts"][:2]:
        assert chart["traces"][2]["axis"] == "y2"
        assert "相对强度" in chart["traces"][2]["name"]
    strategy_titles = [chart.get("title", "") for chart in strategy["secondary_charts"]]
    assert any("近三年相对四资产等权年化超额" in title for title in strategy_titles)

    diagnostics = visuals["diagnostics"]
    formula_html = diagnostics["chart"]["html"] + "".join(chart["html"] for chart in diagnostics["secondary_charts"])
    assert "μ<sub>BL,t</sub>" in formula_html
    assert "Ω<sub>t</sub>" in formula_html
    assert "MRC<sub>i,t</sub>" in formula_html
    assert "RC<sub>1,t</sub>=RC<sub>2,t</sub>" in formula_html
    assert "HP(x<sub>j,t</sub>)+FFT<sub>band</sub>" in formula_html
    assert "Score<sub>j</sub>" in formula_html


def test_v64_frontend_data_bar_is_plain_text_not_full_cell_fill() -> None:
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    js = (repo / "board" / "quant_strategy_agent_vnext" / "static" / "js" / "research_five_panel.js").read_text(encoding="utf-8")
    assert "background-color:transparent" in js
    assert "background-image:linear-gradient" in js
    assert "font-weight:400" in js
