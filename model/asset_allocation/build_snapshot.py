"""Build the public asset-allocation snapshot from approved read-only sources."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from asset_allocation_engine import (
    ASSET_PROXIES,
    BacktestConfig,
    build_snapshot,
    fetch_baostock_prices,
    fetch_sina_total_return_prices,
    load_etf_prices_from_sqlite,
    load_macro_from_sqlite,
    merge_price_series,
    write_snapshot,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="Path to research_warehouse.db")
    parser.add_argument("--output", required=True, help="Destination JSON snapshot")
    parser.add_argument("--start-date", default="2014-01-01")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--transaction-cost-bps", type=float, default=10.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    database = Path(args.database).resolve()
    output = Path(args.output).resolve()
    if database == output:
        raise ValueError("output_must_not_overwrite_database")

    macro_rows = load_macro_from_sqlite(database)
    local_etfs = load_etf_prices_from_sqlite(database)
    continuity_etfs = fetch_sina_total_return_prices(
        {key: ASSET_PROXIES[key] for key in ("bond", "commodity", "cash")}
    )
    incremental_start = min(
        max(row["date"] for row in rows) for rows in local_etfs.values() if rows
    )
    incremental_start = f"{incremental_start[:4]}-{incremental_start[4:6]}-01"
    recent_etfs = fetch_baostock_prices(
        start_date=incremental_start,
        end_date=args.end_date,
        proxies={"equity": ASSET_PROXIES["equity"]},
    )
    prices = merge_price_series(local_etfs, continuity_etfs, recent_etfs)
    snapshot = build_snapshot(
        macro_rows,
        prices,
        config=BacktestConfig(transaction_cost_bps=args.transaction_cost_bps),
    )
    write_snapshot(snapshot, output)
    summary = {
        "status": snapshot["status"],
        "output": str(output),
        "generated_at": snapshot["generated_at"],
        "data_as_of": snapshot["data_as_of"],
        "quality": snapshot["quality"]["status"],
        "recommended_weights": snapshot["allocations"]["recommended"]["weights"],
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
