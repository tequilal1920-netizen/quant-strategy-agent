"""One-shot remote RQData exporter for the v5.4.1 research data freeze.

This script contains no credentials and writes only market data plus query
lineage.  It intentionally uses a bounded, preregistered universe and one
complete date range.  The commodity output remains research-only until an
independent source cross-check and the final transaction-cost policy are
certified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import rqdatac


ASSETS = {
    "equity": "H00300.INDX",
    "bond": "H11006.XSHG",
    "gold": "AU9999.SGEX",
}
UNDERLYINGS = (
    "A", "AL", "C", "CF", "CU", "J", "L", "M", "P", "RB", "RU", "SR", "TA", "V", "Y", "ZN"
)
EXCLUDED = ("AU", "AG")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest().upper()


def records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    output = []
    for row in frame.reset_index().to_dict(orient="records"):
        clean = {}
        for key, value in row.items():
            if pd.isna(value):
                clean[str(key)] = None
            elif isinstance(value, (pd.Timestamp, datetime)):
                clean[str(key)] = value.isoformat()
            elif isinstance(value, (np.integer, np.floating)):
                clean[str(key)] = value.item()
            else:
                clean[str(key)] = value
        output.append(clean)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2013-01-01")
    parser.add_argument("--end", default="2026-06-30")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rqdatac.init()
    query_ledger = []
    asset_blocks = {}
    for asset, code in ASSETS.items():
        frame = rqdatac.get_price(
            code, start_date=args.start, end_date=args.end,
            frequency="1d", fields=["close"], adjust_type="none", market="cn"
        )
        block = records(frame)
        asset_blocks[asset] = {"code": code, "daily": block, "sha256": canonical_hash(block)}
        query_ledger.append({"api": "get_price", "code": code, "fields": ["close"], "adjust_type": "none"})
    dominant = {}
    all_contracts = set()
    for root in UNDERLYINGS:
        series = rqdatac.futures.get_dominant(
            root, start_date=args.start, end_date=args.end, rule=0, rank=1, market="cn"
        )
        rows = [{"trade_date": str(index)[:10], "contract": str(value)} for index, value in series.items()]
        dominant[root] = rows
        all_contracts.update(row["contract"] for row in rows)
        query_ledger.append({"api": "futures.get_dominant", "root": root, "rule": 0, "rank": 1})
    multiplier = rqdatac.futures.get_contract_multiplier(
        list(UNDERLYINGS), start_date=args.start, end_date=args.end, market="cn"
    )
    query_ledger.append({"api": "futures.get_contract_multiplier", "roots": list(UNDERLYINGS)})
    contract_prices = rqdatac.get_price(
        sorted(all_contracts), start_date=args.start, end_date=args.end,
        frequency="1d", fields=["settlement", "prev_settlement", "open_interest", "volume"],
        adjust_type="none", market="cn"
    )
    query_ledger.append({"api": "get_price", "real_contract_count": len(all_contracts), "adjust_type": "none"})
    try:
        collateral = rqdatac.econ.get_interbank_pledged_repo_rate(
            start_date=args.start, end_date=args.end, fields=["DR001"], market="cn"
        )
        collateral_method = "econ.get_interbank_pledged_repo_rate.DR001"
    except (AttributeError, TypeError):
        collateral = rqdatac.get_interbank_offered_rate(
            start_date=args.start, end_date=args.end, fields=["ON"], source="Shibor"
        )
        collateral_method = "get_interbank_offered_rate.Shibor_ON_fallback"
    query_ledger.append({"api": collateral_method})
    payload = {
        "schema_version": "asset-allocation-rqdata-v541-freeze/1.0",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "provider": "RQData",
        "date_range": {"start": args.start, "end": args.end},
        "asset_blocks": asset_blocks,
        "commodity_raw": {
            "underlyings": list(UNDERLYINGS),
            "excluded": list(EXCLUDED),
            "gold_weight": 0.0,
            "precious_metals_weight": 0.0,
            "dominant_rule": "rule0_OI_1.1x_T_minus_1_effective_next_day",
            "dominant": dominant,
            "multipliers": records(multiplier),
            "real_contract_daily": records(contract_prices),
            "continuous_adjusted_price_used_for_PnL": False,
            "status": "research_raw_not_D3_pending_cost_and_second_source",
        },
        "collateral": {"method": collateral_method, "daily": records(collateral)},
        "query_ledger": query_ledger,
        "query_sha256": canonical_hash(query_ledger),
        "credentials_in_output": False,
        "deployment_allowed": False,
    }
    payload["content_sha256"] = canonical_hash(payload)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    temporary.replace(output)
    print(json.dumps({
        "status": "ok", "output": str(output), "content_sha256": payload["content_sha256"],
        "real_contract_count": len(all_contracts), "query_count": len(query_ledger),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
