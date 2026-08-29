"""Canonical public dashboard application entry point.

This is the only supported web entry. It composes the base dashboard, the
industry-rotation routes and the Factor Laboratory API without release-layer
overlays, then applies transport caching/compression in one place.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

# Stable production defaults for the audited optimizer LLM constraint compiler.
# Existing deployment secrets or endpoints are preserved; these only fill missing knobs.
os.environ.setdefault('AI_ROUTER_MODEL', 'gpt-5.5')
os.environ.setdefault('AI_ROUTER_REASONING_EFFORT', 'xhigh')
os.environ.setdefault('AI_ROUTER_TIMEOUT_SECONDS', '180')
os.environ.setdefault('AI_ROUTER_MAX_TOKENS', '1400')

from flask import Response, jsonify, render_template, request, session

import app as legacy
import factor_lab_backend
import model_governance_backend
import rotation_app as rotation
import optimizer_backend
import technical_factor_backend


APP_VERSION = "2026.08.29-portfolio-full-framework-v1"
legacy.APP_VERSION = APP_VERSION
rotation.APP_VERSION = APP_VERSION

PROJECT_ROOT = Path(__file__).resolve().parents[2]
factor_lab_backend.API_VERSION = "factor-lab-api/3.0-full-framework"
factor_lab_backend.ENGINE_PATH = (
    PROJECT_ROOT / "model" / "factor_laboratory" / "worker.py"
)
for key, label in {
    "lstm": "LSTM",
    "gru": "GRU",
    "rl_transformer": "Transformer+LLM",
    "strategy": "等权 / RankIC / OLS / Lasso / Ridge / LSTM 打分回测",
    "joint_test": "联合检验",
}.items():
    if key in factor_lab_backend.MODEL_PRESETS:
        factor_lab_backend.MODEL_PRESETS[key]["label"] = label


app = rotation.app
if "factor_lab" not in app.blueprints:
    factor_lab_backend.register_factor_lab(app)
if "optimizer" not in app.blueprints:
    optimizer_backend.register_optimizer(app)


def index() -> str:
    """Render the single production template and its canonical asset list."""
    return render_template(
        "index_rotation_factor_lab.html",
        authenticated=True,
        user=session.get("user") or legacy.USERNAME,
        app_version=APP_VERSION,
        public_host=legacy.PUBLIC_HOST,
    )


app.view_functions["index"] = index

_SERVICE_LOCK = threading.RLock()
_SERVICE_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}


def _rotation_health() -> dict[str, Any]:
    return rotation._snapshot_contract(rotation._load_json(rotation.ROTATION_SNAPSHOT))


def _factor_lab_health() -> dict[str, Any]:
    health = factor_lab_backend.bootstrap_payload()
    return {
        "status": health.get("status"),
        "version": factor_lab_backend.API_VERSION,
        "database_available": (health.get("data") or {}).get("database_available"),
        "worker_isolated": (health.get("worker") or {}).get("isolated_process"),
    }


TRUMP_BLOCKED_ORIGINS = ("ocmacro.com",)


def _contains_blocked_trump_origin(value: Any) -> bool:
    return isinstance(value, str) and any(origin in value.lower() for origin in TRUMP_BLOCKED_ORIGINS)


def _scrub_trump_payload(value: Any) -> Any:
    if isinstance(value, list):
        cleaned: list[Any] = []
        for item in value:
            if isinstance(item, dict) and any(_contains_blocked_trump_origin(str(v)) for v in item.values()):
                continue
            next_item = _scrub_trump_payload(item)
            if next_item is not None:
                cleaned.append(next_item)
        return cleaned
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, child in value.items():
            if key.lower() in {"url", "href", "link", "sourceurl", "source_url", "aggregator"} and _contains_blocked_trump_origin(child):
                continue
            cleaned_child = _scrub_trump_payload(child)
            if cleaned_child is not None:
                output[key] = cleaned_child
        return output
    if _contains_blocked_trump_origin(value):
        return ""
    return value


def _trump_payload_has_official_data(payload: dict[str, Any]) -> bool:
    if payload.get("events") or payload.get("tacoEvents"):
        return True
    pressure = payload.get("pressure") if isinstance(payload.get("pressure"), dict) else {}
    if pressure.get("available") and pressure.get("series"):
        return True
    approval = payload.get("approval") if isinstance(payload.get("approval"), dict) else {}
    if approval.get("current") or approval.get("series"):
        return True
    markets = payload.get("markets") if isinstance(payload.get("markets"), dict) else {}
    return any(isinstance(item, dict) and item.get("value") is not None for item in markets.values())


def _normalize_trump_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    cleaned = _scrub_trump_payload(payload)
    if not isinstance(cleaned, dict):
        return cleaned
    if cleaned.get("status") == "failed" and _trump_payload_has_official_data(cleaned):
        cleaned["status"] = "partial"
        cleaned["data_state"] = "reference_pressure_unavailable"
    return cleaned


def _trump_health_payload() -> dict[str, Any]:
    try:
        payload = _normalize_trump_payload(legacy.proxy_json("trump", "/api/tracker", query={"scope": "core"}, timeout=70))
        if not isinstance(payload, dict):
            return {"status": "failed", "message": "trump_payload_unavailable"}
        state = str(payload.get("status") or "failed")
        usable = _trump_payload_has_official_data(payload)
        return {
            "status": "ok" if usable else "failed",
            "data_state": state,
            "generated_at": payload.get("generatedAt"),
            "as_of": payload.get("asOf") or (payload.get("pressure") or {}).get("asOfDate"),
            "pressure_available": bool((payload.get("pressure") or {}).get("available")),
            "events": len(payload.get("events") or []),
            "taco_events": len(payload.get("tacoEvents") or []),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "message": str(exc)}


def _service_payload() -> dict[str, Any]:
    checks: dict[str, Callable[[], dict[str, Any]]] = {
        "board": lambda: legacy.safe_proxy("board", "/healthz"),
        "kline": lambda: legacy.safe_proxy("kline", "/health"),
        "factor": lambda: legacy.safe_proxy("factor", "/api/status"),
        "ai_monitor": lambda: legacy.safe_proxy("ai_monitor", "/healthz"),
        "trump": _trump_health_payload,
        "allocation": legacy.allocation_health_payload,
        "liquidity": legacy.liquidity_health_payload,
        "index_enhancement": legacy.index_enhancement_health_payload,
        "portfolio": legacy.portfolio_health_payload,
        "rotation": _rotation_health,
        "factor_lab": _factor_lab_health,
    }
    services: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=len(checks), thread_name_prefix="health") as pool:
        futures = {pool.submit(func): name for name, func in checks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                services[name] = future.result()
            except Exception as exc:  # noqa: BLE001
                services[name] = {"status": "failed", "message": str(exc)}
    return {
        "status": "ok",
        "version": APP_VERSION,
        "checked_at": legacy.iso_now(),
        "services": services,
    }


def services() -> Response:
    """Return one concurrently checked, short-lived health snapshot."""
    now = time.time()
    with _SERVICE_LOCK:
        cached = _SERVICE_CACHE.get("payload")
        if cached and now - float(_SERVICE_CACHE.get("at") or 0) < 60:
            return jsonify(cached)
    payload = _service_payload()
    with _SERVICE_LOCK:
        _SERVICE_CACHE.update({"at": now, "payload": payload})
    return jsonify(payload)


app.view_functions["services"] = services




# data_dashboard_stock_reports_v1: stock news and latest research reports for Data Dashboard.
def _dashboard_stock_code(code: str) -> str:
    raw = str(code or "").strip()
    try:
        norm = legacy.normalize_a_code(raw)
    except Exception:  # noqa: BLE001
        norm = ""
    digits = re.sub(r"\D", "", str(norm or raw))
    return digits[-6:] if len(digits) >= 6 else digits


def _dashboard_limit(default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(request.args.get("limit", str(default)))
    except Exception:  # noqa: BLE001
        value = default
    return max(minimum, min(maximum, value))


def _dashboard_clean(value: Any) -> str:
    if value is None:
        return ""
    try:
        cleaned = legacy._stock_ai_clean_value(value)
    except Exception:  # noqa: BLE001
        cleaned = value
    text = str(cleaned or "").strip()
    return "" if text.lower() in {"nan", "nat", "none", "--"} else text


def _dashboard_pick_col(columns: list[Any], patterns: tuple[str, ...], fallback: int = 0) -> Any:
    for pattern in patterns:
        rx = re.compile(pattern, re.IGNORECASE)
        for column in columns:
            if rx.search(str(column)):
                return column
    if not columns:
        return ""
    index = fallback if fallback >= 0 else len(columns) + fallback
    index = max(0, min(len(columns) - 1, index))
    return columns[index]


def _dashboard_pdf_url(value: Any) -> str:
    url = _dashboard_clean(value)
    lower = url.lower()
    if not url:
        return ""
    is_direct_pdf = lower.endswith(".pdf") or "/pdf/" in lower or "attachment" in lower
    if "warrenq" in lower and not is_direct_pdf:
        return ""
    if is_direct_pdf:
        return url
    return ""


def _dashboard_report_row(row: Any, norm: str, fallback_name: str = "", source: str = "") -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    title = (
        _dashboard_clean(row.get("title"))
        or _dashboard_clean(row.get("研报名称"))
        or _dashboard_clean(row.get("报告名称"))
        or _dashboard_clean(row.get("report_title"))
    )
    if not title:
        return None
    pdf_url = (
        _dashboard_pdf_url(row.get("pdf_url"))
        or _dashboard_pdf_url(row.get("报告PDF链接"))
        or _dashboard_pdf_url(row.get("pdf"))
        or _dashboard_pdf_url(row.get("url"))
    )
    summary = (
        _dashboard_clean(row.get("summary"))
        or _dashboard_clean(row.get("摘要"))
        or _dashboard_clean(row.get("要点"))
        or _dashboard_clean(row.get("abstract"))
        or title
    )
    broker = (
        _dashboard_clean(row.get("broker"))
        or _dashboard_clean(row.get("机构"))
        or _dashboard_clean(row.get("研报机构"))
        or _dashboard_clean(row.get("institution"))
    )
    published_at = (
        _dashboard_clean(row.get("published_at"))
        or _dashboard_clean(row.get("日期"))
        or _dashboard_clean(row.get("date"))
        or _dashboard_clean(row.get("time"))
    )[:19]
    rating = _dashboard_clean(row.get("评级"))
    name = (
        _dashboard_clean(row.get("name"))
        or _dashboard_clean(row.get("股票名称"))
        or fallback_name
    )
    return {
        "code": norm,
        "name": name,
        "title": title[:260],
        "summary": summary[:900],
        "broker": broker,
        "rating": rating,
        "published_at": published_at,
        "source": source or _dashboard_clean(row.get("source")) or "WarrenQ缓存",
        "pdf_url": pdf_url,
        "url": pdf_url,
    }


def _dashboard_stock_identity(norm: str) -> dict[str, Any]:
    try:
        identity = legacy._stock_identity_from_universe(norm)
        return identity if isinstance(identity, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _dashboard_warrenq_reports(norm: str, name: str, limit: int) -> list[dict[str, Any]]:
    cache_path = Path(__file__).resolve().parent / "data" / "warrenq_reports_cache.json"
    if not cache_path.exists():
        return []
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    raw_rows: list[Any] = []
    if isinstance(payload, dict):
        for key in (norm, f"{norm}.SZ", f"{norm}.SH", f"{norm}.BJ"):
            value = payload.get(key)
            if isinstance(value, list):
                raw_rows.extend(value)
        if not raw_rows:
            for key, value in payload.items():
                if norm in str(key) and isinstance(value, list):
                    raw_rows.extend(value)
    elif isinstance(payload, list):
        for row in payload:
            text = json.dumps(row, ensure_ascii=False, default=str)
            if norm in text or (name and name in text):
                raw_rows.append(row)
    reports: list[dict[str, Any]] = []
    for row in raw_rows:
        item = _dashboard_report_row(row, norm, name, "WarrenQ缓存")
        if item and item.get("pdf_url"):
            reports.append(item)
        if len(reports) >= limit:
            break
    return reports


def _dashboard_akshare_reports(norm: str, name: str, limit: int) -> list[dict[str, Any]]:
    def builder() -> list[dict[str, Any]]:
        import akshare as ak  # type: ignore

        frame = ak.stock_research_report_em(symbol=norm)
        if frame is None or getattr(frame, "empty", True):
            return []
        columns = list(frame.columns)
        title_col = _dashboard_pick_col(columns, (r"研报.*名|报告.*名|title",), 3)
        rating_col = _dashboard_pick_col(columns, (r"评级|rating",), 4)
        broker_col = _dashboard_pick_col(columns, (r"机构|券商|broker|institution",), 5)
        name_col = _dashboard_pick_col(columns, (r"股票名称|名称|name",), 2)
        date_col = _dashboard_pick_col(columns, (r"日期|时间|date|time",), -2)
        pdf_col = _dashboard_pick_col(columns, (r"PDF|pdf|链接|url|link",), -1)
        rows: list[dict[str, Any]] = []
        for _, record in frame.head(max(limit * 4, 20)).iterrows():
            title = _dashboard_clean(record.get(title_col))
            if not title:
                continue
            broker = _dashboard_clean(record.get(broker_col))
            rating = _dashboard_clean(record.get(rating_col))
            published_at = _dashboard_clean(record.get(date_col))[:19]
            pdf_url = _dashboard_pdf_url(record.get(pdf_col))
            if not pdf_url:
                continue
            stock_name = _dashboard_clean(record.get(name_col)) or name
            summary_parts = []
            if rating:
                summary_parts.append(f"评级：{rating}")
            summary_parts.append(title)
            rows.append({
                "code": norm,
                "name": stock_name,
                "title": title[:260],
                "summary": "；".join(summary_parts)[:900],
                "broker": broker,
                "rating": rating,
                "published_at": published_at,
                "source": "东方财富个股研报",
                "pdf_url": pdf_url,
                "url": pdf_url,
            })
            if len(rows) >= limit:
                break
        return rows

    try:
        caller = getattr(legacy, "_stock_ai_call_without_dead_local_proxy", None)
        return caller(builder) if callable(caller) else builder()
    except Exception:  # noqa: BLE001
        return []


def _dashboard_dedupe_reports(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.get("pdf_url") or "") or f"{row.get('published_at')}|{row.get('title')}"
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(row)
        if len(output) >= limit:
            break
    return output


def _dashboard_build_stock_reports(norm: str, limit: int) -> dict[str, Any]:
    identity = _dashboard_stock_identity(norm)
    name = str(identity.get("name") or "")
    reports = _dashboard_warrenq_reports(norm, name, limit)
    if len(reports) < limit:
        reports = _dashboard_dedupe_reports(
            reports + _dashboard_akshare_reports(norm, name, limit * 2),
            limit,
        )
    return {
        "status": "ok" if reports else "partial",
        "code": norm,
        "name": name,
        "count": len(reports),
        "reports": reports,
        "rows": reports,
        "source": "WarrenQ缓存优先；缺口使用东方财富个股研报",
    }


def _dashboard_build_stock_news(norm: str, limit: int) -> dict[str, Any]:
    identity = _dashboard_stock_identity(norm)
    name = str(identity.get("name") or "")
    rows: list[dict[str, Any]] = []
    try:
        rows = legacy._stock_ai_news_akshare(norm, name, limit)
    except Exception:  # noqa: BLE001
        rows = []
    if not rows:
        global_news: list[dict[str, Any]] = []
        for builder in (legacy.build_sina_news, legacy.build_wallstreet_wechat_news):
            try:
                global_news.extend((builder(80).get("rows") or [])[:80])
            except Exception:  # noqa: BLE001
                pass
        try:
            rows = legacy._stock_ai_related_news(norm, name, global_news, limit)
        except Exception:  # noqa: BLE001
            rows = []
    return {
        "status": "ok" if rows else "partial",
        "code": norm,
        "name": name,
        "count": len(rows[:limit]),
        "rows": rows[:limit],
        "source": "东方财富个股新闻",
    }


@app.get("/api/stock/news/<code>")
def data_dashboard_stock_news(code: str) -> Response:
    norm = _dashboard_stock_code(code)
    if not re.fullmatch(r"\d{6}", norm or ""):
        return jsonify({"status": "failed", "message": "invalid_stock_code", "rows": []}), 400
    limit = _dashboard_limit(20, 5, 60)
    payload = legacy.cached_data(
        f"data-dashboard:stock-news:{norm}:r45.17",
        900,
        lambda: _dashboard_build_stock_news(norm, limit),
    )
    return jsonify(payload)


@app.get("/api/stock/reports/<code>")
def data_dashboard_stock_reports(code: str) -> Response:
    norm = _dashboard_stock_code(code)
    if not re.fullmatch(r"\d{6}", norm or ""):
        return jsonify({"status": "failed", "message": "invalid_stock_code", "reports": [], "rows": []}), 400
    limit = _dashboard_limit(5, 1, 12)
    payload = legacy.cached_data(
        f"data-dashboard:stock-reports:{norm}:r45.19",
        3600,
        lambda: _dashboard_build_stock_reports(norm, limit),
    )
    return jsonify(payload)


@app.get("/api/model-governance")
def model_governance() -> Response:
    """Expose the immutable split and promotion evidence used by the UI."""
    return jsonify(model_governance_backend.build_model_governance())



@app.get("/api/technical-factor/dashboard")
def technical_factor_dashboard() -> Response:
    """Expose the audited OHLCV technical-factor research dashboard."""
    refresh = request.args.get("refresh") == "1"
    return jsonify(technical_factor_backend.build_dashboard_snapshot(refresh=refresh))

@app.get("/api/ai-monitor/<path:upstream_path>")
def ai_monitor_proxy(upstream_path: str) -> Response:
    """Expose the authenticated technology-diffusion JSON API in this app."""
    clean_path = upstream_path.strip("/")
    if not clean_path.startswith("api/") or ".." in clean_path.split("/"):
        return jsonify({"status": "failed", "message": "invalid_ai_monitor_path"}), 400
    query = request.args.to_dict(flat=False)
    encoded_query = legacy.urllib.parse.urlencode(query, doseq=True)
    cache_key = f"ai-monitor:{clean_path}?{encoded_query}"
    upstream_api_path = "/" + "/".join(
        legacy.urllib.parse.quote(segment, safe="") for segment in clean_path.split("/")
    )
    if clean_path == "api/snapshot":
        ttl = 21_600
    elif clean_path == "api/dynamic-series":
        ttl = 900
    else:
        ttl = 1_800

    def load() -> Any:
        last_error: legacy.ProxyError | None = None
        for attempt in range(2):
            try:
                if attempt:
                    legacy.service_session("ai_monitor").authenticated_at = 0.0
                    time.sleep(0.9)
                legacy.ensure_service_login("ai_monitor")
                payload = legacy.proxy_json(
                    "ai_monitor",
                    upstream_api_path,
                    query=query,
                    auth=True,
                    timeout=35,
                )
                if isinstance(payload, dict) and "raw" in payload:
                    legacy.service_session("ai_monitor").authenticated_at = 0.0
                    legacy.ensure_service_login("ai_monitor")
                    payload = legacy.proxy_json(
                        "ai_monitor",
                        upstream_api_path,
                        query=query,
                        auth=True,
                        timeout=35,
                    )
                if isinstance(payload, dict) and "raw" in payload:
                    raise legacy.ProxyError("ai_monitor", 502, "upstream_returned_html")
                return payload
            except legacy.ProxyError as exc:
                last_error = exc
                legacy.service_session("ai_monitor").authenticated_at = 0.0
                if attempt == 0 and exc.status in {401, 403, 502, 503, 504}:
                    continue
                raise
        if last_error:
            raise last_error
        raise legacy.ProxyError("ai_monitor", 502, "upstream_unavailable")

    return jsonify(legacy.cached_data(cache_key, ttl, load))

@app.get("/api/trump/core")
def trump_core_proxy() -> Response:
    """Expose the verified Trump policy research payload through the main shell."""
    refresh = request.args.get("refresh") == "1"
    query = {"scope": "core"}
    if refresh:
        query["refresh"] = "1"

    def load() -> Any:
        payload = _normalize_trump_payload(legacy.proxy_json("trump", "/api/tracker", query=query, timeout=70))
        if not isinstance(payload, dict) or not isinstance(payload.get("pressure"), dict):
            raise legacy.ProxyError("trump", 502, "upstream_payload_unavailable")
        payload.setdefault("status", "failed")
        return payload

    payload = load() if refresh else legacy.cached_data("trump:core:v3", 300, load)
    return jsonify(payload)


@app.get("/api/trump/truths")
def trump_truths_proxy() -> Response:
    """Proxy the validated public Truth Social archive without exposing a second origin."""
    allowed = ("category", "limit", "offset", "period", "search", "sort", "topic", "type")
    query = {"scope": "truths"}
    for key in allowed:
        if key in request.args:
            query[key] = request.args.get(key, "")
    fingerprint = hashlib.sha256(repr(sorted(query.items())).encode("utf-8")).hexdigest()[:16]

    def load() -> Any:
        payload = _normalize_trump_payload(legacy.proxy_json("trump", "/api/tracker", query=query, timeout=35))
        if not isinstance(payload, dict) or "truths" not in payload:
            raise legacy.ProxyError("trump", 502, "truth_archive_unavailable")
        payload.setdefault("status", "failed")
        return payload

    return jsonify(legacy.cached_data("trump:truths:" + fingerprint, 300, load))

_CACHEABLE_API_ENDPOINTS = {
    "asset_allocation_interactive",
    "allocation_snapshot",
    "liquidity_snapshot",
    "index_enhancement_snapshot",
    "portfolio_snapshot",
    "board_snapshot",
    "board_series",
    "board_stock",
    "rotation_snapshot",
    "rotation_tracking",
    "rotation_industry_dashboard",
    "factor_lab.bootstrap",
    "factor_lab.catalog",
    "factor_lab.dashboard",
    "services",
    "model_governance",
    "global_market_supplement",
    "domestic_demand_snapshot",
    "trump_core_proxy",
    "trump_truths_proxy",
    "kline_llm_dashboard",
    "kline_llm_rule_context",
    "kline_llm_stock",
    "technical_factor_dashboard",
    "kline_health",
    "kline_stocks",
    "kline_dates",
    "kline_history",
    "kline_job",
    "factor_status",
    "factor_history",
    "factor_history_detail",
    "ai_monitor_proxy",
}


@app.get("/api/asset-allocation/interactive", endpoint="asset_allocation_interactive")
def asset_allocation_interactive() -> Response:
    payload_path = Path(__file__).resolve().parent / "data" / "asset_allocation_interactive_v66.json"
    if not payload_path.exists():
        return jsonify({"status": "failed", "message": "asset_allocation_interactive_payload_missing"}), 503
    return jsonify(json.loads(payload_path.read_text(encoding="utf-8")))

@app.after_request
def optimize_transport(response: Response) -> Response:
    """Add conditional caching and gzip for large public-dashboard payloads."""
    if request.method != "GET" or response.status_code not in {200, 203}:
        return response

    endpoint = request.endpoint or ""
    is_cacheable_api = endpoint in _CACHEABLE_API_ENDPOINTS and not (
        endpoint == "kline_job" and request.args.get("live") == "1"
    )
    if is_cacheable_api:
        response.headers["Cache-Control"] = "private, max-age=300, stale-while-revalidate=86400"

    mimetype = (response.mimetype or "").lower()
    compressible = (
        mimetype.startswith("text/")
        or mimetype in {"application/json", "application/javascript", "application/xml", "image/svg+xml"}
    )
    accepts_gzip = "gzip" in request.headers.get("Accept-Encoding", "").lower()
    if not (is_cacheable_api or (compressible and accepts_gzip)):
        return response

    if response.direct_passthrough:
        response.direct_passthrough = False
    raw = response.get_data()
    if is_cacheable_api:
        response.set_etag(hashlib.blake2b(raw, digest_size=16).hexdigest())
        response.make_conditional(request)
        if response.status_code == 304:
            return response

    if (
        accepts_gzip
        and compressible
        and len(raw) >= 1024
        and not response.headers.get("Content-Encoding")
    ):
        compressed = gzip.compress(raw, compresslevel=5, mtime=0)
        if len(compressed) < len(raw):
            response.set_data(compressed)
            response.headers["Content-Encoding"] = "gzip"
            response.headers["Content-Length"] = str(len(compressed))
            response.headers.add("Vary", "Accept-Encoding")
    return response


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8071")),
        threaded=True,
    )
