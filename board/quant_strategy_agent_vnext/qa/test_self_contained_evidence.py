from __future__ import annotations

from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = APP_DIR / "data"


def test_remote_evidence_files_are_self_contained() -> None:
    factor = DATA_DIR / "factor_strategy_inverse_vol_v32_20260726.json"
    index = DATA_DIR / "index_active_risk_diagnostics.json"

    assert factor.is_file()
    assert factor.stat().st_size > 30_000_000
    assert index.is_file()
    assert index.stat().st_size > 250_000

    source = (APP_DIR / "research_evidence_backend.py").read_text(encoding="utf-8")
    assert 'FACTOR_RESULT = DATA_ROOT / "factor_strategy_inverse_vol_v32_20260726.json"' in source
    assert 'INDEX_SHADOW = DATA_ROOT / "index_active_risk_diagnostics.json"' in source
    assert "PROJECT_ROOT" not in source
