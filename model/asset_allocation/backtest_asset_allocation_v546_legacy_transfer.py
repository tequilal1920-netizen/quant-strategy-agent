"""Single post-v5.4.5 legacy-transfer challenger evaluation.

This is intentionally not merged into the v5.4.5 frozen candidate board.  It
is one separately labelled, fixed-mechanism challenger whose validation period
has already been observed by prior model generations.  It can diagnose whether
the old B06 mechanism transfers; it cannot earn a fresh blind-champion label.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np

import backtest_asset_allocation_v545_long as frozen
from asset_allocation_v53_stack import StackParametersV53
from asset_allocation_v541_stack import ASSET_ORDER_V541, POLICY_WEIGHTS_V541
from asset_allocation_v546_legacy_stack import allocate_relative_legacy_v546
from backtest_asset_allocation_v541_long import (
    LINEAR_COST_BPS_V541,
    NO_D3_CYCLES_V541,
    QUADRATIC_COST_V541,
    _drift,
    metrics_v541,
)


CHALLENGER_ID_V546 = "V546-LEGACY-B06-DIRECT-01"


def _simulate(panel: Mapping[str, Any], *, allow_test: bool) -> dict[str, Any]:
    months, returns = frozen._validate_panel(panel, allow_test=allow_test)
    parameters = StackParametersV53(
        market_view_scale_monthly=.0025,
        uncertainty_penalty=.075,
        active_l2_penalty=.01,
        macro_blend_weight=0.0,
    )
    previous = POLICY_WEIGHTS_V541.copy()
    benchmark_drifted = POLICY_WEIGHTS_V541.copy()
    linear = np.asarray(LINEAR_COST_BPS_V541) / 10000.0
    quadratic = np.asarray(QUADRATIC_COST_V541)
    macro = np.zeros((frozen.LOOKBACK_V545, 4))
    admission = np.zeros((frozen.LOOKBACK_V545, 4), dtype=bool)
    rows = []
    maximum_kkt = 0.0
    for signal_index in range(frozen.LOOKBACK_V545 - 1, len(returns) - 1):
        left = signal_index - frozen.LOOKBACK_V545 + 1
        diagnostics = allocate_relative_legacy_v546(
            returns[left : signal_index + 1],
            macro,
            admission,
            months[left : signal_index + 1],
            NO_D3_CYCLES_V541,
            previous,
            parameters,
            transaction_cost_bps=LINEAR_COST_BPS_V541,
            quadratic_cost=QUADRATIC_COST_V541,
        )
        target = np.asarray(diagnostics["weights"], dtype=float)
        kkt = float(diagnostics["optimizer"]["solver"]["maximum_kkt_residual"])
        if diagnostics["optimizer"]["status"] != "optimal" or kkt > 1.0e-7:
            raise RuntimeError("v546_legacy_monthly_solver_failed")
        maximum_kkt = max(maximum_kkt, kkt)
        realized = returns[signal_index + 1]
        change = target - previous
        benchmark_change = POLICY_WEIGHTS_V541 - benchmark_drifted
        cost = float(linear @ np.abs(change) + .5 * quadratic @ (change**2))
        benchmark_cost = float(linear @ np.abs(benchmark_change) + .5 * quadratic @ (benchmark_change**2))
        realized_month = months[signal_index + 1]
        rows.append(
            {
                "month": realized_month,
                "sample": frozen._sample(realized_month),
                "net_return": float(target @ realized) - cost,
                "benchmark_return": float(POLICY_WEIGHTS_V541 @ realized) - benchmark_cost,
                "turnover": .5 * float(np.abs(change).sum()),
                "cost": cost,
                "benchmark_cost": benchmark_cost,
                "maximum_kkt_residual": kkt,
            }
        )
        previous = _drift(target, realized)
        benchmark_drifted = _drift(POLICY_WEIGHTS_V541, realized)
    return {
        "metrics": {
            sample: metrics_v541([row for row in rows if row["sample"] == sample])
            for sample in ("train", "validation", "test")
        },
        "calendar_years": {
            year: metrics_v541([row for row in rows if row["month"].startswith(year)])
            for year in frozen.PRETEST_YEARS_V545
        },
        "returns": rows,
        "end_drifted_weights": previous.tolist(),
        "maximum_monthly_kkt_residual": maximum_kkt,
    }


def build_v546(panel: Mapping[str, Any]) -> dict[str, Any]:
    pretest = frozen._pretest_panel(panel)
    pretest_result = _simulate(pretest, allow_test=False)
    selector_payload = {
        "spec": {"id": CHALLENGER_ID_V546, "mode": "benchmark_relative"},
        "metrics": {
            "train": pretest_result["metrics"]["train"],
            "validation": pretest_result["metrics"]["validation"],
        },
        "pretest_calendar_years": pretest_result["calendar_years"],
    }
    passes_frozen_gate, board = frozen.select_pretest_v545(
        [selector_payload], "benchmark_relative"
    )
    # This test reporter is diagnostic only.  Its outcome can never change the
    # governance label or promote the challenger.
    try:
        full = _simulate(panel, allow_test=True)
        test_report = {
            "status": "retrospective_report_only_not_pristine",
            "metrics": full["metrics"]["test"],
            "selection_affected": False,
        }
    except Exception as error:
        test_report = {
            "status": "retrospective_reporter_failed_closed",
            "error_code": type(error).__name__,
            "selection_affected": False,
        }
    output = {
        "schema_version": "asset-allocation-v546-legacy-transfer-research/1.0",
        "challenger_id": CHALLENGER_ID_V546,
        "governance_label": "legacy_transfer_challenger_not_blind_champion",
        "passes_v545_pretest_gate": passes_frozen_gate == CHALLENGER_ID_V546,
        "selection_board": board,
        "pretest": pretest_result,
        "test_report": test_report,
        "selection_uses_test": False,
        "candidate_count": 1,
        "deployment_allowed": False,
        "promotion_blockers": [
            "validation_period_already_observed_by_prior_model_generations",
            "test_period_not_pristine",
            "D3_Wind_primary_and_second_source_crosscheck_not_complete",
            "future_pristine_shadow_holdout_not_complete",
        ],
    }
    output = frozen._strip_runtime_fields(output)
    output["content_sha256"] = frozen._canonical_hash(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    panel = json.loads(Path(args.panel).read_text(encoding="utf-8"))
    result = build_v546(panel)
    target = Path(args.output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(target)
    print(
        json.dumps(
            {
                "status": result["governance_label"],
                "passes_v545_pretest_gate": result["passes_v545_pretest_gate"],
                "content_sha256": result["content_sha256"],
                "deployment_allowed": result["deployment_allowed"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
