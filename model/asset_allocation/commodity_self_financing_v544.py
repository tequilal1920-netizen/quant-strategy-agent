"""Corrected real-contract commodity sleeve for the governed long sample.

Corrections relative to the rejected v5.4.3 draft:
* all sixteen ex-AU/ex-AG roots must be active; missing fees never cause a
  silent zero weight;
* every roll/rebalance executes at the previous trading day's settlement;
* old-contract close and new-contract open fees are charged separately;
* the series starts only after dated fee coverage is complete for every root.

The output remains D2 research-only until the independent Wind/iFinD monthly
hash cross-check is complete.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Mapping

import numpy as np

import commodity_self_financing_v543 as base


OUTPUT_SCHEMA_V544 = "asset-allocation-panel-v544-d2-research/1.0"
INDEX_START_V544 = "2014-12-24"


def _execution_price(
    contract: str,
    root: str,
    when: str,
    execution_date: str,
    current_contracts: Mapping[str, str],
    prices: Mapping[tuple[str, str], Mapping[str, Any]],
) -> float:
    if current_contracts[root] == contract:
        current = prices.get((contract, when))
        if current is None:
            raise ValueError(f"v544_new_contract_price_missing:{contract}:{when}")
        return base._finite_positive(current["prev_settlement"], "execution_prev_settlement")
    prior = prices.get((contract, execution_date))
    if prior is None:
        raise ValueError(f"v544_old_contract_price_missing:{contract}:{execution_date}")
    return base._finite_positive(prior["settlement"], "execution_settlement")


def construct_panel_v544(
    source: Mapping[str, Any],
    trading: Mapping[str, Any],
    *,
    expected_source_hash: str | None = base.CANONICAL_SOURCE_HASH_V543,
    expected_trading_hash: str | None = base.CANONICAL_TRADING_HASH_V543,
    index_start: str = INDEX_START_V544,
) -> dict[str, Any]:
    base.validate_inputs_v543(
        source,
        trading,
        expected_source_hash=expected_source_hash,
        expected_trading_hash=expected_trading_hash,
    )
    commodity = source["commodity_raw"]
    dominant = {
        root: {base._day(row["trade_date"]): str(row["contract"]) for row in rows}
        for root, rows in commodity["dominant"].items()
    }
    contract_to_root: dict[str, str] = {}
    for root, rows in dominant.items():
        for contract in rows.values():
            if base._root_from_contract(contract) != root:
                raise ValueError("v544_contract_root_mapping_invalid")
            existing = contract_to_root.setdefault(contract, root)
            if existing != root:
                raise ValueError("v544_contract_root_collision")
    prices = {
        (str(row["order_book_id"]), base._day(row["date"])): row
        for row in commodity["real_contract_daily"]
    }
    multipliers = {
        (str(row["underlying_symbol"]).upper(), base._day(row["date"])): base._finite_positive(
            row["contract_multiplier"], "contract_multiplier"
        )
        for row in commodity["multipliers"]
    }
    fees = {
        (str(row["order_book_id"]), base._day(row["trading_date"])): row
        for row in trading["rows"]
    }
    tick = base._tick_grid_by_root(
        commodity["real_contract_daily"], contract_to_root, base.TICK_CALIBRATION_END_V543
    )
    calendar = sorted(set.intersection(*(set(dominant[root]) for root in base.UNDERLYINGS_V543)))
    if index_start not in calendar:
        raise ValueError("v544_index_start_not_trading_day")
    collateral_rows = sorted(
        [
            (base._day(row["date"]), float(row.get("DR001", row.get("ON"))))
            for row in source["collateral"]["daily"]
        ],
        key=lambda item: item[0],
    )
    if not collateral_rows or not all(math.isfinite(value) and value >= 0.0 for _, value in collateral_rows):
        raise ValueError("v544_collateral_invalid")
    collateral_index = 0
    last_prior_rate: float | None = None
    history: dict[str, list[float]] = {root: [] for root in base.UNDERLYINGS_V543}
    target = {root: 0.0 for root in base.UNDERLYINGS_V543}
    previous_exposure: dict[str, float] = {}
    previous_date: str | None = None
    nav = 1.0
    daily_ledger = []
    for when in calendar:
        current_contracts = {root: dominant[root][when] for root in base.UNDERLYINGS_V543}
        root_returns = {}
        for root, contract in current_contracts.items():
            row = prices.get((contract, when))
            if row is None:
                raise ValueError(f"v544_dominant_price_missing:{root}:{when}")
            settlement = base._finite_positive(row["settlement"], "settlement")
            prior_settlement = base._finite_positive(row["prev_settlement"], "prev_settlement")
            root_returns[root] = settlement / prior_settlement - 1.0
        if when < index_start:
            for root, value in root_returns.items():
                history[root].append(value)
            previous_date = when
            continue
        if previous_date is None:
            raise ValueError("v544_previous_execution_date_missing")
        month_changed = not daily_ledger or base._month(previous_date) != base._month(when)
        if month_changed:
            new_target = {root: 0.0 for root in base.UNDERLYINGS_V543}
            for sector_roots in base.SECTORS_V543.values():
                sleeve = base._inverse_vol_weights(sector_roots, history)
                if set(sleeve) != set(sector_roots):
                    raise ValueError("v544_sector_root_silently_dropped")
                for root, value in sleeve.items():
                    new_target[root] = base.SECTOR_NOTIONAL_V543 * value
            if not math.isclose(sum(new_target.values()), 1.0, abs_tol=1.0e-12):
                raise ValueError("v544_target_notional_not_one")
            target = new_target
        new_exposure: dict[str, float] = defaultdict(float)
        for root, weight in target.items():
            if weight <= 0.0:
                raise ValueError(f"v544_root_weight_nonpositive:{root}:{when}")
            new_exposure[current_contracts[root]] += weight
        commission_cost = 0.0
        slippage_cost = 0.0
        traded_notional = 0.0
        trade_ledger = []
        for contract in sorted(set(previous_exposure) | set(new_exposure)):
            old_weight = float(previous_exposure.get(contract, 0.0))
            new_weight = float(new_exposure.get(contract, 0.0))
            delta = new_weight - old_weight
            if abs(delta) <= 1.0e-15:
                continue
            root = contract_to_root[contract]
            fee = fees.get((contract, previous_date))
            multiplier = multipliers.get((root, previous_date))
            if not base._fee_is_usable(fee) or multiplier is None:
                raise ValueError(f"v544_previous_day_trade_input_missing:{contract}:{previous_date}")
            execution_price = _execution_price(
                contract, root, when, previous_date, current_contracts, prices
            )
            amount = abs(delta)
            direction = "open" if delta > 0.0 else "close"
            commission = base._commission_cost(
                amount, direction, fee, execution_price, multiplier
            )
            slippage = amount * 0.5 * tick[root] / execution_price
            commission_cost += commission
            slippage_cost += slippage
            traded_notional += amount
            trade_ledger.append(
                {
                    "contract": contract,
                    "root": root,
                    "direction": direction,
                    "execution_date": previous_date,
                    "effective_date": when,
                    "notional": amount,
                    "execution_settlement": execution_price,
                    "contract_multiplier": multiplier,
                    "commission_type": fee["commission_type"],
                    "commission_cost": commission,
                    "half_tick_slippage_cost": slippage,
                }
            )
        while collateral_index < len(collateral_rows) and collateral_rows[collateral_index][0] < when:
            last_prior_rate = collateral_rows[collateral_index][1]
            collateral_index += 1
        if last_prior_rate is None:
            raise ValueError(f"v544_prior_collateral_missing:{when}")
        day_count = (date.fromisoformat(when) - date.fromisoformat(previous_date)).days
        if day_count <= 0:
            raise ValueError("v544_calendar_not_increasing")
        collateral_return = (last_prior_rate / 100.0) * day_count / 365.0
        futures_return = sum(target[root] * root_returns[root] for root in base.UNDERLYINGS_V543)
        total_cost = commission_cost + slippage_cost
        daily_return = collateral_return + futures_return - total_cost
        if not math.isfinite(daily_return) or daily_return <= -1.0:
            raise ValueError(f"v544_daily_return_invalid:{when}")
        nav *= 1.0 + daily_return
        daily_ledger.append(
            {
                "date": when,
                "nav": nav,
                "return": daily_return,
                "collateral_return": collateral_return,
                "futures_return": futures_return,
                "commission_cost": commission_cost,
                "half_tick_slippage_cost": slippage_cost,
                "traded_notional": traded_notional,
                "target_notional": sum(target.values()),
                "target_weights": {root: target[root] for root in base.UNDERLYINGS_V543},
                "dominant_contracts": current_contracts,
                "trades": trade_ledger,
            }
        )
        previous_exposure = dict(new_exposure)
        previous_date = when
        for root, value in root_returns.items():
            history[root].append(value)
    if len(daily_ledger) < 500:
        raise ValueError("v544_commodity_ledger_too_short")
    if any(abs(row["target_notional"] - 1.0) > 1.0e-12 for row in daily_ledger):
        raise ValueError("v544_daily_target_notional_incomplete")
    commodity_levels = base._month_end_levels(daily_ledger, "nav")
    direct_levels = {
        asset: base._month_end_levels(source["asset_blocks"][asset]["daily"], "close")
        for asset in ("equity", "bond", "gold")
    }
    common = sorted(set(commodity_levels).intersection(*(set(value) for value in direct_levels.values())))
    if len(common) < 61:
        raise ValueError("v544_common_months_insufficient")
    months = common[1:]
    returns = []
    levels = []
    for index in range(1, len(common)):
        left, right = common[index - 1], common[index]
        left_values = [direct_levels[asset][left] for asset in ("equity", "bond", "gold")] + [commodity_levels[left]]
        right_values = [direct_levels[asset][right] for asset in ("equity", "bond", "gold")] + [commodity_levels[right]]
        row = [right_values[column] / left_values[column] - 1.0 for column in range(4)]
        if not all(math.isfinite(value) and value > -1.0 for value in row):
            raise ValueError(f"v544_monthly_return_invalid:{right}")
        returns.append(row)
        levels.append(right_values)
    output: dict[str, Any] = {
        "schema_version": OUTPUT_SCHEMA_V544,
        "asset_order": list(base.ASSET_ORDER_V543),
        "months": months,
        "returns": returns,
        "levels": levels,
        "level_base_month": common[0],
        "source_lineage": {
            "provider": "RQData",
            "source_content_sha256": source["content_sha256"],
            "trading_parameters_content_sha256": trading["content_sha256"],
            "direct_series": {
                asset: {
                    "code": source["asset_blocks"][asset]["code"],
                    "daily_sha256": source["asset_blocks"][asset]["sha256"],
                }
                for asset in ("equity", "bond", "gold")
            },
        },
        "commodity": {
            "method": "real_contract_rule0_T_minus_1_fully_collateralised_self_financing",
            "underlyings": list(base.UNDERLYINGS_V543),
            "sectors": {key: list(value) for key, value in base.SECTORS_V543.items()},
            "sector_notional": base.SECTOR_NOTIONAL_V543,
            "excluded_underlyings": list(base.EXCLUDED_V543),
            "gold_weight": 0.0,
            "precious_metals_weight": 0.0,
            "continuous_adjusted_price_used_for_PnL": False,
            "dominant_rule": commodity["dominant_rule"],
            "index_start": index_start,
            "roll_and_rebalance_execution": "previous_trading_day_settlement",
            "volatility_weighting": {
                "lookback_trading_days": base.VOL_LOOKBACK_DAYS_V543,
                "minimum_observations": base.MIN_VOL_OBSERVATIONS_V543,
                "insufficient_history": "equal_weight_within_sector",
            },
            "tick_size": {
                "method": "integer_gcd_of_real_settlement_changes_before_selection_period",
                "calibration_end": base.TICK_CALIBRATION_END_V543,
                "values": tick,
                "execution_charge": "half_tick_each_traded_notional_side",
            },
            "collateral": {
                "source_method": source["collateral"]["method"],
                "information_lag": "strictly_previous_calendar_observation",
                "day_count": "ACT/365",
            },
            "fees": {
                "method": "dated_RQData_by_volume_or_by_money_at_previous_trading_day",
                "missing_fee_policy": "fail_closed_no_root_drop_no_imputation",
            },
            "daily_ledger": daily_ledger,
            "monthly_nav": {month: commodity_levels[month] for month in common},
        },
        "data_quality": {
            "status": "D2_research_not_D3",
            "production_ready": False,
            "blocking_items": [
                "Wind_or_iFinD_independent_monthly_hash_crosscheck_not_completed",
                "direct_series_primary_source_is_RQData_not_Wind",
                "settlement_execution_and_half_tick_cost_are_research_assumptions",
            ],
        },
        "credentials_in_output": False,
        "deployment_allowed": False,
    }
    output["content_sha256"] = base._canonical_hash(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--trading-parameters", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = json.loads(Path(args.source).read_text(encoding="utf-8"))
    trading = json.loads(Path(args.trading_parameters).read_text(encoding="utf-8"))
    result = construct_panel_v544(source, trading)
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
                "status": result["data_quality"]["status"],
                "months": len(result["months"]),
                "daily_rows": len(result["commodity"]["daily_ledger"]),
                "content_sha256": result["content_sha256"],
                "deployment_allowed": result["deployment_allowed"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["OUTPUT_SCHEMA_V544", "construct_panel_v544"]
