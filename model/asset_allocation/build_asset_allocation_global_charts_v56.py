"""Build reference-style PNG charts for the v5.6 asset-allocation block."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from build_asset_allocation_global_charts_v55 import (
    CN_ANNUAL_TABLE,
    CN_AUDIT,
    CN_MONTHLY_RETURNS,
    CN_NAV_CSV,
    CN_NAV_RELATIVE,
    OUTPUT_DIR as _V55_OUTPUT_DIR,
    _align_rows,
    _canonical_hash,
    _render_nav_chart,
    _render_table,
    _strategy_summary,
    _write_monthly_csv,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = PROJECT_ROOT / "board" / "quant_strategy_agent_vnext" / "data" / "asset_allocation_snapshot.json"
OUTPUT_DIR = PROJECT_ROOT / "output" / "asset_allocation_global_charts_v56"
EXPECTED_SCHEMA = "5.6.0"
EXPECTED_SNAPSHOT_HASH = "61A01408451012A95D24D1A4F8D38720723B1EEC45B087A94077A8424210F327"


def _read_snapshot() -> dict[str, Any]:
    data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    if data.get("schema_version") != EXPECTED_SCHEMA:
        raise ValueError("asset_allocation_v56_snapshot_schema_mismatch")
    if data.get("content_sha256") != EXPECTED_SNAPSHOT_HASH:
        raise ValueError("asset_allocation_v56_snapshot_hash_mismatch")
    return data


def _rows(model: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "month": str(row["month"]),
            "net_return": float(row["net_return"]),
        }
        for row in model.get("returns") or []
    ]


def build() -> dict[str, Any]:
    snapshot = _read_snapshot()
    models = snapshot["allocation_models"]
    equal = snapshot["benchmarks"]["equal_weight_25"]
    equal_rows = _rows(equal)
    strategies = [
        ("01_BL", "BL", models["black_litterman"]),
        ("02_风险平价", "风险平价", models["risk_parity"]),
        ("03_全天候", "全天候", models["all_weather"]),
        ("04_宏观因子", "宏观因子", models["macro_factor"]),
    ]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    strategy_frames: dict[str, pd.DataFrame] = {}
    nav_frames: dict[str, pd.DataFrame] = {}
    audit: dict[str, Any] = {
        "schema_version": "asset-allocation-global-performance-charts-v56/1.0",
        "input_snapshot": str(SNAPSHOT_PATH),
        "snapshot_content_sha256": snapshot["content_sha256"],
        "display_benchmark": snapshot["display_benchmark"],
        "policy_benchmark": snapshot["policy_benchmark"],
        "recommended": snapshot["recommended"],
        "outputs": {},
    }

    for prefix, label, model in strategies:
        frame = _align_rows(_rows(model), equal_rows)
        strategy_frames[label] = frame
        table_rows = __import__("build_asset_allocation_global_charts_v55")._annual_table(frame)
        table_path = OUTPUT_DIR / f"{prefix}_{CN_ANNUAL_TABLE}.png"
        nav_path = OUTPUT_DIR / f"{prefix}_{CN_NAV_RELATIVE}.png"
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

    _write_monthly_csv(strategy_frames, OUTPUT_DIR / CN_MONTHLY_RETURNS)
    with (OUTPUT_DIR / CN_NAV_CSV).open("w", encoding="utf-8-sig", newline="") as handle:
        import csv

        writer = csv.writer(handle)
        writer.writerow(["strategy", "date", "strategy_nav", "equal_weight_nav", "relative_strength"])
        for key, frame in nav_frames.items():
            for row in frame.itertuples(index=False):
                writer.writerow([key, row.date, row.strategy_nav, row.equal_weight_nav, row.relative_strength])

    audit["output_content_sha256"] = _canonical_hash({k: v for k, v in audit.items() if k != "output_content_sha256"})
    (OUTPUT_DIR / CN_AUDIT).write_text(json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return audit


def main() -> int:
    audit = build()
    print(json.dumps({"status": "ok", "output_dir": str(OUTPUT_DIR), "outputs": audit["outputs"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
