"""Canonical public dashboard application entry point.

This is the only supported web entry. It composes the base dashboard, the
industry-rotation routes and the Factor Laboratory API without release-layer
overlays, then applies transport caching/compression in one place.
"""
from __future__ import annotations

import gzip
import hashlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from flask import Response, jsonify, render_template, request, session

import app as legacy
import factor_lab_backend
import rotation_app as rotation


APP_VERSION = "2026.07.24-research-workspace-r17.4-ai-monitor-rotation-ui"
legacy.APP_VERSION = APP_VERSION
rotation.APP_VERSION = APP_VERSION

PROJECT_ROOT = Path(__file__).resolve().parents[2]
factor_lab_backend.API_VERSION = "factor-lab-api/2.1"
factor_lab_backend.ENGINE_PATH = (
    PROJECT_ROOT / "model" / "factor_laboratory" / "worker.py"
)
for key, label in {
    "lstm": "LSTM",
    "rl_transformer": "RL+Transformer",
    "strategy": "OLS / Lasso / 深度模型",
    "joint_test": "联合检验",
}.items():
    if key in factor_lab_backend.MODEL_PRESETS:
        factor_lab_backend.MODEL_PRESETS[key]["label"] = label
        factor_lab_backend.MODEL_PRESETS[key]["architecture"] = []

app = rotation.app
if "factor_lab" not in app.blueprints:
    factor_lab_backend.register_factor_lab(app)


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


def _service_payload() -> dict[str, Any]:
    checks: dict[str, Callable[[], dict[str, Any]]] = {
        "board": lambda: legacy.safe_proxy("board", "/healthz"),
        "kline": lambda: legacy.safe_proxy("kline", "/health"),
        "factor": lambda: legacy.safe_proxy("factor", "/api/status"),
        "ai_monitor": lambda: legacy.safe_proxy("ai_monitor", "/healthz"),
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
    ttl = 90 if clean_path in {"api/snapshot", "api/dynamic-series"} else 300

    def load() -> Any:
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

    return jsonify(legacy.cached_data(cache_key, ttl, load))

_CACHEABLE_API_ENDPOINTS = {
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
    "global_market_supplement",
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
