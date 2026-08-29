"""Migrate an existing K-line cross-sectional cache to train/validation governance."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from model.kline_memory_learning.cross_sectional_factor_study import (  # noqa: E402
    _selection_score_result,
)


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def repair(source: Path, board_output: Path) -> dict[str, Any]:
    payload = json.loads(source.read_text(encoding="utf-8"))
    integrity = dict(payload.get("integrity") or {})
    integrity["test_not_used_for_selection"] = True
    payload["integrity"] = integrity
    candidates: list[dict[str, Any]] = []
    matrix_by_key = {
        (row.get("universe"), row.get("frequency")): row
        for row in payload.get("matrix") or []
    }
    for universe, frequency_blocks in (payload.get("results") or {}).items():
        for frequency, block in frequency_blocks.items():
            metrics = block.get("metrics") or {}
            if not all(name in metrics for name in ("train", "valid", "test")):
                continue
            score = _selection_score_result(metrics, integrity, frequency)
            block["score"] = score
            diagnostics = block.setdefault("diagnostics", {})
            diagnostics["selection_uses_test"] = False
            diagnostics["test_usage"] = "sealed_report_only"
            matrix_row = matrix_by_key.get((universe, frequency))
            if matrix_row is not None:
                matrix_row["score"] = score["score"]
                matrix_row["grade"] = score["grade"]
                matrix_row["passed"] = score["passed"]
                matrix_row["selection_uses_test"] = False
            candidates.append(
                {
                    "universe": universe,
                    "frequency": frequency,
                    "score": score["score"],
                    "grade": score["grade"],
                    "passed": score["passed"],
                    "checks": score["checks"],
                    "train": {
                        key: metrics["train"].get(key)
                        for key in (
                            "periods",
                            "rank_ic",
                            "excess_annual_return",
                            "sharpe",
                            "max_drawdown",
                            "turnover",
                        )
                    },
                    "validation": {
                        key: metrics["valid"].get(key)
                        for key in (
                            "periods",
                            "rank_ic",
                            "excess_annual_return",
                            "sharpe",
                            "max_drawdown",
                            "turnover",
                        )
                    },
                    "test_report_only": score["test_report_only"],
                }
            )
    eligible = [candidate for candidate in candidates if candidate["passed"]]
    selected = max(eligible, key=lambda row: row["score"], default=None)
    audit = {
        "status": "validated_candidate" if selected else "observe_only_no_validated_strategy",
        "version": "kline-cross-sectional-governance/1.0",
        "source_version": "cross-sectional-factor-study/1.3-train-validation-promotion",
        "selection_policy": "train_validation_only_test_report_only",
        "selection_uses_test": False,
        "selected_candidate": selected,
        "candidate_count": len(candidates),
        "eligible_count": len(eligible),
        "candidates": sorted(candidates, key=lambda row: row["score"], reverse=True),
        "split": payload.get("split"),
        "integrity": integrity,
    }
    payload["version"] = "cross-sectional-factor-study/1.3-train-validation-promotion"
    payload["selection_policy"] = audit["selection_policy"]
    payload["selection_audit"] = audit
    _atomic_write(source, payload)
    board_output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(board_output, audit)
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=PROJECT_ROOT
        / "output"
        / "kline_memory_learning"
        / "cross_sectional_factor_study.json",
    )
    parser.add_argument(
        "--board-output",
        type=Path,
        default=PROJECT_ROOT
        / "board"
        / "quant_strategy_agent"
        / "data"
        / "kline_cross_sectional_audit.json",
    )
    args = parser.parse_args()
    result = repair(args.source, args.board_output)
    print(
        json.dumps(
            {
                "status": result["status"],
                "candidate_count": result["candidate_count"],
                "eligible_count": result["eligible_count"],
                "selected_candidate": result["selected_candidate"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
