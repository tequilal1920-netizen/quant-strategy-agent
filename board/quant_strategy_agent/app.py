"""Unified public entry for the Quant Strategy Agent.

This app intentionally does not modify the three existing production apps. It
acts as a login-protected shell plus a narrow API proxy to:

- market data dashboard on 127.0.0.1:8070 / public :10007
- K-line memory learning service on 127.0.0.1:8877 / public /kline
- factor mining service on 127.0.0.1:8895 / public /factor-mining
"""

from __future__ import annotations

import json
import csv
import hashlib
import html
import math
import os
import re
import ssl
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for


def load_private_env() -> None:
    """Load server-side secrets without exposing them to templates or JS."""
    candidates = [Path(__file__).resolve().parent / "private" / "quant_agent.env"]
    extra = os.environ.get("QUANT_AGENT_ENV_FILE")
    if extra:
        candidates.insert(0, Path(extra))
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


load_private_env()

APP_VERSION = "2026.08.29-industry-style-full-framework-v1"
PUBLIC_HOST = os.environ.get("QUANT_AGENT_PUBLIC_HOST", "https://desktop-i22b489.tailf9d7ac.ts.net").rstrip("/")
USERNAME = os.environ.get("QUANT_AGENT_USER", "")
PASSWORD = os.environ.get("QUANT_AGENT_PASSWORD", "")
SCRIPT_PREFIXES = [x.rstrip("/") for x in os.environ.get("QUANT_AGENT_PREFIXES", "/quant-agent").split(",") if x.strip().startswith("/")]
AI_ROUTER_KEY = os.environ.get("AI_ROUTER_API_KEY", "")
_AI_ROUTER_BASE_URL = (os.environ.get("AI_ROUTER_BASE_URL") or "").rstrip("/")
AI_ROUTER_URL = os.environ.get("AI_ROUTER_URL") or ((_AI_ROUTER_BASE_URL + "/chat/completions") if _AI_ROUTER_BASE_URL else "https://ai.router.team/v1/chat/completions")
AI_ROUTER_MODEL = os.environ.get("AI_ROUTER_MODEL", "gpt-5.5")
AI_ROUTER_REASONING_EFFORT = os.environ.get("AI_ROUTER_REASONING_EFFORT", "xhigh")
AI_ROUTER_MAX_COMPLETION_TOKENS = int(os.environ.get("AI_ROUTER_MAX_COMPLETION_TOKENS", "1200"))
AI_CACHE_TTL_SECONDS = int(os.environ.get("AI_CACHE_TTL_SECONDS", "21600"))
AI_CACHE: dict[str, tuple[float, str]] = {}
DATA_CACHE_LOCK = threading.RLock()
DATA_CACHE: dict[str, tuple[float, Any]] = {}
ALLOCATION_SNAPSHOT_PATH = Path(
    os.environ.get(
        "ASSET_ALLOCATION_SNAPSHOT",
        str(Path(__file__).resolve().parent / "data" / "asset_allocation_snapshot.json"),
    )
).resolve()
ALLOCATION_CACHE_LOCK = threading.RLock()
ALLOCATION_CACHE: dict[str, Any] = {"mtime_ns": None, "payload": None}
LIQUIDITY_SNAPSHOT_PATH = Path(
    os.environ.get(
        "LIQUIDITY_SNAPSHOT",
        str(Path(__file__).resolve().parent / "data" / "liquidity_snapshot.json"),
    )
).resolve()
LIQUIDITY_CACHE_LOCK = threading.RLock()
LIQUIDITY_CACHE: dict[str, Any] = {"mtime_ns": None, "payload": None}
LIQUIDITY_SOURCE_CACHE_PATH = Path(
    os.environ.get(
        "LIQUIDITY_SOURCE_CACHE",
        str(Path(__file__).resolve().parent / "data" / "liquidity_source_cache.sqlite"),
    )
).resolve()
LIQUIDITY_EXTERNAL_CACHE_DIR = Path(
    os.environ.get(
        "LIQUIDITY_EXTERNAL_CACHE_DIR",
        str(Path(__file__).resolve().parent / "data" / "liquidity_external_cache"),
    )
).resolve()
LIQUIDITY_AUTO_REFRESH = os.environ.get("LIQUIDITY_AUTO_REFRESH", "1") != "0"
LIQUIDITY_REFRESH_MIN_SECONDS = max(900, int(os.environ.get("LIQUIDITY_REFRESH_MIN_SECONDS", "21600")))
LIQUIDITY_REFRESH_START = os.environ.get("LIQUIDITY_REFRESH_START", "2010-01-01")
LIQUIDITY_REFRESH_LOCK = threading.RLock()
LIQUIDITY_REFRESH_STATE: dict[str, Any] = {"running": False, "last_started": 0.0, "last_finished": 0.0, "last_status": "idle"}
INDEX_ENHANCEMENT_SNAPSHOT_PATH = Path(
    os.environ.get(
        "INDEX_ENHANCEMENT_SNAPSHOT",
        str(Path(__file__).resolve().parent / "data" / "index_enhancement_snapshot.json"),
    )
).resolve()
INDEX_ENHANCEMENT_CACHE_LOCK = threading.RLock()
INDEX_ENHANCEMENT_CACHE: dict[str, Any] = {"mtime_ns": None, "payload": None}
PORTFOLIO_SNAPSHOT_PATH = Path(
    os.environ.get(
        "PORTFOLIO_OPTIMIZATION_SNAPSHOT",
        str(Path(__file__).resolve().parent / "data" / "portfolio_optimization_snapshot.json"),
    )
).resolve()
PORTFOLIO_CACHE_LOCK = threading.RLock()
PORTFOLIO_CACHE: dict[str, Any] = {"mtime_ns": None, "payload": None}
GLOBAL_MARKET_SNAPSHOT_PATH = Path(
    os.environ.get(
        "GLOBAL_MARKET_SNAPSHOT",
        str(Path(__file__).resolve().parent / "data" / "global_market_snapshot.json"),
    )
).resolve()
SW_L1_MEMBERSHIP_CACHE_PATH = Path(__file__).resolve().parent / "data" / "sw_l1_membership_cache.json"
SW_L1_MEMBERSHIP_LOCK = threading.RLock()
DOMESTIC_DEMAND_ROOT = Path(
    os.environ.get("DOMESTIC_DEMAND_ROOT", r"G:\招银理财\消费指数0519")
).resolve()
DEFAULT_STOCK_UNIVERSE_LOCAL_PATH = Path(__file__).resolve().parent / "data" / "all_a_stocks.json"
STOCK_UNIVERSE_LOCAL_PATH = Path(
    os.environ.get("STOCK_UNIVERSE_LOCAL_PATH", str(DEFAULT_STOCK_UNIVERSE_LOCAL_PATH))
).resolve()
STOCK_UNIVERSE_LEGACY_PATH = Path(r"G:\招银理财\消费指数0519\全部A股.xlsx")
WALLSTREET_WECHAT_ACCOUNT = os.environ.get("WALLSTREET_WECHAT_ACCOUNT", "华尔街见闻")
WALLSTREET_WECHAT_BIZ = os.environ.get("WALLSTREET_WECHAT_BIZ", "MjM5NzAwMzU0MA==")
WALLSTREET_WECHAT_EXPORT_DIR = Path(
    os.environ.get(
        "WALLSTREET_WECHAT_EXPORT_DIR",
        str(Path(__file__).resolve().parent / "data" / "wallstreet_wechat_export"),
    )
).resolve()
WALLSTREET_WECHAT_SHH_ROOT = Path(
    os.environ.get("WALLSTREET_WECHAT_SHH_ROOT", r"C:\Users\Rye\Desktop\Program\ELSE\ubantu\SHH\remote-windows-ssh")
).resolve()
WALLSTREET_WECHAT_ROOTS = [
    WALLSTREET_WECHAT_EXPORT_DIR,
    Path(os.environ.get("WALLSTREET_WECHAT_ROOT", r"C:\Users\Rye\Desktop\Program\ELSE\SHH\remote_wechat_results")).resolve(),
    Path(os.environ.get("WALLSTREET_WECHAT_ALT_ROOT", r"C:\Users\Rye\Desktop\Program\ELSE\SHH")).resolve(),
    Path(os.environ.get("WALLSTREET_WECHAT_ALT_ROOT_2", r"C:\Users\Rye\Desktop\Program\ELSE\ubantu\SHH")).resolve(),
]
WALLSTREET_WECHAT_HOST_ALIAS = os.environ.get("WALLSTREET_WECHAT_HOST_ALIAS", "homeserver")
WALLSTREET_WECHAT_REMOTE_PYTHON = os.environ.get("WALLSTREET_WECHAT_REMOTE_PYTHON", r"F:\apps\Access_wechat_article\.venv\Scripts\python.exe")
WALLSTREET_WECHAT_REMOTE_PLAYWRIGHT = os.environ.get("WALLSTREET_WECHAT_REMOTE_PLAYWRIGHT", r"F:\apps\Access_wechat_article\.venv\.playwright-browsers")
WALLSTREET_WECHAT_LOOKBACK_DAYS = max(2, int(os.environ.get("WALLSTREET_WECHAT_LOOKBACK_DAYS", "14")))
WALLSTREET_WECHAT_MAX_PAGES = max(1, int(os.environ.get("WALLSTREET_WECHAT_MAX_PAGES", "12")))
WALLSTREET_WECHAT_EXPORT_TIMEOUT_SECONDS = max(60, int(os.environ.get("WALLSTREET_WECHAT_EXPORT_TIMEOUT_SECONDS", "900")))

SERVICE_BASES: dict[str, list[str]] = {
    "board": [
        os.environ.get("BOARD_BASE_URL", "http://127.0.0.1:8070").rstrip("/"),
        f"{PUBLIC_HOST}:10007",
    ],
    "kline": [
        os.environ.get("KLINE_BASE_URL", "http://127.0.0.1:8877/kline").rstrip("/"),
        f"{PUBLIC_HOST}/kline",
    ],
    "factor": [
        os.environ.get("FACTOR_BASE_URL", "http://127.0.0.1:8895/factor-mining").rstrip("/"),
        f"{PUBLIC_HOST}/factor-mining",
    ],
    "ai_monitor": [
        os.environ.get("AI_MONITOR_BASE_URL", "http://127.0.0.1:8074/tech-diffusion").rstrip("/"),
        f"{PUBLIC_HOST}/tech-diffusion",
    ],
    "trump": [
        os.environ.get("TRUMP_BASE_URL", "http://127.0.0.1:8092").rstrip("/"),
        f"{PUBLIC_HOST}:10009",
    ],
}

SSL_CONTEXT = ssl._create_unverified_context()


@dataclass
class ProxySession:
    cookies: CookieJar
    opener: urllib.request.OpenerDirector
    authenticated_at: float = 0.0


PROXY_LOCK = threading.RLock()
PROXY_SESSIONS: dict[str, dict[str, ProxySession]] = {}


class ProxyError(RuntimeError):
    def __init__(self, service: str, status: int, message: str, payload: Any | None = None):
        super().__init__(message)
        self.service = service
        self.status = status
        self.message = message
        self.payload = payload


class PrefixMiddleware:
    """Allow the same Flask app to work at / and at a public path prefix."""

    def __init__(self, app: Any, prefixes: list[str]):
        self.app = app
        self.prefixes = sorted({p for p in prefixes if p}, key=len, reverse=True)

    def __call__(self, environ: dict[str, Any], start_response: Any) -> Any:
        path = environ.get("PATH_INFO", "") or ""
        for prefix in self.prefixes:
            if path == prefix or path.startswith(prefix + "/"):
                environ["SCRIPT_NAME"] = (environ.get("SCRIPT_NAME", "") or "") + prefix
                environ["PATH_INFO"] = path[len(prefix) :] or "/"
                break
        return self.app(environ, start_response)


def safe_login_return_url(value: str | None) -> str:
    """Return to this app's mounted root without escaping a reverse-proxy prefix."""
    parsed = urllib.parse.urlsplit((value or "/").strip())
    if parsed.scheme or parsed.netloc or parsed.path not in {"", "/"}:
        return "./"
    return "./" + (f"?{parsed.query}" if parsed.query else "")


def create_app() -> Flask:
    app = Flask(__name__)
    if SCRIPT_PREFIXES:
        app.wsgi_app = PrefixMiddleware(app.wsgi_app, SCRIPT_PREFIXES)
    app.secret_key = os.environ.get("QUANT_AGENT_SECRET") or os.urandom(32)
    app.config.update(
        SESSION_COOKIE_NAME="quant_agent_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        JSON_AS_ASCII=False,
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )

    @app.after_request
    def cache_static_assets(resp: Response) -> Response:
        if request.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "public, max-age=2592000, immutable"
        elif request.endpoint in {"index", "login"}:
            resp.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
        return resp

    @app.before_request
    def require_login() -> Response | None:
        allowed = {
            "login",
            "healthz",
            "favicon",
            "static",
        }
        if request.endpoint in allowed:
            return None
        if request.path.startswith("/static/"):
            return None
        if not session.get("authenticated"):
            if request.path.startswith("/api/"):
                return jsonify({"status": "failed", "message": "not_authenticated"}), 401
            return redirect(url_for("login", next=request.full_path if request.query_string else request.path))
        return None

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok", "app": "quant_strategy_agent", "version": APP_VERSION})

    @app.get("/favicon.ico")
    def favicon():
        return Response(status=204)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        message = ""
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            if username.lower() == USERNAME.lower() and password == PASSWORD:
                session.clear()
                session["authenticated"] = True
                session["user"] = USERNAME
                session["proxy_id"] = uuid.uuid4().hex
                return redirect(safe_login_return_url(request.args.get("next")))
            message = "账号或密码不正确"
        return render_template("index.html", authenticated=False, message=message, app_version=APP_VERSION)

    @app.post("/logout")
    def logout():
        proxy_id = session.get("proxy_id")
        if proxy_id:
            with PROXY_LOCK:
                PROXY_SESSIONS.pop(str(proxy_id), None)
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            authenticated=True,
            user=session.get("user") or USERNAME,
            app_version=APP_VERSION,
            public_host=PUBLIC_HOST,
        )

    @app.get("/api/services")
    def services():
        payload = {
            "status": "ok",
            "version": APP_VERSION,
            "checked_at": iso_now(),
            "services": {
                "board": safe_proxy("board", "/healthz"),
                "kline": safe_proxy("kline", "/health"),
                "factor": safe_proxy("factor", "/api/status"),
                "allocation": allocation_health_payload(),
                "liquidity": liquidity_health_payload(),
                "index_enhancement": index_enhancement_health_payload(),
                "portfolio": portfolio_health_payload(),
            },
        }
        return jsonify(payload)

    @app.get("/api/allocation/health")
    def allocation_health():
        payload = allocation_health_payload()
        return jsonify(payload), (200 if payload.get("status") == "ok" else 503)

    @app.get("/api/allocation/snapshot")
    def allocation_snapshot():
        try:
            return jsonify(load_allocation_snapshot())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return jsonify({"status": "failed", "message": f"allocation_snapshot_unavailable:{exc}"}), 503

    @app.get("/api/liquidity/health")
    def liquidity_health():
        payload = liquidity_health_payload()
        return jsonify(payload), (200 if payload.get("status") == "ok" else 503)

    @app.get("/api/liquidity/snapshot")
    def liquidity_snapshot():
        try:
            return jsonify(load_liquidity_snapshot())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return jsonify({"status": "failed", "message": f"liquidity_snapshot_unavailable:{exc}"}), 503

    @app.get("/api/index-enhancement/health")
    def index_enhancement_health():
        payload = index_enhancement_health_payload()
        return jsonify(payload), (200 if payload.get("status") == "ok" else 503)

    @app.get("/api/index-enhancement/snapshot")
    def index_enhancement_snapshot():
        try:
            return jsonify(load_index_enhancement_snapshot())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return jsonify({"status": "failed", "message": f"index_enhancement_snapshot_unavailable:{exc}"}), 503

    @app.get("/api/portfolio/health")
    def portfolio_health():
        payload = portfolio_health_payload()
        return jsonify(payload), (200 if payload.get("status") == "ok" else 503)

    @app.get("/api/portfolio/snapshot")
    def portfolio_snapshot():
        try:
            return jsonify(load_portfolio_snapshot())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return jsonify({"status": "failed", "message": f"portfolio_snapshot_unavailable:{exc}"}), 503

    @app.post("/api/allocation/report")
    def allocation_report():
        snapshot = load_allocation_snapshot()
        context = build_allocation_report_context(snapshot)
        cache_key = "allocation_report:" + json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)[:24000]
        cached = AI_CACHE.get(cache_key)
        if cached and time.time() - cached[0] < AI_CACHE_TTL_SECONDS:
            return jsonify({"status": "ok", "cached": True, "html": cached[1], "model": AI_ROUTER_MODEL, "reasoning_effort": AI_ROUTER_REASONING_EFFORT})
        text = call_ai_router(build_allocation_report_prompt(context), reasoning_effort=AI_ROUTER_REASONING_EFFORT, timeout=300)
        html = render_ai_markup(text)
        AI_CACHE[cache_key] = (time.time(), html)
        return jsonify({"status": "ok", "cached": False, "html": html, "model": AI_ROUTER_MODEL, "reasoning_effort": AI_ROUTER_REASONING_EFFORT})

    @app.get("/api/board/snapshot")
    def board_snapshot():
        return jsonify(proxy_json("board", "/api/v1/snapshot", query={"view": "metadata"}))

    @app.get("/api/board/coverage")
    def board_coverage():
        return jsonify(proxy_json("board", "/api/v1/coverage"))

    @app.get("/api/board/series")
    def board_series():
        query = request.args.to_dict(flat=True)
        payload = proxy_json("board", "/api/v1/series", query=query)
        return jsonify(patch_macro_series_payload(payload, query.get("ids", "")))

    @app.get("/api/board/stock/<code>")
    def board_stock(code: str):
        try:
            return jsonify(proxy_json("board", f"/api/v1/stock/{urllib.parse.quote(code)}"))
        except ProxyError:
            return jsonify(build_board_stock_fallback(code))

    @app.get("/api/stock/ohlc/<code>")
    def stock_ohlc(code: str):
        return jsonify(build_stock_ohlc(code))

    @app.get("/api/stock/universe")
    def stock_universe():
        return jsonify(cached_data("stock:universe:r45", 86400, build_stock_universe))

    @app.get("/api/market/global_supplement")
    def global_market_supplement():
        return jsonify(build_global_market_supplement())

    @app.get("/api/news/sina24h")
    def sina_news_24h():
        limit = max(20, min(300, int(request.args.get("limit", "160"))))
        return jsonify(build_sina_news(limit))

    @app.get("/api/news/wallstreet-wechat")
    def wallstreet_wechat_news():
        limit = max(20, min(300, int(request.args.get("limit", "120"))))
        if request.args.get("refresh") == "1":
            return jsonify(_fetch_wallstreet_wechat_live(limit))
        return jsonify(build_wallstreet_wechat_news(limit))

    @app.get("/api/market/lhb")
    def market_lhb():
        return jsonify(build_lhb_snapshot())

    @app.get("/api/domestic-demand/snapshot")
    def domestic_demand_snapshot():
        return jsonify(cached_data("domestic-demand:snapshot:r44", 900, build_domestic_demand_snapshot))

    @app.get("/api/domestic-demand/image")
    def domestic_demand_image():
        key = str(request.args.get("key") or "").strip()[:80]
        kind = str(request.args.get("kind") or "trend").strip()[:20]
        image_path = domestic_demand_image_path(key, kind)
        if not image_path or not image_path.exists():
            return jsonify({"status": "failed", "message": "domestic_image_unavailable"}), 404
        headers = {"Cache-Control": "private, max-age=900"}
        return Response(image_path.read_bytes(), mimetype="image/png", headers=headers)


    @app.get("/api/kline/health")
    def kline_health():
        return jsonify(cached_data("kline:health:r16", 60, lambda: safe_proxy("kline", "/health")))

    @app.get("/api/kline/session")
    def kline_session():
        ensure_service_login("kline")
        return jsonify(proxy_json("kline", "/api/session", auth=True))

    @app.get("/api/kline/stocks")
    def kline_stocks():
        query = request.args.to_dict(flat=True)
        key = "kline:stocks:r16:" + urllib.parse.urlencode(sorted(query.items()))
        def load():
            ensure_service_login("kline")
            return proxy_json("kline", "/api/stocks", query=query, auth=True)
        return jsonify(cached_data(key, 300, load))

    @app.get("/api/kline/dates")
    def kline_dates():
        query = request.args.to_dict(flat=True)
        key = "kline:dates:r16:" + urllib.parse.urlencode(sorted(query.items()))
        def load():
            ensure_service_login("kline")
            return proxy_json("kline", "/api/dates", query=query, auth=True)
        return jsonify(cached_data(key, 1800, load))

    @app.get("/api/kline/history")
    def kline_history():
        query = request.args.to_dict(flat=True)
        key = "kline:history:r16:" + urllib.parse.urlencode(sorted(query.items()))
        def load():
            ensure_service_login("kline")
            return proxy_json("kline", "/api/history", query=query, auth=True)
        return jsonify(cached_data(key, 300, load))

    @app.post("/api/kline/jobs")
    def kline_start_job():
        ensure_service_login("kline")
        return jsonify(proxy_json("kline", "/api/jobs", method="POST", payload=request.get_json(silent=True) or {}, auth=True))

    @app.get("/api/kline/jobs/<job_id>")
    def kline_job(job_id: str):
        quoted = urllib.parse.quote(job_id)

        def load() -> Any:
            ensure_service_login("kline")
            return proxy_json("kline", f"/api/jobs/{quoted}", auth=True)

        live = request.args.get("live") == "1"
        return jsonify(cached_data(f"kline:job:r16:{quoted}", 0 if live else 300, load))

    @app.get("/api/kline/artifact/<path:artifact>")
    def kline_artifact(artifact: str):
        ensure_service_login("kline")
        safe_path = "/" + artifact.lstrip("/")
        if not safe_path.startswith("/outputs/"):
            return jsonify({"status": "failed", "message": "invalid_artifact_path"}), 400
        raw, content_type = proxy_raw("kline", safe_path, auth=True)
        return Response(raw, content_type=content_type or "application/octet-stream")

    @app.get("/api/kline-llm/dashboard")
    def kline_llm_dashboard():
        try:
            try:
                from kline_llm_backend import build_dashboard_snapshot
            except ModuleNotFoundError as exc:
                if exc.name != "kline_llm_backend":
                    raise
                from agent.board.quant_strategy_agent.kline_llm_backend import build_dashboard_snapshot

            refresh = request.args.get("refresh") == "1"
            return jsonify(build_dashboard_snapshot(refresh=refresh))
        except Exception as exc:
            return jsonify({"status": "failed", "message": f"kline_llm_dashboard_unavailable:{exc}"}), 503

    @app.get("/api/kline-llm/rule-context")
    def kline_llm_rule_context():
        try:
            try:
                from kline_llm_backend import rule_context
            except ModuleNotFoundError as exc:
                if exc.name != "kline_llm_backend":
                    raise
                from agent.board.quant_strategy_agent.kline_llm_backend import rule_context

            return jsonify(
                rule_context(
                    request.args.get("domain", ""),
                    request.args.get("rule_id", ""),
                    request.args.get("stock", ""),
                )
            )
        except Exception as exc:
            return jsonify({"status": "failed", "message": f"kline_llm_rule_context_unavailable:{exc}"}), 503

    @app.get("/api/kline-llm/stock")
    def kline_llm_stock():
        try:
            try:
                from kline_llm_backend import stock_timing_payload
            except ModuleNotFoundError as exc:
                if exc.name != "kline_llm_backend":
                    raise
                from agent.board.quant_strategy_agent.kline_llm_backend import stock_timing_payload

            return jsonify(
                stock_timing_payload(
                    request.args.get("code", ""),
                    request.args.get("rule_id", ""),
                )
            )
        except Exception as exc:
            return jsonify({"status": "failed", "message": f"kline_llm_stock_unavailable:{exc}"}), 503

    @app.get("/api/factor/status")
    def factor_status():
        refresh = request.args.get("refresh") == "1"
        return jsonify(cached_data(
            "factor:status:r16",
            0 if refresh else 60,
            lambda: safe_proxy("factor", "/api/status"),
        ))

    @app.get("/api/factor/me")
    def factor_me():
        ensure_service_login("factor")
        return jsonify(proxy_json("factor", "/api/auth/me", auth=True))

    @app.get("/api/factor/history")
    def factor_history():
        def load() -> Any:
            ensure_service_login("factor")
            return proxy_json("factor", "/api/history", auth=True)

        refresh = request.args.get("refresh") == "1"
        return jsonify(cached_data("factor:history:r16", 0 if refresh else 300, load))

    @app.get("/api/factor/history/<job_id>")
    def factor_history_detail(job_id: str):
        quoted = urllib.parse.quote(job_id)

        def load() -> Any:
            ensure_service_login("factor")
            return proxy_json("factor", f"/api/history/{quoted}", auth=True)

        refresh = request.args.get("refresh") == "1"
        return jsonify(cached_data(f"factor:history-detail:r16:{quoted}", 0 if refresh else 300, load))

    @app.get("/api/factor/history/<job_id>/stock")
    def factor_history_stock(job_id: str):
        ensure_service_login("factor")
        return jsonify(proxy_json(
            "factor",
            f"/api/history/{urllib.parse.quote(job_id)}/stock",
            query=request.args.to_dict(flat=True),
            auth=True,
            timeout=120,
        ))

    @app.post("/api/factor/job/start")
    def factor_start_job():
        ensure_service_login("factor")
        payload = proxy_json("factor", "/api/job/start", method="POST", payload=request.get_json(silent=True) or {}, auth=True)
        with DATA_CACHE_LOCK:
            DATA_CACHE.pop("factor:history:r16", None)
        return jsonify(payload)

    @app.get("/api/factor/job/<job_id>")
    def factor_job(job_id: str):
        ensure_service_login("factor")
        return jsonify(proxy_json("factor", f"/api/job/{urllib.parse.quote(job_id)}", auth=True))

    @app.post("/api/ai/analyze")
    def ai_analyze():
        payload = request.get_json(silent=True) or {}
        module = str(payload.get("module") or "general")[:80]
        mode = str(payload.get("mode") or "brief")[:80]
        context = payload.get("context")
        subject = str(payload.get("subject") or "")[:200]
        context = enrich_ai_context(module, subject, context, mode=mode)
        prompt = build_ai_prompt(module, subject, context, mode=mode)
        cache_key = json.dumps({"module": module, "mode": mode, "subject": subject, "context": context}, ensure_ascii=False, sort_keys=True)[:8000]
        cached = AI_CACHE.get(cache_key)
        if cached and time.time() - cached[0] < AI_CACHE_TTL_SECONDS:
            return jsonify({
                "status": "ok",
                "cached": True,
                "html": cached[1],
                "model": AI_ROUTER_MODEL,
                "reasoning_effort": AI_ROUTER_REASONING_EFFORT,
            })
        timeout = 360 if mode == "deep_report" else 240
        text = call_ai_router(prompt, reasoning_effort=AI_ROUTER_REASONING_EFFORT, timeout=timeout)
        text = clean_ai_text(module, text)
        html = render_ai_markup(text)
        AI_CACHE[cache_key] = (time.time(), html)
        return jsonify({
            "status": "ok",
            "cached": False,
            "html": html,
            "model": AI_ROUTER_MODEL,
            "reasoning_effort": AI_ROUTER_REASONING_EFFORT,
        })

    @app.errorhandler(ProxyError)
    def handle_proxy_error(exc: ProxyError):
        payload = {
            "status": "failed",
            "service": exc.service,
            "message": exc.message,
            "upstream": exc.payload,
        }
        return jsonify(payload), exc.status

    @app.errorhandler(404)
    def not_found(_exc):
        return jsonify({"status": "failed", "message": "not_found"}), 404

    return app


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_allocation_snapshot() -> dict[str, Any]:
    stat = ALLOCATION_SNAPSHOT_PATH.stat()
    with ALLOCATION_CACHE_LOCK:
        if ALLOCATION_CACHE.get("mtime_ns") == stat.st_mtime_ns and isinstance(ALLOCATION_CACHE.get("payload"), dict):
            return ALLOCATION_CACHE["payload"]
        payload = json.loads(ALLOCATION_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        if payload.get("status") != "ready" or payload.get("quality", {}).get("status") != "passed":
            raise ValueError("snapshot_quality_gate_not_passed")
        ALLOCATION_CACHE.update({"mtime_ns": stat.st_mtime_ns, "payload": payload})
        return payload


def allocation_health_payload() -> dict[str, Any]:
    try:
        payload = load_allocation_snapshot()
        return {
            "status": "ok",
            "engine_version": payload.get("engine_version"),
            "generated_at": payload.get("generated_at"),
            "data_as_of": payload.get("data_as_of"),
            "quality": payload.get("quality", {}).get("status"),
        }
    except Exception as exc:  # noqa: BLE001 - health checks must stay serializable
        return {"status": "failed", "message": str(exc)}

def _parse_snapshot_date(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def _liquidity_snapshot_is_stale(payload: dict[str, Any]) -> bool:
    latest = _parse_snapshot_date((payload.get("data_as_of") or {}).get("latest"))
    if latest is None:
        return True
    return (datetime.now() - latest).days >= 3


def _liquidity_refresh_worker() -> None:
    model_dir = Path(__file__).resolve().parents[2] / "model" / "liquidity_tracking"
    refresh_cmd = [
        sys.executable,
        "data_sources.py",
        "refresh",
        "--cache",
        str(LIQUIDITY_SOURCE_CACHE_PATH),
        "--start",
        LIQUIDITY_REFRESH_START,
        "--allow-incomplete",
    ]
    build_cmd = [
        sys.executable,
        "build_snapshot.py",
        "--liquidity-cache",
        str(LIQUIDITY_SOURCE_CACHE_PATH),
        "--cache-dir",
        str(LIQUIDITY_EXTERNAL_CACHE_DIR),
        "--output",
        str(LIQUIDITY_SNAPSHOT_PATH),
    ]
    status = "failed"
    try:
        subprocess.run(refresh_cmd, cwd=str(model_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1800, check=False)
        completed = subprocess.run(build_cmd, cwd=str(model_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900, check=False)
        status = "ok" if completed.returncode == 0 else "build_failed"
    except Exception:
        status = "failed"
    finally:
        with LIQUIDITY_REFRESH_LOCK:
            LIQUIDITY_REFRESH_STATE.update({"running": False, "last_finished": time.time(), "last_status": status})


def _trigger_liquidity_refresh_async(payload: dict[str, Any]) -> bool:
    if not LIQUIDITY_AUTO_REFRESH or not _liquidity_snapshot_is_stale(payload):
        return False
    now = time.time()
    with LIQUIDITY_REFRESH_LOCK:
        if LIQUIDITY_REFRESH_STATE.get("running"):
            return False
        if now - float(LIQUIDITY_REFRESH_STATE.get("last_started") or 0.0) < LIQUIDITY_REFRESH_MIN_SECONDS:
            return False
        LIQUIDITY_REFRESH_STATE.update({"running": True, "last_started": now, "last_status": "running"})
    threading.Thread(target=_liquidity_refresh_worker, name="liquidity-snapshot-refresh", daemon=True).start()
    return True


def load_liquidity_snapshot() -> dict[str, Any]:
    stat = LIQUIDITY_SNAPSHOT_PATH.stat()
    with LIQUIDITY_CACHE_LOCK:
        if LIQUIDITY_CACHE.get("mtime_ns") == stat.st_mtime_ns and isinstance(LIQUIDITY_CACHE.get("payload"), dict):
            payload = LIQUIDITY_CACHE["payload"]
            _trigger_liquidity_refresh_async(payload)
            return payload
        payload = json.loads(LIQUIDITY_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        quality = payload.get("quality", {})
        if payload.get("status") != "ready" or quality.get("status") != "passed":
            raise ValueError("liquidity_snapshot_quality_gate_not_passed")
        if int(quality.get("chart_count") or 0) < 36:
            raise ValueError("liquidity_snapshot_chart_count_below_contract")
        pages = payload.get("pages", {})
        required = {"home", "retail", "public", "etf", "margin", "primary", "private", "foreign"}
        if set(pages) != required:
            raise ValueError("liquidity_snapshot_page_contract_mismatch")
        _trigger_liquidity_refresh_async(payload)
        LIQUIDITY_CACHE.update({"mtime_ns": stat.st_mtime_ns, "payload": payload})
        return payload

def liquidity_health_payload() -> dict[str, Any]:
    try:
        payload = load_liquidity_snapshot()
        return {"status": "ok", "generated_at": payload.get("generated_at"), "data_as_of": payload.get("data_as_of"), "quality": payload.get("quality", {}).get("status"), "charts": payload.get("quality", {}).get("chart_count")}
    except Exception as exc:  # noqa: BLE001 - health checks must stay serializable
        return {"status": "failed", "message": str(exc)}



def load_index_enhancement_snapshot() -> dict[str, Any]:
    stat = INDEX_ENHANCEMENT_SNAPSHOT_PATH.stat()
    with INDEX_ENHANCEMENT_CACHE_LOCK:
        if INDEX_ENHANCEMENT_CACHE.get("mtime_ns") == stat.st_mtime_ns and isinstance(INDEX_ENHANCEMENT_CACHE.get("payload"), dict):
            return INDEX_ENHANCEMENT_CACHE["payload"]
        payload = json.loads(INDEX_ENHANCEMENT_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        quality = payload.get("quality", {})
        if payload.get("status") != "ready" or quality.get("status") != "passed":
            raise ValueError("index_enhancement_snapshot_quality_gate_not_passed")
        required_pages = ["home", "universe", "alpha", "smartbeta", "risk", "tracking"]
        if payload.get("page_contract") != required_pages:
            raise ValueError("index_enhancement_page_contract_mismatch")
        if int(quality.get("chart_contract") or 0) < 30:
            raise ValueError("index_enhancement_chart_contract_below_minimum")
        if int(quality.get("external_api_calls", -1)) != 0:
            raise ValueError("index_enhancement_snapshot_must_be_offline_first")
        INDEX_ENHANCEMENT_CACHE.update({"mtime_ns": stat.st_mtime_ns, "payload": payload})
        return payload


def index_enhancement_health_payload() -> dict[str, Any]:
    try:
        payload = load_index_enhancement_snapshot()
        return {
            "status": "ok",
            "engine_version": payload.get("engine_version"),
            "generated_at": payload.get("generated_at"),
            "data_as_of": payload.get("data_as_of"),
            "quality": payload.get("quality", {}).get("status"),
            "pages": len(payload.get("page_contract", [])),
            "charts": payload.get("quality", {}).get("chart_contract"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "message": str(exc)}


def load_portfolio_snapshot() -> dict[str, Any]:
    stat = PORTFOLIO_SNAPSHOT_PATH.stat()
    with PORTFOLIO_CACHE_LOCK:
        if PORTFOLIO_CACHE.get("mtime_ns") == stat.st_mtime_ns and isinstance(PORTFOLIO_CACHE.get("payload"), dict):
            return PORTFOLIO_CACHE["payload"]
        payload = json.loads(PORTFOLIO_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        if payload.get("status") != "ready" or payload.get("quality", {}).get("status") != "passed":
            raise ValueError("portfolio_snapshot_quality_gate_not_passed")
        required = {"home", "asset_pool", "risk_constraints", "optimization", "backtest"}
        if not required.issubset(payload):
            raise ValueError("portfolio_snapshot_page_contract_mismatch")
        if payload.get("method", {}).get("test_policy") != "report_only":
            raise ValueError("portfolio_snapshot_test_policy_mismatch")
        PORTFOLIO_CACHE.update({"mtime_ns": stat.st_mtime_ns, "payload": payload})
        return payload


def portfolio_health_payload() -> dict[str, Any]:
    try:
        payload = load_portfolio_snapshot()
        return {
            "status": "ok",
            "engine_version": payload.get("engine_version"),
            "generated_at": payload.get("generated_at"),
            "data_as_of": payload.get("data_as_of"),
            "quality": payload.get("quality", {}).get("status"),
            "candidate_count": payload.get("method", {}).get("candidate_count"),
            "promotion_status": payload.get("backtest", {}).get("promotion_gate", {}).get("status"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "message": str(exc)}


def safe_proxy(service: str, path: str) -> dict[str, Any]:
    try:
        payload = proxy_json(service, path, timeout=8)
        if isinstance(payload, dict):
            payload.setdefault("status", "ok")
            return payload
        return {"status": "ok", "payload": payload}
    except Exception as exc:  # noqa: BLE001 - health payload should not raise
        return {"status": "failed", "message": str(exc)}


def proxy_bucket() -> dict[str, ProxySession]:
    proxy_id = session.get("proxy_id")
    if not proxy_id:
        proxy_id = uuid.uuid4().hex
        session["proxy_id"] = proxy_id
    with PROXY_LOCK:
        return PROXY_SESSIONS.setdefault(str(proxy_id), {})


def service_session(service: str) -> ProxySession:
    bucket = proxy_bucket()
    with PROXY_LOCK:
        if service in bucket:
            return bucket[service]
        cookies = CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cookies),
            urllib.request.HTTPSHandler(context=SSL_CONTEXT),
        )
        item = ProxySession(cookies=cookies, opener=opener)
        bucket[service] = item
        return item


def join_url(base: str, path: str, query: dict[str, Any] | None = None) -> str:
    url = base.rstrip("/") + "/" + path.lstrip("/")
    if query:
        clean = {k: v for k, v in query.items() if v is not None and str(v) != ""}
        if clean:
            url += "?" + urllib.parse.urlencode(clean, doseq=True)
    return url


def parse_body(raw: bytes, content_type: str = "") -> Any:
    text = raw.decode("utf-8", errors="replace")
    if "json" in content_type.lower() or text[:1] in "[{":
        try:
            return json.loads(text)
        except Exception:
            return {"raw": text}
    return {"raw": text}


def proxy_json(
    service: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    auth: bool = False,
    timeout: int = 30,
) -> Any:
    if service not in SERVICE_BASES:
        raise ProxyError(service, 400, "invalid_service")
    errors: list[str] = []
    data = None
    headers = {
        "Accept": "application/json",
        "User-Agent": f"QuantStrategyAgent/{APP_VERSION}",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req_method = method.upper()
    opener = service_session(service).opener if auth else urllib.request.build_opener(urllib.request.HTTPSHandler(context=SSL_CONTEXT))
    for base in SERVICE_BASES[service]:
        url = join_url(base, path, query=query)
        req = urllib.request.Request(url, data=data, method=req_method, headers=headers)
        try:
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
                return parse_body(raw, resp.headers.get("Content-Type", ""))
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            body = parse_body(raw, exc.headers.get("Content-Type", ""))
            raise ProxyError(service, exc.code, f"upstream_http_{exc.code}", body) from exc
        except Exception as exc:  # noqa: BLE001 - try fallback base before failing
            errors.append(f"{base}: {exc}")
    raise ProxyError(service, 502, "all_upstream_unavailable", {"errors": errors})



def proxy_form(
    service: str,
    path: str,
    payload: dict[str, Any],
    *,
    timeout: int = 30,
) -> Any:
    """POST an HTML form while retaining the service session cookies."""
    if service not in SERVICE_BASES:
        raise ProxyError(service, 400, "invalid_service")
    data = urllib.parse.urlencode(payload).encode("utf-8")
    headers = {
        "Accept": "text/html,application/json",
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        "User-Agent": f"QuantStrategyAgent/{APP_VERSION}",
    }
    errors: list[str] = []
    opener = service_session(service).opener
    for base in SERVICE_BASES[service]:
        req = urllib.request.Request(join_url(base, path), data=data, method="POST", headers=headers)
        try:
            with opener.open(req, timeout=timeout) as resp:
                return parse_body(resp.read(), resp.headers.get("Content-Type", ""))
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            raise ProxyError(
                service,
                exc.code,
                f"upstream_http_{exc.code}",
                parse_body(raw, exc.headers.get("Content-Type", "")),
            ) from exc
        except Exception as exc:  # noqa: BLE001 - try fallback base before failing
            errors.append(f"{base}: {exc}")
    raise ProxyError(service, 502, "all_upstream_unavailable", {"errors": errors})

def proxy_raw(
    service: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    auth: bool = False,
    timeout: int = 30,
) -> tuple[bytes, str]:
    if service not in SERVICE_BASES:
        raise ProxyError(service, 400, "invalid_service")
    headers = {"User-Agent": f"QuantStrategyAgent/{APP_VERSION}"}
    opener = service_session(service).opener if auth else urllib.request.build_opener(urllib.request.HTTPSHandler(context=SSL_CONTEXT))
    errors: list[str] = []
    for base in SERVICE_BASES[service]:
        url = join_url(base, path, query=query)
        req = urllib.request.Request(url, method="GET", headers=headers)
        try:
            with opener.open(req, timeout=timeout) as resp:
                return resp.read(), resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            raise ProxyError(service, exc.code, f"upstream_http_{exc.code}", parse_body(raw, exc.headers.get("Content-Type", ""))) from exc
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{base}: {exc}")
    raise ProxyError(service, 502, "all_upstream_unavailable", {"errors": errors})


def compose_market_item(points: list[dict[str, Any]], symbol: str, market: str, region: str, source: str) -> dict[str, Any] | None:
    if len(points) < 8:
        return None
    points.sort(key=lambda x: x["date"])
    values = [float(x["value"]) for x in points]
    last = values[-1]
    ret_1d = (values[-1] / values[-2] - 1.0) * 100 if values[-2] else None
    ret_5d = (values[-1] / values[-6] - 1.0) * 100 if len(values) >= 6 and values[-6] else None
    ret_20d = (values[-1] / values[-21] - 1.0) * 100 if len(values) >= 21 and values[-21] else None
    returns = [(values[i] / values[i - 1] - 1.0) for i in range(max(1, len(values) - 20), len(values)) if values[i - 1]]
    vol = None
    if len(returns) > 2:
        mean = sum(returns) / len(returns)
        vol = math.sqrt(sum((x - mean) ** 2 for x in returns) / (len(returns) - 1)) * math.sqrt(252) * 100
    window = values[-60:] if len(values) >= 60 else values
    peak = max(window) if window else None
    mdd = (last / peak - 1.0) * 100 if peak else None
    series_prefix = "supp_" + symbol.lower().replace("^", "").replace(".", "_")
    return {
        "row": {
            "market": market,
            "region": region,
            "close": last,
            "ret_1d": ret_1d,
            "ret_5d": ret_5d,
            "ret_20d": ret_20d,
            "vol_20d": vol,
            "mdd_60d": mdd,
            "as_of": points[-1]["date"],
            "source": source,
        },
        "series": [{
            "id": f"{series_prefix}_close",
            "label": f"{market} close",
            "submodule": f"Global/{region}",
            "unit": "index points",
            "source": source,
            "as_of": points[-1]["date"],
            "status": "live",
            "data": points,
        }],
    }


def yahoo_chart(symbol: str, market: str, region: str) -> dict[str, Any] | None:
    encoded = urllib.parse.quote(symbol, safe="")
    payload = None
    last_error: Exception | None = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{encoded}?range=8mo&interval=1d"
        req = urllib.request.Request(url, headers={"User-Agent": f"QuantStrategyAgent/{APP_VERSION}"})
        try:
            with urllib.request.urlopen(req, timeout=16, context=SSL_CONTEXT) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            break
        except Exception as exc:
            last_error = exc
            continue
    try:
        if payload is None:
            raise last_error or RuntimeError("empty yahoo response")
        result = (((payload.get("chart") or {}).get("result") or [None])[0])
        if not result:
            return None
        timestamps = result.get("timestamp") or []
        close = (((result.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
        points: list[dict[str, Any]] = []
        for ts, value in zip(timestamps, close):
            if value is None:
                continue
            day = datetime.fromtimestamp(int(ts), timezone.utc).date().isoformat()
            points.append({"date": day, "value": float(value)})
        return compose_market_item(points, symbol, market, region, "Yahoo Finance chart API")
    except Exception:
        return None


def eastmoney_chart(secid: str, market: str, region: str) -> dict[str, Any] | None:
    end = datetime.now(timezone.utc).date()
    beg = end - timedelta(days=260)
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "0",
        "beg": beg.strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
    }
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": f"QuantStrategyAgent/{APP_VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=16, context=SSL_CONTEXT) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        if payload.get("rc") != 0 or not payload.get("data"):
            return None
        points: list[dict[str, Any]] = []
        for raw in payload["data"].get("klines") or []:
            parts = str(raw).split(",")
            if len(parts) < 3:
                continue
            try:
                points.append({"date": parts[0], "value": float(parts[2])})
            except ValueError:
                continue
        return compose_market_item(points, secid.replace(".", "_"), market, region, "Eastmoney global index kline API")
    except Exception:
        return None


GLOBAL_MARKET_REFRESH_LOCK = threading.Lock()
GLOBAL_MARKET_REFRESHING = False
GLOBAL_MARKET_REFRESH_TTL_SECONDS = int(os.environ.get("GLOBAL_MARKET_REFRESH_TTL_SECONDS", "21600"))
GLOBAL_MARKET_TARGET_COUNT = 10


def _global_market_snapshot_score(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    rows = payload.get("rows")
    series = payload.get("series")
    if not isinstance(rows, list) or not isinstance(series, list):
        return 0
    return min(len(rows), len(series))


def _global_market_snapshot_complete(payload: Any) -> bool:
    return _global_market_snapshot_score(payload) >= GLOBAL_MARKET_TARGET_COUNT


def _read_global_market_snapshot_raw() -> dict[str, Any] | None:
    try:
        payload = json.loads(GLOBAL_MARKET_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None



def _fetch_global_market_supplement() -> dict[str, Any]:
    targets = [
        ("000001.SS", "1.000001", "SSE Composite", "China A"), ("000300.SS", "1.000300", "CSI 300", "China A"),
        ("^GSPC", "100.SPX", "S&P 500", "United States"), ("^IXIC", "100.NDX", "NASDAQ", "United States"),
        ("^DJI", "100.DJIA", "Dow Jones", "United States"), ("^HSI", "100.HSI", "Hang Seng", "Hong Kong"),
        ("^N225", "100.N225", "Nikkei 225", "Japan"), ("^KS11", "100.KS11", "KOSPI", "Korea"),
        ("^STOXX50E", "100.SX5E", "Euro Stoxx 50", "Europe"), ("^GDAXI", "100.GDAXI", "DAX", "Europe"),
    ]
    rows: list[dict[str, Any]] = []
    series: list[dict[str, Any]] = []
    for symbol, secid, market, region in targets:
        try:
            item = yahoo_chart(symbol, market, region) or eastmoney_chart(secid, market, region) or sina_index_chart(symbol, market, region)
        except Exception:
            item = None
        if item:
            rows.append(item["row"])
            series.extend(item["series"])
    order = {target[2]: index for index, target in enumerate(targets)}
    rows.sort(key=lambda row: order.get(str(row.get("market")), 99))
    return {
        "status": "ok" if len(rows) == len(targets) else ("partial" if rows else "unavailable"),
        "as_of": max((row.get("as_of") or "" for row in rows), default=""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
        "series": series,
    }


def _refresh_global_market_snapshot() -> None:
    global GLOBAL_MARKET_REFRESHING
    try:
        payload = _fetch_global_market_supplement()
        if payload.get("rows"):
            current = _read_global_market_snapshot_raw()
            if current:
                current_score = _global_market_snapshot_score(current)
                payload_score = _global_market_snapshot_score(payload)
                if _global_market_snapshot_complete(current) and not _global_market_snapshot_complete(payload):
                    return
                if payload_score < current_score:
                    return
            GLOBAL_MARKET_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
            temporary = GLOBAL_MARKET_SNAPSHOT_PATH.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(temporary, GLOBAL_MARKET_SNAPSHOT_PATH)
    finally:
        with GLOBAL_MARKET_REFRESH_LOCK:
            GLOBAL_MARKET_REFRESHING = False

def _schedule_global_market_refresh() -> bool:
    global GLOBAL_MARKET_REFRESHING
    with GLOBAL_MARKET_REFRESH_LOCK:
        if GLOBAL_MARKET_REFRESHING:
            return True
        GLOBAL_MARKET_REFRESHING = True
    threading.Thread(target=_refresh_global_market_snapshot, daemon=True, name="global-market-refresh").start()
    return True


def build_global_market_supplement() -> dict[str, Any]:
    snapshot = load_global_market_snapshot()
    if snapshot:
        try:
            stale = time.time() - GLOBAL_MARKET_SNAPSHOT_PATH.stat().st_mtime > GLOBAL_MARKET_REFRESH_TTL_SECONDS
        except OSError:
            stale = True
        incomplete = not _global_market_snapshot_complete(snapshot)
        if stale or incomplete:
            snapshot["refreshing"] = _schedule_global_market_refresh()
        return snapshot
    _schedule_global_market_refresh()
    return {"status": "refreshing", "as_of": "", "rows": [], "series": [], "refreshing": True}

# r25 data-quality layer: corrected macro dates, cached parallel markets, live news and actual LHB.
def cached_data(key: str, ttl: int, builder: Any) -> Any:
    now = time.time()
    with DATA_CACHE_LOCK:
        hit = DATA_CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = builder()
    with DATA_CACHE_LOCK:
        DATA_CACHE[key] = (now, value)
    return value


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def build_tsf_corrections() -> dict[str, dict[str, Any]]:
    columns = {
        "cn_tsf_increment": ("社会融资规模增量", "社会融资规模增量"),
        "cn_tsf_rmb_loan": ("其中-人民币贷款", "社融人民币贷款"),
        "cn_tsf_entrusted": ("其中-委托贷款", "委托贷款"),
        "cn_tsf_trust": ("其中-信托贷款", "信托贷款"),
        "cn_tsf_undiscounted_bill": ("其中-未贴现银行承兑汇票", "未贴现银行承兑汇票"),
        "cn_tsf_corp_bond": ("其中-企业债券", "企业债券融资"),
        "cn_tsf_equity": ("其中-非金融企业境内股票融资", "非金融企业境内股票融资"),
    }

    def load() -> dict[str, dict[str, Any]]:
        import akshare as ak  # type: ignore

        frame = ak.macro_china_shrzgm()
        output: dict[str, dict[str, Any]] = {}
        for series_id, (column, label) in columns.items():
            points: list[dict[str, Any]] = []
            for _, row in frame.iterrows():
                month = re.sub(r"\D", "", str(row.get("月份") or ""))[:6]
                value = _finite_number(row.get(column))
                if len(month) != 6 or value is None:
                    continue
                points.append({"date": f"{month[:4]}-{month[4:]}-01", "value": value})
            points.sort(key=lambda item: item["date"])
            output[series_id] = {
                "id": series_id,
                "label": label,
                "unit": "亿元",
                "frequency": "monthly",
                "status": "available" if points else "unavailable",
                "as_of": points[-1]["date"] if points else "",
                "source": "AKShare macro_china_shrzgm（月份字段校正）",
                "data": points,
                "points": points,
            }
        return output

    return cached_data("macro:tsf:corrected", 6 * 3600, load)


TSF_CORRECTIONS_CACHE_PATH = Path(__file__).resolve().parent / "data" / "tsf_corrections_cache.json"
TSF_CORRECTIONS_REFRESH_TTL_SECONDS = int(os.environ.get("TSF_CORRECTIONS_REFRESH_TTL_SECONDS", "21600"))
TSF_CORRECTIONS_REFRESH_LOCK = threading.Lock()
TSF_CORRECTIONS_REFRESHING = False


def _load_tsf_corrections_cache() -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(TSF_CORRECTIONS_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): value
        for key, value in payload.items()
        if isinstance(value, dict) and isinstance(value.get("data"), list)
    }


def _refresh_tsf_corrections() -> None:
    global TSF_CORRECTIONS_REFRESHING
    try:
        payload = build_tsf_corrections()
        if payload:
            TSF_CORRECTIONS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            temporary = TSF_CORRECTIONS_CACHE_PATH.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, TSF_CORRECTIONS_CACHE_PATH)
    finally:
        with TSF_CORRECTIONS_REFRESH_LOCK:
            TSF_CORRECTIONS_REFRESHING = False


def _schedule_tsf_corrections_refresh() -> bool:
    global TSF_CORRECTIONS_REFRESHING
    with TSF_CORRECTIONS_REFRESH_LOCK:
        if TSF_CORRECTIONS_REFRESHING:
            return False
        TSF_CORRECTIONS_REFRESHING = True
    threading.Thread(
        target=_refresh_tsf_corrections,
        name="tsf-corrections-refresh",
        daemon=True,
    ).start()
    return True


def load_tsf_corrections_snapshot() -> dict[str, dict[str, Any]]:
    payload = _load_tsf_corrections_cache()
    try:
        stale = time.time() - TSF_CORRECTIONS_CACHE_PATH.stat().st_mtime > TSF_CORRECTIONS_REFRESH_TTL_SECONDS
    except OSError:
        stale = True
    if stale:
        _schedule_tsf_corrections_refresh()
    return payload


def patch_macro_series_payload(payload: Any, requested: str) -> Any:
    if not isinstance(payload, dict):
        return payload
    wanted = {item.strip() for item in str(requested or "").split(",") if item.strip()}
    correction_ids = {
        "cn_tsf_increment", "cn_tsf_rmb_loan", "cn_tsf_entrusted", "cn_tsf_trust",
        "cn_tsf_undiscounted_bill", "cn_tsf_corp_bond", "cn_tsf_equity",
    }
    if not wanted.intersection(correction_ids):
        return payload
    corrections = load_tsf_corrections_snapshot()
    if not corrections:
        return payload

    current = [item for item in payload.get("series", []) if isinstance(item, dict)]
    index = {str(item.get("id") or ""): item for item in current}
    for series_id in wanted.intersection(correction_ids):
        if series_id in corrections:
            index[series_id] = corrections[series_id]
    ordered = []
    seen: set[str] = set()
    for item in current:
        series_id = str(item.get("id") or "")
        ordered.append(index.get(series_id, item))
        seen.add(series_id)
    for series_id in wanted:
        if series_id not in seen and series_id in index:
            ordered.append(index[series_id])
    payload = dict(payload)
    payload["series"] = ordered
    return payload


def _fetch_sina_news_live(limit: int = 160) -> dict[str, Any]:
    def load() -> dict[str, Any]:
        from html import unescape

        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        max_pages = max(1, min(32, math.ceil(max(limit, 20) / 10) + 2))
        for page in range(1, max_pages + 1):
            params = {"page": page, "page_size": 20, "zhibo_id": 152, "tag_id": 0, "dire": "f", "dpc": 1}
            url = "https://zhibo.sina.com.cn/api/zhibo/feed?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 QuantStrategyAgent", "Referer": "https://finance.sina.com.cn/7x24/"})
            with _urlopen_ipv4(req, timeout=18, context=SSL_CONTEXT) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            feed = (((payload.get("result") or {}).get("data") or {}).get("feed") or {})
            items = feed.get("list") or []
            if not items:
                break
            added = 0
            for item in items:
                raw = str(item.get("rich_text") or item.get("text") or "")
                title = unescape(re.sub(r"<[^>]+>", "", raw)).strip()
                if not title:
                    continue
                ident = str(item.get("id") or "") or hashlib.sha1(title.encode("utf-8", errors="ignore")).hexdigest()[:16]
                if ident in seen:
                    continue
                seen.add(ident)
                ext = item.get("ext") or {}
                if isinstance(ext, str):
                    try:
                        ext = json.loads(ext)
                    except json.JSONDecodeError:
                        ext = {}
                stocks = ext.get("stocks") if isinstance(ext, dict) else []
                first = stocks[0] if isinstance(stocks, list) and stocks else {}
                code = str(first.get("symbol") or first.get("key") or "") if isinstance(first, dict) else ""
                code = re.sub(r"\D", "", code)[-6:] if code else ""
                tags = ext.get("tag") if isinstance(ext, dict) else []
                tag_names = [str(tag.get("name") or "") for tag in tags or [] if isinstance(tag, dict)]
                docurl = str(item.get("docurl") or "").strip()
                if not docurl and ident:
                    docurl = f"https://finance.sina.cn/7x24/{ident}.d.html"
                rows.append({
                    "id": ident,
                    "published_at": str(item.get("create_time") or item.get("update_time") or "")[:19],
                    "title": title[:360],
                    "source": "新浪财经7×24",
                    "event_type": "、".join(tag_names[:3]) or "财经快讯",
                    "code": code,
                    "name": str(first.get("key") or "") if isinstance(first, dict) else "",
                    "url": docurl,
                })
                added += 1
                if len(rows) >= limit:
                    break
            if len(rows) >= limit or added == 0:
                break
            time.sleep(0.08)
        rows.sort(key=lambda item: item["published_at"], reverse=True)
        return {"status": "ok" if rows else "unavailable", "as_of": rows[0]["published_at"] if rows else "", "source": "新浪财经7×24官方接口", "rows": rows[:limit]}

    return cached_data(f"news:sina24h:{limit}", 60, load)
SW_L1_INDUSTRIES = [
    "农林牧渔", "基础化工", "钢铁", "有色金属", "电子", "家用电器", "食品饮料", "纺织服饰",
    "轻工制造", "医药生物", "公用事业", "交通运输", "房地产", "商贸零售", "社会服务", "综合",
    "建筑材料", "建筑装饰", "电力设备", "国防军工", "计算机", "传媒", "通信", "银行", "非银金融",
    "汽车", "机械设备", "煤炭", "石油石化", "环保", "美容护理",
]


def classify_sw_industry(industry: str, name: str = "") -> str:
    text = f"{industry} {name}"
    rules = [
        ("银行", ["银行"]), ("非银金融", ["证券", "保险", "多元金融", "期货", "信托"]),
        ("房地产", ["房地产", "地产"]), ("煤炭", ["煤炭", "焦煤", "焦炭"]),
        ("石油石化", ["石油", "炼化", "石化", "油气"]), ("钢铁", ["钢铁", "特钢"]),
        ("有色金属", ["有色", "黄金", "铜", "铝", "铅锌", "稀土", "小金属"]),
        ("基础化工", ["化工", "化学", "农药", "化肥", "塑料", "橡胶", "涂料"]),
        ("电子", ["半导体", "元器件", "电子", "显示器件", "消费电子"]),
        ("计算机", ["软件", "互联网", "信息技术", "IT设备", "数据服务"]),
        ("通信", ["通信", "电信", "网络设备"]), ("传媒", ["传媒", "影视", "出版", "广告", "游戏"]),
        ("医药生物", ["医药", "医疗", "生物", "制药", "医疗保健"]),
        ("食品饮料", ["食品", "饮料", "白酒", "啤酒", "乳制品"]),
        ("农林牧渔", ["农业", "林业", "渔业", "种植", "养殖", "饲料"]),
        ("汽车", ["汽车", "摩托车", "汽车零部件"]), ("家用电器", ["家电", "电器"]),
        ("机械设备", ["机械", "设备", "机床", "工程机械", "专用机械"]),
        ("电力设备", ["电气设备", "电力设备", "电池", "光伏", "风电"]),
        ("国防军工", ["军工", "航空", "航天", "船舶"]),
        ("交通运输", ["运输", "航运", "港口", "机场", "航空", "物流", "铁路"]),
        ("公用事业", ["电力", "燃气", "水务", "供热"]), ("环保", ["环保", "环境"]),
        ("建筑材料", ["建材", "水泥", "玻璃", "陶瓷"]), ("建筑装饰", ["建筑", "工程", "装修"]),
        ("商贸零售", ["零售", "商贸", "百货", "超市"]), ("社会服务", ["旅游", "酒店", "餐饮", "教育"]),
        ("纺织服饰", ["纺织", "服饰", "服装"]), ("轻工制造", ["造纸", "家具", "包装", "文教休闲"]),
        ("美容护理", ["美容", "化妆品", "个人护理"]),
    ]
    for category, keys in rules:
        if any(key in text for key in keys):
            return category
    return "综合"


def build_sw_l1_membership() -> dict[str, Any]:
    """Return the official SW2021 level-1 constituent mapping with a durable cache."""

    def valid(payload: Any) -> bool:
        return (
            isinstance(payload, dict)
            and int(payload.get("industry_count") or 0) == len(SW_L1_INDUSTRIES)
            and int(payload.get("stock_count") or 0) >= 4000
            and isinstance(payload.get("membership"), dict)
        )

    def load() -> dict[str, Any]:
        stale: dict[str, Any] | None = None
        with SW_L1_MEMBERSHIP_LOCK:
            try:
                payload = json.loads(SW_L1_MEMBERSHIP_CACHE_PATH.read_text(encoding="utf-8"))
                if valid(payload):
                    stale = payload
                    age = time.time() - SW_L1_MEMBERSHIP_CACHE_PATH.stat().st_mtime
                    if age < 7 * 86400:
                        payload["cache_state"] = "fresh"
                        return payload
            except (OSError, ValueError, json.JSONDecodeError):
                pass

            import akshare as ak  # type: ignore

            try:
                first = ak.sw_index_first_info()
            except Exception:
                if stale is not None:
                    stale["cache_state"] = "stale"
                    return stale
                raise

            membership: dict[str, str] = {}
            failures: list[dict[str, str]] = []
            completed: list[str] = []
            for _, item in first.iterrows():
                symbol = str(item.get("\u884c\u4e1a\u4ee3\u7801") or "").split(".")[0]
                industry = str(item.get("\u884c\u4e1a\u540d\u79f0") or "").strip()
                if industry not in SW_L1_INDUSTRIES or not symbol:
                    continue
                frame = None
                last_error = ""
                for attempt in range(3):
                    try:
                        frame = ak.index_component_sw(symbol=symbol)
                        break
                    except Exception as exc:  # noqa: BLE001
                        last_error = type(exc).__name__
                        time.sleep(0.8 * (attempt + 1))
                if frame is None or getattr(frame, "empty", True):
                    failures.append({"code": symbol, "industry": industry, "error": last_error or "empty"})
                    continue
                completed.append(industry)
                for raw_code in frame.get("\u8bc1\u5238\u4ee3\u7801", []):
                    code = re.sub(r"\D", "", str(raw_code))[-6:]
                    if code:
                        membership[code] = industry
                time.sleep(0.2)

            if len(completed) != len(SW_L1_INDUSTRIES) or len(membership) < 4000:
                if stale is not None:
                    stale["cache_state"] = "stale"
                    stale["refresh_failures"] = failures
                    return stale
                raise RuntimeError(f"sw_l1_membership_incomplete:{len(completed)}/{len(SW_L1_INDUSTRIES)}")

            payload = {
                "status": "ok",
                "generated_at": iso_now(),
                "source": "AKShare sw_index_first_info + index_component_sw (official SW2021 constituents)",
                "industry_count": len(completed),
                "stock_count": len(membership),
                "membership": membership,
                "cache_state": "refreshed",
            }
            try:
                SW_L1_MEMBERSHIP_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                temp_path = SW_L1_MEMBERSHIP_CACHE_PATH.with_suffix(".tmp")
                temp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                temp_path.replace(SW_L1_MEMBERSHIP_CACHE_PATH)
            except OSError:
                pass
            return payload

    return cached_data("sw:l1:membership:r38", 12 * 3600, load)

def _fetch_lhb_snapshot_live() -> dict[str, Any]:
    def load() -> dict[str, Any]:
        import akshare as ak  # type: ignore

        frame = None
        trade_date = ""
        today = datetime.now().date()
        for offset in range(0, 10):
            candidate = (today - timedelta(days=offset)).strftime("%Y%m%d")
            try:
                test = ak.stock_lhb_detail_em(start_date=candidate, end_date=candidate)
            except Exception:
                continue
            if test is not None and not test.empty:
                frame, trade_date = test, candidate
                break
        if frame is None or frame.empty:
            return {"status": "unavailable", "as_of": "", "industries": [], "stocks": []}

        sw_payload = build_sw_l1_membership()
        membership = sw_payload.get("membership") if isinstance(sw_payload.get("membership"), dict) else {}
        stock_map: dict[str, dict[str, Any]] = {}
        industry_map = {
            name: {"industry": name, "count": 0, "net_buy": 0.0, "turnover": 0.0}
            for name in SW_L1_INDUSTRIES
        }
        mapped_rows = 0
        unclassified_rows = 0
        excluded_non_equity_rows = 0
        for _, row in frame.iterrows():
            code = re.sub(r"\D", "", str(row.get("\u4ee3\u7801") or ""))[-6:]
            if not code:
                continue
            if code.startswith("1"):
                excluded_non_equity_rows += 1
                continue
            name = str(row.get("\u540d\u79f0") or code)
            net_buy = _finite_number(row.get("\u9f99\u864e\u699c\u51c0\u4e70\u989d")) or 0.0
            turnover = _finite_number(row.get("\u9f99\u864e\u699c\u6210\u4ea4\u989d")) or 0.0
            industry = str(membership.get(code) or "\u672a\u7eb3\u5165\u7533\u4e07\u4e00\u7ea7\u884c\u4e1a")
            item = stock_map.setdefault(
                code,
                {"code": code, "name": name, "industry": industry, "count": 0, "net_buy": 0.0, "turnover": 0.0},
            )
            item["count"] += 1
            item["net_buy"] += net_buy
            item["turnover"] += turnover
            if industry in industry_map:
                mapped_rows += 1
                bucket = industry_map[industry]
                bucket["count"] += 1
                bucket["net_buy"] += net_buy
                bucket["turnover"] += turnover
            else:
                unclassified_rows += 1

        def finalize_heat(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            max_count = max((float(row.get("count") or 0) for row in rows), default=0.0) or 1.0
            max_turnover = max((abs(float(row.get("turnover") or 0)) for row in rows), default=0.0) or 1.0
            max_net_buy = max((abs(float(row.get("net_buy") or 0)) for row in rows), default=0.0) or 1.0
            for row in rows:
                count_score = float(row.get("count") or 0) / max_count
                turnover_score = abs(float(row.get("turnover") or 0)) / max_turnover
                flow_score = abs(float(row.get("net_buy") or 0)) / max_net_buy
                row["heat_score"] = round(100 * (0.50 * count_score + 0.30 * turnover_score + 0.20 * flow_score), 2)
                row["net_buy_wan"] = round(float(row.pop("net_buy", 0.0)) / 10000, 2)
                row["turnover_wan"] = round(float(row.pop("turnover", 0.0)) / 10000, 2)
            return rows

        industries = finalize_heat(list(industry_map.values()))
        stocks = finalize_heat(list(stock_map.values()))
        industries.sort(key=lambda row: (row["heat_score"], row["count"], abs(row["net_buy_wan"])), reverse=True)
        stocks.sort(key=lambda row: (row["heat_score"], row["count"], abs(row["net_buy_wan"])), reverse=True)
        return {
            "status": "ok",
            "as_of": f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}",
            "source": "AKShare stock_lhb_detail_em + official SW2021 constituent mapping",
            "industry_mapping": {
                "status": sw_payload.get("status"),
                "cache_state": sw_payload.get("cache_state"),
                "industry_count": sw_payload.get("industry_count"),
                "stock_count": sw_payload.get("stock_count"),
                "mapped_rows": mapped_rows,
                "unclassified_rows": unclassified_rows,
                "excluded_non_equity_rows": excluded_non_equity_rows,
            },
            "score_method": "count 50% + turnover 30% + absolute net-buy 20%; normalized within the same trade date",
            "industries": industries,
            "stocks": stocks[:10],
        }

    day_key = datetime.now().strftime("%Y%m%d")
    return cached_data(f"market:lhb:r38:{day_key}", 1800, load)

def _path_within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _latest_domestic_build_dir() -> Path:
    candidates: list[Path] = []
    if DOMESTIC_DEMAND_ROOT.exists():
        for dated_dir in DOMESTIC_DEMAND_ROOT.iterdir():
            if not dated_dir.is_dir():
                continue
            for table_path in dated_dir.glob("_build_*/tables.json"):
                candidates.append(table_path.parent)
    if not candidates:
        raise FileNotFoundError(f"domestic demand build not found under {DOMESTIC_DEMAND_ROOT}")
    return max(candidates, key=lambda item: item.joinpath("tables.json").stat().st_mtime)


def _domestic_tables_payload() -> dict[str, Any]:
    build_dir = _latest_domestic_build_dir()
    payload = json.loads(build_dir.joinpath("tables.json").read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("domestic_tables_not_object")
    payload["_build_dir"] = str(build_dir)
    payload["_source_dir"] = str(build_dir.parent)
    return payload


def _domestic_sheet(payload: dict[str, Any], name: str) -> dict[str, Any]:
    for sheet in payload.get("sheets", []):
        if isinstance(sheet, dict) and sheet.get("name") == name:
            return sheet
    return {"name": name, "columns": [], "data": []}


def _domestic_rows(payload: dict[str, Any], name: str) -> list[dict[str, Any]]:
    sheet = _domestic_sheet(payload, name)
    columns = [str(item) for item in sheet.get("columns", [])]
    rows: list[dict[str, Any]] = []
    for raw in sheet.get("data", []):
        if isinstance(raw, list):
            rows.append({columns[i]: raw[i] if i < len(raw) else None for i in range(len(columns))})
    return rows


def _domestic_as_of(rows: list[dict[str, Any]]) -> str:
    latest = ""
    for row in rows:
        sample = str(row.get("样本区间") or "")
        match = re.search(r"至\s*(\d{4}-\d{2}-\d{2})", sample)
        if match and match.group(1) > latest:
            latest = match.group(1)
    return latest


def _domestic_sheet_name(short_name: str, suffix: str) -> str:
    return f"{short_name}_{suffix}"


def _domestic_image_url(key: str, kind: str) -> str:
    return "/api/domestic-demand/image?key=" + urllib.parse.quote(key) + "&kind=" + urllib.parse.quote(kind)


def build_domestic_demand_snapshot() -> dict[str, Any]:
    payload = _domestic_tables_payload()
    overview = _domestic_rows(payload, "四指标汇总")
    extra = _domestic_rows(payload, "其余指数汇总")
    manifest = _domestic_rows(payload, "图片清单")
    indices: list[dict[str, Any]] = []
    for item in manifest:
        key = str(item.get("指标key") or "").strip()
        short_name = str(item.get("指标简称") or "").strip()
        if not key or not short_name:
            continue
        trend_path = Path(str(item.get("走势买点图") or "")).resolve()
        backtest_path = Path(str(item.get("策略回测图") or "")).resolve()
        if not (_path_within_root(trend_path, DOMESTIC_DEMAND_ROOT) and _path_within_root(backtest_path, DOMESTIC_DEMAND_ROOT)):
            continue
        performance = _domestic_rows(payload, _domestic_sheet_name(short_name, "绩效"))
        buy_points = _domestic_rows(payload, _domestic_sheet_name(short_name, "买点"))
        indices.append({
            "key": key,
            "label": short_name,
            "signal_date": item.get("标注日期"),
            "trend_image_url": _domestic_image_url(key, "trend"),
            "backtest_image_url": _domestic_image_url(key, "backtest"),
            "trend_image_ok": trend_path.exists(),
            "backtest_image_ok": backtest_path.exists(),
            "performance": performance,
            "buy_points": buy_points,
        })
    as_of = _domestic_as_of(overview) or _domestic_as_of(extra)
    return {
        "status": "ok" if overview and indices else "partial",
        "as_of": as_of,
        "generated_at": iso_now(),
        "source_dir": payload.get("_source_dir"),
        "build_dir": payload.get("_build_dir"),
        "overview": overview,
        "extra": extra,
        "indices": indices,
    }


def domestic_demand_image_path(key: str, kind: str) -> Path | None:
    if not key:
        return None
    payload = _domestic_tables_payload()
    target_column = "策略回测图" if kind == "backtest" else "走势买点图"
    for row in _domestic_rows(payload, "图片清单"):
        if str(row.get("指标key") or "").strip() != key:
            continue
        image_path = Path(str(row.get(target_column) or "")).resolve()
        if _path_within_root(image_path, DOMESTIC_DEMAND_ROOT):
            return image_path
    return None


# r44.11: full domestic-demand index loader. Prefer the latest all-index CSV build and
# fall back to the legacy four-index workbook extraction only if the all-index build is absent.
_LEGACY_BUILD_DOMESTIC_DEMAND_SNAPSHOT = build_domestic_demand_snapshot
_LEGACY_DOMESTIC_DEMAND_IMAGE_PATH = domestic_demand_image_path
DOMESTIC_DEMAND_PACKAGED_SNAPSHOT_PATH = Path(__file__).resolve().parent / "data" / "domestic_demand_snapshot.json"


def _domestic_latest_all_dir() -> Path | None:
    candidates: list[Path] = []
    if DOMESTIC_DEMAND_ROOT.exists():
        for manifest in DOMESTIC_DEMAND_ROOT.rglob("all_annotated_plot_manifest.csv"):
            if manifest.is_file() and manifest.parent.joinpath("all_metrics_summary.csv").exists():
                candidates.append(manifest.parent)
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.joinpath("all_annotated_plot_manifest.csv").stat().st_mtime)


def _domestic_csv_rows(path: Path, limit: int = 0) -> list[dict[str, Any]]:
    if not path.exists() or not _path_within_root(path, DOMESTIC_DEMAND_ROOT):
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                rows.append({str(key): value for key, value in row.items() if key is not None})
    except OSError:
        return []
    if limit and len(rows) > limit:
        return rows[-limit:]
    return rows


def _domestic_float(value: Any) -> float | None:
    if value is None:
        return None
    raw = str(value).strip().replace(",", "")
    if not raw:
        return None
    if raw.endswith("%"):
        raw = raw[:-1]
        scale = 1.0
    else:
        scale = 1.0
    try:
        return float(raw) / scale
    except ValueError:
        return None


def _domestic_format_percent(column: str, value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.endswith("%"):
        return raw
    number = _domestic_float(raw)
    if number is None:
        return raw
    if abs(number) <= 2:
        number *= 100
    return f"{number:.2f}%"


def _domestic_format_number(value: Any, digits: int = 2) -> Any:
    number = _domestic_float(value)
    if number is None:
        return value
    if abs(number - round(number)) < 1e-9:
        return int(round(number))
    return round(number, digits)


def _domestic_metric_row(row: dict[str, Any], signal_row: dict[str, Any] | None = None) -> dict[str, Any]:
    signal_row = signal_row or {}
    out: dict[str, Any] = {
        "key": row.get("key") or signal_row.get("key") or "",
        "指标": row.get("指标") or signal_row.get("indicator") or "",
        "样本区间": row.get("样本区间") or "",
        "买点数": _domestic_format_number(row.get("买点数") or signal_row.get("buy_count"), 0),
        "绝对年化": _domestic_format_percent("绝对年化", row.get("绝对年化")),
        "最大回撤": _domestic_format_percent("最大回撤", row.get("最大回撤")),
        "夏普比": _domestic_format_number(row.get("夏普比"), 2),
        "持有20日胜率": _domestic_format_percent("持有20日胜率", row.get("持有20日胜率")),
        "持有40日胜率": _domestic_format_percent("持有40日胜率", row.get("持有40日胜率")),
        "持有60日胜率": _domestic_format_percent("持有60日胜率", row.get("持有60日胜率")),
        "持有20日绝对年化收益": _domestic_format_percent("持有20日绝对年化收益", row.get("持有20日绝对年化收益")),
        "持有40日绝对年化收益": _domestic_format_percent("持有40日绝对年化收益", row.get("持有40日绝对年化收益")),
        "持有60日绝对年化收益": _domestic_format_percent("持有60日绝对年化收益", row.get("持有60日绝对年化收益")),
        "最新买点": signal_row.get("latest_buy_date") or "",
        "距最新买点交易日": _domestic_format_number(signal_row.get("trading_days_since_latest_buy_to_0814"), 0),
        "数据状态": signal_row.get("source_status") or "",
    }
    return out


def _domestic_build_all_snapshot() -> dict[str, Any] | None:
    data_dir = _domestic_latest_all_dir()
    if not data_dir:
        return None
    manifest = _domestic_csv_rows(data_dir / "all_annotated_plot_manifest.csv")
    metrics = _domestic_csv_rows(data_dir / "all_metrics_summary.csv")
    signals = _domestic_csv_rows(data_dir / "all_signal_summary.csv")
    if not manifest:
        return None
    signal_by_key = {str(row.get("key") or "").strip(): row for row in signals if str(row.get("key") or "").strip()}
    metric_by_key: dict[str, dict[str, Any]] = {}
    for row in metrics:
        key = str(row.get("key") or "").strip()
        if key and key not in {"index2_latest", "index_signal_quality"} and key not in metric_by_key:
            metric_by_key[key] = row
    indices: list[dict[str, Any]] = []
    overview: list[dict[str, Any]] = []
    for item in sorted(manifest, key=lambda row: int(float(str(row.get("order") or "9999") or 9999))):
        key = str(item.get("key") or "").strip()
        label = str(item.get("indicator") or "").strip()
        if not key or not label:
            continue
        signal_row = signal_by_key.get(key, {})
        metric_row = metric_by_key.get(key, {"key": key, "指标": label})
        compare_path = Path(str(item.get("compare_annotated") or "")).resolve()
        backtest_path = Path(str(item.get("backtest") or "")).resolve()
        decision_path = Path(str(item.get("decision_csv") or "")).resolve()
        daily_path = Path(str(item.get("daily_csv") or "")).resolve()
        trades_path = Path(str(item.get("trades_csv") or "")).resolve()
        metric_display = _domestic_metric_row(metric_row, signal_row)
        metric_display["指标"] = label
        metric_display["key"] = key
        overview.append(metric_display)
        indices.append({
            "key": key,
            "label": label,
            "order": _domestic_format_number(item.get("order"), 0),
            "signal_date": signal_row.get("signal_end") or metric_row.get("终点") or "",
            "price_date": signal_row.get("price_end") or "",
            "latest_buy_date": signal_row.get("latest_buy_date") or "",
            "fresh_to_price_date": signal_row.get("fresh_to_0814") or "",
            "source_status": signal_row.get("source_status") or "",
            "compare_image_ok": _path_within_root(compare_path, DOMESTIC_DEMAND_ROOT) and compare_path.exists(),
            "backtest_image_ok": _path_within_root(backtest_path, DOMESTIC_DEMAND_ROOT) and backtest_path.exists(),
            "performance": [metric_display],
            "decision_rows": _domestic_csv_rows(decision_path, 900),
            "strategy_rows": _domestic_csv_rows(daily_path, 0),
            "trades": _domestic_csv_rows(trades_path, 240),
        })
    as_of = max([str(row.get("price_end") or row.get("signal_end") or "") for row in signals] + [_domestic_as_of(overview) or ""])
    payload = {
        "status": "ok" if indices else "partial",
        "as_of": as_of,
        "generated_at": iso_now(),
        "source_dir": str(data_dir.parent),
        "build_dir": str(data_dir),
        "overview": overview,
        "extra": [],
        "indices": indices,
    }
    try:
        DOMESTIC_DEMAND_PACKAGED_SNAPSHOT_PATH.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass
    return payload


def build_domestic_demand_snapshot() -> dict[str, Any]:
    payload = _domestic_build_all_snapshot()
    if payload:
        return payload
    packaged = _read_runtime_snapshot(DOMESTIC_DEMAND_PACKAGED_SNAPSHOT_PATH)
    if packaged:
        return packaged
    return _LEGACY_BUILD_DOMESTIC_DEMAND_SNAPSHOT()


def domestic_demand_image_path(key: str, kind: str) -> Path | None:
    data_dir = _domestic_latest_all_dir()
    if data_dir:
        column = "backtest" if kind == "backtest" else "compare_annotated"
        for row in _domestic_csv_rows(data_dir / "all_annotated_plot_manifest.csv"):
            if str(row.get("key") or "").strip() != key:
                continue
            image_path = Path(str(row.get(column) or "")).resolve()
            if _path_within_root(image_path, DOMESTIC_DEMAND_ROOT):
                return image_path
    return _LEGACY_DOMESTIC_DEMAND_IMAGE_PATH(key, kind)
RUNTIME_NEWS_SNAPSHOT_PATH = Path(__file__).resolve().parent / "data" / "sina_news_snapshot.json"
RUNTIME_WALLSTREET_WECHAT_SNAPSHOT_PATH = Path(__file__).resolve().parent / "data" / "wallstreet_wechat_snapshot.json"
RUNTIME_LHB_SNAPSHOT_PATH = Path(__file__).resolve().parent / "data" / "lhb_snapshot.json"
RUNTIME_SNAPSHOT_LOCK = threading.Lock()
RUNTIME_SNAPSHOT_REFRESHING: set[str] = set()


def _read_runtime_snapshot(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _refresh_runtime_snapshot(key: str, path: Path, builder: Any) -> None:
    try:
        payload = builder()
        if isinstance(payload, dict) and payload:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, path)
    except Exception:
        pass
    finally:
        with RUNTIME_SNAPSHOT_LOCK:
            RUNTIME_SNAPSHOT_REFRESHING.discard(key)


def _schedule_runtime_snapshot(key: str, path: Path, builder: Any) -> bool:
    with RUNTIME_SNAPSHOT_LOCK:
        if key in RUNTIME_SNAPSHOT_REFRESHING:
            return False
        RUNTIME_SNAPSHOT_REFRESHING.add(key)
    threading.Thread(
        target=_refresh_runtime_snapshot,
        args=(key, path, builder),
        name=f"{key}-snapshot-refresh",
        daemon=True,
    ).start()
    return True


def _runtime_snapshot(path: Path, key: str, ttl: int, builder: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    payload = _read_runtime_snapshot(path)
    try:
        stale = time.time() - path.stat().st_mtime > ttl
    except OSError:
        stale = True
    if stale and payload:
        _schedule_runtime_snapshot(key, path, builder)
        return payload
    if stale:
        try:
            fresh = builder()
            if isinstance(fresh, dict) and fresh:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp")
                temporary.write_text(
                    json.dumps(fresh, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                os.replace(temporary, path)
                return fresh
        except Exception:
            _schedule_runtime_snapshot(key, path, builder)
    return payload or dict(fallback, refreshing=True)
def build_sina_news(limit: int = 160) -> dict[str, Any]:
    return _runtime_snapshot(
        RUNTIME_NEWS_SNAPSHOT_PATH,
        "sina-news",
        300,
        lambda: _fetch_sina_news_live(limit),
        {"status": "refreshing", "as_of": "", "source": "新浪财经7×24官方接口", "rows": []},
    )






def _wallstreet_export_metadata_path() -> Path:
    return WALLSTREET_WECHAT_EXPORT_DIR / "metadata.json"


def _wallstreet_export_is_stale() -> bool:
    path = _wallstreet_export_metadata_path()
    try:
        return time.time() - path.stat().st_mtime > 3600
    except OSError:
        return True


def _wallstreet_wechat_script_candidates() -> list[Path]:
    app_root = Path(__file__).resolve().parent
    roots = [
        app_root / "deploy" / "remote-windows-ssh",
        WALLSTREET_WECHAT_SHH_ROOT,
        Path(r"C:\Users\Rye\Desktop\Program\ELSE\SHH"),
        Path(r"C:\Users\Rye\Desktop\Program\ELSE\SHH\remote-windows-ssh"),
        Path(r"C:\Users\Rye\Desktop\Program\ELSE\ubantu\SHH\remote-windows-ssh"),
    ]
    seen: set[str] = set()
    candidates: list[Path] = []
    for root in roots:
        try:
            base = root.resolve()
        except OSError:
            base = root
        key = str(base).lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(base / "scripts" / "run_wechat_export_from_local.ps1")
    return candidates


def _run_wallstreet_wechat_export() -> dict[str, Any]:
    script = next((path for path in _wallstreet_wechat_script_candidates() if path.exists()), None)
    if script is None:
        return {"status": "skipped", "message": "shh_wechat_wrapper_missing"}
    script_root = script.parent.parent
    WALLSTREET_WECHAT_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    end = datetime.now().date()
    start = end - timedelta(days=WALLSTREET_WECHAT_LOOKBACK_DAYS)
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-HostAlias",
        WALLSTREET_WECHAT_HOST_ALIAS,
        "-Biz",
        WALLSTREET_WECHAT_BIZ,
        "-StartDate",
        start.isoformat(),
        "-EndDate",
        end.isoformat(),
        "-LocalOutput",
        str(WALLSTREET_WECHAT_EXPORT_DIR),
        "-MaxPages",
        str(WALLSTREET_WECHAT_MAX_PAGES),
        "-MetadataOnly",
        "-SkipPdfValidation",
    ]
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(script_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=WALLSTREET_WECHAT_EXPORT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "failed", "message": type(exc).__name__}
    if completed.returncode != 0:
        try:
            (WALLSTREET_WECHAT_EXPORT_DIR / "last_error.txt").write_text(
                (completed.stdout or "")[-4000:] + "\n" + (completed.stderr or "")[-4000:],
                encoding="utf-8",
            )
        except OSError:
            pass
        return {"status": "failed", "message": "remote_wechat_export_failed", "returncode": completed.returncode}
    return {"status": "ok", "start": start.isoformat(), "end": end.isoformat()}

def _wechat_candidate_files() -> list[Path]:
    roots: list[Path] = []
    for root in WALLSTREET_WECHAT_ROOTS:
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        if resolved.exists() and resolved not in roots:
            roots.append(resolved)
    suffixes = {".json", ".jsonl", ".ndjson", ".csv"}
    files: list[Path] = []
    for root in roots:
        try:
            for path in root.rglob("*"):
                if path.is_file() and path.suffix.lower() in suffixes:
                    files.append(path)
        except OSError:
            continue
    return sorted(files, key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)[:500]


def _normalise_wechat_time(value: Any, fallback_mtime: float = 0.0) -> str:
    if value is None or value == "":
        if fallback_mtime:
            return datetime.fromtimestamp(fallback_mtime).strftime("%Y-%m-%d %H:%M:%S")
        return ""
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts = ts / 1000.0
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except (OverflowError, OSError, ValueError):
            return str(value)
    raw = str(value).strip()
    if not raw:
        return _normalise_wechat_time(None, fallback_mtime)
    digits_only = raw.replace(".", "", 1)
    if digits_only.isdigit():
        try:
            return _normalise_wechat_time(float(raw), fallback_mtime)
        except ValueError:
            pass
    raw = raw.replace("T", " ").replace("Z", "").replace("/", "-")
    return raw[:19]


def _iter_wechat_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        parent_biz = payload.get("target_biz") or payload.get("__biz") or payload.get("biz")
        parent_source = payload.get("source") or payload.get("account") or payload.get("nickname")
        for key in ("rows", "articles", "items", "data", "list", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                out: list[dict[str, Any]] = []
                for item in value:
                    if isinstance(item, dict):
                        row = dict(item)
                        if parent_biz:
                            row.setdefault("_target_biz", parent_biz)
                        if parent_source:
                            row.setdefault("_parent_source", parent_source)
                        out.append(row)
                return out
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _extract_wechat_records(path: Path) -> list[dict[str, Any]]:
    try:
        fallback_mtime = path.stat().st_mtime
    except OSError:
        fallback_mtime = 0.0
    records: list[dict[str, Any]] = []
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    if isinstance(row, dict):
                        records.append(dict(row, _fallback_mtime=fallback_mtime, _source_file=str(path)))
            return records
        text = path.read_text(encoding="utf-8-sig", errors="replace").strip()
    except OSError:
        return []
    if not text:
        return []
    if suffix in {".jsonl", ".ndjson"}:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            for row in _iter_wechat_records(item):
                row = dict(row)
                row["_fallback_mtime"] = fallback_mtime
                row["_source_file"] = str(path)
                records.append(row)
        return records
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    for row in _iter_wechat_records(payload):
        row = dict(row)
        row["_fallback_mtime"] = fallback_mtime
        row["_source_file"] = str(path)
        records.append(row)
    return records


def _first_text(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _normalise_wechat_article(row: dict[str, Any]) -> dict[str, Any] | None:
    title = _first_text(row, ("title", "标题", "article_title", "name", "subject", "digest", "result_title"))
    url = _first_text(row, ("url", "link", "链接", "article_url", "content_url", "mp_url", "source_url", "retrieved_url", "stable_url", "sogou_url"))
    if not url and isinstance(row.get("links"), list):
        for item in row.get("links") or []:
            if isinstance(item, dict) and str(item.get("url") or "").strip():
                url = str(item.get("url") or "").strip()
                break
    author = _first_text(row, ("author", "公众号", "account", "source", "biz_name", "nickname", "_parent_source"))
    if not author and isinstance(row.get("user_data"), dict):
        author = _first_text(row["user_data"], ("referrals", "nickname", "name", "account"))
    identity_marker = " ".join([
        author,
        _first_text(row, ("biz", "biz_name", "wechat_name", "公众号名称", "account_name", "nickname", "source_file")),
        str(row.get("_source_file") or ""),
    ]).lower()
    target_biz = str(row.get("_target_biz") or row.get("target_biz") or row.get("__biz") or row.get("biz") or "")
    is_target = (
        target_biz == WALLSTREET_WECHAT_BIZ
        or WALLSTREET_WECHAT_ACCOUNT in identity_marker
        or "wallstreet" in identity_marker
        or "wall street" in identity_marker
        or "wscn" in identity_marker
    )
    if not is_target or not title:
        return None
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    published = _first_text(row, ("published_at", "publish_time", "发布时间", "datetime", "time", "date", "created_at", "update_time", "create_time"))
    return {
        "published_at": _normalise_wechat_time(published, float(row.get("_fallback_mtime") or 0.0)),
        "event_type": "公众号",
        "code": "",
        "title": title,
        "source": WALLSTREET_WECHAT_ACCOUNT + "公众号",
        "url": url,
    }



def _fetch_wallstreet_wechat_qnmlgb(limit: int = 120) -> list[dict[str, Any]]:
    author_url = "https://qnmlgb.tech/authors/5b575dbb58e5c4583338db33"
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursor = ""
    for _ in range(8):
        url = author_url if not cursor else author_url + "?_lst=" + urllib.parse.quote(cursor) + "&_tab="
        req = urllib.request.Request(url, headers={"User-Agent": f"QuantStrategyAgent/{APP_VERSION}"})
        try:
            with _urlopen_ipv4(req, timeout=12, context=SSL_CONTEXT) as resp:
                page = resp.read().decode("utf-8", errors="replace")
        except Exception:
            break
        parts = re.split(r'<div class="ae" data-id="([^"]+)"', page)
        article_blocks: list[tuple[str, str]] = []
        for index in range(1, len(parts), 2):
            article_id = parts[index]
            block = parts[index + 1] if index + 1 < len(parts) else ""
            article_blocks.append((article_id, block))
        before = len(rows)
        for article_id, block in article_blocks:
            if article_id in seen:
                continue
            title_match = re.search(r'class="pretty">(.*?)</span>', block, flags=re.S)
            date_match = re.search(r'<span style="font-size:\s*12px">\s*([0-9]{1,2})\s*/\s*([0-9]{1,2})\s*</span>', block, flags=re.S)
            if not title_match:
                continue
            title = html.unescape(re.sub(r"<.*?>", "", title_match.group(1))).strip()
            if not title:
                continue
            year = datetime.now().year
            if date_match:
                month, day = int(date_match.group(1)), int(date_match.group(2))
                published = f"{year:04d}-{month:02d}-{day:02d}"
            else:
                published = ""
            seen.add(article_id)
            rows.append({
                "published_at": published,
                "event_type": "公众号",
                "code": "",
                "title": title,
                "source": WALLSTREET_WECHAT_ACCOUNT + "公众号",
                "url": "https://qnmlgb.tech/articles/" + article_id,
                "source_chain": "华尔街见闻公众号聚合页",
            })
            cursor = article_id
            if len(rows) >= limit:
                break
        if len(rows) >= limit or len(rows) == before:
            break
    rows.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
    return rows[:limit]
def _fetch_wallstreet_wechat_live(limit: int = 120) -> dict[str, Any]:
    export_status = _run_wallstreet_wechat_export() if _wallstreet_export_is_stale() else {"status": "cached"}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    files = _wechat_candidate_files()
    for path in files:
        for raw in _extract_wechat_records(path):
            article = _normalise_wechat_article(raw)
            if not article:
                continue
            key = article["url"] or article["title"]
            if key in seen:
                continue
            seen.add(key)
            rows.append(article)
    rows.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
    fallback_rows: list[dict[str, Any]] = []
    if not rows:
        fallback_rows = _fetch_wallstreet_wechat_qnmlgb(limit)
        rows = fallback_rows
    status = "ok" if rows else ("failed" if export_status.get("status") == "failed" else "empty")
    message = ""
    if status == "failed":
        message = "wechat_auth_missing"
    elif status == "empty":
        message = "no_recent_wallstreet_wechat_articles"
    return {
        "status": status,
        "as_of": iso_now(),
        "source": "server-side WeChat: " + WALLSTREET_WECHAT_ACCOUNT + "公众号",
        "account": WALLSTREET_WECHAT_ACCOUNT,
        "target_biz": WALLSTREET_WECHAT_BIZ,
        "export_status": export_status,
        "fallback_used": bool(fallback_rows),
        "message": message,
        "file_count": len(files),
        "rows": rows[: max(20, min(300, limit))],
    }
def build_wallstreet_wechat_news(limit: int = 120) -> dict[str, Any]:
    fallback = {"status": "refreshing", "as_of": "", "source": "server-side WeChat: " + WALLSTREET_WECHAT_ACCOUNT + "公众号", "account": WALLSTREET_WECHAT_ACCOUNT, "target_biz": WALLSTREET_WECHAT_BIZ, "message": "等待服务端微信公众号导出", "rows": []}
    payload = _read_runtime_snapshot(RUNTIME_WALLSTREET_WECHAT_SNAPSHOT_PATH)
    rows = payload.get("rows") if isinstance(payload, dict) and isinstance(payload.get("rows"), list) else []
    try:
        stale = time.time() - RUNTIME_WALLSTREET_WECHAT_SNAPSHOT_PATH.stat().st_mtime > 3600
    except OSError:
        stale = True
    if rows:
        if stale:
            _schedule_runtime_snapshot("wallstreet-wechat", RUNTIME_WALLSTREET_WECHAT_SNAPSHOT_PATH, lambda: _fetch_wallstreet_wechat_live(limit))
        return payload
    fresh = _fetch_wallstreet_wechat_live(limit)
    fresh_rows = fresh.get("rows") if isinstance(fresh, dict) and isinstance(fresh.get("rows"), list) else []
    if fresh_rows:
        try:
            RUNTIME_WALLSTREET_WECHAT_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
            temporary = RUNTIME_WALLSTREET_WECHAT_SNAPSHOT_PATH.with_suffix(".tmp")
            temporary.write_text(json.dumps(fresh, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(temporary, RUNTIME_WALLSTREET_WECHAT_SNAPSHOT_PATH)
        except OSError:
            pass
        return fresh
    return fresh if isinstance(fresh, dict) and fresh else fallback


def _stock_identity_from_universe(norm: str) -> dict[str, Any]:
    try:
        payload = cached_data("stock:universe:r45", 86400, build_stock_universe)
    except Exception:
        payload = {"rows": []}
    for row in payload.get("rows") or []:
        code = normalize_a_code(str(row.get("code") or row.get("display_code") or row.get("symbol") or ""))
        if code == norm:
            return {
                "code": norm,
                "name": row.get("name") or row.get("stock_name") or "",
                "market": row.get("market") or _stock_market_from_code(str(row.get("display_code") or ""), norm),
                "display_code": row.get("display_code") or norm,
            }
    return {"code": norm, "name": "", "market": _stock_market_from_code(norm, norm), "display_code": norm}


def _stock_return(rows: list[dict[str, Any]], days: int) -> float | None:
    if len(rows) <= days:
        return None
    start = _finite_number(rows[-days - 1].get("close"))
    end = _finite_number(rows[-1].get("close"))
    if start in (None, 0) or end is None:
        return None
    return (end / start - 1) * 100


def _stock_mdd(rows: list[dict[str, Any]], days: int) -> float | None:
    window = rows[-days:] if len(rows) >= days else rows
    peak: float | None = None
    drawdown = 0.0
    for row in window:
        value = _finite_number(row.get("close"))
        if value is None:
            continue
        peak = value if peak is None else max(peak, value)
        if peak:
            drawdown = min(drawdown, value / peak - 1)
    return drawdown * 100 if peak is not None else None


def _stock_vol(rows: list[dict[str, Any]], days: int) -> float | None:
    values = [_finite_number(row.get("ret")) for row in rows[-days:]]
    values = [value for value in values if value is not None]
    if len(values) < 3:
        return None
    return statistics.pstdev(values) * math.sqrt(252)


def build_board_stock_fallback(code: str) -> dict[str, Any]:
    norm = normalize_a_code(code)
    if not norm:
        raise ProxyError("board", 400, "invalid_stock_code")
    identity = _stock_identity_from_universe(norm)
    try:
        ohlc = build_stock_ohlc(norm)
    except ProxyError:
        record = dict(identity)
        return {
            "status": "partial",
            "as_of": datetime.now(timezone.utc).date().isoformat(),
            "record": record,
            "tables": [{"id": "stock_watchlist", "rows": [record]}],
            "series": [],
            "source": "stock_universe_only",
        }
    rows = ohlc.get("rows") or []
    last = rows[-1] if rows else {}
    record = dict(identity)
    record.update({
        "close": last.get("close"),
        "qfq_close": last.get("close"),
        "ret_1d": last.get("ret"),
        "ret_5d": _stock_return(rows, 5),
        "ret_20d": _stock_return(rows, 20),
        "vol_20d": _stock_vol(rows, 20),
        "mdd_20d": _stock_mdd(rows, 20),
        "turnover": last.get("turnover"),
        "as_of": ohlc.get("as_of") or last.get("date"),
        "source": ohlc.get("source"),
    })
    points = [{"date": row.get("date"), "value": row.get("close")} for row in rows if row.get("date") and _finite_number(row.get("close")) is not None]
    returns = [{"date": row.get("date"), "value": row.get("ret")} for row in rows if row.get("date") and _finite_number(row.get("ret")) is not None]
    series = [
        {"id": f"stock_{norm}_close", "label": f"{identity.get('name') or norm} close", "unit": "元", "frequency": "daily", "as_of": record.get("as_of"), "source": ohlc.get("source"), "data": points, "points": points},
        {"id": f"stock_{norm}_ret_1d", "label": f"{identity.get('name') or norm} ret_1d", "unit": "%", "frequency": "daily", "as_of": record.get("as_of"), "source": ohlc.get("source"), "data": returns, "points": returns},
    ]
    return {
        "status": "ok",
        "as_of": record.get("as_of"),
        "record": record,
        "tables": [{"id": "stock_watchlist", "rows": [record]}],
        "series": series,
        "recent_bars": rows[-80:],
        "source": ohlc.get("source"),
    }

def build_lhb_snapshot() -> dict[str, Any]:
    return _runtime_snapshot(
        RUNTIME_LHB_SNAPSHOT_PATH,
        "market-lhb",
        1800,
        _fetch_lhb_snapshot_live,
        {"status": "refreshing", "as_of": "", "industries": [], "stocks": []},
    )


def sina_index_chart(symbol: str, market: str, region: str) -> dict[str, Any] | None:
    try:
        import akshare as ak  # type: ignore

        if symbol == "000001.SS":
            frame = ak.stock_zh_index_daily(symbol="sh000001")
        elif symbol == "000300.SS":
            frame = ak.stock_zh_index_daily(symbol="sh000300")
        elif symbol in {"^GSPC", "^IXIC", "^DJI"}:
            frame = ak.index_us_stock_sina({"^GSPC": ".INX", "^IXIC": ".IXIC", "^DJI": ".DJI"}[symbol])
        elif symbol == "^HSI":
            frame = ak.stock_hk_index_daily_sina(symbol="HSI")
        else:
            names = {
                "^N225": "日经225指数", "^KS11": "首尔综合指数",
                "^STOXX50E": "欧洲Stoxx50指数", "^GDAXI": "德国DAX 30种股价指数",
            }
            frame = ak.index_global_hist_sina(names[symbol])
        if frame is None or frame.empty:
            return None
        points: list[dict[str, Any]] = []
        for _, row in frame.tail(320).iterrows():
            value = _finite_number(row.get("close"))
            if value is None:
                continue
            points.append({"date": str(row.get("date"))[:10], "value": value})
        return compose_market_item(points, symbol, market, region, "AKShare 新浪指数历史行情") if points else None
    except Exception:
        return None

def load_global_market_snapshot() -> dict[str, Any] | None:
    payload = _read_global_market_snapshot_raw()
    rows = payload.get("rows") or [] if isinstance(payload, dict) else []
    series = payload.get("series") or [] if isinstance(payload, dict) else []
    if not rows or not series:
        return None
    payload["snapshot"] = True
    if not _global_market_snapshot_complete(payload):
        payload["status"] = "partial"
    return payload

def normalize_a_code(code: str) -> str:
    raw = ''.join(ch for ch in str(code or '') if ch.isdigit())
    return raw[-6:] if len(raw) >= 6 else raw


def stock_eastmoney_secid(code: str) -> str:
    norm = normalize_a_code(code)
    if not norm:
        return ''
    if norm.startswith(('6', '9')):
        return '1.' + norm
    return '0.' + norm


def parse_stock_kline_rows(klines: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in klines or []:
        parts = str(raw).split(',')
        if len(parts) < 11:
            continue
        try:
            rows.append({
                'date': parts[0],
                'open': float(parts[1]),
                'close': float(parts[2]),
                'high': float(parts[3]),
                'low': float(parts[4]),
                'volume': float(parts[5]),
                'amount': float(parts[6]),
                'amplitude': float(parts[7]),
                'ret': float(parts[8]),
                'change': float(parts[9]),
                'turnover': float(parts[10]),
            })
        except ValueError:
            continue
    return rows


_STOCK_PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy", "NO_PROXY", "no_proxy")
_STOCK_PROXY_ENV_LOCK = threading.Lock()


def _stock_proxy_value_is_dead_local(value: str | None) -> bool:
    text = str(value or "").lower()
    return "127.0.0.1:17890" in text or "localhost:17890" in text


def _stock_run_without_dead_local_proxy(getter, norm: str, limit: int) -> tuple[list[dict[str, Any]], str]:
    with _STOCK_PROXY_ENV_LOCK:
        saved = {key: os.environ.get(key) for key in _STOCK_PROXY_ENV_KEYS}
        try:
            for key, value in saved.items():
                if _stock_proxy_value_is_dead_local(value):
                    os.environ.pop(key, None)
            existing = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
            parts = [item.strip() for item in existing.split(",") if item.strip()]
            for host in ("127.0.0.1", "localhost", "api.waditu.com", "push2his.eastmoney.com", "quote.eastmoney.com"):
                if host not in parts:
                    parts.append(host)
            value = ",".join(parts)
            os.environ["NO_PROXY"] = value
            os.environ["no_proxy"] = value
            return getter(norm, limit)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def fetch_stock_ohlc_eastmoney(norm: str, limit: int) -> tuple[list[dict[str, Any]], str]:
    secid = stock_eastmoney_secid(norm)
    end = datetime.now(timezone.utc).date() + timedelta(days=2)
    beg = end - timedelta(days=max(420, limit * 2))
    params = {
        'secid': secid,
        'fields1': 'f1,f2,f3,f4,f5,f6',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
        'klt': '101',
        'fqt': request.args.get('fqt', '1'),
        'beg': beg.strftime('%Y%m%d'),
        'end': end.strftime('%Y%m%d'),
    }
    url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': f'QuantStrategyAgent/{APP_VERSION}'})
    with _urlopen_ipv4(req, timeout=18, context=SSL_CONTEXT) as resp:
        payload = json.loads(resp.read().decode('utf-8', errors='replace'))
    rows = parse_stock_kline_rows((payload.get('data') or {}).get('klines') or [])
    return rows, 'Eastmoney push2his kline'


def fetch_stock_ohlc_akshare(norm: str, limit: int) -> tuple[list[dict[str, Any]], str]:
    import akshare as ak  # type: ignore

    end = datetime.now().date() + timedelta(days=2)
    beg = end - timedelta(days=max(420, limit * 2))
    df = ak.stock_zh_a_hist(
        symbol=norm,
        period='daily',
        start_date=beg.strftime('%Y%m%d'),
        end_date=end.strftime('%Y%m%d'),
        adjust='qfq',
    )
    if df is None or getattr(df, 'empty', True):
        return [], 'AKShare stock_zh_a_hist'
    rows: list[dict[str, Any]] = []
    for _, rec in df.iterrows():
        try:
            rows.append({
                'date': str(rec.get('日期') or rec.get('date') or '')[:10],
                'open': float(rec.get('开盘')),
                'close': float(rec.get('收盘')),
                'high': float(rec.get('最高')),
                'low': float(rec.get('最低')),
                'volume': float(rec.get('成交量') or 0),
                'amount': float(rec.get('成交额') or 0),
                'amplitude': float(rec.get('振幅') or 0),
                'ret': float(rec.get('涨跌幅') or 0),
                'change': float(rec.get('涨跌额') or 0),
                'turnover': float(rec.get('换手率') or 0),
            })
        except (TypeError, ValueError):
            continue
    return rows, 'AKShare stock_zh_a_hist'

def fetch_stock_ohlc_tushare(norm: str, limit: int) -> tuple[list[dict[str, Any]], str]:
    token = os.environ.get("TUSHARE_TOKEN") or os.environ.get("TUSHARE_PRO_TOKEN")
    if not token:
        raise RuntimeError("tushare_token_missing")
    import tushare as ts  # type: ignore

    ts.set_token(token)
    suffix = ".SH" if norm.startswith(("6", "9")) else ".BJ" if norm.startswith(("4", "8")) else ".SZ"
    ts_code = norm + suffix
    end = datetime.now().date() + timedelta(days=2)
    beg = end - timedelta(days=max(520, limit * 3))
    start_date = beg.strftime("%Y%m%d")
    end_date = end.strftime("%Y%m%d")
    try:
        frame = ts.pro_bar(ts_code=ts_code, adj="qfq", start_date=start_date, end_date=end_date)
    except Exception:
        frame = None
    if frame is None or getattr(frame, "empty", True):
        pro = ts.pro_api(token)
        frame = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    if frame is None or getattr(frame, "empty", True):
        return [], "Tushare pro daily"
    frame = frame.sort_values("trade_date")
    rows: list[dict[str, Any]] = []
    for _, rec in frame.tail(limit).iterrows():
        date_raw = str(rec.get("trade_date") or "")
        if not re.fullmatch(r"\d{8}", date_raw):
            continue
        try:
            rows.append({
                "date": f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:]}",
                "open": float(rec.get("open")),
                "high": float(rec.get("high")),
                "low": float(rec.get("low")),
                "close": float(rec.get("close")),
                "volume": float(rec.get("vol") or 0),
                "amount": float(rec.get("amount") or 0),
                "amplitude": 0.0,
                "ret": float(rec.get("pct_chg") or 0),
                "change": float(rec.get("change") or 0),
                "turnover": 0.0,
            })
        except (TypeError, ValueError):
            continue
    return rows, "Tushare pro daily"

def fetch_stock_ohlc_baostock(norm: str, limit: int) -> tuple[list[dict[str, Any]], str]:
    import baostock as bs  # type: ignore

    prefix = "sh" if norm.startswith(("6", "9")) else "sz"
    end = datetime.now().date() + timedelta(days=2)
    beg = end - timedelta(days=max(520, limit * 3))
    login = bs.login()
    if str(login.error_code) != "0":
        raise RuntimeError(f"baostock_login:{login.error_msg}")
    rows: list[dict[str, Any]] = []
    try:
        result = bs.query_history_k_data_plus(
            f"{prefix}.{norm}",
            "date,open,high,low,close,volume,amount,turn,pctChg",
            start_date=beg.isoformat(), end_date=end.isoformat(), frequency="d", adjustflag="2",
        )
        if str(result.error_code) != "0":
            raise RuntimeError(f"baostock_query:{result.error_msg}")
        while result.next():
            values = result.get_row_data()
            try:
                rows.append({
                    "date": values[0], "open": float(values[1]), "high": float(values[2]),
                    "low": float(values[3]), "close": float(values[4]), "volume": float(values[5] or 0),
                    "amount": float(values[6] or 0), "turnover": float(values[7] or 0),
                    "ret": float(values[8] or 0), "amplitude": 0.0, "change": 0.0,
                })
            except (TypeError, ValueError, IndexError):
                continue
    finally:
        bs.logout()
    return rows, "BaoStock query_history_k_data_plus（前复权）"

def build_stock_ohlc(code: str) -> dict[str, Any]:
    norm = normalize_a_code(code)
    secid = stock_eastmoney_secid(norm)
    if not norm or not secid:
        raise ProxyError('stock', 400, 'invalid_stock_code')
    try:
        limit = max(60, min(520, int(request.args.get('limit', '260'))))
    except ValueError:
        limit = 260
    errors: list[dict[str, str]] = []
    for label, getter in (
        ('Tushare pro daily', fetch_stock_ohlc_tushare),
        ('Eastmoney push2his kline', fetch_stock_ohlc_eastmoney),
        ('AKShare stock_zh_a_hist', fetch_stock_ohlc_akshare),
        ('BaoStock query_history_k_data_plus', fetch_stock_ohlc_baostock),
    ):
        try:
            rows, source = _stock_run_without_dead_local_proxy(getter, norm, limit)
            by_date: dict[str, dict[str, Any]] = {}
            for row in rows:
                date = str(row.get("date") or "")[:10]
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                    continue
                required = [row.get("open"), row.get("high"), row.get("low"), row.get("close")]
                if not all(_finite_number(value) is not None for value in required):
                    continue
                row["date"] = date
                by_date[date] = row
            rows = [by_date[date] for date in sorted(by_date)][-limit:]
            if rows:
                return {
                    'status': 'ok',
                    'code': norm,
                    'secid': secid,
                    'as_of': rows[-1]['date'],
                    'source': source,
                    'rows': rows,
                }
            errors.append({'source': label, 'error': 'empty'})
        except Exception as exc:  # noqa: BLE001
            errors.append({'source': label, 'error': str(exc)})
            continue
    return {
        'status': 'empty',
        'code': norm,
        'secid': secid,
        'as_of': '',
        'source': 'no_available_stock_ohlc',
        'rows': [],
        'errors': errors,
    }


def _stock_market_from_code(raw_code: str, norm: str) -> str:
    text = str(raw_code or "").upper()
    if text.endswith(".SH") or norm.startswith(("6", "9")):
        return "SH"
    if text.endswith(".SZ") or norm.startswith(("0", "2", "3")):
        return "SZ"
    if text.endswith(".BJ") or norm.startswith(("4", "8")):
        return "BJ"
    return ""


def _stock_universe_rows_from_local() -> tuple[list[dict[str, Any]], str]:
    paths = [STOCK_UNIVERSE_LOCAL_PATH]
    if STOCK_UNIVERSE_LEGACY_PATH not in paths:
        paths.append(STOCK_UNIVERSE_LEGACY_PATH)
    source_path = next((path for path in paths if path.exists()), None)
    if source_path is None:
        return [], "local_xlsx_missing"
    if source_path.suffix.lower() == ".json":
        try:
            payload = json.loads(source_path.read_text(encoding="utf-8-sig"))
            items = payload.get("rows", []) if isinstance(payload, dict) else payload
        except Exception as exc:  # noqa: BLE001
            return [], f"local_json_failed:{type(exc).__name__}"
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            raw_code = str(item.get("code") or item.get("display_code") or item.get("symbol") or "").strip().upper()
            norm = normalize_a_code(raw_code)
            if not re.fullmatch(r"\d{6}", norm or "") or norm in seen:
                continue
            name = str(item.get("name") or item.get("stock_name") or "").strip()
            if not name or name.lower() == "nan":
                continue
            market = str(item.get("market") or "").strip().upper() or _stock_market_from_code(raw_code, norm)
            rows.append({"code": norm, "name": name, "market": market, "display_code": str(item.get("display_code") or (f"{norm}.{market}" if market else norm))})
            seen.add(norm)
        return rows, str(source_path)
    try:
        import pandas as pd  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return [], f"pandas_unavailable:{type(exc).__name__}"
    try:
        frame = pd.read_excel(source_path)
    except Exception as exc:  # noqa: BLE001
        return [], f"local_xlsx_failed:{type(exc).__name__}"
    if frame.empty:
        return [], "local_xlsx_empty"
    columns = [str(col) for col in frame.columns]
    code_col = next((col for col in columns if "代码" in col or col.lower() in {"code", "symbol"}), columns[0])
    name_col = next((col for col in columns if "名称" in col or col.lower() in {"name", "stock_name"}), columns[1] if len(columns) > 1 else columns[0])
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, item in frame[[code_col, name_col]].iterrows():
        raw_code = str(item.get(code_col) or "").strip().upper()
        norm = normalize_a_code(raw_code)
        if not re.fullmatch(r"\d{6}", norm or "") or norm in seen:
            continue
        name = str(item.get(name_col) or "").strip()
        if not name or name.lower() == "nan":
            continue
        market = _stock_market_from_code(raw_code, norm)
        rows.append({"code": norm, "name": name, "market": market, "display_code": f"{norm}.{market}" if market else norm})
        seen.add(norm)
    return rows, str(source_path)


def _stock_universe_rows_from_akshare() -> tuple[list[dict[str, Any]], str]:
    try:
        import akshare as ak  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return [], f"akshare_unavailable:{type(exc).__name__}"
    try:
        frame = ak.stock_info_a_code_name()
    except Exception as exc:  # noqa: BLE001
        return [], f"akshare_stock_info_a_code_name_failed:{type(exc).__name__}"
    if frame is None or getattr(frame, "empty", True):
        return [], "akshare_empty"
    columns = [str(col) for col in frame.columns]
    code_col = next((col for col in columns if col.lower() in {"code", "symbol"} or "代码" in col), columns[0])
    name_col = next((col for col in columns if col.lower() in {"name", "stock_name"} or "名称" in col), columns[1] if len(columns) > 1 else columns[0])
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, item in frame[[code_col, name_col]].iterrows():
        raw_code = str(item.get(code_col) or "").strip().upper()
        norm = normalize_a_code(raw_code)
        if not re.fullmatch(r"\d{6}", norm or "") or norm in seen:
            continue
        name = str(item.get(name_col) or "").strip()
        if not name or name.lower() == "nan":
            continue
        market = _stock_market_from_code(raw_code, norm)
        rows.append({"code": norm, "name": name, "market": market, "display_code": f"{norm}.{market}" if market else norm})
        seen.add(norm)
    return rows, "AKShare stock_info_a_code_name"


def build_stock_universe() -> dict[str, Any]:
    errors: list[str] = []
    rows, source = _stock_universe_rows_from_local()
    if not rows:
        errors.append(source)
        rows, source = _stock_universe_rows_from_akshare()
    if not rows:
        errors.append(source)
        rows = [
            {"code": "000001", "name": "平安银行", "market": "SZ", "display_code": "000001.SZ"},
            {"code": "600519", "name": "贵州茅台", "market": "SH", "display_code": "600519.SH"},
            {"code": "300750", "name": "宁德时代", "market": "SZ", "display_code": "300750.SZ"},
        ]
        source = "watchlist_fallback"
    rows.sort(key=lambda row: (str(row.get("market") or ""), str(row.get("code") or "")))
    return {
        "status": "ok" if len(rows) >= 4000 else "partial",
        "as_of": datetime.now().date().isoformat(),
        "generated_at": iso_now(),
        "source": source,
        "count": len(rows),
        "errors": errors,
        "rows": rows,
    }


def _stock_ai_clean_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if hasattr(value, "item"):
            value = value.item()
    except Exception:
        pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()[:19]
        except Exception:
            return str(value)
    return value


def _stock_ai_call_without_dead_local_proxy(builder: Any) -> Any:
    with _STOCK_PROXY_ENV_LOCK:
        saved = {key: os.environ.get(key) for key in _STOCK_PROXY_ENV_KEYS}
        try:
            for key, value in saved.items():
                if _stock_proxy_value_is_dead_local(value):
                    os.environ.pop(key, None)
            existing = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
            parts = [item.strip() for item in existing.split(",") if item.strip()]
            for host in (
                "127.0.0.1", "localhost", "api.waditu.com", "push2his.eastmoney.com",
                "quote.eastmoney.com", "www.eastmoney.com", "stock.eastmoney.com",
            ):
                if host not in parts:
                    parts.append(host)
            value = ",".join(parts)
            os.environ["NO_PROXY"] = value
            os.environ["no_proxy"] = value
            return builder()
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _stock_ai_ts_code(norm: str) -> str:
    if norm.startswith(("6", "9")):
        return norm + ".SH"
    if norm.startswith(("4", "8")):
        return norm + ".BJ"
    return norm + ".SZ"


def _stock_ai_pick_column(columns: list[Any], patterns: tuple[str, ...], fallback_index: int = 0) -> Any:
    for pattern in patterns:
        regex = re.compile(pattern, re.I)
        for col in columns:
            if regex.search(str(col)):
                return col
    if 0 <= fallback_index < len(columns):
        return columns[fallback_index]
    return columns[0] if columns else ""


def _stock_ai_profile_akshare(norm: str) -> dict[str, Any]:
    def builder() -> dict[str, Any]:
        import akshare as ak  # type: ignore

        frame = ak.stock_individual_info_em(symbol=norm)
        if frame is None or getattr(frame, "empty", True):
            return {"source": "AKShare stock_individual_info_em", "fields": {}, "items": []}
        columns = list(frame.columns)
        if not columns:
            return {"source": "AKShare stock_individual_info_em", "fields": {}, "items": []}
        item_col = _stock_ai_pick_column(columns, (r"^item$", r"项目|指标|名称"), 0)
        value_col = _stock_ai_pick_column(columns, (r"^value$", r"取值|数值|内容|值"), 1 if len(columns) > 1 else 0)
        fields: dict[str, Any] = {}
        items: list[dict[str, Any]] = []
        for _, rec in frame.iterrows():
            key = str(rec.get(item_col) or "").strip()
            if not key or key.lower() == "nan":
                continue
            value = _stock_ai_clean_value(rec.get(value_col))
            if value in (None, "", "nan", "NaN"):
                continue
            fields[key] = value
            items.append({"item": key, "value": value})
        return {"source": "AKShare stock_individual_info_em", "fields": fields, "items": items[:40]}

    try:
        return _stock_ai_call_without_dead_local_proxy(builder)
    except Exception:
        return {"source": "AKShare stock_individual_info_em", "fields": {}, "items": []}



def _stock_ai_f10_code(norm: str) -> str:
    market = _stock_market_from_code(norm, norm) or "SZ"
    return market.upper() + norm


def _stock_ai_profile_eastmoney(norm: str) -> dict[str, Any]:
    params = urllib.parse.urlencode({"code": _stock_ai_f10_code(norm)})
    url = "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?" + params
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"QuantStrategyAgent/{APP_VERSION}",
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://emweb.securities.eastmoney.com/",
        },
    )
    try:
        with _urlopen_ipv4(req, timeout=15, context=SSL_CONTEXT) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return {"source": "Eastmoney F10 CompanySurvey", "fields": {}, "items": []}
    base = ((payload.get("jbzl") or [{}]) if isinstance(payload, dict) else [{}])[0] or {}
    issue = ((payload.get("fxxg") or [{}]) if isinstance(payload, dict) else [{}])[0] or {}
    fields = {
        "股票简称": base.get("SECURITY_NAME_ABBR"),
        "公司全称": base.get("ORG_NAME"),
        "证券类型": base.get("SECURITY_TYPE"),
        "交易市场": base.get("TRADE_MARKET"),
        "行业": base.get("EM2016") or base.get("INDUSTRYCSRC1"),
        "证监会行业": base.get("INDUSTRYCSRC1"),
        "成立时间": str(issue.get("FOUND_DATE") or "")[:10],
        "上市时间": str(issue.get("LISTING_DATE") or "")[:10],
        "发行价格": issue.get("ISSUE_PRICE"),
        "发行市盈率": issue.get("AFTER_ISSUE_PE"),
        "首日开盘价": issue.get("OPEN_PRICE"),
        "首日收盘价": issue.get("CLOSE_PRICE"),
        "法人代表": base.get("LEGAL_PERSON"),
        "董事长": base.get("CHAIRMAN"),
        "员工人数": base.get("EMP_NUM"),
        "注册资本": base.get("REG_CAPITAL"),
        "注册地址": base.get("REG_ADDRESS"),
        "公司网址": base.get("ORG_WEB"),
        "主营业务": re.sub(r"\s+", " ", str(base.get("ORG_PROFILE") or "")).strip()[:900],
        "经营范围": re.sub(r"\s+", " ", str(base.get("BUSINESS_SCOPE") or "")).strip()[:900],
    }
    fields = {key: _stock_ai_clean_value(value) for key, value in fields.items() if value not in (None, "", "nan", "NaN")}
    return {"source": "Eastmoney F10 CompanySurvey", "fields": fields, "items": [{"item": key, "value": value} for key, value in fields.items()]}


def _stock_ai_profile(norm: str) -> dict[str, Any]:
    profile = _stock_ai_profile_akshare(norm)
    if profile.get("items"):
        return profile
    return _stock_ai_profile_eastmoney(norm)

def _stock_ai_daily_basic_tushare(norm: str) -> dict[str, Any]:
    token = os.environ.get("TUSHARE_TOKEN") or os.environ.get("TUSHARE_PRO_TOKEN")
    if not token:
        return {"source": "Tushare daily_basic", "latest": {}, "rows": []}

    def builder() -> dict[str, Any]:
        import tushare as ts  # type: ignore

        pro = ts.pro_api(token)
        end = datetime.now().date() + timedelta(days=2)
        beg = end - timedelta(days=140)
        fields = "ts_code,trade_date,turnover_rate,turnover_rate_f,volume_ratio,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv"
        frame = pro.daily_basic(
            ts_code=_stock_ai_ts_code(norm),
            start_date=beg.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            fields=fields,
        )
        if frame is None or getattr(frame, "empty", True):
            return {"source": "Tushare daily_basic", "latest": {}, "rows": []}
        frame = frame.sort_values("trade_date")
        rows: list[dict[str, Any]] = []
        for _, rec in frame.tail(30).iterrows():
            row: dict[str, Any] = {}
            for col in frame.columns:
                value = _stock_ai_clean_value(rec.get(col))
                if value is not None:
                    row[str(col)] = value
            date_raw = str(row.get("trade_date") or "")
            if re.fullmatch(r"\d{8}", date_raw):
                row["date"] = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:]}"
            rows.append(row)
        return {"source": "Tushare daily_basic", "latest": rows[-1] if rows else {}, "rows": rows}

    try:
        return _stock_ai_call_without_dead_local_proxy(builder)
    except Exception:
        return {"source": "Tushare daily_basic", "latest": {}, "rows": []}


def _stock_ai_news_akshare(norm: str, name: str, limit: int = 20) -> list[dict[str, Any]]:
    def builder() -> list[dict[str, Any]]:
        import akshare as ak  # type: ignore

        frame = ak.stock_news_em(symbol=norm)
        if frame is None or getattr(frame, "empty", True):
            return []
        columns = list(frame.columns)
        title_col = _stock_ai_pick_column(columns, (r"标题|title|新闻"), 0)
        time_col = _stock_ai_pick_column(columns, (r"时间|日期|发布时间|time|date"), 1 if len(columns) > 1 else 0)
        source_col = _stock_ai_pick_column(columns, (r"来源|source|文章来源"), 2 if len(columns) > 2 else 0)
        url_col = _stock_ai_pick_column(columns, (r"链接|url|link"), len(columns) - 1)
        rows: list[dict[str, Any]] = []
        for _, rec in frame.head(max(limit, 40)).iterrows():
            title = str(rec.get(title_col) or "").strip()
            if not title or title.lower() == "nan":
                continue
            rows.append({
                "published_at": str(_stock_ai_clean_value(rec.get(time_col)) or "")[:19],
                "event_type": "个股新闻",
                "code": norm,
                "name": name,
                "title": title[:220],
                "source": str(rec.get(source_col) or "东方财富个股新闻").strip()[:80],
                "url": str(rec.get(url_col) or "").strip(),
            })
            if len(rows) >= limit:
                break
        return rows

    try:
        return _stock_ai_call_without_dead_local_proxy(builder)
    except Exception:
        return []


def _stock_ai_merge_dicts(*rows: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            if value not in (None, "", "--", "nan", "NaN"):
                merged[key] = value
    return merged


def _stock_ai_extract_code(subject: str, context: Any) -> str:
    candidates: list[Any] = [subject]
    if isinstance(context, dict):
        quote = context.get("quote") if isinstance(context.get("quote"), dict) else {}
        candidates.extend([
            quote.get("code"), quote.get("display_code"), quote.get("symbol"),
            context.get("code"), context.get("display_code"), context.get("symbol"),
        ])
    for value in candidates:
        norm = normalize_a_code(str(value or ""))
        if re.fullmatch(r"\d{6}", norm or ""):
            return norm
    return ""


def _stock_ai_compact_series(series_list: Any, limit: int = 120) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for series in series_list or []:
        if not isinstance(series, dict):
            continue
        raw_points = series.get("points") or series.get("data") or []
        points: list[dict[str, Any]] = []
        for point in raw_points[-limit:] if isinstance(raw_points, list) else []:
            if not isinstance(point, dict):
                continue
            value = _finite_number(point.get("value"))
            date = str(point.get("date") or point.get("x") or "")[:10]
            if date and value is not None:
                points.append({"date": date, "value": value})
        if points:
            compact.append({
                "id": series.get("id"),
                "label": series.get("label"),
                "unit": series.get("unit"),
                "source": series.get("source"),
                "as_of": series.get("as_of") or points[-1].get("date"),
                "latest": points[-1],
                "points": points,
            })
    return compact


def _stock_ai_news_key(row: dict[str, Any]) -> str:
    return str(row.get("url") or "") or (str(row.get("published_at") or "") + "|" + str(row.get("title") or ""))


def _stock_ai_related_news(norm: str, name: str, rows: Any, limit: int = 24) -> list[dict[str, Any]]:
    related: list[dict[str, Any]] = []
    seen: set[str] = set()
    name = str(name or "").strip()
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        blob = " ".join(str(raw.get(key) or "") for key in ("code", "name", "title", "event_type", "source"))
        if norm not in blob and (not name or name not in blob):
            continue
        key = _stock_ai_news_key(raw)
        if key in seen:
            continue
        seen.add(key)
        related.append({
            "published_at": str(raw.get("published_at") or raw.get("date") or "")[:19],
            "event_type": str(raw.get("event_type") or raw.get("channel") or "新闻")[:40],
            "code": str(raw.get("code") or norm)[:20],
            "name": str(raw.get("name") or name)[:40],
            "title": title[:240],
            "source": str(raw.get("source") or "")[:80],
            "url": str(raw.get("url") or "")[:300],
        })
        if len(related) >= limit:
            break
    return related


def build_stock_ai_context(subject: str, context: Any) -> dict[str, Any]:
    ctx: dict[str, Any] = dict(context) if isinstance(context, dict) else {}
    norm = _stock_ai_extract_code(subject, ctx)
    if not norm:
        return ctx
    board = cached_data(f"stock:ai-board:{norm}:r45.14", 300, lambda: build_board_stock_fallback(norm))
    record = board.get("record") if isinstance(board, dict) and isinstance(board.get("record"), dict) else {}
    existing_quote = ctx.get("quote") if isinstance(ctx.get("quote"), dict) else {}
    identity = _stock_identity_from_universe(norm)
    quote = _stock_ai_merge_dicts(identity, existing_quote, record)
    name = str(quote.get("name") or identity.get("name") or "")
    profile = cached_data(f"stock:ai-profile:{norm}:r45.14", 21600, lambda: _stock_ai_profile(norm))
    profile_fields = profile.get("fields") if isinstance(profile, dict) and isinstance(profile.get("fields"), dict) else {}
    if profile_fields:
        quote = _stock_ai_merge_dicts(quote, {
            "industry": profile_fields.get("行业"),
            "list_date": profile_fields.get("上市时间"),
            "found_date": profile_fields.get("成立时间"),
            "business_summary": profile_fields.get("主营业务"),
            "total_mv_profile": profile_fields.get("总市值"),
            "circ_mv_profile": profile_fields.get("流通市值"),
        })
    daily_basic = cached_data(f"stock:ai-daily-basic:{norm}:r45.14", 21600, lambda: _stock_ai_daily_basic_tushare(norm))
    bars = board.get("recent_bars") if isinstance(board, dict) and isinstance(board.get("recent_bars"), list) else []
    series = _stock_ai_compact_series(board.get("series") if isinstance(board, dict) else [])
    existing_news = ctx.get("news") if isinstance(ctx.get("news"), list) else []
    direct_news = cached_data(f"stock:ai-news:{norm}:r45.14", 1800, lambda: _stock_ai_news_akshare(norm, name, 24))
    global_news: list[dict[str, Any]] = []
    try:
        global_news.extend((build_sina_news(80).get("rows") or [])[:80])
    except Exception:
        pass
    try:
        global_news.extend((build_wallstreet_wechat_news(80).get("rows") or [])[:80])
    except Exception:
        pass
    related_news = _stock_ai_related_news(norm, name, list(existing_news) + list(direct_news) + global_news, 28)
    market_news = [] if related_news else [
        {key: row.get(key) for key in ("published_at", "event_type", "title", "source", "url")}
        for row in global_news[:12] if isinstance(row, dict)
    ]
    ctx.update({
        "quote": quote,
        "profile": profile,
        "valuation_liquidity": daily_basic,
        "stock_data": {
            "code": norm,
            "name": name,
            "status": board.get("status") if isinstance(board, dict) else "",
            "as_of": (board.get("as_of") if isinstance(board, dict) else "") or quote.get("as_of"),
            "source": (board.get("source") if isinstance(board, dict) else "") or quote.get("source"),
            "latest_bar": bars[-1] if bars else None,
            "recent_bars": bars[-40:],
            "series": series,
        },
        "news": related_news,
        "market_news_when_stock_news_sparse": market_news,
        "coverage": {
            "quote_fields": len([key for key, value in quote.items() if value not in (None, "", "--")]),
            "recent_bar_count": len(bars),
            "series_count": len(series),
            "profile_item_count": len(profile.get("items") or []) if isinstance(profile, dict) else 0,
            "valuation_row_count": len(daily_basic.get("rows") or []) if isinstance(daily_basic, dict) else 0,
            "related_news_count": len(related_news),
            "market_news_count": len(market_news),
        },
    })
    return ctx


def enrich_ai_context(module: str, subject: str, context: Any, *, mode: str = "brief") -> Any:
    if str(module or "").lower() == "stock":
        return build_stock_ai_context(subject, context)
    return context
def _allocation_news_records(value: Any, output: list[dict[str, Any]], depth: int = 0) -> None:
    if depth > 7 or len(output) >= 12:
        return
    if isinstance(value, list):
        for item in value:
            _allocation_news_records(item, output, depth + 1)
            if len(output) >= 12:
                break
        return
    if not isinstance(value, dict):
        return
    title = next((value.get(key) for key in ("title", "headline", "event", "news_title", "name") if value.get(key)), None)
    date = next((value.get(key) for key in ("published_at", "date", "time", "as_of", "trade_date") if value.get(key)), None)
    if title and date and len(str(title)) >= 8:
        output.append({"date": str(date)[:19], "title": str(title)[:240], "source": str(value.get("source") or value.get("provider") or "数据看板")[:80]})
    for child in value.values():
        if isinstance(child, (dict, list)):
            _allocation_news_records(child, output, depth + 1)
            if len(output) >= 12:
                break


def build_allocation_report_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    allocation = snapshot.get("allocations", {})
    profile_name = allocation.get("default_profile") or "equity_preferred"
    profile = allocation.get("profiles", {}).get(profile_name) or allocation.get("recommended", {})
    backtest = snapshot.get("backtest", {})
    recommended = backtest.get("strategies", {}).get("recommended", {})
    selected_factors: dict[str, Any] = {}
    for role, rows in snapshot.get("factor_selection", {}).get("roles", {}).items():
        selected_factors[role] = [
            {key: row.get(key) for key in ("name", "transform", "train_ic", "block_stability", "band_power", "score", "observations")}
            for row in rows[:3]
        ]
    events: list[dict[str, Any]] = []
    try:
        board = proxy_json("board", "/api/v1/snapshot", timeout=12)
        _allocation_news_records(board, events)
    except Exception:
        events = []
    evidence = [
        {"institution": row.get("institution"), "title": row.get("title"), "method": row.get("method")}
        for row in snapshot.get("research_evidence", [])[:16]
    ]
    return {
        "data_as_of": snapshot.get("data_as_of"),
        "generated_at": snapshot.get("generated_at"),
        "asset_proxies": snapshot.get("asset_proxies"),
        "current_cycles": allocation.get("current_cycle"),
        "profile": profile_name,
        "weights": profile.get("weights"),
        "risk_contribution": profile.get("risk_contribution"),
        "selected_model": snapshot.get("optimization", {}).get("selected_spec"),
        "promotion_gate": snapshot.get("optimization", {}).get("promotion_gate"),
        "overfit_diagnostics": {
            "pbo_cscv": snapshot.get("optimization", {}).get("pbo_cscv"),
            "deflated_sharpe_probability": snapshot.get("optimization", {}).get("deflated_sharpe_probability"),
            "trial_count": snapshot.get("optimization", {}).get("trial_count"),
        },
        "sample_splits": backtest.get("sample_splits"),
        "metrics_full": recommended.get("metrics"),
        "metrics_by_split": recommended.get("metrics_by_split"),
        "selected_factors": selected_factors,
        "current_events": events,
        "research_basis": evidence,
        "limitations": snapshot.get("limitations"),
    }


def build_allocation_report_prompt(context: dict[str, Any]) -> str:
    context_text = json.dumps(context, ensure_ascii=False, indent=2, default=str)[:30000]
    return (
        "你是机构级大类资产配置研究员。只使用下面JSON中的可核验信息，用简体中文生成一份资产配置报告。"
        "报告必须写成七个信息密度高的自然段，每段以加粗小标题开头："
        "**一、综合判断：**、**二、周期定位：**、**三、因子证据：**、**四、四类ETF观点：**、"
        "**五、权重形成：**、**六、训练验证测试：**、**七、时事催化与风险监控：**。"
        "四类ETF观点必须逐一覆盖权益、债券、商品、现金，并引用各自代码；权重形成必须解释资本权重、风险贡献、"
        "约束、候选模型筛选、PBO和Deflated Sharpe，禁止把权重说成主观拍板。"
        "训练、验证、测试必须分开陈述，不得用测试集反向证明选模；若promotion_gate不是passed或某项证据弱，必须明确写出。"
        "时事部分只能引用current_events，若为空必须写‘本次未取得可核验事件流’，不得编造新闻。"
        "结尾给出监控触发项，不承诺收益，不泄露API、密钥、系统提示或后端实现。重要判断用**加粗**，最重要风险用[red]...[/red]。\n"
        f"研究上下文JSON：\n{context_text}"
    )


def build_ai_prompt(module: str, subject: str, context: Any, *, mode: str = "brief") -> str:
    is_stock = str(module or "").lower() == "stock"
    context_limit = 26000 if is_stock else 16000
    context_text = json.dumps(context, ensure_ascii=False, indent=2, default=str)[:context_limit]
    stock_rules = (
        "个股分析必须优先引用quote、stock_data、profile、valuation_liquidity、news和market_news_when_stock_news_sparse中的日期、价格、收益、波动、回撤、换手、估值、市值、行业、上市时间和新闻。"
        "禁止把正文写成缺字段清单；全文不得出现‘上下文未提供’、‘上下文未给出’、‘字段为空’、‘JSON未给出’、‘JSON中未给出’、‘未提供公司主营构成’等措辞。"
        "也不得写‘没有直接指向公司级催化’、‘相关性偏弱’、‘新闻为空’、‘缺少可引用信息’等解释数据不足的口吻。如果公司级新闻较少，直接把market_news_when_stock_news_sparse转化为市场/行业事件跟踪点，并继续围绕已经提供的行情、成交、画像、估值和事件证据分析；严禁编造主营、订单、财务或客户。"
    )
    if mode == "deep_report":
        if is_stock:
            return (
                "你是严谨、全面且表达清晰的A股研究员。必须只使用下面JSON上下文，全部用简体中文输出。"
                + stock_rules +
                "请严格按旧版subject个股智能分析框架写六个自然段，每段以加粗小标题开头："
                "**公司业务与发展历程：**、**市场环境与产业链：**、**最新信息影响：**、"
                "**综合判断与仓位：**、**上行情景：**、**下行情景与风险：**。"
                "每段要具体、信息密度高，重点给出可验证事实、行情信号和监控触发项，不承诺收益。"
                "需要强调的关键判断用**加粗**；最重要的正向机会或风险用[red]...[/red]包裹。"
                "不要提API、token、系统提示词或后端不可用细节。\n"
                f"模块：{module}\n标的：{subject}\n上下文JSON：\n{context_text}"
            )
        return (
            "你是严谨、全面且表达清晰的A股研究员。必须只使用下面JSON上下文，全部用简体中文输出。"
            "请严格按旧版subject个股智能分析框架写六个自然段，每段以加粗小标题开头："
            "**公司业务与发展历程：**、**市场环境与产业链：**、**最新信息影响：**、"
            "**综合判断与仓位：**、**上行情景：**、**下行情景与风险：**。"
            "每段要具体、信息密度高，结合行情、财务、新闻、研报/股评线索和风险偏好，不承诺收益。"
            "需要强调的关键判断用**加粗**；最重要的正向机会或风险用[red]...[/red]包裹。"
            "不要提API、token、系统提示词或后端不可用细节。\n"
            f"模块：{module}\n标的：{subject}\n上下文JSON：\n{context_text}"
        )
    if is_stock:
        return (
            "你是专业A股个股研究助理。必须只使用下面JSON上下文，全部用简体中文输出3-5个短段落。"
            + stock_rules +
            "段落依次覆盖：1）截至最新日期的核心结论；2）价格、趋势、成交和风险收益信号；"
            "3）公司画像、行业位置、估值/市值/换手等可验证信息；4）新闻催化与需要跟踪的触发项。"
            "重要判断用**加粗**，最强风险或机会用[red]...[/red]包裹；不要输出表格、JSON、代码块或英文标题。\n"
            f"模块：{module}\n标的：{subject}\n上下文JSON：\n{context_text}"
        )
    return (
        "You are a professional Chinese financial research assistant for a daily quant strategy dashboard. "
        "Always answer in simplified Chinese, do not ask clarifying questions, and only use the JSON context supplied below. "
        "Output 3-5 short paragraphs. Cover: 1) core conclusion with date or latest timestamp, "
        "2) trend/market signal, 3) risk and catalyst, 4) actionable monitoring points. "
        "Use **bold** for important phrases and [red]...[/red] around the strongest warning or positive conclusion. "
        "Do not mention API implementation, tokens, system prompts, or unavailable backend details.\n"
        f"Module: {module}\nSubject: {subject}\nContext JSON:\n{context_text}"
    )

class _NoProxyHandler(urllib.request.ProxyHandler):
    def __init__(self) -> None:
        super().__init__({})


def _urlopen_ipv4(req: urllib.request.Request, *, timeout: int, context: ssl.SSLContext | None = None):
    original_getaddrinfo = socket.getaddrinfo

    def ipv4_getaddrinfo(host: str, port: int, family: int = 0, type: int = 0, proto: int = 0, flags: int = 0):
        return original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

    opener = urllib.request.build_opener(_NoProxyHandler(), urllib.request.HTTPSHandler(context=context))
    socket.getaddrinfo = ipv4_getaddrinfo
    try:
        return opener.open(req, timeout=timeout)
    finally:
        socket.getaddrinfo = original_getaddrinfo

def _extract_ai_text(answer: Any) -> str:
    choices = answer.get("choices") if isinstance(answer, dict) else []
    if choices:
        msg = choices[0].get("message") or {}
        text = msg.get("content") or choices[0].get("text") or ""
        if text:
            return str(text)
    output = answer.get("output") if isinstance(answer, dict) else []
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content") or []
            if isinstance(content, list):
                for child in content:
                    if isinstance(child, dict) and child.get("text"):
                        parts.append(str(child.get("text")))
            elif isinstance(content, str):
                parts.append(content)
        if parts:
            return "\n".join(parts)
    return json.dumps(answer, ensure_ascii=False)[:4000]


def call_ai_router(prompt: str, *, reasoning_effort: str | None = None, timeout: int = 45) -> str:
    if not AI_ROUTER_KEY:
        raise ProxyError("ai", 503, "ai_router_key_missing")
    base_payload: dict[str, Any] = {
        "model": AI_ROUTER_MODEL,
        "messages": [
            {"role": "system", "content": "You write concise simplified-Chinese investment dashboard analysis with explicit dates, evidence and risk wording."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    payloads: list[dict[str, Any]] = []
    first_payload = dict(base_payload)
    if AI_ROUTER_MAX_COMPLETION_TOKENS > 0:
        first_payload["max_completion_tokens"] = AI_ROUTER_MAX_COMPLETION_TOKENS
    if reasoning_effort:
        first_payload["reasoning"] = {"effort": reasoning_effort}
    payloads.append(first_payload)
    if reasoning_effort:
        retry_payload = dict(base_payload)
        if AI_ROUTER_MAX_COMPLETION_TOKENS > 0:
            retry_payload["max_completion_tokens"] = AI_ROUTER_MAX_COMPLETION_TOKENS
        retry_payload["reasoning_effort"] = reasoning_effort
        payloads.append(retry_payload)
    payloads.append(dict(base_payload))
    last_error: tuple[int, Any] | None = None
    for index, payload in enumerate(payloads):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            AI_ROUTER_URL,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "Authorization": f"Bearer {AI_ROUTER_KEY}",
                "User-Agent": f"QuantStrategyAgent/{APP_VERSION}",
            },
        )
        try:
            with _urlopen_ipv4(req, timeout=timeout, context=SSL_CONTEXT) as resp:
                answer = json.loads(resp.read().decode("utf-8", errors="replace"))
            return _extract_ai_text(answer)
        except urllib.error.HTTPError as exc:
            body = parse_body(exc.read(), exc.headers.get("Content-Type", ""))
            last_error = (exc.code, body)
            if exc.code not in {400, 422, 502, 503, 504, 524} or index == len(payloads) - 1:
                raise ProxyError("ai", exc.code, f"ai_router_http_{exc.code}", body) from exc
        except Exception as exc:  # noqa: BLE001
            raise ProxyError("ai", 502, "ai_router_unavailable", {"error": str(exc)}) from exc
    if last_error:
        raise ProxyError("ai", last_error[0], f"ai_router_http_{last_error[0]}", last_error[1])
    raise ProxyError("ai", 502, "ai_router_empty_response")



def clean_ai_text(module: str, text: str) -> str:
    if str(module or "").lower() != "stock":
        return text
    original = text or ""
    cleaned = original
    bad_sentence = re.compile(
        r"[^。！？\n]*(?:上下文未(?:提供|给出)|字段为空|新闻字段为空|新闻为空|JSON\s*中?未(?:给出|提供)|缺少可引用信息|没有直接指向(?:[^。！？\n]*)?|相关性偏弱|未提供(?:公司主营构成|成立/上市历程|客户结构|财务报表|研发投入|研报/股评线索|订单数据|所属行业指数|产业链上下游|宏观/政策新闻|有效换手信息))[^。！？\n]*[。！？]?",
        re.I,
    )
    cleaned = bad_sentence.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned or original

def render_ai_markup(text: str) -> str:
    import html
    import re

    safe = html.escape(text or "")
    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe, flags=re.S)
    safe = re.sub(r"\[red\](.+?)\[/red\]", lambda m: f'<strong class="ai-red">{m.group(1)}</strong>', safe, flags=re.S | re.I)
    paragraphs = [x.strip() for x in safe.split("\n") if x.strip()]
    return "".join(f"<p>{p}</p>" for p in paragraphs[:8])


def ensure_service_login(service: str) -> None:
    state = service_session(service)
    if time.time() - state.authenticated_at < 1800:
        return
    if service == "kline":
        try:
            current = proxy_json("kline", "/api/session", auth=True, timeout=10)
            if isinstance(current, dict) and current.get("authenticated"):
                state.authenticated_at = time.time()
                return
        except ProxyError:
            pass
        try:
            proxy_json("kline", "/api/login", method="POST", payload={"username": USERNAME, "password": PASSWORD}, auth=True, timeout=12)
            state.authenticated_at = time.time()
            return
        except ProxyError as login_error:
            try:
                proxy_json("kline", "/api/register", method="POST", payload={"username": USERNAME, "password": PASSWORD}, auth=True, timeout=12)
                state.authenticated_at = time.time()
                return
            except ProxyError:
                raise login_error
    elif service == "factor":
        try:
            current = proxy_json("factor", "/api/auth/me", auth=True, timeout=10)
            if isinstance(current, dict) and current.get("authenticated"):
                state.authenticated_at = time.time()
                return
        except ProxyError:
            pass
        try:
            proxy_json("factor", "/api/auth/login", method="POST", payload={"username": USERNAME, "password": PASSWORD}, auth=True, timeout=12)
            state.authenticated_at = time.time()
            return
        except ProxyError as login_error:
            try:
                proxy_json("factor", "/api/auth/register", method="POST", payload={"username": USERNAME, "password": PASSWORD}, auth=True, timeout=12)
                state.authenticated_at = time.time()
                return
            except ProxyError:
                raise login_error
    elif service == "ai_monitor":
        proxy_form(
            "ai_monitor",
            "/login",
            {"username": USERNAME, "password": PASSWORD},
            timeout=12,
        )
        current = proxy_json("ai_monitor", "/api/snapshot", auth=True, timeout=20)
        if not isinstance(current, dict) or "raw" in current:
            raise ProxyError("ai_monitor", 502, "upstream_login_failed")
        state.authenticated_at = time.time()

app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8071"))
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=port, threaded=True)
