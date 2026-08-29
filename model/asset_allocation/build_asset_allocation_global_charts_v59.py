"""Build reference-style PNG charts for v5.9 three-asset equal-anchor model zoo."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from build_asset_allocation_global_charts_v55 import (
    _align_rows,
    _annual_table,
    _canonical_hash,
    _render_nav_chart,
    _render_table,
    _strategy_summary,
    _write_monthly_csv,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = PROJECT_ROOT / "board" / "quant_strategy_agent_vnext" / "data" / "asset_allocation_snapshot.json"
OUTPUT_DIR = PROJECT_ROOT / "output" / "asset_allocation_global_charts_v59_equal_anchor_model_zoo"
EXPECTED_SCHEMA = "5.9.0"


def _read_snapshot() -> dict[str, Any]:
    data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    if data.get("schema_version") != EXPECTED_SCHEMA:
        raise ValueError("asset_allocation_v59_snapshot_schema_mismatch")
    if data.get("asset_order") != ["equity", "bond", "commodity"]:
        raise ValueError("asset_allocation_v59_asset_order_mismatch")
    if data.get("removed_assets") != ["gold"]:
        raise ValueError("asset_allocation_v59_gold_not_removed")
    if data.get("recommended", {}).get("primary_model") != "active_rotation":
        raise ValueError("asset_allocation_v59_recommended_model_mismatch")
    return data


def _rows(model: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"month": str(row["month"]), "net_return": float(row["net_return"])} for row in model.get("returns") or []]


def build() -> dict[str, Any]:
    snapshot = _read_snapshot()
    models = snapshot["allocation_models"]
    equal = snapshot["benchmarks"]["equal_weight_3_assets"]
    equal_rows = _rows(equal)
    strategies = [
        ("01_active_rotation", "等权锚主动轮动", models["active_rotation"]),
        ("02_BL", "BL", models["black_litterman"]),
        ("03_risk_parity", "风险平价", models["risk_parity"]),
        ("04_all_weather", "全天候", models["all_weather"]),
        ("05_macro_factor", "宏观因子", models["macro_factor"]),
    ]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    strategy_frames: dict[str, pd.DataFrame] = {}
    nav_frames: dict[str, pd.DataFrame] = {}
    audit: dict[str, Any] = {
        "schema_version": "asset-allocation-global-performance-charts-v59-model-zoo/1.0",
        "input_snapshot": str(SNAPSHOT_PATH),
        "snapshot_content_sha256": snapshot["content_sha256"],
        "asset_order": snapshot["asset_order"],
        "removed_assets": snapshot.get("removed_assets") or [],
        "display_benchmark": snapshot["display_benchmark"],
        "policy_benchmark": snapshot["policy_benchmark"],
        "recommended": snapshot["recommended"],
        "outputs": {},
    }

    for prefix, label, model in strategies:
        frame = _align_rows(_rows(model), equal_rows)
        strategy_frames[label] = frame
        table_rows = _annual_table(frame)
        table_path = OUTPUT_DIR / f"{prefix}_annual_table.png"
        nav_path = OUTPUT_DIR / f"{prefix}_nav_relative_strength.png"
        nav_frames[label] = _render_nav_chart(frame, label, nav_path)
        _render_table(table_rows, table_path)
        audit["outputs"][label] = {
            "annual_table_png": str(table_path),
            "nav_relative_strength_png": str(nav_path),
            "summary": _strategy_summary(frame),
            "annual_table_rows": table_rows,
            "current_weights": model.get("current_weights"),
            "governance": model.get("governance"),
        }

    _write_monthly_csv(strategy_frames, OUTPUT_DIR / "all_strategy_monthly_returns_equal_3_display.csv")
    with (OUTPUT_DIR / "all_strategy_nav_relative_strength.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        import csv

        writer = csv.writer(handle)
        writer.writerow(["strategy", "date", "strategy_nav", "equal_weight_nav", "relative_strength"])
        for key, frame in nav_frames.items():
            for row in frame.itertuples(index=False):
                writer.writerow([key, row.date, row.strategy_nav, row.equal_weight_nav, row.relative_strength])

    audit["output_content_sha256"] = _canonical_hash({k: v for k, v in audit.items() if k != "output_content_sha256"})
    (OUTPUT_DIR / "chart_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return audit


def main() -> int:
    audit = build()
    print(json.dumps({"status": "ok", "output_dir": str(OUTPUT_DIR), "outputs": audit["outputs"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
