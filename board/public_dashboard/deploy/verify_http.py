from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlencode, urljoin

MODULES = {"macro", "global_markets", "sw_industries", "commodities", "stock", "news_events"}


def fetch(base: str, relative: str):
    url = urljoin(base.rstrip("/") + "/", relative.lstrip("/"))
    request = urllib.request.Request(url, headers={"User-Agent": "ResearchMarketBoardVerifier/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        return response.status, {key.lower(): value for key, value in response.headers.items()}, body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    args = parser.parse_args()
    checks = {}

    status, headers, body = fetch(args.base, "/")
    checks["root"] = status == 200 and b'id="dashboard-main"' in body
    required_headers = {"content-security-policy", "x-content-type-options", "referrer-policy", "permissions-policy"}
    checks["security_headers"] = required_headers <= set(headers)

    status, _, body = fetch(args.base, "/livez")
    livez = json.loads(body)
    checks["livez"] = status == 200 and livez.get("status") == "ok" and livez.get("catalog_rows") == 367

    status, _, body = fetch(args.base, "/healthz")
    health = json.loads(body)
    try:
        generated_at = datetime.fromisoformat(str(health.get("generated_at")).replace("Z", "+00:00"))
        age_seconds = (datetime.now(timezone.utc) - generated_at.astimezone(timezone.utc)).total_seconds()
        generated_at_fresh = -300 <= age_seconds <= 30 * 60 * 60
    except (TypeError, ValueError):
        generated_at_fresh = False
    checks["health"] = (
        status == 200
        and health.get("status") == "ok"
        and health.get("snapshot_status") in {"ok", "partial"}
        and health.get("catalog_rows") == 367
        and not health.get("failures")
        and health.get("coverage", {}).get("live", 0) > 0
        and generated_at_fresh
    )

    status, _, body = fetch(args.base, "/api/v1/snapshot")
    snapshot = json.loads(body)
    module_statuses = {module.get("status") for module in snapshot.get("modules", {}).values() if isinstance(module, dict)}
    checks["snapshot"] = (
        status == 200
        and set(snapshot.get("modules", {})) == MODULES
        and snapshot.get("status") in {"ok", "partial"}
        and not ({"failed", "stale"} & module_statuses)
        and snapshot.get("summary", {}).get("fabricated_observations") == 0
    )

    status, _, body = fetch(args.base, "/api/v1/catalog")
    catalog = json.loads(body)
    checks["catalog"] = status == 200 and len(catalog.get("rows", [])) == 367

    status, _, body = fetch(args.base, "/api/v1/coverage")
    coverage = json.loads(body)
    checks["coverage"] = (
        status == 200
        and coverage.get("catalog_rows") == 367
        and coverage.get("totals", {}).get("total") == 367
        and coverage.get("totals", {}).get("live", 0) > 0
        and set(coverage.get("modules", {})) == MODULES
    )

    selected_series = None
    for module_key, module in snapshot.get("modules", {}).items():
        if not isinstance(module, dict):
            continue
        for item in module.get("series", []):
            if isinstance(item, dict) and item.get("id") and item.get("data"):
                selected_series = (module_key, item["id"])
                break
        if selected_series:
            break
    if selected_series:
        module_key, series_id = selected_series
        query = urlencode({"ids": series_id, "module": module_key, "frequency": "raw", "transform": "raw"})
        status, _, body = fetch(args.base, "/api/v1/series?" + query)
        series_payload = json.loads(body)
        returned = series_payload.get("series", [])
        checks["series"] = (
            status == 200
            and series_payload.get("count") == 1
            and len(returned) == 1
            and returned[0].get("id") == series_id
            and returned[0].get("point_count", 0) > 0
        )
    else:
        checks["series"] = False

    status, _, body = fetch(args.base, "/api/v1/stock/000001")
    stock = json.loads(body)
    stock_record = stock.get("data", {}).get("record", {})
    checks["stock"] = (
        status == 200
        and stock.get("code") == "000001"
        and stock.get("status") in {"ok", "partial"}
        and isinstance(stock_record.get("close"), (int, float))
        and bool(stock_record.get("source"))
        and bool(stock_record.get("as_of"))
    )

    failed = sorted(name for name, passed in checks.items() if not passed)
    print(json.dumps({"base": args.base, "checks": checks, "failed": failed}, ensure_ascii=False, indent=2))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
