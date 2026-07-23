import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
snapshot_path = ROOT / "board/quant_strategy_agent/data/rotation_snapshot.json"
p = json.loads(snapshot_path.read_text(encoding="utf-8"))
today = date.today().isoformat()
summary = p["high_frequency"]["summary"]
industries = p["high_frequency"]["industries"]
indicators = [x for industry in industries for x in industry["indicators"]]
for key, expected in {
    "industry_count": 31,
    "field_count": 248,
    "live_field_count": 248,
    "min_live_per_industry": 8,
}.items():
    if summary[key] != expected:
        raise AssertionError(f"{key}: {summary[key]} != {expected}")

counts = Counter(x["industry"] for x in industries for _ in x["indicators"])
if len(counts) != 31 or set(counts.values()) != {8}:
    raise AssertionError(f"industry field counts invalid: {counts}")
names = [x["name"] for x in indicators]
if len(names) != len(set(names)):
    raise AssertionError("indicator names are not globally unique")
forbidden = p["method"]["forbidden_fields"]
hits = [(x["name"], word) for x in indicators for word in forbidden if word.lower() in x["name"].lower()]
if hits:
    raise AssertionError(f"forbidden indicators: {hits[:10]}")

live = defaultdict(int)
future = []
short = []
for industry in industries:
    for x in industry["indicators"]:
        if x["status"] == "live":
            live[industry["industry"]] += 1
        for field in ("last_date", "last_available_date"):
            if x.get(field) and x[field] > today:
                future.append((industry["industry"], x["name"], field, x[field]))
        if x.get("history_years", 0) < 3:
            short.append((industry["industry"], x["name"], x.get("history_years")))
if min(live.values()) < 8:
    raise AssertionError(f"live floor failed: {min(live.values())}")
if future:
    raise AssertionError(f"future-dated data: {future[:10]}")
if short:
    raise AssertionError(f"history shorter than 3y: {short[:10]}")

for frequency in ("monthly", "weekly"):
    bt = p["industry"]["frequencies"][frequency]
    if set(bt["metrics"]) != {"train", "validation", "test", "all"}:
        raise AssertionError(f"{frequency}: split metrics incomplete")
    holdings = bt["holdings"]
    bad_dates = [row for row in holdings if row["signal_date"] >= row["execution_date"]]
    bad_size = [row for row in holdings if len(row["names"]) != 10]
    bad_weight = [row for row in holdings if abs(row["weight"] - 0.1) > 1e-12]
    if bad_dates:
        raise AssertionError(f"{frequency}: T/T+1 violation: {bad_dates[:3]}")
    if bad_size:
        raise AssertionError(f"{frequency}: not Top10: {bad_size[:3]}")
    if bad_weight:
        raise AssertionError(f"{frequency}: not equal weight: {bad_weight[:3]}")
if p["method"]["industry_benchmark"].split("；")[0] != "31行业等权":
    raise AssertionError("benchmark mismatch")

print(json.dumps({
    "status": "ok",
    "today": today,
    "industry_count": len(industries),
    "field_count": len(indicators),
    "live_field_count": sum(live.values()),
    "min_live_per_industry": min(live.values()),
    "max_last_observation": max(x["last_date"] for x in indicators if x.get("last_date")),
    "max_last_available": max(x["last_available_date"] for x in indicators if x.get("last_available_date")),
    "monthly_test_excess": p["industry"]["frequencies"]["monthly"]["metrics"]["test"]["annual_excess"],
    "weekly_test_excess": p["industry"]["frequencies"]["weekly"]["metrics"]["test"]["annual_excess"],
}, ensure_ascii=False, indent=2))
