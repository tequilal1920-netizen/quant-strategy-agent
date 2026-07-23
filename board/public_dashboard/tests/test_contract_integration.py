from __future__ import annotations

import json
from pathlib import Path

from app import create_app

MODULES = ("macro", "global_markets", "sw_industries", "commodities", "stock", "news_events")


def write_contract(tmp_path: Path) -> None:
    modules = {
        key: {"title": key, "status": "partial", "as_of": "2026-07-10", "series": [], "kpis": [], "charts": [], "tables": [], "alerts": []}
        for key in MODULES
    }
    modules["macro"]["series"] = [{
        "id": "macro.integration",
        "catalog_id": "M0001",
        "label": "integration",
        "submodule": "test",
        "unit": "%",
        "frequency": "日",
        "status": "live",
        "source": "fixture",
        "as_of": "2026-07-10",
        "data": [{"date": "2026-07-10", "value": 1.0}],
    }]
    modules["macro"]["charts"] = [{"id": "unknown-chart", "title": "unknown", "kind": "line", "series": []}]
    modules["macro"]["tables"] = [{"id": "unknown-table", "title": "unknown", "columns": [], "rows": []}]
    modules["stock"].update(
        {
            "status": "ok",
            "kpis": [{"id": "stock_600519", "label": "600519", "value": 1, "status": "ok"}],
            "charts": [
                {
                    "id": "stock-watch",
                    "title": "watch",
                    "kind": "line",
                    "status": "ok",
                    "series": [
                        {"name": "贵州茅台 600519", "data": [{"date": "2026-07-10", "value": 1}]},
                        {"name": "平安银行 000001", "data": [{"date": "2026-07-10", "value": 2}]},
                    ],
                }
            ],
            "tables": [
                {
                    "id": "stock-table",
                    "title": "watch",
                    "status": "ok",
                    "columns": ["code", "name"],
                    "rows": [{"code": "600519", "name": "贵州茅台"}, {"code": "000001.SZ", "name": "平安银行"}],
                }
            ],
        }
    )
    snapshot = {
        "schema_version": "1.0",
        "generated_at": "2026-07-11T16:00:00Z",
        "as_of": "2026-07-10",
        "status": "partial",
        "summary": {},
        "modules": modules,
        "quality": {},
        "catalog_status": [{"id": "M0001", "module": "macro", "status": "live"}],
    }
    catalog = json.loads((Path(__file__).parents[1] / "data" / "indicator_catalog.seed.json").read_text(encoding="utf-8"))
    (tmp_path / "dashboard_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "indicator_catalog.json").write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")


def test_pipeline_contract_is_consumed_without_false_ok(tmp_path: Path) -> None:
    write_contract(tmp_path)
    client = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "MAX_SNAPSHOT_AGE_SECONDS": 10**12}).test_client()
    response = client.get("/api/v1/snapshot")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["modules"]["macro"]["charts"][0]["status"] == "unavailable"
    assert payload["modules"]["macro"]["tables"][0]["status"] == "unavailable"
    assert payload["modules"]["macro"]["series"][0]["id"] == "macro.integration"
    assert payload["modules"]["macro"]["series"][0]["status"] == "live"


def test_stock_api_filters_real_table_contract(tmp_path: Path) -> None:
    write_contract(tmp_path)
    client = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "MAX_SNAPSHOT_AGE_SECONDS": 10**12}).test_client()
    response = client.get("/api/v1/stock/600519.SH")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["data"]["record"]["code"] == "600519"
    assert [series["name"] for series in payload["data"]["charts"][0]["series"]] == ["贵州茅台 600519"]


def test_health_requires_exact_catalog(tmp_path: Path) -> None:
    write_contract(tmp_path)
    client = create_app({
        "TESTING": True,
        "DATA_DIR": str(tmp_path),
        "MAX_SNAPSHOT_AGE_SECONDS": 10**12,
        "MIN_TOTAL_LIVE_CATALOG": 0,
        "CORE_MIN_LIVE_BY_MODULE": {},
        "DAILY_AS_OF_MAX_AGE_DAYS": {},
    }).test_client()
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json()["catalog_rows"] == 367


def test_deployment_scripts_are_bounded_and_project_scoped() -> None:
    root = Path(__file__).parents[1]
    health = (root / "deploy" / "healthcheck.ps1").read_text(encoding="utf-8")
    update = (root / "deploy" / "run_update.ps1").read_text(encoding="utf-8")
    tasks = (root / "deploy" / "install_tasks.ps1").read_text(encoding="utf-8")
    assert "/livez" in health and "/healthz" in health
    assert "quality_failed_no_restart" in health
    assert "RestartCooldownMinutes" in health
    assert "restart_refused_wrong_task_scope" in health
    assert "ValidateRange(1, 3)" in update
    assert '"--modules", "news_events"' in update
    assert "ResearchMarketBoardNewsUpdate" in tasks
    assert "New-ScheduledTaskAction -Execute $servicePython" in tasks
    assert "-WorkingDirectory $Root" in tasks
    assert "-Mode Full -Force -MaxAttempts 2" in tasks
    assert "-Mode News -Force -MaxAttempts 2" in tasks
    assert "New-TimeSpan -Minutes 60" in tasks
    assert 'UserId "SYSTEM"' in tasks


def test_notebook_keeps_clean_seven_plus_seven_contract() -> None:
    notebook = json.loads((Path(__file__).parents[1] / "notebooks" / "update_dashboard.ipynb").read_text(encoding="utf-8"))
    cells = notebook["cells"]
    assert len(cells) == 14
    assert sum(cell["cell_type"] == "markdown" for cell in cells) == 7
    assert sum(cell["cell_type"] == "code" for cell in cells) == 7
    assert all(cell.get("execution_count") is None and cell.get("outputs") == [] for cell in cells if cell["cell_type"] == "code")
    source = "\n".join("".join(cell.get("source", [])) for cell in cells)
    assert "module.get('series')" in source
    assert "fabricated_observations" in source


def test_frontend_status_units_watermarks_and_favicon_contract() -> None:
    root = Path(__file__).parents[1]
    script = (root / "static" / "js" / "dashboard.js").read_text(encoding="utf-8")
    template = (root / "templates" / "index.html").read_text(encoding="utf-8")
    assert "displayValue.indexOf(String(kpi.unit)) < 0" in script
    assert 'if (key === "live") key = "ok"' in script
    assert 'key === "metadata_only" || key === "metadata-only"' in script
    assert "chart.as_of || activeModuleData().as_of || state.snapshot.as_of" in script
    assert "if (Array.isArray(module.series))" in script
    assert "Compatibility path for snapshots created before modules[*].series became canonical" in script
    assert "table.source || module.source || snapshot.source" in script
    assert "table.as_of || module.as_of || snapshot.as_of" in script
    assert "renderActiveView();" in script
    assert "availableIds.has(seriesId)" in script
    assert "series.id === requestedBenchmark && hasNumericData(series)" in script
    assert 'id: "stock-record-detail"' in script
    assert "rows: [detailRow]" in script
    assert 'id="fresh-catalog"' in template
    assert 'id="fresh-catalog-breakdown"' in template
    assert "catalog_status" in script
    assert "只作用于“趋势 / 横截面”" in template
    assert "favicon.svg" in template
    assert (root / "static" / "favicon.svg").is_file()
