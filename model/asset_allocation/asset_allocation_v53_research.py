"""Pre-registered v5.3 research harness for policy-relative allocation.

The module is intentionally isolated from the deployed v5.2.2 engine.  It reads
the local warehouse in read-only mode, constructs the governed four-asset panel,
and evaluates a small, explicit candidate family against the 60/15/10/15
internal policy vector.  Retrospective test observations are never used to rank,
filter, scale, or choose parameters.

This first implementation is a research gate, not a release switch.  It keeps
the complete v5 cycle/BL/risk-budget machinery available for the eventual model
snapshot while testing whether a causal active overlay can actually add value.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

import asset_allocation_v5 as v5
from asset_allocation_v52 import POLICY_BENCHMARK_WEIGHTS_V52
from asset_data_authoritative_v51 import load_local_authoritative_execution_prices_v51


ASSETS = ("equity", "bond", "gold", "commodity")
POLICY = np.asarray(POLICY_BENCHMARK_WEIGHTS_V52, dtype=float)
SCHEMA_VERSION = "asset-allocation-v5.3-research/1"


@dataclass(frozen=True)
class ResearchProtocolV53:
    train_end: str = "202312"
    validation_end: str = "202412"
    lookback_months: int = 24
    transaction_cost_bps: tuple[float, float, float, float] = (5.0, 2.0, 5.0, 6.0)
    active_bands: tuple[float, float, float, float] = (0.10, 0.05, 0.03, 0.05)
    max_active_share: float = 0.10
    max_one_way_turnover: float = 0.08
    selection_uses_test: bool = False

    def validate(self) -> None:
        if tuple(ASSETS) != ("equity", "bond", "gold", "commodity"):
            raise ValueError("v53_asset_order_changed")
        if not np.allclose(POLICY, (0.60, 0.15, 0.10, 0.15), atol=1.0e-12):
            raise ValueError("v53_policy_order_changed")
        if self.selection_uses_test:
            raise ValueError("v53_test_selection_forbidden")
        if self.lookback_months < 12:
            raise ValueError("v53_lookback_too_short")


def candidate_grid_v53() -> tuple[dict[str, Any], ...]:
    """Small pre-registered grid; no result-dependent candidate generation."""

    candidates: list[dict[str, Any]] = [
        {
            "id": "V53-POLICY",
            "family": "policy_hold",
            "momentum_horizons": (),
            "momentum_weights": (),
            "risk_scale": 0.0,
            "active_scale": 0.0,
            "volatility_penalty": 0.0,
        }
    ]
    # The two horizon sets preserve the causal multi-horizon mechanism of B12,
    # but operate as bounded active tilts around the user's policy benchmark.
    horizon_sets = (
        ((3, 6, 12), (0.20, 0.35, 0.45)),
        ((1, 3, 6, 12), (0.10, 0.20, 0.30, 0.40)),
    )
    for h_index, (horizons, horizon_weights) in enumerate(horizon_sets, 1):
        for active_scale in (0.025, 0.050, 0.075):
            for volatility_penalty in (0.0, 0.25):
                candidates.append(
                    {
                        "id": f"V53-MH{h_index}-A{int(active_scale * 1000):03d}-V{int(volatility_penalty * 100):02d}",
                        "family": "b12_active_multi_horizon",
                        "momentum_horizons": horizons,
                        "momentum_weights": horizon_weights,
                        "risk_scale": 1.0,
                        "active_scale": active_scale,
                        "volatility_penalty": volatility_penalty,
                    }
                )
    return tuple(candidates)


def _bounded_simplex(values: np.ndarray, lower: np.ndarray, upper: np.ndarray) -> np.ndarray:
    output = np.asarray(values, dtype=float).copy()
    for _ in range(200):
        output = np.minimum(np.maximum(output, lower), upper)
        gap = 1.0 - float(output.sum())
        if abs(gap) <= 1.0e-12:
            break
        room = upper - output if gap > 0.0 else output - lower
        total = float(room.sum())
        if total <= 1.0e-14:
            raise RuntimeError("v53_bounded_simplex_infeasible")
        output += gap * room / total
    if abs(float(output.sum()) - 1.0) > 1.0e-9:
        raise RuntimeError("v53_bounded_simplex_failed")
    return output


def _rank_score(values: np.ndarray) -> np.ndarray:
    order = np.argsort(np.asarray(values, dtype=float), kind="mergesort")
    output = np.empty(len(order), dtype=float)
    if len(order) == 1:
        output[order[0]] = 0.0
        return output
    for rank, index in enumerate(order):
        output[index] = -1.0 + 2.0 * rank / (len(order) - 1.0)
    return output


def _drift(weights: np.ndarray, realized: np.ndarray) -> np.ndarray:
    value = np.asarray(weights, dtype=float) * (1.0 + np.asarray(realized, dtype=float))
    return value / max(float(value.sum()), 1.0e-12)


def _target(
    history: np.ndarray,
    drifted: np.ndarray,
    spec: Mapping[str, Any],
    protocol: ResearchProtocolV53,
) -> tuple[np.ndarray, dict[str, Any]]:
    if spec["family"] == "policy_hold":
        return POLICY.copy(), {"signal": [0.0] * 4, "raw_active": [0.0] * 4}
    annual_vol = np.maximum(np.std(history[-12:], axis=0, ddof=1) * math.sqrt(12.0), 0.02)
    score = np.zeros(4)
    for horizon, coefficient in zip(spec["momentum_horizons"], spec["momentum_weights"]):
        compound = np.prod(1.0 + history[-int(horizon) :], axis=0) - 1.0
        score += float(coefficient) * compound / (
            annual_vol * math.sqrt(float(horizon) / 12.0)
        )
    ranked_signal = _rank_score(score)
    volatility_rank = _rank_score(np.log(annual_vol))
    signal = ranked_signal - float(spec["volatility_penalty"]) * volatility_rank
    signal -= float(signal.mean())
    scale_denominator = max(float(np.sum(np.abs(signal))) / 2.0, 1.0e-12)
    raw_active = float(spec["active_scale"]) * signal / scale_denominator
    lower = POLICY - np.asarray(protocol.active_bands, dtype=float)
    upper = POLICY + np.asarray(protocol.active_bands, dtype=float)
    raw = _bounded_simplex(POLICY + raw_active, lower, upper)
    active_share = 0.5 * float(np.abs(raw - POLICY).sum())
    if active_share > protocol.max_active_share:
        raw = POLICY + (raw - POLICY) * protocol.max_active_share / active_share
    turnover = 0.5 * float(np.abs(raw - drifted).sum())
    if turnover > protocol.max_one_way_turnover:
        raw = drifted + (raw - drifted) * protocol.max_one_way_turnover / turnover
        raw = _bounded_simplex(raw, lower, upper)
    return raw, {
        "signal": signal.tolist(),
        "raw_active": raw_active.tolist(),
        "active_share": 0.5 * float(np.abs(raw - POLICY).sum()),
    }


def _sample(month: str, protocol: ResearchProtocolV53) -> str:
    if month <= protocol.train_end:
        return "train"
    if month <= protocol.validation_end:
        return "validation"
    return "test"


def _metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"months": 0}
    strategy = np.asarray([row["net_return"] for row in rows], dtype=float)
    benchmark = np.asarray([row["benchmark_return"] for row in rows], dtype=float)
    active = strategy - benchmark
    total = float(np.prod(1.0 + strategy))
    benchmark_total = float(np.prod(1.0 + benchmark))
    annual_return = total ** (12.0 / len(rows)) - 1.0
    benchmark_annual = benchmark_total ** (12.0 / len(rows)) - 1.0
    volatility = float(strategy.std(ddof=1) * math.sqrt(12.0)) if len(rows) > 1 else 0.0
    tracking_error = float(active.std(ddof=1) * math.sqrt(12.0)) if len(rows) > 1 else 0.0
    nav = np.cumprod(1.0 + strategy)
    relative_nav = np.cumprod((1.0 + strategy) / np.maximum(1.0 + benchmark, 1.0e-12))
    return {
        "months": len(rows),
        "annual_return": annual_return,
        "benchmark_annual_return": benchmark_annual,
        "annual_excess_return": float(relative_nav[-1] ** (12.0 / len(rows)) - 1.0),
        "annual_volatility": volatility,
        "sharpe": float(strategy.mean() * 12.0 / volatility) if volatility > 1.0e-12 else None,
        "tracking_error": tracking_error,
        "information_ratio": float(active.mean() * 12.0 / tracking_error) if tracking_error > 1.0e-12 else None,
        "max_drawdown": float(np.min(nav / np.maximum.accumulate(nav) - 1.0)),
        "max_active_drawdown": float(
            np.min(relative_nav / np.maximum.accumulate(relative_nav) - 1.0)
        ),
        "positive_active_month_rate": float(np.mean(active > 0.0)),
        "average_turnover": float(np.mean([row["turnover"] for row in rows])),
        "annual_cost_drag": float(np.mean([row["cost"] for row in rows]) * 12.0),
    }


def simulate(
    months: Sequence[str],
    returns: np.ndarray,
    spec: Mapping[str, Any],
    protocol: ResearchProtocolV53,
) -> dict[str, Any]:
    drifted = POLICY.copy()
    rows: list[dict[str, Any]] = []
    weights: list[dict[str, Any]] = []
    costs = np.asarray(protocol.transaction_cost_bps, dtype=float) / 10000.0
    start = max(protocol.lookback_months - 1, max(spec.get("momentum_horizons") or (1,)) - 1)
    for signal_index in range(start, len(returns) - 1):
        history = returns[: signal_index + 1]
        target, diagnostics = _target(history, drifted, spec, protocol)
        realized = returns[signal_index + 1]
        month = months[signal_index + 1]
        delta = target - drifted
        turnover = 0.5 * float(np.abs(delta).sum())
        cost = float(costs @ np.abs(delta))
        gross = float(target @ realized)
        benchmark_return = float(POLICY @ realized)
        rows.append(
            {
                "month": month,
                "sample": _sample(month, protocol),
                "gross_return": gross,
                "net_return": gross - cost,
                "benchmark_return": benchmark_return,
                "turnover": turnover,
                "cost": cost,
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
        drifted = _drift(target, realized)
    metrics = {
        sample: _metrics([row for row in rows if row["sample"] == sample])
        for sample in ("train", "validation", "test")
    }
    return {"spec": dict(spec), "metrics": metrics, "returns": rows, "weights": weights}


def _finite_positive(metrics: Mapping[str, Any], key: str) -> bool:
    value = metrics.get(key)
    return value is not None and math.isfinite(float(value)) and float(value) > 0.0


def select(results: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    leaderboard: list[dict[str, Any]] = []
    for result in results:
        train = result["metrics"]["train"]
        validation = result["metrics"]["validation"]
        eligible = all(
            (
                int(train.get("months") or 0) >= 12,
                int(validation.get("months") or 0) >= 12,
                _finite_positive(train, "annual_excess_return"),
                _finite_positive(validation, "annual_excess_return"),
                _finite_positive(train, "information_ratio"),
                _finite_positive(validation, "information_ratio"),
            )
        )
        train_ir = float(train.get("information_ratio") or -99.0)
        validation_ir = float(validation.get("information_ratio") or -99.0)
        train_sharpe = float(train.get("sharpe") or -99.0)
        validation_sharpe = float(validation.get("sharpe") or -99.0)
        score = (
            min(train_ir, validation_ir)
            + 0.25 * min(train_sharpe, validation_sharpe)
            - 0.25 * abs(train_ir - validation_ir)
            - 0.50 * float(validation.get("average_turnover") or 0.0)
        )
        leaderboard.append(
            {
                "id": result["spec"]["id"],
                "eligible": eligible,
                "score": score,
                "train": train,
                "validation": validation,
                "test_report_only": result["metrics"]["test"],
            }
        )
    leaderboard.sort(key=lambda item: item["score"], reverse=True)
    eligible_ids = {item["id"] for item in leaderboard if item["eligible"]}
    winner = next(
        (dict(result) for item in leaderboard for result in results if item["id"] in eligible_ids and result["spec"]["id"] == item["id"]),
        None,
    )
    return winner, leaderboard


def build_research(database: Path, protocol: ResearchProtocolV53) -> dict[str, Any]:
    protocol.validate()
    panel, lineage = load_local_authoritative_execution_prices_v51(database)
    price_months, prices, price_audit = v5.monthly_prices_v5(panel)
    months = price_months[1:]
    returns = prices[1:] / prices[:-1] - 1.0
    results = [simulate(months, returns, spec, protocol) for spec in candidate_grid_v53()]
    winner, leaderboard = select(results)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "research_candidate" if winner else "no_candidate_passed",
        "asset_order": list(ASSETS),
        "policy_benchmark_internal": POLICY.tolist(),
        "policy_benchmark_display": {"equity": 0.60, "bond": 0.15, "commodity": 0.15, "gold": 0.10},
        "protocol": asdict(protocol),
        "candidate_count": len(results),
        "selected_id": None if winner is None else winner["spec"]["id"],
        "selected_metrics": None if winner is None else winner["metrics"],
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
            "deployment_allowed": False,
            "reason": "research harness must be integrated with complete v5 cycle/BL/risk-budget stack and future shadow holdout before release",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_research(Path(args.database), ResearchProtocolV53())
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": payload["status"],
                "candidate_count": payload["candidate_count"],
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
