"""Governed RQData exporter for an ex-precious-metals commodity input.

This module exports the raw, auditable building blocks for a T-1 real-contract,
fully-collateralized self-financing index.  It never uses adjusted continuous
prices for P&L and never includes AU or AG.  Construction remains fail-closed
until a separate builder supplies and validates collateral-rate and dated
transaction-cost ledgers; consequently this exporter cannot label its output
D3 or feed the public allocation service by itself.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence


SCHEMA_V540 = "rqdata-ex-precious-metals-commodity-raw-ledger/1.0"
UNDERLYINGS_V540 = (
    "A", "AL", "C", "CF", "CU", "J", "L", "M", "P", "RB", "RU", "SR", "TA", "V", "Y", "ZN"
)
EXCLUDED_V540 = ("AU", "AG")
SECTOR_V540 = {
    "A": "agriculture", "C": "agriculture", "CF": "agriculture", "M": "agriculture",
    "P": "agriculture", "SR": "agriculture", "Y": "agriculture",
    "AL": "industrial_metals", "CU": "industrial_metals", "ZN": "industrial_metals",
    "J": "ferrous", "RB": "ferrous",
    "L": "energy_chemicals", "RU": "energy_chemicals", "TA": "energy_chemicals", "V": "energy_chemicals",
}


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def export_raw_contract_ledger_v540(
    rqdatac: Any,
    start_date: str,
    end_date: str,
    *,
    underlyings: Sequence[str] = UNDERLYINGS_V540,
) -> dict[str, Any]:
    """Fetch one deterministic dominant/multiplier/real-contract ledger per root."""

    requested = tuple(str(item).upper() for item in underlyings)
    if requested != UNDERLYINGS_V540 or any(item in EXCLUDED_V540 for item in requested):
        raise ValueError("v540_commodity_underlying_universe_must_match_frozen_ex_PM_set")
    rows: list[dict[str, Any]] = []
    query_ledger: list[dict[str, Any]] = []
    for root in requested:
        dominant = rqdatac.futures.get_dominant(
            root, start_date=start_date, end_date=end_date, rule=0, rank=1, market="cn"
        )
        multipliers = rqdatac.futures.get_contract_multiplier(
            root, start_date=start_date, end_date=end_date, market="cn"
        )
        dominant_records = [
            {"trade_date": str(index)[:10], "contract": str(value)}
            for index, value in dominant.items()
        ]
        contracts = sorted({record["contract"] for record in dominant_records})
        if not contracts:
            raise RuntimeError(f"v540_no_dominant_contracts:{root}")
        prices = rqdatac.get_price(
            contracts,
            start_date=start_date,
            end_date=end_date,
            frequency="1d",
            fields=["settlement", "prev_settlement", "open_interest", "volume"],
            adjust_type="none",
            market="cn",
        )
        multiplier_records = []
        for index, record in multipliers.reset_index().iterrows():
            del index
            multiplier_records.append(
                {
                    "underlying": str(record["underlying_symbol"]),
                    "trade_date": str(record["date"])[:10],
                    "exchange": str(record["exchange"]),
                    "contract_multiplier": float(record["contract_multiplier"]),
                }
            )
        price_records = []
        frame = prices.reset_index()
        date_column = "date" if "date" in frame.columns else "datetime"
        for _, record in frame.iterrows():
            price_records.append(
                {
                    "contract": str(record["order_book_id"]),
                    "trade_date": str(record[date_column])[:10],
                    "settlement": float(record["settlement"]),
                    "prev_settlement": float(record["prev_settlement"]),
                    "open_interest": float(record["open_interest"]),
                    "volume": float(record["volume"]),
                }
            )
        block = {
            "underlying": root,
            "sector": SECTOR_V540[root],
            "dominant_rule": "rule0_OI_1.1x_T_minus_1_effective_next_day",
            "dominant": dominant_records,
            "multipliers": multiplier_records,
            "real_contract_daily": price_records,
        }
        if not multiplier_records or not price_records:
            raise RuntimeError(f"v540_incomplete_contract_ledger:{root}")
        block["content_sha256"] = _hash(block)
        rows.append(block)
        query_ledger.extend(
            [
                {"api": "futures.get_dominant", "underlying": root, "rule": 0, "rank": 1},
                {"api": "futures.get_contract_multiplier", "underlying": root},
                {"api": "get_price", "underlying": root, "adjust_type": "none", "real_contracts_only": True},
            ]
        )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_V540,
        "provider": "RQData",
        "date_range": {"start": start_date, "end": end_date},
        "underlying_universe": list(requested),
        "excluded_underlyings": list(EXCLUDED_V540),
        "gold_weight": 0.0,
        "precious_metals_weight": 0.0,
        "information_boundary": "dominant_rule0_is_T_minus_1_and_effective_next_day",
        "continuous_adjusted_price_used_for_PnL": False,
        "raw_blocks": rows,
        "query_ledger": query_ledger,
        "query_sha256": _hash(query_ledger),
        "governance": {
            "status": "raw_research_input_not_D3",
            "construction_allowed": False,
            "missing_required_ledgers": [
                "collateral_rate_with_available_time",
                "dated_fee_schedule",
                "slippage_schedule",
                "independent_second_source_monthly_hash_crosscheck",
            ],
            "deployment_allowed": False,
        },
    }
    payload["content_sha256"] = _hash(payload)
    return payload


def validate_raw_contract_ledger_v540(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_V540:
        raise ValueError("v540_commodity_raw_schema_invalid")
    if tuple(payload.get("underlying_universe") or ()) != UNDERLYINGS_V540:
        raise ValueError("v540_commodity_raw_universe_invalid")
    if set(payload.get("excluded_underlyings") or ()) != set(EXCLUDED_V540):
        raise ValueError("v540_commodity_raw_exclusion_invalid")
    if float(payload.get("gold_weight", -1.0)) != 0.0 or float(payload.get("precious_metals_weight", -1.0)) != 0.0:
        raise ValueError("v540_commodity_raw_precious_metal_contamination")
    if payload.get("continuous_adjusted_price_used_for_PnL") is not False:
        raise ValueError("v540_commodity_raw_adjusted_continuous_PnL_forbidden")
    governance = payload.get("governance") or {}
    if governance.get("deployment_allowed") is not False or governance.get("construction_allowed") is not False:
        raise ValueError("v540_raw_ledger_cannot_be_promoted")
    stored = str(payload.get("content_sha256") or "")
    body = dict(payload)
    body.pop("content_sha256", None)
    if stored != _hash(body):
        raise ValueError("v540_commodity_raw_content_hash_mismatch")


__all__ = [
    "EXCLUDED_V540",
    "SCHEMA_V540",
    "UNDERLYINGS_V540",
    "export_raw_contract_ledger_v540",
    "validate_raw_contract_ledger_v540",
]
