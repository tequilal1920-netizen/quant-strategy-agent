"""LLM-assisted, evidence-bound portfolio mandate compiler.

The module deliberately stops before portfolio construction.  It turns a natural
language request into an auditable ``OptimizationMandate/v1`` draft, validates
the draft, checks obvious infeasibilities, and requires an explicit confirmation
hash.  The LLM has no authority to emit security weights and there is no local
template fallback when the LLM is unavailable or invalid.
"""

from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


SCHEMA_VERSION = "OptimizationMandate/v1"
KNOWLEDGE_SCHEMA_VERSION = "ConstraintKnowledgeBase/v1"
PLAN_SCHEMA_VERSION = "OptimizationPlanOptions/v1"

BLOCKED_LLM = "BLOCKED_LLM"
BLOCKED_SCHEMA = "BLOCKED_SCHEMA"
BLOCKED_SEMANTIC = "BLOCKED_SEMANTIC"
BLOCKED_SOLVER_CAPABILITY = "BLOCKED_SOLVER_CAPABILITY"
INFEASIBLE = "INFEASIBLE"
AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
AWAITING_PLAN_SELECTION = "AWAITING_PLAN_SELECTION"
CONFIRMED = "CONFIRMED"

ALLOWED_MODES = {"joint_cardinality", "fixed_candidate_set"}
ALLOWED_TYPES = {
    "holding",
    "industry",
    "style",
    "active_risk",
    "trading",
    "liquidity",
    "list",
}
ALLOWED_UNITS = {
    "count",
    "weight_fraction",
    "annualized_fraction",
    "variance",
    "turnover_fraction",
    "adv_fraction",
    "raw_exposure",
    "zscore",
    "currency",
    "days",
    "binary",
}
MISOCP_SOLVERS = {"GUROBI", "CPLEX", "MOSEK", "SCIP", "XPRESS"}
HYBRID_PHASE_I_SOLVER = "SCIPY_HIGHS_MILP"
HYBRID_PHASE_II_SOLVER = "CLARABEL"

TYPE_ALIASES = {
    "持仓": "holding",
    "持仓数量": "holding",
    "行业": "industry",
    "风格": "style",
    "主动风险": "active_risk",
    "跟踪误差": "active_risk",
    "交易": "trading",
    "换手": "trading",
    "流动性": "liquidity",
    "名单": "list",
}
UNIT_ALIASES = {
    "%": "weight_fraction",
    "百分比": "weight_fraction",
    "比例": "weight_fraction",
    "只": "count",
    "个": "count",
    "年化比例": "annualized_fraction",
    "换手比例": "turnover_fraction",
    "adv比例": "adv_fraction",
    "标准差": "zscore",
}

DIRECT_WEIGHT_KEYS = {
    "weights",
    "target_weights",
    "portfolio_weights",
    "security_weights",
    "weight_by_security",
    "orders",
    "target_positions",
}

TOP_LEVEL_KEYS = {
    "schema_version",
    "mode",
    "objective",
    "constraints",
    "retrieval_source_ids",
    "assumptions",
}
OBJECTIVE_KEYS = {
    "type",
    "benchmark_id",
    "score_artifact_id",
    "rebalance_frequency",
    "candidate_set_id",
    "risk_model_id",
    "as_of_date",
}
CONSTRAINT_KEYS = {
    "id",
    "type",
    "scope",
    "lower",
    "upper",
    "unit",
    "hard",
    "penalty",
    "priority",
    "formula",
    "data_dependencies",
    "evidence",
}
REQUIRED_CONSTRAINT_KEYS = CONSTRAINT_KEYS
SCOPE_KEYS = {
    "metric",
    "universe",
    "group",
    "style",
    "list_name",
    "security_set",
    "benchmark_relative",
    "aggregation",
    "candidate_set_id",
    "turnover_convention",
    "lookback",
    "lag",
}
EVIDENCE_KEYS = {"source_id", "claim", "field", "value"}

METRICS_BY_TYPE = {
    "holding": {"cardinality", "security_weight", "active_security_weight"},
    "industry": {"active_exposure"},
    "style": {"active_exposure"},
    "active_risk": {"tracking_error", "active_variance"},
    "trading": {"one_way_turnover", "two_way_turnover", "transaction_cost"},
    "liquidity": {"adv_participation", "days_to_liquidate", "minimum_adv"},
    "list": {"blacklist", "whitelist", "forced_include", "forced_exclude"},
}

_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,79}$")
_NUM_RE = re.compile(
    r"(?<![A-Za-z0-9_.])([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(%|bp|bps|基点)?",
    re.IGNORECASE,
)


class MandateCompilerError(ValueError):
    """Raised for explicit confirmation and compiler-contract violations."""


def load_knowledge_base(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Load and minimally validate the local constraint knowledge base."""

    kb_path = Path(path) if path else Path(__file__).with_name("constraint_knowledge_base.json")
    with kb_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("schema_version") != KNOWLEDGE_SCHEMA_VERSION:
        raise MandateCompilerError(
            f"knowledge base must use {KNOWLEDGE_SCHEMA_VERSION}, got {payload.get('schema_version')!r}"
        )
    if not isinstance(payload.get("sources"), list) or not isinstance(
        payload.get("constraint_templates"), list
    ):
        raise MandateCompilerError("knowledge base sources and constraint_templates must be lists")
    source_ids = [item.get("source_id") for item in payload["sources"] if isinstance(item, dict)]
    if any(not isinstance(item, str) or not item for item in source_ids):
        raise MandateCompilerError("every knowledge source requires a source_id")
    if len(source_ids) != len(set(source_ids)):
        raise MandateCompilerError("knowledge source_id values must be unique")
    return payload


def _source_map(knowledge_base: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        item["source_id"]: item
        for item in knowledge_base.get("sources", [])
        if isinstance(item, Mapping) and isinstance(item.get("source_id"), str)
    }


def retrieve_constraint_knowledge(
    query: str,
    *,
    top_k: int = 12,
    knowledge_base: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Retrieve constraint templates with explicit, resolvable source records.

    Retrieval is deterministic and auditable.  It is intentionally small: its
    purpose is to ground the LLM's constraint draft, not to generate weights.
    """

    kb = dict(knowledge_base or load_knowledge_base())
    text = (query or "").strip().lower()
    scored: list[tuple[int, str, Mapping[str, Any]]] = []
    for template in kb.get("constraint_templates", []):
        if not isinstance(template, Mapping):
            continue
        terms = [
            str(template.get("type", "")),
            str(template.get("template_id", "")),
            *[str(item) for item in template.get("aliases", [])],
        ]
        score = sum(3 if term.lower() in text else 0 for term in terms if term)
        tokens = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{2,}", " ".join(terms).lower())
        score += sum(1 for token in tokens if token and token in text)
        scored.append((score, str(template.get("template_id", "")), template))
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = [copy.deepcopy(item[2]) for item in scored[: max(1, min(top_k, len(scored)))]]

    source_lookup = _source_map(kb)
    selected_source_ids: list[str] = []
    for template in selected:
        for source_id in template.get("source_ids", []):
            if source_id in source_lookup and source_id not in selected_source_ids:
                selected_source_ids.append(source_id)
    sources = [copy.deepcopy(source_lookup[source_id]) for source_id in selected_source_ids]
    return {
        "knowledge_schema_version": kb["schema_version"],
        "numeric_policy": kb.get("numeric_policy"),
        "templates": selected,
        "sources": sources,
        "source_ids": selected_source_ids,
        "data_dependency_registry": list(kb.get("data_dependency_registry", [])),
    }


def _parse_bound(value: Any, *, unit: str | None = None) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return value
    stripped = value.strip().lower().replace(",", "")
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(%|bp|bps|基点)?", stripped)
    if not match:
        return value
    number = float(match.group(1))
    suffix = match.group(2)
    if suffix == "%":
        number /= 100.0
    elif suffix in {"bp", "bps", "基点"}:
        number /= 10000.0
    if unit == "count" and number.is_integer():
        return int(number)
    return number


def normalize_constraint(constraint: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize aliases and numeric strings without inventing fields."""

    output = copy.deepcopy(dict(constraint))
    raw_type = output.get("type")
    if isinstance(raw_type, str):
        output["type"] = TYPE_ALIASES.get(raw_type.strip(), raw_type.strip().lower())
    raw_unit = output.get("unit")
    if isinstance(raw_unit, str):
        output["unit"] = UNIT_ALIASES.get(raw_unit.strip(), raw_unit.strip().lower())
    unit = output.get("unit") if isinstance(output.get("unit"), str) else None
    for field in ("lower", "upper", "penalty"):
        if field in output:
            output[field] = _parse_bound(output[field], unit=unit)
    if isinstance(output.get("scope"), Mapping):
        scope = copy.deepcopy(dict(output["scope"]))
        if isinstance(scope.get("metric"), str):
            scope["metric"] = scope["metric"].strip().lower()
        output["scope"] = scope
    if isinstance(output.get("data_dependencies"), list):
        output["data_dependencies"] = [
            item.strip() if isinstance(item, str) else item for item in output["data_dependencies"]
        ]
    return output


def normalize_mandate_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    output = copy.deepcopy(dict(payload))
    if isinstance(output.get("mode"), str):
        output["mode"] = output["mode"].strip().lower()
    if isinstance(output.get("objective"), Mapping):
        objective = copy.deepcopy(dict(output["objective"]))
        if isinstance(objective.get("type"), str):
            objective["type"] = objective["type"].strip().lower()
        output["objective"] = objective
    if isinstance(output.get("constraints"), list):
        output["constraints"] = [
            normalize_constraint(item) if isinstance(item, Mapping) else item
            for item in output["constraints"]
        ]
    if isinstance(output.get("retrieval_source_ids"), list):
        output["retrieval_source_ids"] = [
            item.strip() if isinstance(item, str) else item
            for item in output["retrieval_source_ids"]
        ]
    return output


def _find_direct_weight_paths(value: Any, path: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key).strip().lower()
            child_path = f"{path}.{key}"
            if key_text in DIRECT_WEIGHT_KEYS:
                hits.append(child_path)
            hits.extend(_find_direct_weight_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            hits.extend(_find_direct_weight_paths(child, f"{path}[{index}]"))
    return hits


def _numeric_candidates(raw_request: str) -> list[float]:
    values: list[float] = []
    for match in _NUM_RE.finditer(raw_request or ""):
        number = float(match.group(1))
        suffix = (match.group(2) or "").lower()
        values.append(number)
        if suffix == "%":
            values.append(number / 100.0)
        elif suffix in {"bp", "bps", "基点"}:
            values.append(number / 10000.0)
    return values


def _numbers_close(left: Any, right: Any, tolerance: float = 1e-10) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return False
    return math.isclose(float(left), float(right), rel_tol=tolerance, abs_tol=tolerance)


def _user_request_contains_number(raw_request: str, value: float) -> bool:
    return any(
        _numbers_close(candidate, value) or _numbers_close(abs(candidate), abs(float(value)))
        for candidate in _numeric_candidates(raw_request)
    )


def _numeric_evidence_error(
    constraint: Mapping[str, Any],
    field: str,
    value: int | float,
    raw_request: str,
    known_source_ids: set[str],
) -> str | None:
    evidence = constraint.get("evidence")
    if not isinstance(evidence, list):
        return f"constraint {constraint.get('id')!r} numeric {field} requires evidence"
    matching: list[Mapping[str, Any]] = []
    for item in evidence:
        if not isinstance(item, Mapping):
            continue
        evidence_field = item.get("field")
        evidence_value = _parse_bound(item.get("value"), unit=str(constraint.get("unit", "")))
        if evidence_field == field and _numbers_close(evidence_value, value):
            matching.append(item)
    if not matching:
        return (
            f"constraint {constraint.get('id')!r} numeric {field}={value!r} requires a "
            "value-matched evidence item"
        )
    for item in matching:
        source_id = item.get("source_id")
        if source_id == "user_supplied" and _user_request_contains_number(raw_request, float(value)):
            return None
        if isinstance(source_id, str) and source_id in known_source_ids:
            return None
    return (
        f"constraint {constraint.get('id')!r} numeric {field}={value!r} is not present "
        "in the user request and has no recognized source evidence"
    )


def validate_mandate_schema(
    payload: Any,
    *,
    raw_request: str,
    knowledge_base: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return strict structural and evidence-binding validation errors."""

    kb = knowledge_base or load_knowledge_base()
    known_source_ids = set(_source_map(kb))
    errors: list[str] = []
    if not isinstance(payload, Mapping):
        return ["mandate must be a JSON object"]
    extra = set(payload) - TOP_LEVEL_KEYS
    missing = TOP_LEVEL_KEYS - set(payload)
    if extra:
        errors.append(f"unknown top-level keys: {sorted(extra)}")
    if missing:
        errors.append(f"missing top-level keys: {sorted(missing)}")
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must equal {SCHEMA_VERSION!r}")
    if payload.get("mode") not in ALLOWED_MODES:
        errors.append(f"mode must be one of {sorted(ALLOWED_MODES)}")

    objective = payload.get("objective")
    if not isinstance(objective, Mapping):
        errors.append("objective must be an object")
    else:
        objective_extra = set(objective) - OBJECTIVE_KEYS
        if objective_extra:
            errors.append(f"unknown objective keys: {sorted(objective_extra)}")
        required_objective = {"type", "benchmark_id", "score_artifact_id", "rebalance_frequency"}
        missing_objective = required_objective - set(objective)
        if missing_objective:
            errors.append(f"missing objective keys: {sorted(missing_objective)}")
        for key in required_objective:
            if key in objective and (not isinstance(objective[key], str) or not objective[key].strip()):
                errors.append(f"objective.{key} must be a non-empty string")
        if payload.get("mode") == "fixed_candidate_set":
            candidate_set_id = objective.get("candidate_set_id")
            if not isinstance(candidate_set_id, str) or not candidate_set_id.strip():
                errors.append("fixed_candidate_set mode requires objective.candidate_set_id")

    constraints = payload.get("constraints")
    if not isinstance(constraints, list) or not constraints:
        errors.append("constraints must be a non-empty list")
    else:
        ids: list[str] = []
        dependency_registry = set(kb.get("data_dependency_registry", []))
        for index, constraint in enumerate(constraints):
            prefix = f"constraints[{index}]"
            if not isinstance(constraint, Mapping):
                errors.append(f"{prefix} must be an object")
                continue
            missing_constraint = REQUIRED_CONSTRAINT_KEYS - set(constraint)
            extra_constraint = set(constraint) - CONSTRAINT_KEYS
            if missing_constraint:
                errors.append(f"{prefix} missing keys: {sorted(missing_constraint)}")
            if extra_constraint:
                errors.append(f"{prefix} unknown keys: {sorted(extra_constraint)}")
            constraint_id = constraint.get("id")
            if not isinstance(constraint_id, str) or not _ID_RE.fullmatch(constraint_id):
                errors.append(f"{prefix}.id must match {_ID_RE.pattern}")
            else:
                ids.append(constraint_id)
            if constraint.get("type") not in ALLOWED_TYPES:
                errors.append(f"{prefix}.type must be one of {sorted(ALLOWED_TYPES)}")
            scope = constraint.get("scope")
            if not isinstance(scope, Mapping):
                errors.append(f"{prefix}.scope must be an object")
            else:
                extra_scope = set(scope) - SCOPE_KEYS
                if extra_scope:
                    errors.append(f"{prefix}.scope has unknown keys: {sorted(extra_scope)}")
                if not isinstance(scope.get("metric"), str) or not scope.get("metric"):
                    errors.append(f"{prefix}.scope.metric must be a non-empty string")
                security_set = scope.get("security_set")
                if security_set is not None and (
                    not isinstance(security_set, list)
                    or any(not isinstance(item, str) or not item for item in security_set)
                ):
                    errors.append(f"{prefix}.scope.security_set must be a list of non-empty strings")
            for field in ("lower", "upper", "penalty"):
                value = constraint.get(field)
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                    errors.append(f"{prefix}.{field} must be numeric or null")
                elif isinstance(value, (int, float)) and not math.isfinite(float(value)):
                    errors.append(f"{prefix}.{field} must be finite")
            if constraint.get("unit") not in ALLOWED_UNITS:
                errors.append(f"{prefix}.unit must be one of {sorted(ALLOWED_UNITS)}")
            if not isinstance(constraint.get("hard"), bool):
                errors.append(f"{prefix}.hard must be boolean")
            priority = constraint.get("priority")
            if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 5:
                errors.append(f"{prefix}.priority must be an integer from 1 to 5")
            formula = constraint.get("formula")
            if not isinstance(formula, str) or not formula.strip():
                errors.append(f"{prefix}.formula must be a non-empty display formula")
            dependencies = constraint.get("data_dependencies")
            if not isinstance(dependencies, list) or not dependencies:
                errors.append(f"{prefix}.data_dependencies must be a non-empty list")
            else:
                unknown_dependencies = [item for item in dependencies if item not in dependency_registry]
                if unknown_dependencies:
                    errors.append(
                        f"{prefix}.data_dependencies contains unknown ids: {sorted(set(map(str, unknown_dependencies)))}"
                    )
            evidence = constraint.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                errors.append(f"{prefix}.evidence must be a non-empty list")
            else:
                for evidence_index, item in enumerate(evidence):
                    evidence_prefix = f"{prefix}.evidence[{evidence_index}]"
                    if not isinstance(item, Mapping):
                        errors.append(f"{evidence_prefix} must be an object")
                        continue
                    extra_evidence = set(item) - EVIDENCE_KEYS
                    if extra_evidence:
                        errors.append(f"{evidence_prefix} unknown keys: {sorted(extra_evidence)}")
                    source_id = item.get("source_id")
                    if source_id != "user_supplied" and source_id not in known_source_ids:
                        errors.append(f"{evidence_prefix}.source_id is not recognized")
                    if not isinstance(item.get("claim"), str) or not item.get("claim", "").strip():
                        errors.append(f"{evidence_prefix}.claim must be a non-empty string")
            for field in ("lower", "upper", "penalty"):
                value = constraint.get(field)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    evidence_error = _numeric_evidence_error(
                        constraint,
                        field,
                        value,
                        raw_request,
                        known_source_ids,
                    )
                    if evidence_error:
                        errors.append(evidence_error)
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        if duplicates:
            errors.append(f"constraint ids must be unique; duplicates: {duplicates}")

    retrieval_source_ids = payload.get("retrieval_source_ids")
    if not isinstance(retrieval_source_ids, list):
        errors.append("retrieval_source_ids must be a list")
    else:
        for source_id in retrieval_source_ids:
            if source_id not in known_source_ids:
                errors.append(f"retrieval_source_ids contains unknown source_id {source_id!r}")
    assumptions = payload.get("assumptions")
    if not isinstance(assumptions, list) or any(not isinstance(item, str) for item in assumptions):
        errors.append("assumptions must be a list of strings")

    weight_paths = _find_direct_weight_paths(payload)
    if weight_paths:
        errors.append(f"LLM mandate must not emit weights or orders; forbidden paths: {weight_paths}")
    return errors


def validate_mandate_semantics(payload: Mapping[str, Any]) -> list[str]:
    """Return cross-field errors that are independent of solver feasibility."""

    errors: list[str] = []
    constraints = payload.get("constraints", [])
    if not isinstance(constraints, list):
        return ["constraints are unavailable for semantic validation"]
    for index, constraint in enumerate(constraints):
        if not isinstance(constraint, Mapping):
            continue
        prefix = f"constraints[{index}]"
        constraint_type = constraint.get("type")
        scope = constraint.get("scope")
        metric = scope.get("metric") if isinstance(scope, Mapping) else None
        if constraint_type in METRICS_BY_TYPE and metric not in METRICS_BY_TYPE[constraint_type]:
            errors.append(
                f"{prefix}.scope.metric {metric!r} is incompatible with type {constraint_type!r}"
            )
        lower, upper = constraint.get("lower"), constraint.get("upper")
        if lower is None and upper is None and constraint_type != "list":
            errors.append(f"{prefix} requires at least one of lower or upper")
        if constraint.get("hard") is True and constraint.get("penalty") not in (None, 0, 0.0):
            errors.append(f"{prefix} hard constraints must use null or zero penalty")
        if constraint.get("hard") is False:
            penalty = constraint.get("penalty")
            if not isinstance(penalty, (int, float)) or isinstance(penalty, bool) or penalty <= 0:
                errors.append(f"{prefix} soft constraints require a positive penalty")
        if constraint_type == "list":
            if not isinstance(scope, Mapping) or not scope.get("security_set"):
                errors.append(f"{prefix} list constraints require scope.security_set")
            if lower is not None or upper is not None:
                errors.append(f"{prefix} list constraints must use null lower and upper")
        if metric == "cardinality":
            for field, value in (("lower", lower), ("upper", upper)):
                if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                    errors.append(f"{prefix}.{field} cardinality bound must be a non-negative integer")
        if metric in {"tracking_error", "active_variance", "one_way_turnover", "two_way_turnover", "adv_participation", "days_to_liquidate", "minimum_adv"}:
            for field, value in (("lower", lower), ("upper", upper)):
                if isinstance(value, (int, float)) and value < 0:
                    errors.append(f"{prefix}.{field} cannot be negative for metric {metric!r}")
    return errors


def _scope_key(constraint: Mapping[str, Any]) -> str:
    scope = constraint.get("scope") if isinstance(constraint.get("scope"), Mapping) else {}
    normalized = {
        key: scope.get(key)
        for key in ("metric", "universe", "group", "style", "list_name", "candidate_set_id")
        if key in scope
    }
    return f"{constraint.get('type')}:{json.dumps(normalized, sort_keys=True, ensure_ascii=False)}"


def quick_feasibility_precheck(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Check only deterministic, cheaply provable feasibility conditions.

    A successful precheck is not a feasibility certificate.  Full matrix
    feasibility, IIS generation and numerical tolerances remain solver duties.
    """

    errors: list[str] = []
    warnings: list[str] = []
    hard_intervals: dict[str, list[tuple[float | None, float | None, str]]] = {}
    exact_cardinality: int | None = None
    security_lower: float | None = None
    security_upper: float | None = None
    blacklist: set[str] = set()
    whitelist: set[str] = set()

    constraints = payload.get("constraints", [])
    if not isinstance(constraints, list):
        return {"status": INFEASIBLE, "errors": ["constraints unavailable"], "warnings": warnings}
    for constraint in constraints:
        if not isinstance(constraint, Mapping):
            continue
        constraint_id = str(constraint.get("id", "unknown"))
        lower = constraint.get("lower")
        upper = constraint.get("upper")
        if isinstance(lower, (int, float)) and isinstance(upper, (int, float)) and lower > upper:
            errors.append(f"{constraint_id}: lower {lower} exceeds upper {upper}")
        if constraint.get("hard") is True:
            hard_intervals.setdefault(_scope_key(constraint), []).append((lower, upper, constraint_id))
        scope = constraint.get("scope") if isinstance(constraint.get("scope"), Mapping) else {}
        metric = scope.get("metric")
        if constraint.get("hard") is True and metric == "cardinality" and lower == upper and isinstance(lower, int):
            if exact_cardinality is not None and exact_cardinality != lower:
                errors.append(
                    f"conflicting exact cardinalities: {exact_cardinality} and {lower} ({constraint_id})"
                )
            exact_cardinality = lower
        if constraint.get("hard") is True and metric == "security_weight":
            if isinstance(lower, (int, float)):
                security_lower = max(security_lower if security_lower is not None else -math.inf, float(lower))
            if isinstance(upper, (int, float)):
                security_upper = min(security_upper if security_upper is not None else math.inf, float(upper))
        if constraint.get("type") == "list" and isinstance(scope.get("security_set"), list):
            names = {str(item) for item in scope["security_set"]}
            if metric in {"blacklist", "forced_exclude"}:
                blacklist.update(names)
            if metric in {"whitelist", "forced_include"}:
                whitelist.update(names)

    for key, intervals in hard_intervals.items():
        lowers = [float(item[0]) for item in intervals if isinstance(item[0], (int, float))]
        uppers = [float(item[1]) for item in intervals if isinstance(item[1], (int, float))]
        if lowers and uppers and max(lowers) > min(uppers):
            errors.append(
                f"hard constraints have empty intersection for {key}: "
                f"lower={max(lowers)}, upper={min(uppers)}"
            )

    if exact_cardinality is not None:
        if security_lower is not None and exact_cardinality * security_lower > 1.0 + 1e-12:
            errors.append(
                f"cardinality {exact_cardinality} times minimum security weight {security_lower} exceeds budget 1"
            )
        if security_upper is not None and exact_cardinality * security_upper < 1.0 - 1e-12:
            errors.append(
                f"cardinality {exact_cardinality} times maximum security weight {security_upper} cannot fill budget 1"
            )
        if len(whitelist) > exact_cardinality:
            errors.append(
                f"forced-include list has {len(whitelist)} names but exact cardinality is {exact_cardinality}"
            )
    overlap = sorted(blacklist & whitelist)
    if overlap:
        errors.append(f"security list conflict; names are both included and excluded: {overlap}")
    warnings.append(
        "Quick precheck is not a solver feasibility certificate; full PIT matrices, covariance PSD, "
        "tradeability and an IIS-capable deterministic solver must still be checked."
    )
    return {
        "status": INFEASIBLE if errors else "PRECHECK_PASSED",
        "errors": errors,
        "warnings": warnings,
        "facts": {
            "exact_cardinality": exact_cardinality,
            "security_weight_lower": security_lower,
            "security_weight_upper": security_upper,
            "forced_include_count": len(whitelist),
            "forced_exclude_count": len(blacklist),
        },
    }


def _discover_available_solvers(explicit: Sequence[str] | None) -> list[str]:
    if explicit is not None:
        return sorted({str(item).upper() for item in explicit})
    configured = os.getenv("PORTFOLIO_AVAILABLE_SOLVERS", "")
    if configured.strip():
        return sorted({item.strip().upper() for item in configured.split(",") if item.strip()})
    try:
        import cvxpy as cp  # type: ignore

        return sorted({str(item).upper() for item in cp.installed_solvers()})
    except Exception:
        return []


def build_solver_policy(mode: str, available_solvers: Sequence[str] | None = None) -> dict[str, Any]:
    solvers = _discover_available_solvers(available_solvers)
    if mode == "joint_cardinality":
        native_capable = sorted(set(solvers) & MISOCP_SOLVERS)
        hybrid_ready = {
            HYBRID_PHASE_I_SOLVER,
            HYBRID_PHASE_II_SOLVER,
        }.issubset(solvers)
        if hybrid_ready:
            selected_route = "highs_milp_linear_support_then_clarabel_full_socp_certification"
        elif native_capable:
            selected_route = "native_misocp_or_miqcp"
        else:
            selected_route = None
        return {
            "mode": mode,
            "construction": selected_route,
            "required_problem_class": (
                "SCIPY_HIGHS_MILP_linear_support_plus_CLARABEL_SOCP_certification"
                "_or_native_MISOCP_or_MIQCP"
            ),
            "available_solvers": solvers,
            "capable_solvers": native_capable,
            "native_misocp_solvers": native_capable,
            "hybrid_route_ready": hybrid_ready,
            "hybrid_phase_i_solver": HYBRID_PHASE_I_SOLVER,
            "hybrid_phase_i_certificate": "all_linear_mandates_and_exact_binary_support_feasible",
            "hybrid_phase_ii_solver": HYBRID_PHASE_II_SOLVER,
            "hybrid_phase_ii_certificate": "full_socp_constraints_and_independent_residuals_certified",
            "candidate_set_must_be_frozen_before_solver": False,
            "support_is_jointly_optimized_with_linear_mandates": True,
            "milp_trial_weights_are_tradable": False,
            "global_miqcp_optimality_claimed": False,
            "capability_status": (
                "READY"
                if hybrid_ready or native_capable
                else BLOCKED_SOLVER_CAPABILITY
            ),
            "semantic_fallback_allowed": False,
            "prohibited_fallback": (
                "pre-ranked_or_heuristic_top-K_support_followed_by_continuous_QP"
            ),
        }
    return {
        "mode": mode,
        "construction": "stage_1_deterministic_candidate_selection_then_stage_2_continuous_optimization",
        "required_problem_class": "QP_or_SOCP",
        "available_solvers": solvers,
        "capability_status": "READY",
        "candidate_set_must_be_frozen_before_solver": True,
        "is_mixed_integer_formulation": False,
        "semantic_fallback_allowed": False,
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _core_mandate(value: Mapping[str, Any]) -> Mapping[str, Any]:
    mandate = value.get("mandate") if isinstance(value.get("mandate"), Mapping) else value
    return {key: mandate.get(key) for key in sorted(TOP_LEVEL_KEYS)}


def compute_draft_hash(value: Mapping[str, Any]) -> str:
    """Hash only decision-relevant mandate fields, excluding status and timestamps."""

    return hashlib.sha256(_canonical_json(_core_mandate(value)).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def confirm_mandate(
    compiled: Mapping[str, Any],
    *,
    actor: str,
    expected_draft_hash: str,
    confirmed_at: str | None = None,
) -> dict[str, Any]:
    """Confirm an unchanged draft; confirmation never executes a solver."""

    if not isinstance(actor, str) or not actor.strip():
        raise MandateCompilerError("actor is required")
    if compiled.get("status") != AWAITING_CONFIRMATION:
        raise MandateCompilerError("only an AWAITING_CONFIRMATION draft can be confirmed")
    current_hash = compute_draft_hash(compiled)
    if expected_draft_hash != current_hash or compiled.get("draft_hash") != current_hash:
        raise MandateCompilerError("draft hash mismatch; review the edited mandate before confirming")
    timestamp = confirmed_at or _utc_now()
    confirmation_payload = {
        "draft_hash": current_hash,
        "actor": actor.strip(),
        "confirmed_at": timestamp,
    }
    confirmation_payload["confirm_hash"] = hashlib.sha256(
        _canonical_json(confirmation_payload).encode("utf-8")
    ).hexdigest()
    output = copy.deepcopy(dict(compiled))
    output["status"] = CONFIRMED
    output["confirmation"] = confirmation_payload
    return output


def is_confirmation_valid(compiled: Mapping[str, Any]) -> bool:
    confirmation = compiled.get("confirmation")
    if compiled.get("status") != CONFIRMED or not isinstance(confirmation, Mapping):
        return False
    current_hash = compute_draft_hash(compiled)
    if confirmation.get("draft_hash") != current_hash:
        return False
    payload = {
        "draft_hash": confirmation.get("draft_hash"),
        "actor": confirmation.get("actor"),
        "confirmed_at": confirmation.get("confirmed_at"),
    }
    expected = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return confirmation.get("confirm_hash") == expected


def refresh_after_edit(compiled: Mapping[str, Any]) -> dict[str, Any]:
    """Re-hash an edited draft and invalidate any prior confirmation."""

    output = copy.deepcopy(dict(compiled))
    new_hash = compute_draft_hash(output)
    if new_hash != output.get("draft_hash"):
        output["draft_hash"] = new_hash
        output["confirmation"] = None
        if output.get("status") == CONFIRMED:
            output["status"] = AWAITING_CONFIRMATION
    return output


def _router_endpoint(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    if value.endswith("/chat/completions"):
        return value
    if value.endswith("/v1"):
        return f"{value}/chat/completions"
    return f"{value}/v1/chat/completions"


def _extract_router_content(response: Any) -> str:
    if not isinstance(response, Mapping):
        raise MandateCompilerError("AI Router response must be a JSON object")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise MandateCompilerError("AI Router response is missing choices[0]")
    message = choices[0].get("message")
    if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
        raise MandateCompilerError("AI Router response is missing string message.content")
    return message["content"]


def _router_timeout_seconds() -> int:
    raw = os.getenv("AI_ROUTER_TIMEOUT_SECONDS", "60").strip()
    try:
        value = int(raw)
    except ValueError:
        return 60
    return min(max(value, 15), 180)


def _stream_router_content(response: Any) -> str:
    """Parse OpenAI-compatible Chat Completions SSE chunks."""
    parts: list[str] = []
    for raw_line in response:
        try:
            line = raw_line.decode("utf-8", errors="ignore").strip()
        except AttributeError:
            line = str(raw_line).strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        choices = event.get("choices") if isinstance(event, Mapping) else None
        if not choices:
            continue
        first = choices[0] if isinstance(choices[0], Mapping) else {}
        delta = first.get("delta") if isinstance(first.get("delta"), Mapping) else {}
        message = first.get("message") if isinstance(first.get("message"), Mapping) else {}
        piece = delta.get("content") or message.get("content") or first.get("text") or ""
        if isinstance(piece, list):
            piece = "".join(str(item.get("text") if isinstance(item, Mapping) else item) for item in piece)
        if piece:
            parts.append(str(piece))
    content = "".join(parts).strip()
    if not content:
        raise MandateCompilerError("AI Router streaming response did not contain content")
    return content

def _call_ai_router(system_prompt: str, user_payload: Mapping[str, Any]) -> str:
    key = os.getenv("AI_ROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("AI_ROUTER_URL") or os.getenv("AI_ROUTER_BASE_URL")
    if not key:
        raise MandateCompilerError("AI_ROUTER_API_KEY or OPENAI_API_KEY is required")
    if not base_url:
        raise MandateCompilerError("AI_ROUTER_URL or AI_ROUTER_BASE_URL is required")
    user_content = str(user_payload.get("text")) if isinstance(user_payload, Mapping) and user_payload.get("text") else _canonical_json(user_payload)
    request_payload = {
        "model": os.getenv("AI_ROUTER_MODEL", "gpt-5.5"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
    max_tokens_raw = os.getenv("AI_ROUTER_MAX_TOKENS", "900").strip()
    if max_tokens_raw:
        try:
            max_tokens = int(max_tokens_raw)
        except ValueError:
            max_tokens = 2200
        if max_tokens > 0:
            request_payload["max_tokens"] = min(max(max_tokens, 512), 6000)
    reasoning_effort = os.getenv("AI_ROUTER_REASONING_EFFORT", "xhigh").strip()
    if reasoning_effort:
        request_payload["reasoning_effort"] = reasoning_effort
    stream_raw = os.getenv("AI_ROUTER_STREAM", "0").strip().lower()
    stream_enabled = stream_raw not in {"0", "false", "no", "off"}
    if stream_enabled:
        request_payload["stream"] = True
    body = _canonical_json(request_payload).encode("utf-8")
    request = urllib.request.Request(
        _router_endpoint(base_url),
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Origin": "https://ai.router.team",
            "Referer": "https://ai.router.team/docs",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_router_timeout_seconds()) as response:  # nosec B310 - configured API endpoint
            if request_payload.get("stream"):
                try:
                    return _stream_router_content(response)
                except MandateCompilerError as stream_exc:
                    if "streaming response did not contain content" not in str(stream_exc):
                        raise
                    retry_payload = copy.deepcopy(request_payload)
                    retry_payload.pop("stream", None)
                    retry_body = _canonical_json(retry_payload).encode("utf-8")
                    retry_request = urllib.request.Request(
                        _router_endpoint(base_url),
                        data=retry_body,
                        headers={
                            "Authorization": f"Bearer {key}",
                            "Content-Type": "application/json",
                            "Accept": "application/json, text/plain, */*",
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                            "Origin": "https://ai.router.team",
                            "Referer": "https://ai.router.team/docs",
                        },
                        method="POST",
                    )
                    with urllib.request.urlopen(retry_request, timeout=_router_timeout_seconds()) as retry_response:  # nosec B310 - configured API endpoint
                        parsed = json.loads(retry_response.read().decode("utf-8"))
                    return _extract_router_content(parsed)
            parsed = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Keep the status code for diagnosis, but never surface the upstream body:
        # gateways may echo request fragments or other sensitive diagnostics.
        raise MandateCompilerError(f"AI Router call failed: HTTP {int(exc.code)}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise MandateCompilerError(f"AI Router call failed: {type(exc).__name__}") from exc
    return _extract_router_content(parsed)


def _invoke_llm(
    client: Callable[..., Any] | None,
    system_prompt: str,
    user_payload: Mapping[str, Any],
) -> Any:
    if client is None:
        return _call_ai_router(system_prompt, user_payload)
    try:
        return client(system_prompt=system_prompt, user_payload=copy.deepcopy(dict(user_payload)))
    except TypeError:
        return client(system_prompt, copy.deepcopy(dict(user_payload)))


def _strict_json_object(value: Any) -> tuple[dict[str, Any] | None, str | None, str]:
    if isinstance(value, Mapping):
        copied = copy.deepcopy(dict(value))
        return copied, None, _canonical_json(copied)
    if not isinstance(value, str):
        return None, "LLM response must be a JSON object or a string containing exactly one JSON object", repr(value)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        return None, f"strict JSON parse failed: {exc.msg} at line {exc.lineno} column {exc.colno}", value
    if not isinstance(parsed, dict):
        return None, "LLM response root must be a JSON object", value
    return parsed, None, value


def _system_prompt() -> str:
    return (
        "You are a portfolio-mandate compiler, not a portfolio manager. Return one strict JSON object "
        f"conforming to {SCHEMA_VERSION}. Never return markdown, comments, code fences, security weights, "
        "orders or target positions. Preserve the user's benchmark, score artifact and frequency. Express "
        "constraints only with the allowed schema. Numeric lower, upper and penalty values must have a "
        "value-matched evidence item; use source_id=user_supplied only when the number occurs in the request. "
        "Do not invent page numbers or performance guarantees. The deterministic solver, not the LLM, owns "
        "feasibility and weights. For joint_cardinality require exact binary support selection jointly "
        "with every linear mandate. The certified hybrid route is HiGHS MILP linear-support feasibility "
        "followed by Clarabel full-SOCP certification on that solver-selected support; it is not a "
        "pre-ranked candidate set, not a semantic fallback, and makes no global MIQCP optimality claim. "
        "A native MISOCP/MIQCP route is also valid. For fixed_candidate_set require a genuinely frozen "
        "candidate_set_id and do not describe it as a MIP."
    )


def _generation_payload(
    raw_request: str,
    mode: str,
    retrieval: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "phase": "initial",
        "raw_request": raw_request,
        "requested_mode": mode,
        "schema_contract": {
            "schema_version": SCHEMA_VERSION,
            "top_level_keys": sorted(TOP_LEVEL_KEYS),
            "constraint_keys": sorted(CONSTRAINT_KEYS),
            "constraint_types": sorted(ALLOWED_TYPES),
            "units": sorted(ALLOWED_UNITS),
            "objective_required": [
                "type",
                "benchmark_id",
                "score_artifact_id",
                "rebalance_frequency",
            ],
            "evidence_item": {"source_id": "...", "claim": "...", "field": "lower|upper|penalty", "value": "exact numeric value"},
        },
        "retrieved_knowledge": retrieval,
        "deterministic_context": copy.deepcopy(dict(context or {})),
    }


def _repair_payload(
    initial: Mapping[str, Any],
    prior_output: str,
    errors: Sequence[str],
    repair_index: int,
) -> dict[str, Any]:
    return {
        **copy.deepcopy(dict(initial)),
        "phase": "repair",
        "repair_index": repair_index,
        "prior_output": prior_output,
        "validation_errors": list(errors),
        "instruction": "Return a complete corrected strict JSON object only. Do not omit evidence or add weights.",
    }


def _blocked_result(
    status: str,
    *,
    errors: Sequence[str],
    attempts: int,
    retrieval: Mapping[str, Any],
    mandate: Mapping[str, Any] | None = None,
    solver_policy: Mapping[str, Any] | None = None,
    feasibility: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "schema_version": SCHEMA_VERSION,
        "mandate": copy.deepcopy(dict(mandate)) if isinstance(mandate, Mapping) else None,
        "errors": list(errors),
        "attempts": attempts,
        "llm_required": True,
        "fallback_used": False,
        "weights_emitted": False,
        "retrieval": {
            "knowledge_schema_version": retrieval.get("knowledge_schema_version"),
            "source_ids": list(retrieval.get("source_ids", [])),
        },
        "solver_policy": copy.deepcopy(dict(solver_policy)) if isinstance(solver_policy, Mapping) else None,
        "feasibility": copy.deepcopy(dict(feasibility)) if isinstance(feasibility, Mapping) else None,
        "draft_hash": None,
        "confirmation": None,
    }


def _blocked_plan_result(
    status: str,
    *,
    errors: Sequence[str],
    attempts: int,
    retrieval: Mapping[str, Any],
    solver_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "schema_version": PLAN_SCHEMA_VERSION,
        "options": [],
        "errors": list(errors),
        "attempts": attempts,
        "llm_required": True,
        "fallback_used": False,
        "weights_emitted": False,
        "retrieval": {
            "knowledge_schema_version": retrieval.get("knowledge_schema_version"),
            "source_ids": list(retrieval.get("source_ids", [])),
        },
        "solver_policy": copy.deepcopy(dict(solver_policy)) if isinstance(solver_policy, Mapping) else None,
    }


def _plan_system_prompt() -> str:
    return (
        'Return minified ASCII JSON only. If user content starts with '
        '"Return exactly this JSON:", copy that JSON object exactly and stop. '
        'Never return markdown, prose, weights, holdings, formulas, or code.'
    )


def _compact_plan_retrieval(retrieval: Mapping[str, Any]) -> dict[str, Any]:
    formulas: list[str] = []
    for item in list(retrieval.get("templates") or [])[:8]:
        if not isinstance(item, Mapping):
            continue
        formula = str(item.get("formula") or "").strip()
        if formula and formula not in formulas:
            formulas.append(formula)
    return {
        "source_ids": list(retrieval.get("source_ids") or [])[:8],
        "formulas": formulas,
    }


def _compact_plan_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    ctx = copy.deepcopy(dict(context or {}))
    base = ctx.get("base_config") if isinstance(ctx.get("base_config"), Mapping) else {}
    universe = base.get("universe") if isinstance(base.get("universe"), Mapping) else {}
    compact: dict[str, Any] = {}
    for key in ("universe", "rebalance_frequency", "knowledge_base_version"):
        if key in ctx:
            compact[key] = ctx[key]
    if universe:
        compact["base_universe"] = {
            "name": universe.get("name"),
            "code": universe.get("code"),
            "holdings": universe.get("holdings"),
            "rebalance_frequency": universe.get("rebalance_frequency"),
            "score_source": universe.get("score_source"),
        }
    return compact




def _keyword_number(raw_request: str, keywords: Sequence[str]) -> float | None:
    text = str(raw_request or "")
    if not text:
        return None
    pattern = re.compile(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(%|％)?")
    for keyword in keywords:
        index = text.find(keyword)
        if index < 0:
            continue
        window = text[index:index + 36]
        match = pattern.search(window)
        if not match:
            continue
        try:
            value = float(match.group(1))
        except ValueError:
            continue
        if match.group(2) or value > 1.0:
            value = value / 100.0
        if math.isfinite(value):
            return value
    return None


def _deterministic_plan_updates(raw_request: str) -> dict[str, Any]:
    candidates: list[tuple[str, tuple[str, ...]]] = [
        ("industry_bound", ("行业偏离", "行业暴露", "行业约束", "行业")),
        ("style_bound", ("风格暴露", "风格偏离", "风格约束", "风格")),
        ("tracking_error_limit", ("跟踪误差", "TE", "tracking error")),
        ("turnover_limit", ("换手率", "换手", "turnover")),
        ("min_weight", ("权重下限", "单票下限", "最小权重")),
        ("max_weight", ("权重上限", "单票上限", "最大权重")),
        ("max_active_weight", ("主动权重", "个股偏离", "active weight")),
        ("max_adv_participation", ("ADV", "流动性参与", "成交额参与")),
    ]
    updates: dict[str, Any] = {}
    for key, keywords in candidates:
        value = _keyword_number(raw_request, keywords)
        if value is not None:
            updates[key] = round(float(value), 8)
    count_match = re.search(r"(?:选取|选择|配置|持有)?\s*(\d{1,3})\s*只", str(raw_request or ""))
    if count_match:
        count = int(count_match.group(1))
        if 1 <= count <= 500:
            updates["target_count"] = count
    return updates


def _plan_candidate_json(raw_request: str) -> str:
    option = {
        "id": "plan_1",
        "parameter_updates": _deterministic_plan_updates(raw_request),
        "extra_constraints": [],
        "risk_bias": "balanced",
        "constraint_keywords": [],
    }
    return json.dumps(
        {"schema_version": PLAN_SCHEMA_VERSION, "options": [option]},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )

def _plan_generation_payload(
    raw_request: str,
    mode: str,
    retrieval: Mapping[str, Any],
    solver_policy: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    compact_context = _compact_plan_context(context)
    base_universe = compact_context.get("base_universe") if isinstance(compact_context.get("base_universe"), Mapping) else {}
    formulas = list(_compact_plan_retrieval(retrieval).get("formulas") or [])[:6]
    if not formulas:
        formulas = [
            "sum_i z_i = target_count, z_i in {0,1}",
            "min_weight*z_i <= w_i <= max_weight*z_i",
            "abs(G_g'(w-b)) <= industry_bound",
            "abs(F_k'(w-b)) <= style_bound",
            "sqrt(annualization*(w-b)'Sigma(w-b)) <= tracking_error_limit",
            "0.5*sum_i abs(w_i-w_prev_i) <= turnover_limit",
        ]
    candidate_json = _plan_candidate_json(raw_request)
    return {
        "phase": "plan_options",
        "text": "Return exactly this JSON: " + candidate_json,
    }

def _text_list(value: Any, *, max_items: int = 12) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, Mapping):
        items: list[str] = []
        for key, item in value.items():
            if len(items) >= max_items:
                break
            if isinstance(item, (str, int, float, bool)) or item is None:
                items.append(f"{key}: {item}")
            else:
                items.append(f"{key}: {_canonical_json(item)}")
        return items
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        output: list[str] = []
        for item in value:
            if len(output) >= max_items:
                break
            if isinstance(item, str) and item.strip():
                output.append(item.strip())
            elif isinstance(item, Mapping):
                label = item.get("formula") or item.get("name") or item.get("metric") or _canonical_json(item)
                output.append(str(label))
            elif item is not None:
                output.append(str(item))
        return output
    return [str(value)]


def _normalize_plan_constraints(value: Any) -> list[dict[str, Any]]:
    raw_items = value if isinstance(value, list) else ([] if value is None else [value])
    output: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items[:12]):
        if isinstance(item, Mapping):
            normalized = copy.deepcopy(dict(item))
            normalized.setdefault("name", normalized.get("metric") or f"constraint_{index + 1}")
            normalized.setdefault("type", normalized.get("category") or "active_risk")
            normalized.setdefault("formula", normalized.get("equation") or normalized.get("display_formula") or "")
            normalized.setdefault("rationale", normalized.get("reason") or normalized.get("description") or "")
            output.append(normalized)
        elif item is not None:
            output.append({"name": f"constraint_{index + 1}", "type": "active_risk", "formula": str(item), "rationale": "LLM generated constraint expression"})
    return output


def _safe_plan_id(value: Any, index: int) -> str:
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value or "").strip().lower()).strip("_.-")
    if not text:
        text = f"plan_{index + 1}"
    if not re.match(r"^[a-z]", text):
        text = f"plan_{index + 1}_{text}"
    return text[:64]


def _build_mandate_request(raw_request: str, option: Mapping[str, Any]) -> str:
    parts = [raw_request.strip(), f"选择方案：{option.get('name') or option.get('id')}。"]
    objective = str(option.get("objective_equation") or "").strip()
    if objective:
        parts.append(f"目标函数：{objective}")
    equations = _text_list(option.get("constraint_equations"), max_items=8)
    if equations:
        parts.append("约束方程：" + "；".join(equations))
    params = option.get("default_parameters")
    if isinstance(params, Mapping) and params:
        parts.append("默认参数：" + _canonical_json(params))
    return "\n".join(item for item in parts if item)


def _default_optimizer_objective_equation() -> str:
    return "max λa·α'w - λr·||R(w-b)||₂² - λs·||w-s||₂² - λt·||w-p||₁ - λ2·||w-p||₂²"


def _default_optimizer_constraint_equations() -> list[str]:
    return [
        "Σw_i=1, w_i≥0, z_i∈{0,1}, Σz_i=50",
        "w_min z_i ≤ w_i ≤ w_max z_i",
        "|G'(w-b)|≤industry_bound",
        "|F'(w-b)|≤style_bound",
        "√(252·(w-b)'Σ(w-b))≤tracking_error_limit",
        "0.5·Σ|w_i-w_prev_i|≤turnover_limit",
    ]


def _default_optimizer_solver_steps() -> list[str]:
    return [
        "默认参数：读取中证500、因子实验室champion得分、风险暴露、历史持仓",
        "约束解释：LLM把自然语言转成参数更新和1-3个方案骨架",
        "人工确认：用户选择/修改一个方案后再进入求解",
        "HiGHS选股：branch-and-cut整数规划锁定50只股票支持集",
        "Clarabel求权：原始-对偶内点法求SOCP连续权重并审计约束",
    ]


def _compact_generated_constraints(option: Mapping[str, Any]) -> list[dict[str, Any]]:
    updates = option.get("parameter_updates") or option.get("parameter_adjustments") or option.get("params")
    # Real router output is constrained to ASCII parameter extraction.  If that
    # field is present, ignore free-text LLM constraint prose so user-facing
    # Chinese never depends on upstream encoding quirks.  Test doubles and
    # legacy payloads without parameter_updates still keep their constraints.
    items = None if isinstance(updates, Mapping) else (option.get("added_constraints") or option.get("constraints"))
    constraints = _normalize_plan_constraints(items)
    if isinstance(updates, Mapping):
        label_map = {
            "tracking_error_limit": "跟踪误差上限",
            "min_weight": "权重下限",
            "max_weight": "权重上限",
            "industry_bound": "行业偏离",
            "style_bound": "风格暴露",
            "turnover_limit": "换手率",
            "target_count": "持仓数量",
            "whitelist": "白名单",
            "blacklist": "黑名单",
        }
        for key, value in list(updates.items())[:8]:
            constraints.append({
                "name": label_map.get(str(key), str(key)),
                "type": "parameter_update",
                "formula": f"{key} = {value}",
                "rationale": "LLM识别出的自然语言参数修改",
            })
    return constraints[:12]


def _normalize_plan_option(option: Mapping[str, Any], index: int, raw_request: str) -> dict[str, Any]:
    normalized = copy.deepcopy(dict(option))
    normalized["id"] = _safe_plan_id(normalized.get("id") or normalized.get("name"), index)
    bias = str(normalized.get("risk_bias") or normalized.get("profile") or "balanced").lower()
    default_names = ["稳健约束方案", "收益增强方案", "低换手防守方案"]
    raw_name = str(normalized.get("name") or "").strip()
    if raw_name and raw_name.isascii() and not raw_name.lower().startswith("plan_"):
        normalized["name"] = raw_name
    else:
        normalized["name"] = default_names[min(index, len(default_names) - 1)]
    normalized["profile"] = str(normalized.get("profile") or normalized.get("style") or bias or "约束组合优化").strip()
    raw_summary = str(normalized.get("summary") or normalized.get("description") or "").strip()
    normalized["summary"] = raw_summary if raw_summary and raw_summary.isascii() else "根据自然语言约束调整默认参数，并保留HiGHS选股与Clarabel求权链路。"
    normalized["objective_equation"] = str(normalized.get("objective_equation") or normalized.get("objective") or _default_optimizer_objective_equation()).strip()
    normalized["objective_terms"] = _text_list(normalized.get("objective_terms"), max_items=10) or ["alpha_score", "tracking_error", "style_industry_deviation", "turnover", "weight_smoothing"]
    default_parameters = normalized.get("default_parameters")
    if isinstance(default_parameters, Mapping):
        params = copy.deepcopy(dict(default_parameters))
    else:
        updates = normalized.get("parameter_updates") or normalized.get("parameter_adjustments") or normalized.get("params")
        params = {"llm_parameter_updates": copy.deepcopy(dict(updates))} if isinstance(updates, Mapping) else {}
    normalized["default_parameters"] = params
    normalized["added_constraints"] = _compact_generated_constraints(normalized)
    if not normalized["added_constraints"]:
        normalized["added_constraints"] = [
            {"name": "tracking_error", "type": "active_risk", "formula": "TE≤tracking_error_limit", "rationale": "默认跟踪误差约束"},
            {"name": "turnover", "type": "implementation", "formula": "0.5·Σ|w_i-w_prev_i|≤turnover_limit", "rationale": "默认换手约束"},
        ]
    normalized["constraint_equations"] = _text_list(normalized.get("constraint_equations"), max_items=10) or _default_optimizer_constraint_equations()
    normalized["solver_steps"] = _text_list(normalized.get("solver_steps"), max_items=8) or _default_optimizer_solver_steps()
    raw_tradeoff = str(normalized.get("expected_tradeoff") or normalized.get("tradeoff") or "").strip()
    normalized["expected_tradeoff"] = raw_tradeoff if raw_tradeoff and raw_tradeoff.isascii() else "在提高alpha得分的同时控制行业、风格、跟踪误差和换手。"
    mandate_request = str(normalized.get("mandate_request") or normalized.get("compile_instruction") or "").strip()
    if not mandate_request:
        mandate_request = _build_mandate_request(raw_request, normalized)
    normalized["mandate_request"] = mandate_request
    normalized["compile_instruction"] = mandate_request
    return normalized


def _normalize_plan_payload(payload: Mapping[str, Any], raw_request: str) -> dict[str, Any]:
    raw_options = payload.get("options") or payload.get("plans") or payload.get("schemes")
    options = raw_options if isinstance(raw_options, list) else []
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "options": [
            _normalize_plan_option(item, index, raw_request)
            for index, item in enumerate(options)
            if isinstance(item, Mapping)
        ],
    }


def _validate_plan_payload(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != PLAN_SCHEMA_VERSION:
        errors.append(f"schema_version must equal {PLAN_SCHEMA_VERSION!r}")
    options = payload.get("options")
    if not isinstance(options, list) or not options:
        errors.append("options must contain 1 to 3 plan options")
        return errors
    if len(options) > 3:
        errors.append("options must contain no more than 3 plan options")
    forbidden = _find_direct_weight_paths(payload)
    if forbidden:
        errors.append("plan options must not contain direct weight/order fields: " + ",".join(forbidden))
    ids: set[str] = set()
    for index, option in enumerate(options):
        prefix = f"options[{index}]"
        if not isinstance(option, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        option_id = option.get("id")
        if not isinstance(option_id, str) or not option_id.strip():
            errors.append(f"{prefix}.id must be a non-empty string")
        elif option_id in ids:
            errors.append(f"{prefix}.id must be unique")
        else:
            ids.add(option_id)
        for field in ("name", "objective_equation", "mandate_request"):
            if not isinstance(option.get(field), str) or not option.get(field, "").strip():
                errors.append(f"{prefix}.{field} must be a non-empty string")
        for field in ("objective_terms", "added_constraints", "constraint_equations", "solver_steps"):
            if not isinstance(option.get(field), list) or not option.get(field):
                errors.append(f"{prefix}.{field} must be a non-empty list")
        if not isinstance(option.get("default_parameters"), Mapping):
            errors.append(f"{prefix}.default_parameters must be an object grouped by parameter family")
    return errors


def generate_mandate_plan_options(
    raw_request: str,
    *,
    llm_client: Callable[..., Any] | None = None,
    require_llm: bool = True,
    mode: str = "joint_cardinality",
    available_solvers: Sequence[str] | None = None,
    context: Mapping[str, Any] | None = None,
    knowledge_base: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask the configured LLM/router for 1-3 selectable optimizer equation plans.

    This is the planning layer before ``compile_mandate``.  It deliberately has
    no local fallback and never returns weights.  A selected option's
    ``mandate_request`` can be passed to ``compile_mandate`` for strict schema,
    feasibility and solver-capability validation.
    """

    kb = knowledge_base or load_knowledge_base()
    retrieval = retrieve_constraint_knowledge(raw_request, knowledge_base=kb)
    solver_policy = build_solver_policy(mode, available_solvers)
    if not isinstance(raw_request, str) or not raw_request.strip():
        return _blocked_plan_result(
            BLOCKED_SCHEMA,
            errors=["raw_request must be a non-empty string"],
            attempts=0,
            retrieval=retrieval,
            solver_policy=solver_policy,
        )
    if mode not in ALLOWED_MODES:
        return _blocked_plan_result(
            BLOCKED_SCHEMA,
            errors=[f"mode must be one of {sorted(ALLOWED_MODES)}"],
            attempts=0,
            retrieval=retrieval,
            solver_policy=solver_policy,
        )
    if not require_llm:
        return _blocked_plan_result(
            BLOCKED_LLM,
            errors=["LLM plan generation was disabled; local template fallback is prohibited"],
            attempts=0,
            retrieval=retrieval,
            solver_policy=solver_policy,
        )
    if llm_client is None and not (os.getenv("AI_ROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")):
        return _blocked_plan_result(
            BLOCKED_LLM,
            errors=["AI_ROUTER_API_KEY or OPENAI_API_KEY is required; no local fallback was used"],
            attempts=0,
            retrieval=retrieval,
            solver_policy=solver_policy,
        )

    attempts = 1
    try:
        response = _invoke_llm(
            llm_client,
            _plan_system_prompt(),
            _plan_generation_payload(raw_request, mode, retrieval, solver_policy, context),
        )
    except Exception as exc:
        return _blocked_plan_result(
            BLOCKED_LLM,
            errors=[str(exc)],
            attempts=attempts,
            retrieval=retrieval,
            solver_policy=solver_policy,
        )
    parsed, parse_error, _raw = _strict_json_object(response)
    if parse_error or parsed is None:
        return _blocked_plan_result(
            BLOCKED_LLM,
            errors=[parse_error or "LLM did not return a strict JSON object"],
            attempts=attempts,
            retrieval=retrieval,
            solver_policy=solver_policy,
        )
    normalized = _normalize_plan_payload(parsed, raw_request)
    errors = _validate_plan_payload(normalized)
    if errors:
        return _blocked_plan_result(
            BLOCKED_SCHEMA,
            errors=errors,
            attempts=attempts,
            retrieval=retrieval,
            solver_policy=solver_policy,
        )
    return {
        "status": AWAITING_PLAN_SELECTION,
        "schema_version": PLAN_SCHEMA_VERSION,
        "options": normalized["options"],
        "errors": [],
        "attempts": attempts,
        "llm_required": True,
        "fallback_used": False,
        "weights_emitted": False,
        "retrieval": {
            "knowledge_schema_version": retrieval.get("knowledge_schema_version"),
            "source_ids": list(retrieval.get("source_ids", [])),
        },
        "solver_policy": solver_policy,
    }


def _context_value(context: Mapping[str, Any] | None, *path: str, default: Any = None) -> Any:
    current: Any = context or {}
    for key in path:
        if not isinstance(current, Mapping):
            return default
        current = current.get(key)
    return default if current is None else current


def _float_context(context: Mapping[str, Any] | None, paths: Sequence[Sequence[str]], default: float) -> float:
    for path in paths:
        value = _context_value(context, *path, default=None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return float(default)


def _int_context(context: Mapping[str, Any] | None, paths: Sequence[Sequence[str]], default: int) -> int:
    for path in paths:
        value = _context_value(context, *path, default=None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
    return int(default)


def _str_context(context: Mapping[str, Any] | None, paths: Sequence[Sequence[str]], default: str) -> str:
    for path in paths:
        value = _context_value(context, *path, default=None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default




def _selected_plan_updates(selected_plan: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("parameter_updates", "parameter_adjustments", "params"):
        value = selected_plan.get(key)
        if isinstance(value, Mapping):
            return copy.deepcopy(dict(value))
    defaults = selected_plan.get("default_parameters")
    if isinstance(defaults, Mapping):
        nested = defaults.get("llm_parameter_updates")
        if isinstance(nested, Mapping):
            return copy.deepcopy(dict(nested))
    return {}


def _update_float(updates: Mapping[str, Any], names: Sequence[str], current: float) -> float:
    for name in names:
        if name not in updates:
            continue
        value = updates.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            number = float(value)
            if math.isfinite(number):
                return number
    return float(current)


def _update_int(updates: Mapping[str, Any], names: Sequence[str], current: int) -> int:
    for name in names:
        if name not in updates:
            continue
        value = updates.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            return int(value)
        if isinstance(value, float) and value.is_integer():
            return int(value)
    return int(current)


def _update_code_list(updates: Mapping[str, Any], names: Sequence[str]) -> list[str]:
    for name in names:
        if name not in updates:
            continue
        value = updates.get(name)
        raw_items: list[Any]
        if isinstance(value, str):
            raw_items = re.split(r"[,;，；\s]+", value.strip())
        elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, Mapping)):
            raw_items = list(value)
        else:
            raw_items = []
        codes: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            text = str(item).strip().upper()
            if not text:
                continue
            if re.fullmatch(r"\d{6}", text):
                text = text + (".SH" if text.startswith("6") else ".SZ")
            if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", text) and text not in seen:
                seen.add(text)
                codes.append(text)
        return codes
    return []

def _selected_plan_constraint(
    raw_request: str,
    evidence_source: str,
    constraint_id: str,
    constraint_type: str,
    metric: str,
    lower: int | float | None,
    upper: int | float | None,
    unit: str,
    formula: str,
    dependencies: Sequence[str],
    *,
    scope_extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    scope = {"metric": metric, "universe": "CSI500"}
    scope.update(dict(scope_extra or {}))
    evidence: list[dict[str, Any]] = []
    for field, value in (("lower", lower), ("upper", upper)):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            source_id = "user_supplied" if _user_request_contains_number(raw_request, float(value)) else evidence_source
            evidence.append(
                {
                    "source_id": source_id,
                    "field": field,
                    "value": value,
                    "claim": f"{constraint_id} {field}={value} comes from the selected plan or optimizer base_config.",
                }
            )
    if not evidence:
        evidence.append({"source_id": evidence_source, "claim": f"{constraint_id} is required by the selected optimizer plan."})
    return {
        "id": constraint_id,
        "type": constraint_type,
        "scope": scope,
        "lower": lower,
        "upper": upper,
        "unit": unit,
        "hard": True,
        "penalty": None,
        "priority": 1,
        "formula": formula,
        "data_dependencies": list(dependencies),
        "evidence": evidence,
    }


def _build_selected_plan_mandate(
    raw_request: str,
    selected_plan: Mapping[str, Any],
    *,
    mode: str,
    context: Mapping[str, Any] | None,
    retrieval: Mapping[str, Any],
) -> dict[str, Any]:
    source_ids = list(retrieval.get("source_ids", []))
    evidence_source = source_ids[0] if source_ids else "repo.portfolio_optimizer.convex_solver.v2_6"
    benchmark = _str_context(context, (("universe", "benchmark"), ("base_config", "universe", "benchmark")), "000905.SH")
    score_source = _str_context(
        context,
        (("universe", "score_source"), ("base_config", "universe", "score_source"), ("objective", "score_artifact_id"), ("base_config", "objective", "score_artifact_id")),
        "factor_laboratory_champion",
    )
    rebalance = _str_context(
        context,
        (("rebalance_frequency",), ("universe", "rebalance_frequency"), ("base_config", "universe", "rebalance_frequency")),
        "monthly",
    )
    risk_model = _str_context(
        context,
        (("risk", "covariance"), ("base_config", "risk", "covariance"), ("objective", "risk_model_id"), ("base_config", "objective", "risk_model_id")),
        "shrunk_factor_covariance",
    )
    updates = _selected_plan_updates(selected_plan)
    target_count = _int_context(context, (("holdings", "target_count"), ("base_config", "holdings", "target_count"), ("universe", "holdings"), ("base_config", "universe", "holdings")), 50)
    target_count = _update_int(updates, ("target_count", "holdings", "cardinality"), target_count)
    min_weight = _float_context(context, (("holdings", "min_weight"), ("base_config", "holdings", "min_weight")), 0.005)
    min_weight = _update_float(updates, ("min_weight", "weight_min", "lower_weight"), min_weight)
    max_weight = _float_context(context, (("holdings", "max_weight"), ("base_config", "holdings", "max_weight")), 0.05)
    max_weight = _update_float(updates, ("max_weight", "weight_max", "upper_weight"), max_weight)
    max_active_weight = _float_context(context, (("holdings", "max_active_weight"), ("base_config", "holdings", "max_active_weight"), ("active_risk", "max_active_weight"), ("base_config", "active_risk", "max_active_weight")), 0.03)
    max_active_weight = _update_float(updates, ("max_active_weight", "active_weight_bound", "active_bound"), max_active_weight)
    industry_bound = _float_context(context, (("industry", "max_active_deviation"), ("base_config", "industry", "max_active_deviation")), 0.02)
    industry_bound = _update_float(updates, ("industry_bound", "industry_deviation", "max_industry_active_deviation", "industry_max_active_deviation"), industry_bound)
    te_limit = _float_context(context, (("risk", "annual_tracking_error_limit"), ("base_config", "risk", "annual_tracking_error_limit"), ("active_risk", "tracking_error_limit"), ("base_config", "active_risk", "tracking_error_limit")), 0.06)
    te_limit = _update_float(updates, ("tracking_error_limit", "te_limit", "annual_tracking_error_limit", "target_tracking_error"), te_limit)
    turnover_limit = _float_context(context, (("trading", "one_way_turnover_limit"), ("base_config", "trading", "one_way_turnover_limit"), ("trading", "turnover_limit"), ("base_config", "trading", "turnover_limit")), 0.60)
    turnover_limit = _update_float(updates, ("turnover_limit", "one_way_turnover_limit", "max_turnover"), turnover_limit)
    adv_limit = _float_context(context, (("trading", "max_adv_participation"), ("base_config", "trading", "max_adv_participation"), ("liquidity", "max_adv_participation"), ("base_config", "liquidity", "max_adv_participation")), 0.05)
    adv_limit = _update_float(updates, ("adv_limit", "max_adv_participation", "liquidity_adv_participation"), adv_limit)
    constraints: list[dict[str, Any]] = [
        _selected_plan_constraint(raw_request, evidence_source, "holding.cardinality.exact", "holding", "cardinality", target_count, target_count, "count", "sum_i z_i = target_count", ["universe_membership", "alpha_scores"]),
        _selected_plan_constraint(raw_request, evidence_source, "holding.security_weight", "holding", "security_weight", min_weight, max_weight, "weight_fraction", "min_weight * z_i <= w_i <= max_weight * z_i", ["universe_membership", "benchmark_weights"]),
        _selected_plan_constraint(raw_request, evidence_source, "holding.active_security_weight", "holding", "active_security_weight", -max_active_weight, max_active_weight, "weight_fraction", "-active_bound <= w_i - b_i <= active_bound", ["benchmark_weights"], scope_extra={"benchmark_relative": True}),
        _selected_plan_constraint(raw_request, evidence_source, "industry.active_exposure.all", "industry", "active_exposure", -industry_bound, industry_bound, "weight_fraction", "-industry_bound <= G_g' * (w-b) <= industry_bound", ["industry_classification_pit", "benchmark_weights"], scope_extra={"group": "all", "benchmark_relative": True}),
    ]
    style_config = _context_value(context, "style", default=None)
    if not isinstance(style_config, Mapping):
        style_config = _context_value(context, "base_config", "style", default={})
    style_bound = _update_float(
        updates,
        ("style_bound", "style_exposure_bound", "max_abs_style_exposure", "max_abs_exposure"),
        float("nan"),
    )
    style_defaults = {"size": 0.10, "value": 0.10, "momentum": 0.10, "liquidity": 0.10}
    if isinstance(style_config, Mapping):
        for style_name in style_defaults:
            if math.isfinite(style_bound):
                bound = style_bound
            else:
                raw = style_config.get(style_name, style_defaults[style_name])
                bound = float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else style_defaults[style_name]
            bound = _update_float(
                updates,
                (f"{style_name}_bound", f"style_{style_name}", style_name),
                bound,
            )
            constraints.append(
                _selected_plan_constraint(raw_request, evidence_source, f"style.active_exposure.{style_name}", "style", "active_exposure", -bound, bound, "zscore", f"-{bound:g} <= F_{style_name}' * (w-b) <= {bound:g}", ["style_exposure_matrix_pit", "benchmark_weights"], scope_extra={"style": style_name, "benchmark_relative": True})
            )
    constraints.extend(
        [
            _selected_plan_constraint(raw_request, evidence_source, "active_risk.tracking_error", "active_risk", "tracking_error", None, te_limit, "annualized_fraction", "sqrt((w-b)' Sigma (w-b) * annualization) <= tracking_error_limit", ["benchmark_weights", "factor_covariance_pit", "specific_risk_pit"], scope_extra={"benchmark_relative": True}),
            _selected_plan_constraint(raw_request, evidence_source, "trading.one_way_turnover", "trading", "one_way_turnover", None, turnover_limit, "turnover_fraction", "0.5 * sum_i |w_i - w_prev_i| <= turnover_limit", ["previous_weights", "tradeability_flags", "transaction_cost_model"], scope_extra={"turnover_convention": "one_way"}),
            _selected_plan_constraint(raw_request, evidence_source, "liquidity.adv_participation", "liquidity", "adv_participation", None, adv_limit, "adv_fraction", "trade_value_i / ADV_i <= max_adv_participation", ["point_in_time_adv", "portfolio_nav", "previous_weights", "prices_pit"], scope_extra={"lookback": "20 trading days", "lag": "1 trading day"}),
        ]
    )
    whitelist = _update_code_list(updates, ("whitelist", "include", "forced_include", "mandatory"))
    blacklist = _update_code_list(updates, ("blacklist", "exclude", "forced_exclude"))
    if whitelist:
        constraints.append(_selected_plan_constraint(raw_request, evidence_source, "list.whitelist", "list", "whitelist", None, None, "binary", "ts_code in whitelist", ["security_master_pit", "tradeability_flags"], scope_extra={"security_set": whitelist}))
    if blacklist:
        constraints.append(_selected_plan_constraint(raw_request, evidence_source, "list.blacklist", "list", "blacklist", None, None, "binary", "ts_code not in blacklist", ["security_master_pit", "tradeability_flags"], scope_extra={"security_set": blacklist}))
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "objective": {
            "type": "benchmark_relative_alpha",
            "benchmark_id": benchmark,
            "score_artifact_id": score_source,
            "rebalance_frequency": rebalance,
            "risk_model_id": risk_model,
        },
        "constraints": constraints,
        "retrieval_source_ids": source_ids[:8] or [evidence_source],
        "assumptions": [
            "All optimizer inputs are point-in-time snapshots available at the rebalance date.",
            f"Selected plan: {selected_plan.get('name') or selected_plan.get('id') or 'selected_plan'}.",
            "The LLM planning layer may propose equations, but the deterministic solver owns feasibility and weights.",
        ],
    }


def compile_selected_plan_mandate(
    raw_request: str,
    *,
    selected_plan: Mapping[str, Any],
    require_llm: bool = True,
    mode: str = "joint_cardinality",
    available_solvers: Sequence[str] | None = None,
    context: Mapping[str, Any] | None = None,
    knowledge_base: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    kb = knowledge_base or load_knowledge_base()
    retrieval = retrieve_constraint_knowledge(raw_request, knowledge_base=kb)
    if not isinstance(selected_plan, Mapping):
        return _blocked_result(BLOCKED_SCHEMA, errors=["selected_plan must be an object"], attempts=0, retrieval=retrieval)
    if mode not in ALLOWED_MODES:
        return _blocked_result(BLOCKED_SCHEMA, errors=[f"mode must be one of {sorted(ALLOWED_MODES)}"], attempts=0, retrieval=retrieval)
    mandate = normalize_mandate_payload(_build_selected_plan_mandate(raw_request, selected_plan, mode=mode, context=context, retrieval=retrieval))
    schema_errors = validate_mandate_schema(mandate, raw_request=raw_request, knowledge_base=kb)
    if mandate.get("mode") != mode:
        schema_errors.append(f"mandate.mode must match requested_mode {mode!r}")
    if schema_errors:
        return _blocked_result(BLOCKED_SCHEMA, errors=schema_errors, attempts=0, retrieval=retrieval, mandate=mandate)
    semantic_errors = validate_mandate_semantics(mandate)
    if semantic_errors:
        return _blocked_result(BLOCKED_SEMANTIC, errors=semantic_errors, attempts=0, retrieval=retrieval, mandate=mandate)
    feasibility = quick_feasibility_precheck(mandate)
    solver_policy = build_solver_policy(mode, available_solvers)
    if feasibility["status"] == INFEASIBLE:
        return _blocked_result(INFEASIBLE, errors=feasibility["errors"], attempts=0, retrieval=retrieval, mandate=mandate, solver_policy=solver_policy, feasibility=feasibility)
    if solver_policy.get("capability_status") == BLOCKED_SOLVER_CAPABILITY:
        return _blocked_result(BLOCKED_SOLVER_CAPABILITY, errors=["joint_cardinality requires SCIPY_HIGHS_MILP linear-support selection plus CLARABEL full-SOCP certification, or a native production MISOCP/MIQCP solver; pre-ranked or heuristic top-K fallback is prohibited"], attempts=0, retrieval=retrieval, mandate=mandate, solver_policy=solver_policy, feasibility=feasibility)
    result = {
        "status": AWAITING_CONFIRMATION,
        "schema_version": SCHEMA_VERSION,
        "mandate": copy.deepcopy(mandate),
        "errors": [],
        "attempts": 0,
        "llm_required": require_llm,
        "fallback_used": False,
        "weights_emitted": False,
        "retrieval": {"knowledge_schema_version": retrieval.get("knowledge_schema_version"), "source_ids": source_ids if (source_ids := list(retrieval.get("source_ids", []))) else []},
        "solver_policy": solver_policy,
        "feasibility": feasibility,
        "draft_hash": None,
        "confirmation": None,
        "selected_plan_id": selected_plan.get("id"),
        "selected_plan_name": selected_plan.get("name"),
        "compiled_from_selected_plan": True,
    }
    result["draft_hash"] = compute_draft_hash(result)
    return result
def compile_mandate(
    raw_request: str,
    *,
    llm_client: Callable[..., Any] | None = None,
    require_llm: bool = True,
    mode: str = "joint_cardinality",
    available_solvers: Sequence[str] | None = None,
    context: Mapping[str, Any] | None = None,
    knowledge_base: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a natural-language constraint request into a confirmed-ready draft.

    There is no semantic fallback.  Missing credentials, invalid JSON and failed
    repairs stop with a blocking status.  The result contains no weights.
    """

    kb = knowledge_base or load_knowledge_base()
    retrieval = retrieve_constraint_knowledge(raw_request, knowledge_base=kb)
    if not isinstance(raw_request, str) or not raw_request.strip():
        return _blocked_result(
            BLOCKED_SCHEMA,
            errors=["raw_request must be a non-empty string"],
            attempts=0,
            retrieval=retrieval,
        )
    if mode not in ALLOWED_MODES:
        return _blocked_result(
            BLOCKED_SCHEMA,
            errors=[f"mode must be one of {sorted(ALLOWED_MODES)}"],
            attempts=0,
            retrieval=retrieval,
        )
    if not require_llm:
        return _blocked_result(
            BLOCKED_LLM,
            errors=["LLM compilation was disabled; local template fallback is prohibited"],
            attempts=0,
            retrieval=retrieval,
        )
    if llm_client is None and not (os.getenv("AI_ROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")):
        return _blocked_result(
            BLOCKED_LLM,
            errors=["AI_ROUTER_API_KEY or OPENAI_API_KEY is required; no local fallback was used"],
            attempts=0,
            retrieval=retrieval,
        )

    base_payload = _generation_payload(raw_request, mode, retrieval, context)
    system_prompt = _system_prompt()
    last_raw = ""
    last_payload: dict[str, Any] | None = None
    last_schema_errors: list[str] = []
    last_semantic_errors: list[str] = []
    parse_errors: list[str] = []
    attempts = 0

    for attempt_index in range(3):
        call_payload = (
            base_payload
            if attempt_index == 0
            else _repair_payload(
                base_payload,
                last_raw,
                [*parse_errors, *last_schema_errors, *last_semantic_errors],
                attempt_index,
            )
        )
        attempts += 1
        try:
            response = _invoke_llm(llm_client, system_prompt, call_payload)
        except Exception as exc:
            # Repair prompts can correct invalid model output, not transport or
            # gateway failures.  Repeating the same failed request wastes quota
            # and obscures the first actionable error.
            return _blocked_result(
                BLOCKED_LLM,
                errors=[str(exc)],
                attempts=attempts,
                retrieval=retrieval,
            )
        parsed, parse_error, last_raw = _strict_json_object(response)
        if parse_error:
            parse_errors = [parse_error]
            last_payload = None
            last_schema_errors = []
            last_semantic_errors = []
            continue
        parse_errors = []
        assert parsed is not None
        normalized = normalize_mandate_payload(parsed)
        last_payload = normalized
        last_schema_errors = validate_mandate_schema(
            normalized,
            raw_request=raw_request,
            knowledge_base=kb,
        )
        if normalized.get("mode") != mode:
            last_schema_errors.append(
                f"mandate.mode must match requested_mode {mode!r}"
            )
        last_semantic_errors = [] if last_schema_errors else validate_mandate_semantics(normalized)
        if not last_schema_errors and not last_semantic_errors:
            break

    if last_payload is None:
        return _blocked_result(
            BLOCKED_LLM,
            errors=parse_errors or ["LLM did not return a strict JSON object after two repair attempts"],
            attempts=attempts,
            retrieval=retrieval,
        )
    if last_schema_errors:
        return _blocked_result(
            BLOCKED_SCHEMA,
            errors=last_schema_errors,
            attempts=attempts,
            retrieval=retrieval,
            mandate=last_payload,
        )
    if last_semantic_errors:
        return _blocked_result(
            BLOCKED_SEMANTIC,
            errors=last_semantic_errors,
            attempts=attempts,
            retrieval=retrieval,
            mandate=last_payload,
        )

    feasibility = quick_feasibility_precheck(last_payload)
    solver_policy = build_solver_policy(mode, available_solvers)
    if feasibility["status"] == INFEASIBLE:
        return _blocked_result(
            INFEASIBLE,
            errors=feasibility["errors"],
            attempts=attempts,
            retrieval=retrieval,
            mandate=last_payload,
            solver_policy=solver_policy,
            feasibility=feasibility,
        )
    if solver_policy.get("capability_status") == BLOCKED_SOLVER_CAPABILITY:
        return _blocked_result(
            BLOCKED_SOLVER_CAPABILITY,
            errors=[
                "joint_cardinality requires SCIPY_HIGHS_MILP linear-support selection plus "
                "CLARABEL full-SOCP certification, or a native production MISOCP/MIQCP solver; "
                "pre-ranked or heuristic top-K fallback is prohibited"
            ],
            attempts=attempts,
            retrieval=retrieval,
            mandate=last_payload,
            solver_policy=solver_policy,
            feasibility=feasibility,
        )

    result = {
        "status": AWAITING_CONFIRMATION,
        "schema_version": SCHEMA_VERSION,
        "mandate": copy.deepcopy(last_payload),
        "errors": [],
        "attempts": attempts,
        "llm_required": True,
        "fallback_used": False,
        "weights_emitted": False,
        "retrieval": {
            "knowledge_schema_version": retrieval.get("knowledge_schema_version"),
            "source_ids": list(retrieval.get("source_ids", [])),
        },
        "solver_policy": solver_policy,
        "feasibility": feasibility,
        "draft_hash": None,
        "confirmation": None,
    }
    result["draft_hash"] = compute_draft_hash(result)
    return result


__all__ = [
    "SCHEMA_VERSION",
    "PLAN_SCHEMA_VERSION",
    "BLOCKED_LLM",
    "BLOCKED_SCHEMA",
    "BLOCKED_SEMANTIC",
    "BLOCKED_SOLVER_CAPABILITY",
    "INFEASIBLE",
    "AWAITING_CONFIRMATION",
    "AWAITING_PLAN_SELECTION",
    "CONFIRMED",
    "MandateCompilerError",
    "load_knowledge_base",
    "retrieve_constraint_knowledge",
    "normalize_constraint",
    "normalize_mandate_payload",
    "validate_mandate_schema",
    "validate_mandate_semantics",
    "quick_feasibility_precheck",
    "build_solver_policy",
    "compute_draft_hash",
    "confirm_mandate",
    "is_confirmation_valid",
    "refresh_after_edit",
    "compile_mandate",
    "generate_mandate_plan_options",
    "compile_selected_plan_mandate",
]
