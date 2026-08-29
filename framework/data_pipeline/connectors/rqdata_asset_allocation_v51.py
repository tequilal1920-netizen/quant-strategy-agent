"""Low-frequency RQData probe/export for governed asset allocation v5.1.

The connector is intentionally whitelist-only.  It never prints or stores a
license, username, password, token, or RQData configuration.  ``probe`` keeps
at most five observations per series.  ``export`` retrieves only the declared
date window and writes the raw provider fields needed for later point-in-time
and revision audits; it does not claim that a latest-vintage response is a
historical vintage.

The primary production source priority remains Wind then iFind.  RQData is a
PIT supplement/cross-check unless and until the project registry records D3
evidence for an exact research series.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


RQ_ASSETS_V51: dict[str, str] = {
    "equity_execution": "510300.XSHG",
    "bond_execution": "511010.XSHG",
    "gold_execution": "518880.XSHG",
    "gold_research_crosscheck": "AU9999.SGEX",
}

RQ_MACRO_FACTORS_V51: dict[str, str] = {
    "pmi_manufacturing": "制造业采购经理指数PMI_当月",
    "pmi_production": "制造业采购经理指数PMI_生产",
    "pmi_new_orders": "制造业采购经理指数PMI_新订单",
    "pmi_raw_material_inventory": "制造业采购经理指数PMI_原材料库存",
    "pmi_finished_goods_inventory": "制造业采购经理指数PMI_产成品库存",
    "pmi_supplier_delivery": "制造业采购经理指数PMI_供应商配送时间",
    "cpi_national_yoy": "居民消费价格指数CPI_当月同比(上年同月=100)",
    "ppi_yoy": "工业品出厂价格指数PPI_当月同比_(上年同月=100)",
    "sf_monthly": "社会融资规模_当月值",
    "sf_new_rmb_loan": "社会融资规模_新增贷款(人民币)_当月值",
    "sf_stock_yoy": "社会融资规模存量_同比增速_月末数",
    "financial_institution_medium_long_loan": "金融机构境内中长期贷款(人民币)_月末数",
    "fixed_asset_investment_yoy": "固定资产投资完成额(不含农户):累计同比:月",
    "industrial_activity_quarterly_yoy": "工业增加值:当期同比:季",
    "government_bond_yield_1y": "中债国债到期收益率曲线:1年:日",
    "government_bond_yield_10y": "中债国债到期收益率曲线:10年:日",
    "dr007": "存款类机构质押式回购加权利率:DR007:日",
}

NON_SUBSTITUTION_RULES_V51 = {
    "pmi_finished_goods_inventory": "survey_confirmation_only_not_real_industrial_inventory",
    "financial_institution_medium_long_loan": "aggregate_validation_only_not_enterprise_medium_long_credit",
    "fixed_asset_investment_yoy": "aggregate_validation_only_not_manufacturing_investment",
    "industrial_activity_quarterly_yoy": "activity_confirmation_only_not_capacity_utilization_or_profit",
    "government_bond_yield_10y": "macro_factor_only_not_bond_total_return",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _records(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    reset = frame.reset_index() if hasattr(frame, "reset_index") else frame
    if not hasattr(reset, "to_dict"):
        return []
    return [
        {str(key): _json_value(value) for key, value in row.items()}
        for row in reset.to_dict(orient="records")
    ]


def _query_hash(kind: str, payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        {"kind": kind, **dict(payload)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _tail_by(records: Iterable[Mapping[str, Any]], key: str, limit: int = 5) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for raw in records:
        row = dict(raw)
        grouped.setdefault(str(row.get(key) or "UNKNOWN"), []).append(row)
    output: dict[str, list[dict[str, Any]]] = {}
    for name, rows in grouped.items():
        rows.sort(key=lambda row: str(row.get("info_date") or row.get("date") or row.get("index") or ""))
        output[name] = rows[-limit:]
    return output


def _instrument_metadata(rqdatac: Any, code: str) -> dict[str, Any]:
    instrument = rqdatac.instruments(code)
    fields = (
        "order_book_id",
        "symbol",
        "type",
        "listed_date",
        "de_listed_date",
        "currency",
        "exchange",
        "underlying_symbol",
    )
    return {field: _json_value(getattr(instrument, field, None)) for field in fields}


def collect_rqdata_v51(mode: str, start_date: str, end_date: str) -> dict[str, Any]:
    if not os.environ.get("RQSDK_LICENSE") and not os.environ.get("RQDATAC_CONF"):
        raise RuntimeError("rqdata_runtime_configuration_not_present")
    import rqdatac

    rqdatac.init()
    factor_names = list(RQ_MACRO_FACTORS_V51.values())
    factor_frame = rqdatac.econ.get_factors(factor_names, start_date, end_date)
    factor_records = _records(factor_frame)
    money_records = _records(rqdatac.econ.get_money_supply(start_date, end_date))
    assets: dict[str, Any] = {}
    for alias, code in RQ_ASSETS_V51.items():
        metadata = _instrument_metadata(rqdatac, code)
        prices = _records(
            rqdatac.get_price(
                code,
                start_date=start_date,
                end_date=end_date,
                frequency="1d",
                fields=["close"],
                adjust_type="pre",
                skip_suspended=False,
                expect_df=True,
            )
        )
        assets[alias] = {
            "code": code,
            "metadata": metadata,
            "rows": prices if mode == "export" else prices[-5:],
            "row_count": len(prices),
            "query_hash": _query_hash(
                "rqdatac.get_price",
                {
                    "code": code,
                    "start_date": start_date,
                    "end_date": end_date,
                    "fields": ["close"],
                    "adjust_type": "pre",
                },
            ),
        }
    reverse = {provider_name: alias for alias, provider_name in RQ_MACRO_FACTORS_V51.items()}
    factor_samples = _tail_by(factor_records, "factor", 5)
    macro_by_alias = {
        reverse.get(provider_name, provider_name): rows
        for provider_name, rows in factor_samples.items()
    }
    payload: dict[str, Any] = {
        "schema_version": "rqdata-asset-allocation-v5.1",
        "mode": mode,
        "retrieved_at": _utc_now(),
        "provider": "RQData",
        "source_priority_role": "PIT supplement and cross-check after Wind/iFind",
        "date_window": {"start": start_date, "end": end_date},
        "whitelist": {
            "assets": dict(RQ_ASSETS_V51),
            "macro_factors": dict(RQ_MACRO_FACTORS_V51),
        },
        "non_substitution_rules": dict(NON_SUBSTITUTION_RULES_V51),
        "assets": assets,
        "macro": {
            "row_count": len(factor_records),
            "fields": sorted({key for row in factor_records for key in row}),
            "query_hash": _query_hash(
                "rqdatac.econ.get_factors",
                {
                    "factors": factor_names,
                    "start_date": start_date,
                    "end_date": end_date,
                },
            ),
            "samples_by_alias": macro_by_alias,
        },
        "money_supply": {
            "row_count": len(money_records),
            "fields": sorted({key for row in money_records for key in row}),
            "query_hash": _query_hash(
                "rqdatac.econ.get_money_supply",
                {"start_date": start_date, "end_date": end_date},
            ),
            "rows": money_records if mode == "export" else money_records[-5:],
        },
        "pit_policy": {
            "info_date": "candidate availability date, subject to sample verification",
            "rice_create_tm": "provider record creation timestamp; not automatically a complete revision vintage",
            "latest_vintage_warning": "raw exports are not admitted to historical backtests until revision/as-of checks pass",
        },
        "secrets_stored": False,
    }
    if mode == "export":
        payload["macro"]["rows"] = factor_records
    return payload


def _atomic_json(payload: Mapping[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(destination)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("probe", "export"), default="probe")
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = collect_rqdata_v51(args.mode, args.start_date, args.end_date)
    destination = Path(args.output).resolve()
    _atomic_json(payload, destination)
    print(
        json.dumps(
            {
                "status": "ok",
                "mode": args.mode,
                "output": str(destination),
                "asset_count": len(payload["assets"]),
                "macro_rows": payload["macro"]["row_count"],
                "money_supply_rows": payload["money_supply"]["row_count"],
                "secrets_stored": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
