"""Governed v5.3.1 policy-relative challenger research.

This module deliberately leaves the deployed v5.2.2 snapshot untouched.  It
strengthens the first v5.3 research harness in four ways: the policy benchmark
is charged the same monthly rebalance costs, every pre-test calendar segment is
reported, the selection score rewards Sharpe improvement over the policy as
well as positive active return, and the retrospective test remains report-only.

The investable order is always ``equity, bond, gold, commodity``.  The internal
policy vector is therefore ``60%, 15%, 10%, 15%``; display code may reorder gold
and commodity but numerical code may not.  No result from this file authorises
deployment because the common commodity history starts in 2020 and the current
macro rows do not yet carry verified release-vintage timestamps.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Any, Mapping, Sequence

import numpy as np

import asset_allocation_v5 as v5
import asset_allocation_v53_research as baseline
from asset_data_authoritative_v51 import load_local_authoritative_execution_prices_v51


ASSETS = baseline.ASSETS
POLICY = baseline.POLICY
SCHEMA_VERSION = "asset-allocation-v5.3.1-research/1"


@dataclass(frozen=True)
class ResearchProtocolV531(baseline.ResearchProtocolV53):
    declared_trials: int = 13
    minimum_positive_pretest_year_fraction: float = 2.0 / 3.0
    minimum_sharpe_improvement: float = 0.0

    def validate(self) -> None:
        super().validate()
        if self.declared_trials != len(baseline.candidate_grid_v53()):
            raise ValueError("v531_declared_trial_count_mismatch")
        if not 0.5 <= self.minimum_positive_pretest_year_fraction <= 1.0:
            raise ValueError("v531_positive_year_fraction_invalid")


def _drawdown(values: np.ndarray) -> float:
    nav = np.cumprod(1.0 + values)
    return float(np.min(nav / np.maximum.accumulate(nav) - 1.0))


def metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"months": 0}
    strategy = np.asarray([row["net_return"] for row in rows], dtype=float)
    benchmark = np.asarray([row["benchmark_return"] for row in rows], dtype=float)
    active = strategy - benchmark
    count = len(rows)
    strategy_total = float(np.prod(1.0 + strategy))
    benchmark_total = float(np.prod(1.0 + benchmark))
    relative_nav = np.cumprod((1.0 + strategy) / np.maximum(1.0 + benchmark, 1.0e-12))
    strategy_vol = float(strategy.std(ddof=1) * math.sqrt(12.0)) if count > 1 else 0.0
    benchmark_vol = float(benchmark.std(ddof=1) * math.sqrt(12.0)) if count > 1 else 0.0
    tracking_error = float(active.std(ddof=1) * math.sqrt(12.0)) if count > 1 else 0.0
    strategy_sharpe = (
        float(strategy.mean() * 12.0 / strategy_vol) if strategy_vol > 1.0e-12 else None
    )
    benchmark_sharpe = (
        float(benchmark.mean() * 12.0 / benchmark_vol) if benchmark_vol > 1.0e-12 else None
    )
    information_ratio = (
        float(active.mean() * 12.0 / tracking_error) if tracking_error > 1.0e-12 else None
    )
    standardized = active / max(float(active.std(ddof=0)), 1.0e-12)
    active_skewness = float(np.mean((standardized - standardized.mean()) ** 3))
    active_kurtosis = float(np.mean((standardized - standardized.mean()) ** 4))
    return {
        "months": count,
        "annual_return": strategy_total ** (12.0 / count) - 1.0,
        "benchmark_annual_return": benchmark_total ** (12.0 / count) - 1.0,
        "annual_excess_return": float(relative_nav[-1] ** (12.0 / count) - 1.0),
        "annual_volatility": strategy_vol,
        "benchmark_annual_volatility": benchmark_vol,
        "sharpe": strategy_sharpe,
        "benchmark_sharpe": benchmark_sharpe,
        "sharpe_improvement": (
            None
            if strategy_sharpe is None or benchmark_sharpe is None
            else float(strategy_sharpe - benchmark_sharpe)
        ),
        "tracking_error": tracking_error,
        "information_ratio": information_ratio,
        "max_drawdown": _drawdown(strategy),
        "benchmark_max_drawdown": _drawdown(benchmark),
        "max_active_drawdown": float(
            np.min(relative_nav / np.maximum.accumulate(relative_nav) - 1.0)
        ),
        "positive_active_month_rate": float(np.mean(active > 0.0)),
        "average_turnover": float(np.mean([row["turnover"] for row in rows])),
        "annual_cost_drag": float(np.mean([row["cost"] for row in rows]) * 12.0),
        "benchmark_annual_cost_drag": float(
            np.mean([row["benchmark_cost"] for row in rows]) * 12.0
        ),
        "active_skewness": active_skewness,
        "active_kurtosis": active_kurtosis,
    }


def simulate(
    months: Sequence[str],
    returns: np.ndarray,
    spec: Mapping[str, Any],
    protocol: ResearchProtocolV531,
) -> dict[str, Any]:
    strategy_drifted = POLICY.copy()
    benchmark_drifted = POLICY.copy()
    rows: list[dict[str, Any]] = []
    weights: list[dict[str, Any]] = []
    costs = np.asarray(protocol.transaction_cost_bps, dtype=float) / 10000.0
    longest_horizon = max(spec.get("momentum_horizons") or (1,))
    start = max(protocol.lookback_months - 1, int(longest_horizon) - 1)
    for signal_index in range(start, len(returns) - 1):
        history = returns[: signal_index + 1]
        target, diagnostics = baseline._target(
            history, strategy_drifted, spec, protocol
        )
        realized = returns[signal_index + 1]
        month = months[signal_index + 1]
        strategy_change = target - strategy_drifted
        strategy_turnover = 0.5 * float(np.abs(strategy_change).sum())
        strategy_cost = float(costs @ np.abs(strategy_change))
        benchmark_change = POLICY - benchmark_drifted
        benchmark_turnover = 0.5 * float(np.abs(benchmark_change).sum())
        benchmark_cost = float(costs @ np.abs(benchmark_change))
        rows.append(
            {
                "month": month,
                "sample": baseline._sample(month, protocol),
                "gross_return": float(target @ realized),
                "net_return": float(target @ realized) - strategy_cost,
                "benchmark_gross_return": float(POLICY @ realized),
                "benchmark_return": float(POLICY @ realized) - benchmark_cost,
                "turnover": strategy_turnover,
                "cost": strategy_cost,
                "benchmark_turnover": benchmark_turnover,
                "benchmark_cost": benchmark_cost,
            }
        )
        weights.append(
            {
                "signal_month": months[signal_index],
                "realized_month": month,
                **{asset: float(target[pos]) for pos, asset in enumerate(ASSETS)},
                "diagnostics": diagnostics,
            }
        )
        strategy_drifted = baseline._drift(target, realized)
        benchmark_drifted = baseline._drift(POLICY, realized)
    split_metrics = {
        sample: metrics([row for row in rows if row["sample"] == sample])
        for sample in ("train", "validation", "test")
    }
    pretest_years = {
        year: metrics([row for row in rows if row["month"].startswith(year)])
        for year in ("2022", "2023", "2024")
    }
    return {
        "spec": dict(spec),
        "metrics": split_metrics,
        "pretest_calendar_years": pretest_years,
        "returns": rows,
        "weights": weights,
    }


def _positive(value: Any) -> bool:
    return value is not None and math.isfinite(float(value)) and float(value) > 0.0


def _active_psr(metric: Mapping[str, Any], annual_hurdle: float) -> float | None:
    n = int(metric.get("months") or 0)
    annual_ir = metric.get("information_ratio")
    if n < 3 or annual_ir is None:
        return None
    monthly_ir = float(annual_ir) / math.sqrt(12.0)
    hurdle = float(annual_hurdle) / math.sqrt(12.0)
    skewness = float(metric.get("active_skewness") or 0.0)
    kurtosis = float(metric.get("active_kurtosis") or 3.0)
    denominator = math.sqrt(
        max(1.0 - skewness * monthly_ir + (kurtosis - 1.0) * monthly_ir * monthly_ir / 4.0, 1.0e-12)
    )
    z_score = (monthly_ir - hurdle) * math.sqrt(n - 1.0) / denominator
    return float(NormalDist().cdf(z_score))


def select(
    results: Sequence[Mapping[str, Any]], protocol: ResearchProtocolV531
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    hurdle = (
        NormalDist().inv_cdf(1.0 - 1.0 / protocol.declared_trials)
        * math.sqrt(12.0 / 35.0)
    )
    leaderboard: list[dict[str, Any]] = []
    for result in results:
        train = result["metrics"]["train"]
        validation = result["metrics"]["validation"]
        yearly = result["pretest_calendar_years"]
        observed_years = [row for row in yearly.values() if int(row.get("months") or 0) >= 6]
        positive_years = sum(
            _positive(row.get("annual_excess_return"))
            and _positive(row.get("information_ratio"))
            for row in observed_years
        )
        positive_fraction = positive_years / max(len(observed_years), 1)
        eligible = all(
            (
                int(train.get("months") or 0) >= 12,
                int(validation.get("months") or 0) >= 12,
                _positive(train.get("annual_excess_return")),
                _positive(validation.get("annual_excess_return")),
                _positive(train.get("information_ratio")),
                _positive(validation.get("information_ratio")),
                float(train.get("sharpe_improvement") or -99.0)
                >= protocol.minimum_sharpe_improvement,
                float(validation.get("sharpe_improvement") or -99.0)
                >= protocol.minimum_sharpe_improvement,
                positive_fraction >= protocol.minimum_positive_pretest_year_fraction,
            )
        )
        train_ir = float(train.get("information_ratio") or -99.0)
        validation_ir = float(validation.get("information_ratio") or -99.0)
        train_sharpe_gain = float(train.get("sharpe_improvement") or -99.0)
        validation_sharpe_gain = float(validation.get("sharpe_improvement") or -99.0)
        score = (
            0.50 * min(train_ir, validation_ir)
            + 0.25 * min(train_sharpe_gain, validation_sharpe_gain)
            + 0.20 * positive_fraction
            - 0.20 * abs(train_ir - validation_ir)
            - 0.50 * float(validation.get("average_turnover") or 0.0)
        )
        combined_rows = [
            row for row in result["returns"] if row["sample"] in ("train", "validation")
        ]
        combined = metrics(combined_rows)
        leaderboard.append(
            {
                "id": result["spec"]["id"],
                "eligible": bool(eligible),
                "score": score,
                "positive_pretest_year_fraction": positive_fraction,
                "pretest_calendar_years": yearly,
                "combined_train_validation": combined,
                "active_psr_zero_hurdle": _active_psr(combined, 0.0),
                "active_psr_multiple_trial_hurdle": _active_psr(combined, hurdle),
                "multiple_trial_annual_ir_hurdle": hurdle,
                "train": train,
                "validation": validation,
                "test_report_only": result["metrics"]["test"],
            }
        )
    leaderboard.sort(key=lambda row: row["score"], reverse=True)
    selected_id = next((row["id"] for row in leaderboard if row["eligible"]), None)
    selected = next(
        (dict(result) for result in results if result["spec"]["id"] == selected_id),
        None,
    )
    return selected, leaderboard


def build_research(database: Path, protocol: ResearchProtocolV531) -> dict[str, Any]:
    protocol.validate()
    panel, lineage = load_local_authoritative_execution_prices_v51(database)
    price_months, prices, price_audit = v5.monthly_prices_v5(panel)
    months = price_months[1:]
    returns = prices[1:] / prices[:-1] - 1.0
    results = [
        simulate(months, returns, spec, protocol)
        for spec in baseline.candidate_grid_v53()
    ]
    winner, leaderboard = select(results, protocol)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "research_candidate" if winner else "no_candidate_passed",
        "asset_order": list(ASSETS),
        "policy_benchmark_internal": POLICY.tolist(),
        "policy_benchmark_display": {
            "equity": 0.60,
            "bond": 0.15,
            "commodity": 0.15,
            "gold": 0.10,
        },
        "protocol": asdict(protocol),
        "candidate_count": len(results),
        "selected_id": None if winner is None else winner["spec"]["id"],
        "selected_metrics": None if winner is None else winner["metrics"],
        "selected_pretest_calendar_years": (
            None if winner is None else winner["pretest_calendar_years"]
        ),
        "leaderboard": leaderboard,
        "results": results,
        "data": {
            "first_return_month": months[0],
            "last_return_month": months[-1],
            "return_months": len(months),
            "price_audit": price_audit,
            "lineage": lineage,
        },
        "governance": {
            "selection_uses_test": False,
            "test_role": "retrospective_report_only_not_pristine",
            "benchmark_cost_convention": "monthly policy rebalance with identical per-asset one-way costs",
            "deployment_allowed": False,
            "macro_pit_production_admitted": False,
            "reason": "short execution-proxy history and absent macro release-vintage timestamps require research-only status and future shadow holdout",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_research(Path(args.database), ResearchProtocolV531())
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": payload["status"],
                "selected_id": payload["selected_id"],
                "selected_metrics": payload["selected_metrics"],
                "output": str(output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
