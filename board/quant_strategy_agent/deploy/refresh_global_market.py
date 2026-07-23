from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))

from app import sina_index_chart

TARGETS = [
    ("000001.SS", "上证综指", "A股"),
    ("000300.SS", "沪深300", "A股"),
    ("^GSPC", "标普500", "美股"),
    ("^IXIC", "纳斯达克综合", "美股"),
    ("^DJI", "道琼斯工业", "美股"),
    ("^HSI", "恒生指数", "港股"),
    ("^N225", "日经225", "日股"),
    ("^KS11", "韩国综合", "韩股"),
    ("^STOXX50E", "欧洲斯托克50", "欧股"),
    ("^GDAXI", "德国DAX", "欧股"),
]


def validate_item(item: dict, market: str) -> None:
    row = item.get("row") or {}
    series = (item.get("series") or [{}])[0]
    points = series.get("data") or []
    if row.get("market") != market or len(points) < 120:
        raise RuntimeError(f"quality_failed:{market}:rows={len(points)}")
    dates = [str(point.get("date") or "") for point in points]
    values = [point.get("value") for point in points]
    if dates != sorted(set(dates)):
        raise RuntimeError(f"date_order_failed:{market}")
    if any(not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in values):
        raise RuntimeError(f"finite_value_failed:{market}")
    if row.get("as_of") != dates[-1]:
        raise RuntimeError(f"as_of_failed:{market}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rows = []
    series = []
    for symbol, market, region in TARGETS:
        item = sina_index_chart(symbol, market, region)
        if not item:
            raise RuntimeError(f"provider_unavailable:{market}")
        validate_item(item, market)
        rows.append(item["row"])
        series.extend(item["series"])
    payload = {
        "status": "ok",
        "as_of": max(row["as_of"] for row in rows),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "AKShare 新浪指数历史行情（独立进程日度快照）",
        "rows": rows,
        "series": series,
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"status": "ok", "rows": len(rows), "series": len(series), "as_of": payload["as_of"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())