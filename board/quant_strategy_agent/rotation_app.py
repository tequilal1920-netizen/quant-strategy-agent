"""Industry/style rotation extension for the authenticated quant strategy shell."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from flask import jsonify, render_template, request, session

from app import APP_VERSION as BASE_VERSION
from app import PUBLIC_HOST, USERNAME, app


APP_VERSION = f"{BASE_VERSION}-rotation-r2"
ROOT = Path(__file__).resolve().parent
ROTATION_SNAPSHOT = ROOT / "data" / "rotation_snapshot.json"
ROTATION_TRACKING = ROOT / "data" / "rotation_tracking.json"
_CACHE_LOCK = threading.RLock()
_CACHE: dict[str, dict[str, Any]] = {}


def _load_json(path: Path) -> dict[str, Any]:
    stat = path.stat()
    key = str(path)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and cached.get("mtime_ns") == stat.st_mtime_ns:
            return cached["payload"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        _CACHE[key] = {"mtime_ns": stat.st_mtime_ns, "payload": payload}
        return payload


def _snapshot_contract(payload: dict[str, Any]) -> dict[str, Any]:
    industries = payload.get("high_frequency", {}).get("industries", [])
    field_count = sum(len(row.get("indicators", [])) for row in industries)
    summary = payload.get("high_frequency", {}).get("summary", {})
    expected_field_count = int(summary.get("field_count") or field_count)
    live_field_count = int(summary.get("live_field_count") or 0)
    min_live_per_industry = int(summary.get("min_live_per_industry") or 0)
    frequencies = payload.get("industry", {}).get("frequencies", {})
    style_frequencies = payload.get("style", {}).get("frequencies", {})
    errors: list[str] = []
    if len(industries) != 31:
        errors.append("industry_count_not_31")
    if field_count < 94:
        errors.append("field_count_below_94")
    if field_count != expected_field_count:
        errors.append("field_count_summary_mismatch")
    if live_field_count < field_count:
        errors.append("live_field_count_incomplete")
    if min_live_per_industry < 6:
        errors.append("min_live_per_industry_below_6")
    for family, rows in (("industry", frequencies), ("style", style_frequencies)):
        if set(rows) != {"monthly", "weekly"}:
            errors.append(f"{family}_frequency_contract")
        for frequency, model in rows.items():
            if set(model.get("metrics", {})) != {"train", "validation", "test", "all"}:
                errors.append(f"{family}_{frequency}_split_contract")
            for holding in model.get("holdings", []):
                if holding.get("signal_date", "") >= holding.get("execution_date", ""):
                    errors.append(f"{family}_{frequency}_timing_contract")
                    break
    return {
        "status": "ok" if not errors else "failed",
        "errors": errors,
        "industry_count": len(industries),
        "field_count": field_count,
        "live_field_count": live_field_count,
        "min_live_per_industry": min_live_per_industry,
        "as_of": payload.get("as_of"),
    }


def rotation_index():
    return render_template(
        "index_rotation.html",
        authenticated=True,
        user=session.get("user") or USERNAME,
        app_version=APP_VERSION,
        public_host=PUBLIC_HOST,
    )


app.view_functions["index"] = rotation_index


@app.get("/api/rotation/health")
def rotation_health():
    try:
        contract = _snapshot_contract(_load_json(ROTATION_SNAPSHOT))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "failed", "message": str(exc)}), 503
    return jsonify(contract), (200 if contract["status"] == "ok" else 503)


@app.get("/api/rotation/snapshot")
def rotation_snapshot():
    try:
        payload = _load_json(ROTATION_SNAPSHOT)
        contract = _snapshot_contract(payload)
        if contract["status"] != "ok":
            return jsonify({"status": "failed", "quality": contract}), 503
        return jsonify(payload)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "failed", "message": str(exc)}), 503


@app.get("/api/rotation/industry-dashboard")
def rotation_industry_dashboard():
    """Return a compact, quality-labelled industry payload for selected SW L1 rows."""
    try:
        snapshot = _load_json(ROTATION_SNAPSHOT)
        tracking = _load_json(ROTATION_TRACKING)
        snapshot_rows = snapshot.get("high_frequency", {}).get("industries", [])
        snapshot_index = {row.get("industry"): row for row in snapshot_rows if row.get("industry")}
        requested = [value.strip() for value in request.args.get("industries", "").split(",") if value.strip()]
        names = requested or list(snapshot_index)
        unknown = [name for name in names if name not in snapshot_index]
        if unknown:
            return jsonify({"status": "failed", "message": "unknown_industry", "industries": unknown}), 400

        tracking_index = tracking.get("industries", {})
        industries: list[dict[str, Any]] = []
        for name in names:
            row = snapshot_index[name]
            tracking_row = tracking_index.get(name, {})
            indicators: list[dict[str, Any]] = []
            for indicator in row.get("indicators", []):
                status = indicator.get("status") or "unavailable"
                item = {
                    "name": indicator.get("name"),
                    "variable": indicator.get("variable"),
                    "series_id": indicator.get("series_id"),
                    "source": indicator.get("source"),
                    "field": indicator.get("field"),
                    "frequency": indicator.get("frequency"),
                    "unit": indicator.get("unit"),
                    "lag": indicator.get("lag"),
                    "status": status,
                    "last_date": indicator.get("last_date"),
                    "availability_rule": indicator.get("availability_rule"),
                    "data": [],
                }
                if status == "live":
                    item["data"] = [
                        {"date": point.get("date"), "value": point.get("value")}
                        for point in indicator.get("data", [])
                        if point.get("date") and point.get("value") is not None
                    ]
                indicators.append(item)
            industries.append({
                "industry": name,
                "rank": row.get("rank"),
                "score": row.get("score"),
                "selected": row.get("selected"),
                "data_quality": row.get("data_quality"),
                "live_indicators": row.get("live_indicators", 0),
                "total_indicators": row.get("total_indicators", len(indicators)),
                "indicators": indicators,
                "trend": tracking_row.get("trend", []),
                "score_history": tracking_row.get("score_history", []),
            })
        return jsonify({
            "status": "ok",
            "as_of": snapshot.get("as_of"),
            "tracking_as_of": tracking.get("as_of"),
            "summary": snapshot.get("high_frequency", {}).get("summary", {}),
            "industries": industries,
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "failed", "message": str(exc)}), 503

@app.get("/api/rotation/tracking")
def rotation_tracking():
    try:
        payload = _load_json(ROTATION_TRACKING)
        if len(payload.get("industries", {})) != 31:
            return jsonify({"status": "failed", "message": "tracking_industry_count_not_31"}), 503
        return jsonify(payload)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "failed", "message": str(exc)}), 503

