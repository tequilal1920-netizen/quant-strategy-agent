"""Strict portfolio-optimizer HTTP API with one durable SQLite state store.

The LLM is restricted to compiling an auditable constraint mandate.  It never
returns security weights.  A deterministic optimizer may run only after the
exact draft hash has been confirmed.  Missing data, missing solver capability,
infeasibility and LLM failures are blocking states; this module has no fallback
portfolio construction path.
"""

from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import hashlib
import inspect
import json
import math
import os
import sqlite3
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from flask import Blueprint, Response, current_app, jsonify, request


API_VERSION = "optimizer-api/1.1-factor-lab-champion"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_DB = Path(__file__).resolve().parents[2] / "database" / "optimizer_state.db"

BLOCKED_LLM = "BLOCKED_LLM"
BLOCKED_SCHEMA = "BLOCKED_SCHEMA"
BLOCKED_SEMANTIC = "BLOCKED_SEMANTIC"
BLOCKED_SOLVER_CAPABILITY = "BLOCKED_SOLVER_CAPABILITY"
BLOCKED_DATA = "BLOCKED_DATA"
BLOCKED_INFEASIBLE = "BLOCKED_INFEASIBLE"
BLOCKED_SOLVER = "BLOCKED_SOLVER"
BLOCKED_INPUT = "BLOCKED_INPUT"
AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
CONFIRMED = "CONFIRMED"
QUEUED = "QUEUED"
SOLVING = "SOLVING"
SOLVED = "SOLVED"
AUDITED = "AUDITED"
CANCEL_REQUESTED = "CANCEL_REQUESTED"
CANCELLED = "CANCELLED"
FAILED = "FAILED"

FINAL_RUN_STATUSES = {
    AUDITED,
    CANCELLED,
    FAILED,
    BLOCKED_DATA,
    BLOCKED_INFEASIBLE,
    BLOCKED_SOLVER,
    BLOCKED_SOLVER_CAPABILITY,
    BLOCKED_INPUT,
}
DIRECT_SOLUTION_FIELDS = {
    "weights",
    "active_weights",
    "target_weights",
    "security_weights",
    "portfolio_weights",
    "weight_by_security",
    "orders",
    "target_positions",
    "transactions",
}


def _scipy_highs_milp_available() -> bool:
    """Return whether SciPy exposes its HiGHS-backed MILP entry point."""

    try:
        from scipy.optimize import milp as scipy_milp  # type: ignore
    except Exception:
        return False
    return callable(scipy_milp)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _identifier(prefix: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:10]}"


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _jsonable(dataclasses.asdict(value))
    value_type = type(value)
    if str(getattr(value_type, "__module__", "")).startswith("pandas"):
        if value_type.__name__ == "DataFrame":
            return {
                "__type__": "DataFrame",
                "rows": int(len(value)),
                "columns": [str(item) for item in getattr(value, "columns", [])],
                "omitted_from_state_store": True,
            }
        if value_type.__name__ == "Series":
            return {
                "__type__": "Series",
                "rows": int(len(value)),
                "name": str(getattr(value, "name", "")),
                "omitted_from_state_store": True,
            }
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if hasattr(value, "item") and callable(value.item):
        try:
            return _jsonable(value.item())
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite values cannot be persisted")
    return value


def _dumps(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )

def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_dumps(value).encode("utf-8")).hexdigest()


def _public_run_status(status: str) -> str:
    internal = str(status or "").upper()
    if internal in {CONFIRMED, QUEUED}:
        return "queued"
    if internal in {SOLVING, SOLVED}:
        return "running"
    if internal == AUDITED:
        return "completed"
    if internal in {CANCEL_REQUESTED, CANCELLED}:
        return "cancelled"
    if internal == FAILED:
        return "failed"
    if internal.startswith("BLOCKED_"):
        return "blocked"
    return str(status or "unknown").lower()


def _sealed_test_publication_gate(
    result: Mapping[str, Any],
) -> tuple[bool, dict[str, Any]]:
    """Use sealed test only as a post-selection production veto."""

    audit = (
        result.get("backtest_audit")
        if isinstance(result.get("backtest_audit"), Mapping) else {}
    )
    split_metrics = audit.get("metrics_by_split")
    split_metrics = (
        split_metrics if isinstance(split_metrics, Mapping) else {}
    )
    raw = split_metrics.get("test_report_only")
    raw = raw if isinstance(raw, Mapping) else {}
    optimized = raw.get("optimized")
    optimized = optimized if isinstance(optimized, Mapping) else {}
    benchmark = raw.get("benchmark")
    benchmark = benchmark if isinstance(benchmark, Mapping) else {}
    periods = int(optimized.get("periods") or 0)
    annual_return = optimized.get("annual_return")
    benchmark_return = benchmark.get("annual_return")
    sharpe = optimized.get("sharpe")
    information_ratio = optimized.get("information_ratio")
    annual_excess = (
        float(annual_return) - float(benchmark_return)
        if annual_return is not None and benchmark_return is not None
        else None
    )
    passed = bool(
        periods >= 24
        and annual_excess is not None and annual_excess > 0.0
        and sharpe is not None and float(sharpe) > 0.0
        and information_ratio is not None
        and float(information_ratio) > 0.0
    )
    return passed, {
        "status": "passed" if passed else "production_vetoed",
        "role": "post_selection_production_veto_only",
        "used_for_candidate_ranking": False,
        "periods": periods,
        "annual_return": annual_return,
        "benchmark_annual_return": benchmark_return,
        "annual_excess_return": annual_excess,
        "sharpe": sharpe,
        "information_ratio": information_ratio,
    }



def _loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return copy.deepcopy(default)
    return json.loads(value)


def _scrub_solution_fields(value: Any) -> Any:
    """Remove every weight/order field from a non-tradable response."""

    if isinstance(value, Mapping):
        return {
            str(key): _scrub_solution_fields(item)
            for key, item in value.items()
            if str(key).lower() not in DIRECT_SOLUTION_FIELDS
        }
    if isinstance(value, list):
        return [_scrub_solution_fields(item) for item in value]
    return value


def _public_compiler_status(status: str) -> str:
    return BLOCKED_INFEASIBLE if status == "INFEASIBLE" else status


class OptimizerStateStore:
    """Small connection-per-operation SQLite repository safe for worker threads."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._write_lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS optimizer_draft (
                    draft_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    raw_request TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    compiled_json TEXT NOT NULL,
                    draft_hash TEXT,
                    confirmation_hash TEXT
                );
                CREATE TABLE IF NOT EXISTS optimizer_run (
                    run_id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL REFERENCES optimizer_draft(draft_id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    run_name TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    mandate_json TEXT NOT NULL,
                    result_json TEXT,
                    error_json TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS optimizer_audit_event (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_optimizer_run_created
                    ON optimizer_run(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_optimizer_event_entity
                    ON optimizer_audit_event(entity_type, entity_id, event_id);
                """
            )

    def _event(
        self,
        connection: sqlite3.Connection,
        entity_type: str,
        entity_id: str,
        state: str,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO optimizer_audit_event
               (entity_type, entity_id, created_at, state, payload_json)
               VALUES (?, ?, ?, ?, ?)""",
            (entity_type, entity_id, _utc_now(), state, _dumps(payload or {})),
        )

    def create_draft(
        self,
        *,
        raw_request: str,
        mode: str,
        request_payload: Mapping[str, Any],
        compiled: Mapping[str, Any],
    ) -> str:
        draft_id = _identifier("draft")
        now = _utc_now()
        status = _public_compiler_status(str(compiled.get("status") or BLOCKED_SCHEMA))
        confirmation = compiled.get("confirmation")
        confirmation_hash = (
            confirmation.get("confirm_hash") if isinstance(confirmation, Mapping) else None
        )
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO optimizer_draft
                   (draft_id, created_at, updated_at, status, mode, raw_request,
                    request_json, compiled_json, draft_hash, confirmation_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    draft_id,
                    now,
                    now,
                    status,
                    mode,
                    raw_request,
                    _dumps(request_payload),
                    _dumps(compiled),
                    compiled.get("draft_hash"),
                    confirmation_hash,
                ),
            )
            self._event(connection, "draft", draft_id, "DRAFT_RECEIVED", {})
        return draft_id

    def update_draft(self, draft_id: str, compiled: Mapping[str, Any], state: str) -> None:
        confirmation = compiled.get("confirmation")
        confirmation_hash = (
            confirmation.get("confirm_hash") if isinstance(confirmation, Mapping) else None
        )
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """UPDATE optimizer_draft
                   SET updated_at=?, status=?, compiled_json=?, draft_hash=?, confirmation_hash=?
                   WHERE draft_id=?""",
                (
                    _utc_now(),
                    _public_compiler_status(str(compiled.get("status") or state)),
                    _dumps(compiled),
                    compiled.get("draft_hash"),
                    confirmation_hash,
                    draft_id,
                ),
            )
            self._event(connection, "draft", draft_id, state, {})

    def append_draft_event(
        self, draft_id: str, state: str, payload: Mapping[str, Any] | None = None
    ) -> None:
        with self._write_lock, self._connect() as connection:
            self._event(connection, "draft", draft_id, state, payload)

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM optimizer_draft WHERE draft_id=?", (draft_id,)
            ).fetchone()
            if row is None:
                return None
            events = connection.execute(
                """SELECT created_at, state, payload_json
                   FROM optimizer_audit_event
                   WHERE entity_type='draft' AND entity_id=? ORDER BY event_id""",
                (draft_id,),
            ).fetchall()
        return {
            "draft_id": row["draft_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "status": row["status"],
            "mode": row["mode"],
            "raw_request": row["raw_request"],
            "request": _loads(row["request_json"], {}),
            "compiled": _loads(row["compiled_json"], {}),
            "draft_hash": row["draft_hash"],
            "confirmation_hash": row["confirmation_hash"],
            "audit": [
                {
                    "created_at": item["created_at"],
                    "state": item["state"],
                    "payload": _loads(item["payload_json"], {}),
                }
                for item in events
            ],
        }

    def find_draft_by_confirmation_hash(
        self, confirmation_hash: str
    ) -> dict[str, Any] | None:
        if not confirmation_hash:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """SELECT draft_id FROM optimizer_draft
                   WHERE confirmation_hash=? ORDER BY updated_at DESC LIMIT 1""",
                (confirmation_hash,),
            ).fetchone()
        return None if row is None else self.get_draft(str(row["draft_id"]))
    def create_run(
        self,
        *,
        draft_id: str,
        run_name: str,
        request_payload: Mapping[str, Any],
        mandate: Mapping[str, Any],
    ) -> str:
        run_id = _identifier("run")
        now = _utc_now()
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO optimizer_run
                   (run_id, draft_id, created_at, updated_at, status, run_name,
                    request_json, mandate_json, result_json, error_json, cancel_requested)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0)""",
                (
                    run_id,
                    draft_id,
                    now,
                    now,
                    CONFIRMED,
                    run_name,
                    _dumps(request_payload),
                    _dumps(mandate),
                ),
            )
            self._event(connection, "run", run_id, CONFIRMED, {"draft_id": draft_id})
        return run_id

    def transition_run(
        self,
        run_id: str,
        state: str,
        *,
        result: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        with self._write_lock, self._connect() as connection:
            connection.execute(
                """UPDATE optimizer_run SET updated_at=?, status=?,
                   result_json=COALESCE(?, result_json),
                   error_json=COALESCE(?, error_json)
                   WHERE run_id=?""",
                (
                    _utc_now(),
                    state,
                    None if result is None else _dumps(result),
                    None if error is None else _dumps(error),
                    run_id,
                ),
            )
            self._event(connection, "run", run_id, state, payload or {})

    def request_cancel(self, run_id: str) -> dict[str, Any] | None:
        with self._write_lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM optimizer_run WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                return None
            status = str(row["status"])
            if status in FINAL_RUN_STATUSES:
                return {"status": status, "accepted": False}
            next_state = CANCELLED if status in {CONFIRMED, QUEUED} else CANCEL_REQUESTED
            connection.execute(
                """UPDATE optimizer_run SET cancel_requested=1, status=?, updated_at=?
                   WHERE run_id=?""",
                (next_state, _utc_now(), run_id),
            )
            self._event(connection, "run", run_id, next_state, {})
            return {"status": next_state, "accepted": True}

    def cancel_requested(self, run_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM optimizer_run WHERE run_id=?", (run_id,)
            ).fetchone()
        return bool(row and row["cancel_requested"])

    def get_run(self, run_id: str, *, include_audit: bool = True) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM optimizer_run WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                return None
            events = []
            if include_audit:
                events = connection.execute(
                    """SELECT created_at, state, payload_json
                       FROM optimizer_audit_event
                       WHERE entity_type='run' AND entity_id=? ORDER BY event_id""",
                    (run_id,),
                ).fetchall()
        return {
            "run_id": row["run_id"],
            "draft_id": row["draft_id"],
            "run_name": row["run_name"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "status": row["status"],
            "request": _loads(row["request_json"], {}),
            "mandate": _loads(row["mandate_json"], {}),
            "result": _loads(row["result_json"], None),
            "error": _loads(row["error_json"], None),
            "cancel_requested": bool(row["cancel_requested"]),
            "audit": [
                {
                    "created_at": item["created_at"],
                    "state": item["state"],
                    "payload": _loads(item["payload_json"], {}),
                }
                for item in events
            ],
        }

    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 200))
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT run_id, draft_id, run_name, created_at, updated_at, status,
                          cancel_requested
                   FROM optimizer_run ORDER BY created_at DESC LIMIT ?""",
                (bounded,),
            ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "draft_id": row["draft_id"],
                "run_name": row["run_name"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "status": row["status"],
                "cancel_requested": bool(row["cancel_requested"]),
            }
            for row in rows
        ]


class OptimizerBackendService:
    def __init__(
        self,
        store: OptimizerStateStore,
        *,
        llm_client: Callable[..., Any] | None = None,
        runner: Callable[..., Mapping[str, Any]] | None = None,
        available_solvers: Sequence[str] | None = None,
    ) -> None:
        self.store = store
        self.llm_client = llm_client
        self.runner = runner or self._default_runner
        self.available_solvers = (
            None if available_solvers is None else tuple(str(item).upper() for item in available_solvers)
        )
        self._threads: dict[str, threading.Thread] = {}
        self._thread_lock = threading.RLock()

    @staticmethod
    def _compiler_module():
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from model.portfolio_optimization import mandate_compiler

        return mandate_compiler

    def _discover_solvers(self) -> list[str]:
        if self.available_solvers is not None:
            solvers = set(self.available_solvers)
        else:
            configured = os.getenv("PORTFOLIO_AVAILABLE_SOLVERS", "")
            if configured.strip():
                solvers = {
                    item.strip().upper()
                    for item in configured.split(",")
                    if item.strip()
                }
            else:
                try:
                    import cvxpy as cp  # type: ignore

                    solvers = {
                        str(item).upper() for item in cp.installed_solvers()
                    }
                except Exception:
                    solvers = set()
        if _scipy_highs_milp_available():
            solvers.add("SCIPY_HIGHS_MILP")
        return sorted(solvers)

    def bootstrap(self) -> dict[str, Any]:
        solvers = self._discover_solvers()
        clarabel_ready = "CLARABEL" in solvers
        highs_ready = "SCIPY_HIGHS_MILP" in solvers
        solver_ready = clarabel_ready and highs_ready
        joint_solver_policy = self._compiler_module().build_solver_policy(
            "joint_cardinality", solvers
        )
        llm_ready = bool(
            self.llm_client is not None
            or (
                (os.getenv("AI_ROUTER_API_KEY") or os.getenv("OPENAI_API_KEY"))
                and (os.getenv("AI_ROUTER_URL") or os.getenv("AI_ROUTER_BASE_URL"))
            )
        )
        warehouse = Path(
            os.getenv(
                "RESEARCH_WAREHOUSE_DB",
                str(PROJECT_ROOT / "database" / "research_warehouse.db"),
            )
        ).expanduser().resolve()
        audit: dict[str, Any] = {
            "ready": False,
            "reason": "research_warehouse_missing",
            "warehouse": str(warehouse),
            "constituent_periods": 0,
            "constituent_start": None,
            "constituent_end": None,
            "latest_constituent_count": 0,
            "latest_constituent_weight_total": None,
            "score_periods": 0,
            "latest_score_date": None,
            "latest_score_count": 0,
            "latest_score_run_id": None,
            "score_name": None,
        }
        if warehouse.is_file():
            try:
                with sqlite3.connect(
                    "file:" + warehouse.as_posix() + "?mode=ro",
                    uri=True,
                    timeout=10.0,
                ) as connection:
                    tables = {
                        str(row[0])
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    }
                    if "index_constituent_period" not in tables:
                        audit["reason"] = "index_constituent_period_table_missing"
                    else:
                        periods = connection.execute(
                            """
                            SELECT trade_date, COUNT(DISTINCT con_code), SUM(weight)
                            FROM index_constituent_period
                            WHERE status='ready'
                              AND universe='CSI500_ENH'
                            GROUP BY trade_date ORDER BY trade_date
                            """
                        ).fetchall()
                        if periods:
                            audit.update(
                                constituent_periods=len(periods),
                                constituent_start=str(periods[0][0]),
                                constituent_end=str(periods[-1][0]),
                                latest_constituent_count=int(periods[-1][1]),
                                latest_constituent_weight_total=(
                                    None
                                    if periods[-1][2] is None
                                    else float(periods[-1][2])
                                ),
                            )
                        if "optimizer_factor_score_period" not in tables:
                            audit["reason"] = (
                                "optimizer_factor_score_period_table_missing"
                            )
                        else:
                            score_rows = connection.execute(
                                """
                                SELECT score_run_id, score_name, signal_date,
                                       COUNT(DISTINCT ts_code),
                                       MAX(rowid) AS insertion_order
                                FROM optimizer_factor_score_period
                                GROUP BY score_run_id, score_name, signal_date
                                ORDER BY signal_date, insertion_order
                                """
                            ).fetchall()
                            if score_rows:
                                latest = score_rows[-1]
                                audit.update(
                                    score_periods=len(score_rows),
                                    latest_score_run_id=str(latest[0]),
                                    score_name=str(latest[1]),
                                    latest_score_date=str(latest[2]),
                                    latest_score_count=int(latest[3]),
                                    latest_score_insertion_order=int(latest[4]),
                                )
                            pool_ok = audit["latest_constituent_count"] == 500
                            score_ok = audit["latest_score_count"] == 500
                            audit["ready"] = bool(pool_ok and score_ok)
                            audit["reason"] = (
                                None
                                if audit["ready"]
                                else (
                                    "latest_csi500_constituent_count_is_not_500"
                                    if not pool_ok
                                    else "latest_factor_score_count_is_not_500"
                                )
                            )
            except (OSError, sqlite3.Error) as exc:
                audit["reason"] = (
                    f"warehouse_read_failed:{type(exc).__name__}:{exc}"
                )

        universe = {
            "id": "CSI500_ENH",
            "code": "000905.SH",
            "symbol": "000905.SH",
            "name": "\u4e2d\u8bc1500",
            "constituent_count": audit["latest_constituent_count"],
            "as_of": audit["constituent_end"],
            "start_date": audit["constituent_start"],
            "period_count": audit["constituent_periods"],
            "available": audit["latest_constituent_count"] == 500,
            "source": "research_warehouse.index_constituent_period",
        }
        scores: list[dict[str, Any]] = []
        if audit["latest_score_date"]:
            scores.append(
                {
                    "id": audit["score_name"],
                    "name": "\u56e0\u5b50\u5b9e\u9a8c\u5ba4\u5386\u53f2\u5f97\u5206",
                    "score_run_id": audit["latest_score_run_id"],
                    "frequency": "monthly",
                    "as_of": audit["latest_score_date"],
                    "constituent_count": audit["latest_score_count"],
                    "available": audit["latest_score_count"] == 500,
                    "source": (
                        "research_warehouse.optimizer_factor_score_period"
                    ),
                }
            )
        knowledge = {
            "version": None,
            "source_count": 0,
            "as_of": None,
            "available": False,
        }
        try:
            path = (
                PROJECT_ROOT
                / "model"
                / "portfolio_optimization"
                / "constraint_knowledge_base.json"
            )
            raw = json.loads(path.read_text(encoding="utf-8"))
            sources = raw.get("sources")
            knowledge.update(
                version=raw.get("version")
                or raw.get("knowledge_base_version")
                or raw.get("schema_version"),
                source_count=(
                    len(sources) if isinstance(sources, list) else 0
                ),
                as_of=raw.get("as_of") or raw.get("updated_at"),
                available=True,
            )
        except (OSError, ValueError):
            pass

        data_ready = bool(audit["ready"])
        defaults = {
            "universe": {
                "code": "000905.SH",
                "name": "\u4e2d\u8bc1500",
                "rebalance_frequency": "monthly",
                "holdings": 50,
                "score_source": scores[0]["id"] if scores else "",
            },
            "objective": {
                "type": "active_alpha",
                "alpha_scale": 3.0,
                "risk_aversion": 0.35,
                "turnover_penalty": 0.18,
                "cost_penalty": 1.0,
            },
            "holdings": {
                "long_only": True,
                "fully_invested": True,
                "min_weight": 0.002,
                "max_weight": 0.05,
            },
            "industry": {
                "classification": "SW_L1",
                "max_active_deviation": 0.03,
            },
            "style": {"max_abs_exposure": 0.14},
            "active_risk": {
                "tracking_error_limit": 0.09,
                "max_active_weight": 0.045,
                "covariance_model": "factor",
            },
            "trading": {
                "turnover_limit": 1.0,
                "transaction_cost_bps": 10.0,
            },
            "liquidity": {
                "max_adv_participation": 0.10,
                "exclude_suspended": True,
                "exclude_limit_locked": True,
            },
            "lists": {"include": "", "exclude": ""},
            "backtest": {
                "start": "20190531",
                "end": audit["constituent_end"] or "20260630",
            },
        }
        return {
            "status": (
                "ready"
                if solver_ready and data_ready
                else BLOCKED_SOLVER if not solver_ready else BLOCKED_DATA
            ),
            "api_version": API_VERSION,
            "default_mode": "joint_cardinality",
            "data_ready": data_ready,
            "block_reason": (
                audit["reason"]
                if solver_ready
                else "required_runtime_solvers_unavailable:"
                + ",".join(
                    name
                    for name, ready in (
                        ("SCIPY_HIGHS_MILP", highs_ready),
                        ("CLARABEL", clarabel_ready),
                    )
                    if not ready
                )
            ),
            "availability": audit,
            "data_status": audit,
            "universes": [universe],
            "asset_universes": [],
            "score_sources": scores,
            "factor_score_sources": [],
            "knowledge_base": knowledge,
            "knowledge_base_version": knowledge["version"],
            "defaults": defaults,
            "capabilities": {
                "llm": {
                    "configured": llm_ready,
                    "status": "READY" if llm_ready else BLOCKED_LLM,
                },
                "fixed_candidate_socp": {
                    "available_solvers": solvers,
                    "status": BLOCKED_SOLVER_CAPABILITY,
                    "reason": "deployed_runtime_does_not_use_pre_frozen_candidate_sets",
                    "fallback_allowed": False,
                },
                "joint_cardinality": {
                    **joint_solver_policy,
                    "required_runtime_solvers": [
                        "SCIPY_HIGHS_MILP",
                        "CLARABEL",
                    ],
                    "capability_status": (
                        "READY" if solver_ready else BLOCKED_SOLVER_CAPABILITY
                    ),
                    "status": (
                        "READY" if solver_ready else BLOCKED_SOLVER_CAPABILITY
                    ),
                    "execution_guarantee": (
                        "HiGHS certifies exact binary support against every linear mandate; "
                        "Clarabel then certifies the complete SOCP and independently recomputed residuals"
                    ),
                    "global_miqcp_optimality_claimed": False,
                    "fallback_allowed": False,
                },
                "warehouse": {
                    "configured": warehouse.is_file(),
                    "status": "READY" if data_ready else BLOCKED_DATA,
                    "audit": audit,
                },
            },
            "constraint_groups": [
                {"id": "holding", "label": "\u6301\u4ed3\u7ea6\u675f", "metrics": ["cardinality", "security_weight", "active_security_weight"]},
                {"id": "industry", "label": "\u884c\u4e1a\u7ea6\u675f", "metrics": ["active_exposure"]},
                {"id": "style", "label": "\u98ce\u683c\u7ea6\u675f", "metrics": ["active_exposure"]},
                {"id": "active_risk", "label": "\u4e3b\u52a8\u98ce\u9669", "metrics": ["tracking_error", "active_variance"]},
                {"id": "trading", "label": "\u4ea4\u6613\u7ea6\u675f", "metrics": ["one_way_turnover", "two_way_turnover", "transaction_cost"]},
                {"id": "liquidity", "label": "\u6d41\u52a8\u6027\u7ea6\u675f", "metrics": ["adv_participation", "days_to_liquidate", "minimum_adv"]},
                {"id": "list", "label": "\u540d\u5355\u7ea6\u675f", "metrics": ["blacklist", "whitelist", "forced_include", "forced_exclude"]},
            ],
            "state_machine": [
                "DRAFT_RECEIVED", "RETRIEVAL_COMPLETE", "PARSED",
                "SCHEMA_VALIDATED", "SEMANTIC_VALIDATED",
                "FEASIBILITY_ANALYZED", AWAITING_CONFIRMATION, CONFIRMED,
                QUEUED, SOLVING, SOLVED, AUDITED,
            ],
            "policies": {
                "optimizer_mode": "joint_cardinality",
                "candidate_set_pre_frozen": False,
                "llm_emits_weights": False,
                "test_set_usage": "report_only",
                "fallback_allowed": False,
                "edited_draft_requires_reconfirmation": True,
                "state_storage": "single_sqlite_database",
            },
        }

    def strategy_snapshot(self) -> dict[str, Any]:
        """Read the audited CSI 500 cross-section and governed optimizer result.

        Runs are selected by solver certification and mandate completeness. Test
        Sharpe/return is deliberately excluded from the publication rule.
        """

        warehouse = Path(
            os.getenv(
                "RESEARCH_WAREHOUSE_DB",
                str(PROJECT_ROOT / "database" / "research_warehouse.db"),
            )
        ).expanduser().resolve()
        if not warehouse.is_file():
            return {
                "status": BLOCKED_DATA,
                "message": "research_warehouse_missing",
                "assets": [],
                "selected_run": None,
            }

        assets: list[dict[str, Any]] = []
        factor_weight_history: list[dict[str, Any]] = []
        score_meta: dict[str, Any] = {}
        try:
            with sqlite3.connect(
                "file:" + warehouse.as_posix() + "?mode=ro",
                uri=True,
                timeout=30.0,
            ) as connection:
                connection.row_factory = sqlite3.Row
                latest = None
                latest_date_row = connection.execute(
                    "SELECT MAX(signal_date) FROM optimizer_factor_score_period"
                ).fetchone()
                cursor_date = (
                    str(latest_date_row[0])
                    if latest_date_row is not None and latest_date_row[0]
                    else None
                )
                attempts = 0
                while cursor_date and attempts < 36:
                    latest = connection.execute(
                        """
                        SELECT score_run_id, score_name, signal_date, maturity_date,
                               factor_weights_json, neutralization_json,
                               COUNT(DISTINCT ts_code) AS asset_count
                        FROM optimizer_factor_score_period
                        WHERE signal_date=?
                        GROUP BY score_run_id, score_name, signal_date
                        HAVING COUNT(DISTINCT ts_code)=500
                        ORDER BY MAX(rowid) DESC
                        LIMIT 1
                        """,
                        (cursor_date,),
                    ).fetchone()
                    if latest is not None:
                        break
                    previous = connection.execute(
                        """
                        SELECT MAX(signal_date)
                        FROM optimizer_factor_score_period
                        WHERE signal_date<?
                        """,
                        (cursor_date,),
                    ).fetchone()
                    cursor_date = (
                        str(previous[0])
                        if previous is not None and previous[0]
                        else None
                    )
                    attempts += 1
                if latest is None:
                    return {
                        "status": BLOCKED_DATA,
                        "message": "audited_csi500_score_cross_section_missing",
                        "assets": [],
                        "selected_run": None,
                    }
                signal_date = str(latest["signal_date"])
                score_run_id = str(latest["score_run_id"])
                score_meta = {
                    "score_run_id": score_run_id,
                    "score_name": str(latest["score_name"]),
                    "signal_date": signal_date,
                    "maturity_date": latest["maturity_date"],
                    "asset_count": int(latest["asset_count"]),
                    "factor_weights": _loads(latest["factor_weights_json"], {}),
                    "neutralization": _loads(latest["neutralization_json"], {}),
                    "source": "research_warehouse.optimizer_factor_score_period",
                }
                valuation_date = connection.execute(
                    "SELECT MAX(trade_date) FROM stock_valuation_daily WHERE trade_date<=?",
                    (signal_date,),
                ).fetchone()[0]
                market_date = connection.execute(
                    "SELECT MAX(trade_date) FROM stock_ohlcv_daily WHERE trade_date<=?",
                    (signal_date,),
                ).fetchone()[0]
                rows = connection.execute(
                    """
                    SELECT s.ts_code,
                           COALESCE(sm.stock_name, md.stock_name, s.ts_code) AS stock_name,
                           s.industry, s.score, s.raw_score, s.benchmark_weight,
                           s.style_size, s.style_value, s.style_momentum,
                           s.style_liquidity, v.pe_ttm, v.pb, v.ps_ttm,
                           v.dv_ttm, v.total_mv, v.circ_mv, v.turnover_rate,
                           md.amount, md.pct_chg, md.close, md.up_limit,
                           md.down_limit, md.suspend_timing, md.trade_date AS market_date
                    FROM optimizer_factor_score_period s
                    LEFT JOIN security_master sm ON sm.ts_code=s.ts_code
                    LEFT JOIN stock_valuation_daily v
                      ON v.ts_code=s.ts_code AND v.trade_date=?
                    LEFT JOIN stock_ohlcv_daily md
                      ON md.ts_code=s.ts_code AND md.trade_date=?
                    WHERE s.score_run_id=? AND s.signal_date=?
                    ORDER BY s.score DESC, s.ts_code
                    """,
                    (valuation_date, market_date, score_run_id, signal_date),
                ).fetchall()
                for row in rows:
                    close = row["close"]
                    suspended = bool(str(row["suspend_timing"] or "").strip())
                    limit_locked = bool(
                        close is not None
                        and (
                            row["up_limit"] is not None
                            and float(close) >= float(row["up_limit"]) - 1e-10
                            or row["down_limit"] is not None
                            and float(close) <= float(row["down_limit"]) + 1e-10
                        )
                    )
                    assets.append(
                        {
                            "code": str(row["ts_code"]),
                            "name": str(row["stock_name"] or row["ts_code"]),
                            "industry": str(row["industry"] or "未知"),
                            "score": row["score"],
                            "raw_score": row["raw_score"],
                            "benchmark_weight": row["benchmark_weight"],
                            "style": {
                                "size": row["style_size"],
                                "value": row["style_value"],
                                "momentum": row["style_momentum"],
                                "liquidity": row["style_liquidity"],
                            },
                            "valuation": {
                                "pe_ttm": row["pe_ttm"],
                                "pb": row["pb"],
                                "ps_ttm": row["ps_ttm"],
                                "dividend_yield": row["dv_ttm"],
                                "total_mv": row["total_mv"],
                                "circ_mv": row["circ_mv"],
                            },
                            "market": {
                                "date": row["market_date"],
                                "amount": row["amount"],
                                "turnover_rate": row["turnover_rate"],
                                "pct_chg": row["pct_chg"],
                            },
                            "tradable": not suspended and not limit_locked,
                            "suspended": suspended,
                            "limit_locked": limit_locked,
                        }
                    )
                history_rows = connection.execute(
                    """
                    SELECT signal_date, factor_weights_json,
                           COUNT(DISTINCT ts_code) AS asset_count
                    FROM optimizer_factor_score_period
                    WHERE score_run_id=?
                    GROUP BY signal_date, factor_weights_json
                    ORDER BY signal_date
                    """,
                    (score_run_id,),
                ).fetchall()
                factor_weight_history = [
                    {
                        "signal_date": str(row["signal_date"]),
                        "asset_count": int(row["asset_count"]),
                        "weights": _loads(row["factor_weights_json"], {}),
                    }
                    for row in history_rows
                ]
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            return {
                "status": BLOCKED_DATA,
                "message": f"strategy_snapshot_read_failed:{type(exc).__name__}:{exc}",
                "assets": [],
                "selected_run": None,
            }

        def development_gate(
            result: Mapping[str, Any],
        ) -> tuple[bool, float | None, dict[str, Any]]:
            """Gate and rank using train+validation only; never read test."""

            audit = (
                result.get("backtest_audit")
                if isinstance(result.get("backtest_audit"), Mapping) else {}
            )
            split_metrics = (
                audit.get("metrics_by_split")
                if isinstance(audit.get("metrics_by_split"), Mapping)
                else result.get("metrics_by_split")
            )
            split_metrics = (
                split_metrics if isinstance(split_metrics, Mapping) else {}
            )
            rows: dict[str, dict[str, Any]] = {}
            reasons: list[str] = []
            for split, minimum_periods in (
                ("train", 24), ("validation", 12),
            ):
                raw = (
                    split_metrics.get(split)
                    if isinstance(split_metrics.get(split), Mapping) else {}
                )
                optimized = (
                    raw.get("optimized")
                    if isinstance(raw.get("optimized"), Mapping)
                    else raw.get("constrained_optimizer")
                    if isinstance(
                        raw.get("constrained_optimizer"), Mapping
                    )
                    else {}
                )
                benchmark = (
                    raw.get("benchmark")
                    if isinstance(raw.get("benchmark"), Mapping) else {}
                )
                periods = int(optimized.get("periods") or 0)
                annual_return = optimized.get("annual_return")
                benchmark_return = benchmark.get("annual_return")
                sharpe = optimized.get("sharpe")
                information_ratio = optimized.get("information_ratio")
                annual_excess = (
                    float(annual_return) - float(benchmark_return)
                    if annual_return is not None
                    and benchmark_return is not None else None
                )
                rows[split] = {
                    "periods": periods,
                    "annual_return": annual_return,
                    "benchmark_annual_return": benchmark_return,
                    "annual_excess_return": annual_excess,
                    "sharpe": sharpe,
                    "information_ratio": information_ratio,
                }
                if periods < minimum_periods:
                    reasons.append(
                        f"{split}_periods_below_{minimum_periods}"
                    )
                if (
                    annual_excess is None or annual_excess <= 0.0
                    or information_ratio is None
                    or float(information_ratio) <= 0.0
                ):
                    reasons.append(f"{split}_positive_quality_gate_failed")
                benchmark_sharpe = benchmark.get("sharpe")
                relative_sharpe = (
                    float(sharpe) - float(benchmark_sharpe)
                    if sharpe is not None and benchmark_sharpe is not None
                    else None
                )
                rows[split]["benchmark_sharpe"] = benchmark_sharpe
                rows[split]["relative_sharpe_improvement"] = relative_sharpe
                if relative_sharpe is None or relative_sharpe <= 0.0:
                    reasons.append(f"{split}_relative_sharpe_gate_failed")
            formal = bool(
                result.get("formal_metrics_valid") is True
                and audit.get("formal_metrics_valid") is True
            )
            if not formal:
                reasons.append("formal_complete_window_required")
            score = None
            if not reasons:
                train = rows["train"]
                validation = rows["validation"]
                score = min(
                    float(train["information_ratio"]),
                    float(validation["information_ratio"]),
                ) + 0.25 * min(
                    float(train["relative_sharpe_improvement"]),
                    float(validation["relative_sharpe_improvement"]),
                ) + 0.50 * min(
                    float(train["annual_excess_return"]),
                    float(validation["annual_excess_return"]),
                )
            return not reasons, score, {
                "status": "passed" if not reasons else "rejected",
                "reasons": reasons,
                "train": rows.get("train"),
                "validation": rows.get("validation"),
                "selection_uses_test_metrics": False,
                "score": score,
            }

        candidates: list[dict[str, Any]] = []
        for summary in self.store.list_runs(limit=100):
            if str(summary.get("status")) != AUDITED:
                continue
            full = self.store.get_run(str(summary["run_id"]), include_audit=False)
            if not full or not isinstance(full.get("result"), Mapping):
                continue
            result = dict(full["result"])
            solver = result.get("solver") if isinstance(result.get("solver"), Mapping) else {}
            phase_ii = solver.get("phase_ii") if isinstance(solver.get("phase_ii"), Mapping) else {}
            portfolio = result.get("portfolio") if isinstance(result.get("portfolio"), Mapping) else {}
            weights = result.get("weights")
            if not isinstance(weights, list):
                weights = portfolio.get("weights")
            if not isinstance(weights, list):
                weights = []
            certified = bool(
                solver.get("certified") is True or phase_ii.get("certified") is True
            )
            fallback_used = bool(
                result.get("fallback_used") is True or solver.get("fallback_used") is True
            )
            weight_sum = sum(
                float(row.get("weight") or 0.0)
                for row in weights
                if isinstance(row, Mapping)
            )
            if certified and not fallback_used and len(weights) == 50 and abs(weight_sum - 1.0) <= 1e-6:
                passed, development_score, development_audit = (
                    development_gate(result)
                )
                publication_passed, publication_audit = (
                    _sealed_test_publication_gate(result)
                )
                candidates.append(
                    {
                        "summary": summary,
                        "run": full,
                        "result": result,
                        "formal": result.get("formal_metrics_valid") is True,
                        "development_gate_passed": passed,
                        "development_score": development_score,
                        "development_audit": development_audit,
                        "publication_gate_passed": publication_passed,
                        "publication_audit": publication_audit,
                    }
                )
        eligible = [
            candidate for candidate in candidates
            if candidate["development_gate_passed"]
            and candidate["publication_gate_passed"]
        ]
        eligible.sort(
            key=lambda candidate: (
                float(candidate["development_score"]),
                str(candidate["summary"].get("created_at") or ""),
            ),
            reverse=True,
        )
        selected = eligible[0] if eligible else None
        latest_diagnostic = candidates[0] if candidates else None
        selected_run: dict[str, Any] | None = None
        selected_weights: dict[str, Mapping[str, Any]] = {}
        if selected is not None:
            result = selected["result"]
            portfolio = result.get("portfolio") if isinstance(result.get("portfolio"), Mapping) else {}
            weights = result.get("weights")
            if not isinstance(weights, list):
                weights = portfolio.get("weights", [])
            selected_weights = {
                str(row.get("code") or row.get("ts_code")): row
                for row in weights
                if isinstance(row, Mapping)
            }
            selected_run = {
                "run_id": selected["run"]["run_id"],
                "run_name": selected["run"]["run_name"],
                "created_at": selected["run"]["created_at"],
                "updated_at": selected["run"]["updated_at"],
                "status": "completed",
                "governance_status": (
                    "formal_validated" if selected["formal"] else "research_diagnostic"
                ),
                "selection_basis": (
                    "best_train_validation_robust_score_after_positive_gates"
                ),
                "selection_uses_test_metrics": False,
                "development_gate": selected["development_audit"],
                "sealed_test_publication_gate": (
                    selected["publication_audit"]
                ),
                "result": result,
            }
        for asset in assets:
            weight = selected_weights.get(asset["code"])
            asset["selected"] = weight is not None
            asset["portfolio_weight"] = None if weight is None else weight.get("weight")
            asset["active_weight"] = None if weight is None else weight.get("active_weight")

        industry_map: dict[str, dict[str, Any]] = {}
        for asset in assets:
            industry = str(asset["industry"])
            bucket = industry_map.setdefault(
                industry,
                {
                    "industry": industry,
                    "asset_count": 0,
                    "selected_count": 0,
                    "benchmark_weight": 0.0,
                    "portfolio_weight": 0.0,
                    "score_total": 0.0,
                },
            )
            bucket["asset_count"] += 1
            bucket["selected_count"] += int(bool(asset["selected"]))
            bucket["benchmark_weight"] += float(asset["benchmark_weight"] or 0.0)
            bucket["portfolio_weight"] += float(asset["portfolio_weight"] or 0.0)
            bucket["score_total"] += float(asset["score"] or 0.0)
        industries = []
        for bucket in industry_map.values():
            bucket["average_score"] = bucket.pop("score_total") / max(bucket["asset_count"], 1)
            bucket["active_weight"] = bucket["portfolio_weight"] - bucket["benchmark_weight"]
            industries.append(bucket)
        industries.sort(key=lambda row: row["benchmark_weight"], reverse=True)


        published_result = (
            selected_run.get("result")
            if isinstance(selected_run, Mapping)
            and isinstance(selected_run.get("result"), Mapping)
            else (
                latest_diagnostic["result"]
                if selected is None
                and latest_diagnostic is not None
                and isinstance(latest_diagnostic.get("result"), Mapping)
                else {}
            )
        )
        published_metrics = (
            published_result.get("metrics")
            if isinstance(published_result, Mapping)
            and isinstance(published_result.get("metrics"), Mapping)
            else {}
        )
        optimizer_metrics = (
            published_metrics.get("constrained_optimizer")
            if isinstance(published_metrics.get("constrained_optimizer"), Mapping)
            else {}
        )
        benchmark_metrics = (
            published_metrics.get("benchmark")
            if isinstance(published_metrics.get("benchmark"), Mapping)
            else {}
        )
        timing_payload = (
            published_result.get("timing_overlay")
            if isinstance(published_result, Mapping)
            and isinstance(published_result.get("timing_overlay"), Mapping)
            else {}
        )
        latest_timing = (
            timing_payload.get("latest")
            if isinstance(timing_payload.get("latest"), Mapping)
            else {}
        )
        factor_weights = (
            score_meta.get("factor_weights")
            if isinstance(score_meta.get("factor_weights"), Mapping) else {}
        )
        framework = {
            "version": "portfolio-framework/2026-08-15-three-layer-incremental",
                        "policy": "keep_current_best_strategy;add_framework_audit_and_ui_only",
                                    "scope": "generic_optimizer_kernel + csi500_timing + csi500_stock_level_index_enhancement",
                                                "components": [
                                                                {
                                                                                    "id": "generic_optimizer",
                                                                                                        "name": "优化求解器",
                                                                                                                            "status": "active",
                                                                                                                                                "input_contract": [
                                                                                                                                                                        "资产代码",
                                                                                                                                                                                                "历史得分",
                                                                                                                                                                                                                        "历史收益/风险矩阵",
                                                                                                                                                                                                                                                "历史持仓",
                                                                                                                                                                                                                                                                        "基准权重",
                                                                                                                                                                                                                                                                                                "约束参数",
                                                                                                                                                                                                                                                                                                                    ],
                                                                                                                                                                                                                                                                                                                                        "output_contract": [
                                                                                                                                                                                                                                                                                                                                                                "最新目标持仓",
                                                                                                                                                                                                                                                                                                                                                                                        "组合权重",
                                                                                                                                                                                                                                                                                                                                                                                                                "主动暴露",
                                                                                                                                                                                                                                                                                                                                                                                                                                        "约束余量",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                "求解审计",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    ],
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        "solver_stack": ["HiGHS整数规划", "Clarabel二阶锥优化", "约束审计"],
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            "constraints": [
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    "权重上下限",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            "行业偏离",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    "风格暴露",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            "跟踪误差",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    "换手率",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            "白名单/黑名单",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    "流动性约束",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        ],
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        },
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        {
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            "id": "timing_framework",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                "name": "宽基择时",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    "status": "active_monitoring",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        "left_side": ["估值位置", "宏观状态"],
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            "right_side": ["趋势强度", "情绪资金"],
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                "latest": {
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        "left_score": latest_timing.get("left_score"),
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                "right_score": latest_timing.get("right_score"),
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        "active_side": latest_timing.get("active_side"),
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                "timing_position": latest_timing.get("timing_position"),
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        "risk_budget_multiplier": latest_timing.get("risk_budget_multiplier"),
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            },
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                "current_use": "用于宽基仓位调节和指数增强主动风险预算，不替代个股Alpha得分。",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                },
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                {
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    "id": "index_enhancement",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        "name": "中证500指数增强",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            "status": "active",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                "benchmark": "000905.SH",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    "target_holdings": sum(bool(row["selected"]) for row in assets),
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        "asset_count": len(assets),
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            "linked_modules": [
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    "因子实验室champion得分",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            "行业轮动 industry_rotation_v4",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    "SmartBeta暴露",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            "组合优化约束求解器",
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                ],
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    "performance": {
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            "annual_return": optimizer_metrics.get("annual_return"),
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    "benchmark_annual_return": benchmark_metrics.get("annual_return"),
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            "annual_excess_return": optimizer_metrics.get("annual_excess_return"),
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    "sharpe": optimizer_metrics.get("sharpe"),
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            "information_ratio": optimizer_metrics.get("information_ratio"),
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    "tracking_error": optimizer_metrics.get("tracking_error"),
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        },
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        },
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    ],
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                "module_linkage": [
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                {"source": "因子实验室", "target": "个股Alpha得分", "field": score_meta.get("score_name")},
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                {"source": "行业轮动", "target": "行业Alpha预算", "field": "industry_rotation_v4"},
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                {"source": "数据看板", "target": "收益/风险矩阵", "field": "point-in-time returns"},
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                {"source": "历史持仓", "target": "换手约束", "field": "previous optimized weights"},
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                {"source": "宽基择时", "target": "风险预算", "field": latest_timing.get("active_side")},
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                {"source": "优化求解器", "target": "最新50只持仓", "field": "certified target weights"},
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            ],
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        "factor_model": {
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        "score_run_id": score_meta.get("score_run_id"),
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        "score_name": score_meta.get("score_name"),
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        "signal_date": score_meta.get("signal_date"),
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        "factor_count": len(factor_weights),
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        "industry_rotation_enabled": "industry_rotation_v4" in set(factor_weights),
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        "factor_weights": copy.deepcopy(dict(factor_weights)),
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        "history_periods": len(factor_weight_history),
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    },
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                "governance": {
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                "fallback_allowed": False,
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                "selection_uses_test_metrics": False,
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                "current_best_run_locked": selected_run.get("run_id") if isinstance(selected_run, Mapping) else None,
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                "ui_change_affects_returns": False,
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            },
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    }

        broad_index_timing_path = PROJECT_ROOT / "board" / "quant_strategy_agent" / "data" / "broad_index_timing_snapshot.json"
        if broad_index_timing_path.is_file():
            try:
                broad_index_timing = json.loads(broad_index_timing_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as exc:
                broad_index_timing = {"status": BLOCKED_DATA, "message": f"broad_index_timing_read_failed:{type(exc).__name__}:{exc}"}
        else:
            broad_index_timing = {"status": BLOCKED_DATA, "message": "broad_index_timing_snapshot_missing"}

        research_snapshot_path = PROJECT_ROOT / "board" / "quant_strategy_agent" / "data" / "portfolio_optimization_snapshot.json"
        optimizer_research_snapshot: dict[str, Any] = {}
        if research_snapshot_path.is_file():
            try:
                raw_research = json.loads(research_snapshot_path.read_text(encoding="utf-8"))
                raw_optimization = raw_research.get("optimization") if isinstance(raw_research.get("optimization"), Mapping) else {}
                raw_backtest = raw_research.get("backtest") if isinstance(raw_research.get("backtest"), Mapping) else {}
                optimizer_research_snapshot = {
                    "status": raw_research.get("status"),
                    "engine_version": raw_research.get("engine_version"),
                    "generated_at": raw_research.get("generated_at"),
                    "data_as_of": raw_research.get("data_as_of"),
                    "optimization": {
                        "leaderboard": raw_optimization.get("leaderboard", []),
                        "efficient_frontier": raw_optimization.get("efficient_frontier", []),
                        "solver_benchmark": raw_optimization.get("solver_benchmark", []),
                    },
                    "backtest": {
                        "cost_sensitivity_test": raw_backtest.get("cost_sensitivity_test", []),
                        "stress_scenarios": raw_backtest.get("stress_scenarios", []),
                        "promotion_gate": raw_backtest.get("promotion_gate", {}),
                    },
                }
            except (OSError, ValueError, TypeError) as exc:
                optimizer_research_snapshot = {
                    "status": BLOCKED_DATA,
                    "message": f"portfolio_optimization_snapshot_read_failed:{type(exc).__name__}:{exc}",
                }
        else:
            optimizer_research_snapshot = {
                "status": BLOCKED_DATA,
                "message": "portfolio_optimization_snapshot_missing",
            }

        return {
            "status": "ready" if len(assets) == 500 else BLOCKED_DATA,
            "universe": {
                "code": "000905.SH",
                "name": "中证500",
                "asset_count": len(assets),
                "selected_count": sum(bool(row["selected"]) for row in assets),
            },
            "score": score_meta,
            "assets": assets,
            "industry_summary": industries,
            "factor_weight_history": factor_weight_history,
            "framework": framework,
            "optimizer_research_snapshot": optimizer_research_snapshot,
            "broad_index_timing": broad_index_timing,
            "selected_run": selected_run,
            "latest_diagnostic_run": (
                {
                    "run_id": latest_diagnostic["run"]["run_id"],
                    "run_name": latest_diagnostic["run"]["run_name"],
                    "created_at": latest_diagnostic["run"]["created_at"],
                    "status": "research_diagnostic",
                    "governance_status": "research_diagnostic_not_published",
                    "development_gate": (
                        latest_diagnostic["development_audit"]
                    ),
                    "sealed_test_publication_gate": (
                        latest_diagnostic["publication_audit"]
                    ),
                    "result": latest_diagnostic["result"],
                }
                if selected is None and latest_diagnostic is not None
                else None
            ),
            "governance": {
                "selection_uses_test_metrics": False,
                "sealed_test_role": "post_selection_production_veto_only",
                "selection_priority": [
                    "solver_certified",
                    "exact_50_support",
                    "all_constraints_audited",
                    "formal_window_validity",
                    "positive_train_and_validation_excess_ir_sharpe",
                    "robust_train_validation_score",
                ],
                "fallback_allowed": False,
                "candidate_count": len(candidates),
                "eligible_candidate_count": len(eligible),
                "diagnostic_runs_are_never_published_as_best": True,
            },
            "data_quality": {
                "exact_500": len(assets) == 500,
                "score_complete": sum(row["score"] is not None for row in assets),
                "industry_complete": sum(row["industry"] != "未知" for row in assets),
                "valuation_complete": sum(
                    row["valuation"]["total_mv"] is not None for row in assets
                ),
                "market_complete": sum(row["market"]["amount"] is not None for row in assets),
                "tradable_count": sum(bool(row["tradable"]) for row in assets),
            },
        }

    def _legacy_bootstrap(self) -> dict[str, Any]:
        solvers = self._discover_solvers()
        solver_ready = {
            "SCIPY_HIGHS_MILP", "CLARABEL"
        }.issubset(solvers)
        joint_solver_policy = self._compiler_module().build_solver_policy(
            "joint_cardinality", solvers
        )
        llm_ready = bool(
            (self.llm_client is not None)
            or ((os.getenv("AI_ROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")) and (os.getenv("AI_ROUTER_URL") or os.getenv("AI_ROUTER_BASE_URL")))
        )
        warehouse = Path(
            os.getenv("RESEARCH_WAREHOUSE_DB", str(PROJECT_ROOT / "database" / "research_warehouse.db"))
        )
        return {
            "status": "ready" if solver_ready else BLOCKED_SOLVER,
            "api_version": API_VERSION,
            "defaults": {
                "universe": "CSI500_ENH",
                "benchmark_id": "000905.SH",
                "score_artifact_id": "artifact.factor_lab.csi500.monthly",
                "rebalance_frequency": "monthly",
                "target_holdings": 50,
                "mode": "joint_cardinality",
                "run_name": "中证500组合优化",
            },
            "capabilities": {
                "llm": {"configured": llm_ready, "status": "READY" if llm_ready else BLOCKED_LLM},
                "fixed_candidate_socp": {
                    "available_solvers": solvers,
                    "status": BLOCKED_SOLVER_CAPABILITY,
                    "reason": "deployed_runtime_does_not_use_pre_frozen_candidate_sets",
                    "fallback_allowed": False,
                },
                "joint_cardinality": {
                    **joint_solver_policy,
                    "required_runtime_solvers": ["SCIPY_HIGHS_MILP", "CLARABEL"],
                    "capability_status": (
                        "READY" if solver_ready else BLOCKED_SOLVER_CAPABILITY
                    ),
                    "status": (
                        "READY" if solver_ready else BLOCKED_SOLVER_CAPABILITY
                    ),
                    "global_miqcp_optimality_claimed": False,
                    "fallback_allowed": False,
                },
                "warehouse": {
                    "configured": warehouse.is_file(),
                    "status": "AVAILABLE_REQUIRES_RUN_LEVEL_PIT_AUDIT" if warehouse.is_file() else BLOCKED_DATA,
                },
            },
            "constraint_groups": [
                {"id": "holding", "label": "持仓约束", "metrics": ["cardinality", "security_weight", "active_security_weight"]},
                {"id": "industry", "label": "行业约束", "metrics": ["active_exposure"]},
                {"id": "style", "label": "风格约束", "metrics": ["active_exposure"]},
                {"id": "active_risk", "label": "主动风险", "metrics": ["tracking_error", "active_variance"]},
                {"id": "trading", "label": "交易约束", "metrics": ["one_way_turnover", "two_way_turnover", "transaction_cost"]},
                {"id": "liquidity", "label": "流动性约束", "metrics": ["adv_participation", "days_to_liquidate", "minimum_adv"]},
                {"id": "list", "label": "名单约束", "metrics": ["blacklist", "whitelist", "forced_include", "forced_exclude"]},
            ],
            "state_machine": [
                "DRAFT_RECEIVED",
                "RETRIEVAL_COMPLETE",
                "PARSED",
                "SCHEMA_VALIDATED",
                "SEMANTIC_VALIDATED",
                "FEASIBILITY_ANALYZED",
                AWAITING_CONFIRMATION,
                CONFIRMED,
                QUEUED,
                SOLVING,
                SOLVED,
                AUDITED,
            ],
            "policies": {
                "llm_emits_weights": False,
                "test_set_usage": "report_only",
                "fallback_allowed": False,
                "edited_draft_requires_reconfirmation": True,
                "state_storage": "single_sqlite_database",
            },
        }

    @staticmethod
    def _constraint_cards(mandate: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        constraints = (
            mandate.get("constraints")
            if isinstance(mandate, Mapping)
            else None
        )
        cards: list[dict[str, Any]] = []
        if not isinstance(constraints, list):
            return cards
        category_map = {"holding": "holdings", "list": "lists"}
        for index, item in enumerate(constraints):
            if not isinstance(item, Mapping):
                continue
            scope = (
                copy.deepcopy(dict(item.get("scope") or {}))
                if isinstance(item.get("scope"), Mapping)
                else {}
            )
            metric = str(scope.get("metric") or "")
            scope_parts = [
                str(value)
                for key, value in scope.items()
                if key != "metric" and value not in (None, "", [], {})
            ]
            scope_label = metric
            if scope_parts:
                scope_label += ":" + ",".join(scope_parts)
            lower, upper = item.get("lower"), item.get("upper")
            if lower is not None and upper is not None:
                operator = "=" if lower == upper else "between"
            elif upper is not None:
                operator = "<="
            elif lower is not None:
                operator = ">="
            else:
                operator = "in" if "security_set" in scope else "="
            dependencies = item.get("data_dependencies")
            evidence = item.get("evidence")
            cards.append(
                {
                    "id": str(item.get("id") or f"constraint.{index + 1}"),
                    "name": str(item.get("name") or item.get("id") or f"\u7ea6\u675f {index + 1}"),
                    "category": category_map.get(
                        str(item.get("type") or ""), str(item.get("type") or "")
                    ),
                    "scope": scope_label or "portfolio",
                    "operator": operator,
                    "lower": lower,
                    "upper": upper,
                    "unit": item.get("unit") or "",
                    "hard": item.get("hard") is not False,
                    "priority": "\u5fc5\u987b" if int(item.get("priority") or 1) == 1 else str(item.get("priority")),
                    "formula": item.get("formula") or "",
                    "data_dependency": ", ".join(
                        str(value) for value in dependencies
                    ) if isinstance(dependencies, list) else str(dependencies or ""),
                    "reference": _dumps(evidence) if isinstance(evidence, list) else "",
                    "_scope_payload": scope,
                    "_constraint_payload": copy.deepcopy(dict(item)),
                }
            )
        return cards

    @staticmethod
    def _cards_to_mandate(
        cards: Sequence[Any],
        base_config: Mapping[str, Any],
        *,
        universe: str,
        score_source: str,
    ) -> dict[str, Any]:
        type_map = {"holdings": "holding", "lists": "list"}
        converted: list[dict[str, Any]] = []
        for index, raw in enumerate(cards):
            if not isinstance(raw, Mapping):
                raise ValueError(f"constraint_{index + 1}_must_be_an_object")
            original = (
                copy.deepcopy(dict(raw.get("_constraint_payload") or {}))
                if isinstance(raw.get("_constraint_payload"), Mapping)
                else {}
            )
            scope = (
                copy.deepcopy(dict(raw.get("_scope_payload") or {}))
                if isinstance(raw.get("_scope_payload"), Mapping)
                else {}
            )
            category = type_map.get(
                str(raw.get("category") or original.get("type") or ""),
                str(raw.get("category") or original.get("type") or ""),
            )
            metric_defaults = {
                "holding": "security_weight",
                "industry": "active_exposure",
                "style": "active_exposure",
                "active_risk": "tracking_error",
                "trading": "one_way_turnover",
                "liquidity": "adv_participation",
                "list": "blacklist",
            }
            scope_text = str(raw.get("scope") or "").strip()
            if not scope:
                scope = {"metric": metric_defaults.get(category, "")}
            if not scope.get("metric"):
                scope["metric"] = metric_defaults.get(category, "")
            if scope_text and scope_text not in {"portfolio", "universe", "all"}:
                head = scope_text.split(":", 1)[0].strip()
                known = {
                    "cardinality", "security_weight", "active_security_weight",
                    "active_exposure", "tracking_error", "active_variance",
                    "one_way_turnover", "two_way_turnover",
                    "adv_participation", "days_to_liquidate", "minimum_adv",
                    "blacklist", "whitelist", "forced_include", "forced_exclude",
                }
                if head in known:
                    scope["metric"] = head
            dependencies = raw.get("data_dependency")
            if isinstance(dependencies, str):
                dependencies = [
                    item.strip()
                    for item in dependencies.split(",")
                    if item.strip()
                ]
            evidence = original.get("evidence")
            if not isinstance(evidence, list):
                try:
                    decoded = json.loads(str(raw.get("reference") or ""))
                    evidence = decoded if isinstance(decoded, list) else []
                except ValueError:
                    evidence = []
            priority = raw.get("priority")
            numeric_priority = (
                1 if str(priority) in {"\u5fc5\u987b", "1", "must"} else 2
            )
            item = {
                **original,
                "id": str(raw.get("id") or original.get("id") or f"constraint.{index + 1}"),
                "type": category,
                "scope": scope,
                "lower": raw.get("lower"),
                "upper": raw.get("upper"),
                "unit": str(raw.get("unit") or original.get("unit") or ""),
                "hard": raw.get("hard") is not False,
                "penalty": original.get("penalty"),
                "priority": numeric_priority,
                "formula": str(raw.get("formula") or original.get("formula") or ""),
                "data_dependencies": dependencies if isinstance(dependencies, list) else [],
                "evidence": evidence,
            }
            converted.append(item)
        universe_config = (
            base_config.get("universe")
            if isinstance(base_config.get("universe"), Mapping)
            else {}
        )
        frequency = str(
            universe_config.get("rebalance_frequency") or "monthly"
        )
        return {
            "schema_version": "OptimizationMandate/v1",
            "mode": "joint_cardinality",
            "objective": {
                "type": "benchmark_relative_alpha",
                "benchmark_id": universe or "000905.SH",
                "score_artifact_id": score_source or universe_config.get("score_source"),
                "rebalance_frequency": frequency,
                "risk_model_id": "barra_like_pit_v1",
            },
            "constraints": converted,
            "retrieval_source_ids": sorted(
                {
                    str(evidence.get("source_id"))
                    for item in converted
                    for evidence in item.get("evidence", [])
                    if (
                        isinstance(evidence, Mapping)
                        and evidence.get("source_id")
                        and evidence.get("source_id") != "user_supplied"
                    )
                }
            ),
            "assumptions": [
                "\u6240\u6709\u7ea6\u675f\u4ec5\u4f7f\u7528\u4fe1\u53f7\u65e5\u53ef\u5f97\u7684PIT\u6570\u636e",
                "HiGHS\u8054\u5408\u6c42\u89e3\u5168\u90e8\u7ebf\u6027\u7ea6\u675f\u4e0e\u7cbe\u786e\u6301\u4ed3\u652f\u6301\uff0cClarabel\u8ba4\u8bc1\u5b8c\u6574SOCP\u7ea6\u675f",
                "\u5019\u9009\u652f\u6301\u4e0d\u5728\u6c42\u89e3\u524d\u9884\u51bb\u7ed3\uff0c\u4e0d\u5ba3\u79f0\u5168\u5c40MIQCP\u6700\u4f18",
            ],
        }


    def plan_options(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
        compiler = self._compiler_module()
        raw_request = str(
            payload.get("instruction")
            or payload.get("raw_request")
            or payload.get("prompt")
            or payload.get("text")
            or ""
        ).strip()
        mode = str(payload.get("mode") or "joint_cardinality")
        context = (
            copy.deepcopy(dict(payload.get("context") or {}))
            if isinstance(payload.get("context"), Mapping)
            else {}
        )
        for key in (
            "base_config", "universe", "rebalance_frequency",
            "knowledge_base_version",
        ):
            if key in payload:
                context[key] = copy.deepcopy(payload[key])
        planned = compiler.generate_mandate_plan_options(
            raw_request,
            llm_client=self.llm_client,
            require_llm=True,
            mode=mode,
            available_solvers=self._discover_solvers(),
            context=context,
        )
        status = str(planned.get("status") or BLOCKED_SCHEMA)
        output = copy.deepcopy(dict(planned))
        output["compiler_status"] = status
        output["status"] = (
            status if status == getattr(compiler, "AWAITING_PLAN_SELECTION", "AWAITING_PLAN_SELECTION")
            else _public_compiler_status(status)
        )
        output["planner"] = "ai_router_mandate_plan_options"
        output["mode"] = mode
        output["raw_request_hash"] = _stable_hash({"raw_request": raw_request, "mode": mode})
        return output, 200

    def interpret(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
        compiler = self._compiler_module()
        raw_request = str(
            payload.get("instruction")
            or payload.get("raw_request")
            or payload.get("prompt")
            or payload.get("text")
            or ""
        ).strip()
        mode = str(payload.get("mode") or "joint_cardinality")
        context = (
            copy.deepcopy(dict(payload.get("context") or {}))
            if isinstance(payload.get("context"), Mapping)
            else {}
        )
        for key in (
            "base_config", "universe", "rebalance_frequency",
            "knowledge_base_version",
        ):
            if key in payload:
                context[key] = copy.deepcopy(payload[key])
        selected_plan = payload.get("selected_plan")
        if isinstance(selected_plan, Mapping) and hasattr(compiler, "compile_selected_plan_mandate"):
            compiled = compiler.compile_selected_plan_mandate(
                raw_request,
                selected_plan=selected_plan,
                require_llm=True,
                mode=mode,
                available_solvers=self._discover_solvers(),
                context=context,
            )
        else:
            compiled = compiler.compile_mandate(
                raw_request,
                llm_client=self.llm_client,
                require_llm=True,
                mode=mode,
                available_solvers=self._discover_solvers(),
                context=context,
            )
        draft_id = self.store.create_draft(
            raw_request=raw_request,
            mode=mode,
            request_payload=payload,
            compiled=compiled,
        )
        self.store.append_draft_event(draft_id, "RETRIEVAL_COMPLETE", compiled.get("retrieval") or {})
        if compiled.get("mandate") is not None:
            self.store.append_draft_event(draft_id, "PARSED", {})
        status = str(compiled.get("status") or BLOCKED_SCHEMA)
        if status not in {BLOCKED_LLM, BLOCKED_SCHEMA}:
            self.store.append_draft_event(draft_id, "SCHEMA_VALIDATED", {})
        if status not in {BLOCKED_LLM, BLOCKED_SCHEMA, BLOCKED_SEMANTIC}:
            self.store.append_draft_event(draft_id, "SEMANTIC_VALIDATED", {})
        if compiled.get("feasibility") is not None:
            self.store.append_draft_event(
                draft_id, "FEASIBILITY_ANALYZED", compiled.get("feasibility") or {}
            )
        if status == AWAITING_CONFIRMATION:
            self.store.append_draft_event(draft_id, AWAITING_CONFIRMATION, {"draft_hash": compiled.get("draft_hash")})
        output = copy.deepcopy(dict(compiled))
        output["compiler_status"] = status
        output["status"] = _public_compiler_status(status)
        cards = self._constraint_cards(
            output.get("mandate")
            if isinstance(output.get("mandate"), Mapping) else None
        )
        output.update(constraints=cards, draft=cards, items=cards)
        output["draft_id"] = draft_id
        return output, 200

    def _revalidate(
        self,
        draft: Mapping[str, Any],
        mandate: Mapping[str, Any],
    ) -> dict[str, Any]:
        compiler = self._compiler_module()
        normalized = compiler.normalize_mandate_payload(mandate)
        existing = copy.deepcopy(dict(draft["compiled"]))
        schema_errors = compiler.validate_mandate_schema(
            normalized,
            raw_request=str(draft["raw_request"]),
        )
        semantic_errors = [] if schema_errors else compiler.validate_mandate_semantics(normalized)
        feasibility = None
        solver_policy = None
        if not schema_errors and not semantic_errors:
            feasibility = compiler.quick_feasibility_precheck(normalized)
            solver_policy = compiler.build_solver_policy(
                str(normalized.get("mode") or draft["mode"]), self._discover_solvers()
            )
        existing["mandate"] = normalized
        existing["confirmation"] = None
        existing["weights_emitted"] = False
        existing["fallback_used"] = False
        existing["feasibility"] = feasibility
        existing["solver_policy"] = solver_policy
        if schema_errors:
            existing.update(status=BLOCKED_SCHEMA, errors=schema_errors, draft_hash=None)
        elif semantic_errors:
            existing.update(status=BLOCKED_SEMANTIC, errors=semantic_errors, draft_hash=None)
        elif feasibility and feasibility.get("status") == "INFEASIBLE":
            existing.update(status="INFEASIBLE", errors=feasibility.get("errors", []), draft_hash=None)
        elif solver_policy and solver_policy.get("capability_status") == BLOCKED_SOLVER_CAPABILITY:
            existing.update(
                status=BLOCKED_SOLVER_CAPABILITY,
                errors=["requested optimizer mode is unavailable; no semantic fallback was used"],
                draft_hash=None,
            )
        else:
            existing.update(status=AWAITING_CONFIRMATION, errors=[], draft_hash=None)
            existing["draft_hash"] = compiler.compute_draft_hash(existing)
        return existing

    def _validate_structured(
        self, payload: Mapping[str, Any]
    ) -> tuple[dict[str, Any], int]:
        compiler = self._compiler_module()
        mode = str(payload.get("mode") or "joint_cardinality")
        if mode != "joint_cardinality":
            return {
                "status": BLOCKED_SOLVER_CAPABILITY,
                "feasible": False,
                "errors": ["structured_ui_requires_joint_cardinality"],
                "fallback_used": False,
            }, 200
        cards = payload.get("constraints")
        base_config = payload.get("base_config")
        if not isinstance(cards, list) or not cards:
            return {
                "status": BLOCKED_SCHEMA,
                "feasible": False,
                "errors": ["non_empty_constraints_array_required"],
            }, 200
        if not isinstance(base_config, Mapping):
            return {
                "status": BLOCKED_SCHEMA,
                "feasible": False,
                "errors": ["base_config_object_required"],
            }, 200
        universe = str(payload.get("universe") or "000905.SH")
        score_source = str(payload.get("score_source") or "").strip()
        if universe not in {"000905.SH", "CSI500_ENH"}:
            return {
                "status": BLOCKED_INPUT,
                "feasible": False,
                "errors": ["only_csi500_000905_is_supported"],
            }, 200
        if not score_source:
            return {
                "status": BLOCKED_DATA,
                "feasible": False,
                "errors": ["audited_score_source_required"],
            }, 200
        try:
            mandate = self._cards_to_mandate(
                cards,
                base_config,
                universe="000905.SH",
                score_source=score_source,
            )
            normalized = compiler.normalize_mandate_payload(mandate)
            schema_errors = compiler.validate_mandate_schema(
                normalized,
                raw_request=_dumps(cards),
            )
            semantic_errors = (
                [] if schema_errors
                else compiler.validate_mandate_semantics(normalized)
            )
            feasibility = (
                None
                if schema_errors or semantic_errors
                else compiler.quick_feasibility_precheck(normalized)
            )
            solver_policy = (
                None
                if schema_errors or semantic_errors
                else compiler.build_solver_policy(
                    "joint_cardinality", self._discover_solvers()
                )
            )
        except (TypeError, ValueError) as exc:
            return {
                "status": BLOCKED_SCHEMA,
                "feasible": False,
                "errors": [str(exc)],
            }, 200

        control_contract: dict[str, Any] | None = None
        control_errors: list[str] = []
        if not schema_errors and not semantic_errors:
            try:
                _, strategy_values, control_contract = self._ui_optimizer_values(
                    base_config, normalized
                )
                if strategy_values["score_name"] != score_source:
                    raise ValueError("score_source_mismatch")
            except (TypeError, ValueError) as exc:
                control_errors.append(str(exc))
                control_contract = {"status": "blocked", "error": str(exc)}

        errors = list(schema_errors) + list(semantic_errors) + control_errors
        status = AWAITING_CONFIRMATION
        if schema_errors:
            status = BLOCKED_SCHEMA
        elif control_errors:
            status = BLOCKED_SOLVER_CAPABILITY
        elif semantic_errors:
            status = BLOCKED_SEMANTIC
        elif feasibility and feasibility.get("status") == "INFEASIBLE":
            status = BLOCKED_INFEASIBLE
            errors.extend(feasibility.get("errors") or [])
        elif (
            solver_policy
            and solver_policy.get("capability_status")
            == BLOCKED_SOLVER_CAPABILITY
        ):
            status = BLOCKED_SOLVER_CAPABILITY
            errors.append("requested_optimizer_mode_unavailable")
        compiled: dict[str, Any] = {
            "status": status,
            "mandate": normalized,
            "errors": errors,
            "feasibility": feasibility,
            "solver_policy": solver_policy,
            "confirmation": None,
            "control_contract": control_contract,
            "weights_emitted": False,
            "fallback_used": False,
            "draft_hash": None,
        }
        if status == AWAITING_CONFIRMATION:
            compiled["draft_hash"] = compiler.compute_draft_hash(compiled)
        normalized_config = copy.deepcopy(dict(base_config))
        normalized_constraints = copy.deepcopy(cards)
        compiled["validation_fingerprint"] = _stable_hash(
            {
                "config": normalized_config,
                "constraints": normalized_constraints,
                "universe": "000905.SH",
                "score_source": score_source,
            }
        )
        draft_id = self.store.create_draft(
            raw_request="structured_ui_validation",
            mode="joint_cardinality",
            request_payload=payload,
            compiled=compiled,
        )
        self.store.append_draft_event(
            draft_id, "SCHEMA_VALIDATED" if not schema_errors else BLOCKED_SCHEMA, {}
        )
        if errors:
            return {
                **copy.deepcopy(compiled),
                "status": _public_compiler_status(status),
                "draft_id": draft_id,
                "feasible": False,
                "normalized_config": normalized_config,
                "normalized_constraints": normalized_constraints,
            }, 200

        return {
            "status": AWAITING_CONFIRMATION,
            "compiler_status": AWAITING_CONFIRMATION,
            "feasible": True,
            "draft_id": draft_id,
            "draft_hash": compiled["draft_hash"],
            "validation_id": None,
            "validation_hash": None,
            "config_hash": compiled["validation_fingerprint"],
            "normalized_config": normalized_config,
            "normalized_constraints": normalized_constraints,
            "feasibility": feasibility,
            "solver_policy": solver_policy,
            "errors": [],
            "fallback_used": False,
            "confirmation_valid": False,
        }, 200

    def validate(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
        if not str(payload.get("draft_id") or "").strip() and isinstance(
            payload.get("constraints"), list
        ):
            return self._validate_structured(payload)
        draft_id = str(payload.get("draft_id") or "").strip()
        draft = self.store.get_draft(draft_id)
        if draft is None:
            return {"status": "NOT_FOUND", "message": "draft_not_found"}, 404
        compiled = copy.deepcopy(dict(draft["compiled"]))
        edited_mandate: Mapping[str, Any] | None = None
        if isinstance(payload.get("mandate"), Mapping):
            edited_mandate = payload["mandate"]
        elif isinstance(payload.get("compiled"), Mapping) and isinstance(
            payload["compiled"].get("mandate"), Mapping
        ):
            edited_mandate = payload["compiled"]["mandate"]
        elif isinstance(payload.get("constraints"), list) and isinstance(compiled.get("mandate"), Mapping):
            edited_mandate = copy.deepcopy(dict(compiled["mandate"]))
            edited_mandate["constraints"] = copy.deepcopy(payload["constraints"])

        edited = False
        if edited_mandate is not None:
            compiler = self._compiler_module()
            old_hash = compiler.compute_draft_hash(compiled) if compiled.get("mandate") else None
            new_envelope = {"mandate": edited_mandate}
            new_hash = compiler.compute_draft_hash(new_envelope)
            edited = old_hash != new_hash
            compiled = self._revalidate(draft, edited_mandate)
            self.store.update_draft(draft_id, compiled, "DRAFT_EDITED" if edited else "DRAFT_REVALIDATED")

        status = str(compiled.get("status") or BLOCKED_SCHEMA)
        wants_confirmation = bool(payload.get("confirm")) or payload.get("action") == "confirm"
        http_status = 200
        if wants_confirmation:
            expected_hash = str(
                payload.get("expected_draft_hash") or payload.get("draft_hash") or ""
            )
            if edited and expected_hash != compiled.get("draft_hash"):
                http_status = 409
            elif status != AWAITING_CONFIRMATION:
                http_status = 422
            else:
                try:
                    compiled = self._compiler_module().confirm_mandate(
                        compiled,
                        actor=str(payload.get("actor") or "portfolio_reviewer"),
                        expected_draft_hash=expected_hash,
                    )
                    self.store.update_draft(draft_id, compiled, CONFIRMED)
                    status = CONFIRMED
                except Exception as exc:  # compiler raises a contract-specific ValueError
                    http_status = 409
                    compiled["errors"] = [str(exc)]

        output = copy.deepcopy(compiled)
        output["compiler_status"] = str(compiled.get("status") or status)
        output["status"] = _public_compiler_status(str(compiled.get("status") or status))
        output["draft_id"] = draft_id
        output["edited"] = edited
        output["confirmation_valid"] = self._compiler_module().is_confirmation_valid(compiled)
        if output["confirmation_valid"]:
            confirmation = compiled.get("confirmation") or {}
            validation_id = str(confirmation.get("confirm_hash") or "")
            output["validation_id"] = validation_id
            output["validation_hash"] = validation_id
            output["config_hash"] = compiled.get("validation_fingerprint")
            request_payload = (
                draft.get("request") if isinstance(draft.get("request"), Mapping) else {}
            )
            output["normalized_config"] = copy.deepcopy(request_payload.get("base_config"))
            output["normalized_constraints"] = copy.deepcopy(request_payload.get("constraints"))
        if http_status == 409:
            output["message"] = "draft_changed_or_hash_mismatch; review and confirm the current hash"
        return output, http_status

    def create_run(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
        draft_id = str(payload.get("draft_id") or "").strip()
        validation_id = str(payload.get("validation_id") or "").strip()
        draft = (
            self.store.get_draft(draft_id)
            if draft_id
            else self.store.find_draft_by_confirmation_hash(validation_id)
        )
        if draft is None:
            return {"status": "NOT_FOUND", "message": "validated_draft_not_found"}, 404
        draft_id = str(draft["draft_id"])
        compiled = draft["compiled"]
        compiler = self._compiler_module()
        if not compiler.is_confirmation_valid(compiled):
            return {
                "status": AWAITING_CONFIRMATION,
                "message": "a valid confirmed draft hash is required before solving",
                "draft_id": draft_id,
            }, 409
        if validation_id:
            confirmation = compiled.get("confirmation") or {}
            if validation_id != confirmation.get("confirm_hash"):
                return {
                    "status": AWAITING_CONFIRMATION,
                    "message": "validation_id_mismatch",
                }, 409
            submitted = {
                "config": payload.get("config"),
                "constraints": payload.get("constraints"),
                "universe": str(payload.get("universe") or "000905.SH"),
                "score_source": str(payload.get("score_source") or ""),
            }
            if (
                _stable_hash(submitted)
                != compiled.get("validation_fingerprint")
            ):
                return {
                    "status": AWAITING_CONFIRMATION,
                    "message": "validated_configuration_changed",
                    "draft_id": draft_id,
                }, 409
        else:
            expected_hash = str(
                payload.get("draft_hash")
                or payload.get("expected_draft_hash")
                or ""
            )
            if expected_hash != compiled.get("draft_hash"):
                return {
                    "status": AWAITING_CONFIRMATION,
                    "message": "draft_hash_mismatch",
                    "draft_id": draft_id,
                    "draft_hash": compiled.get("draft_hash"),
                }, 409
        confirmation = compiled.get("confirmation") or {}
        supplied_confirmation = payload.get("confirm_hash")
        if supplied_confirmation and supplied_confirmation != confirmation.get("confirm_hash"):
            return {"status": AWAITING_CONFIRMATION, "message": "confirm_hash_mismatch"}, 409
        if isinstance(payload.get("mandate"), Mapping):
            inline_hash = compiler.compute_draft_hash({"mandate": payload["mandate"]})
            if inline_hash != compiled.get("draft_hash"):
                return {
                    "status": AWAITING_CONFIRMATION,
                    "message": "inline_mandate_changed; validate and reconfirm before solving",
                }, 409

        run_name = str(payload.get("run_name") or "中证500组合优化").strip()
        if not run_name or len(run_name) > 80:
            return {"status": BLOCKED_INPUT, "message": "run_name_must_be_1_to_80_characters"}, 422
        run_id = self.store.create_run(
            draft_id=draft_id,
            run_name=run_name,
            request_payload=payload,
            mandate=compiled,
        )
        self.store.transition_run(run_id, QUEUED)
        thread = threading.Thread(
            target=self._execute_run,
            args=(run_id,),
            name=f"optimizer-{run_id[-10:]}",
            daemon=True,
        )
        with self._thread_lock:
            self._threads[run_id] = thread
        thread.start()
        return {
            "status": "queued",
            "internal_status": QUEUED,
            "run_id": run_id,
            "draft_id": draft_id,
            "run_name": run_name,
            "poll_url": f"/api/optimizer/runs/{run_id}?live=1",
            "fallback_used": False,
        }, 202

    def _call_runner(
        self,
        run_request: Mapping[str, Any],
        mandate: Mapping[str, Any],
        run_id: str,
    ) -> Mapping[str, Any]:
        callback = lambda: self.store.cancel_requested(run_id)
        try:
            signature = inspect.signature(self.runner)
            parameters = signature.parameters
        except (TypeError, ValueError):
            parameters = {}
        if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters.values()) or {
            "run_request",
            "mandate",
        }.issubset(parameters):
            return self.runner(
                run_request=copy.deepcopy(dict(run_request)),
                mandate=copy.deepcopy(dict(mandate)),
                cancel_requested=callback,
            )
        if len(parameters) >= 3:
            return self.runner(copy.deepcopy(dict(run_request)), copy.deepcopy(dict(mandate)), callback)
        return self.runner(copy.deepcopy(dict(run_request)), copy.deepcopy(dict(mandate)))

    @staticmethod
    def _blocked_run_status(result: Mapping[str, Any]) -> str:
        raw = str(result.get("status") or "").upper()
        if raw.startswith("BLOCKED_"):
            return raw
        stage = str(result.get("blocked_stage") or "").lower()
        reason = str(result.get("reason") or "").lower()
        combined = f"{stage}:{reason}"
        if "solver_availability" in combined or "clarabel" in combined:
            return BLOCKED_SOLVER
        if "feasibility" in combined or "infeasible" in combined or "precheck" in combined:
            return BLOCKED_INFEASIBLE
        if "data" in combined or "missing" in combined or "input_contract" in combined:
            return BLOCKED_DATA
        if "capability" in combined or "unsupported" in combined:
            return BLOCKED_SOLVER_CAPABILITY
        return BLOCKED_INPUT

    def _execute_run(self, run_id: str) -> None:
        try:
            if self.store.cancel_requested(run_id):
                self.store.transition_run(run_id, CANCELLED)
                return
            record = self.store.get_run(run_id, include_audit=False)
            if record is None:
                return
            self.store.transition_run(run_id, SOLVING)
            result = _jsonable(self._call_runner(record["request"], record["mandate"], run_id))
            if not isinstance(result, Mapping):
                raise TypeError("optimizer runner must return a mapping")
            if self.store.cancel_requested(run_id):
                self.store.transition_run(run_id, CANCELLED)
                return
            raw_status = str(result.get("status") or "").lower()
            tradable = result.get("tradable") is True
            if raw_status in {"ready", "solved", "audited", "ok"} and tradable:
                self.store.transition_run(run_id, SOLVED, result=result)
                self.store.transition_run(run_id, AUDITED, result=result)
            else:
                blocked_status = self._blocked_run_status(result)
                scrubbed = _scrub_solution_fields(result)
                scrubbed["status"] = blocked_status
                scrubbed["tradable"] = False
                scrubbed["fallback_used"] = False
                self.store.transition_run(run_id, blocked_status, result=scrubbed)
        except Exception as exc:  # noqa: BLE001 - task failure must be persisted
            self.store.transition_run(
                run_id,
                FAILED,
                error={
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "weights_returned": False,
                    "fallback_used": False,
                },
            )
        finally:
            with self._thread_lock:
                self._threads.pop(run_id, None)

    @staticmethod
    def _default_runner(
        *,
        run_request: Mapping[str, Any],
        mandate: Mapping[str, Any],
        cancel_requested: Callable[[], bool],
    ) -> Mapping[str, Any]:
        """Run the strict stock optimizer from explicit PIT matrices in the request."""

        if cancel_requested():
            return {"status": CANCELLED, "tradable": False}
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        try:
            import numpy as np
            import pandas as pd
            from model.portfolio_optimization.stock_constraint_optimizer import (
                StockOptimizerConfig,
                optimize_stock_portfolio,
            )
        except Exception as exc:
            return {
                "status": BLOCKED_SOLVER,
                "tradable": False,
                "blocked_stage": "solver_availability",
                "reason": f"strict_optimizer_import_failed:{type(exc).__name__}",
                "fallback_used": False,
            }

        inputs = run_request.get("inputs")
        if not isinstance(inputs, Mapping):
            inputs = run_request.get("solver_inputs")
        if not isinstance(inputs, Mapping):
            return OptimizerBackendService._database_runner(
                run_request=run_request,
                mandate=mandate,
                cancel_requested=cancel_requested,
            )
        rows = inputs.get("cross_section")
        if not isinstance(rows, list) or not rows:
            return {
                "status": BLOCKED_DATA,
                "tradable": False,
                "blocked_stage": "data_contract",
                "reason": "cross_section_records_are_required",
                "fallback_used": False,
            }
        compiled_mandate = mandate.get("mandate") if isinstance(mandate.get("mandate"), Mapping) else None
        if not isinstance(compiled_mandate, Mapping):
            return {
                "status": BLOCKED_INPUT,
                "tradable": False,
                "blocked_stage": "mandate_contract",
                "reason": "confirmed_mandate_is_missing",
                "fallback_used": False,
            }
        try:
            config_values = OptimizerBackendService._optimizer_config_from_mandate(
                compiled_mandate, rows, inputs
            )
        except ValueError as exc:
            return {
                "status": BLOCKED_SOLVER_CAPABILITY,
                "tradable": False,
                "blocked_stage": "mandate_capability",
                "reason": str(exc),
                "fallback_used": False,
            }
        controls = inputs.get("objective_controls")
        if isinstance(controls, Mapping):
            for name in (
                "alpha_weight",
                "alpha_scale",
                "active_risk_penalty",
                "transaction_cost_rate",
                "turnover_l1_penalty",
                "turnover_l2_penalty",
                "solver_max_iterations",
            ):
                if name in controls:
                    config_values[name] = controls[name]
        try:
            config = StockOptimizerConfig(**config_values)
            frame = pd.DataFrame(rows)
            style = inputs.get("style_exposures")
            style_frame = pd.DataFrame(style) if isinstance(style, list) else style
            covariance = inputs.get("annual_covariance")
            root = inputs.get("risk_root")
            result = optimize_stock_portfolio(
                frame,
                style_exposures=style_frame,
                annual_covariance=None if covariance is None else np.asarray(covariance, dtype=float),
                risk_root=None if root is None else np.asarray(root, dtype=float),
                previous_weights=inputs.get("previous_weights"),
                config=config,
            )
            return result
        except Exception as exc:  # optimizer returns blocks for expected contract failures
            return {
                "status": BLOCKED_INPUT,
                "tradable": False,
                "blocked_stage": "input_contract",
                "reason": f"optimizer_input_failed:{type(exc).__name__}:{exc}",
                "fallback_used": False,
            }

    @staticmethod
    def _ui_optimizer_values(
        base_config: Mapping[str, Any],
        mandate: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        def section(name: str) -> Mapping[str, Any]:
            value = base_config.get(name)
            return value if isinstance(value, Mapping) else {}

        universe = section("universe")
        objective = section("objective")
        holdings = section("holdings")
        industry = section("industry")
        style = section("style")
        active_risk = section("active_risk")
        trading = section("trading")
        liquidity = section("liquidity")
        lists = section("lists")
        allowed_fields = {
            "universe": {"code", "name", "rebalance_frequency", "holdings", "score_source"},
            "objective": {"type", "alpha_scale", "risk_aversion", "turnover_penalty", "cost_penalty"},
            "holdings": {"long_only", "fully_invested", "min_weight", "max_weight"},
            "industry": {"classification", "max_active_deviation"},
            "style": {"max_abs_exposure", "size", "value", "momentum", "liquidity", "beta"},
            "active_risk": {"tracking_error_limit", "max_active_weight", "covariance_model"},
            "trading": {"turnover_limit", "transaction_cost_bps"},
            "liquidity": {"max_adv_participation", "exclude_suspended", "exclude_limit_locked"},
            "lists": {"include", "exclude"},
            "backtest": {"start", "end", "max_months"},
        }
        unsupported_fields: list[str] = []
        for section_name, allowed in allowed_fields.items():
            raw_section = base_config.get(section_name)
            if raw_section is None:
                continue
            if not isinstance(raw_section, Mapping):
                unsupported_fields.append(f"{section_name}:object_required")
                continue
            unsupported_fields.extend(
                f"{section_name}.{key}"
                for key in sorted(set(raw_section) - allowed)
            )
        unsupported_fields.extend(
            str(key)
            for key in sorted(set(base_config) - set(allowed_fields))
        )
        if unsupported_fields:
            raise ValueError(
                "unsupported_control_fields:" + ",".join(unsupported_fields)
            )

        unsupported_values: list[str] = []
        if str(universe.get("code") or "000905.SH") not in {"000905.SH", "CSI500_ENH"}:
            unsupported_values.append("universe.code")
        if str(universe.get("rebalance_frequency") or "monthly") != "monthly":
            unsupported_values.append("universe.rebalance_frequency")
        if str(objective.get("type") or "active_alpha") != "active_alpha":
            unsupported_values.append("objective.type")
        if holdings.get("long_only", True) is not True:
            unsupported_values.append("holdings.long_only")
        if holdings.get("fully_invested", True) is not True:
            unsupported_values.append("holdings.fully_invested")
        if str(industry.get("classification") or "SW_L1") != "SW_L1":
            unsupported_values.append("industry.classification")
        if str(active_risk.get("covariance_model") or "factor") != "factor":
            unsupported_values.append("active_risk.covariance_model")
        if liquidity.get("exclude_suspended", True) is not True:
            unsupported_values.append("liquidity.exclude_suspended")
        if liquidity.get("exclude_limit_locked", True) is not True:
            unsupported_values.append("liquidity.exclude_limit_locked")
        score_name = str(universe.get("score_source") or "").strip()
        if not score_name:
            unsupported_values.append("universe.score_source")
        try:
            cost_penalty = float(objective.get("cost_penalty", 1.0))
        except (TypeError, ValueError) as exc:
            raise ValueError("objective.cost_penalty_must_be_numeric") from exc
        if not math.isfinite(cost_penalty) or cost_penalty < 1.0:
            unsupported_values.append("objective.cost_penalty_must_be_at_least_1")
        if unsupported_values:
            raise ValueError(
                "unsupported_control_values:" + ",".join(unsupported_values)
            )

        style_map = {
            "size": "style_size",
            "value": "style_value",
            "momentum": "style_momentum",
            "liquidity": "style_liquidity",
            "beta": "style_beta",
        }
        style_bounds: dict[str, tuple[float, float]] = {}
        for ui_name, model_name in style_map.items():
            if ui_name == "beta" and ui_name not in style:
                continue
            limit = style.get(ui_name, style.get("max_abs_exposure", 0.20))
            style_bounds[model_name] = (-float(limit), float(limit))
        transaction_cost_rate = float(
            trading.get("transaction_cost_bps", 10.0)
        ) / 10000.0
        turnover_penalty = float(objective.get("turnover_penalty", 1.0))
        values: dict[str, Any] = {
            "target_holdings": int(universe.get("holdings", 50)),
            "min_weight": float(holdings.get("min_weight", 0.001)),
            "max_weight": float(holdings.get("max_weight", 0.03)),
            "max_active_weight": float(
                active_risk.get("max_active_weight", 0.03)
            ),
            "industry_deviation": float(
                industry.get("max_active_deviation", 0.02)
            ),
            "style_bounds": style_bounds,
            "default_style_bounds": (
                -float(style.get("max_abs_exposure", 0.20)),
                float(style.get("max_abs_exposure", 0.20)),
            ),
            "target_tracking_error": float(
                active_risk.get("tracking_error_limit", 0.06)
            ),
            "one_way_turnover_limit": float(
                trading.get("turnover_limit", 0.40)
            ),
            "alpha_weight": float(objective.get("alpha_scale", 1.8)),
            "alpha_scale": 0.05,
            "score_target_penalty": (
                3.0 * float(objective.get("alpha_scale", 1.8)) / 1.8
            ),
            "active_risk_penalty": float(
                objective.get("risk_aversion", 0.5)
            ),
            "transaction_cost_rate": transaction_cost_rate,
            "turnover_l1_penalty": (
                turnover_penalty * 0.001
                + (cost_penalty - 1.0) * transaction_cost_rate
            ),
            "turnover_l2_penalty": turnover_penalty * 0.05,
        }

        def codes(value: Any) -> tuple[str, ...]:
            if isinstance(value, str):
                source = value.replace("\uff0c", ",").replace(";", ",").replace("\n", ",").split(",")
            elif isinstance(value, list):
                source = value
            else:
                source = []
            return tuple(sorted({str(item).strip() for item in source if str(item).strip()}))

        values["mandatory"] = codes(lists.get("include"))
        values["blacklist"] = codes(lists.get("exclude"))
        constraints = mandate.get("constraints")
        unsupported: list[str] = []
        if isinstance(constraints, list):
            for item in constraints:
                if not isinstance(item, Mapping) or item.get("hard") is False:
                    continue
                scope = (
                    item.get("scope")
                    if isinstance(item.get("scope"), Mapping)
                    else {}
                )
                kind = str(item.get("type") or "")
                metric = str(scope.get("metric") or "")
                lower, upper = item.get("lower"), item.get("upper")
                constraint_id = str(item.get("id") or f"{kind}.{metric}")
                if kind == "holding" and metric == "cardinality":
                    if not isinstance(lower, int) or lower != upper:
                        unsupported.append(constraint_id)
                    else:
                        values["target_holdings"] = lower
                elif kind == "holding" and metric == "security_weight":
                    if lower is not None:
                        values["min_weight"] = float(lower)
                    if upper is not None:
                        values["max_weight"] = float(upper)
                elif kind == "holding" and metric == "active_security_weight":
                    if lower is None or upper is None:
                        unsupported.append(constraint_id)
                    else:
                        values["max_active_weight"] = max(
                            abs(float(lower)), abs(float(upper))
                        )
                elif kind == "industry" and metric == "active_exposure":
                    if (
                        lower is None or upper is None
                        or not math.isclose(
                            abs(float(lower)), abs(float(upper)),
                            abs_tol=1.0e-12,
                        )
                    ):
                        unsupported.append(constraint_id)
                    elif str(scope.get("group") or "all") == "all":
                        values["industry_deviation"] = abs(float(upper))
                    else:
                        unsupported.append(
                            constraint_id + ":industry_specific_requires_period_universe"
                        )
                elif kind == "style" and metric == "active_exposure":
                    if lower is None or upper is None:
                        unsupported.append(constraint_id)
                    else:
                        style_name = str(scope.get("style") or "all")
                        if style_name == "all":
                            values["default_style_bounds"] = (
                                float(lower), float(upper)
                            )
                        else:
                            model_name = style_map.get(style_name, style_name)
                            if model_name not in style_bounds:
                                unsupported.append(
                                    constraint_id + ":style_factor_unavailable"
                                )
                            else:
                                style_bounds[model_name] = (
                                    float(lower), float(upper)
                                )
                elif kind == "active_risk" and metric == "tracking_error":
                    if upper is None or (
                        isinstance(lower, (int, float)) and float(lower) > 0
                    ):
                        unsupported.append(constraint_id)
                    else:
                        values["target_tracking_error"] = float(upper)
                elif kind == "trading" and metric in {
                    "one_way_turnover", "two_way_turnover",
                }:
                    if upper is None or (
                        isinstance(lower, (int, float)) and float(lower) > 0
                    ):
                        unsupported.append(constraint_id)
                    else:
                        values["one_way_turnover_limit"] = float(upper) * (
                            0.5 if metric == "two_way_turnover" else 1.0
                        )
                elif kind == "liquidity" and metric == "adv_participation":
                    if upper is None:
                        unsupported.append(constraint_id)
                    else:
                        liquidity = {
                            **dict(liquidity),
                            "max_adv_participation": float(upper),
                        }
                elif kind == "list" and metric in {
                    "blacklist", "forced_exclude",
                }:
                    values["blacklist"] = tuple(
                        sorted(
                            set(values.get("blacklist", ()))
                            | set(codes(scope.get("security_set")))
                        )
                    )
                elif kind == "list" and metric == "forced_include":
                    values["mandatory"] = tuple(
                        sorted(
                            set(values.get("mandatory", ()))
                            | set(codes(scope.get("security_set")))
                        )
                    )
                elif kind == "list" and metric == "whitelist":
                    values["whitelist"] = codes(scope.get("security_set"))
                else:
                    unsupported.append(
                        f"{constraint_id}:{kind}.{metric}"
                    )
        if unsupported:
            raise ValueError(
                "unsupported_hard_constraints:" + ",".join(unsupported)
            )
        strategy_values = {
            "max_adv_participation": float(
                liquidity.get("max_adv_participation", 0.05)
            ),
            "transaction_cost_rate": values["transaction_cost_rate"],
            "score_name": score_name,
            "optimizer_style_columns": tuple(style_bounds.keys()),
        }
        control_contract = {
            "status": "validated",
            "applied": [
                "universe.score_source", "universe.holdings",
                "holdings.min_weight", "holdings.max_weight",
                "industry.max_active_deviation", "style.max_abs_exposure",
                "style.size", "style.value", "style.momentum", "style.liquidity",
                "active_risk.tracking_error_limit", "active_risk.max_active_weight",
                "objective.alpha_scale", "objective.risk_aversion",
                "objective.turnover_penalty", "objective.cost_penalty",
                "trading.turnover_limit", "trading.transaction_cost_bps",
                "liquidity.max_adv_participation", "lists.include", "lists.exclude",
            ],
            "fixed": {
                "universe.rebalance_frequency": "monthly",
                "holdings.long_only": True,
                "holdings.fully_invested": True,
                "industry.classification": "SW_L1",
                "active_risk.covariance_model": "factor",
                "liquidity.exclude_suspended": True,
                "liquidity.exclude_limit_locked": True,
            },
        }
        return values, strategy_values, control_contract

    @staticmethod
    def _frontend_strategy_result(
        result: Mapping[str, Any],
        mandate: Mapping[str, Any],
    ) -> dict[str, Any]:
        curves = result.get("curves")
        metrics = result.get("metrics")
        periods = result.get("periods")
        if not isinstance(curves, list) or not curves:
            return {
                "status": BLOCKED_DATA,
                "tradable": False,
                "blocked_stage": "backtest_output",
                "reason": "four_strategy_nav_curves_are_unavailable",
                "fallback_used": False,
            }
        metrics = metrics if isinstance(metrics, Mapping) else {}
        requested_status = str(
            result.get("requested_window_performance_status")
            or metrics.get("requested_window_performance_status")
            or metrics.get("performance_status")
            or "unavailable_performance_contract"
        )
        formal_metrics_valid = bool(
            metrics.get("formal_metrics_valid") is True
            and requested_status == "valid_complete_requested_window"
        )
        continuity = (
            copy.deepcopy(dict(metrics.get("continuity")))
            if isinstance(metrics.get("continuity"), Mapping) else {}
        )
        longest_segment = (
            copy.deepcopy(dict(metrics.get("longest_contiguous_segment")))
            if isinstance(metrics.get("longest_contiguous_segment"), Mapping)
            else None
        )

        def date_key(value: Any) -> str:
            return "".join(
                character for character in str(value or "")
                if character.isdigit()
            )[:8]

        display_curves = list(curves)
        series_scope = "formal_requested_window"
        if not formal_metrics_valid:
            start_key = date_key(longest_segment.get("start")) if longest_segment else ""
            end_key = date_key(longest_segment.get("end")) if longest_segment else ""
            if not start_key or not end_key:
                return {
                    "status": BLOCKED_DATA,
                    "tradable": False,
                    "blocked_stage": "backtest_output",
                    "reason": "diagnostic_contiguous_segment_is_unavailable",
                    "requested_window_performance_status": requested_status,
                    "fallback_used": False,
                }
            display_curves = [
                row for row in curves
                if isinstance(row, Mapping)
                and start_key <= date_key(
                    row.get("signal_date") or row.get("date")
                ) <= end_key
            ]
            series_scope = "diagnostic_longest_contiguous_segment"
            if not display_curves:
                return {
                    "status": BLOCKED_DATA,
                    "tradable": False,
                    "blocked_stage": "backtest_output",
                    "reason": "diagnostic_contiguous_segment_has_no_curve_rows",
                    "requested_window_performance_status": requested_status,
                    "fallback_used": False,
                }
        benchmark_metrics = (
            metrics.get("benchmark")
            if isinstance(metrics.get("benchmark"), Mapping) else {}
        )
        invalid_formal_fields = {
            "annual_return", "annual_volatility", "annual_excess_return",
            "sharpe", "max_drawdown", "win_rate", "tracking_error",
            "information_ratio",
        }

        def strategy(
            key: str, nav_key: str, metric_key: str
        ) -> dict[str, Any]:
            raw_metric = (
                metrics.get(metric_key)
                if isinstance(metrics.get(metric_key), Mapping) else {}
            )
            annual_return = raw_metric.get("annual_return")
            benchmark_return = benchmark_metrics.get("annual_return")
            enriched = copy.deepcopy(dict(raw_metric))
            enriched["annual_excess_return"] = (
                float(annual_return) - float(benchmark_return)
                if formal_metrics_valid
                and annual_return is not None and benchmark_return is not None
                else None
            )
            if not formal_metrics_valid:
                for field_name in invalid_formal_fields:
                    enriched[field_name] = None
                enriched["formal_metric_status"] = requested_status
            enriched["turnover"] = raw_metric.get("average_turnover")
            return {
                "id": key,
                "series_scope": series_scope,
                "nav": [
                    {
                        "date": str(row.get("signal_date") or row.get("date")),
                        "nav": row.get(nav_key),
                    }
                    for row in display_curves
                    if isinstance(row, Mapping) and row.get(nav_key) is not None
                ],
                "metrics": enriched,
            }

        strategies = {
            "benchmark": strategy(
                "benchmark", "benchmark_nav", "benchmark"
            ),
            "direct_score_top50": strategy(
                "direct_score_top50", "direct_nav", "direct"
            ),
            "same_support_score_weighted": strategy(
                "same_support_score_weighted",
                "same_support_nav",
                "same_support_score_weighted",
            ),
            "constrained_optimizer": strategy(
                "constrained_optimizer", "optimized_nav", "optimized"
            ),
        }
        ready_periods = [
            item for item in (periods if isinstance(periods, list) else [])
            if isinstance(item, Mapping)
            and isinstance(item.get("optimizer_result"), Mapping)
            and item["optimizer_result"].get("status") == "ready"
            and item["optimizer_result"].get("tradable") is True
        ]
        if not ready_periods:
            return {
                "status": BLOCKED_DATA,
                "tradable": False,
                "blocked_stage": "optimizer_output",
                "reason": "no_certified_optimizer_period_is_available",
                "fallback_used": False,
            }
        latest_period = ready_periods[-1]
        latest = latest_period["optimizer_result"]
        raw_weights = (
            latest.get("weights")
            if isinstance(latest.get("weights"), Mapping) else {}
        )
        raw_active = (
            latest.get("active_weights")
            if isinstance(latest.get("active_weights"), Mapping) else {}
        )
        weights = []
        for code, value in sorted(
            raw_weights.items(), key=lambda item: float(item[1]), reverse=True
        ):
            weight = float(value)
            if weight <= 1.0e-10:
                continue
            active = float(raw_active.get(code, 0.0))
            weights.append(
                {
                    "code": str(code),
                    "weight": weight,
                    "benchmark_weight": weight - active,
                    "active_weight": active,
                }
            )
        realized = (
            latest.get("realized")
            if isinstance(latest.get("realized"), Mapping) else {}
        )
        exposures: list[dict[str, Any]] = []
        for category, field in (
            ("industry", "industry_active_exposure"),
            ("style", "style_active_exposure"),
        ):
            values = realized.get(field)
            if isinstance(values, Mapping):
                for name, value in sorted(values.items()):
                    exposures.append(
                        {
                            "name": str(name),
                            "category": category,
                            "active_exposure": float(value),
                        }
                    )
        slack = latest.get("slack") if isinstance(latest.get("slack"), Mapping) else {}
        dual = latest.get("dual") if isinstance(latest.get("dual"), Mapping) else {}
        constraint_rows = OptimizerBackendService._constraint_cards(mandate)
        for row in constraint_rows:
            row["status"] = "satisfied"
        transactions = []
        for row in latest.get("transactions") or []:
            if not isinstance(row, Mapping):
                continue
            transactions.append(
                {
                    **copy.deepcopy(dict(row)),
                    "code": str(row.get("ts_code") or row.get("code") or ""),
                }
            )
        period_rows = [
            item for item in (periods if isinstance(periods, list) else [])
            if isinstance(item, Mapping)
        ]
        blocked_events: list[dict[str, Any]] = []
        blocked_reason_counts: dict[str, int] = {}
        for period in period_rows:
            status = str(period.get("status") or "")
            rebalance_blocked = bool(period.get("rebalance_blocked"))
            if not status.startswith("blocked") and not rebalance_blocked:
                continue
            reason = str(
                period.get("reason")
                or period.get("benchmark_return_reason")
                or period.get("carry_status")
                or status
                or "unspecified"
            )
            blocked_reason_counts[reason] = (
                blocked_reason_counts.get(reason, 0) + 1
            )
            event = {
                "signal_date": period.get("signal_date"),
                "phase": period.get("phase"),
                "status": status,
                "reason": reason,
                "rebalance_status": period.get("rebalance_status"),
                "carry_status": period.get("carry_status"),
                "evaluation_included": bool(period.get("evaluation_included")),
            }
            for key in (
                "optimizer_status", "optimizer_reason", "direct_status",
                "direct_reason", "same_support_status",
                "same_support_reason", "realization_reason",
                "carry_performance_status", "holding_valuation_status",
                "no_alpha_view_score_policy", "no_alpha_view_style_policy",
                "score_missing_codes", "missing_return_position_audit",
                "comparator_missing_returns",
                "missing_executable_return_trade_policy",
                "liquidity_audit",
            ):
                if key in period:
                    event[key] = copy.deepcopy(period.get(key))
            blocked_events.append(event)

        requested = (
            latest.get("requested")
            if isinstance(latest.get("requested"), Mapping) else {}
        )
        selection = (
            requested.get("selection")
            if isinstance(requested.get("selection"), Mapping) else {}
        )
        support_search = (
            copy.deepcopy(dict(selection.get("support_search")))
            if isinstance(selection.get("support_search"), Mapping) else {}
        )
        support_attempts = [
            item for item in support_search.get("attempts") or []
            if isinstance(item, Mapping)
        ]
        linear_residuals = []
        for attempt in support_attempts:
            diagnostics = attempt.get("linear_diagnostics")
            if not isinstance(diagnostics, Mapping):
                continue
            value = diagnostics.get("max_constraint_violation")
            if value is not None:
                linear_residuals.append(float(value))
        rejected_supports = sum(
            str(attempt.get("status")) == "rejected_by_clarabel_socp"
            for attempt in support_attempts
        )

        solver = copy.deepcopy(dict(latest.get("solver") or {}))
        solver["objective_value"] = solver.get(
            "objective_value", solver.get("objective")
        )
        solver["max_violation"] = solver.get(
            "max_violation", solver.get("max_constraint_violation")
        )
        solver["max_residual"] = solver.get("max_violation")
        solver["phase_i"] = {
            "name": support_search.get(
                "phase_i_linear_solver", "SCIPY_HIGHS_MILP"
            ),
            "status": (
                "certified_support_selected"
                if support_search.get("selected_strategy")
                else (
                    support_attempts[-1].get("status")
                    if support_attempts else "not_available"
                )
            ),
            "attempt_count": support_search.get(
                "attempt_count", len(support_attempts)
            ),
            "attempt_limit": support_search.get("attempt_limit"),
            "rejected_supports": rejected_supports,
            "no_good_cut_per_rejected_support": bool(
                support_search.get("no_good_cut_per_rejected_support")
            ),
            "no_good_cuts_applied": rejected_supports,
            "max_linear_constraint_violation": (
                max(linear_residuals) if linear_residuals else None
            ),
            "heuristic_support_fallback_used": bool(
                support_search.get("heuristic_support_fallback_used")
            ),
        }
        solver["phase_ii"] = {
            "name": solver.get("name") or "CLARABEL",
            "status": solver.get("status"),
            "certified": solver.get("certified"),
            "iterations": solver.get("iterations"),
            "solve_time_ms": solver.get("solve_time_ms"),
            "objective_value": solver.get("objective_value"),
            "max_constraint_violation": solver.get("max_violation"),
        }
        raw_timing_overlay = (
            result.get("timing_overlay")
            if isinstance(result.get("timing_overlay"), Mapping) else {}
        )
        raw_timing_periods = (
            raw_timing_overlay.get("periods")
            if isinstance(raw_timing_overlay, Mapping) else []
        )
        timing_periods = [
            copy.deepcopy(dict(item))
            for item in (raw_timing_periods if isinstance(raw_timing_periods, list) else [])
            if isinstance(item, Mapping)
        ]
        latest_timing = (
            copy.deepcopy(dict(latest_period.get("timing_overlay")))
            if isinstance(latest_period.get("timing_overlay"), Mapping) else None
        )
        timing_budget_latest = (
            copy.deepcopy(dict(latest_period.get("timing_budget_audit")))
            if isinstance(latest_period.get("timing_budget_audit"), Mapping) else None
        )
        alpha_overlay_latest = (
            copy.deepcopy(dict(latest_period.get("alpha_overlay_audit")))
            if isinstance(latest_period.get("alpha_overlay_audit"), Mapping) else None
        )
        timing_payload = {
            "status": raw_timing_overlay.get("status") if isinstance(raw_timing_overlay, Mapping) else None,
            "latest": latest_timing,
            "latest_budget": timing_budget_latest,
            "latest_alpha_overlay": alpha_overlay_latest,
            "periods": timing_periods,
            "audit": raw_timing_overlay.get("audit") if isinstance(raw_timing_overlay, Mapping) else {},
            "config": raw_timing_overlay.get("config") if isinstance(raw_timing_overlay, Mapping) else {},
        }
        return {
            "status": "ready",
            "tradable": True,
            "requested_window_performance_status": requested_status,
            "formal_metrics_valid": formal_metrics_valid,
            "curve_status": result.get("curve_status"),
            "series_scope": series_scope,
            "strategies": strategies,
            "metrics": {
                "performance_status": metrics.get("performance_status"),
                "requested_window_performance_status": requested_status,
                "formal_metrics_valid": formal_metrics_valid,
                "continuity": continuity,
                "benchmark": strategies["benchmark"]["metrics"],
                "direct_score_top50": strategies["direct_score_top50"]["metrics"],
                "same_support_score_weighted": (
                    strategies["same_support_score_weighted"]["metrics"]
                ),
                "constrained_optimizer": strategies["constrained_optimizer"]["metrics"],
            },
            "portfolio": {"weights": weights},
            "weights": weights,
            "risk": {"exposures": exposures},
            "exposures": exposures,
            "optimization": {
                "constraints": constraint_rows,
                "slack": copy.deepcopy(dict(slack)),
                "dual": copy.deepcopy(dict(dual)),
            },
            "constraints": constraint_rows,
            "constraint_audit": constraint_rows,
            "slack": copy.deepcopy(dict(slack)),
            "dual": copy.deepcopy(dict(dual)),
            "trades": transactions,
            "transactions": transactions,
            "solver": solver,
            "timing_overlay": timing_payload,
            "latest_signal_date": latest_period.get("signal_date"),
            "latest_phase": latest_period.get("phase"),
            "backtest_audit": {
                "status": result.get("status"),
                "requested_window_performance_status": requested_status,
                "formal_metrics_valid": formal_metrics_valid,
                "curve_status": result.get("curve_status"),
                "series_scope": series_scope,
                "continuity": continuity,
                "longest_contiguous_segment": (
                    {
                        key: longest_segment.get(key)
                        for key in (
                            "start", "end", "periods", "diagnostic_only"
                        )
                    }
                    if longest_segment else None
                ),
                "diagnostic_contiguous_segments": [
                    {
                        key: segment.get(key)
                        for key in (
                            "start", "end", "periods", "diagnostic_only"
                        )
                    }
                    for segment in metrics.get(
                        "diagnostic_contiguous_segments", []
                    )
                    if isinstance(segment, Mapping)
                ],
                "tradable_period_count": result.get("tradable_period_count"),
                "evaluated_period_count": result.get("evaluated_period_count"),
                "optimizer_attempts": result.get("optimizer_attempts"),
                "optimizer_certified_periods": result.get("optimizer_certified_periods"),
                "blocked_periods": result.get("blocked_periods"),
                "rebalance_blocked_periods": result.get(
                    "rebalance_blocked_periods"
                ),
                "carried_period_count": result.get("carried_period_count"),
                "blocked_reason_counts": dict(
                    sorted(blocked_reason_counts.items())
                ),
                "blocked_events": blocked_events,
                "constraint_hit_rate": result.get("constraint_hit_rate"),
                "governance": result.get("governance"),
                "metrics_by_split": result.get("metrics_by_split"),
                "database_audit": result.get("database_audit"),
                "timing_overlay": timing_payload.get("audit"),
            },
            "fallback_used": False,
        }

    @staticmethod
    def _database_runner(
        *,
        run_request: Mapping[str, Any],
        mandate: Mapping[str, Any],
        cancel_requested: Callable[[], bool],
    ) -> Mapping[str, Any]:
        if cancel_requested():
            return {"status": CANCELLED, "tradable": False}
        config_payload = run_request.get("config")
        compiled_mandate = (
            mandate.get("mandate")
            if isinstance(mandate.get("mandate"), Mapping) else None
        )
        if not isinstance(config_payload, Mapping) or not isinstance(
            compiled_mandate, Mapping
        ):
            return {
                "status": BLOCKED_INPUT,
                "tradable": False,
                "blocked_stage": "request_contract",
                "reason": "validated_config_and_confirmed_mandate_are_required",
                "fallback_used": False,
            }
        if str(run_request.get("universe") or "") not in {
            "000905.SH", "CSI500_ENH",
        }:
            return {
                "status": BLOCKED_INPUT,
                "tradable": False,
                "blocked_stage": "universe_contract",
                "reason": "only_csi500_000905_is_supported",
                "fallback_used": False,
            }
        try:
            from model.portfolio_optimization.csi500_strategy import (
                CSI500StrategyConfig,
                run_csi500_strategy_from_database,
            )
            from model.portfolio_optimization.timing_overlay import (
                TimingOverlayConfig,
            )
            from model.portfolio_optimization.stock_constraint_optimizer import (
                StockOptimizerConfig,
            )
            optimizer_values, strategy_values, _ = (
                OptimizerBackendService._ui_optimizer_values(
                    config_payload, compiled_mandate
                )
            )
            optimizer_config = StockOptimizerConfig(**optimizer_values)
            strategy_style_columns = (
                "style_size",
                "style_value",
                "style_momentum",
                "style_liquidity",
            )
            optimizer_style_columns = strategy_style_columns
            if "style_beta" in dict(optimizer_values.get("style_bounds") or {}):
                optimizer_style_columns = (*optimizer_style_columns, "style_beta")
            strategy_config = CSI500StrategyConfig(
                timing_overlay=TimingOverlayConfig(
                    enabled=True,
                    min_history_periods=18,
                    valuation_lookback_periods=84,
                    sentiment_lookback_periods=36,
                    macro_lookback_months=60,
                    left_weight=0.45,
                    right_weight=0.55,
                    alpha_overlay_enabled=False,
                    max_alpha_overlay_weight=0.00,
                    min_alpha_base_weight=1.00,
                    risk_budget_floor=0.40,
                    risk_budget_ceiling=1.00,
                    min_tracking_error_multiplier=1.00,
                    min_tracking_error_absolute=0.06,
                    min_active_weight_multiplier=1.00,
                    min_industry_multiplier=1.00,
                    min_style_multiplier=1.00,
                    min_turnover_multiplier=1.00,
                    score_target_low_regime_multiplier=1.00,
                    score_target_high_regime_multiplier=1.00,
                    active_risk_low_regime_multiplier=1.00,
                    active_risk_high_regime_multiplier=1.00,
                ),
                optimizer=optimizer_config,
                max_adv_participation=strategy_values[
                    "max_adv_participation"
                ],
                transaction_cost_rate=strategy_values[
                    "transaction_cost_rate"
                ],
                score_name=strategy_values["score_name"],
                optimizer_style_columns=optimizer_style_columns,
                score_source_mode="factor_lab_champion",
                factor_lab_profile="high_sharpe_enhanced",
                factor_lab_bridge_include_warehouse_factors=True,
                factor_lab_bridge_max_factor_candidates=180,
                factor_lab_bridge_screen_top_n=60,
                factor_lab_bridge_screen_lookback_days=252,
                factor_lab_bridge_screen_rebalance_days=63,
                factor_lab_bridge_screen_min_coverage=0.10,
                factor_lab_bridge_screen_min_dates=20,
                factor_lab_bridge_screen_min_assets_per_date=80,
                factor_lab_bridge_screen_max_pair_corr=0.92,
                factor_lab_bridge_external_factor_max_staleness_days=63,
                factor_lab_bridge_lookback_periods=48,
                factor_lab_bridge_min_periods=12,
                factor_lab_bridge_min_training_periods=12,
                factor_lab_bridge_min_training_rows=3000,
                factor_lab_bridge_alpha_neutralization=True,
                allow_no_alpha_view_missing_members=True,
                database_risk_allow_ipo_specific_prior=True,
                require_official_index_benchmark=True,
                trading_days_per_period=20,
                train_end="20221231",
                validation_end="20231231",
                formal_evaluation_start="20200630",
                official_index_database_path=str(
                    os.getenv("SUBJECT_DATABASE_DB", "")
                    or os.getenv("SUBJECT_DB_PATH", "")
                    or (
                        "F:/data/agent_console_private/database.db"
                        if os.name == "nt"
                        and Path(
                            "F:/data/agent_console_private/database.db"
                        ).is_file()
                        else "G:/subject/main/database/database.db"
                    )
                ),
            )
        except (ImportError, TypeError, ValueError) as exc:
            return {
                "status": BLOCKED_SOLVER_CAPABILITY,
                "tradable": False,
                "blocked_stage": "configuration_contract",
                "reason": str(exc),
                "fallback_used": False,
            }
        warehouse = Path(
            os.getenv(
                "RESEARCH_WAREHOUSE_DB",
                str(PROJECT_ROOT / "database" / "research_warehouse.db"),
            )
        ).expanduser().resolve()
        if not warehouse.is_file():
            return {
                "status": BLOCKED_DATA,
                "tradable": False,
                "blocked_stage": "data_contract",
                "reason": "research_warehouse_missing",
                "fallback_used": False,
            }
        backtest = (
            config_payload.get("backtest")
            if isinstance(config_payload.get("backtest"), Mapping)
            else {}
        )
        start = str(
            run_request.get("start")
            or backtest.get("start")
            or "20190531"
        ).replace("-", "")
        end = str(
            run_request.get("end")
            or backtest.get("end")
            or dt.date.today().strftime("%Y%m%d")
        ).replace("-", "")
        max_months = backtest.get("max_months")
        if max_months is not None:
            try:
                max_months = int(max_months)
            except (TypeError, ValueError):
                return {
                    "status": BLOCKED_INPUT,
                    "tradable": False,
                    "blocked_stage": "backtest_contract",
                    "reason": "max_months_must_be_integer",
                    "fallback_used": False,
                }
            if max_months < 84:
                return {
                    "status": BLOCKED_INPUT,
                    "tradable": False,
                    "blocked_stage": "backtest_contract",
                    "reason": (
                        "formal_publication_requires_at_least_84_months;"
                        "short_windows_are_diagnostic_only"
                    ),
                    "fallback_used": False,
                }
        try:
            with sqlite3.connect(str(warehouse), timeout=30.0) as connection:
                result = run_csi500_strategy_from_database(
                    connection,
                    start=start,
                    end=end,
                    max_months=max_months,
                    config=strategy_config,
                    persist_scores=True,
                    score_run_id=_identifier("ui-score"),
                )
                connection.commit()
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            return {
                "status": BLOCKED_DATA,
                "tradable": False,
                "blocked_stage": "database_pipeline",
                "reason": f"{type(exc).__name__}:{exc}",
                "fallback_used": False,
            }
        if cancel_requested():
            return {"status": CANCELLED, "tradable": False}
        if (
            not isinstance(result, Mapping)
            or int(result.get("tradable_period_count") or 0) <= 0
        ):
            return {
                "status": BLOCKED_DATA,
                "tradable": False,
                "blocked_stage": "database_pipeline",
                "reason": (
                    str(result.get("reason") or "no_tradable_period")
                    if isinstance(result, Mapping)
                    else "invalid_pipeline_result"
                ),
                "pipeline_audit": (
                    _scrub_solution_fields(result)
                    if isinstance(result, Mapping) else None
                ),
                "fallback_used": False,
            }
        return OptimizerBackendService._frontend_strategy_result(
            result, compiled_mandate
        )

    def _optimizer_config_from_mandate(
        mandate: Mapping[str, Any],
        rows: Sequence[Mapping[str, Any]],
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Translate only exactly representable mandate constraints."""

        values: dict[str, Any] = {}
        style_bounds: dict[str, tuple[float, float]] = {}
        industry_limits: dict[str, float] = {}
        blacklist: set[str] = set()
        whitelist: set[str] = set()
        mandatory: set[str] = set()
        buy_limit: dict[str, float] | None = None
        unsupported: list[str] = []
        constraints = mandate.get("constraints")
        if not isinstance(constraints, list):
            raise ValueError("confirmed mandate constraints are unavailable")
        for item in constraints:
            if not isinstance(item, Mapping) or not item.get("hard", True):
                if isinstance(item, Mapping) and item.get("hard") is False:
                    unsupported.append(f"{item.get('id')}:soft_constraint")
                continue
            scope = item.get("scope") if isinstance(item.get("scope"), Mapping) else {}
            kind = str(item.get("type") or "")
            metric = str(scope.get("metric") or "")
            lower = item.get("lower")
            upper = item.get("upper")
            constraint_id = str(item.get("id") or metric)
            if kind == "holding" and metric == "cardinality":
                if not isinstance(lower, int) or lower != upper:
                    unsupported.append(f"{constraint_id}:cardinality_must_be_exact")
                else:
                    values["target_holdings"] = lower
            elif kind == "holding" and metric == "security_weight":
                if lower is not None:
                    values["min_weight"] = float(lower)
                if upper is not None:
                    values["max_weight"] = float(upper)
            elif kind == "holding" and metric == "active_security_weight":
                if lower is None or upper is None or not math.isclose(float(lower), -float(upper), abs_tol=1e-12):
                    unsupported.append(f"{constraint_id}:active_bound_must_be_symmetric")
                else:
                    values["max_active_weight"] = float(upper)
            elif kind == "industry" and metric == "active_exposure":
                if lower is None or upper is None or not math.isclose(float(lower), -float(upper), abs_tol=1e-12):
                    unsupported.append(f"{constraint_id}:industry_bound_must_be_symmetric")
                else:
                    group = str(scope.get("group") or "all")
                    if group == "all":
                        values["industry_deviation"] = float(upper)
                    else:
                        industry_limits[group] = float(upper)
            elif kind == "style" and metric == "active_exposure":
                if lower is None or upper is None:
                    unsupported.append(f"{constraint_id}:style_requires_two_sided_bound")
                else:
                    style = str(scope.get("style") or "all")
                    if style == "all":
                        values["default_style_bounds"] = (float(lower), float(upper))
                    else:
                        style_bounds[style] = (float(lower), float(upper))
            elif kind == "active_risk" and metric == "tracking_error":
                if isinstance(lower, (int, float)) and float(lower) > 0:
                    unsupported.append(f"{constraint_id}:minimum_tracking_error_is_nonconvex")
                if upper is not None:
                    values["target_tracking_error"] = float(upper)
            elif kind == "active_risk" and metric == "active_variance":
                if isinstance(lower, (int, float)) and float(lower) > 0:
                    unsupported.append(f"{constraint_id}:minimum_active_variance_is_nonconvex")
                if upper is not None:
                    values["target_tracking_error"] = math.sqrt(float(upper))
            elif kind == "trading" and metric == "one_way_turnover":
                if isinstance(lower, (int, float)) and float(lower) > 0:
                    unsupported.append(f"{constraint_id}:minimum_turnover_is_nonconvex")
                if upper is not None:
                    values["one_way_turnover_limit"] = float(upper)
            elif kind == "trading" and metric == "two_way_turnover":
                if isinstance(lower, (int, float)) and float(lower) > 0:
                    unsupported.append(f"{constraint_id}:minimum_turnover_is_nonconvex")
                if upper is not None:
                    values["one_way_turnover_limit"] = 0.5 * float(upper)
            elif kind == "liquidity" and metric == "adv_participation":
                nav = inputs.get("portfolio_nav")
                if upper is None or not isinstance(nav, (int, float)) or float(nav) <= 0:
                    unsupported.append(f"{constraint_id}:portfolio_nav_and_upper_bound_required")
                else:
                    capacity: dict[str, float] = {}
                    for row in rows:
                        code = str(row.get("ts_code") or "")
                        adv = row.get("adv20_value", row.get("adv_value"))
                        if not code or not isinstance(adv, (int, float)) or float(adv) < 0:
                            capacity = {}
                            break
                        capacity[code] = float(upper) * float(adv) / float(nav)
                    if not capacity:
                        unsupported.append(f"{constraint_id}:point_in_time_adv_is_required")
                    else:
                        buy_limit = capacity
            elif kind == "list" and metric in {"blacklist", "forced_exclude"}:
                blacklist.update(str(code) for code in scope.get("security_set", []))
            elif kind == "list" and metric == "whitelist":
                whitelist.update(str(code) for code in scope.get("security_set", []))
            elif kind == "list" and metric == "forced_include":
                mandatory.update(str(code) for code in scope.get("security_set", []))
            else:
                unsupported.append(f"{constraint_id}:{kind}.{metric}")
        if unsupported:
            raise ValueError("unsupported hard mandate constraints: " + ",".join(unsupported))
        if industry_limits:
            industries = sorted({str(row.get("industry") or "") for row in rows})
            default = values.get("industry_deviation")
            if default is None and set(industry_limits) != set(industries):
                raise ValueError("industry-specific bounds must cover every industry or include an all bound")
            values["industry_deviation"] = {
                industry: industry_limits.get(industry, float(default)) for industry in industries
            }
        if style_bounds:
            values["style_bounds"] = style_bounds
        if blacklist:
            values["blacklist"] = tuple(sorted(blacklist))
        if whitelist:
            values["whitelist"] = tuple(sorted(whitelist))
        if mandatory:
            values["mandatory"] = tuple(sorted(mandatory))
        if buy_limit is not None:
            values["buy_limit"] = buy_limit
        return values

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        result = self.store.get_run(run_id)
        if result is None:
            return None
        internal = str(result["status"])
        if internal in FINAL_RUN_STATUSES and internal != AUDITED:
            result["result"] = _scrub_solution_fields(result.get("result"))
        public = _public_run_status(internal)
        progress = {
            "queued": 5,
            "running": 55,
            "completed": 100,
            "blocked": 100,
            "failed": 100,
            "cancelled": 100,
        }.get(public, 0)
        result["internal_status"] = internal
        result["status"] = public
        result["stage"] = internal.lower()
        result["progress"] = progress
        return result


optimizer_blueprint = Blueprint("optimizer", __name__)


def _service() -> OptimizerBackendService:
    service = current_app.extensions.get("optimizer_backend")
    if not isinstance(service, OptimizerBackendService):
        raise RuntimeError("optimizer backend is not registered")
    return service


def _request_payload() -> Mapping[str, Any] | None:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, Mapping) else None


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    return response


@optimizer_blueprint.get("/api/optimizer/bootstrap")
def optimizer_bootstrap() -> Response:
    return _no_store(jsonify(_service().bootstrap()))


@optimizer_blueprint.get("/api/optimizer/strategy-snapshot")
def optimizer_strategy_snapshot() -> Response:
    return _no_store(jsonify(_service().strategy_snapshot()))



@optimizer_blueprint.post("/api/optimizer/constraints/plans")
def optimizer_plan_options() -> tuple[Response, int] | Response:
    payload = _request_payload()
    if payload is None:
        return _no_store(jsonify({"status": BLOCKED_SCHEMA, "message": "JSON object required"})), 400
    output, status = _service().plan_options(payload)
    return _no_store(jsonify(output)), status


@optimizer_blueprint.post("/api/optimizer/constraints/interpret")
def optimizer_interpret() -> tuple[Response, int] | Response:
    payload = _request_payload()
    if payload is None:
        return _no_store(jsonify({"status": BLOCKED_SCHEMA, "message": "JSON object required"})), 400
    output, status = _service().interpret(payload)
    return _no_store(jsonify(output)), status


@optimizer_blueprint.post("/api/optimizer/constraints/validate")
def optimizer_validate() -> tuple[Response, int] | Response:
    payload = _request_payload()
    if payload is None:
        return _no_store(jsonify({"status": BLOCKED_SCHEMA, "message": "JSON object required"})), 400
    output, status = _service().validate(payload)
    return _no_store(jsonify(output)), status


@optimizer_blueprint.post("/api/optimizer/runs")
def optimizer_create_run() -> tuple[Response, int] | Response:
    payload = _request_payload()
    if payload is None:
        return _no_store(jsonify({"status": BLOCKED_SCHEMA, "message": "JSON object required"})), 400
    output, status = _service().create_run(payload)
    return _no_store(jsonify(output)), status


@optimizer_blueprint.get("/api/optimizer/runs")
def optimizer_list_runs() -> Response:
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        limit = 50
    runs = _service().store.list_runs(limit=limit)
    for item in runs:
        internal = str(item["status"])
        item["internal_status"] = internal
        item["status"] = _public_run_status(internal)
    return _no_store(
        jsonify({"status": "ok", "runs": runs})
    )


@optimizer_blueprint.get("/api/optimizer/runs/<run_id>")
def optimizer_get_run(run_id: str) -> tuple[Response, int] | Response:
    output = _service().get_run(run_id)
    if output is None:
        return _no_store(jsonify({"status": "NOT_FOUND", "message": "run_not_found"})), 404
    return _no_store(jsonify(output))


@optimizer_blueprint.post("/api/optimizer/runs/<run_id>/cancel")
def optimizer_cancel_run(run_id: str) -> tuple[Response, int] | Response:
    output = _service().store.request_cancel(run_id)
    if output is None:
        return _no_store(jsonify({"status": "NOT_FOUND", "message": "run_not_found"})), 404
    status_code = 202 if output["accepted"] else 409
    internal = str(output["status"])
    return _no_store(
        jsonify({
            "run_id": run_id, **output,
            "internal_status": internal,
            "status": _public_run_status(internal),
        })
    ), status_code


def register_optimizer(
    app: Any,
    *,
    state_db_path: str | os.PathLike[str] | None = None,
    llm_client: Callable[..., Any] | None = None,
    runner: Callable[..., Mapping[str, Any]] | None = None,
    available_solvers: Sequence[str] | None = None,
) -> OptimizerBackendService:
    """Register the API and return its injectable service instance."""

    existing = app.extensions.get("optimizer_backend")
    if isinstance(existing, OptimizerBackendService):
        return existing
    db_path = state_db_path or os.getenv("OPTIMIZER_STATE_DB") or DEFAULT_STATE_DB
    service = OptimizerBackendService(
        OptimizerStateStore(db_path),
        llm_client=llm_client,
        runner=runner,
        available_solvers=available_solvers,
    )
    app.extensions["optimizer_backend"] = service
    if "optimizer" not in app.blueprints:
        app.register_blueprint(optimizer_blueprint)
    return service


register_optimizer_backend = register_optimizer


__all__ = [
    "API_VERSION",
    "OptimizerStateStore",
    "OptimizerBackendService",
    "optimizer_blueprint",
    "register_optimizer",
    "register_optimizer_backend",
]
