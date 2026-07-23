"""Read-only Flask application for the public market dashboard.

The web process never fetches upstream data.  It only reads the two JSON
artifacts produced by the separate data pipeline and makes missing or invalid
data explicit to callers.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from flask import Flask, abort, jsonify, render_template, request


MODULE_META: dict[str, dict[str, str]] = {
    "macro": {
        "nav": "宏观",
        "title": "宏观高频与流动性看板",
        "subtitle": "增长 · 通胀 · 信用 · 流动性 · 地产 · 运输",
    },
    "global_markets": {
        "nav": "全球市场",
        "title": "全球权益与利率看板",
        "subtitle": "美洲 · 欧洲 · 亚洲 · 中国 · 港股",
    },
    "sw_industries": {
        "nav": "申万行业",
        "title": "申万一级行业高频景气看板",
        "subtitle": "行情 · 估值 · 宽度 · 高频景气代理",
    },
    "commodities": {
        "nav": "大宗商品",
        "title": "大宗商品行情与供需看板",
        "subtitle": "能源 · 黑色 · 有色 · 贵金属 · 农产品 · 航运",
    },
    "stock": {
        "nav": "个股",
        "title": "个股行情、基本面与事件看板",
        "subtitle": "价格 · 估值 · 资金 · 财务 · 公告",
    },
    "news_events": {
        "nav": "新闻事件",
        "title": "新闻、公告与宏观事件看板",
        "subtitle": "来源分级 · 事件去重 · 重要度 · 全链路追溯",
    },
}

TOP_LEVEL_STATUSES = {"ok", "partial", "stale", "failed"}
ITEM_STATUSES = TOP_LEVEL_STATUSES | {"unavailable", "warning", "unknown"}
SERIES_STATUSES = {"live", "stale", "unavailable"}
STOCK_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
SERIES_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SERIES_FREQUENCIES = {"raw", "daily", "weekly", "monthly", "quarterly"}
SERIES_TRANSFORMS = {"raw", "difference", "pct_change", "mom", "yoy", "rebased", "zscore"}
PUBLIC_SERIES_OHLC_FIELDS = ("open", "high", "low", "close")
CATALOG_MODULE_TO_KEY = {
    "宏观": "macro",
    "全球市场": "global_markets",
    "申万行业": "sw_industries",
    "商品": "commodities",
    "大宗商品": "commodities",
    "个股": "stock",
    "新闻事件": "news_events",
}


class DashboardDataError(RuntimeError):
    """Safe data error whose code may be returned to public clients."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class DashboardQueryError(ValueError):
    """Strict public query error without local implementation details."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class JsonArtifactStore:
    """Small thread-safe cache that invalidates on every filesystem change."""

    def __init__(self, data_dir: Path, max_bytes: int) -> None:
        self.data_dir = data_dir.resolve()
        self.max_bytes = max_bytes
        self._cache: dict[Path, tuple[tuple[int, int, int], dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def load(self, filename: str) -> dict[str, Any]:
        path = (self.data_dir / filename).resolve()
        if path.parent != self.data_dir:
            raise DashboardDataError("invalid_artifact")

        with self._lock:
            try:
                stat = path.stat()
            except FileNotFoundError as exc:
                self._cache.pop(path, None)
                raise DashboardDataError("artifact_missing") from exc
            except OSError as exc:
                self._cache.pop(path, None)
                raise DashboardDataError("artifact_unreadable") from exc

            if not path.is_file():
                self._cache.pop(path, None)
                raise DashboardDataError("artifact_unreadable")
            if stat.st_size > self.max_bytes:
                self._cache.pop(path, None)
                raise DashboardDataError("artifact_too_large")

            signature = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
            cached = self._cache.get(path)
            if cached and cached[0] == signature:
                return cached[1]

            try:
                with path.open("r", encoding="utf-8-sig") as handle:
                    payload = json.load(handle)
            except (UnicodeError, json.JSONDecodeError) as exc:
                self._cache.pop(path, None)
                raise DashboardDataError("artifact_invalid_json") from exc
            except OSError as exc:
                self._cache.pop(path, None)
                raise DashboardDataError("artifact_unreadable") from exc

            if not isinstance(payload, dict):
                self._cache.pop(path, None)
                raise DashboardDataError("artifact_invalid_shape")

            self._cache[path] = (signature, payload)
            return payload


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _status(value: Any, *, default: str = "unavailable") -> str:
    candidate = str(value).lower() if value is not None else ""
    return candidate if candidate in ITEM_STATUSES else default


def _display(value: Any) -> str:
    if value is None or value == "":
        return "暂无数据"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (str, int, float)):
        return str(value)
    return "暂无数据"


def _normalise_kpi(item: Any, index: int) -> dict[str, Any]:
    raw = _mapping(item)
    value = raw.get("value")
    display = raw.get("display")
    change = raw.get("change")
    change_display = raw.get("change_display")
    return {
        "id": _text(raw.get("id")) or f"kpi-{index + 1}",
        "label": _text(raw.get("label")) or "未命名指标",
        "value": value if isinstance(value, (str, int, float)) and not isinstance(value, bool) else None,
        "display": _display(display if display not in (None, "") else value),
        "unit": _text(raw.get("unit")),
        "change": change if isinstance(change, (str, int, float)) and not isinstance(change, bool) else None,
        "change_display": _display(change_display if change_display not in (None, "") else change)
        if change not in (None, "") or change_display not in (None, "")
        else "—",
        "as_of": _text(raw.get("as_of")),
        "source": _text(raw.get("source")),
        "quality": _text(raw.get("quality")),
        "status": _status(raw.get("status")),
    }


def _normalise_point(point: Any) -> dict[str, Any] | None:
    raw = _mapping(point)
    date = raw.get("date", raw.get("x"))
    value = raw.get("value", raw.get("y"))
    if date is None and value is None:
        return None
    result: dict[str, Any] = {
        "date": _text(date),
        "value": value if isinstance(value, (str, int, float)) and not isinstance(value, bool) else None,
    }
    if "label" in raw:
        result["label"] = _text(raw.get("label"))
    ohlc: dict[str, float] = {}
    for field in PUBLIC_SERIES_OHLC_FIELDS:
        raw_value = raw.get(field)
        if isinstance(raw_value, bool):
            break
        try:
            number = float(raw_value)
        except (TypeError, ValueError):
            break
        if not math.isfinite(number):
            break
        ohlc[field] = number
    if len(ohlc) == len(PUBLIC_SERIES_OHLC_FIELDS):
        result.update(ohlc)
    return result


def _normalise_series(item: Any, index: int) -> dict[str, Any]:
    raw = _mapping(item)
    raw_status = (_text(raw.get("status")) or "unavailable").lower()
    status = raw_status if raw_status in SERIES_STATUSES else "unavailable"
    points = []
    for point in (_normalise_point(x) for x in _list(raw.get("data"))):
        if point is None:
            continue
        # Public series points have a closed schema; chart-only fields such as
        # label are intentionally not exposed through modules[*].series.
        points.append({
            field: point[field]
            for field in ("date", "value", *PUBLIC_SERIES_OHLC_FIELDS)
            if field in point
        })
    return {
        "id": _text(raw.get("id")) or f"series-{index + 1}",
        "catalog_id": _text(raw.get("catalog_id")),
        "label": _text(raw.get("label")) or "未命名序列",
        "submodule": _text(raw.get("submodule")),
        "unit": _text(raw.get("unit")),
        "frequency": _text(raw.get("frequency")),
        "status": status,
        "source": _text(raw.get("source")),
        "as_of": _text(raw.get("as_of")),
        "data": points,
    }


def _normalise_chart(item: Any, index: int) -> dict[str, Any]:
    raw = _mapping(item)
    series: list[dict[str, Any]] = []
    for series_index, source_series in enumerate(_list(raw.get("series"))):
        source = _mapping(source_series)
        points = [point for point in (_normalise_point(x) for x in _list(source.get("data"))) if point]
        series.append(
            {
                "name": _text(source.get("name")) or f"序列 {series_index + 1}",
                "data": points,
            }
        )
    return {
        "id": _text(raw.get("id")) or f"chart-{index + 1}",
        "title": _text(raw.get("title")) or "未命名图表",
        "kind": (_text(raw.get("kind")) or "unknown").lower(),
        "unit": _text(raw.get("unit")),
        "series": series,
        "source": _text(raw.get("source")),
        "as_of": _text(raw.get("as_of")),
        "status": _status(raw.get("status")),
    }


def _normalise_table(item: Any, index: int) -> dict[str, Any]:
    raw = _mapping(item)
    columns: list[Any] = []
    for column in _list(raw.get("columns")):
        if isinstance(column, Mapping):
            key = _text(column.get("key"))
            label = _text(column.get("label")) or key
            if key:
                columns.append({"key": key, "label": label})
        elif _text(column):
            columns.append(_text(column))

    rows = [row for row in _list(raw.get("rows")) if isinstance(row, (Mapping, list, tuple))]
    return {
        "id": _text(raw.get("id")) or f"table-{index + 1}",
        "title": _text(raw.get("title")) or "未命名数据表",
        "columns": columns,
        "rows": rows,
        "source": _text(raw.get("source")),
        "as_of": _text(raw.get("as_of")),
        "status": _status(raw.get("status")),
    }


def _normalise_alert(item: Any) -> dict[str, Any] | None:
    if isinstance(item, str):
        return {"level": "info", "message": item}
    raw = _mapping(item)
    message = _text(raw.get("message", raw.get("text")))
    if not message:
        return None
    level = (_text(raw.get("level")) or "info").lower()
    if level not in {"info", "success", "warning", "error"}:
        level = "info"
    return {
        "level": level,
        "message": message,
        "as_of": _text(raw.get("as_of")),
        "source": _text(raw.get("source")),
    }


def _normalise_module(key: str, item: Any) -> dict[str, Any]:
    raw = _mapping(item)
    present = bool(raw)
    alerts = [alert for alert in (_normalise_alert(x) for x in _list(raw.get("alerts"))) if alert]
    if not present:
        alerts = [{"level": "info", "message": "该模块暂无可用数据", "as_of": None, "source": None}]
    return {
        "key": key,
        "title": _text(raw.get("title")) or MODULE_META[key]["title"],
        "subtitle": _text(raw.get("subtitle")) or MODULE_META[key]["subtitle"],
        "status": _status(raw.get("status")) if present else "unavailable",
        "as_of": _text(raw.get("as_of")),
        "series": [_normalise_series(x, i) for i, x in enumerate(_list(raw.get("series")))],
        "kpis": [_normalise_kpi(x, i) for i, x in enumerate(_list(raw.get("kpis")))],
        "charts": [_normalise_chart(x, i) for i, x in enumerate(_list(raw.get("charts")))],
        "tables": [_normalise_table(x, i) for i, x in enumerate(_list(raw.get("tables")))],
        "alerts": alerts,
    }


def normalise_snapshot(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw_modules = _mapping(payload.get("modules"))
    modules = {key: _normalise_module(key, raw_modules.get(key)) for key in MODULE_META}

    raw_status = _text(payload.get("status"))
    if raw_status and raw_status.lower() in TOP_LEVEL_STATUSES:
        overall_status = raw_status.lower()
    else:
        module_statuses = [module["status"] for module in modules.values()]
        available_count = sum(status not in {"unavailable", "failed"} for status in module_statuses)
        overall_status = "failed" if available_count == 0 else "partial" if available_count < len(modules) else "ok"

    return {
        "schema_version": _text(payload.get("schema_version")),
        "generated_at": _text(payload.get("generated_at")),
        "as_of": _text(payload.get("as_of")),
        "status": overall_status,
        "summary": dict(_mapping(payload.get("summary"))),
        "modules": modules,
        "quality": dict(_mapping(payload.get("quality"))),
        "catalog_status": [dict(x) for x in _list(payload.get("catalog_status")) if isinstance(x, Mapping)],
    }


def metadata_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return the dashboard shell without embedded chart point arrays.

    The canonical UI only needs module and table metadata at boot and loads
    time series through /api/v1/series on demand. The full snapshot remains
    the default response for API compatibility.
    """
    compact = dict(snapshot)
    compact_modules: dict[str, Any] = {}
    for key, raw_module in _mapping(snapshot.get("modules")).items():
        module = dict(_mapping(raw_module))
        module["series"] = [
            {
                field: value
                for field, value in _mapping(item).items()
                if field not in {"data", "points"}
            }
            for item in _list(module.get("series"))
        ]
        module.pop("charts", None)
        compact_modules[str(key)] = module
    compact["modules"] = compact_modules
    return compact


def unavailable_snapshot(reason: str) -> dict[str, Any]:
    payload = normalise_snapshot({})
    payload["status"] = "failed"
    payload["summary"] = {
        "message": "数据快照暂不可用",
        "reason": reason,
    }
    return payload


def _canonical_stock_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    return match.group(1) if match else text


def _find_stock(payload: Mapping[str, Any], code: str) -> Any | None:
    target = code.upper()
    target_key = _canonical_stock_code(target)
    stock_module = _mapping(_mapping(payload.get("modules")).get("stock"))
    pools = [payload.get("stocks"), stock_module.get("stocks"), stock_module.get("details")]

    for pool in pools:
        if isinstance(pool, Mapping):
            for candidate, data in pool.items():
                if _canonical_stock_code(candidate) == target_key:
                    return data
        elif isinstance(pool, list):
            for item in pool:
                raw = _mapping(item)
                candidate = raw.get("code", raw.get("ts_code", raw.get("symbol")))
                if candidate is not None and _canonical_stock_code(candidate) == target_key:
                    return item

    for table_item in _list(stock_module.get("tables")):
        table = _mapping(table_item)
        for row_item in _list(table.get("rows")):
            row = _mapping(row_item)
            candidate = row.get("code", row.get("ts_code", row.get("symbol")))
            if candidate is None or _canonical_stock_code(candidate) != target_key:
                continue
            record_name = (_text(row.get("name")) or "").upper()
            matching_kpis = []
            for kpi_item in _list(stock_module.get("kpis")):
                kpi = _mapping(kpi_item)
                haystack = " ".join(str(kpi.get(field, "")) for field in ("id", "label")).upper()
                if target_key in haystack or (record_name and record_name in haystack):
                    matching_kpis.append(dict(kpi))
            matching_charts = []
            for chart_item in _list(stock_module.get("charts")):
                chart = _mapping(chart_item)
                series_matches = []
                for series_item in _list(chart.get("series")):
                    series = _mapping(series_item)
                    name = str(series.get("name", "")).upper()
                    if target_key in name or (record_name and record_name in name):
                        series_matches.append(dict(series))
                if series_matches:
                    selected_chart = dict(chart)
                    selected_chart["series"] = series_matches
                    matching_charts.append(selected_chart)
            return {
                "status": _status(table.get("status")),
                "record": dict(row),
                "kpis": matching_kpis,
                "charts": matching_charts,
            }

    module_code = stock_module.get("code", stock_module.get("ts_code", stock_module.get("symbol")))
    if module_code is not None and _canonical_stock_code(module_code) == target_key:
        return stock_module
    return None


def _query_date(value: str | None, field: str) -> date | None:
    if value in (None, ""):
        return None
    if not ISO_DATE_RE.fullmatch(value):
        raise DashboardQueryError(f"invalid_{field}", f"{field}必须为YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DashboardQueryError(f"invalid_{field}", f"{field}不是有效日期") from exc


def _point_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _numeric_point_records(
    series: Mapping[str, Any], start: date | None, end: date | None
) -> list[tuple[date, dict[str, Any]]]:
    values: dict[date, dict[str, Any]] = {}
    for point in _list(series.get("data")):
        raw = _mapping(point)
        observed = _point_date(raw.get("date", raw.get("x")))
        value = raw.get("value", raw.get("y"))
        if observed is None or isinstance(value, bool):
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(numeric):
            continue
        if start is not None and observed < start:
            continue
        if end is not None and observed > end:
            continue
        record: dict[str, Any] = {"date": observed.isoformat(), "value": numeric}
        ohlc: dict[str, float] = {}
        for field in PUBLIC_SERIES_OHLC_FIELDS:
            raw_value = raw.get(field)
            if isinstance(raw_value, bool):
                break
            try:
                candle_value = float(raw_value)
            except (TypeError, ValueError):
                break
            if not math.isfinite(candle_value):
                break
            ohlc[field] = candle_value
        if len(ohlc) == len(PUBLIC_SERIES_OHLC_FIELDS):
            record.update(ohlc)
        values[observed] = record
    return sorted(values.items())


def _numeric_points(series: Mapping[str, Any], start: date | None, end: date | None) -> list[tuple[date, float]]:
    return [
        (observed, float(record["value"]))
        for observed, record in _numeric_point_records(series, start, end)
    ]


def _resample_points(points: Sequence[tuple[date, float]], frequency: str) -> list[tuple[date, float]]:
    if frequency in {"raw", "daily"}:
        return list(points)
    buckets: dict[tuple[int, ...], tuple[date, float]] = {}
    for observed, value in points:
        if frequency == "weekly":
            iso = observed.isocalendar()
            key = (iso.year, iso.week)
        elif frequency == "monthly":
            key = (observed.year, observed.month)
        else:
            key = (observed.year, (observed.month - 1) // 3 + 1)
        current = buckets.get(key)
        if current is None or observed >= current[0]:
            buckets[key] = (observed, value)
    return sorted(buckets.values())


def _previous_year_value(
    points: Sequence[tuple[date, float]], index: int, frequency: str
) -> float | None:
    observed = points[index][0]
    if frequency == "monthly":
        for candidate_date, value in reversed(points[:index]):
            if (candidate_date.year, candidate_date.month) == (observed.year - 1, observed.month):
                return value
        return None
    if frequency == "quarterly":
        quarter = (observed.month - 1) // 3 + 1
        for candidate_date, value in reversed(points[:index]):
            candidate_quarter = (candidate_date.month - 1) // 3 + 1
            if (candidate_date.year, candidate_quarter) == (observed.year - 1, quarter):
                return value
        return None
    try:
        target = observed.replace(year=observed.year - 1)
    except ValueError:
        target = observed.replace(year=observed.year - 1, day=28)
    candidates = [(candidate_date, value) for candidate_date, value in points[:index] if timedelta(0) <= target - candidate_date <= timedelta(days=7)]
    return candidates[-1][1] if candidates else None


def _transform_points(
    points: Sequence[tuple[date, float]], transform: str, frequency: str
) -> list[tuple[date, float]]:
    if transform == "raw":
        return list(points)
    if transform == "rebased":
        base = next((value for _, value in points if value != 0), None)
        return [] if base is None else [(observed, value / base * 100.0) for observed, value in points]
    if transform == "zscore":
        values = [value for _, value in points]
        if len(values) < 2:
            return []
        deviation = pstdev(values)
        if deviation == 0:
            return []
        mean = fmean(values)
        return [(observed, (value - mean) / deviation) for observed, value in points]

    output: list[tuple[date, float]] = []
    for index, (observed, value) in enumerate(points):
        if transform == "yoy":
            previous = _previous_year_value(points, index, frequency)
        else:
            previous = points[index - 1][1] if index else None
        if previous is None:
            continue
        if transform == "difference":
            output.append((observed, value - previous))
        elif previous != 0:
            output.append((observed, (value / previous - 1.0) * 100.0))
    return output


def _relative_points(
    points: Sequence[tuple[date, float]], benchmark: Sequence[tuple[date, float]]
) -> list[tuple[date, float]]:
    benchmark_by_date = dict(benchmark)
    aligned = [(observed, value, benchmark_by_date[observed]) for observed, value in points if observed in benchmark_by_date]
    base = next(((value, benchmark_value) for _, value, benchmark_value in aligned if value != 0 and benchmark_value != 0), None)
    if base is None:
        return []
    base_value, base_benchmark = base
    return [
        (observed, ((value / base_value) / (benchmark_value / base_benchmark) - 1.0) * 100.0)
        for observed, value, benchmark_value in aligned
        if benchmark_value != 0
    ]


def _series_index(snapshot: Mapping[str, Any]) -> tuple[dict[str, tuple[str, Mapping[str, Any]]], dict[str, list[Mapping[str, Any]]]]:
    lookup: dict[str, tuple[str, Mapping[str, Any]]] = {}
    by_module: dict[str, list[Mapping[str, Any]]] = {key: [] for key in MODULE_META}
    for module_key, module in _mapping(snapshot.get("modules")).items():
        if module_key not in MODULE_META:
            continue
        for series in _list(_mapping(module).get("series")):
            item = _mapping(series)
            identifier = _text(item.get("id"))
            if not identifier:
                continue
            by_module[module_key].append(item)
            lookup.setdefault(identifier, (module_key, item))
            catalog_id = _text(item.get("catalog_id"))
            if catalog_id:
                lookup.setdefault(catalog_id, (module_key, item))
    return lookup, by_module


def _coverage_payload(snapshot: Mapping[str, Any], catalog: Mapping[str, Any]) -> dict[str, Any]:
    catalog_rows = _list(catalog.get("rows"))
    runtime: dict[str, str] = {}
    for row in _list(snapshot.get("catalog_status")):
        item = _mapping(row)
        identifier = _text(item.get("id"))
        status = (_text(item.get("status")) or "").lower()
        if identifier and status in {"live", "stale", "unavailable", "metadata_only"}:
            runtime[identifier] = status
    for module in _mapping(snapshot.get("modules")).values():
        for series in _list(_mapping(module).get("series")):
            item = _mapping(series)
            catalog_id = _text(item.get("catalog_id"))
            status = (_text(item.get("status")) or "").lower()
            if catalog_id and status in SERIES_STATUSES:
                runtime[catalog_id] = status

    keys = ("live", "stale", "unavailable", "metadata_only")
    totals = {key: 0 for key in keys}
    modules = {key: {"total": 0, **{status: 0 for status in keys}} for key in MODULE_META}
    for row in catalog_rows:
        item = _mapping(row)
        status = runtime.get(_text(item.get("id")) or "", "metadata_only")
        totals[status] += 1
        module_value = _text(item.get("module")) or ""
        module_key = module_value if module_value in MODULE_META else CATALOG_MODULE_TO_KEY.get(module_value)
        if module_key:
            modules[module_key]["total"] += 1
            modules[module_key][status] += 1

    def finish(counts: dict[str, int]) -> dict[str, Any]:
        total = int(counts.get("total", sum(counts.get(key, 0) for key in keys)))
        result = {"total": total, **{key: int(counts.get(key, 0)) for key in keys}}
        result["observable"] = result["live"] + result["stale"]
        result["live_ratio"] = round(result["live"] / total, 6) if total else 0.0
        result["observable_ratio"] = round(result["observable"] / total, 6) if total else 0.0
        return result

    total_counts = {"total": len(catalog_rows), **totals}
    return {
        "status": "ok",
        "generated_at": _text(snapshot.get("generated_at")),
        "catalog_rows": len(catalog_rows),
        "totals": finish(total_counts),
        "modules": {key: finish(value) for key, value in modules.items()},
    }
def create_app(test_config: Mapping[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    project_dir = Path(__file__).resolve().parent
    app.config.from_mapping(
        DATA_DIR=str(project_dir / "data"),
        MAX_CONTENT_LENGTH=64 * 1024,
        MAX_JSON_BYTES=32 * 1024 * 1024,
        EXPECTED_CATALOG_ROWS=367,
        MAX_SNAPSHOT_AGE_SECONDS=30 * 60 * 60,
        MAX_QUERY_STRING_BYTES=4096,
        MAX_SERIES_IDS=24,
        MAX_SERIES_RANGE_DAYS=20000,
        MAX_SERIES_POINTS_PER_SERIES=5000,
        MIN_TOTAL_LIVE_CATALOG=40,
        CORE_MIN_LIVE_BY_MODULE={
            "macro": 4,
            "global_markets": 4,
            "sw_industries": 3,
            "commodities": 3,
            "stock": 3,
            "news_events": 1,
        },
        DAILY_AS_OF_MAX_AGE_DAYS={
            "global_markets": 4,
            "sw_industries": 4,
            "commodities": 4,
            "stock": 4,
            "news_events": 2,
        },
        HEALTH_TIMEZONE="Asia/Shanghai",
        JSON_AS_ASCII=False,
    )
    if test_config:
        app.config.update(test_config)
    app.json.ensure_ascii = False
    app.json.sort_keys = False

    store = JsonArtifactStore(Path(app.config["DATA_DIR"]), int(app.config["MAX_JSON_BYTES"]))
    app.extensions["dashboard_store"] = store
    normalised_cache_lock = threading.RLock()
    normalised_cache: dict[str, Any] = {"raw": None, "snapshot": None, "metadata": None}

    @app.before_request
    def enforce_request_size() -> None:
        if len(request.query_string) > int(app.config["MAX_QUERY_STRING_BYTES"]):
            abort(414)
        length = request.content_length
        if length is not None and length > int(app.config["MAX_CONTENT_LENGTH"]):
            abort(413)

    @app.after_request
    def add_security_headers(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://cdn.plot.ly; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; font-src 'self'; object-src 'none'; "
            "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        if request.path.startswith("/api/") or request.path in {"/healthz", "/livez"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    def load_snapshot() -> tuple[dict[str, Any], dict[str, Any]]:
        raw = store.load("dashboard_snapshot.json")
        with normalised_cache_lock:
            if normalised_cache["raw"] is raw and isinstance(normalised_cache["snapshot"], dict):
                return raw, normalised_cache["snapshot"]
            snapshot = normalise_snapshot(raw)
            normalised_cache.update({
                "raw": raw,
                "snapshot": snapshot,
                "metadata": metadata_snapshot(snapshot),
            })
            return raw, snapshot

    @app.get("/")
    def index():
        try:
            _, snapshot = load_snapshot()
            data_error = None
        except DashboardDataError as exc:
            snapshot = unavailable_snapshot(exc.code)
            data_error = exc.code
        return render_template(
            "index.html",
            snapshot=snapshot,
            module_meta=MODULE_META,
            data_error=data_error,
        )

    def liveness() -> tuple[dict[str, Any], int]:
        try:
            _, snapshot = load_snapshot()
            catalog = store.load("indicator_catalog.json")
        except DashboardDataError as exc:
            return {"status": "failed", "data_state": exc.code}, 503

        rows = catalog.get("rows")
        return {
            "status": "ok",
            "files": {"snapshot": "readable", "catalog": "readable"},
            "generated_at": snapshot["generated_at"],
            "catalog_rows": len(rows) if isinstance(rows, list) else None,
        }, 200

    def strict_health() -> tuple[dict[str, Any], int]:
        try:
            _, snapshot = load_snapshot()
            catalog = store.load("indicator_catalog.json")
        except DashboardDataError as exc:
            return {"status": "failed", "data_state": exc.code, "failures": [exc.code]}, 503

        rows = catalog.get("rows")
        if not isinstance(rows, list):
            return {"status": "failed", "data_state": "catalog_invalid_shape", "failures": ["catalog_invalid_shape"]}, 503
        expected_rows = int(app.config["EXPECTED_CATALOG_ROWS"])
        failures: list[str] = []
        payload = {
            "status": "ok",
            "snapshot_status": snapshot["status"],
            "generated_at": snapshot["generated_at"],
            "catalog_rows": len(rows),
            "expected_catalog_rows": expected_rows,
            "module_statuses": {key: module["status"] for key, module in snapshot["modules"].items()},
        }
        if len(rows) != expected_rows:
            failures.append("catalog_row_count_mismatch")
        if snapshot["status"] not in {"ok", "partial"}:
            failures.append(f"snapshot_{snapshot['status']}")
        bad_modules = sorted(
            key for key, module in snapshot["modules"].items() if module["status"] in {"stale", "failed"}
        )
        if bad_modules:
            failures.append("module_stale_or_failed")
            payload["bad_modules"] = bad_modules
        try:
            generated_at = datetime.fromisoformat(str(snapshot["generated_at"]).replace("Z", "+00:00"))
            if generated_at.tzinfo is None:
                raise ValueError("timezone is required")
        except (TypeError, ValueError):
            generated_at = None
            failures.append("generated_at_invalid")
        if generated_at is not None:
            age_seconds = (datetime.now(timezone.utc) - generated_at.astimezone(timezone.utc)).total_seconds()
            payload["snapshot_age_seconds"] = round(age_seconds, 3)
            if age_seconds < -300:
                failures.append("generated_at_future")
            elif age_seconds > int(app.config["MAX_SNAPSHOT_AGE_SECONDS"]):
                failures.append("snapshot_expired")

        coverage = _coverage_payload(snapshot, catalog)
        payload["coverage"] = coverage["totals"]
        if coverage["totals"]["live"] < int(app.config["MIN_TOTAL_LIVE_CATALOG"]):
            failures.append("core_coverage_below_minimum")
        minimums = _mapping(app.config.get("CORE_MIN_LIVE_BY_MODULE"))
        deficient_modules = {
            key: {"live": coverage["modules"][key]["live"], "required": int(required)}
            for key, required in minimums.items()
            if key in coverage["modules"] and coverage["modules"][key]["live"] < int(required)
        }
        if deficient_modules:
            failures.append("module_core_coverage_below_minimum")
            payload["coverage_deficient_modules"] = deficient_modules

        try:
            today = datetime.now(ZoneInfo(str(app.config["HEALTH_TIMEZONE"]))).date()
        except Exception:
            today = datetime.now(timezone.utc).date()
        daily_as_of: dict[str, Any] = {}
        for key, max_age in _mapping(app.config.get("DAILY_AS_OF_MAX_AGE_DAYS")).items():
            if key not in snapshot["modules"]:
                continue
            raw_as_of = snapshot["modules"][key].get("as_of")
            observed = _point_date(raw_as_of)
            if observed is None:
                daily_as_of[key] = {"as_of": raw_as_of, "state": "missing"}
                failures.append(f"daily_as_of_missing:{key}")
                continue
            age_days = (today - observed).days
            state = "ok" if -1 <= age_days <= int(max_age) else "future" if age_days < -1 else "stale"
            daily_as_of[key] = {"as_of": observed.isoformat(), "age_days": age_days, "max_age_days": int(max_age), "state": state}
            if state != "ok":
                failures.append(f"daily_as_of_{state}:{key}")
        payload["daily_as_of"] = daily_as_of

        if failures:
            payload.update({"status": "failed", "data_state": failures[0], "failures": failures})
            return payload, 503
        payload["failures"] = []
        return payload, 200

    @app.get("/livez")
    def livez():
        payload, status_code = liveness()
        return jsonify(payload), status_code

    @app.get("/healthz")
    def healthz():
        payload, status_code = strict_health()
        return jsonify(payload), status_code

    @app.get("/api/v1/snapshot")
    def snapshot_api():
        try:
            _, snapshot = load_snapshot()
        except DashboardDataError as exc:
            return jsonify({"status": "failed", "data_state": exc.code, "message": "数据快照暂不可用"}), 503
        if request.args.get("view") == "metadata":
            with normalised_cache_lock:
                metadata = normalised_cache.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = metadata_snapshot(snapshot)
                    normalised_cache["metadata"] = metadata
            return jsonify(metadata)
        return jsonify(snapshot)

    @app.get("/api/v1/catalog")
    def catalog_api():
        try:
            catalog = store.load("indicator_catalog.json")
        except DashboardDataError as exc:
            return jsonify({"status": "failed", "data_state": exc.code, "message": "指标目录暂不可用"}), 503
        if not isinstance(catalog.get("rows"), list):
            return jsonify({"status": "failed", "data_state": "catalog_invalid_shape", "message": "指标目录暂不可用"}), 503
        return jsonify(catalog)

    @app.get("/api/v1/coverage")
    def coverage_api():
        try:
            _, snapshot = load_snapshot()
            catalog = store.load("indicator_catalog.json")
        except DashboardDataError as exc:
            return jsonify({"status": "failed", "data_state": exc.code, "message": "覆盖率暂不可用"}), 503
        rows = catalog.get("rows")
        if not isinstance(rows, list):
            return jsonify({"status": "failed", "data_state": "catalog_invalid_shape", "message": "覆盖率暂不可用"}), 503
        if len(rows) != int(app.config["EXPECTED_CATALOG_ROWS"]):
            return jsonify({
                "status": "failed",
                "data_state": "catalog_row_count_mismatch",
                "catalog_rows": len(rows),
                "expected_catalog_rows": int(app.config["EXPECTED_CATALOG_ROWS"]),
            }), 503
        return jsonify(_coverage_payload(snapshot, catalog))

    @app.get("/api/v1/series")
    def series_api():
        allowed_parameters = {"ids", "module", "start", "end", "frequency", "transform", "benchmark"}
        unknown = sorted(set(request.args) - allowed_parameters)
        if unknown:
            return jsonify({"status": "invalid_request", "data_state": "unknown_parameter", "parameters": unknown}), 400
        for field in allowed_parameters - {"ids"}:
            if len(request.args.getlist(field)) > 1:
                return jsonify({"status": "invalid_request", "data_state": "duplicate_parameter", "parameter": field}), 400
        try:
            module_key = (request.args.get("module") or "").strip()
            if module_key and module_key not in MODULE_META:
                raise DashboardQueryError("invalid_module", "module不是有效模块")
            start = _query_date(request.args.get("start"), "start")
            end = _query_date(request.args.get("end"), "end")
            if start is not None and end is not None:
                if start > end:
                    raise DashboardQueryError("invalid_date_range", "start不得晚于end")
                if (end - start).days > int(app.config["MAX_SERIES_RANGE_DAYS"]):
                    raise DashboardQueryError("date_range_too_large", "日期跨度超过上限")
            frequency = (request.args.get("frequency") or "raw").strip().lower()
            if frequency not in SERIES_FREQUENCIES:
                raise DashboardQueryError("invalid_frequency", "frequency参数无效")
            transform = (request.args.get("transform") or "raw").strip().lower()
            if transform not in SERIES_TRANSFORMS:
                raise DashboardQueryError("invalid_transform", "transform参数无效")
            benchmark_id = (request.args.get("benchmark") or "").strip()
            if benchmark_id and not SERIES_ID_RE.fullmatch(benchmark_id):
                raise DashboardQueryError("invalid_benchmark", "benchmark格式无效")
            if benchmark_id and transform not in {"raw", "rebased"}:
                raise DashboardQueryError("benchmark_transform_conflict", "benchmark仅支持raw或rebased")

            requested_ids: list[str] = []
            for group in request.args.getlist("ids"):
                for identifier in group.split(","):
                    identifier = identifier.strip()
                    if not identifier:
                        continue
                    if not SERIES_ID_RE.fullmatch(identifier):
                        raise DashboardQueryError("invalid_series_id", "ids包含无效序列ID")
                    if identifier not in requested_ids:
                        requested_ids.append(identifier)
            if len(requested_ids) > int(app.config["MAX_SERIES_IDS"]):
                raise DashboardQueryError("too_many_series", "请求序列数超过上限")
            if not requested_ids and not module_key:
                raise DashboardQueryError("series_selector_required", "必须提供ids或module")
        except DashboardQueryError as exc:
            return jsonify({"status": "invalid_request", "data_state": exc.code, "message": exc.message}), 400

        try:
            _, snapshot = load_snapshot()
        except DashboardDataError as exc:
            return jsonify({"status": "failed", "data_state": exc.code, "message": "序列暂不可用"}), 503
        lookup, by_module = _series_index(snapshot)
        if not requested_ids:
            requested_ids = [str(item.get("id")) for item in by_module[module_key] if item.get("id")]
            if len(requested_ids) > int(app.config["MAX_SERIES_IDS"]):
                return jsonify({
                    "status": "invalid_request",
                    "data_state": "too_many_series",
                    "available_count": len(requested_ids),
                    "max_series": int(app.config["MAX_SERIES_IDS"]),
                    "message": "该模块序列过多，请使用ids明确选择",
                }), 400

        selected: list[tuple[str, Mapping[str, Any]]] = []
        missing_ids: list[str] = []
        for requested_id in requested_ids:
            found = lookup.get(requested_id)
            if found is None or (module_key and found[0] != module_key):
                missing_ids.append(requested_id)
                continue
            selected.append(found)

        benchmark_points: list[tuple[date, float]] | None = None
        benchmark_actual_id: str | None = None
        if benchmark_id:
            benchmark_found = lookup.get(benchmark_id)
            if benchmark_found is None:
                return jsonify({"status": "invalid_request", "data_state": "benchmark_not_found", "message": "基准序列不存在"}), 400
            benchmark_item = benchmark_found[1]
            benchmark_actual_id = _text(benchmark_item.get("id"))
            benchmark_points = _resample_points(_numeric_points(benchmark_item, start, end), frequency)
            if not benchmark_points:
                return jsonify({"status": "unavailable", "data_state": "benchmark_has_no_points", "series": []}), 200

        result_series: list[dict[str, Any]] = []
        for selected_module, item in selected:
            point_records = _numeric_point_records(item, start, end)
            points = _resample_points(
                [(observed, float(record["value"])) for observed, record in point_records],
                frequency,
            )
            preserve_ohlc = (
                benchmark_points is None
                and transform == "raw"
                and frequency in {"raw", "daily"}
            )
            if benchmark_points is not None:
                points = _relative_points(points, benchmark_points)
                output_transform = "relative_to_benchmark"
                output_unit = "%"
            else:
                points = _transform_points(points, transform, frequency)
                output_transform = transform
                output_unit = "%" if transform in {"pct_change", "mom", "yoy"} else "指数" if transform == "rebased" else "标准差" if transform == "zscore" else _text(item.get("unit"))
            if len(points) > int(app.config["MAX_SERIES_POINTS_PER_SERIES"]):
                return jsonify({
                    "status": "invalid_request",
                    "data_state": "too_many_points",
                    "series_id": _text(item.get("id")),
                    "message": "返回点数超过上限，请缩短日期范围或降低频率",
                }), 400
            item_status = (_text(item.get("status")) or "unavailable").lower()
            if not points:
                item_status = "unavailable"
            if preserve_ohlc:
                output_data = [record for _, record in point_records]
            else:
                output_data = [{"date": observed.isoformat(), "value": value} for observed, value in points]
            ohlc_included = any(
                all(field in point for field in PUBLIC_SERIES_OHLC_FIELDS)
                for point in output_data
            )
            result_series.append({
                "id": _text(item.get("id")),
                "catalog_id": _text(item.get("catalog_id")),
                "module": selected_module,
                "label": _text(item.get("label")),
                "submodule": _text(item.get("submodule")),
                "unit": output_unit,
                "source_frequency": _text(item.get("frequency")),
                "frequency": frequency,
                "transform": output_transform,
                "status": item_status if item_status in SERIES_STATUSES else "unavailable",
                "source": _text(item.get("source")),
                "as_of": _text(item.get("as_of")),
                "point_count": len(points),
                "point_schema": "date_value_ohlc" if ohlc_included else "date_value",
                "ohlc_preserved": ohlc_included,
                "data": output_data,
            })

        has_points = any(item["point_count"] for item in result_series)
        response_status = "unavailable" if not has_points else "partial" if missing_ids or any(item["status"] != "live" for item in result_series) else "ok"
        return jsonify({
            "status": response_status,
            "query": {
                "ids": requested_ids,
                "module": module_key or None,
                "start": start.isoformat() if start else None,
                "end": end.isoformat() if end else None,
                "frequency": frequency,
                "transform": transform,
                "benchmark": benchmark_actual_id,
            },
            "count": len(result_series),
            "missing_ids": missing_ids,
            "series": result_series,
        })

    @app.get("/api/v1/stock/<code>")
    def stock_api(code: str):
        if not STOCK_CODE_RE.fullmatch(code) or ".." in code:
            return jsonify({"status": "invalid_request", "message": "证券代码格式无效"}), 400
        canonical_code = code.upper()
        try:
            raw, snapshot = load_snapshot()
        except DashboardDataError as exc:
            return jsonify({"status": "failed", "data_state": exc.code, "code": canonical_code}), 503

        stock = _find_stock(raw, canonical_code)
        if stock is None:
            return (
                jsonify(
                    {
                        "status": "unavailable",
                        "code": canonical_code,
                        "as_of": snapshot["as_of"],
                        "message": "该证券暂无可用数据",
                    }
                ),
                404,
            )
        return jsonify({"status": _status(_mapping(stock).get("status")), "code": canonical_code, "as_of": snapshot["as_of"], "data": stock})

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify({"status": "not_found", "message": "接口不存在"}), 404
        return "页面不存在", 404, {"Content-Type": "text/plain; charset=utf-8"}

    @app.errorhandler(405)
    def method_not_allowed(_error):
        return jsonify({"status": "method_not_allowed", "message": "请求方法不受支持"}), 405

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify({"status": "request_too_large", "message": "请求体超过限制"}), 413

    @app.errorhandler(414)
    def query_string_too_large(_error):
        return jsonify({"status": "invalid_request", "data_state": "query_string_too_large", "message": "查询参数超过限制"}), 414

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host=os.environ.get("DASHBOARD_BIND", "127.0.0.1"), port=int(os.environ.get("DASHBOARD_PORT", "8000")))
