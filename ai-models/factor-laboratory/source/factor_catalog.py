"""Unified factor catalog for the Factor Laboratory.

This module is intentionally read-only.  It inventories the local production
feature set, the external subject factor library when present, and the dynamic
research warehouse factors without changing any database or parquet artifact.
"""
from __future__ import annotations

import ast
import json
import os
import sqlite3
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CORE_PATH = Path(__file__).with_name("core.py")

FAMILY_LABELS = {
    "core_model": "核心模型特征",
    "technical": "技术",
    "money": "资金",
    "fundamental": "基本面",
    "valuation": "估值",
    "macro": "宏观",
    "subject_strategy_technical": "技术策略精选",
    "smartbeta": "SmartBeta",
    "llm_mined": "LLM挖掘",
    "deep_mined": "深度挖掘",
    "warehouse_dynamic": "研究库动态",
    "secking": "Secking外部因子",
    "subject_parquet": "Subject物料因子",
    "discovered": "发现因子",
}

SUBJECT_STANDARD_ASSIGNMENTS = {
    "TECHNICAL_FACTORS": ("technical", "subject_standard"),
    "MONEY_FACTORS": ("money", "subject_standard"),
    "FUNDAMENTAL_FACTORS": ("fundamental", "subject_standard"),
    "VALUATION_FACTORS": ("valuation", "subject_standard"),
    "MACRO_FACTORS": ("macro", "subject_standard"),
}


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _module(path: Path) -> ast.Module | None:
    try:
        return ast.parse(_text(path))
    except (OSError, SyntaxError, UnicodeError):
        return None


def _assignment(path: Path, name: str) -> ast.AST | None:
    tree = _module(path)
    if tree is None:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return node.value
    return None


def _safe_literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (TypeError, ValueError):
        return None


def _literal_string(node: ast.AST) -> str | None:
    value = _safe_literal(node)
    return value if isinstance(value, str) else None


def _dict_rows(node: ast.AST | None) -> list[dict[str, Any]]:
    if not isinstance(node, ast.List):
        return []
    rows: list[dict[str, Any]] = []
    for item in node.elts:
        if not isinstance(item, ast.Dict):
            continue
        row: dict[str, Any] = {}
        for key_node, value_node in zip(item.keys, item.values):
            if key_node is None:
                continue
            key = _literal_string(key_node)
            if not key:
                continue
            value = _safe_literal(value_node)
            if isinstance(value, (str, int, float, bool)) or value is None:
                row[key] = value
        if row:
            rows.append(row)
    return rows


def _literal_list(path: Path, name: str) -> list[Any]:
    node = _assignment(path, name)
    value = _safe_literal(node) if node is not None else None
    return value if isinstance(value, list) else []


def _literal_dict(path: Path, name: str) -> dict[str, Any]:
    node = _assignment(path, name)
    value = _safe_literal(node) if node is not None else None
    return value if isinstance(value, dict) else {}


def _record(
    factor_name: str,
    family_id: str,
    source_agent: str,
    *,
    factor_group: str | None = None,
    label: str | None = None,
    description: str | None = None,
    materialized: bool = False,
    eligible_for_model: bool = False,
    path: Path | None = None,
    value_count: int | None = None,
    last_date: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = {
        "factor_name": str(factor_name),
        "family_id": family_id,
        "family_label": FAMILY_LABELS.get(family_id, family_id),
        "factor_group": factor_group or family_id,
        "source_agent": source_agent,
        "label": label or str(factor_name),
        "description": description or "",
        "materialized": bool(materialized),
        "eligible_for_model": bool(eligible_for_model),
        "value_count": value_count,
        "last_date": last_date,
    }
    if path is not None:
        row["path"] = str(path)
    if extra:
        row.update(extra)
    return row


def _default_subject_roots(subject_roots: list[str | Path] | None) -> list[Path]:
    raw_roots: list[str | Path] = []
    if subject_roots:
        raw_roots.extend(subject_roots)
    env_root = os.environ.get("FACTOR_LAB_SUBJECT_ROOT")
    if env_root:
        raw_roots.append(env_root)
    raw_roots.extend([Path("G:/subject/main"), Path("G:/subject")])
    seen: set[Path] = set()
    roots: list[Path] = []
    for raw in raw_roots:
        root = Path(raw)
        main = root if (root / "factor").exists() or (root / "model").exists() else root / "main"
        try:
            key = main.resolve()
        except OSError:
            key = main
        if key not in seen:
            seen.add(key)
            roots.append(main)
    return roots


def _core_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    features = [str(item) for item in _literal_list(CORE_PATH, "FEATURES")]
    domains = _literal_dict(CORE_PATH, "DOMAINS")
    domain_by_feature = {
        str(feature): str(domain)
        for domain, members in domains.items()
        if isinstance(members, list)
        for feature in members
    }
    rows = [
        _record(
            name,
            "core_model",
            "factor_laboratory_core",
            factor_group=f"core/{domain_by_feature.get(name, 'other')}",
            materialized=True,
            eligible_for_model=True,
        )
        for name in features
    ]
    return rows, {"source": "factor_laboratory_core", "status": "ok", "count": len(rows), "path": str(CORE_PATH)}


def _subject_standard_rows(subject_main: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    factors_py = subject_main / "factor" / "factors.py"
    rows: list[dict[str, Any]] = []
    if not factors_py.exists():
        return rows, {"source": "subject_standard", "status": "missing", "count": 0, "path": str(factors_py)}
    parquet_root = subject_main / "factor" / "parquet"
    for assignment, (family_id, source) in SUBJECT_STANDARD_ASSIGNMENTS.items():
        for item in _dict_rows(_assignment(factors_py, assignment)):
            name = item.get("name")
            if not name:
                continue
            folder = str(item.get("folder") or family_id)
            materialized_path = parquet_root / folder / f"{name}.parquet.gzip"
            rows.append(
                _record(
                    str(name),
                    family_id,
                    source,
                    factor_group=family_id,
                    label=str(item.get("label") or name),
                    description=str(item.get("description") or ""),
                    materialized=materialized_path.exists(),
                    path=materialized_path if materialized_path.exists() else None,
                    extra={"external_folder": folder},
                )
            )
    return rows, {"source": "subject_standard", "status": "ok", "count": len(rows), "path": str(factors_py)}


def _subject_technical_rows(subject_main: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = subject_main / "model" / "technical.py"
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows, {"source": "subject_strategy_technical", "status": "missing", "count": 0, "path": str(path)}
    for item in _dict_rows(_assignment(path, "TECHNICAL_FACTOR_REGISTRY")):
        name = item.get("name")
        if not name:
            continue
        rows.append(
            _record(
                str(name),
                "subject_strategy_technical",
                "subject_strategy_technical",
                factor_group=str(item.get("category") or "technical"),
                label=str(item.get("label") or name),
                materialized=True,
                extra={"prior_sign": item.get("prior_sign"), "external_folder": item.get("folder")},
            )
        )
    return rows, {"source": "subject_strategy_technical", "status": "ok", "count": len(rows), "path": str(path)}


def _subject_smartbeta_rows(subject_main: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = subject_main / "model" / "smartbeta.py"
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows, {"source": "smartbeta", "status": "missing", "count": 0, "path": str(path)}
    for item in _dict_rows(_assignment(path, "SMARTBETA_SUBFACTOR_SPECS")):
        name = item.get("name")
        if not name:
            continue
        rows.append(
            _record(
                str(name),
                "smartbeta",
                "subject_smartbeta",
                factor_group=str(item.get("category") or "smartbeta"),
                label=str(item.get("label") or name),
                materialized=True,
                extra={"raw_field": item.get("raw"), "higher_is_better": item.get("higher")},
            )
        )
    return rows, {"source": "smartbeta", "status": "ok", "count": len(rows), "path": str(path)}


def _parquet_factor_name(path: Path) -> str:
    name = path.name
    for suffix in (".parquet.gzip", ".parquet", ".gzip"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def _subject_parquet_rows(subject_main: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = subject_main / "factor" / "parquet"
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows, {"source": "subject_parquet_files", "status": "missing", "count": 0, "path": str(root)}
    family_map = {"technical": "technical", "money": "money", "fundamental": "fundamental", "valuation": "valuation", "macro": "macro", "secking": "secking"}
    for path in sorted(root.rglob("*.parquet*")):
        if not path.is_file():
            continue
        factor_name = _parquet_factor_name(path)
        if not factor_name or factor_name.startswith("_"):
            continue
        folder = path.parent.name.lower()
        family_id = family_map.get(folder, "subject_parquet")
        rows.append(
            _record(
                factor_name,
                family_id,
                "subject_parquet_file",
                factor_group=folder,
                label=factor_name,
                materialized=True,
                path=path,
                extra={"external_folder": folder},
            )
        )
    return rows, {"source": "subject_parquet_files", "status": "ok", "count": len(rows), "path": str(root)}


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "select 1 from sqlite_master where type='table' and name=?",
        (name,),
    ).fetchone()
    return bool(row)


def _table_columns(conn: sqlite3.Connection, name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({name})").fetchall()}


def _warehouse_family(row: sqlite3.Row | dict[str, Any]) -> str:
    factor_name = str(row["factor_name"]).lower()
    group = str(row["factor_group"] or "").lower()
    source = str(row["source_agent"] or "").lower()
    if "deep" in factor_name or "lstm" in factor_name or "gru" in factor_name or "transformer" in factor_name:
        return "deep_mined"
    if "05_factor" in source or "llm" in source or "agent" in source:
        return "llm_mined"
    if "discover" in group or "formula" in group:
        return "discovered"
    return "warehouse_dynamic"


def _warehouse_rows(warehouse_path: Path | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if warehouse_path is None or not warehouse_path.exists():
        return [], [{"source": "research_warehouse", "status": "missing", "count": 0, "path": str(warehouse_path or "")}]
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    conn = sqlite3.connect("file:" + warehouse_path.as_posix() + "?mode=ro", uri=True, timeout=20)
    conn.row_factory = sqlite3.Row

    def first_existing(columns: set[str], names: tuple[str, ...], default_sql: str) -> str:
        available = [name for name in names if name in columns]
        if not available:
            return default_sql
        return "coalesce(" + ",".join(available + [default_sql]) + ")"

    try:
        if _table_exists(conn, "factor_value_daily"):
            columns = _table_columns(conn, "factor_value_daily")
            if "factor_name" in columns:
                group_expr = first_existing(columns, ("factor_group", "family", "factor_family", "domain"), "'未分类'")
                source_expr = first_existing(columns, ("source_agent", "source", "creator"), "'research_warehouse'")
                date_expr = "max(trade_date)" if "trade_date" in columns else "null"
                value_rows = conn.execute(
                    f"""
                    select factor_name,
                           {group_expr} as factor_group,
                           {source_expr} as source_agent,
                           count(*) as value_count,
                           {date_expr} as last_date
                    from factor_value_daily
                    group by factor_name, factor_group, source_agent
                    """
                ).fetchall()
                for item in value_rows:
                    family_id = _warehouse_family(item)
                    rows.append(
                        _record(
                            item["factor_name"],
                            family_id,
                            item["source_agent"],
                            factor_group=item["factor_group"],
                            materialized=True,
                            value_count=int(item["value_count"] or 0),
                            last_date=str(item["last_date"] or ""),
                        )
                    )
                sources.append({"source": "factor_value_daily", "status": "ok", "count": len(value_rows), "path": str(warehouse_path)})
            else:
                sources.append({"source": "factor_value_daily", "status": "missing_factor_name", "count": 0, "path": str(warehouse_path)})
        if _table_exists(conn, "v3_factor_candidate_registry"):
            columns = _table_columns(conn, "v3_factor_candidate_registry")
            if "factor_name" in columns:
                group_expr = first_existing(columns, ("factor_group", "family", "factor_family", "domain"), "'未分类'")
                source_expr = first_existing(columns, ("source_agent", "source", "creator"), "'candidate_registry'")
                status_expr = "max(status)" if "status" in columns else "'accepted'"
                registry_rows = conn.execute(
                    f"""
                    select factor_name,
                           {group_expr} as factor_group,
                           {source_expr} as source_agent,
                           {status_expr} as status,
                           count(*) as registry_count
                    from v3_factor_candidate_registry
                    group by factor_name, factor_group, source_agent
                    """
                ).fetchall()
                existing = {(row["factor_name"], row["source_agent"]) for row in rows}
                for item in registry_rows:
                    if (item["factor_name"], item["source_agent"]) in existing:
                        continue
                    family_id = _warehouse_family(item)
                    rows.append(
                        _record(
                            item["factor_name"],
                            family_id,
                            item["source_agent"],
                            factor_group=item["factor_group"],
                            materialized=False,
                            value_count=int(item["registry_count"] or 0),
                            extra={"candidate_status": item["status"]},
                        )
                    )
                sources.append({"source": "v3_factor_candidate_registry", "status": "ok", "count": len(registry_rows), "path": str(warehouse_path)})
            else:
                sources.append({"source": "v3_factor_candidate_registry", "status": "missing_factor_name", "count": 0, "path": str(warehouse_path)})
    finally:
        conn.close()
    return rows, sources


def _snapshot_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if os.environ.get("FACTOR_LAB_DISABLE_CATALOG_SNAPSHOT") == "1":
        return [], {"source": "factor_catalog_snapshot", "status": "disabled", "count": 0, "path": ""}
    raw_path = os.environ.get("FACTOR_LAB_CATALOG_SNAPSHOT")
    path = Path(raw_path) if raw_path else Path(__file__).with_name("factor_catalog_snapshot.json")
    if not path.exists():
        return [], {"source": "factor_catalog_snapshot", "status": "missing", "count": 0, "path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [], {"source": "factor_catalog_snapshot", "status": "blocked", "count": 0, "path": str(path), "message": str(exc)}
    rows: list[dict[str, Any]] = []
    for item in payload.get("factors", []):
        if not isinstance(item, dict) or not item.get("factor_name"):
            continue
        row = dict(item)
        row.setdefault("family_id", "discovered")
        row.setdefault("factor_group", row.get("family_id"))
        row.setdefault("source_agent", "factor_catalog_snapshot")
        row.setdefault("materialized", False)
        rows.append(row)
    return rows, {"source": "factor_catalog_snapshot", "status": "ok", "count": len(rows), "path": str(path)}


def _deduplicate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    priority = {
        "factor_laboratory_core": 0,
        "subject_standard": 1,
        "subject_parquet_file": 2,
        "subject_strategy_technical": 3,
        "subject_smartbeta": 4,
        "factor_value_daily": 5,
        "candidate_registry": 6,
    }
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("factor_name")), str(row.get("source_agent")))
        if key not in best:
            best[key] = row
            continue
        old = best[key]
        old_score = (not old.get("materialized"), priority.get(str(old.get("source_agent")), 9))
        new_score = (not row.get("materialized"), priority.get(str(row.get("source_agent")), 9))
        if new_score < old_score:
            best[key] = row
    return list(best.values())


def build_factor_catalog(
    warehouse_path: str | Path | None = None,
    subject_roots: list[str | Path] | None = None,
) -> dict[str, Any]:
    rows, core_source = _core_rows()
    sources = [core_source]
    for subject_main in _default_subject_roots(subject_roots):
        standard, standard_source = _subject_standard_rows(subject_main)
        technical, technical_source = _subject_technical_rows(subject_main)
        smartbeta, smartbeta_source = _subject_smartbeta_rows(subject_main)
        parquet, parquet_source = _subject_parquet_rows(subject_main)
        rows.extend(standard)
        rows.extend(technical)
        rows.extend(smartbeta)
        rows.extend(parquet)
        sources.extend([standard_source, technical_source, smartbeta_source, parquet_source])
        if standard or technical or smartbeta or parquet:
            break
    warehouse_factor_rows, warehouse_sources = _warehouse_rows(Path(warehouse_path) if warehouse_path else None)
    rows.extend(warehouse_factor_rows)
    sources.extend(warehouse_sources)
    snapshot_factor_rows, snapshot_source = _snapshot_rows()
    rows.extend(snapshot_factor_rows)
    sources.append(snapshot_source)
    rows = _deduplicate(rows)

    unique_names = {str(row["factor_name"]) for row in rows}
    materialized_names = {str(row["factor_name"]) for row in rows if row.get("materialized")}
    current_model_names = {
        str(row["factor_name"]) for row in rows
        if row.get("source_agent") == "factor_laboratory_core" and row.get("eligible_for_model")
    }
    family_counts: dict[str, set[str]] = {}
    for row in rows:
        family_counts.setdefault(str(row["family_id"]), set()).add(str(row["factor_name"]))
    family_order = list(FAMILY_LABELS)
    families = [
        {"id": family_id, "label": FAMILY_LABELS.get(family_id, family_id), "count": len(family_counts[family_id])}
        for family_id in family_order
        if family_id in family_counts
    ]
    for family_id in sorted(set(family_counts) - set(family_order)):
        families.append({"id": family_id, "label": FAMILY_LABELS.get(family_id, family_id), "count": len(family_counts[family_id])})

    rows.sort(
        key=lambda row: (
            not bool(row.get("materialized")),
            str(row.get("family_id")),
            str(row.get("factor_name")),
        )
    )
    return {
        "status": "ok",
        "registered_factor_count": len(unique_names),
        "explicit_factor_entry_count": len(rows),
        "materialized_factor_count": len(materialized_names),
        "current_model_feature_count": len(current_model_names),
        "pending_unified_model_factor_count": max(0, len(unique_names) - len(current_model_names)),
        "families": families,
        "factors": rows,
        "sources": sources,
        "pipeline_status": {
            "factor_catalog": "ready",
            "current_strategy_training_panel": "core_29_features",
            "external_factor_library": "cataloged_materialization_tracked",
            "next_required_step": "build_unified_date_stock_factor_panel_for_screening_and_quarterly_refit",
        },
    }
