from __future__ import annotations

import sqlite3
import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
import model.portfolio_optimization.csi500_strategy as strategy_module

from model.portfolio_optimization.csi500_strategy import (
    DatabasePointInTimeRiskProvider,
    build_database_point_in_time_risk_provider,
    CSI500DataContractError,
    CSI500StrategyConfig,
    SCORE_TABLE,
    build_causal_icir_scores,
    load_csi500_constituents,
    persist_optimizer_factor_scores,
    run_csi500_strategy,
)
from model.portfolio_optimization.stock_constraint_optimizer import (
    StockOptimizerConfig,
)


def _dates(periods: int = 9) -> list[str]:
    return [
        value.strftime("%Y%m%d")
        for value in pd.date_range("2019-01-31", periods=periods, freq="ME")
    ]


def _synthetic_panel(periods: int = 8, seed: int = 711) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    codes = [f"{index:06d}.SZ" for index in range(500)]
    dates = _dates(periods + 1)
    alpha = rng.normal(size=500)
    noise_factor = rng.normal(size=500)
    style_size = rng.normal(size=500)
    style_value = rng.normal(size=500)
    rows: list[dict[str, object]] = []
    for period, signal_date in enumerate(dates[:-1]):
        label_noise = rng.normal(scale=0.001, size=500)
        for index, code in enumerate(codes):
            rows.append({
                "signal_date": signal_date,
                "maturity_date": dates[period + 1],
                "ts_code": code,
                # Raw index data are percentages and sum to exactly 100.
                "benchmark_weight": 0.2,
                "industry": f"industry_{index // 50:02d}",
                "label_next_ret": 0.008 * alpha[index] + label_noise[index],
                "factor_quality": alpha[index] + 0.01 * period,
                "factor_noise": noise_factor[index] + 0.002 * period,
                "style_size": style_size[index],
                "style_value": style_value[index],
                "buy_limit_weight": 1.0,
                "sell_limit_weight": 1.0,
                "is_suspended": 0,
                "limit_pressure": 0.0,
            })
    return pd.DataFrame(rows)


def _config(*, optimizer: StockOptimizerConfig | None = None) -> CSI500StrategyConfig:
    optimizer = optimizer or StockOptimizerConfig(
        target_holdings=50,
        min_weight=0.005,
        max_weight=0.04,
        max_active_weight=0.04,
        industry_deviation=0.025,
        style_bounds={
            "style_size": (-2.0, 2.0),
            "style_value": (-2.0, 2.0),
        },
        target_tracking_error=0.05,
        one_way_turnover_limit=1.0,
    )
    return CSI500StrategyConfig(
        factor_columns=("factor_quality", "factor_noise"),
        factor_lookback=4,
        factor_min_history=2,
        minimum_ic_cross_section=100,
        factor_min_coverage=1.0,
        style_columns=("style_size", "style_value"),
        style_min_coverage=1.0,
        train_end="20190331",
        validation_end="20190630",
        optimizer=optimizer,
    )


def _risk_provider(signal_date: str, group: pd.DataFrame, history: pd.DataFrame):
    del history
    return {
        "risk_root": np.eye(len(group), dtype=float) * 0.05,
        "diagnostics": {"signal_date": signal_date, "synthetic": True},
    }


def test_causal_rolling_icir_has_explicit_warmup_and_maturity_guard():
    panel = _synthetic_panel()
    config = _config()
    result = build_causal_icir_scores(panel, config=config)

    statuses = [item["status"] for item in result["periods"]]
    assert statuses[:3] == ["warmup", "warmup", "warmup"]
    assert statuses[3:] == ["ready"] * 5
    assert result["fallback_used"] is False
    assert len(result["scores"]) == 5 * 500
    for item in result["periods"][3:]:
        assert item["maturity_date"] < item["signal_date"]
        assert item["neutralization"]["after"]["max_abs_exposure"] < 2.0e-7

    # Changing labels that have not matured by the target signal cannot alter
    # that target's score.
    target = "20190630"
    changed = panel.copy()
    changed.loc[changed["maturity_date"] >= target, "label_next_ret"] += 1000.0
    changed_result = build_causal_icir_scores(changed, config=config)
    original_score = (
        result["scores"].loc[result["scores"]["signal_date"] == target]
        .sort_values("ts_code")["score"].to_numpy()
    )
    changed_score = (
        changed_result["scores"].loc[changed_result["scores"]["signal_date"] == target]
        .sort_values("ts_code")["score"].to_numpy()
    )
    np.testing.assert_allclose(original_score, changed_score, atol=0.0, rtol=0.0)


def test_ic_precision_blend_audits_causal_matrix_shrinkage_and_effective_cap():
    result = build_causal_icir_scores(_synthetic_panel(), config=_config())
    ready = next(item for item in result["periods"] if item["status"] == "ready")
    audit = ready["ic_combination"]
    weights = ready["weights"]
    assert audit["status"] == "ready"
    assert audit["future_data_used"] is False
    assert audit["hyperparameters_selected_from_report_only_test"] is False
    assert audit["common_observations"] >= 2
    assert len(audit["ic_matrix"]) == audit["common_observations"]
    assert len(audit["sample_covariance"]) == len(audit["factor_order"])
    assert len(audit["shrunk_covariance"]) == len(audit["factor_order"])
    assert sum(abs(value) for value in weights.values()) == pytest.approx(1.0)
    assert max(abs(value) for value in weights.values()) <= (
        audit["effective_absolute_weight_cap"] + 1.0e-12
    )
    assert max(audit["event_maturity_dates"]) < ready["signal_date"]


def test_walkforward_champion_reproduces_positive_ic_formula_without_fallback():
    assert strategy_module.WALKFORWARD_CHAMPION_FACTORS == (
        "quality_value_low_crowding_v8",
        "fundamental_quality_v4",
        "index_industry_risk_alpha_v8",
        "factor_domain_agent_v9",
        "deep_factor_agent_v4",
        "ai_factor_blend_v5",
        "small_value_quality_momo",
        "portfolio_optimizer_score_v2",
        "ai_factor_factory_v4",
        "industry_rotation_v4",
        "kline_ai_pattern_score",
    )
    config = replace(
        _config(),
        factor_weight_method="walkforward_positive_ic",
        factor_lookback=4,
        factor_min_history=2,
        walkforward_positive_ic_min_hit_rate=0.45,
    )
    matured = [
        {"ic": {"factor_quality": 0.10, "factor_noise": -0.03}},
        {"ic": {"factor_quality": 0.20, "factor_noise": 0.01}},
        {"ic": {"factor_quality": 0.30, "factor_noise": -0.02}},
    ]

    weights, evidence, audit = (
        strategy_module._causal_walkforward_positive_ic_weights(
            matured, config
        )
    )

    assert weights == {"factor_quality": pytest.approx(1.0)}
    assert evidence["factor_quality"]["mean_rank_ic"] == pytest.approx(0.20)
    assert evidence["factor_quality"]["positive_ratio"] == pytest.approx(1.0)
    assert evidence["factor_noise"]["raw_weight"] == pytest.approx(0.0)
    assert audit["method"] == "walkforward_positive_ic_v10"
    assert audit["future_data_used"] is False
    assert audit["test_used_for_calibration_or_selection"] is False
    assert audit["fallback_used"] is False


def test_walkforward_champion_blocks_when_no_factor_passes_gate():
    config = replace(
        _config(),
        factor_weight_method="walkforward_positive_ic",
        factor_lookback=4,
        factor_min_history=2,
    )
    matured = [
        {"ic": {"factor_quality": -0.10, "factor_noise": -0.03}},
        {"ic": {"factor_quality": -0.20, "factor_noise": -0.01}},
    ]

    weights, _, audit = (
        strategy_module._causal_walkforward_positive_ic_weights(
            matured, config
        )
    )

    assert weights is None
    assert audit["status"] == "warmup"
    assert audit["reason"] == "no_factor_passed_positive_ic_and_hit_rate_gate"
    assert audit["fallback_used"] is False


def test_walkforward_champion_end_to_end_preserves_raw_alpha_for_optimizer():
    config = replace(
        _config(), factor_weight_method="walkforward_positive_ic"
    )

    result = build_causal_icir_scores(_synthetic_panel(), config=config)

    statuses = [item["status"] for item in result["periods"]]
    assert statuses[:3] == ["warmup", "warmup", "warmup"]
    ready = next(item for item in result["periods"] if item["status"] == "ready")
    assert ready["neutralization"]["status"] == "not_applied"
    assert "hard_constraints" in ready["neutralization"]["reason"]
    assert all(weight >= 0.0 for weight in ready["weights"].values())
    assert sum(ready["weights"].values()) == pytest.approx(1.0)
    np.testing.assert_allclose(
        result["scores"]["raw_score"].to_numpy(),
        result["scores"]["score"].to_numpy(),
        atol=0.0,
        rtol=0.0,
    )


def test_score_is_invariant_to_input_row_order():
    panel = _synthetic_panel()
    config = _config()
    first = build_causal_icir_scores(panel, config=config)
    second = build_causal_icir_scores(
        panel.sample(frac=1.0, random_state=982).reset_index(drop=True),
        config=config,
    )
    columns = ["signal_date", "ts_code", "score", "source_hash"]
    left = first["scores"][columns].sort_values(columns[:2]).reset_index(drop=True)
    right = second["scores"][columns].sort_values(columns[:2]).reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right, check_exact=True)


def test_exact_csi500_sqlite_membership_and_weight_contract():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        create table index_constituent_period (
          universe text not null,
          index_code text,
          trade_date text not null,
          con_code text not null,
          weight real,
          source text,
          status text not null default 'ready',
          primary key (universe, trade_date, con_code)
        )
        """
    )
    rows = [
        ("CSI500_ENH", "000905.SH", "20200131", f"{index:06d}.SZ", 0.2, "test", "ready")
        for index in range(500)
    ]
    connection.executemany(
        "insert into index_constituent_period values (?, ?, ?, ?, ?, ?, ?)", rows
    )
    loaded = load_csi500_constituents(
        connection, start="20200131", end="20200131"
    )
    assert len(loaded) == 500
    assert loaded["ts_code"].nunique() == 500
    assert loaded["benchmark_weight"].sum() == pytest.approx(1.0, abs=1.0e-12)
    assert loaded["raw_benchmark_weight_total"].iloc[0] == pytest.approx(100.0)
    normalization = json.loads(loaded["benchmark_weight_normalization_json"].iloc[0])
    assert normalization["status"] == "normalized"
    assert normalization["member_count"] == 500
    assert normalization["accepted_total_range"] == pytest.approx([98.0, 102.0])

    incomplete = [
        ("CSI500_ENH", "000905.SH", "20200228", f"{index:06d}.SZ", 100.0 / 499, "test", "ready")
        for index in range(499)
    ]
    connection.executemany(
        "insert into index_constituent_period values (?, ?, ?, ?, ?, ?, ?)", incomplete
    )
    with pytest.raises(CSI500DataContractError, match="member_count_not_500"):
        load_csi500_constituents(
            connection, start="20200131", end="20200228"
        )


def test_sqlite_score_contract_is_atomic_idempotent_and_conflict_strict():
    scores = build_causal_icir_scores(
        _synthetic_panel(), config=_config()
    )["scores"]
    one_period = scores[scores["signal_date"] == "20190430"].copy()
    connection = sqlite3.connect(":memory:")
    inserted = persist_optimizer_factor_scores(
        connection, one_period,
        score_run_id="strict-run-1",
        score_name="causal-score",
    )
    assert inserted == 500
    assert connection.execute(f"select count(*) from {SCORE_TABLE}").fetchone()[0] == 500
    # Same evidence is idempotent.
    assert persist_optimizer_factor_scores(
        connection, one_period,
        score_run_id="strict-run-1",
        score_name="causal-score",
    ) == 500
    assert connection.execute(f"select count(*) from {SCORE_TABLE}").fetchone()[0] == 500

    changed = one_period.copy()
    changed["source_hash"] = "different-evidence"
    with pytest.raises(CSI500DataContractError, match="different_source_hash"):
        persist_optimizer_factor_scores(
            connection, changed,
            score_run_id="strict-run-1",
            score_name="causal-score",
        )


def _benchmark_mtm_calendar(
    dates: list[str],
) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        create table trade_calendar (
          trade_date text primary key,
          is_trade_day integer not null,
          source text not null
        )
        """
    )
    connection.executemany(
        "insert into trade_calendar values (?, 1, 'authoritative-test')",
        [(date,) for date in dates],
    )
    return connection


def test_benchmark_mtm_carries_whole_month_suspension_at_zero_return():
    signal = "20220531"
    maturity = "20220630"
    post_signal_dates = [
        value.strftime("%Y%m%d")
        for value in pd.bdate_range(end="2022-06-30", periods=21)
    ]
    connection = _benchmark_mtm_calendar([signal, *post_signal_dates])
    panel = pd.DataFrame([{
        "signal_date": signal,
        "maturity_date": maturity,
        "ts_code": "002670.SZ",
        "px": 9.60,
        "px_next": np.nan,
        "exit_trade_date": None,
        "pit_latest_pre_signal_quote_date": signal,
        "pit_latest_pre_signal_quote_price": 9.60,
        "label_next_ret": np.nan,
    }])
    valued, audit = strategy_module._attach_benchmark_mark_to_market_returns(
        connection, panel, config=_config()
    )
    row = valued.iloc[0]
    assert row["benchmark_mtm_status"] == "ready"
    assert row["benchmark_mark_to_market_return"] == pytest.approx(0.0)
    assert row["benchmark_mtm_start_quote_date"] == signal
    assert row["benchmark_mtm_end_quote_date"] == signal
    assert row["benchmark_mtm_end_staleness_trading_days"] == 21
    assert bool(row["benchmark_mtm_stale_price_carried_forward"]) is True
    assert bool(row["benchmark_mtm_forward_valuation_only"]) is True
    assert pd.isna(row["label_next_ret"])
    assert audit["permitted_for_ic_label"] is False
    assert audit["permitted_for_optimizer_or_comparator_realization"] is False


def test_benchmark_mtm_blocks_quote_staler_than_22_trading_days():
    signal = "20220531"
    post_signal_dates = [
        value.strftime("%Y%m%d")
        for value in pd.bdate_range(start="2022-06-01", periods=23)
    ]
    maturity = post_signal_dates[-1]
    connection = _benchmark_mtm_calendar([signal, *post_signal_dates])
    panel = pd.DataFrame([{
        "signal_date": signal,
        "maturity_date": maturity,
        "ts_code": "DELISTED.SZ",
        "px": 9.60,
        "px_next": np.nan,
        "exit_trade_date": None,
        "pit_latest_pre_signal_quote_date": signal,
        "pit_latest_pre_signal_quote_price": 9.60,
        "label_next_ret": np.nan,
    }])
    valued, audit = strategy_module._attach_benchmark_mark_to_market_returns(
        connection, panel, config=_config()
    )
    row = valued.iloc[0]
    assert row["benchmark_mtm_status"] == "blocked"
    assert pd.isna(row["benchmark_mark_to_market_return"])
    assert row["benchmark_mtm_end_staleness_trading_days"] == 23
    assert row["benchmark_mtm_reason"] == (
        "benchmark_mtm_exit_quote_staleness_exceeds_limit:23>22"
    )
    assert audit["status"] == "partial"
    assert audit["status_counts"] == {"blocked": 1}


def test_duplicate_calendar_sources_count_each_trade_date_once():
    signal = "20220531"
    maturity = "20220630"
    post_signal_dates = [
        value.strftime("%Y%m%d")
        for value in pd.bdate_range(end="2022-06-30", periods=21)
    ]
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        create table trade_calendar (
          trade_date text not null,
          is_trade_day integer not null,
          source text not null
        )
        """
    )
    connection.executemany(
        "insert into trade_calendar values (?, 1, ?)",
        [
            (date, source)
            for date in [signal, *post_signal_dates]
            for source in ("source-a", "source-b")
        ],
    )
    connection.execute(
        """
        create table stock_ohlcv_daily (
          trade_date text not null,
          ts_code text not null,
          qfq_close real
        )
        """
    )
    connection.execute(
        "insert into stock_ohlcv_daily values (?, ?, ?)",
        (signal, "OLD.SZ", 10.0),
    )
    panel = pd.DataFrame([{
        "signal_date": signal,
        "maturity_date": maturity,
        "ts_code": "OLD.SZ",
        "px": 10.0,
        "px_next": np.nan,
        "exit_trade_date": None,
        "pit_latest_pre_signal_quote_date": signal,
        "pit_latest_pre_signal_quote_price": 10.0,
        "label_next_ret": np.nan,
    }])
    benchmark, benchmark_audit = (
        strategy_module._attach_benchmark_mark_to_market_returns(
            connection, panel, config=_config()
        )
    )
    provider = strategy_module.DatabasePointInTimeHoldingValuationProvider(
        connection, config=_config()
    )
    holding = provider(signal, maturity, ["OLD.SZ"])
    assert benchmark.iloc[0][
        "benchmark_mtm_end_staleness_trading_days"
    ] == 21
    assert benchmark_audit["calendar_source"] == ["source-a", "source-b"]
    assert holding["status"] == "ready"
    assert holding["assets"]["OLD.SZ"][
        "end_quote_staleness_trading_days"
    ] == 21
    assert provider.prefetch_audit["calendar_source"] == [
        "source-a", "source-b"
    ]

def test_existing_holding_provider_blocks_quote_staler_than_22_days():
    signal = "20220531"
    post_signal_dates = [
        value.strftime("%Y%m%d")
        for value in pd.bdate_range(start="2022-06-01", periods=23)
    ]
    maturity = post_signal_dates[-1]
    connection = _benchmark_mtm_calendar([signal, *post_signal_dates])
    connection.execute(
        """
        create table stock_ohlcv_daily (
          trade_date text not null,
          ts_code text not null,
          qfq_close real,
          primary key (trade_date, ts_code)
        )
        """
    )
    connection.execute(
        "insert into stock_ohlcv_daily values (?, ?, ?)",
        (signal, "OLD.SZ", 10.0),
    )
    provider = strategy_module.DatabasePointInTimeHoldingValuationProvider(
        connection, config=_config()
    )
    result = provider(signal, maturity, ["OLD.SZ"])
    asset = result["assets"]["OLD.SZ"]
    assert result["status"] == "blocked"
    assert result["returns"] == {}
    assert asset["end_quote_staleness_trading_days"] == 23
    assert asset["reason"] == (
        "holding_valuation_end_quote_staleness_exceeds_limit:23>22"
    )
    assert asset["existing_holding_valuation_only"] is True
    assert asset["permitted_for_new_positions"] is False
    assert asset["permitted_for_alpha_or_ic"] is False
    assert asset["future_quote_used"] is False


def test_prior_holding_outside_incomplete_universe_uses_pit_without_state_loss():
    panel = _synthetic_panel()
    baseline = run_csi500_strategy(
        panel, config=_config(), risk_provider=_risk_provider
    )
    first_ready = next(
        item for item in baseline["periods"] if item["status"] == "ready"
    )
    missing_code = next(iter(first_ready["optimized_order_weights"]))
    gap_date = "20190531"
    incomplete = panel[
        ~(
            (panel["signal_date"] == gap_date)
            & (panel["ts_code"] == missing_code)
        )
    ].copy()
    calls: list[tuple[str, str, tuple[str, ...]]] = []

    def holding_provider(signal_date, maturity_date, codes):
        requested = tuple(sorted(map(str, codes)))
        calls.append((signal_date, maturity_date, requested))
        return {
            "status": "ready",
            "returns": {code: 0.0 for code in requested},
            "assets": {
                code: {
                    "status": "ready",
                    "existing_holding_valuation_only": True,
                    "permitted_for_new_positions": False,
                    "permitted_for_alpha_or_ic": False,
                }
                for code in requested
            },
            "usage_restricted_to_previous_holdings": True,
        }

    result = run_csi500_strategy(
        incomplete,
        config=_config(),
        risk_provider=_risk_provider,
        holding_valuation_provider=holding_provider,
    )
    gap = next(
        item for item in result["periods"]
        if item["signal_date"] == gap_date
    )
    following = next(
        item for item in result["periods"]
        if item["signal_date"] == "20190630"
    )
    assert gap["status"] == "blocked"
    assert gap["portfolio_state_advanced"] is True
    assert gap["holding_valuation_status"] == "ready"
    assert gap["holding_valuation_permitted_for_new_positions"] is False
    assert gap["holding_valuation_permitted_for_alpha_or_ic"] is False
    used = gap["holding_valuation_used_codes_by_portfolio"]
    assert missing_code in used["optimized"]
    assert calls and missing_code in calls[0][2]
    assert following["status"] != "blocked_state_continuity"


def test_zero_buy_missing_label_cannot_become_new_position_or_use_mtm():
    panel = _synthetic_panel()
    baseline = run_csi500_strategy(
        panel, config=_config(), risk_provider=_risk_provider
    )
    first_ready = next(
        item for item in baseline["periods"] if item["status"] == "ready"
    )
    target_date = first_ready["signal_date"]
    target_code = first_ready["global_top50_codes"][0]
    target = (
        (panel["signal_date"] == target_date)
        & (panel["ts_code"] == target_code)
    )
    panel["benchmark_mark_to_market_return"] = panel["label_next_ret"]
    panel.loc[target, "benchmark_mark_to_market_return"] = 0.0
    panel.loc[target, "label_next_ret"] = np.nan
    calls: list[tuple[str, ...]] = []

    def forbidden_provider(signal_date, maturity_date, codes):
        calls.append(tuple(codes))
        raise AssertionError("new/comparator positions must never use holding MTM")

    result = run_csi500_strategy(
        panel,
        config=_config(),
        risk_provider=_risk_provider,
        holding_valuation_provider=forbidden_provider,
    )
    period = next(
        item for item in result["periods"]
        if item["signal_date"] == target_date
    )
    assert float((
        period["optimizer_result"].get("weights") or {}
    ).get(target_code, 0.0)) <= _config().optimizer.feasibility_tolerance
    assert target_code in period["direct_result"]["weights"]
    assert period["status"] == "ready"
    assert target_code in period[
        "missing_executable_return_trade_policy"
    ]["buy_blocked_codes"]
    assert period["direct_status"] == "unavailable_missing_company_action_return"
    assert period["direct_return"] is None
    assert period["direct_not_required_for_formal_optimizer_path"] is True
    assert period[
        "holding_valuation_not_used_for_comparator_establishment"
    ] is True
    assert calls == []

def test_strategy_uses_mtm_only_for_benchmark_not_asset_realization():
    panel = _synthetic_panel()
    baseline = run_csi500_strategy(
        panel, config=_config(), risk_provider=_risk_provider
    )
    target_date = "20190531"
    target_period = next(
        item for item in baseline["periods"]
        if item["signal_date"] == target_date
    )
    strategy_holdings = (
        set(target_period["optimized_order_weights"])
        | set(target_period["direct_comparator_weights"])
        | set(target_period["same_support_comparator_weights"])
    )
    missing_code = next(
        code for code in panel["ts_code"].unique()
        if code not in strategy_holdings
    )
    panel["benchmark_mark_to_market_return"] = panel["label_next_ret"]
    target = (
        (panel["signal_date"] == target_date)
        & (panel["ts_code"] == missing_code)
    )
    panel.loc[target, "label_next_ret"] = np.nan
    panel.loc[target, "benchmark_mark_to_market_return"] = 0.0
    result = run_csi500_strategy(
        panel, config=_config(), risk_provider=_risk_provider
    )
    period = next(
        item for item in result["periods"]
        if item["signal_date"] == target_date
    )
    assert period["status"] == "ready"
    assert period["benchmark_valuation_audit"]["return_column"] == (
        "benchmark_mark_to_market_return"
    )
    assert period["benchmark_valuation_audit"]["missing_code_count"] == 0
    assert missing_code not in period["optimized_order_weights"]
    assert missing_code not in period["direct_comparator_weights"]
    assert missing_code not in period["same_support_comparator_weights"]

def test_full_pipeline_solves_500_to_50_and_keeps_two_score_comparators():
    result = run_csi500_strategy(
        _synthetic_panel(), config=_config(), risk_provider=_risk_provider
    )
    assert result["status"] == "ready"
    assert result["tradable_period_count"] == 5
    assert result["optimizer_attempts"] == 5
    assert result["optimizer_certified_periods"] == 5
    assert result["constraint_hit_rate"] == pytest.approx(1.0)
    assert len(result["curves"]) == 5
    assert result["governance"]["test_role"] == "report_only"
    assert result["governance"]["test_parameters_mutated"] is False
    assert result["metrics"]["optimized"]["periods"] == 5
    assert result["metrics"]["direct"]["definition"].startswith("global_top50")

    ready = [item for item in result["periods"] if item["status"] == "ready"]
    assert len(ready) == 5
    for period in ready:
        optimized = period["optimized_order_weights"]
        direct = period["direct_comparator_weights"]
        same_support = period["same_support_comparator_weights"]
        candidates = period["candidate_codes"]
        global_top50 = period["global_top50_codes"]
        assert period["direct_order_weights"] == {}
        assert period["same_support_order_weights"] == {}
        assert len(optimized) == len(direct) == len(same_support) == 50
        assert set(optimized) == set(candidates)
        assert set(same_support) == set(candidates)
        assert set(direct) == set(global_top50)
        assert sum(optimized.values()) == pytest.approx(1.0, abs=2.0e-6)
        assert sum(direct.values()) == pytest.approx(1.0, abs=1.0e-12)
        assert sum(same_support.values()) == pytest.approx(1.0, abs=1.0e-12)
        assert period["optimizer_result"]["fallback_used"] is False
        for comparator in (period["direct_result"], period["same_support_result"]):
            assert comparator["not_optimizer_fallback"] is True
            assert comparator["comparator_only"] is True
            assert comparator["tradable"] is False
            assert comparator["trade_limit_violation_count"] == 0


def test_blocked_rebalance_carries_all_prior_portfolios_with_zero_cost():
    blocked_date = "20190531"

    def intermittently_blocked_risk(
        signal_date: str, group: pd.DataFrame, history: pd.DataFrame
    ):
        if signal_date == blocked_date:
            raise CSI500DataContractError("planned_signal_date_risk_block")
        return _risk_provider(signal_date, group, history)

    panel = _synthetic_panel()
    result = run_csi500_strategy(
        panel, config=_config(), risk_provider=intermittently_blocked_risk
    )
    carried = next(
        item for item in result["periods"]
        if item["signal_date"] == blocked_date
    )
    assert carried["status"] == "carried"
    assert carried["evaluation_included"] is True
    assert carried["rebalance_blocked"] is True
    assert carried["no_new_orders"] is True
    assert carried["optimized_order_weights"] == {}
    assert carried["direct_order_weights"] == {}
    assert carried["same_support_order_weights"] == {}
    assert carried["optimized_turnover"] == 0.0
    assert carried["direct_turnover"] == 0.0
    assert carried["same_support_turnover"] == 0.0
    assert carried["optimized_cost"] == 0.0
    assert carried["direct_cost"] == 0.0
    assert carried["same_support_cost"] == 0.0

    first = next(item for item in result["periods"] if item["status"] == "ready")
    first_returns = dict(zip(
        panel.loc[panel["signal_date"] == first["signal_date"], "ts_code"],
        panel.loc[panel["signal_date"] == first["signal_date"], "label_next_ret"],
    ))
    prior = strategy_module._drift_weights(
        first["optimized_order_weights"], first_returns
    )
    blocked_returns = dict(zip(
        panel.loc[panel["signal_date"] == blocked_date, "ts_code"],
        panel.loc[panel["signal_date"] == blocked_date, "label_next_ret"],
    ))
    assert carried["optimized_return"] == pytest.approx(
        strategy_module._portfolio_return(prior, blocked_returns)
    )
    assert result["carried_period_count"] == 1
    assert result["rebalance_blocked_periods"] == 1
    assert result["metrics"]["performance_status"] == (
        "invalid_incomplete_requested_window"
    )
    assert result["requested_window_performance_status"] == (
        "invalid_incomplete_requested_window"
    )
    assert result["metrics"]["optimized"]["sharpe"] is None
    assert result["metrics"]["longest_contiguous_segment"]["periods"] == 5
    assert result["metrics"]["optimized"]["periods"] == 5


def test_incomplete_benchmark_month_invalidates_formal_path_but_keeps_segments():
    panel = _synthetic_panel()
    baseline = run_csi500_strategy(
        panel, config=_config(), risk_provider=_risk_provider
    )
    first = next(item for item in baseline["periods"] if item["status"] == "ready")
    held = (
        set(first["optimized_order_weights"])
        | set(first["direct_comparator_weights"])
        | set(first["same_support_comparator_weights"])
    )
    missing_code = next(
        code for code in panel["ts_code"].unique() if code not in held
    )
    gap_date = "20190531"
    incomplete = panel[
        ~(
            (panel["signal_date"] == gap_date)
            & (panel["ts_code"] == missing_code)
        )
    ].copy()
    result = run_csi500_strategy(
        incomplete, config=_config(), risk_provider=_risk_provider
    )
    gap = next(
        item for item in result["periods"] if item["signal_date"] == gap_date
    )
    assert gap["status"] == "blocked"
    assert gap["carry_status"] == "held_and_marked_to_market"
    assert gap["portfolio_state_advanced"] is True
    assert gap["evaluation_included"] is False
    assert result["metrics"]["performance_status"] == (
        "invalid_incomplete_requested_window"
    )
    assert result["metrics"]["formal_metrics_valid"] is False
    assert result["metrics"]["optimized"]["sharpe"] is None
    assert result["metrics"]["optimized"]["information_ratio"] is None
    assert gap_date in result["metrics"]["continuity"]["gap_periods"]
    assert len(result["metrics"]["diagnostic_contiguous_segments"]) >= 2
    assert result["metrics"]["longest_contiguous_segment"] is not None
    assert result["curve_status"] == "diagnostic_non_contiguous"


def test_infeasible_optimizer_period_returns_no_optimizer_or_direct_weights():
    infeasible = replace(
        _config().optimizer,
        min_weight=0.03,
        max_weight=0.04,
    )
    result = run_csi500_strategy(
        _synthetic_panel(),
        config=_config(optimizer=infeasible),
        risk_provider=_risk_provider,
    )
    attempted = [
        item for item in result["periods"] if item["optimizer_status"] != "not_attempted"
    ]
    assert attempted
    assert result["tradable_period_count"] == 0
    assert result["fallback_used"] is False
    for period in attempted:
        assert period["optimizer_status"] == "blocked"
        assert period["optimized_order_weights"] == {}
        assert period["direct_order_weights"] == {}
        assert period["optimizer_result"]["weights"] == {}
        assert period["optimizer_result"]["tradable"] is False


def test_incomplete_period_is_blocked_without_score_or_weights():
    panel = _synthetic_panel()
    bad_date = "20190630"
    code = panel.loc[panel["signal_date"] == bad_date, "ts_code"].iloc[0]
    incomplete = panel[
        ~((panel["signal_date"] == bad_date) & (panel["ts_code"] == code))
    ].copy()
    scores = build_causal_icir_scores(incomplete, config=_config())
    audit = {item["signal_date"]: item for item in scores["periods"]}
    assert audit[bad_date]["status"] == "blocked"
    assert not (scores["scores"]["signal_date"] == bad_date).any()

    strategy = run_csi500_strategy(
        incomplete, config=_config(), risk_provider=_risk_provider
    )
    period = next(item for item in strategy["periods"] if item["signal_date"] == bad_date)
    assert period["status"] == "blocked"
    assert period["optimized_order_weights"] == {}
    assert period["direct_order_weights"] == {}


def test_complete_500_fraction_weights_are_audited_and_normalized():
    panel = _synthetic_panel()
    panel["benchmark_weight"] = 0.993 / 500.0
    result = build_causal_icir_scores(panel, config=_config())

    ready = [item for item in result["periods"] if item["status"] == "ready"]
    assert ready
    for period in ready:
        audit = period["benchmark_weight_normalization"]
        assert audit["status"] == "normalized"
        assert audit["member_count"] == 500
        assert audit["input_unit"] == "fraction"
        assert audit["input_total"] == pytest.approx(0.993, abs=1.0e-12)
        assert audit["accepted_total_range"] == pytest.approx([0.98, 1.02])
        assert audit["missing_member_masked_by_normalization"] is False
    totals = result["scores"].groupby("signal_date")["benchmark_weight"].sum()
    np.testing.assert_allclose(totals.to_numpy(), 1.0, atol=1.0e-12, rtol=0.0)
    stored_audit = json.loads(
        result["scores"]["benchmark_weight_normalization_json"].iloc[0]
    )
    assert stored_audit["input_total"] == pytest.approx(0.993, abs=1.0e-12)


def test_missing_member_is_blocked_before_weight_normalization():
    panel = _synthetic_panel()
    bad_date = "20190630"
    period_mask = panel["signal_date"] == bad_date
    panel.loc[period_mask, "benchmark_weight"] = 0.999 / 500.0
    missing_code = panel.loc[period_mask, "ts_code"].iloc[0]
    panel = panel[
        ~((panel["signal_date"] == bad_date) & (panel["ts_code"] == missing_code))
    ].copy()

    result = build_causal_icir_scores(panel, config=_config())
    period = next(item for item in result["periods"] if item["signal_date"] == bad_date)
    assert period["status"] == "blocked"
    assert "benchmark_member_count_not_500" in period["reason"]
    assert "benchmark_weight_total_invalid" not in period["reason"]
    assert not (result["scores"]["signal_date"] == bad_date).any()


def test_nonfinite_style_values_use_only_contemporaneous_audited_robust_processing():
    panel = _synthetic_panel()
    for _, indexes in panel.groupby("signal_date", sort=True).groups.items():
        selected = list(indexes)[:10]
        panel.loc[selected[:5], "style_size"] = np.inf
        panel.loc[selected[5:], "style_size"] = np.nan
        panel.loc[selected[:4], "style_value"] = -np.inf
    config = replace(_config(), style_min_coverage=0.95)

    result = build_causal_icir_scores(panel, config=config)
    ready = [item for item in result["periods"] if item["status"] == "ready"]
    assert ready
    audit = ready[0]["neutralization"]["style_preprocessing"]
    assert audit["status"] == "ready"
    assert audit["point_in_time"] is True
    assert audit["future_data_used"] is False
    assert audit["zero_fill_used"] is False
    size = audit["columns"]["style_size"]
    assert size["raw_coverage"] == pytest.approx(0.98)
    assert size["positive_inf_replaced_with_nan"] == 5
    assert size["missing_input_count"] == 10
    assert size["imputed_count"] == 10
    assert size["fill_value"] != 0.0
    assert size["postprocess_finite_count"] == 500
    assert size["winsorized_count"] > 0
    assert np.isfinite(
        result["scores"][["style_size", "style_value"]].to_numpy(dtype=float)
    ).all()


def _database_risk_config() -> CSI500StrategyConfig:
    return replace(
        _config(),
        database_risk_lookback_trading_days=30,
        database_risk_min_return_observations=20,
        database_risk_min_row_coverage=0.60,
        database_risk_max_common_return_observations=30,
        database_risk_base_diagonal_shrinkage=0.30,
        database_risk_max_diagonal_shrinkage=0.90,
        database_risk_half_life=10.0,
        database_risk_max_abs_daily_return=0.50,
        risk_min_asset_coverage=0.60,
    )


def _create_daily_price_table(
    connection: sqlite3.Connection,
    *,
    short_history_price_observations: int = 10,
    include_short_history_asset: bool = False,
    include_future: bool = True,
) -> tuple[list[str], str]:
    connection.execute(
        """
        create table stock_ohlcv_daily (
          trade_date text not null,
          ts_code text not null,
          qfq_close real,
          suspend_timing text,
          primary key (trade_date, ts_code)
        )
        """
    )
    connection.execute(
        """
        create table trade_calendar (
          trade_date text not null primary key,
          is_trade_day integer not null,
          source text not null
        )
        """
    )
    history_dates = [
        value.strftime("%Y%m%d")
        for value in pd.bdate_range("2024-01-02", periods=35)
    ]
    signal_date = "20240301"
    future_dates = [
        value.strftime("%Y%m%d")
        for value in pd.bdate_range("2024-03-01", periods=5)
    ]
    calendar_dates = sorted({*history_dates, signal_date, *future_dates})
    connection.executemany(
        "insert into trade_calendar values (?, ?, ?)",
        [(trade_date, 1, "unit_test_calendar") for trade_date in calendar_dates],
    )
    codes = ["000001.SZ", "000002.SZ", "000003.SZ"]
    rows: list[tuple[object, ...]] = []
    for code_index, code in enumerate(codes):
        for date_index, trade_date in enumerate(history_dates):
            price: float | None = 100.0 + code_index * 5.0 + date_index * 0.10
            suspension = ""
            if code == "000002.SZ" and date_index == 12:
                price = None
                suspension = "SUSPENDED"
            rows.append((trade_date, code, price, suspension))
        if include_future:
            for date_index, trade_date in enumerate(future_dates):
                # Deliberately very different future prices.  They must never
                # enter the risk window or alter the point-in-time result.
                rows.append(
                    (trade_date, code, 1000.0 + code_index + date_index, "")
                )
    if include_short_history_asset:
        code = "000004.SZ"
        codes.append(code)
        for date_index, trade_date in enumerate(history_dates[-short_history_price_observations:]):
            rows.append((trade_date, code, 80.0 + date_index * 0.05, ""))
    connection.executemany(
        "insert into stock_ohlcv_daily values (?, ?, ?, ?)", rows
    )
    return codes, signal_date


def _database_risk_group(codes: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": list(reversed(codes)),
        "industry": [
            f"industry_{index % 2}" for index in range(len(codes))
        ],
        "style_size": np.linspace(-1.0, 1.0, len(codes)),
        "style_value": np.linspace(0.5, -0.5, len(codes)),
    })


def test_database_risk_provider_uses_pre_index_history_and_excludes_future():
    connection = sqlite3.connect(":memory:")
    codes, signal_date = _create_daily_price_table(connection)
    group = _database_risk_group(codes)
    provider = build_database_point_in_time_risk_provider(
        connection,
        pd.DataFrame({
            "signal_date": [signal_date] * len(codes),
            "ts_code": codes,
        }),
        config=_database_risk_config(),
    )

    supplied = provider(signal_date, group, pd.DataFrame())
    assert isinstance(supplied["risk_root"], pd.DataFrame)
    assert set(supplied["risk_root"].columns) == set(codes)
    assert supplied["risk_asset_codes"] == sorted(codes)
    audit = supplied["diagnostics"]
    assert audit["current_asset_count"] == 3
    assert audit["latest_return_end_date"] < signal_date
    assert audit["strictly_before_signal"] is True
    assert audit["future_data_used"] is False
    assert audit["future_rows_loaded"] == 0
    assert audit["price_field"] == "qfq_close"
    assert audit["annualization"] == pytest.approx(252.0)
    assert audit["explicit_suspension_prices_filled"] == 1
    assert audit["zero_fill_used"] is False
    assert audit["backfill_used"] is False
    assert audit["calendar_table"] == "trade_calendar"
    assert audit["calendar_source"] == ["unit_test_calendar"]
    assert audit["risk_window_observations"] == 30
    assert audit["minimum_factor_return_observations"] == 20
    assert audit["maximum_factor_return_observations"] == 30
    assert audit["sample_length_ratio_to_factor_maximum"] == pytest.approx(1.0)
    assert audit["effective_diagonal_shrinkage"] == pytest.approx(0.30)
    assert audit["sample_length_shrinkage_addon"] == pytest.approx(0.0)
    assert audit["short_sample_risk_policy"].endswith(
        "conservative_short_history_specific_prior"
    )
    assert audit["individual_return_imputation_used"] is False
    assert audit["daily_factor_regression"].endswith("observed_assets_only")
    assert audit["unrestricted_forward_fill_used"] is False
    # 000003.SZ is treated exactly like the other current names even though
    # no historical index-membership table was supplied: its stock history is
    # available before it enters the current index cross-section.
    assert audit["minimum_return_observations_per_asset"] >= 20

    cached = provider(signal_date, group, pd.DataFrame())
    assert cached["diagnostics"]["cache_hit"] is True
    pd.testing.assert_frame_equal(
        supplied["risk_root"], cached["risk_root"], check_exact=True
    )


def test_database_risk_provider_excludes_nontrading_null_singletons():
    connection = sqlite3.connect(":memory:")
    codes, signal_date = _create_daily_price_table(
        connection, include_future=False
    )
    connection.executemany(
        "insert into trade_calendar values (?, ?, ?)",
        [
            ("20240113", 0, "unit_test_calendar"),
            ("20240114", 0, "unit_test_calendar"),
        ],
    )
    connection.executemany(
        "insert into stock_ohlcv_daily values (?, ?, ?, ?)",
        [
            ("20240113", codes[0], None, ""),
            ("20240114", codes[0], None, ""),
        ],
    )
    provider = DatabasePointInTimeRiskProvider(
        connection,
        asset_codes=codes,
        signal_dates=[signal_date],
        config=_database_risk_config(),
    )

    supplied = provider(signal_date, _database_risk_group(codes), pd.DataFrame())
    audit = supplied["diagnostics"]
    assert audit["calendar_nontrading_dates_excluded"] >= 2
    assert audit["risk_window_observations"] == 30
    assert audit["minimum_row_coverage_observed"] == pytest.approx(1.0)
    assert audit["individual_return_imputation_used"] is False


def test_database_risk_provider_uses_fixed_factor_window_and_short_history_prior():
    connection = sqlite3.connect(":memory:")
    codes, signal_date = _create_daily_price_table(
        connection,
        include_short_history_asset=True,
        short_history_price_observations=24,
        include_future=False,
    )
    group = _database_risk_group(codes)
    provider = DatabasePointInTimeRiskProvider(
        connection,
        asset_codes=codes,
        signal_dates=[signal_date],
        config=_database_risk_config(),
    )

    supplied = provider(signal_date, group, pd.DataFrame())
    audit = supplied["diagnostics"]
    assert audit["risk_window_observations"] == 30
    assert audit["latest_first_valid_return_date"] > audit["risk_window_start"]
    assert audit["latest_entry_assets"] == ["000004.SZ"]
    assert audit["minimum_asset_return_observations"] == 20
    assert audit["minimum_return_observations_per_asset"] == 23
    assert audit["minimum_asset_coverage_observed"] >= 0.60
    assert audit["minimum_row_coverage_observed"] >= 0.60
    assert audit["base_diagonal_shrinkage"] == pytest.approx(0.30)
    assert audit["effective_diagonal_shrinkage"] == pytest.approx(0.30)
    assert audit["sample_length_shrinkage_addon"] == pytest.approx(0.0)
    assert audit["short_history_asset_count"] == 1
    short = audit["short_history_assets"][0]
    assert short["ts_code"] == "000004.SZ"
    assert short["observations"] == 23
    assert short["prior_strength"] > 0.0
    assert short["final_specific_variance"] >= short["raw_specific_variance"]
    assert audit["individual_return_imputation_used"] is False
    assert audit["future_data_used"] is False
    assert audit["zero_fill_used"] is False


def test_database_risk_provider_blocks_new_asset_below_history_floor():
    connection = sqlite3.connect(":memory:")
    codes, signal_date = _create_daily_price_table(
        connection, include_short_history_asset=True, include_future=False
    )
    group = _database_risk_group(codes)
    provider = DatabasePointInTimeRiskProvider(
        connection,
        asset_codes=codes,
        signal_dates=[signal_date],
        config=_database_risk_config(),
    )
    with pytest.raises(
        CSI500DataContractError,
        match=r"database_risk_asset_return_observations_below_minimum:000004.SZ:9",
    ):
        provider(signal_date, group, pd.DataFrame())


def test_database_runner_uses_strict_provider_by_default(monkeypatch):
    connection = sqlite3.connect(":memory:")
    panel = pd.DataFrame({
        "signal_date": ["20240131"],
        "ts_code": ["000001.SZ"],
    })
    captured: dict[str, object] = {}

    class DummyProvider:
        prefetch_audit = {
            "provider": "database_point_in_time_qfq_factor_risk",
            "prefetched": True,
        }

        def __call__(self, signal_date, group, history):
            raise AssertionError("runner stub must not execute provider")

    dummy = DummyProvider()

    monkeypatch.setattr(
        strategy_module,
        "build_csi500_panel_from_database",
        lambda *args, **kwargs: (panel, {"periods": []}),
    )

    def fake_builder(connection_arg, panel_arg, *, config):
        assert connection_arg is connection
        assert panel_arg is panel
        captured["builder_config"] = config
        return dummy

    def fake_run(panel_arg, **kwargs):
        assert panel_arg is panel
        captured["risk_provider"] = kwargs["risk_provider"]
        captured["holding_valuation_provider"] = kwargs[
            "holding_valuation_provider"
        ]
        return {"status": "blocked", "tradable_period_count": 0}

    monkeypatch.setattr(
        strategy_module, "build_database_point_in_time_risk_provider", fake_builder
    )
    monkeypatch.setattr(strategy_module, "run_csi500_strategy", fake_run)

    holding_provider = lambda signal, maturity, codes: {
        "status": "ready", "returns": {}
    }
    result = strategy_module.run_csi500_strategy_from_database(
        connection, start="20240101", end="20240131",
        config=_database_risk_config(),
        holding_valuation_provider=holding_provider,
    )
    assert captured["risk_provider"] is dummy
    assert captured["holding_valuation_provider"] is holding_provider
    assert result["database_audit"]["risk_provider"][
        "default_database_provider_used"
    ] is True
    assert result["database_audit"]["risk_provider"]["prefetched"] is True


def test_style_coverage_below_threshold_blocks_without_degradation():
    panel = _synthetic_panel()
    bad_date = "20190630"
    selected = panel.index[panel["signal_date"] == bad_date][:30]
    panel.loc[selected, "style_size"] = np.inf
    config = replace(_config(), style_min_coverage=0.95)

    result = build_causal_icir_scores(panel, config=config)
    period = next(item for item in result["periods"] if item["signal_date"] == bad_date)
    assert period["status"] == "blocked"
    assert "style_coverage_below_threshold" in period["reason"]
    assert not (result["scores"]["signal_date"] == bad_date).any()


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"portfolio_notional": 0.0}, "portfolio_notional_must_be_positive"),
        ({"raw_index_weight_total": 0.0}, "raw_index_weight_total_must_be_positive"),
        (
            {"raw_index_weight_tolerance": 100.0},
            "raw_index_weight_tolerance_must_be_below_total",
        ),
    ],
)
def test_weight_and_notional_config_guards_are_independent(changes, reason):
    config = replace(_config(), **changes)
    with pytest.raises(CSI500DataContractError, match=reason):
        build_causal_icir_scores(_synthetic_panel(), config=config)


def test_tushare_thousand_cny_amount_converts_to_single_day_cny_capacity():
    group = pd.DataFrame({
        "ts_code": ["000001.SZ"],
        # Tushare daily amount is in thousand CNY: 10,000 means CNY 10m.
        "amount": [10_000.0],
        "is_suspended": [0],
        "limit_pressure": [0.0],
    })
    config = replace(
        _config(),
        portfolio_notional=100_000_000.0,
        max_adv_participation=0.10,
        trading_days_per_period=1,
        liquidity_amount_unit="thousand_cny",
        liquidity_amount_to_cny_multiplier=1000.0,
    )

    buy, sell, audit = strategy_module._trade_limits(group, config)
    assert buy["000001.SZ"] == pytest.approx(0.01)
    assert sell["000001.SZ"] == pytest.approx(0.01)
    assert audit["method"] == "adv_participation_weight_limits_cny"
    assert audit["raw_amount_unit"] == "thousand_cny"
    assert audit["amount_to_cny_multiplier"] == pytest.approx(1000.0)
    assert audit["amount_unit_inferred"] is False
    assert audit["capacity_currency"] == "CNY"
    assert audit["execution_days"] == 1
    assert audit["raw_amount_quantiles"]["p50"] == pytest.approx(10_000.0)
    assert audit["amount_cny_quantiles"]["p50"] == pytest.approx(10_000_000.0)
    assert audit["participation_capacity_cny_quantiles"]["p50"] == pytest.approx(
        1_000_000.0
    )
    assert audit["uncapped_capacity_weight_quantiles"]["p50"] == pytest.approx(
        0.01
    )
    assert audit["final_buy_limit_weight_quantiles"]["p50"] == pytest.approx(
        0.01
    )


def test_liquidity_execution_days_are_explicit_and_unit_mismatch_blocks():
    group = pd.DataFrame({
        "ts_code": ["000001.SZ"],
        "amount": [10_000.0],
    })
    staged = replace(
        _config(),
        portfolio_notional=100_000_000.0,
        max_adv_participation=0.10,
        trading_days_per_period=5,
    )
    buy, _, audit = strategy_module._trade_limits(group, staged)
    assert buy["000001.SZ"] == pytest.approx(0.05)
    assert audit["execution_days"] == 5

    mismatched = replace(
        staged,
        liquidity_amount_unit="thousand_cny",
        liquidity_amount_to_cny_multiplier=1.0,
    )
    with pytest.raises(
        CSI500DataContractError,
        match="liquidity_amount_unit_multiplier_mismatch",
    ):
        strategy_module._trade_limits(group, mismatched)




def _pit_completion_config() -> CSI500StrategyConfig:
    return replace(
        _config(),
        factor_columns=("factor_quality",),
        style_columns=(),
    )


def _create_pit_completion_case(
    *,
    include_previous_signal: bool,
) -> tuple[
    sqlite3.Connection,
    pd.DataFrame,
    list[tuple[str, str]],
    str,
    CSI500StrategyConfig,
]:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        create table index_constituent_period (
          universe text not null,
          index_code text,
          trade_date text not null,
          con_code text not null,
          weight real,
          source text,
          status text not null default 'ready',
          primary key (universe, trade_date, con_code)
        )
        """
    )
    connection.execute(
        """
        create table trade_calendar (
          trade_date text primary key,
          is_trade_day integer not null,
          source text not null
        )
        """
    )
    connection.execute(
        """
        create table stock_ohlcv_daily (
          trade_date text not null,
          ts_code text not null,
          stock_name text,
          open real,
          high real,
          low real,
          close real,
          qfq_close real,
          pre_close real,
          pct_chg real,
          vol real,
          amount real,
          up_limit real,
          down_limit real,
          suspend_timing text,
          primary key (trade_date, ts_code)
        )
        """
    )
    codes = [f"{index:06d}.SZ" for index in range(500)]
    missing_code = codes[0]
    if include_previous_signal:
        signal_dates = ["20240131", "20240229"]
        pairs = [
            ("20240131", "20240229"),
            ("20240229", "20240329"),
        ]
    else:
        signal_dates = ["20240131"]
        pairs = [("20240131", "20240229")]
    membership_rows = [
        (
            "CSI500_ENH", "000905.SH", signal_date, code,
            0.2, "pit-test", "ready",
        )
        for signal_date in signal_dates
        for code in codes
    ]
    connection.executemany(
        "insert into index_constituent_period values (?, ?, ?, ?, ?, ?, ?)",
        membership_rows,
    )
    calendar_dates = [
        "20240130", "20240131", "20240228", "20240229",
        "20240301", "20240329", "20990104",
    ]
    connection.executemany(
        "insert into trade_calendar values (?, 1, 'authoritative-pit-test')",
        [(date,) for date in calendar_dates],
    )

    if include_previous_signal:
        price_rows = [
            (
                "20240131", missing_code, "missing", 10.0, 10.0, 10.0,
                10.0, 10.0, 10.0, 0.0, 100.0, 10_000.0,
                11.0, 9.0, None,
            ),
            (
                "20240228", missing_code, "missing", 10.5, 10.5, 10.5,
                10.5, 10.5, 10.0, 5.0, 100.0, 10_000.0,
                11.5, 9.5, None,
            ),
            (
                "20240301", missing_code, "missing", 11.0, 11.1, 10.9,
                11.0, 11.0, 10.5, 4.8, 100.0, 10_000.0,
                12.0, 10.0, None,
            ),
            (
                "20240329", missing_code, "missing", 12.0, 12.1, 11.9,
                12.0, 12.0, 11.0, 9.1, 100.0, 10_000.0,
                13.0, 11.0, None,
            ),
        ]
    else:
        price_rows = [
            (
                "20240130", missing_code, "missing", 10.0, 10.0, 10.0,
                10.0, 10.0, 10.0, 0.0, 100.0, 10_000.0,
                11.0, 9.0, None,
            ),
            (
                "20240201", missing_code, "missing", 11.0, 11.1, 10.9,
                11.0, 11.0, 10.0, 10.0, 100.0, 10_000.0,
                12.0, 10.0, None,
            ),
            (
                "20240229", missing_code, "missing", 12.0, 12.1, 11.9,
                12.0, 12.0, 11.0, 9.1, 100.0, 10_000.0,
                13.0, 11.0, None,
            ),
        ]
    connection.executemany(
        "insert into stock_ohlcv_daily values "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        price_rows,
    )

    panel_rows: list[dict[str, object]] = []
    for period, signal_date in enumerate(signal_dates):
        for index, code in enumerate(codes):
            if code == missing_code and (
                not include_previous_signal or signal_date == "20240229"
            ):
                continue
            panel_rows.append({
                "trade_date": signal_date,
                "ts_code": code,
                "index_weight": 0.2,
                "factor_quality": float(index) + period * 0.25,
                "industry_name": f"industry_{index // 50:02d}",
                "label_next_ret": 0.01,
                "amount": 10_000.0,
                "is_suspended": 0,
                "limit_pressure": 0.0,
                "entry_trade_date": (
                    "20240201" if signal_date == "20240131" else "20240301"
                ),
                "exit_trade_date": (
                    "20240229" if signal_date == "20240131" else "20240329"
                ),
            })
    return (
        connection,
        pd.DataFrame(panel_rows),
        pairs,
        missing_code,
        _pit_completion_config(),
    )


def _install_pit_panel_stubs(
    monkeypatch,
    source_panel: pd.DataFrame,
    pairs: list[tuple[str, str]],
) -> None:
    from framework.backtest import run_v2_models
    from model.llm_factor_mining import factor_miner

    monkeypatch.setattr(
        run_v2_models,
        "build_stock_panel",
        lambda *args, **kwargs: (source_panel.copy(), None),
    )
    monkeypatch.setattr(
        factor_miner,
        "month_dates",
        lambda *args, **kwargs: list(pairs),
    )


def test_database_panel_carries_suspended_member_with_pit_audit_and_zero_limits(
    monkeypatch,
):
    connection, source, pairs, missing_code, config = (
        _create_pit_completion_case(include_previous_signal=True)
    )
    _install_pit_panel_stubs(monkeypatch, source, pairs)

    panel, audit = strategy_module.build_csi500_panel_from_database(
        connection,
        start="20240101",
        end="20240329",
        config=config,
    )
    current = panel[panel["signal_date"] == "20240229"].copy()
    assert len(current) == 500
    assert current["ts_code"].nunique() == 500
    carried = current.loc[current["ts_code"] == missing_code].iloc[0]
    prior = panel.loc[
        (panel["signal_date"] == "20240131")
        & (panel["ts_code"] == missing_code)
    ].iloc[0]
    assert carried["factor_quality"] == pytest.approx(prior["factor_quality"])
    assert carried["pit_member_source"] == "previous_month_exact_factor_carry"
    assert carried["pit_factor_source_signal_date"] == "20240131"
    assert carried["pit_latest_pre_signal_quote_date"] == "20240228"
    assert carried["pit_quote_staleness_trading_days"] == 1
    assert carried["pit_factor_staleness_periods"] == 1
    assert carried["pit_nontradable"] == 1
    assert carried["buy_limit_weight"] == pytest.approx(0.0)
    assert carried["sell_limit_weight"] == pytest.approx(0.0)
    assert carried["label_next_ret"] == pytest.approx(12.0 / 11.0 - 1.0)
    assert carried["pit_label_entry_date"] == "20240301"
    assert carried["pit_label_exit_date"] == "20240329"

    period = next(
        item for item in audit["periods"]
        if item["signal_date"] == "20240229"
    )
    assert period["expected_members"] == 500
    assert period["exact_signal_panel_members"] == 499
    assert period["carried_nontradable_member_count"] == 1
    assert period["panel_members"] == 500
    assert period["missing_members"] == []
    assert period["unrecoverable_members"] == []
    assert period["status"] == "ready"
    assert audit["all_periods_member_complete"] is True
    assert audit["all_periods_exact_signal_rows"] is False
    member_audit = period["member_source_audit"][0]
    assert member_audit["cross_sectional_imputation_used"] is False
    assert member_audit["future_feature_data_used"] is False
    assert member_audit["label_evidence"]["backfill_used"] is False
    assert member_audit["label_evidence"][
        "future_beyond_maturity_used"
    ] is False

    buy, sell, limit_audit = strategy_module._trade_limits(current, config)
    assert buy[missing_code] == pytest.approx(0.0)
    assert sell[missing_code] == pytest.approx(0.0)
    assert buy["000001.SZ"] > 0.0
    assert sell["000001.SZ"] > 0.0
    assert limit_audit["explicit_zero_override_count"] == 1
    assert limit_audit["pit_nontradable_zero_count"] == 1


def test_database_panel_without_previous_factor_history_remains_blocked(
    monkeypatch,
):
    connection, source, pairs, missing_code, config = (
        _create_pit_completion_case(include_previous_signal=False)
    )
    _install_pit_panel_stubs(monkeypatch, source, pairs)

    panel, audit = strategy_module.build_csi500_panel_from_database(
        connection,
        start="20240101",
        end="20240229",
        config=config,
    )
    assert len(panel) == 499
    period = audit["periods"][0]
    assert period["expected_members"] == 500
    assert period["panel_members"] == 499
    assert period["missing_members"] == [missing_code]
    assert period["status"] == "blocked"
    assert audit["all_periods_member_complete"] is False
    assert period["unrecoverable_members"][0]["reason"] == (
        "missing_previous_month_factor_row"
    )
    assert period["unrecoverable_members"][0][
        "cross_sectional_imputation_used"
    ] is False


def test_database_panel_carry_never_reads_price_after_maturity(
    monkeypatch,
):
    connection, source, pairs, missing_code, config = (
        _create_pit_completion_case(include_previous_signal=True)
    )
    _install_pit_panel_stubs(monkeypatch, source, pairs)
    first, first_audit = strategy_module.build_csi500_panel_from_database(
        connection,
        start="20240101",
        end="20240329",
        config=config,
    )
    baseline = first.loc[
        (first["signal_date"] == "20240229")
        & (first["ts_code"] == missing_code)
    ].iloc[0]

    connection.execute(
        "insert into stock_ohlcv_daily values "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "20990104", missing_code, "future", 1_000_000.0, 1_000_000.0,
            1_000_000.0, 1_000_000.0, 1_000_000.0, 12.0, 0.0,
            100.0, 10_000.0, 1_100_000.0, 900_000.0, None,
        ),
    )
    changed, changed_audit = strategy_module.build_csi500_panel_from_database(
        connection,
        start="20240101",
        end="20240329",
        config=config,
    )
    after = changed.loc[
        (changed["signal_date"] == "20240229")
        & (changed["ts_code"] == missing_code)
    ].iloc[0]
    assert after["factor_quality"] == pytest.approx(
        baseline["factor_quality"]
    )
    assert after["label_next_ret"] == pytest.approx(
        baseline["label_next_ret"]
    )
    assert after["pit_latest_pre_signal_quote_date"] <= "20240229"
    assert "20240229" < after["pit_label_entry_date"] <= "20240329"
    assert after["pit_label_exit_date"] <= "20240329"
    for result_audit in (first_audit, changed_audit):
        policy = result_audit["member_completion_policy"]
        assert policy["label_backfill_used"] is False
        assert policy["label_future_beyond_maturity_used"] is False



def test_direct_score_comparator_audits_limit_violations_without_blocking():
    codes = [f"{index:06d}.SZ" for index in range(50)]
    group = pd.DataFrame({
        "ts_code": codes,
        "alpha_score": np.arange(50, dtype=float),
    })
    zero_limits = {code: 0.0 for code in codes}

    result = strategy_module._direct_score_portfolio(
        codes,
        group,
        {},
        zero_limits,
        zero_limits,
        0.001,
    )

    assert result["status"] == "ready"
    assert result["comparator_only"] is True
    assert result["research_use_only"] is True
    assert result["not_trading_advice"] is True
    assert result["tradable"] is False
    assert result["executable"] is False
    assert result["reason"] == (
        "trade_limit_violations_recorded_for_comparator_only"
    )
    assert len(result["weights"]) == 50
    assert set(result["weights"]) == set(codes)
    assert sum(result["weights"].values()) == pytest.approx(1.0)
    assert result["trade_limit_violation_count"] == 50
    assert result["trade_limit_violations"] == [
        f"buy:{code}" for code in codes
    ]
    assert result["one_way_turnover"] == pytest.approx(1.0)
    assert result["transaction_cost"] == pytest.approx(0.001)
    assert result["fallback_used"] is False
    assert result["not_optimizer_fallback"] is True


def test_fetch_panel_resolves_overlapping_industry_intervals(monkeypatch):
    from model.llm_factor_mining import factor_miner

    connection = sqlite3.connect(':memory:')
    connection.executescript(
        '''
        create table index_constituent_period (
          universe text, index_code text, trade_date text, con_code text,
          weight real, source text, status text
        );
        create table stock_ohlcv_daily (
          ts_code text, trade_date text, stock_name text,
          open real, high real, low real, close real, qfq_close real,
          pre_close real, pct_chg real, vol real, amount real,
          up_limit real, down_limit real, suspend_timing text
        );
        create table stock_valuation_daily (
          ts_code text, trade_date text, pb real, pe_ttm real, ps_ttm real,
          dv_ttm real, total_mv real, circ_mv real, turnover_rate real,
          turnover_rate_f real, volume_ratio real
        );
        create table stock_moneyflow_daily (
          ts_code text, trade_date text, net_mf_amount real,
          buy_lg_amount real, sell_lg_amount real,
          buy_elg_amount real, sell_elg_amount real
        );
        create table sw_l1_industry_daily (
          ts_code text, start_date text, end_date text, industry_name text
        );
        insert into index_constituent_period values
          ('CSI500_ENH','000905.SH','20200131','000001.SZ',0.2,'qa','ready');
        insert into stock_ohlcv_daily values
          ('000001.SZ','20200131','QA',10,10.5,9.5,10,10,9.8,2,100,1000,11,9,null),
          ('000001.SZ','20200228','QA',10.2,10.8,10,10.5,10.5,10,5,100,1000,11.5,9.5,null);
        insert into stock_valuation_daily values
          ('000001.SZ','20200131',1,10,1,0.02,1000,800,1,1,1);
        insert into stock_moneyflow_daily values
          ('000001.SZ','20200131',1,2,1,2,1);
        insert into sw_l1_industry_daily values
          ('000001.SZ','20190101','20201231','old_industry'),
          ('000001.SZ','20200101',null,'latest_industry');
        '''
    )
    monkeypatch.setattr(
        factor_miner, 'get_members', lambda *_args, **_kwargs: [('000001.SZ', 0.2)]
    )
    monkeypatch.setattr(
        factor_miner, 'offset_trade_date', lambda _conn, date, _offset: date
    )
    monkeypatch.setattr(
        factor_miner, 'load_event_features', lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        factor_miner, 'load_kline_feature_summary', lambda *_args, **_kwargs: {}
    )

    panel = factor_miner.fetch_panel(
        connection, 'CSI500_ENH', '20200131', '20200228', {}, {}
    )
    connection.close()

    assert len(panel) == 1
    assert panel.loc[0, 'ts_code'] == '000001.SZ'
    assert panel.loc[0, 'industry_name'] == 'latest_industry'
