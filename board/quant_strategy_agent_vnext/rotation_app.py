"""Industry/style rotation extension for the authenticated quant strategy shell."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from flask import jsonify, render_template, request, session

from app import APP_VERSION as BASE_VERSION
from app import PUBLIC_HOST, USERNAME, app


APP_VERSION = f"{BASE_VERSION}-rotation-r4-industry-style-v129"
ROOT = Path(__file__).resolve().parent
ROTATION_SNAPSHOT = ROOT / "data" / "rotation_snapshot.json"
ROTATION_TRACKING = ROOT / "data" / "rotation_tracking.json"
ROTATION_FINAL_FIGURES = ROOT / "data" / "rotation_final_figures.json"
ROTATION_FINAL_FIGURES_STATIC = ROOT / "static" / "rotation_figures" / "manifest.json"
ROTATION_RESEARCH_DASHBOARD = ROOT / "data" / "industry_research_dashboard.json"
ROTATION_STYLE_RESEARCH = ROOT / "data" / "style_six_dimension_monthly.json"
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


def _overlay_style_research_figures(payload: dict[str, Any]) -> dict[str, Any]:
    """Expose web-static figure paths while keeping model JSON as the data source."""
    public_payload = dict(payload)
    strategies = dict(public_payload.get("strategies") or {})
    try:
        manifest_source = ROTATION_FINAL_FIGURES if ROTATION_FINAL_FIGURES.exists() else ROTATION_FINAL_FIGURES_STATIC
        manifest = _load_json(manifest_source)
        figures = manifest.get("figures", {})
    except Exception:  # noqa: BLE001
        figures = {}
    patched: dict[str, Any] = {}
    for key, strategy in strategies.items():
        local = dict(strategy or {})
        figure_row = figures.get(key, {}) if isinstance(figures, dict) else {}
        if figure_row:
            local["figures"] = {
                "annual_table": figure_row.get("annual_table"),
                "daily_nav": figure_row.get("daily_nav"),
            }
            if figure_row.get("long_short"):
                local["long_short_figures"] = figure_row.get("long_short")
        patched[key] = local
    public_payload["strategies"] = patched
    return public_payload


def _snapshot_contract(payload: dict[str, Any]) -> dict[str, Any]:
    industries = payload.get("high_frequency", {}).get("industries", [])
    field_count = sum(len(row.get("indicators", [])) for row in industries)
    frequencies = payload.get("industry", {}).get("frequencies", {})
    style_frequencies = payload.get("style", {}).get("frequencies", {})
    errors: list[str] = []
    forbidden = (
        "营业收入", "营收", "利润", "净利润", "ROE", "ROA", "毛利率", "负债率",
        "动量", "趋势", "换手", "拥挤", "估值", "市盈率", "市净率",
    )

    if payload.get("schema_version") != "4.0":
        errors.append("schema_version_not_4")
    if len(industries) != 31:
        errors.append("industry_count_not_31")
    if field_count != 248:
        errors.append("field_count_not_248")
    for row in industries:
        name = str(row.get("industry") or "unknown")
        indicators = row.get("indicators", [])
        if len(indicators) != 8:
            errors.append(f"{name}_field_count_not_8")
        live = sum(bool(item.get("status") == "live" and item.get("model_eligible")) for item in indicators)
        if live < 6:
            errors.append(f"{name}_live_below_6")
        labels = [str(item.get("name") or "") for item in indicators]
        if len(set(labels)) != len(labels):
            errors.append(f"{name}_duplicate_fields")
        for item in indicators:
            text = f"{item.get('name', '')}|{item.get('field', '')}"
            if any(word.lower() in text.lower() for word in forbidden):
                errors.append(f"{name}_forbidden_field")
                break

    expected_frequencies = {"industry": {"monthly", "weekly"}, "style": {"quarterly"}}
    for family, rows in (("industry", frequencies), ("style", style_frequencies)):
        if set(rows) != expected_frequencies[family]:
            errors.append(f"{family}_frequency_contract")
        for frequency, model in rows.items():
            if set(model.get("metrics", {})) != {"train", "validation", "test", "all"}:
                errors.append(f"{family}_{frequency}_split_contract")
            for holding in model.get("holdings", []):
                if holding.get("signal_date", "") >= holding.get("execution_date", ""):
                    errors.append(f"{family}_{frequency}_timing_contract")
                    break
                if (
                    holding.get("status") == "planned"
                    and holding.get("execution_date", "") <= str(payload.get("as_of") or "")
                ):
                    errors.append(f"{family}_{frequency}_stale_planned_status")
                    break

    style = payload.get("style", {})
    labels = style.get("stock_labels", [])
    label_codes = [str(row.get("code") or "") for row in labels]
    allowed_sizes = {"大盘", "中盘", "小盘"}
    allowed_styles = {"成长", "均衡", "价值", "红利"}
    quality = style.get("data_quality", {})
    if style.get("count") != 12 or len(style.get("cells", [])) != 12:
        errors.append("style_cell_count_not_12")
    if len(label_codes) != len(set(label_codes)):
        errors.append("style_stock_labels_not_unique")
    if any(row.get("size") not in allowed_sizes or row.get("style") not in allowed_styles for row in labels):
        errors.append("style_label_outside_3x4_box")
    if int(quality.get("unclassified_stock_count") or 0) != 0:
        errors.append("style_unclassified_stock")
    if int(quality.get("duplicate_label_count") or 0) != 0:
        errors.append("style_duplicate_stock")
    if int(quality.get("latest_labelled_stock_count") or 0) != len(labels):
        errors.append("style_label_count_mismatch")

    return {
        "status": "ok" if not errors else "failed",
        "errors": errors,
        "schema_version": payload.get("schema_version"),
        "industry_count": len(industries),
        "field_count": field_count,
        "live_field_count": sum(int(row.get("live_indicators", 0)) for row in industries),
        "min_live_per_industry": min((int(row.get("live_indicators", 0)) for row in industries), default=0),
        "style_cell_count": int(style.get("count") or 0),
        "style_labelled_stock_count": len(labels),
        "style_frequency": "quarterly",
        "as_of": payload.get("as_of"),
    }

def rotation_index():
    return render_template(
        "index.html",
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
        public_payload = dict(payload)
        public_style = dict(payload.get("style", {}))
        public_style.pop("stock_labels", None)
        public_style["stock_labels_endpoint"] = "/api/rotation/style-labels"
        public_payload["style"] = public_style
        return jsonify(public_payload)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "failed", "message": str(exc)}), 503


@app.get("/api/rotation/style-labels")
def rotation_style_labels():
    """Return the latest quarterly stock labels on demand."""
    try:
        payload = _load_json(ROTATION_SNAPSHOT)
        contract = _snapshot_contract(payload)
        if contract["status"] != "ok":
            return jsonify({"status": "failed", "quality": contract}), 503

        style = payload.get("style", {})
        labels = style.get("stock_labels", [])
        allowed_cells = {
            str(row.get("cell"))
            for row in style.get("cells", [])
            if row.get("cell")
        }
        cell = str(request.args.get("cell") or "").strip()
        query = str(request.args.get("q") or "").strip().casefold()[:64]
        if cell and cell not in allowed_cells:
            return jsonify({"status": "failed", "message": "unknown_style_cell"}), 400

        rows = labels
        if cell:
            rows = [row for row in rows if str(row.get("cell") or "") == cell]
        if query:
            rows = [
                row
                for row in rows
                if query in f"{row.get('code', '')} {row.get('name', '')}".casefold()
            ]

        limit = max(1, min(int(request.args.get("limit") or 120), 200))
        offset = max(0, int(request.args.get("offset") or 0))
        total = len(rows)
        return jsonify({
            "status": "ok",
            "as_of": style.get("latest_signal_date") or payload.get("as_of"),
            "cell": cell or "全部",
            "query": query,
            "total": total,
            "offset": offset,
            "limit": limit,
            "rows": rows[offset:offset + limit],
        })
    except (TypeError, ValueError):
        return jsonify({"status": "failed", "message": "invalid_pagination"}), 400
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

@app.get("/api/rotation/research-dashboard")
def rotation_research_dashboard():
    """Return the full industry prosperity and six-dimension rotation research dashboard."""
    try:
        payload = _load_json(ROTATION_RESEARCH_DASHBOARD)
        prosperity = payload.get("prosperity", {})
        rotation = payload.get("rotation", {})
        errors: list[str] = []
        if payload.get("schema_version") != "1.0":
            errors.append("schema_version_not_1")
        if len(prosperity.get("industries", [])) != 31:
            errors.append("prosperity_industry_count_not_31")
        if len(prosperity.get("industry_detail", {})) != 31:
            errors.append("prosperity_detail_count_not_31")
        if len(rotation.get("factor_table", [])) < 60:
            errors.append("rotation_factor_table_too_short")
        if len(rotation.get("ranking", [])) != 31:
            errors.append("rotation_ranking_count_not_31")
        figures = rotation.get("figures", {})
        for field in ("annual_table", "daily_nav"):
            value = str(figures.get(field) or "")
            if value and (not value.startswith("/static/rotation_figures/") or not (ROOT / value.lstrip("/")).exists()):
                errors.append(f"rotation_{field}_missing")
        if errors:
            return jsonify({"status": "failed", "errors": errors}), 503
        public_payload = dict(payload)
        public_payload["status"] = "ok"
        return jsonify(public_payload)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "failed", "message": str(exc)}), 503


@app.get("/api/rotation/style-research-dashboard")
def rotation_style_research_dashboard():
    """Return the full five-factor style rotation research dashboard."""
    try:
        payload = _overlay_style_research_figures(_load_json(ROTATION_STYLE_RESEARCH))
        strategies = payload.get("strategies", {})
        errors: list[str] = []
        if payload.get("schema_version") != "1.2":
            errors.append("style_schema_version_not_1_2")
        expected = {"style12": 12, "size3": 3, "style4": 4}
        if set(strategies) != set(expected):
            errors.append("style_strategy_keys_contract")
        for key, count in expected.items():
            strategy = strategies.get(key, {})
            if len(strategy.get("groups", [])) != count:
                errors.append(f"{key}_group_count_contract")
            if len(strategy.get("factor_table", [])) < 100:
                errors.append(f"{key}_factor_table_too_short")
            if not strategy.get("factor_details"):
                errors.append(f"{key}_factor_details_missing")
            if not strategy.get("efficient_factors"):
                errors.append(f"{key}_efficient_factors_missing")
            if not strategy.get("ytd_top_bottom"):
                errors.append(f"{key}_ytd_top_bottom_missing")
            if not strategy.get("annual_attribution"):
                errors.append(f"{key}_annual_attribution_missing")
            for figure_group, label in ((strategy.get("figures", {}), "long"), (strategy.get("long_short_figures", {}), "long_short")):
                for field in ("annual_table", "daily_nav"):
                    value = str(figure_group.get(field) or "")
                    if not value.startswith("/static/rotation_figures/"):
                        errors.append(f"{key}_{label}_{field}_path_contract")
                    elif not (ROOT / value.lstrip("/")).exists():
                        errors.append(f"{key}_{label}_{field}_missing")
        if errors:
            return jsonify({"status": "failed", "errors": errors}), 503
        payload["status"] = "ok"
        return jsonify(payload)
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


@app.get("/api/rotation/final-figures")
def rotation_final_figures():
    """Return final daily performance figure manifest for industry/style rotation."""
    try:
        source = ROTATION_FINAL_FIGURES if ROTATION_FINAL_FIGURES.exists() else ROTATION_FINAL_FIGURES_STATIC
        payload = _load_json(source)
        figures = payload.get("figures", {})
        expected = {"industry_monthly", "style12", "size3", "style4"}
        if set(figures) != expected:
            return jsonify({"status": "failed", "message": "rotation_final_figures_contract"}), 503
        for key, row in figures.items():
            figure_groups = [(row, "")]
            if isinstance(row.get("long_short"), dict):
                figure_groups.append((row["long_short"], "long_short_"))
            for figure_group, prefix in figure_groups:
                for field in ("annual_table", "daily_nav"):
                    value = str(figure_group.get(field) or "")
                    if not value.startswith("/static/rotation_figures/"):
                        return jsonify({"status": "failed", "message": f"{key}_{prefix}{field}_path_contract"}), 503
                    if not (ROOT / value.lstrip("/")).exists():
                        return jsonify({"status": "failed", "message": f"{key}_{prefix}{field}_missing"}), 503
        return jsonify(payload)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "failed", "message": str(exc)}), 503
