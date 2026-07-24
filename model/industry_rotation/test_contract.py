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

style = p["style"]
quality = style["data_quality"]
quarterly = style["frequencies"]["quarterly"]
expected_cells = {
    f"{size}{kind}"
    for size in ("大盘", "中盘", "小盘")
    for kind in ("成长", "均衡", "价值", "红利")
}
actual_cells = {row["cell"] for row in style["cells"]}
if style["count"] != 12 or quality["cell_count"] != 12 or actual_cells != expected_cells:
    raise AssertionError(f"style cells invalid: {sorted(actual_cells)}")
if style["frequency"] != "quarterly" or quarterly["frequency"] != "quarterly":
    raise AssertionError("style frequency must be quarterly")
if style["benchmark"] != "12风格箱等权":
    raise AssertionError("style benchmark mismatch")
coverage = {
    quality["latest_eligible_stock_count"],
    quality["latest_labelled_stock_count"],
    quality["latest_unique_stock_count"],
}
if len(coverage) != 1 or quality["unclassified_stock_count"] != 0 or quality["duplicate_label_count"] != 0:
    raise AssertionError(f"style label coverage invalid: {quality}")
if quality["latest_labelled_stock_count"] <= 0 or quality["min_cell_stock_count"] <= 0:
    raise AssertionError("style universe or a style cell is empty")
if set(quarterly["metrics"]) != {"train", "validation", "test", "all"}:
    raise AssertionError("style split metrics incomplete")
if "验证集" not in quarterly["selection_rule"] or "2022年后仅报告" not in quarterly["selection_rule"]:
    raise AssertionError("style candidate selection rule is not frozen")
style_holdings = quarterly["holdings"]
bad_style_dates = [row for row in style_holdings if row["signal_date"] >= row["execution_date"]]
bad_style_size = [row for row in style_holdings if len(row["names"]) != 3]
bad_style_weight = [row for row in style_holdings if abs(row["weight"] - 1 / 3) > 1e-6]
if bad_style_dates:
    raise AssertionError(f"quarterly style T/T+1 violation: {bad_style_dates[:3]}")
if bad_style_size:
    raise AssertionError(f"quarterly style is not Top3: {bad_style_size[:3]}")
if bad_style_weight:
    raise AssertionError(f"quarterly style is not equal weight: {bad_style_weight[:3]}")
latest_style = style_holdings[-1]
if latest_style["execution_date"] <= p["as_of"] and latest_style.get("status") != "executed":
    raise AssertionError(f"stale planned style holding: {latest_style}")
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
    "style_cell_count": style["count"],
    "style_labelled_stock_count": quality["latest_labelled_stock_count"],
    "style_selected_candidate": quarterly["selected_candidate"],
    "style_test_excess": quarterly["metrics"]["test"]["annual_excess"],
}, ensure_ascii=False, indent=2))
