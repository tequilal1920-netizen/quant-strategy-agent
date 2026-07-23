from __future__ import annotations

"""One-call, timeout-bounded probe for the dashboard's free AKShare sources."""

import argparse
import concurrent.futures
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ENDPOINTS = (
    "macro_china_gdp", "macro_china_gdzctz", "macro_china_gyzjz",
    "macro_china_pmi", "macro_china_lpi_index",
    "macro_china_enterprise_boom_index", "macro_china_consumer_goods_retail",
    "macro_china_xfzxx", "macro_china_mobile_number", "macro_china_cpi",
    "macro_china_ppi", "macro_china_agricultural_product",
    "macro_china_commodity_price_index", "macro_china_construction_index",
    "macro_china_energy_index", "macro_china_new_house_price",
    "macro_china_money_supply", "macro_china_shibor_all", "macro_china_lpr",
    "macro_china_reserve_requirement_ratio", "macro_china_central_bank_balance",
    "macro_china_new_financial_credit", "macro_china_shrzgm", "macro_china_czsr",
    "macro_china_hgjck", "macro_china_fx_gold",
    "macro_china_society_traffic_volume", "macro_china_passenger_load_factor",
    "macro_china_society_electricity", "macro_shipping_bdi", "macro_shipping_bci",
    "macro_shipping_bpi", "macro_shipping_bcti", "macro_china_bdti_index",
    "futures_comex_inventory", "futures_hog_core", "futures_hog_cost",
    "futures_hog_supply", "macro_china_postal_telecommunicational",
    "macro_china_insurance_income", "car_market_total_cpca",
    "car_market_fuel_cpca", "stock_margin_sse",
)

CHILD = r'''import akshare as ak, inspect, json, sys, time
name = sys.argv[1]
out = {"endpoint": name}
try:
    function = getattr(ak, name)
    signature = inspect.signature(function)
    out["signature"] = str(signature)
    required = [
        item.name for item in signature.parameters.values()
        if item.default is inspect._empty
        and item.kind in (item.POSITIONAL_ONLY, item.POSITIONAL_OR_KEYWORD, item.KEYWORD_ONLY)
    ]
    if required:
        out.update(ok=False, skipped=True, error_type="RequiredArguments", required=required)
    else:
        started = time.time()
        frame = function()
        out.update(
            ok=True,
            rows=int(len(frame)),
            columns=[str(value) for value in list(frame.columns)[:40]],
            column_count=int(len(frame.columns)),
            elapsed_s=round(time.time() - started, 2),
        )
except Exception as exc:
    out.update(ok=False, error_type=type(exc).__name__, message=str(exc)[:160])
print(json.dumps(out, ensure_ascii=True))
'''


def probe(endpoint: str, timeout: int) -> dict:
    try:
        process = subprocess.run(
            [sys.executable, "-c", CHILD, endpoint],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"endpoint": endpoint, "ok": False, "error_type": "Timeout", "message": f"{timeout}s timeout"}
    lines = [line for line in process.stdout.splitlines() if line.strip().startswith("{")]
    if not lines:
        return {"endpoint": endpoint, "ok": False, "error_type": "NoJson", "message": process.stderr[-160:]}
    return json.loads(lines[-1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=50)
    args = parser.parse_args()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(args.workers, 4))) as executor:
        results = list(executor.map(lambda name: probe(name, args.timeout), ENDPOINTS))
    report = {
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "ok": sum(bool(item.get("ok")) for item in results),
        "results": results,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(json.dumps({"tested_at": report["tested_at"], "total": report["total"], "ok": report["ok"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
