from __future__ import annotations

from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]


def test_dense_visuals_show_the_full_view_without_scroll_frames() -> None:
    css = (APP_DIR / "static" / "css" / "research_evidence_dense.css").read_text(
        encoding="utf-8"
    )
    script = (
        APP_DIR / "static" / "js" / "research_evidence_dense.js"
    ).read_text(encoding="utf-8")

    assert "max-height: 390px" not in css
    assert "overflow: auto" not in css
    assert "min-width: 760px" not in css
    assert "table-layout: fixed" in css
    assert "rangeslider: { visible: false }" in script
    assert "dragmode: false" in script
    assert "scrollZoom: false" in script
    assert "responsive: true" in script
