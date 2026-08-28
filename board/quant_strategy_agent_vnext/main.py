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
from datetime import datetime
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
import research_evidence_backend
import rotation_app as rotation
import optimizer_backend
import technical_factor_backend


APP_VERSION = "2026.08.28-vnext-all-modules-merge-repair-v1"
legacy.APP_VERSION = APP_VERSION
rotation.APP_VERSION = APP_VERSION

PROJECT_ROOT = Path(__file__).resolve().parents[2]
factor_lab_backend.API_VERSION = "factor-lab-api/2.9"
factor_lab_backend.ENGINE_PATH = (
    PROJECT_ROOT / "model" / "factor_laboratory" / "worker.py"
)
for key, label in {
    "lstm": "LSTM",
    "gru": "GRU",
    "rl_transformer": "Transformer+LLM",
    "strategy": "等权 / RankIC / OLS / Lasso / Ridge / MLP 打分回测",
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


def _service_payload() -> dict[str, Any]:
    checks: dict[str, Callable[[], dict[str, Any]]] = {
        "board": lambda: legacy.safe_proxy("board", "/healthz"),
        "kline": lambda: legacy.safe_proxy("kline", "/health"),
        "factor": lambda: legacy.safe_proxy("factor", "/api/status"),
        "ai_monitor": lambda: legacy.safe_proxy("ai_monitor", "/healthz"),
        "trump": lambda: legacy.safe_proxy("trump", "/api/tracker/health"),
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


def _dashboard_stock_market(raw_code: str, norm: str) -> str:
    text = str(raw_code or "").upper()
    if text.endswith(".SH") or norm.startswith(("6", "9")):
        return "SH"
    if text.endswith(".BJ") or norm.startswith(("4", "8")):
        return "BJ"
    if text.endswith(".SZ") or norm.startswith(("0", "2", "3")):
        return "SZ"
    return ""


def _dashboard_stock_universe_from_local() -> tuple[list[dict[str, Any]], str]:
    root = Path(__file__).resolve().parent
    candidates = [
        root / "data" / "all_a_stocks.json",
        root.parent / "quant_strategy_agent" / "data" / "all_a_stocks.json",
    ]
    source = next((path for path in candidates if path.exists()), None)
    if source is None:
        return [], "local_all_a_stocks_missing"
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        return [], f"local_all_a_stocks_failed:{type(exc).__name__}"
    items = payload.get("rows", []) if isinstance(payload, dict) else payload
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        raw_code = str(
            item.get("code") or item.get("display_code") or item.get("symbol") or ""
        ).strip().upper()
        norm = _dashboard_stock_code(raw_code)
        name = str(item.get("name") or item.get("stock_name") or "").strip()
        if not re.fullmatch(r"\d{6}", norm or "") or not name or norm in seen:
            continue
        market = str(item.get("market") or "").strip().upper() or _dashboard_stock_market(raw_code, norm)
        rows.append({
            "code": norm,
            "name": name,
            "market": market,
            "display_code": str(item.get("display_code") or (f"{norm}.{market}" if market else norm)),
        })
        seen.add(norm)
    return rows, str(source)


def _dashboard_stock_universe_from_akshare() -> tuple[list[dict[str, Any]], str]:
    def builder() -> tuple[list[dict[str, Any]], str]:
        import akshare as ak  # type: ignore

        frame = ak.stock_info_a_code_name()
        if frame is None or getattr(frame, "empty", True):
            return [], "akshare_stock_info_a_code_name_empty"
        columns = [str(col) for col in frame.columns]
        code_col = next((col for col in columns if col.lower() in {"code", "symbol"} or "代码" in col), columns[0])
        name_col = next((col for col in columns if col.lower() in {"name", "stock_name"} or "名称" in col), columns[1] if len(columns) > 1 else columns[0])
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for _, item in frame[[code_col, name_col]].iterrows():
            raw_code = str(item.get(code_col) or "").strip().upper()
            norm = _dashboard_stock_code(raw_code)
            name = str(item.get(name_col) or "").strip()
            if not re.fullmatch(r"\d{6}", norm or "") or not name or norm in seen:
                continue
            market = _dashboard_stock_market(raw_code, norm)
            rows.append({
                "code": norm,
                "name": name,
                "market": market,
                "display_code": f"{norm}.{market}" if market else norm,
            })
            seen.add(norm)
        return rows, "AKShare stock_info_a_code_name"

    try:
        return _dashboard_call_without_dead_local_proxy(builder)
    except Exception as exc:  # noqa: BLE001
        return [], f"akshare_stock_info_a_code_name_failed:{type(exc).__name__}"


def _dashboard_build_stock_universe() -> dict[str, Any]:
    errors: list[str] = []
    rows, source = _dashboard_stock_universe_from_local()
    if not rows:
        errors.append(source)
        rows, source = _dashboard_stock_universe_from_akshare()
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
        "generated_at": legacy.iso_now(),
        "source": source,
        "count": len(rows),
        "errors": errors,
        "rows": rows,
    }


@app.get("/api/stock/universe")
def data_dashboard_stock_universe() -> Response:
    return jsonify(legacy.cached_data("stock:universe:r45-vnext", 86400, _dashboard_build_stock_universe))


def _dashboard_stock_return(rows: list[dict[str, Any]], days: int) -> float | None:
    closes = [legacy._finite_number(row.get("close")) for row in rows]
    closes = [value for value in closes if value is not None]
    if len(closes) <= days or not closes[-days - 1]:
        return None
    return round((closes[-1] / closes[-days - 1] - 1.0) * 100.0, 2)


def _dashboard_stock_vol(rows: list[dict[str, Any]], days: int) -> float | None:
    closes = [legacy._finite_number(row.get("close")) for row in rows]
    closes = [value for value in closes if value is not None]
    if len(closes) <= days:
        return None
    returns = []
    for prev, cur in zip(closes[-days - 1:-1], closes[-days:]):
        if prev:
            returns.append(cur / prev - 1.0)
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return round((variance ** 0.5) * (252 ** 0.5) * 100.0, 2)


def _dashboard_stock_mdd(rows: list[dict[str, Any]], days: int) -> float | None:
    closes = [legacy._finite_number(row.get("close")) for row in rows]
    closes = [value for value in closes if value is not None]
    window = closes[-days:] if len(closes) >= days else closes
    if not window:
        return None
    peak = window[0]
    drawdown = 0.0
    for value in window:
        peak = max(peak, value)
        if peak:
            drawdown = min(drawdown, value / peak - 1.0)
    return round(drawdown * 100.0, 2)


def _dashboard_build_board_stock_fallback(norm: str) -> dict[str, Any]:
    identity = _dashboard_stock_identity(norm)
    name = str(identity.get("name") or norm)
    try:
        ohlc = legacy.build_stock_ohlc(norm)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "partial",
            "source": "stock_universe_only",
            "record": {"code": norm, "name": name},
            "data": {"record": {"code": norm, "name": name}, "tables": [{"id": "stock_watchlist", "rows": [{"code": norm, "name": name}]}]},
            "errors": [str(exc)],
        }
    rows = ohlc.get("rows") if isinstance(ohlc, dict) else []
    rows = rows if isinstance(rows, list) else []
    last = rows[-1] if rows else {}
    prev = rows[-2] if len(rows) >= 2 else {}
    close = legacy._finite_number(last.get("close"))
    prev_close = legacy._finite_number(prev.get("close"))
    ret_1d = round((close / prev_close - 1.0) * 100.0, 2) if close is not None and prev_close else None
    record = {
        "code": norm,
        "name": name,
        "close": close,
        "qfq_close": close,
        "ret_1d": ret_1d,
        "ret_5d": _dashboard_stock_return(rows, 5),
        "ret_20d": _dashboard_stock_return(rows, 20),
        "vol_20d": _dashboard_stock_vol(rows, 20),
        "mdd_20d": _dashboard_stock_mdd(rows, 20),
        "turnover": legacy._finite_number(last.get("turnover")),
        "as_of": str(last.get("date") or ohlc.get("as_of") or "")[:10],
    }
    return {
        "status": "ok" if rows else "partial",
        "source": ohlc.get("source") or "stock_ohlc_fallback",
        "record": record,
        "data": {
            "record": record,
            "tables": [{"id": "stock_watchlist", "rows": [record]}],
            "series": [],
            "as_of": record["as_of"],
        },
    }


def data_dashboard_board_stock(code: str) -> Response:
    norm = _dashboard_stock_code(code)
    if not re.fullmatch(r"\d{6}", norm or ""):
        return jsonify({"status": "failed", "message": "invalid_stock_code"}), 400
    try:
        payload = legacy.proxy_json("board", f"/api/v1/stock/{legacy.urllib.parse.quote(norm)}")
        status = str(payload.get("status") or "").lower() if isinstance(payload, dict) else ""
        has_data = isinstance(payload, dict) and (payload.get("data") or payload.get("record"))
        if status not in {"failed", "error"} and has_data:
            return jsonify(payload)
    except Exception:  # noqa: BLE001
        pass
    return jsonify(_dashboard_build_board_stock_fallback(norm))


app.view_functions["board_stock"] = data_dashboard_board_stock


_DASHBOARD_PROXY_ENV_LOCK = threading.RLock()
_DASHBOARD_PROXY_ENV_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
)


def _dashboard_call_without_dead_local_proxy(builder: Callable[[], Any]) -> Any:
    with _DASHBOARD_PROXY_ENV_LOCK:
        saved = {key: os.environ.get(key) for key in _DASHBOARD_PROXY_ENV_KEYS}
        saved_no_proxy = {"NO_PROXY": os.environ.get("NO_PROXY"), "no_proxy": os.environ.get("no_proxy")}
        try:
            for key, value in saved.items():
                if value and ("127.0.0.1" in value or "localhost" in value):
                    os.environ.pop(key, None)
            existing = saved_no_proxy.get("NO_PROXY") or saved_no_proxy.get("no_proxy") or ""
            parts = [item.strip() for item in existing.split(",") if item.strip()]
            for host in ("127.0.0.1", "localhost", "www.eastmoney.com", "stock.eastmoney.com", "push2his.eastmoney.com"):
                if host not in parts:
                    parts.append(host)
            no_proxy = ",".join(parts)
            os.environ["NO_PROXY"] = no_proxy
            os.environ["no_proxy"] = no_proxy
            return builder()
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            for key, value in saved_no_proxy.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


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
        payload = legacy.cached_data("stock:universe:r45-vnext", 86400, _dashboard_build_stock_universe)
    except Exception:  # noqa: BLE001
        payload = {}
    rows = payload.get("rows") if isinstance(payload, dict) else []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and _dashboard_stock_code(row.get("code")) == norm:
            return row
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
        return _dashboard_call_without_dead_local_proxy(builder)
    except Exception:  # noqa: BLE001
        return []


def _dashboard_news_key(row: dict[str, Any]) -> str:
    return str(row.get("url") or "") or (str(row.get("published_at") or "") + "|" + str(row.get("title") or ""))


def _dashboard_stock_news_akshare(norm: str, name: str, limit: int) -> list[dict[str, Any]]:
    def builder() -> list[dict[str, Any]]:
        import akshare as ak  # type: ignore

        frame = ak.stock_news_em(symbol=norm)
        if frame is None or getattr(frame, "empty", True):
            return []
        columns = list(frame.columns)
        title_col = _dashboard_pick_col(columns, (r"标题|title|新闻",), 0)
        time_col = _dashboard_pick_col(columns, (r"时间|日期|发布时间|time|date",), 1 if len(columns) > 1 else 0)
        source_col = _dashboard_pick_col(columns, (r"来源|source|文章来源",), 2 if len(columns) > 2 else 0)
        url_col = _dashboard_pick_col(columns, (r"链接|url|link",), len(columns) - 1)
        rows: list[dict[str, Any]] = []
        for _, rec in frame.head(max(limit, 40)).iterrows():
            title = _dashboard_clean(rec.get(title_col))
            if not title:
                continue
            rows.append({
                "published_at": _dashboard_clean(rec.get(time_col))[:19],
                "event_type": "个股新闻",
                "code": norm,
                "name": name,
                "title": title[:220],
                "source": (_dashboard_clean(rec.get(source_col)) or "东方财富个股新闻")[:80],
                "url": _dashboard_clean(rec.get(url_col))[:300],
            })
            if len(rows) >= limit:
                break
        return rows

    try:
        return _dashboard_call_without_dead_local_proxy(builder)
    except Exception:  # noqa: BLE001
        return []


def _dashboard_related_news(norm: str, name: str, rows: Any, limit: int) -> list[dict[str, Any]]:
    related: list[dict[str, Any]] = []
    seen: set[str] = set()
    name = str(name or "").strip()
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        title = _dashboard_clean(raw.get("title"))
        if not title:
            continue
        blob = " ".join(_dashboard_clean(raw.get(key)) for key in ("code", "name", "title", "event_type", "source"))
        if norm not in blob and (not name or name not in blob):
            continue
        key = _dashboard_news_key(raw)
        if key in seen:
            continue
        seen.add(key)
        related.append({
            "published_at": _dashboard_clean(raw.get("published_at") or raw.get("date"))[:19],
            "event_type": (_dashboard_clean(raw.get("event_type") or raw.get("channel")) or "新闻")[:40],
            "code": (_dashboard_clean(raw.get("code")) or norm)[:20],
            "name": (_dashboard_clean(raw.get("name")) or name)[:40],
            "title": title[:240],
            "source": _dashboard_clean(raw.get("source"))[:80],
            "url": _dashboard_clean(raw.get("url"))[:300],
        })
        if len(related) >= limit:
            break
    return related


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
    rows = _dashboard_stock_news_akshare(norm, name, limit)
    if not rows:
        global_news: list[dict[str, Any]] = []
        for builder in (legacy.build_sina_news,):
            try:
                global_news.extend((builder(80).get("rows") or [])[:80])
            except Exception:  # noqa: BLE001
                pass
        rows = _dashboard_related_news(norm, name, global_news, limit)
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


@app.get("/api/research-evidence")
def research_evidence() -> Response:
    """Return compact evidence for the current existing workspace route."""
    route = str(request.args.get("route") or "").strip()
    return jsonify(research_evidence_backend.build(route))


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


@app.get("/api/trump/core")
def trump_core_proxy() -> Response:
    """Expose the verified Trump policy research payload."""
    refresh = request.args.get("refresh") == "1"
    query = {"scope": "core"}
    if refresh:
        query["refresh"] = "1"

    def load() -> Any:
        payload = legacy.proxy_json("trump", "/api/tracker", query=query, timeout=70)
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise legacy.ProxyError("trump", 502, "upstream_payload_unavailable")
        pressure = payload.get("pressure") or {}
        if pressure.get("available") is not True:
            raise legacy.ProxyError("trump", 503, "pressure_index_unavailable")
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
        payload = legacy.proxy_json("trump", "/api/tracker", query=query, timeout=35)
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise legacy.ProxyError("trump", 502, "truth_archive_unavailable")
        source = payload.get("source") or {}
        if source.get("verifiedSource") is not True:
            raise legacy.ProxyError("trump", 503, "truth_archive_unverified")
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
    "data_dashboard_stock_universe",
    "data_dashboard_stock_news",
    "data_dashboard_stock_reports",
    "rotation_snapshot",
    "rotation_tracking",
    "rotation_industry_dashboard",
    "factor_lab.bootstrap",
    "factor_lab.catalog",
    "factor_lab.dashboard",
    "services",
    "model_governance",
    "research_evidence",
    "global_market_supplement",
    "kline_health",
    "kline_llm_dashboard",
    "kline_llm_rule_context",
    "kline_llm_stock",
    "technical_factor_dashboard",
    "kline_stocks",
    "kline_dates",
    "kline_history",
    "kline_job",
    "factor_status",
    "factor_history",
    "factor_history_detail",
    "ai_monitor_proxy",
    "trump_core_proxy",
    "trump_truths_proxy",
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
        (endpoint == "kline_job" and request.args.get("live") == "1")
        or (endpoint == "trump_core_proxy" and request.args.get("refresh") == "1")
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
