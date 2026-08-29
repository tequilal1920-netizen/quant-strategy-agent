"""Auditable ex-precious-metals commodity total-return sleeve for v5.4.3.

The implementation deliberately uses real contracts only.  RQData's rule-0
dominant mapping is treated as a T-1 signal that becomes effective on the next
trading day; adjusted continuous prices are never used for P&L.  The sleeve is
fully collateralised, rolls actual contracts, applies dated commissions and a
half-tick execution charge, and keeps fixed 25% sector risk sleeves.  AU and AG
are prohibited at every boundary.

This file constructs a D2 research series.  It cannot promote itself to D3:
an independent Wind/iFinD month-hash cross-check is still mandatory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import date, datetime
from functools import reduce
from math import gcd
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


SOURCE_SCHEMA_V543 = "asset-allocation-rqdata-v541-freeze/1.0"
TRADING_SCHEMA_V543 = "rqdata-futures-trading-parameters-v541/1.0"
OUTPUT_SCHEMA_V543 = "asset-allocation-panel-v543-d2-research/1.0"
CANONICAL_SOURCE_HASH_V543 = "E0E7001141EED0C8D1A46E58F47C875ADBC628BF62B491773C5A8BBF71D4F731"
CANONICAL_TRADING_HASH_V543 = "7D103E6EFB4923C34BA95DAD4B1A1E7F767CB41E88C697E6768938C4CA33436C"
ASSET_ORDER_V543 = ("equity", "bond", "gold", "commodity")
EXCLUDED_V543 = ("AU", "AG")
SECTORS_V543 = {
    "agriculture": ("A", "C", "CF", "M", "P", "SR", "Y"),
    "industrial_metals": ("AL", "CU", "ZN"),
    "ferrous": ("J", "RB"),
    "energy_chemicals": ("L", "RU", "TA", "V"),
}
UNDERLYINGS_V543 = ("A", "AL", "C", "CF", "CU", "J", "L", "M", "P", "RB", "RU", "SR", "TA", "V", "Y", "ZN")
TICK_CALIBRATION_END_V543 = "2013-03-31"
INDEX_START_V543 = "2013-04-01"
VOL_LOOKBACK_DAYS_V543 = 60
MIN_VOL_OBSERVATIONS_V543 = 20
SECTOR_NOTIONAL_V543 = 0.25


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def _validated_content_hash(payload: Mapping[str, Any], expected: str | None, label: str) -> str:
    stored = str(payload.get("content_sha256") or "")
    body = dict(payload)
    body.pop("content_sha256", None)
    if stored != _canonical_hash(body):
        raise ValueError(f"v543_{label}_content_hash_mismatch")
    if expected is not None and stored != expected:
        raise ValueError(f"v543_{label}_canonical_hash_mismatch")
    return stored


def _day(value: Any) -> str:
    result = str(value)[:10]
    datetime.strptime(result, "%Y-%m-%d")
    return result


def _month(value: str) -> str:
    return value[:7].replace("-", "")


def _finite_positive(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"v543_nonpositive:{label}")
    return number


def _root_from_contract(contract: str) -> str:
    match = re.match(r"[A-Za-z]+", str(contract))
    if match is None:
        raise ValueError("v543_contract_without_underlying")
    return match.group(0).upper()


def validate_inputs_v543(
    source: Mapping[str, Any],
    trading: Mapping[str, Any],
    *,
    expected_source_hash: str | None = CANONICAL_SOURCE_HASH_V543,
    expected_trading_hash: str | None = CANONICAL_TRADING_HASH_V543,
) -> None:
    if source.get("schema_version") != SOURCE_SCHEMA_V543:
        raise ValueError("v543_source_schema_invalid")
    if trading.get("schema_version") != TRADING_SCHEMA_V543:
        raise ValueError("v543_trading_schema_invalid")
    source_hash = _validated_content_hash(source, expected_source_hash, "source")
    _validated_content_hash(trading, expected_trading_hash, "trading")
    if trading.get("source_content_sha256") != source_hash:
        raise ValueError("v543_trading_source_lineage_mismatch")
    commodity = source.get("commodity_raw") or {}
    if tuple(commodity.get("underlyings") or ()) != UNDERLYINGS_V543:
        raise ValueError("v543_underlying_universe_invalid")
    if set(commodity.get("excluded") or ()) != set(EXCLUDED_V543):
        raise ValueError("v543_precious_metal_exclusion_invalid")
    if float(commodity.get("gold_weight", -1.0)) != 0.0 or float(commodity.get("precious_metals_weight", -1.0)) != 0.0:
        raise ValueError("v543_precious_metal_weight_nonzero")
    if commodity.get("continuous_adjusted_price_used_for_PnL") is not False:
        raise ValueError("v543_adjusted_continuous_price_forbidden")
    if commodity.get("dominant_rule") != "rule0_OI_1.1x_T_minus_1_effective_next_day":
        raise ValueError("v543_dominant_rule_invalid")
    if source.get("credentials_in_output") is not False:
        raise ValueError("v543_credentials_boundary_invalid")
    if any(root in EXCLUDED_V543 for root in commodity.get("dominant", {})):
        raise ValueError("v543_precious_metal_dominant_contamination")


def _tick_grid_by_root(
    prices: Sequence[Mapping[str, Any]],
    contract_to_root: Mapping[str, str],
    calibration_end: str,
) -> dict[str, float]:
    scaled: dict[str, list[int]] = defaultdict(list)
    for row in prices:
        when = _day(row["date"])
        if when > calibration_end:
            continue
        contract = str(row["order_book_id"])
        root = contract_to_root.get(contract)
        if root is None:
            continue
        settlement = _finite_positive(row["settlement"], "settlement")
        previous = _finite_positive(row["prev_settlement"], "prev_settlement")
        delta = int(round(abs(settlement - previous) * 10000.0))
        if delta > 0:
            scaled[root].append(delta)
    output = {}
    for root in UNDERLYINGS_V543:
        values = scaled.get(root) or []
        if len(values) < MIN_VOL_OBSERVATIONS_V543:
            raise ValueError(f"v543_tick_calibration_insufficient:{root}")
        grid = reduce(gcd, values)
        if grid <= 0:
            raise ValueError(f"v543_tick_grid_invalid:{root}")
        output[root] = grid / 10000.0
        if any(value % grid != 0 for value in values):
            raise AssertionError(f"v543_tick_grid_fit_failed:{root}")
    return output


def _fee_is_usable(row: Mapping[str, Any] | None) -> bool:
    if row is None or row.get("commission_type") not in ("by_volume", "by_money"):
        return False
    try:
        return all(
            math.isfinite(float(row[key])) and float(row[key]) >= 0.0
            for key in ("open_commission", "close_commission")
        )
    except (KeyError, TypeError, ValueError):
        return False


def _commission_cost(
    notional: float,
    direction: str,
    fee: Mapping[str, Any],
    settlement: float,
    multiplier: float,
) -> float:
    key = "open_commission" if direction == "open" else "close_commission"
    rate = float(fee[key])
    if fee["commission_type"] == "by_money":
        return notional * rate
    if fee["commission_type"] == "by_volume":
        return notional * rate / (settlement * multiplier)
    raise ValueError("v543_commission_type_invalid")


def _month_end_levels(rows: Sequence[Mapping[str, Any]], value_key: str) -> dict[str, float]:
    output: dict[str, float] = {}
    for row in sorted(rows, key=lambda item: _day(item["date"])):
        output[_month(_day(row["date"]))] = _finite_positive(row[value_key], value_key)
    return output


def _inverse_vol_weights(
    roots: Sequence[str],
    history: Mapping[str, Sequence[float]],
) -> dict[str, float]:
    if not roots:
        return {}
    vols = {}
    for root in roots:
        values = np.asarray(list(history.get(root, ()))[-VOL_LOOKBACK_DAYS_V543:], dtype=float)
        if len(values) >= MIN_VOL_OBSERVATIONS_V543 and np.all(np.isfinite(values)):
            volatility = float(np.std(values, ddof=1))
            if volatility > 1.0e-10:
                vols[root] = volatility
    if len(vols) != len(roots):
        return {root: 1.0 / len(roots) for root in roots}
    inverse = {root: 1.0 / vols[root] for root in roots}
    denominator = sum(inverse.values())
    return {root: value / denominator for root, value in inverse.items()}


def construct_panel_v543(
    source: Mapping[str, Any],
    trading: Mapping[str, Any],
    *,
    expected_source_hash: str | None = CANONICAL_SOURCE_HASH_V543,
    expected_trading_hash: str | None = CANONICAL_TRADING_HASH_V543,
    tick_calibration_end: str = TICK_CALIBRATION_END_V543,
    index_start: str = INDEX_START_V543,
) -> dict[str, Any]:
    validate_inputs_v543(
        source,
        trading,
        expected_source_hash=expected_source_hash,
        expected_trading_hash=expected_trading_hash,
    )
    commodity = source["commodity_raw"]
    dominant = {
        root: {_day(row["trade_date"]): str(row["contract"]) for row in rows}
        for root, rows in commodity["dominant"].items()
    }
    contract_to_root: dict[str, str] = {}
    for root, rows in dominant.items():
        for contract in rows.values():
            existing = contract_to_root.setdefault(contract, root)
            if existing != root or _root_from_contract(contract) != root:
                raise ValueError("v543_contract_root_mapping_invalid")
    prices = {
        (str(row["order_book_id"]), _day(row["date"])): row
        for row in commodity["real_contract_daily"]
    }
    multipliers = {
        (str(row["underlying_symbol"]).upper(), _day(row["date"])): _finite_positive(
            row["contract_multiplier"], "contract_multiplier"
        )
        for row in commodity["multipliers"]
    }
    fees = {
        (str(row["order_book_id"]), _day(row["trading_date"])): row
        for row in trading["rows"]
    }
    tick = _tick_grid_by_root(commodity["real_contract_daily"], contract_to_root, tick_calibration_end)
    calendar = sorted(set.intersection(*(set(dominant[root]) for root in UNDERLYINGS_V543)))
    if not calendar or calendar[0] > tick_calibration_end:
        raise ValueError("v543_calendar_insufficient_for_tick_calibration")
    collateral_rows = sorted(
        [(_day(row["date"]), float(row.get("DR001", row.get("ON")))) for row in source["collateral"]["daily"]],
        key=lambda item: item[0],
    )
    if not collateral_rows or not all(math.isfinite(value) and value >= 0.0 for _, value in collateral_rows):
        raise ValueError("v543_collateral_invalid")
    collateral_index = 0
    last_prior_rate: float | None = None
    history: dict[str, list[float]] = {root: [] for root in UNDERLYINGS_V543}
    target = {root: 0.0 for root in UNDERLYINGS_V543}
    previous_exposure: dict[str, float] = {}
    previous_date: str | None = None
    nav = 1.0
    daily_ledger = []
    fee_missing_counts = {root: 0 for root in UNDERLYINGS_V543}
    for when in calendar:
        root_returns = {}
        current_contracts = {}
        for root in UNDERLYINGS_V543:
            contract = dominant[root][when]
            current_contracts[root] = contract
            row = prices.get((contract, when))
            if row is None:
                raise ValueError(f"v543_dominant_price_missing:{root}:{when}")
            settlement = _finite_positive(row["settlement"], "settlement")
            previous = _finite_positive(row["prev_settlement"], "prev_settlement")
            root_returns[root] = settlement / previous - 1.0
        if when < index_start:
            for root, value in root_returns.items():
                history[root].append(value)
            continue
        month_changed = previous_date is None or _month(previous_date) != _month(when)
        if month_changed:
            new_target = {root: 0.0 for root in UNDERLYINGS_V543}
            for sector_roots in SECTORS_V543.values():
                eligible = []
                for root in sector_roots:
                    contract = current_contracts[root]
                    if (
                        (root, when) in multipliers
                        and _fee_is_usable(fees.get((contract, when)))
                        and root in tick
                    ):
                        eligible.append(root)
                    else:
                        fee_missing_counts[root] += 1
                sleeve = _inverse_vol_weights(eligible, history)
                for root, value in sleeve.items():
                    new_target[root] = SECTOR_NOTIONAL_V543 * value
            target = new_target
        new_exposure: dict[str, float] = defaultdict(float)
        for root, weight in target.items():
            if weight > 0.0:
                new_exposure[current_contracts[root]] += weight
        all_contracts = sorted(set(previous_exposure) | set(new_exposure))
        commission_cost = 0.0
        slippage_cost = 0.0
        traded_notional = 0.0
        for contract in all_contracts:
            old = float(previous_exposure.get(contract, 0.0))
            new = float(new_exposure.get(contract, 0.0))
            delta = new - old
            if abs(delta) <= 1.0e-15:
                continue
            root = contract_to_root[contract]
            price_row = prices.get((contract, when))
            fee_row = fees.get((contract, when))
            multiplier = multipliers.get((root, when))
            if price_row is None or multiplier is None or not _fee_is_usable(fee_row):
                raise ValueError(f"v543_trade_cost_input_missing:{contract}:{when}")
            settlement = _finite_positive(price_row["settlement"], "settlement")
            amount = abs(delta)
            traded_notional += amount
            commission_cost += _commission_cost(
                amount, "open" if delta > 0.0 else "close", fee_row, settlement, multiplier
            )
            slippage_cost += amount * 0.5 * tick[root] / settlement
        while collateral_index < len(collateral_rows) and collateral_rows[collateral_index][0] < when:
            last_prior_rate = collateral_rows[collateral_index][1]
            collateral_index += 1
        if last_prior_rate is None:
            raise ValueError(f"v543_prior_collateral_missing:{when}")
        day_count = 1 if previous_date is None else (date.fromisoformat(when) - date.fromisoformat(previous_date)).days
        if day_count <= 0:
            raise ValueError("v543_trading_calendar_not_increasing")
        collateral_return = (last_prior_rate / 100.0) * day_count / 365.0
        futures_return = sum(target[root] * root_returns[root] for root in UNDERLYINGS_V543)
        total_cost = commission_cost + slippage_cost
        daily_return = collateral_return + futures_return - total_cost
        if not math.isfinite(daily_return) or daily_return <= -1.0:
            raise ValueError(f"v543_daily_return_invalid:{when}")
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
                "target_weights": {root: target[root] for root in UNDERLYINGS_V543},
                "dominant_contracts": current_contracts,
            }
        )
        previous_exposure = dict(new_exposure)
        previous_date = when
        for root, value in root_returns.items():
            history[root].append(value)
    if len(daily_ledger) < 500:
        raise ValueError("v543_commodity_ledger_too_short")
    commodity_levels = _month_end_levels(daily_ledger, "nav")
    direct_levels = {
        asset: _month_end_levels(source["asset_blocks"][asset]["daily"], "close")
        for asset in ("equity", "bond", "gold")
    }
    common = sorted(set(commodity_levels).intersection(*(set(levels) for levels in direct_levels.values())))
    if len(common) < 61:
        raise ValueError("v543_common_months_insufficient")
    months = common[1:]
    returns = []
    levels = []
    for index in range(1, len(common)):
        previous_month, current_month = common[index - 1], common[index]
        current_levels = [direct_levels[asset][current_month] for asset in ("equity", "bond", "gold")] + [commodity_levels[current_month]]
        previous_levels = [direct_levels[asset][previous_month] for asset in ("equity", "bond", "gold")] + [commodity_levels[previous_month]]
        row = [current_levels[column] / previous_levels[column] - 1.0 for column in range(4)]
        if not all(math.isfinite(value) and value > -1.0 for value in row):
            raise ValueError(f"v543_monthly_return_invalid:{current_month}")
        returns.append(row)
        levels.append(current_levels)
    output: dict[str, Any] = {
        "schema_version": OUTPUT_SCHEMA_V543,
        "asset_order": list(ASSET_ORDER_V543),
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
            "underlyings": list(UNDERLYINGS_V543),
            "sectors": {key: list(value) for key, value in SECTORS_V543.items()},
            "sector_notional": SECTOR_NOTIONAL_V543,
            "excluded_underlyings": list(EXCLUDED_V543),
            "gold_weight": 0.0,
            "precious_metals_weight": 0.0,
            "continuous_adjusted_price_used_for_PnL": False,
            "dominant_rule": commodity["dominant_rule"],
            "volatility_weighting": {
                "lookback_trading_days": VOL_LOOKBACK_DAYS_V543,
                "minimum_observations": MIN_VOL_OBSERVATIONS_V543,
                "insufficient_history": "equal_weight_among_currently_cost_verified_roots",
            },
            "tick_size": {
                "method": "integer_gcd_of_real_settlement_changes_before_selection_period",
                "calibration_end": tick_calibration_end,
                "values": tick,
                "execution_charge": "half_tick_each_traded_notional_side",
            },
            "collateral": {
                "source_method": source["collateral"]["method"],
                "information_lag": "strictly_previous_calendar_observation",
                "day_count": "ACT/365",
            },
            "fees": {
                "method": "dated_RQData_by_volume_or_by_money",
                "missing_fee_policy": "root_remains_zero_weight_until_current_fee_is_verified; held_trade_missing_fails_closed",
                "missing_monthly_rebalance_observations": fee_missing_counts,
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
                "early_history_some_roots_zero_weight_until_dated_fee_available",
            ],
        },
        "credentials_in_output": False,
        "deployment_allowed": False,
    }
    output["content_sha256"] = _canonical_hash(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--trading-parameters", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = json.loads(Path(args.source).read_text(encoding="utf-8"))
    trading = json.loads(Path(args.trading_parameters).read_text(encoding="utf-8"))
    result = construct_panel_v543(source, trading)
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


__all__ = [
    "ASSET_ORDER_V543",
    "OUTPUT_SCHEMA_V543",
    "construct_panel_v543",
    "validate_inputs_v543",
]
