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
    assert payload["release"] == "2026.08.13-institutional-visual-governed-r35.1"
    assert payload["summary"]["model_count"] == 9
    assert payload["policy"]["selection"].endswith("sealed test report only")
    assert "test-set retuning" in payload["policy"]["prohibited"]
    kline = payload["models"]["kline_memory"]
    assert kline["gate"] == "research_diagnostic"
    assert kline["splits"]["train"]["sharpe"] > 1.5
    assert kline["splits"]["validation"]["sharpe"] > 1.5
    assert kline["splits"]["test"]["sharpe"] < 0
    guard = kline["robustness"]["multiscale_release_guard"]
    assert guard["selection_uses_test"] is False
    assert guard["accepted_by_train_validation"] is True
    assert guard["release_approved"] is False
    assert guard["signal_uses_close_or_earlier"] is True
    assert guard["execution_is_next_trade_open"] is True


def test_index_champion_exposes_failed_sealed_test_without_reselection() -> None:
    model = governance.build_model_governance()["models"]["index_enhancement"]
    assert model["engine"] == "index-enhancement/1.3-bayesian-core-satellite-audit"
    assert model["champion"] == "csi800_walkforward_ic_agent_v10"
    assert model["splits"]["train"]["sharpe"] > 0
    assert model["splits"]["validation"]["sharpe"] > 0
    assert model["splits"]["test"]["sharpe"] < 0
    assert model["gate"] == "review"
    shadow = model["robustness"]["post_test_shadow"]
    assert shadow["model"] == "index_bayesian_stability_core_v16"
    assert shadow["promotion_eligible"] is False
    assert shadow["selection_uses_test"] is False
    assert shadow["validation"]["information_ratio"] > 1.5
    assert -0.11 < shadow["test_diagnostic"]["information_ratio"] < 0.0


def test_sharpe_target_is_not_promotion_override() -> None:
    payload = governance.build_model_governance()
    assert payload["policy"]["target"] == "Sharpe 1.5 is an aspiration, not a promotion override"
    assert payload["models"]["asset_allocation"]["gate"] == "conditional"
    assert payload["models"]["portfolio_optimization"]["gate"] == "post_test_diagnostic_candidate"
    asset = payload["models"]["asset_allocation"]
    assert asset["engine"] == "asset-allocation-research-v4.7-dual-objective"
    stable = asset["robustness"]["objective_champions"]["stable_absolute"]
    assert stable["strategy"] == "hrp"
    assert stable["validation_sharpe"] > 0.7
    assert stable["selection_uses_test"] is False


def test_portfolio_governance_reads_v24_solver_audit_snapshot() -> None:
    model = governance.build_model_governance()["models"]["portfolio_optimization"]
    assert model["engine"] == "portfolio-optimizer/2.6-cash-duration-segmentation"
    assert model["champion"].startswith("C272 risk_adjusted_trend")
    assert model["robustness"]["quality_status"] == "passed"
    solvers = model["robustness"]["solver_benchmark"]
    assert {row["solver"] for row in solvers} >= {
        "CLARABEL", "OSQP", "SCS", "SCIPY_SLSQP"
    }
    assert all(row.get("actual_solver") for row in solvers)
