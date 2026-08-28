"""Audited four-asset data contract for asset-allocation v5.

The v5 research universe is deliberately small and semantic: Chinese equity,
Chinese government bonds, RMB gold and an ex-gold commodity-futures sleeve.
This module never downloads data and never writes to the research warehouse.
It validates provider lineage and offers a clearly labelled local proxy panel
for shadow research only.  Production use requires a D3 registry entry: a
successfully probed primary source, point-in-time/revision metadata and a
second-source cross-check.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


ASSET_ORDER_V5 = ("equity", "bond", "gold", "commodity")
ASSET_LABELS_V5 = {
    "equity": "权益",
    "bond": "国债",
    "gold": "黄金",
    "commodity": "商品",
}

COMMODITY_EXECUTION_CODES_V5 = (
    "159980.SZ",  # 大成有色金属期货ETF
    "159981.SZ",  # 建信易盛郑商所能源化工期货ETF
    "159985.SZ",  # 华夏饲料豆粕期货ETF
)


@dataclass(frozen=True)
class AssetSeriesSpecV5:
    asset: str
    research_provider: str
    research_series_id: str
    execution_code: str | None
    source_table_or_api: str
    value_field: str
    is_total_return: bool
    currency: str
    excludes_gold: bool
    gold_weight: float
    verification_status: str
    cross_check_provider: str | None = None
    query_hash: str | None = None
    available_time_field: str | None = None
    revision_field: str | None = None
    note: str = ""


def default_asset_registry_v5() -> dict[str, AssetSeriesSpecV5]:
    """Return the documented target registry, without overstating entitlement.

    D1 means that an official manual/function exists.  These defaults are not
    production approval: exact codes, current account permission and five-row
    evidence still have to be recorded before upgrading an entry to D3.
    """

    return {
        "equity": AssetSeriesSpecV5(
            asset="equity",
            research_provider="Wind",
            research_series_id="PENDING_AINDEX_TOTAL_RETURN_CODE",
            execution_code="510300.SH",
            source_table_or_api="AINDEXEODPRICES",
            value_field="S_DQ_CLOSE",
            is_total_return=True,
            currency="CNY",
            excludes_gold=True,
            gold_weight=0.0,
            verification_status="D1_documented_not_entitlement_verified",
            cross_check_provider="RQData/iFind",
            available_time_field="OPDATE",
            revision_field="OPMODE",
            note="研究序列必须由INCOME_PROCESSING_METHOD确认全收益口径。",
        ),
        "bond": AssetSeriesSpecV5(
            asset="bond",
            research_provider="Wind",
            research_series_id="PENDING_CNBOND_GOV_WEALTH_CODE",
            execution_code="511010.SH",
            source_table_or_api="CBONDINDEXEODCNBD",
            value_field="S_DQ_CLOSE",
            is_total_return=True,
            currency="CNY",
            excludes_gold=True,
            gold_weight=0.0,
            verification_status="D1_documented_not_entitlement_verified",
            cross_check_provider="RQData/iFind",
            available_time_field="OPDATE",
            revision_field="OPMODE",
            note="国债收益率曲线不能替代国债财富/总收益指数。",
        ),
        "gold": AssetSeriesSpecV5(
            asset="gold",
            research_provider="Wind",
            research_series_id="PENDING_SGE_AU9999_CODE",
            execution_code="518880.SH",
            source_table_or_api="CGOLDSPOTEODPRICES",
            value_field="S_DQ_CLOSE",
            is_total_return=False,
            currency="CNY",
            excludes_gold=False,
            gold_weight=1.0,
            verification_status="D1_documented_not_entitlement_verified",
            cross_check_provider="RQData/iFind",
            available_time_field="OPDATE",
            revision_field="OPMODE",
            note="人民币黄金现货价格收益；ETF用于执行映射。",
        ),
        "commodity": AssetSeriesSpecV5(
            asset="commodity",
            research_provider="Wind",
            research_series_id="PENDING_EX_GOLD_COMMODITY_TOTAL_RETURN",
            execution_code="BASKET:159980.SZ|159981.SZ|159985.SZ",
            source_table_or_api="CCOMMODITYFUTURESEODPRICES+CFUTURESCONTRACTMAPPING",
            value_field="S_DQ_SETTLE",
            is_total_return=True,
            currency="CNY",
            excludes_gold=True,
            gold_weight=0.0,
            verification_status="D1_components_documented_index_not_verified",
            cross_check_provider="RQData/iFind",
            available_time_field="OPDATE",
            revision_field="OPMODE",
            note="生产前必须固定品种池、换月、抵押收益、成本并证明黄金权重为零。",
        ),
    }


def _registry_hash(registry: Mapping[str, AssetSeriesSpecV5]) -> str:
    payload = {key: asdict(registry[key]) for key in sorted(registry)}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_asset_registry_v5(
    registry: Mapping[str, AssetSeriesSpecV5],
    *,
    require_production: bool = True,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if tuple(registry) != ASSET_ORDER_V5:
        errors.append("asset_order_must_be_equity_bond_gold_commodity")
    if "cash" in registry:
        errors.append("cash_is_not_an_investable_v5_asset")
    for asset in ASSET_ORDER_V5:
        item = registry.get(asset)
        if item is None:
            errors.append(f"missing_asset:{asset}")
            continue
        if item.asset != asset:
            errors.append(f"asset_key_mismatch:{asset}:{item.asset}")
        if item.currency != "CNY":
            errors.append(f"non_cny_series:{asset}:{item.currency}")
        if not item.research_series_id or item.research_series_id.startswith("PENDING_"):
            warnings.append(f"unresolved_research_series:{asset}")
        if require_production and item.verification_status != "D3_production_verified":
            errors.append(f"production_verification_missing:{asset}")
        if require_production and not item.query_hash:
            errors.append(f"five_row_query_hash_missing:{asset}")
        if require_production and not item.cross_check_provider:
            errors.append(f"cross_check_source_missing:{asset}")

    commodity = registry.get("commodity")
    gold = registry.get("gold")
    if commodity is not None:
        if not commodity.excludes_gold or abs(float(commodity.gold_weight)) > 1.0e-12:
            errors.append("commodity_must_exclude_gold")
        identifier = f"{commodity.research_series_id}|{commodity.execution_code}".lower()
        normalized_identifier = identifier.replace("ex_gold", "").replace("ex-gold", "")
        if any(
            token in normalized_identifier
            for token in ("510170", "gold", "黄金", "au9999", "518880", "159934")
        ):
            errors.append("invalid_commodity_proxy_or_gold_overlap")
    if commodity is not None and gold is not None:
        if commodity.research_series_id == gold.research_series_id:
            errors.append("gold_and_commodity_series_must_be_distinct")

    return {
        "status": "passed" if not errors else "failed",
        "production_ready": not errors and require_production,
        "errors": errors,
        "warnings": warnings,
        "registry_hash": _registry_hash(registry),
        "policy": "only D3, point-in-time and cross-checked series may feed production",
    }


def load_asset_registry_v5(path: str | Path) -> dict[str, AssetSeriesSpecV5]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("assets", payload)
    registry: dict[str, AssetSeriesSpecV5] = {}
    for asset in ASSET_ORDER_V5:
        row = rows.get(asset)
        if not isinstance(row, dict):
            raise ValueError(f"asset_registry_missing:{asset}")
        registry[asset] = AssetSeriesSpecV5(**row)
    return registry


def _read_code(connection: sqlite3.Connection, code: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT trade_date, close, pct_chg, fund_name FROM etf_ohlcv_daily "
        "WHERE ts_code=? AND close>0 ORDER BY trade_date",
        (code,),
    )
    return [
        {
            "date": str(row[0]),
            "close": float(row[1]),
            "pct_chg": None if row[2] is None else float(row[2]),
            "fund_name": str(row[3] or ""),
            "source_code": code,
            "research_only_proxy": True,
        }
        for row in rows
    ]


def build_execution_commodity_basket_v5(
    components: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """Build a transparent equal-weight *execution* proxy from futures ETFs.

    The result is intentionally marked research-only and is not treated as a
    long-history commodity total-return index.  Each day is equal-weighted
    across the three ex-gold futures ETFs on their common calendar.
    """

    missing = [code for code in COMMODITY_EXECUTION_CODES_V5 if not components.get(code)]
    if missing:
        raise ValueError("commodity_execution_components_missing:" + ",".join(missing))
    by_code: dict[str, dict[str, float]] = {}
    for code in COMMODITY_EXECUTION_CODES_V5:
        by_code[code] = {
            str(row["date"]): float(row["close"])
            for row in components[code]
            if row.get("close") is not None and float(row["close"]) > 0
        }
    dates = sorted(set.intersection(*(set(rows) for rows in by_code.values())))
    if len(dates) < 24:
        raise ValueError(f"commodity_execution_history_too_short:{len(dates)}")
    level = 100.0
    output: list[dict[str, Any]] = []
    previous = None
    for date in dates:
        closes = [by_code[code][date] for code in COMMODITY_EXECUTION_CODES_V5]
        daily_return = 0.0
        if previous is not None:
            component_returns = [current / prior - 1.0 for current, prior in zip(closes, previous)]
            if any(not math.isfinite(value) or value <= -1.0 for value in component_returns):
                raise ValueError(f"commodity_component_return_invalid:{date}")
            daily_return = sum(component_returns) / len(component_returns)
            level *= 1.0 + daily_return
        output.append(
            {
                "date": date,
                "close": level,
                "pct_chg": daily_return * 100.0,
                "source_code": "BASKET:" + "|".join(COMMODITY_EXECUTION_CODES_V5),
                "components": list(COMMODITY_EXECUTION_CODES_V5),
                "excludes_gold": True,
                "gold_weight": 0.0,
                "research_only_proxy": True,
                "total_return_verified": False,
            }
        )
        previous = closes
    return output


def load_local_shadow_prices_v5(path: str | Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Read local ETF proxies for engineering/shadow tests without mutation."""

    db_path = Path(path).resolve()
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as connection:
        equity = _read_code(connection, "510300.SH")
        bond = _read_code(connection, "511010.SH")
        gold = _read_code(connection, "159934.SZ")
        component_rows = {
            code: _read_code(connection, code) for code in COMMODITY_EXECUTION_CODES_V5
        }
    commodity = build_execution_commodity_basket_v5(component_rows)
    panel = {"equity": equity, "bond": bond, "gold": gold, "commodity": commodity}
    coverage = {
        asset: {
            "rows": len(rows),
            "first": rows[0]["date"] if rows else None,
            "last": rows[-1]["date"] if rows else None,
        }
        for asset, rows in panel.items()
    }
    lineage = {
        "status": "research_only",
        "production_ready": False,
        "coverage": coverage,
        "warnings": [
            "bond_local_history_requires_verified_research_series_or_external_execution_history",
            "gold_uses_159934_execution_proxy_in_local_shadow_panel",
            "commodity_is_short_history_equal_weight_futures_etf_execution_proxy",
        ],
        "forbidden_proxy": "510170.SH",
    }
    return panel, lineage


def reconcile_provider_series_v5(
    primary: Sequence[Mapping[str, Any]],
    cross_checks: Sequence[Sequence[Mapping[str, Any]]],
    *,
    return_tolerance: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Cross-check overlapping one-period returns without silently overwriting."""

    output = [dict(row) for row in primary]
    primary_by_date = {str(row["date"]): float(row["close"]) for row in primary}
    comparisons = 0
    breaches: list[dict[str, Any]] = []
    for provider_index, rows in enumerate(cross_checks):
        other = {str(row["date"]): float(row["close"]) for row in rows}
        common = sorted(set(primary_by_date).intersection(other))
        for prior_date, date in zip(common, common[1:]):
            left = primary_by_date[date] / primary_by_date[prior_date] - 1.0
            right = other[date] / other[prior_date] - 1.0
            comparisons += 1
            if abs(left - right) > return_tolerance:
                breaches.append(
                    {
                        "provider_index": provider_index,
                        "date": date,
                        "primary_return": left,
                        "cross_check_return": right,
                        "absolute_difference": abs(left - right),
                    }
                )
    audit = {
        "status": "passed" if comparisons > 0 and not breaches else "failed",
        "comparisons": comparisons,
        "breaches": breaches[:20],
        "return_tolerance": return_tolerance,
    }
    return output, audit
