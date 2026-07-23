from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app import MODULE_META, create_app  # noqa: E402


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def sample_snapshot() -> dict:
    modules = {
        key: {
            "title": meta["title"],
            "status": "ok",
            "as_of": "2026-07-10",
            "kpis": [],
            "charts": [],
            "tables": [],
            "alerts": [],
        }
        for key, meta in MODULE_META.items()
    }
    modules["macro"].update(
        {
            "series": [
                {
                    "id": "macro.rate",
                    "catalog_id": "M0001",
                    "label": "测试利率序列",
                    "submodule": "流动性",
                    "unit": "%",
                    "frequency": "日",
                    "status": "live",
                    "source": "offline-fixture",
                    "as_of": "2026-07-10",
                    "data": [
                        {"date": "2026-07-08", "value": 1.0, "open": 0.9, "high": 1.1, "low": 0.8, "close": 1.0},
                        {"date": "2026-07-09", "value": 1.2, "open": 1.0, "high": 1.3, "low": 0.95, "close": 1.2},
                        {"date": "2026-07-10", "value": 1.5, "open": 1.2, "high": 1.6, "low": 1.1, "close": 1.5, "vendor_flag": "drop-me"},
                    ],
                },
                {
                    "id": "macro.benchmark",
                    "label": "测试基准序列",
                    "unit": "%",
                    "frequency": "日",
                    "status": "live",
                    "source": "offline-fixture",
                    "as_of": "2026-07-10",
                    "data": [
                        {"date": "2026-07-08", "value": 2.0},
                        {"date": "2026-07-09", "value": 2.2},
                        {"date": "2026-07-10", "value": 2.5},
                    ],
                },
            ],
            "kpis": [
                {
                    "id": "macro-rate",
                    "label": "测试利率",
                    "value": 1.23,
                    "display": "1.23",
                    "unit": "%",
                    "change": -0.01,
                    "change_display": "-1 bp",
                    "as_of": "2026-07-10",
                    "source": "offline-fixture",
                    "quality": "passed",
                    "status": "ok",
                }
            ],
            "charts": [
                {
                    "id": "macro-line",
                    "title": "离线折线",
                    "kind": "line",
                    "unit": "%",
                    "status": "ok",
                    "series": [
                        {
                            "name": "序列A",
                            "data": [
                                {"date": "2026-07-09", "value": 1.20},
                                {"date": "2026-07-10", "value": 1.23},
                            ],
                        }
                    ],
                }
            ],
            "tables": [
                {
                    "id": "macro-table",
                    "title": "离线明细",
                    "columns": ["名称", "数值"],
                    "rows": [{"名称": "样本", "数值": 1.23}],
                    "status": "ok",
                }
            ],
        }
    )
    modules["stock"]["stocks"] = {
        "000001.SZ": {
            "title": "离线证券样本",
            "status": "ok",
            "as_of": "2026-07-10",
            "kpis": [{"id": "close", "label": "收盘价", "value": 9.2, "display": "9.20", "status": "ok"}],
            "charts": [],
            "tables": [],
            "alerts": [],
        }
    }
    return {
        "schema_version": "1.0",
        "generated_at": "2026-07-11T08:30:00+08:00",
        "as_of": "2026-07-10",
        "status": "ok",
        "summary": {"message": "offline test fixture"},
        "modules": modules,
        "quality": {"status": "passed"},
        "catalog_status": [{"id": "M0001", "module": "macro", "status": "live"}],
    }


@pytest.fixture
def data_dir(tmp_path: Path, sample_snapshot: dict) -> Path:
    write_json(tmp_path / "dashboard_snapshot.json", sample_snapshot)
    write_json(
        tmp_path / "indicator_catalog.json",
        {"schema_version": "1.0", "as_of": "2026-07-11", "rows": [{"id": "M0001", "module": "宏观"}]},
    )
    return tmp_path


@pytest.fixture
def app(data_dir: Path):
    return create_app(
        {
            "TESTING": True,
            "DATA_DIR": str(data_dir),
            "MAX_CONTENT_LENGTH": 1024,
            "MAX_JSON_BYTES": 1024 * 1024,
            "EXPECTED_CATALOG_ROWS": 1,
            "MAX_SNAPSHOT_AGE_SECONDS": 10**12,
            "MIN_TOTAL_LIVE_CATALOG": 0,
            "CORE_MIN_LIVE_BY_MODULE": {},
            "DAILY_AS_OF_MAX_AGE_DAYS": {},
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()


def assert_security_headers(response) -> None:
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "https://cdn.plot.ly" in response.headers["Content-Security-Policy"]
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"


def test_index_contains_six_modules_and_no_javascript_status_fallback(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    for label in ["宏观", "全球市场", "申万行业", "大宗商品", "个股", "新闻事件"]:
        assert label in text
    assert "关键状态（无需 JavaScript）" in text
    assert "2026-07-10" in text
    assert "DATA_DIR" not in text
    assert str(PROJECT_DIR) not in text
    assert_security_headers(response)


def test_snapshot_api_returns_fixed_modules_and_normalised_contract(client) -> None:
    response = client.get("/api/v1/snapshot")
    assert response.status_code == 200
    payload = response.get_json()
    assert list(payload["modules"]) == list(MODULE_META)
    kpi = payload["modules"]["macro"]["kpis"][0]
    assert set(kpi) == {
        "id",
        "label",
        "value",
        "display",
        "unit",
        "change",
        "change_display",
        "as_of",
        "source",
        "quality",
        "status",
    }
    assert kpi["value"] == 1.23
    series = payload["modules"]["macro"]["series"][0]
    assert series["id"] == "macro.rate"
    assert series["catalog_id"] == "M0001"
    assert series["status"] == "live"
    assert series["data"][-1] == {
        "date": "2026-07-10",
        "value": 1.5,
        "open": 1.2,
        "high": 1.6,
        "low": 1.1,
        "close": 1.5,
    }
    assert payload["modules"]["macro"]["charts"][0]["series"][0]["data"][-1]["value"] == 1.23
    assert response.headers["Cache-Control"] == "no-store"


def test_snapshot_metadata_view_omits_large_point_arrays_and_charts(client) -> None:
    full = client.get("/api/v1/snapshot")
    metadata = client.get("/api/v1/snapshot?view=metadata")

    assert full.status_code == 200
    assert metadata.status_code == 200
    payload = metadata.get_json()
    assert list(payload["modules"]) == list(MODULE_META)
    assert "charts" not in payload["modules"]["macro"]
    assert "data" not in payload["modules"]["macro"]["series"][0]
    assert "points" not in payload["modules"]["macro"]["series"][0]
    assert payload["modules"]["macro"]["tables"] == full.get_json()["modules"]["macro"]["tables"]
    assert len(metadata.data) < len(full.data)


def test_missing_module_and_kpi_fields_are_unavailable_not_fabricated(tmp_path: Path) -> None:
    write_json(
        tmp_path / "dashboard_snapshot.json",
        {
            "schema_version": "1.0",
            "generated_at": None,
            "as_of": None,
            "modules": {"macro": {"kpis": [{"id": "missing-value", "label": "缺失指标"}]}},
        },
    )
    write_json(tmp_path / "indicator_catalog.json", {"rows": []})
    client = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)}).test_client()
    payload = client.get("/api/v1/snapshot").get_json()
    assert payload["status"] == "failed"
    assert payload["modules"]["global_markets"]["status"] == "unavailable"
    kpi = payload["modules"]["macro"]["kpis"][0]
    assert kpi["value"] is None
    assert kpi["display"] == "暂无数据"
    assert kpi["status"] == "unavailable"


def test_catalog_endpoint_preserves_catalog_rows(client) -> None:
    response = client.get("/api/v1/catalog")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["rows"] == [{"id": "M0001", "module": "宏观"}]


def test_healthz_checks_both_artifacts(client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["snapshot_status"] == "ok"
    assert payload["catalog_rows"] == 1
    assert payload["coverage"]["live"] == 1
    assert payload["failures"] == []
    assert_security_headers(response)
    livez = client.get("/livez")
    assert livez.status_code == 200
    assert livez.get_json()["status"] == "ok"
    assert livez.get_json()["files"] == {"snapshot": "readable", "catalog": "readable"}
    assert livez.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    ("snapshot_status", "expected_state"),
    [("failed", "snapshot_failed"), ("stale", "snapshot_stale")],
)
def test_healthz_rejects_failed_or_stale_snapshot_but_livez_stays_up(
    tmp_path: Path, sample_snapshot: dict, snapshot_status: str, expected_state: str
) -> None:
    sample_snapshot["status"] = snapshot_status
    write_json(tmp_path / "dashboard_snapshot.json", sample_snapshot)
    write_json(tmp_path / "indicator_catalog.json", {"rows": [{"id": "M0001"}]})
    test_client = create_app({
        "TESTING": True,
        "DATA_DIR": str(tmp_path),
        "EXPECTED_CATALOG_ROWS": 1,
        "MAX_SNAPSHOT_AGE_SECONDS": 10**12,
    }).test_client()

    health = test_client.get("/healthz")
    assert health.status_code == 503
    assert health.get_json()["data_state"] == expected_state
    assert test_client.get("/livez").status_code == 200


def test_healthz_rejects_expired_and_invalid_generation_time(tmp_path: Path, sample_snapshot: dict) -> None:
    write_json(tmp_path / "indicator_catalog.json", {"rows": [{"id": "M0001"}]})
    for generated_at, expected_state in [
        ("2020-01-01T00:00:00Z", "snapshot_expired"),
        ("not-a-timestamp", "generated_at_invalid"),
    ]:
        sample_snapshot["generated_at"] = generated_at
        write_json(tmp_path / "dashboard_snapshot.json", sample_snapshot)
        test_client = create_app({
            "TESTING": True,
            "DATA_DIR": str(tmp_path),
            "EXPECTED_CATALOG_ROWS": 1,
            "MAX_SNAPSHOT_AGE_SECONDS": 60,
        }).test_client()
        response = test_client.get("/healthz")
        assert response.status_code == 503
        assert response.get_json()["data_state"] == expected_state


def test_healthz_requires_exact_catalog_count_but_livez_only_checks_readability(tmp_path: Path, sample_snapshot: dict) -> None:
    write_json(tmp_path / "dashboard_snapshot.json", sample_snapshot)
    write_json(tmp_path / "indicator_catalog.json", {"rows": []})
    test_client = create_app({
        "TESTING": True,
        "DATA_DIR": str(tmp_path),
        "EXPECTED_CATALOG_ROWS": 1,
        "MAX_SNAPSHOT_AGE_SECONDS": 10**12,
    }).test_client()
    response = test_client.get("/healthz")
    assert response.status_code == 503
    assert response.get_json()["data_state"] == "catalog_row_count_mismatch"
    livez = test_client.get("/livez")
    assert livez.status_code == 200
    assert livez.get_json()["catalog_rows"] == 0


def test_series_api_filters_dates_and_applies_transform(client) -> None:
    response = client.get(
        "/api/v1/series?ids=macro.rate&start=2026-07-09&end=2026-07-10&frequency=daily&transform=pct_change"
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["count"] == 1
    assert payload["series"][0]["unit"] == "%"
    assert payload["series"][0]["point_schema"] == "date_value"
    assert payload["series"][0]["ohlc_preserved"] is False
    assert payload["series"][0]["data"] == [{"date": "2026-07-10", "value": pytest.approx(25.0)}]


def test_series_api_preserves_ohlc_only_for_identity_frequency(client) -> None:
    raw_response = client.get("/api/v1/series?ids=macro.rate&frequency=raw")
    assert raw_response.status_code == 200
    raw_series = raw_response.get_json()["series"][0]
    assert raw_series["point_schema"] == "date_value_ohlc"
    assert raw_series["ohlc_preserved"] is True
    assert raw_series["data"][-1] == {
        "date": "2026-07-10",
        "value": 1.5,
        "open": 1.2,
        "high": 1.6,
        "low": 1.1,
        "close": 1.5,
    }

    daily_series = client.get(
        "/api/v1/series?ids=macro.rate&frequency=daily&transform=raw"
    ).get_json()["series"][0]
    assert daily_series["ohlc_preserved"] is True

    weekly_series = client.get(
        "/api/v1/series?ids=macro.rate&frequency=weekly&transform=raw"
    ).get_json()["series"][0]
    assert weekly_series["point_schema"] == "date_value"
    assert weekly_series["ohlc_preserved"] is False
    assert weekly_series["data"] == [{"date": "2026-07-10", "value": 1.5}]


def test_series_api_supports_module_selector_and_benchmark(client) -> None:
    module_response = client.get("/api/v1/series?module=macro")
    assert module_response.status_code == 200
    assert {item["id"] for item in module_response.get_json()["series"]} == {"macro.rate", "macro.benchmark"}

    response = client.get("/api/v1/series?ids=macro.rate&benchmark=macro.benchmark")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["query"]["benchmark"] == "macro.benchmark"
    assert payload["series"][0]["transform"] == "relative_to_benchmark"
    assert payload["series"][0]["ohlc_preserved"] is False
    assert payload["series"][0]["data"][0]["value"] == pytest.approx(0.0)
    assert payload["series"][0]["data"][-1]["value"] == pytest.approx(20.0)


@pytest.mark.parametrize(
    ("query", "state"),
    [
        ("ids=bad/id", "invalid_series_id"),
        ("ids=macro.rate&frequency=hourly", "invalid_frequency"),
        ("ids=macro.rate&transform=unknown", "invalid_transform"),
        ("ids=macro.rate&benchmark=macro.benchmark&transform=zscore", "benchmark_transform_conflict"),
        ("ids=macro.rate&unexpected=1", "unknown_parameter"),
        ("module=not-real", "invalid_module"),
        ("start=2026-07-10&end=2026-07-01&ids=macro.rate", "invalid_date_range"),
    ],
)
def test_series_api_rejects_invalid_or_ambiguous_queries(client, query: str, state: str) -> None:
    response = client.get("/api/v1/series?" + query)
    assert response.status_code == 400
    assert response.get_json()["data_state"] == state


def test_series_api_enforces_id_and_query_string_limits(app, client) -> None:
    app.config["MAX_SERIES_IDS"] = 1
    response = client.get("/api/v1/series?ids=macro.rate,macro.benchmark")
    assert response.status_code == 400
    assert response.get_json()["data_state"] == "too_many_series"
    app.config["MAX_QUERY_STRING_BYTES"] = 32
    response = client.get("/api/v1/series?ids=" + "a" * 100)
    assert response.status_code == 414
    assert response.get_json()["data_state"] == "query_string_too_large"


def test_coverage_api_aggregates_runtime_catalog_status(client) -> None:
    response = client.get("/api/v1/coverage")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["catalog_rows"] == 1
    assert payload["totals"] == {
        "total": 1,
        "live": 1,
        "stale": 0,
        "unavailable": 0,
        "metadata_only": 0,
        "observable": 1,
        "live_ratio": 1.0,
        "observable_ratio": 1.0,
    }
    assert payload["modules"]["macro"]["live"] == 1


def test_healthz_rejects_core_coverage_below_configured_minimum(app, client) -> None:
    app.config["MIN_TOTAL_LIVE_CATALOG"] = 2
    response = client.get("/healthz")
    assert response.status_code == 503
    assert "core_coverage_below_minimum" in response.get_json()["failures"]
    assert client.get("/livez").status_code == 200


def test_healthz_rejects_stale_daily_module_as_of(tmp_path: Path, sample_snapshot: dict) -> None:
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    sample_snapshot["generated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    sample_snapshot["modules"]["global_markets"]["as_of"] = (today - timedelta(days=10)).isoformat()
    write_json(tmp_path / "dashboard_snapshot.json", sample_snapshot)
    write_json(tmp_path / "indicator_catalog.json", {"rows": [{"id": "M0001", "module": "宏观"}]})
    test_client = create_app({
        "TESTING": True,
        "DATA_DIR": str(tmp_path),
        "EXPECTED_CATALOG_ROWS": 1,
        "MIN_TOTAL_LIVE_CATALOG": 0,
        "CORE_MIN_LIVE_BY_MODULE": {},
        "DAILY_AS_OF_MAX_AGE_DAYS": {"global_markets": 2},
    }).test_client()
    response = test_client.get("/healthz")
    assert response.status_code == 503
    assert "daily_as_of_stale:global_markets" in response.get_json()["failures"]
    assert test_client.get("/livez").status_code == 200


def test_stock_endpoint_returns_only_matching_snapshot_data(client) -> None:
    response = client.get("/api/v1/stock/000001.sz")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["code"] == "000001.SZ"
    assert payload["data"]["title"] == "离线证券样本"


def test_stock_endpoint_never_invents_missing_security(client) -> None:
    response = client.get("/api/v1/stock/600000.SH")
    assert response.status_code == 404
    payload = response.get_json()
    assert payload["status"] == "unavailable"
    assert payload["code"] == "600000.SH"
    assert "data" not in payload


@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/stock/%2e%2e",
        "/api/v1/stock/..%2Fdashboard_snapshot.json",
        "/api/v1/stock/bad%5Cpath",
        "/api/v1/stock/%00",
    ],
)
def test_stock_path_traversal_is_rejected(client, url: str) -> None:
    response = client.get(url)
    assert response.status_code in {400, 404}
    text = response.get_data(as_text=True)
    assert "offline test fixture" not in text
    assert "dashboard_snapshot.json" not in text


def test_warm_cache_does_not_serve_deleted_snapshot(app, client, data_dir: Path) -> None:
    assert client.get("/api/v1/snapshot").status_code == 200
    (data_dir / "dashboard_snapshot.json").unlink()
    response = client.get("/api/v1/snapshot")
    assert response.status_code == 503
    assert response.get_json()["data_state"] == "artifact_missing"


def test_warm_cache_does_not_serve_deleted_catalog(client, data_dir: Path) -> None:
    assert client.get("/api/v1/catalog").status_code == 200
    (data_dir / "indicator_catalog.json").unlink()
    response = client.get("/api/v1/catalog")
    assert response.status_code == 503
    assert response.get_json()["data_state"] == "artifact_missing"


@pytest.mark.parametrize(
    ("content", "expected_state"),
    [
        ("{not-json", "artifact_invalid_json"),
        ("[]", "artifact_invalid_shape"),
    ],
)
def test_invalid_snapshot_json_is_explicit_and_path_safe(tmp_path: Path, content: str, expected_state: str) -> None:
    (tmp_path / "dashboard_snapshot.json").write_text(content, encoding="utf-8")
    write_json(tmp_path / "indicator_catalog.json", {"rows": []})
    client = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)}).test_client()

    api_response = client.get("/api/v1/snapshot")
    assert api_response.status_code == 503
    assert api_response.get_json()["data_state"] == expected_state
    assert str(tmp_path) not in api_response.get_data(as_text=True)

    page_response = client.get("/")
    assert page_response.status_code == 200
    page = page_response.get_data(as_text=True)
    assert "当前无法读取数据快照" in page
    assert str(tmp_path) not in page


def test_invalid_catalog_shape_fails_health_and_catalog(tmp_path: Path, sample_snapshot: dict) -> None:
    write_json(tmp_path / "dashboard_snapshot.json", sample_snapshot)
    write_json(tmp_path / "indicator_catalog.json", {"rows": "not-a-list"})
    client = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)}).test_client()
    assert client.get("/api/v1/catalog").status_code == 503
    health = client.get("/healthz")
    assert health.status_code == 503
    assert health.get_json()["data_state"] == "catalog_invalid_shape"


def test_missing_files_return_service_unavailable_without_local_paths(tmp_path: Path) -> None:
    client = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)}).test_client()
    for url in ["/api/v1/snapshot", "/api/v1/catalog", "/healthz", "/livez"]:
        response = client.get(url)
        assert response.status_code == 503
        assert str(tmp_path) not in response.get_data(as_text=True)


def test_request_body_limit_is_enforced_before_route_dispatch(client) -> None:
    response = client.post("/api/v1/snapshot", data="x" * 2048, content_type="application/json")
    assert response.status_code == 413
    assert response.get_json()["status"] == "request_too_large"


def test_api_errors_are_json_and_receive_security_headers(client) -> None:
    response = client.get("/api/v1/not-a-real-route")
    assert response.status_code == 404
    assert response.is_json
    assert response.get_json()["status"] == "not_found"
    assert_security_headers(response)


def test_authored_frontend_contains_no_obvious_secret_assignments() -> None:
    authored = [
        PROJECT_DIR / "app.py",
        PROJECT_DIR / "templates" / "index.html",
        PROJECT_DIR / "static" / "js" / "dashboard.js",
        PROJECT_DIR / "static" / "js" / "plotly-loader.js",
        PROJECT_DIR / "static" / "css" / "dashboard.css",
    ]
    forbidden = ["refresh_token=", "access_token=", "password=", "pwd=", "Authorization: Bearer"]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in authored)
    for marker in forbidden:
        assert marker.lower() not in combined.lower()
