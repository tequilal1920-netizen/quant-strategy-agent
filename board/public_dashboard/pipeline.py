from __future__ import annotations

"""Daily, cache-first data pipeline for the public market dashboard.

The module has no network side effect on import.  A refresh is explicit, writes
JSON atomically, never manufactures observations, and keeps the last valid
module as a clearly labelled stale fallback when an upstream source fails.
"""

import argparse
import copy
import hashlib
import json
import math
import os
import re
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

import pandas as pd


SCHEMA_VERSION = "1.0"
CATALOG_ROWS_REQUIRED = 367
DEFAULT_TTL_SECONDS = 20 * 60 * 60
MODULE_KEYS = (
    "macro",
    "global_markets",
    "sw_industries",
    "commodities",
    "stock",
    "news_events",
)
MODULE_TITLES = {
    "macro": "中国宏观",
    "global_markets": "全球市场",
    "sw_industries": "申万一级行业",
    "commodities": "大宗商品",
    "stock": "个股行情",
    "news_events": "新闻与事件",
}
VALID_STATUSES = {"ok", "partial", "stale", "failed"}
PUBLIC_SERIES_POINT_FIELDS = {"date", "value"}
PUBLIC_SERIES_OHLC_FIELDS = ("open", "high", "low", "close")

CATALOG_MODULE_MAP = {
    "宏观": "macro",
    "全球市场": "global_markets",
    "申万行业": "sw_industries",
    "商品": "commodities",
    "个股": "stock",
    "新闻事件": "news_events",
}


class PipelineError(RuntimeError):
    pass


class SourceUnavailable(PipelineError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_bytes(payload: Any) -> bytes:
    text = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
        separators=(",", ": "),
    )
    return (text + "\n").encode("utf-8")


def atomic_write_json(path: Path | str, payload: Any) -> None:
    """Durably replace a JSON file; partial files are never visible."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = _json_bytes(payload)  # validates NaN/Infinity before touching disk
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{target.name}.", suffix=".tmp",
            dir=target.parent, delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
        temp_name = None
    finally:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)


def load_json(path: Path | str) -> dict[str, Any] | None:
    target = Path(path)
    if not target.exists():
        return None
    with target.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise PipelineError(f"JSON root must be an object: {target.name}")
    return value


@contextmanager
def update_lock(path: Path, stale_after_seconds: int = 3 * 60 * 60):
    """Small cross-platform lock preventing overlapping scheduled refreshes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and time.time() - path.stat().st_mtime > stale_after_seconds:
        path.unlink(missing_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise PipelineError("another dashboard refresh is already running") from exc
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        descriptor = -1
        yield
    finally:
        if "descriptor" in locals() and descriptor >= 0:
            os.close(descriptor)
        path.unlink(missing_ok=True)


def _flag(name: str, environ: Mapping[str, str]) -> bool:
    return environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def premium_source_state(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return booleans only; secret values are never copied to snapshots/logs."""

    env = os.environ if environ is None else environ
    definitions = {
        "tushare": ("DASHBOARD_ENABLE_TUSHARE", "TUSHARE_TOKEN"),
        "ifind": ("DASHBOARD_ENABLE_IFIND", "IFIND_ACCESS_TOKEN"),
        "wind": ("DASHBOARD_ENABLE_WIND", "WIND_SQL_SERVER"),
    }
    return {
        name: {
            "enabled": _flag(enable_key, env),
            "configured": bool(env.get(config_key, "").strip()),
            "called": False,
            "policy": "not_called_by_daily_free_pipeline",
        }
        for name, (enable_key, config_key) in definitions.items()
    }


class AkshareClient:
    def __init__(self) -> None:
        try:
            import akshare as ak
        except ImportError as exc:
            raise SourceUnavailable("AKShare is not installed") from exc
        self._ak = ak
        self._cache: dict[tuple[str, str], pd.DataFrame] = {}

    def call(self, endpoint: str, **kwargs: Any) -> pd.DataFrame:
        function = getattr(self._ak, endpoint, None)
        if function is None:
            raise SourceUnavailable(f"AKShare endpoint is unavailable: {endpoint}")
        cache_key = (endpoint, json.dumps(kwargs, ensure_ascii=False, sort_keys=True, default=str))
        if cache_key in self._cache:
            return self._cache[cache_key].copy()
        result = function(**kwargs)
        if not isinstance(result, pd.DataFrame):
            raise SourceUnavailable(f"AKShare endpoint did not return a DataFrame: {endpoint}")
        self._cache[cache_key] = result.copy()
        return result.copy()


class OfflineClient:
    def call(self, endpoint: str, **kwargs: Any) -> pd.DataFrame:
        del kwargs
        raise SourceUnavailable(f"offline mode blocks source: {endpoint}")


def _safe_error(endpoint: str, exc: Exception) -> dict[str, str]:
    # Do not serialize exception messages: third-party libraries sometimes echo
    # request URLs or headers.  Type and endpoint are sufficient for operations.
    return {
        "endpoint": endpoint,
        "error_type": type(exc).__name__,
        "message": "source call failed; existing data was not replaced by placeholders",
    }


def _as_frame(value: Any) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        return value.copy()
    return pd.DataFrame()


def _normalized_name(value: Any) -> str:
    return re.sub(r"[\s_\-()/（）]", "", str(value)).casefold()


def _find_column(frame: pd.DataFrame, candidates: Sequence[str]) -> Any | None:
    if frame.empty:
        return None
    exact = {_normalized_name(column): column for column in frame.columns}
    for candidate in candidates:
        key = _normalized_name(candidate)
        if key in exact:
            return exact[key]
    for candidate in candidates:
        key = _normalized_name(candidate)
        if len(key) < 3:
            continue
        for normalized, column in exact.items():
            if key in normalized or normalized in key:
                return column
    return None


def _parse_date(value: Any) -> date | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    # Parse an explicit day before the looser year-month patterns below.  The
    # old order matched ``2026-07-10`` as July 2026 and silently rewrote every
    # daily observation to the first of the month.
    full_day = re.fullmatch(
        r"\s*((?:19|20)\d{2})\D?([01]?\d)\D?([0-3]?\d)(?:[T\s].*)?\s*",
        text,
    )
    if full_day:
        try:
            return date(*(int(full_day.group(index)) for index in (1, 2, 3)))
        except ValueError:
            return None
    quarter = re.search(r"(20\d{2}).*?([1-4])\s*(?:季|Q)", text, re.I)
    if not quarter:
        quarter = re.search(r"(20\d{2})\s*Q\s*([1-4])", text, re.I)
    if quarter:
        year, number = int(quarter.group(1)), int(quarter.group(2))
        return date(year, number * 3, 1)
    month = re.search(r"(20\d{2})\D+(\d{1,2})(?:\D|$)", text)
    if month:
        try:
            return date(int(month.group(1)), int(month.group(2)), 1)
        except ValueError:
            pass
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _to_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().replace(",", "").replace("%", "")
        if text in {"", "-", "--", "None", "nan", "NaN"}:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    return number if math.isfinite(number) else None


def _points(
    frame: pd.DataFrame,
    date_candidates: Sequence[str],
    value_candidates: Sequence[str],
    today: date,
    limit: int = 380,
) -> list[dict[str, Any]]:
    frame = _as_frame(frame)
    date_col = _find_column(frame, date_candidates)
    value_col = _find_column(frame, value_candidates)
    if date_col is None and not isinstance(frame.index, pd.RangeIndex):
        frame = frame.reset_index()
        date_col = _find_column(frame, ["index", *(date_candidates or [])])
    if date_col is None or value_col is None:
        return []
    observations: dict[str, float] = {}
    for raw_date, raw_value in zip(frame[date_col], frame[value_col]):
        parsed_date = _parse_date(raw_date)
        value = _to_number(raw_value)
        if parsed_date is None or value is None or parsed_date > today + timedelta(days=1):
            continue
        observations[parsed_date.isoformat()] = value
    return [
        {"date": day, "value": observations[day]}
        for day in sorted(observations)[-limit:]
    ]


def _display(value: Any, unit: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        return value
    number = _to_number(value)
    if number is None:
        return "—"
    if abs(number) >= 100_000_000:
        rendered = f"{number / 100_000_000:.2f}亿"
    elif abs(number) >= 10_000:
        rendered = f"{number / 10_000:.2f}万"
    elif abs(number) >= 100:
        rendered = f"{number:,.2f}"
    else:
        rendered = f"{number:.2f}"
    return f"{rendered}{unit}" if unit else rendered


def _change_display(value: float | None, unit: str = "%") -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}{unit}"


def _pct_change(points: Sequence[Mapping[str, Any]], periods: int = 1) -> float | None:
    if len(points) <= periods:
        return None
    current = _to_number(points[-1].get("value"))
    prior = _to_number(points[-1 - periods].get("value"))
    if current is None or prior in {None, 0.0}:
        return None
    return (current / prior - 1.0) * 100.0


def _volatility(points: Sequence[Mapping[str, Any]], periods: int = 20) -> float | None:
    values = pd.Series([_to_number(item.get("value")) for item in points], dtype="float64").dropna()
    if len(values) < periods + 1 or (values <= 0).any():
        return None
    returns = values.pct_change().dropna().tail(periods)
    if len(returns) < periods:
        return None
    value = float(returns.std(ddof=1) * math.sqrt(252) * 100.0)
    return value if math.isfinite(value) else None


def _max_drawdown(points: Sequence[Mapping[str, Any]], periods: int = 60) -> float | None:
    values = pd.Series([_to_number(item.get("value")) for item in points], dtype="float64").dropna().tail(periods)
    if len(values) < 2:
        return None
    peaks = values.cummax()
    drawdown = values / peaks - 1.0
    result = float(drawdown.min() * 100.0)
    return result if math.isfinite(result) else None


def _rebased(points: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    first = next((_to_number(point.get("value")) for point in points if _to_number(point.get("value")) not in {None, 0.0}), None)
    if first is None:
        return []
    return [
        {"date": point["date"], "value": round(float(point["value"]) / first * 100.0, 6)}
        for point in points
        if _to_number(point.get("value")) is not None
    ]


def _kpi(
    identifier: str,
    label: str,
    value: Any,
    unit: str,
    change: float | None,
    as_of: str | None,
    source: str,
    catalog_id: str | None = None,
    quality: str = "valid",
    status: str = "ok",
) -> dict[str, Any]:
    item = {
        "id": identifier,
        "label": label,
        "value": value,
        "display": _display(value, unit),
        "unit": unit,
        "change": change,
        "change_display": _change_display(change),
        "as_of": as_of,
        "source": source,
        "quality": quality,
        "status": status,
    }
    if catalog_id:
        item["catalog_id"] = catalog_id
    return item


def _module(key: str) -> dict[str, Any]:
    return {
        "title": MODULE_TITLES[key],
        "status": "failed",
        "kpis": [],
        "series": [],
        "charts": [],
        "tables": [],
        "alerts": [],
        "_errors": [],
        "_attempts": 0,
        "_successes": 0,
        "_live_catalog_ids": set(),
    }


def _attempt(module: MutableMapping[str, Any], client: Any, endpoint: str, **kwargs: Any) -> pd.DataFrame:
    module["_attempts"] += 1
    try:
        frame = client.call(endpoint, **kwargs)
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise SourceUnavailable("empty DataFrame")
        module["_successes"] += 1
        return frame.copy()
    except Exception as exc:  # source failure must not stop other free sources
        module["_errors"].append(_safe_error(endpoint, exc))
        return pd.DataFrame()


def _mark_live(module: MutableMapping[str, Any], *catalog_ids: str) -> None:
    module["_live_catalog_ids"].update(item for item in catalog_ids if item)


def _series_item(
    *,
    identifier: str,
    label: str,
    submodule: str,
    unit: str,
    frequency: str,
    source: str,
    points: Sequence[Mapping[str, Any]],
    catalog_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Build the stable public time-series contract without filling gaps."""

    clean_points: list[dict[str, Any]] = []
    for point in points:
        if not isinstance(point, Mapping) or not point.get("date"):
            continue
        raw_value = point.get("value")
        value = None if isinstance(raw_value, bool) else _to_number(raw_value)
        if value is None:
            continue
        clean_point: dict[str, Any] = {"date": str(point["date"]), "value": value}
        # OHLC is an all-or-none extension of the public point contract.  A
        # partial upstream candle must never suppress an otherwise valid close.
        ohlc = {
            field: None if isinstance(point.get(field), bool) else _to_number(point.get(field))
            for field in PUBLIC_SERIES_OHLC_FIELDS
        }
        if all(number is not None for number in ohlc.values()):
            clean_point.update(ohlc)
        clean_points.append(clean_point)
    item: dict[str, Any] = {
        "id": identifier,
        "label": label,
        "submodule": submodule,
        "unit": unit,
        "frequency": frequency,
        "status": status or ("live" if clean_points else "unavailable"),
        "source": source,
        "as_of": clean_points[-1]["date"] if clean_points else None,
        "data": clean_points,
    }
    if catalog_id:
        item["catalog_id"] = catalog_id
    return item


def _publish_series(
    module: MutableMapping[str, Any],
    *,
    identifier: str,
    label: str,
    submodule: str,
    unit: str,
    frequency: str,
    source: str,
    points: Sequence[Mapping[str, Any]],
    catalog_id: str | None = None,
) -> dict[str, Any]:
    item = _series_item(
        identifier=identifier,
        label=label,
        submodule=submodule,
        unit=unit,
        frequency=frequency,
        source=source,
        points=points,
        catalog_id=catalog_id,
    )
    module["series"].append(item)
    if item["status"] == "live" and catalog_id:
        _mark_live(module, catalog_id)
    return item


def _add_series(
    module: MutableMapping[str, Any],
    *,
    identifier: str,
    title: str,
    points: list[dict[str, Any]],
    source: str,
    unit: str,
    catalog_id: str,
    submodule: str = "总览",
    frequency: str = "日",
    change_mode: str = "pct",
) -> None:
    _publish_series(
        module,
        identifier=identifier,
        label=title,
        submodule=submodule,
        unit=unit,
        frequency=frequency,
        source=source,
        points=points,
        catalog_id=catalog_id,
    )
    if not points:
        return
    if change_mode == "difference" and len(points) >= 2:
        change = float(points[-1]["value"] - points[-2]["value"])
    else:
        change = _pct_change(points)
    module["kpis"].append(
        _kpi(identifier, title, points[-1]["value"], unit, change, points[-1]["date"], source, catalog_id)
    )
    module["charts"].append({
        "id": f"{identifier}_history",
        "title": title,
        "kind": "line",
        "unit": unit,
        "series": [{"name": title, "data": points}],
        "source": source,
    })
def _finalize_module(module: MutableMapping[str, Any]) -> dict[str, Any]:
    has_data = bool(
        any(series.get("data") for series in module["series"])
        or module["kpis"]
        or module["charts"]
        or module["tables"]
    )
    if has_data:
        module["status"] = "partial" if module["_errors"] else "ok"
    else:
        module["status"] = "failed"
    observed_dates: list[date] = []
    for chart in module["charts"]:
        chart["status"] = "ok" if any(series.get("data") for series in chart.get("series", [])) else "unavailable"
        chart_dates: list[date] = []
        for chart_series in chart.get("series", []):
            if not isinstance(chart_series, Mapping):
                continue
            for point in chart_series.get("data", []):
                if not isinstance(point, Mapping):
                    continue
                parsed = _parse_date(point.get("date"))
                if parsed is not None:
                    chart_dates.append(parsed)
        if chart_dates:
            chart["as_of"] = max(chart_dates).isoformat()
            observed_dates.extend(chart_dates)
        else:
            parsed = _parse_date(chart.get("as_of"))
            if parsed is not None:
                observed_dates.append(parsed)

    table_date_fields = (
        "as_of", "published_at", "date", "datetime", "timestamp", "time",
        "发布日期", "公告日期", "发布时间", "日期", "时间",
    )
    table_source_fields = ("source", "来源", "文章来源", "信息来源")
    for table in module["tables"]:
        rows = table.get("rows", [])
        table["status"] = "ok" if rows else "unavailable"
        row_dates: list[date] = []
        row_sources: list[str] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            for field in table_date_fields:
                raw_date = row.get(field)
                if not isinstance(raw_date, (date, datetime, pd.Timestamp)):
                    # A time-only value such as 15:30 must not be interpreted
                    # by pandas as "today" and silently advance table as_of.
                    if not re.search(r"(?:19|20)\d{2}", str(raw_date or "")):
                        continue
                parsed = _parse_date(raw_date)
                if parsed is not None:
                    row_dates.append(parsed)
            for field in table_source_fields:
                raw_source = row.get(field)
                if not isinstance(raw_source, (str, int, float)) or isinstance(raw_source, bool):
                    continue
                source = str(raw_source).strip()
                if source and source not in row_sources:
                    row_sources.append(source)
        if row_dates:
            table["as_of"] = max(row_dates).isoformat()
            observed_dates.extend(row_dates)
        else:
            parsed = _parse_date(table.get("as_of"))
            if parsed is not None:
                observed_dates.append(parsed)
        if row_sources:
            table["source"] = " / ".join(row_sources)

    for series in module["series"]:
        parsed = _parse_date(series.get("as_of"))
        if parsed is not None:
            observed_dates.append(parsed)
    for kpi in module["kpis"]:
        parsed = _parse_date(kpi.get("as_of"))
        if parsed is not None:
            observed_dates.append(parsed)
    module["as_of"] = max(observed_dates).isoformat() if observed_dates else None
    for error in module["_errors"]:
        module["alerts"].append({
            "level": "warning",
            "message": f"{error['endpoint']} 暂不可用；未用占位数替代",
            "error_type": error["error_type"],
        })
    return dict(module)


def _module_catalog_rows(
    module_name: str,
    catalog_rows: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows = list(catalog_rows or [])
    if not rows or not any(row.get("api_field") for row in rows):
        seed = Path(__file__).resolve().parent / "data" / "indicator_catalog.seed.json"
        try:
            payload = load_json(seed)
            rows = list(payload.get("rows", [])) if payload else []
        except (OSError, ValueError, PipelineError):
            rows = []
    return [dict(row) for row in rows if row.get("module") == module_name]


def _difference_points(
    left: Sequence[Mapping[str, Any]],
    right: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    right_values = {
        str(point.get("date")): _to_number(point.get("value"))
        for point in right
        if point.get("date")
    }
    result: list[dict[str, Any]] = []
    for point in left:
        day = str(point.get("date") or "")
        left_value = _to_number(point.get("value"))
        right_value = right_values.get(day)
        if day and left_value is not None and right_value is not None:
            result.append({"date": day, "value": left_value - right_value})
    return result


def _series_by_id(module: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id")): item
        for item in module.get("series", [])
        if item.get("id")
    }


def _add_combination_chart(
    module: MutableMapping[str, Any],
    *,
    identifier: str,
    title: str,
    series_ids: Sequence[str],
    unit: str,
) -> None:
    index = _series_by_id(module)
    chart_series = [
        {"name": index[item]["label"], "data": index[item]["data"]}
        for item in series_ids
        if item in index and index[item].get("data")
    ]
    if chart_series:
        module["charts"].append({
            "id": identifier,
            "title": title,
            "kind": "line",
            "unit": unit,
            "series": chart_series,
            "source": "AKShare公开宏观数据；缺失观测保持断点",
        })


def fetch_macro(
    client: Any,
    now: datetime,
    catalog_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fetch every parameter-free AKShare macro series registered in catalog."""

    module = _module("macro")
    today = now.date()
    rows = _module_catalog_rows("宏观", catalog_rows)
    grouped: dict[str, list[dict[str, Any]]] = {}
    derived_rows: dict[str, dict[str, Any]] = {}
    for row in rows:
        api_field = str(row.get("api_field") or "")
        primary = str(row.get("primary_source") or "")
        if "/" in api_field and primary == "AKShare":
            endpoint, field = api_field.split("/", 1)
            if endpoint.startswith("macro_"):
                item = dict(row)
                item["_field"] = field
                grouped.setdefault(endpoint, []).append(item)
        elif row.get("id") in {"M0016", "M0046", "M0072"}:
            derived_rows[str(row["id"])] = dict(row)

    date_candidates = [
        "日期", "月份", "季度", "统计时间", "时间", "周期", "报告期",
        "date", "month", "quarter",
    ]
    latest_rows: list[dict[str, Any]] = []
    for endpoint, definitions in grouped.items():
        if endpoint == "macro_china_society_traffic_volume":
            # Remote audit: this sole endpoint exceeded 50 seconds while the
            # other 42 free endpoints succeeded.  Keep its fields explicitly
            # unavailable instead of making it a blocking daily dependency.
            frame = pd.DataFrame()
            module["_errors"].append({
                "endpoint": endpoint,
                "error_type": "SkippedSlowSource",
                "message": "audited endpoint exceeded daily latency budget",
            })
        else:
            frame = _attempt(module, client, endpoint)
        source = f"AKShare {endpoint}"
        for row in definitions:
            points = _points(frame, date_candidates, [str(row["_field"])], today)
            item_source = source
            if (
                endpoint == "macro_china_hgjck"
                and "金额" in str(row["_field"])
                and str(row.get("unit") or "") == "亿美元"
                and points
                and abs(float(points[-1]["value"])) >= 1_000_000
            ):
                points = [
                    {"date": point["date"], "value": float(point["value"]) / 100_000.0}
                    for point in points
                ]
                item_source += "；原始金额按1e5换算为亿美元，并与AKShare贸易帐端点交叉校验"
            item = _publish_series(
                module,
                identifier=str(row.get("variable") or row["id"]),
                label=str(row.get("metric") or row.get("variable") or row["id"]),
                submodule=str(row.get("submodule") or "宏观"),
                unit=str(row.get("unit") or ""),
                frequency=str(row.get("frequency") or ""),
                source=item_source,
                points=points,
                catalog_id=str(row["id"]),
            )
            if points:
                change_mode = "difference" if str(row.get("unit")) in {"%", "点", "百分点", "指数"} else "pct"
                change = (
                    float(points[-1]["value"] - points[-2]["value"])
                    if change_mode == "difference" and len(points) >= 2
                    else _pct_change(points)
                )
                module["kpis"].append(_kpi(
                    item["id"], item["label"], points[-1]["value"], item["unit"],
                    change, item["as_of"], item["source"], item.get("catalog_id"),
                ))
                latest_rows.append({
                    "indicator": item["label"], "value": points[-1]["value"],
                    "unit": item["unit"], "as_of": item["as_of"], "source": item["source"],
                })

    # Derived series only use dates present in both verified inputs; there is no
    # forward fill, interpolation, or zero substitution.
    derived_specs = {
        "M0016": ("cn_pmi_mfg", "cn_pmi_non_mfg"),
        "M0046": ("cn_m1_yoy", "cn_m2_yoy"),
        "M0072": ("cn_export_value", "cn_import_value"),
    }
    index = _series_by_id(module)
    for catalog_id, (left_id, right_id) in derived_specs.items():
        row = derived_rows.get(catalog_id)
        if row is None:
            continue
        points = _difference_points(index.get(left_id, {}).get("data", []), index.get(right_id, {}).get("data", []))
        source = f"本地派生：{left_id}-{right_id}；仅使用同日期AKShare观测"
        item = _publish_series(
            module,
            identifier=str(row.get("variable") or catalog_id),
            label=str(row.get("metric") or row.get("variable") or catalog_id),
            submodule=str(row.get("submodule") or "宏观"),
            unit=str(row.get("unit") or ""),
            frequency=str(row.get("frequency") or ""),
            source=source,
            points=points,
            catalog_id=catalog_id,
        )
        if points:
            derived_unit = str(item.get("unit") or "")
            derived_change = (
                float(points[-1]["value"] - points[-2]["value"])
                if derived_unit in {"%", "点", "百分点", "指数"} and len(points) >= 2
                else _pct_change(points)
            )
            module["kpis"].append(_kpi(
                item["id"], item["label"], points[-1]["value"], item["unit"],
                derived_change,
                item["as_of"], source, catalog_id,
            ))
            latest_rows.append({
                "indicator": item["label"], "value": points[-1]["value"],
                "unit": item["unit"], "as_of": item["as_of"], "source": source,
            })
        index[item["id"]] = item

    for chart in (
        ("macro_growth_combo", "增长与需求同比", ["cn_gdp_yoy", "cn_industrial_prod_yoy", "cn_fai_yoy", "cn_retail_yoy"], "%"),
        ("macro_pmi_combo", "PMI与景气", ["cn_pmi_mfg", "cn_pmi_non_mfg", "cn_pmi_mfg_nonmfg_gap"], "点"),
        ("macro_inflation_combo", "通胀与价格", ["cn_cpi_yoy", "cn_ppi_yoy"], "%"),
        ("macro_money_combo", "货币供应同比", ["cn_m2_yoy", "cn_m1_yoy", "cn_m0_yoy", "cn_m1_m2_gap"], "%"),
        ("macro_rates_combo", "货币市场与政策利率", ["cn_shibor_on", "cn_shibor_1w", "cn_shibor_1m", "cn_shibor_3m", "cn_lpr_1y", "cn_lpr_5y"], "%"),
        ("macro_trade_combo", "进出口同比", ["cn_export_yoy", "cn_import_yoy"], "%"),
        ("macro_shipping_combo", "全球航运景气", ["global_bdi", "global_bci", "global_bpi", "global_bcti", "global_bdti"], "点"),
    ):
        _add_combination_chart(
            module, identifier=chart[0], title=chart[1], series_ids=chart[2], unit=chart[3],
        )
    if latest_rows:
        latest_rows.sort(key=lambda item: (item["as_of"] or "", item["indicator"]), reverse=True)
        module["tables"].append({
            "id": "macro_latest",
            "title": "宏观指标最新值（仅实际获取）",
            "columns": ["indicator", "value", "unit", "as_of", "source"],
            "rows": latest_rows,
        })
    return _finalize_module(module)

def _market_metrics(points: list[dict[str, Any]]) -> dict[str, float | None]:
    return {
        "ret_1d": _pct_change(points, 1),
        "ret_5d": _pct_change(points, 5),
        "ret_20d": _pct_change(points, 20),
        "vol_20d": _volatility(points, 20),
        "mdd_60d": _max_drawdown(points, 60),
    }


def _return_points(points: Sequence[Mapping[str, Any]], periods: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index in range(periods, len(points)):
        current = _to_number(points[index].get("value"))
        prior = _to_number(points[index - periods].get("value"))
        if current is not None and prior not in {None, 0.0}:
            result.append({"date": str(points[index]["date"]), "value": (current / prior - 1.0) * 100.0})
    return result


def _rolling_volatility_points(
    points: Sequence[Mapping[str, Any]], periods: int = 20,
) -> list[dict[str, Any]]:
    values = pd.Series([_to_number(item.get("value")) for item in points], dtype="float64")
    returns = values.pct_change(fill_method=None)
    volatility = returns.rolling(periods, min_periods=periods).std(ddof=1) * math.sqrt(252) * 100.0
    result: list[dict[str, Any]] = []
    for index, value in enumerate(volatility):
        if pd.notna(value) and math.isfinite(float(value)):
            result.append({"date": str(points[index]["date"]), "value": float(value)})
    return result


def _rolling_drawdown_points(
    points: Sequence[Mapping[str, Any]], periods: int = 60,
) -> list[dict[str, Any]]:
    values = pd.Series([_to_number(item.get("value")) for item in points], dtype="float64")
    peaks = values.rolling(periods, min_periods=1).max()
    drawdown = (values / peaks - 1.0) * 100.0
    result: list[dict[str, Any]] = []
    for index, value in enumerate(drawdown):
        if pd.notna(value) and math.isfinite(float(value)):
            result.append({"date": str(points[index]["date"]), "value": float(value)})
    return result


def _rolling_max_drawdown_points(
    points: Sequence[Mapping[str, Any]], periods: int,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index in range(len(points)):
        window = points[max(0, index - periods + 1): index + 1]
        value = _max_drawdown(window, len(window))
        if value is not None:
            result.append({"date": str(points[index]["date"]), "value": value})
    return result


def fetch_global_markets(client: Any, now: datetime) -> dict[str, Any]:
    module = _module("global_markets")
    today = now.date()
    markets = [
        ("sp500", "标普500", "美国", "index_us_stock_sina", {"symbol": ".INX"}, ("M0091", "M0093", "M0094", "M0095", "M0097", "M0098")),
        ("nasdaq", "纳斯达克", "美国", "index_us_stock_sina", {"symbol": ".IXIC"}, ("M0091", "M0093", "M0094", "M0095", "M0097", "M0098")),
        ("dow", "道琼斯", "美国", "index_us_stock_sina", {"symbol": ".DJI"}, ("M0091", "M0093", "M0094", "M0095", "M0097", "M0098")),
        ("dax", "德国DAX", "欧洲", "index_global_hist_sina", {"symbol": "德国DAX30"}, ("M0104", "M0106", "M0107", "M0108", "M0110", "M0111")),
        ("ftse", "英国富时100", "欧洲", "index_global_hist_sina", {"symbol": "英国富时100"}, ("M0104", "M0106", "M0107", "M0108", "M0110", "M0111")),
        ("nikkei", "日经225", "亚洲", "index_global_hist_sina", {"symbol": "日经225"}, ("M0104", "M0106", "M0107", "M0108", "M0110", "M0111")),
        ("kospi", "韩国KOSPI", "亚洲", "index_global_hist_sina", {"symbol": "韩国KOSPI"}, ("M0104", "M0106", "M0107", "M0108", "M0110", "M0111")),
        ("hsi", "恒生指数", "中国香港", "stock_hk_index_daily_sina", {"symbol": "HSI"}, ("M0117", "M0119", "M0120", "M0121", "M0123", "M0124")),
        ("hstech", "恒生科技", "中国香港", "stock_hk_index_daily_sina", {"symbol": "HSTECH"}, ("M0117", "M0119", "M0120", "M0121", "M0123", "M0124")),
        ("sse", "上证综指", "中国A股", "stock_zh_index_daily", {"symbol": "sh000001"}, ("M0130", "M0132", "M0133", "M0134", "M0136", "M0137")),
        ("csi300", "沪深300", "中国A股", "stock_zh_index_daily", {"symbol": "sh000300"}, ("M0130", "M0132", "M0133", "M0134", "M0136", "M0137")),
    ]
    rows: list[dict[str, Any]] = []
    rebased_series: list[dict[str, Any]] = []
    return_heatmap: list[dict[str, Any]] = []
    drawdown_series: list[dict[str, Any]] = []
    for identifier, name, region, endpoint, kwargs, catalog_ids in markets:
        frame = _attempt(module, client, endpoint, **kwargs)
        points = _points(frame, ["date", "日期", "时间"], ["close", "收盘", "收盘价"], today)
        source = f"AKShare {endpoint}"
        close_id, ret1_id, ret5_id, ret20_id, vol_id, mdd_id = catalog_ids
        close_series = _publish_series(
            module, identifier=f"global_{identifier}_close", label=f"{name}收盘",
            submodule=f"股票指数/{region}", unit="指数点", frequency="日",
            source=source, points=points, catalog_id=close_id,
        )
        ret1_points = _return_points(points, 1)
        ret5_points = _return_points(points, 5)
        ret20_points = _return_points(points, 20)
        vol_points = _rolling_volatility_points(points, 20)
        drawdown_points = _rolling_drawdown_points(points, 60)
        for suffix, label, data, catalog_id in (
            ("ret_1d", "日收益", ret1_points, ret1_id),
            ("ret_5d", "5日收益", ret5_points, ret5_id),
            ("ret_20d", "20日收益", ret20_points, ret20_id),
            ("vol_20d", "20日年化波动率", vol_points, vol_id),
            ("drawdown_60d", "60日滚动回撤", drawdown_points, mdd_id),
        ):
            _publish_series(
                module, identifier=f"global_{identifier}_{suffix}", label=f"{name}{label}",
                submodule=f"股票指数/{region}", unit="%", frequency="日", source=source,
                points=data, catalog_id=catalog_id,
            )
        if not points:
            continue
        metrics = _market_metrics(points)
        module["kpis"].append(_kpi(
            f"market_{identifier}", name, points[-1]["value"], "指数点", metrics["ret_1d"],
            points[-1]["date"], source, close_id,
        ))
        rows.append({
            "market": name, "region": region, "close": points[-1]["value"],
            "ret_1d": metrics["ret_1d"], "ret_5d": metrics["ret_5d"],
            "ret_20d": metrics["ret_20d"], "vol_20d": metrics["vol_20d"],
            "mdd_60d": metrics["mdd_60d"], "as_of": points[-1]["date"], "source": source,
        })
        rebased = _rebased(points[-120:])
        if rebased:
            rebased_series.append({"name": name, "data": rebased})
        if ret1_points:
            return_heatmap.append({"name": name, "data": ret1_points[-20:]})
        if drawdown_points:
            drawdown_series.append({"name": name, "data": drawdown_points[-120:]})
    if rebased_series:
        module["charts"].append({
            "id": "global_rebased_120d", "title": "全球主要指数相对表现（起点=100）",
            "kind": "line", "unit": "点", "series": rebased_series,
            "source": "AKShare公开行情；本地仅做起点归一化",
        })
    if return_heatmap:
        module["charts"].append({
            "id": "global_return_heatmap_20d", "title": "全球指数近20日收益热力图",
            "kind": "heatmap", "unit": "%", "series": return_heatmap,
            "source": "AKShare公开收盘价本地派生；缺失交易日不补值",
        })
    valid_rows = [row for row in rows if row.get("ret_20d") is not None]
    if valid_rows:
        ranking = sorted(valid_rows, key=lambda item: float(item["ret_20d"]), reverse=True)
        module["charts"].append({
            "id": "global_return_ranking_20d", "title": "全球指数20日收益排名",
            "kind": "bar", "unit": "%",
            "series": [{"name": row["market"], "data": [{"date": row["as_of"], "value": row["ret_20d"]}]} for row in ranking],
            "source": "AKShare公开收盘价本地派生",
        })
        risk_rows = [row for row in valid_rows if row.get("vol_20d") is not None]
        if risk_rows:
            module["charts"].append({
                "id": "global_risk_return_20d", "title": "全球指数风险—收益",
                "kind": "scatter", "unit": "%",
                "series": [{
                    "name": row["market"],
                    "data": [{"date": row["as_of"], "value": row["ret_20d"], "x": row["vol_20d"]}],
                } for row in risk_rows],
                "x_title": "20日年化波动率(%)", "y_title": "20日收益率(%)",
                "source": "AKShare公开收盘价本地派生",
            })
    if drawdown_series:
        module["charts"].append({
            "id": "global_drawdown_60d", "title": "全球指数60日滚动回撤",
            "kind": "line", "unit": "%", "series": drawdown_series,
            "source": "AKShare公开收盘价本地派生",
        })
    if rows:
        module["tables"].append({
            "id": "global_market_matrix", "title": "全球市场横截面",
            "columns": ["market", "region", "close", "ret_1d", "ret_5d", "ret_20d", "vol_20d", "mdd_60d", "as_of", "source"],
            "rows": rows,
        })
    return _finalize_module(module)

SW_L1_INDUSTRIES: dict[str, str] = {
    "801010": "农林牧渔", "801030": "基础化工", "801040": "钢铁",
    "801050": "有色金属", "801080": "电子", "801110": "家用电器",
    "801120": "食品饮料", "801130": "纺织服饰", "801140": "轻工制造",
    "801150": "医药生物", "801160": "公用事业", "801170": "交通运输",
    "801180": "房地产", "801200": "商贸零售", "801210": "社会服务",
    "801230": "综合", "801710": "建筑材料", "801720": "建筑装饰",
    "801730": "电力设备", "801740": "国防军工", "801750": "计算机",
    "801760": "传媒", "801770": "通信", "801780": "银行",
    "801790": "非银金融", "801880": "汽车", "801890": "机械设备",
    "801950": "煤炭", "801960": "石油石化", "801970": "环保",
    "801980": "美容护理",
}


def fetch_sw_industries(client: Any, now: datetime) -> dict[str, Any]:
    module = _module("sw_industries")
    # index_realtime_sw remains excluded because the audited AKShare version can
    # shift columns.  index_hist_sw is called independently for every L1 code so
    # one failed industry cannot invalidate the other 30.
    configured = os.environ.get("DASHBOARD_SW_HISTORY_CODES", "").strip()
    history_codes = (
        [item.strip() for item in configured.split(",") if item.strip()]
        if configured else list(SW_L1_INDUSTRIES)
    )
    unknown = [code for code in history_codes if code not in SW_L1_INDUSTRIES]
    if unknown:
        module["alerts"].append({
            "level": "warning", "message": "忽略不在申万一级行业清单中的代码：" + ",".join(unknown),
        })
    history_codes = [code for code in history_codes if code in SW_L1_INDUSTRIES]
    rows: list[dict[str, Any]] = []
    rebased_series: list[dict[str, Any]] = []
    return_heatmap: list[dict[str, Any]] = []
    drawdown_series: list[dict[str, Any]] = []
    source = "AKShare index_hist_sw"
    for code in history_codes:
        name = SW_L1_INDUSTRIES[code]
        frame = _attempt(module, client, "index_hist_sw", symbol=code, period="day")
        points = _points(frame, ["日期", "date"], ["收盘", "close"], now.date(), limit=380)
        ret1_points = _return_points(points, 1)
        ret5_points = _return_points(points, 5)
        ret20_points = _return_points(points, 20)
        vol_points = _rolling_volatility_points(points, 20)
        drawdown_points = _rolling_drawdown_points(points, 60)
        for identifier, label, data, unit, catalog_id in (
            (f"sw_{code}_close", f"{name}收盘", points, "点", "M0156"),
            (f"sw_{code}_ret_1d", f"{name}日收益", ret1_points, "%", "M0168"),
            (f"sw_{code}_ret_5d", f"{name}5日收益", ret5_points, "%", "M0169"),
            (f"sw_{code}_ret_20d", f"{name}20日收益", ret20_points, "%", "M0170"),
            (f"sw_{code}_vol_20d", f"{name}20日波动率", vol_points, "%", "M0173"),
            (f"sw_{code}_drawdown_60d", f"{name}60日滚动回撤", drawdown_points, "%", "M0174"),
        ):
            _publish_series(
                module, identifier=identifier, label=label, submodule="申万一级行业行情",
                unit=unit, frequency="日", source=source, points=data, catalog_id=catalog_id,
            )
        if not points:
            continue
        metrics = _market_metrics(points)
        rows.append({
            "code": code, "industry": name, "close": points[-1]["value"],
            "ret_1d": metrics["ret_1d"], "ret_5d": metrics["ret_5d"],
            "ret_20d": metrics["ret_20d"], "vol_20d": metrics["vol_20d"],
            "mdd_60d": metrics["mdd_60d"], "as_of": points[-1]["date"], "source": source,
        })
        rebased = _rebased(points[-120:])
        if rebased:
            rebased_series.append({"name": name, "data": rebased})
        if ret1_points:
            return_heatmap.append({"name": name, "data": ret1_points[-20:]})
        if drawdown_points:
            drawdown_series.append({"name": name, "data": drawdown_points[-120:]})
    rows.sort(key=lambda item: item["ret_1d"] if item["ret_1d"] is not None else -10**9, reverse=True)
    if rows:
        valid_returns = [row["ret_1d"] for row in rows if row["ret_1d"] is not None]
        valid_rank = [row for row in rows if row["ret_1d"] is not None]
        best = valid_rank[0] if valid_rank else rows[0]
        worst = valid_rank[-1] if valid_rank else rows[-1]
        median = float(pd.Series(valid_returns).median()) if valid_returns else None
        latest_date = max(row["as_of"] for row in rows)
        module["kpis"].extend([
            _kpi("sw_best", f"领涨：{best['industry']}", best["ret_1d"], "%", None, best["as_of"], source, "M0168"),
            _kpi("sw_worst", f"领跌：{worst['industry']}", worst["ret_1d"], "%", None, worst["as_of"], source, "M0168"),
            _kpi("sw_median", "31行业涨跌幅中位数", median, "%", None, latest_date, source, "M0168"),
            _kpi("sw_coverage", "申万一级行业有效覆盖", len(rows), "个", None, latest_date, source),
        ])
        module["tables"].append({
            "id": "sw_l1_full_snapshot", "title": "申万一级31行业行情",
            "columns": ["code", "industry", "close", "ret_1d", "ret_5d", "ret_20d", "vol_20d", "mdd_60d", "as_of", "source"],
            "rows": rows,
        })
    if rebased_series:
        module["charts"].append({
            "id": "sw_l1_rebased_120d", "title": "申万一级行业相对表现（起点=100）",
            "kind": "line", "unit": "点", "series": rebased_series, "source": source + "；本地起点归一化",
        })
    if return_heatmap:
        module["charts"].append({
            "id": "sw_l1_return_heatmap_20d", "title": "申万一级行业近20日收益热力图",
            "kind": "heatmap", "unit": "%", "series": return_heatmap, "source": source + "；本地派生",
        })
    valid_20d = [row for row in rows if row.get("ret_20d") is not None]
    if valid_20d:
        ranking = sorted(valid_20d, key=lambda item: float(item["ret_20d"]), reverse=True)
        module["charts"].append({
            "id": "sw_l1_return_ranking_20d", "title": "申万一级行业20日收益排名",
            "kind": "bar", "unit": "%",
            "series": [{"name": row["industry"], "data": [{"date": row["as_of"], "value": row["ret_20d"]}]} for row in ranking],
            "source": source + "；本地派生",
        })
        risk_rows = [row for row in valid_20d if row.get("vol_20d") is not None]
        if risk_rows:
            module["charts"].append({
                "id": "sw_l1_risk_return_20d", "title": "申万一级行业风险—收益",
                "kind": "scatter", "unit": "%",
                "series": [{"name": row["industry"], "data": [{"date": row["as_of"], "value": row["ret_20d"], "x": row["vol_20d"]}]} for row in risk_rows],
                "x_title": "20日年化波动率(%)", "y_title": "20日收益率(%)", "source": source + "；本地派生",
            })
    if drawdown_series:
        module["charts"].append({
            "id": "sw_l1_drawdown_60d", "title": "申万一级行业60日滚动回撤",
            "kind": "line", "unit": "%", "series": drawdown_series, "source": source + "；本地派生",
        })
    finalized = _finalize_module(module)
    if len(rows) < len(history_codes):
        finalized["alerts"].append({
            "level": "warning",
            "message": f"申万一级行业实际获取 {len(rows)}/{len(history_codes)}；失败行业保持 unavailable，未补值。",
        })
        if finalized["status"] == "ok":
            finalized["status"] = "partial"
    return finalized

def _business_candidates(today: date, count: int = 7) -> Iterable[date]:
    current = today
    yielded = 0
    while yielded < count:
        if current.weekday() < 5:
            yield current
            yielded += 1
        current -= timedelta(days=1)


def fetch_commodities(client: Any, now: datetime) -> dict[str, Any]:
    module = _module("commodities")
    symbols = [
        item.strip().upper()
        for item in os.environ.get("DASHBOARD_COMMODITIES", "AU,AG,CU,AL,RB,I,SC,TA,M,CF").split(",")
        if item.strip()
    ]
    spot = pd.DataFrame()
    spot_date: date | None = None
    for candidate in _business_candidates(now.date(), 5):
        spot = _attempt(module, client, "futures_spot_price", date=candidate.strftime("%Y%m%d"), vars_list=symbols)
        if not spot.empty:
            spot_date = candidate
            break
    basis_rows: list[dict[str, Any]] = []
    if not spot.empty:
        mappings = {
            "symbol": ["symbol", "var", "品种"],
            "spot": ["spot_price", "sp", "现货价格"],
            "near_contract": ["near_contract", "near_symbol", "近月合约"],
            "near_price": ["near_contract_price", "near_price", "近月价格"],
            "dominant_contract": ["dominant_contract", "dom_symbol", "主力合约"],
            "dominant_price": ["dominant_contract_price", "dom_price", "主力价格"],
            "near_basis": ["near_basis", "近月基差"],
            "dominant_basis": ["dom_basis", "dominant_basis", "主力基差"],
            "near_basis_rate": ["near_basis_rate", "近月基差率"],
            "dominant_basis_rate": ["dom_basis_rate", "dominant_basis_rate", "主力基差率"],
        }
        columns = {name: _find_column(spot, candidates) for name, candidates in mappings.items()}
        for _, record in spot.iterrows():
            symbol = str(record.get(columns["symbol"], "")).strip().upper() if columns["symbol"] is not None else ""
            if not symbol:
                continue
            row: dict[str, Any] = {"symbol": symbol, "as_of": spot_date.isoformat() if spot_date else None}
            for name in ("spot", "near_price", "dominant_price", "near_basis", "dominant_basis", "near_basis_rate", "dominant_basis_rate"):
                row[name] = _to_number(record.get(columns[name])) if columns[name] is not None else None
            for name in ("near_contract", "dominant_contract"):
                row[name] = str(record.get(columns[name], "")).strip() if columns[name] is not None else ""
            basis_rows.append(row)
            day = row["as_of"]
            for suffix, label, field, unit, catalog_id in (
                ("spot", "现货价", "spot", "品种单位", "M0229"),
                ("near_price", "近月价", "near_price", "元/品种单位", "M0231"),
                ("dominant_price", "主力价", "dominant_price", "元/品种单位", "M0233"),
                ("near_basis", "近月基差", "near_basis", "元/品种单位", "M0234"),
                ("dominant_basis", "主力基差", "dominant_basis", "元/品种单位", "M0235"),
                ("near_basis_rate", "近月基差率", "near_basis_rate", "%", "M0236"),
                ("dominant_basis_rate", "主力基差率", "dominant_basis_rate", "%", "M0237"),
            ):
                value = row[field]
                points = [{"date": day, "value": value}] if day and value is not None else []
                _publish_series(
                    module, identifier=f"commodity_{symbol.lower()}_{suffix}", label=f"{symbol}{label}",
                    submodule="期现与基差", unit=unit, frequency="日",
                    source="AKShare futures_spot_price", points=points, catalog_id=catalog_id,
                )
            if row["near_contract"]:
                _mark_live(module, "M0230")
            if row["dominant_contract"]:
                _mark_live(module, "M0232")
        if basis_rows:
            module["tables"].append({
                "id": "commodity_basis_snapshot", "title": "主要商品期现与基差",
                "source": "AKShare futures_spot_price",
                "columns": ["symbol", "spot", "near_contract", "near_price", "dominant_contract", "dominant_price", "near_basis", "dominant_basis", "near_basis_rate", "dominant_basis_rate", "as_of"],
                "rows": basis_rows,
            })
            basis_values = [row["dominant_basis_rate"] for row in basis_rows if row["dominant_basis_rate"] is not None]
            module["kpis"].append(_kpi(
                "commodity_basis_median", "主力基差率中位数",
                float(pd.Series(basis_values).median()) if basis_values else None, "%", None,
                spot_date.isoformat() if spot_date else None, "AKShare futures_spot_price", "M0237",
            ))

    market_rows: list[dict[str, Any]] = []
    rebased_series: list[dict[str, Any]] = []
    return_heatmap: list[dict[str, Any]] = []
    drawdown_series: list[dict[str, Any]] = []
    end_date = now.date().strftime("%Y%m%d")
    start_date = (now.date() - timedelta(days=430)).strftime("%Y%m%d")
    for symbol in symbols:
        frame = _attempt(module, client, "futures_main_sina", symbol=f"{symbol}0", start_date=start_date, end_date=end_date)
        points = _points(frame, ["日期", "date"], ["收盘价", "收盘", "close"], now.date(), limit=300)
        ret1_points = _return_points(points, 1)
        ret20_points = _return_points(points, 20)
        vol_points = _rolling_volatility_points(points, 20)
        drawdown_points = _rolling_drawdown_points(points, 60)
        source = "AKShare futures_main_sina"
        for identifier, label, data, catalog_id in (
            (f"commodity_{symbol.lower()}_main_close", f"{symbol}主力连续收盘", points, "M0242"),
            (f"commodity_{symbol.lower()}_ret_1d", f"{symbol}日收益", ret1_points, "M0250"),
            (f"commodity_{symbol.lower()}_ret_20d", f"{symbol}20日收益", ret20_points, "M0251"),
            (f"commodity_{symbol.lower()}_vol_20d", f"{symbol}20日波动率", vol_points, "M0252"),
        ):
            _publish_series(
                module, identifier=identifier, label=label, submodule="期货行情与派生信号",
                unit="元" if catalog_id == "M0242" else "%", frequency="日",
                source=source, points=data, catalog_id=catalog_id,
            )
        if not points:
            continue
        metrics = _market_metrics(points)
        market_rows.append({
            "symbol": symbol, "close": points[-1]["value"], "ret_1d": metrics["ret_1d"],
            "ret_20d": metrics["ret_20d"], "vol_20d": metrics["vol_20d"],
            "mdd_60d": metrics["mdd_60d"], "as_of": points[-1]["date"], "source": source,
        })
        rebased = _rebased(points[-120:])
        if rebased:
            rebased_series.append({"name": symbol, "data": rebased})
        if ret1_points:
            return_heatmap.append({"name": symbol, "data": ret1_points[-20:]})
        if drawdown_points:
            drawdown_series.append({"name": symbol, "data": drawdown_points[-120:]})
    if market_rows:
        module["tables"].append({
            "id": "commodity_market_matrix", "title": "主要商品收益与风险",
            "columns": ["symbol", "close", "ret_1d", "ret_20d", "vol_20d", "mdd_60d", "as_of", "source"],
            "rows": market_rows,
        })
    if rebased_series:
        module["charts"].append({
            "id": "commodity_main_120d", "title": "主要商品主力连续相对表现（起点=100）",
            "kind": "line", "unit": "点", "series": rebased_series,
            "source": "AKShare futures_main_sina；本地起点归一化",
        })
    if return_heatmap:
        module["charts"].append({
            "id": "commodity_return_heatmap_20d", "title": "主要商品近20日收益热力图",
            "kind": "heatmap", "unit": "%", "series": return_heatmap,
            "source": "AKShare futures_main_sina；本地派生",
        })
    valid_20d = [row for row in market_rows if row.get("ret_20d") is not None]
    if valid_20d:
        ranking = sorted(valid_20d, key=lambda item: float(item["ret_20d"]), reverse=True)
        module["charts"].append({
            "id": "commodity_return_ranking_20d", "title": "主要商品20日收益排名",
            "kind": "bar", "unit": "%",
            "series": [{"name": row["symbol"], "data": [{"date": row["as_of"], "value": row["ret_20d"]}]} for row in ranking],
            "source": "AKShare futures_main_sina；本地派生",
        })
        risk_rows = [row for row in valid_20d if row.get("vol_20d") is not None]
        if risk_rows:
            module["charts"].append({
                "id": "commodity_risk_return_20d", "title": "主要商品风险—收益",
                "kind": "scatter", "unit": "%",
                "series": [{"name": row["symbol"], "data": [{"date": row["as_of"], "value": row["ret_20d"], "x": row["vol_20d"]}]} for row in risk_rows],
                "x_title": "20日年化波动率(%)", "y_title": "20日收益率(%)",
                "source": "AKShare futures_main_sina；本地派生",
            })
    if drawdown_series:
        module["charts"].append({
            "id": "commodity_drawdown_60d", "title": "主要商品60日滚动回撤",
            "kind": "line", "unit": "%", "series": drawdown_series,
            "source": "AKShare futures_main_sina；本地派生",
        })

    auxiliary_specs = [
        ("futures_comex_inventory", {"symbol": "黄金"}, [
            ("comex_gold_inventory_ton", "COMEX黄金库存吨", ["COMEX黄金库存量-吨"], "吨", "M0256"),
            ("comex_gold_inventory_oz", "COMEX黄金库存盎司", ["COMEX黄金库存量-盎司"], "盎司", "M0257"),
        ], ["日期", "date"]),
        ("macro_shipping_bdi", {}, [("cmdty_bdi", "BDI", ["最新值"], "点", "M0258")], ["日期", "date"]),
        ("macro_shipping_bci", {}, [("cmdty_bci", "BCI", ["最新值"], "点", "M0259")], ["日期", "date"]),
        ("macro_shipping_bpi", {}, [("cmdty_bpi", "BPI", ["最新值"], "点", "M0260")], ["日期", "date"]),
        ("macro_shipping_bcti", {}, [("cmdty_bcti", "BCTI", ["最新值"], "点", "M0261")], ["日期", "date"]),
        ("macro_china_bdti_index", {}, [("cmdty_bdti", "BDTI", ["最新值"], "点", "M0262")], ["日期", "date"]),
        ("futures_hog_core", {"symbol": "外三元"}, [("hog_outer_ternary", "外三元猪价", ["value", "外三元"], "元/公斤", "M0263")], ["date", "日期"]),
        ("futures_hog_cost", {"symbol": "玉米"}, [("hog_feed_corn", "玉米饲料价", ["value", "玉米"], "元/吨", "M0264")], ["date", "日期"]),
        ("futures_hog_cost", {"symbol": "豆粕"}, [("hog_feed_soymeal", "豆粕饲料价", ["value", "豆粕"], "元/吨", "M0265")], ["date", "日期"]),
        ("futures_hog_supply", {"symbol": "生猪产能"}, [("hog_breeding_sow", "能繁母猪存栏", ["能繁母猪存栏", "value"], "万头", "M0266")], ["周期", "date", "日期"]),
    ]
    for endpoint, kwargs, definitions, date_candidates in auxiliary_specs:
        frame = _attempt(module, client, endpoint, **kwargs)
        source = f"AKShare {endpoint}"
        for identifier, label, value_candidates, unit, catalog_id in definitions:
            points = _points(frame, date_candidates, value_candidates, now.date(), limit=380)
            _publish_series(
                module, identifier=identifier, label=label, submodule="库存与运输",
                unit=unit, frequency="日/月", source=source, points=points, catalog_id=catalog_id,
            )
            if points:
                module["kpis"].append(_kpi(
                    identifier, label, points[-1]["value"], unit, _pct_change(points),
                    points[-1]["date"], source, catalog_id,
                ))
    auxiliary_index = _series_by_id(module)
    for chart_id, title, ids, unit in (
        ("commodity_shipping_indices", "航运景气指数", ["cmdty_bdi", "cmdty_bci", "cmdty_bpi", "cmdty_bcti", "cmdty_bdti"], "点"),
        ("commodity_hog_cost_supply", "生猪价格、饲料成本与供给", ["hog_outer_ternary", "hog_feed_corn", "hog_feed_soymeal", "hog_breeding_sow"], "多口径"),
        ("commodity_comex_gold_inventory", "COMEX黄金库存", ["comex_gold_inventory_ton"], "吨"),
    ):
        chart_series = [
            {"name": auxiliary_index[item]["label"], "data": auxiliary_index[item]["data"]}
            for item in ids if item in auxiliary_index and auxiliary_index[item].get("data")
        ]
        if chart_series:
            module["charts"].append({
                "id": chart_id, "title": title, "kind": "line", "unit": unit,
                "series": chart_series, "source": "AKShare公开数据；各序列保留原始单位",
            })
    return _finalize_module(module)

def fetch_stocks(client: Any, now: datetime) -> dict[str, Any]:
    module = _module("stock")
    symbols = [
        item.strip()
        for item in os.environ.get("DASHBOARD_STOCKS", "000001,600519,300750").split(",")
        if re.fullmatch(r"\d{6}", item.strip())
    ][:20]

    def record_schema_error(endpoint: str) -> None:
        module["_errors"].append({
            "endpoint": endpoint,
            "error_type": "SchemaError",
            "message": "source schema could not be parsed; a fallback was attempted",
        })

    def exact_column(frame: pd.DataFrame, candidates: Sequence[str]) -> Any | None:
        exact = {_normalized_name(column): column for column in frame.columns}
        for candidate in candidates:
            column = exact.get(_normalized_name(candidate))
            if column is not None:
                return column
        return None

    def stock_history_points(frame: pd.DataFrame) -> list[dict[str, Any]]:
        candidate = frame
        date_col = exact_column(candidate, ["日期", "date"])
        if date_col is None and not isinstance(candidate.index, pd.RangeIndex):
            candidate = candidate.reset_index()
            date_col = exact_column(candidate, ["index", "日期", "date"])
        close_col = exact_column(candidate, ["收盘", "close"])
        if date_col is None or close_col is None:
            return []
        ohlc_columns = {
            "open": exact_column(candidate, ["开盘", "open"]),
            "high": exact_column(candidate, ["最高", "high"]),
            "low": exact_column(candidate, ["最低", "low"]),
            "close": close_col,
        }
        observations: dict[str, dict[str, Any]] = {}
        for _, row in candidate.iterrows():
            parsed_date = _parse_date(row.get(date_col))
            close_value = _to_number(row.get(close_col))
            if parsed_date is None or close_value is None or parsed_date > now.date() + timedelta(days=1):
                continue
            point: dict[str, Any] = {"date": parsed_date.isoformat(), "value": close_value}
            if all(column is not None for column in ohlc_columns.values()):
                candle = {
                    field: _to_number(row.get(column))
                    for field, column in ohlc_columns.items()
                }
                if all(value is not None for value in candle.values()):
                    point.update(candle)
            observations[point["date"]] = point
        return [observations[day] for day in sorted(observations)[-300:]]

    def spot_records(frame: pd.DataFrame, endpoint: str) -> dict[str, tuple[Mapping[str, Any], pd.DataFrame, str]]:
        if frame.empty:
            return {}
        code_col = exact_column(frame, ["代码", "symbol"])
        if code_col is None:
            record_schema_error(endpoint)
            return {}
        found: dict[str, tuple[Mapping[str, Any], pd.DataFrame, str]] = {}
        source = f"AKShare {endpoint}"
        for _, record in frame.iterrows():
            raw_code = str(record.get(code_col, "")).strip()
            if re.fullmatch(r"\d{1,6}(?:\.0)?", raw_code):
                code = f"{int(float(raw_code)):06d}"
            else:
                match = re.search(r"(\d{6})$", raw_code)
                code = match.group(1) if match else ""
            if code in symbols:
                found[code] = (record, frame, source)
        return found

    spot_lookup = spot_records(_attempt(module, client, "stock_zh_a_spot_em"), "stock_zh_a_spot_em")
    if len(spot_lookup) < len(symbols):
        fallback_lookup = spot_records(_attempt(module, client, "stock_zh_a_spot"), "stock_zh_a_spot")
        for symbol, record_bundle in fallback_lookup.items():
            spot_lookup.setdefault(symbol, record_bundle)

    rows: list[dict[str, Any]] = []
    chart_series: list[dict[str, Any]] = []
    chart_sources: set[str] = set()
    return_heatmap: list[dict[str, Any]] = []
    drawdown_series: list[dict[str, Any]] = []
    start_date = (now.date() - timedelta(days=430)).strftime("%Y%m%d")
    end_date = now.date().strftime("%Y%m%d")

    def latest_value(frame: pd.DataFrame, candidates: Sequence[str]) -> float | None:
        if frame.empty:
            return None
        date_col = exact_column(frame, ["日期", "date"])
        value_col = exact_column(frame, candidates)
        if date_col is None or value_col is None:
            return None
        latest: tuple[date, float] | None = None
        for raw_date, raw_value in zip(frame[date_col], frame[value_col]):
            parsed_date = _parse_date(raw_date)
            value = _to_number(raw_value)
            if parsed_date is None or value is None or parsed_date > now.date() + timedelta(days=1):
                continue
            if latest is None or parsed_date >= latest[0]:
                latest = (parsed_date, value)
        return latest[1] if latest else None

    for symbol in symbols:
        market_symbol = (
            "bj" if symbol.startswith(("4", "8", "92"))
            else "sh" if symbol.startswith(("5", "6", "9"))
            else "sz"
        ) + symbol
        candidates = [
            ("stock_zh_a_hist", {"symbol": symbol, "period": "daily", "start_date": start_date, "end_date": end_date, "adjust": "qfq", "timeout": 20}),
            ("stock_zh_a_daily", {"symbol": market_symbol, "start_date": start_date, "end_date": end_date, "adjust": "qfq"}),
            ("stock_zh_a_hist_tx", {"symbol": market_symbol, "start_date": start_date, "end_date": end_date, "adjust": "qfq", "timeout": 20}),
        ]
        frame = pd.DataFrame()
        points: list[dict[str, Any]] = []
        history_source: str | None = None
        for endpoint, kwargs in candidates:
            candidate_frame = _attempt(module, client, endpoint, **kwargs)
            candidate_points = stock_history_points(candidate_frame)
            if candidate_points:
                frame = candidate_frame
                points = candidate_points
                history_source = f"AKShare {endpoint}"
                break
            if not candidate_frame.empty:
                record_schema_error(endpoint)

        spot_bundle = spot_lookup.get(symbol)
        record: Mapping[str, Any] | None = None
        spot_frame = pd.DataFrame()
        spot_source: str | None = None
        if spot_bundle is not None:
            record, spot_frame, spot_source = spot_bundle

        name = symbol
        qfq_close = points[-1]["value"] if points else None
        raw_close = None
        as_of = points[-1]["date"] if points else None
        pct = _pct_change(points, 1)
        # The history calls are qfq-adjusted. Raw open/high/low/pre-close are only
        # populated from an explicit spot field; qfq values remain separately labelled.
        open_price = high = low = pre_close = None
        if history_source == "AKShare stock_zh_a_hist_tx":
            # Tencent names its volume-in-hands column "amount"; it is not turnover value.
            tx_volume_hands = latest_value(frame, ["amount"])
            volume = tx_volume_hands * 100.0 if tx_volume_hands is not None else None
            amount = None
        else:
            volume = latest_value(frame, ["成交量", "volume"])
            if volume is not None and history_source == "AKShare stock_zh_a_hist":
                volume *= 100.0  # Eastmoney history uses hands; catalog M0281 uses shares.
            amount = latest_value(frame, ["成交额", "amount"])
        turnover = latest_value(frame, ["换手率", "turnover"])
        if turnover is not None and history_source == "AKShare stock_zh_a_daily":
            # Sina history exposes volume/outstanding_share (0-1); catalog M0283 uses %.
            turnover *= 100.0
        volume_ratio = pe = pb = total_mv = circ_mv = None

        if record is not None:
            def spot_value(candidates: Sequence[str]) -> float | None:
                column = exact_column(spot_frame, candidates)
                return _to_number(record.get(column)) if column is not None else None

            def prefer_spot(current: float | None, candidates: Sequence[str]) -> float | None:
                candidate = spot_value(candidates)
                return candidate if candidate is not None else current

            name_column = exact_column(spot_frame, ["名称", "name"])
            if name_column is not None:
                name = str(record.get(name_column, symbol)).strip() or symbol
            open_price = prefer_spot(open_price, ["今开", "开盘", "open"])
            high = prefer_spot(high, ["最高", "high"])
            low = prefer_spot(low, ["最低", "low"])
            pre_close = prefer_spot(pre_close, ["昨收", "pre_close"])
            raw_close = spot_value(["最新价", "close"])
            pct = prefer_spot(pct, ["涨跌幅", "pct_chg"])
            spot_volume = spot_value(["成交量", "volume"])
            if spot_volume is not None and spot_source == "AKShare stock_zh_a_spot_em":
                spot_volume *= 100.0  # Eastmoney spot uses hands; catalog M0281 uses shares.
            if spot_volume is not None:
                volume = spot_volume
            amount = prefer_spot(amount, ["成交额", "amount"])
            turnover = prefer_spot(turnover, ["换手率", "turnover_rate", "turnover"])
            volume_ratio = spot_value(["量比", "volume_ratio"])
            # Dynamic PE is not PE TTM. Only an explicitly TTM-labelled source may fill M0285.
            pe = spot_value(["市盈率-TTM", "PE TTM", "pe_ttm"])
            pb = spot_value(["市净率", "pb"])
            total_mv = spot_value(["总市值", "total_mv"])
            circ_mv = spot_value(["流通市值", "circ_mv"])
            if as_of is None:
                timestamp_col = exact_column(spot_frame, ["时间戳", "日期", "时间"])
                if timestamp_col is not None:
                    raw_timestamp = record.get(timestamp_col)
                    timestamp_text = str(raw_timestamp or "").strip()
                    has_explicit_date = isinstance(raw_timestamp, (date, datetime, pd.Timestamp)) or bool(
                        re.search(r"(?:19|20)\d{2}\D{0,3}\d{1,2}\D{0,3}\d{1,2}", timestamp_text)
                    )
                    if has_explicit_date:
                        spot_date = _parse_date(raw_timestamp)
                        as_of = spot_date.isoformat() if spot_date is not None else None

        close = raw_close if raw_close is not None else qfq_close
        if close is None:
            continue
        close_basis = "raw" if raw_close is not None else "qfq"
        source = "/".join(item for item in (history_source, spot_source) if item)
        if not source:
            continue
        ret_5d = _pct_change(points, 5)
        ret_20d = _pct_change(points, 20)
        vol_20d = _volatility(points, 20)
        mdd_20d = _max_drawdown(points, 20)
        row = {
            "code": symbol,
            "name": name,
            "open": open_price,
            "high": high,
            "low": low,
            "pre_close": pre_close,
            "close": close,
            "close_basis": close_basis,
            "qfq_close": qfq_close,
            "ret_1d": pct,
            "ret_5d": ret_5d,
            "ret_20d": ret_20d,
            "vol_20d": vol_20d,
            "mdd_20d": mdd_20d,
            "volume": volume,
            "amount": amount,
            "turnover": turnover,
            "volume_ratio": volume_ratio,
            "pe": pe,
            "pb": pb,
            "total_mv": total_mv,
            "circ_mv": circ_mv,
            "as_of": as_of,
            "source": source,
        }
        rows.append(row)
        if points and history_source:
            ret1_points = _return_points(points, 1)
            ret5_points = _return_points(points, 5)
            ret20_points = _return_points(points, 20)
            vol_points = _rolling_volatility_points(points, 20)
            mdd_points = _rolling_max_drawdown_points(points, 20)
            for series_id, label, data, catalog_id in (
                (f"stock_{symbol}_qfq_close", f"{name}前复权收盘", points, "M0290"),
                (f"stock_{symbol}_ret_5d", f"{name}5日收益", ret5_points, "M0321"),
                (f"stock_{symbol}_ret_20d", f"{name}20日收益", ret20_points, "M0322"),
                (f"stock_{symbol}_vol_20d", f"{name}20日波动率", vol_points, "M0323"),
                (f"stock_{symbol}_mdd_20d", f"{name}20日最大回撤", mdd_points, "M0324"),
            ):
                _publish_series(
                    module, identifier=series_id, label=label, submodule="自选股行情与风险",
                    unit="元" if catalog_id == "M0290" else "%", frequency="日",
                    source=history_source, points=data, catalog_id=catalog_id,
                )
            if ret1_points:
                return_heatmap.append({"name": f"{name} {symbol}", "data": ret1_points[-20:]})
            if mdd_points:
                drawdown_series.append({"name": f"{name} {symbol}", "data": mdd_points[-120:]})
        elif raw_close is not None and as_of:
            _publish_series(
                module, identifier=f"stock_{symbol}_raw_close", label=f"{name}最新价",
                submodule="自选股行情与风险", unit="元", frequency="日", source=source,
                points=[{"date": as_of, "value": raw_close}], catalog_id="M0278",
            )
        kpi_label = f"{name} {symbol}" if raw_close is not None else f"{name} {symbol}（前复权）"
        kpi_catalog_id = "M0278" if raw_close is not None else "M0290"
        module["kpis"].append(
            _kpi(f"stock_{symbol}", kpi_label, close, "元", pct, as_of, source, kpi_catalog_id)
        )
        if points and history_source:
            chart_series.append({"name": f"{name} {symbol}", "data": _rebased(points[-120:])})
            chart_sources.add(history_source)

        conditional_catalog = {
            "M0275": open_price,
            "M0276": high,
            "M0277": low,
            "M0278": raw_close,
            "M0279": pre_close,
            "M0280": pct,
            "M0281": volume,
            "M0282": amount,
            "M0283": turnover,
            "M0284": volume_ratio,
            "M0285": pe,
            "M0286": pb,
            "M0287": total_mv,
            "M0288": circ_mv,
            "M0290": qfq_close,
            "M0321": ret_5d,
            "M0322": ret_20d,
            "M0323": vol_20d,
            "M0324": mdd_20d,
        }
        _mark_live(module, *(catalog_id for catalog_id, value in conditional_catalog.items() if value is not None))

    if chart_series:
        module["charts"].append({
            "id": "stock_watchlist_120d",
            "title": "自选股相对表现（起点=100）",
            "kind": "line",
            "unit": "点",
            "series": chart_series,
            "source": f"{' / '.join(sorted(chart_sources))}；本地仅做起点归一化",
        })
    if return_heatmap:
        module["charts"].append({
            "id": "stock_return_heatmap_20d", "title": "自选股近20日收益热力图",
            "kind": "heatmap", "unit": "%", "series": return_heatmap,
            "source": "AKShare历史收盘价本地派生",
        })
    valid_20d = [row for row in rows if row.get("ret_20d") is not None]
    if valid_20d:
        ranking = sorted(valid_20d, key=lambda item: float(item["ret_20d"]), reverse=True)
        module["charts"].append({
            "id": "stock_return_ranking_20d", "title": "自选股20日收益排名",
            "kind": "bar", "unit": "%",
            "series": [{"name": f"{row['name']} {row['code']}", "data": [{"date": row["as_of"], "value": row["ret_20d"]}]} for row in ranking],
            "source": "AKShare历史收盘价本地派生",
        })
        risk_rows = [row for row in valid_20d if row.get("vol_20d") is not None]
        if risk_rows:
            module["charts"].append({
                "id": "stock_risk_return_20d", "title": "自选股风险—收益",
                "kind": "scatter", "unit": "%",
                "series": [{"name": f"{row['name']} {row['code']}", "data": [{"date": row["as_of"], "value": row["ret_20d"], "x": row["vol_20d"]}]} for row in risk_rows],
                "x_title": "20日年化波动率(%)", "y_title": "20日收益率(%)",
                "source": "AKShare历史收盘价本地派生",
            })
    if drawdown_series:
        module["charts"].append({
            "id": "stock_drawdown_20d", "title": "自选股20日滚动最大回撤",
            "kind": "line", "unit": "%", "series": drawdown_series,
            "source": "AKShare历史收盘价本地派生",
        })
    if rows:
        module["tables"].append({
            "id": "stock_watchlist",
            "title": "自选股行情与估值",
            "columns": [
                "code", "name", "open", "high", "low", "pre_close", "close", "close_basis", "qfq_close",
                "ret_1d", "ret_5d", "ret_20d", "vol_20d", "mdd_20d", "volume", "amount",
                "turnover", "volume_ratio", "pe", "pb", "total_mv", "circ_mv", "as_of", "source",
            ],
            "rows": rows,
        })
    return _finalize_module(module)

def _safe_text(value: Any, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _event_id(source: str, published: str, title: str, url: str) -> str:
    body = "|".join((source, published, title, url)).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:24]


def fetch_news_events(client: Any, now: datetime) -> dict[str, Any]:
    module = _module("news_events")
    symbols = [item.strip() for item in os.environ.get("DASHBOARD_STOCKS", "000001,600519,300750").split(",") if re.fullmatch(r"\d{6}", item.strip())]
    max_rows = max(10, min(int(os.environ.get("DASHBOARD_MAX_NEWS", "60")), 200))
    news_rows: list[dict[str, Any]] = []
    for symbol in symbols[:10]:
        frame = _attempt(module, client, "stock_news_em", symbol=symbol)
        title_col = _find_column(frame, ["新闻标题", "标题"])
        time_col = _find_column(frame, ["发布时间", "时间", "日期"])
        source_col = _find_column(frame, ["文章来源", "来源"])
        url_col = _find_column(frame, ["新闻链接", "链接", "url"])
        if title_col is None:
            continue
        for _, record in frame.head(max_rows).iterrows():
            title = _safe_text(record.get(title_col), 160)
            if not title:
                continue
            published = _safe_text(record.get(time_col), 40) if time_col is not None else ""
            source = _safe_text(record.get(source_col), 60) if source_col is not None else ""
            url = _safe_text(record.get(url_col), 500) if url_col is not None else ""
            news_rows.append({
                "event_id": _event_id(source, published, title, url),
                "event_type": "stock_news",
                "code": symbol,
                "title": title,
                "published_at": published,
                "source": source,
                "url": url,
            })
    calendar = _attempt(module, client, "news_economic_baidu", date=now.date().strftime("%Y%m%d"))
    if not calendar.empty:
        time_col = _find_column(calendar, ["日期", "时间", "date"])
        title_col = _find_column(calendar, ["事件", "标题", "内容", "经济数据"])
        region_col = _find_column(calendar, ["地区", "国家"])
        if title_col is not None:
            for _, record in calendar.head(max_rows).iterrows():
                title = _safe_text(record.get(title_col), 160)
                published = _safe_text(record.get(time_col), 40) if time_col is not None else now.date().isoformat()
                region = _safe_text(record.get(region_col), 40) if region_col is not None else ""
                if title:
                    news_rows.append({
                        "event_id": _event_id("Baidu economic calendar", published, title, ""),
                        "event_type": "macro_calendar",
                        "code": "",
                        "title": f"{region} {title}".strip(),
                        "published_at": published,
                        "source": "AKShare news_economic_baidu",
                        "url": "",
                    })
    begin = (now.date() - timedelta(days=2)).strftime("%Y%m%d")
    end = now.date().strftime("%Y%m%d")
    notice_rows: list[dict[str, Any]] = []
    notice_frames: list[tuple[str, pd.DataFrame]] = []
    for requested_security in symbols[:10]:
        frame = _attempt(
            module, client, "stock_individual_notice_report",
            security=requested_security, symbol="全部", begin_date=begin, end_date=end,
        )
        if not frame.empty:
            notice_frames.append((requested_security, frame))
    for requested_security, notices in notice_frames:
        code_col = _find_column(notices, ["代码", "证券代码"])
        name_col = _find_column(notices, ["名称", "简称", "证券简称"])
        title_col = _find_column(notices, ["公告标题", "标题"])
        type_col = _find_column(notices, ["公告类型", "类型"])
        time_col = _find_column(notices, ["公告日期", "公告时间", "日期"])
        url_col = _find_column(notices, ["网址", "公告链接", "链接"])
        if title_col is None:
            continue
        for _, record in notices.head(max_rows).iterrows():
            title = _safe_text(record.get(title_col), 160)
            if not title:
                continue
            published = _safe_text(record.get(time_col), 40) if time_col is not None else ""
            url = _safe_text(record.get(url_col), 500) if url_col is not None else ""
            notice_rows.append({
                "code": _safe_text(record.get(code_col), 20) if code_col is not None else requested_security,
                "name": _safe_text(record.get(name_col), 40) if name_col is not None else "",
                "title": title,
                "type": _safe_text(record.get(type_col), 60) if type_col is not None else "",
                "published_at": published,
                "url": url,
                "event_id": _event_id("company_notice", published, title, url),
            })
    # De-duplicate by stable content hash; never invent sentiment or summaries.
    news_rows = list({row["event_id"]: row for row in news_rows}.values())[:max_rows]
    notice_rows = list({row["event_id"]: row for row in notice_rows}.values())[:max_rows]
    if news_rows:
        module["tables"].append({
            "id": "news_feed",
            "title": "个股新闻与宏观日历",
            "columns": ["event_id", "event_type", "code", "title", "published_at", "source", "url"],
            "rows": news_rows,
        })
        module["kpis"].append(_kpi("news_count", "当期新闻/日历事件", len(news_rows), "条", None, now.date().isoformat(), "AKShare公开新闻与经济日历", "M0330"))
        _mark_live(module, "M0330", "M0331", "M0332", "M0333", "M0356", "M0357")
    if notice_rows:
        module["tables"].append({
            "id": "notice_feed",
            "title": "公司公告",
            "columns": ["event_id", "code", "name", "title", "type", "published_at", "url"],
            "rows": notice_rows,
        })
        module["kpis"].append(_kpi("notice_count", "近两日公司公告", len(notice_rows), "条", None, now.date().isoformat(), "AKShare stock_individual_notice_report", "M0337"))
        _mark_live(module, "M0335", "M0336", "M0337", "M0338", "M0339", "M0340", "M0356", "M0357")

    normalized_events = [
        {"event_type": row.get("event_type") or "stock_news", "published_at": row.get("published_at"), "source": row.get("source") or "未知来源"}
        for row in news_rows
    ] + [
        {"event_type": "company_notice", "published_at": row.get("published_at"), "source": "公司公告"}
        for row in notice_rows
    ]
    by_type: dict[str, Counter[str]] = {}
    by_source: dict[str, Counter[str]] = {}
    for event in normalized_events:
        parsed = _parse_date(event.get("published_at"))
        if parsed is None:
            continue
        day = parsed.isoformat()
        event_type = str(event["event_type"])
        source = str(event["source"])
        by_type.setdefault(event_type, Counter())[day] += 1
        by_source.setdefault(source, Counter())[day] += 1
    type_chart_series: list[dict[str, Any]] = []
    source_chart_series: list[dict[str, Any]] = []
    all_days = sorted({day for counter in [*by_type.values(), *by_source.values()] for day in counter})
    for event_type, counts in sorted(by_type.items()):
        points = [{"date": day, "value": counts[day]} for day in sorted(counts)]
        item = _publish_series(
            module, identifier=f"events_type_{re.sub(r'[^a-z0-9]+', '_', event_type.casefold()).strip('_')}",
            label=f"{event_type}事件数", submodule="事件统计", unit="条", frequency="日",
            source="AKShare公开新闻、经济日历与公告", points=points,
        )
        if item["data"]:
            type_chart_series.append({"name": event_type, "data": item["data"]})
    for source, counts in sorted(by_source.items(), key=lambda pair: sum(pair[1].values()), reverse=True)[:12]:
        stable = hashlib.sha256(source.encode("utf-8")).hexdigest()[:10]
        points = [{"date": day, "value": counts[day]} for day in sorted(counts)]
        item = _publish_series(
            module, identifier=f"events_source_{stable}", label=f"{source}事件数",
            submodule="来源统计", unit="条", frequency="日", source=source, points=points,
        )
        if item["data"]:
            source_chart_series.append({"name": source, "data": item["data"]})
    if type_chart_series:
        module["charts"].append({
            "id": "news_event_daily_trend", "title": "新闻与事件数量趋势",
            "kind": "line", "unit": "条", "series": type_chart_series,
            "source": "AKShare公开新闻、经济日历与公告；按可解析发布日期聚合",
        })
        latest_day = all_days[-1]
        module["charts"].append({
            "id": "news_event_type_distribution", "title": "事件类型分布",
            "kind": "bar", "unit": "条",
            "series": [{"name": name, "data": [{"date": latest_day, "value": sum(counter.values())}]} for name, counter in sorted(by_type.items())],
            "source": "AKShare公开新闻、经济日历与公告；去重后聚合",
        })
    if source_chart_series and all_days:
        latest_day = all_days[-1]
        module["charts"].append({
            "id": "news_event_source_distribution", "title": "事件来源分布（前12）",
            "kind": "bar", "unit": "条",
            "series": [{"name": name, "data": [{"date": latest_day, "value": sum(counter.values())}]} for name, counter in sorted(by_source.items(), key=lambda pair: sum(pair[1].values()), reverse=True)[:12]],
            "source": "AKShare公开事件来源；空来源明确归入未知来源",
        })
    return _finalize_module(module)


FETCHERS = {
    "macro": fetch_macro,
    "global_markets": fetch_global_markets,
    "sw_industries": fetch_sw_industries,
    "commodities": fetch_commodities,
    "stock": fetch_stocks,
    "news_events": fetch_news_events,
}


def _strip_internal(module: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in module.items() if not key.startswith("_")}


def _stale_module(previous: Mapping[str, Any], reason: str) -> dict[str, Any]:
    module = copy.deepcopy(dict(previous))
    module["status"] = "stale"
    for series in module.get("series", []):
        if series.get("data"):
            series["status"] = "stale"
            series["stale_reason"] = reason
    for kpi in module.get("kpis", []):
        kpi["status"] = "stale"
        kpi["quality"] = "stale_cache"
    for chart in module.get("charts", []):
        chart["status"] = "stale" if any(series.get("data") for series in chart.get("series", [])) else "unavailable"
    for table in module.get("tables", []):
        table["status"] = "stale" if table.get("rows") else "unavailable"
    module.setdefault("alerts", []).append({"level": "warning", "message": reason})
    return module


def _merge_series_stale(
    current: MutableMapping[str, Any],
    previous: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Fallback at series granularity; a healthy sibling remains live."""

    result = copy.deepcopy(dict(current))
    if not previous:
        return result
    current_series = list(result.get("series", []))
    positions = {str(item.get("id")): index for index, item in enumerate(current_series) if item.get("id")}
    recovered = 0
    for old in previous.get("series", []):
        identifier = str(old.get("id") or "")
        if not identifier or not old.get("data"):
            continue
        position = positions.get(identifier)
        existing = current_series[position] if position is not None else None
        if existing is not None and existing.get("status") == "live" and existing.get("data"):
            continue
        stale = copy.deepcopy(dict(old))
        stale["status"] = "stale"
        stale["stale_reason"] = "本次该序列无有效新观测，保留上一期实际值"
        if position is None:
            positions[identifier] = len(current_series)
            current_series.append(stale)
        else:
            current_series[position] = stale
        recovered += 1
    result["series"] = current_series
    statuses = {item.get("status") for item in current_series if item.get("data")}
    if recovered:
        result.setdefault("alerts", []).append({
            "level": "warning",
            "message": f"{recovered} 条序列本次更新失败，已按序列ID回退上一期并标记 stale。",
        })
        if "live" in statuses:
            result["status"] = "partial"
        elif statuses == {"stale"}:
            result["status"] = "stale"
    return result


def _catalog_digest(catalog: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json_bytes(catalog)).hexdigest()


def load_catalog(path: Path | str) -> dict[str, Any]:
    catalog = load_json(path)
    if catalog is None:
        raise PipelineError("indicator catalog is missing")
    rows = catalog.get("rows")
    if not isinstance(rows, list) or len(rows) != CATALOG_ROWS_REQUIRED:
        raise PipelineError(f"indicator catalog must contain exactly {CATALOG_ROWS_REQUIRED} rows")
    ids = [row.get("id") for row in rows if isinstance(row, dict)]
    if len(ids) != CATALOG_ROWS_REQUIRED or any(not item for item in ids) or len(set(ids)) != len(ids):
        raise PipelineError("indicator catalog ids are missing or duplicated")
    return catalog


def _runtime_ids_from_module(module: Mapping[str, Any]) -> dict[str, str]:
    priority = {"unavailable": 0, "stale": 1, "live": 2}
    result: dict[str, str] = {}

    def record(catalog_id: Any, status: str) -> None:
        identifier = str(catalog_id or "")
        if not identifier or status not in priority:
            return
        current = result.get(identifier)
        if current is None or priority[status] > priority[current]:
            result[identifier] = status

    module_status = str(module.get("status") or "failed")
    for catalog_id in module.get("_live_catalog_ids", set()):
        record(catalog_id, "stale" if module_status == "stale" else "live")
    for series in module.get("series", []):
        status = str(series.get("status") or ("live" if series.get("data") else "unavailable"))
        record(series.get("catalog_id"), status)
    for kpi in module.get("kpis", []):
        if kpi.get("catalog_id") and _to_number(kpi.get("value")) is not None:
            record(kpi.get("catalog_id"), "stale" if kpi.get("status") == "stale" else "live")
    return result


def _build_catalog_status(
    catalog: Mapping[str, Any],
    runtime_by_module: Mapping[str, Mapping[str, str]],
    generated_at: str,
) -> list[dict[str, Any]]:
    reasons = {
        "live": "actual_observation_published",
        "stale": "last_valid_observation_retained_after_series_failure",
        "unavailable": "registered_source_returned_no_valid_observation",
        "metadata_only": "catalogued_but_no_runtime_output_registered",
    }
    result: list[dict[str, Any]] = []
    for row in catalog["rows"]:
        catalog_id = str(row["id"])
        module_key = CATALOG_MODULE_MAP.get(row.get("module"), "")
        runtime_status = runtime_by_module.get(module_key, {}).get(catalog_id, "metadata_only")
        result.append({
            "id": catalog_id,
            "variable": row.get("variable"),
            "module": module_key,
            "status": runtime_status,
            "reason": reasons[runtime_status],
            "checked_at": generated_at,
        })
    return result


def _snapshot_as_of(modules: Mapping[str, Mapping[str, Any]]) -> str | None:
    dates: list[str] = []
    for module in modules.values():
        for series in module.get("series", []):
            parsed = _parse_date(series.get("as_of"))
            if parsed:
                dates.append(parsed.isoformat())
        for kpi in module.get("kpis", []):
            parsed = _parse_date(kpi.get("as_of"))
            if parsed:
                dates.append(parsed.isoformat())
        for table in module.get("tables", []):
            for row in table.get("rows", []):
                for key in ("as_of", "published_at"):
                    parsed = _parse_date(row.get(key))
                    if parsed:
                        dates.append(parsed.isoformat())
    return max(dates) if dates else None

def _overall_status(module_statuses: Sequence[str]) -> str:
    if all(status == "ok" for status in module_statuses):
        return "ok"
    if all(status == "failed" for status in module_statuses):
        return "failed"
    if all(status == "stale" for status in module_statuses):
        return "stale"
    return "partial"


def validate_snapshot(snapshot: Mapping[str, Any]) -> None:
    required = {"schema_version", "generated_at", "as_of", "status", "summary", "modules", "quality"}
    missing = required - set(snapshot)
    if missing:
        raise PipelineError(f"snapshot missing required fields: {sorted(missing)}")
    if snapshot["status"] not in VALID_STATUSES:
        raise PipelineError("snapshot status is invalid")
    modules = snapshot.get("modules")
    if not isinstance(modules, dict) or tuple(modules.keys()) != MODULE_KEYS:
        raise PipelineError("snapshot module keys/order do not match the public contract")
    for key, module in modules.items():
        for field in ("title", "status", "series", "kpis", "charts", "tables", "alerts"):
            if field not in module:
                raise PipelineError(f"module {key} missing field: {field}")
        if module["status"] not in VALID_STATUSES:
            raise PipelineError(f"module {key} has invalid status")
        series_ids: set[str] = set()
        series_fields = {"id", "label", "submodule", "unit", "frequency", "status", "source", "as_of", "data"}
        for item in module["series"]:
            if not series_fields.issubset(item):
                raise PipelineError(f"module {key} contains an incomplete public series")
            identifier = str(item.get("id") or "")
            if not identifier or identifier in series_ids:
                raise PipelineError(f"module {key} contains a missing or duplicated public series id")
            series_ids.add(identifier)
            if item["status"] not in {"live", "stale", "unavailable"}:
                raise PipelineError(f"module {key} contains an invalid public series status")
            if item["as_of"] is not None and _parse_date(item["as_of"]) is None:
                raise PipelineError(f"module {key} contains an invalid public series as_of")
            for point in item["data"]:
                if not isinstance(point, Mapping):
                    raise PipelineError(f"module {key} contains a malformed public series point")
                fields = set(point)
                allowed_shapes = {
                    frozenset(PUBLIC_SERIES_POINT_FIELDS),
                    frozenset(PUBLIC_SERIES_POINT_FIELDS | set(PUBLIC_SERIES_OHLC_FIELDS)),
                }
                if frozenset(fields) not in allowed_shapes:
                    raise PipelineError(f"module {key} contains a malformed public series point")
                if (
                    _parse_date(point["date"]) is None
                    or isinstance(point["value"], bool)
                    or _to_number(point["value"]) is None
                ):
                    raise PipelineError(f"module {key} contains an invalid public series point")
                if fields != PUBLIC_SERIES_POINT_FIELDS:
                    if any(
                        isinstance(point[field], bool) or _to_number(point[field]) is None
                        for field in PUBLIC_SERIES_OHLC_FIELDS
                    ):
                        raise PipelineError(f"module {key} contains an invalid public series OHLC point")
        for kpi in module["kpis"]:
            fields = {"id", "label", "value", "display", "unit", "change", "change_display", "as_of", "source", "quality", "status"}
            if not fields.issubset(kpi):
                raise PipelineError(f"module {key} contains an incomplete KPI")
        for chart in module["charts"]:
            if not {"id", "title", "kind", "unit", "series"}.issubset(chart):
                raise PipelineError(f"module {key} contains an incomplete chart")
            for series in chart["series"]:
                if not {"name", "data"}.issubset(series):
                    raise PipelineError(f"module {key} contains an incomplete chart series")
                for point in series["data"]:
                    if not {"date", "value"}.issubset(point) or _parse_date(point["date"]) is None or _to_number(point["value"]) is None:
                        raise PipelineError(f"module {key} contains an invalid chart point")
                    if "x" in point and _to_number(point["x"]) is None:
                        raise PipelineError(f"module {key} contains an invalid chart x value")
        for table in module["tables"]:
            if not {"id", "title", "columns", "rows"}.issubset(table):
                raise PipelineError(f"module {key} contains an incomplete table")
    catalog = snapshot.get("catalog")
    if not isinstance(catalog, dict) or len(catalog.get("rows", [])) != CATALOG_ROWS_REQUIRED:
        raise PipelineError("full 367-row catalog is not embedded in snapshot")
    runtime = snapshot.get("catalog_status")
    if not isinstance(runtime, list) or len(runtime) != CATALOG_ROWS_REQUIRED:
        raise PipelineError("catalog runtime status must cover all 367 rows")
    if {item.get("status") for item in runtime} - {"live", "stale", "unavailable", "metadata_only"}:
        raise PipelineError("catalog runtime status contains an unknown value")
    _json_bytes(snapshot)  # final finite-number and serialization gate


class DashboardPipeline:
    def __init__(
        self,
        *,
        catalog_path: Path | str,
        data_dir: Path | str,
        client: Any | None = None,
        now: datetime | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.catalog_path = Path(catalog_path)
        self.data_dir = Path(data_dir)
        self.snapshot_path = self.data_dir / "dashboard_snapshot.json"
        self.catalog_publish_path = self.data_dir / "indicator_catalog.json"
        self.history_dir = self.data_dir / "history"
        self.lock_path = self.data_dir / ".update.lock"
        self._client_instance = client
        self.now = now or _utc_now()
        if self.now.tzinfo is None:
            self.now = self.now.replace(tzinfo=timezone.utc)
        self.environ = os.environ if environ is None else environ

    def _client(self) -> Any:
        if self._client_instance is None:
            self._client_instance = OfflineClient() if _flag("DASHBOARD_OFFLINE", self.environ) else AkshareClient()
        return self._client_instance

    def _cache_is_fresh(self, snapshot: Mapping[str, Any], catalog: Mapping[str, Any], ttl_seconds: int) -> bool:
        if ttl_seconds <= 0 or snapshot.get("catalog_sha256") != _catalog_digest(catalog):
            return False
        try:
            generated = datetime.fromisoformat(str(snapshot["generated_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            return False
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        age = (self.now.astimezone(timezone.utc) - generated.astimezone(timezone.utc)).total_seconds()
        return 0 <= age < ttl_seconds

    def build_snapshot(
        self,
        catalog: Mapping[str, Any],
        previous: Mapping[str, Any] | None = None,
        selected_modules: set[str] | None = None,
    ) -> dict[str, Any]:
        selected = set(selected_modules or MODULE_KEYS)
        unknown = selected - set(MODULE_KEYS)
        if unknown:
            raise PipelineError(f"unknown dashboard modules: {sorted(unknown)}")
        if selected != set(MODULE_KEYS) and previous is None:
            raise PipelineError("partial module refresh requires a previous valid snapshot")

        client = self._client()
        modules: dict[str, dict[str, Any]] = {}
        runtime_by_module: dict[str, dict[str, str]] = {key: {} for key in MODULE_KEYS}
        error_registry: list[dict[str, str]] = []
        previous_modules = previous.get("modules", {}) if previous else {}
        if previous:
            for item in previous.get("catalog_status", []):
                module_key = str(item.get("module") or "")
                identifier = str(item.get("id") or "")
                status = str(item.get("status") or "")
                if module_key in runtime_by_module and identifier and status in {"live", "stale", "unavailable"}:
                    runtime_by_module[module_key][identifier] = status

        for key in MODULE_KEYS:
            prior = previous_modules.get(key)
            if key not in selected:
                modules[key] = copy.deepcopy(dict(prior))
                continue

            if key == "macro":
                fetched = fetch_macro(client, self.now, catalog.get("rows", []))
            else:
                fetched = FETCHERS[key](client, self.now)
            error_registry.extend({"module": key, **item} for item in fetched.get("_errors", []))

            if fetched.get("status") == "failed" and prior:
                has_prior = bool(
                    any(series.get("data") for series in prior.get("series", []))
                    or prior.get("kpis")
                    or prior.get("charts")
                    or prior.get("tables")
                )
                if has_prior:
                    merged = _stale_module(
                        prior,
                        "本次免费数据源整体失败，保留上次有效值并明确标记为 stale",
                    )
                else:
                    merged = _strip_internal(fetched)
            else:
                merged = _merge_series_stale(_strip_internal(fetched), prior)
            modules[key] = merged
            runtime_by_module[key] = _runtime_ids_from_module(merged)

        generated_at = _iso_datetime(self.now)
        catalog_status = _build_catalog_status(catalog, runtime_by_module, generated_at)
        module_statuses = [modules[key]["status"] for key in MODULE_KEYS]
        runtime_counts = Counter(item["status"] for item in catalog_status)
        snapshot: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at,
            "as_of": _snapshot_as_of(modules),
            "status": _overall_status(module_statuses),
            "summary": {
                "title": "六模块市场数据日更快照",
                "module_status_counts": dict(Counter(module_statuses)),
                "catalog_count": len(catalog["rows"]),
                "catalog_runtime_counts": dict(runtime_counts),
                "refresh_policy": "daily_full" if selected == set(MODULE_KEYS) else "partial_modules",
                "refreshed_modules": [key for key in MODULE_KEYS if key in selected],
                "fabricated_observations": 0,
            },
            "modules": modules,
            "quality": {
                "status": _overall_status(module_statuses),
                "source_errors": error_registry,
                "checks": {
                    "atomic_write": True,
                    "finite_numbers_only": True,
                    "catalog_rows": len(catalog["rows"]),
                    "failed_sources_replaced_with_placeholders": False,
                    "premium_sources": premium_source_state(self.environ),
                },
            },
            "catalog_sha256": _catalog_digest(catalog),
            "catalog": copy.deepcopy(dict(catalog)),
            "catalog_status": catalog_status,
        }
        validate_snapshot(snapshot)
        return snapshot

    def update(
        self,
        *,
        force: bool = False,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        history_keep: int = 30,
        modules: set[str] | None = None,
    ) -> dict[str, Any]:
        catalog = load_catalog(self.catalog_path)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with update_lock(self.lock_path):
            previous = load_json(self.snapshot_path)
            if previous is not None:
                try:
                    validate_snapshot(previous)
                except PipelineError:
                    previous = None
            if not force and not modules and previous is not None and self._cache_is_fresh(previous, catalog, ttl_seconds):
                cached = copy.deepcopy(previous)
                cached.setdefault("summary", {})["cache"] = "fresh_cache"
                return cached
            if modules and previous is None:
                raise PipelineError("partial module refresh requires a previous valid snapshot")
            snapshot = self.build_snapshot(catalog, previous, modules)
            atomic_write_json(self.catalog_publish_path, catalog)
            atomic_write_json(self.snapshot_path, snapshot)
            self.history_dir.mkdir(parents=True, exist_ok=True)
            stamp = self.now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            atomic_write_json(self.history_dir / f"snapshot_{stamp}.json", snapshot)
            history_files = sorted(self.history_dir.glob("snapshot_*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
            for obsolete in history_files[max(1, history_keep):]:
                obsolete.unlink(missing_ok=True)
            return snapshot


def resolve_catalog_path(project_dir: Path, data_dir: Path, explicit: Path | str | None = None) -> Path:
    candidates = [
        Path(explicit) if explicit else None,
        Path(os.environ["DASHBOARD_CATALOG_PATH"]) if os.environ.get("DASHBOARD_CATALOG_PATH") else None,
        project_dir.parent / "board" / "indicator_catalog.json",
        data_dir / "indicator_catalog.json",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.exists():
            return candidate
    raise PipelineError("no 367-row indicator catalog was found")


def run_update(
    *,
    project_dir: Path | str | None = None,
    data_dir: Path | str | None = None,
    catalog_path: Path | str | None = None,
    force: bool = False,
    ttl_seconds: int | None = None,
    client: Any | None = None,
    now: datetime | None = None,
    environ: Mapping[str, str] | None = None,
    modules: set[str] | None = None,
) -> dict[str, Any]:
    root = Path(project_dir) if project_dir else Path(__file__).resolve().parent
    output = Path(data_dir) if data_dir else Path(os.environ.get("DASHBOARD_DATA_DIR", root / "data"))
    catalog = resolve_catalog_path(root, output, catalog_path)
    ttl = ttl_seconds if ttl_seconds is not None else int(os.environ.get("DASHBOARD_CACHE_TTL_SECONDS", DEFAULT_TTL_SECONDS))
    pipeline = DashboardPipeline(catalog_path=catalog, data_dir=output, client=client, now=now, environ=environ)
    return pipeline.update(force=force, ttl_seconds=ttl, modules=modules)


def _status_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": snapshot.get("schema_version"),
        "generated_at": snapshot.get("generated_at"),
        "as_of": snapshot.get("as_of"),
        "status": snapshot.get("status"),
        "modules": {key: value.get("status") for key, value in snapshot.get("modules", {}).items()},
        "catalog_runtime_counts": snapshot.get("summary", {}).get("catalog_runtime_counts", {}),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update or validate the public dashboard data snapshot")
    parser.add_argument("command", choices=("update", "validate", "status"), nargs="?", default="update")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--catalog", type=Path, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--modules",
        action="append",
        default=[],
        help="Comma-separated module keys for a bounded partial refresh; requires a valid previous snapshot",
    )
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent
    data_dir = args.data_dir or Path(os.environ.get("DASHBOARD_DATA_DIR", root / "data"))
    if args.command == "update":
        requested_modules = {
            item.strip()
            for group in args.modules
            for item in group.split(",")
            if item.strip()
        }
        unknown = requested_modules - set(MODULE_KEYS)
        if unknown:
            raise PipelineError(f"unknown dashboard modules: {sorted(unknown)}")
        snapshot = run_update(
            project_dir=root,
            data_dir=data_dir,
            catalog_path=args.catalog,
            force=args.force,
            modules=requested_modules or None,
        )
    else:
        snapshot = load_json(data_dir / "dashboard_snapshot.json")
        if snapshot is None:
            raise PipelineError("dashboard_snapshot.json does not exist")
        validate_snapshot(snapshot)
    print(json.dumps(_status_payload(snapshot), ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
