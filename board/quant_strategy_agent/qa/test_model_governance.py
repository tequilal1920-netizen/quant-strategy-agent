from __future__ import annotations

import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import model_governance_backend as governance


def test_model_governance_keeps_test_report_only() -> None:
    payload = governance.build_model_governance()
    assert payload["status"] == "ok"
    assert payload["release"] == "2026.07.27-scoped-controls-ai-cache-r21.2"
    assert payload["summary"]["model_count"] == 9
    assert payload["policy"]["selection"].endswith("sealed test report only")
    assert "test-set retuning" in payload["policy"]["prohibited"]
    assert payload["models"]["kline_memory"]["gate"] == "observe_only"
    assert payload["models"]["kline_memory"]["splits"]["train"]["sharpe"] == 0.0
    kline_audit = payload["models"]["kline_memory"]["robustness"]["cross_sectional_audit"]
    assert kline_audit["candidate_count"] == 12
    assert kline_audit["eligible_count"] == 0
    assert kline_audit["selection_uses_test"] is False
    assert kline_audit["best_rejected"]["train"]["sharpe"] < 0
    assert kline_audit["best_rejected"]["validation"]["sharpe"] > 1.5
    assert kline_audit["best_rejected"]["test_report_only"]["excess_annual_return"] < 0


def test_index_champion_exposes_failed_sealed_test_without_reselection() -> None:
    model = governance.build_model_governance()["models"]["index_enhancement"]
    assert model["engine"] == "index-enhancement/1.2-active-risk-shadow-audit"
    assert model["champion"] == "csi800_walkforward_ic_agent_v10"
    assert model["splits"]["train"]["sharpe"] > 0
    assert model["splits"]["validation"]["sharpe"] > 0
    assert model["splits"]["test"]["sharpe"] < 0
    assert model["gate"] == "review"
    shadow = model["robustness"]["post_test_shadow"]
    assert shadow["model"] == "index_active_risk_optimizer_v12"
    assert shadow["promotion_eligible"] is False
    assert shadow["selection_uses_test"] is False
    assert shadow["validation"]["information_ratio"] > 1.0
    assert shadow["test_diagnostic"]["information_ratio"] < 0.2


def test_sharpe_target_is_not_promotion_override() -> None:
    payload = governance.build_model_governance()
    assert payload["policy"]["target"] == "Sharpe 1.5 is an aspiration, not a promotion override"
    assert payload["models"]["asset_allocation"]["gate"] == "conditional"
    assert payload["models"]["portfolio_optimization"]["gate"] == "research_candidate"
