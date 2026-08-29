"""Isolated research runner for the v5.3.4 explicit risk-anchor correction."""

from __future__ import annotations

import json
from pathlib import Path

import backtest_asset_allocation_v53_stack as base
import backtest_asset_allocation_v533_stack as runner
import asset_allocation_v533_stack as stack
from asset_allocation_v534_stack import truth_gated_risk_budget_v534


SCHEMA = "asset-allocation-v5.3.4-stack-backtest/1"


def build_research_v534(database: Path, protocol: base.BacktestProtocolV53):
    original = stack._truth_gated_risk_budget
    stack._truth_gated_risk_budget = truth_gated_risk_budget_v534
    try:
        payload = runner.build_research_v533(database, protocol)
    finally:
        stack._truth_gated_risk_budget = original
    payload["schema_version"] = SCHEMA
    payload["governance"]["risk_anchor_negative_RC_projection_applied"] = False
    payload["governance"]["research_runner_uses_scoped_hook"] = True
    payload["governance"]["production_builder_must_use_explicit_v534_calls"] = True
    return payload


def main() -> int:
    arguments = base.parse_args()
    payload = build_research_v534(Path(arguments.database), base.BacktestProtocolV53())
    output = Path(arguments.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": payload["status"],
                "selected_ids_pretest": payload["selected_ids_pretest"],
                "selected_metrics": {
                    mode: None if report is None else report["metrics"]
                    for mode, report in payload["selected_reports_with_retrospective_test_attached_after_freeze"].items()
                },
                "output": str(output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
