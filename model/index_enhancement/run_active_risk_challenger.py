"""Run only the benchmark-relative index-enhancement shadow challenger.

This entry point does not rebuild or overwrite the existing model family.  It
adds/replaces one explicitly named shadow model and updates its formal evidence
row after the point-in-time panel has been reconstructed.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BACKTEST_PATH = PROJECT_ROOT / "framework" / "backtest" / "run_v2_models.py"
MODEL_NAME = "index_active_risk_optimizer_v12"
UNIVERSE = "CSI800_ENH"


def _load_backtest_module() -> Any:
    spec = importlib.util.spec_from_file_location("formal_model_backtest", BACKTEST_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load formal backtest module: {BACKTEST_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _replace_leaderboard_row(path: Path, row: dict[str, Any]) -> None:
    existing: list[dict[str, Any]] = []
    fieldnames = list(row)
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            existing = list(reader)
            if reader.fieldnames:
                fieldnames = list(dict.fromkeys([*reader.fieldnames, *fieldnames]))
    existing = [
        item
        for item in existing
        if not (item.get("universe") == row["universe"] and item.get("model") == row["model"])
    ]
    existing.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing)


def run(db: Path, out_dir: Path, model_name: str = MODEL_NAME) -> dict[str, Any]:
    formal = _load_backtest_module()
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("pragma busy_timeout=120000")
    try:
        miner = formal.load_factor_miner(PROJECT_ROOT)
        panel, reason = formal.build_stock_panel(
            conn,
            miner,
            UNIVERSE,
            formal.START_DATE,
            formal.END_DATE,
        )
        if panel is None:
            raise RuntimeError(reason or "point-in-time panel is unavailable")
        panel, static_diagnostics = formal.add_ic_learned_alpha(panel)
        panel, walkforward_diagnostics = formal.add_walkforward_ic_alpha(panel)
        panel = formal.add_v11_alpha_scores(panel)
        config = formal.INDEX_ACTIVE_RISK_MODELS[model_name]
        returns, benchmark, nav_rows, signal_rows, solver_evidence = (
            formal.backtest_active_risk_optimizer(
                panel,
                config["score"],
                cost_rate=formal.COST_RATE,
                config=config["config"],
                safe_float=formal.safe_float,
            )
        )
        formal.write_model(
            conn,
            formal.RUN_ID,
            UNIVERSE,
            model_name,
            nav_rows,
            signal_rows,
            returns,
            benchmark,
        )
        metrics = formal.metrics_from_returns(returns, benchmark)
        split_rows = conn.execute(
            """select split_name,periods,annual_return,annual_volatility,sharpe,
            max_drawdown,excess_annual_return,information_ratio
            from metrics_by_split_year
            where run_id=? and universe=? and model_name=? and year='all'
            order by case split_name when 'train' then 1 when 'valid' then 2
            when 'test' then 3 else 4 end""",
            (formal.RUN_ID, UNIVERSE, model_name),
        ).fetchall()
    finally:
        conn.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    leaderboard_row = {
        "universe": UNIVERSE,
        "model": model_name,
        "status": "post_test_diagnostic_challenger",
        **metrics,
    }
    _replace_leaderboard_row(out_dir / "model_leaderboard.csv", leaderboard_row)
    payload = {
        "status": "ready",
        "research_status": "post_test_diagnostic_challenger",
        "promotion_eligible": False,
        "reason": (
            "The 2023-2026 interval had already been inspected before this "
            "architecture was specified; it is retained as diagnostic evidence only."
        ),
        "run_id": formal.RUN_ID,
        "universe": UNIVERSE,
        "model": model_name,
        "selection_uses_test": False,
        "source_alpha": config["score"],
        "split_metrics": [
            {
                "split": row[0],
                "periods": row[1],
                "annual_return": row[2],
                "annual_volatility": row[3],
                "sharpe": row[4],
                "max_drawdown": row[5],
                "excess_annual_return": row[6],
                "information_ratio": row[7],
            }
            for row in split_rows
        ],
        "full_metrics": metrics,
        "solver_evidence": solver_evidence,
        "alpha_diagnostics": {
            "static_ic": static_diagnostics,
            "walkforward_ic": walkforward_diagnostics,
        },
    }
    (out_dir / "index_active_risk_diagnostics.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        type=Path,
        default=PROJECT_ROOT / "database" / "research_warehouse.db",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=PROJECT_ROOT / "output" / "framework" / "backtest" / "model_outputs_formal",
    )
    parser.add_argument(
        "--model",
        choices=("index_active_risk_optimizer_v12", "index_active_risk_reliability_v13"),
        default=MODEL_NAME,
    )
    args = parser.parse_args()
    payload = run(args.db, args.out_dir, args.model)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "research_status": payload["research_status"],
                "model": payload["model"],
                "split_metrics": payload["split_metrics"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
