"""Governed RQData long-sample asset panel exporter, schema v5.3.8.

This module is deliberately independent from every allocation solver and
stack.  It exports provider levels only; it does not estimate returns, select
models, or write production snapshots.

Three RQData research series are fixed and cannot be overridden:

* H00300.INDX -- CSI 300 total-return index;
* H11006.XSHG -- CSI government-bond index with interest/reinvestment, not the
  ChinaBond CBA00601 wealth index and not the H01006 net-price index;
* AU9999.SGEX -- Shanghai Gold Exchange Au99.99 RMB spot price.

RQData does not provide an admitted ex-precious-metals commodity total-return
series for this project.  The exporter therefore fails closed unless the
caller supplies a separately audited D3 series satisfying the explicit input
contract below.  Back-adjusted continuous futures, commodity ETFs, and broad
indices containing gold or other precious metals are rejected.

No runtime authentication material is read by this module, accepted as an
argument, written to disk, included in hashes, or printed.  RQData runtime
initialisation is delegated to the installed SDK and its console output is
discarded.
"""

from __future__ import annotations

import argparse
import calendar
import contextlib
import hashlib
import importlib
import io
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "rqdata-asset-panel-v5.3.8"
EXPORTER_VERSION = "5.3.8"
DEFAULT_START_DATE = "2013-01-01"
COMMODITY_INPUT_SCHEMA = "audited-ex-precious-metals-commodity-v1"
MAX_COMMODITY_INPUT_BYTES = 5 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class AssetPanelError(RuntimeError):
    """A safe, credential-free exporter failure."""

    code = "asset_panel_error"

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail
        message = self.code if detail is None else f"{self.code}:{detail}"
        super().__init__(message)


class MissingAuditedCommoditySeries(AssetPanelError):
    code = "audited_ex_precious_metals_commodity_series_required"


class CommodityAuditError(AssetPanelError):
    code = "commodity_series_audit_contract_failed"


class ProviderAccessError(AssetPanelError):
    code = "rqdata_provider_access_failed"


class ProviderQueryError(AssetPanelError):
    code = "rqdata_provider_query_failed"


class CoverageError(AssetPanelError):
    code = "asset_panel_monthly_coverage_failed"


@dataclass(frozen=True)
class ResearchSeries:
    asset: str
    code: str
    output_field: str
    provider_name: str
    level_semantics: str
    currency: str
    governance_note: str
    official_reference: str


RQDATA_RESEARCH_SERIES_V538: tuple[ResearchSeries, ...] = (
    ResearchSeries(
        asset="equity",
        code="H00300.INDX",
        output_field="equity_total_return_level",
        provider_name="沪深300全收益指数",
        level_semantics="pre_tax_cash_dividend_reinvestment_total_return_index",
        currency="CNY",
        governance_note="RQData D2 candidate; requires independent primary-source reconciliation before D3",
        official_reference="https://www.ricequant.com/doc/rqdata/python/indices-mod",
    ),
    ResearchSeries(
        asset="government_bond",
        code="H11006.XSHG",
        output_field="government_bond_reinvestment_level",
        provider_name="中证国债指数",
        level_semantics="government_bond_interest_and_reinvestment_index",
        currency="CNY",
        governance_note=(
            "not H01006 net-price and not ChinaBond CBA00601 wealth; issuer methodology "
            "and independent primary-source code mapping must be reconciled before D3"
        ),
        official_reference=(
            "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/"
            "detail/files/zh_CN/H11006factsheet.pdf"
        ),
    ),
    ResearchSeries(
        asset="gold",
        code="AU9999.SGEX",
        output_field="rmb_gold_spot_level",
        provider_name="上海黄金交易所Au99.99",
        level_semantics="rmb_per_gram_spot_price_without_collateral_income",
        currency="CNY",
        governance_note="spot-price return only; 518880 is an execution proxy, not this research truth series",
        official_reference=(
            "https://sge.com.cn/h5_cpfw/xhsph_xq?cplx=7&parent_cplx=0&"
            "pro_id=793730879941324800"
        ),
    ),
)

_EXPECTED_CODES = tuple(series.code for series in RQDATA_RESEARCH_SERIES_V538)
_COMMODITY_OUTPUT_FIELD = "commodity_ex_precious_metals_total_return_level"
_ALLOWED_COMMODITY_CONSTRUCTIONS = {
    "issuer_ex_precious_metals_total_return_index",
    "t_minus_1_real_contract_self_financing",
}
_ALLOWED_COMMODITY_RETURN_SEMANTICS = {
    "total_return",
    "fully_collateralized_total_return",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_end_date() -> str:
    first_this_month = date.today().replace(day=1)
    return (first_this_month - timedelta(days=1)).isoformat()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _records(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    reset = frame.reset_index() if hasattr(frame, "reset_index") else frame
    if isinstance(reset, Sequence) and not isinstance(reset, (str, bytes, bytearray)):
        raw_rows = list(reset)
    elif hasattr(reset, "to_dict"):
        raw_rows = reset.to_dict(orient="records")
    else:
        raise ProviderQueryError("unsupported_provider_frame")
    records: list[dict[str, Any]] = []
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            raise ProviderQueryError("provider_row_is_not_mapping")
        records.append({str(key): _json_value(value) for key, value in raw.items()})
    return records


def _parse_iso_date(value: Any, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if len(text) >= 10:
        text = text[:10]
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise AssetPanelError(f"invalid_{field}") from exc


def _month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _expected_months(start: date, end: date) -> list[str]:
    cursor = start.replace(day=1)
    final = end.replace(day=1)
    output: list[str] = []
    while cursor <= final:
        output.append(_month_key(cursor))
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return output


def _date_from_record(row: Mapping[str, Any]) -> date:
    for field in ("date", "datetime", "trade_date", "observation_date", "index"):
        if row.get(field) not in (None, ""):
            return _parse_iso_date(row[field], "observation_date")
    raise AssetPanelError("observation_date_missing")


def _finite_positive_level(row: Mapping[str, Any], fields: Iterable[str]) -> float:
    value: Any = None
    for field in fields:
        if row.get(field) is not None:
            value = row[field]
            break
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AssetPanelError("index_level_missing_or_invalid") from exc
    if not math.isfinite(number) or number <= 0:
        raise AssetPanelError("index_level_not_finite_positive")
    return number


def _to_monthly_levels(
    rows: Iterable[Mapping[str, Any]],
    *,
    value_fields: Iterable[str] = ("close",),
) -> list[dict[str, Any]]:
    selected: dict[str, tuple[date, float]] = {}
    for raw in rows:
        row = dict(raw)
        observation_date = _date_from_record(row)
        level = _finite_positive_level(row, value_fields)
        key = _month_key(observation_date)
        incumbent = selected.get(key)
        if incumbent is None or observation_date >= incumbent[0]:
            selected[key] = (observation_date, level)
    monthly: list[dict[str, Any]] = []
    for key in sorted(selected):
        observation_date, level = selected[key]
        monthly.append(
            {
                "month": key,
                "month_end": _month_end(observation_date).isoformat(),
                "observation_date": observation_date.isoformat(),
                "level": level,
            }
        )
    return monthly


def _assert_complete_months(
    alias: str,
    monthly: Sequence[Mapping[str, Any]],
    expected: Sequence[str],
) -> None:
    actual = [str(row.get("month")) for row in monthly]
    if actual != list(expected):
        missing = sorted(set(expected).difference(actual))
        extra = sorted(set(actual).difference(expected))
        detail = f"{alias}:missing={','.join(missing[:6]) or 'none'}:extra={','.join(extra[:6]) or 'none'}"
        raise CoverageError(detail)


def _instrument_metadata(rqdatac: Any, code: str) -> dict[str, Any]:
    try:
        instrument = rqdatac.instruments(code)
    except Exception:
        raise ProviderQueryError(f"instrument_metadata:{code}") from None
    if instrument is None:
        raise ProviderQueryError(f"instrument_unknown:{code}")
    fields = (
        "order_book_id",
        "symbol",
        "type",
        "listed_date",
        "de_listed_date",
        "currency",
        "exchange",
        "underlying_symbol",
        "contract_multiplier",
        "base_date",
        "base_point",
    )
    return {field: _json_value(getattr(instrument, field, None)) for field in fields}


def _load_rqdata_safely(rqdatac_module: Any | None) -> Any:
    try:
        rqdatac = rqdatac_module or importlib.import_module("rqdatac")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rqdatac.init()
    except Exception:
        raise ProviderAccessError() from None
    return rqdatac


def _query_rq_series(
    rqdatac: Any,
    definition: ResearchSeries,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    query = {
        "provider": "RQData",
        "operation": "get_price",
        "code": definition.code,
        "start_date": start_date,
        "end_date": end_date,
        "frequency": "1d",
        "fields": ["close"],
        "adjust_type": "none",
        "skip_suspended": False,
        "monthly_selector": "last_available_observation_in_calendar_month",
        "exporter_schema": SCHEMA_VERSION,
    }
    try:
        frame = rqdatac.get_price(
            definition.code,
            start_date=start_date,
            end_date=end_date,
            frequency="1d",
            fields=["close"],
            adjust_type="none",
            skip_suspended=False,
            expect_df=True,
        )
        daily = _records(frame)
    except AssetPanelError:
        raise
    except Exception:
        raise ProviderQueryError(definition.asset) from None
    monthly = _to_monthly_levels(daily)
    return {
        "asset": definition.asset,
        "code": definition.code,
        "output_field": definition.output_field,
        "provider_name": definition.provider_name,
        "level_semantics": definition.level_semantics,
        "currency": definition.currency,
        "governance_grade": "D2_candidate_not_D3",
        "governance_note": definition.governance_note,
        "official_reference": definition.official_reference,
        "instrument_metadata": _instrument_metadata(rqdatac, definition.code),
        "query": query,
        "query_sha256": _canonical_hash(query),
        "daily_observation_count": len(daily),
        "monthly_observation_count": len(monthly),
        "monthly_content_sha256": _canonical_hash(monthly),
        "rows": monthly,
    }


def load_audited_commodity_input(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        if source.stat().st_size > MAX_COMMODITY_INPUT_BYTES:
            raise CommodityAuditError("input_file_too_large")
        payload = json.loads(source.read_text(encoding="utf-8"))
    except CommodityAuditError:
        raise
    except (OSError, json.JSONDecodeError):
        raise CommodityAuditError("input_file_unreadable") from None
    if not isinstance(payload, Mapping):
        raise CommodityAuditError("input_root_not_object")
    return dict(payload)


def _require_sha256(value: Any, field: str) -> str:
    text = str(value or "")
    if not _SHA256_RE.fullmatch(text):
        raise CommodityAuditError(f"{field}_must_be_sha256")
    return text.lower()


def validate_audited_commodity_input(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate and normalize a D3 ex-precious-metals commodity series.

    The strict contract is intentional.  A caller cannot relabel a broad
    commodity index or an ETF proxy and silently pass the gate.
    """

    if payload is None:
        raise MissingAuditedCommoditySeries()
    if payload.get("schema_version") != COMMODITY_INPUT_SCHEMA:
        raise CommodityAuditError("schema_version")
    if payload.get("asset_class") != "commodity_ex_precious_metals":
        raise CommodityAuditError("asset_class")
    provider = str(payload.get("provider") or "").strip()
    series_id = str(payload.get("series_id") or "").strip()
    retrieved_at = str(payload.get("retrieved_at") or "").strip()
    if not provider or not series_id or not retrieved_at:
        raise CommodityAuditError("provenance_fields")
    query_hash = _require_sha256(payload.get("query_sha256"), "query_sha256")

    methodology = payload.get("methodology")
    if not isinstance(methodology, Mapping):
        raise CommodityAuditError("methodology")
    if methodology.get("construction_type") not in _ALLOWED_COMMODITY_CONSTRUCTIONS:
        raise CommodityAuditError("construction_type")
    if methodology.get("return_semantics") not in _ALLOWED_COMMODITY_RETURN_SEMANTICS:
        raise CommodityAuditError("return_semantics")
    if str(methodology.get("version") or "").strip() == "":
        raise CommodityAuditError("methodology_version")
    for zero_weight in ("gold_weight", "precious_metals_weight"):
        try:
            value = float(methodology.get(zero_weight))
        except (TypeError, ValueError):
            raise CommodityAuditError(zero_weight) from None
        if not math.isfinite(value) or abs(value) > 1e-12:
            raise CommodityAuditError(zero_weight)
    required_true = (
        "gold_excluded",
        "precious_metals_excluded",
        "t_minus_1_information_only",
        "fully_collateralized",
    )
    for field in required_true:
        if methodology.get(field) is not True:
            raise CommodityAuditError(field)
    if methodology.get("back_adjusted_continuous_prices_used_for_pnl") is not False:
        raise CommodityAuditError("back_adjusted_continuous_prices_used_for_pnl")

    audit = payload.get("audit")
    if not isinstance(audit, Mapping) or audit.get("status") != "D3":
        raise CommodityAuditError("audit_status")
    evidence_hash = _require_sha256(audit.get("evidence_sha256"), "evidence_sha256")

    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise CommodityAuditError("rows")
    content_hash = _require_sha256(payload.get("content_sha256"), "content_sha256")
    if _canonical_hash(raw_rows) != content_hash:
        raise CommodityAuditError("content_sha256_mismatch")
    monthly = _to_monthly_levels(raw_rows, value_fields=("level", "close", "index_level"))
    return {
        "asset": "commodity_ex_precious_metals",
        "code": series_id,
        "output_field": _COMMODITY_OUTPUT_FIELD,
        "provider": provider,
        "retrieved_at": retrieved_at,
        "governance_grade": "D3_input_attested",
        "query_sha256": query_hash,
        "input_content_sha256": content_hash,
        "audit_evidence_sha256": evidence_hash,
        "methodology": {str(key): _json_value(value) for key, value in methodology.items()},
        "monthly_observation_count": len(monthly),
        "monthly_content_sha256": _canonical_hash(monthly),
        "rows": monthly,
    }


def _validate_window(start_date: str, end_date: str) -> tuple[date, date]:
    start = _parse_iso_date(start_date, "start_date")
    end = _parse_iso_date(end_date, "end_date")
    if start > end:
        raise AssetPanelError("start_after_end")
    if start > date(2013, 1, 1):
        raise AssetPanelError("long_sample_must_start_no_later_than_2013_01_01")
    if end != _month_end(end):
        raise AssetPanelError("end_date_must_be_calendar_month_end")
    return start, end


def collect_rqdata_asset_panel_v538(
    start_date: str,
    end_date: str,
    *,
    audited_commodity: Mapping[str, Any] | None,
    rqdatac_module: Any | None = None,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    """Collect a governed four-asset month-end level panel.

    Commodity validation deliberately runs before RQData initialisation so a
    missing or contaminated commodity input causes zero provider queries.
    """

    start, end = _validate_window(start_date, end_date)
    commodity = validate_audited_commodity_input(audited_commodity)
    expected = _expected_months(start, end)
    commodity_rows = [
        row for row in commodity["rows"] if start <= _parse_iso_date(row["month_end"], "month_end") <= end
    ]
    _assert_complete_months("commodity_ex_precious_metals", commodity_rows, expected)
    commodity["rows"] = commodity_rows
    commodity["monthly_observation_count"] = len(commodity_rows)
    commodity["monthly_content_sha256"] = _canonical_hash(commodity_rows)

    rqdatac = _load_rqdata_safely(rqdatac_module)
    rq_series: dict[str, dict[str, Any]] = {}
    for definition in RQDATA_RESEARCH_SERIES_V538:
        result = _query_rq_series(rqdatac, definition, start_date, end_date)
        _assert_complete_months(definition.asset, result["rows"], expected)
        rq_series[definition.asset] = result

    row_maps = {
        asset: {row["month"]: row for row in result["rows"]}
        for asset, result in rq_series.items()
    }
    commodity_map = {row["month"]: row for row in commodity_rows}
    panel: list[dict[str, Any]] = []
    for month in expected:
        row: dict[str, Any] = {
            "month": month,
            "month_end": commodity_map[month]["month_end"],
            "observation_dates": {},
        }
        for definition in RQDATA_RESEARCH_SERIES_V538:
            source_row = row_maps[definition.asset][month]
            row[definition.output_field] = source_row["level"]
            row["observation_dates"][definition.asset] = source_row["observation_date"]
        row[_COMMODITY_OUTPUT_FIELD] = commodity_map[month]["level"]
        row["observation_dates"]["commodity_ex_precious_metals"] = commodity_map[month][
            "observation_date"
        ]
        panel.append(row)

    definitions = [
        {
            "asset": item.asset,
            "code": item.code,
            "output_field": item.output_field,
            "provider_name": item.provider_name,
            "level_semantics": item.level_semantics,
            "currency": item.currency,
            "governance_note": item.governance_note,
            "official_reference": item.official_reference,
        }
        for item in RQDATA_RESEARCH_SERIES_V538
    ]
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "exporter_version": EXPORTER_VERSION,
        "provider": "RQData",
        "retrieved_at": retrieved_at or _utc_now(),
        "date_window": {"start": start_date, "end": end_date},
        "frequency": "calendar_month_end_last_available_observation",
        "rqdata_fixed_codes": list(_EXPECTED_CODES),
        "series_definition_sha256": _canonical_hash(definitions),
        "series_definitions": definitions,
        "source_series": rq_series,
        "commodity_source": commodity,
        "panel": panel,
        "panel_content_sha256": _canonical_hash(panel),
        "panel_policy": {
            "join": "strict_complete_month_intersection",
            "month_end": "calendar month tagged with last real provider observation",
            "levels_not_returns": True,
            "commodity_direct_rqdata_substitution_allowed": False,
            "broad_commodity_with_precious_metals_allowed": False,
            "execution_etf_substitution_allowed": False,
            "downstream_signal_policy": "allocation signals must lag all month-end observations",
        },
        "governance": {
            "rqdata_series_grade": "D2_candidate_not_D3",
            "commodity_input_grade": "D3_attested_by_input_contract",
            "production_warning": (
                "RQData research series require independent primary-source reconciliation and "
                "lineage evidence before production D3 admission"
            ),
            "credentials_collected": False,
            "credentials_stored": False,
        },
    }
    return payload


def _atomic_json(payload: Mapping[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(destination)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=_default_end_date())
    parser.add_argument("--commodity-input", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        commodity = load_audited_commodity_input(args.commodity_input)
        payload = collect_rqdata_asset_panel_v538(
            args.start_date,
            args.end_date,
            audited_commodity=commodity,
        )
        destination = Path(args.output).resolve()
        _atomic_json(payload, destination)
        print(
            json.dumps(
                {
                    "status": "ok",
                    "schema_version": SCHEMA_VERSION,
                    "output": str(destination),
                    "months": len(payload["panel"]),
                    "panel_content_sha256": payload["panel_content_sha256"],
                    "credentials_stored": False,
                },
                ensure_ascii=False,
                allow_nan=False,
            )
        )
        return 0
    except AssetPanelError as exc:
        print(
            json.dumps({"status": "error", "code": exc.code, "detail": exc.detail}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    except Exception:
        print(json.dumps({"status": "error", "code": "asset_panel_export_failed"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
