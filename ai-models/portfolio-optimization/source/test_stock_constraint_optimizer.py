from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import model.portfolio_optimization.stock_constraint_optimizer as optimizer_module
from model.portfolio_optimization.stock_constraint_optimizer import (
    StockOptimizerConfig,
    build_psd_factor_risk_root,
    optimize_stock_portfolio,
    precheck_stock_problem,
)


def _strict_problem(seed: int = 20260811):
    rng = np.random.default_rng(seed)
    codes = [f"{index:06d}.SZ" for index in range(500)]
    industries = [f"industry_{index % 10:02d}" for index in range(500)]
    score = rng.normal(size=500) + np.linspace(-0.25, 0.25, 500)
    frame = pd.DataFrame(
        {
            "ts_code": codes,
            "alpha_score": score,
            # Tushare index_weight uses percentage units.
            "benchmark_weight": np.full(500, 0.2),
            "industry": industries,
        }
    )
    styles = pd.DataFrame(
        {
            "ts_code": codes,
            "style_size": np.linspace(-1.0, 1.0, 500),
            "style_value": np.sin(np.linspace(0.0, 8.0 * np.pi, 500)),
            "style_momentum": rng.normal(size=500),
        }
    )
    covariance = pd.DataFrame(
        np.eye(500, dtype=float) * 0.04,
        index=codes,
        columns=codes,
    )
    previous = dict(zip(codes, np.full(500, 1.0 / 500.0)))
    config = StockOptimizerConfig(
        target_holdings=50,
        min_weight=0.005,
        max_weight=0.04,
        max_active_weight=0.04,
        industry_deviation=0.015,
        style_bounds={
            "style_size": (-0.12, 0.12),
            "style_value": (-0.12, 0.12),
            "style_momentum": (-0.12, 0.12),
        },
        target_tracking_error=0.08,
        one_way_turnover_limit=1.0,
        transaction_cost_rate=0.001,
        turnover_l1_penalty=0.001,
        turnover_l2_penalty=0.02,
        benchmark_weight_unit="percent",
    )
    return frame, styles, covariance, previous, config


def test_exact_500_to_50_solution_satisfies_every_constraint() -> None:
    frame, styles, covariance, previous, config = _strict_problem()
    result = optimize_stock_portfolio(
        frame,
        style_exposures=styles,
        annual_covariance=covariance,
        previous_weights=previous,
        config=config,
    )

    assert result["status"] == "ready", result
    assert result["tradable"] is True
    assert result["fallback_used"] is False
    assert result["solver"]["name"] == "CLARABEL"
    assert result["solver"]["certified"] is True
    assert result["solver"]["status"] == "optimal"
    weights = pd.Series(result["weights"], dtype=float)
    positive = weights[weights > config.feasibility_tolerance]
    assert len(weights) == 500
    assert len(positive) == 50
    assert weights.sum() == pytest.approx(1.0, abs=config.feasibility_tolerance)
    assert positive.min() >= config.min_weight - config.feasibility_tolerance
    assert positive.max() <= config.max_weight + config.feasibility_tolerance
    assert result["realized"]["holdings_count"] == 50
    assert result["realized"]["candidate_count"] == 50
    assert result["realized"]["full_active_vector_count"] == 500
    assert result["realized"]["tracking_error"] <= (
        config.target_tracking_error + config.feasibility_tolerance
    )
    assert result["realized"]["one_way_turnover"] <= (
        config.one_way_turnover_limit + config.feasibility_tolerance
    )
    assert max(
        abs(value)
        for value in result["realized"]["industry_active_exposure"].values()
    ) <= config.industry_deviation + config.feasibility_tolerance
    for name, exposure in result["realized"]["style_active_exposure"].items():
        lower, upper = config.style_bounds[name]
        assert lower - config.feasibility_tolerance <= exposure
        assert exposure <= upper + config.feasibility_tolerance
    assert result["solver"]["max_constraint_violation"] <= config.feasibility_tolerance
    assert result["baseline"]["used_as_optimizer_fallback"] is False


def test_sell_locked_existing_position_uses_narrow_execution_exception():
    frame, styles, covariance, previous, config = _strict_problem()
    locked = frame.sort_values("ts_code").iloc[0]["ts_code"]
    previous = {code: 0.0 for code in previous}
    previous[locked] = 0.06
    sell_limits = {code: 1.0 for code in previous}
    sell_limits[locked] = 0.0
    config = replace(
        config,
        sell_limit=sell_limits,
        target_tracking_error=0.20,
        industry_deviation=0.10,
        style_bounds={
            "style_size": (-0.50, 0.50),
            "style_value": (-0.50, 0.50),
            "style_momentum": (-0.50, 0.50),
        },
    )

    result = optimize_stock_portfolio(
        frame,
        style_exposures=styles,
        annual_covariance=covariance,
        previous_weights=previous,
        config=config,
    )

    assert result["status"] == "ready", result
    assert result["weights"][locked] == pytest.approx(0.06, abs=2.0e-6)
    exceptions = result["realized"]["execution_exceptions"]
    assert result["realized"]["execution_exception_count"] == 1
    assert exceptions[0]["ts_code"] == locked
    assert exceptions[0]["target_policy"] == "maximum_feasible_sell_no_buy"
    assert exceptions[0]["buy_authority_expanded"] is False
    assert exceptions[0]["other_constraints_relaxed"] is False
    assert result["solver"]["max_constraint_violation"] <= (
        config.feasibility_tolerance
    )


def test_sell_locked_exception_can_be_disabled_and_then_blocks():
    frame, styles, covariance, previous, config = _strict_problem()
    locked = frame.sort_values("ts_code").iloc[0]["ts_code"]
    previous = {code: 0.0 for code in previous}
    previous[locked] = 0.06
    sell_limits = {code: 1.0 for code in previous}
    sell_limits[locked] = 0.0
    config = replace(
        config,
        sell_limit=sell_limits,
        allow_forced_retention_execution_exception=False,
        target_tracking_error=0.20,
        industry_deviation=0.10,
        style_bounds={
            "style_size": (-0.50, 0.50),
            "style_value": (-0.50, 0.50),
            "style_momentum": (-0.50, 0.50),
        },
    )

    result = optimize_stock_portfolio(
        frame,
        style_exposures=styles,
        annual_covariance=covariance,
        previous_weights=previous,
        config=config,
    )

    assert result["status"] == "blocked"
    assert result["tradable"] is False
    assert result["weights"] == {}
    assert result["fallback_used"] is False


def test_security_row_permutation_does_not_change_solution() -> None:
    frame, styles, covariance, previous, config = _strict_problem()
    expected = optimize_stock_portfolio(
        frame,
        style_exposures=styles,
        annual_covariance=covariance,
        previous_weights=previous,
        config=config,
    )
    shuffled = frame.sample(frac=1.0, random_state=91).reset_index(drop=True)
    actual = optimize_stock_portfolio(
        shuffled,
        style_exposures=styles.sample(frac=1.0, random_state=19).reset_index(drop=True),
        annual_covariance=covariance,
        previous_weights=previous,
        config=config,
    )

    assert expected["status"] == actual["status"] == "ready"
    expected_weights = pd.Series(expected["weights"]).sort_index()
    actual_weights = pd.Series(actual["weights"]).sort_index()
    assert np.max(np.abs(expected_weights - actual_weights)) < 1.0e-7
    assert expected["solution_hash"] == actual["solution_hash"]


def test_infeasible_capacity_is_blocked_without_weights_or_trades() -> None:
    frame, styles, covariance, previous, config = _strict_problem()
    infeasible = replace(config, max_weight=0.019)
    result = optimize_stock_portfolio(
        frame,
        style_exposures=styles,
        annual_covariance=covariance,
        previous_weights=previous,
        config=infeasible,
    )

    assert result["status"] == "blocked"
    assert result["tradable"] is False
    assert result["blocked_stage"] == "feasibility_precheck"
    assert result["weights"] == {}
    assert result["transactions"] == []
    assert result["fallback_used"] is False
    assert result["minimum_relaxation"]


@pytest.mark.parametrize(
    ("config", "reason_fragment"),
    [
        ({"target_holdings": 50.0}, "target_holdings_must_be_integer"),
        ({"target_holdings": 50, "min_weight": 0.0}, "strictly_positive"),
        ({"target_holdings": 50, "benchmark_weight_unit": "guess"}, "benchmark_weight_unit"),
        ({"support_search_max_attempts": 1.5}, "support_search_max_attempts_must_be_integer"),
        ({"support_search_max_attempts": 0}, "support_search_max_attempts_must_be_positive"),
        ({"support_search_beam_width": "4"}, "support_search_beam_width_must_be_integer"),
        ({"support_search_beam_width": 0}, "support_search_beam_width_must_be_positive"),
        ({"target_holdings": 50, "unknown_parameter": 1}, "invalid_config_fields"),
    ],
)
def test_invalid_configuration_is_audited_and_blocked(
    config: dict[str, object],
    reason_fragment: str,
) -> None:
    frame, styles, covariance, previous, _ = _strict_problem()
    result = optimize_stock_portfolio(
        frame,
        style_exposures=styles,
        annual_covariance=covariance,
        previous_weights=previous,
        config=config,
    )

    assert result["status"] == "blocked"
    assert result["blocked_stage"] == "input_contract"
    assert reason_fragment in result["reason"]
    assert result["weights"] == {}


def test_unlabelled_style_rows_are_never_positionally_accepted() -> None:
    frame, styles, covariance, previous, config = _strict_problem()
    unlabelled = styles.drop(columns="ts_code").reset_index(drop=True)
    result = precheck_stock_problem(
        frame,
        style_exposures=unlabelled,
        annual_covariance=covariance,
        previous_weights=previous,
        config=config,
    )

    assert result["status"] == "blocked"
    assert "requires_ts_code_or_exact_code_index" in result["reason"]


def test_risk_builder_rejects_unmatured_and_all_missing_history() -> None:
    frame, styles, _, _, _ = _strict_problem()
    base = frame[["ts_code", "industry"]]
    style = styles.set_index("ts_code")
    dates = pd.date_range("2026-01-01", periods=30, freq="D")
    returns = pd.DataFrame(
        np.full((30, 500), 0.001),
        index=dates,
        columns=frame["ts_code"],
    )

    with pytest.raises(ValueError, match="unmatured_or_signal_date"):
        build_psd_factor_risk_root(
            returns,
            base,
            style_exposures=style,
            signal_date="2026-01-20",
        )

    missing = returns.copy()
    missing.iloc[:, :] = np.nan
    with pytest.raises(ValueError, match="row_coverage_below_minimum"):
        build_psd_factor_risk_root(
            missing,
            base,
            style_exposures=style,
            signal_date="2026-02-15",
        )


def test_risk_builder_uses_observed_cross_sections_and_conservative_short_history_prior() -> None:
    frame, styles, _, _, _ = _strict_problem()
    base = frame[["ts_code", "industry"]]
    style = styles.set_index("ts_code")
    dates = pd.date_range("2025-10-01", periods=60, freq="B")
    rng = np.random.default_rng(20260811)
    returns = pd.DataFrame(
        rng.normal(0.0, 0.01, size=(60, 500)),
        index=dates,
        columns=frame["ts_code"],
    )
    short_code = frame["ts_code"].iloc[0]
    returns.loc[dates[:35], short_code] = np.nan

    built = build_psd_factor_risk_root(
        returns,
        base,
        style_exposures=style,
        signal_date="2026-01-01",
        minimum_row_coverage=0.90,
        minimum_asset_coverage=0.75,
        minimum_factor_observations=40,
        minimum_asset_observations=20,
        specific_variance_prior_observations=40.0,
    )

    assert built["risk_root"].shape == (500, 500)
    assert np.isfinite(built["risk_root"]).all()
    audit = built["diagnostics"]
    assert audit["individual_return_imputation_used"] is False
    assert audit["daily_cross_section_observations_min"] == 499
    assert audit["minimum_specific_residual_observations"] == 25
    assert audit["rank_deficient_date_count"] == 0
    short = next(
        item for item in audit["short_history_assets"]
        if item["ts_code"] == short_code
    )
    assert short["observations"] == 25
    assert short["prior_strength"] > 0.0
    assert short["final_specific_variance"] >= short["raw_specific_variance"]

    below_floor = returns.copy()
    below_floor.loc[dates[:41], short_code] = np.nan
    with pytest.raises(
        ValueError,
        match=rf"risk_history_asset_observations_below_minimum:{short_code}:19",
    ):
        build_psd_factor_risk_root(
            below_floor,
            base,
            style_exposures=style,
            signal_date="2026-01-01",
            minimum_row_coverage=0.90,
            minimum_factor_observations=40,
            minimum_asset_observations=20,
        )


def _phase_i_complete_seed_problem():
    count = 52
    codes = [f"S{index:03d}.SZ" for index in range(count)]
    benchmark = np.asarray([94.0 / 50.0] * 50 + [3.0, 3.0], dtype=float)
    frame = pd.DataFrame({
        "ts_code": codes,
        "alpha_score": list(range(count, 2, -1)) + [1.0, 0.0],
        "benchmark_weight": benchmark,
        "industry": ["all"] * count,
    })
    styles = pd.DataFrame({
        "ts_code": codes,
        "style": [1.0] * 50 + [-1.0, -1.0],
    })
    covariance = pd.DataFrame(
        np.eye(count, dtype=float) * 1.0e-4, index=codes, columns=codes
    )
    config = StockOptimizerConfig(
        target_holdings=50,
        min_weight=0.005,
        max_weight=0.04,
        max_active_weight=0.10,
        industry_deviation=1.0,
        style_bounds={"style": (-0.05, 0.05)},
        target_tracking_error=0.20,
        one_way_turnover_limit=1.0,
        benchmark_weight_unit="percent",
        candidate_style_balance_penalty=0.0,
        mandatory=("S000.SZ",),
        support_search_max_attempts=16,
        support_search_beam_width=4,
    )
    return frame, styles, covariance, config


def test_highs_joint_support_preserves_contract_and_is_permutation_invariant() -> None:
    frame, styles, covariance, config = _phase_i_complete_seed_problem()
    precheck = precheck_stock_problem(
        frame,
        style_exposures=styles,
        annual_covariance=covariance,
        previous_weights={},
        config=config,
    )
    assert precheck["status"] == "passed"
    assert "S050.SZ" not in precheck["candidate_codes"]
    assert "S051.SZ" not in precheck["candidate_codes"]

    result = optimize_stock_portfolio(
        frame,
        style_exposures=styles,
        annual_covariance=covariance,
        previous_weights={},
        config=config,
    )
    assert result["status"] == "ready", result
    selection = result["requested"]["selection"]
    search = selection["support_search"]
    assert search["method"] == (
        "alpha_candidate_then_scipy_highs_joint_support_then_clarabel_socp"
    )
    assert search["selected_strategy"] == "highs_milp_joint_linear_support"
    assert search["milp_attempt_limit"] == 16
    assert search["attempt_count"] == 2
    assert search["attempt_count"] <= search["attempt_limit"] == 18
    assert search["attempts"][0]["strategy"] == (
        "initial_alpha_industry_candidate_clarabel_certification"
    )
    assert search["attempts"][0]["status"] == "rejected_by_clarabel_socp"
    attempt = search["attempts"][-1]
    assert attempt["solver"] == "SCIPY_HIGHS_MILP"
    assert attempt["highs_status_code"] == 0
    assert attempt["mip_gap"] == pytest.approx(0.0)
    assert attempt["status"] == "certified"
    assert attempt["clarabel_certification"] == "feasible"
    assert attempt["linear_diagnostics"]["max_constraint_violation"] <= (
        config.feasibility_tolerance
    )
    assert attempt["trial_weights_returned"] is False
    assert attempt["milp_trial_weights_discarded"] is True
    assert search["heuristic_support_fallback_used"] is False
    support = set(selection["candidate_codes"])
    assert len(support) == config.target_holdings
    assert set(selection["required_codes"]).issubset(support)
    assert support.issubset(set(selection["eligible_codes"]))
    assert {"S050.SZ", "S051.SZ"}.issubset(support)
    assert result["solver"]["max_constraint_violation"] <= (
        config.feasibility_tolerance
    )

    shuffled = optimize_stock_portfolio(
        frame.sample(frac=1.0, random_state=811).reset_index(drop=True),
        style_exposures=styles.sample(frac=1.0, random_state=812).reset_index(drop=True),
        annual_covariance=covariance,
        previous_weights={},
        config=config,
    )
    assert shuffled["status"] == "ready"
    shuffled_search = shuffled["requested"]["selection"]["support_search"]
    assert shuffled_search["selected_strategy"] == search["selected_strategy"]
    assert shuffled_search["selected_support_hash"] == search["selected_support_hash"]
    assert [item["support_hash"] for item in shuffled_search["attempts"]] == [
        item["support_hash"] for item in search["attempts"]
    ]


def _phase_i_swap_problem(*, target_tracking_error: float = 0.20):
    """A 52-name universe whose alpha-first 50 names breach the style band."""

    count = 52
    codes = [f"T{index:03d}.SZ" for index in range(count)]
    frame = pd.DataFrame({
        "ts_code": codes,
        "alpha_score": list(range(count, 2, -1)) + [1.0, 0.0],
        "benchmark_weight": np.full(count, 100.0 / count),
        "industry": ["all"] * count,
    })
    styles = pd.DataFrame({
        "ts_code": codes,
        "style": [1.0] * 50 + [-1.0, -1.0],
    })
    covariance = pd.DataFrame(
        np.eye(count, dtype=float) * 1.0e-4, index=codes, columns=codes
    )
    config = StockOptimizerConfig(
        target_holdings=50,
        min_weight=0.005,
        max_weight=0.03,
        max_active_weight=0.10,
        industry_deviation=1.0,
        style_bounds={"style": (-0.04, 0.04)},
        target_tracking_error=target_tracking_error,
        one_way_turnover_limit=1.0,
        benchmark_weight_unit="percent",
        support_search_max_attempts=16,
        support_search_beam_width=4,
    )
    return frame, styles, covariance, config


def test_highs_joint_support_replaces_infeasible_alpha_first_support() -> None:
    frame, styles, covariance, config = _phase_i_swap_problem()
    precheck = precheck_stock_problem(
        frame,
        style_exposures=styles,
        annual_covariance=covariance,
        previous_weights={},
        config=config,
    )
    assert precheck["status"] == "passed"
    assert "T050.SZ" not in precheck["candidate_codes"]

    result = optimize_stock_portfolio(
        frame,
        style_exposures=styles,
        annual_covariance=covariance,
        previous_weights={},
        config=config,
    )

    assert result["status"] == "ready", result
    search = result["requested"]["selection"]["support_search"]
    assert search["phase_i_uses_all_linear_mandate_constraints"] is True
    assert search["phase_i_tracking_error_modeled"] is False
    assert search["tracking_error_certifier"] == "CLARABEL"
    assert search["trial_weights_returned"] is False
    assert search["attempt_count"] == 2
    assert search["attempts"][0]["strategy"] == (
        "initial_alpha_industry_candidate_clarabel_certification"
    )
    assert search["attempts"][0]["status"] == "rejected_by_clarabel_socp"
    assert search["attempts"][-1]["status"] == "certified"
    assert search["selected_strategy"] == "highs_milp_joint_linear_support"
    support = set(result["requested"]["selection"]["candidate_codes"])
    assert {"T050.SZ", "T051.SZ"} & support
    assert result["solver"]["certified"] is True
    assert result["solver"]["max_constraint_violation"] <= (
        config.feasibility_tolerance
    )


def test_highs_no_good_cuts_block_when_no_support_passes_full_te() -> None:
    frame, styles, covariance, config = _phase_i_swap_problem(
        target_tracking_error=0.0
    )
    result = optimize_stock_portfolio(
        frame,
        style_exposures=styles,
        annual_covariance=covariance,
        previous_weights={},
        config=replace(config, support_search_max_attempts=4),
    )

    assert result["status"] == "blocked"
    assert result["tradable"] is False
    assert result["blocked_stage"] == "support_search"
    assert result["reason"] == (
        "no_clarabel_certified_exact_support_within_highs_budget"
    )
    assert result["weights"] == {}
    assert result["fallback_used"] is False
    search = result["requested"]["selection"]["support_search"]
    assert search["milp_attempt_limit"] == 4
    assert search["attempt_count"] == 5
    assert search["selected_strategy"] is None
    assert len({item["support_hash"] for item in search["attempts"]}) == 5
    assert all(
        item["status"] == "rejected_by_clarabel_socp"
        for item in search["attempts"]
    )
    milp_attempts = [
        item for item in search["attempts"]
        if item["strategy"] == "highs_milp_joint_linear_support"
    ]
    assert len(milp_attempts) == 4
    assert all(item["highs_status_code"] == 0 for item in milp_attempts)
    assert all(
        item["clarabel_certification"] == "infeasible"
        for item in search["attempts"]
    )
    assert all(item["trial_weights_returned"] is False for item in search["attempts"])


def test_highs_support_search_is_row_permutation_invariant() -> None:
    frame, styles, covariance, config = _phase_i_swap_problem()
    expected = optimize_stock_portfolio(
        frame,
        style_exposures=styles,
        annual_covariance=covariance,
        previous_weights={},
        config=config,
    )
    actual = optimize_stock_portfolio(
        frame.sample(frac=1.0, random_state=701).reset_index(drop=True),
        style_exposures=styles.sample(frac=1.0, random_state=702).reset_index(drop=True),
        annual_covariance=covariance,
        previous_weights={},
        config=config,
    )

    assert expected["status"] == actual["status"] == "ready"
    expected_search = expected["requested"]["selection"]["support_search"]
    actual_search = actual["requested"]["selection"]["support_search"]
    assert expected_search["selected_support_hash"] == actual_search["selected_support_hash"]
    assert [item["support_hash"] for item in expected_search["attempts"]] == [
        item["support_hash"] for item in actual_search["attempts"]
    ]


def _initial_support_capacity_problem():
    count = 100
    codes = [f"C{index:03d}.SZ" for index in range(count)]
    frame = pd.DataFrame({
        "ts_code": codes,
        "alpha_score": list(range(count, 0, -1)),
        "benchmark_weight": np.full(count, 1.0),
        "industry": ["all"] * count,
    })
    covariance = pd.DataFrame(
        np.eye(count, dtype=float) * 1.0e-4,
        index=codes,
        columns=codes,
    )
    buy_limit = {
        code: (0.013 if index < 50 else 0.030)
        for index, code in enumerate(codes)
    }
    config = StockOptimizerConfig(
        target_holdings=50,
        min_weight=0.005,
        max_weight=0.03,
        max_active_weight=0.04,
        industry_deviation=1.0,
        target_tracking_error=0.50,
        one_way_turnover_limit=1.0,
        buy_limit=buy_limit,
        benchmark_weight_unit="percent",
    )
    return frame, covariance, config


def test_initial_support_specific_precheck_cannot_short_circuit_joint_milp() -> None:
    frame, covariance, config = _initial_support_capacity_problem()
    precheck = precheck_stock_problem(
        frame,
        annual_covariance=covariance,
        previous_weights={},
        config=config,
    )
    assert precheck["status"] == "passed"
    initial = precheck["precheck"]["initial_support_diagnostics"]
    assert initial["status"] == "infeasible"
    assert initial["blocking_before_joint_milp"] is False
    assert any(
        item["message"] == "candidate_upper_bounds_below_budget"
        for item in initial["minimum_relaxation"]
    )

    result = optimize_stock_portfolio(
        frame,
        annual_covariance=covariance,
        previous_weights={},
        config=config,
    )
    assert result["status"] == "ready", result
    search = result["requested"]["selection"]["support_search"]
    assert search["attempts"][0]["strategy"] == (
        "initial_alpha_industry_candidate_clarabel_certification"
    )
    assert search["attempts"][0]["status"] == "rejected_by_clarabel_socp"
    certified_attempt = search["attempts"][-1]
    assert certified_attempt["status"] == "certified"
    assert certified_attempt["linear_diagnostics"][
        "max_constraint_violation"
    ] <= config.feasibility_tolerance
    selected = set(result["requested"]["selection"]["candidate_codes"])
    assert any(int(code[1:4]) >= 50 for code in selected)


def test_missing_highs_blocks_without_heuristic_support_fallback(monkeypatch) -> None:
    frame, styles, covariance, previous, config = _strict_problem()
    monkeypatch.setattr(optimizer_module, "scipy_milp", None)
    result = optimize_stock_portfolio(
        frame,
        style_exposures=styles,
        annual_covariance=covariance,
        previous_weights=previous,
        config=config,
    )

    assert result["status"] == "blocked"
    assert result["blocked_stage"] == "solver_availability"
    assert result["reason"] == "certified_support_solver_highs_milp_unavailable"
    assert result["solver"]["heuristic_support_fallback_used"] is False
    assert result["weights"] == {}
    assert result["transactions"] == []
    assert result["fallback_used"] is False

def test_linear_alpha_normalization_preserves_weighted_neutrality_and_scale() -> None:
    rng = np.random.default_rng(20260812)
    benchmark = rng.uniform(0.1, 2.0, size=80)
    benchmark /= benchmark.sum()
    style = rng.normal(size=80)
    raw = rng.normal(size=80)
    design = np.column_stack([np.ones(80), style])
    gram = design.T @ (benchmark[:, None] * design)
    beta = np.linalg.solve(gram, design.T @ (benchmark * raw))
    neutral = raw - design @ beta

    alpha = optimizer_module._normalise_alpha(
        pd.Series(neutral), benchmark, 0.05
    )
    affine = optimizer_module._normalise_alpha(
        pd.Series(7.5 * neutral + 13.0), benchmark, 0.05
    )
    np.testing.assert_allclose(alpha, affine, atol=1.0e-12, rtol=1.0e-12)
    assert float(benchmark @ alpha) == pytest.approx(0.0, abs=1.0e-12)
    assert float(benchmark @ (style * alpha)) == pytest.approx(0.0, abs=1.0e-12)
    assert np.sqrt(float(benchmark @ np.square(alpha))) == pytest.approx(
        0.05, abs=1.0e-12
    )


def test_optimizer_source_has_no_replacement_or_typical_mojibake_fragments() -> None:
    source = Path(__file__).with_name("stock_constraint_optimizer.py").read_text(
        encoding="utf-8"
    )
    assert "\ufffd" not in source
    assert not any(fragment in source for fragment in ("脳", "鏈", "闈", "Ã", "Â", "â€"))
