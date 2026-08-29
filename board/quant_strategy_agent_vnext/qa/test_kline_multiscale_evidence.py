from __future__ import annotations

import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import model_governance_backend
import research_evidence_backend


def test_kline_four_panel_uses_the_frozen_dual_model_result() -> None:
    payload = research_evidence_backend.build("technical:kline")
    assert payload["module"] == "kline"
    assert len(payload["layers"]) == 4
    assert "mechanism" not in payload
    assert set(payload["visuals"]) == {
        "descriptive",
        "history",
        "diagnostics",
        "strategy",
    }
    assert len(payload["visuals"]["descriptive"]["table"]["rows"]) >= 10

    splits = payload["visuals"]["diagnostics"]["table"]["rows"]
    assert len(splits) >= 12
    assert len({row["model"] for row in splits}) >= 3
    llm_rows = [row for row in splits if row["model"] == "模型二：LLM记忆多周期"]
    pure_rows = [row for row in splits if row["model"] == "模型一：纯技术信号栈"]
    full_rows = [row for row in splits if row["model"] == "模型三：全历史低频拟合"]
    assert llm_rows[0]["sharpe"] > 1.5
    assert llm_rows[1]["sharpe"] > 1.5
    assert llm_rows[2]["sharpe"] < 0
    assert pure_rows[0]["sharpe"] > 1.5
    assert pure_rows[1]["sharpe"] > 1.5
    assert pure_rows[2]["sharpe"] < 0
    full_diagnostic = next(row for row in full_rows if row["split"] == "全样本诊断")
    assert full_diagnostic["periods"] >= 120
    assert full_diagnostic["sharpe"] > 0

    governance = payload["governance"]
    assert governance["selection_uses_test"] is False
    assert governance["accepted_by_train_validation"] is True
    assert governance["release_approved"] is False
    assert governance["pure_technical_selection_uses_test"] is False
    assert governance["pure_technical_release_approved"] is False
    assert governance["pure_technical_candidate"]
    assert governance["full_history_sample_split_used"] is False
    assert governance["full_history_holdout_validation_claimed"] is False
    assert governance["full_history_candidate"]
    strategy_traces = payload["visuals"]["strategy"]["chart"]["traces"]
    assert len(strategy_traces) == 1
    assert strategy_traces[0]["x"]
    assert "candidate_evaluations" not in payload


def test_kline_governance_blocks_the_failed_sealed_test() -> None:
    payload = model_governance_backend.build_model_governance()
    model = payload["models"]["kline_memory"]
    assert payload["release"] == "2026.08.17-technical-full-history-fit-governed-r38.1"
    assert "technical-signal-stack/1.0" in model["engine"]
    assert "kline-multiscale-expert/1.6" in model["engine"]
    assert "technical-signal-stack/1.1" in model["engine"]
    assert model["gate"] == "research_diagnostic"
    assert model["splits"]["train"]["sharpe"] > 1.5
    assert model["splits"]["validation"]["sharpe"] > 1.5
    assert model["splits"]["test"]["sharpe"] < 0

    guard = model["robustness"]["multiscale_release_guard"]
    assert guard["selection_uses_test"] is False
    assert guard["release_approved"] is False
    assert guard["deployment_candidate"] is None
    assert guard["research_candidate"]

    pure_guard = model["robustness"]["pure_technical_release_guard"]
    assert pure_guard["selection_uses_test"] is False
    assert pure_guard["release_approved"] is False
    assert pure_guard["framework_family_count"] >= 6
    assert pure_guard["sealed_test"]["sharpe"] < 0