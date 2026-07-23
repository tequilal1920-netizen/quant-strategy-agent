from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
REQUIRED = [
    "app.py",
    "pipeline.py",
    "notebooks/update_dashboard.ipynb",
    "templates/index.html",
    "static/css/dashboard.css",
    "static/js/dashboard.js",
    "static/js/plotly-loader.js",
    "static/favicon.svg",
    "static/vendor/plotly.min.js",
    "data/indicator_catalog.seed.json",
    "deploy/run_service.ps1",
    "deploy/run_update.ps1",
    "deploy/healthcheck.ps1",
    "deploy/install_tasks.ps1",
    "deploy/install_release.ps1",
    "deploy/execute_notebook.py",
    "deploy/verify_http.py",
    "tools/probe_free_endpoints.py",

    "README.md",
    "requirements.txt",
    "requirements-data.txt",
    "requirements-web.txt",
    "requirements-dev.txt",
    "tests/test_app.py",
    "tests/test_pipeline.py",
    "tests/test_contract_integration.py",
]
FORBIDDEN_PARTS = {"__pycache__", ".pytest_cache", ".nb_smoke"}
IGNORED_RUNTIME_PARTS = {".venv", "logs", "deployment_evidence", "history"}
FORBIDDEN_NAMES = {"_mechanical_fix.py"}
SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:password|passwd|access[_-]?token|refresh[_-]?token|api[_-]?key|license[_-]?key|secret)"
    r"\s*[:=]\s*[\"'][^\"']{4,}[\"']"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    errors: list[str] = []
    for relative in REQUIRED:
        path = ROOT / relative
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"missing_or_empty:{relative}")

    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part in IGNORED_RUNTIME_PARTS for part in relative.parts):
            continue
        if any(part in FORBIDDEN_PARTS for part in relative.parts) or path.name in FORBIDDEN_NAMES or path.name.endswith(".codex-tmp"):
            errors.append(f"temporary_artifact:{relative.as_posix()}")

    catalog_path = ROOT / "data" / "indicator_catalog.seed.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {}
    rows = catalog.get("rows") if isinstance(catalog, dict) else None
    counts = Counter(str(row.get("module")) for row in rows) if isinstance(rows, list) else Counter()
    expected_counts = {"宏观": 87, "全球市场": 64, "申万行业": 77, "商品": 38, "个股": 62, "新闻事件": 39}
    if not isinstance(rows, list) or len(rows) != 367 or dict(counts) != expected_counts:
        errors.append("catalog_contract_failed")

    notebook_path = ROOT / "notebooks" / "update_dashboard.ipynb"
    notebook = json.loads(notebook_path.read_text(encoding="utf-8")) if notebook_path.exists() else {}
    cells = notebook.get("cells", []) if isinstance(notebook, dict) else []
    markdown = sum(cell.get("cell_type") == "markdown" for cell in cells)
    code = sum(cell.get("cell_type") == "code" for cell in cells)
    ids = [cell.get("id") for cell in cells]
    output_count = sum(len(cell.get("outputs", [])) for cell in cells if cell.get("cell_type") == "code")
    executed_count = sum(cell.get("execution_count") is not None for cell in cells if cell.get("cell_type") == "code")
    if len(cells) != 14 or markdown != 7 or code != 7 or len(set(ids)) != 14 or None in ids or output_count or executed_count:
        errors.append("notebook_contract_failed")

    probe_path = PROJECT_ROOT / "output" / "deployment_evidence" / "data_dashboard" / "free_api_validation.json"
    probe = json.loads(probe_path.read_text(encoding="utf-8")) if probe_path.exists() else {}
    probe_results = probe.get("results", []) if isinstance(probe, dict) else []
    failed_probes = [item for item in probe_results if not item.get("ok")]
    allowed_failed = {
        ("macro_china_society_traffic_volume", "Timeout"),
    }
    actual_failed = {(str(item.get("endpoint")), str(item.get("error_type"))) for item in failed_probes}
    if probe.get("total") != 43 or probe.get("ok", 0) < 42 or actual_failed - allowed_failed:
        errors.append("free_api_probe_contract_failed")

    scan_extensions = {".py", ".ps1", ".md", ".txt", ".html", ".js", ".css", ".ipynb", ".json"}
    scan_hits = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part in IGNORED_RUNTIME_PARTS for part in relative.parts):
            continue
        if not path.is_file() or path.suffix.lower() not in scan_extensions:
            continue
        if path.name == "plotly.min.js" or path.name == "release-validation.json":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if SECRET_ASSIGNMENT.search(text):
            scan_hits.append(path.relative_to(ROOT).as_posix())
    if scan_hits:
        errors.append("credential_literal:" + ",".join(scan_hits))

    pipeline_text = (ROOT / "pipeline.py").read_text(encoding="utf-8")
    app_text = (ROOT / "app.py").read_text(encoding="utf-8")
    frontend_text = (ROOT / "static" / "js" / "dashboard.js").read_text(encoding="utf-8")
    if '_attempt(module, client, "index_realtime_sw"' in pipeline_text:
        errors.append("excluded_sw_endpoint_called")
    if "dashboard_snapshot.json" not in pipeline_text or "indicator_catalog.json" not in pipeline_text:
        errors.append("artifact_names_missing")
    if "[:8]" in pipeline_text or "_live_ids_from_module" in pipeline_text:
        errors.append("pipeline_coverage_regression")
    if not all(route in app_text for route in ('@app.get("/api/v1/series")', '@app.get("/api/v1/coverage")')):
        errors.append("series_api_contract_missing")
    frontend_markers = (
        "CHART_STYLE", "scrollZoom: true", "displayModeBar: true", "window.localStorage",
        'type: "heatmap"', 'type: "candlestick"', "downloadCsv", "fixedrange: false",
    )
    if not all(marker in frontend_text for marker in frontend_markers):
        errors.append("frontend_control_contract_missing")
    if "gunicorn" in (ROOT / "requirements-web.txt").read_text(encoding="utf-8").lower():
        errors.append("windows_incompatible_gunicorn")

    report = {
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "catalog_rows": len(rows) if isinstance(rows, list) else 0,
        "catalog_counts": dict(counts),
        "notebook": {"cells": len(cells), "markdown": markdown, "code": code, "outputs": output_count, "executed": executed_count},
        "free_api_probe": {"total": probe.get("total", 0), "ok": probe.get("ok", 0), "failed": len(failed_probes)},
        "credential_literal_hits": len(scan_hits),
        "required_files": {relative: sha256(ROOT / relative) for relative in REQUIRED if (ROOT / relative).is_file()},
    }
    (ROOT / "release-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "required_files"}, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
