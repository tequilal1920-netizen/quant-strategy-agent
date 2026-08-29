from __future__ import annotations

import json
import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import research_evidence_backend as evidence


def test_index_page_uses_only_selected_bayesian_core_satellite_result() -> None:
    payload = evidence.build("factorlab:strategy")
    assert payload["status"] == "diagnostic_only"
    assert payload["champion"]["name"] == "\u6162\u53d8\u91cf\u7a33\u5b9a\u589e\u5f3a"
    assert payload["governance"]["selection_uses_test"] is False
    assert payload["governance"]["promotion_eligible"] is False
    metrics = {row["split"]: row for row in payload["metrics"]}
    assert metrics["validation"]["information_ratio"] > 1.5
    assert metrics["test"]["information_ratio"] > -0.11


def test_index_page_has_four_dense_data_visuals_without_placeholder_rows() -> None:
    payload = evidence.build("factorlab:strategy")
    visuals = payload["visuals"]
    assert set(visuals) == {"descriptive", "history", "diagnostics", "strategy"}
    factor_rows = visuals["descriptive"]["table"]["rows"]
    assert len(factor_rows) >= 5
    assert all("_v" not in str(row["factor"]) for row in factor_rows)
    assert len(visuals["history"]["chart"]["traces"]) == 3
    assert len(visuals["diagnostics"]["table"]["rows"]) == 11
    assert len(visuals["strategy"]["table"]["rows"]) >= 10
    json.dumps(payload, ensure_ascii=False, allow_nan=False)


def test_index_research_links_are_direct_pdf_files() -> None:
    payload = evidence.build("factorlab:strategy")
    links = [row["url"] for row in payload["references"]]
    assert len(links) >= 4
    assert all(link.lower().endswith(".pdf") for link in links)
