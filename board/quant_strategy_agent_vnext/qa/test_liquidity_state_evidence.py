from __future__ import annotations

import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import model_governance_backend
import research_evidence_backend


LIQUIDITY_PAGES = (
    "home",
    "retail",
    "public",
    "etf",
    "margin",
    "primary",
    "private",
    "foreign",
)


def test_liquidity_state_contract_is_causal_and_compact() -> None:
    for page in LIQUIDITY_PAGES:
        payload = research_evidence_backend.build(f"liquidity:{page}")
        assert payload["status"] == "\u7814\u7a76\u8bca\u65ad"
        assert payload["champion"]["model"] == "月末资金后验与货币ETF"
        assert len(payload["layers"]) == 4
        assert "mechanism" not in payload
        assert set(payload["visuals"]) == {
            "descriptive",
            "history",
            "diagnostics",
            "strategy",
        }
        assert payload["descriptive"]["exact_model_input_count"] == 22
        assert payload["descriptive"]["effective_model_input_count"] == 18
        assert payload["descriptive"]["excluded_contract_count"] == 10
        assert payload["governance"]["exact_series_only"] is True
        assert payload["governance"]["database_read_only"] is True
        assert payload["governance"]["selection_uses_test"] is False
        assert payload["governance"]["promotion_eligible"] is False
        assert "candidate_evaluations" not in payload


def test_liquidity_governance_reports_the_real_validation_result() -> None:
    payload = model_governance_backend.build_model_governance()
    model = payload["models"]["liquidity_tracking"]
    assert payload["release"] == "2026.08.11-graph-first-governed-r34.1"
    assert model["engine"] == "liquidity-state/1.1-investable-cash-monthly"
    assert model["champion"] == "liquidity_monthly_investable_cash_v9"
    assert model["gate"] == "research_diagnostic"
    assert model["splits"]["validation"]["sharpe"] > 0
    assert model["splits"]["validation"]["information_ratio"] > 0
    assert model["robustness"]["selection_uses_test"] is False
    assert model["robustness"]["promotion_eligible"] is False
    assert model["robustness"]["production_snapshot_used_for_model"] is False
    assert model["robustness"]["effective_training_series"] == 18
