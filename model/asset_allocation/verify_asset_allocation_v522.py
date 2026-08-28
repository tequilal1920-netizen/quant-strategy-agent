"""Fail-closed verification for a built v5.2.2 asset-allocation snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import ntpath
import re
from pathlib import Path
from typing import Any

import asset_allocation_v522 as engine


ASSETS = ("equity", "bond", "gold", "commodity")
EXPECTED_BENCHMARK = {
    "equity": 0.60,
    "bond": 0.15,
    "gold": 0.10,
    "commodity": 0.15,
}
EXPECTED_CYCLES_V522 = {
    "kondratieff",
    "juglar",
    "kitchin",
    "merrill",
    "pring",
}
CANONICAL_STATISTICAL_WARNINGS_V522 = {
    "asset_registry_d3",
    "macro_pit_coverage",
    "probabilistic_sharpe_validation",
    "future_pristine_paper_holdout",
    "validation_excess_positive",
    "validation_information_ratio_positive",
    "cycle_factor_completeness",
    "cycle_input_d3",
}


def _hash(payload: dict[str, Any]) -> str:
    candidate = dict(payload)
    candidate.pop("model_hash", None)
    canonical = json.dumps(
        candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _weights_close_v522(
    actual: dict[str, Any], expected: dict[str, float], tolerance: float = 1.0e-10
) -> bool:
    return (
        set(actual) == set(expected)
        and all(
            abs(float(actual[asset]) - weight) <= tolerance
            for asset, weight in expected.items()
        )
    )


def _public_path_violations_v522(payload: Any) -> list[str]:
    violations: list[str] = []
    pattern = re.compile(
        r"(?i)(?:^|[\s\"'=])(?:[a-z]:[\\/]|\\\\[^\\/\s]+[\\/])"
    )

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
        elif isinstance(value, str):
            if ntpath.isabs(value) or pattern.search(value):
                violations.append(path)
            if "???" in value:
                violations.append(f"{path}:question_mark_corruption")

    walk(payload, "$")
    return violations


def _statistical_evidence_errors_v522(
    evidence: dict[str, Any], canonical_release: bool
) -> list[str]:
    errors: list[str] = []
    checks = evidence.get("checks") or {}
    if not isinstance(checks, dict) or not checks:
        errors.append("statistical_checks_missing")
        checks = {}
    if any(not isinstance(passed, bool) for passed in checks.values()):
        errors.append("statistical_check_type")
    computed_failed = [
        name for name, passed in checks.items() if passed is not True
    ]
    reported_failed = list(evidence.get("failed") or [])
    if reported_failed != computed_failed:
        errors.append("statistical_failed_check_mismatch")
    expected_status = "warning" if computed_failed else "passed"
    if evidence.get("status") != expected_status:
        errors.append("statistical_status_mismatch")
    if canonical_release:
        if set(reported_failed) != CANONICAL_STATISTICAL_WARNINGS_V522:
            errors.append("canonical_statistical_warning_evidence")
    if evidence.get("effect_on_user_authorized_deployment") != "warning_only":
        errors.append("statistical_warning_effect")
    return errors


def _cycle_availability_errors_v522(
    availability_contract: dict[str, Any],
    current_cycles: dict[str, Any],
    cycle_models: dict[str, Any],
    canonical_release: bool,
) -> list[str]:
    """Cross-check cycle admission, evidence and contribution contracts."""

    errors: list[str] = []

    def fail(name: str) -> None:
        if name not in errors:
            errors.append(name)

    availability_cycles = availability_contract.get("cycles") or {}
    if set(availability_cycles) != EXPECTED_CYCLES_V522:
        fail("cycle_availability_set")
    if set(current_cycles) != EXPECTED_CYCLES_V522:
        fail("current_cycle_set")
    if set(cycle_models) != EXPECTED_CYCLES_V522:
        fail("cycle_model_set")

    admitted_list = list(availability_contract.get("admitted_cycles") or [])
    production_list = list(
        availability_contract.get("production_admitted_cycles") or []
    )
    if len(admitted_list) != len(set(admitted_list)):
        fail("cycle_admitted_duplicates")
    if len(production_list) != len(set(production_list)):
        fail("cycle_production_admitted_duplicates")
    admitted_cycles = set(admitted_list)
    production_cycles = set(production_list)
    eligible_cycles = {
        cycle
        for cycle, available in availability_cycles.items()
        if available.get("eligible_for_views") is True
    }
    production_eligible_cycles = {
        cycle
        for cycle, available in availability_cycles.items()
        if available.get("eligible_for_production_views") is True
    }
    if admitted_cycles != eligible_cycles:
        fail("cycle_admitted_set_mismatch")
    if production_cycles != production_eligible_cycles:
        fail("cycle_production_admitted_set_mismatch")
    if not production_cycles.issubset(admitted_cycles):
        fail("cycle_production_without_admission")

    for cycle in EXPECTED_CYCLES_V522:
        available = availability_cycles.get(cycle) or {}
        current = current_cycles.get(cycle) or {}
        model = cycle_models.get(cycle) or {}
        effects = model.get("effects") or {}
        inputs = model.get("inputs") or {}
        output_current = (
            ((model.get("outputs") or {}).get("current_state_payload")) or {}
        )

        for field in (
            "eligible_for_views",
            "eligible_for_shadow_views",
            "eligible_for_production_views",
        ):
            if not isinstance(available.get(field), bool):
                fail(f"cycle_availability_flag_type:{cycle}:{field}")
        if not isinstance(current.get("eligible_for_views"), bool):
            fail(f"current_cycle_flag_type:{cycle}:eligible_for_views")
        for field in (
            "eligible_for_shadow_views",
            "eligible_for_production_views",
        ):
            if field in current and not isinstance(current.get(field), bool):
                fail(f"current_cycle_flag_type:{cycle}:{field}")

        current_eligible = current.get("eligible_for_views") is True
        current_production = (
            current.get("eligible_for_production_views", False) is True
        )
        current_shadow = (
            current.get("eligible_for_shadow_views", current_eligible) is True
        )
        current_scope = current.get("view_scope") or (
            "production"
            if current_production
            else "shadow_only"
            if current_eligible
            else "not_admitted"
        )
        available_eligible = available.get("eligible_for_views") is True
        available_shadow = (
            available.get("eligible_for_shadow_views") is True
        )
        available_production = (
            available.get("eligible_for_production_views") is True
        )
        expected_scope = (
            "production"
            if available_production
            else "shadow_only"
            if available_eligible
            else "not_admitted"
        )

        if (
            available.get("data_status") != current.get("data_status")
            or available_eligible != current_eligible
            or available_shadow != current_shadow
            or available_production != current_production
            or available.get("view_scope") != current_scope
        ):
            fail(f"cycle_current_contract_mismatch:{cycle}")
        if (
            available.get("view_scope") != expected_scope
            or available_shadow != available_eligible
            or (available_production and not available_eligible)
        ):
            fail(f"cycle_view_scope_contract:{cycle}")

        if not isinstance(effects.get("eligible_for_views"), bool):
            fail(f"cycle_effect_flag_type:{cycle}:eligible_for_views")
        if not isinstance(effects.get("eligible_for_production_views"), bool):
            fail(
                f"cycle_effect_flag_type:{cycle}:eligible_for_production_views"
            )
        if (
            effects.get("eligible_for_views") is not available_eligible
            or effects.get("eligible_for_production_views")
            is not available_production
        ):
            fail(f"cycle_catalog_effect_mismatch:{cycle}")
        expected_status = (
            "display_only"
            if cycle == "kondratieff"
            else "admitted"
            if available_production
            else "admitted_shadow_only"
            if available_eligible
            else "not_admitted"
        )
        if model.get("status") != expected_status:
            fail(f"cycle_catalog_status_mismatch:{cycle}")
        if inputs.get("data_status") != available.get("data_status"):
            fail(f"cycle_catalog_data_status_mismatch:{cycle}")

        output_eligible = output_current.get("eligible_for_views") is True
        output_production = (
            output_current.get("eligible_for_production_views", False) is True
        )
        output_shadow = (
            output_current.get("eligible_for_shadow_views", output_eligible)
            is True
        )
        output_scope = output_current.get("view_scope") or (
            "production"
            if output_production
            else "shadow_only"
            if output_eligible
            else "not_admitted"
        )
        if (
            output_current.get("data_status") != current.get("data_status")
            or output_eligible != current_eligible
            or output_shadow != current_shadow
            or output_production != current_production
            or output_scope != current_scope
        ):
            fail(f"cycle_catalog_current_payload_mismatch:{cycle}")

        raw_contribution = effects.get("current_bl_view_contribution")
        try:
            contribution = [float(value) for value in raw_contribution]
        except (TypeError, ValueError):
            contribution = []
        contribution_valid = len(contribution) == 3 and all(
            math.isfinite(value) for value in contribution
        )
        if not contribution_valid:
            fail(f"cycle_contribution_shape:{cycle}")
        contribution_is_zero = contribution_valid and all(
            abs(value) <= 1.0e-15 for value in contribution
        )
        if not isinstance(effects.get("current_contribution_is_zero"), bool):
            fail(f"cycle_contribution_zero_flag_type:{cycle}")
        elif effects.get("current_contribution_is_zero") is not contribution_is_zero:
            fail(f"cycle_contribution_zero_flag_mismatch:{cycle}")
        if not available_eligible and not contribution_is_zero:
            fail(f"nonadmitted_cycle_nonzero_contribution:{cycle}")

        source_verification = inputs.get("authoritative_source_verification")
        verified_ids = inputs.get("verified_vendor_series_ids")
        valid_verified_ids = (
            isinstance(verified_ids, list)
            and bool(verified_ids)
            and all(
                isinstance(series_id, str) and bool(series_id.strip())
                for series_id in verified_ids
            )
            and len(verified_ids) == len(set(verified_ids))
        )
        if source_verification == "not_verified":
            if verified_ids != []:
                fail(f"unverified_vendor_ids_present:{cycle}")
        elif source_verification == "verified":
            if not valid_verified_ids:
                fail(f"verified_vendor_ids_missing:{cycle}")
        else:
            fail(f"cycle_source_verification_status:{cycle}")
        if available_production and (
            source_verification != "verified" or not valid_verified_ids
        ):
            fail(f"production_cycle_source_not_verified:{cycle}")

        if cycle == "kondratieff" and (
            available_eligible
            or available_production
            or model.get("status") != "display_only"
            or not contribution_is_zero
        ):
            fail("kondratieff_display_only_zero_contribution")

    if canonical_release:
        if admitted_cycles != {"pring"}:
            fail("canonical_cycle_admitted_set")
        if production_cycles:
            fail("canonical_cycle_production_admitted_set")
        pring_available = availability_cycles.get("pring") or {}
        pring_current = current_cycles.get("pring") or {}
        pring_model = cycle_models.get("pring") or {}
        pring_effects = pring_model.get("effects") or {}
        if (
            pring_available.get("data_status")
            != "D2_execution_proxy_shadow_only_not_D3"
            or pring_current.get("data_status")
            != "D2_execution_proxy_shadow_only_not_D3"
            or pring_available.get("eligible_for_views") is not True
            or pring_available.get("eligible_for_shadow_views") is not True
            or pring_available.get("eligible_for_production_views") is not False
            or pring_available.get("view_scope") != "shadow_only"
            or pring_model.get("status") != "admitted_shadow_only"
            or pring_effects.get("eligible_for_views") is not True
            or pring_effects.get("eligible_for_production_views") is not False
            or pring_effects.get("current_contribution_is_zero") is not False
        ):
            fail("canonical_pring_d2_shadow_contract")
        for cycle, model in cycle_models.items():
            inputs = model.get("inputs") or {}
            if (
                inputs.get("authoritative_source_verification")
                != "not_verified"
                or inputs.get("verified_vendor_series_ids") != []
            ):
                fail(f"canonical_vendor_source_unverified:{cycle}")

    return errors


def verify(payload: dict[str, Any]) -> dict[str, Any]:
    """Verify the authorized v5.2.2 contract without hiding statistical gaps."""

    errors: list[str] = []

    def fail(name: str) -> None:
        if name not in errors:
            errors.append(name)

    if payload.get("schema_version") != engine.SCHEMA_VERSION_V522:
        fail("schema_version")
    if payload.get("engine_version") != engine.ENGINE_VERSION_V522:
        fail("engine_version")
    if payload.get("status") != "ready":
        fail("status")
    if payload.get("asset_order") != list(ASSETS):
        fail("asset_order")
    if payload.get("model_hash") != _hash(payload):
        fail("model_hash")

    public_paths = _public_path_violations_v522(payload)
    if public_paths:
        fail("public_local_absolute_path")
    sanitization = payload.get("public_snapshot_sanitization") or {}
    if (
        sanitization.get("status") != "passed"
        or sanitization.get("local_absolute_path_count") != 0
    ):
        fail("public_snapshot_sanitization")

    quality = payload.get("quality") or {}
    service = quality.get("service_contract_gate") or {}
    if quality.get("status") != "passed":
        fail("quality_status")
    if service.get("status") != "passed" or service.get("failed"):
        fail("service_contract_gate")

    decision = payload.get("deployment_decision") or {}
    expected_decision = {
        "status": "user_approved_sharpe_mandate",
        "deployable_dynamic_model": True,
        "executed_mode": "benchmark_relative",
        "authorization_basis": engine.AUTHORIZATION_BASIS_V522,
    }
    for key, value in expected_decision.items():
        if decision.get(key) != value:
            fail(f"deployment_decision:{key}")

    if not _weights_close_v522(
        (payload.get("benchmark") or {}).get("weights") or {},
        EXPECTED_BENCHMARK,
    ):
        fail("strategic_benchmark")
    config_anchor = (
        (payload.get("config") or {}).get("policy_benchmark_weights") or []
    )
    expected_internal_anchor = [0.60, 0.15, 0.10, 0.15]
    if len(config_anchor) != 4 or any(
        abs(float(actual) - expected) > 1.0e-10
        for actual, expected in zip(config_anchor, expected_internal_anchor)
    ):
        fail("config_strategic_anchor")

    allocations = payload.get("allocations") or {}
    for mode in (
        "strategic_benchmark",
        "benchmark_relative",
        "absolute_no_benchmark",
        "recommended",
    ):
        weights = (allocations.get(mode) or {}).get("weights") or {}
        if (
            set(weights) != set(ASSETS)
            or abs(sum(float(value) for value in weights.values()) - 1.0)
            > 1.0e-8
        ):
            fail(f"weights:{mode}")
    if allocations.get("recommended_mode") != "benchmark_relative":
        fail("recommended_mode")
    relative_weights = (
        (allocations.get("benchmark_relative") or {}).get("weights") or {}
    )
    recommended_weights = (
        (allocations.get("recommended") or {}).get("weights") or {}
    )
    if not _weights_close_v522(
        recommended_weights,
        {asset: float(value) for asset, value in relative_weights.items()},
    ):
        fail("recommended_weights_do_not_match_dynamic_relative")
    canonical_release = engine.canonical_release_applicable_v522(payload)
    if canonical_release:
        for mode, weights in (
            ("benchmark_relative", relative_weights),
            ("recommended", recommended_weights),
        ):
            if not _weights_close_v522(
                weights, engine.APPROVED_RELATIVE_WEIGHTS_V522
            ):
                fail(f"approved_weights:{mode}")
    try:
        freeze = engine.assert_approved_relative_snapshot_v522(payload)
    except AssertionError:
        freeze = payload.get("approved_weight_freeze") or {}
        fail("approved_weight_freeze")
    stored_freeze = payload.get("approved_weight_freeze") or {}
    if stored_freeze.get("status") != "passed":
        fail("approved_weight_freeze_audit")
    if stored_freeze.get("applicable") is not canonical_release:
        fail("approved_weight_freeze_applicability")

    backtest = payload.get("backtest") or {}
    strategies = backtest.get("strategies") or {}
    selection = backtest.get("selection_audit") or {}
    relative_selection = selection.get("benchmark_relative") or {}
    if selection.get("selection_uses_test") is not False:
        fail("selection_uses_test")
    if relative_selection.get("selection_uses_test") is not False:
        fail("relative_selection_uses_test")
    if not relative_selection.get("selected_id"):
        fail("missing_selected_id")
    if canonical_release and relative_selection.get("selected_id") != engine.APPROVED_RELATIVE_MODEL_ID_V522:
        fail("approved_selected_id")
    if int(relative_selection.get("eligible_count") or 0) <= 0:
        fail("relative_validation_eligibility")
    for row in relative_selection.get("leaderboard") or []:
        if row.get("score_objective") != "validation_standard_sharpe":
            fail("relative_selection_objective")

    equal_full = strategies.get(engine.EQUAL_WEIGHT_DISPLAY_ID_V522) or {}
    equal_compact = (
        (backtest.get("display_benchmarks") or {}).get(
            engine.EQUAL_WEIGHT_DISPLAY_ID_V522
        )
        or {}
    )
    if (
        equal_full.get("id") != engine.EQUAL_WEIGHT_DISPLAY_ID_V522
        or equal_full.get("role") != "nav_display_only_not_optimizer_input"
        or equal_full.get("optimizer_input") is not False
        or equal_full.get("active_return_reference") is not False
        or any(
            abs(float(value) - 0.25) > 1.0e-12
            for value in (equal_full.get("current_weights") or [])
        )
        or len(equal_full.get("current_weights") or []) != 4
    ):
        fail("equal_weight_full_strategy")
    if (
        equal_compact.get("id") != engine.EQUAL_WEIGHT_DISPLAY_ID_V522
        or equal_compact.get("strategy_key") != engine.EQUAL_WEIGHT_DISPLAY_ID_V522
        or equal_compact.get("role") != "nav_display_only_not_optimizer_input"
        or equal_compact.get("optimizer_input") is not False
        or equal_compact.get("active_return_reference") is not False
        or not _weights_close_v522(
            equal_compact.get("weights") or {},
            {asset: 0.25 for asset in ASSETS},
            1.0e-12,
        )
    ):
        fail("equal_weight_compact_contract")
    comparison = backtest.get("comparison_policy") or {}
    if (
        comparison.get("primary_benchmark") != "strategic_60_15_15_10"
        or comparison.get("active_return_reference") != "strategic_60_15_15_10"
        or comparison.get("optimizer_policy_anchor") != "strategic_60_15_15_10"
        or comparison.get("nav_display_reference")
        != engine.EQUAL_WEIGHT_DISPLAY_ID_V522
    ):
        fail("benchmark_role_separation")

    cost_rows = 0
    max_cost_identity_error = 0.0
    max_net_return_identity_error = 0.0
    for mode in (
        "strategic_benchmark",
        "benchmark_relative",
        "absolute_no_benchmark",
        engine.EQUAL_WEIGHT_DISPLAY_ID_V522,
    ):
        rows = (strategies.get(mode) or {}).get("returns") or []
        if not rows:
            fail(f"empty_cost_history:{mode}")
        for row in rows:
            cost_rows += 1
            linear = float(row.get("linear_cost") or 0.0)
            quadratic = float(row.get("quadratic_cost") or 0.0)
            cost = float(row.get("cost") or 0.0)
            gross = float(row.get("gross_return") or 0.0)
            net = float(row.get("net_return") or 0.0)
            max_cost_identity_error = max(
                max_cost_identity_error,
                abs(cost - linear - quadratic),
            )
            max_net_return_identity_error = max(
                max_net_return_identity_error,
                abs(net - gross + linear + quadratic),
            )
            if linear < -1.0e-15 or quadratic < -1.0e-15:
                fail(f"negative_cost:{mode}:{row.get('month')}")
    if max_cost_identity_error > 1.0e-12:
        fail("cost_identity")
    if max_net_return_identity_error > 1.0e-12:
        fail("net_return_cost_identity")

    config = payload.get("config") or {}
    constraint_summary: dict[str, Any] = {}
    for mode in ("benchmark_relative", "absolute_no_benchmark"):
        history = (strategies.get(mode) or {}).get("constraint_history") or []
        if not history:
            fail(f"empty_constraint_history:{mode}")
        maximum = max(
            (float(row.get("max_violation") or 0.0) for row in history),
            default=0.0,
        )
        constraint_summary[mode] = {
            "months": len(history),
            "max_violation": maximum,
        }
        if maximum > 1.0e-7:
            fail(f"constraint_violation:{mode}")
        if mode == "benchmark_relative":
            max_active = max(
                (float(row.get("active_share") or 0.0) for row in history),
                default=0.0,
            )
            max_te = max(
                (
                    float(row.get("annual_tracking_error") or 0.0)
                    for row in history
                ),
                default=0.0,
            )
            max_turnover = max(
                (float(row.get("turnover") or 0.0) for row in history),
                default=0.0,
            )
            if max_active > float(config.get("policy_max_active_share") or 0.0) + 1.0e-8:
                fail("active_share_history")
            if max_te > float(config.get("policy_max_annual_tracking_error") or 0.0) + 1.0e-8:
                fail("tracking_error_history")
            if max_turnover > float(config.get("policy_max_one_way_turnover") or 0.0) + 1.0e-8:
                fail("policy_turnover_history")

    promotions = quality.get("promotion_by_version") or {}
    relative_gate = promotions.get("benchmark_relative") or {}
    if (
        relative_gate.get("status") != "passed"
        or relative_gate.get("authorization_basis")
        != engine.AUTHORIZATION_BASIS_V522
        or relative_gate.get("retrospective_test_enters_checks") is not False
        or relative_gate.get("excess_return_required_for_authorization") is not False
        or relative_gate.get("information_ratio_required_for_authorization") is not False
    ):
        fail("relative_authorization_gate")
    if {
        "validation_excess_positive",
        "validation_information_ratio_positive",
        "future_pristine_paper_holdout",
        "asset_registry_d3",
        "macro_pit_coverage",
        "probabilistic_sharpe_validation",
    } & set((relative_gate.get("checks") or {})):
        fail("statistical_checks_leaked_into_authorization")
    if (promotions.get("absolute_no_benchmark") or {}).get("status") != "research_reference":
        fail("absolute_reference_status")

    statistical = quality.get("statistical_evidence_by_version") or {}
    relative_statistical = statistical.get("benchmark_relative") or {}
    for error in _statistical_evidence_errors_v522(
        relative_statistical, canonical_release
    ):
        fail(error)

    performance_claim = payload.get("performance_claim") or {}
    if (
        performance_claim.get("authorized_objective")
        != "policy_relative_sharpe_improvement"
        or performance_claim.get("positive_excess_required_for_authorization")
        is not False
        or performance_claim.get(
            "positive_information_ratio_required_for_authorization"
        )
        is not False
        or performance_claim.get("retrospective_test_is_report_only") is not True
    ):
        fail("performance_claim")
    for sample in ("train", "validation"):
        if (
            float(
                (
                    (performance_claim.get("sharpe_evidence") or {}).get(sample)
                    or {}
                ).get("improvement")
                or 0.0
            )
            <= 0.0
        ):
            fail(f"sharpe_improvement:{sample}")

    catalog = payload.get("model_evidence_catalog") or {}
    audit = catalog.get("completeness_audit") or {}
    cycle_models = catalog.get("cycle_models") or {}
    allocation_models = catalog.get("allocation_models") or {}
    expected_cycles = {"kondratieff", "juglar", "kitchin", "merrill", "pring"}
    expected_allocations = {
        "black_litterman",
        "risk_parity",
        "risk_budget",
        "macro_factor_model",
    }
    required_model_fields = {
        "id",
        "name_cn",
        "status",
        "inputs",
        "steps",
        "constraints",
        "outputs",
        "effects",
        "authoritative_references",
    }
    reference_required_fields = {
        "institution",
        "title",
        "date",
        "url",
        "scope",
        "verification_status",
    }
    reference_optional_fields = {
        "matched_section",
        "report_date",
        "cataloged_at",
    }
    reference_allowed_fields = reference_required_fields | reference_optional_fields
    if (
        audit.get("status") != "passed"
        or set(cycle_models) != expected_cycles
        or set(allocation_models) != expected_allocations
    ):
        fail("model_evidence_catalog")
    for family_name, family in (
        ("cycle", cycle_models),
        ("allocation", allocation_models),
    ):
        for model_id, model in family.items():
            if set(model) != required_model_fields:
                fail(f"catalog_fields:{family_name}:{model_id}")
            references = model.get("authoritative_references") or []
            if not 2 <= len(references) <= 5:
                fail(f"catalog_reference_count:{family_name}:{model_id}")
            for index, reference in enumerate(references):
                fields = set(reference)
                report_date = str(reference.get("report_date") or "")
                cataloged_at = str(reference.get("cataloged_at") or "")
                matched_section = str(reference.get("matched_section") or "")
                if (
                    not reference_required_fields.issubset(fields)
                    or not fields.issubset(reference_allowed_fields)
                    or reference.get("scope")
                    not in {"exact_method", "cross_cycle_framework"}
                    or reference.get("verification_status") != "inspected"
                    or not str(reference.get("url") or "").startswith("https://")
                    or (
                        "report_date" in fields
                        and report_date != str(reference.get("date") or "")
                    )
                    or (
                        "cataloged_at" in fields
                        and (
                            len(cataloged_at) != 10
                            or cataloged_at < str(reference.get("date") or "")
                        )
                    )
                    or (
                        "matched_section" in fields
                        and (
                            not matched_section
                            or matched_section == str(reference.get("title") or "")
                        )
                    )
                ):
                    fail(f"catalog_reference:{family_name}:{model_id}:{index}")
                url = str(reference.get("url") or "")
                if (
                    url.endswith("H3_AP202601121816952139_1.pdf")
                    and (
                        reference.get("title")
                        != "AI赋能资产配置（三十四）：首发，AI+多资产泛量化系列指数"
                        or matched_section
                        != "AI视角驱动的Black-Litterman资产配置"
                    )
                ):
                    fail(f"catalog_reference_identity:{family_name}:{model_id}:{index}")
                if (
                    url.endswith("738530789217.pdf")
                    and reference.get("institution") != "国泰君安证券"
                ):
                    fail(f"catalog_reference_issuer:{family_name}:{model_id}:{index}")
                if (
                    url.endswith("815736789040.pdf")
                    and (
                        reference.get("date") != "2025-11-05"
                        or report_date != "2025-11-05"
                        or cataloged_at != "2025-11-06"
                    )
                ):
                    fail(f"catalog_reference_date:{family_name}:{model_id}:{index}")
    cycle_availability = payload.get("cycle_factor_availability") or {}
    current_cycles = (
        ((allocations.get("current_cycle") or {}).get("cycles")) or {}
    )
    for cycle, model in cycle_models.items():
        inputs = model.get("inputs") or {}
        if not {
            "current_source",
            "observed_field",
            "authoritative_source_verification",
        }.issubset(inputs):
            fail(f"cycle_source_contract:{cycle}")
    for error in _cycle_availability_errors_v522(
        cycle_availability,
        current_cycles,
        cycle_models,
        canonical_release,
    ):
        fail(error)

    decisions = payload.get("asset_decisions") or {}
    strength = payload.get("current_strength_summary") or {}
    for mode in ("benchmark_relative", "absolute_no_benchmark"):
        rows = decisions.get(mode) or {}
        if set(rows) != set(ASSETS):
            fail(f"asset_decisions:{mode}")
        for asset, row in rows.items():
            if not row.get("decision_summary_cn") or not row.get("input_signals"):
                fail(f"asset_decision_explanation:{mode}:{asset}")
        summary = strength.get(mode) or {}
        if not summary.get("strongest_assets") or not summary.get("weakest_assets"):
            fail(f"asset_strength_summary:{mode}")

    return {
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "schema_version": payload.get("schema_version"),
        "engine_version": payload.get("engine_version"),
        "model_hash_verified": payload.get("model_hash") == _hash(payload),
        "public_path_violations": public_paths,
        "approved_weight_freeze": freeze,
        "service_contract_status": service.get("status"),
        "statistical_evidence_status": relative_statistical.get("status"),
        "cost_rows_checked": cost_rows,
        "max_cost_identity_error": max_cost_identity_error,
        "max_net_return_identity_error": max_net_return_identity_error,
        "constraint_summary": constraint_summary,
        "selected_model_id": relative_selection.get("selected_id"),
        "selection_uses_test": selection.get("selection_uses_test"),
        "equal_weight_display_strategy_rows": len(equal_full.get("returns") or []),
        "catalog_cycle_models": sorted(cycle_models),
        "catalog_allocation_models": sorted(allocation_models),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    source = Path(args.snapshot).resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    result = verify(payload)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        destination = Path(args.output).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
